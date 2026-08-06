"""DAISY 运行环境能力模型与隔离探测。

数据库能力继续由 DBS-05 Reader 负责；本模块只描述运行时工具／Python 可选能力。
rawpy 探测始终在本调用新建的 ``spawn`` 子进程中执行，导入失败或 native 崩溃不会
进入 Tk 主进程，也不会影响其它进程。
"""
from __future__ import annotations

from dataclasses import dataclass, field
import multiprocessing
import time
from typing import Callable, Mapping


RUNTIME_CAPABILITY_STATES = frozenset((
    "available",
    "unavailable",
    "incompatible",
    "crashed",
    "timeout",
))
RAW_CAPABILITY_ID = "rawpy_libraw"
RAW_CAPABILITY_TITLE = "RAW 深度校验"


@dataclass(frozen=True)
class RuntimeCapability:
    capability_id: str
    title: str
    state: str
    version: str | None = None
    reason: str | None = None
    provider: str | None = None
    isolated: bool = False
    details: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.state not in RUNTIME_CAPABILITY_STATES:
            raise ValueError(f"未知运行能力状态：{self.state}")
        if self.state == "available" and not self.version:
            raise ValueError("可用运行能力必须包含版本")
        if self.state != "available" and not self.reason:
            raise ValueError("不可用运行能力必须包含原因")

    @property
    def available(self) -> bool:
        return self.state == "available"

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.capability_id,
            "title": self.title,
            "state": self.state,
            "available": self.available,
            "version": self.version,
            "reason": self.reason,
            "provider": self.provider,
            "isolated": self.isolated,
            "details": dict(self.details),
        }


def _version_text(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, (tuple, list)):
        text = ".".join(str(part) for part in value)
    else:
        text = str(value).strip()
    return text or None


def _rawpy_probe_child(connection) -> None:
    """子进程入口；本项目中唯一允许为能力探测导入 rawpy 的位置。"""
    try:
        try:
            import rawpy  # type: ignore[import-not-found]
        except ModuleNotFoundError as exc:
            connection.send({
                "state": "unavailable",
                "reason": f"未安装 rawpy：{exc}",
            })
            return
        except (ImportError, OSError) as exc:
            connection.send({
                "state": "incompatible",
                "reason": (
                    f"rawpy／LibRaw 无法加载：{type(exc).__name__}: {exc}"
                )[:2048],
            })
            return

        version = _version_text(getattr(rawpy, "__version__", None))
        libraw_version = _version_text(
            getattr(rawpy, "libraw_version", None))
        raw_type = getattr(rawpy, "RawPy", None)
        if not version or not callable(getattr(rawpy, "imread", None)) \
                or raw_type is None \
                or not callable(getattr(raw_type, "postprocess", None)):
            connection.send({
                "state": "incompatible",
                "reason": "rawpy 缺少版本、imread 或 RawPy.postprocess API",
                "version": version,
                "libraw_version": libraw_version,
            })
            return
        connection.send({
            "state": "available",
            "version": version,
            "libraw_version": libraw_version,
        })
    except BaseException as exc:
        try:
            connection.send({
                "state": "incompatible",
                "reason": (
                    f"rawpy 探测异常：{type(exc).__name__}: {exc}"
                )[:2048],
            })
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        connection.close()


def finish_owned_process(
    process,
    *,
    terminate: bool,
    join_seconds: float = 2.0,
) -> tuple[int | None, bool]:
    """只回收调用方持有的精确 multiprocessing.Process。"""
    if terminate and process.is_alive():
        process.terminate()
    process.join(timeout=join_seconds)
    if process.is_alive():
        process.terminate()
        process.join(timeout=join_seconds)
    if process.is_alive() and hasattr(process, "kill"):
        process.kill()
        process.join(timeout=join_seconds)
    exitcode = process.exitcode
    reaped = not process.is_alive()
    if reaped:
        process.close()
    return exitcode, reaped


def _raw_capability(
    state: str,
    *,
    version: str | None = None,
    reason: str | None = None,
    details: Mapping[str, object] | None = None,
) -> RuntimeCapability:
    return RuntimeCapability(
        RAW_CAPABILITY_ID,
        RAW_CAPABILITY_TITLE,
        state,
        version=version,
        reason=reason,
        provider="rawpy/LibRaw",
        isolated=True,
        details=dict(details or {}),
    )


