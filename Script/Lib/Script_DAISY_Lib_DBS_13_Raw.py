"""RAW 深度校验候选识别与每文件隔离解码 worker。

父进程不导入 rawpy。每个候选文件由本次调用创建的独立 ``spawn`` 子进程执行
``rawpy.imread(...).postprocess()``；像素数组只存在于子进程，验证非空后立即丢弃。
本模块不连接或修改 SQLite，扫描恢复／伴随报告由上层编排。
"""
from __future__ import annotations

from dataclasses import dataclass
import multiprocessing
import os
import time
from typing import Callable

import Script_DAISY_Lib_DBS_02_Meta as dbmeta
import Script_DAISY_Lib_DBS_03_Hash as dbhash
import Script_DAISY_Lib_ENV_01_Capabilities as envcap


RAW_EXTENSIONS = frozenset((
    "3fr", "ari", "arw", "bay", "cap", "cr2", "cr3", "crw",
    "dcr", "dcs", "dng", "drf", "eip", "erf", "fff", "gpr",
    "iiq", "k25", "kdc", "mdc", "mef", "mos", "mrw", "nef",
    "nrw", "obm", "orf", "pef", "ptx", "pxn", "r3d", "raf",
    "raw", "rw2", "rwl", "rwz", "sr2", "srf", "srw", "x3f",
))
RAW_EVENT_LIMIT = 512


@dataclass(frozen=True)
class RawDecodeOutcome:
    outcome: str
    status: str | None
    code: str | None
    detail: str | None
    decision: str
    decision_source: str
    control_action: str | None
    size_bytes: int
    elapsed_seconds: float
    threshold_seconds: float
    threshold_count: int
    worker_pid: int
    worker_exitcode: int | None
    worker_reaped: bool
    rawpy_version: str | None
    libraw_version: str | None
    width: int | None
    height: int | None
    channels: int | None
    pixel_count: int | None
    decoded_bytes: int | None
    events: tuple[dict[str, object], ...]
    events_truncated: bool

    @property
    def succeeded(self) -> bool:
        return (
            self.outcome == "completed"
            and self.status == "valid"
            and self.worker_reaped
            and self.worker_exitcode == 0
            and int(self.pixel_count or 0) > 0
            and int(self.decoded_bytes or 0) > 0
        )


def is_raw_candidate(extension_or_path: str) -> bool:
    text = str(extension_or_path).strip().casefold()
    if "/" in text or "\\" in text:
        text = os.path.splitext(text)[1].lstrip(".")
    else:
        text = text.lstrip(".")
    return text in RAW_EXTENSIONS


def raw_timeout_policy() -> dict[str, object]:
    """沿用 ExifTool 的 90s／9 GiB 阶梯，避免出现第二套模糊阈值。"""
    return dict(dbmeta.exiftool_timeout_policy())


def raw_timeout_for_size(
    size_bytes: int | None,
    policy: dict[str, object] | None = None,
) -> int:
    return dbmeta.exiftool_timeout_for_size(
        size_bytes,
        raw_timeout_policy() if policy is None else policy,
    )


def _version_text(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, (tuple, list)):
        text = ".".join(str(part) for part in value)
    else:
        text = str(value).strip()
    return text or None


def _raw_exception_result(exc: BaseException) -> dict[str, object]:
    name = type(exc).__name__
    detail = f"{name}: {exc}"[:2048]
    if name == "LibRawFileUnsupportedError":
        return {
            "kind": "result",
            "status": "unsupported",
            "code": "raw_unsupported",
            "detail": None,
        }
    if isinstance(exc, MemoryError):
        return {
            "kind": "result",
            "status": "error",
            "code": "memory_error",
            "detail": detail,
        }
    if isinstance(exc, (OSError, EOFError)):
        return {
            "kind": "result",
            "status": "invalid",
            "code": "read_error",
            "detail": detail,
        }
    if name.startswith("LibRaw"):
        return {
            "kind": "result",
            "status": "invalid",
            "code": "decode_error",
            "detail": detail,
        }
    return {
        "kind": "result",
        "status": "error",
        "code": "worker_error",
        "detail": detail,
    }


