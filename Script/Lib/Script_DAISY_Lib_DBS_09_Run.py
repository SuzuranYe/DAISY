"""DAISY schema 4 运行文件、控制协议、阶段停点与 lease 生命周期。

这里封装 partial 的独占创建和明确恢复，以及扫描阶段的控制边界。所有 lease
和 worker 操作只使用调用方给出的精确路径、lease ID 与进程句柄，不枚举或
终止其它进程。
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import queue
import sqlite3
import threading
import time
from typing import Callable
import uuid

import Script_DAISY_Lib_DBS_01_Core as core
import Script_DAISY_Lib_DBS_02_Meta as dbmeta
import Script_DAISY_Lib_DBS_03_Hash as dbhash
import Script_DAISY_Lib_DBS_08_State as dbstate


LEASE_SUFFIX = ".lease"
CONTROL_PROTOCOL = "daisy-control-v1"
CONTROL_MAX_LINE_BYTES = 4096
CONTROL_ACTIONS = frozenset((
    "pause", "continue", "save_exit", "stop", "timeout_decision",
))


@dataclass(frozen=True)
class ResumePreview:
    partial_path: str
    lease_path: str
    run_state: str
    resume_hint: str
    current_stage: str
    active_session_id: str
    active_session_ended: bool
    lease_classification: str
    roots: tuple[tuple[str, str], ...]
    config: dict[str, object]
    tools: dict[str, object]


@dataclass
class RunHandle:
    connection: sqlite3.Connection
    partial_path: str
    lease_path: str
    lease: dbstate.LeaseRecord
    resumed: bool


@dataclass(frozen=True)
class ControlCommand:
    sequence: int
    action: str
    worker_pid: int | None = None
    decision: str | None = None
    request_id: str | None = None

    def as_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "protocol": CONTROL_PROTOCOL,
            "sequence": self.sequence,
            "action": self.action,
        }
        if self.worker_pid is not None:
            payload["worker_pid"] = self.worker_pid
        if self.decision is not None:
            payload["decision"] = self.decision
        if self.request_id is not None:
            payload["request_id"] = self.request_id
        return payload


@dataclass(frozen=True)
class ControlRejection:
    code: str
    detail: str


@dataclass(frozen=True)
class ControlReceipt:
    sequence: int
    action: str
    accepted: bool
    reason: str


def _normalized(path: str) -> str:
    return os.path.normpath(os.path.abspath(os.fspath(path)))


def _positive_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{label} 必须是正整数")
    return value


def decode_control_line(line: str | bytes) -> ControlCommand:
    """严格解析一行 GUI→任务控制消息，不接受未知协议或多行内容。"""
    if isinstance(line, bytes):
        if len(line) > CONTROL_MAX_LINE_BYTES:
            raise ValueError("控制消息超过长度上限")
        try:
            text = line.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ValueError("控制消息不是 UTF-8") from exc
    else:
        text = str(line)
        if len(text.encode("utf-8")) > CONTROL_MAX_LINE_BYTES:
            raise ValueError("控制消息超过长度上限")
    if text.endswith("\r\n"):
        text = text[:-2]
    elif text.endswith("\n"):
        text = text[:-1]
    if not text or "\n" in text or "\r" in text or "\x00" in text:
        raise ValueError("控制消息必须是单行非空 JSON")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("控制消息不是有效 JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("控制消息必须是 JSON 对象")
    if payload.get("protocol") != CONTROL_PROTOCOL:
        raise ValueError("控制协议不兼容")
    sequence = _positive_int(payload.get("sequence"), "sequence")
    action = payload.get("action")
    if action not in CONTROL_ACTIONS:
        raise ValueError(f"未知控制动作：{action}")
    request_id = payload.get("request_id")
    if request_id is not None:
        if not isinstance(request_id, str) or not (1 <= len(request_id) <= 128):
            raise ValueError("request_id 必须是 1～128 字符字符串")

    worker_pid = payload.get("worker_pid")
    decision = payload.get("decision")
    if action == "timeout_decision":
        worker_pid = _positive_int(worker_pid, "worker_pid")
        if decision not in (
            "continue_waiting", "skip_and_record", "stop_and_resume",
        ):
            raise ValueError(f"未知 timeout 决策：{decision}")
    elif worker_pid is not None or decision is not None:
        raise ValueError("只有 timeout_decision 可以携带 worker_pid/decision")
    return ControlCommand(
        sequence=sequence,
        action=str(action),
        worker_pid=worker_pid,
        decision=decision,
        request_id=request_id,
    )


def encode_control_command(command: ControlCommand) -> bytes:
    """编码为单行 UTF-8 JSONL，并复用严格解析器自校验。"""
    encoded = (
        json.dumps(
            command.as_payload(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ) + "\n"
    ).encode("utf-8")
    decode_control_line(encoded)
    return encoded


class ControlInbox:
    """后台读取一个已知输入流；不关闭、不替换调用方的 stdin。"""

    def __init__(
        self,
        stream,
        *,
        max_queue: int = 64,
        on_command: Callable[[ControlCommand], None] | None = None,
        on_rejected: Callable[[ControlRejection], None] | None = None,
    ) -> None:
        if max_queue <= 0:
            raise ValueError("控制队列上限必须大于 0")
        self._stream = stream
        self._queue: queue.Queue[ControlCommand] = queue.Queue(max_queue)
        self._on_command = on_command
        self._on_rejected = on_rejected
        self._last_sequence = 0
        self._stop = threading.Event()
        self._eof = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def eof(self) -> bool:
        return self._eof.is_set()

    def _reject(self, code: str, detail: str) -> None:
        if self._on_rejected is not None:
            try:
                self._on_rejected(ControlRejection(code, detail))
            except Exception:
                pass

    @staticmethod
    def _has_line_end(value: str | bytes) -> bool:
        return value.endswith(b"\n") if isinstance(value, bytes) \
            else value.endswith("\n")

    def _discard_line_tail(self) -> None:
        while not self._stop.is_set():
            tail = self._stream.readline(CONTROL_MAX_LINE_BYTES + 1)
            if not tail or self._has_line_end(tail):
                return

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                line = self._stream.readline(CONTROL_MAX_LINE_BYTES + 1)
                if not line:
                    return
                if self._stop.is_set():
                    return
                byte_length = (
                    len(line) if isinstance(line, bytes)
                    else len(str(line).encode("utf-8"))
                )
                if byte_length > CONTROL_MAX_LINE_BYTES:
                    if not self._has_line_end(line):
                        self._discard_line_tail()
                    self._reject("line_too_long", "控制消息超过长度上限")
                    continue
                if not self._has_line_end(line):
                    self._reject(
                        "unterminated_line", "控制消息缺少 JSONL 换行边界")
                    return
                try:
                    command = decode_control_line(line)
                except ValueError as exc:
                    self._reject("invalid_message", str(exc))
                    continue
                if command.sequence <= self._last_sequence:
                    self._reject(
                        "stale_sequence",
                        f"sequence={command.sequence} 不大于"
                        f" {self._last_sequence}",
                    )
                    continue
                self._last_sequence = command.sequence
                if self._on_command is not None:
                    try:
                        self._on_command(command)
                    except Exception as exc:
                        self._reject("command_callback_failed", str(exc))
                    continue
                try:
                    self._queue.put_nowait(command)
                except queue.Full:
                    self._reject("queue_full", "控制队列已满")
        except (OSError, ValueError) as exc:
            self._reject("stream_failed", str(exc))
        finally:
            self._eof.set()

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("控制输入线程已启动")
        self._thread = threading.Thread(
            target=self._run,
            name="DAISY-ControlInbox",
            daemon=True,
        )
        self._thread.start()

    def poll(self) -> tuple[ControlCommand, ...]:
        commands = []
        while True:
            try:
                commands.append(self._queue.get_nowait())
            except queue.Empty:
                return tuple(commands)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)


class RunCommandRouter:
    """把已验证命令路由到当前哈希 worker 或 paused 等待点。"""

    def __init__(
        self,
        *,
        on_receipt: Callable[[ControlReceipt], None] | None = None,
    ) -> None:
        self._condition = threading.Condition()
        self._state = "running"
        self._hash_control = dbhash.HashWorkerControl()
        self._paused_action: str | None = None
        self._on_receipt = on_receipt

    @property
    def state(self) -> str:
        with self._condition:
            return self._state

    @property
    def hash_control(self) -> dbhash.HashWorkerControl:
        with self._condition:
            return self._hash_control

    def _emit_receipt(self, receipt: ControlReceipt) -> None:
        if self._on_receipt is not None:
            try:
                self._on_receipt(receipt)
            except Exception:
                pass

    def route(self, command: ControlCommand) -> ControlReceipt:
        with self._condition:
            state = self._state
            if state == "ended":
                accepted, reason = False, "run_ended"
            elif state == "paused":
                if command.action not in ("continue", "save_exit", "stop"):
                    accepted, reason = False, "not_valid_while_paused"
                elif self._paused_action is not None:
                    accepted, reason = False, "paused_action_already_decided"
                else:
                    self._paused_action = command.action
                    self._condition.notify_all()
                    accepted, reason = True, "accepted"
            elif command.action == "continue":
                accepted, reason = False, "not_paused"
            elif command.action == "pause":
                accepted = self._hash_control.request_pause()
                reason = "accepted" if accepted else "action_already_decided"
            elif command.action == "save_exit":
                accepted = self._hash_control.request_save_exit()
                reason = "accepted" if accepted else "action_already_decided"
            elif command.action == "stop":
                accepted = self._hash_control.request_stop()
                reason = "accepted" if accepted else "action_already_decided"
            elif command.action == "timeout_decision":
                assert command.worker_pid is not None
                assert command.decision is not None
                accepted = self._hash_control.request_timeout_decision(
                    command.worker_pid,
                    command.decision,
                )
                reason = (
                    "accepted" if accepted
                    else "worker_or_decision_mismatch"
                )
            else:
                accepted, reason = False, "unsupported_action"
        receipt = ControlReceipt(
            command.sequence, command.action, accepted, reason)
        self._emit_receipt(receipt)
        return receipt

    def enter_paused(self) -> None:
        with self._condition:
            if self._state != "running":
                raise RuntimeError(
                    f"状态 {self._state} 不能进入 paused 等待点")
            self._state = "paused"
            self._paused_action = None

    def wait_paused_action(self, timeout: float | None = None) -> str | None:
        with self._condition:
            if self._state != "paused":
                raise RuntimeError("当前不在 paused 等待点")
            self._condition.wait_for(
                lambda: self._paused_action is not None
                or self._state == "ended",
                timeout,
            )
            return self._paused_action

    def begin_running(self) -> dbhash.HashWorkerControl:
        with self._condition:
            if self._state != "paused":
                raise RuntimeError(
                    f"状态 {self._state} 不能开始新的运行段")
            self._state = "running"
            self._paused_action = None
            self._hash_control = dbhash.HashWorkerControl()
            return self._hash_control

    def end(self) -> None:
        with self._condition:
            self._state = "ended"
            self._condition.notify_all()


def lease_path_for_partial(partial_path: str) -> str:
    partial = _normalized(partial_path)
    if not partial.casefold().endswith(".partial.sqlite"):
        raise core.PreflightError(
            f"schema 4 partial 路径后缀无效：{partial}")
    return partial + LEASE_SUFFIX


def _json_object(value: object, label: str) -> dict[str, object]:
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise core.PreflightError(f"{label} 不是有效 JSON：{exc}") from exc
    if not isinstance(parsed, dict):
        raise core.PreflightError(f"{label} 必须是 JSON 对象")
    return parsed


def _readonly_connection(path: str) -> sqlite3.Connection:
    try:
        uri = Path(path).resolve(strict=True).as_uri() + "?mode=ro"
        return sqlite3.connect(uri, uri=True)
    except (OSError, sqlite3.Error) as exc:
        raise core.PreflightError(
            f"无法只读打开 schema 4 partial：{path}：{exc}") from exc


def _readwrite_connection(path: str) -> sqlite3.Connection:
    try:
        uri = Path(path).resolve(strict=True).as_uri() + "?mode=rw"
        return sqlite3.connect(uri, uri=True)
    except (OSError, sqlite3.Error) as exc:
        raise core.PreflightError(
            f"无法读写打开 schema 4 partial：{path}：{exc}") from exc


def _session_payload(
    con: sqlite3.Connection,
    session_id: str,
) -> tuple[bool, dict[str, object], dict[str, object]]:
    session = con.execute(
        "SELECT ended_at_utc,config_json,tools_json FROM run_sessions"
        " WHERE session_id=?",
        (session_id,),
    ).fetchone()
    if session is None:
        raise core.PreflightError("partial 缺少 active session")
    return (
        session[0] is not None,
        _json_object(session[1], "session config_json"),
        _json_object(session[2], "session tools_json"),
    )


def inspect_resume(
    partial_path: str,
    *,
    now_utc: str | None = None,
    local_host: str | None = None,
    pid_alive: Callable[[int], bool] | None = None,
    process_token: Callable[[int], str | None] | None = None,
) -> ResumePreview:
    """只读检查恢复候选；不创建、接管或刷新 lease。"""
    partial = _normalized(partial_path)
    if not os.path.isfile(partial):
        raise core.PreflightError(f"schema 4 partial 不存在：{partial}")
    lease_path = lease_path_for_partial(partial)
    con = _readonly_connection(partial)
    try:
        runtime = dbstate.load_runtime(con)
        if os.path.normcase(runtime.partial_path) != os.path.normcase(partial):
            raise core.PreflightError(
                "partial 实际路径与数据库冻结身份不一致")
        session_ended, config, tools = _session_payload(
            con, runtime.active_session_id)
        roots = tuple(
            (str(label), str(path))
            for label, path in con.execute(
                "SELECT root_label,root_path FROM roots ORDER BY root_id")
        )
    finally:
        con.close()

    if not os.path.exists(lease_path):
        lease_classification = "missing"
    else:
        try:
            record = dbstate.read_lease_file(lease_path)
        except (OSError, core.PreflightError):
            lease_classification = "invalid"
        else:
            lease_classification = dbstate.classify_lease(
                record,
                now_utc=now_utc or core.now_utc_iso(),
                local_host=local_host,
                pid_alive=pid_alive,
                process_token=process_token,
            )
    return ResumePreview(
        partial_path=partial,
        lease_path=lease_path,
        run_state=runtime.run_state,
        resume_hint=runtime.resume_hint,
        current_stage=runtime.current_stage,
        active_session_id=runtime.active_session_id,
        active_session_ended=session_ended,
        lease_classification=lease_classification,
        roots=roots,
        config=config,
        tools=tools,
    )


def _reserve_partial(path: str) -> None:
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(descriptor)
    except FileExistsError as exc:
        raise core.PreflightError(
            f"partial 已存在且不会覆盖：{path}") from exc


def _remove_owned_sqlite_files(partial_path: str) -> None:
    for path in (partial_path, partial_path + "-wal", partial_path + "-shm"):
        try:
            os.remove(path)
        except OSError:
            pass


def create_run(
    partial_path: str,
    roots: list[tuple[str, str]],
    config: dict[str, object],
    *,
    output_dir: str,
    publish_stem_path: str,
    event_log_path: str | None = None,
    tool_versions: dict[str, object] | None = None,
    scanner_version: str = dbstate.MIN_READER_VERSION,
    snapshot_uuid: str | None = None,
    session_id: str | None = None,
    lease_id: str | None = None,
) -> RunHandle:
    """以 no-clobber 方式创建 schema 4 partial 和同身份 lease。"""
    if not roots:
        raise core.PreflightError("schema 4 至少需要一个 root")
    for _label, root_path in roots:
        core.validate_root(root_path)
    partial = _normalized(partial_path)
    lease_path = lease_path_for_partial(partial)
    active_session = session_id or uuid.uuid4().hex
    active_lease = lease_id or uuid.uuid4().hex
    _reserve_partial(partial)
    lease = None
    con = None
    try:
        lease = dbstate.acquire_lease_file(
            lease_path,
            active_session,
            lease_id=active_lease,
        )
        con = sqlite3.connect(partial)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        runtime = dbstate.initialize_v4_connection(
            con,
            roots,
            config,
            output_dir=output_dir,
            partial_path=partial,
            publish_stem_path=publish_stem_path,
            event_log_path=event_log_path,
            tool_versions=tool_versions,
            scanner_version=scanner_version,
            snapshot_uuid=snapshot_uuid,
            session_id=active_session,
            lease_id=active_lease,
            hostname=lease.host,
            pid=lease.pid,
            process_start_token=lease.process_start_token,
        )
        for root_id, (_label, root_path) in enumerate(roots, 1):
            volume_serial, filesystem = core.volume_info(root_path)
            con.execute(
                "UPDATE roots SET volume_serial=?,filesystem=?"
                " WHERE root_id=?",
                (volume_serial, filesystem, root_id),
            )
        con.commit()
        if runtime.active_session_id != lease.session_id:
            raise core.PreflightError(
                "数据库 session 与 lease session 不一致")
        return RunHandle(con, partial, lease_path, lease, False)
    except Exception:
        if con is not None:
            con.close()
        if lease is not None:
            try:
                dbstate.release_lease_file(lease_path, lease.lease_id)
            except (OSError, core.PreflightError):
                pass
        _remove_owned_sqlite_files(partial)
        raise


def resume_run(
    partial_path: str,
    *,
    manual: bool = False,
    scanner_version: str = dbstate.MIN_READER_VERSION,
    session_id: str | None = None,
    lease_id: str | None = None,
    pid_alive: Callable[[int], bool] | None = None,
    process_token: Callable[[int], str | None] | None = None,
) -> RunHandle:
    """明确接管可恢复 partial，并创建新的 resume session。"""
    preview = inspect_resume(
        partial_path,
        pid_alive=pid_alive,
        process_token=process_token,
    )
    if preview.run_state in ("published", "failed_terminal"):
        raise core.PreflightError(
            f"状态 {preview.run_state} 不能恢复")
    if preview.run_state == "stopped" and not manual:
        raise core.PreflightError("stopped partial 需要用户明确手动恢复")
    next_session = session_id or uuid.uuid4().hex
    next_lease = lease_id or uuid.uuid4().hex
    lease = dbstate.acquire_lease_file(
        preview.lease_path,
        next_session,
        takeover=True,
        lease_id=next_lease,
        pid_alive=pid_alive,
        token_probe=process_token,
    )
    con = None
    try:
        con = _readwrite_connection(preview.partial_path)
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        runtime = dbstate.load_runtime(con)
        if runtime.active_session_id != preview.active_session_id:
            raise core.PreflightError(
                "partial 在恢复预览后更换了 active session，拒绝接管")
        if runtime.run_state in ("published", "failed_terminal"):
            raise core.PreflightError(
                f"状态 {runtime.run_state} 不能恢复")
        if runtime.run_state == "stopped" and not manual:
            raise core.PreflightError(
                "stopped partial 需要用户明确手动恢复")
        session_ended, config, tools = _session_payload(
            con, runtime.active_session_id)
        dbstate.validate_resume_identity(
            con,
            runtime.output_dir,
            runtime.partial_path,
            runtime.publish_stem_path,
            runtime.event_log_path,
        )
        interrupted = runtime.run_state in (
            "running", "pause_requested", "sealing", "sealed_unpublished",
        ) or (runtime.run_state == "paused"
              and not session_ended)
        if interrupted:
            dbstate.recover_interrupted(con)
        runtime = dbstate.start_resume_session(
            con,
            config=config,
            tools=tools,
            manual=manual,
            scanner_version=scanner_version,
            session_id=next_session,
            lease_id=next_lease,
            hostname=lease.host,
            pid=lease.pid,
            process_start_token=lease.process_start_token,
        )
        if runtime.active_session_id != lease.session_id:
            raise core.PreflightError(
                "恢复 session 与 lease session 不一致")
        return RunHandle(
            con,
            preview.partial_path,
            preview.lease_path,
            lease,
            True,
        )
    except Exception:
        if con is not None:
            con.close()
        try:
            dbstate.release_lease_file(
                preview.lease_path, lease.lease_id)
        except (OSError, core.PreflightError):
            pass
        raise


def heartbeat_once(
    partial_path: str,
    lease_path: str,
    lease_id: str,
    *,
    now_utc: str | None = None,
) -> dbstate.LeaseRecord:
    """刷新精确 lease 文件和对应数据库 session。"""
    con = _readwrite_connection(_normalized(partial_path))
    try:
        con.execute("PRAGMA foreign_keys=ON")
        refreshed = dbstate.refresh_lease_file(
            lease_path, lease_id, now_utc=now_utc)
        dbstate.heartbeat_session(con, lease_id, now_utc=now_utc)
    finally:
        con.close()
    return refreshed


class LeaseHeartbeat:
    """仅刷新一个已知 partial/lease；失败后停止并暴露原异常。"""

    def __init__(
        self,
        handle: RunHandle,
        *,
        interval_seconds: float = dbstate.LEASE_HEARTBEAT_SECONDS,
        on_error: Callable[[BaseException], None] | None = None,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("lease heartbeat 间隔必须大于 0")
        self._partial_path = handle.partial_path
        self._lease_path = handle.lease_path
        self._lease_id = handle.lease.lease_id
        self._interval = float(interval_seconds)
        self._on_error = on_error
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._error: BaseException | None = None

    @property
    def error(self) -> BaseException | None:
        return self._error

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("lease heartbeat 已启动")
        heartbeat_once(
            self._partial_path, self._lease_path, self._lease_id)
        self._thread = threading.Thread(
            target=self._run,
            name="DAISY-LeaseHeartbeat",
            daemon=True,
        )
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                heartbeat_once(
                    self._partial_path,
                    self._lease_path,
                    self._lease_id,
                )
            except Exception as exc:
                self._error = exc
                self._stop.set()
                if self._on_error is not None:
                    try:
                        self._on_error(exc)
                    except Exception:
                        pass

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)


def close_handle(handle: RunHandle, *, release_lease: bool) -> None:
    """关闭连接；仅在调用方明确声明时释放匹配的 lease。"""
    handle.connection.close()
    if release_lease:
        dbstate.release_lease_file(
            handle.lease_path, handle.lease.lease_id)


def _emit_control_event(
    on_event: Callable[..., None] | None,
    event: str,
    **payload: object,
) -> None:
    if on_event is not None:
        try:
            on_event(event, **payload)
        except Exception:
            pass


def _wait_after_pause(
    con: sqlite3.Connection,
    stage: str,
    router: RunCommandRouter,
    *,
    on_event: Callable[..., None] | None,
    paused_wait_seconds: float,
) -> str:
    if paused_wait_seconds <= 0:
        raise ValueError("paused 等待轮询间隔必须大于 0")
    runtime = dbstate.load_runtime(con)
    if runtime.run_state != "paused" or runtime.resume_hint != "none":
        raise core.PreflightError(
            "同会话暂停点状态不一致，拒绝继续控制循环")
    router.enter_paused()
    _emit_control_event(
        on_event, "run_paused", stage=stage, state="paused")

    action = None
    while action is None:
        action = router.wait_paused_action(paused_wait_seconds)
        if action is None and router.state == "ended":
            raise core.PreflightError("控制器在 paused 等待期间结束")

    if action == "continue":
        dbstate.continue_running(con)
        router.begin_running()
        dbstate.update_stage_checkpoint(
            con,
            stage,
            "running",
            current_entry_id=None,
            checkpoint={"reason": "continued_after_pause"},
        )
        _emit_control_event(
            on_event, "run_resumed", stage=stage, state="running")
        return "running"
    if action == "save_exit":
        dbstate.save_paused_for_exit(con)
        dbstate.update_stage_checkpoint(
            con,
            stage,
            "paused",
            current_entry_id=None,
            checkpoint={"reason": "save_exit_after_pause"},
        )
        router.end()
        _emit_control_event(
            on_event, "run_saved", stage=stage, state="save_exit")
        return "save_exit"
    if action == "stop":
        dbstate.update_stage_checkpoint(
            con,
            stage,
            "failed_recoverable",
            current_entry_id=None,
            checkpoint={"reason": "user_stop_after_pause"},
        )
        dbstate.stop_run(con, reason="user_stop")
        router.end()
        _emit_control_event(
            on_event, "run_stopped", stage=stage, state="stopped")
        return "stopped"
    raise core.PreflightError(f"paused 等待点收到未知动作：{action}")


def settle_pending_stage_control(
    con: sqlite3.Connection,
    stage: str,
    router: RunCommandRouter,
    *,
    on_event: Callable[..., None] | None = None,
    paused_wait_seconds: float = 0.25,
) -> str:
    """在非哈希阶段的安全边界提交一个待处理生命周期动作。"""
    if stage not in dbstate.STAGES:
        raise ValueError(f"未知阶段：{stage}")
    if router.state != "running":
        raise core.PreflightError(
            f"阶段控制要求 running 控制器，实际为 {router.state}")
    control_action = router.hash_control.current()
    if control_action is None:
        return "running"
    action, _source = control_action
    if action in ("pause", "save_exit"):
        for_exit = action == "save_exit"
        dbstate.request_pause(con, for_exit=for_exit)
        dbstate.update_stage_checkpoint(
            con,
            stage,
            "pause_requested",
            current_entry_id=None,
            checkpoint={"reason": "stage_control"},
        )
        dbstate.mark_paused(con, for_exit=for_exit)
        dbstate.update_stage_checkpoint(
            con,
            stage,
            "paused",
            current_entry_id=None,
            checkpoint={"reason": "stage_control"},
        )
        if for_exit:
            router.end()
            _emit_control_event(
                on_event, "run_saved", stage=stage, state="save_exit")
            return "save_exit"
        return _wait_after_pause(
            con,
            stage,
            router,
            on_event=on_event,
            paused_wait_seconds=paused_wait_seconds,
        )

    dbstate.update_stage_checkpoint(
        con,
        stage,
        "failed_recoverable",
        current_entry_id=None,
        checkpoint={"reason": "stage_stop"},
    )
    dbstate.stop_run(con, reason="user_stop")
    router.end()
    _emit_control_event(
        on_event, "run_stopped", stage=stage, state="stopped")
    return "stopped"


def _run_controlled_boundary_operation(
    con: sqlite3.Connection,
    stage: str,
    router: RunCommandRouter,
    operation: Callable[[Callable[[], bool]], object],
    *,
    on_event: Callable[..., None] | None,
    paused_wait_seconds: float,
) -> tuple[str, object | None]:
    """执行可重跑阶段；收到控制时先落库，再按用户后续动作处理。"""
    while True:
        dbstate.update_stage_checkpoint(
            con, stage, "running", current_entry_id=None)
        boundary = settle_pending_stage_control(
            con,
            stage,
            router,
            on_event=on_event,
            paused_wait_seconds=paused_wait_seconds,
        )
        if boundary != "running":
            return boundary, None
        _emit_control_event(
            on_event, "stage_started", stage=stage)
        try:
            value = operation(
                lambda: router.hash_control.current() is not None)
        except core.StageControlBoundary:
            if router.hash_control.current() is None:
                raise core.PreflightError(
                    f"阶段 {stage} 在没有控制动作时中断")
            boundary = settle_pending_stage_control(
                con,
                stage,
                router,
                on_event=on_event,
                paused_wait_seconds=paused_wait_seconds,
            )
            if boundary == "running":
                _emit_control_event(
                    on_event, "stage_restarted", stage=stage)
                continue
            return boundary, None
        return "completed", value


def run_enumeration_stage_controlled(
    con: sqlite3.Connection,
    router: RunCommandRouter,
    *,
    collect_file_id: bool = True,
    exclude_paths: set | None = None,
    exclude_dirs: set | None = None,
    on_progress: Callable[[dict[str, object]], None] | None = None,
    on_event: Callable[..., None] | None = None,
    paused_wait_seconds: float = 0.25,
    max_files: int | None = None,
) -> dict[str, object]:
    """运行可重跑枚举阶段；旧枚举器只在显式回调下增加停点。"""

    def operation(should_stop: Callable[[], bool]) -> object:
        def progress(stats: dict[str, object]) -> None:
            dbstate.update_stage_checkpoint(
                con,
                "enumerate",
                "running",
                items_done=int(stats["files"]),
                bytes_done=int(stats["bytes"]),
                error_count=int(stats["dir_errors"]),
            )
            if on_progress is not None:
                on_progress(dict(stats))

        return core.enumerate_and_reconcile(
            con,
            collect_file_id=collect_file_id,
            exclude_paths=exclude_paths,
            exclude_dirs=exclude_dirs,
            on_progress=progress,
            max_files=max_files,
            should_stop=should_stop,
        )

    state, value = _run_controlled_boundary_operation(
        con,
        "enumerate",
        router,
        operation,
        on_event=on_event,
        paused_wait_seconds=paused_wait_seconds,
    )
    if state != "completed":
        return {"state": state}
    stats = dict(value)
    stats["state"] = "completed"
    dbstate.update_stage_checkpoint(
        con,
        "enumerate",
        "completed",
        items_done=int(stats["files"]),
        items_total=int(stats["files"]),
        bytes_done=int(stats["bytes"]),
        bytes_total=int(stats["bytes"]),
        error_count=int(stats["dir_errors"]),
        current_entry_id=None,
    )
    _emit_control_event(
        on_event, "stage_finished", stage="enumerate", **stats)
    return stats


def run_metadata_stage_controlled(
    con: sqlite3.Connection,
    tools: dict[str, object],
    router: RunCommandRouter,
    *,
    retain_original_metadata: bool = True,
    timeout_policy: dict[str, object] | None = None,
    show_current_file: bool = False,
    on_progress: Callable[[int, dict[str, object]], None] | None = None,
    on_event: Callable[..., None] | None = None,
    paused_wait_seconds: float = 0.25,
) -> dict[str, object]:
    """运行元数据阶段，并只在单文件处理边界接受生命周期动作。"""
    total_entries = int(con.execute(
        "SELECT COUNT(*) FROM entries"
    ).fetchone()[0])
    last_current_event = 0.0
    last_progress_event = 0.0

    def operation(should_stop: Callable[[], bool]) -> object:
        already_done = int(con.execute(
            "SELECT COUNT(*) FROM entries"
            " WHERE meta_status NOT IN ('pending','processing')"
        ).fetchone()[0])

        def progress(index: int, stats: dict[str, object]) -> None:
            nonlocal last_progress_event
            overall_done = min(total_entries, already_done + int(index))
            now = time.monotonic()
            if now - last_progress_event < 0.5 \
                    and overall_done != total_entries:
                return
            dbstate.update_stage_checkpoint(
                con,
                "metadata",
                "running",
                items_done=overall_done,
                items_total=total_entries,
                error_count=(
                    int(stats["error"]) + int(stats["timeout"])),
            )
            if on_progress is not None:
                current = dict(stats)
                current["processed"] = overall_done
                current["total"] = total_entries
                on_progress(overall_done, current)
            last_progress_event = now

        def current_item(rel_path: str) -> None:
            nonlocal last_current_event
            now = time.monotonic()
            if now - last_current_event >= 0.1:
                _emit_control_event(
                    on_event,
                    "current_item",
                    stage="metadata",
                    item=rel_path,
                )
                last_current_event = now

        return dbmeta.process_metadata_stage(
            con,
            tools,
            retain_original_metadata=retain_original_metadata,
            timeout_policy=timeout_policy,
            on_progress=progress,
            should_stop=should_stop,
            on_current=current_item if show_current_file else None,
        )

    state, value = _run_controlled_boundary_operation(
        con,
        "metadata",
        router,
        operation,
        on_event=on_event,
        paused_wait_seconds=paused_wait_seconds,
    )
    if state != "completed":
        return {"state": state}
    stats = dict(value)
    status_counts = {
        str(status): int(count)
        for status, count in con.execute(
            "SELECT meta_status,COUNT(*) FROM entries GROUP BY meta_status")
    }
    processed = total_entries \
        - status_counts.get("pending", 0) \
        - status_counts.get("processing", 0)
    stats.update({
        "total": total_entries,
        "processed": processed,
        "done": status_counts.get("done", 0),
        "error": status_counts.get("error", 0),
        "timeout": status_counts.get("timeout", 0),
        "unstable": status_counts.get("unstable", 0),
        "not_applicable": status_counts.get("not_applicable", 0),
        "skipped": status_counts.get("skipped", 0),
    })
    stats["state"] = "completed"
    dbstate.update_stage_checkpoint(
        con,
        "metadata",
        "completed",
        items_done=processed,
        items_total=total_entries,
        error_count=int(stats["error"]) + int(stats["timeout"]),
        current_entry_id=None,
    )
    _emit_control_event(
        on_event, "stage_finished", stage="metadata", **stats)
    return stats


def run_rescan_stage_controlled(
    con: sqlite3.Connection,
    router: RunCommandRouter,
    *,
    on_progress: Callable[[int, int, int], None] | None = None,
    on_event: Callable[..., None] | None = None,
    paused_wait_seconds: float = 0.25,
) -> dict[str, object]:
    """运行可重跑复扫阶段，并在条目边界提交暂停／停止。"""
    total = int(con.execute(
        "SELECT COUNT(*) FROM entries WHERE is_placeholder=0"
    ).fetchone()[0])
    last_progress_event = 0.0

    def operation(should_stop: Callable[[], bool]) -> object:
        def progress(done: int, current_total: int, changed: int) -> None:
            nonlocal last_progress_event
            now = time.monotonic()
            if now - last_progress_event < 0.5 and done != current_total:
                return
            dbstate.update_stage_checkpoint(
                con,
                "rescan",
                "running",
                items_done=done,
                items_total=current_total,
                error_count=changed,
            )
            if on_progress is not None:
                on_progress(done, current_total, changed)
            last_progress_event = now

        return core.rescan_check(
            con, should_stop=should_stop, on_progress=progress)

    state, value = _run_controlled_boundary_operation(
        con,
        "rescan",
        router,
        operation,
        on_event=on_event,
        paused_wait_seconds=paused_wait_seconds,
    )
    if state != "completed":
        return {"state": state}
    changed = int(value)
    stats: dict[str, object] = {
        "state": "completed",
        "total": total,
        "changed": changed,
    }
    dbstate.update_stage_checkpoint(
        con,
        "rescan",
        "completed",
        items_done=total,
        items_total=total,
        error_count=changed,
        current_entry_id=None,
    )
    _emit_control_event(
        on_event, "stage_finished", stage="rescan", **stats)
    return stats


def run_hash_stage_controlled(
    con: sqlite3.Connection,
    mode: str,
    router: RunCommandRouter,
    *,
    previous: dbhash.PreviousSnapshot | None = None,
    retry_mode: str = "pending",
    chunk_bytes: int = core.HASH_CHUNK_BYTES,
    stall_seconds: float = dbhash.HASH_STALL_SECONDS,
    timeout_seconds: float | None = None,
    default_decision: str = "continue_waiting",
    show_current_file: bool = False,
    on_progress: Callable[[int, dict[str, object]], None] | None = None,
    on_event: Callable[..., None] | None = None,
    on_threshold: Callable[
        [dict[str, object], dbhash.AtomicTimeoutDecision], None] | None = None,
    poll_seconds: float = 0.05,
    paused_wait_seconds: float = 0.25,
    max_files: int | None = None,
    _worker_target=None,
) -> dict[str, object]:
    """运行可交互哈希阶段，并在文件边界处理暂停后的动作。"""
    if paused_wait_seconds <= 0:
        raise ValueError("paused 等待轮询间隔必须大于 0")
    if router.state != "running":
        raise core.PreflightError(
            f"哈希阶段要求 running 控制器，实际为 {router.state}")

    while True:
        stats = dbhash.process_hash_stage_v4(
            con,
            mode,
            previous=previous,
            retry_mode=retry_mode,
            chunk_bytes=chunk_bytes,
            stall_seconds=stall_seconds,
            timeout_seconds=timeout_seconds,
            default_decision=default_decision,
            control=router.hash_control,
            save_on_pause=False,
            show_current_file=show_current_file,
            on_progress=on_progress,
            on_event=on_event,
            on_threshold=on_threshold,
            poll_seconds=poll_seconds,
            max_files=max_files,
            _worker_target=_worker_target,
        )
        outcome = str(stats["state"])
        if outcome == "completed":
            return stats
        if outcome in ("save_exit", "stopped"):
            router.end()
            _emit_control_event(
                on_event,
                "run_saved" if outcome == "save_exit" else "run_stopped",
                stage="hash",
                state=outcome,
            )
            return stats
        if outcome != "paused":
            raise core.PreflightError(
                f"哈希阶段返回未知控制状态：{outcome}")
        result = _wait_after_pause(
            con,
            "hash",
            router,
            on_event=on_event,
            paused_wait_seconds=paused_wait_seconds,
        )
        if result == "running":
            continue
        stats["state"] = result
        return stats
