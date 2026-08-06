"""统一核验的外部格式工具直接句柄监督器。

每次调用只启动调用方明确指定的 ExifTool、FFprobe 或 7-Zip 进程；不枚举、
附加或终止任何其它进程，也不使用 native Job。stdout／stderr 由专属线程持续
排空，主线程只通过本次 ``Popen`` 对象暂停、timeout、终止并回收精确子进程。
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
import subprocess
import threading
import time
from typing import Callable, Mapping

import Script_DAISY_Lib_DBS_01_Core as core
import Script_DAISY_Lib_DBS_02_Meta as dbmeta
import Script_DAISY_Lib_DBS_03_Hash as dbhash
import Script_DAISY_Lib_DBS_06_Verify as legacy


TIMEOUT_DECISIONS = frozenset((
    "continue_waiting", "skip_and_record", "stop_and_resume",
))
_READ_CHUNK_BYTES = 64 * 1024
_MAX_CAPTURE_BYTES = 8 * 1024 * 1024
_SEVENZIP_TOOL_ERRORS = {
    7: "命令行参数错误",
    8: "内存不足",
    255: "工具报告用户中止",
}


@dataclass(frozen=True)
class ControlledToolOutcome:
    outcome: str
    returncode: int | None
    stdout: bytes
    stderr: bytes
    decision: str
    decision_source: str
    elapsed_seconds: float
    threshold_count: int
    worker_pid: int
    worker_reaped: bool
    events: tuple[dict[str, object], ...]
    stdout_truncated: bool = False
    stderr_truncated: bool = False


@dataclass(frozen=True)
class ExternalFormatOutcome:
    outcome: str
    status: str | None
    detail: str | None
    decision: str
    decision_source: str
    size_bytes: int
    elapsed_seconds: float
    threshold_count: int
    worker_pid: int | None
    worker_exitcode: int | None
    worker_reaped: bool
    events: tuple[dict[str, object], ...]


def _emit(callback, event: str, **payload: object) -> None:
    if callback is not None:
        callback(event, **payload)


def _drain_stream(
    stream,
    chunks: list[bytes],
    truncated: list[bool],
) -> None:
    captured = 0
    try:
        while True:
            block = stream.read(_READ_CHUNK_BYTES)
            if not block:
                return
            payload = bytes(block)
            remaining = max(0, _MAX_CAPTURE_BYTES - captured)
            if remaining:
                kept = payload[:remaining]
                chunks.append(kept)
                captured += len(kept)
            if len(payload) > remaining:
                truncated[0] = True
    except (OSError, ValueError):
        truncated[0] = True
        return


def _finish_process(
    process,
    started_threads: tuple[threading.Thread, ...] = (),
    *,
    terminate: bool,
) -> tuple[int | None, bool, bool]:
    """尽力终止并回收精确 Popen 句柄；清理过程本身不遮蔽原异常。"""
    if terminate and process.poll() is None:
        try:
            process.terminate()
        except (OSError, ProcessLookupError):
            pass
    try:
        process.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except (OSError, ProcessLookupError):
            pass
        try:
            process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            pass
    except (OSError, ValueError):
        pass
    for thread in started_threads:
        thread.join(timeout=2.0)
    for stream in (process.stdout, process.stderr):
        if stream is not None and not stream.closed:
            try:
                stream.close()
            except OSError:
                pass
    for thread in started_threads:
        if thread.is_alive():
            thread.join(timeout=2.0)
    try:
        returncode = process.poll()
    except (OSError, ValueError):
        returncode = getattr(process, "returncode", None)
    streams_drained = all(not thread.is_alive() for thread in started_threads)
    return returncode, returncode is not None, streams_drained


def run_controlled_tool(
    command: list[str],
    *,
    expected_size: int,
    timeout_seconds: float | None = None,
    default_decision: str = "continue_waiting",
    display_name: str,
    control: dbhash.HashWorkerControl | None = None,
    on_event: Callable[..., None] | None = None,
    on_threshold: Callable[..., None] | None = None,
    poll_seconds: float = 0.05,
    _popen_factory=None,
) -> ControlledToolOutcome:
    """监督一个直接工具进程，并保证只通过本次 Popen 句柄回收。"""
    if not command or not isinstance(command[0], str) or not command[0]:
        raise ValueError("外部工具命令不能为空")
    if expected_size < 0:
        raise ValueError("expected_size 不能小于 0")
    if default_decision not in TIMEOUT_DECISIONS:
        raise ValueError(f"未知 timeout 默认处置：{default_decision}")
    if poll_seconds <= 0:
        raise ValueError("poll_seconds 必须大于 0")
    threshold_seconds = (
        dbhash.hash_no_progress_timeout_for_size(expected_size)
        if timeout_seconds is None else float(timeout_seconds)
    )
    if not math.isfinite(threshold_seconds) or threshold_seconds <= 0:
        raise ValueError("timeout_seconds 必须是大于 0 的有限数")

    factory = _popen_factory or subprocess.Popen
    kwargs: dict[str, object] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = factory(list(command), **kwargs)
    worker_pid = int(process.pid)
    if worker_pid <= 0:
        _finish_process(process, terminate=True)
        raise core.PreflightError("外部格式工具没有有效 PID")
    if process.stdout is None or process.stderr is None:
        _finish_process(process, terminate=True)
        raise core.PreflightError("外部格式工具没有可监管的输出管道")

    owned_control = control or dbhash.HashWorkerControl()
    bound = False
    started_threads: list[threading.Thread] = []
    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
    stdout_truncated = [False]
    stderr_truncated = [False]
    stdout_thread = threading.Thread(
        target=_drain_stream,
        args=(process.stdout, stdout_chunks, stdout_truncated),
        name=f"daisy-tool-stdout-{worker_pid}",
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=_drain_stream,
        args=(process.stderr, stderr_chunks, stderr_truncated),
        name=f"daisy-tool-stderr-{worker_pid}",
        daemon=True,
    )
    events: list[dict[str, object]] = []

    def emit(event: str, **payload: object) -> None:
        record = {"event": event, **payload}
        events.append(record)
        _emit(on_event, event, **payload)

    started = time.monotonic()
    timeout_window_started = started
    threshold_count = 0
    outcome = "tool_error"
    decision = "none"
    decision_source = "none"
    terminate = False

    try:
        owned_control.bind_worker(worker_pid)
        bound = True
        for thread in (stdout_thread, stderr_thread):
            thread.start()
            started_threads.append(thread)
        emit(
            "worker_started",
            file=display_name,
            worker_pid=worker_pid,
            implementation="direct_external_tool",
        )
        while True:
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
                emit(
                    "worker_controlled",
                    file=display_name,
                    worker_pid=worker_pid,
                    action=action_name,
                )
                break
            returncode = process.poll()
            if returncode is not None:
                outcome = "completed"
                emit(
                    "worker_completed",
                    file=display_name,
                    worker_pid=worker_pid,
                    worker_exitcode=returncode,
                )
                break

            now = time.monotonic()
            pending = owned_control.take_timeout_decision(worker_pid)
            if pending is not None:
                decision = pending.decision
                decision_source = pending.source
                emit(
                    "stall_decided",
                    file=display_name,
                    worker_pid=worker_pid,
                    decision=decision,
                    decision_source=decision_source,
                    threshold_count=threshold_count,
                )
                if decision == "continue_waiting":
                    timeout_window_started = now
                    owned_control.open_timeout_decision(worker_pid)
                    time.sleep(poll_seconds)
                    continue
                terminate = True
                outcome = (
                    "timeout" if decision == "skip_and_record" else "stopped")
                break

            if now - timeout_window_started >= threshold_seconds:
                threshold_count += 1
                owned_control.open_timeout_decision(worker_pid)
                payload = {
                    "file": display_name,
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
                        emit(
                            "threshold_callback_error",
                            file=display_name,
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
                    file=display_name,
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
                    "timeout" if decision == "skip_and_record" else "stopped")
                break
            time.sleep(poll_seconds)
    finally:
        try:
            returncode, reaped, streams_drained = _finish_process(
                process,
                tuple(started_threads),
                terminate=terminate or outcome != "completed",
            )
            if not streams_drained:
                stdout_truncated[0] = True
                stderr_truncated[0] = True
        finally:
            if bound:
                owned_control.unbind_worker(worker_pid)

    return ControlledToolOutcome(
        outcome=outcome,
        returncode=returncode,
        stdout=b"".join(stdout_chunks),
        stderr=b"".join(stderr_chunks),
        decision=decision,
        decision_source=decision_source,
        elapsed_seconds=max(0.0, time.monotonic() - started),
        threshold_count=threshold_count,
        worker_pid=worker_pid,
        worker_reaped=reaped,
        events=tuple(events),
        stdout_truncated=stdout_truncated[0],
        stderr_truncated=stderr_truncated[0],
    )


def _tool_path(tools: Mapping[str, object], name: str) -> str:
    value = tools.get(name)
    if not isinstance(value, Mapping):
        raise core.PreflightError(f"格式核验缺少 {name} 工具身份")
    path = value.get("path")
    if not isinstance(path, str) or not path:
        raise core.PreflightError(f"格式核验的 {name} 路径无效")
    return path


def _format_outcome(
    source: ControlledToolOutcome,
    *,
    expected_size: int,
    status: str | None,
    detail: str | None,
    started: float,
    events: list[dict[str, object]],
    threshold_count: int,
) -> ExternalFormatOutcome:
    return ExternalFormatOutcome(
        outcome=source.outcome,
        status=status,
        detail=detail,
        decision=source.decision,
        decision_source=source.decision_source,
        size_bytes=expected_size,
        elapsed_seconds=max(0.0, time.monotonic() - started),
        threshold_count=threshold_count + source.threshold_count,
        worker_pid=source.worker_pid,
        worker_exitcode=source.returncode,
        worker_reaped=source.worker_reaped,
        events=tuple([*events, *source.events]),
    )


def _local_format_outcome(
    *,
    expected_size: int,
    status: str,
    detail: str | None,
    started: float,
    outcome: str = "completed",
    threshold_count: int = 0,
    events: tuple[dict[str, object], ...] = (),
) -> ExternalFormatOutcome:
    """返回未启动外部工具的本地头部判定结果。"""
    return ExternalFormatOutcome(
        outcome=outcome,
        status=status,
        detail=detail,
        decision="none",
        decision_source="none",
        size_bytes=expected_size,
        elapsed_seconds=max(0.0, time.monotonic() - started),
        threshold_count=threshold_count,
        worker_pid=None,
        worker_exitcode=None,
        worker_reaped=True,
        events=events,
    )


def _start_error_outcome(
    tool_name: str,
    exc: OSError,
    *,
    expected_size: int,
    started: float,
    threshold_count: int = 0,
    events: tuple[dict[str, object], ...] = (),
) -> ExternalFormatOutcome:
    reason = getattr(exc, "strerror", None) or type(exc).__name__
    return _local_format_outcome(
        expected_size=expected_size,
        status="error",
        detail=f"{tool_name} 启动失败（{reason}）",
        started=started,
        outcome="tool_error",
        threshold_count=threshold_count,
        events=events,
    )


def _process_crash_detail(
    result: ControlledToolOutcome,
    tool_name: str,
) -> str | None:
    if not result.worker_reaped:
        return f"{tool_name} 工具进程未干净回收"
    returncode = result.returncode
    if returncode is None:
        return f"{tool_name} 工具进程没有退出码"
    unsigned = int(returncode) & 0xFFFFFFFF
    if int(returncode) < 0 or unsigned >= 0x80000000:
        return f"{tool_name} 工具进程异常退出（0x{unsigned:08X}）"
    return None


def _controlled(
    command: list[str],
    *,
    expected_size: int,
    timeout_seconds: float | None,
    default_decision: str,
    display_name: str,
    control: dbhash.HashWorkerControl | None,
    on_event,
    on_threshold,
    direct_runner,
) -> ControlledToolOutcome:
    runner = direct_runner or run_controlled_tool
    return runner(
        command,
        expected_size=expected_size,
        timeout_seconds=timeout_seconds,
        default_decision=default_decision,
        display_name=display_name,
        control=control,
        on_event=on_event,
        on_threshold=on_threshold,
    )


def _sevenzip_result(
    result: ControlledToolOutcome,
) -> tuple[str, str | None]:
    if result.returncode == 0:
        return "valid", None
    text = (result.stderr + result.stdout).decode("utf-8", "replace")
    if "Wrong password" in text or "Enter password" in text:
        return "unsupported", "加密压缩包无法完整性测试"
    tool_error = _SEVENZIP_TOOL_ERRORS.get(int(result.returncode or 0))
    if tool_error is not None:
        return "error", f"7z t 工具错误：{tool_error}"
    tail = " | ".join(
        line for line in text.splitlines() if line.strip())[-300:]
    truncated = (
        "（工具输出已截断）"
        if result.stdout_truncated or result.stderr_truncated else ""
    )
    return (
        "invalid",
        f"7z t 退出码 {result.returncode}：{tail}{truncated}",
    )


def _ffprobe_findings(
    path: str,
    kind: str,
    result: ControlledToolOutcome,
) -> list[str]:
    if result.stdout_truncated:
        return ["ffprobe: JSON 输出超过证据上限，无法完成结构解析"]
    document: Mapping[str, object] = {}
    streams: list[Mapping[str, object]] = []
    invalid_shape = False
    try:
        parsed = json.loads(result.stdout.decode("utf-8", "replace"))
        if not isinstance(parsed, Mapping):
            invalid_shape = True
        else:
            document = parsed
            raw_streams = parsed.get("streams")
            if not isinstance(raw_streams, list) or not all(
                    isinstance(stream, Mapping) for stream in raw_streams):
                invalid_shape = True
            else:
                streams = raw_streams
    except (TypeError, ValueError):
        invalid_shape = True
    bad = []
    if result.returncode != 0 or invalid_shape or not streams:
        error = result.stderr.decode("utf-8", "replace").strip()[-200:]
        bad.append(
            f"ffprobe: rc={result.returncode}, streams={len(streams)}"
            + ("，JSON 结构无效" if invalid_shape else "")
            + (f"，{error}" if error else ""))
    elif kind == "audio":
        try:
            size_bytes = os.path.getsize(path)
        except OSError as exc:
            reason = getattr(exc, "strerror", None) or type(exc).__name__
            bad.append(f"ffprobe: 核验期间无法读取文件大小（{reason}）")
            return bad
        if size_bytes > 44:
            return bad
        audio_streams = [
            stream for stream in streams
            if stream.get("codec_type") == "audio"
        ]
        format_payload = document.get("format")
        if not isinstance(format_payload, Mapping):
            format_payload = {}
        format_duration = dbmeta.first_float(
            format_payload.get("duration"))
        stream_durations = [
            dbmeta.first_float(stream.get("duration"))
            for stream in audio_streams
        ]
        if (
            not audio_streams
            or not (format_duration and format_duration > 0)
            and not any(value and value > 0 for value in stream_durations)
        ):
            bad.append("ffprobe: 音频容器只有头部且没有可确认的音频样本")
    return bad


def run_external_format_validator(
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
    _direct_runner=None,
) -> ExternalFormatOutcome:
    """按旧格式判据组合直接 ExifTool／FFprobe／7-Zip 进程结果。"""
    validator = spec.validator
    if validator not in ("ole", "7z", "gif", "media"):
        raise core.PreflightError(
            f"外部格式监督器不接受校验器：{validator}")
    normalized = os.path.abspath(path)
    label = str(display_name or os.path.basename(normalized))
    started = time.monotonic()
    events: list[dict[str, object]] = []
    threshold_count = 0

    if validator == "ole":
        try:
            with open(core.to_extended_path(normalized), "rb") as handle:
                magic = handle.read(len(legacy._OLE_MAGIC))
        except OSError as exc:
            return _local_format_outcome(
                expected_size=expected_size,
                status="invalid",
                detail=str(exc),
                started=started,
            )
        if magic != legacy._OLE_MAGIC:
            return _local_format_outcome(
                expected_size=expected_size,
                status="unsupported",
                detail="不是 OLE 复合文档；可能是 RTF 或扩展名不符",
                started=started,
            )

    if validator in ("ole", "7z"):
        sevenzip = _tool_path(tools, "sevenzip")
        try:
            result = _controlled(
                [sevenzip, "t", "-p", "-y", "-sccUTF-8", normalized],
                expected_size=expected_size,
                timeout_seconds=timeout_seconds,
                default_decision=default_decision,
                display_name=label,
                control=control,
                on_event=on_event,
                on_threshold=on_threshold,
                direct_runner=_direct_runner,
            )
        except OSError as exc:
            return _start_error_outcome(
                "7-Zip", exc,
                expected_size=expected_size,
                started=started,
            )
        if result.outcome != "completed":
            status = "timeout" if result.outcome == "timeout" else None
            return _format_outcome(
                result,
                expected_size=expected_size,
                status=status,
                detail=(
                    "7-Zip 无进展 timeout"
                    if status == "timeout" else None),
                started=started,
                events=events,
                threshold_count=threshold_count,
            )
        crash_detail = _process_crash_detail(result, "7-Zip")
        if crash_detail is not None:
            return _format_outcome(
                result,
                expected_size=expected_size,
                status="error",
                detail=crash_detail,
                started=started,
                events=events,
                threshold_count=threshold_count,
            )
        status, detail = _sevenzip_result(result)
        return _format_outcome(
            result,
            expected_size=expected_size,
            status=status,
            detail=detail,
            started=started,
            events=events,
            threshold_count=threshold_count,
        )

    exiftool = _tool_path(tools, "exiftool")
    exif_args = [
        "-validate", "-a", "-s", "-Warning", "-Error",
        "-charset", "filename=utf8", normalized,
    ]
    dbmeta.guard_exiftool_args(exif_args)
    try:
        exif = _controlled(
            [exiftool, *exif_args],
            expected_size=expected_size,
            timeout_seconds=timeout_seconds,
            default_decision=default_decision,
            display_name=label,
            control=control,
            on_event=on_event,
            on_threshold=on_threshold,
            direct_runner=_direct_runner,
        )
    except OSError as exc:
        return _start_error_outcome(
            "ExifTool", exc,
            expected_size=expected_size,
            started=started,
        )
    if exif.outcome != "completed":
        status = "timeout" if exif.outcome == "timeout" else None
        return _format_outcome(
            exif,
            expected_size=expected_size,
            status=status,
            detail=("ExifTool 无进展 timeout" if status == "timeout" else None),
            started=started,
            events=events,
            threshold_count=threshold_count,
        )
    crash_detail = _process_crash_detail(exif, "ExifTool")
    if crash_detail is not None:
        return _format_outcome(
            exif,
            expected_size=expected_size,
            status="error",
            detail=crash_detail,
            started=started,
            events=[],
            threshold_count=0,
        )
    if exif.stdout_truncated or exif.stderr_truncated:
        return _format_outcome(
            exif,
            expected_size=expected_size,
            status="error",
            detail="ExifTool 输出超过证据上限，无法可靠完成格式分类",
            started=started,
            events=[],
            threshold_count=0,
        )
    findings = legacy.classify_et_findings(legacy.parse_et_text(
        exif.stdout.decode("utf-8", "replace")))
    if exif.returncode not in (0, None) and not findings:
        error = exif.stderr.decode("utf-8", "replace").strip()[-300:]
        return _format_outcome(
            exif,
            expected_size=expected_size,
            status="error",
            detail=f"ExifTool 退出码 {exif.returncode}" + (
                f"：{error}" if error else ""),
            started=started,
            events=[],
            threshold_count=0,
        )

    effective_kind = "image_gif" if validator == "gif" else media_kind
    last = exif
    if effective_kind in legacy._FFPROBE_KINDS:
        events.extend(exif.events)
        threshold_count += exif.threshold_count
        ffprobe = _tool_path(tools, "ffprobe")
        try:
            last = _controlled(
                [
                    ffprobe, "-v", "error", "-print_format", "json",
                    "-show_format", "-show_streams", normalized,
                ],
                expected_size=expected_size,
                timeout_seconds=timeout_seconds,
                default_decision=default_decision,
                display_name=label,
                control=control,
                on_event=on_event,
                on_threshold=on_threshold,
                direct_runner=_direct_runner,
            )
        except OSError as exc:
            return _start_error_outcome(
                "FFprobe", exc,
                expected_size=expected_size,
                started=started,
                threshold_count=threshold_count,
                events=tuple(events),
            )
        if last.outcome != "completed":
            status = "timeout" if last.outcome == "timeout" else None
            return _format_outcome(
                last,
                expected_size=expected_size,
                status=status,
                detail=(
                    "FFprobe 无进展 timeout" if status == "timeout" else None),
                started=started,
                events=events,
                threshold_count=threshold_count,
            )
        crash_detail = _process_crash_detail(last, "FFprobe")
        if crash_detail is not None or last.stderr_truncated:
            return _format_outcome(
                last,
                expected_size=expected_size,
                status="error",
                detail=(
                    crash_detail
                    or "FFprobe 错误输出超过证据上限，无法可靠分类"),
                started=started,
                events=events,
                threshold_count=threshold_count,
            )
        findings.extend(_ffprobe_findings(normalized, effective_kind, last))
    status = "invalid" if findings else "valid"
    return _format_outcome(
        last,
        expected_size=expected_size,
        status=status,
        detail="；".join(findings) if findings else None,
        started=started,
        events=events,
        threshold_count=threshold_count,
    )