def _raw_decode_worker_child(connection) -> None:
    """每文件子进程入口；rawpy 导入、文件打开与像素解码均只发生在这里。"""
    try:
        connection.send({"kind": "ready"})
        request = connection.recv()
        if not isinstance(request, dict) or not request.get("path"):
            raise ValueError("RAW worker 请求缺少路径")
        try:
            import rawpy  # type: ignore[import-not-found]
        except (ImportError, OSError) as exc:
            connection.send({
                "kind": "result",
                "status": "error",
                "code": "rawpy_unavailable",
                "detail": (
                    f"rawpy／LibRaw 无法加载：{type(exc).__name__}: {exc}"
                )[:2048],
            })
            return

        rawpy_version = _version_text(getattr(rawpy, "__version__", None))
        libraw_version = _version_text(
            getattr(rawpy, "libraw_version", None))
        try:
            with rawpy.imread(str(request["path"])) as raw:
                pixels = raw.postprocess()
                shape = tuple(int(value) for value in (
                    getattr(pixels, "shape", ()) or ()))
                pixel_count = int(getattr(pixels, "size", 0) or 0)
                decoded_bytes = int(getattr(pixels, "nbytes", 0) or 0)
                if len(shape) < 2 or shape[0] <= 0 or shape[1] <= 0 \
                        or pixel_count <= 0 or decoded_bytes <= 0:
                    raise ValueError("postprocess 返回空像素缓冲或无效尺寸")
                height = shape[0]
                width = shape[1]
                channels = shape[2] if len(shape) >= 3 else 1
                del pixels
        except BaseException as exc:
            payload = _raw_exception_result(exc)
            payload.update({
                "rawpy_version": rawpy_version,
                "libraw_version": libraw_version,
            })
            connection.send(payload)
            return
        connection.send({
            "kind": "result",
            "status": "valid",
            "code": None,
            "detail": None,
            "rawpy_version": rawpy_version,
            "libraw_version": libraw_version,
            "width": width,
            "height": height,
            "channels": channels,
            "pixel_count": pixel_count,
            "decoded_bytes": decoded_bytes,
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


def run_raw_decode_worker(
    path: str,
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
    _worker_target: Callable[[object], None] | None = None,
) -> RawDecodeOutcome:
    """监督一个 RAW 深度解码 worker；只终止和等待本次精确子进程。"""
    if expected_size < 0:
        raise ValueError("expected_size 不能小于 0")
    if default_decision not in dbhash.HASH_TIMEOUT_DECISIONS:
        raise ValueError(f"未知 timeout 默认处置：{default_decision}")
    if poll_seconds <= 0 or worker_start_timeout_seconds <= 0:
        raise ValueError("poll 与启动 timeout 必须大于 0")
    threshold_seconds = float(
        raw_timeout_for_size(expected_size)
        if timeout_seconds is None else timeout_seconds)
    if threshold_seconds <= 0:
        raise ValueError("timeout_seconds 必须大于 0")

    normalized = os.path.abspath(path)
    label = str(display_name or os.path.basename(normalized))
    owned_control = control or dbhash.HashWorkerControl()
    events: list[dict[str, object]] = []
    events_truncated = False

    def emit(event: str, **payload: object) -> None:
        nonlocal events_truncated
        record = {"event": event, **payload}
        if len(events) < RAW_EVENT_LIMIT:
            events.append(record)
        else:
            events_truncated = True
        if on_event is not None:
            try:
                on_event(event, **payload)
            except Exception:
                pass

    context = multiprocessing.get_context("spawn")
    receive, send = context.Pipe(duplex=True)
    process = context.Process(
        target=_worker_target or _raw_decode_worker_child,
        args=(send,),
        daemon=True,
    )
    process_started = False
    try:
        process.start()
        process_started = True
        send.close()
        worker_pid = int(process.pid)
        owned_control.bind_worker(worker_pid)
    except Exception:
        try:
            receive.close()
        except OSError:
            pass
        try:
            send.close()
        except OSError:
            pass
        if process_started:
            envcap.finish_owned_process(process, terminate=True)
        else:
            process.close()
        raise

    emit(
        "worker_started",
        file=label,
        worker_pid=worker_pid,
        implementation="rawpy_spawn_worker",
    )
    started = time.monotonic()
    ready = False
    request_sent = False
    terminate = False
    outcome = "crashed"
    status = None
    code = None
    detail = None
    decision = "none"
    decision_source = "none"
    control_action = None
    threshold_count = 0
    timeout_window_started = started
    rawpy_version = None
    libraw_version = None
    width = None
    height = None
    channels = None
    pixel_count = None
    decoded_bytes = None

    try:
        while not ready:
            try:
                has_message = receive.poll(poll_seconds)
            except (BrokenPipeError, EOFError, OSError):
                code = "worker_crashed"
                detail = "RAW worker 在握手期间断开控制管道"
                break
            if has_message:
                try:
                    message = receive.recv()
                except EOFError:
                    message = None
                if isinstance(message, dict) and message.get("kind") == "ready":
                    ready = True
                    break
            if not process.is_alive():
                detail = "RAW worker 在握手前退出"
                code = "worker_crashed"
                break
            if time.monotonic() - started >= worker_start_timeout_seconds:
                detail = "RAW worker 启动超时"
                code = "worker_start_timeout"
                terminate = True
                break
        if ready:
            receive.send({"path": normalized})
            request_sent = True
            emit("worker_ready", file=label, worker_pid=worker_pid)

        while ready and request_sent:
            action = owned_control.current()
            if action is not None:
                control_action, action_source = action
                outcome = {
                    "pause": "paused",
                    "save_exit": "save_exit",
                    "stop": "stopped",
                }.get(control_action, "stopped")
                decision = "stop_and_resume"
                decision_source = action_source
                detail = control_action
                code = "controlled_boundary"
                terminate = True
                emit(
                    "worker_controlled", file=label,
                    action=control_action, source=action_source)
                break
            try:
                has_message = receive.poll(poll_seconds)
            except (BrokenPipeError, EOFError, OSError):
                code = "worker_crashed"
                detail = "RAW worker 未返回结果即断开控制管道"
                break
            if has_message:
                try:
                    message = receive.recv()
                except EOFError:
                    message = None
                if isinstance(message, dict) and message.get("kind") == "result":
                    status = str(message.get("status") or "error")
                    code_value = message.get("code")
                    code = str(code_value) if code_value is not None else None
                    detail_value = message.get("detail")
                    detail = (
                        str(detail_value)[:2048]
                        if detail_value is not None else None)
                    rawpy_version = _version_text(
                        message.get("rawpy_version"))
                    libraw_version = _version_text(
                        message.get("libraw_version"))
                    if status == "valid":
                        try:
                            width = int(message["width"])
                            height = int(message["height"])
                            channels = int(message["channels"])
                            pixel_count = int(message["pixel_count"])
                            decoded_bytes = int(message["decoded_bytes"])
                        except (KeyError, TypeError, ValueError):
                            status = "error"
                            code = "invalid_worker_result"
                            detail = "RAW worker 成功结果缺少有效像素尺寸"
                        if min(
                            width or 0,
                            height or 0,
                            channels or 0,
                            pixel_count or 0,
                            decoded_bytes or 0,
                        ) <= 0:
                            status = "error"
                            code = "empty_decode"
                            detail = "RAW worker 返回空像素缓冲"
                    if status not in (
                            "valid", "unsupported", "invalid", "error"):
                        status = "error"
                        code = "unknown_worker_status"
                        detail = "RAW worker 返回未知状态"
                    outcome = "completed"
                    emit("worker_completed", file=label, status=status)
                    break
                if isinstance(message, dict) and message.get("kind") == "crash":
                    code = "worker_crashed"
                    detail = (
                        f"{message.get('error_type')}: {message.get('error')}"
                    )[:2048]
                    emit("worker_crashed", file=label, error=detail)
                    break
            if not process.is_alive():
                code = code or "worker_crashed"
                detail = detail or "RAW worker 未返回结果"
                break

            now = time.monotonic()
            pending = owned_control.take_timeout_decision(worker_pid)
            if pending is not None:
                decision = pending.decision
                decision_source = pending.source
                emit(
                    "threshold_decided", file=label, worker_pid=worker_pid,
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
                code = (
                    "raw_no_progress_timeout"
                    if outcome == "timeout" else "stop_and_resume")
                detail = code
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
                        emit(
                            "threshold_callback_error",
                            file=label,
                            error=str(exc)[:2048],
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
                code = (
                    "raw_no_progress_timeout"
                    if outcome == "timeout" else "stop_and_resume")
                detail = code
                break
    finally:
        receive.close()
        try:
            exitcode, reaped = envcap.finish_owned_process(
                process,
                terminate=terminate or outcome != "completed",
            )
        finally:
            owned_control.unbind_worker(worker_pid)

    if outcome == "completed" and (not reaped or exitcode != 0):
        outcome = "crashed"
        status = "error"
        code = "worker_not_reaped"
        detail = "RAW worker 未干净退出并回收"
    elif outcome == "timeout":
        status = "timeout"
    elif outcome == "crashed":
        status = "error"
        code = code or "worker_crashed"
    return RawDecodeOutcome(
        outcome=outcome,
        status=status,
        code=code,
        detail=detail,
        decision=decision,
        decision_source=decision_source,
        control_action=control_action,
        size_bytes=expected_size,
        elapsed_seconds=max(0.0, time.monotonic() - started),
        threshold_seconds=threshold_seconds,
        threshold_count=threshold_count,
        worker_pid=worker_pid,
        worker_exitcode=exitcode,
        worker_reaped=reaped,
        rawpy_version=rawpy_version,
        libraw_version=libraw_version,
        width=width,
        height=height,
        channels=channels,
        pixel_count=pixel_count,
        decoded_bytes=decoded_bytes,
        events=tuple(events),
        events_truncated=events_truncated,
    )
