"""DAISY v1.6.0 统一核验编排、控制与人读报告。

本模块只读消费已经封存的 schema 3／4 快照，不修改输入数据库。旧版
DBS-31／32 的兼容行为仍由 ``Script_DAISY_Lib_DBS_06_Verify`` 提供；
这里实现新 ``verify`` 入口所需的严格前后 stat、独立哈希、受控格式 worker、
共享暂停／timeout／停止协议，以及一份 Markdown＋一份 JSON 的报告发布。
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import multiprocessing
import os
import threading
import time
import uuid
from typing import Callable, Mapping

import Script_DAISY_Lib_DBS_01_Core as core
import Script_DAISY_Lib_DBS_03_Hash as dbhash
import Script_DAISY_Lib_DBS_06_Verify as legacy
import Script_DAISY_Lib_DBS_12_Verify_Tools as verifytools
import Script_DAISY_Lib_DBS_13_Raw as dbraw
import Script_DAISY_Lib_DBS_18_Tool_Runtime as toolruntime
import Script_DAISY_Lib_ENV_01_Capabilities as envcap


VERIFICATION_CONTRACT = "daisy-verification-v1"
HASH_MODES = frozenset(("off", "sample", "all"))
FORMAT_MODES = frozenset(("off", "sample", "all"))
TIMEOUT_DECISIONS = frozenset((
    "continue_waiting", "skip_and_record", "stop_and_resume",
))
FORMAT_COVERAGE_NOTE = (
    "格式校验检查容器、文件头尾、工具可解析性和已知损坏诊断；"
    "它不等于逐帧解码，也不证明文件内容在语义上完整。"
)
_CURRENT_ITEM_INTERVAL = 0.1
_PROGRESS_INTERVAL = 0.5
_ISSUE_ROW_LIMIT = 100


def _finite_percent(value: object, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label}不是有效数字：{value!r}") from exc
    if not math.isfinite(number) or number <= 0.0 or number > 100.0:
        raise ValueError(f"{label}必须大于 0 且不超过 100：{value!r}")
    return number


@dataclass(frozen=True)
class VerificationOptions:
    """一次统一核验的冻结选项；哈希与格式抽样口径互不复用。"""

    hash_mode: str = "sample"
    hash_sample_percent: float = 1.0
    format_mode: str = "off"
    format_sample_percent: float = 100.0
    timeout_decision: str = "continue_waiting"
    hash_timeout_seconds: float | None = None
    format_timeout_seconds: float | None = None
    raw_deep_validation: bool = False
    raw_timeout_seconds: float | None = None
    show_current_file: bool = False

    def __post_init__(self) -> None:
        if self.hash_mode not in HASH_MODES:
            raise ValueError(f"未知哈希核验模式：{self.hash_mode}")
        if self.format_mode not in FORMAT_MODES:
            raise ValueError(f"未知格式核验模式：{self.format_mode}")
        if self.raw_deep_validation and self.format_mode == "off":
            raise ValueError("RAW 深度校验必须依附已启用的格式校验")
        if self.raw_timeout_seconds is not None \
                and not self.raw_deep_validation:
            raise ValueError("RAW 深度校验关闭时不能设置 RAW timeout")
        if self.timeout_decision not in TIMEOUT_DECISIONS:
            raise ValueError(f"未知 timeout 默认处置：{self.timeout_decision}")
        _finite_percent(self.hash_sample_percent, "哈希抽样比例")
        _finite_percent(self.format_sample_percent, "格式抽样比例")
        for value, label in (
            (self.hash_timeout_seconds, "哈希 timeout"),
            (self.format_timeout_seconds, "格式 timeout"),
            (self.raw_timeout_seconds, "RAW timeout"),
        ):
            if value is not None and (
                    not math.isfinite(float(value)) or float(value) <= 0.0):
                raise ValueError(f"{label}必须大于 0：{value!r}")

    def as_dict(self) -> dict[str, object]:
        return {
            "hash_mode": self.hash_mode,
            "hash_sample_percent": self.hash_sample_percent,
            "format_mode": self.format_mode,
            "format_sample_percent": self.format_sample_percent,
            "timeout_decision": self.timeout_decision,
            "hash_timeout_seconds": self.hash_timeout_seconds,
            "format_timeout_seconds": self.format_timeout_seconds,
            "raw_deep_validation": self.raw_deep_validation,
            "raw_timeout_seconds": self.raw_timeout_seconds,
            "show_current_file": self.show_current_file,
        }


class UnifiedVerificationControl:
    """统一核验的进程内暂停／继续／停止和当前 worker 决策状态。"""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._state = "running"
        self._worker_control = dbhash.HashWorkerControl()
        self._paused_action: str | None = None

    @property
    def state(self) -> str:
        with self._condition:
            return self._state

    @property
    def worker_control(self) -> dbhash.HashWorkerControl:
        with self._condition:
            return self._worker_control

    def request_pause(self, source: str = "user") -> tuple[bool, str]:
        with self._condition:
            if self._state != "running":
                return False, "not_running"
            accepted = self._worker_control.request_pause(source)
            return accepted, (
                "accepted" if accepted else "action_already_decided")

    def request_continue(self) -> tuple[bool, str]:
        with self._condition:
            if self._state != "paused":
                return False, "not_paused"
            if self._paused_action is not None:
                return False, "paused_action_already_decided"
            self._paused_action = "continue"
            self._condition.notify_all()
            return True, "accepted"

    def request_stop(self, source: str = "user") -> tuple[bool, str]:
        with self._condition:
            if self._state == "ended":
                return False, "run_ended"
            if self._state == "paused":
                if self._paused_action is not None:
                    return False, "paused_action_already_decided"
                self._paused_action = "stop"
                self._condition.notify_all()
                return True, "accepted"
            accepted = self._worker_control.request_stop(source)
            return accepted, (
                "accepted" if accepted else "action_already_decided")

    def request_timeout_decision(
        self,
        worker_pid: int,
        decision: str,
        source: str = "user",
    ) -> tuple[bool, str]:
        if decision not in TIMEOUT_DECISIONS:
            return False, "invalid_decision"
        with self._condition:
            if self._state != "running":
                return False, "not_running"
            accepted = self._worker_control.request_timeout_decision(
                worker_pid, decision, source)
            return accepted, (
                "accepted" if accepted else "worker_or_decision_mismatch")

    def current_action(self) -> tuple[str, str] | None:
        return self.worker_control.current()

    def wait_after_pause(self, poll_seconds: float = 0.25) -> str:
        if poll_seconds <= 0:
            raise ValueError("暂停轮询间隔必须大于 0")
        with self._condition:
            if self._state != "running":
                raise RuntimeError(f"状态 {self._state} 不能进入暂停")
            self._state = "paused"
            self._paused_action = None
            while self._paused_action is None:
                self._condition.wait(poll_seconds)
            action = self._paused_action
            self._paused_action = None
            if action == "continue":
                self._state = "running"
                self._worker_control = dbhash.HashWorkerControl()
                return "continue"
            self._state = "ended"
            return "stop"

    def finish(self) -> None:
        with self._condition:
            self._state = "ended"
            self._condition.notify_all()


@dataclass(frozen=True)
class _Entry:
    entry_id: int
    root_id: int
    rel_path: str
    logical_path: str
    physical_path: str
    extension: str
    media_kind: str
    size_bytes: int
    modified_at_utc: str
    baseline_hash: str | None


@dataclass(frozen=True)
class _FileStat:
    size_bytes: int
    modified_at_utc: str
    modified_ns: int


@dataclass(frozen=True)
class FormatWorkerOutcome:
    outcome: str
    status: str | None
    detail: str | None
    decision: str
    decision_source: str
    size_bytes: int
    elapsed_seconds: float
    threshold_count: int
    worker_pid: int
    worker_exitcode: int | None
    worker_reaped: bool
    events: tuple[dict[str, object], ...]
    failure_kind: str | None = None


@dataclass(frozen=True)
class VerificationPublication:
    json_path: str
    markdown_path: str


def _emit(on_event, event: str, **payload: object) -> None:
    if on_event is not None:
        on_event(event, **payload)


class _RateEmitter:
    def __init__(self, callback, interval: float) -> None:
        self._callback = callback
        self._interval = interval
        self._last: dict[str, float] = {}

    def send(self, key: str, *args, force: bool = False, **kwargs) -> None:
        if self._callback is None:
            return
        now = time.monotonic()
        if not force and now - self._last.get(key, 0.0) < self._interval:
            return
        self._last[key] = now
        self._callback(*args, **kwargs)


def _file_identity(path: str) -> dict[str, object]:
    stat_result = os.stat(path, follow_symlinks=False)
    return {
        "sha256": core.sha256_file(path),
        "size_bytes": int(stat_result.st_size),
        "modified_ns": int(stat_result.st_mtime_ns),
    }


def _stat_file(path: str) -> tuple[_FileStat | None, str | None]:
    try:
        stat_result = os.stat(
            core.to_extended_path(path), follow_symlinks=False)
    except OSError as exc:
        return None, f"{type(exc).__name__}: {exc}"
    return _FileStat(
        int(stat_result.st_size),
        core.ns_to_utc_iso(stat_result.st_mtime_ns),
        int(stat_result.st_mtime_ns),
    ), None


def _matches_baseline(entry: _Entry, current: _FileStat) -> bool:
    return (
        current.size_bytes == entry.size_bytes
        and current.modified_at_utc == entry.modified_at_utc
    )


def _same_stat(left: _FileStat, right: _FileStat) -> bool:
    return (
        left.size_bytes == right.size_bytes
        and left.modified_ns == right.modified_ns
    )


def _descriptor_summary(descriptor) -> dict[str, object]:
    capability_ids = ("files", "hashes", "format_checks")
    return {
        "database_type": descriptor.database_type,
        "schema_version": descriptor.schema_version,
        "source_version": descriptor.source_version,
        "lifecycle": descriptor.lifecycle,
        "status": descriptor.status,
        "database_integrity": descriptor.database_integrity,
        "sqlite_integrity": descriptor.sqlite_integrity,
        "data_contract": descriptor.data_contract,
        "min_reader_version": descriptor.min_reader_version,
        "capabilities": {
            capability_id: descriptor.capability(capability_id).as_dict()
            for capability_id in capability_ids
        },
        "warnings": list(descriptor.warnings),
    }


def _load_entries(
    snapshot_path: str,
    root_specs: list[str],
    *,
    force: bool,
) -> tuple[dict[str, object], list[_Entry], dict[str, object]]:
    before = _file_identity(os.path.abspath(snapshot_path))
    verification = legacy.open_verification_snapshot(
        snapshot_path,
        root_specs=root_specs,
        force=force,
        required_capabilities=("files",),
    )
    try:
        descriptor = verification.descriptor
        hash_capability = descriptor.capability("hashes")
        has_hash_table = hash_capability.state in ("available", "empty")
        selected = (
            "SELECT e.entry_id,e.root_id,e.rel_path,e.extension,e.media_kind,"
            " e.size_bytes,e.modified_at_utc,h.hash_hex"
            " FROM entries e LEFT JOIN hashes h"
            " ON h.entry_id=e.entry_id AND h.algorithm='sha256'"
            " AND h.status='valid' WHERE e.is_placeholder=0"
            " ORDER BY e.root_id,e.rel_path"
            if has_hash_table else
            "SELECT e.entry_id,e.root_id,e.rel_path,e.extension,e.media_kind,"
            " e.size_bytes,e.modified_at_utc,NULL"
            " FROM entries e WHERE e.is_placeholder=0"
            " ORDER BY e.root_id,e.rel_path"
        )
        entries = []
        for row in verification.connection.execute(selected):
            (entry_id, root_id, rel_path, extension, media_kind,
             size_bytes, modified_at_utc, baseline_hash) = row
            root_id = int(root_id)
            rel_path = str(rel_path)
            digest = str(baseline_hash).lower() if baseline_hash else None
            if digest is not None and (
                    len(digest) != 64
                    or any(ch not in "0123456789abcdef" for ch in digest)):
                digest = None
            entries.append(_Entry(
                entry_id=int(entry_id),
                root_id=root_id,
                rel_path=rel_path,
                logical_path=verification.logical_path(root_id, rel_path),
                physical_path=verification.physical_path(root_id, rel_path),
                extension=str(extension or "").casefold(),
                media_kind=str(media_kind or "other"),
                size_bytes=int(size_bytes),
                modified_at_utc=str(modified_at_utc),
                baseline_hash=digest,
            ))
        snapshot = {
            "path": verification.path,
            "filename": verification.filename,
            "snapshot_uuid": verification.snapshot_uuid,
            "hash_coverage": verification.hash_coverage,
            "root_labels": list(verification.root_labels),
            "database": _descriptor_summary(descriptor),
        }
    finally:
        verification.close()
    after_load = _file_identity(os.path.abspath(snapshot_path))
    if before != after_load:
        raise core.PreflightError("核验准入期间输入快照发生变化，已拒绝继续")
    return snapshot, entries, before


def _choose_entries(
    entries: list[_Entry],
    mode: str,
    percent: float,
    seed: str,
) -> list[_Entry]:
    if mode == "off":
        return []
    if mode == "all":
        return list(entries)
    ids = {
        int(entry_id)
        for entry_id, _size in dbhash.pick_sample(
            [(entry.entry_id, entry.size_bytes) for entry in entries],
            percent,
            100,
            seed=seed,
        )
    }
    return [entry for entry in entries if entry.entry_id in ids]


def _issue_row(
    entry: _Entry,
    status: str,
    detail: str | None = None,
    **extra: object,
) -> dict[str, object]:
    row: dict[str, object] = {
        "path": entry.logical_path,
        "rel_path": entry.rel_path,
        "root_id": entry.root_id,
        "status": status,
        "detail": detail,
    }
    row.update(extra)
    return row


def _runtime_failure(
    *,
    tool: str,
    operation: str,
    failure_kind: str,
    detail: str | None,
    pid: int | None,
    returncode: int | None,
) -> toolruntime.ToolRuntimeFailure:
    unrecovered = failure_kind in {
        "start_failed", "cleanup_failed", "worker_start_failed",
        "rawpy_unavailable",
    }
    return toolruntime.ToolRuntimeFailure(
        toolruntime.ToolFaultEvidence(
            tool=str(tool),
            operation=str(operation),
            failure_kind=str(failure_kind),
            message=str(detail or f"{tool} 运行故障")[:2048],
            pid=None if pid is None else int(pid),
            returncode=(
                None if returncode is None else int(returncode)),
        ),
        recovered=not unrecovered,
    )


def _record_tool_failure_group(
    groups: dict[tuple[object, ...], dict[str, object]],
    failure: toolruntime.ToolRuntimeFailure,
    entry: _Entry,
) -> dict[str, object]:
    signature = failure.signature
    group = groups.setdefault(signature, {
        "tool": failure.latest.tool,
        "failure_kind": failure.latest.failure_kind,
        "detail": failure.latest.message,
        "affected_files": 0,
        "entry_ids": [],
        "sample_paths": [],
        "not_processed": 0,
        "returncode": failure.latest.returncode,
        "returncode_hex": toolruntime.format_returncode(
            failure.latest.returncode),
    })
    group["affected_files"] = int(group["affected_files"]) + 1
    entry_ids = group["entry_ids"]
    if isinstance(entry_ids, list):
        entry_ids.append(entry.entry_id)
    samples = group["sample_paths"]
    if isinstance(samples, list) and len(samples) < 3:
        samples.append(entry.logical_path)
    return group


def _tool_failure_problem_rows(
    groups: Mapping[tuple[object, ...], Mapping[str, object]],
) -> list[dict[str, object]]:
    rows = []
    for group in groups.values():
        affected = int(group.get("affected_files") or 0)
        not_processed = int(group.get("not_processed") or 0)
        tool = str(group.get("tool") or "外部工具")
        kind = str(group.get("failure_kind") or "tool_error")
        detail = (
            f"{tool} 工具级故障 {kind}；影响已尝试文件 {affected} 个"
            + (f"；熔断后未处理 {not_processed} 个" if not_processed else "")
            + f"；{str(group.get('detail') or '').strip()}"
        ).rstrip("；")
        rows.append({
            "path": "（工具故障聚合）",
            "rel_path": None,
            "root_id": None,
            "status": "tool_error",
            "detail": detail,
            **dict(group),
        })
    return rows


def _problem_file_count(
    problems: list[dict[str, object]],
) -> int:
    return sum(
        int(row.get("affected_files") or 1)
        for row in problems
    )


def _settle_boundary(
    control: UnifiedVerificationControl,
    stage: str,
    on_event,
) -> str:
    action = control.current_action()
    if action is None:
        return "running"
    name, _source = action
    if name == "stop":
        control.finish()
        _emit(on_event, "run_stopped", stage=stage, state="stopped")
        return "stopped"
    if name not in ("pause", "save_exit"):
        raise core.PreflightError(f"核验收到未知控制动作：{name}")
    _emit(on_event, "run_paused", stage=stage, state="paused")
    result = control.wait_after_pause()
    if result == "continue":
        _emit(on_event, "run_resumed", stage=stage, state="running")
        return "running"
    _emit(on_event, "run_stopped", stage=stage, state="stopped")
    return "stopped"


def _wait_worker_pause(
    control: UnifiedVerificationControl,
    stage: str,
    on_event,
) -> str:
    _emit(on_event, "run_paused", stage=stage, state="paused")
    result = control.wait_after_pause()
    if result == "continue":
        _emit(on_event, "run_resumed", stage=stage, state="running")
        return "running"
    _emit(on_event, "run_stopped", stage=stage, state="stopped")
    return "stopped"


def _format_worker_main(connection) -> None:
    """在受控子进程中执行一个不创建孙进程的内置格式校验。"""
    try:
        connection.send({"kind": "ready"})
        request = connection.recv()
        if not isinstance(request, dict):
            raise ValueError("格式 worker 请求不是对象")
        spec_payload = request["spec"]
        spec = legacy.FormatValidatorSpec(
            str(spec_payload["validator"]),
            str(spec_payload["tool_name"]),
            str(spec_payload["tool_version"]),
        )
        if spec.validator not in ("zip", "pdf"):
            raise ValueError("内置格式 worker 不允许启动外部工具")
        with legacy.FormatValidationSession(request.get("tools") or {}) \
                as session:
            status, detail = session.validate(
                str(request["path"]),
                str(request["media_kind"]),
                spec,
            )
        connection.send({
            "kind": "result",
            "status": status,
            "detail": detail,
        })
    except BaseException as exc:
        try:
            connection.send({
                "kind": "crash",
                "error_type": type(exc).__name__,
                "error": str(exc)[:2048],
            })
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        connection.close()


def _terminate_format_process(process) -> None:
    if not process.is_alive():
        return
    process.terminate()


def _finish_format_process(
    process,
    *,
    terminate: bool,
) -> tuple[int | None, bool]:
    if terminate:
        _terminate_format_process(process)
    process.join(timeout=2.0)
    if process.is_alive():
        _terminate_format_process(process)
        process.join(timeout=2.0)
    exitcode = process.exitcode
    reaped = not process.is_alive()
    if reaped:
        process.close()
    return exitcode, reaped


def run_format_worker(
    path: str,
    media_kind: str,
    spec: legacy.FormatValidatorSpec,
    tools: Mapping[str, object],
    *,
    expected_size: int,
    timeout_seconds: float | None = None,
    default_decision: str = "continue_waiting",
    display_name: str | None = None,
    control: dbhash.HashWorkerControl | None = None,
    on_event: Callable[..., None] | None = None,
    on_threshold: Callable[..., None] | None = None,
    poll_seconds: float = 0.05,
    worker_start_timeout_seconds: float = 30.0,
    _worker_target=None,
) -> FormatWorkerOutcome:
    """监督一个内置格式 worker；它不允许创建任何工具孙进程。"""
    if expected_size < 0:
        raise ValueError("expected_size 不能小于 0")
    if default_decision not in TIMEOUT_DECISIONS:
        raise ValueError(f"未知 timeout 默认处置：{default_decision}")
    if poll_seconds <= 0 or worker_start_timeout_seconds <= 0:
        raise ValueError("poll 与启动 timeout 必须大于 0")
    if spec.validator not in ("zip", "pdf"):
        raise core.PreflightError(
            "外部格式校验必须使用直接工具句柄监督器，"
            "不能交给可被强制终止的 Python worker")
    threshold_seconds = (
        dbhash.hash_no_progress_timeout_for_size(expected_size)
        if timeout_seconds is None else float(timeout_seconds)
    )
    if threshold_seconds <= 0:
        raise ValueError("timeout_seconds 必须大于 0")

    normalized = os.path.abspath(path)
    label = str(display_name or os.path.basename(normalized))
    owned_control = control or dbhash.HashWorkerControl()
    events: list[dict[str, object]] = []

    def emit(event: str, **payload: object) -> None:
        record = {"event": event, **payload}
        events.append(record)
        _emit(on_event, event, **payload)

    context = multiprocessing.get_context("spawn")
    receive, send = context.Pipe(duplex=True)
    process = context.Process(
        target=_worker_target or _format_worker_main,
        args=(send,),
        daemon=True,
    )
    started_process = False
    try:
        process.start()
        started_process = True
        send.close()
        owned_control.bind_worker(int(process.pid))
    except Exception:
        try:
            receive.close()
        except OSError:
            pass
        try:
            send.close()
        except OSError:
            pass
        if started_process:
            _finish_format_process(process, terminate=True)
        else:
            process.close()
        raise

    worker_pid = int(process.pid)
    emit("worker_started", file=label, worker_pid=worker_pid,
         implementation="daisy_format_worker")
    started = time.monotonic()
    ready = False
    terminate = False
    outcome = "crashed"
    status = None
    detail = None
    decision = "none"
    decision_source = "none"
    threshold_count = 0
    timeout_window_started = started
    request_sent = False

    try:
        while not ready:
            if receive.poll(poll_seconds):
                message = receive.recv()
                if isinstance(message, dict) and message.get("kind") == "ready":
                    ready = True
                    break
            if not process.is_alive():
                detail = "格式 worker 在握手前退出"
                break
            if time.monotonic() - started >= worker_start_timeout_seconds:
                detail = "格式 worker 启动超时"
                terminate = True
                break
        if ready:
            receive.send({
                "path": normalized,
                "media_kind": media_kind,
                "spec": {
                    "validator": spec.validator,
                    "tool_name": spec.tool_name,
                    "tool_version": spec.tool_version,
                },
                "tools": dict(tools),
            })
            request_sent = True
            emit("worker_ready", file=label, worker_pid=worker_pid)

        while ready and request_sent:
            action = owned_control.current()
            if action is not None:
                action_name, action_source = action
                outcome = {
                    "pause": "paused",
                    "save_exit": "paused",
                    "stop": "stopped",
                }[action_name]
                decision = "stop_and_resume"
                decision_source = action_source
                terminate = True
                emit("worker_controlled", file=label, action=action_name)
                break
            if receive.poll(poll_seconds):
                try:
                    message = receive.recv()
                except EOFError:
                    message = None
                if isinstance(message, dict) and message.get("kind") == "result":
                    status = str(message.get("status") or "error")
                    detail_value = message.get("detail")
                    detail = str(detail_value) if detail_value is not None else None
                    outcome = "completed"
                    emit("worker_completed", file=label, status=status)
                    break
                if isinstance(message, dict) and message.get("kind") == "crash":
                    detail = (
                        f"{message.get('error_type')}: {message.get('error')}")
                    emit("worker_crashed", file=label, error=detail)
                    break
            if not process.is_alive():
                detail = detail or "格式 worker 未返回结果"
                break

            now = time.monotonic()
            pending = owned_control.take_timeout_decision(worker_pid)
            if pending is not None:
                decision = pending.decision
                decision_source = pending.source
                emit(
                    "stall_decided", file=label, worker_pid=worker_pid,
                    decision=decision, decision_source=decision_source,
                    threshold_count=threshold_count,
                )
                if decision == "continue_waiting":
                    timeout_window_started = now
                    owned_control.open_timeout_decision(worker_pid)
                    continue
                terminate = True
                outcome = (
                    "timeout" if decision == "skip_and_record" else "stopped")
                detail = (
                    "format_no_progress_timeout"
                    if outcome == "timeout" else "stop_and_resume")
                break
            if now - timeout_window_started >= threshold_seconds:
                threshold_count += 1
                owned_control.open_timeout_decision(worker_pid)
                payload = {
                    "file": label,
                    "worker_pid": worker_pid,
                    "size_bytes": expected_size,
                    "bytes_read": 0,
                    "threshold_seconds": threshold_seconds,
                    "threshold_count": threshold_count,
                }
                emit("threshold_reached", **payload)
                arbiter = dbhash.AtomicTimeoutDecision()
                if on_threshold is not None:
                    try:
                        on_threshold(payload, arbiter)
                    except Exception as exc:
                        emit("threshold_callback_error", file=label,
                             error=str(exc))
                if owned_control.current() is not None:
                    continue
                choice = owned_control.resolve_timeout_decision(
                    worker_pid,
                    default_decision,
                    preferred=arbiter.current(),
                )
                decision = choice.decision
                decision_source = choice.source
                emit(
                    "threshold_decided", file=label, worker_pid=worker_pid,
                    decision=decision, decision_source=decision_source,
                    threshold_count=threshold_count,
                )
                if decision == "continue_waiting":
                    timeout_window_started = time.monotonic()
                    owned_control.open_timeout_decision(worker_pid)
                    continue
                terminate = True
                outcome = (
                    "timeout" if decision == "skip_and_record" else "stopped")
                detail = (
                    "format_no_progress_timeout"
                    if outcome == "timeout" else "stop_and_resume")
                break
    finally:
        receive.close()
        try:
            exitcode, reaped = _finish_format_process(
                process, terminate=terminate or outcome != "completed")
        finally:
            owned_control.unbind_worker(worker_pid)

    if outcome == "completed" and (not reaped or exitcode != 0):
        outcome = "crashed"
        status = "error"
        detail = "格式 worker 未干净退出并回收"
    elif outcome == "timeout":
        status = "timeout"
    elif outcome == "crashed":
        status = "error"
    failure_kind = None
    if outcome == "crashed":
        if toolruntime.is_native_crash_returncode(exitcode):
            failure_kind = "native_crash"
        elif not reaped:
            failure_kind = "cleanup_failed"
        elif detail == "格式 worker 启动超时":
            failure_kind = "start_timeout"
        else:
            failure_kind = "worker_protocol_failed"
    return FormatWorkerOutcome(
        outcome=outcome,
        status=status,
        detail=detail,
        decision=decision,
        decision_source=decision_source,
        size_bytes=expected_size,
        elapsed_seconds=max(0.0, time.monotonic() - started),
        threshold_count=threshold_count,
        worker_pid=worker_pid,
        worker_exitcode=exitcode,
        worker_reaped=reaped,
        events=tuple(events),
        failure_kind=failure_kind,
    )


def run_format_validator(
    path: str,
    media_kind: str,
    spec: legacy.FormatValidatorSpec,
    tools: Mapping[str, object],
    *,
    expected_size: int,
    timeout_seconds: float | None = None,
    default_decision: str = "continue_waiting",
    display_name: str | None = None,
    control: dbhash.HashWorkerControl | None = None,
    on_event: Callable[..., None] | None = None,
    on_threshold: Callable[..., None] | None = None,
) -> FormatWorkerOutcome | verifytools.ExternalFormatOutcome:
    """把内置判据与直接外部工具路由到各自的精确监督器。"""
    if spec.validator in ("zip", "pdf"):
        return run_format_worker(
            path,
            media_kind,
            spec,
            tools,
            expected_size=expected_size,
            timeout_seconds=timeout_seconds,
            default_decision=default_decision,
            display_name=display_name,
            control=control,
            on_event=on_event,
            on_threshold=on_threshold,
        )
    return verifytools.run_external_format_validator(
        path,
        media_kind,
        spec,
        tools,
        expected_size=expected_size,
        timeout_seconds=timeout_seconds,
        default_decision=default_decision,
        display_name=display_name,
        control=control,
        on_event=on_event,
        on_threshold=on_threshold,
    )


def _resolve_tool(
    name: str,
    supplied: Mapping[str, str | None],
    used: dict[str, dict[str, object]],
    on_event,
    resolver,
) -> dict[str, object]:
    if name in used:
        return used[name]
    if resolver is not None:
        info = dict(resolver(name, supplied.get(name)))
    else:
        explicit = supplied.get(name)
        path = core.discover_tool(name, explicit)
        info = core.resolved_tool_info(
            name, path, explicit=bool(explicit))
    if not info.get("path") or not info.get("version"):
        raise core.PreflightError(f"{name} 工具身份不完整")
    used[name] = info
    _emit(on_event, "tools_detected", tools={name: info})
    return info


def _format_spec(
    entry: _Entry,
    supplied: Mapping[str, str | None],
    used: dict[str, dict[str, object]],
    on_event,
    resolver,
) -> legacy.FormatValidatorSpec:
    validator = legacy.pick_format_validator(
        entry.extension, entry.media_kind)
    if validator == "zip":
        version = ".".join(map(str, os.sys.version_info[:3]))
        return legacy.FormatValidatorSpec(
            validator, "python-zipfile", version)
    if validator in ("pdf", "none"):
        return legacy.FormatValidatorSpec(
            validator, "daisy-format", legacy.FORMAT_VALIDATION_PROFILE)
    if validator in ("ole", "7z"):
        sevenzip = _resolve_tool(
            "sevenzip", supplied, used, on_event, resolver)
        return legacy.FormatValidatorSpec(
            validator, "7-Zip", str(sevenzip["version"]))
    exiftool = _resolve_tool(
        "exiftool", supplied, used, on_event, resolver)
    effective_kind = (
        "image_gif" if validator == "gif" else entry.media_kind)
    if effective_kind in legacy._FFPROBE_KINDS:
        ffprobe = _resolve_tool(
            "ffprobe", supplied, used, on_event, resolver)
        return legacy.FormatValidatorSpec(
            validator,
            "exiftool+ffprobe",
            f"exiftool {exiftool['version']}; ffprobe {ffprobe['version']}",
        )
    return legacy.FormatValidatorSpec(
        validator, "exiftool", str(exiftool["version"]))


def _section_counts(rows: list[dict[str, object]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        status = str(row.get("status") or "error")
        counts[status] = counts.get(status, 0) + 1
    return counts


def _run_stat_stage(
    entries: list[_Entry],
    options: VerificationOptions,
    control: UnifiedVerificationControl,
    on_progress,
    on_event,
) -> tuple[dict[str, object], dict[int, _FileStat], str]:
    progress = _RateEmitter(on_progress, _PROGRESS_INTERVAL)
    current = _RateEmitter(on_event, _CURRENT_ITEM_INTERVAL)
    stats: dict[int, _FileStat] = {}
    problems: list[dict[str, object]] = []
    _emit(on_event, "stage_started", stage="stat")
    processed = 0
    for entry in entries:
        boundary = _settle_boundary(control, "stat", on_event)
        if boundary != "running":
            return {
                "state": "stopped", "checked": processed,
                "total": len(entries), "problems": problems,
                "counts": _section_counts(problems),
            }, stats, "stopped"
        if options.show_current_file:
            current.send(
                "stat", "current_item", stage="stat",
                item=entry.rel_path)
        observed, error = _stat_file(entry.physical_path)
        if observed is None:
            problems.append(_issue_row(entry, "missing", error))
        else:
            stats[entry.entry_id] = observed
            if not _matches_baseline(entry, observed):
                problems.append(_issue_row(
                    entry,
                    "changed",
                    "size／mtime 与快照记录不同",
                    size_recorded=entry.size_bytes,
                    size_now=observed.size_bytes,
                    mtime_recorded=entry.modified_at_utc,
                    mtime_now=observed.modified_at_utc,
                ))
        processed += 1
        progress.send(
            "stat", "stat", processed, len(entries),
            {"problems": len(problems)},
        )
    progress.send(
        "stat", "stat", processed, len(entries),
        {"problems": len(problems)}, force=True)
    _emit(on_event, "stage_finished", stage="stat", processed=processed,
          problems=len(problems))
    return {
        "state": "executed",
        "checked": processed,
        "total": len(entries),
        "matched": processed - len(problems),
        "counts": _section_counts(problems),
        "problems": problems,
    }, stats, "running"


def _run_hash_stage(
    entries: list[_Entry],
    initial_stats: dict[int, _FileStat],
    snapshot: dict[str, object],
    options: VerificationOptions,
    control: UnifiedVerificationControl,
    supplied_tools: Mapping[str, str | None],
    on_progress,
    on_event,
    on_threshold,
    powershell_resolver,
    hash_runner,
) -> tuple[dict[str, object], str, dict[str, dict[str, object]]]:
    if options.hash_mode == "off":
        _emit(on_event, "stage_skipped", stage="hash", reason="未选择")
        return {
            "state": "NULL",
            "reason": "本次未选择内容哈希",
            "mode": "off",
            "problems": [],
        }, "running", {}
    capability = snapshot["database"]["capabilities"]["hashes"]
    if capability["state"] not in ("available", "empty"):
        _emit(on_event, "stage_skipped", stage="hash",
              reason="快照未记录逐文件哈希")
        return {
            "state": "unavailable",
            "reason": capability.get("reason") or "快照未记录逐文件哈希",
            "mode": options.hash_mode,
            "unverifiable": len(entries),
            "problems": [],
        }, "running", {}

    selected = _choose_entries(
        entries,
        options.hash_mode,
        options.hash_sample_percent,
        # 保持 v1.5.1 check-hash 的确定性样本，便于新旧报告对照。
        str(snapshot["snapshot_uuid"]) + ":patrol",
    )
    with_baseline = [entry for entry in selected if entry.baseline_hash]
    used_tools: dict[str, dict[str, object]] = {}
    powershell_path = None
    if with_baseline:
        explicit = supplied_tools.get("powershell")
        if powershell_resolver is None:
            path, version = dbhash.discover_powershell(explicit)
            info = core.resolved_tool_info(
                "powershell", path, explicit=bool(explicit), version=version)
        else:
            info = dict(powershell_resolver(explicit))
        if not info.get("path") or not info.get("version"):
            raise core.PreflightError("PowerShell 工具身份不完整")
        used_tools["powershell"] = info
        powershell_path = str(info["path"])
        _emit(on_event, "tools_detected", tools={"powershell": info})

    _emit(on_event, "stage_started", stage="hash")
    progress = _RateEmitter(on_progress, _PROGRESS_INTERVAL)
    current = _RateEmitter(on_event, _CURRENT_ITEM_INTERVAL)
    problems: list[dict[str, object]] = []
    matched = 0
    unverifiable = 0
    checked = 0
    performance: list[dict[str, object]] = []
    processed = 0
    runner = hash_runner or dbhash.run_independent_hash_process
    circuit = toolruntime.ConsecutiveToolFailureCircuit()
    tool_failure_groups: dict[
        tuple[object, ...], dict[str, object]
    ] = {}

    def combined_problems() -> list[dict[str, object]]:
        return [*problems, *_tool_failure_problem_rows(tool_failure_groups)]

    for entry in selected:
        while True:
            boundary = _settle_boundary(control, "hash", on_event)
            if boundary != "running":
                return {
                    "state": "stopped", "mode": options.hash_mode,
                    "selected": len(selected), "processed": processed,
                    "checked": checked, "matched": matched,
                    "unverifiable": unverifiable,
                    "counts": _section_counts(combined_problems()),
                    "problems": combined_problems(),
                    "problem_files": _problem_file_count(combined_problems()),
                    "performance": performance,
                    "tools": used_tools,
                }, "stopped", used_tools
            if not entry.baseline_hash:
                unverifiable += 1
                processed += 1
                break
            initial = initial_stats.get(entry.entry_id)
            if initial is None or not _matches_baseline(entry, initial):
                unverifiable += 1
                processed += 1
                break
            before, before_error = _stat_file(entry.physical_path)
            if before is None:
                problems.append(_issue_row(entry, "missing", before_error))
                processed += 1
                break
            if not _matches_baseline(entry, before):
                problems.append(_issue_row(
                    entry, "stat_changed",
                    "开始哈希前 size／mtime 已变化"))
                processed += 1
                break
            if options.show_current_file:
                current.send(
                    "hash", "current_item", stage="hash",
                    item=entry.rel_path)
            assert powershell_path is not None
            try:
                outcome = runner(
                    entry.physical_path,
                    powershell_path,
                    expected_size=entry.size_bytes,
                    timeout_seconds=options.hash_timeout_seconds,
                    default_decision=options.timeout_decision,
                    display_name=entry.rel_path,
                    control=control.worker_control,
                    on_event=on_event,
                    on_threshold=on_threshold,
                )
            except (OSError, core.PreflightError) as exc:
                failure = _runtime_failure(
                    tool="powershell",
                    operation="independent_hash",
                    failure_kind="start_failed",
                    detail=f"PowerShell 哈希进程无法启动：{exc}",
                    pid=None,
                    returncode=None,
                )
                group = _record_tool_failure_group(
                    tool_failure_groups, failure, entry)
                snapshot_failure = circuit.record_failure(
                    entry.entry_id, failure)
                processed += 1
                checked += 1
                group["not_processed"] = max(0, len(selected) - processed)
                all_problems = combined_problems()
                _emit(
                    on_event, "tool_circuit_open", stage="hash",
                    tool="powershell",
                    failure_kind=failure.latest.failure_kind,
                    affected=int(group["affected_files"]),
                    not_processed=int(group["not_processed"]),
                )
                _emit(
                    on_event, "stage_failed", stage="hash",
                    processed=processed, total=len(selected),
                    not_processed=int(group["not_processed"]),
                    tool="powershell",
                )
                return {
                    "state": "failed", "mode": options.hash_mode,
                    "reason": "PowerShell 工具无法启动，哈希核验已熔断",
                    "selected": len(selected), "processed": processed,
                    "checked": checked, "matched": matched,
                    "unverifiable": unverifiable,
                    "not_processed": int(group["not_processed"]),
                    "counts": _section_counts(all_problems),
                    "problems": all_problems,
                    "problem_files": _problem_file_count(all_problems),
                    "performance": performance,
                    "tools": used_tools,
                    "tool_circuit": snapshot_failure.as_dict(),
                }, "failed", used_tools
            if outcome.outcome == "paused":
                if _wait_worker_pause(control, "hash", on_event) == "running":
                    continue
                return {
                    "state": "stopped", "mode": options.hash_mode,
                    "selected": len(selected), "processed": processed,
                    "checked": checked, "matched": matched,
                    "unverifiable": unverifiable,
                    "counts": _section_counts(combined_problems()),
                    "problems": combined_problems(),
                    "problem_files": _problem_file_count(combined_problems()),
                    "performance": performance,
                    "tools": used_tools,
                }, "stopped", used_tools
            if outcome.outcome in ("stopped", "save_exit"):
                control.finish()
                _emit(on_event, "run_stopped", stage="hash", state="stopped")
                return {
                    "state": "stopped", "mode": options.hash_mode,
                    "selected": len(selected), "processed": processed,
                    "checked": checked, "matched": matched,
                    "unverifiable": unverifiable,
                    "counts": _section_counts(problems),
                    "problems": problems, "performance": performance,
                    "tools": used_tools,
                }, "stopped", used_tools

            after, after_error = _stat_file(entry.physical_path)
            checked += 1
            performance.append({
                "path": entry.logical_path,
                **outcome.performance(),
                "threshold_count": outcome.threshold_count,
                "worker_reaped": outcome.worker_reaped,
            })
            if after is None:
                circuit.record_success("powershell")
                problems.append(_issue_row(entry, "missing", after_error))
            elif not _same_stat(before, after) or not _matches_baseline(
                    entry, after):
                circuit.record_success("powershell")
                problems.append(_issue_row(
                    entry, "unstable",
                    "哈希读取前后 size／mtime 不稳定"))
            elif getattr(outcome, "failure_kind", None):
                failure = _runtime_failure(
                    tool="powershell",
                    operation="independent_hash",
                    failure_kind=str(outcome.failure_kind),
                    detail=outcome.error,
                    pid=outcome.worker_pid,
                    returncode=outcome.worker_exitcode,
                )
                group = _record_tool_failure_group(
                    tool_failure_groups, failure, entry)
                snapshot_failure = circuit.record_failure(
                    entry.entry_id, failure)
                if snapshot_failure.opened:
                    processed += 1
                    group["not_processed"] = max(
                        0, len(selected) - processed)
                    all_problems = combined_problems()
                    _emit(
                        on_event, "tool_circuit_open", stage="hash",
                        tool="powershell",
                        failure_kind=failure.latest.failure_kind,
                        affected=int(group["affected_files"]),
                        not_processed=int(group["not_processed"]),
                    )
                    _emit(
                        on_event, "stage_failed", stage="hash",
                        processed=processed, total=len(selected),
                        not_processed=int(group["not_processed"]),
                        tool="powershell",
                    )
                    return {
                        "state": "failed", "mode": options.hash_mode,
                        "reason": "PowerShell 连续工具故障，哈希核验已熔断",
                        "selected": len(selected), "processed": processed,
                        "checked": checked, "matched": matched,
                        "unverifiable": unverifiable,
                        "not_processed": int(group["not_processed"]),
                        "counts": _section_counts(all_problems),
                        "problems": all_problems,
                        "problem_files": _problem_file_count(all_problems),
                        "performance": performance,
                        "tools": used_tools,
                        "tool_circuit": snapshot_failure.as_dict(),
                    }, "failed", used_tools
            elif outcome.outcome == "timeout":
                circuit.record_success("powershell")
                problems.append(_issue_row(
                    entry, "timeout", outcome.error))
            elif outcome.outcome != "completed" or not outcome.hash_hex \
                    or outcome.bytes_read != entry.size_bytes \
                    or not outcome.worker_reaped \
                    or outcome.worker_exitcode != 0:
                circuit.record_success("powershell")
                problems.append(_issue_row(
                    entry, "tool_error",
                    outcome.error or "独立哈希进程未完整成功"))
            elif outcome.hash_hex.casefold() != entry.baseline_hash:
                circuit.record_success("powershell")
                problems.append(_issue_row(
                    entry,
                    "mismatched",
                    "独立 SHA-256 与快照基准不同",
                    recorded=entry.baseline_hash,
                    independent=outcome.hash_hex.casefold(),
                ))
            else:
                circuit.record_success("powershell")
                matched += 1
            processed += 1
            break
        progress.send(
            "hash", "hash", processed, len(selected),
            {"matched": matched,
             "problems": _problem_file_count(combined_problems()),
             "unverifiable": unverifiable},
        )
    progress.send(
        "hash", "hash", processed, len(selected),
        {"matched": matched,
         "problems": _problem_file_count(combined_problems()),
         "unverifiable": unverifiable}, force=True)
    all_problems = combined_problems()
    _emit(on_event, "stage_finished", stage="hash", processed=processed,
          matched=matched, problems=_problem_file_count(all_problems),
          unverifiable=unverifiable)
    return {
        "state": "executed",
        "mode": options.hash_mode,
        "sample_percent": (
            options.hash_sample_percent
            if options.hash_mode == "sample" else 100.0),
        "selected": len(selected),
        "baseline_available": len(with_baseline),
        "checked": checked,
        "matched": matched,
        "unverifiable": unverifiable,
        "counts": _section_counts(all_problems),
        "problems": all_problems,
        "problem_files": _problem_file_count(all_problems),
        "performance": performance,
        "tools": used_tools,
    }, "running", used_tools


def _run_format_stage(
    entries: list[_Entry],
    snapshot: dict[str, object],
    options: VerificationOptions,
    control: UnifiedVerificationControl,
    supplied_tools: Mapping[str, str | None],
    on_progress,
    on_event,
    on_threshold,
    tool_resolver,
    format_runner,
) -> tuple[dict[str, object], str, dict[str, dict[str, object]]]:
    if options.format_mode == "off":
        _emit(on_event, "stage_skipped", stage="format", reason="未选择")
        return {
            "state": "NULL",
            "reason": "本次未选择格式校验",
            "mode": "off",
            "coverage_note": FORMAT_COVERAGE_NOTE,
            "problems": [],
        }, "running", {}
    selected = _choose_entries(
        entries,
        options.format_mode,
        options.format_sample_percent,
        # 保持 v1.5.1 check-format 的确定性样本，且与哈希 seed 独立。
        str(snapshot["snapshot_uuid"]) + ":validate",
    )
    _emit(on_event, "stage_started", stage="format")
    progress = _RateEmitter(on_progress, _PROGRESS_INTERVAL)
    current = _RateEmitter(on_event, _CURRENT_ITEM_INTERVAL)
    used_tools: dict[str, dict[str, object]] = {}
    problems: list[dict[str, object]] = []
    valid = 0
    unsupported = 0
    processed = 0
    runner = format_runner or run_format_validator
    circuit = toolruntime.ConsecutiveToolFailureCircuit()
    tool_failure_groups: dict[
        tuple[object, ...], dict[str, object]
    ] = {}

    def combined_problems() -> list[dict[str, object]]:
        return [*problems, *_tool_failure_problem_rows(tool_failure_groups)]

    def record_validator_success(
        spec: legacy.FormatValidatorSpec,
        media_kind: str,
    ) -> None:
        if spec.validator in ("zip", "pdf"):
            circuit.record_success("daisy-format-worker")
        elif spec.validator in ("ole", "7z"):
            circuit.record_success("sevenzip")
        else:
            circuit.record_success("exiftool")
            effective_kind = (
                "image_gif" if spec.validator == "gif" else media_kind)
            if effective_kind in legacy._FFPROBE_KINDS:
                circuit.record_success("ffprobe")

    for entry in selected:
        while True:
            boundary = _settle_boundary(control, "format", on_event)
            if boundary != "running":
                return {
                    "state": "stopped", "mode": options.format_mode,
                    "selected": len(selected), "processed": processed,
                    "valid": valid, "unsupported": unsupported,
                    "counts": _section_counts(combined_problems()),
                    "problems": combined_problems(),
                    "problem_files": _problem_file_count(combined_problems()),
                    "tools": used_tools,
                    "coverage_note": FORMAT_COVERAGE_NOTE,
                }, "stopped", used_tools
            before, before_error = _stat_file(entry.physical_path)
            if before is None:
                problems.append(_issue_row(entry, "missing", before_error))
                processed += 1
                break
            spec = _format_spec(
                entry, supplied_tools, used_tools, on_event, tool_resolver)
            if spec.validator == "none":
                unsupported += 1
                processed += 1
                break
            if options.show_current_file:
                current.send(
                    "format", "current_item", stage="format",
                    item=entry.rel_path)
            try:
                outcome = runner(
                    entry.physical_path,
                    entry.media_kind,
                    spec,
                    used_tools,
                    expected_size=entry.size_bytes,
                    timeout_seconds=options.format_timeout_seconds,
                    default_decision=options.timeout_decision,
                    display_name=entry.rel_path,
                    control=control.worker_control,
                    on_event=on_event,
                    on_threshold=on_threshold,
                )
            except (OSError, RuntimeError, core.PreflightError) as exc:
                tool = (
                    "daisy-format-worker"
                    if spec.validator in ("zip", "pdf") else
                    "sevenzip" if spec.validator in ("ole", "7z") else
                    "external-format-tool"
                )
                failure = _runtime_failure(
                    tool=tool,
                    operation="format_validate",
                    failure_kind="start_failed",
                    detail=f"格式校验工具无法启动：{exc}",
                    pid=None,
                    returncode=None,
                )
                group = _record_tool_failure_group(
                    tool_failure_groups, failure, entry)
                snapshot_failure = circuit.record_failure(
                    entry.entry_id, failure)
                processed += 1
                group["not_processed"] = max(0, len(selected) - processed)
                all_problems = combined_problems()
                _emit(
                    on_event, "tool_circuit_open", stage="format",
                    tool=tool,
                    failure_kind=failure.latest.failure_kind,
                    affected=int(group["affected_files"]),
                    not_processed=int(group["not_processed"]),
                )
                _emit(
                    on_event, "stage_failed", stage="format",
                    processed=processed, total=len(selected),
                    not_processed=int(group["not_processed"]), tool=tool,
                )
                return {
                    "state": "failed", "mode": options.format_mode,
                    "reason": "格式校验工具无法启动，阶段已熔断",
                    "selected": len(selected), "processed": processed,
                    "valid": valid, "unsupported": unsupported,
                    "checked": processed - unsupported,
                    "not_processed": int(group["not_processed"]),
                    "counts": _section_counts(all_problems),
                    "problems": all_problems,
                    "problem_files": _problem_file_count(all_problems),
                    "tools": used_tools,
                    "tool_circuit": snapshot_failure.as_dict(),
                    "coverage_note": FORMAT_COVERAGE_NOTE,
                }, "failed", used_tools
            if outcome.outcome == "paused":
                if _wait_worker_pause(control, "format", on_event) == "running":
                    continue
                return {
                    "state": "stopped", "mode": options.format_mode,
                    "selected": len(selected), "processed": processed,
                    "valid": valid, "unsupported": unsupported,
                    "counts": _section_counts(combined_problems()),
                    "problems": combined_problems(),
                    "problem_files": _problem_file_count(combined_problems()),
                    "tools": used_tools,
                    "coverage_note": FORMAT_COVERAGE_NOTE,
                }, "stopped", used_tools
            if outcome.outcome in ("stopped", "save_exit"):
                control.finish()
                _emit(on_event, "run_stopped", stage="format", state="stopped")
                return {
                    "state": "stopped", "mode": options.format_mode,
                    "selected": len(selected), "processed": processed,
                    "valid": valid, "unsupported": unsupported,
                    "counts": _section_counts(problems),
                    "problems": problems, "tools": used_tools,
                    "coverage_note": FORMAT_COVERAGE_NOTE,
                }, "stopped", used_tools
            after, after_error = _stat_file(entry.physical_path)
            if after is None:
                record_validator_success(spec, entry.media_kind)
                problems.append(_issue_row(entry, "missing", after_error))
            elif not _same_stat(before, after):
                record_validator_success(spec, entry.media_kind)
                problems.append(_issue_row(
                    entry, "unstable",
                    "格式读取前后 size／mtime 不稳定"))
            elif getattr(outcome, "failure_kind", None):
                tool = str(
                    getattr(outcome, "tool", None)
                    or "daisy-format-worker")
                failure = _runtime_failure(
                    tool=tool,
                    operation="format_validate",
                    failure_kind=str(outcome.failure_kind),
                    detail=outcome.detail,
                    pid=getattr(outcome, "worker_pid", None),
                    returncode=getattr(outcome, "worker_exitcode", None),
                )
                group = _record_tool_failure_group(
                    tool_failure_groups, failure, entry)
                snapshot_failure = circuit.record_failure(
                    entry.entry_id, failure)
                if snapshot_failure.opened:
                    processed += 1
                    group["not_processed"] = max(
                        0, len(selected) - processed)
                    all_problems = combined_problems()
                    _emit(
                        on_event, "tool_circuit_open", stage="format",
                        tool=tool,
                        failure_kind=failure.latest.failure_kind,
                        affected=int(group["affected_files"]),
                        not_processed=int(group["not_processed"]),
                    )
                    _emit(
                        on_event, "stage_failed", stage="format",
                        processed=processed, total=len(selected),
                        not_processed=int(group["not_processed"]),
                        tool=tool,
                    )
                    return {
                        "state": "failed", "mode": options.format_mode,
                        "reason": f"{tool} 连续工具故障，格式校验已熔断",
                        "selected": len(selected), "processed": processed,
                        "valid": valid, "unsupported": unsupported,
                        "checked": processed - unsupported,
                        "not_processed": int(group["not_processed"]),
                        "counts": _section_counts(all_problems),
                        "problems": all_problems,
                        "problem_files": _problem_file_count(all_problems),
                        "tools": used_tools,
                        "tool_circuit": snapshot_failure.as_dict(),
                        "coverage_note": FORMAT_COVERAGE_NOTE,
                    }, "failed", used_tools
            elif outcome.status == "unsupported":
                record_validator_success(spec, entry.media_kind)
                unsupported += 1
            elif outcome.status == "valid" and outcome.outcome == "completed" \
                    and outcome.worker_reaped and outcome.worker_exitcode == 0:
                record_validator_success(spec, entry.media_kind)
                valid += 1
            else:
                record_validator_success(spec, entry.media_kind)
                status = str(outcome.status or "error")
                if status not in (
                        "invalid", "timeout", "error", "missing", "unstable"):
                    status = "error"
                problems.append(_issue_row(entry, status, outcome.detail,
                    validator=spec.validator,
                    tool_name=spec.tool_name,
                    tool_version=spec.tool_version,
                ))
            processed += 1
            break
        progress.send(
            "format", "format", processed, len(selected),
            {"valid": valid, "unsupported": unsupported,
             "problems": _problem_file_count(combined_problems())},
        )
    progress.send(
        "format", "format", processed, len(selected),
        {"valid": valid, "unsupported": unsupported,
         "problems": _problem_file_count(combined_problems())}, force=True)
    all_problems = combined_problems()
    _emit(on_event, "stage_finished", stage="format", processed=processed,
          valid=valid, unsupported=unsupported,
          problems=_problem_file_count(all_problems))
    return {
        "state": "executed",
        "mode": options.format_mode,
        "sample_percent": (
            options.format_sample_percent
            if options.format_mode == "sample" else 100.0),
        "selected": len(selected),
        "checked": processed - unsupported,
        "valid": valid,
        "unsupported": unsupported,
        "counts": _section_counts(all_problems),
        "problems": all_problems,
        "problem_files": _problem_file_count(all_problems),
        "tools": used_tools,
        "coverage_note": FORMAT_COVERAGE_NOTE,
    }, "running", used_tools


def _raw_capability_payload(probe=None) -> dict[str, object]:
    """在读取快照／源文件前完成隔离能力探测并收窄为报告字段。"""
    result = (
        envcap.probe_rawpy_capability()
        if probe is None else probe()
    )
    if isinstance(result, envcap.RuntimeCapability):
        payload = result.as_dict()
    elif isinstance(result, Mapping):
        payload = dict(result)
    else:
        raise core.PreflightError("RAW 能力探测未返回结构化结果")
    state = str(payload.get("state") or "")
    reason = str(payload.get("reason") or "").strip()
    version = str(payload.get("version") or "").strip()
    if state != "available":
        raise core.PreflightError(
            "RAW 深度校验不可用："
            + (reason or f"能力状态为 {state or 'unknown'}"))
    if not version:
        raise core.PreflightError("RAW 深度校验能力缺少 rawpy 版本")
    details = payload.get("details")
    if not isinstance(details, Mapping):
        details = {}
    if not bool(payload.get("isolated")):
        raise core.PreflightError("RAW 深度校验能力不是隔离探测结果")
    if details.get("worker_reaped") is not True:
        raise core.PreflightError("RAW 深度校验能力探测子进程未确认回收")
    return {
        "id": str(payload.get("id") or envcap.RAW_CAPABILITY_ID),
        "title": str(payload.get("title") or envcap.RAW_CAPABILITY_TITLE),
        "state": "available",
        "version": version,
        "provider": str(payload.get("provider") or "rawpy/LibRaw"),
        "isolated": True,
        "details": dict(details),
    }


def _run_raw_stage(
    entries: list[_Entry],
    initial_stats: dict[int, _FileStat],
    snapshot: dict[str, object],
    options: VerificationOptions,
    control: UnifiedVerificationControl,
    capability: Mapping[str, object] | None,
    on_progress,
    on_event,
    on_threshold,
    raw_runner,
) -> tuple[dict[str, object], str]:
    if not options.raw_deep_validation:
        _emit(on_event, "stage_skipped", stage="raw", reason="未选择")
        return {
            "state": "NULL",
            "reason": "本次未选择 RAW 深度校验",
            "problems": [],
        }, "running"
    if capability is None:
        raise core.PreflightError("RAW 深度校验缺少已通过的隔离能力")

    format_selected = _choose_entries(
        entries,
        options.format_mode,
        options.format_sample_percent,
        str(snapshot["snapshot_uuid"]) + ":validate",
    )
    candidate_total = sum(
        1 for entry in entries if dbraw.is_raw_candidate(entry.extension))
    selected = [
        entry for entry in format_selected
        if dbraw.is_raw_candidate(entry.extension)
    ]
    _emit(
        on_event,
        "stage_started",
        stage="raw",
        candidate_total=candidate_total,
        selected=len(selected),
    )
    progress = _RateEmitter(on_progress, _PROGRESS_INTERVAL)
    current = _RateEmitter(on_event, _CURRENT_ITEM_INTERVAL)
    runner = raw_runner or dbraw.run_raw_decode_worker
    problems: list[dict[str, object]] = []
    valid = 0
    unsupported = 0
    unverifiable = 0
    processed = 0
    decoded_pixels = 0
    decoded_bytes = 0
    circuit = toolruntime.ConsecutiveToolFailureCircuit()
    tool_failure_groups: dict[
        tuple[object, ...], dict[str, object]
    ] = {}
    stage_reason: str | None = None
    not_processed = 0
    circuit_snapshot: dict[str, object] | None = None

    def combined_problems() -> list[dict[str, object]]:
        return [*problems, *_tool_failure_problem_rows(tool_failure_groups)]

    def section(section_state: str) -> dict[str, object]:
        all_problems = combined_problems()
        payload: dict[str, object] = {
            "state": section_state,
            "format_mode": options.format_mode,
            "format_sample_percent": (
                options.format_sample_percent
                if options.format_mode == "sample" else 100.0),
            "raw_candidate_total": candidate_total,
            "selected": len(selected),
            "processed": processed,
            "checked": processed - unverifiable,
            "valid": valid,
            "unsupported": unsupported,
            "unverifiable": unverifiable,
            "counts": _section_counts(all_problems),
            "problems": all_problems,
            "problem_files": _problem_file_count(all_problems),
            "decoded_pixels": decoded_pixels,
            "decoded_bytes": decoded_bytes,
            "capability": dict(capability),
            "coverage_note": (
                "RAW 范围继承本次格式校验选择；sample 不代表全部 RAW。"
            ),
        }
        if stage_reason is not None:
            payload["reason"] = stage_reason
        if not_processed:
            payload["not_processed"] = not_processed
        if circuit_snapshot is not None:
            payload["tool_circuit"] = circuit_snapshot
        return payload

    for entry in selected:
        while True:
            boundary = _settle_boundary(control, "raw", on_event)
            if boundary != "running":
                return section("stopped"), "stopped"
            initial = initial_stats.get(entry.entry_id)
            if initial is None or not _matches_baseline(entry, initial):
                unverifiable += 1
                processed += 1
                break
            before, _before_error = _stat_file(entry.physical_path)
            if before is None or not _matches_baseline(entry, before):
                unverifiable += 1
                processed += 1
                break
            if options.show_current_file:
                current.send(
                    "raw", "current_item", stage="raw", item=entry.rel_path)

            def raw_event(event: str, **payload: object) -> None:
                _emit(on_event, event, stage="raw", **payload)

            try:
                outcome = runner(
                    entry.physical_path,
                    expected_size=entry.size_bytes,
                    timeout_seconds=options.raw_timeout_seconds,
                    default_decision=options.timeout_decision,
                    display_name=entry.rel_path,
                    control=control.worker_control,
                    on_event=raw_event,
                    on_threshold=on_threshold,
                )
            except (OSError, RuntimeError, core.PreflightError) as exc:
                failure = _runtime_failure(
                    tool="rawpy/LibRaw",
                    operation="raw_decode",
                    failure_kind="worker_start_failed",
                    detail=f"RAW worker 无法启动：{exc}",
                    pid=None,
                    returncode=None,
                )
                group = _record_tool_failure_group(
                    tool_failure_groups, failure, entry)
                opened = circuit.record_failure(entry.entry_id, failure)
                processed += 1
                not_processed = max(0, len(selected) - processed)
                group["not_processed"] = not_processed
                stage_reason = "RAW worker 无法启动，深度校验已熔断"
                circuit_snapshot = opened.as_dict()
                _emit(
                    on_event, "tool_circuit_open", stage="raw",
                    tool="rawpy/LibRaw",
                    failure_kind=failure.latest.failure_kind,
                    affected=int(group["affected_files"]),
                    not_processed=not_processed,
                )
                _emit(
                    on_event, "stage_failed", stage="raw",
                    processed=processed, total=len(selected),
                    not_processed=not_processed, tool="rawpy/LibRaw",
                )
                return section("failed"), "failed"
            if outcome.outcome == "paused":
                if _wait_worker_pause(control, "raw", on_event) == "running":
                    continue
                return section("stopped"), "stopped"
            if outcome.outcome == "stopped":
                control.finish()
                _emit(on_event, "run_stopped", stage="raw", state="stopped")
                return section("stopped"), "stopped"

            after, after_error = _stat_file(entry.physical_path)
            if after is None:
                circuit.record_success("rawpy/LibRaw")
                problems.append(_issue_row(
                    entry,
                    "error",
                    after_error,
                    code="raw_post_stat_missing",
                ))
            elif not _same_stat(before, after) \
                    or not _matches_baseline(entry, after):
                circuit.record_success("rawpy/LibRaw")
                problems.append(_issue_row(
                    entry,
                    "error",
                    "RAW 解码前后 size／mtime 不稳定",
                    code="raw_unstable",
                ))
            elif getattr(outcome, "failure_kind", None):
                failure = _runtime_failure(
                    tool="rawpy/LibRaw",
                    operation="raw_decode",
                    failure_kind=str(outcome.failure_kind),
                    detail=outcome.detail,
                    pid=outcome.worker_pid,
                    returncode=outcome.worker_exitcode,
                )
                group = _record_tool_failure_group(
                    tool_failure_groups, failure, entry)
                opened = circuit.record_failure(entry.entry_id, failure)
                if opened.opened:
                    processed += 1
                    not_processed = max(0, len(selected) - processed)
                    group["not_processed"] = not_processed
                    stage_reason = (
                        "rawpy／LibRaw 连续工具故障，深度校验已熔断")
                    circuit_snapshot = opened.as_dict()
                    _emit(
                        on_event, "tool_circuit_open", stage="raw",
                        tool="rawpy/LibRaw",
                        failure_kind=failure.latest.failure_kind,
                        affected=int(group["affected_files"]),
                        not_processed=not_processed,
                    )
                    _emit(
                        on_event, "stage_failed", stage="raw",
                        processed=processed, total=len(selected),
                        not_processed=not_processed, tool="rawpy/LibRaw",
                    )
                    return section("failed"), "failed"
            elif outcome.succeeded:
                circuit.record_success("rawpy/LibRaw")
                valid += 1
                decoded_pixels += int(outcome.pixel_count or 0)
                decoded_bytes += int(outcome.decoded_bytes or 0)
            elif outcome.outcome == "completed" \
                    and outcome.status == "unsupported" \
                    and outcome.worker_reaped \
                    and outcome.worker_exitcode == 0:
                circuit.record_success("rawpy/LibRaw")
                unsupported += 1
            else:
                circuit.record_success("rawpy/LibRaw")
                status = str(outcome.status or "error")
                if status not in ("invalid", "timeout", "error"):
                    status = "error"
                problems.append(_issue_row(
                    entry,
                    status,
                    outcome.detail,
                    code=outcome.code,
                    rawpy_version=outcome.rawpy_version,
                    libraw_version=outcome.libraw_version,
                    worker_exitcode=outcome.worker_exitcode,
                    worker_reaped=outcome.worker_reaped,
                ))
            processed += 1
            break
        progress.send(
            "raw",
            "raw",
            processed,
            len(selected),
            {
                "valid": valid,
                "unsupported": unsupported,
                "unverifiable": unverifiable,
                "problems": _problem_file_count(combined_problems()),
            },
        )
    progress.send(
        "raw",
        "raw",
        processed,
        len(selected),
        {
            "valid": valid,
            "unsupported": unsupported,
            "unverifiable": unverifiable,
            "problems": _problem_file_count(combined_problems()),
        },
        force=True,
    )
    _emit(
        on_event,
        "stage_finished",
        stage="raw",
        processed=processed,
        valid=valid,
        unsupported=unsupported,
        unverifiable=unverifiable,
        problems=_problem_file_count(combined_problems()),
    )
    return section("executed"), "running"


def _conclusion(report: dict[str, object]) -> str:
    if report["run_state"] == "failed":
        return "failed"
    if report["run_state"] != "complete":
        return "stopped"
    stat = report["sections"]["stat"]
    hashed = report["sections"]["hash"]
    formatted = report["sections"]["format"]
    raw = report["sections"]["raw"]
    if stat.get("problems") or hashed.get("problems") \
            or formatted.get("problems") or raw.get("problems"):
        return "issues_found"
    if hashed.get("state") == "unavailable" \
            or int(hashed.get("unverifiable") or 0) > 0:
        return "incomplete"
    return "passed"


def run_unified_verification(
    snapshot_path: str,
    root_specs: list[str],
    *,
    options: VerificationOptions | None = None,
    force: bool = False,
    tools: Mapping[str, str | None] | None = None,
    control: UnifiedVerificationControl | None = None,
    on_progress: Callable[..., None] | None = None,
    on_event: Callable[..., None] | None = None,
    on_threshold: Callable[..., None] | None = None,
    _powershell_resolver=None,
    _tool_resolver=None,
    _hash_runner=None,
    _format_runner=None,
    _raw_capability_probe=None,
    _raw_runner=None,
) -> dict[str, object]:
    """执行统一核验并返回报告模型；此函数本身不写报告文件。"""
    selected_options = options or VerificationOptions()
    raw_capability = None
    if selected_options.raw_deep_validation:
        raw_capability = _raw_capability_payload(_raw_capability_probe)
        _emit(
            on_event,
            "runtime_capability_detected",
            capability=raw_capability,
        )
    owned_control = control or UnifiedVerificationControl()
    supplied = dict(tools or {})
    started = time.monotonic()
    checked_at = core.now_utc_iso()
    snapshot, entries, input_identity = _load_entries(
        snapshot_path, root_specs, force=force)
    _emit(
        on_event,
        "database_detected",
        database=snapshot["database"],
        snapshot_uuid=snapshot["snapshot_uuid"],
    )

    stat, initial_stats, state = _run_stat_stage(
        entries, selected_options, owned_control, on_progress, on_event)
    hash_section: dict[str, object] = {
        "state": "NULL", "reason": "任务在内容哈希前停止", "problems": []}
    format_section: dict[str, object] = {
        "state": "NULL", "reason": "任务在格式校验前停止",
        "coverage_note": FORMAT_COVERAGE_NOTE, "problems": []}
    raw_section: dict[str, object] = {
        "state": "NULL",
        "reason": (
            "任务在 RAW 深度校验前停止"
            if selected_options.raw_deep_validation
            else "本次未选择 RAW 深度校验"),
        "problems": [],
    }
    used_tools: dict[str, dict[str, object]] = {}
    if state == "running":
        hash_section, state, hash_tools = _run_hash_stage(
            entries,
            initial_stats,
            snapshot,
            selected_options,
            owned_control,
            supplied,
            on_progress,
            on_event,
            on_threshold,
            _powershell_resolver,
            _hash_runner,
        )
        used_tools.update(hash_tools)
    if state == "running":
        format_section, state, format_tools = _run_format_stage(
            entries,
            snapshot,
            selected_options,
            owned_control,
            supplied,
            on_progress,
            on_event,
            on_threshold,
            _tool_resolver,
            _format_runner,
        )
        used_tools.update(format_tools)
    if state == "running":
        raw_section, state = _run_raw_stage(
            entries,
            initial_stats,
            snapshot,
            selected_options,
            owned_control,
            raw_capability,
            on_progress,
            on_event,
            on_threshold,
            _raw_runner,
        )

    after = _file_identity(os.path.abspath(snapshot_path))
    if after != input_identity:
        owned_control.finish()
        raise core.PreflightError("核验期间输入快照发生变化，拒绝发布报告")
    report: dict[str, object] = {
        "contract": VERIFICATION_CONTRACT,
        "report_metadata": core.report_metadata("DBS-30 统一核验"),
        "run_state": {
            "running": "complete",
            "stopped": "stopped",
            "failed": "failed",
        }.get(state, "failed"),
        "snapshot": snapshot,
        "input_identity": input_identity,
        "input_unchanged": True,
        "options": selected_options.as_dict(),
        "checked_at_utc": checked_at,
        "elapsed_s": round(time.monotonic() - started, 3),
        "sections": {
            "stat": stat,
            "hash": hash_section,
            "format": format_section,
            "raw": raw_section,
        },
        "tools": used_tools,
    }
    report["conclusion"] = _conclusion(report)
    report["ok"] = report["conclusion"] == "passed"
    owned_control.finish()
    _emit(on_event, "run_result", state=report["run_state"],
          conclusion=report["conclusion"])
    return report


def _module_state_text(section: Mapping[str, object]) -> str:
    state = str(section.get("state") or "NULL")
    if state == "executed":
        return "已执行"
    if state == "unavailable":
        return "NULL（快照未记录可用基准）"
    if state == "stopped":
        return "未完成（任务已停止）"
    if state == "failed":
        return "失败（工具故障熔断）"
    return "NULL（本次未执行）"


def _problem_count(section: Mapping[str, object]) -> str:
    if section.get("state") not in ("executed", "stopped", "failed"):
        return "NULL"
    explicit = section.get("problem_files")
    return str(
        int(explicit)
        if isinstance(explicit, int) and not isinstance(explicit, bool)
        else len(section.get("problems") or []))


def _append_problem_rows(
    lines: list[str],
    rows: list[dict[str, object]],
) -> None:
    if not rows:
        lines.extend(["", "- 未发现需要处理的问题。"])
        return
    lines.extend(["", "### 需要处理", ""])
    for row in rows[:_ISSUE_ROW_LIMIT]:
        detail = str(row.get("detail") or "").strip()
        lines.append(
            f"- [{row.get('status')}] `"
            f"{core.markdown_cell(row.get('path'))}`"
            + (f"：{core.markdown_cell(detail)}" if detail else ""))
    if len(rows) > _ISSUE_ROW_LIMIT:
        lines.append(
            f"- …仅展示前 {_ISSUE_ROW_LIMIT}／{len(rows)} 条；"
            "完整证据见同名 JSON。")


def render_verification_markdown(report: Mapping[str, object]) -> str:
    """渲染给人阅读的统一核验主报告；未知格式不展开路径。"""
    snapshot = report["snapshot"]
    database = snapshot["database"]
    sections = report["sections"]
    stat = sections["stat"]
    hashed = sections["hash"]
    formatted = sections["format"]
    raw = sections["raw"]
    conclusion_text = {
        "passed": "在本次覆盖口径内未发现问题。",
        "issues_found": "发现需要处理或复核的问题。",
        "incomplete": "未发现确定性问题，但部分内容没有可用哈希基准，不能宣称完整一致。",
        "stopped": "任务已停止；以下仅代表停止前已完成的范围。",
        "failed": "外部工具连续故障，核验已熔断；未处理范围不作结论。",
    }.get(str(report.get("conclusion")), "结论不可用。")
    lines = [
        "# DAISY 统一核验报告",
        "",
        *core.report_markdown_lines("DBS-30 统一核验"),
        "",
        f"- 结论：**{conclusion_text}**",
        f"- 快照：`{core.markdown_cell(snapshot['filename'])}`",
        f"- 快照 UUID：`{core.markdown_cell(snapshot['snapshot_uuid'])}`",
        f"- 数据库：schema {database['schema_version']}；"
        f"生成器 {database.get('source_version') or '未知'}；只读输入未变化",
        f"- 核验时间：`{report['checked_at_utc']}`；用时 {report['elapsed_s']}s",
        "",
        "## 板块状态",
        "",
        "| 板块 | 执行状态 | 问题文件 | 覆盖 |",
        "| --- | --- | ---: | --- |",
        f"| 文件状态 | {_module_state_text(stat)} | {_problem_count(stat)} | "
        f"{stat.get('checked', 0)}/{stat.get('total', 0)} |",
        f"| 内容哈希 | {_module_state_text(hashed)} | {_problem_count(hashed)} | "
        f"核对 {hashed.get('checked', 0)}；不可核验 {hashed.get('unverifiable', 0)} |",
        f"| 格式校验 | {_module_state_text(formatted)} | {_problem_count(formatted)} | "
        f"核对 {formatted.get('checked', 0)}；不支持 {formatted.get('unsupported', 0)} |",
        f"| RAW 深检 | {_module_state_text(raw)} | {_problem_count(raw)} | "
        f"选中 {raw.get('selected', 0)}；不支持 {raw.get('unsupported', 0)} |",
        "",
        "## 文件状态",
        "",
        f"- 执行状态：{_module_state_text(stat)}",
        f"- 问题文件：{_problem_count(stat)}",
    ]
    _append_problem_rows(lines, list(stat.get("problems") or []))
    lines.extend([
        "",
        "## 哈希问题",
        "",
        f"- 执行状态：{_module_state_text(hashed)}",
        f"- 问题文件：{_problem_count(hashed)}",
        f"- 不可核验：{hashed.get('unverifiable', 'NULL')}",
    ])
    if hashed.get("reason"):
        lines.append(f"- 原因：{core.markdown_cell(hashed['reason'])}")
    _append_problem_rows(lines, list(hashed.get("problems") or []))
    lines.extend([
        "",
        "## 格式校验问题",
        "",
        f"- 执行状态：{_module_state_text(formatted)}",
        f"- 问题文件：{_problem_count(formatted)}",
        f"- 未识别／不支持格式：{formatted.get('unsupported', 'NULL')}"
        "（只记录总数，不展开文件名）",
        f"- 覆盖边界：{FORMAT_COVERAGE_NOTE}",
    ])
    if formatted.get("reason"):
        lines.append(f"- 原因：{core.markdown_cell(formatted['reason'])}")
    _append_problem_rows(lines, list(formatted.get("problems") or []))
    lines.extend([
        "",
        "## RAW 深度校验问题",
        "",
        f"- 执行状态：{_module_state_text(raw)}",
        f"- 问题文件：{_problem_count(raw)}",
        f"- RAW 候选：{raw.get('raw_candidate_total', 'NULL')}；"
        f"选中：{raw.get('selected', 'NULL')}；"
        f"不可核验：{raw.get('unverifiable', 'NULL')}",
        f"- 不支持 RAW：{raw.get('unsupported', 'NULL')}"
        "（只记录总数，不展开文件名）",
    ])
    if raw.get("capability"):
        lines.append(
            "- 解码能力：rawpy "
            f"{core.markdown_cell(raw['capability'].get('version'))}；"
            "隔离子进程")
    if raw.get("coverage_note"):
        lines.append(
            f"- 覆盖边界：{core.markdown_cell(raw['coverage_note'])}")
    if raw.get("reason"):
        lines.append(f"- 原因：{core.markdown_cell(raw['reason'])}")
    _append_problem_rows(lines, list(raw.get("problems") or []))
    lines.extend([
        "",
        "## 技术证据",
        "",
        "逐文件哈希值、完整问题字段、RAW worker 回收状态、工具版本和"
        "读取性能摘要位于同名 JSON；Markdown 是证据提炼，不是新的事实来源。",
        "",
    ])
    return "\n".join(lines)


def _write_staging(path: str, payload: bytes) -> None:
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _publish_staged_pair(
    staged: tuple[str, str],
    final: tuple[str, str],
) -> None:
    created: list[str] = []
    try:
        for source, target in zip(staged, final):
            try:
                os.link(source, target)
            except FileExistsError as exc:
                raise core.PreflightError(
                    f"报告发布冲突，既有文件未覆盖：{target}") from exc
            except OSError as exc:
                raise core.PreflightError(
                    f"报告无法以 no-clobber 原子链接发布：{target}：{exc}") \
                    from exc
            created.append(target)
        for source in staged:
            os.remove(source)
    except Exception:
        for target in created:
            try:
                os.remove(target)
            except OSError:
                pass
        raise


def publish_verification_report(
    report: Mapping[str, object],
    output_dir: str | None = None,
) -> VerificationPublication:
    """在同一输出目录内 staging、验证并 no-clobber 发布 JSON＋Markdown。"""
    directory = os.path.abspath(output_dir or "Output/Reports")
    os.makedirs(directory, exist_ok=True)
    labels = list(report["snapshot"]["root_labels"])
    stem = core.snapshot_working_name(
        core.snapshot_name(labels, "Verify"))
    base = os.path.join(directory, stem)
    final_json = base + ".json"
    final_markdown = base + ".md"
    token = uuid.uuid4().hex
    staged_json = os.path.join(directory, f".{stem}.{token}.partial.json")
    staged_markdown = os.path.join(
        directory, f".{stem}.{token}.partial.md")
    json_bytes = (
        json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    markdown_bytes = render_verification_markdown(report).encode("utf-8")
    _write_staging(staged_json, json_bytes)
    try:
        _write_staging(staged_markdown, markdown_bytes)
        with open(staged_json, "r", encoding="utf-8") as handle:
            verified = json.load(handle)
        if verified.get("contract") != VERIFICATION_CONTRACT:
            raise core.PreflightError("统一核验 JSON staging 契约验证失败")
        with open(staged_markdown, "r", encoding="utf-8", newline="") \
                as handle:
            markdown = handle.read()
        if not markdown.startswith("# DAISY 统一核验报告\n") \
                or "\r" in markdown:
            raise core.PreflightError("统一核验 Markdown staging 验证失败")
        _publish_staged_pair(
            (staged_json, staged_markdown),
            (final_json, final_markdown),
        )
    except Exception:
        for path in (staged_json, staged_markdown):
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
        raise
    return VerificationPublication(final_json, final_markdown)
