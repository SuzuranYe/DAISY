"""DAISY DBS 哈希模块：三模式＋溯源＋独立实现抽验。

实现 full／incremental／none 三种模式、五项复用条件、计算溯源、
schema 3 既有 stall 观测、schema 4 受控工作进程和 PowerShell 独立抽验。
哈希 valid 的条件是摘要非空、读取字节等于文件大小，并且读取前后
size 和 mtime 一致。
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import json
import math
import multiprocessing
import os
import platform
import random
import re
import shutil
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from typing import Callable

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import Script_DAISY_Lib_DBS_01_Core as core
import Script_DAISY_Lib_DBS_05_Reader as dbreader
import Script_DAISY_Lib_DBS_08_State as dbstate

HASH_TOOL = "python-hashlib"
HASH_TOOL_VERSION = platform.python_version()
HASH_TIMEOUT_STEP_BYTES = 9 * 1024 ** 3
HASH_TIMEOUT_STEP_SECONDS = 90
HASH_STALL_SECONDS = 30
HASH_TIMEOUT_DECISIONS = frozenset((
    "continue_waiting", "skip_and_record", "stop_and_resume",
))


# === 单文件流式哈希 ===
def hash_one_file(path: str, expected_size: int | None = None,
                  chunk_bytes: int = core.HASH_CHUNK_BYTES,
                  on_chunk=None, _metrics: dict | None = None) -> dict:
    """流式 SHA-256＋前后 stat 一致性。返回 hashes 行所需全部字段。

    expected_size 为枚举登记的 size_bytes；不符（读前或读后）判 unstable。
    on_chunk(bytes_so_far) 每块回调（进度与 stall 心跳）。
    """
    r = {"hash_hex": None, "bytes_read": None, "chunk_bytes": chunk_bytes,
         "started_at_utc": core.now_utc_iso(), "finished_at_utc": None,
         "pre_size": None, "pre_mtime_utc": None,
         "post_size": None, "post_mtime_utc": None,
         "status": "failed", "failure_reason": None}
    ext = core.to_extended_path(path)
    try:
        st = os.stat(ext, follow_symlinks=False)
    except OSError as exc:
        r["failure_reason"] = f"pre_stat: {exc}"
        r["finished_at_utc"] = core.now_utc_iso()
        return r
    r["pre_size"] = st.st_size
    r["pre_mtime_utc"] = core.ns_to_utc_iso(st.st_mtime_ns)
    if expected_size is not None and st.st_size != expected_size:
        r["status"] = "unstable"
        r["failure_reason"] = (f"size_changed_since_enumeration: "
                               f"{expected_size} -> {st.st_size}")
        r["finished_at_utc"] = core.now_utc_iso()
        return r
    h = hashlib.sha256()
    n = 0
    try:
        with open(ext, "rb") as f:
            while True:
                if _metrics is None:
                    b = f.read(chunk_bytes)
                else:
                    read_started = time.monotonic()
                    b = f.read(chunk_bytes)
                    _metrics["active_read_seconds"] = (
                        float(_metrics.get("active_read_seconds", 0.0))
                        + time.monotonic() - read_started
                    )
                if not b:
                    break
                h.update(b)
                n += len(b)
                if on_chunk:
                    on_chunk(n)
    except OSError as exc:
        r["bytes_read"] = n
        r["failure_reason"] = f"read: {exc}"
        r["finished_at_utc"] = core.now_utc_iso()
        return r
    r["hash_hex"] = h.hexdigest()
    r["bytes_read"] = n
    try:
        st2 = os.stat(ext, follow_symlinks=False)
        r["post_size"] = st2.st_size
        r["post_mtime_utc"] = core.ns_to_utc_iso(st2.st_mtime_ns)
    except OSError as exc:
        r["status"] = "unstable"
        r["failure_reason"] = f"post_stat: {exc}"
        r["finished_at_utc"] = core.now_utc_iso()
        return r
    if (r["pre_size"] == r["post_size"]
            and r["pre_mtime_utc"] == r["post_mtime_utc"]
            and (expected_size is None or n == expected_size)):
        r["status"] = "valid"
    else:
        r["status"] = "unstable"
        r["failure_reason"] = "changed_during_read"
    r["finished_at_utc"] = core.now_utc_iso()
    return r


class StallWatchdog:
    """哈希无固定超时：threshold 秒无进展报一次 stall，恢复后重新武装。"""

    def __init__(self, threshold_s: float, on_stall, poll_s: float = 5.0):
        self._threshold = threshold_s
        self._on_stall = on_stall
        self._poll = poll_s
        self._lock = threading.Lock()
        self._label = None
        self._last = time.monotonic()
        self._reported = True          # beat 之前不报
        self._stopped = threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()

    def beat(self, label: str) -> None:
        with self._lock:
            self._label = label
            self._last = time.monotonic()
            self._reported = False

    def _run(self) -> None:
        while not self._stopped.wait(self._poll):
            with self._lock:
                idle = time.monotonic() - self._last
                if self._reported or self._label is None or idle < self._threshold:
                    continue
                self._reported = True
                label, snap_idle = self._label, idle
            try:
                self._on_stall(label, snap_idle)
            except Exception:
                pass

    def stop(self) -> None:
        self._stopped.set()
        self._t.join(timeout=2.0)


@dataclass(frozen=True)
class TimeoutChoice:
    decision: str
    source: str


class AtomicTimeoutDecision:
    """一次 threshold episode 只接受第一个用户或预设决定。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._choice: TimeoutChoice | None = None

    def choose(self, decision: str, source: str = "user") -> bool:
        if decision not in HASH_TIMEOUT_DECISIONS:
            raise ValueError(f"未知 timeout 决策：{decision}")
        if source not in dbstate.DECISION_SOURCES or source == "none":
            raise ValueError(f"未知 timeout 决策来源：{source}")
        with self._lock:
            if self._choice is not None:
                return False
            self._choice = TimeoutChoice(decision, source)
            return True

    def resolve(self, default_decision: str) -> TimeoutChoice:
        if default_decision not in HASH_TIMEOUT_DECISIONS:
            raise ValueError(f"未知默认 timeout 决策：{default_decision}")
        default_source = (
            "default" if default_decision == "continue_waiting"
            else "advanced_policy"
        )
        with self._lock:
            if self._choice is None:
                self._choice = TimeoutChoice(
                    default_decision, default_source)
            return self._choice

    def current(self) -> TimeoutChoice | None:
        with self._lock:
            return self._choice


