"""外部工具的统一运行故障、证据与连续失败熔断原语。

本模块只监管调用方本次创建的精确进程／会话，不枚举、附加或终止其他进程。
不同后端仍可保留长驻会话、一次性进程或多进程工作进程的实现差异。
"""
from __future__ import annotations

from dataclasses import dataclass
import os
import subprocess
import threading
import time
from typing import Callable

import Script_DAISY_Lib_Snapshot_Core as core


READ_CHUNK_BYTES = 64 * 1024
DEFAULT_MAX_STDOUT_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_STDERR_BYTES = 256 * 1024
DEFAULT_CIRCUIT_THRESHOLD = 3


@dataclass(frozen=True)
class ToolFaultEvidence:
    tool: str
    operation: str
    failure_kind: str
    message: str
    pid: int | None = None
    returncode: int | None = None
    errno: int | None = None
    tool_session_id: str | None = None
    stderr_tail: str | None = None
    retry_count: int = 0
    restart_count: int = 0

    @property
    def signature(self) -> tuple[object, ...]:
        return (
            self.tool,
            self.operation,
            self.failure_kind,
            self.returncode,
            self.errno,
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "tool": self.tool,
            "operation": self.operation,
            "failure_kind": self.failure_kind,
            "message": self.message,
            "pid": self.pid,
            "returncode": self.returncode,
            "returncode_hex": format_returncode(self.returncode),
            "errno": self.errno,
            "tool_session_id": self.tool_session_id,
            "stderr_tail": self.stderr_tail,
            "retry_count": self.retry_count,
            "restart_count": self.restart_count,
        }


class ToolRuntimeFailure(RuntimeError):
    """源文件诊断之外的外部工具运行故障。"""

    def __init__(
        self,
        evidence: ToolFaultEvidence | tuple[ToolFaultEvidence, ...],
        *,
        recovered: bool,
    ) -> None:
        rows = evidence if isinstance(evidence, tuple) else (evidence,)
        if not rows:
            raise ValueError("工具故障证据不能为空")
        self.evidence = rows
        self.recovered = bool(recovered)
        super().__init__(rows[-1].message)

    @property
    def latest(self) -> ToolFaultEvidence:
        return self.evidence[-1]

    @property
    def signature(self) -> tuple[object, ...]:
        return self.latest.signature

    def as_dict(self) -> dict[str, object]:
        return {
            "recovered": self.recovered,
            "failures": [row.as_dict() for row in self.evidence],
        }


class ToolProcessTimeout(subprocess.TimeoutExpired):
    """一次性工具超时；保留受控进程证据并兼容 TimeoutExpired。"""

    def __init__(
        self,
        cmd: list[str],
        timeout: float,
        *,
        pid: int,
        output: bytes,
        stderr: bytes,
        reaped: bool,
    ) -> None:
        super().__init__(cmd, timeout, output=output, stderr=stderr)
        self.pid = int(pid)
        self.reaped = bool(reaped)


@dataclass(frozen=True)
class ToolProcessResult:
    command: tuple[str, ...]
    returncode: int
    stdout: bytes
    stderr: bytes
    elapsed_seconds: float
    pid: int
    reaped: bool
    stdout_truncated: bool
    stderr_truncated: bool


@dataclass(frozen=True)
class ToolCircuitSnapshot:
    tool: str
    threshold: int
    consecutive_failures: int
    signature: tuple[object, ...]
    entry_ids: tuple[int, ...]
    opened: bool
    recovered: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "tool": self.tool,
            "threshold": self.threshold,
            "consecutive_failures": self.consecutive_failures,
            "signature": [value for value in self.signature],
            "entry_ids": list(self.entry_ids),
            "opened": self.opened,
            "recovered": self.recovered,
        }


class ConsecutiveToolFailureCircuit:
    """按工具和故障签名计数；健康结果只重置对应工具。"""

    def __init__(self, threshold: int = DEFAULT_CIRCUIT_THRESHOLD) -> None:
        if isinstance(threshold, bool) or int(threshold) <= 0:
            raise ValueError("工具熔断阈值必须是正整数")
        self.threshold = int(threshold)
        self._states: dict[
            str, tuple[tuple[object, ...], list[int], bool]
        ] = {}

    def record_success(self, tool: str) -> None:
        self._states.pop(str(tool), None)

    def record_failure(
        self,
        entry_id: int,
        failure: ToolRuntimeFailure,
    ) -> ToolCircuitSnapshot:
        tool = failure.latest.tool
        signature = failure.signature
        previous = self._states.get(tool)
        if previous is None or previous[0] != signature:
            entries = [int(entry_id)]
        else:
            entries = [*previous[1], int(entry_id)]
        self._states[tool] = (signature, entries, failure.recovered)
        opened = not failure.recovered or len(entries) >= self.threshold
        return ToolCircuitSnapshot(
            tool=tool,
            threshold=self.threshold,
            consecutive_failures=len(entries),
            signature=signature,
            entry_ids=tuple(entries),
            opened=opened,
            recovered=failure.recovered,
        )


