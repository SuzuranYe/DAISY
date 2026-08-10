"""DAISY 共享数据模型与无副作用工具函数。"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import platform
import re
import socket
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


APP_NAME = "DAISY"
APP_TITLE = "DAISY 硬盘信息登记"
APP_VERSION = "1.6.6"
APP_AUTHOR = "Suzuran Ye"
APP_CONTACT = "151104858+SuzuranYe@users.noreply.github.com"
ARCHIVE_SCHEMA_VERSION = 3
ARCHIVE_KIND = "PROFILE"
ARCHIVE_ROLE = "single_disk_read_only_profile"
FILENAME_LAYOUT_VERSION = 3
COLLECTION_STATUSES = frozenset(
    {"complete", "complete_with_warnings", "incomplete"}
)
GUI_EVENT_PREFIX = "@@DAISY_GUI@@"


def report_metadata(tool_name: str) -> dict[str, str]:
    """返回 STG 报告及 JSON 输出共用的工具身份。"""
    return {
        "tool_name": f"{APP_NAME} {str(tool_name).strip()}",
        "tool_version": APP_VERSION,
        "tool_author": APP_AUTHOR,
    }

SMARTCTL_EXIT_FLAGS = (
    (0x01, "命令行解析或内部错误"),
    (0x02, "设备无法打开或设备标识读取失败"),
    (0x04, "SMART 命令执行失败"),
    (0x08, "硬盘固件报告 SMART 总体失败"),
    (0x10, "预故障属性当前达到或低于阈值"),
    (0x20, "属性过去曾达到或低于阈值"),
    (0x40, "SMART 错误日志中存在记录"),
    (0x80, "SMART 自检日志中存在错误记录"),
)


class DaisySmartError(RuntimeError):
    """适合直接展示给用户的 DAISY 错误。"""


def gui_events_enabled() -> bool:
    return os.environ.get("DAISY_GUI_PROGRESS") == "1"


def emit_gui_event(event: str, **payload: Any) -> None:
    """复用 DAISY GUI 的行式事件协议，不向普通 CLI 输出机器事件。"""
    if not gui_events_enabled():
        return
    record = {"event": event, **payload}
    print(
        GUI_EVENT_PREFIX
        + json.dumps(record, ensure_ascii=False, separators=(",", ":")),
        flush=True,
    )


class Progress:
    """为存储任务提供与 DAISY 主界面兼容的阶段进度。"""

    def __init__(
        self, stage_idx: int, stage_total: int, name: str, *, quiet: bool = False,
    ):
        self.stage_idx = stage_idx
        self.stage_total = stage_total
        self.name = name
        self.quiet = quiet
        self.started = time.monotonic()
        if not quiet:
            print(f"[{stage_idx}/{stage_total}] {name} …", flush=True)
        emit_gui_event(
            "progress_start",
            stage_idx=stage_idx,
            stage_total=stage_total,
            name=name,
        )

    def finish(self, summary: str) -> None:
        elapsed = time.monotonic() - self.started
        emit_gui_event(
            "progress_finish",
            stage_idx=self.stage_idx,
            stage_total=self.stage_total,
            name=self.name,
            summary=summary,
            elapsed=elapsed,
        )
        if not self.quiet:
            print(
                f"[{self.stage_idx}/{self.stage_total}] {self.name} 完成："
                f"{summary}（{elapsed:.1f} 秒）",
                flush=True,
            )


@dataclass(frozen=True)
class SmartDevice:
    name: str
    device_type: str
    protocol: str = ""
    info_name: str = ""
    open_error: str = ""
    disk_number: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "device_type": self.device_type,
            "protocol": self.protocol,
            "info_name": self.info_name,
            "open_error": self.open_error,
            "disk_number": self.disk_number,
        }


@dataclass(frozen=True)
class WindowsDiskRecord:
    disk_number: int
    data: dict[str, Any]
    warnings: tuple[str, ...] = ()
    detail_level: str = "summary"

    @property
    def disk(self) -> dict[str, Any]:
        value = self.data.get("disk")
        return value if isinstance(value, dict) else {}

    @property
    def partitions(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            item for item in as_list(self.data.get("partitions"))
            if isinstance(item, dict)
        )

    @property
    def layout_gaps(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            item for item in as_list(self.data.get("layout_gaps"))
            if isinstance(item, dict)
        )

    @property
    def model(self) -> str:
        win32 = self.data.get("win32_disk_drive")
        if not isinstance(win32, dict):
            win32 = {}
        return clean_text(
            self.disk.get("friendly_name")
            or self.disk.get("model")
            or win32.get("model")
        )

    @property
    def serial(self) -> str:
        win32 = self.data.get("win32_disk_drive")
        if not isinstance(win32, dict):
            win32 = {}
        return clean_text(
            self.disk.get("serial_number")
            or win32.get("serial_number")
        )

    @property
    def unique_id(self) -> str:
        return clean_text(self.disk.get("unique_id"))

    @property
    def size(self) -> int | None:
        return int_or_none(self.disk.get("size"))

    @property
    def bus_type(self) -> str:
        return clean_text(self.disk.get("bus_type"))

    @property
    def partition_style(self) -> str:
        return clean_text(self.disk.get("partition_style"))

    @property
    def drive_letters(self) -> tuple[str, ...]:
        values: list[str] = []
        for partition in self.partitions:
            volume = partition.get("volume")
            raw = (
                volume.get("drive_letter")
                if isinstance(volume, dict) else
                partition.get("drive_letter")
            )
            text = clean_text(raw).upper().rstrip(":")
            if text and len(text) == 1 and text.isalpha():
                item = f"{text}:"
                if item not in values:
                    values.append(item)
        return tuple(values)

    @property
    def volume_labels(self) -> tuple[str, ...]:
        values: list[str] = []
        for partition in self.partitions:
            volume = partition.get("volume")
            if not isinstance(volume, dict):
                continue
            label = clean_text(volume.get("file_system_label"))
            if label and label not in values:
                values.append(label)
        return tuple(values)

    @property
    def explorer_names(self) -> tuple[str, ...]:
        values: list[str] = []
        for partition in self.partitions:
            volume = partition.get("volume")
            if not isinstance(volume, dict):
                continue
            label = clean_text(volume.get("file_system_label"))
            letter = clean_text(volume.get("drive_letter")).upper().rstrip(":")
            if label and letter:
                value = f"{letter}: {label}"
            elif label:
                value = label
            elif letter:
                value = f"{letter}:（无卷标）"
            else:
                continue
            if value not in values:
                values.append(value)
        return tuple(values)

    def to_dict(self) -> dict[str, Any]:
        return {
            "disk_number": self.disk_number,
            "detail_level": self.detail_level,
            "warnings": list(self.warnings),
            **self.data,
        }


@dataclass(frozen=True)
class DiskTarget:
    disk_number: int | None
    windows: WindowsDiskRecord | None
    smart_device: SmartDevice | None

    @property
    def physical_label(self) -> str:
        if self.disk_number is not None:
            return f"PhysicalDrive{self.disk_number}"
        if self.smart_device is not None:
            return self.smart_device.name
        return "未知物理盘"

    @property
    def stable_key(self) -> str:
        if self.disk_number is not None:
            return f"disk:{self.disk_number}"
        if self.smart_device is not None:
            return f"smartctl:{self.smart_device.name}:{self.smart_device.device_type}"
        return "unknown"

    def to_dict(self) -> dict[str, Any]:
        return {
            "disk_number": self.disk_number,
            "physical_label": self.physical_label,
            "windows": self.windows.to_dict() if self.windows else None,
            "smart_device": (
                self.smart_device.to_dict() if self.smart_device else None
            ),
        }


@dataclass(frozen=True)
class ScanResult:
    targets: tuple[DiskTarget, ...]
    warnings: tuple[str, ...]
    smartctl_executable: str | None
    smartctl_version: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "application": {
                "name": APP_NAME,
                "version": APP_VERSION,
                "author": APP_AUTHOR,
            },
            "targets": [target.to_dict() for target in self.targets],
            "warnings": list(self.warnings),
            "smartctl": {
                "executable": self.smartctl_executable,
                "version": self.smartctl_version,
            },
        }


@dataclass(frozen=True)
class SmartRead:
    payload: dict[str, Any]
    raw_json: str
    stderr: str
    exit_status: int
    command: tuple[str, ...]
    smartctl_version: str

    def metadata_dict(self) -> dict[str, Any]:
        return {
            "command": list(self.command),
            "exit_status": self.exit_status,
            "exit_status_flags": decode_smartctl_exit_status(self.exit_status),
            "smartctl_version": self.smartctl_version,
            "stderr": self.stderr,
        }


@dataclass(frozen=True)
class CollectionResult:
    target: DiskTarget
    windows: WindowsDiskRecord
    smart: SmartRead
    started_at_utc: str
    collected_at_utc: str
    collected_at_local: str
    warnings: tuple[str, ...]
    report: str

    @property
    def collection_status(self) -> str:
        return classify_collection_status(
            self.smart.exit_status,
            has_warnings=bool(self.warnings),
        )

    @property
    def is_complete(self) -> bool:
        return self.collection_status != "incomplete"

    def summary_dict(self) -> dict[str, Any]:
        return {
            "application": {
                "name": APP_NAME,
                "version": APP_VERSION,
                "author": APP_AUTHOR,
            },
            "collection_status": self.collection_status,
            "started_at_utc": self.started_at_utc,
            "collected_at_utc": self.collected_at_utc,
            "collected_at_local": self.collected_at_local,
            "target": self.target.to_dict(),
            "windows": self.windows.to_dict(),
            "smartctl": self.smart.metadata_dict(),
            "warnings": list(self.warnings),
        }


def classify_collection_status(
    smartctl_exit_status: int,
    *,
    has_warnings: bool,
) -> str:
    """区分完整采集、带提示的完整采集和访问／命令层不完整采集。"""
    if smartctl_exit_status & 0x07:
        return "incomplete"
    if smartctl_exit_status or has_warnings:
        return "complete_with_warnings"
    return "complete"


def normalise_text(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return None


def utc_iso(value: datetime) -> str:
    """将带时区时间转换为秒精度 UTC；拒绝无法审计的 naive datetime。"""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("UTC 转换要求带时区的 datetime")
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def utc_now_iso() -> str:
    return utc_iso(datetime.now(timezone.utc))


def local_now() -> datetime:
    return datetime.now().astimezone()


def format_bytes(value: int | None) -> str:
    if value is None:
        return "—"
    units = ("B", "KB", "MB", "GB", "TB", "PB")
    amount = float(value)
    unit = units[0]
    for unit in units:
        if abs(amount) < 1000 or unit == units[-1]:
            break
        amount /= 1000
    if unit == "B":
        return f"{int(amount)} {unit}"
    return f"{amount:.2f} {unit}"


_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def safe_file_component(value: str, limit: int = 80) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", value.strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ._")
    if cleaned.upper() in _WINDOWS_RESERVED_NAMES:
        cleaned = "_" + cleaned
    return cleaned[:limit].rstrip(" ._") or "Disk"


def archive_identity(record: WindowsDiskRecord) -> str:
    labels = [safe_file_component(label) for label in record.volume_labels]
    if labels:
        return "+".join(dict.fromkeys(labels))[:120]
    letters = [letter.rstrip(":") for letter in record.drive_letters]
    if letters:
        return "+".join(letters)
    return f"PhysicalDrive{record.disk_number}"


def archive_base_name(record: WindowsDiskRecord, local_time: datetime) -> str:
    stamp = local_time.strftime("%Y-%m-%d_%H-%M-%S")
    return f"{archive_identity(record)}_{ARCHIVE_KIND}_{stamp}"


def json_text(value: Any, *, indent: int = 2) -> str:
    return json.dumps(value, ensure_ascii=False, indent=indent) + "\n"


def utf8_lf_bytes(value: str) -> bytes:
    return normalise_text(value).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: str | os.PathLike[str], chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(chunk_size)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def decode_smartctl_exit_status(status: int) -> list[str]:
    return [description for bit, description in SMARTCTL_EXIT_FLAGS if status & bit]


def is_windows_admin() -> bool:
    if os.name != "nt":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def host_metadata() -> dict[str, Any]:
    return {
        "computer_name": socket.gethostname(),
        "platform": platform.platform(),
        "windows_release": platform.release(),
        "windows_version": platform.version(),
        "machine": platform.machine(),
        "python_version": platform.python_version(),
        "python_executable": str(Path(sys.executable).resolve()),
        "administrator": is_windows_admin(),
    }


def unique_nonempty(values: Iterable[Any]) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        text = clean_text(value)
        if text and text not in result:
            result.append(text)
    return tuple(result)
