"""smartctl 发现与完整只读 SMART 读取。"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import Script_DAISY_Lib_Storage_Core as core
import Script_DAISY_Lib_Tool_Runtime as toolruntime


SMARTCTL_ENV = "SMARTCTL_PATH"
SMARTCTL_CANDIDATES = (
    Path(r"C:\Program Files\smartmontools\bin\smartctl.exe"),
    Path(r"C:\Program Files (x86)\smartmontools\bin\smartctl.exe"),
)
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
MINIMUM_VERSION = (7, 5)

MUTATING_OR_ACTIVE_OPTIONS = frozenset(
    {
        "-s",
        "--smart",
        "-t",
        "--test",
        "-C",
        "--captive",
        "-X",
        "--abort",
        "--set",
        "--drivedb",
        "--download",
    }
)


@dataclass(frozen=True)
class SmartctlScan:
    devices: tuple[core.SmartDevice, ...]
    warnings: tuple[str, ...]
    executable: str
    version: str
    command: tuple[str, ...]


def find_smartctl(explicit: str | os.PathLike[str] | None = None) -> Path:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    configured = os.environ.get(SMARTCTL_ENV, "").strip()
    if configured:
        candidates.append(Path(configured))
    found = shutil.which("smartctl")
    if found:
        candidates.append(Path(found))
    candidates.extend(SMARTCTL_CANDIDATES)

    seen: set[str] = set()
    for candidate in candidates:
        key = os.path.normcase(os.path.abspath(candidate))
        if key in seen:
            continue
        seen.add(key)
        if candidate.is_file():
            return candidate.resolve()
    raise core.DaisySmartError(
        "未找到 smartctl.exe。请安装 smartmontools，或通过 SMARTCTL_PATH "
        "环境变量指定可执行文件。"
    )

def _run(arguments: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    operation = (
        "version"
        if "--version" in arguments else
        "device_discovery"
        if "--scan-open" in arguments else
        "device_read"
    )
    try:
        result = toolruntime.run_bounded_tool(
            arguments,
            tool="smartctl",
            operation=operation,
            timeout_seconds=float(timeout),
        )
    except toolruntime.ToolProcessTimeout as exc:
        raise core.DaisySmartError(f"smartctl 运行超过 {timeout} 秒。") from exc
    except toolruntime.ToolRuntimeFailure as exc:
        evidence = exc.latest
        code = toolruntime.format_returncode(evidence.returncode)
        suffix = f"（退出码 {code}）" if code is not None else ""
        raise core.DaisySmartError(
            f"smartctl 工具运行故障：{evidence.failure_kind}{suffix}；"
            f"{evidence.message}"
        ) from exc
    if toolruntime.is_native_crash_returncode(result.returncode):
        failure = toolruntime.failure_from_process(
            result,
            tool="smartctl",
            operation=operation,
            failure_kind="native_crash",
            recovered=False,
        )
        raise core.DaisySmartError(
            "smartctl 工具进程发生原生崩溃："
            f"{failure.latest.message}"
        ) from failure
    if result.stdout_truncated or result.stderr_truncated:
        stream = "stdout" if result.stdout_truncated else "stderr"
        failure = toolruntime.failure_from_process(
            result,
            tool="smartctl",
            operation=operation,
            failure_kind="output_limit_exceeded",
            recovered=False,
            message=f"smartctl {stream} 超出安全采集上限",
        )
        raise core.DaisySmartError(failure.latest.message) from failure
    return subprocess.CompletedProcess(
        list(arguments),
        result.returncode,
        stdout=result.stdout.decode("utf-8", "replace"),
        stderr=result.stderr.decode("utf-8", "replace"),
    )


def _parse_json(stdout: str, stderr: str, purpose: str) -> dict[str, Any]:
    text = core.normalise_text(stdout).lstrip("\ufeff").strip()
    if not text:
        detail = core.normalise_text(stderr).strip() or "没有返回内容"
        raise core.DaisySmartError(f"{purpose}未返回 JSON：{detail}")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise core.DaisySmartError(
            f"{purpose}返回了无效 JSON：{exc}\n\n{text[:800]}"
        ) from exc
    if not isinstance(payload, dict):
        raise core.DaisySmartError(f"{purpose}返回的 JSON 根节点不是对象。")
    return payload


def _messages(payload: dict[str, Any]) -> list[str]:
    smartctl = payload.get("smartctl")
    if not isinstance(smartctl, dict):
        return []
    messages: list[str] = []
    for item in core.as_list(smartctl.get("messages")):
        text = (
            core.clean_text(item.get("string"))
            if isinstance(item, dict) else core.clean_text(item)
        )
        if text:
            messages.append(text)
    return messages


def messages(payload: dict[str, Any]) -> tuple[str, ...]:
    return core.unique_nonempty(_messages(payload))


def windows_disk_number(*values: str) -> int | None:
    combined = " ".join(value for value in values if value)
    match = re.search(r"PhysicalDrive(\d+)", combined, flags=re.IGNORECASE)
    if match:
        return int(match.group(1))
    match = re.search(r"/dev/pd(\d+)", combined, flags=re.IGNORECASE)
    if match:
        return int(match.group(1))
    match = re.search(r"/dev/sd([a-z]+)(?:\s|$)", combined, flags=re.IGNORECASE)
    if not match:
        return None
    number = 0
    for character in match.group(1).lower():
        number = number * 26 + ord(character) - ord("a") + 1
    return number - 1


def build_version_command(executable: str | os.PathLike[str]) -> list[str]:
    return [str(executable), "--version"]


def build_scan_command(executable: str | os.PathLike[str]) -> list[str]:
    command = [str(executable), "--scan-open", "--json=c"]
    assert_read_only_command(command, purpose="scan")
    return command


def build_read_command(
    executable: str | os.PathLike[str],
    device: core.SmartDevice,
) -> list[str]:
    if not device.name or not device.device_type:
        raise core.DaisySmartError("smartctl 设备枚举结果缺少设备名称或设备类型。")
    if not re.fullmatch(r"[A-Za-z0-9_,.+-]+", device.device_type):
        raise core.DaisySmartError(f"smartctl 设备类型包含异常字符：{device.device_type!r}")
    if any(character in device.name for character in ("\x00", "\r", "\n")):
        raise core.DaisySmartError("smartctl 设备名称包含异常控制字符。")
    command = [
        str(executable),
        "-x",
        "--json=ov",
        "-d",
        device.device_type,
        device.name,
    ]
    assert_read_only_command(command, purpose="read")
    return command


def assert_read_only_command(command: list[str], *, purpose: str) -> None:
    if not command:
        raise AssertionError("smartctl 命令不能为空。")
    options = set(command[1:])
    forbidden = sorted(options & MUTATING_OR_ACTIVE_OPTIONS)
    if forbidden:
        raise AssertionError("smartctl 命令包含禁止选项：" + ", ".join(forbidden))
    if purpose == "scan":
        if command[1:] != ["--scan-open", "--json=c"]:
            raise AssertionError(f"扫描命令不符合固定只读模板：{command!r}")
    elif purpose == "read":
        if len(command) != 6 or command[1:4] != ["-x", "--json=ov", "-d"]:
            raise AssertionError(f"读取命令不符合固定只读模板：{command!r}")
    else:
        raise AssertionError(f"未知 smartctl 命令用途：{purpose}")


def version(executable: str | os.PathLike[str]) -> str:
    process = _run(build_version_command(executable), timeout=20)
    output = core.normalise_text(process.stdout or process.stderr).strip()
    first = output.splitlines()[0] if output else ""
    match = re.search(r"smartctl\s+([^\s]+)", first, flags=re.IGNORECASE)
    return match.group(1) if match else (first or "未知版本")


def require_supported_version(found: str) -> str:
    numbers = tuple(int(value) for value in re.findall(r"\d+", found))
    if numbers[:2] < MINIMUM_VERSION:
        required = ".".join(map(str, MINIMUM_VERSION))
        raise core.DaisySmartError(
            f"smartctl 版本过低或无法识别：{found}（需 ≥ {required}）。")
    return found


def scan(
    explicit: str | os.PathLike[str] | None = None,
    *,
    timeout: int = 90,
) -> SmartctlScan:
    executable = find_smartctl(explicit)
    found_version = require_supported_version(version(executable))
    command = build_scan_command(executable)
    process = _run(command, timeout=timeout)
    payload = _parse_json(process.stdout, process.stderr, "smartctl 设备枚举")
    devices: list[core.SmartDevice] = []
    for item in core.as_list(payload.get("devices")):
        if not isinstance(item, dict):
            continue
        name = core.clean_text(item.get("name"))
        device_type = core.clean_text(item.get("type"))
        if not name or not device_type:
            continue
        info_name = core.clean_text(item.get("info_name"))
        open_error = core.clean_text(item.get("open_error"))
        devices.append(
            core.SmartDevice(
                name=name,
                device_type=device_type,
                protocol=core.clean_text(item.get("protocol")),
                info_name=info_name,
                open_error=open_error,
                disk_number=windows_disk_number(name, info_name, open_error),
            )
        )
    devices.sort(
        key=lambda device: (
            device.disk_number is None,
            device.disk_number if device.disk_number is not None else 1_000_000,
            device.name,
        )
    )
    warnings = _messages(payload)
    stderr = core.normalise_text(process.stderr).strip()
    if stderr:
        warnings.append(stderr)
    if process.returncode and not warnings:
        warnings.append(f"smartctl 设备枚举退出码：{process.returncode}")
    if not devices and not warnings:
        warnings.append("smartctl 没有发现设备。")
    return SmartctlScan(
        devices=tuple(devices),
        warnings=core.unique_nonempty(warnings),
        executable=str(executable),
        version=found_version,
        command=tuple(command),
    )


def read_all(
    device: core.SmartDevice,
    explicit: str | os.PathLike[str] | None = None,
    *,
    timeout: int = 120,
    known_version: str | None = None,
) -> core.SmartRead:
    executable = find_smartctl(explicit)
    found_version = require_supported_version(
        known_version or version(executable))
    command = build_read_command(executable, device)
    process = _run(command, timeout=timeout)
    payload = _parse_json(process.stdout, process.stderr, "smartctl 读取")
    raw_json = core.normalise_text(process.stdout).lstrip("\ufeff").rstrip() + "\n"
    raw_status = (
        payload.get("smartctl", {}).get("exit_status", process.returncode)
        if isinstance(payload.get("smartctl"), dict) else process.returncode
    )
    exit_status = core.int_or_none(raw_status)
    if exit_status is None:
        exit_status = process.returncode

    stderr = core.normalise_text(process.stderr).strip()
    return core.SmartRead(
        payload=payload,
        raw_json=raw_json,
        stderr=stderr,
        exit_status=exit_status,
        command=tuple(command),
        smartctl_version=found_version,
    )