class _BoundedCapture:
    def __init__(self, limit: int, *, keep_tail: bool) -> None:
        if isinstance(limit, bool) or int(limit) <= 0:
            raise ValueError("工具输出上限必须是正整数")
        self.limit = int(limit)
        self.keep_tail = bool(keep_tail)
        self.data = bytearray()
        self.truncated = False
        self.read_error = False

    def add(self, payload: bytes) -> None:
        if not payload:
            return
        if self.keep_tail:
            self.data.extend(payload)
            if len(self.data) > self.limit:
                del self.data[:-self.limit]
                self.truncated = True
            return
        remaining = max(0, self.limit - len(self.data))
        if remaining:
            self.data.extend(payload[:remaining])
        if len(payload) > remaining:
            self.truncated = True


def _drain_stream(stream, capture: _BoundedCapture) -> None:
    try:
        while True:
            payload = stream.read(READ_CHUNK_BYTES)
            if not payload:
                return
            capture.add(bytes(payload))
    except (OSError, ValueError):
        capture.read_error = True


def _finish_process(
    process,
    threads: tuple[threading.Thread, ...],
    *,
    terminate: bool,
) -> tuple[int | None, bool, bool]:
    if terminate:
        try:
            if process.poll() is None:
                process.terminate()
        except (OSError, ProcessLookupError, ValueError):
            pass
    try:
        process.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except (OSError, ProcessLookupError, ValueError):
            pass
        try:
            process.wait(timeout=2.0)
        except (OSError, subprocess.TimeoutExpired, ValueError):
            pass
    except (OSError, ValueError):
        pass
    for thread in threads:
        thread.join(timeout=2.0)
    for stream in (getattr(process, "stdout", None),
                   getattr(process, "stderr", None)):
        if stream is not None and not stream.closed:
            try:
                stream.close()
            except OSError:
                pass
    for thread in threads:
        if thread.is_alive():
            thread.join(timeout=2.0)
    try:
        returncode = process.poll()
    except (OSError, ValueError):
        returncode = getattr(process, "returncode", None)
    return (
        returncode,
        returncode is not None,
        all(not thread.is_alive() for thread in threads),
    )


def format_returncode(returncode: int | None) -> str | None:
    if returncode is None:
        return None
    value = int(returncode)
    unsigned = value & 0xFFFFFFFF
    if value < 0 or unsigned >= 0x80000000:
        return f"0x{unsigned:08X}"
    return str(value)


def is_native_crash_returncode(returncode: int | None) -> bool:
    if returncode is None:
        return False
    value = int(returncode)
    return value < 0 or (value & 0xFFFFFFFF) >= 0x80000000


def failure_from_process(
    result: ToolProcessResult,
    *,
    tool: str,
    operation: str,
    failure_kind: str,
    recovered: bool,
    message: str | None = None,
) -> ToolRuntimeFailure:
    code = format_returncode(result.returncode)
    detail = message or f"{tool} 工具进程异常退出（{code}）"
    evidence = ToolFaultEvidence(
        tool=tool,
        operation=operation,
        failure_kind=failure_kind,
        message=detail,
        pid=result.pid,
        returncode=result.returncode,
        stderr_tail=result.stderr.decode("utf-8", "replace")[-2000:] or None,
    )
    return ToolRuntimeFailure(evidence, recovered=recovered)