class HashWorkerControl:
    """控制线程写入生命周期动作与当前 worker 的 timeout 决策。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._action: tuple[str, str] | None = None
        self._worker_pid: int | None = None
        self._timeout_open = False
        self._timeout_choice: TimeoutChoice | None = None
        self._timeout_terminal = False

    def _request(self, action: str, source: str) -> bool:
        if action not in ("pause", "save_exit", "stop"):
            raise ValueError(f"未知 worker 控制动作：{action}")
        if source not in dbstate.DECISION_SOURCES or source == "none":
            raise ValueError(f"未知 worker 控制来源：{source}")
        with self._lock:
            if self._action is not None or self._timeout_choice is not None \
                    or self._timeout_terminal:
                return False
            self._action = (action, source)
            return True

    def request_pause(self, source: str = "user") -> bool:
        return self._request("pause", source)

    def request_save_exit(self, source: str = "user") -> bool:
        return self._request("save_exit", source)

    def request_stop(self, source: str = "user") -> bool:
        return self._request("stop", source)

    def current(self) -> tuple[str, str] | None:
        with self._lock:
            return self._action

    def bind_worker(self, worker_pid: int) -> None:
        process_id = int(worker_pid)
        if process_id <= 0:
            raise ValueError("worker PID 必须大于 0")
        with self._lock:
            if self._worker_pid not in (None, process_id):
                raise RuntimeError("控制器仍绑定到另一个 worker")
            self._worker_pid = process_id
            self._timeout_open = False
            self._timeout_choice = None
            self._timeout_terminal = False

    def unbind_worker(self, worker_pid: int) -> None:
        with self._lock:
            if self._worker_pid == int(worker_pid):
                self._worker_pid = None
                self._timeout_open = False
                self._timeout_choice = None
                self._timeout_terminal = False

    def open_timeout_decision(self, worker_pid: int) -> bool:
        """只为当前 worker 打开决定窗口；已打开时保留现有选择。"""
        with self._lock:
            if self._worker_pid != int(worker_pid) or self._action is not None \
                    or self._timeout_terminal:
                return False
            self._timeout_open = True
            return True

    def close_timeout_decision(self, worker_pid: int) -> None:
        """关闭当前 worker 的决定窗口，并丢弃尚未执行的过时选择。"""
        with self._lock:
            if self._worker_pid == int(worker_pid):
                self._timeout_open = False
                self._timeout_choice = None
                self._timeout_terminal = False

    def request_timeout_decision(
        self,
        worker_pid: int,
        decision: str,
        source: str = "user",
    ) -> bool:
        if decision not in HASH_TIMEOUT_DECISIONS:
            raise ValueError(f"未知 timeout 决策：{decision}")
        if source not in dbstate.DECISION_SOURCES or source == "none":
            raise ValueError(f"未知 timeout 决策来源：{source}")
        with self._lock:
            if self._worker_pid != int(worker_pid):
                return False
            if not self._timeout_open or self._timeout_choice is not None \
                    or self._action is not None:
                return False
            self._timeout_choice = TimeoutChoice(decision, source)
            return True

    def take_timeout_decision(
        self,
        worker_pid: int,
    ) -> TimeoutChoice | None:
        with self._lock:
            if self._worker_pid != int(worker_pid) or not self._timeout_open:
                return None
            choice = self._timeout_choice
            self._timeout_choice = None
            if choice is not None:
                self._timeout_open = False
                self._timeout_terminal = (
                    choice.decision != "continue_waiting")
            return choice

    def resolve_timeout_decision(
        self,
        worker_pid: int,
        default_decision: str,
        preferred: TimeoutChoice | None = None,
    ) -> TimeoutChoice:
        if default_decision not in HASH_TIMEOUT_DECISIONS:
            raise ValueError(f"未知默认 timeout 决策：{default_decision}")
        default_source = (
            "default" if default_decision == "continue_waiting"
            else "advanced_policy"
        )
        with self._lock:
            if self._worker_pid != int(worker_pid):
                raise RuntimeError("timeout 决策不属于当前 worker")
            if not self._timeout_open:
                raise RuntimeError("当前 worker 没有打开 timeout 决策窗口")
            choice = self._timeout_choice or preferred or TimeoutChoice(
                default_decision, default_source)
            self._timeout_open = False
            self._timeout_choice = None
            self._timeout_terminal = (
                choice.decision != "continue_waiting")
            return choice


@dataclass(frozen=True)
class HashWorkerOutcome:
    outcome: str
    result: dict[str, object] | None
    decision: str
    decision_source: str
    size_bytes: int
    bytes_read: int
    final_offset: int
    elapsed_seconds: float
    active_read_seconds: float
    stall_count: int
    longest_stall_seconds: float
    first_stall_offset: int | None
    last_stall_offset: int | None
    threshold_count: int
    worker_pid: int
    worker_exitcode: int | None
    worker_reaped: bool
    events: tuple[dict[str, object], ...]

    def performance(self) -> dict[str, object]:
        return {
            "origin": "computed",
            "size_bytes": self.size_bytes,
            "bytes_read": self.bytes_read,
            "elapsed_seconds": self.elapsed_seconds,
            "active_read_seconds": self.active_read_seconds,
            "stall_count": self.stall_count,
            "longest_stall_seconds": self.longest_stall_seconds,
            "first_stall_offset": self.first_stall_offset,
            "last_stall_offset": self.last_stall_offset,
            "final_offset": self.final_offset,
            "ended_reason": self.outcome,
        }


@dataclass(frozen=True)
class IndependentHashOutcome:
    """单个 PowerShell Get-FileHash 进程的受控结果。"""

    outcome: str
    hash_hex: str | None
    error: str | None
    decision: str
    decision_source: str
    size_bytes: int
    bytes_read: int
    final_offset: int
    elapsed_seconds: float
    active_read_seconds: float
    stall_count: int
    longest_stall_seconds: float
    first_stall_offset: int | None
    last_stall_offset: int | None
    threshold_count: int
    worker_pid: int
    worker_exitcode: int | None
    worker_reaped: bool
    events: tuple[dict[str, object], ...]

    def performance(self) -> dict[str, object]:
        return {
            "origin": "independent",
            "size_bytes": self.size_bytes,
            "bytes_read": self.bytes_read,
            "elapsed_seconds": self.elapsed_seconds,
            "active_read_seconds": self.active_read_seconds,
            "stall_count": self.stall_count,
            "longest_stall_seconds": self.longest_stall_seconds,
            "first_stall_offset": self.first_stall_offset,
            "last_stall_offset": self.last_stall_offset,
            "final_offset": self.final_offset,
            "ended_reason": self.outcome,
        }


def hash_no_progress_timeout_for_size(
    size_bytes: int,
    *,
    minimum_seconds: float = HASH_TIMEOUT_STEP_SECONDS,
    step_bytes: int = HASH_TIMEOUT_STEP_BYTES,
    seconds_per_step: float = HASH_TIMEOUT_STEP_SECONDS,
) -> float:
    """返回无进展阈值；9 GiB 整数边界仍属于前一个 90 秒档。"""
    size = int(size_bytes)
    if size < 0:
        raise ValueError("size_bytes 不能小于 0")
    if minimum_seconds <= 0 or step_bytes <= 0 or seconds_per_step <= 0:
        raise ValueError("timeout policy 参数必须大于 0")
    steps = max(1, math.ceil(size / step_bytes))
    return float(max(minimum_seconds, steps * seconds_per_step))


def _hash_worker_main(
    connection,
    path: str,
    expected_size: int | None,
    chunk_bytes: int,
) -> None:
    metrics: dict[str, float] = {"active_read_seconds": 0.0}

    def progress(bytes_read: int) -> None:
        connection.send({
            "kind": "progress",
            "bytes_read": int(bytes_read),
            "active_read_seconds": float(metrics["active_read_seconds"]),
        })

    try:
        connection.send({"kind": "ready"})
        result = hash_one_file(
            path,
            expected_size=expected_size,
            chunk_bytes=chunk_bytes,
            on_chunk=progress,
            _metrics=metrics,
        )
        connection.send({
            "kind": "result",
            "result": result,
            "active_read_seconds": float(metrics["active_read_seconds"]),
        })
    except BaseException as exc:
        try:
            connection.send({
                "kind": "crash",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "active_read_seconds": float(metrics["active_read_seconds"]),
            })
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        connection.close()


def _finish_owned_worker(
    process,
    *,
    terminate: bool,
    join_seconds: float = 2.0,
) -> tuple[int | None, bool]:
    if terminate and process.is_alive():
        process.terminate()
    process.join(join_seconds)
    if process.is_alive() and hasattr(process, "kill"):
        process.kill()
        process.join(join_seconds)
    reaped = not process.is_alive()
    exitcode = process.exitcode
    if reaped:
        process.close()
    return exitcode, reaped


def run_hash_worker(
    path: str,
    *,
    expected_size: int | None = None,
    chunk_bytes: int = core.HASH_CHUNK_BYTES,
    stall_seconds: float = HASH_STALL_SECONDS,
    timeout_seconds: float | None = None,
    default_decision: str = "continue_waiting",
    display_name: str | None = None,
    control: HashWorkerControl | None = None,
    on_progress: Callable[[int, int, str], None] | None = None,
    on_event: Callable[..., None] | None = None,
    on_threshold: Callable[
        [dict[str, object], AtomicTimeoutDecision], None] | None = None,
    poll_seconds: float = 0.05,
    worker_start_timeout_seconds: float = 30.0,
    _worker_target=None,
) -> HashWorkerOutcome:
    """监督本次创建的单文件 worker；只用精确句柄终止和回收。"""
    if (chunk_bytes <= 0 or stall_seconds <= 0 or poll_seconds <= 0
            or worker_start_timeout_seconds <= 0):
        raise ValueError("chunk、stall 和 poll 参数必须大于 0")
    if default_decision not in HASH_TIMEOUT_DECISIONS:
        raise ValueError(f"未知默认 timeout 决策：{default_decision}")
    normalized = os.path.abspath(os.fspath(path))
    if expected_size is None:
        try:
            size_bytes = int(os.stat(
                core.to_extended_path(normalized),
                follow_symlinks=False,
            ).st_size)
        except OSError:
            size_bytes = 0
    else:
        size_bytes = int(expected_size)
    if size_bytes < 0:
        raise ValueError("expected_size 不能小于 0")
    threshold_seconds = (
        hash_no_progress_timeout_for_size(size_bytes)
        if timeout_seconds is None else float(timeout_seconds)
    )
    if threshold_seconds <= 0:
        raise ValueError("timeout_seconds 必须大于 0")

    label = str(display_name or os.path.basename(normalized))
    owned_control = control or HashWorkerControl()
    events: list[dict[str, object]] = []

    def emit(event: str, **payload: object) -> None:
        record = {"event": event, **payload}
        events.append(record)
        if on_event is not None:
            try:
                on_event(event, **payload)
            except Exception:
                pass

    context = multiprocessing.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    target = _worker_target or _hash_worker_main
    process = context.Process(
        target=target,
        args=(send, normalized, expected_size, int(chunk_bytes)),
        daemon=True,
    )
    try:
        process.start()
    except Exception:
        receive.close()
        send.close()
        process.close()
        raise
    send.close()
    worker_pid = int(process.pid)
    try:
        owned_control.bind_worker(worker_pid)
    except Exception:
        receive.close()
        _finish_owned_worker(process, terminate=True)
        raise
    emit("worker_started", file=label, worker_pid=worker_pid)
    pipe_closed = False

    def pipe_poll(timeout: float = 0.0) -> bool:
        nonlocal pipe_closed
        try:
            return receive.poll(timeout)
        except (BrokenPipeError, EOFError, OSError):
            pipe_closed = True
            return False

    started = time.monotonic()
    last_progress_at = started
    timeout_window_started = started
    bytes_read = 0
    active_read_seconds = 0.0
    stall_count = 0
    longest_stall_seconds = 0.0
    first_stall_offset = None
    last_stall_offset = None
    threshold_count = 0
    stall_reported = False
    ready = False
    outcome = "crashed"
    result: dict[str, object] | None = None
    decision = "none"
    decision_source = "none"
    terminate = False

    try:
        while True:
            now = time.monotonic()
            action = owned_control.current()
            if action is not None:
                control_action, control_source = action
                outcome = {
                    "pause": "paused",
                    "save_exit": "save_exit",
                    "stop": "stopped",
                }[control_action]
                decision = "stop_and_resume"
                decision_source = control_source
                terminate = True
                emit(
                    "worker_controlled",
                    file=label,
                    action=control_action,
                    bytes_read=bytes_read,
                )
                break

            if pipe_poll(poll_seconds):
                try:
                    message = receive.recv()
                except EOFError:
                    message = None
                if not isinstance(message, dict):
                    if not process.is_alive():
                        emit("worker_no_result", file=label)
                        break
                    continue
                kind = message.get("kind")
                if kind == "ready":
                    ready = True
                    last_progress_at = time.monotonic()
                    timeout_window_started = last_progress_at
                    emit("worker_ready", file=label, worker_pid=worker_pid)
                    continue
                if kind == "progress":
                    next_bytes = max(bytes_read, int(message.get(
                        "bytes_read", bytes_read)))
                    active_read_seconds = max(
                        active_read_seconds,
                        float(message.get(
                            "active_read_seconds", active_read_seconds)),
                    )
                    if next_bytes > bytes_read:
                        bytes_read = next_bytes
                        last_progress_at = time.monotonic()
                        timeout_window_started = last_progress_at
                        stall_reported = False
                        owned_control.close_timeout_decision(worker_pid)
                        if on_progress is not None:
                            try:
                                on_progress(bytes_read, size_bytes, label)
                            except Exception:
                                pass
                    continue
                if kind == "result":
                    raw_result = message.get("result")
                    if isinstance(raw_result, dict):
                        result = dict(raw_result)
                        result_bytes = result.get("bytes_read")
                        if result_bytes is not None:
                            bytes_read = max(bytes_read, int(result_bytes))
                        active_read_seconds = max(
                            active_read_seconds,
                            float(message.get(
                                "active_read_seconds", active_read_seconds)),
                        )
                        outcome = "completed"
                        emit(
                            "worker_completed",
                            file=label,
                            status=result.get("status"),
                            bytes_read=bytes_read,
                        )
                        break
                    emit("worker_bad_result", file=label)
                    break
                if kind == "crash":
                    active_read_seconds = max(
                        active_read_seconds,
                        float(message.get(
                            "active_read_seconds", active_read_seconds)),
                    )
                    emit(
                        "worker_crashed",
                        file=label,
                        error_type=message.get("error_type"),
                        error=message.get("error"),
                    )
                    break

            now = time.monotonic()
            if not ready:
                if now - started >= worker_start_timeout_seconds:
                    outcome = "crashed"
                    terminate = True
                    emit(
                        "worker_start_timeout",
                        file=label,
                        timeout_seconds=worker_start_timeout_seconds,
                    )
                    break
                if pipe_closed or (
                        not process.is_alive() and not pipe_poll()):
                    emit(
                        "worker_no_result",
                        file=label,
                        worker_exitcode=process.exitcode,
                    )
                    break
                continue
            idle = now - last_progress_at
            longest_stall_seconds = max(longest_stall_seconds, idle)
            if idle >= stall_seconds and not stall_reported:
                stall_reported = True
                stall_count += 1
                if first_stall_offset is None:
                    first_stall_offset = bytes_read
                last_stall_offset = bytes_read
                owned_control.open_timeout_decision(worker_pid)
                emit(
                    "stall",
                    file=label,
                    worker_pid=worker_pid,
                    idle_seconds=round(idle, 3),
                    final_offset=bytes_read,
                )

            pending_choice = (
                owned_control.take_timeout_decision(worker_pid)
                if stall_reported else None
            )
            if pending_choice is not None:
                decision = pending_choice.decision
                decision_source = pending_choice.source
                emit(
                    "stall_decided",
                    file=label,
                    worker_pid=worker_pid,
                    decision=decision,
                    decision_source=decision_source,
                    threshold_count=threshold_count,
                )
                if decision == "continue_waiting":
                    timeout_window_started = time.monotonic()
                    owned_control.open_timeout_decision(worker_pid)
                    continue
                terminate = True
                outcome = (
                    "timeout" if decision == "skip_and_record"
                    else "stopped"
                )
                break

            timeout_idle = now - timeout_window_started
            if timeout_idle >= threshold_seconds:
                threshold_count += 1
                owned_control.open_timeout_decision(worker_pid)
                emit(
                    "threshold_reached",
                    file=label,
                    worker_pid=worker_pid,
                    threshold_seconds=threshold_seconds,
                    idle_seconds=round(idle, 3),
                    final_offset=bytes_read,
                    threshold_count=threshold_count,
                )
                arbiter = AtomicTimeoutDecision()
                if on_threshold is not None:
                    try:
                        on_threshold({
                            "file": label,
                            "worker_pid": worker_pid,
                            "size_bytes": size_bytes,
                            "bytes_read": bytes_read,
                            "threshold_seconds": threshold_seconds,
                            "threshold_count": threshold_count,
                        }, arbiter)
                    except Exception as exc:
                        emit(
                            "threshold_callback_error",
                            file=label,
                            error=str(exc),
                        )
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
                    "threshold_decided",
                    file=label,
                    worker_pid=worker_pid,
                    decision=decision,
                    decision_source=decision_source,
                    threshold_count=threshold_count,
                )
                if decision == "continue_waiting":
                    timeout_window_started = time.monotonic()
                    owned_control.open_timeout_decision(worker_pid)
                    continue
                terminate = True
                outcome = (
                    "timeout" if decision == "skip_and_record"
                    else "stopped"
                )
                break

            if pipe_closed or (
                    not process.is_alive() and not pipe_poll()):
                emit(
                    "worker_no_result",
                    file=label,
                    worker_exitcode=process.exitcode,
                )
                break
    finally:
        receive.close()
        try:
            exitcode, reaped = _finish_owned_worker(
                process, terminate=terminate or outcome != "completed")
        finally:
            owned_control.unbind_worker(worker_pid)

    elapsed_seconds = max(0.0, time.monotonic() - started)
    longest_stall_seconds = max(
        longest_stall_seconds, time.monotonic() - last_progress_at)
    return HashWorkerOutcome(
        outcome=outcome,
        result=result,
        decision=decision,
        decision_source=decision_source,
        size_bytes=size_bytes,
        bytes_read=bytes_read,
        final_offset=bytes_read,
        elapsed_seconds=elapsed_seconds,
        active_read_seconds=active_read_seconds,
        stall_count=stall_count,
        longest_stall_seconds=longest_stall_seconds,
        first_stall_offset=first_stall_offset,
        last_stall_offset=last_stall_offset,
        threshold_count=threshold_count,
        worker_pid=worker_pid,
        worker_exitcode=exitcode,
        worker_reaped=reaped,
        events=tuple(events),
    )


def _independent_hash_command(powershell: str, path: str) -> list[str]:
    """以不含原始路径的 EncodedCommand 构造 PowerShell 参数。"""
    path_token = base64.b64encode(path.encode("utf-8")).decode("ascii")
    script = (
        "$ErrorActionPreference='Stop';"
        "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
        "$p=[Text.Encoding]::UTF8.GetString("
        "[Convert]::FromBase64String('" + path_token + "'));"
        "try { (Get-FileHash -LiteralPath $p -Algorithm SHA256"
        " -ErrorAction Stop).Hash }"
        " catch { [Console]::Error.Write($_.Exception.Message); exit 7 }"
    )
    encoded = base64.b64encode(
        script.encode("utf-16-le")).decode("ascii")
    return [
        powershell,
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-EncodedCommand",
        encoded,
    ]


def _finish_owned_subprocess(
    process,
    *,
    terminate: bool,
    wait_seconds: float = 2.0,
) -> tuple[int | None, bool, bytes, bytes]:
    """只终止并回收调用方刚创建的一个精确子进程句柄。"""
    if terminate and process.poll() is None:
        process.terminate()
    try:
        stdout, stderr = process.communicate(timeout=wait_seconds)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate(timeout=wait_seconds)
    return (
        process.returncode,
        process.poll() is not None,
        bytes(stdout or b""),
        bytes(stderr or b""),
    )


def run_independent_hash_process(
    path: str,
    powershell: str,
    *,
    expected_size: int | None = None,
    stall_seconds: float = HASH_STALL_SECONDS,
    timeout_seconds: float | None = None,
    default_decision: str = "continue_waiting",
    display_name: str | None = None,
    control: HashWorkerControl | None = None,
    on_event: Callable[..., None] | None = None,
    on_threshold: Callable[
        [dict[str, object], AtomicTimeoutDecision], None] | None = None,
    poll_seconds: float = 0.05,
    _popen_factory=None,
) -> IndependentHashOutcome:
    """监督一个 Get-FileHash 进程；无进展时沿用 schema 4 timeout 决策。"""
    if stall_seconds <= 0 or poll_seconds <= 0:
        raise ValueError("stall 和 poll 参数必须大于 0")
    if default_decision not in HASH_TIMEOUT_DECISIONS:
        raise ValueError(f"未知默认 timeout 决策：{default_decision}")
    normalized = os.path.abspath(os.fspath(path))
    if expected_size is None:
        try:
            size_bytes = int(os.stat(
                core.to_extended_path(normalized),
                follow_symlinks=False,
            ).st_size)
        except OSError:
            size_bytes = 0
    else:
        size_bytes = int(expected_size)
    if size_bytes < 0:
        raise ValueError("expected_size 不能小于 0")
    threshold_seconds = (
        hash_no_progress_timeout_for_size(size_bytes)
        if timeout_seconds is None else float(timeout_seconds)
    )
    if threshold_seconds <= 0:
        raise ValueError("timeout_seconds 必须大于 0")
    shell = os.path.abspath(os.fspath(powershell))
    if _popen_factory is None and not os.path.isfile(shell):
        raise core.PreflightError(f"PowerShell 路径不存在：{shell}")

    label = str(display_name or os.path.basename(normalized))
    owned_control = control or HashWorkerControl()
    events: list[dict[str, object]] = []

    def emit(event: str, **payload: object) -> None:
        record = {"event": event, **payload}
        events.append(record)
        if on_event is not None:
            try:
                on_event(event, **payload)
            except Exception:
                pass

    command = _independent_hash_command(shell, normalized)
    factory = _popen_factory or subprocess.Popen
    process = factory(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    worker_pid = int(process.pid)
    if worker_pid <= 0:
        try:
            _finish_owned_subprocess(process, terminate=True)
        finally:
            raise core.PreflightError("独立哈希进程没有有效 PID")
    try:
        owned_control.bind_worker(worker_pid)
    except Exception:
        _finish_owned_subprocess(process, terminate=True)
        raise

    emit("worker_started", file=label, worker_pid=worker_pid,
         implementation="powershell_get_filehash")
    started = time.monotonic()
    timeout_window_started = started
    stall_count = 0
    longest_stall_seconds = 0.0
    threshold_count = 0
    stall_reported = False
    outcome = "tool_error"
    decision = "none"
    decision_source = "none"
    terminate = False
    exitcode = None
    reaped = False
    stdout = b""
    stderr = b""

    try:
        while True:
            action = owned_control.current()
            if action is not None:
                control_action, control_source = action
                outcome = {
                    "pause": "paused",
                    "save_exit": "save_exit",
                    "stop": "stopped",
                }[control_action]
                decision = "stop_and_resume"
                decision_source = control_source
                terminate = True
                emit(
                    "worker_controlled",
                    file=label,
                    action=control_action,
                    bytes_read=0,
                )
                break
            if process.poll() is not None:
                outcome = "completed"
                break

            now = time.monotonic()
            idle = now - started
            longest_stall_seconds = max(longest_stall_seconds, idle)
            if idle >= stall_seconds and not stall_reported:
                stall_reported = True
                stall_count = 1
                owned_control.open_timeout_decision(worker_pid)
                emit(
                    "stall",
                    file=label,
                    worker_pid=worker_pid,
                    idle_seconds=round(idle, 3),
                    final_offset=0,
                )

            pending_choice = (
                owned_control.take_timeout_decision(worker_pid)
                if stall_reported else None
            )
            if pending_choice is not None:
                decision = pending_choice.decision
                decision_source = pending_choice.source
                emit(
                    "stall_decided",
                    file=label,
                    worker_pid=worker_pid,
                    decision=decision,
                    decision_source=decision_source,
                    threshold_count=threshold_count,
                )
                if decision == "continue_waiting":
                    timeout_window_started = time.monotonic()
                    owned_control.open_timeout_decision(worker_pid)
                    time.sleep(poll_seconds)
                    continue
                terminate = True
                outcome = (
                    "timeout" if decision == "skip_and_record"
                    else "stopped"
                )
                break

            if now - timeout_window_started >= threshold_seconds:
                threshold_count += 1
                owned_control.open_timeout_decision(worker_pid)
                emit(
                    "threshold_reached",
                    file=label,
                    worker_pid=worker_pid,
                    threshold_seconds=threshold_seconds,
                    idle_seconds=round(idle, 3),
                    final_offset=0,
                    threshold_count=threshold_count,
                )
                arbiter = AtomicTimeoutDecision()
                if on_threshold is not None:
                    try:
                        on_threshold({
                            "file": label,
                            "worker_pid": worker_pid,
                            "size_bytes": size_bytes,
                            "bytes_read": 0,
                            "threshold_seconds": threshold_seconds,
                            "threshold_count": threshold_count,
                        }, arbiter)
                    except Exception as exc:
                        emit(
                            "threshold_callback_error",
                            file=label,
                            error=str(exc),
                        )
                if owned_control.current() is not None:
                    time.sleep(poll_seconds)
                    continue
                choice = owned_control.resolve_timeout_decision(
                    worker_pid,
                    default_decision,
                    preferred=arbiter.current(),
                )
                decision = choice.decision
                decision_source = choice.source
                emit(
                    "threshold_decided",
                    file=label,
                    worker_pid=worker_pid,
                    decision=decision,
                    decision_source=decision_source,
                    threshold_count=threshold_count,
                )
                if decision == "continue_waiting":
                    timeout_window_started = time.monotonic()
                    owned_control.open_timeout_decision(worker_pid)
                    time.sleep(poll_seconds)
                    continue
                terminate = True
                outcome = (
                    "timeout" if decision == "skip_and_record"
                    else "stopped"
                )
                break
            time.sleep(poll_seconds)
    finally:
        try:
            exitcode, reaped, stdout, stderr = _finish_owned_subprocess(
                process, terminate=terminate or outcome != "completed")
        finally:
            owned_control.unbind_worker(worker_pid)

    elapsed_seconds = max(0.0, time.monotonic() - started)
    longest_stall_seconds = max(longest_stall_seconds, elapsed_seconds)
    digest = None
    error = None
    if outcome == "completed":
        tokens = stdout.decode("utf-8", "replace").strip().split()
        if exitcode == 0 and len(tokens) == 1 and _HEX64.match(tokens[0]):
            digest = tokens[0].lower()
            emit("worker_completed", file=label, bytes_read=size_bytes)
        else:
            outcome = "tool_error"
            detail = stderr.decode("utf-8", "replace").strip()
            error = (detail or "Get-FileHash 未返回唯一的 SHA-256")[:2048]
            emit(
                "worker_tool_error",
                file=label,
                worker_exitcode=exitcode,
                error=error,
            )
    elif outcome == "timeout":
        error = "independent_hash_timeout"
    elif outcome == "stopped":
        error = "stop_and_resume"

    completed_bytes = size_bytes if digest is not None else 0
    return IndependentHashOutcome(
        outcome=outcome,
        hash_hex=digest,
        error=error,
        decision=decision,
        decision_source=decision_source,
        size_bytes=size_bytes,
        bytes_read=completed_bytes,
        final_offset=completed_bytes,
        elapsed_seconds=elapsed_seconds,
        active_read_seconds=elapsed_seconds if digest is not None else 0.0,
        stall_count=stall_count,
        longest_stall_seconds=longest_stall_seconds,
        first_stall_offset=0 if stall_count else None,
        last_stall_offset=0 if stall_count else None,
        threshold_count=threshold_count,
        worker_pid=worker_pid,
        worker_exitcode=exitcode,
        worker_reaped=reaped,
        events=tuple(events),
    )


def _reset_current_hash(
    con: sqlite3.Connection,
    entry_id: int,
    _attempt_id: int,
) -> None:
    con.execute(
        "DELETE FROM hashes WHERE entry_id=? AND algorithm='sha256'",
        (entry_id,),
    )
    con.execute(
        "DELETE FROM errors WHERE entry_id=? AND stage='hash'"
        " AND error_code IN"
        " ('hash_failed','hash_timeout','hash_worker_crash')",
        (entry_id,),
    )


def _synthetic_failed_hash(
    size_bytes: int,
    bytes_read: int,
    chunk_bytes: int,
    reason: str,
) -> dict[str, object]:
    now = core.now_utc_iso()
    return {
        "hash_hex": None,
        "bytes_read": bytes_read,
        "chunk_bytes": chunk_bytes,
        "started_at_utc": now,
        "finished_at_utc": now,
        "pre_size": size_bytes,
        "pre_mtime_utc": None,
        "post_size": None,
        "post_mtime_utc": None,
        "status": "failed",
        "failure_reason": reason,
    }


def _validated_worker_hash(
    outcome: HashWorkerOutcome,
    chunk_bytes: int,
) -> tuple[dict[str, object], str | None]:
    if outcome.outcome != "completed" or outcome.result is None:
        reason = {
            "timeout": "no_progress_timeout",
            "paused": "pause_requested",
            "save_exit": "save_exit_requested",
            "stopped": "stop_and_resume",
            "crashed": "worker_crashed_without_result",
        }.get(outcome.outcome, "worker_failed_without_result")
        return _synthetic_failed_hash(
            outcome.size_bytes,
            outcome.bytes_read,
            chunk_bytes,
            reason,
        ), reason
    if not outcome.worker_reaped or outcome.worker_exitcode != 0:
        reason = "worker_not_cleanly_reaped"
        return _synthetic_failed_hash(
            outcome.size_bytes,
            outcome.bytes_read,
            chunk_bytes,
            reason,
        ), reason
    result = dict(outcome.result)
    status = result.get("status")
    if status not in ("valid", "failed", "unstable"):
        reason = f"worker_invalid_status:{status}"
        return _synthetic_failed_hash(
            outcome.size_bytes,
            outcome.bytes_read,
            chunk_bytes,
            reason,
        ), reason
    if status == "valid":
        hash_hex = result.get("hash_hex")
        valid_hex = (
            isinstance(hash_hex, str)
            and re.fullmatch(r"[0-9a-fA-F]{64}", hash_hex) is not None
        )
        stable = (
            result.get("bytes_read") == outcome.size_bytes
            and result.get("pre_size") == outcome.size_bytes
            and result.get("post_size") == outcome.size_bytes
            and result.get("pre_mtime_utc") == result.get("post_mtime_utc")
        )
        if not valid_hex or not stable:
            reason = "worker_invalid_valid_result"
            return _synthetic_failed_hash(
                outcome.size_bytes,
                outcome.bytes_read,
                chunk_bytes,
                reason,
            ), reason
    return result, None


def process_hash_attempt_v4(
    con: sqlite3.Connection,
    entry_id: int,
    path: str,
    *,
    chunk_bytes: int = core.HASH_CHUNK_BYTES,
    stall_seconds: float = HASH_STALL_SECONDS,
    timeout_seconds: float | None = None,
    default_decision: str = "continue_waiting",
    display_name: str | None = None,
    control: HashWorkerControl | None = None,
    save_on_pause: bool = False,
    on_progress: Callable[[int, int, str], None] | None = None,
    on_event: Callable[..., None] | None = None,
    on_threshold: Callable[
        [dict[str, object], AtomicTimeoutDecision], None] | None = None,
    poll_seconds: float = 0.05,
    _worker_target=None,
) -> HashWorkerOutcome:
    """以 schema 4 attempt 包裹单文件 worker，并原子提交当前哈希。"""
    row = con.execute(
        "SELECT size_bytes FROM entries WHERE entry_id=?",
        (entry_id,),
    ).fetchone()
    if row is None:
        raise core.PreflightError(f"哈希 entry_id 不存在：{entry_id}")
    size_bytes = int(row[0])
    attempt_id = dbstate.start_attempt(
        con,
        entry_id,
        "hash",
        tool_name=HASH_TOOL,
        tool_version=HASH_TOOL_VERSION,
        _current_reset=_reset_current_hash,
    )
    try:
        outcome = run_hash_worker(
            path,
            expected_size=size_bytes,
            chunk_bytes=chunk_bytes,
            stall_seconds=stall_seconds,
            timeout_seconds=timeout_seconds,
            default_decision=default_decision,
            display_name=display_name,
            control=control,
            on_progress=on_progress,
            on_event=on_event,
            on_threshold=on_threshold,
            poll_seconds=poll_seconds,
            _worker_target=_worker_target,
        )
    except Exception as exc:
        failed = _synthetic_failed_hash(
            size_bytes, 0, chunk_bytes, f"worker_start_failed:{exc}")

        def write_start_failure(
            current: sqlite3.Connection,
            current_entry_id: int,
            _current_attempt_id: int,
        ) -> None:
            _write_hash_current(
                current,
                current_entry_id,
                failed,
                "hash_worker_crash",
                size_bytes,
            )

        dbstate.finish_attempt(
            con,
            attempt_id,
            "error",
            end_reason="worker_start_failed",
            error_code="hash_worker_crash",
            error_message=str(exc),
            result={"worker_outcome": "start_failed"},
            _current_writer=write_start_failure,
        )
        raise

    result, validation_error = _validated_worker_hash(
        outcome, chunk_bytes)
    result_status = str(result["status"])
    error_code = None
    error_message = None
    if outcome.outcome in ("paused", "save_exit", "stopped"):
        attempt_status = "cancelled"
    elif outcome.outcome == "timeout":
        attempt_status = "timeout"
        error_code = "hash_timeout"
        error_message = str(result["failure_reason"])
    elif outcome.outcome == "crashed" or validation_error is not None:
        attempt_status = "error"
        error_code = "hash_worker_crash"
        error_message = str(result["failure_reason"])
    else:
        attempt_status = {
            "valid": "succeeded",
            "failed": "error",
            "unstable": "unstable",
        }[result_status]
        if attempt_status == "error":
            error_code = "hash_failed"
            error_message = str(result.get("failure_reason") or "hash_failed")

    def write_current(
        current: sqlite3.Connection,
        current_entry_id: int,
        _current_attempt_id: int,
    ) -> None:
        if attempt_status != "cancelled":
            _write_hash_current(
                current,
                current_entry_id,
                result,
                error_code,
                size_bytes,
            )

    event_counts: dict[str, int] = {}
    for event in outcome.events:
        event_name = str(event.get("event"))
        event_counts[event_name] = event_counts.get(event_name, 0) + 1
    performance = outcome.performance()
    performance["ended_reason"] = (
        str(result.get("failure_reason") or result_status)
        if outcome.outcome == "completed" else outcome.outcome
    )
    dbstate.finish_attempt(
        con,
        attempt_id,
        attempt_status,
        bytes_read=outcome.bytes_read,
        final_offset=outcome.final_offset,
        stall_count=outcome.stall_count,
        max_stall_seconds=outcome.longest_stall_seconds,
        decision=outcome.decision,
        decision_source=outcome.decision_source,
        end_reason=performance["ended_reason"],
        error_code=error_code,
        error_message=error_message,
        result={
            "worker_outcome": outcome.outcome,
            "worker_exitcode": outcome.worker_exitcode,
            "worker_reaped": outcome.worker_reaped,
            "threshold_count": outcome.threshold_count,
            "event_counts": event_counts,
        },
        performance=performance,
        _current_writer=write_current,
    )
    if outcome.outcome in ("paused", "save_exit"):
        for_exit = outcome.outcome == "save_exit" or save_on_pause
        dbstate.request_pause(con, for_exit=for_exit)
        dbstate.update_stage_checkpoint(
            con,
            "hash",
            "pause_requested",
            current_entry_id=None,
            checkpoint={"reason": "worker_pause"},
        )
        dbstate.mark_paused(con, for_exit=for_exit)
        dbstate.update_stage_checkpoint(
            con,
            "hash",
            "paused",
            current_entry_id=None,
            checkpoint={"reason": "worker_pause"},
        )
    elif outcome.outcome == "stopped":
        dbstate.update_stage_checkpoint(
            con,
            "hash",
            "failed_recoverable",
            current_entry_id=None,
            checkpoint={"reason": "stop_and_resume"},
        )
        dbstate.stop_run(con, reason="stop_and_resume")
    return outcome


def _write_hash_current(
    con: sqlite3.Connection,
    entry_id: int,
    result: dict[str, object],
    error_code: str | None,
    size_bytes: int,
) -> None:
    con.execute(
        "INSERT INTO hashes"
        " (entry_id,algorithm,hash_hex,origin,size_bytes,bytes_read,"
        " chunk_bytes,started_at_utc,finished_at_utc,pre_size,"
        " pre_mtime_utc,post_size,post_mtime_utc,status,failure_reason,"
        " tool,tool_version) VALUES"
        " (?,'sha256',?,'computed',?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            entry_id,
            result.get("hash_hex"),
            int(size_bytes),
            result.get("bytes_read"),
            result.get("chunk_bytes"),
            result.get("started_at_utc"),
            result.get("finished_at_utc"),
            result.get("pre_size"),
            result.get("pre_mtime_utc"),
            result.get("post_size"),
            result.get("post_mtime_utc"),
            result.get("status"),
            result.get("failure_reason"),
            HASH_TOOL,
            HASH_TOOL_VERSION,
        ),
    )
    if error_code is not None:
        con.execute(
            "INSERT INTO errors"
            " (entry_id,stage,error_code,message,occurred_at_utc)"
            " VALUES (?,'hash',?,?,?)",
            (
                entry_id,
                error_code,
                result.get("failure_reason"),
                core.now_utc_iso(),
            ),
        )


def _commit_reused_hash_v4(
    con: sqlite3.Connection,
    entry_id: int,
    size_bytes: int,
    previous: dict[str, object],
    reuse_basis: str,
) -> None:
    attempt_id = dbstate.start_attempt(
        con,
        entry_id,
        "hash",
        tool_name=str(previous["tool"]),
        tool_version=str(previous["tool_version"]),
        _current_reset=_reset_current_hash,
    )
    source_uuid, source_time = previous["source"]

    def write_current(
        current: sqlite3.Connection,
        current_entry_id: int,
        _current_attempt_id: int,
    ) -> None:
        current.execute(
            "INSERT INTO hashes"
            " (entry_id,algorithm,hash_hex,origin,source_snapshot_uuid,"
            " source_computed_at_utc,reuse_basis,size_bytes,bytes_read,"
            " status,tool,tool_version) VALUES"
            " (?,'sha256',?,'reused',?,?,?,?,0,'valid',?,?)",
            (
                current_entry_id,
                previous["hash_hex"],
                source_uuid,
                source_time,
                reuse_basis,
                size_bytes,
                previous["tool"],
                previous["tool_version"],
            ),
        )

    dbstate.finish_attempt(
        con,
        attempt_id,
        "succeeded",
        bytes_read=0,
        final_offset=0,
        end_reason="reused",
        result={
            "reuse_basis": reuse_basis,
            "source_snapshot_uuid": source_uuid,
            "source_computed_at_utc": source_time,
        },
        performance={
            "origin": "reused",
            "size_bytes": size_bytes,
            "bytes_read": 0,
            "elapsed_seconds": 0.0,
            "active_read_seconds": 0.0,
            "stall_count": 0,
            "longest_stall_seconds": 0.0,
            "first_stall_offset": None,
            "last_stall_offset": None,
            "final_offset": 0,
            "ended_reason": "reused",
        },
        _current_writer=write_current,
    )


def _apply_hash_stage_control(
    con: sqlite3.Connection,
    control_action: tuple[str, str],
    *,
    save_on_pause: bool,
) -> str:
    action, _source = control_action
    if action in ("pause", "save_exit"):
        for_exit = action == "save_exit" or save_on_pause
        dbstate.request_pause(con, for_exit=for_exit)
        dbstate.update_stage_checkpoint(
            con,
            "hash",
            "pause_requested",
            current_entry_id=None,
            checkpoint={"reason": "stage_pause"},
        )
        dbstate.mark_paused(con, for_exit=for_exit)
        dbstate.update_stage_checkpoint(
            con,
            "hash",
            "paused",
            current_entry_id=None,
            checkpoint={"reason": "stage_pause"},
        )
        return "save_exit" if action == "save_exit" else "paused"
    dbstate.update_stage_checkpoint(
        con,
        "hash",
        "failed_recoverable",
        current_entry_id=None,
        checkpoint={"reason": "stage_stop"},
    )
    dbstate.stop_run(con, reason="user_stop")
    return "stopped"


def process_hash_stage_v4(
    con: sqlite3.Connection,
    mode: str,
    *,
    previous: PreviousSnapshot | None = None,
    retry_mode: str = "pending",
    chunk_bytes: int = core.HASH_CHUNK_BYTES,
    stall_seconds: float = HASH_STALL_SECONDS,
    timeout_seconds: float | None = None,
    default_decision: str = "continue_waiting",
    control: HashWorkerControl | None = None,
    save_on_pause: bool = False,
    show_current_file: bool = False,
    on_progress: Callable[[int, dict[str, object]], None] | None = None,
    on_event: Callable[..., None] | None = None,
    on_threshold: Callable[
        [dict[str, object], AtomicTimeoutDecision], None] | None = None,
    poll_seconds: float = 0.05,
    max_files: int | None = None,
    _worker_target=None,
) -> dict[str, object]:
    """schema 4 逐文件阶段；不复用 schema 3 的线程内读取路径。"""
    if mode not in ("none", "incremental", "full"):
        raise ValueError(f"mode={mode}")
    if retry_mode not in ("pending", "transient", "all_unsuccessful"):
        raise ValueError(f"未知 retry_mode：{retry_mode}")
    if mode == "incremental" and previous is None:
        raise core.PreflightError("增量模式需要 previous")
    dbstate.require_v4_connection(con)
    owned_control = control or HashWorkerControl()
    if retry_mode in ("transient", "all_unsuccessful"):
        statuses = "'error'"
        if retry_mode == "all_unsuccessful":
            statuses = "'error','unstable','skipped'"
        con.execute(
            "UPDATE entries SET hash_status='pending'"
            f" WHERE hash_status IN ({statuses}) AND is_placeholder=0"
        )
    placeholders = con.execute(
        "UPDATE entries SET hash_status='skipped'"
        " WHERE hash_status IN ('pending','processing')"
        " AND is_placeholder=1"
    ).rowcount
    con.commit()
    if mode == "none":
        skipped = con.execute(
            "UPDATE entries SET hash_status='skipped'"
            " WHERE hash_status IN ('pending','processing')"
        ).rowcount
        con.commit()
        dbstate.update_stage_checkpoint(
            con,
            "hash",
            "skipped",
            items_done=skipped + placeholders,
            current_entry_id=None,
            checkpoint={"reason": "hash_mode_none"},
        )
        return {
            "state": "completed",
            "total": skipped + placeholders,
            "processed": skipped,
            "done": 0,
            "reused": 0,
            "error": 0,
            "timeout": 0,
            "unstable": 0,
            "skipped": skipped + placeholders,
            "bytes_total": 0,
            "bytes_read": 0,
        }

    total, bytes_total = con.execute(
        "SELECT COUNT(*),COALESCE(SUM(size_bytes),0) FROM entries"
        " WHERE is_placeholder=0"
    ).fetchone()
    processed = con.execute(
        "SELECT COUNT(*) FROM entries WHERE is_placeholder=0"
        " AND hash_status NOT IN ('pending','processing')"
    ).fetchone()[0]
    done = con.execute(
        "SELECT COUNT(*) FROM entries WHERE is_placeholder=0"
        " AND hash_status='done'"
    ).fetchone()[0]
    reused_count = con.execute(
        "SELECT COUNT(*) FROM entries e JOIN hashes h ON h.entry_id=e.entry_id"
        " AND h.algorithm='sha256' WHERE e.is_placeholder=0"
        " AND e.hash_status='done' AND h.origin='reused'"
    ).fetchone()[0]
    timeout_count = con.execute(
        "SELECT COUNT(*) FROM entries e WHERE e.is_placeholder=0"
        " AND e.hash_status='error' AND"
        " (SELECT a.status FROM entry_attempts a"
        "  WHERE a.entry_id=e.entry_id AND a.stage='hash'"
        "  ORDER BY a.attempt_number DESC LIMIT 1)='timeout'"
    ).fetchone()[0]
    error_count = con.execute(
        "SELECT COUNT(*) FROM entries WHERE is_placeholder=0"
        " AND hash_status='error'"
    ).fetchone()[0] - int(timeout_count)
    unstable_count = con.execute(
        "SELECT COUNT(*) FROM entries WHERE is_placeholder=0"
        " AND hash_status='unstable'"
    ).fetchone()[0]
    nonplaceholder_skipped = con.execute(
        "SELECT COUNT(*) FROM entries WHERE is_placeholder=0"
        " AND hash_status='skipped'"
    ).fetchone()[0]
    completed_bytes = con.execute(
        "SELECT COALESCE(SUM(size_bytes),0) FROM entries"
        " WHERE is_placeholder=0 AND hash_status='done'"
    ).fetchone()[0]
    stats: dict[str, object] = {
        "state": "running",
        "total": int(total),
        "processed": int(processed),
        "done": int(done),
        "reused": int(reused_count),
        "error": int(error_count),
        "timeout": int(timeout_count),
        "unstable": int(unstable_count),
        "skipped": placeholders + int(nonplaceholder_skipped),
        "bytes_total": int(bytes_total),
        "bytes_read": int(completed_bytes),
    }
    dbstate.update_stage_checkpoint(
        con,
        "hash",
        "running",
        items_done=int(processed),
        items_total=int(total),
        bytes_done=int(completed_bytes),
        bytes_total=int(bytes_total),
        current_entry_id=None,
    )
    roots = dict(con.execute("SELECT root_id,root_path FROM roots"))
    labels = dict(con.execute("SELECT root_id,root_label FROM roots"))
    last_current_event = 0.0
    last_progress_event = 0.0

    while True:
        control_action = owned_control.current()
        if control_action is not None:
            stats["state"] = _apply_hash_stage_control(
                con, control_action, save_on_pause=save_on_pause)
            break
        row = con.execute(
            "SELECT entry_id,root_id,rel_path,path_key,size_bytes,"
            " modified_at_utc,is_placeholder,volume_serial,file_index_hex"
            " FROM entries WHERE hash_status='pending'"
            " ORDER BY root_id,path_key,rel_path LIMIT 1"
        ).fetchone()
        if row is None:
            stats["state"] = "completed"
            dbstate.update_stage_checkpoint(
                con,
                "hash",
                "completed",
                items_done=int(stats["processed"]),
                items_total=int(stats["total"]),
                bytes_done=int(stats["bytes_read"]),
                bytes_total=int(stats["bytes_total"]),
                error_count=int(stats["error"]) + int(stats["timeout"]),
                current_entry_id=None,
            )
            break
        (entry_id, root_id, rel_path, path_key, size_bytes, modified_at,
         placeholder, volume_serial, file_index_hex) = tuple(row)
        if max_files is not None and int(stats["processed"]) >= max_files:
            raise KeyboardInterrupt
        now = time.monotonic()
        if show_current_file and on_event is not None \
                and now - last_current_event >= 0.1:
            on_event("current_item", stage="hash", item=rel_path)
            last_current_event = now

        reused = False
        if mode == "incremental":
            prior = previous.lookup(labels[root_id], path_key)
            can_reuse, basis = reuse_decision(
                {
                    "size": size_bytes,
                    "mtime": modified_at,
                    "placeholder": placeholder,
                    "volume_serial": volume_serial,
                    "file_index_hex": file_index_hex,
                },
                prior,
            )
            if can_reuse:
                _commit_reused_hash_v4(
                    con, entry_id, size_bytes, prior, basis)
                stats["reused"] = int(stats["reused"]) + 1
                stats["done"] = int(stats["done"]) + 1
                stats["bytes_read"] = (
                    int(stats["bytes_read"]) + int(size_bytes))
                reused = True
        outcome = None
        if not reused:
            outcome = process_hash_attempt_v4(
                con,
                entry_id,
                os.path.join(roots[root_id], rel_path),
                chunk_bytes=chunk_bytes,
                stall_seconds=stall_seconds,
                timeout_seconds=timeout_seconds,
                default_decision=default_decision,
                display_name=rel_path,
                control=owned_control,
                save_on_pause=save_on_pause,
                on_event=on_event,
                on_threshold=on_threshold,
                poll_seconds=poll_seconds,
                _worker_target=_worker_target,
            )
            stats["bytes_read"] = (
                int(stats["bytes_read"]) + outcome.bytes_read)
            if outcome.outcome in ("paused", "save_exit", "stopped"):
                stats["state"] = outcome.outcome
                break
            attempt_status = con.execute(
                "SELECT status FROM entry_attempts"
                " WHERE entry_id=? AND stage='hash'"
                " ORDER BY attempt_number DESC LIMIT 1",
                (entry_id,),
            ).fetchone()[0]
            if attempt_status == "succeeded":
                stats["done"] = int(stats["done"]) + 1
            elif attempt_status == "timeout":
                stats["timeout"] = int(stats["timeout"]) + 1
            elif attempt_status == "unstable":
                stats["unstable"] = int(stats["unstable"]) + 1
            else:
                stats["error"] = int(stats["error"]) + 1
        stats["processed"] = int(stats["processed"]) + 1
        dbstate.update_stage_checkpoint(
            con,
            "hash",
            "running",
            items_done=int(stats["processed"]),
            items_total=int(stats["total"]),
            bytes_done=int(stats["bytes_read"]),
            bytes_total=int(stats["bytes_total"]),
            error_count=int(stats["error"]) + int(stats["timeout"]),
            current_entry_id=None,
        )
        now = time.monotonic()
        if on_progress is not None and (
                now - last_progress_event >= 0.5
                or int(stats["processed"]) == int(stats["total"])):
            on_progress(int(stats["processed"]), dict(stats))
            last_progress_event = now
    return stats


def classify_read_performance_candidates(
    con: sqlite3.Connection,
    *,
    group_minimum: int = 8,
    throughput_minimum_bytes: int = 1024 * 1024,
) -> dict[str, object]:
    """按同卷、同类型和相近大小组标注读取性能异常候选。"""
    if isinstance(group_minimum, bool) or not isinstance(group_minimum, int) \
            or group_minimum < 4:
        raise ValueError("group_minimum 必须是至少 4 的整数")
    if isinstance(throughput_minimum_bytes, bool) \
            or not isinstance(throughput_minimum_bytes, int) \
            or throughput_minimum_bytes < 0:
        raise ValueError("throughput_minimum_bytes 必须是非负整数")
    dbstate.require_v4_connection(con)
    runtime = dbstate.load_runtime(con)
    if runtime.run_state != "running":
        raise core.PreflightError(
            f"性能候选分析要求 running，实际为 {runtime.run_state}")

    raw_rows = con.execute(
        "SELECT p.performance_id,p.size_bytes,p.bytes_read,"
        " p.active_read_seconds,p.stall_count,p.longest_stall_seconds,"
        " e.extension,e.media_kind,r.volume_serial,r.root_id"
        " FROM read_performance p"
        " JOIN entry_attempts a ON a.attempt_id=p.attempt_id"
        " JOIN entries e ON e.entry_id=p.entry_id"
        " JOIN roots r ON r.root_id=e.root_id"
        " JOIN hashes h ON h.entry_id=e.entry_id"
        "  AND h.algorithm='sha256'"
        " WHERE p.stage='hash' AND p.origin='computed'"
        " AND a.status='succeeded'"
        " AND NOT EXISTS (SELECT 1 FROM entry_attempts newer"
        "  WHERE newer.entry_id=a.entry_id AND newer.stage='hash'"
        "  AND newer.attempt_number>a.attempt_number)"
        " AND e.hash_status='done'"
        " AND h.origin='computed' AND h.status='valid'"
        " AND p.bytes_read>=p.size_bytes"
        " ORDER BY p.performance_id"
    ).fetchall()
    reused_rows = int(con.execute(
        "SELECT COUNT(*) FROM read_performance"
        " WHERE stage='hash' AND origin='reused'"
    ).fetchone()[0])
    rows: list[dict[str, object]] = []
    groups: dict[tuple[str, str, int], list[dict[str, object]]] = {}
    for raw in raw_rows:
        (performance_id, size_bytes, bytes_read, active_seconds,
         stall_count, longest_stall, extension, media_kind,
         volume_serial, root_id) = tuple(raw)
        size = int(size_bytes)
        active = float(active_seconds)
        throughput = (
            float(bytes_read) / active if active > 0 and bytes_read else 0.0)
        volume_key = (
            str(volume_serial)
            if str(volume_serial or "").strip()
            else f"root:{int(root_id)}"
        )
        normalized_extension = str(extension or "").strip().casefold()
        type_key = (
            "ext:" + normalized_extension
            if normalized_extension else
            "kind:" + str(media_kind or "other").strip().casefold()
        )
        size_band = int(round(math.log2(max(size, 1))))
        row = {
            "performance_id": int(performance_id),
            "size_bytes": size,
            "bytes_read": int(bytes_read),
            "throughput": throughput,
            "stall_count": int(stall_count),
            "longest_stall": float(longest_stall),
            "group": (volume_key, type_key, size_band),
        }
        rows.append(row)
        if size >= throughput_minimum_bytes and throughput > 0:
            groups.setdefault(row["group"], []).append(row)

    assignments: dict[int, tuple[str, str | None]] = {
        int(row["performance_id"]): ("none", None) for row in rows
    }
    reason_parts: dict[int, list[str]] = {}

    def promote(performance_id: int, confidence: str, reason: str) -> None:
        current, _current_reason = assignments[performance_id]
        rank = {"none": 0, "low": 1, "high": 2}
        if rank[confidence] > rank[current]:
            assignments[performance_id] = (confidence, None)
        reason_parts.setdefault(performance_id, []).append(reason)

    analyzed_groups = 0
    for grouped in groups.values():
        if len(grouped) < group_minimum:
            continue
        analyzed_groups += 1
        throughputs = [float(row["throughput"]) for row in grouped]
        median = float(statistics.median(throughputs))
        if median <= 0:
            continue
        deviations = [abs(value - median) for value in throughputs]
        mad = float(statistics.median(deviations))
        for row in grouped:
            throughput = float(row["throughput"])
            ratio = throughput / median
            confidence = "none"
            if mad == 0:
                if ratio <= 0.25:
                    confidence = "high"
                elif ratio <= 0.50:
                    confidence = "low"
            else:
                robust_score = (median - throughput) / (1.4826 * mad)
                if robust_score >= 6.0 and ratio <= 0.50:
                    confidence = "high"
                elif robust_score >= 3.5 and ratio <= 0.75:
                    confidence = "low"
            if confidence != "none":
                promote(
                    int(row["performance_id"]),
                    confidence,
                    "同卷、同类型、相近大小组"
                    f"（样本 {len(grouped)}，吞吐 "
                    f"{throughput / 1024 ** 2:.2f} MiB/s，"
                    f"中位数 {median / 1024 ** 2:.2f} MiB/s，"
                    f"比例 {ratio:.3f}）",
                )

    for row in rows:
        longest = float(row["longest_stall"])
        if longest <= 0:
            continue
        threshold = hash_no_progress_timeout_for_size(
            int(row["size_bytes"]))
        if longest >= threshold:
            promote(
                int(row["performance_id"]),
                "high",
                f"最长无进展 {longest:.3f}s 达到动态阈值 "
                f"{threshold:.3f}s",
            )
        elif longest >= HASH_STALL_SECONDS:
            promote(
                int(row["performance_id"]),
                "low",
                f"最长无进展 {longest:.3f}s 达到早期 stall 告警 "
                f"{HASH_STALL_SECONDS}s",
            )

    finalized: dict[int, tuple[str, str | None]] = {}
    for performance_id, (confidence, _reason) in assignments.items():
        parts = reason_parts.get(performance_id, [])
        reason = None
        if confidence != "none":
            reason = (
                "读取性能异常候选：" + "；".join(parts)
                + "。该结论仅定位可疑逻辑路径／时段，"
                "不能据此认定物理坏区。"
            )[:2048]
        finalized[performance_id] = (confidence, reason)

    with con:
        con.execute(
            "UPDATE read_performance SET candidate_confidence='none',"
            " candidate_reason=NULL"
        )
        con.executemany(
            "UPDATE read_performance SET candidate_confidence=?,"
            " candidate_reason=? WHERE performance_id=?",
            (
                (confidence, reason, performance_id)
                for performance_id, (confidence, reason)
                in finalized.items()
            ),
        )
    confidence_counts = {"none": 0, "low": 0, "high": 0}
    for confidence, _reason in finalized.values():
        confidence_counts[confidence] += 1
    return {
        "method": "daisy-read-performance-v1",
        "eligible": len(rows),
        "throughput_groups": analyzed_groups,
        "group_minimum": group_minimum,
        "throughput_minimum_bytes": throughput_minimum_bytes,
        "excluded_reused": reused_rows,
        **confidence_counts,
        "physical_location_claimed": False,
    }


# === 增量复用与计算溯源 ===
class PreviousSnapshot:
    def __init__(self, path: str, uuid_: str, index: dict,
                 has_file_issues: bool = False):
        self.path = path
        self.uuid = uuid_
        self.has_file_issues = has_file_issues
        self._index = index      # (当前 label, path_key) -> rec | "ambiguous"

    def lookup(self, label: str, path_key: str):
        rec = self._index.get((label, path_key))
        return None if rec == "ambiguous" else rec


def load_previous(prev_path: str,
                  map_root: dict | None = None) -> PreviousSnapshot:
    """验证当前 schema 3 来源并载入 status='valid' 的哈希索引。

    SQLite 损坏、扫描未完成、枚举缺口、哈希失败或 unstable 一律拒绝。
    单纯存在损坏／空白／无法解析的源文件不妨碍其他有效哈希复用；新扫描会
    重新读取元数据，并按当前结果生成自己的 Issues.md。"""
    if not os.path.isfile(prev_path):
        raise core.PreflightError(f"上一快照不存在：{prev_path}")
    recorded = core.filename_sha256_high32(prev_path)
    if recorded is None:
        raise core.PreflightError(f"上一快照文件名缺少 SHA-256 高32bit 指纹：{prev_path}")
    actual = core.sha256_file(prev_path)[:8].upper()
    if recorded != actual:
        raise core.PreflightError(
            f"上一快照文件名高32bit指纹不符：记录 {recorded}，实际 {actual}")
    con, descriptor = dbreader.open_database(
        prev_path, expected_type="snapshot")
    try:
        dbreader.require_capabilities(
            descriptor, "files", "directories", "hashes")
        (uuid_, schema_v, pk_rule, has_file_issues, has_unstable_entries,
         has_enumeration_gaps) = con.execute(
            "SELECT snapshot_uuid,schema_version,path_key_rule,"
            " has_file_issues,has_unstable_entries,"
            " has_enumeration_gaps FROM snapshot_info").fetchone()
        if pk_rule != core.PATH_KEY_RULE:
            raise core.PreflightError(
                f"上一快照 schema_version/path_key_rule 不符：{schema_v}/{pk_rule}"
                f"（可读 schema {sorted(core.READABLE_SCHEMA_VERSIONS)}；"
                f"当前 path_key_rule {core.PATH_KEY_RULE}）")
        actual_file_issues, = con.execute(
            "SELECT EXISTS(SELECT 1 FROM entries WHERE"
            " meta_status IN ('error','timeout'))").fetchone()
        actual_unstable, = con.execute(
            "SELECT EXISTS(SELECT 1 FROM entries WHERE"
            " meta_status='unstable' OR hash_status='unstable')").fetchone()
        actual_gaps, = con.execute(
            "SELECT EXISTS(SELECT 1 FROM dirs WHERE enum_status<>'ok')"
            " OR EXISTS(SELECT 1 FROM roots WHERE enum_status='failed')"
        ).fetchone()
        hash_failures, = con.execute(
            "SELECT COUNT(*) FROM entries WHERE hash_status='error'").fetchone()
        expected = (bool(actual_file_issues), bool(actual_unstable),
                    bool(actual_gaps))
        recorded_status = (bool(has_file_issues), bool(has_unstable_entries),
                           bool(has_enumeration_gaps))
        if recorded_status != expected:
            raise core.PreflightError(
                "上一快照状态字段与明细不一致："
                f"记录={recorded_status}，实际={expected}")
        blockers = []
        if has_enumeration_gaps:
            blockers.append("存在目录枚举缺口")
        if hash_failures:
            blockers.append(f"存在 {hash_failures} 个哈希失败条目")
        if has_unstable_entries:
            blockers.append("存在 unstable 条目")
        if blockers:
            raise core.PreflightError(
                "上一快照禁止作为增量来源：" + "；".join(blockers))
        mapping = map_root or {}
        index: dict = {}
        for (label, path_key, size, mtime, placeholder, vs, fih, hex_,
             origin, src_uuid, src_t, fin_t, tool, tool_v) in con.execute(
                "SELECT r.root_label, e.path_key, e.size_bytes,"
                " e.modified_at_utc, e.is_placeholder, e.volume_serial,"
                " e.file_index_hex, h.hash_hex, h.origin,"
                " h.source_snapshot_uuid, h.source_computed_at_utc,"
                " h.finished_at_utc, h.tool, h.tool_version"
                " FROM entries e JOIN roots r ON r.root_id = e.root_id"
                " JOIN hashes h ON h.entry_id = e.entry_id"
                " AND h.algorithm='sha256' AND h.status='valid'"):
            key = (mapping.get(label, label), path_key)
            if key in index:
                index[key] = "ambiguous"    # path_key 碰撞：不复用（条件 1 唯一性）
                continue
            # computed 行记录本快照事件，reused 行沿用最初计算事件
            src = (uuid_, fin_t) if origin == "computed" else (src_uuid, src_t)
            index[key] = {"size": size, "mtime": mtime,
                          "placeholder": placeholder,
                          "volume_serial": vs, "file_index_hex": fih,
                          "hash_hex": hex_, "source": src,
                          "tool": tool, "tool_version": tool_v}
        return PreviousSnapshot(
            prev_path, uuid_, index,
            has_file_issues=bool(has_file_issues))
    except sqlite3.Error as exc:
        raise core.PreflightError(
            f"上一快照 SQLite 结构不可读：{exc}") from exc
    finally:
        con.close()


def reuse_decision(entry: dict, prev: dict | None) -> tuple[bool, str]:
    """判断上一快照条目能否复用；存在性与唯一性由 lookup 负责。
    返回 (可否复用, reuse_basis 或拒绝原因)。"""
    if prev is None:
        return False, "no_previous_entry"
    if entry["size"] != prev["size"] or entry["mtime"] != prev["mtime"]:
        return False, "stat_changed"
    if entry["placeholder"] or prev["placeholder"]:
        return False, "placeholder"
    if (entry["volume_serial"] and entry["file_index_hex"]
            and prev["volume_serial"] and prev["file_index_hex"]):
        if (entry["volume_serial"] != prev["volume_serial"]
                or entry["file_index_hex"] != prev["file_index_hex"]):
            return False, "file_id_mismatch"      # 条件 5：不等强制重算
        return True, "size+mtime+fileid"
    return True, "size+mtime"


# === 哈希阶段（管线 [3/6]；逐文件断点续传） ===
def process_hash_stage(con: sqlite3.Connection, mode: str,
                       previous: PreviousSnapshot | None = None,
                       chunk_bytes: int = core.HASH_CHUNK_BYTES,
                       commit_every: int = 100,
                       max_files: int | None = None,
                       stall_seconds: float = 30.0,
                       on_progress=None, on_event=None,
                       error_warn_ratio: float = 0.2,
                       error_abort_ratio: float = 0.5) -> dict:
    """按 hash_status='pending' 逐文件哈希/复用入库。

    max_files 为内部测试钩子：处理 N 个文件后模拟中断（KeyboardInterrupt）。
    错误率 >warn 时告警继续，>abort 时中止并保留 partial。
    """
    if mode not in ("full", "incremental"):
        raise ValueError(f"mode={mode}")
    if mode == "incremental" and previous is None:
        raise core.PreflightError("增量模式需要 previous（--previous-snapshot）")
    cur = con.execute("UPDATE entries SET hash_status='skipped'"
                      " WHERE hash_status IN ('pending','processing')"
                      " AND is_placeholder=1")       # 云占位文件恒为 skipped
    n_placeholder = cur.rowcount
    con.execute("UPDATE entries SET hash_status='pending'"
                " WHERE hash_status='processing'")   # 遗留 processing 重置续传
    con.commit()
    roots = dict(con.execute("SELECT root_id, root_path FROM roots"))
    labels = dict(con.execute("SELECT root_id, root_label FROM roots"))
    todo = con.execute(
        "SELECT entry_id, root_id, rel_path, path_key, size_bytes,"
        " modified_at_utc, is_placeholder, volume_serial, file_index_hex"
        " FROM entries WHERE hash_status='pending'"
        " ORDER BY root_id, rel_path").fetchall()
    stats = {"total": len(todo), "done": 0, "reused": 0, "error": 0,
             "unstable": 0, "skipped": n_placeholder,
             "bytes_total": sum(r[4] for r in todo), "bytes_hashed": 0}
    warned = False
    wd = StallWatchdog(stall_seconds, lambda label, idle: (
        on_event and on_event("stall", file=label, idle_seconds=round(idle, 1))))
    processed = 0
    try:
        for (entry_id, root_id, rel, path_key, size, mtime, placeholder,
             vs, fih) in todo:
            if max_files is not None and processed >= max_files:
                con.commit()
                raise KeyboardInterrupt
            con.execute("UPDATE entries SET hash_status='processing'"
                        " WHERE entry_id=?", (entry_id,))
            con.execute("DELETE FROM hashes WHERE entry_id=?"
                        " AND algorithm='sha256'", (entry_id,))
            reused = False
            if mode == "incremental":
                prev = previous.lookup(labels[root_id], path_key)
                ok, basis = reuse_decision(
                    {"size": size, "mtime": mtime, "placeholder": placeholder,
                     "volume_serial": vs, "file_index_hex": fih}, prev)
                if ok:
                    src_uuid, src_t = prev["source"]
                    con.execute(
                        "INSERT INTO hashes (entry_id, algorithm, hash_hex,"
                        " origin, source_snapshot_uuid, source_computed_at_utc,"
                        " reuse_basis, size_bytes, status, tool, tool_version)"
                        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (entry_id, "sha256", prev["hash_hex"], "reused",
                         src_uuid, src_t, basis, size, "valid",
                         prev["tool"], prev["tool_version"]))
                    con.execute("UPDATE entries SET hash_status='done'"
                                " WHERE entry_id=?", (entry_id,))
                    stats["reused"] += 1
                    stats["done"] += 1
                    reused = True
            if not reused:
                path = os.path.join(roots[root_id], rel)
                wd.beat(rel)
                r = hash_one_file(path, expected_size=size,
                                  chunk_bytes=chunk_bytes,
                                  on_chunk=lambda _n: wd.beat(rel))
                con.execute(
                    "INSERT INTO hashes (entry_id, algorithm, hash_hex, origin,"
                    " size_bytes, bytes_read, chunk_bytes, started_at_utc,"
                    " finished_at_utc, pre_size, pre_mtime_utc, post_size,"
                    " post_mtime_utc, status, failure_reason, tool,"
                    " tool_version) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (entry_id, "sha256", r["hash_hex"], "computed", size,
                     r["bytes_read"], r["chunk_bytes"], r["started_at_utc"],
                     r["finished_at_utc"], r["pre_size"], r["pre_mtime_utc"],
                     r["post_size"], r["post_mtime_utc"], r["status"],
                     r["failure_reason"], HASH_TOOL, HASH_TOOL_VERSION))
                new_status = {"valid": "done", "failed": "error",
                              "unstable": "unstable"}[r["status"]]
                con.execute("UPDATE entries SET hash_status=?"
                            " WHERE entry_id=?", (new_status, entry_id))
                if r["status"] == "failed":
                    stats["error"] += 1
                    con.execute(
                        "INSERT INTO errors (entry_id, stage, error_code,"
                        " message, occurred_at_utc)"
                        " VALUES (?, 'hash', 'hash_failed', ?, ?)",
                        (entry_id, r["failure_reason"], core.now_utc_iso()))
                elif r["status"] == "unstable":
                    stats["unstable"] += 1
                else:
                    stats["done"] += 1
                stats["bytes_hashed"] += r["bytes_read"] or 0
            processed += 1
            if processed % commit_every == 0:
                con.commit()
            if on_progress:
                on_progress(processed, stats)
            if processed >= 20:
                ratio = stats["error"] / processed
                if ratio > error_abort_ratio:
                    con.commit()
                    raise core.PreflightError(
                        f"哈希错误率 {ratio:.0%} 超过 {error_abort_ratio:.0%}，"
                        f"中止并保留 partial（可 --resume 续传）")
                if ratio > error_warn_ratio and not warned:
                    warned = True
                    if on_event:
                        on_event("error_rate_warning", stage="hash",
                                 ratio=round(ratio, 3))
        con.commit()
    finally:
        wd.stop()
    return stats


# === 独立实现抽验（PowerShell Get-FileHash） ===
_PS_PROBE = (
    "if (-not (Get-Command Get-FileHash -ErrorAction SilentlyContinue)) {"
    " [Console]::Error.Write('Get-FileHash unavailable'); exit 3 };"
    "$PSVersionTable.PSVersion.ToString()"
)


def _powershell_candidates() -> list[str]:
    """按 PATH → Windows 常规位置返回去重后的 PowerShell 候选。"""
    candidates = []
    for command in ("powershell.exe", "powershell", "pwsh.exe", "pwsh"):
        found = shutil.which(command)
        if found:
            candidates.append(found)

    if os.name == "nt":
        windows_root = (os.environ.get("SystemRoot")
                        or os.environ.get("WINDIR"))
        if windows_root:
            candidates.append(os.path.join(
                windows_root, "System32", "WindowsPowerShell", "v1.0",
                "powershell.exe"))

        program_roots = [
            os.environ.get("ProgramW6432"),
            os.environ.get("ProgramFiles"),
            os.environ.get("ProgramFiles(x86)"),
        ]
        for root in program_roots:
            if root:
                candidates.append(
                    os.path.join(root, "PowerShell", "7", "pwsh.exe"))

        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            candidates.append(os.path.join(
                local_app_data, "Microsoft", "WindowsApps", "pwsh.exe"))

    unique = []
    seen = set()
    for path in candidates:
        absolute = os.path.abspath(path)
        key = os.path.normcase(absolute)
        if key not in seen:
            seen.add(key)
            unique.append(absolute)
    return unique


def _probe_powershell(path: str) -> tuple[str, str]:
    """验证 PowerShell 可启动、可报告版本并提供 Get-FileHash。"""
    try:
        proc = subprocess.run(
            [path, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command",
             _PS_PROBE],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise core.PreflightError(
            f"PowerShell 无法启动：{path}（{exc}）") from exc
    version = (proc.stdout or "").strip()
    if proc.returncode != 0:
        reason = (proc.stderr or "").strip()
        suffix = f"（{reason}）" if reason else ""
        raise core.PreflightError(
            f"PowerShell 缺少 Get-FileHash 或探测失败：{path}{suffix}")
    if not version:
        raise core.PreflightError(f"无法取得 PowerShell 版本：{path}")
    return os.path.abspath(path), version


def discover_powershell(explicit: str | None = None) -> tuple[str, str]:
    if explicit:
        if not os.path.isfile(explicit):
            raise core.PreflightError(
                f"PowerShell 显式路径不存在：{explicit}")
        return _probe_powershell(os.path.abspath(explicit))

    failures = []
    for path in _powershell_candidates():
        if not os.path.isfile(path):
            continue
        try:
            return _probe_powershell(path)
        except core.PreflightError as exc:
            failures.append(str(exc))

    if failures:
        raise core.PreflightError(
            "找到了 PowerShell 候选，但均无法用于独立哈希抽验：\n  "
            + "\n  ".join(failures))
    raise core.PreflightError(
        "未找到 PowerShell（已检查 PATH 与 Windows 常规安装位置；"
        "可用 --powershell-path 手动指定）")


_PS_BATCH = (
    "$ErrorActionPreference='Continue';"
    "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
    "$i=0;"
    "Get-Content -LiteralPath '{list}' -Encoding UTF8 | ForEach-Object {{"
    " try {{ $h=(Get-FileHash -LiteralPath $_ -Algorithm SHA256).Hash }}"
    " catch {{ $h='ERROR' }};"
    " \"$i $h\"; $i++ }}"
)
_HEX64 = re.compile(r"[0-9A-Fa-f]{64}\Z")
_PS_BATCH_SIZE = 200


def get_filehash_batch(paths: list[str], powershell: str | None = None,
                       on_progress=None) -> list[str | None]:
    """独立实现批量 SHA-256（Get-FileHash）。逐批一个 PS 进程，按行号回配；
    返回与 paths 等长的小写 hex 列表，读不到者为 None。"""
    if not paths:
        return []
    ps = powershell or discover_powershell()[0]
    result: list[str | None] = [None] * len(paths)
    for base in range(0, len(paths), _PS_BATCH_SIZE):
        batch = paths[base:base + _PS_BATCH_SIZE]
        fd, listfile = tempfile.mkstemp(suffix=".txt")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\r\n") as f:
                for p in batch:
                    f.write(os.path.abspath(p) + "\n")
            cmd = _PS_BATCH.format(list=listfile.replace("'", "''"))
            proc = subprocess.run(
                [ps, "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-Command", cmd], capture_output=True)
            for line in proc.stdout.decode("utf-8", "replace").splitlines():
                parts = line.strip().split()
                if len(parts) == 2 and parts[0].isdigit():
                    idx = int(parts[0])
                    if idx < len(batch) and _HEX64.match(parts[1]):
                        result[base + idx] = parts[1].lower()
        finally:
            os.unlink(listfile)
        if on_progress:
            on_progress(min(base + _PS_BATCH_SIZE, len(paths)), len(paths))
    return result


def pick_sample(rows: list[tuple], percent: float, min_count: int,
                seed: str = "") -> list[tuple]:
    """按大小四分层抽样：rows=[(entry_id, size_bytes), ...]。
    抽 max(min_count, ceil(percent%))，不足全取；同 seed 结果确定。"""
    n = len(rows)
    k = max(min_count, math.ceil(n * percent / 100.0))
    if k >= n:
        return list(rows)
    ordered = sorted(rows, key=lambda r: (r[1], r[0]))
    rng = random.Random(f"script-db-verify:{seed}")
    strata = 4
    bounds = [round(i * n / strata) for i in range(strata + 1)]
    quota = [k // strata] * strata
    for i in range(k % strata):
        quota[i] += 1
    picked: list[tuple] = []
    for i in range(strata):
        seg = ordered[bounds[i]:bounds[i + 1]]
        picked.extend(rng.sample(seg, min(len(seg), quota[i])))
    if len(picked) < k:                      # 层内不足时从剩余补齐
        taken = set(picked)
        rest = [r for r in ordered if r not in taken]
        picked.extend(rng.sample(rest, k - len(picked)))
    return picked


def independent_verify(con: sqlite3.Connection, percent: float = 1.0,
                       min_count: int = 100, powershell: str | None = None,
                       on_event=None, on_progress=None) -> dict:
    """对本次 computed valid 哈希抽样，用 Get-FileHash 独立复算比对。
    不一致→双方各重算一次→仍不一致标 unstable＋errors 留痕（醒目告警由调用方输出）。"""
    uuid_, = con.execute("SELECT snapshot_uuid FROM snapshot_info").fetchone()
    roots = dict(con.execute("SELECT root_id, root_path FROM roots"))
    rows = con.execute(
        "SELECT h.entry_id, e.size_bytes, e.root_id, e.rel_path, h.hash_hex"
        " FROM hashes h JOIN entries e ON e.entry_id = h.entry_id"
        " WHERE h.algorithm='sha256' AND h.status='valid'"
        " AND h.origin='computed' AND e.hash_status='done'"
        " ORDER BY e.root_id, e.rel_path").fetchall()
    stats = {"eligible": len(rows), "sampled": 0, "matched": 0,
             "mismatched": 0, "tool_error": 0}
    if not rows:
        return stats
    sample_ids = {eid for eid, _ in pick_sample(
        [(r[0], r[1]) for r in rows], percent, min_count, seed=uuid_)}
    chosen = [r for r in rows if r[0] in sample_ids]
    stats["sampled"] = len(chosen)
    ps = powershell or discover_powershell()[0]
    paths = [os.path.join(roots[rid], rel) for _, _, rid, rel, _ in chosen]
    got = get_filehash_batch(paths, powershell=ps, on_progress=on_progress)
    for (eid, _size, rid, rel, recorded), indep in zip(chosen, got):
        if indep is None:
            stats["tool_error"] += 1
            if on_event:
                on_event("verify_tool_error", rel_path=rel)
            continue
        if indep == recorded:
            stats["matched"] += 1
            continue
        # 复核一次：本工具重读重算＋独立实现重算
        path = os.path.join(roots[rid], rel)
        ours2 = hash_one_file(path)
        indep2 = get_filehash_batch([path], powershell=ps)[0]
        if (ours2["status"] == "valid" and ours2["hash_hex"] == recorded
                and indep2 == recorded):
            stats["matched"] += 1        # 首轮偶发异常，复核通过
            continue
        stats["mismatched"] += 1
        reason = (f"verify_mismatch: recorded={recorded}"
                  f" independent={indep} recheck_ours={ours2['hash_hex']}"
                  f" recheck_independent={indep2}")
        con.execute("UPDATE hashes SET status='unstable', failure_reason=?"
                    " WHERE entry_id=? AND algorithm='sha256'", (reason, eid))
        con.execute("UPDATE entries SET hash_status='unstable'"
                    " WHERE entry_id=?", (eid,))
        con.execute("INSERT INTO errors (entry_id, stage, error_code, message,"
                    " occurred_at_utc) VALUES (?, 'hash', 'verify_mismatch', ?, ?)",
                    (eid, reason, core.now_utc_iso()))
        if on_event:
            on_event("verify_mismatch", rel_path=rel, recorded=recorded,
                     independent=indep)
    con.commit()
    return stats