def probe_rawpy_capability(
    *,
    timeout_seconds: float = 10.0,
    poll_seconds: float = 0.05,
    _probe_target: Callable[[object], None] | None = None,
) -> RuntimeCapability:
    """在精确 ``spawn`` 子进程中探测 rawpy／LibRaw，并返回结构化状态。"""
    if timeout_seconds <= 0 or poll_seconds <= 0:
        raise ValueError("rawpy 探测 timeout 与 poll 必须大于 0")
    context = multiprocessing.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    process = context.Process(
        target=_probe_target or _rawpy_probe_child,
        args=(send,),
        daemon=True,
    )
    started = time.monotonic()
    process_started = False
    payload: object = None
    timed_out = False
    try:
        try:
            process.start()
            process_started = True
            send.close()
        except Exception as exc:
            receive.close()
            try:
                send.close()
            except OSError:
                pass
            if process_started:
                finish_owned_process(process, terminate=True)
            else:
                process.close()
            return _raw_capability(
                "crashed",
                reason=(
                    f"rawpy 隔离探测无法启动：{type(exc).__name__}: {exc}"
                )[:2048],
                details={"elapsed_seconds": time.monotonic() - started},
            )

        while True:
            if receive.poll(poll_seconds):
                try:
                    payload = receive.recv()
                except EOFError:
                    payload = None
                break
            if not process.is_alive():
                break
            if time.monotonic() - started >= timeout_seconds:
                timed_out = True
                break
    finally:
        receive.close()

    worker_pid = int(process.pid or 0)
    exitcode, reaped = finish_owned_process(
        process, terminate=timed_out or payload is None)
    details: dict[str, object] = {
        "worker_pid": worker_pid,
        "worker_exitcode": exitcode,
        "worker_reaped": reaped,
        "elapsed_seconds": max(0.0, time.monotonic() - started),
    }
    if timed_out:
        return _raw_capability(
            "timeout",
            reason=f"rawpy 隔离探测超过 {timeout_seconds:g}s",
            details=details,
        )
    if not reaped or exitcode != 0:
        return _raw_capability(
            "crashed",
            reason=(
                "rawpy 隔离探测异常退出"
                f"（exitcode={exitcode!r}，reaped={reaped}）"
            ),
            details=details,
        )
    if not isinstance(payload, dict):
        return _raw_capability(
            "crashed",
            reason="rawpy 隔离探测未返回有效对象",
            details=details,
        )
    state = str(payload.get("state") or "")
    if state not in ("available", "unavailable", "incompatible"):
        return _raw_capability(
            "crashed",
            reason=f"rawpy 隔离探测返回未知状态：{state!r}",
            details=details,
        )
    version = _version_text(payload.get("version"))
    reason_value = payload.get("reason")
    reason = str(reason_value)[:2048] if reason_value is not None else None
    libraw_version = _version_text(payload.get("libraw_version"))
    if libraw_version:
        details["libraw_version"] = libraw_version
    if state == "available" and not version:
        return _raw_capability(
            "incompatible",
            reason="rawpy 隔离探测未返回版本",
            details=details,
        )
    if state != "available" and not reason:
        reason = "rawpy／LibRaw 当前不可用"
    return _raw_capability(
        state,
        version=version,
        reason=reason,
        details=details,
    )


def probe_runtime_capabilities(
    capability_ids: tuple[str, ...] = (RAW_CAPABILITY_ID,),
    *,
    rawpy_timeout_seconds: float = 10.0,
    _rawpy_probe_target: Callable[[object], None] | None = None,
) -> dict[str, RuntimeCapability]:
    """统一运行能力注册表入口；未知能力显式拒绝，不静默猜测。"""
    result: dict[str, RuntimeCapability] = {}
    for capability_id in capability_ids:
        if capability_id != RAW_CAPABILITY_ID:
            raise KeyError(f"未知运行能力：{capability_id}")
        result[capability_id] = probe_rawpy_capability(
            timeout_seconds=rawpy_timeout_seconds,
            _probe_target=_rawpy_probe_target,
        )
    return result