def run_bounded_tool(
    command: list[str],
    *,
    tool: str,
    operation: str,
    timeout_seconds: float,
    max_stdout_bytes: int = DEFAULT_MAX_STDOUT_BYTES,
    max_stderr_bytes: int = DEFAULT_MAX_STDERR_BYTES,
    _popen_factory: Callable[..., object] | None = None,
) -> ToolProcessResult:
    """运行一次性工具，持续排空有限输出并精确回收本次 Popen。"""
    if not command or not isinstance(command[0], str) or not command[0]:
        raise ValueError("外部工具命令不能为空")
    timeout = float(timeout_seconds)
    if timeout <= 0:
        raise ValueError("外部工具超时阈值必须大于 0")
    stdout_capture = _BoundedCapture(max_stdout_bytes, keep_tail=False)
    stderr_capture = _BoundedCapture(max_stderr_bytes, keep_tail=True)
    core.configure_windows_worker_error_mode()
    kwargs: dict[str, object] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    factory = _popen_factory or subprocess.Popen
    try:
        process = factory(list(command), **kwargs)
    except (OSError, ValueError) as exc:
        reason = getattr(exc, "strerror", None) or str(exc)
        evidence = ToolFaultEvidence(
            tool=tool,
            operation=operation,
            failure_kind="start_failed",
            message=f"{tool} 启动失败：{reason}",
            errno=getattr(exc, "errno", None),
        )
        raise ToolRuntimeFailure(evidence, recovered=False) from exc
    pid = int(getattr(process, "pid", 0) or 0)
    if pid <= 0 or process.stdout is None or process.stderr is None:
        _finish_process(process, (), terminate=True)
        evidence = ToolFaultEvidence(
            tool=tool,
            operation=operation,
            failure_kind="start_invalid",
            message=f"{tool} 没有可监管的 PID 或输出管道",
            pid=pid or None,
        )
        raise ToolRuntimeFailure(evidence, recovered=False)
    threads = (
        threading.Thread(
            target=_drain_stream,
            args=(process.stdout, stdout_capture),
            name=f"daisy-{tool}-stdout-{pid}",
            daemon=True,
        ),
        threading.Thread(
            target=_drain_stream,
            args=(process.stderr, stderr_capture),
            name=f"daisy-{tool}-stderr-{pid}",
            daemon=True,
        ),
    )
    started = time.monotonic()
    started_threads: list[threading.Thread] = []
    try:
        for thread in threads:
            thread.start()
            started_threads.append(thread)
    except RuntimeError as exc:
        returncode, reaped, _drained = _finish_process(
            process, tuple(started_threads), terminate=True)
        evidence = ToolFaultEvidence(
            tool=tool,
            operation=operation,
            failure_kind="monitor_start_failed",
            message=f"{tool} 输出监管线程启动失败：{exc}",
            pid=pid,
            returncode=returncode,
        )
        raise ToolRuntimeFailure(
            evidence, recovered=False) from exc

    timed_out = False
    wait_failure: OSError | ValueError | None = None
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
    except (OSError, ValueError) as exc:
        wait_failure = exc
    finally:
        returncode, reaped, drained = _finish_process(
            process,
            tuple(started_threads),
            terminate=timed_out or wait_failure is not None,
        )
    stdout = bytes(stdout_capture.data)
    stderr = bytes(stderr_capture.data)
    if returncode is None or not reaped or not drained:
        evidence = ToolFaultEvidence(
            tool=tool,
            operation=operation,
            failure_kind="cleanup_failed",
            message=f"{tool} 工具进程未能干净回收",
            pid=pid,
            returncode=returncode,
            stderr_tail=stderr.decode("utf-8", "replace")[-2000:] or None,
        )
        raise ToolRuntimeFailure(evidence, recovered=False)
    if wait_failure is not None:
        reason = getattr(wait_failure, "strerror", None) or str(wait_failure)
        evidence = ToolFaultEvidence(
            tool=tool,
            operation=operation,
            failure_kind="supervision_failed",
            message=f"{tool} 进程等待失败：{reason}",
            pid=pid,
            returncode=returncode,
            errno=getattr(wait_failure, "errno", None),
            stderr_tail=stderr.decode("utf-8", "replace")[-2000:] or None,
        )
        raise ToolRuntimeFailure(evidence, recovered=False) from wait_failure
    if timed_out:
        raise ToolProcessTimeout(
            list(command), timeout,
            pid=pid,
            output=stdout,
            stderr=stderr,
            reaped=reaped,
        )
    return ToolProcessResult(
        command=tuple(command),
        returncode=int(returncode),
        stdout=stdout,
        stderr=stderr,
        elapsed_seconds=max(0.0, time.monotonic() - started),
        pid=pid,
        reaped=True,
        stdout_truncated=stdout_capture.truncated or stdout_capture.read_error,
        stderr_truncated=stderr_capture.truncated or stderr_capture.read_error,
    )
