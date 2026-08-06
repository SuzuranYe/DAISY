r"""Script_DAISY_GUI：DAISY 的零依赖 Windows 图形界面。

GUI 只负责收集参数、预览命令和管理子进程；扫描、核验、对比与导出仍由
Script\Script_DAISY_MAIN.py 的既有子命令完成，避免形成第二套业务逻辑。
"""
from __future__ import annotations

import codecs
import ctypes
import json
import math
import os
import queue
import re
import signal
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass


_TCL_RUNTIME_HANDLE: object | None = None


def _prepare_windows_tk_runtime() -> None:
    """在导入 tkinter 前初始化当前 Python 随附的 Tcl/Tk。"""
    global _TCL_RUNTIME_HANDLE
    if os.name != "nt":
        return
    runtime_root = os.path.dirname(os.path.abspath(sys.executable))
    script_root = os.path.join(runtime_root, "tcl")
    try:
        with os.scandir(script_root) as entries:
            directories = tuple(entries)
    except OSError:
        return

    def script_directory(prefix: str, marker: str) -> str | None:
        matches = [
            entry.path for entry in directories
            if entry.is_dir()
            and entry.name.casefold().startswith(prefix)
            and os.path.isfile(os.path.join(entry.path, marker))
        ]
        return max(
            matches,
            key=lambda path: tuple(
                int(part) for part in re.findall(
                    r"\d+", os.path.basename(path))
            ),
            default=None,
        )

    tcl_library = script_directory("tcl", "init.tcl")
    tk_library = script_directory("tk", "tk.tcl")
    if tcl_library is None or tk_library is None:
        return

    version_token = os.path.basename(tcl_library)[3:].replace(".", "")
    dll_root = os.path.join(runtime_root, "DLLs")
    previous_tcl_library = os.environ.pop("TCL_LIBRARY", None)
    previous_tk_library = os.environ.pop("TK_LIBRARY", None)
    try:
        for filename in (
            f"tcl{version_token}t.dll",
            f"tcl{version_token}.dll",
        ):
            dll_path = os.path.join(dll_root, filename)
            if not os.path.isfile(dll_path):
                continue
            try:
                handle = ctypes.WinDLL(dll_path)
                find_executable = handle.Tcl_FindExecutable
                find_executable.argtypes = [ctypes.c_char_p]
                find_executable.restype = None
                find_executable(os.fsencode(sys.executable))
            except (AttributeError, OSError):
                continue
            _TCL_RUNTIME_HANDLE = handle
            break
    finally:
        os.environ["TCL_LIBRARY"] = (
            previous_tcl_library
            if previous_tcl_library and os.path.isfile(
                os.path.join(previous_tcl_library, "init.tcl"))
            else
            tcl_library.replace("\\", "/")
        )
        os.environ["TK_LIBRARY"] = (
            previous_tk_library
            if previous_tk_library and os.path.isfile(
                os.path.join(previous_tk_library, "tk.tcl"))
            else
            tk_library.replace("\\", "/")
        )
    # 某些 Windows Python 安装包含完整 Tcl/Tk 文件，但未在 tkinter 导入前
    # 注册解释器路径；在无环境变量干扰时提前注册，可避免 init.tcl 被误报缺失。


_prepare_windows_tk_runtime()

import tkinter as tk
from tkinter import filedialog, font as tkfont, messagebox, simpledialog, ttk

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_BASE = os.path.dirname(_SCRIPT_DIR)
_LIB_DIR = os.path.join(_SCRIPT_DIR, "Lib")
_TEST_DIR = os.path.join(_SCRIPT_DIR, "Test")
_MAIN = os.path.join(_SCRIPT_DIR, "Script_DAISY_MAIN.py")
sys.path.insert(0, _LIB_DIR)

import Script_DAISY_Lib_DBS_01_Core as core
import Script_DAISY_Lib_DBS_02_Meta as metadata
import Script_DAISY_Lib_DBS_09_Run as dbrun
import Script_DAISY_Lib_STG_01_Core as storage_core


_DEFAULT_OUTPUT_ROOT = os.path.join(_BASE, "Output")
_DEFAULT_REPORTS_DIR = os.path.join(_DEFAULT_OUTPUT_ROOT, "Reports")
_DEFAULT_SNAPSHOTS_DIR = os.path.join(_DEFAULT_OUTPUT_ROOT, "Snapshots")
_DEFAULT_DIFFS_DIR = os.path.join(_DEFAULT_OUTPUT_ROOT, "Diffs")
_DEFAULT_STORAGE_DIR = os.path.join(_DEFAULT_OUTPUT_ROOT, "Storage")
_DEFAULT_HASH_REPORT_LOCATION = _DEFAULT_REPORTS_DIR
_GUI_SETTINGS_PATH = os.path.join(_DEFAULT_OUTPUT_ROOT, "GUI_Settings.json")
_GUI_EVENT_PREFIX = "@@DAISY_GUI@@"
_PROJECT_SELF_TEST_KEY = "project_self_test"
_DEPENDENCY_INSTALL_KEY = "dependency_install"
_PROJECT_TEST_PATTERN = "Script_DAISY_Test_*.py"
_PROJECT_TEST_FILES = (
    "Script_DAISY_Test_Unit.py",
    "Script_DAISY_Test_No_Clobber.py",
    "Script_DAISY_Test_Tree.py",
    "Script_DAISY_Test_Storage_Unit.py",
    "Script_DAISY_Test_Storage_Read_Only.py",
    "Script_DAISY_Test_DBS_Reader.py",
    "Script_DAISY_Test_DBS_Verify.py",
    "Script_DAISY_Test_DBS_Verify_Unified.py",
    "Script_DAISY_Test_DBS_Parse.py",
    "Script_DAISY_Test_DBS_State.py",
    "Script_DAISY_Test_DBS_Hash_Worker.py",
    "Script_DAISY_Test_DBS_Run.py",
    "Script_DAISY_Test_GUI_Scan.py",
)
_PROJECT_GITHUB_URL = "https://github.com/SuzuranYe/DAISY"
_PROJECT_CONTACT = "151104858+SuzuranYe@users.noreply.github.com"
_MAX_ROOT_DIRECTORIES = 9
_ROOT_BATCH_TASKS = frozenset(("full_scan", "quick_scan"))
_SCAN_TASK_KEYS = frozenset(("full_scan", "quick_scan"))
_ROOT_BATCH_SEPARATE = "separate"
_ROOT_BATCH_COMBINED = "combined"
_DEFAULT_WINDOW_SIZE = (1920, 1080)
_WINDOW_WORK_MARGIN = (32, 40)
_UI_FONT_FAMILY = "Microsoft YaHei UI"
_UI_FONT_FAMILY_CANDIDATES = (
    "Microsoft YaHei UI", "Noto Sans SC", "Microsoft JhengHei UI",
    "Segoe UI",
)
_UI_FONT_SIZE_OPTIONS = (
    ("标准", 0), ("较大", 1), ("特大", 2),
)
_WINDOW_SIZE_OPTIONS = (
    ("1920 × 1080（默认）", (1920, 1080)),
    ("1600 × 900", (1600, 900)),
    ("1366 × 768", (1366, 768)),
)
_COLOUR_STRIP_HEIGHT = 4

_TOOL_FIELD_BY_NAME = {
    "exiftool": "exiftool_path",
    "ffprobe": "ffprobe_path",
    "sevenzip": "sevenzip_path",
    "powershell": "powershell_path",
    "smartctl": "smartctl_path",
}
_TOOL_DISPLAY_NAMES = {
    "exiftool": "ExifTool",
    "ffprobe": "ffprobe",
    "sevenzip": "7-Zip",
    "powershell": "PowerShell",
    "smartctl": "smartctl",
}
_TOOL_PATH_MENU_ORDER = (
    "exiftool", "ffprobe", "sevenzip", "powershell", "smartctl",
)
_INSTALLABLE_TOOL_PACKAGES = {
    "exiftool": ("ExifTool", "OliverBetz.ExifTool"),
    "ffprobe": ("ffprobe（FFmpeg）", "Gyan.FFmpeg"),
    "sevenzip": ("7-Zip", "7zip.7zip"),
    "smartctl": ("smartctl", "smartmontools.smartmontools"),
}
_TASK_TOOL_NAMES = {
    "env_check": (
        "exiftool", "ffprobe", "sevenzip", "powershell", "smartctl"),
    "full_scan": ("exiftool", "ffprobe", "sevenzip", "powershell"),
    "check_format": ("exiftool", "ffprobe", "sevenzip"),
    "check_hash": ("powershell",),
    "storage_list": ("smartctl", "powershell"),
    "storage_collect": ("smartctl", "powershell"),
}
_RESULT_DIRECTORY_TASKS = frozenset((
    "env_check", "full_scan", "quick_scan", "diff", "check_hash",
    "check_format", "export_report", "storage_collect",
))
_STG_ADMIN_TASKS = frozenset(("storage_list", "storage_collect"))
_PROJECT_CACHE_DIR_NAMES = frozenset((
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
))
_PROJECT_CACHE_FILE_SUFFIXES = (".pyc", ".pyo")
_CACHE_SCAN_EXCLUDED_DIR_NAMES = frozenset((
    ".git", ".venv", "venv", "node_modules", "output",
))


def default_gui_preferences() -> dict[str, object]:
    """返回不含任务参数的本地界面偏好默认值。"""
    return {
        "version": 1,
        "window_size": list(_DEFAULT_WINDOW_SIZE),
        "font_family": _UI_FONT_FAMILY,
        "font_size_delta": 0,
        "confirm_close_when_idle": True,
        "last_task_key": "env_check",
        "recovery_scans": [],
    }


def load_gui_preferences(
    path: str = _GUI_SETTINGS_PATH,
) -> dict[str, object]:
    """容错读取 GUI 偏好；文件损坏时回到安全默认值。"""
    preferences = default_gui_preferences()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            loaded = json.load(handle)
    except (OSError, ValueError, TypeError):
        return preferences
    if not isinstance(loaded, dict):
        return preferences

    raw_size = loaded.get("window_size")
    if (isinstance(raw_size, (list, tuple)) and len(raw_size) == 2
            and all(isinstance(value, int) for value in raw_size)):
        width, height = int(raw_size[0]), int(raw_size[1])
        if 760 <= width <= 3840 and 640 <= height <= 2160:
            preferences["window_size"] = [width, height]

    family = loaded.get("font_family")
    if isinstance(family, str) and 1 <= len(family.strip()) <= 80:
        preferences["font_family"] = family.strip()

    size_delta = loaded.get("font_size_delta")
    allowed_deltas = {delta for _label, delta in _UI_FONT_SIZE_OPTIONS}
    if isinstance(size_delta, int) and size_delta in allowed_deltas:
        preferences["font_size_delta"] = size_delta

    confirm_close = loaded.get("confirm_close_when_idle")
    if isinstance(confirm_close, bool):
        preferences["confirm_close_when_idle"] = confirm_close

    last_task_key = loaded.get("last_task_key")
    if (isinstance(last_task_key, str)
            and last_task_key in _RESTORABLE_TASK_KEYS):
        preferences["last_task_key"] = last_task_key

    recovery_scans = loaded.get("recovery_scans")
    if isinstance(recovery_scans, list):
        validated = []
        seen_paths: set[str] = set()
        for item in recovery_scans[-20:]:
            if not isinstance(item, dict):
                continue
            task_key = item.get("task_key")
            partial = item.get("partial")
            if task_key not in _SCAN_TASK_KEYS or not isinstance(partial, str):
                continue
            partial = partial.strip()
            canonical = os.path.normcase(os.path.abspath(partial))
            if (not partial.lower().endswith(".partial.sqlite")
                    or not partial or canonical in seen_paths):
                continue
            validated.append({"task_key": task_key, "partial": partial})
            seen_paths.add(canonical)
        preferences["recovery_scans"] = validated
    return preferences


def save_gui_preferences(
    preferences: dict[str, object], path: str = _GUI_SETTINGS_PATH,
) -> None:
    """以 UTF-8／LF 原子保存本地 GUI 偏好。"""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(preferences, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.replace(temporary, path)

# 《孤星》专项调查取色：米黄色纸面＋薄荷绿、橙黄、深红三种强调色。
# 三个基准色取自官方素材右上角色条，米白取自设备外壳。
_BG = "#e9dfcc"
_SURFACE = "#f7efe1"
_GREEN = "#87c1af"
_GREEN_DARK = "#347a68"
_GREEN_DEEP = "#245a4e"
_GREEN_SOFT = "#dce9e1"
_AMBER = "#eca93b"
_AMBER_DARK = "#9a6519"
_AMBER_DEEP = "#70470f"
_AMBER_SOFT = "#f1ddb2"
_RED = "#9a2d28"
_RED_DARK = "#7b2925"
_RED_DEEP = "#5b1f1c"
_RED_SOFT = "#e8ccc5"
_ACCENT = _GREEN_DARK
_ACCENT_DARK = _GREEN_DEEP
_ACCENT_SOFT = _GREEN_SOFT
_TEXT = "#272820"
_MUTED = "#66685e"
_BORDER = "#cfc2aa"
_FIELD = "#fff9ee"
_CONTROL = "#e8ddc9"
_MENU_BACKGROUND = "#eee3cf"
_CONTROL_HOVER = "#d9ccb4"
_LOG_BG = "#eee4d2"
_LOG_HEADER = "#dfd3bd"
_LOG_TEXT = "#303128"
_LOG_SELECT = "#d2e4dc"
_SUCCESS = _GREEN_DARK
_WARNING = _AMBER_DARK
_DANGER = _RED_DARK
_DANGER_SOFT = _RED_SOFT
_DANGER_HOVER = "#ddb9b1"
_DANGER_BORDER = "#c99f98"

_TASK_ACCENTS = {
    "env_check": (_GREEN_DARK, _GREEN_DEEP, _GREEN),
    _PROJECT_SELF_TEST_KEY: (_GREEN_DARK, _GREEN_DEEP, _GREEN),
    "full_scan": (_GREEN_DARK, _GREEN_DEEP, _GREEN),
    "quick_scan": (_GREEN_DARK, _GREEN_DEEP, _GREEN),
    "check_format": (_GREEN_DARK, _GREEN_DEEP, _GREEN),
    "check_hash": (_GREEN_DARK, _GREEN_DEEP, _GREEN),
    "diff": (_GREEN_DARK, _GREEN_DEEP, _GREEN),
    "export_report": (_GREEN_DARK, _GREEN_DEEP, _GREEN),
    "storage_list": (_GREEN_DARK, _GREEN_DEEP, _GREEN),
    "storage_collect": (_GREEN_DARK, _GREEN_DEEP, _GREEN),
}
_NAV_COLOURS = {
    key: (colours[0], colours[1])
    for key, colours in _TASK_ACCENTS.items()
}


def task_accent_colours(task_key: str) -> tuple[str, str, str]:
    """返回任务组的常态、按下和悬停强调色。"""
    return _TASK_ACCENTS.get(
        task_key, (_ACCENT, _ACCENT_DARK, _GREEN))


def status_badge_background(
    task_key: str, semantic_colour: str | None = None,
) -> str:
    """返回状态徽标底色；常态随任务，结果态使用语义色。"""
    return (
        task_accent_colours(task_key)[0]
        if semantic_colour is None else semantic_colour
    )


def should_offer_result_directory(
    returncodes: list[int | None] | tuple[int | None, ...],
    *,
    stopped: bool,
    maintenance: bool,
) -> bool:
    """仅为已完成且可能生成正式结果的任务提供目录入口。"""
    return (
        bool(returncodes)
        and not stopped
        and not maintenance
        and all(code in (0, 1) for code in returncodes)
    )


def parse_gui_stream(
    buffer: str, text: str, *, final: bool = False,
) -> tuple[str, list[tuple[str, object]]]:
    """拆分子进程输出，并把结构化 GUI 事件从普通日志中分离。"""
    pending = buffer + text
    parsed: list[tuple[str, object]] = []

    def consume(line: str, newline: bool) -> None:
        candidate = line.lstrip("\r")
        if candidate.startswith(_GUI_EVENT_PREFIX):
            try:
                payload = json.loads(candidate[len(_GUI_EVENT_PREFIX):])
            except (json.JSONDecodeError, TypeError):
                payload = None
            if isinstance(payload, dict):
                parsed.append(("gui_event", payload))
                return
        output = line + ("\n" if newline else "")
        if parsed and parsed[-1][0] == "output":
            parsed[-1] = ("output", str(parsed[-1][1]) + output)
        elif output:
            parsed.append(("output", output))

    while "\n" in pending:
        line, pending = pending.split("\n", 1)
        consume(line, True)
    if final and pending:
        consume(pending, False)
        pending = ""
    return pending, parsed


def progress_fraction(done: object, total: object) -> float | None:
    """返回 0..100 的真实进度；总量未知或无效时返回 None。"""
    try:
        done_number = float(done)
        total_number = float(total)
    except (TypeError, ValueError):
        return None
    if total_number <= 0:
        return None
    return max(0.0, min(100.0, done_number / total_number * 100.0))


def queue_progress_fraction(
    completed: object, total: object, current_fraction: object = 0.0,
) -> float:
    """返回队列总进度；当前项进度使用 0..1 的比例。"""
    try:
        completed_number = max(0.0, float(completed))
        total_number = float(total)
        current_number = max(0.0, min(1.0, float(current_fraction)))
    except (TypeError, ValueError):
        return 0.0
    if total_number <= 0:
        return 0.0
    return max(
        0.0,
        min(100.0, (completed_number + current_number) / total_number * 100),
    )


@dataclass(frozen=True)
class StorageDiskOption:
    """硬盘池中的一项；脱机或资料不完整的设备只展示、不可选择。"""

    disk_number: int
    display: str
    online: bool
    registrable: bool
    reason: str

    @property
    def selectable(self) -> bool:
        return self.online and self.registrable

    @property
    def value(self) -> str:
        return str(self.disk_number)


def storage_disk_options(raw_targets: object) -> tuple[StorageDiskOption, ...]:
    """把 STG-11 事件转换为包含可用与不可用设备的稳定硬盘池。"""
    if not isinstance(raw_targets, list):
        return ()
    options: list[StorageDiskOption] = []
    seen: set[int] = set()
    for target in raw_targets:
        if not isinstance(target, dict):
            continue
        disk_number = target.get("disk_number")
        windows = target.get("windows")
        smart_device = target.get("smart_device")
        if (
            not isinstance(disk_number, int)
            or isinstance(disk_number, bool)
            or disk_number < 0
            or disk_number in seen
        ):
            continue
        windows_available = isinstance(windows, dict)
        smart_available = isinstance(smart_device, dict)
        disk = windows.get("disk") if windows_available else {}
        if not isinstance(disk, dict):
            disk = {}
        model = str(
            disk.get("friendly_name") or disk.get("model")
            or (smart_device.get("info_name") if smart_available else "")
            or "型号未提供"
        ).strip()
        explorer_names: list[str] = []
        partitions = windows.get("partitions") if windows_available else None
        if isinstance(partitions, list):
            for partition in partitions:
                if not isinstance(partition, dict):
                    continue
                volume = partition.get("volume")
                if not isinstance(volume, dict):
                    continue
                letter = str(volume.get("drive_letter") or "").strip()
                label = str(volume.get("file_system_label") or "").strip()
                name = (
                    f"{letter} {label}" if letter and label else
                    label or letter
                )
                if name and name not in explorer_names:
                    explorer_names.append(name)
        size = disk.get("size")
        size_text = (
            _format_bytes(size)
            if isinstance(size, (int, float)) and not isinstance(size, bool)
            else "容量未提供"
        )
        display = " · ".join((
            f"PhysicalDrive{disk_number}",
            ("／".join(explorer_names) or "无卷标")[:64],
            model[:64],
            size_text,
        ))
        online = windows_available and disk.get("is_offline") is not True
        registrable = windows_available and smart_available
        if not windows_available:
            reason = "Windows 资料不可用"
        elif not online:
            reason = "已脱机"
        elif not smart_available:
            reason = "smartctl 未关联"
        else:
            reason = "联机 · 可登记"
        options.append(StorageDiskOption(
            disk_number=disk_number,
            display=display,
            online=online,
            registrable=registrable,
            reason=reason,
        ))
        seen.add(disk_number)
    options.sort(key=lambda item: item.disk_number)
    return tuple(options)


def storage_target_choices(
    raw_targets: object,
) -> tuple[tuple[str, str], ...]:
    """兼容旧调用：返回硬盘池中联机且资料完整的设备。"""
    return tuple(
        (option.display, option.value)
        for option in storage_disk_options(raw_targets)
        if option.selectable
    )


def _format_bytes(value: object) -> str:
    try:
        number = max(0.0, float(value))
    except (TypeError, ValueError):
        return "0 B"
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    unit = units[0]
    for unit in units:
        if number < 1024.0 or unit == units[-1]:
            break
        number /= 1024.0
    precision = 0 if unit == "B" else 1
    return f"{number:.{precision}f} {unit}"


def _format_duration(value: object) -> str:
    try:
        seconds = max(0, int(float(value)))
    except (TypeError, ValueError):
        return "--:--"
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return (
        f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        if hours else f"{minutes:02d}:{seconds:02d}"
    )


def progress_detail(payload: dict[str, object]) -> tuple[str, float | None]:
    """把结构化进度转为紧凑的人类可读说明与真实百分比。"""
    parts: list[str] = []
    done = payload.get("done")
    total = payload.get("total")
    bytes_done = payload.get("bytes_done")
    bytes_total = payload.get("bytes_total")

    if total not in (None, 0):
        parts.append(f"{int(done or 0):,}/{int(total):,} 个")
    elif done is not None:
        parts.append(f"{int(done):,} 个")

    fraction = progress_fraction(bytes_done, bytes_total)
    if bytes_done is not None:
        if bytes_total not in (None, 0):
            parts.append(
                f"{_format_bytes(bytes_done)}/{_format_bytes(bytes_total)}")
        else:
            parts.append(_format_bytes(bytes_done))
    if fraction is None:
        fraction = progress_fraction(done, total)

    bytes_rate = payload.get("bytes_rate")
    rate = payload.get("rate")
    if bytes_rate:
        parts.append(f"{_format_bytes(bytes_rate)}/s")
    elif rate:
        parts.append(f"{float(rate):,.0f} 个/s")

    eta = payload.get("eta")
    if eta is not None:
        parts.append(f"ETA {_format_duration(eta)}")
    elapsed = payload.get("elapsed")
    if elapsed is not None:
        parts.append(f"已用 {_format_duration(elapsed)}")
    errors = int(payload.get("errors") or 0)
    if errors:
        parts.append(f"错误 {errors:,}")
    return " · ".join(parts) or "正在处理…", fraction


def merge_session_tool_paths(
    task_key: str, values: dict[str, object],
    cache: dict[str, dict[str, object]], *,
    manual_paths: dict[str, str] | None = None,
    path_exists=os.path.isfile,
) -> tuple[dict[str, object], dict[str, str]]:
    """按顶部手动指定→本窗口缓存→运行时自动发现合并工具路径。"""
    effective = dict(values)
    sources: dict[str, str] = {}
    selected_paths = manual_paths or {}
    for name in _TASK_TOOL_NAMES.get(task_key, ()):
        field = _TOOL_FIELD_BY_NAME[name]
        selected = str(selected_paths.get(name) or "").strip()
        if selected:
            effective[field] = selected
            sources[name] = "manual_menu"
            continue
        manual = str(values.get(field) or "").strip()
        if manual:
            sources[name] = "manual"
            continue
        cached = cache.get(name) or {}
        path = str(cached.get("path") or "").strip()
        if path and cached.get("verified") is True and path_exists(path):
            effective[field] = path
            sources[name] = "session_cache"
        else:
            sources[name] = "auto_discovery"
    return effective, sources


def session_tool_cache_summary(
    task_key: str, cache: dict[str, dict[str, object]], *,
    path_exists=os.path.isfile,
) -> str:
    """只在当前任务有可用缓存时，返回一行简短状态。"""
    cached = []
    for name in _TASK_TOOL_NAMES.get(task_key, ()):
        info = cache.get(name) or {}
        path = str(info.get("path") or "").strip()
        if path and info.get("verified") is True and path_exists(path):
            display = _TOOL_DISPLAY_NAMES[name]
            if task_key == "env_check":
                version = str(info.get("version") or "版本未知")
                cached.append(f"{display} {version}")
            else:
                cached.append(display)
    if not cached:
        return ""
    prefix = "本机版本：" if task_key == "env_check" else "已缓存："
    return prefix + "、".join(cached)


def clear_session_tool_cache(
    cache: dict[str, dict[str, object]],
) -> int:
    """清空可安全重建的本窗口工具路径缓存，返回移除的记录数。"""
    count = len(cache)
    cache.clear()
    return count


@dataclass(frozen=True)
class ProjectCacheCleanup:
    directories: tuple[str, ...]
    files: tuple[str, ...]
    errors: tuple[str, ...]


def clean_project_caches(project_root: str = _BASE) -> ProjectCacheCleanup:
    """只清理项目内白名单缓存，不跟随链接，也不扫描输出与依赖目录。"""
    root = os.path.abspath(project_root)
    root_real = os.path.normcase(os.path.realpath(root))
    removed_dirs: list[str] = []
    removed_files: list[str] = []
    errors: list[str] = []
    cache_dirs: list[tuple[str, str]] = []
    cache_files: list[tuple[str, str]] = []
    is_junction = getattr(os.path, "isjunction", lambda _path: False)

    if not os.path.isdir(root):
        return ProjectCacheCleanup(
            (), (), (f"项目目录不存在：{root}",))

    def relative_if_safe(path: str) -> str | None:
        try:
            candidate_real = os.path.normcase(os.path.realpath(path))
            if (candidate_real == root_real
                    or os.path.commonpath((root_real, candidate_real))
                    != root_real):
                return None
            return os.path.relpath(path, root)
        except (OSError, ValueError):
            return None

    def walk_error(exc: OSError) -> None:
        errors.append(f"无法扫描：{exc}")

    for current, dirnames, filenames in os.walk(
            root, topdown=True, onerror=walk_error, followlinks=False):
        descend: list[str] = []
        for name in dirnames:
            path = os.path.join(current, name)
            lower_name = name.casefold()
            relative = relative_if_safe(path)
            linked = os.path.islink(path) or is_junction(path)
            if lower_name in _PROJECT_CACHE_DIR_NAMES:
                if relative is None or linked:
                    errors.append(
                        f"跳过不安全的缓存目录："
                        f"{relative or os.path.abspath(path)}")
                else:
                    cache_dirs.append((path, relative))
                continue
            if (lower_name in _CACHE_SCAN_EXCLUDED_DIR_NAMES
                    or linked or relative is None):
                continue
            descend.append(name)
        dirnames[:] = descend

        for name in filenames:
            if not name.casefold().endswith(_PROJECT_CACHE_FILE_SUFFIXES):
                continue
            path = os.path.join(current, name)
            relative = relative_if_safe(path)
            if (relative is None or os.path.islink(path)
                    or is_junction(path)):
                errors.append(
                    f"跳过不安全的缓存文件："
                    f"{relative or os.path.abspath(path)}")
                continue
            cache_files.append((path, relative))

    for path, relative in sorted(
            cache_dirs, key=lambda item: item[1].casefold()):
        try:
            shutil.rmtree(path)
            removed_dirs.append(relative)
        except FileNotFoundError:
            continue
        except OSError as exc:
            errors.append(f"无法清理目录 {relative}：{exc}")

    for path, relative in sorted(
            cache_files, key=lambda item: item[1].casefold()):
        try:
            os.remove(path)
            removed_files.append(relative)
        except FileNotFoundError:
            continue
        except OSError as exc:
            errors.append(f"无法清理文件 {relative}：{exc}")

    return ProjectCacheCleanup(
        tuple(removed_dirs), tuple(removed_files), tuple(errors))


def dependency_install_command(
    tool_name: str, winget_path: str = "winget.exe",
) -> list[str]:
    """返回固定白名单工具的 WinGet 非交互安装命令。"""
    try:
        _display, package_id = _INSTALLABLE_TOOL_PACKAGES[tool_name]
    except KeyError as exc:
        raise ValueError(f"不允许安装未知工具：{tool_name}") from exc
    return [
        winget_path,
        "install",
        "--exact",
        "--id", package_id,
        "--source", "winget",
        "--accept-source-agreements",
        "--accept-package-agreements",
        "--disable-interactivity",
    ]


def discover_winget(path_lookup=shutil.which) -> str | None:
    """查找 WinGet；不联网、不安装 App Installer。"""
    found = path_lookup("winget.exe") or path_lookup("winget")
    if found and os.path.isfile(found):
        return os.path.abspath(found)
    candidate = os.path.join(
        os.environ.get("LOCALAPPDATA", ""),
        "Microsoft", "WindowsApps", "winget.exe",
    )
    return os.path.abspath(candidate) if os.path.isfile(candidate) else None


def refresh_windows_process_path() -> bool:
    """安装后从注册表刷新当前 GUI 进程 PATH，失败时保持原值。"""
    if os.name != "nt":
        return False
    try:
        import winreg
        with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
        ) as key:
            machine_path = str(winreg.QueryValueEx(key, "Path")[0])
        with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, r"Environment",
        ) as key:
            user_path = str(winreg.QueryValueEx(key, "Path")[0])
    except (OSError, ValueError):
        return False
    os.environ["PATH"] = os.path.expandvars(
        machine_path + os.pathsep + user_path)
    return True


@dataclass(frozen=True)
class FieldSpec:
    key: str
    label: str
    flag: str | None
    kind: str = "text"
    default: str | bool = ""
    required: bool = False
    help: str = ""
    choices: tuple[tuple[str, object], ...] = ()
    filetypes: tuple[tuple[str, str], ...] = ()
    section: str = "任务参数"
    top_menu: bool = False
    active_when: tuple[tuple[str, tuple[object, ...]], ...] = ()
    flag_value: object | None = None


@dataclass(frozen=True)
class TaskSpec:
    key: str
    command: str
    nav: str
    title: str
    description: str
    badge: str
    fields: tuple[FieldSpec, ...]

@dataclass(frozen=True)
class RunJob:
    label: str
    values: dict[str, object]


def run_job_target_text(task_key: str, job: RunJob) -> str:
    """返回进度区使用的完整当前目标；目录任务不缩写路径。"""
    field_key = {
        "full_scan": "roots",
        "quick_scan": "roots",
        "check_hash": "root_map",
        "check_format": "root_map",
        "diff": "map_root",
    }.get(task_key)
    if field_key:
        paths: list[str] = []
        for root_spec in _lines(job.values.get(field_key)):
            try:
                _label, root_path = core.parse_root_spec(root_spec)
            except (OSError, ValueError, core.PreflightError):
                root_path = root_spec
            paths.append(str(root_path))
        if paths:
            return "；".join(paths)
    if task_key == "full_scan":
        resume = str(job.values.get("resume") or "").strip()
        if resume:
            return f"续传快照：{_absolute(resume)}"
    if task_key == "quick_scan":
        resume = str(job.values.get("resume") or "").strip()
        if resume:
            return f"续传快照：{_absolute(resume)}"
    if task_key == "storage_collect":
        disk_number = str(job.values.get("disk_number") or "").strip()
        if disk_number:
            return f"PhysicalDrive{disk_number}"
    if task_key == "storage_list":
        return "本机物理硬盘"
    for field in ("source_path", "snapshot", "old", "archive"):
        value = str(job.values.get(field) or "").strip()
        if value:
            return _absolute(value)
    return job.label


@dataclass(frozen=True)
class MonitorWorkArea:
    handle: int
    left: int
    top: int
    right: int
    bottom: int
    dpi: int = 96

    @property
    def width(self) -> int:
        return max(1, self.right - self.left)

    @property
    def height(self) -> int:
        return max(1, self.bottom - self.top)

    @property
    def signature(self) -> tuple[int, int, int, int, int, int]:
        return (
            self.handle, self.left, self.top,
            self.right, self.bottom, self.dpi,
        )


_SQLITE_TYPES = (
    ("SQLite 数据库", "*.sqlite"),
    ("全部文件", "*.*"),
)
_PARTIAL_TYPES = (
    ("未完成快照", "*.partial.sqlite"),
    ("SQLite 数据库", "*.sqlite"),
    ("全部文件", "*.*"),
)
_EXE_TYPES = (
    ("可执行文件", "*.exe"),
    ("全部文件", "*.*"),
)

_FULL_NEW = (("start_mode", ("new",)),)
_FULL_RESUME = (("start_mode", ("resume",)),)
_QUICK_NEW = (("start_mode", ("new",)),)
_QUICK_RESUME = (("start_mode", ("resume",)),)
_FULL_INCREMENTAL = (
    ("start_mode", ("new",)),
    ("hash_mode", ("incremental",)),
)
_FULL_HASHED = (
    ("start_mode", ("new",)),
    ("hash_mode", ("incremental", "full")),
)
_FULL_POWERSHELL = (
    ("start_mode", ("new",)),
    ("hash_mode", ("incremental", "full")),
)
_FULL_FORMAT_SAMPLE = (
    ("start_mode", ("new",)),
    ("format_validation", ("sample",)),
)
_FORMAT_SAMPLE = (("check_scope", ("sample",)),)
_HASH_SAMPLE = (("check_scope", ("sample",)),)


TASKS = (
    TaskSpec(
        "env_check",
        "env-check",
        "ENV-01  运行环境检测",
        "运行环境检测",
        "检查五项运行依赖与工具可用性，不读取档案或保存设置。",
        "只读检查 · 不读取档案 · 不保存设置",
        (
            FieldSpec(
                "output_dir", "环境报告目录", "--output-dir", "dir",
                _DEFAULT_REPORTS_DIR,
                help="默认指向项目内 Output\\Reports；也可选择其它完整路径。",
                section="结果输出",
            ),
            FieldSpec(
                "exiftool_path", "Exif工具", "--exiftool-path",
                "file", help="通常留空；手动指定成功后也会更新本窗口缓存。",
                filetypes=_EXE_TYPES, section="工具路径", top_menu=True,
            ),
            FieldSpec(
                "ffprobe_path", "视频工具", "--ffprobe-path", "file",
                help="通常留空；手动指定成功后也会更新本窗口缓存。",
                filetypes=_EXE_TYPES, section="工具路径", top_menu=True,
            ),
            FieldSpec(
                "sevenzip_path", "压缩工具", "--sevenzip-path",
                "file", help="通常留空；手动指定成功后也会更新本窗口缓存。",
                filetypes=_EXE_TYPES, section="工具路径", top_menu=True,
            ),
            FieldSpec(
                "powershell_path", "系统工具",
                "--powershell-path", "file",
                help="通常留空；会依次检查 PATH 与 Windows 常规安装位置。",
                filetypes=_EXE_TYPES, section="工具路径", top_menu=True,
            ),
            FieldSpec(
                "smartctl_path", "硬盘工具", "--smartctl-path",
                "file", help="通常留空；用于 STG 物理硬盘登记与只读核验。",
                filetypes=_EXE_TYPES, section="工具路径", top_menu=True,
            ),
        ),
    ),
    TaskSpec(
        _PROJECT_SELF_TEST_KEY,
        "",
        "DBS-91  DAISY功能自检",
        "DAISY功能自检",
        "运行项目自动化测试；夹具仅写入系统临时目录。",
        "DBS 功能检测 · 临时夹具 · 不读取私人档案",
        (),
    ),
    TaskSpec(
        "full_scan",
        "scan",
        "DBS-11  完整档案扫描",
        "完整档案扫描",
        "登记目录、元数据与 SHA-256，生成可续传的封存快照。",
        "档案只读 · 长时任务 · 生成快照",
        (
            FieldSpec(
                "start_mode", "启动方式", None, "choice", "new",
                choices=(
                    ("新建完整扫描（默认）", "new"),
                    ("续传未完成的 partial 快照", "resume"),
                ),
                help="续传时，哈希、Payload 与 File ID 等设置沿用 partial "
                     "快照内的原配置。",
                section="启动方式",
            ),
            FieldSpec(
                "roots", "档案根目录", "--root", "multidir", required=True,
                help="使用“添加目录”建立列表；可修改为“label=路径”，也可用 ×"
                     " 单独移除，最多 9 个。",
                section="任务输入", active_when=_FULL_NEW,
            ),
            FieldSpec(
                "root_batch_mode", "生成方式", None, "choice",
                _ROOT_BATCH_SEPARATE,
                choices=(
                    ("分别生成：每个目录一个数据库（默认）",
                     _ROOT_BATCH_SEPARATE),
                    ("合并生成：所有目录一个数据库", _ROOT_BATCH_COMBINED),
                ),
                help="分别模式按添加顺序逐项运行，单项失败后继续下一项；"
                     "停止会终止整个队列。",
                section="任务输入", active_when=_FULL_NEW,
            ),
            FieldSpec(
                "resume", "续传快照", "--resume", "file",
                required=True,
                help="必须指向 .partial.sqlite；其内部参数是本次续传的权威配置。",
                filetypes=_PARTIAL_TYPES, section="任务输入",
                active_when=_FULL_RESUME,
            ),
            FieldSpec(
                "output_dir", "快照目录", "--output-dir", "dir",
                _DEFAULT_SNAPSHOTS_DIR,
                help="默认指向项目内 Output\\Snapshots；也可选择其它完整路径。",
                section="结果输出", active_when=_FULL_NEW,
            ),
            FieldSpec(
                "metadata_storage", "元数据范围",
                "--metadata-storage", "choice", "complete",
                choices=(
                    ("全量元数据：基础字段＋原始工具输出（默认）", "complete"),
                    ("基础元数据：仅保留规范化常用字段", "normalized"),
                ),
                help="基础范围保留规范化字段，视频和音频还通过 ffprobe 记录容器"
                     "与流；GIF 在基础范围只使用 ExifTool。全量范围另存工具原文，"
                     "便于重释和比较提取变化。",
                section="快照内容", active_when=_FULL_NEW,
            ),
            FieldSpec(
                "format_validation", "格式校验", "--format-validation",
                "choice", "off",
                choices=(
                    ("关闭（默认）", "off"),
                    ("抽样校验", "sample"),
                    ("全部校验", "all"),
                ),
                help="Full 可选格式校验；默认关闭，保持既有 Full 含义和性能。",
                section="快照内容", top_menu=True,
                active_when=_FULL_NEW,
            ),
            FieldSpec(
                "format_sample_percent", "格式抽样",
                "--format-sample-percent", default="10.0",
                help="仅抽样格式校验使用；必须大于 0 且不超过 100。",
                section="快照内容", top_menu=True,
                active_when=_FULL_FORMAT_SAMPLE,
            ),
            FieldSpec(
                "collect_file_id", "NTFS标识",
                "--no-file-id", "choice_flag", True,
                choices=(
                    ("采集（默认）", True),
                    ("不采集（No-FID）", False),
                ),
                flag_value=False,
                help="建议保留；选择“不采集”会降低移动／重命名判定证据。",
                section="快照内容", active_when=_FULL_NEW,
            ),
            FieldSpec(
                "hash_mode", "哈希模式", "--hash", "choice", "full",
                choices=(
                    ("不计算哈希（none）", "none"),
                    ("复用上一快照（incremental）", "incremental"),
                    ("完整 SHA-256（full）（默认）", "full"),
                ),
                help="默认完整 SHA-256，会读取每个文件内容；增量模式必须提供上一快照。",
                section="哈希设置", active_when=_FULL_NEW,
            ),
            FieldSpec(
                "previous_snapshot", "上一封存快照", "--previous-snapshot",
                "file", required=True,
                help="仅增量哈希使用；作为可复用哈希的来源。",
                filetypes=_SQLITE_TYPES, section="哈希设置",
                active_when=_FULL_INCREMENTAL,
            ),
            FieldSpec(
                "verify_percent", "哈希抽验",
                "--verify-sample-percent", default="1.0",
                help=(
                    "主 SHA-256 完成后，按比例抽取本次实际计算且有效的条目，"
                    "再由 PowerShell Get-FileHash 独立重算；默认 1%，至少 "
                    "100 个（不足则全验）。这不是主哈希的覆盖比例。"
                ),
                section="哈希比例", top_menu=True,
                active_when=_FULL_HASHED,
            ),
            FieldSpec(
                "map_root", "根标签映射", "--map-root", "multiline",
                help="可选；每行“旧label=新label”。",
                section="增量复用",
                active_when=_FULL_INCREMENTAL,
            ),
            FieldSpec(
                "exiftool_path", "Exif工具", "--exiftool-path", "file",
                help="留空时优先使用本窗口已验证路径，其次自动发现；填写则手动覆盖。",
                filetypes=_EXE_TYPES,
                section="工具路径", top_menu=True,
                active_when=_FULL_NEW,
            ),
            FieldSpec(
                "ffprobe_path", "视频工具", "--ffprobe-path", "file",
                help="留空时优先使用本窗口已验证路径，其次自动发现；填写则手动覆盖。",
                filetypes=_EXE_TYPES,
                section="工具路径", top_menu=True,
                active_when=_FULL_NEW,
            ),
            FieldSpec(
                "sevenzip_path", "压缩工具", "--sevenzip-path", "file",
                help="留空时优先使用本窗口已验证路径，其次自动发现；填写则手动覆盖。",
                filetypes=_EXE_TYPES,
                section="工具路径", top_menu=True,
                active_when=_FULL_NEW,
            ),
            FieldSpec(
                "powershell_path", "系统工具", "--powershell-path",
                "file",
                help="独立哈希抽验使用；留空时优先继承已验证路径，其次自动发现。",
                filetypes=_EXE_TYPES, section="工具路径", top_menu=True,
                active_when=_FULL_POWERSHELL,
            ),
            FieldSpec(
                "timeout_action", "超时默认", "--timeout-action",
                "choice", "continue_waiting",
                choices=(
                    ("继续等待（默认）", "continue_waiting"),
                    ("跳过并记录", "skip_and_record"),
                    ("停止并保留续传", "stop_and_resume"),
                ),
                help="动态无进展阈值到达后的默认处置；弹窗仍可针对当前文件改选。",
                section="高级设置", top_menu=True,
                active_when=_FULL_HASHED,
            ),
            FieldSpec(
                "retry_mode", "重试范围", "--retry-mode", "choice",
                "pending",
                choices=(
                    ("仅未处理（默认）", "pending"),
                    ("瞬时失败", "transient"),
                    ("全部未成功", "all-unsuccessful"),
                ),
                help="恢复 session 时选择哈希重试集合，不改变 partial 的冻结配置。",
                section="故障恢复", active_when=_FULL_RESUME,
            ),
            FieldSpec(
                "show_current_file", "当前文件", "--show-current-file",
                "choice_flag", False,
                choices=(("关闭（默认）", False), ("显示", True)),
                flag_value=True,
                help="在进度区显示正在处理的相对路径；默认关闭以减少事件量。",
                section="高级设置", top_menu=True,
            ),
        ),
    ),
    TaskSpec(
        "quick_scan",
        "scan",
        "DBS-12  快速档案扫描",
        "快速档案扫描",
        "快速登记目录与文件属性，不读取内容或调用外部工具。",
        "档案只读 · 快速 · 生成快照",
        (
            FieldSpec(
                "start_mode", "启动方式", None, "choice", "new",
                choices=(
                    ("新建快速扫描（默认）", "new"),
                    ("恢复未完成扫描", "resume"),
                ),
                help="恢复时沿用 partial 内冻结的 Quick 配置。",
                section="启动方式",
            ),
            FieldSpec(
                "roots", "档案根目录", "--root", "multidir", required=True,
                help="使用“添加目录”建立列表；可修改为“label=路径”，也可用 ×"
                     " 单独移除，最多 9 个。",
                section="任务输入", active_when=_QUICK_NEW,
            ),
            FieldSpec(
                "root_batch_mode", "生成方式", None, "choice",
                _ROOT_BATCH_SEPARATE,
                choices=(
                    ("分别生成：每个目录一个数据库（默认）",
                     _ROOT_BATCH_SEPARATE),
                    ("合并生成：所有目录一个数据库", _ROOT_BATCH_COMBINED),
                ),
                help="分别模式按添加顺序逐项运行，单项失败后继续下一项；"
                     "停止会终止整个队列。",
                section="任务输入", active_when=_QUICK_NEW,
            ),
            FieldSpec(
                "resume", "续传快照", "--resume", "file", required=True,
                help="必须指向 schema 4 .partial.sqlite；恢复时不覆盖冻结参数。",
                filetypes=_PARTIAL_TYPES, section="任务输入",
                active_when=_QUICK_RESUME,
            ),
            FieldSpec(
                "output_dir", "快照目录", "--output-dir", "dir",
                _DEFAULT_SNAPSHOTS_DIR,
                help="默认指向项目内 Output\\Snapshots；也可选择其它完整路径。",
                section="结果输出", active_when=_QUICK_NEW,
            ),
            FieldSpec(
                "collect_file_id", "NTFS标识", "--no-file-id",
                "choice_flag", True,
                choices=(
                    ("采集（默认）", True),
                    ("不采集（No-FID）", False),
                ),
                flag_value=False,
                help="建议采集；选择“不采集”可提高兼容性，但会降低移动／"
                     "重命名判定证据。",
                section="快照内容", active_when=_QUICK_NEW,
            ),
        ),
    ),
    TaskSpec(
        "check_format",
        "check-format",
        "DBS-32  文件结构核验",
        "文件结构核验",
        "按封存快照定位文件，检查常见文件格式能否正常读取。",
        "档案只读 · 生成 CSV/Markdown 报告",
        (
            FieldSpec(
                "snapshot", "封存快照", "--snapshot", "file",
                required=True, filetypes=_SQLITE_TYPES, section="任务输入",
            ),
            FieldSpec(
                "root_map", "档案根目录", "--root", "multimapdir",
                required=True,
                help="必须指定。单根快照可直接添加当前文件夹；多根快照需逐项"
                     "填写“label=当前路径”，label 必须与快照一致。",
                section="档案位置",
            ),
            FieldSpec(
                "check_scope", "校验范围", None, "choice", "full",
                choices=(
                    ("校验全部可校验文件（100%）（默认）", "full"),
                    ("按比例抽样", "sample"),
                ),
                help="抽样适合快速排查；正式完整性校验应选择全部。",
                section="校验范围",
            ),
            FieldSpec(
                "sample_percent", "抽样比例", "--sample-percent",
                default="10.0", help="必须大于 0 且不超过 100。",
                section="校验范围", active_when=_FORMAT_SAMPLE,
            ),
            FieldSpec(
                "report_dir", "报告目录", "--report-dir", "dir",
                _DEFAULT_REPORTS_DIR,
                help="默认指向项目内 Output\\Reports；也可选择其它完整路径。",
                section="结果输出",
            ),
            FieldSpec(
                "exiftool_path", "Exif工具", "--exiftool-path", "file",
                help="留空时优先使用本窗口已验证路径，其次自动发现；填写则手动覆盖。",
                filetypes=_EXE_TYPES,
                section="工具路径", top_menu=True,
            ),
            FieldSpec(
                "ffprobe_path", "视频工具", "--ffprobe-path", "file",
                help="留空时优先使用本窗口已验证路径，其次自动发现；填写则手动覆盖。",
                filetypes=_EXE_TYPES,
                section="工具路径", top_menu=True,
            ),
            FieldSpec(
                "sevenzip_path", "压缩工具", "--sevenzip-path", "file",
                help="留空时优先使用本窗口已验证路径，其次自动发现；填写则手动覆盖。",
                filetypes=_EXE_TYPES,
                section="工具路径", top_menu=True,
            ),
            FieldSpec(
                "force", "指纹降级", "--force",
                "choice_flag", False,
                choices=(("不启用（默认）", False), ("启用", True)),
                flag_value=True,
                help="仅允许指纹缺失；指纹与实际字节不符仍会拒绝。",
                section="故障恢复",
            ),
        ),
    ),
    TaskSpec(
        "check_hash",
        "check-hash",
        "DBS-31  内容哈希核验",
        "内容哈希核验",
        "按封存快照抽样或全量重算 SHA-256，核对文件内容。",
        "档案只读 · 生成 JSON 报告",
        (
            FieldSpec(
                "snapshot", "封存快照", "--snapshot", "file",
                required=True, filetypes=_SQLITE_TYPES, section="任务输入",
            ),
            FieldSpec(
                "root_map", "档案根目录", "--root", "multimapdir",
                required=True,
                help="必须指定。单根快照可直接添加当前文件夹；多根快照需逐项"
                     "填写“label=当前路径”，label 必须与快照一致。",
                section="档案位置",
            ),
            FieldSpec(
                "check_scope", "校验范围", "--full", "choice_flag", "sample",
                choices=(
                    ("按比例抽样（默认）", "sample"),
                    ("全量重新计算 SHA-256", "full"),
                ),
                flag_value="full",
                help="全量模式会读取所有有基准哈希的文件。",
                section="校验范围",
            ),
            FieldSpec(
                "sample_percent", "哈希抽样", "--sample-percent",
                default="1.0", help="默认抽查 1% 的可哈希文件。",
                section="哈希比例", top_menu=True,
                active_when=_HASH_SAMPLE,
            ),
            FieldSpec(
                "powershell_path", "系统工具", "--powershell-path",
                "file",
                help="留空时优先继承 11／31 已验证路径，其次自动发现；填写则手动覆盖。",
                filetypes=_EXE_TYPES, section="工具路径", top_menu=True,
            ),
            FieldSpec(
                "force", "指纹降级", "--force",
                "choice_flag", False,
                choices=(("不启用（默认）", False), ("启用", True)),
                flag_value=True,
                help="仅允许指纹缺失；指纹与实际字节不符仍会拒绝。",
                section="故障恢复",
            ),
            FieldSpec(
                "report", "报告位置", "--report", "save",
                _DEFAULT_HASH_REPORT_LOCATION,
                help="默认显示自动命名报告的输出目录；如需指定 JSON 文件名，"
                     "可点击浏览另存。",
                filetypes=(("JSON 报告", "*.json"), ("全部文件", "*.*")),
                section="结果输出",
            ),
        ),
    ),
    TaskSpec(
        "diff",
        "diff",
        "DBS-21  快照变更分析",
        "快照变更分析",
        "比较新旧封存快照，生成变更分类与证据等级数据库。",
        "输入只读 · 生成 Diff 数据库",
        (
            FieldSpec(
                "old", "基准快照", "--old", "file",
                required=True, filetypes=_SQLITE_TYPES, section="对比输入",
            ),
            FieldSpec(
                "new", "对比快照", "--new", "file",
                required=True, filetypes=_SQLITE_TYPES, section="对比输入",
            ),
            FieldSpec(
                "output_dir", "差异目录", "--output-dir", "dir",
                _DEFAULT_DIFFS_DIR,
                help="默认指向项目内 Output\\Diffs；也可选择其它完整路径。",
                section="结果输出",
            ),
            FieldSpec(
                "map_root", "根标签映射", "--map-root", "multiline",
                help="可选；每行“旧label=新label”。单根异名通常可自动配对。",
                section="标签配对",
            ),
            FieldSpec(
                "force", "指纹降级", "--force",
                "choice_flag", False,
                choices=(("不启用（默认）", False), ("启用", True)),
                flag_value=True,
                help="降级结果会生成同目录 Issues.md；指纹不符仍会拒绝。",
                section="故障恢复",
            ),
        ),
    ),
    TaskSpec(
        "export_report",
        "export-report",
        "DBS-41  结果报告导出",
        "结果报告导出",
        "从快照或 Diff 数据库导出 Markdown、CSV 与 XLSX 报告。",
        "输入只读 · 生成 CSV/Markdown/XLSX",
        (
            FieldSpec(
                "source_type", "输入类型", None, "choice", "snapshot",
                choices=(
                    ("封存快照：导出清单与诊断 CSV（默认）", "snapshot"),
                    ("Diff 数据库：导出摘要 Markdown 与变更 CSV", "diff"),
                ),
                help=(
                    "快照导出 Tree、Summary 等清单与诊断 CSV；Diff 导出 "
                    "Diff_summary.md、Diff_details.csv、Diff_subtrees.csv 等。"
                    "两者均附中文兼容 XLSX。"
                ),
                section="任务输入",
            ),
            FieldSpec(
                "source_path", "输入数据库", None, "file",
                required=True, filetypes=_SQLITE_TYPES, section="任务输入",
            ),
            FieldSpec(
                "output_dir", "报告目录", "--output-dir", "dir",
                _DEFAULT_REPORTS_DIR,
                help="默认指向项目内 Output\\Reports；也可选择其它完整路径。",
                section="结果输出",
            ),
        ),
    ),
    TaskSpec(
        "storage_list",
        "storage-list",
        "内部步骤  检测物理硬盘",
        "检测物理硬盘",
        "以管理员权限只读检测物理硬盘与分区，建立候选清单。",
        "需要管理员权限 · 物理盘只读 · 可能唤醒硬盘",
        (
            FieldSpec(
                "smartctl_path", "硬盘工具", "--smartctl-path", "file",
                help="留空时优先使用本窗口已验证路径，其次自动发现。",
                filetypes=_EXE_TYPES,
                section="工具路径", top_menu=True,
            ),
            FieldSpec(
                "powershell_path", "系统工具", "--powershell-path",
                "file", help="留空时优先使用本窗口已验证路径，其次自动发现。",
                filetypes=_EXE_TYPES,
                section="工具路径", top_menu=True,
            ),
        ),
    ),
    TaskSpec(
        "storage_collect",
        "storage-collect",
        "STG-11  硬盘信息登记",
        "硬盘信息登记",
        "以管理员权限只读采集硬盘资料，每块硬盘生成独立 ZIP。",
        "需要管理员权限 · 物理盘只读 · 生成 ZIP",
        (
            FieldSpec(
                "disk_number", "物理硬盘池", "--disk-number",
                "disk_pool", required=True,
                help="先检测硬盘，再逐项勾选，或选择全部联机硬盘。脱机或"
                "缺少 smartctl 关联的设备会保留在清单中说明原因，但不可登记；"
                "重新检测会清除旧选择，避免热插拔后沿用过期编号。",
                section="采集目标",
            ),
            FieldSpec(
                "output_dir", "存储档案目录", "--output-dir", "dir",
                _DEFAULT_STORAGE_DIR,
                help="默认指向项目内 Output\\Storage；每块硬盘生成独立 ZIP。",
                section="结果输出",
            ),
            FieldSpec(
                "summary_txt", "简化文本", "--summary-txt",
                "choice_flag", False,
                choices=(
                    ("不生成（默认）", False),
                    ("生成 ZIP 外部 TXT", True),
                ),
                flag_value=True,
                help="完整结构化资料始终保存在 ZIP 的 JSON 成员中。",
                section="结果输出",
            ),
            FieldSpec(
                "smartctl_path", "硬盘工具", "--smartctl-path", "file",
                help="留空时优先使用本窗口已验证路径，其次自动发现。",
                filetypes=_EXE_TYPES,
                section="工具路径", top_menu=True,
            ),
            FieldSpec(
                "powershell_path", "系统工具", "--powershell-path",
                "file", help="留空时优先使用本窗口已验证路径，其次自动发现。",
                filetypes=_EXE_TYPES,
                section="工具路径", top_menu=True,
            ),
        ),
    ),
)

TASK_BY_KEY = {task.key: task for task in TASKS}
_RESTORABLE_TASK_KEYS = frozenset(
    task.key for task in TASKS if task.key != "storage_list")
_HASH_PERCENTAGE_MENU_FIELDS = (
    ("full_scan", "verify_percent", "DBS-11 独立哈希抽验", True),
    ("check_hash", "sample_percent", "DBS-31 内容哈希抽样", False),
)
_TASK_MENU_SECTIONS = (
    (
        "环境",
        ("env_check",),
    ),
    (
        "数据",
        (
            "full_scan", "quick_scan", "diff",
            "check_hash", "check_format", "export_report",
        ),
    ),
    ("硬盘", ("storage_collect",)),
)
_TASK_MENU_SECTION_COLOURS = {
    "环境": ("Env", _GREEN, _GREEN_DEEP, _GREEN_SOFT),
    "数据": ("Data", _AMBER, _AMBER_DEEP, _AMBER_SOFT),
    "硬盘": ("Storage", _RED, _RED_DEEP, _RED_SOFT),
}
_TASK_MENU_SEPARATOR_AFTER = frozenset((
    "env_check", "quick_scan", "diff", "check_format", "export_report",
))
_TASK_MENU_ORDER = tuple(
    task_key
    for _label, task_keys in _TASK_MENU_SECTIONS
    for task_key in task_keys
)
_TASK_MENU_SECTION_BY_KEY = {
    task_key: section_label
    for section_label, task_keys in _TASK_MENU_SECTIONS
    for task_key in task_keys
}
_TASK_TOOLBAR_ROWS = (
    ("环境", "环境 ENV", ("env_check",)),
    ("数据", "数据 DBS", _TASK_MENU_SECTIONS[1][1]),
    ("硬盘", "硬盘 STG", _TASK_MENU_SECTIONS[2][1]),
)
_TASK_TOOLBAR_LABELS = {
    "env_check": "运行环境检测",
    "full_scan": "完整档案扫描",
    "quick_scan": "快速档案扫描",
    "diff": "快照变更分析",
    "check_hash": "内容哈希核验",
    "check_format": "文件结构核验",
    "export_report": "结果报告导出",
    "storage_collect": "硬盘信息登记",
}


def task_display_title(task_key: str) -> str:
    """返回设置卡与功能模块共用的六字标题。"""
    return _TASK_TOOLBAR_LABELS.get(task_key, TASK_BY_KEY[task_key].title)


_TASK_TOOLBAR_BUTTON_WIDTH = 12
_TASK_TOOLBAR_BUTTON_PADDING = (12, 4)
_TASK_TOOLBAR_STYLE_PREFIX = "Env"
_TASK_TOOLBAR_LABEL_COLOUR = _TEXT
_UNIFIED_ACTION_BACKGROUND = _GREEN_DARK
_UNIFIED_ACTION_FOREGROUND = "white"
_RUN_BUTTON_TEXT = "开始任务"
_COLLAPSED_PANEL_TITLE_FONT = ("Microsoft YaHei UI", 9, "bold")
_PANEL_HEADER_PADX = 14
_PANEL_ACTION_BUTTON_WIDTH = 12
_PANEL_ACTION_BUTTON_GAP = 6
_FORM_ACTION_BUTTON_WIDTH = 12
_FORM_FIELD_TITLE_MAX_CHARS = 6
_STORAGE_DISK_CHECKBOX_SIZE = 20
_COLLAPSED_SETTINGS_HEADER_PADY = (8, 8)


def _lines(value: object) -> list[str]:
    return [line.strip() for line in str(value or "").splitlines()
            if line.strip()]


def _task_values(task: TaskSpec,
                 values: dict[str, object]) -> dict[str, object]:
    merged = {spec.key: spec.default for spec in task.fields}
    merged.update(values)
    return merged

def _root_job_label(root_spec: str) -> str:
    """返回适合队列显示的根标签，不访问目录内容。"""
    try:
        label, _path = core.parse_root_spec(root_spec)
    except (OSError, ValueError, core.PreflightError):
        label = ""
    return label or root_spec.strip() or "未命名目录"


def build_run_jobs(task_key: str,
                   values: dict[str, object]) -> list[RunJob]:
    """把 GUI 参数拆成实际子进程任务；目录和硬盘均可逐项排队。"""
    task = TASK_BY_KEY[task_key]
    merged = _task_values(task, values)
    if task_key == "storage_collect":
        jobs: list[RunJob] = []
        seen: set[str] = set()
        for disk_number in _lines(merged.get("disk_number")):
            if disk_number in seen:
                continue
            job_values = dict(merged)
            job_values["disk_number"] = disk_number
            jobs.append(RunJob(f"PhysicalDrive{disk_number}", job_values))
            seen.add(disk_number)
        return jobs or [RunJob(task.title, merged)]
    if task_key not in _ROOT_BATCH_TASKS:
        return [RunJob(task.title, merged)]
    if (task_key in _SCAN_TASK_KEYS
            and merged.get("start_mode") == "resume"):
        return [RunJob(task.title, merged)]

    roots = _lines(merged.get("roots"))
    mode = str(merged.get("root_batch_mode") or _ROOT_BATCH_SEPARATE)
    if mode == _ROOT_BATCH_SEPARATE and roots:
        jobs = []
        for root_spec in roots:
            job_values = dict(merged)
            job_values["roots"] = root_spec
            jobs.append(RunJob(_root_job_label(root_spec), job_values))
        return jobs

    label = (
        f"合并 {len(roots)} 个目录"
        if len(roots) > 1 else
        (_root_job_label(roots[0]) if roots else task.title)
    )
    return [RunJob(label, merged)]


def _field_active(spec: FieldSpec, values: dict[str, object]) -> bool:
    return all(values.get(key) in allowed
               for key, allowed in spec.active_when)


def active_field_keys(task_key: str,
                      values: dict[str, object]) -> set[str]:
    """返回当前模式下真正会生效的 GUI 字段，供界面与测试共用。"""
    task = TASK_BY_KEY[task_key]
    merged = _task_values(task, values)
    return {spec.key for spec in task.fields if _field_active(spec, merged)}


def build_tool_args(task_key: str, values: dict[str, object]) -> list[str]:
    """把 GUI 值转换为统一入口参数；目录参数统一展开为绝对路径。"""
    task = TASK_BY_KEY[task_key]
    values = _task_values(task, values)
    args = [task.command]
    if task_key in _SCAN_TASK_KEYS:
        mode = "full" if task_key == "full_scan" else "quick"
        args += ["--mode", mode]
        if values.get("start_mode") == "resume":
            resume = str(values.get("resume") or "").strip()
            if resume:
                args += ["--resume", _absolute(resume), "--manual-resume"]
            retry_mode = str(values.get("retry_mode") or "").strip()
            if task_key == "full_scan" and retry_mode:
                args += ["--retry-mode", retry_mode]
            if (task_key == "full_scan"
                    and bool(values.get("show_current_file"))):
                args.append("--show-current-file")
            args.append("--control-stdin")
            return args
    if task_key == "export_report":
        source_type = str(values.get("source_type") or "snapshot")
        source_path = str(values.get("source_path") or "").strip()
        if source_path:
            args += ["--" + source_type, source_path]
        output_dir = str(values.get("output_dir") or "").strip()
        if output_dir:
            args += ["--output-dir", _absolute(output_dir)]
        return args
    for spec in task.fields:
        if not spec.flag or not _field_active(spec, values):
            continue
        value = values.get(spec.key, spec.default)
        if spec.kind == "bool":
            if bool(value):
                args.append(spec.flag)
        elif spec.kind == "inverse_bool":
            if not bool(value):
                args.append(spec.flag)
        elif spec.kind == "choice_flag":
            if value == spec.flag_value:
                args.append(spec.flag)
        elif spec.kind == "dir":
            text = str(value or "").strip()
            if text:
                args += [spec.flag, _absolute(text)]
        elif spec.kind == "multimapdir":
            for item in _lines(value):
                is_mapping = "=" in item and not os.path.isabs(
                    item.strip().strip('"'))
                label, separator, path = (
                    item.partition("=") if is_mapping else ("", "", item))
                current_path = _absolute(path if separator else item)
                root_arg = (
                    f"{label.strip()}={current_path}"
                    if separator else current_path
                )
                args += [spec.flag, root_arg]
        elif spec.kind in ("multidir", "multiline"):
            for item in _lines(value):
                args += [spec.flag, item]
        else:
            text = str(value or "").strip()
            if (task_key == "check_hash" and spec.key == "report"
                    and os.path.normcase(_absolute(text)) == os.path.normcase(
                        _DEFAULT_HASH_REPORT_LOCATION)):
                # GUI 显示实际默认目录；保持不改写现有 CLI 协议，由 DBS-31
                # 按原规则在该目录中自动生成报告文件名。
                continue
            if text:
                args += [spec.flag, text]
    if task_key in _SCAN_TASK_KEYS:
        args.append("--control-stdin")
    return args


def preview_commands(task_key: str,
                     values: dict[str, object]) -> list[tuple[str, str]]:
    """返回将实际执行的队列命令，供预览、复制和日志共用。"""
    previews = []
    for job in build_run_jobs(task_key, values):
        parts = ["python", "-u", r".\Script\Script_DAISY_MAIN.py"]
        parts += build_tool_args(task_key, job.values)
        previews.append((job.label, subprocess.list2cmdline(parts)))
    return previews


def preview_command(task_key: str, values: dict[str, object]) -> str:
    previews = preview_commands(task_key, values)
    if len(previews) == 1:
        return previews[0][1]
    return f"队列 {len(previews)} 项（分别生成）｜首项：{previews[0][1]}"


def _absolute(path: str) -> str:
    path = os.path.expandvars(os.path.expanduser(path.strip().strip('"')))
    if not os.path.isabs(path):
        path = os.path.join(_BASE, path)
    return os.path.abspath(path)


def _root_path(spec: str) -> str:
    _label, sep, path = spec.partition("=")
    return path if sep else spec


def root_confirmation_text(
    task_key: str, values: dict[str, object],
) -> str:
    """返回开始前用于核对扫描范围的完整目录清单。"""
    if task_key not in _ROOT_BATCH_TASKS:
        return ""
    if (task_key in _SCAN_TASK_KEYS
            and values.get("start_mode") == "resume"):
        return ""
    roots = [
        _absolute(_root_path(root_spec))
        for root_spec in _lines(values.get("roots"))
    ]
    if not roots:
        return ""
    title = task_display_title(task_key)
    mode = str(values.get("root_batch_mode") or _ROOT_BATCH_SEPARATE)
    if mode == _ROOT_BATCH_COMBINED and len(roots) > 1:
        heading = (
            f"将对以下文件夹合并运行{title}，并共同生成一个数据库："
        )
    elif len(roots) > 1:
        heading = (
            f"将对以下文件夹分别运行{title}并分别生成数据库："
        )
    else:
        heading = f"将对以下文件夹运行{title}并生成数据库："
    return heading + "\n" + "\n".join(f"• {path}" for path in roots)


def validate_values(task_key: str, values: dict[str, object]) -> list[str]:
    """返回适合直接展示给用户的参数问题。"""
    issues: list[str] = []
    task = TASK_BY_KEY[task_key]
    values = _task_values(task, values)
    active_keys = active_field_keys(task_key, values)
    fields = {spec.key: spec for spec in task.fields}

    for spec in task.fields:
        if spec.key not in active_keys:
            continue
        value = values.get(spec.key, spec.default)
        if spec.required and not _lines(value):
            issues.append(f"请填写“{spec.label}”。")

    if task_key in _SCAN_TASK_KEYS:
        resume = str(values.get("resume") or "").strip()
        if (values.get("start_mode") == "resume" and resume
                and not resume.lower().endswith(".partial.sqlite")):
            issues.append("续传文件必须以 .partial.sqlite 结尾。")
    if task_key == "storage_collect":
        disk_numbers = _lines(values.get("disk_number"))
        if any(not re.fullmatch(r"\d+", number) for number in disk_numbers):
            issues.append("“物理硬盘池”包含无效编号，请重新检测并选择。")

    numeric_rules = {
        ("full_scan", "verify_percent"): (0.0, 100.0, True, False),
        ("full_scan", "format_sample_percent"): (
            0.0, 100.0, False, False),
        ("check_format", "sample_percent"): (0.0, 100.0, False, False),
        ("check_hash", "sample_percent"): (0.0, 100.0, False, False),
    }
    for (rule_task, key), rule in numeric_rules.items():
        if rule_task != task_key or key not in active_keys:
            continue
        low, high, allow_zero, integer_only = rule
        raw = str(values.get(key) or "").strip()
        if not raw:
            continue
        try:
            number = float(raw)
        except ValueError:
            issues.append(f"“{fields[key].label}”必须是数字。")
            continue
        label = fields[key].label
        if not math.isfinite(number):
            issues.append(f"“{label}”必须是有限数字。")
            continue
        if integer_only and not number.is_integer():
            issues.append(f"“{label}”必须是整数。")
            continue
        if number < low or (number == low and not allow_zero):
            op = "不小于" if allow_zero else "大于"
            issues.append(f"“{label}”必须{op} {low:g}。")
        if high is not None and number > high:
            issues.append(f"“{label}”不能大于 {high:g}。")

    if "roots" in active_keys:
        root_specs = _lines(values.get("roots"))
        if len(root_specs) > _MAX_ROOT_DIRECTORIES:
            issues.append(
                f"档案根目录最多只能添加 {_MAX_ROOT_DIRECTORIES} 个。")
        seen_paths: set[str] = set()
        seen_labels: set[str] = set()
        combined = (
            values.get("root_batch_mode") == _ROOT_BATCH_COMBINED)
        for root_spec in root_specs:
            is_labeled = (
                "=" in root_spec
                and not re.match(r"^[A-Za-z]:[\\/]", root_spec)
            )
            if is_labeled:
                raw_label, _separator, raw_path = root_spec.partition("=")
                if not raw_label.strip() or not raw_path.strip():
                    issues.append(
                        f"档案根目录应为 label=路径：{root_spec}")
                    continue
            try:
                label, path = core.parse_root_spec(root_spec)
            except core.PreflightError as exc:
                issues.append(str(exc))
                continue
            canonical = os.path.normcase(_absolute(path))
            if canonical in seen_paths:
                issues.append(f"档案根目录重复：{path}")
            seen_paths.add(canonical)
            if combined and label in seen_labels:
                issues.append(f"合并模式中的根标签不能重复：{label}")
            seen_labels.add(label)
            if not os.path.isdir(_absolute(path)):
                issues.append(f"档案根目录不存在：{path}")

    for key in ("root_map",):
        if key not in active_keys:
            continue
        root_specs = _lines(values.get(key))
        direct_specs = [
            spec for spec in root_specs
            if "=" not in spec or os.path.isabs(spec.strip().strip('"'))
        ]
        if direct_specs and len(root_specs) != 1:
            issues.append(
                "不带 label 的当前根目录只适用于单根快照；"
                "多根快照应逐项使用 label=当前路径。")
        for root_spec in root_specs:
            is_mapping = "=" in root_spec and not os.path.isabs(
                root_spec.strip().strip('"'))
            label, sep, path = (
                root_spec.partition("=")
                if is_mapping else ("", "", root_spec))
            if sep and (not label.strip() or not path.strip()):
                issues.append(f"根目录映射应为 label=路径：{root_spec}")
                continue
            current_path = path if sep else root_spec
            if not os.path.isdir(_absolute(current_path)):
                issues.append(f"当前档案根目录不存在：{current_path}")

    for key in ("map_root",):
        if key not in active_keys:
            continue
        for mapping in _lines(values.get(key)):
            left, sep, right = mapping.partition("=")
            if not sep or not left or not right:
                issues.append(f"根标签映射应为 旧label=新label：{mapping}")

    input_files = {
        "previous_snapshot", "resume", "snapshot", "old", "new",
        "source_path", "exiftool_path", "ffprobe_path", "sevenzip_path",
        "powershell_path", "smartctl_path", "archive",
    }
    for key in input_files:
        if key not in active_keys:
            continue
        raw = str(values.get(key) or "").strip()
        if raw and not os.path.isfile(_absolute(raw)):
            issues.append(f"文件不存在：{raw}")
    return issues


class ToolTip:
    """为按钮提供延迟出现、自动避开屏幕边缘的简短说明。"""

    def __init__(self, widget: tk.Misc, text: str, delay_ms: int = 480) -> None:
        self.widget = widget
        self.text = text
        self.delay_ms = delay_ms
        self._after_id: str | None = None
        self._window: tk.Toplevel | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")
        widget.bind("<Destroy>", self._hide, add="+")

    def _schedule(self, _event: tk.Event | None = None) -> None:
        self._cancel()
        self._after_id = self.widget.after(self.delay_ms, self._show)

    def _cancel(self) -> None:
        if self._after_id is None:
            return
        try:
            self.widget.after_cancel(self._after_id)
        except tk.TclError:
            pass
        self._after_id = None

    def _show(self) -> None:
        self._after_id = None
        try:
            if self._window is not None or not self.widget.winfo_exists():
                return
            pointer_x = self.widget.winfo_pointerx()
            pointer_y = self.widget.winfo_pointery()
        except (AttributeError, tk.TclError, TypeError):
            return
        window = tk.Toplevel(self.widget)
        self._window = window
        window.wm_overrideredirect(True)
        try:
            window.attributes("-topmost", True)
        except tk.TclError:
            pass
        work_area = _monitor_work_area_for_window(self.widget)
        top_level = self.widget.winfo_toplevel()
        font_family = getattr(
            top_level, "_daisy_font_family", _UI_FONT_FAMILY)
        font_size_delta = int(getattr(
            top_level, "_daisy_font_size_delta", 0))
        label = tk.Label(
            window, text=self.text, bg=_TEXT, fg="white",
            font=(font_family, 9 + font_size_delta), justify="left",
            relief="solid", bd=1, padx=9, pady=6,
            wraplength=max(220, min(420, work_area.width - 32)),
        )
        label.pack()
        window.update_idletasks()
        width = window.winfo_reqwidth()
        height = window.winfo_reqheight()
        minimum_x = work_area.left + 8
        maximum_x = max(minimum_x, work_area.right - width - 8)
        minimum_y = work_area.top + 8
        maximum_y = max(minimum_y, work_area.bottom - height - 8)
        x = min(max(pointer_x + 14, minimum_x), maximum_x)
        preferred_y = pointer_y + 18
        if preferred_y > maximum_y:
            preferred_y = pointer_y - height - 14
        y = min(max(preferred_y, minimum_y), maximum_y)
        window.wm_geometry(f"+{x}+{y}")

    def _hide(self, _event: tk.Event | None = None) -> None:
        self._cancel()
        if self._window is None:
            return
        try:
            self._window.destroy()
        except tk.TclError:
            pass
        self._window = None


def attach_tooltip(widget: tk.Misc, text: str) -> ToolTip:
    """附加并保留 Tooltip，避免实例被提前回收。"""
    tooltip = ToolTip(widget, text)
    widget._daisy_tooltip = tooltip  # type: ignore[attr-defined]
    return tooltip


class AdminModeSwitch(tk.Frame):
    """顶部管理员状态开关；开启动作由外部完成 UAC 重启。"""

    def __init__(
        self, master: tk.Misc, *, value: bool = False,
        enabled: bool = True, command=None,
    ) -> None:
        super().__init__(master, bg=_SURFACE)
        self._value = bool(value)
        self._enabled = bool(enabled)
        self._hovered = False
        self._command = command

        self.title_label = tk.Label(
            self, text="管理员模式", bg=_SURFACE, fg=_TEXT,
            font=("Microsoft YaHei UI", 8, "bold"),
        )
        self.title_label.pack(side="left")
        self.canvas = tk.Canvas(
            self, width=42, height=22, bg=_SURFACE,
            highlightthickness=0, bd=0, takefocus=True,
        )
        self.canvas.pack(side="left", padx=(6, 4))
        self.state_label = tk.Label(
            self, width=4, bg=_SURFACE,
            font=("Microsoft YaHei UI", 8, "bold"),
        )
        self.state_label.pack(side="left")

        for widget in self.tooltip_widgets:
            widget.bind("<Button-1>", self._activate, add="+")
            widget.bind("<Enter>", self._enter, add="+")
            widget.bind("<Leave>", self._leave, add="+")
        self.canvas.bind("<space>", self._activate, add="+")
        self.canvas.bind("<Return>", self._activate, add="+")
        self._draw()

    @property
    def value(self) -> bool:
        return self._value

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def tooltip_widgets(self) -> tuple[tk.Misc, ...]:
        return (self.title_label, self.canvas, self.state_label)

    def set_mode(
        self, *, value: bool | None = None,
        enabled: bool | None = None,
    ) -> None:
        if value is not None:
            self._value = bool(value)
        if enabled is not None:
            self._enabled = bool(enabled)
        if not self._enabled:
            self._hovered = False
        self._draw()

    def _activate(self, _event: tk.Event | None = None) -> None:
        if not self._enabled:
            return
        self.canvas.focus_set()
        if callable(self._command):
            self._command(not self._value)

    def _enter(self, _event: tk.Event | None = None) -> None:
        if self._enabled:
            self._hovered = True
            self._draw()

    def _leave(self, _event: tk.Event | None = None) -> None:
        if self._hovered:
            self._hovered = False
            self._draw()

    def _draw(self) -> None:
        if self._value:
            track = _GREEN_DEEP if self._hovered else _GREEN_DARK
            if not self._enabled:
                track = _GREEN
        else:
            track = _BORDER if self._hovered else _CONTROL_HOVER
            if not self._enabled:
                track = _CONTROL
        self.canvas.delete("all")
        self.canvas.create_oval(
            2, 2, 20, 20, fill=track, outline=track)
        self.canvas.create_oval(
            22, 2, 40, 20, fill=track, outline=track)
        self.canvas.create_rectangle(
            11, 2, 31, 20, fill=track, outline=track)
        knob_left = 22 if self._value else 4
        self.canvas.create_oval(
            knob_left, 4, knob_left + 14, 18,
            fill=_FIELD, outline=_BORDER,
        )
        self.canvas.configure(
            cursor="hand2" if self._enabled else "arrow")
        title_colour = (
            _GREEN_DEEP if self._value else
            _TEXT if self._enabled else _MUTED
        )
        self.title_label.configure(fg=title_colour)
        self.state_label.configure(
            text="开启" if self._value else "关闭",
            fg=_GREEN_DEEP if self._value else _MUTED,
        )


class DirectoryListEditor(tk.Frame):
    """最多九项的目录列表；每项可编辑标签并单独移除。"""

    def __init__(self, master: tk.Misc, *, initial: object = "",
                 title: str = "档案根目录", on_change=None,
                 max_items: int = _MAX_ROOT_DIRECTORIES) -> None:
        super().__init__(
            master, bg=_SURFACE, highlightbackground=_BORDER,
            highlightthickness=1,
        )
        self.title = title
        self.on_change = on_change
        self.max_items = max_items
        self._items = _lines(initial)[:max_items]
        self._variables: list[tk.StringVar] = []
        self._last_directory = _BASE
        self.grid_columnconfigure(0, weight=1)

        self.rows = tk.Frame(self, bg=_SURFACE)
        self.rows.grid(row=1, column=0, sticky="ew", padx=7, pady=(2, 4))
        self.rows.grid_columnconfigure(0, weight=1)

        footer = tk.Frame(self, bg=_SURFACE)
        footer.grid(row=0, column=0, sticky="ew", padx=7, pady=(4, 2))
        footer.grid_columnconfigure(0, weight=1)
        self.add_button = ttk.Button(
            footer, text="添加目录", style="FormAction.TButton",
            width=_FORM_ACTION_BUTTON_WIDTH,
            command=self.add_directory,
        )
        self.add_button.grid(row=0, column=1, sticky="e")
        attach_tooltip(
            self.add_button,
            f"选择并加入一个{self.title}；最多可添加 {self.max_items} 项。",
        )
        self.count_label = tk.Label(
            footer, bg=_SURFACE, fg=_MUTED,
            font=("Microsoft YaHei UI", 8), anchor="w",
        )
        self.count_label.grid(row=0, column=0, sticky="w")
        self._render_rows()

    def _notify(self) -> None:
        if callable(self.on_change):
            self.on_change()

    @staticmethod
    def _canonical_path(value: str) -> str:
        try:
            _label, path = core.parse_root_spec(value)
        except (OSError, ValueError, core.PreflightError):
            path = value
        return os.path.normcase(_absolute(path))

    def _render_rows(self) -> None:
        for child in self.rows.winfo_children():
            child.destroy()
        self._variables = []
        if not self._items:
            tk.Label(
                self.rows, text="尚未添加目录", bg=_SURFACE, fg=_MUTED,
                font=("Microsoft YaHei UI", 8), anchor="w",
            ).grid(row=0, column=0, sticky="ew", padx=7, pady=4)
        for index, item in enumerate(self._items):
            row = tk.Frame(self.rows, bg=_SURFACE)
            row.grid(row=index, column=0, sticky="ew", pady=(0, 4))
            row.grid_columnconfigure(1, weight=1)
            tk.Label(
                row, text=f"{index + 1}", width=2,
                bg=_CONTROL, fg=_GREEN_DEEP,
                font=("Microsoft YaHei UI", 8, "bold"),
            ).grid(row=0, column=0, sticky="ns", padx=(0, 6))
            variable = tk.StringVar(value=item)
            variable.trace_add(
                "write",
                lambda *_args, i=index, v=variable: self._edited(i, v),
            )
            self._variables.append(variable)
            ttk.Entry(row, textvariable=variable).grid(
                row=0, column=1, sticky="ew")
            remove_button = ttk.Button(
                row, text="×", width=3, style="Remove.TButton",
                command=lambda i=index: self.remove(i),
            )
            remove_button.grid(row=0, column=2, padx=(6, 0))
            attach_tooltip(remove_button, f"从队列中移除第 {index + 1} 个目录。")
        self.count_label.configure(
            text=f"已添加 {len(self._items)}/{self.max_items}")
        self.add_button.configure(
            state="disabled" if len(self._items) >= self.max_items else "normal")

    def _edited(self, index: int, variable: tk.StringVar) -> None:
        if index < len(self._items):
            self._items[index] = variable.get()
            self._notify()

    def add_value(self, value: str) -> bool:
        value = str(value or "").strip()
        if not value:
            return False
        if len(self._items) >= self.max_items:
            messagebox.showwarning(
                "目录数量已达上限",
                f"最多只能添加 {self.max_items} 个目录。",
                parent=self.winfo_toplevel(),
            )
            return False
        canonical = self._canonical_path(value)
        if any(self._canonical_path(item) == canonical
               for item in self._items):
            messagebox.showinfo(
                "目录已经添加", value, parent=self.winfo_toplevel())
            return False
        self._items.append(value)
        self._render_rows()
        self._notify()
        return True

    def add_directory(self) -> None:
        chosen = filedialog.askdirectory(
            parent=self.winfo_toplevel(), initialdir=self._last_directory,
            title=f"添加{self.title}",
        )
        if not chosen:
            return
        chosen = os.path.normpath(chosen)
        self._last_directory = os.path.dirname(chosen) or chosen
        self.add_value(chosen)

    def remove(self, index: int) -> None:
        if 0 <= index < len(self._items):
            self._items.pop(index)
            self._render_rows()
            self._notify()

    def get(self) -> str:
        values = [variable.get().strip() for variable in self._variables]
        return "\n".join(value for value in values if value)


class StorageDiskPool(tk.Frame):
    """展示当次列盘结果，并允许逐盘或一次选择全部可登记联机盘。"""

    @staticmethod
    def _checkbox_image(
        master: tk.Misc, *, selected: bool, disabled: bool = False,
    ) -> tk.PhotoImage:
        """绘制 20 px 选择框，避免系统原生小指示器随主题缩得过小。"""
        size = _STORAGE_DISK_CHECKBOX_SIZE
        image = tk.PhotoImage(master=master, width=size + 6, height=size)
        border = _MUTED if disabled else (_GREEN_DEEP if selected else _BORDER)
        fill = _FIELD if disabled or not selected else _GREEN_DEEP
        image.put(border, to=(1, 1, size - 1, size - 1))
        image.put(fill, to=(3, 3, size - 3, size - 3))
        if selected:
            for x, y in (
                (5, 10), (6, 11), (7, 12), (8, 13),
                (9, 12), (10, 11), (11, 10), (12, 9),
                (13, 8), (14, 7), (15, 6),
            ):
                image.put("white", to=(x, y, x + 2, y + 2))
        return image

    def __init__(
        self, master: tk.Misc, *,
        options: tuple[StorageDiskOption, ...], initial: object = "",
        on_change=None,
    ) -> None:
        super().__init__(
            master, bg=_SURFACE, highlightbackground=_BORDER,
            highlightthickness=1,
        )
        self.options = options
        self.on_change = on_change
        selected = set(_lines(initial))
        self._variables: dict[int, tk.BooleanVar] = {}
        self.checkboxes: list[tk.Checkbutton] = []
        self._checkbox_images: dict[str, tk.PhotoImage] = {}
        self.grid_columnconfigure(0, weight=1)

        actions = tk.Frame(self, bg=_SURFACE)
        actions.grid(row=0, column=0, sticky="ew", padx=8, pady=(5, 2))
        actions.grid_columnconfigure(2, weight=1)
        self.select_all_button = ttk.Button(
            actions, text="全选", style="FormAction.TButton",
            width=_FORM_ACTION_BUTTON_WIDTH,
            command=self.select_all_online,
        )
        self.select_all_button.grid(
            row=0, column=0, sticky="w", padx=(0, 6))
        self.clear_selection_button = ttk.Button(
            actions, text="取消选择", style="FormAction.TButton",
            width=_FORM_ACTION_BUTTON_WIDTH,
            command=self.clear_selection,
        )
        self.clear_selection_button.grid(row=0, column=1, sticky="w")
        selectable_count = sum(option.selectable for option in options)
        tk.Label(
            actions,
            text=f"可选 {selectable_count}/{len(options)}",
            bg=_SURFACE, fg=_MUTED,
            font=("Microsoft YaHei UI", 8), anchor="e",
        ).grid(row=0, column=2, sticky="e")
        attach_tooltip(
            self.select_all_button,
            "选择当次清单中所有联机且已成功关联 smartctl 的物理硬盘。",
        )
        attach_tooltip(
            self.clear_selection_button,
            "清除硬盘池中的全部勾选，不会重新检测硬盘。",
        )

        rows = tk.Frame(self, bg=_SURFACE)
        rows.grid(row=1, column=0, sticky="ew", padx=8, pady=(1, 5))
        rows.grid_columnconfigure(0, weight=1)
        if not options:
            tk.Label(
                rows, text="尚无硬盘清单，请先点击「检测物理硬盘」。",
                bg=_SURFACE, fg=_MUTED,
                font=("Microsoft YaHei UI", 9), anchor="w",
            ).grid(row=0, column=0, sticky="ew", padx=4, pady=5)
            return

        self._checkbox_images = {
            "off": self._checkbox_image(self, selected=False),
            "on": self._checkbox_image(self, selected=True),
            "disabled": self._checkbox_image(
                self, selected=False, disabled=True),
        }

        for row_index, option in enumerate(options):
            row = tk.Frame(rows, bg=_SURFACE)
            row.grid(row=row_index, column=0, sticky="ew", pady=(0, 3))
            row.grid_columnconfigure(0, weight=1)
            variable = tk.BooleanVar(
                value=option.selectable and option.value in selected)
            self._variables[option.disk_number] = variable
            base_image = self._checkbox_images[
                "off" if option.selectable else "disabled"]
            checkbox = tk.Checkbutton(
                row, text=option.display, variable=variable,
                command=self._notify, state=(
                    "normal" if option.selectable else "disabled"),
                image=base_image,
                selectimage=self._checkbox_images["on"],
                indicatoron=False, compound="left",
                bg=_SURFACE, activebackground=_SURFACE,
                fg=_TEXT, activeforeground=_TEXT,
                disabledforeground=_MUTED, selectcolor=_FIELD,
                font=("Microsoft YaHei UI", 9), anchor="w",
                justify="left", wraplength=650,
                highlightthickness=0, bd=0, relief="flat",
                offrelief="flat", overrelief="flat", padx=4, pady=4,
            )
            checkbox.grid(row=0, column=0, sticky="ew")
            self.checkboxes.append(checkbox)
            status_colour = _GREEN_DEEP if option.selectable else _MUTED
            tk.Label(
                row, text=option.reason, bg=_SURFACE, fg=status_colour,
                font=("Microsoft YaHei UI", 8), anchor="e",
            ).grid(row=0, column=1, sticky="e", padx=(10, 4))
            row.bind(
                "<Configure>",
                lambda event, widget=checkbox: widget.configure(
                    wraplength=max(220, event.width - 145)),
            )

    def _notify(self) -> None:
        if callable(self.on_change):
            self.on_change()

    def select_all_online(self) -> None:
        for option in self.options:
            self._variables[option.disk_number].set(option.selectable)
        self._notify()

    def clear_selection(self) -> None:
        for variable in self._variables.values():
            variable.set(False)
        self._notify()

    def get(self) -> str:
        return "\n".join(
            option.value
            for option in self.options
            if option.selectable
            and self._variables[option.disk_number].get()
        )


def _console_python() -> str:
    executable = os.path.abspath(sys.executable)
    if os.path.basename(executable).lower() == "pythonw.exe":
        candidate = os.path.join(os.path.dirname(executable), "python.exe")
        if os.path.isfile(candidate):
            return candidate
    return executable


def is_windows_administrator() -> bool:
    """返回当前进程是否具有 Windows 管理员权限。"""
    if os.name != "nt":
        return False
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def administrator_restart_parts(
    *, executable: str | None = None,
    argv: list[str] | tuple[str, ...] | None = None,
    frozen: bool | None = None,
) -> tuple[str, str, str]:
    """返回 ShellExecuteW 所需的程序、参数和工作目录。"""
    program = os.path.abspath(executable or sys.executable)
    source_argv = list(sys.argv if argv is None else argv)
    is_frozen = (
        bool(getattr(sys, "frozen", False))
        if frozen is None else bool(frozen)
    )
    if is_frozen:
        arguments = source_argv[1:]
    else:
        arguments = [os.path.abspath(__file__), *source_argv[1:]]
    return program, subprocess.list2cmdline(arguments), _BASE


def restart_as_windows_administrator() -> None:
    """通过 Windows UAC 启动新的管理员 GUI 进程。"""
    if os.name != "nt":
        raise OSError("管理员模式重启仅适用于 Windows")
    import ctypes
    from ctypes import wintypes

    program, parameters, directory = administrator_restart_parts()
    shell_execute = ctypes.windll.shell32.ShellExecuteW
    shell_execute.argtypes = (
        wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR,
        wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_int,
    )
    shell_execute.restype = ctypes.c_ssize_t
    result = int(shell_execute(
        None, "runas", program, parameters, directory, 1,
    ))
    if result <= 32:
        raise OSError(f"Windows 未能启动管理员进程（代码 {result}）")


def project_self_test_missing_files() -> list[str]:
    """返回 DAISY 功能自检缺少的正式测试文件名，不读取任何档案目录。"""
    return [
        name for name in _PROJECT_TEST_FILES
        if not os.path.isfile(os.path.join(_TEST_DIR, name))
    ]


def project_self_test_command(python_executable: str | None = None) -> list[str]:
    """返回 GUI DAISY 功能自检实际使用的 unittest discovery 命令。"""
    return [
        python_executable or _console_python(),
        "-B", "-m", "unittest", "discover",
        "-s", _TEST_DIR,
        "-p", _PROJECT_TEST_PATTERN,
        "-v",
    ]


def project_self_test_preview() -> str:
    """返回适合在 GUI 日志展示的根目录相对命令。"""
    return subprocess.list2cmdline([
        "python", "-B", "-m", "unittest", "discover",
        "-s", r".\Script\Test",
        "-p", _PROJECT_TEST_PATTERN,
        "-v",
    ])


def window_size_for_screen(
    screen_width: int, screen_height: int,
    preferred_size: tuple[int, int] = _DEFAULT_WINDOW_SIZE,
) -> tuple[int, int]:
    """以 1920×1080 为目标，并按当前屏幕留出安全边缘。"""
    target_width, target_height = preferred_size
    width = min(target_width, max(640, screen_width - 80))
    height = min(target_height, max(480, screen_height - 60))
    width = min(width, max(320, screen_width - 20))
    height = min(height, max(320, screen_height - 40))
    return width, height


def fit_window_to_work_area(
    preferred_size: tuple[int, int], position: tuple[int, int],
    work_area: MonitorWorkArea,
    margin: tuple[int, int] = _WINDOW_WORK_MARGIN,
) -> tuple[int, int, int, int]:
    """把客户区尺寸和位置约束到指定显示器工作区。"""
    horizontal_margin, vertical_margin = margin
    available_width = max(1, work_area.width - horizontal_margin)
    available_height = max(1, work_area.height - vertical_margin)
    width = max(1, min(int(preferred_size[0]), available_width))
    height = max(1, min(int(preferred_size[1]), available_height))
    left_margin = max(0, horizontal_margin // 2)
    top_margin = max(0, vertical_margin // 2)
    minimum_x = work_area.left + left_margin
    minimum_y = work_area.top + top_margin
    maximum_x = max(
        minimum_x,
        work_area.right - width - (horizontal_margin - left_margin),
    )
    maximum_y = max(
        minimum_y,
        work_area.bottom - height - (vertical_margin - top_margin),
    )
    x = min(max(int(position[0]), minimum_x), maximum_x)
    y = min(max(int(position[1]), minimum_y), maximum_y)
    return width, height, x, y


def _window_geometry_string(
    width: int, height: int, x: int, y: int,
) -> str:
    return f"{width}x{height}{x:+d}{y:+d}"


def _monitor_work_area_for_window(
    window: tk.Misc,
) -> MonitorWorkArea:
    """返回窗口所在显示器的工作区；非 Windows 使用 Tk 当前屏幕。"""
    if os.name != "nt":
        return MonitorWorkArea(
            0, 0, 0,
            int(window.winfo_screenwidth()),
            int(window.winfo_screenheight()),
        )
    import ctypes
    from ctypes import wintypes

    class MonitorInfo(ctypes.Structure):
        _fields_ = (
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", wintypes.RECT),
            ("rcWork", wintypes.RECT),
            ("dwFlags", wintypes.DWORD),
        )

    try:
        user32 = ctypes.windll.user32
        hwnd = wintypes.HWND(window.winfo_id())
        monitor_from_window = user32.MonitorFromWindow
        monitor_from_window.argtypes = (wintypes.HWND, wintypes.DWORD)
        monitor_from_window.restype = wintypes.HANDLE
        monitor = monitor_from_window(hwnd, 2)
        if not monitor:
            raise OSError("MonitorFromWindow failed")
        info = MonitorInfo()
        info.cbSize = ctypes.sizeof(MonitorInfo)
        if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            raise OSError("GetMonitorInfoW failed")
        dpi = 96
        get_dpi_for_window = getattr(user32, "GetDpiForWindow", None)
        if get_dpi_for_window is not None:
            detected_dpi = int(get_dpi_for_window(hwnd))
            if detected_dpi > 0:
                dpi = detected_dpi
        handle = int(getattr(monitor, "value", monitor) or 0)
        work = info.rcWork
        return MonitorWorkArea(
            handle,
            int(work.left), int(work.top),
            int(work.right), int(work.bottom),
            dpi,
        )
    except (AttributeError, OSError, TypeError, ValueError):
        return MonitorWorkArea(
            0, 0, 0,
            int(window.winfo_screenwidth()),
            int(window.winfo_screenheight()),
        )


def action_button_row_indexes(
    widths: tuple[int, ...], available: int, gap: int = 8,
) -> tuple[tuple[int, ...], ...]:
    """从末项向前换行，返回保持原顺序的按钮行索引。"""
    if available <= 0 or gap < 0 or any(width <= 0 for width in widths):
        raise ValueError("按钮宽度、可用宽度与间距必须为正数")
    rows_from_right: list[list[int]] = []
    current: list[int] = []
    current_width = 0
    for index in reversed(range(len(widths))):
        needed = widths[index] + (gap if current else 0)
        if current and current_width + needed > available:
            rows_from_right.append(current)
            current = []
            current_width = 0
            needed = widths[index]
        current.append(index)
        current_width += needed
    if current:
        rows_from_right.append(current)
    return tuple(
        tuple(reversed(row)) for row in reversed(rows_from_right)
    )


def _version() -> str:
    return "v" + core.SCANNER_VERSION


def project_window_title() -> str:
    return (f"{core.PROJECT_NAME} {_version()} - "
            f"{core.PROJECT_FULL_NAME} - Author: {core.PROJECT_AUTHOR}")


def about_message() -> str:
    """返回关于窗口的版本、功能域与兼容边界。"""
    return (
        f"{core.PROJECT_NAME} {_version()}\n"
        f"{core.PROJECT_FULL_NAME}\n"
        f"作者：{core.PROJECT_AUTHOR}\n"
        f"联系：{_PROJECT_CONTACT}\n\n"
        "环境：检测并管理本机外部工具。\n"
        "数据：生成、核验、分析和导出独立 SQLite 档案快照。\n"
        "硬盘：只读登记物理硬盘信息并生成独立 STG ZIP。\n\n"
        "版本与格式\n"
        f"DAISY／DBS 生成器：{_version()}\n"
        f"DBS SQLite schema：{core.SCHEMA_VERSION}\n"
        f"DBS 元数据 profile：{metadata.PROFILE_VERSION}\n"
        f"DBS 文件名布局：{core.FILENAME_LAYOUT_VERSION}\n"
        f"STG 归档 schema：{storage_core.ARCHIVE_SCHEMA_VERSION}\n"
        f"STG 文件名布局：{storage_core.FILENAME_LAYOUT_VERSION}\n\n"
        "兼容性\n"
        f"DBS 完整快照最低读取器：v{core.MIN_READER_VERSION}\n"
        f"未完成 partial：仅允许相同生成器版本 {_version()} 续传\n\n"
        "DBS 与 STG 数据模型彼此独立。业务数据留在本机；源档案和物理硬盘"
        "保持只读。"
    )


def contact_message() -> str:
    """返回可复制的作者联系信息。"""
    return (
        f"作者：{core.PROJECT_AUTHOR}\n"
        f"GitHub：{_PROJECT_GITHUB_URL}\n"
        f"邮箱：{_PROJECT_CONTACT}"
    )


def _create_daisy_icon(root: tk.Misc, size: int) -> tk.PhotoImage:
    """生成透明底小雏菊图标；提供独立尺寸以避免标题栏缩放发虚。"""
    image = tk.PhotoImage(master=root, width=size, height=size)
    scale = 32.0 / size
    petal_angles = tuple(index * math.pi / 4 for index in range(8))
    for pixel_y in range(size):
        y = (pixel_y + 0.5) * scale - 16.0
        for pixel_x in range(size):
            x = (pixel_x + 0.5) * scale - 16.0
            petal_outer = False
            petal_inner = False
            for angle in petal_angles:
                cosine = math.cos(angle)
                sine = math.sin(angle)
                radial = x * cosine + y * sine
                lateral = -x * sine + y * cosine
                petal_outer = petal_outer or (
                    ((radial - 8.3) / 6.1) ** 2
                    + (lateral / 3.5) ** 2 <= 1.0
                )
                petal_inner = petal_inner or (
                    ((radial - 8.3) / 5.3) ** 2
                    + (lateral / 2.7) ** 2 <= 1.0
                )
            radius_squared = x * x + y * y
            colour = None
            if petal_outer:
                colour = _GREEN_DARK
            if petal_inner:
                colour = "#FFFDF5"
            if radius_squared <= 24.0:
                colour = _AMBER_DEEP
            if radius_squared <= 14.0:
                colour = _AMBER
            if colour is not None:
                image.put(colour, to=(pixel_x, pixel_y))
    return image


def _create_combobox_chevron(
    root: tk.Misc, colour: str,
) -> tk.PhotoImage:
    """创建细线下箭头；图形与点击热区分离，避免过小难点。"""
    image = tk.PhotoImage(master=root, width=22, height=16)
    for step in range(6):
        y = 4 + step
        for x in (5 + step, 16 - step):
            image.put(colour, to=(x, y, x + 2, y + 2))
    return image


def _install_daisy_window_icon(
    root: tk.Misc,
) -> tuple[tk.PhotoImage, ...] | None:
    """安装简洁小雏菊窗口图标，并保留多尺寸图像防止被回收。"""
    try:
        icons = tuple(_create_daisy_icon(root, size) for size in (16, 32, 48))
        root.iconphoto(True, *icons)
        return icons
    except (AttributeError, tk.TclError, TypeError, ValueError):
        return None


class DaisyApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.gui_preferences = load_gui_preferences()
        raw_window_size = self.gui_preferences["window_size"]
        self.default_window_size = (
            int(raw_window_size[0]), int(raw_window_size[1]))
        self.ui_font_family = str(self.gui_preferences["font_family"])
        self.ui_font_size_delta = int(
            self.gui_preferences["font_size_delta"])
        self.confirm_close_when_idle = bool(
            self.gui_preferences["confirm_close_when_idle"])
        self.recovery_scans = list(
            self.gui_preferences.get("recovery_scans") or [])
        self.task = TASK_BY_KEY[str(
            self.gui_preferences["last_task_key"])]
        self.values: dict[
            str, tk.Variable | tk.Text | DirectoryListEditor
            | StorageDiskPool] = {}
        self.saved_values: dict[str, dict[str, object]] = {}
        self.task_menu_entries: dict[str, tuple[tk.Menu, int]] = {}
        self.task_toolbar_buttons: dict[str, ttk.Button] = {}
        self._task_toolbar_layout_ready = False
        self.detected_tools: dict[str, dict[str, object]] = {}
        self.manual_tool_paths: dict[str, str] = {}
        self.install_tool_buttons: dict[str, ttk.Button] = {}
        self.environment_missing_names: tuple[str, ...] = ()
        self.missing_installable_tools: tuple[str, ...] = ()
        self.is_administrator = is_windows_administrator()
        self.storage_disk_choices: tuple[tuple[str, str], ...] = ()
        self.storage_disk_options: tuple[StorageDiskOption, ...] = ()
        self._work_progress_indeterminate = False
        self.current_stage_index = 0
        self.current_stage_total = 0
        self.mini_mode = False
        self.task_toolbar_expanded = True
        self.settings_expanded = True
        self.progress_expanded = False
        self.log_expanded = False
        self.command_preview_expanded = False
        self.log_window: tk.Toplevel | None = None
        self.log_window_text: tk.Text | None = None
        self.log_window_icon_handle: tuple[tk.PhotoImage, ...] | None = None
        self._normal_geometry = ""
        self._normal_window_state = "normal"
        self._normal_position = (0, 0)
        self._normal_monitor_signature: tuple[
            int, int, int, int, int, int
        ] | None = None
        self.process: subprocess.Popen[bytes] | None = None
        self.process_started = 0.0
        self.process_task_key: str | None = None
        self.run_jobs: list[RunJob] = []
        self.run_job_index = -1
        self.run_results: list[int | None] = []
        self.run_outcomes: list[str | None] = []
        self.run_queue_started = 0.0
        self.worker_starting = False
        self.close_after_stop = False
        self.stop_requested = False
        self.save_exit_requested = False
        self.scan_control_sequence = 0
        self.scan_control_state = "idle"
        self.scan_control_previous_state = "idle"
        self.scan_run_result: dict[str, object] | None = None
        self.timeout_dialog: tk.Toplevel | None = None
        self.timeout_dialog_label: tk.Label | None = None
        self.timeout_worker_pid: int | None = None
        self.events: queue.Queue[tuple] = queue.Queue()
        self._configure_window()
        self._configure_styles()
        self._build_shell()
        self._build_menu()
        self._set_progress_expanded(False)
        self._set_log_expanded(False)
        self._select_task(self.task.key, save_current=False)
        self._refresh_recovery_card()
        self._apply_interface_font_preferences()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.bind(
            "<Configure>", self._schedule_monitor_refresh, add="+")
        self.root.after_idle(self._refresh_monitor_layout)
        self.root.after(80, self._poll_events)

    def _configure_window(self) -> None:
        self.root.title(project_window_title())
        self.window_icon_handle: object | None = None
        self.root.after(100, self._apply_window_icon)
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        width, height = window_size_for_screen(
            screen_width, screen_height, self.default_window_size)
        x = max(0, (screen_width - width) // 2)
        y = max(0, (screen_height - height) // 2)
        self.compact_layout = width < 1080 or height < 700
        self.root.geometry(_window_geometry_string(width, height, x, y))
        self.normal_width_cap = min(1200, width)
        self.normal_min_size = (min(760, width), min(640, height))
        self._preferred_normal_size = (width, height)
        self._monitor_signature: tuple[
            int, int, int, int, int, int
        ] | None = None
        self._monitor_applied_size: tuple[int, int] | None = None
        self._monitor_refresh_after_id: str | None = None
        self._form_scroll_sync_after_id: str | None = None
        self.root.minsize(*self.normal_min_size)
        self.root.configure(bg=_BG)
        available_families = {
            family.casefold(): family
            for family in tkfont.families(self.root)
        }
        selected_family = available_families.get(
            self.ui_font_family.casefold())
        if selected_family is None:
            selected_family = available_families.get(
                _UI_FONT_FAMILY.casefold(), _UI_FONT_FAMILY)
        self.ui_font_family = selected_family
        self.gui_preferences["font_family"] = selected_family
        self.root._daisy_font_family = selected_family  # type: ignore[attr-defined]
        self.root._daisy_font_size_delta = (  # type: ignore[attr-defined]
            self.ui_font_size_delta)
        self.root.option_add("*Font", (selected_family, 10))
        self._named_font_base_sizes: dict[str, int] = {}
        for font_name in (
                "TkDefaultFont", "TkTextFont", "TkFixedFont", "TkMenuFont",
                "TkHeadingFont", "TkCaptionFont", "TkSmallCaptionFont",
                "TkIconFont", "TkTooltipFont"):
            try:
                named_font = tkfont.nametofont(font_name, root=self.root)
                self._named_font_base_sizes[font_name] = abs(int(
                    named_font.actual("size")))
                named_font.configure(family=selected_family)
            except tk.TclError:
                continue

    def _schedule_monitor_refresh(
        self, event: tk.Event | None = None,
    ) -> None:
        if event is not None and event.widget is not self.root:
            return
        if self._monitor_refresh_after_id is not None:
            try:
                self.root.after_cancel(self._monitor_refresh_after_id)
            except tk.TclError:
                pass
        self._monitor_refresh_after_id = self.root.after(
            120, self._refresh_monitor_layout)

    def _refresh_monitor_layout(self) -> None:
        """跨屏后按目标显示器工作区重算尺寸、位置和最小值。"""
        self._monitor_refresh_after_id = None
        try:
            state = self.root.state()
            if state in ("iconic", "withdrawn"):
                return
            work_area = _monitor_work_area_for_window(self.root)
            signature = work_area.signature
            current_size = (
                int(self.root.winfo_width()),
                int(self.root.winfo_height()),
            )
            current_position = (
                int(self.root.winfo_x()), int(self.root.winfo_y()))
        except (tk.TclError, TypeError, ValueError):
            return

        if signature == self._monitor_signature:
            if (not self.mini_mode
                    and current_size != self._monitor_applied_size):
                self._preferred_normal_size = current_size
                self._monitor_applied_size = None
            return

        self._monitor_signature = signature
        if state != "normal":
            return
        preferred_size = (
            current_size if self.mini_mode
            else self._preferred_normal_size
        )
        width, height, x, y = fit_window_to_work_area(
            preferred_size, current_position, work_area)

        if self.mini_mode:
            self.root.minsize(min(520, width), min(height, 300))
        else:
            self.normal_width_cap = min(1200, width)
            self.normal_min_size = (
                min(760, width), min(640, height))
            self._sync_task_toolbar_minimum_width()
            self._monitor_applied_size = (
                (width, height)
                if (width, height) != self._preferred_normal_size else None
            )
        if ((width, height) != current_size
                or (x, y) != current_position):
            self.root.geometry(
                _window_geometry_string(width, height, x, y))

    def _apply_window_icon(self) -> None:
        self.window_icon_handle = _install_daisy_window_icon(self.root)

    def _font_tuple(
        self, base_size: int, weight: str = "normal",
    ) -> tuple[object, ...]:
        value: list[object] = [
            self.ui_font_family,
            max(8, int(base_size) + self.ui_font_size_delta),
        ]
        if weight != "normal":
            value.append(weight)
        return tuple(value)

    def _available_ui_font_families(self) -> tuple[str, ...]:
        installed = {
            family.casefold(): family
            for family in tkfont.families(self.root)
        }
        result: list[str] = []
        for candidate in (
                self.ui_font_family, *_UI_FONT_FAMILY_CANDIDATES):
            family = installed.get(candidate.casefold())
            if family is not None and family not in result:
                result.append(family)
        return tuple(result) or (self.ui_font_family,)

    def _apply_style_fonts(self) -> None:
        style_specs = {
            "TLabel": (10, "normal"),
            "Muted.TLabel": (9, "normal"),
            "Badge.TLabel": (9, "bold"),
            "TEntry": (10, "normal"),
            "TCombobox": (10, "normal"),
            "Daisy.TCombobox": (10, "normal"),
            "Browse.TButton": (9, "normal"),
            "FormAction.TButton": (9, "normal"),
            "Remove.TButton": (9, "bold"),
            "Primary.TButton": (10, "bold"),
            "Stop.TButton": (10, "bold"),
            "Secondary.TButton": (10, "normal"),
            "Mini.TButton": (8, "normal"),
            "PanelHeader.TButton": (8, "normal"),
            "MiniStop.TButton": (8, "bold"),
        }
        for style_name, (size, weight) in style_specs.items():
            self.style.configure(
                style_name, font=self._font_tuple(size, weight))
        for _section_label, (
                style_prefix, _accent, _deep, _soft
        ) in _TASK_MENU_SECTION_COLOURS.items():
            for suffix in ("TopTask", "TopTaskSelected"):
                self.style.configure(
                    f"{style_prefix}.{suffix}.TButton",
                    font=self._font_tuple(9),
                )

    def _apply_font_to_tree(self, widget: tk.Misc) -> None:
        try:
            has_font = "font" in widget.keys()
        except (AttributeError, tk.TclError):
            has_font = False
        if has_font:
            try:
                base = getattr(widget, "_daisy_base_font", None)
                if base is None:
                    raw_font = str(widget.cget("font"))
                    named_size = self._named_font_base_sizes.get(raw_font)
                    actual = tkfont.Font(
                        root=self.root, font=raw_font).actual()
                    base = (
                        named_size or abs(int(actual["size"])),
                        str(actual["weight"]), str(actual["slant"]),
                        bool(actual["underline"]), bool(actual["overstrike"]),
                    )
                    widget._daisy_base_font = base  # type: ignore[attr-defined]
                size, weight, slant, underline, overstrike = base
                font_parts: list[object] = [
                    self.ui_font_family,
                    max(8, int(size) + self.ui_font_size_delta),
                ]
                if weight != "normal":
                    font_parts.append(weight)
                if slant != "roman":
                    font_parts.append(slant)
                if underline:
                    font_parts.append("underline")
                if overstrike:
                    font_parts.append("overstrike")
                widget.configure(font=tuple(font_parts))
            except (tk.TclError, TypeError, ValueError):
                pass
        try:
            children = widget.winfo_children()
        except (AttributeError, tk.TclError):
            children = ()
        for child in children:
            self._apply_font_to_tree(child)

    def _apply_interface_font_preferences(self) -> None:
        self.root._daisy_font_family = (  # type: ignore[attr-defined]
            self.ui_font_family)
        self.root._daisy_font_size_delta = (  # type: ignore[attr-defined]
            self.ui_font_size_delta)
        self.root.option_add("*Font", (self.ui_font_family, 10))
        self._apply_font_to_tree(self.root)
        for font_name, base_size in self._named_font_base_sizes.items():
            try:
                tkfont.nametofont(font_name, root=self.root).configure(
                    family=self.ui_font_family,
                    size=max(8, base_size + self.ui_font_size_delta),
                )
            except tk.TclError:
                continue
        self._apply_style_fonts()
        if hasattr(self, "form_inner"):
            self._configure_form_label_column()
        self.settings_title_expanded_font = self._font_tuple(
            14 if self.compact_layout else 16, "bold")
        self.title_label.configure(font=(
            self.settings_title_expanded_font
            if self.settings_expanded else self._font_tuple(9, "bold")
        ))
        self.root.update_idletasks()

    def _save_gui_preferences(self) -> None:
        self.gui_preferences.update({
            "window_size": list(self.default_window_size),
            "font_family": self.ui_font_family,
            "font_size_delta": self.ui_font_size_delta,
            "confirm_close_when_idle": self.confirm_close_when_idle,
            "last_task_key": self.task.key,
            "recovery_scans": list(getattr(self, "recovery_scans", ())),
        })
        try:
            save_gui_preferences(self.gui_preferences)
        except OSError as exc:
            messagebox.showwarning(
                "无法保存界面设置",
                f"本次设置已生效，但无法写入：\n{exc}",
                parent=self.root,
            )

    @staticmethod
    def _same_recovery_path(left: str, right: str) -> bool:
        return os.path.normcase(os.path.abspath(left)) == os.path.normcase(
            os.path.abspath(right))

    def _refresh_recovery_card(self) -> None:
        if not hasattr(self, "recovery_card"):
            return
        if not self.recovery_scans:
            self.recovery_card.pack_forget()
            return
        record = self.recovery_scans[-1]
        partial = str(record["partial"])
        task_name = task_display_title(str(record["task_key"]))
        self.recovery_title_label.configure(
            text=f"可恢复 · {task_name}")
        display = self._middle_progress_text(
            os.path.basename(partial), 22)
        self.recovery_path_label.configure(text=display)
        self.recovery_path_tooltip.text = partial
        if not self.recovery_card.winfo_manager():
            self.recovery_card.pack(
                side="right", padx=(0, 8),
                before=self.settings_toggle_button,
            )
        self._set_recovery_card_state()

    def _set_recovery_card_state(self) -> None:
        if not hasattr(self, "recovery_use_button"):
            return
        state = "disabled" if self._task_is_active() else "normal"
        self.recovery_use_button.configure(state=state)
        self.recovery_ignore_button.configure(state=state)

    def _add_recovery_scan(self, task_key: str, partial: str) -> None:
        if task_key not in _SCAN_TASK_KEYS or not partial:
            return
        normalized = os.path.abspath(partial)
        self.recovery_scans = [
            record for record in self.recovery_scans
            if not self._same_recovery_path(
                str(record.get("partial") or ""), normalized)
        ]
        self.recovery_scans.append({
            "task_key": task_key,
            "partial": normalized,
        })
        self.recovery_scans = self.recovery_scans[-20:]
        self._refresh_recovery_card()
        self._save_gui_preferences()

    def _remove_recovery_scan(self, partial: str) -> None:
        before = len(self.recovery_scans)
        self.recovery_scans = [
            record for record in self.recovery_scans
            if not self._same_recovery_path(
                str(record.get("partial") or ""), partial)
        ]
        if len(self.recovery_scans) != before:
            self._refresh_recovery_card()
            self._save_gui_preferences()

    def _prepare_latest_recovery(self) -> None:
        if not self.recovery_scans or self._task_is_active():
            return
        record = self.recovery_scans[-1]
        task_key = str(record["task_key"])
        partial = str(record["partial"])
        if not messagebox.askyesno(
                "准备恢复扫描",
                "这只会切换页面并填入 partial 路径，不会自动开始读取。"
                f"\n\n{partial}\n\n继续吗？",
                icon="question", parent=self.root):
            return
        self.saved_values[task_key] = {
            "start_mode": "resume",
            "resume": partial,
        }
        self._select_task(task_key)
        self._set_settings_expanded(True)
        self._set_status("恢复参数已准备；核对后点击“开始任务”。", _WARNING)

    def _dismiss_latest_recovery(self) -> None:
        if not self.recovery_scans or self._task_is_active():
            return
        partial = str(self.recovery_scans[-1]["partial"])
        if not messagebox.askyesno(
                "忽略恢复提示",
                "只移除 DAISY 的恢复提示，不会删除 partial 文件。"
                f"\n\n{partial}\n\n确定忽略吗？",
                icon="question", parent=self.root):
            return
        self._remove_recovery_scan(partial)

    def _set_ui_font(
        self, *, family: str | None = None,
        size_delta: int | None = None, persist: bool = True,
    ) -> None:
        if family is not None:
            available = {
                value.casefold(): value
                for value in self._available_ui_font_families()
            }
            resolved = available.get(family.casefold())
            if resolved is None:
                return
            self.ui_font_family = resolved
        if size_delta is not None:
            allowed = {delta for _label, delta in _UI_FONT_SIZE_OPTIONS}
            if size_delta not in allowed:
                return
            self.ui_font_size_delta = int(size_delta)
        if hasattr(self, "ui_font_family_var"):
            self.ui_font_family_var.set(self.ui_font_family)
        if hasattr(self, "ui_font_size_var"):
            self.ui_font_size_var.set(self.ui_font_size_delta)
        self._apply_interface_font_preferences()
        if persist:
            self._save_gui_preferences()

    def _set_default_window_size(
        self, size: tuple[int, int], *, persist: bool = True,
    ) -> None:
        self.default_window_size = (int(size[0]), int(size[1]))
        self._preferred_normal_size = self.default_window_size
        if hasattr(self, "default_window_size_var"):
            self.default_window_size_var.set(
                f"{size[0]}x{size[1]}")
        if not self.mini_mode:
            work_area = _monitor_work_area_for_window(self.root)
            width, height, x, y = fit_window_to_work_area(
                self.default_window_size,
                (self.root.winfo_x(), self.root.winfo_y()), work_area,
            )
            self.normal_width_cap = min(1200, width)
            self.normal_min_size = (min(760, width), min(640, height))
            self.root.minsize(*self.normal_min_size)
            self.root.geometry(_window_geometry_string(width, height, x, y))
        if persist:
            self._save_gui_preferences()

    def _set_idle_close_confirmation(
        self, enabled: bool, *, persist: bool = True,
    ) -> None:
        self.confirm_close_when_idle = bool(enabled)
        if hasattr(self, "confirm_close_when_idle_var"):
            self.confirm_close_when_idle_var.set(
                self.confirm_close_when_idle)
        if persist:
            self._save_gui_preferences()

    def _configure_styles(self) -> None:
        self.style = ttk.Style(self.root)
        style = self.style
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("TFrame", background=_SURFACE)
        style.configure(
            "TLabel", background=_SURFACE, foreground=_TEXT,
            font=("Microsoft YaHei UI", 10),
        )
        style.configure(
            "Muted.TLabel", foreground=_MUTED,
            font=("Microsoft YaHei UI", 9),
        )
        style.configure(
            "Badge.TLabel", foreground=_GREEN_DEEP,
            background=_CONTROL,
            font=("Microsoft YaHei UI", 9, "bold"), padding=(9, 4),
        )
        style.configure(
            "TEntry", fieldbackground=_FIELD, foreground=_TEXT,
            bordercolor=_BORDER, lightcolor=_BORDER, darkcolor=_BORDER,
            padding=(7, 4),
        )
        style.configure(
            "TCombobox", fieldbackground=_FIELD, background=_FIELD,
            foreground=_TEXT,
            bordercolor=_BORDER, lightcolor=_BORDER, darkcolor=_BORDER,
            padding=(8, 4), relief="flat",
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", _FIELD)],
            background=[("readonly", _FIELD), ("active", _CONTROL_HOVER)],
            selectbackground=[("readonly", _FIELD)],
            selectforeground=[("readonly", _TEXT)],
        )
        normal_arrow = _create_combobox_chevron(self.root, _MUTED)
        active_arrow = _create_combobox_chevron(self.root, _GREEN_DEEP)
        disabled_arrow = _create_combobox_chevron(self.root, _BORDER)
        self.combobox_arrow_images = (
            normal_arrow, active_arrow, disabled_arrow)
        style.element_create(
            "Daisy.Combobox.chevron", "image", normal_arrow,
            ("active", active_arrow), ("pressed", active_arrow),
            ("disabled", disabled_arrow), sticky="",
        )
        style.layout(
            "Daisy.TCombobox",
            [("Combobox.field", {
                "sticky": "nswe",
                "children": [
                    ("Daisy.Combobox.chevron", {
                        "side": "right", "sticky": "ns"}),
                    ("Combobox.padding", {
                        "sticky": "nswe",
                        "children": [
                            ("Combobox.textarea", {"sticky": "nswe"}),
                        ],
                    }),
                ],
            })],
        )
        style.configure(
            "Daisy.TCombobox", fieldbackground=_FIELD, background=_FIELD,
            foreground=_TEXT, bordercolor=_BORDER,
            lightcolor=_BORDER, darkcolor=_BORDER,
            padding=(8, 4), relief="flat",
        )
        style.map(
            "Daisy.TCombobox",
            fieldbackground=[("readonly", _FIELD)],
            background=[("readonly", _FIELD), ("active", _CONTROL_HOVER)],
            selectbackground=[("readonly", _FIELD)],
            selectforeground=[("readonly", _TEXT)],
        )
        style.configure(
            "Browse.TButton", background=_CONTROL, foreground=_TEXT,
            bordercolor=_BORDER, lightcolor=_BORDER, darkcolor=_BORDER,
            padding=(9, 4), font=("Microsoft YaHei UI", 9),
        )
        style.map(
            "Browse.TButton", background=[("active", _CONTROL_HOVER)])
        style.configure(
            "FormAction.TButton", background=_CONTROL, foreground=_TEXT,
            bordercolor=_BORDER, lightcolor=_BORDER, darkcolor=_BORDER,
            padding=(9, 4), font=("Microsoft YaHei UI", 9),
        )
        style.map(
            "FormAction.TButton",
            background=[("active", _CONTROL_HOVER)],
        )
        style.configure(
            "Remove.TButton", background=_DANGER_SOFT, foreground=_DANGER,
            bordercolor=_DANGER_BORDER, padding=(6, 5),
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        style.map(
            "Remove.TButton",
            background=[("active", _DANGER_HOVER)],
        )
        style.configure(
            "Primary.TButton", background=_ACCENT, foreground="white",
            padding=(18, 8), font=("Microsoft YaHei UI", 10, "bold"),
            borderwidth=1, bordercolor=_ACCENT_DARK,
            lightcolor=_ACCENT, darkcolor=_ACCENT,
        )
        style.map(
            "Primary.TButton",
            background=[("active", _ACCENT_DARK), ("disabled", "#afbeb6")],
            foreground=[("disabled", "#f5f8f6")],
        )
        style.configure(
            "Stop.TButton", background=_AMBER_SOFT,
            foreground=_AMBER_DEEP,
            padding=(15, 8), font=("Microsoft YaHei UI", 10, "bold"),
            borderwidth=0, bordercolor=_AMBER_SOFT,
            lightcolor=_AMBER_SOFT, darkcolor=_AMBER_SOFT,
            relief="flat",
        )
        style.map(
            "Stop.TButton", background=[("active", _AMBER)])
        style.configure(
            "Secondary.TButton", background=_CONTROL, foreground=_TEXT,
            padding=(12, 8), font=("Microsoft YaHei UI", 10),
            borderwidth=1, bordercolor=_BORDER,
            lightcolor=_BORDER, darkcolor=_BORDER,
        )
        style.map(
            "Secondary.TButton", background=[("active", _CONTROL_HOVER)])
        style.configure(
            "Mini.TButton", background=_CONTROL, foreground=_TEXT,
            padding=(9, 4), font=("Microsoft YaHei UI", 8),
            borderwidth=1, bordercolor=_BORDER,
            lightcolor=_BORDER, darkcolor=_BORDER,
        )
        style.map(
            "Mini.TButton", background=[("active", _CONTROL_HOVER)])
        style.configure(
            "PanelHeader.TButton", background=_CONTROL, foreground=_TEXT,
            padding=(9, 4), font=("Microsoft YaHei UI", 8),
            borderwidth=1, bordercolor=_BORDER,
            lightcolor=_BORDER, darkcolor=_BORDER,
        )
        style.map(
            "PanelHeader.TButton",
            background=[("active", _CONTROL_HOVER)],
        )
        top_task_layout = [(
            "Button.border",
            {
                "sticky": "nswe",
                "border": "1",
                "children": [(
                    "Button.padding",
                    {
                        "sticky": "nswe",
                        "children": [(
                            "Button.label", {"sticky": "nswe"},
                        )],
                    },
                )],
            },
        )]
        for _section_label, (
                style_prefix, accent_colour, deep_colour,
                soft_colour) in _TASK_MENU_SECTION_COLOURS.items():
            style.configure(
                f"{style_prefix}.TopTask.TButton",
                background=_SURFACE, foreground=deep_colour,
                padding=_TASK_TOOLBAR_BUTTON_PADDING,
                font=("Microsoft YaHei UI", 9),
                borderwidth=1, bordercolor=_BORDER,
                lightcolor=_BORDER, darkcolor=_BORDER,
                focusthickness=0, focuscolor=_SURFACE,
            )
            style.map(
                f"{style_prefix}.TopTask.TButton",
                background=[
                    ("disabled", _CONTROL),
                    ("pressed", soft_colour),
                    ("active", soft_colour),
                ],
                foreground=[("disabled", deep_colour)],
            )
            style.configure(
                f"{style_prefix}.TopTaskSelected.TButton",
                background=_UNIFIED_ACTION_BACKGROUND,
                foreground=_UNIFIED_ACTION_FOREGROUND,
                padding=_TASK_TOOLBAR_BUTTON_PADDING,
                font=("Microsoft YaHei UI", 9),
                borderwidth=1, bordercolor=_BORDER,
                lightcolor=_BORDER, darkcolor=_BORDER,
                focusthickness=0,
                focuscolor=_UNIFIED_ACTION_BACKGROUND,
            )
            style.map(
                f"{style_prefix}.TopTaskSelected.TButton",
                background=[
                    ("disabled", _UNIFIED_ACTION_BACKGROUND),
                    ("pressed", _UNIFIED_ACTION_BACKGROUND),
                    ("active", _UNIFIED_ACTION_BACKGROUND),
                ],
                foreground=[
                    ("disabled", _UNIFIED_ACTION_FOREGROUND),
                    ("active", _UNIFIED_ACTION_FOREGROUND),
                ],
            )
            style.layout(
                f"{style_prefix}.TopTask.TButton", top_task_layout)
            style.layout(
                f"{style_prefix}.TopTaskSelected.TButton",
                top_task_layout,
            )
        style.configure(
            "MiniStop.TButton", background=_AMBER_SOFT,
            foreground=_AMBER_DEEP,
            padding=(9, 4), font=("Microsoft YaHei UI", 8, "bold"),
            borderwidth=0, bordercolor=_AMBER_SOFT,
            lightcolor=_AMBER_SOFT, darkcolor=_AMBER_SOFT,
            relief="flat",
        )
        style.map(
            "MiniStop.TButton", background=[("active", _AMBER)])
        style.configure(
            "Daisy.Vertical.TScrollbar",
            background=_LOG_HEADER, troughcolor=_CONTROL,
            bordercolor=_BORDER, lightcolor=_LOG_HEADER,
            darkcolor=_LOG_HEADER, arrowcolor=_MUTED,
            relief="flat", width=16, arrowsize=13, gripcount=0,
        )
        style.layout(
            "Daisy.Vertical.TScrollbar",
            [(
                "Vertical.Scrollbar.trough",
                {
                    "sticky": "ns",
                    "children": [(
                        "Vertical.Scrollbar.thumb",
                        {"sticky": "nswe"},
                    )],
                },
            )],
        )
        style.map(
            "Daisy.Vertical.TScrollbar",
            background=[
                ("pressed", _BORDER),
                ("active", _CONTROL_HOVER),
            ],
        )
        for name, colour in (
                ("Queue", _GREEN_DEEP),
                ("Stage", _GREEN_DARK),
                ("Work", _GREEN),
                ("Success", _GREEN_DARK),
                ("Warning", _AMBER_DARK),
                ("Danger", _RED)):
            style.configure(
                f"{name}.Horizontal.TProgressbar",
                troughcolor=_CONTROL,
                background=colour,
                lightcolor=colour,
                darkcolor=colour,
                bordercolor=_BORDER,
                thickness=8,
            )
    def _build_menu(self) -> None:
        """建立不带自定义快捷键的标准应用菜单。"""
        base_menu_options = {
            "tearoff": False,
            "background": _SURFACE,
            "foreground": _TEXT,
            "activebackground": _UNIFIED_ACTION_BACKGROUND,
            "activeforeground": _UNIFIED_ACTION_FOREGROUND,
            "disabledforeground": _MUTED,
            "activeborderwidth": 0,
            "borderwidth": 1,
            "relief": "flat",
            "font": ("Microsoft YaHei UI", 9),
        }
        menu = tk.Menu(
            self.root,
            **{**base_menu_options, "background": _MENU_BACKGROUND},
        )

        file_menu = tk.Menu(menu, **base_menu_options)
        file_menu.add_command(
            label="打开项目目录", command=self._open_project_directory)
        file_menu.add_command(
            label="打开结果目录", command=self._open_output)
        file_menu.add_separator()
        file_menu.add_command(label="退出 DAISY", command=self._on_close)
        menu.add_cascade(label="文件", menu=file_menu)

        self.task_menu_var = tk.StringVar(value=self.task.key)
        self.task_menus: dict[str, tk.Menu] = {}
        panel_menu = tk.Menu(menu, **base_menu_options)
        self.panel_menu = panel_menu
        for section_label, task_keys in _TASK_MENU_SECTIONS:
            task_menu = tk.Menu(
                panel_menu,
                **{
                    **base_menu_options,
                    "activebackground": _UNIFIED_ACTION_BACKGROUND,
                    "activeforeground": _UNIFIED_ACTION_FOREGROUND,
                    "selectcolor": _UNIFIED_ACTION_BACKGROUND,
                },
            )
            self.task_menus[section_label] = task_menu
            for task_key in task_keys:
                task = TASK_BY_KEY[task_key]
                task_menu.add_radiobutton(
                    label=_TASK_TOOLBAR_LABELS[task_key],
                    variable=self.task_menu_var,
                    value=task_key,
                    command=lambda key=task_key: self._select_task(key),
                    indicatoron=True,
                    selectcolor=_UNIFIED_ACTION_BACKGROUND,
                )
                entry_index = task_menu.index("end")
                if entry_index is not None:
                    self.task_menu_entries[task_key] = (
                        task_menu, int(entry_index))
                if (task_key in _TASK_MENU_SEPARATOR_AFTER
                        and task_key != task_keys[-1]):
                    task_menu.add_separator()
            panel_menu.add_cascade(label=section_label, menu=task_menu)
        menu.add_cascade(label="面板", menu=panel_menu)

        advanced_menu = tk.Menu(menu, **base_menu_options)
        self.advanced_menu = advanced_menu
        self.advanced_locked_menu_entries: list[int] = []
        self.command_preview_visible_var = tk.BooleanVar(value=False)
        tool_path_menu = tk.Menu(advanced_menu, **base_menu_options)
        self.tool_path_menu = tool_path_menu
        self.tool_path_menu_entries: dict[str, int] = {}
        for tool_name in _TOOL_PATH_MENU_ORDER:
            tool_path_menu.add_command(
                label=self._tool_path_menu_label(tool_name),
                command=lambda name=tool_name:
                self._select_tool_path(name),
            )
            entry_index = tool_path_menu.index("end")
            if entry_index is not None:
                self.tool_path_menu_entries[tool_name] = int(entry_index)
        tool_path_menu.add_separator()
        tool_path_menu.add_command(
            label="全部恢复自动发现",
            command=self._clear_manual_tool_paths,
        )
        advanced_menu.add_cascade(
            label="工具路径", menu=tool_path_menu)
        tool_path_index = advanced_menu.index("end")
        if tool_path_index is not None:
            self.advanced_locked_menu_entries.append(int(tool_path_index))

        hash_percentage_menu = tk.Menu(
            advanced_menu, **base_menu_options)
        self.hash_percentage_menu = hash_percentage_menu
        self.hash_percentage_menu_entries: dict[tuple[str, str], int] = {}
        for task_key, field_key, _label, _allow_zero in (
                _HASH_PERCENTAGE_MENU_FIELDS):
            hash_percentage_menu.add_command(
                label=self._hash_percentage_menu_label(
                    task_key, field_key),
                command=lambda task=task_key, field=field_key:
                self._edit_hash_percentage(task, field),
            )
            entry_index = hash_percentage_menu.index("end")
            if entry_index is not None:
                self.hash_percentage_menu_entries[(task_key, field_key)] = (
                    int(entry_index))
        hash_percentage_menu.add_separator()
        hash_percentage_menu.add_command(
            label="全部恢复默认比例",
            command=self._reset_hash_percentages,
        )
        advanced_menu.add_cascade(
            label="哈希比例", menu=hash_percentage_menu)
        hash_percentage_index = advanced_menu.index("end")
        if hash_percentage_index is not None:
            self.advanced_locked_menu_entries.append(
                int(hash_percentage_index))

        scan_behavior_menu = tk.Menu(
            advanced_menu, **base_menu_options)
        self.scan_behavior_menu = scan_behavior_menu
        timeout_menu = tk.Menu(
            scan_behavior_menu, **base_menu_options)
        self.scan_timeout_action_var = tk.StringVar(
            value="continue_waiting")
        for label, value in (
                ("继续等待（默认）", "continue_waiting"),
                ("跳过并记录", "skip_and_record"),
                ("停止并保留续传", "stop_and_resume")):
            timeout_menu.add_radiobutton(
                label=label,
                variable=self.scan_timeout_action_var,
                value=value,
                command=lambda selected=value:
                self._set_scan_advanced_value(
                    "timeout_action", selected),
                selectcolor=_UNIFIED_ACTION_BACKGROUND,
            )
        scan_behavior_menu.add_cascade(
            label="哈希超时默认", menu=timeout_menu)
        self.scan_show_current_file_var = tk.BooleanVar(value=False)
        scan_behavior_menu.add_checkbutton(
            label="在进度区显示当前文件",
            variable=self.scan_show_current_file_var,
            command=lambda: self._set_scan_advanced_value(
                "show_current_file",
                bool(self.scan_show_current_file_var.get()),
            ),
            selectcolor=_UNIFIED_ACTION_BACKGROUND,
        )
        format_menu = tk.Menu(
            scan_behavior_menu, **base_menu_options)
        self.scan_format_validation_var = tk.StringVar(value="off")
        for label, value in (
                ("关闭（默认）", "off"),
                ("抽样校验", "sample"),
                ("全部校验", "all")):
            format_menu.add_radiobutton(
                label=label,
                variable=self.scan_format_validation_var,
                value=value,
                command=lambda selected=value:
                self._set_scan_advanced_value(
                    "format_validation", selected),
                selectcolor=_UNIFIED_ACTION_BACKGROUND,
            )
        format_menu.add_separator()
        format_menu.add_command(
            label="设置抽样比例（10.0%）…",
            command=self._edit_scan_format_sample_percent,
        )
        self.scan_format_sample_menu = format_menu
        self.scan_format_sample_menu_index = int(format_menu.index("end"))
        scan_behavior_menu.add_cascade(
            label="Full 格式校验", menu=format_menu)
        advanced_menu.add_cascade(
            label="扫描行为", menu=scan_behavior_menu)
        scan_behavior_index = advanced_menu.index("end")
        if scan_behavior_index is not None:
            self.advanced_locked_menu_entries.append(
                int(scan_behavior_index))
        advanced_menu.add_separator()
        advanced_menu.add_command(
            label="显示命令预览",
            command=lambda: self._set_command_preview_expanded(
                not self.command_preview_expanded),
        )
        self.command_preview_menu_index = int(
            advanced_menu.index("end"))
        advanced_menu.add_separator()
        advanced_menu.add_command(
            label="DAISY功能自检",
            command=lambda: self._select_task(_PROJECT_SELF_TEST_KEY),
        )
        self.database_self_test_menu_index = int(
            advanced_menu.index("end"))
        self.advanced_locked_menu_entries.append(
            self.database_self_test_menu_index)
        menu.add_cascade(label="高级", menu=advanced_menu)

        settings_menu = tk.Menu(menu, **base_menu_options)
        self.settings_menu = settings_menu
        window_size_menu = tk.Menu(settings_menu, **base_menu_options)
        self.default_window_size_var = tk.StringVar(
            value=(f"{self.default_window_size[0]}x"
                   f"{self.default_window_size[1]}"))
        for label, size in _WINDOW_SIZE_OPTIONS:
            token = f"{size[0]}x{size[1]}"
            window_size_menu.add_radiobutton(
                label=label,
                variable=self.default_window_size_var,
                value=token,
                command=lambda selected=size:
                self._set_default_window_size(selected),
            )
        settings_menu.add_cascade(
            label="默认窗口大小", menu=window_size_menu)

        font_menu = tk.Menu(settings_menu, **base_menu_options)
        font_family_menu = tk.Menu(font_menu, **base_menu_options)
        self.ui_font_family_var = tk.StringVar(value=self.ui_font_family)
        for family in self._available_ui_font_families():
            font_family_menu.add_radiobutton(
                label=family,
                variable=self.ui_font_family_var,
                value=family,
                command=lambda selected=family:
                self._set_ui_font(family=selected),
            )
        font_menu.add_cascade(label="字体", menu=font_family_menu)

        font_size_menu = tk.Menu(font_menu, **base_menu_options)
        self.ui_font_size_var = tk.IntVar(value=self.ui_font_size_delta)
        for label, size_delta in _UI_FONT_SIZE_OPTIONS:
            font_size_menu.add_radiobutton(
                label=label + ("（默认）" if size_delta == 0 else ""),
                variable=self.ui_font_size_var,
                value=size_delta,
                command=lambda selected=size_delta:
                self._set_ui_font(size_delta=selected),
            )
        font_menu.add_cascade(label="字号", menu=font_size_menu)
        settings_menu.add_cascade(label="界面字体", menu=font_menu)

        settings_menu.add_separator()
        self.confirm_close_when_idle_var = tk.BooleanVar(
            value=self.confirm_close_when_idle)
        settings_menu.add_checkbutton(
            label="空闲关闭时需要确认",
            variable=self.confirm_close_when_idle_var,
            command=lambda: self._set_idle_close_confirmation(
                self.confirm_close_when_idle_var.get()),
        )
        menu.add_cascade(label="设置", menu=settings_menu)

        self.task_toolbar_visible_var = tk.BooleanVar(value=True)
        self.settings_visible_var = tk.BooleanVar(value=True)
        self.progress_visible_var = tk.BooleanVar(value=False)
        self.log_visible_var = tk.BooleanVar(value=False)
        view_menu = tk.Menu(menu, **base_menu_options)
        self.view_menu = view_menu
        self.view_panel_menu_entries: dict[str, int] = {}
        panel_states = {
            "task_toolbar": getattr(self, "task_toolbar_expanded", True),
            "settings": getattr(self, "settings_expanded", True),
            "progress": getattr(self, "progress_expanded", False),
            "log": getattr(self, "log_expanded", False),
        }
        for panel_key, panel_label, command in (
            ("task_toolbar", "功能模块", self._toggle_task_toolbar),
            ("settings", "任务设置", self._toggle_settings_panel),
            ("progress", "运行进度", self._toggle_progress_panel),
            ("log", "运行日志", self._toggle_log_panel),
        ):
            view_menu.add_command(
                label=(
                    ("折叠" if panel_states[panel_key] else "展开")
                    + panel_label
                ),
                command=command,
            )
            entry_index = view_menu.index("end")
            if entry_index is not None:
                self.view_panel_menu_entries[panel_key] = int(entry_index)
            if panel_key == "task_toolbar":
                view_menu.add_separator()
        view_menu.add_separator()
        view_menu.add_command(
            label="进入小窗模式", command=self._toggle_mini_mode)
        self.view_mini_mode_menu_index = int(view_menu.index("end"))
        menu.add_cascade(label="视图", menu=view_menu)

        help_menu = tk.Menu(menu, **base_menu_options)
        help_menu.add_command(label="联系作者", command=self._show_author_contact)
        help_menu.add_command(label="关于 DAISY", command=self._show_about)
        help_menu.add_separator()
        help_menu.add_command(
            label="打开 GitHub 主页", command=self._open_github)
        menu.add_cascade(label="帮助", menu=help_menu)

        self.app_menu = menu
        self.root.configure(menu=menu)

    def _build_task_toolbar(self) -> None:
        """建立 ENV、DBS、STG 各占一行的可折叠功能模块区。"""
        panel = tk.Frame(
            self.root, bg=_SURFACE,
            highlightbackground=_BORDER, highlightthickness=1,
        )
        self.task_toolbar_panel = panel
        panel.pack(fill="x", side="top")

        header = tk.Frame(panel, bg=_SURFACE)
        self.task_toolbar_horizontal_pad = (
            self.content_pad + _PANEL_HEADER_PADX)
        header.pack(
            fill="x", padx=self.task_toolbar_horizontal_pad,
            pady=(4, 2),
        )
        tk.Label(
            header, text="功能模块", bg=_SURFACE,
            fg=_TASK_TOOLBAR_LABEL_COLOUR,
            font=("Microsoft YaHei UI", 9, "bold"),
        ).pack(side="left")
        self.task_toolbar_toggle_button = ttk.Button(
            header, text="收起模块", style="Mini.TButton",
            command=self._toggle_task_toolbar,
        )
        self.task_toolbar_toggle_button.pack(side="right")
        attach_tooltip(
            self.task_toolbar_toggle_button,
            "展开或收起顶部功能模块；当前页面和已填写内容保持不变。",
        )
        self.admin_mode_switch = AdminModeSwitch(
            header,
            value=self.is_administrator,
            enabled=(os.name == "nt" and not self.is_administrator),
            command=self._request_admin_mode,
        )
        self.admin_mode_switch.pack(side="right", padx=(0, 12))
        admin_tooltip = (
            "当前已使用管理员权限。GUI 中目前仅「硬盘信息登记」需要此模式；"
            "其「检测物理硬盘」准备步骤也使用该权限。如需恢复普通模式，"
            "请关闭后正常启动。"
            if self.is_administrator else
            "GUI 中目前仅「硬盘信息登记」需要管理员模式，其「检测物理硬盘」"
            "准备步骤也使用该权限。开启后将确认并通过 Windows UAC 重新启动；"
            "任务运行期间不可切换。"
        )
        for widget in self.admin_mode_switch.tooltip_widgets:
            attach_tooltip(widget, admin_tooltip)

        body = tk.Frame(panel, bg=_SURFACE)
        self.task_toolbar_body = body
        body.pack(
            fill="x", padx=self.task_toolbar_horizontal_pad,
            pady=(0, 5),
        )
        self.task_toolbar_section_labels: dict[str, tk.Label] = {}
        for section_label, short_label, _task_keys in _TASK_TOOLBAR_ROWS:
            self.task_toolbar_section_labels[section_label] = tk.Label(
                body, text=short_label, bg=_SURFACE,
                fg=_TASK_TOOLBAR_LABEL_COLOUR,
                font=("Microsoft YaHei UI", 9, "bold"),
                width=8, anchor="w",
            )
        for task_key in _TASK_MENU_ORDER:
            task = TASK_BY_KEY[task_key]
            button = ttk.Button(
                body, text=_TASK_TOOLBAR_LABELS[task_key],
                style=f"{_TASK_TOOLBAR_STYLE_PREFIX}.TopTask.TButton",
                width=_TASK_TOOLBAR_BUTTON_WIDTH,
                takefocus=False,
                command=lambda key=task_key:
                self._select_task_from_toolbar(key),
            )
            self.task_toolbar_buttons[task_key] = button
            tooltip = (
                f"{task.nav}：切换到「{task_display_title(task_key)}」页面；"
                "运行时功能模块会暂时锁定。"
            )
            if task_key in _STG_ADMIN_TASKS:
                tooltip += (
                    "此功能需要管理员权限才能完整运行；未提权时请开启顶部"
                    "管理员模式开关，并按提示重新启动 DAISY。"
                )
            attach_tooltip(
                button, tooltip)
        self.root.after_idle(self._layout_task_toolbar)

    def _build_shell(self) -> None:
        content_pad = 12 if self.compact_layout else 14
        self.content_pad = content_pad

        colour_strip = tk.Frame(
            self.root, bg=_BG, height=_COLOUR_STRIP_HEIGHT)
        self.colour_strip = colour_strip
        colour_strip.pack(fill="x", side="top")
        colour_strip.pack_propagate(False)
        for colour in (_GREEN, _AMBER, _RED):
            tk.Frame(colour_strip, bg=colour).pack(
                side="left", fill="both", expand=True)

        self._build_task_toolbar()

        body = tk.Frame(self.root, bg=_BG)
        self.body = body
        body.pack(fill="both", expand=True)

        content = tk.Frame(body, bg=_BG)
        self.content = content
        content.pack(
            fill="both", expand=True,
            padx=content_pad, pady=content_pad,
        )
        content.grid_columnconfigure(0, weight=1)
        content.grid_rowconfigure(0, weight=1)
        content.grid_rowconfigure(2, weight=0)

        self.task_card = tk.Frame(
            content, bg=_SURFACE, highlightbackground=_BORDER,
            highlightthickness=1,
        )
        self.task_card.grid(row=0, column=0, sticky="nsew")

        title_row = tk.Frame(self.task_card, bg=_SURFACE)
        self.settings_title_row = title_row
        title_row.pack(fill="x", padx=22, pady=(10, 6))
        self.settings_title_expanded_font = (
            "Microsoft YaHei UI",
            14 if self.compact_layout else 16,
            "bold",
        )
        self.title_label = tk.Label(
            title_row, bg=_SURFACE, fg=_TEXT,
            font=self.settings_title_expanded_font, anchor="w",
        )
        self.title_label.pack(side="left")
        self.settings_toggle_button = ttk.Button(
            title_row, text="收起设置", style="Mini.TButton",
            command=self._toggle_settings_panel,
        )
        self.settings_toggle_button.pack(side="right")
        attach_tooltip(
            self.settings_toggle_button,
            "展开或收起当前任务的说明与设置；已填写内容不会丢失。",
        )

        self.recovery_card = tk.Frame(
            title_row, bg=_AMBER_SOFT,
            highlightbackground=_AMBER, highlightthickness=1,
        )
        self.recovery_title_label = tk.Label(
            self.recovery_card, text="可恢复扫描", bg=_AMBER_SOFT,
            fg=_AMBER_DEEP, font=("Microsoft YaHei UI", 8, "bold"),
            anchor="w",
        )
        self.recovery_title_label.pack(side="left", padx=(8, 4), pady=4)
        self.recovery_path_label = tk.Label(
            self.recovery_card, bg=_AMBER_SOFT, fg=_TEXT,
            font=("Microsoft YaHei UI", 8), anchor="w",
        )
        self.recovery_path_label.pack(side="left", padx=(0, 6), pady=4)
        self.recovery_path_tooltip = attach_tooltip(
            self.recovery_path_label, "")
        self.recovery_ignore_button = ttk.Button(
            self.recovery_card, text="忽略", style="PanelHeader.TButton",
            command=self._dismiss_latest_recovery,
        )
        self.recovery_ignore_button.pack(
            side="right", padx=(0, 4), pady=3)
        self.recovery_use_button = ttk.Button(
            self.recovery_card, text="恢复",
            style="PanelHeader.TButton",
            command=self._prepare_latest_recovery,
        )
        self.recovery_use_button.pack(side="right", padx=(0, 4), pady=3)

        self.settings_body = tk.Frame(self.task_card, bg=_SURFACE)
        self.settings_body.pack(fill="both", expand=True)
        self.desc_label = tk.Label(
            self.settings_body, bg=_SURFACE, fg=_GREEN_DEEP,
            font=("Microsoft YaHei UI", 9), anchor="w", justify="left",
            wraplength=820,
        )
        self.desc_label.pack(fill="x", padx=22, pady=(0, 5))
        self.task_card.bind(
            "<Configure>",
            lambda e: self.desc_label.configure(
                wraplength=max(420, e.width - 44)),
        )

        separator = tk.Frame(self.settings_body, bg=_BORDER, height=1)
        separator.pack(fill="x")

        form_host = tk.Frame(self.settings_body, bg=_SURFACE)
        form_host.pack(fill="both", expand=True)
        self.form_canvas = tk.Canvas(
            form_host, bg=_SURFACE, highlightthickness=0, bd=0, height=80,
        )
        self.form_scroll = ttk.Scrollbar(
            form_host, orient="vertical", command=self.form_canvas.yview,
            style="Daisy.Vertical.TScrollbar",
        )
        self.form_canvas.configure(yscrollcommand=self.form_scroll.set)
        self.form_canvas.pack(side="left", fill="both", expand=True)
        self.form_inner = tk.Frame(self.form_canvas, bg=_SURFACE)
        self.form_window = self.form_canvas.create_window(
            (0, 0), window=self.form_inner, anchor="nw",
        )
        self.form_inner.bind(
            "<Configure>", self._schedule_form_scroll_sync,
        )
        self.form_canvas.bind(
            "<Configure>", self._resize_form_canvas_window,
        )
        self.form_canvas.bind(
            "<Enter>", lambda _e: self.form_canvas.bind_all(
                "<MouseWheel>", self._scroll_form),
        )
        self.form_canvas.bind(
            "<Leave>", lambda _e: self.form_canvas.unbind_all("<MouseWheel>"),
        )

        progress_panel = tk.Frame(
            content, bg=_SURFACE, highlightbackground=_BORDER,
            highlightthickness=1,
        )
        self.progress_panel = progress_panel
        progress_panel.grid(
            row=1, column=0, sticky="ew", pady=(10, 0))
        progress_inner = tk.Frame(progress_panel, bg=_SURFACE)
        self.progress_inner = progress_inner
        progress_inner.pack(
            fill="x", padx=_PANEL_HEADER_PADX,
            pady=8 if self.compact_layout else 10,
        )

        progress_header = tk.Frame(progress_inner, bg=_SURFACE)
        progress_header.pack(fill="x")
        tk.Label(
            progress_header, text="运行进度", bg=_SURFACE, fg=_TEXT,
            font=("Microsoft YaHei UI", 9, "bold"), anchor="w",
        ).pack(side="left")
        progress_actions = tk.Frame(progress_header, bg=_SURFACE)
        progress_actions.pack(side="right")
        progress_actions.grid_columnconfigure(
            0, weight=1, uniform="panel_header_action")
        progress_actions.grid_columnconfigure(
            1, minsize=_PANEL_ACTION_BUTTON_GAP)
        progress_actions.grid_columnconfigure(
            2, weight=1, uniform="panel_header_action")
        self.mini_mode_button = ttk.Button(
            progress_actions, text="小窗运行", style="PanelHeader.TButton",
            width=_PANEL_ACTION_BUTTON_WIDTH,
            command=self._toggle_mini_mode, state="normal",
        )
        self.mini_mode_button.grid(row=0, column=0, sticky="ew")
        attach_tooltip(
            self.mini_mode_button,
            "随时切换小窗；只保留进度、停止与返回控制。",
        )
        self.progress_toggle_button = ttk.Button(
            progress_actions, text="收起进度", style="PanelHeader.TButton",
            width=_PANEL_ACTION_BUTTON_WIDTH,
            command=self._toggle_progress_panel,
        )
        self.progress_toggle_button.grid(row=0, column=2, sticky="ew")
        attach_tooltip(
            self.progress_toggle_button,
            "展开或收起队列、任务阶段与本阶段进度。",
        )
        self.mini_stop_button = ttk.Button(
            progress_header, text="停止", style="MiniStop.TButton",
            command=self._stop, state="disabled",
        )
        attach_tooltip(
            self.mini_stop_button,
            "请求停止当前任务；多项队列中尚未开始的项目也会取消。",
        )
        self.mini_save_button = ttk.Button(
            progress_header, text="保存退出", style="Mini.TButton",
            command=self._save_scan_progress, state="disabled",
        )
        attach_tooltip(
            self.mini_save_button,
            "安全保存已完成进度并结束当前扫描；下次启动会显示恢复入口。",
        )
        self.mini_pause_button = ttk.Button(
            progress_header, text="暂停", style="Mini.TButton",
            command=self._pause_or_continue_scan, state="disabled",
        )
        attach_tooltip(
            self.mini_pause_button,
            "在安全边界暂停当前扫描；暂停期间任务进程和锁仍保留。",
        )

        progress_body = tk.Frame(progress_inner, bg=_SURFACE)
        self.progress_body = progress_body
        progress_body.pack(fill="x", pady=(6, 0))
        progress_body.grid_columnconfigure(1, weight=1)

        tk.Label(
            progress_body, text="当前目标", bg=_SURFACE, fg=_GREEN_DARK,
            font=("Microsoft YaHei UI", 8, "bold"), anchor="nw",
        ).grid(row=0, column=0, sticky="nw", padx=(0, 10), pady=(0, 7))
        self.progress_target_label = tk.Label(
            progress_body, text="尚未选择", bg=_SURFACE, fg=_MUTED,
            font=("Microsoft YaHei UI", 8), anchor="w", justify="left",
            wraplength=760,
        )
        self.progress_target_label.grid(
            row=0, column=1, columnspan=2, sticky="ew", pady=(0, 7))
        progress_body.bind(
            "<Configure>",
            lambda event: self.progress_target_label.configure(
                wraplength=max(260, event.width - 90)),
        )

        self.current_file_title_label = tk.Label(
            progress_body, text="当前文件", bg=_SURFACE, fg=_GREEN_DARK,
            font=("Microsoft YaHei UI", 8, "bold"), anchor="w",
        )
        self.current_file_label = tk.Label(
            progress_body, text="", bg=_SURFACE, fg=_TEXT,
            font=("Microsoft YaHei UI", 8), anchor="w",
        )
        self.current_file_tooltip = attach_tooltip(
            self.current_file_label, "")

        self.queue_title_label = tk.Label(
            progress_body, text="任务队列", bg=_SURFACE, fg=_GREEN_DEEP,
            font=("Microsoft YaHei UI", 8, "bold"), anchor="w",
        )
        self.queue_title_label.grid(
            row=2, column=0, sticky="w", padx=(0, 10))
        self.queue_detail_label = tk.Label(
            progress_body, text="等待队列", bg=_SURFACE, fg=_MUTED,
            font=("Microsoft YaHei UI", 8), anchor="w",
        )
        self.queue_detail_label.grid(row=2, column=1, sticky="ew")
        self.queue_percent_label = tk.Label(
            progress_body, text="0%", bg=_SURFACE, fg=_GREEN_DEEP,
            font=("Microsoft YaHei UI", 8, "bold"), anchor="e",
        )
        self.queue_percent_label.grid(row=2, column=2, sticky="e")
        self.queue_progress_bar = ttk.Progressbar(
            progress_body, mode="determinate", maximum=100, value=0,
            style="Queue.Horizontal.TProgressbar",
        )
        self.queue_progress_bar.grid(
            row=3, column=0, columnspan=3, sticky="ew", pady=(4, 7))

        tk.Label(
            progress_body, text="任务阶段", bg=_SURFACE, fg=_GREEN_DARK,
            font=("Microsoft YaHei UI", 8, "bold"), anchor="w",
        ).grid(row=4, column=0, sticky="w", padx=(0, 10))
        self.progress_stage_label = tk.Label(
            progress_body, text="等待开始", bg=_SURFACE, fg=_MUTED,
            font=("Microsoft YaHei UI", 8), anchor="w",
        )
        self.progress_stage_label.grid(
            row=4, column=1, columnspan=2, sticky="ew")
        self.progress_stage_bar = ttk.Progressbar(
            progress_body, mode="determinate", maximum=100, value=0,
            style="Stage.Horizontal.TProgressbar",
        )
        self.progress_stage_bar.grid(
            row=5, column=0, columnspan=3, sticky="ew", pady=(4, 6))

        tk.Label(
            progress_body, text="本阶段", bg=_SURFACE, fg=_GREEN_DARK,
            font=("Microsoft YaHei UI", 8, "bold"), anchor="w",
        ).grid(row=6, column=0, sticky="w", padx=(0, 10))
        self.progress_detail_label = tk.Label(
            progress_body, text="尚未运行", bg=_SURFACE, fg=_MUTED,
            font=("Microsoft YaHei UI", 8), anchor="w",
        )
        self.progress_detail_label.grid(row=6, column=1, sticky="ew")
        self.progress_percent_label = tk.Label(
            progress_body, text="0%", bg=_SURFACE, fg=_GREEN_DARK,
            font=("Microsoft YaHei UI", 8, "bold"), anchor="e",
        )
        self.progress_percent_label.grid(row=6, column=2, sticky="e")
        self.progress_work_bar = ttk.Progressbar(
            progress_body, mode="determinate", maximum=100, value=0,
            style="Work.Horizontal.TProgressbar",
        )
        self.progress_work_bar.grid(
            row=7, column=0, columnspan=3, sticky="ew", pady=(4, 5))

        log_panel = tk.Frame(
            content, bg=_LOG_BG, highlightbackground=_BORDER,
            highlightthickness=1,
        )
        self.log_panel = log_panel
        log_panel.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        log_header = tk.Frame(log_panel, bg=_LOG_HEADER)
        log_header.pack(fill="x")
        tk.Label(
            log_header, text="运行日志", bg=_LOG_HEADER, fg=_TEXT,
            font=("Microsoft YaHei UI", 9, "bold"),
        ).pack(side="left", padx=14, pady=8)
        log_actions = tk.Frame(log_header, bg=_LOG_HEADER)
        log_actions.pack(
            side="right", padx=_PANEL_HEADER_PADX, pady=5)
        log_actions.grid_columnconfigure(
            0, weight=1, uniform="panel_header_action")
        log_actions.grid_columnconfigure(
            1, minsize=_PANEL_ACTION_BUTTON_GAP)
        log_actions.grid_columnconfigure(
            2, weight=1, uniform="panel_header_action")
        log_actions.grid_columnconfigure(
            3, minsize=_PANEL_ACTION_BUTTON_GAP)
        log_actions.grid_columnconfigure(
            4, weight=1, uniform="panel_header_action")
        self.clear_log_button = ttk.Button(
            log_actions, text="清空日志", style="PanelHeader.TButton",
            width=_PANEL_ACTION_BUTTON_WIDTH,
            command=self._clear_log,
        )
        self.clear_log_button.grid(row=0, column=0, sticky="ew")
        attach_tooltip(
            self.clear_log_button,
            "清空主界面与独立窗口中的运行日志，不影响任务或正式产物。",
        )
        self.open_log_window_button = ttk.Button(
            log_actions, text="独立窗口", style="PanelHeader.TButton",
            width=_PANEL_ACTION_BUTTON_WIDTH,
            command=self._open_log_window,
        )
        self.open_log_window_button.grid(row=0, column=2, sticky="ew")
        attach_tooltip(
            self.open_log_window_button,
            "在独立窗口中打开并实时同步运行日志。",
        )
        self.log_toggle_button = ttk.Button(
            log_actions, text="收起日志", style="PanelHeader.TButton",
            width=_PANEL_ACTION_BUTTON_WIDTH,
            command=self._toggle_log_panel,
        )
        self.log_toggle_button.grid(row=0, column=4, sticky="ew")
        attach_tooltip(
            self.log_toggle_button,
            "展开或收起运行日志；已有日志内容不会被清除。",
        )
        log_body = tk.Frame(
            log_panel, bg=_LOG_BG,
            height=100 if self.compact_layout else 120,
        )
        self.log_body = log_body
        log_body.pack(fill="both", expand=True)
        log_body.pack_propagate(False)
        self.log = tk.Text(
            log_body, bg=_LOG_BG, fg=_LOG_TEXT, insertbackground=_TEXT,
            selectbackground=_LOG_SELECT, relief="flat", bd=0,
            font=("Microsoft YaHei UI", 9), wrap="word", padx=13, pady=10,
            state="disabled",
        )
        log_scroll = ttk.Scrollbar(
            log_body, orient="vertical", command=self.log.yview,
            style="Daisy.Vertical.TScrollbar",
        )
        self.log.configure(yscrollcommand=log_scroll.set)
        log_scroll.pack(side="right", fill="y")
        self.log.pack(fill="both", expand=True)
        self._configure_log_tags(self.log)

        command_panel = tk.Frame(content, bg=_BG)
        self.command_panel = command_panel
        command_panel.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        command_preview_body = tk.Frame(command_panel, bg=_BG)
        self.command_preview_body = command_preview_body
        tk.Label(
            command_preview_body, text="命令预览", bg=_BG, fg=_MUTED,
            font=("Microsoft YaHei UI", 9),
        ).pack(anchor="w")
        preview_row = tk.Frame(command_preview_body, bg=_BG)
        preview_row.pack(fill="x", pady=(5, 9))
        self.preview_var = tk.StringVar()
        preview_entry = ttk.Entry(
            preview_row, textvariable=self.preview_var, state="readonly",
        )
        preview_entry.pack(side="left", fill="x", expand=True)
        self.copy_button = ttk.Button(
            preview_row, text="复制", style="FormAction.TButton",
            width=_FORM_ACTION_BUTTON_WIDTH,
            command=self._copy_command,
        )
        self.copy_button.pack(side="left", padx=(8, 0))
        attach_tooltip(
            self.copy_button,
            "把当前页面生成的命令预览复制到剪贴板，不会执行命令。",
        )

        actions = tk.Frame(command_panel, bg=_BG)
        self.command_actions = actions
        actions.pack(fill="x")
        status_area = tk.Frame(actions, bg=_BG)
        status_area.pack(fill="x")
        self.status_label = tk.Label(
            status_area, text="就绪", bg=_GREEN_DARK, fg="white",
            font=("Microsoft YaHei UI", 9, "bold"), anchor="w",
            padx=10, pady=5,
        )
        self.status_label.pack(side="left", anchor="center")

        action_button_area = tk.Frame(actions, bg=_BG)
        self.action_button_area = action_button_area
        action_button_area.pack(fill="x", pady=(7, 0))

        utility_action_area = tk.Frame(action_button_area, bg=_BG)
        self.utility_action_area = utility_action_area
        utility_action_area.pack(fill="x")
        self.open_output_button = ttk.Button(
            utility_action_area, text="打开结果目录",
            style="Secondary.TButton",
            command=self._open_output,
        )
        self.clear_cache_button = ttk.Button(
            utility_action_area, text="清理缓存", style="Secondary.TButton",
            command=self._clear_tool_cache, state="disabled",
        )
        self.utility_buttons = (
            self.open_output_button,
            self.clear_cache_button,
        )

        tk.Frame(action_button_area, bg=_BORDER, height=1).pack(
            fill="x", pady=(8, 7))
        execution_action_area = tk.Frame(action_button_area, bg=_BG)
        self.execution_action_area = execution_action_area
        execution_action_area.pack(fill="x")
        execution_action_area.grid_columnconfigure(0, weight=1)
        self.stop_button = ttk.Button(
            execution_action_area, text="停止", style="Stop.TButton",
            command=self._stop, state="disabled",
        )
        self.save_scan_button = ttk.Button(
            execution_action_area, text="保存并退出",
            style="Secondary.TButton",
            command=self._save_scan_progress, state="disabled",
        )
        self.pause_scan_button = ttk.Button(
            execution_action_area, text="暂停", style="Secondary.TButton",
            command=self._pause_or_continue_scan, state="disabled",
        )
        self.run_button = ttk.Button(
            execution_action_area, text=_RUN_BUTTON_TEXT,
            style="Primary.TButton",
            command=self._run,
        )
        self.execution_buttons = (
            self.pause_scan_button, self.save_scan_button,
            self.stop_button, self.run_button,
        )
        for button, tooltip in (
            (self.run_button,
             "校验当前页面后开始执行对应任务。"),
            (self.stop_button,
             "请求停止当前任务；多项队列中尚未开始的项目也会取消。"),
            (self.pause_scan_button,
             "在安全边界暂停扫描；暂停后可继续、保存退出或停止。"),
            (self.save_scan_button,
             "安全保存已完成进度并结束扫描；下次启动主动显示恢复入口。"),
            (self.clear_cache_button,
             "清除可重建缓存，并把参数、队列、日志和进度恢复为首次启动状态；"
             "不触碰正式产物。"),
            (self.clear_log_button,
             "清空当前窗口的运行日志。"),
            (self.open_output_button,
             "在资源管理器中打开当前任务对应的结果目录。"),
        ):
            attach_tooltip(button, tooltip)
        action_button_area.bind("<Configure>", self._layout_action_buttons)
        self.root.after_idle(self._layout_action_buttons)

    def _layout_task_toolbar(
        self, _event: tk.Event | None = None,
    ) -> None:
        """固定三类行与等宽六字功能块，不随窗口宽度重新排布。"""
        if getattr(self, "_task_toolbar_layout_ready", False):
            return
        for label in self.task_toolbar_section_labels.values():
            label.grid_forget()
        for button in self.task_toolbar_buttons.values():
            button.grid_forget()
        for row_index, (section_label, _short_label, task_keys) in (
                enumerate(_TASK_TOOLBAR_ROWS)):
            self.task_toolbar_section_labels[section_label].grid(
                row=row_index, column=0, sticky="w",
                padx=(0, 12), pady=(2 if row_index else 0, 0),
            )
            column = 1
            for task_key in task_keys:
                self.task_toolbar_buttons[task_key].grid(
                    row=row_index, column=column, sticky="w",
                    padx=(0, 6), pady=(2 if row_index else 0, 0),
                )
                column += 1
        self._task_toolbar_layout_ready = True
        self._sync_task_toolbar_minimum_width()

    def _sync_task_toolbar_minimum_width(self) -> None:
        """优先容纳功能模块，但允许窗口缩入 720p 工作区。"""
        self.root.update_idletasks()
        toolbar_width = self.task_toolbar_panel.winfo_reqwidth()
        base_width, base_height = self.normal_min_size
        width_cap = int(getattr(self, "normal_width_cap", 1200))
        self.normal_min_size = (
            min(max(base_width, toolbar_width), width_cap),
            base_height,
        )
        if not self.mini_mode:
            self.root.minsize(*self._normal_minimum_size())

    def _refresh_view_menu_labels(self) -> None:
        """让可折叠面板菜单显示下一步会执行的动作。"""
        if not hasattr(self, "view_panel_menu_entries"):
            return
        states = {
            "task_toolbar": (self.task_toolbar_expanded, "功能模块"),
            "settings": (self.settings_expanded, "任务设置"),
            "progress": (self.progress_expanded, "运行进度"),
            "log": (self.log_expanded, "运行日志"),
        }
        for panel_key, entry_index in self.view_panel_menu_entries.items():
            expanded, label = states[panel_key]
            self.view_menu.entryconfigure(
                entry_index,
                label=("折叠" if expanded else "展开") + label,
            )
        if hasattr(self, "view_mini_mode_menu_index"):
            self.view_menu.entryconfigure(
                self.view_mini_mode_menu_index,
                label=(
                    "返回完整界面" if self.mini_mode else "进入小窗模式"
                ),
            )

    def _set_task_toolbar_expanded(self, expanded: bool) -> None:
        self.task_toolbar_expanded = expanded
        if expanded:
            if not self.task_toolbar_body.winfo_manager():
                self.task_toolbar_body.pack(
                    fill="x", padx=self.task_toolbar_horizontal_pad,
                    pady=(0, 8))
                self.root.after_idle(self._layout_task_toolbar)
        else:
            self.task_toolbar_body.pack_forget()
        self.task_toolbar_toggle_button.configure(
            text="收起模块" if expanded else "展开模块")
        if hasattr(self, "task_toolbar_visible_var"):
            self.task_toolbar_visible_var.set(expanded)
        self._refresh_view_menu_labels()

    def _toggle_task_toolbar(self) -> None:
        self._set_task_toolbar_expanded(not self.task_toolbar_expanded)

    def _normal_minimum_size(self) -> tuple[int, int]:
        return self.normal_min_size

    def _layout_action_buttons(
        self, event: tk.Event | None = None,
    ) -> None:
        """辅助操作可换行，开始与停止固定保留在独立任务控制行。"""
        for button in self.utility_buttons:
            button.grid_forget()
        for button in self.execution_buttons:
            button.grid_forget()
        visible_utilities = list(self.utility_buttons)

        width = (
            int(event.width) if event is not None
            else self.utility_action_area.winfo_width()
        )
        if width <= 1:
            width = self.content.winfo_width()
        available = max(180, width)
        widths = tuple(
            button.winfo_reqwidth() for button in visible_utilities)
        rows = action_button_row_indexes(widths, available)
        for row_index, indexes in enumerate(rows):
            buttons = [visible_utilities[index] for index in indexes]
            for column, button in enumerate(buttons):
                button.grid(
                    row=row_index, column=column, sticky="w",
                    padx=(0, 8 if column < len(buttons) - 1 else 0),
                    pady=(5 if row_index else 0, 0),
                )
        controls = [self.stop_button, self.run_button]
        task = getattr(self, "task", None)
        if (getattr(task, "key", None) in _SCAN_TASK_KEYS
                or getattr(self, "process_task_key", None)
                in _SCAN_TASK_KEYS):
            controls = [
                self.pause_scan_button, self.save_scan_button,
                self.stop_button, self.run_button,
            ]
        for column, button in enumerate(controls, start=1):
            button.grid(
                row=0, column=column, sticky="e",
                padx=(0, 8 if column < len(controls) else 0),
            )

    def _refresh_content_row_weights(self) -> None:
        """让设置区或日志区占满固定布局中的剩余纵向空间。"""
        if self.mini_mode:
            return
        settings_weight = 1 if self.settings_expanded else 0
        log_weight = 1 if self.log_expanded and not settings_weight else 0
        self.content.grid_rowconfigure(0, weight=settings_weight)
        self.content.grid_rowconfigure(2, weight=log_weight)
        self.log_panel.grid_configure(
            sticky="nsew" if log_weight else "ew")

    def _set_settings_expanded(self, expanded: bool) -> None:
        self.settings_expanded = expanded
        if expanded:
            if not self.settings_body.winfo_manager():
                self.settings_body.pack(fill="both", expand=True)
        else:
            self.settings_body.pack_forget()
        self.title_label.configure(font=(
            self.settings_title_expanded_font
            if expanded else self._font_tuple(9, "bold")
        ))
        self.settings_title_row.pack_configure(
            padx=(22 if expanded else _PANEL_HEADER_PADX),
            pady=((10, 6) if expanded
                  else _COLLAPSED_SETTINGS_HEADER_PADY),
        )
        self.settings_toggle_button.configure(
            text="收起设置" if expanded else "展开设置")
        if hasattr(self, "settings_visible_var"):
            self.settings_visible_var.set(expanded)
        self._refresh_view_menu_labels()
        self._refresh_content_row_weights()

    def _toggle_settings_panel(self) -> None:
        self._set_settings_expanded(not self.settings_expanded)

    def _set_progress_expanded(self, expanded: bool) -> None:
        self.progress_expanded = expanded
        if expanded:
            if not self.progress_body.winfo_manager():
                self.progress_body.pack(fill="x", pady=(6, 0))
        else:
            self.progress_body.pack_forget()
        self.progress_toggle_button.configure(
            text="收起进度" if expanded else "展开进度")
        if hasattr(self, "progress_visible_var"):
            self.progress_visible_var.set(expanded)
        self._refresh_view_menu_labels()

    def _toggle_progress_panel(self) -> None:
        self._set_progress_expanded(not self.progress_expanded)

    def _set_log_expanded(self, expanded: bool) -> None:
        self.log_expanded = expanded
        if expanded:
            if not self.log_body.winfo_manager():
                self.log_body.pack(fill="both", expand=True)
        else:
            self.log_body.pack_forget()
        self.log_toggle_button.configure(
            text="收起日志" if expanded else "展开日志")
        if hasattr(self, "log_visible_var"):
            self.log_visible_var.set(expanded)
        self._refresh_view_menu_labels()
        self._refresh_content_row_weights()

    def _toggle_log_panel(self) -> None:
        self._set_log_expanded(not self.log_expanded)

    def _set_command_preview_expanded(self, expanded: bool) -> None:
        self.command_preview_expanded = expanded
        if expanded:
            if not self.command_preview_body.winfo_manager():
                self.command_preview_body.pack(
                    fill="x", before=self.command_actions)
        else:
            self.command_preview_body.pack_forget()
        if hasattr(self, "command_preview_visible_var"):
            self.command_preview_visible_var.set(expanded)
        if hasattr(self, "advanced_menu") and hasattr(
                self, "command_preview_menu_index"):
            self.advanced_menu.entryconfigure(
                self.command_preview_menu_index,
                label="隐藏命令预览" if expanded else "显示命令预览",
            )
        if not self.mini_mode:
            self.root.minsize(*self._normal_minimum_size())

    def _task_is_active(self) -> bool:
        return bool(self.process is not None or self.worker_starting
                    or self.run_jobs)

    def _refresh_mini_action(self) -> None:
        self.mini_mode_button.configure(
            text="返回完整界面" if self.mini_mode else "小窗运行",
            state="normal",
        )
        self._refresh_view_menu_labels()

    def _set_stop_state(self, state: str) -> None:
        self.stop_button.configure(state=state)
        self.mini_stop_button.configure(state=state)

    def _refresh_scan_controls(self) -> None:
        """按统一扫描状态同步主窗口与小窗控制按钮。"""
        if not hasattr(self, "pause_scan_button"):
            return
        scan_active = (
            getattr(self, "process_task_key", None) in _SCAN_TASK_KEYS
            and getattr(self, "process", None) is not None
        )
        state = self.scan_control_state
        pause_text = "继续" if state == "paused" else "暂停"
        pause_state = (
            "normal" if scan_active and state in ("running", "paused")
            else "disabled"
        )
        save_state = (
            "normal" if scan_active and state in ("running", "paused")
            else "disabled"
        )
        stop_state = (
            "normal" if scan_active and state in ("running", "paused")
            else "disabled"
        )
        for button in (self.pause_scan_button, self.mini_pause_button):
            button.configure(text=pause_text, state=pause_state)
        for button in (self.save_scan_button, self.mini_save_button):
            button.configure(state=save_state)
        if getattr(self, "process_task_key", None) in _SCAN_TASK_KEYS:
            self._set_stop_state(stop_state)

    def _toggle_mini_mode(self) -> None:
        if self.mini_mode:
            self._leave_mini_mode()
        else:
            self._enter_mini_mode()

    def _enter_mini_mode(self) -> None:
        if self.mini_mode:
            return
        self.root.update_idletasks()
        self._normal_geometry = self.root.geometry()
        self._normal_window_state = self.root.state()
        current_x = self.root.winfo_rootx()
        current_y = self.root.winfo_rooty()
        current_width = self.root.winfo_width()
        if self._normal_window_state != "normal":
            self.root.state("normal")
            self.root.update_idletasks()
        self._preferred_normal_size = (
            self.root.winfo_width(), self.root.winfo_height())
        self._normal_position = (
            self.root.winfo_x(), self.root.winfo_y())
        self._normal_monitor_signature = (
            _monitor_work_area_for_window(self.root).signature)

        self.colour_strip.pack_forget()
        self.task_toolbar_panel.pack_forget()
        self._mini_progress_was_expanded = self.progress_expanded
        self.mini_mode = True
        self._set_progress_expanded(True)
        self.task_card.grid_remove()
        self.log_panel.grid_remove()
        self.command_panel.grid_remove()
        self.progress_panel.grid_configure(row=0, pady=0)
        self.content.grid_rowconfigure(0, weight=0)
        self.content.grid_rowconfigure(2, weight=0)
        self.content.pack_configure(padx=10, pady=10)
        self.mini_stop_button.pack(side="right", padx=(0, 6))
        self.mini_save_button.pack(side="right", padx=(0, 6))
        self.mini_pause_button.pack(side="right", padx=(0, 6))
        self._refresh_scan_controls()
        self._refresh_mini_action()

        self.root.update_idletasks()
        work_area = _monitor_work_area_for_window(self.root)
        width = max(420, min(680, work_area.width - 32))
        requested_height = self.progress_panel.winfo_reqheight() + 20
        height = max(
            190,
            min(300, requested_height, work_area.height - 40),
        )
        width, height, x, y = fit_window_to_work_area(
            (width, height),
            (current_x + current_width - width, current_y),
            work_area,
        )
        self.root.minsize(min(520, width), height)
        self.root.geometry(_window_geometry_string(width, height, x, y))
        self.root.title(f"{core.PROJECT_NAME} {_version()} · 运行进度")

    def _leave_mini_mode(self) -> None:
        if not self.mini_mode:
            return
        self.mini_pause_button.pack_forget()
        self.mini_save_button.pack_forget()
        self.mini_stop_button.pack_forget()
        self.progress_panel.grid_configure(row=1, pady=(10, 0))
        self.task_card.grid()
        self.log_panel.grid()
        self.command_panel.grid()
        self.content.pack_configure(
            padx=self.content_pad, pady=self.content_pad)
        self.colour_strip.pack(fill="x", side="top", before=self.body)
        self.task_toolbar_panel.pack(
            fill="x", side="top", before=self.body)
        self.mini_mode = False
        self._set_settings_expanded(self.settings_expanded)
        self._set_progress_expanded(self._mini_progress_was_expanded)
        self._refresh_mini_action()
        self._refresh_tool_cache_labels()
        self.root.title(project_window_title())
        work_area = _monitor_work_area_for_window(self.root)
        current_monitor = work_area.signature
        restore_position = (
            self._normal_position
            if current_monitor == self._normal_monitor_signature else
            (self.root.winfo_x(), self.root.winfo_y())
        )
        width, height, x, y = fit_window_to_work_area(
            self._preferred_normal_size, restore_position, work_area)
        self.normal_width_cap = min(1200, width)
        self.normal_min_size = (
            min(760, width), min(640, height))
        self._sync_task_toolbar_minimum_width()
        self._monitor_signature = current_monitor
        self._monitor_applied_size = (
            (width, height)
            if (width, height) != self._preferred_normal_size else None
        )
        self.root.geometry(
            _window_geometry_string(width, height, x, y))
        if self._normal_window_state != "normal":
            self.root.after_idle(
                lambda state=self._normal_window_state:
                self.root.state(state))

    def _scroll_form(self, event: tk.Event) -> str:
        delta = int(getattr(event, "delta", 0) or 0)
        units = int(-delta / 120)
        if units == 0 and delta:
            units = -1 if delta > 0 else 1
        if not delta:
            button = int(getattr(event, "num", 0) or 0)
            units = -1 if button == 4 else 1 if button == 5 else 0
        if units:
            if self._form_content_fits_viewport():
                self.form_canvas.yview_moveto(0.0)
            else:
                try:
                    first_fraction = float(self.form_canvas.yview()[0])
                except (AttributeError, tk.TclError, TypeError, ValueError):
                    first_fraction = 1.0
                if units < 0 and first_fraction <= 0.0:
                    self.form_canvas.yview_moveto(0.0)
                else:
                    self.form_canvas.yview_scroll(units, "units")
                    if units < 0:
                        try:
                            if float(self.form_canvas.yview()[0]) < 0.0:
                                self.form_canvas.yview_moveto(0.0)
                        except (
                            AttributeError, tk.TclError,
                            TypeError, ValueError,
                        ):
                            pass
        return "break"

    def _resize_form_canvas_window(self, event: tk.Event) -> None:
        """让表单跟随视口宽度，并在几何稳定后重算滚动范围。"""
        try:
            self.form_canvas.itemconfigure(
                self.form_window, width=max(1, int(event.width)))
        except (AttributeError, tk.TclError, TypeError, ValueError):
            return
        self._schedule_form_scroll_sync()

    def _schedule_form_scroll_sync(
        self, _event: tk.Event | None = None,
    ) -> None:
        """合并连续的控件几何事件，避免保留旧页面的滚动范围。"""
        pending = getattr(self, "_form_scroll_sync_after_id", None)
        if pending is not None:
            try:
                self.root.after_cancel(pending)
            except (AttributeError, tk.TclError):
                pass
        try:
            self._form_scroll_sync_after_id = self.root.after_idle(
                self._sync_form_scroll_region)
        except (AttributeError, tk.TclError):
            self._form_scroll_sync_after_id = None

    def _form_content_height(self) -> int:
        """返回表单真实请求高度，不受旧 scrollregion 或视口空白影响。"""
        inner = getattr(self, "form_inner", None)
        if inner is not None:
            try:
                return max(0, int(inner.winfo_reqheight()))
            except (AttributeError, tk.TclError, TypeError, ValueError):
                pass
        try:
            bounds = self.form_canvas.bbox("all")
            if bounds is None:
                return 0
            return max(0, int(bounds[3]) - int(bounds[1]))
        except (AttributeError, tk.TclError, TypeError, ValueError):
            return -1

    def _sync_form_scroll_region(self) -> None:
        """仅在表单真实溢出时启用滚动，并把未溢出页面锁在顶部。"""
        self._form_scroll_sync_after_id = None
        try:
            viewport_width = max(1, int(self.form_canvas.winfo_width()))
            viewport_height = max(1, int(self.form_canvas.winfo_height()))
            content_height = max(0, self._form_content_height())
            overflow = content_height > viewport_height
            self.form_canvas.configure(
                scrollregion=(
                    0, 0, viewport_width,
                    content_height if overflow else viewport_height,
                ),
            )
            if overflow:
                if not self.form_scroll.winfo_manager():
                    self.form_scroll.pack(
                        side="right", fill="y", before=self.form_canvas)
            else:
                self.form_canvas.yview_moveto(0.0)
                if self.form_scroll.winfo_manager():
                    self.form_scroll.pack_forget()
        except (AttributeError, tk.TclError, TypeError, ValueError):
            return

    def _form_content_fits_viewport(self) -> bool:
        """内容未溢出时禁止 Canvas 产生顶部或底部空白。"""
        try:
            content_height = self._form_content_height()
            viewport_height = max(1, int(self.form_canvas.winfo_height()))
        except (AttributeError, tk.TclError, TypeError, ValueError):
            return False
        if content_height < 0:
            return False
        return content_height <= viewport_height

    def _position_form_scroll(self, fraction: float) -> None:
        self._sync_form_scroll_region()
        target = 0.0 if self._form_content_fits_viewport() else max(
            0.0, min(1.0, float(fraction)))
        self.form_canvas.yview_moveto(target)

    def _save_current_values(self) -> None:
        if self.values:
            self.saved_values[self.task.key] = self._collect_values()

    def _set_scan_advanced_value(self, key: str, value: object) -> None:
        if self._task_is_active() or key not in (
                "timeout_action", "show_current_file",
                "format_validation"):
            self._refresh_scan_advanced_values()
            return
        self.saved_values.setdefault("full_scan", {})[key] = value
        self._refresh_scan_advanced_values()
        if self.task.key == "full_scan":
            self._update_preview()

    def _edit_scan_format_sample_percent(self) -> None:
        if self._task_is_active():
            return
        current = _task_values(
            TASK_BY_KEY["full_scan"],
            self.saved_values.get("full_scan", {}),
        )
        entered = simpledialog.askstring(
            "Full 格式抽样比例",
            "请输入大于 0 且不超过 100 的百分比：",
            initialvalue=str(current.get("format_sample_percent") or "10.0"),
            parent=self.root,
        )
        if entered is None:
            return
        candidate = dict(current)
        candidate["format_validation"] = "sample"
        candidate["format_sample_percent"] = entered.strip()
        issues = validate_values("full_scan", candidate)
        numeric_issue = next((
            issue for issue in issues if "格式抽样" in issue), None)
        if numeric_issue:
            messagebox.showerror(
                "比例无效", numeric_issue, parent=self.root)
            return
        saved = self.saved_values.setdefault("full_scan", {})
        saved["format_validation"] = "sample"
        saved["format_sample_percent"] = entered.strip()
        self._refresh_scan_advanced_values()
        if self.task.key == "full_scan":
            self._update_preview()

    def _refresh_scan_advanced_values(self) -> None:
        if not hasattr(self, "scan_timeout_action_var"):
            return
        values = _task_values(
            TASK_BY_KEY["full_scan"],
            self.saved_values.get("full_scan", {}),
        )
        self.scan_timeout_action_var.set(str(
            values.get("timeout_action") or "continue_waiting"))
        self.scan_show_current_file_var.set(bool(
            values.get("show_current_file", False)))
        self.scan_format_validation_var.set(str(
            values.get("format_validation") or "off"))
        self.scan_format_sample_menu.entryconfigure(
            self.scan_format_sample_menu_index,
            label=(
                "设置抽样比例（"
                f"{values.get('format_sample_percent') or '10.0'}%）…"
            ),
        )

    def _select_task_from_toolbar(self, task_key: str) -> None:
        """切换功能模块，并移除按钮焦点框。"""
        self._select_task(task_key)
        self.root.focus_set()

    def _refresh_task_navigation_selection(self) -> None:
        """同步下拉菜单与按钮菜单的当前任务高亮。"""
        for task_key, (task_menu, entry_index) in (
                self.task_menu_entries.items()):
            selected = task_key == self.task.key
            task_menu.entryconfigure(
                entry_index,
                background=(
                    _UNIFIED_ACTION_BACKGROUND if selected else _SURFACE
                ),
                foreground=(
                    _UNIFIED_ACTION_FOREGROUND if selected else _TEXT
                ),
                activebackground=_UNIFIED_ACTION_BACKGROUND,
                activeforeground=_UNIFIED_ACTION_FOREGROUND,
                font=self._font_tuple(
                    9, "bold" if selected else "normal"),
            )
        for task_key, button in self.task_toolbar_buttons.items():
            selected_suffix = (
                "Selected" if task_key == self.task.key else ""
            )
            button.configure(
                style=(f"{_TASK_TOOLBAR_STYLE_PREFIX}.TopTask"
                       f"{selected_suffix}.TButton"))

    def _set_task_navigation_state(self, state: str) -> None:
        """运行期间锁定任务和参数入口，保留命令预览开关。"""
        for task_menu, entry_index in self.task_menu_entries.values():
            task_menu.entryconfigure(entry_index, state=state)
        for task_key, button in self.task_toolbar_buttons.items():
            button.configure(state=state)
        if hasattr(self, "advanced_menu"):
            for entry_index in getattr(
                    self, "advanced_locked_menu_entries", ()):
                self.advanced_menu.entryconfigure(entry_index, state=state)

    def _select_task(self, task_key: str, save_current: bool = True) -> None:
        if save_current:
            self._save_current_values()
        self.task = TASK_BY_KEY[task_key]
        self.gui_preferences["last_task_key"] = task_key
        if hasattr(self, "task_menu_var"):
            self.task_menu_var.set(task_key)
        self._refresh_task_navigation_selection()
        self.title_label.configure(text=task_display_title(self.task.key))
        self.desc_label.configure(text=self.task.description)
        self._build_form()
        self._refresh_hash_percentage_menu_labels()
        self._refresh_scan_advanced_values()
        self._refresh_tool_cache_labels()
        active = self._task_is_active()
        missing_tests = (
            project_self_test_missing_files()
            if task_key == _PROJECT_SELF_TEST_KEY else ()
        )
        self.run_button.configure(
            text=_RUN_BUTTON_TEXT,
            state="disabled" if missing_tests or active else "normal",
        )
        self._layout_action_buttons()
        self._refresh_scan_controls()
        self._refresh_environment_actions()
        if self.process is None:
            self._reset_progress(task_display_title(self.task.key))
            if not self.run_jobs:
                if (task_key in _STG_ADMIN_TASKS
                        and not self.is_administrator):
                    self._set_status(
                        "此功能需要管理员权限：请开启顶部管理员模式并重新启动 DAISY。",
                        _WARNING,
                    )
                else:
                    self._set_status("就绪")

    def _choice_display(self, spec: FieldSpec, value: object) -> str:
        choices = self._field_choices(spec)
        for label, internal in choices:
            if internal == value:
                return label
        return choices[0][0] if choices else str(value or "")

    def _field_choices(
        self, spec: FieldSpec,
    ) -> tuple[tuple[str, object], ...]:
        if spec.kind != "disk_choice":
            return spec.choices
        discovered = tuple(getattr(self, "storage_disk_choices", ()))
        if not discovered:
            return (("请先检测物理硬盘", ""),)
        return (("请选择本次清单中的物理硬盘", ""), *discovered)

    def _build_environment_installation(
        self, row: int, form_pad: int,
    ) -> int:
        """在 ENV-01 设置页建立独立的软件安装区。"""
        panel = tk.Frame(
            self.form_inner, bg=_SURFACE,
            highlightbackground=_BORDER, highlightthickness=1,
        )
        panel.grid(
            row=row, column=0, columnspan=2, sticky="ew",
            padx=form_pad, pady=(8, 4),
        )
        panel.grid_columnconfigure(0, weight=1)

        header = tk.Frame(panel, bg=_SURFACE)
        header.grid(row=0, column=0, sticky="ew", padx=12, pady=(6, 2))
        tk.Frame(header, bg=_GREEN_DARK, width=4, height=18).pack(
            side="left", fill="y")
        tk.Label(
            header, text="软件安装", bg=_SURFACE, fg=_GREEN_DEEP,
            font=("Microsoft YaHei UI", 9, "bold"), anchor="w",
        ).pack(side="left", padx=(8, 0))

        install_help = tk.Label(
            panel,
            text=(
                "可按需单独下载、安装或更新下列工具。是否已经安装及是否有"
                "可用更新由 Windows 包管理器判断；PowerShell 由系统提供，"
                "不在此安装。"
            ),
            bg=_SURFACE, fg=_TEXT,
            font=("Microsoft YaHei UI", 9), anchor="w",
            justify="left", wraplength=720,
        )
        install_help.grid(
            row=1, column=0, sticky="ew", padx=12, pady=(2, 6))
        panel.bind(
            "<Configure>",
            lambda event, label=install_help: label.configure(
                wraplength=max(260, event.width - 24)),
        )

        button_grid = tk.Frame(panel, bg=_SURFACE)
        button_grid.grid(
            row=2, column=0, sticky="ew", padx=12, pady=(0, 8))
        for column in range(len(_INSTALLABLE_TOOL_PACKAGES)):
            button_grid.grid_columnconfigure(
                column, weight=1, uniform="environment_install")
        for index, (tool_name, (display_name, _package_id)) in enumerate(
                _INSTALLABLE_TOOL_PACKAGES.items()):
            button = ttk.Button(
                button_grid,
                text=f"安装 {_TOOL_DISPLAY_NAMES[tool_name]}",
                style="FormAction.TButton",
                command=lambda name=tool_name: self._install_tool(name),
            )
            button.grid(
                row=0, column=index, sticky="ew",
                padx=(0, 6 if index < len(
                    _INSTALLABLE_TOOL_PACKAGES) - 1 else 0),
            )
            self.install_tool_buttons[tool_name] = button
            attach_tooltip(
                button,
                f"仅通过 WinGet 下载并安装 {display_name}；不会连带安装"
                "其它工具。",
            )
        return row + 1

    def _build_admin_requirement_notice(
        self, row: int, form_pad: int,
    ) -> int:
        panel = tk.Frame(
            self.form_inner, bg=_AMBER_SOFT,
            highlightbackground=_AMBER, highlightthickness=1,
        )
        panel.grid(
            row=row, column=0, columnspan=2, sticky="ew",
            padx=form_pad, pady=(5, 2),
        )
        tk.Label(
            panel, text="需要管理员权限", bg=_AMBER_SOFT,
            fg=_AMBER_DEEP,
            font=("Microsoft YaHei UI", 9, "bold"), anchor="w",
        ).pack(fill="x", padx=12, pady=(6, 2))
        detail = tk.Label(
            panel,
            text=(
                "此功能需要管理员权限才能完整运行。请开启顶部功能模块标题栏"
                "的管理员模式开关，确认后由 Windows UAC 重新启动 DAISY。"
                "未提权继续运行可能只得到不完整诊断或失败。"
            ),
            bg=_AMBER_SOFT, fg=_TEXT,
            font=("Microsoft YaHei UI", 9), anchor="w",
            justify="left", wraplength=720,
        )
        detail.pack(fill="x", padx=12, pady=(0, 7))
        panel.bind(
            "<Configure>",
            lambda event, label=detail: label.configure(
                wraplength=max(260, event.width - 24)),
        )
        return row + 1

    def _build_storage_detection(
        self, row: int, form_pad: int,
    ) -> int:
        panel = tk.Frame(
            self.form_inner, bg=_SURFACE,
            highlightbackground=_BORDER, highlightthickness=1,
        )
        panel.grid(
            row=row, column=0, columnspan=2, sticky="ew",
            padx=form_pad, pady=(5, 2),
        )
        found_count = len(self.storage_disk_options)
        selectable_count = sum(
            option.selectable for option in self.storage_disk_options)
        tk.Label(
            panel, text="登记准备", bg=_SURFACE, fg=_GREEN_DEEP,
            font=("Microsoft YaHei UI", 9, "bold"), anchor="w",
        ).pack(fill="x", padx=12, pady=(6, 2))
        detail = tk.Label(
            panel,
            text=(
                f"已检测 {found_count} 块物理硬盘，其中 {selectable_count} 块"
                "联机且可登记。若接入硬盘发生变化，请重新进行检测。"
                if found_count else
                "先检测本机物理硬盘，再从硬盘池选择登记目标。检测是登记"
                "流程的准备步骤，不是独立功能模块。"
            ),
            bg=_SURFACE, fg=_TEXT,
            font=("Microsoft YaHei UI", 9), anchor="w",
            justify="left", wraplength=720,
        )
        detail.pack(fill="x", padx=12, pady=(0, 5))
        self.storage_detect_button = ttk.Button(
            panel,
            text="重新检测硬盘" if found_count else "检测物理硬盘",
            style="FormAction.TButton", width=_FORM_ACTION_BUTTON_WIDTH,
            command=self._run_storage_inventory,
        )
        self.storage_detect_button.pack(anchor="w", padx=12, pady=(0, 7))
        attach_tooltip(
            self.storage_detect_button,
            "运行内部 STG-11 只读列盘步骤，并刷新本页的物理硬盘选择。",
        )
        panel.bind(
            "<Configure>",
            lambda event, label=detail: label.configure(
                wraplength=max(260, event.width - 24)),
        )
        return row + 1

    def _configure_form_label_column(
        self, form_pad: int | None = None,
    ) -> None:
        """按全局六字标题体系固定标签列，避免切页时输入区左右跳动。"""
        if form_pad is None:
            form_pad = 16 if self.compact_layout else 22
        label_gap = 11 if self.compact_layout else 14
        labels = (
            spec.label
            for task in TASKS
            for spec in task.fields
            if not spec.top_menu
        )
        try:
            label_font = tkfont.Font(
                root=self.root, font=self._font_tuple(9, "bold"))
            widest = max(label_font.measure(label) for label in labels)
        except (tk.TclError, TypeError, ValueError):
            widest = _FORM_FIELD_TITLE_MAX_CHARS * 12
        self.form_inner.grid_columnconfigure(
            0, weight=0, minsize=widest + form_pad + label_gap + 6)
        self.form_inner.grid_columnconfigure(1, weight=1)

    def _build_form(self, scroll_fraction: float = 0.0) -> None:
        for child in self.form_inner.winfo_children():
            child.destroy()
        self.values = {}
        self.install_tool_buttons = {}
        self.storage_detect_button = None
        form_pad = 16 if self.compact_layout else 22
        saved = _task_values(
            self.task, self.saved_values.get(self.task.key, {}))
        active_specs = [
            spec for spec in self.task.fields
            if _field_active(spec, saved) and not spec.top_menu
        ]
        self._configure_form_label_column(form_pad)
        row = 0

        if self.task.key == _PROJECT_SELF_TEST_KEY:
            info = tk.Frame(
                self.form_inner, bg=_SURFACE,
                highlightbackground=_BORDER, highlightthickness=1,
            )
            info.grid(
                row=0, column=0, columnspan=2, sticky="ew",
                padx=form_pad, pady=(18, 8),
            )
            tk.Label(
                info, text="无需设置", bg=_SURFACE, fg=_GREEN_DEEP,
                font=("Microsoft YaHei UI", 10, "bold"), anchor="w",
            ).pack(fill="x", padx=14, pady=(11, 3))
            info_text = tk.Label(
                info,
                text=(
                    "将运行 Script\\Test 中的全部 unittest。测试夹具只写入"
                    "系统临时目录，不读取表单档案，也不生成正式快照。"
                ),
                bg=_SURFACE, fg=_TEXT,
                font=("Microsoft YaHei UI", 9), anchor="w",
                justify="left", wraplength=720,
            )
            info_text.pack(fill="x", padx=14, pady=(0, 12))
            info.bind(
                "<Configure>",
                lambda e, label=info_text: label.configure(
                    wraplength=max(260, e.width - 28)),
            )
            row = 1

        if (self.task.key in _STG_ADMIN_TASKS
                and not self.is_administrator):
            row = self._build_admin_requirement_notice(row, form_pad)
        if self.task.key == "storage_collect":
            row = self._build_storage_detection(row, form_pad)

        current_section: str | None = None
        section_colour = _NAV_COLOURS.get(
            self.task.key, (_ACCENT, _ACCENT_DARK))[0]
        for spec in active_specs:
            if spec.section != current_section:
                current_section = spec.section
                section = tk.Frame(self.form_inner, bg=_SURFACE)
                section.grid(
                    row=row, column=0, columnspan=2, sticky="ew",
                    padx=form_pad, pady=(2, 0),
                )
                tk.Frame(
                    section, bg=section_colour, width=4, height=15,
                ).pack(side="left", fill="y")
                tk.Label(
                    section, text=current_section,
                    bg=_SURFACE, fg=section_colour,
                    font=("Microsoft YaHei UI", 9, "bold"), anchor="w",
                ).pack(side="left", padx=(8, 0))
                row += 1

            current = saved.get(spec.key, spec.default)
            field_label = tk.Label(
                self.form_inner, text=spec.label, bg=_SURFACE, fg=_TEXT,
                font=("Microsoft YaHei UI", 9, "bold"), anchor="ne",
            )
            field_label.grid(
                row=row, column=0, sticky="ne",
                padx=(form_pad, 11 if self.compact_layout else 14),
                pady=(4, 0),
            )

            cell = tk.Frame(self.form_inner, bg=_SURFACE)
            cell.grid(row=row, column=1, sticky="ew", padx=(0, form_pad),
                      pady=(2, 1))
            cell.grid_columnconfigure(0, weight=1)

            if spec.kind == "disk_pool":
                widget = StorageDiskPool(
                    cell, options=self.storage_disk_options,
                    initial=current, on_change=self._update_preview,
                )
                widget.grid(row=0, column=0, columnspan=2, sticky="ew")
                self.values[spec.key] = widget
            elif spec.kind in ("choice", "choice_flag", "disk_choice"):
                choices = self._field_choices(spec)
                var = tk.StringVar(
                    value=self._choice_display(spec, current))
                widget = ttk.Combobox(
                    cell, textvariable=var, state="readonly",
                    style="Daisy.TCombobox",
                    values=[label for label, _value in choices],
                )
                widget._daisy_field_key = spec.key  # type: ignore[attr-defined]
                widget.grid(row=0, column=0, sticky="ew")
                widget.bind("<<ComboboxSelected>>",
                            self._choice_changed)
                widget.bind("<MouseWheel>", self._scroll_form)
                widget.bind("<Button-4>", self._scroll_form)
                widget.bind("<Button-5>", self._scroll_form)
                self.values[spec.key] = var
            elif spec.kind == "multidir":
                widget = DirectoryListEditor(
                    cell, initial=current, title=spec.label,
                    on_change=self._update_preview,
                )
                widget.grid(
                    row=0, column=0, columnspan=2, sticky="ew")
                self.values[spec.key] = widget
            elif spec.kind in ("multimapdir", "multiline"):
                widget = tk.Text(
                    cell, height=3, wrap="none", bg=_FIELD, fg=_TEXT,
                    insertbackground=_TEXT, relief="solid", bd=1,
                    highlightthickness=0,
                    font=("Microsoft YaHei UI", 9),
                    padx=7, pady=6,
                )
                widget.grid(row=0, column=0, sticky="ew")
                widget.insert("1.0", str(current or ""))
                widget.edit_modified(False)
                widget.bind("<<Modified>>", self._text_changed)
                self.values[spec.key] = widget
                if spec.kind == "multimapdir":
                    add_directory_button = ttk.Button(
                        cell, text="添加目录", style="FormAction.TButton",
                        width=_FORM_ACTION_BUTTON_WIDTH,
                        command=lambda s=spec, w=widget:
                        self._append_directory(s, w),
                    )
                    add_directory_button.grid(
                        row=0, column=1, sticky="ne", padx=(8, 0))
                    attach_tooltip(
                        add_directory_button,
                        f"选择一个目录并追加到“{spec.label}”列表。",
                    )
            else:
                var = tk.StringVar(value=str(current or ""))
                var.trace_add("write", lambda *_args: self._update_preview())
                widget = ttk.Entry(cell, textvariable=var)
                widget.grid(row=0, column=0, sticky="ew")
                self.values[spec.key] = var
                if spec.kind == "dir":
                    widget.bind(
                        "<FocusOut>",
                        lambda _event, variable=var:
                        self._normalize_directory_variable(variable),
                    )
                if spec.kind in ("dir", "file", "save"):
                    browse_button = ttk.Button(
                        cell, text="浏览", style="FormAction.TButton",
                        width=_FORM_ACTION_BUTTON_WIDTH,
                        command=lambda s=spec, v=var: self._browse(s, v),
                    )
                    browse_button.grid(
                        row=0, column=1, sticky="e", padx=(8, 0))
                    attach_tooltip(
                        browse_button,
                        (
                            f"选择“{spec.label}”的保存位置。"
                            if spec.kind == "save" else
                            f"选择“{spec.label}”。"
                        ),
                    )

            if spec.help:
                attach_tooltip(field_label, spec.help)
                attach_tooltip(cell, spec.help)
                attach_tooltip(widget, spec.help)
            row += 1

        if self.task.key == "env_check":
            row = self._build_environment_installation(row, form_pad)

        self.form_inner.grid_rowconfigure(row, minsize=4)
        self._apply_font_to_tree(self.form_inner)
        self.form_canvas.update_idletasks()
        self._position_form_scroll(scroll_fraction)
        task_key = self.task.key
        self.root.after_idle(
            lambda key=task_key, fraction=scroll_fraction:
            self._position_form_scroll(fraction)
            if self.task.key == key else None
        )
        self._update_preview()

    def _choice_changed(self, event: tk.Event) -> None:
        task_key = self.task.key
        field_key = getattr(event.widget, "_daisy_field_key", None)
        previous = _task_values(
            self.task, self.saved_values.get(task_key, {}))
        scroll_fraction = self.form_canvas.yview()[0]
        collected = self._collect_values()
        self.saved_values[task_key] = collected
        if field_key is not None and (
                collected.get(field_key) == previous.get(field_key)):
            self._update_preview()
            return

        # 等待 ComboboxSelected 事件完成后再重建条件字段，避免 Tcl/Tk 在原
        # 控件销毁后继续刷新选择状态，导致重复选择当前项时显示为空。
        self.root.after_idle(
            lambda key=task_key, fraction=scroll_fraction:
            self._build_form(fraction) if self.task.key == key else None
        )

    def _text_changed(self, event: tk.Event) -> None:
        widget = event.widget
        if widget.edit_modified():
            widget.edit_modified(False)
            self._update_preview()

    def _normalize_directory_variable(
            self, variable: tk.StringVar) -> None:
        raw = variable.get().strip()
        if not raw:
            return
        absolute = _absolute(raw)
        if absolute != raw:
            variable.set(absolute)

    def _browse(self, spec: FieldSpec, variable: tk.StringVar) -> None:
        initial = variable.get().strip()
        initial_dir = _BASE
        if initial:
            candidate = _absolute(initial)
            initial_dir = candidate if os.path.isdir(candidate) else \
                os.path.dirname(candidate)
        if spec.kind == "dir":
            chosen = filedialog.askdirectory(
                parent=self.root, initialdir=initial_dir,
                title=f"选择{spec.label}",
            )
        elif spec.kind == "save":
            chosen = filedialog.asksaveasfilename(
                parent=self.root, initialdir=initial_dir,
                title=f"选择{spec.label}", filetypes=spec.filetypes,
                defaultextension=".json",
            )
        else:
            chosen = filedialog.askopenfilename(
                parent=self.root, initialdir=initial_dir,
                title=f"选择{spec.label}", filetypes=spec.filetypes,
            )
        if chosen:
            variable.set(os.path.normpath(chosen))

    def _append_directory(self, spec: FieldSpec, widget: tk.Text) -> None:
        chosen = filedialog.askdirectory(
            parent=self.root, initialdir=_BASE, title=f"添加{spec.label}",
        )
        if not chosen:
            return
        value = os.path.normpath(chosen)
        existing = widget.get("1.0", "end-1c")
        widget.insert("end", ("\n" if existing and not existing.endswith("\n")
                              else "") + value)
        widget.edit_modified(True)

    def _collect_values(self) -> dict[str, object]:
        result = _task_values(
            self.task, self.saved_values.get(self.task.key, {}))
        specs = {spec.key: spec for spec in self.task.fields}
        for key, source in self.values.items():
            spec = specs[key]
            if isinstance(source, (DirectoryListEditor, StorageDiskPool)):
                result[key] = source.get()
            elif isinstance(source, tk.Text):
                result[key] = source.get("1.0", "end-1c")
            else:
                value: object = source.get()
                if spec.kind in ("choice", "choice_flag", "disk_choice"):
                    for label, internal in self._field_choices(spec):
                        if label == value:
                            value = internal
                            break
                result[key] = value
        return result

    def _effective_values(
        self, values: dict[str, object] | None = None,
    ) -> tuple[dict[str, object], dict[str, str]]:
        raw = values if values is not None else self._collect_values()
        return merge_session_tool_paths(
            self.task.key, raw, self.detected_tools,
            manual_paths=self.manual_tool_paths,
        )

    def _update_preview(self) -> None:
        if self.task.key == _PROJECT_SELF_TEST_KEY:
            self.preview_var.set(project_self_test_preview())
            return
        if not self.values:
            return
        try:
            effective, _sources = self._effective_values()
            self.preview_var.set(
                preview_command(self.task.key, effective))
        except (KeyError, tk.TclError):
            pass

    def _copy_command(self) -> None:
        if self.task.key == _PROJECT_SELF_TEST_KEY:
            self.root.clipboard_clear()
            self.root.clipboard_append(project_self_test_preview())
            self._set_status("DAISY 功能自检命令已复制到剪贴板。")
            return
        effective, _sources = self._effective_values()
        previews = preview_commands(self.task.key, effective)
        self.root.clipboard_clear()
        self.root.clipboard_append(
            "\n".join(command for _label, command in previews))
        status = (
            f"{len(previews)} 条队列命令已复制。"
            if len(previews) > 1 else "命令已复制到剪贴板。")
        self._set_status(status)

    def _output_path(self) -> str:
        values = self._collect_values()
        if (self.task.key == "full_scan"
                and values.get("start_mode") == "resume"):
            resume = str(values.get("resume") or "").strip()
            if resume:
                return os.path.dirname(_absolute(resume))
        for key in ("output_dir", "report_dir"):
            raw = str(values.get(key) or "").strip()
            if raw:
                return _absolute(raw)
        report = str(values.get("report") or "").strip()
        if report:
            return os.path.dirname(_absolute(report))
        if self.task.key.startswith("storage_"):
            return _DEFAULT_STORAGE_DIR
        return os.path.join(_BASE, "Output")

    def _hash_percentage_menu_label(
        self, task_key: str, field_key: str,
    ) -> str:
        definition = next(
            item for item in _HASH_PERCENTAGE_MENU_FIELDS
            if item[:2] == (task_key, field_key)
        )
        task = TASK_BY_KEY[task_key]
        saved_values = getattr(self, "saved_values", {})
        values = _task_values(task, saved_values.get(task_key, {}))
        return f"{definition[2]}：{values[field_key]}%"

    def _refresh_hash_percentage_menu_labels(self) -> None:
        if not hasattr(self, "hash_percentage_menu_entries"):
            return
        for key, entry_index in self.hash_percentage_menu_entries.items():
            self.hash_percentage_menu.entryconfigure(
                entry_index,
                label=self._hash_percentage_menu_label(*key),
            )

    def _edit_hash_percentage(
        self, task_key: str, field_key: str,
    ) -> None:
        if self._task_is_active():
            messagebox.showinfo(
                "任务运行中",
                "请等待当前任务结束后再修改哈希比例。",
                parent=self.root,
            )
            return
        definition = next(
            (item for item in _HASH_PERCENTAGE_MENU_FIELDS
             if item[:2] == (task_key, field_key)),
            None,
        )
        if definition is None:
            return
        task = TASK_BY_KEY[task_key]
        spec = next(item for item in task.fields if item.key == field_key)
        current = _task_values(
            task, self.saved_values.get(task_key, {}))[field_key]
        allow_zero = definition[3]
        range_text = (
            "0 到 100 之间" if allow_zero else "大于 0 且不超过 100")
        chosen = simpledialog.askstring(
            "高级 · 哈希比例",
            f"{definition[2]}\n\n{spec.help}\n\n请输入{range_text}的百分比：",
            initialvalue=str(current), parent=self.root,
        )
        if chosen is None:
            return
        text = chosen.strip()
        try:
            number = float(text)
        except ValueError:
            number = math.nan
        valid = (
            math.isfinite(number)
            and number <= 100.0
            and (number >= 0.0 if allow_zero else number > 0.0)
        )
        if not valid:
            messagebox.showerror(
                "哈希比例无效",
                f"请输入{range_text}的数字。",
                parent=self.root,
            )
            return
        self._save_current_values()
        normalized = str(number)
        self.saved_values.setdefault(task_key, {})[field_key] = normalized
        self._refresh_hash_percentage_menu_labels()
        self._update_preview()
        self._set_status(f"{definition[2]}已设置为 {normalized}%。")

    def _reset_hash_percentages(self) -> None:
        if self._task_is_active():
            return
        self._save_current_values()
        count = 0
        for task_key, field_key, _label, _allow_zero in (
                _HASH_PERCENTAGE_MENU_FIELDS):
            task_values = self.saved_values.get(task_key, {})
            if field_key in task_values:
                del task_values[field_key]
                count += 1
        self._refresh_hash_percentage_menu_labels()
        self._update_preview()
        self._set_status(
            f"已恢复 {count} 项哈希比例默认值。"
            if count else "哈希比例已经使用默认值。"
        )

    def _tool_path_menu_label(self, tool_name: str) -> str:
        display_name = _TOOL_DISPLAY_NAMES[tool_name]
        manual_paths = getattr(self, "manual_tool_paths", {})
        selected = str(manual_paths.get(tool_name) or "").strip()
        status = os.path.basename(selected) if selected else "自动发现"
        return f"{display_name} 路径：{status}"

    def _refresh_tool_path_menu_labels(self) -> None:
        if not hasattr(self, "tool_path_menu_entries"):
            return
        for tool_name, entry_index in self.tool_path_menu_entries.items():
            self.tool_path_menu.entryconfigure(
                entry_index,
                label=self._tool_path_menu_label(tool_name),
            )

    def _select_tool_path(self, tool_name: str) -> None:
        if self._task_is_active():
            messagebox.showinfo(
                "任务运行中",
                "请等待当前任务结束后再修改工具路径。",
                parent=self.root,
            )
            return
        if tool_name not in _TOOL_DISPLAY_NAMES:
            return
        selected = str(self.manual_tool_paths.get(tool_name) or "").strip()
        initial_dir = (
            os.path.dirname(selected)
            if selected and os.path.isdir(os.path.dirname(selected))
            else _BASE
        )
        display_name = _TOOL_DISPLAY_NAMES[tool_name]
        chosen = filedialog.askopenfilename(
            parent=self.root,
            initialdir=initial_dir,
            title=f"指定 {display_name} 可执行文件",
            filetypes=_EXE_TYPES,
        )
        if not chosen:
            return
        self.manual_tool_paths[tool_name] = os.path.abspath(chosen)
        self._refresh_tool_path_menu_labels()
        self._update_preview()
        self._set_status(
            f"已指定 {display_name} 路径；任务启动时会验证。")

    def _clear_manual_tool_paths(self) -> None:
        if self._task_is_active():
            return
        count = len(self.manual_tool_paths)
        self.manual_tool_paths.clear()
        self._refresh_tool_path_menu_labels()
        self._update_preview()
        self._set_status(
            f"已恢复 {count} 项工具路径的自动发现。"
            if count else "工具路径已经使用自动发现。"
        )

    def _request_admin_mode(self, desired: bool) -> None:
        if not desired:
            self._refresh_environment_actions()
            return
        if self._task_is_active():
            messagebox.showinfo(
                "任务运行中",
                "请先等待任务结束或停止任务，再切换管理员模式。",
                parent=self.root,
            )
            self._refresh_environment_actions()
            return
        if getattr(self, "is_administrator", False):
            messagebox.showinfo(
                "已是管理员模式",
                "当前 DAISY 已具有管理员权限，无需重新启动。",
                parent=self.root,
            )
            self._refresh_environment_actions()
            return
        if os.name != "nt":
            messagebox.showerror(
                "无法切换管理员模式",
                "管理员模式重启仅适用于 Windows。",
                parent=self.root,
            )
            self._refresh_environment_actions()
            return
        if not messagebox.askyesno(
                "以管理员模式重新启动",
                "当前窗口将关闭，并触发 Windows UAC 确认。重启后会返回"
                "当前功能页面，但表单内容和本窗口日志不会保留。\n\n"
                "确定继续吗？",
                icon="question", parent=self.root):
            self._refresh_environment_actions()
            return
        self._save_gui_preferences()
        try:
            restart_as_windows_administrator()
        except OSError as exc:
            messagebox.showerror(
                "管理员模式启动失败", str(exc), parent=self.root)
            self._refresh_environment_actions()
            return
        self._destroy_root()

    def _open_project_directory(self) -> None:
        try:
            os.startfile(_BASE)
        except OSError as exc:
            messagebox.showerror(
                "无法打开项目目录", str(exc), parent=self.root)

    def _open_github(self) -> None:
        try:
            os.startfile(_PROJECT_GITHUB_URL)
        except OSError as exc:
            messagebox.showerror(
                "无法打开 GitHub", str(exc), parent=self.root)

    def _show_about(self) -> None:
        messagebox.showinfo(
            "关于 DAISY",
            about_message(),
            parent=self.root,
        )

    def _show_author_contact(self) -> None:
        messagebox.showinfo(
            "联系作者",
            contact_message(),
            parent=self.root,
        )

    def _open_output(self) -> None:
        path = self._output_path()
        if not os.path.isdir(path):
            messagebox.showinfo(
                "结果目录尚不存在",
                f"目录会在任务首次产出结果时创建：\n{path}",
                parent=self.root,
            )
            return
        try:
            os.startfile(path)
        except OSError as exc:
            messagebox.showerror(
                "无法打开目录", str(exc), parent=self.root)

    def _offer_open_result_directory(self, path: str) -> None:
        """任务完成后询问是否打开本次结果目录。"""
        if not os.path.isdir(path):
            return
        if not messagebox.askyesno(
                "任务已完成",
                f"是否打开结果文件夹？\n\n{path}",
                icon="question", parent=self.root):
            return
        try:
            os.startfile(path)
        except OSError as exc:
            messagebox.showerror(
                "无法打开结果文件夹", str(exc), parent=self.root)

    @staticmethod
    def _configure_log_tags(widget: tk.Text) -> None:
        widget.tag_configure("meta", foreground=_GREEN_DEEP)
        widget.tag_configure("success", foreground=_SUCCESS)
        widget.tag_configure("warning", foreground=_WARNING)
        widget.tag_configure("error", foreground=_DANGER)

    def _active_log_widgets(self) -> tuple[tk.Text, ...]:
        widgets = [self.log]
        detached = getattr(self, "log_window_text", None)
        if detached is not None:
            try:
                exists = bool(detached.winfo_exists())
            except (AttributeError, tk.TclError):
                exists = False
            if exists:
                widgets.append(detached)
        return tuple(widgets)

    def _close_log_window(self) -> None:
        window = getattr(self, "log_window", None)
        self.log_window = None
        self.log_window_text = None
        self.log_window_icon_handle = None
        if window is None:
            return
        try:
            if window.winfo_exists():
                window.destroy()
        except tk.TclError:
            pass

    def _open_log_window(self) -> None:
        """打开单例独立日志窗口，并保持后续日志实时同步。"""
        window = getattr(self, "log_window", None)
        if window is not None:
            try:
                if window.winfo_exists():
                    window.deiconify()
                    window.lift()
                    window.focus_set()
                    return
            except tk.TclError:
                pass
            self._close_log_window()

        window = tk.Toplevel(self.root)
        self.log_window = window
        window.title(f"{core.PROJECT_NAME} {_version()} · 运行日志")
        window.configure(bg=_BG)
        work_area = _monitor_work_area_for_window(self.root)
        width = min(1100, max(640, work_area.width - 80))
        height = min(820, max(420, work_area.height - 80))
        width, height, x, y = fit_window_to_work_area(
            (width, height),
            (self.root.winfo_x() + 48, self.root.winfo_y() + 48),
            work_area,
        )
        window.geometry(_window_geometry_string(width, height, x, y))
        window.minsize(min(640, width), min(360, height))

        header = tk.Frame(window, bg=_LOG_HEADER)
        header.pack(fill="x")
        tk.Label(
            header, text="运行日志 · 与主界面实时同步",
            bg=_LOG_HEADER, fg=_TEXT,
            font=("Microsoft YaHei UI", 9, "bold"),
        ).pack(side="left", padx=14, pady=9)
        ttk.Button(
            header, text="关闭", style="PanelHeader.TButton",
            width=8, command=self._close_log_window,
        ).pack(side="right", padx=(6, 12), pady=6)
        ttk.Button(
            header, text="清空日志", style="PanelHeader.TButton",
            width=10, command=self._clear_log,
        ).pack(side="right", pady=6)

        body = tk.Frame(window, bg=_LOG_BG)
        body.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        detached = tk.Text(
            body, bg=_LOG_BG, fg=_LOG_TEXT, insertbackground=_TEXT,
            selectbackground=_LOG_SELECT, relief="flat", bd=0,
            font=("Microsoft YaHei UI", 9), wrap="word",
            padx=13, pady=10, state="normal",
        )
        scroll = ttk.Scrollbar(
            body, orient="vertical", command=detached.yview,
            style="Daisy.Vertical.TScrollbar",
        )
        detached.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        detached.pack(fill="both", expand=True)
        self._configure_log_tags(detached)
        detached.insert("1.0", self.log.get("1.0", "end-1c"))
        for tag in ("meta", "success", "warning", "error"):
            ranges = self.log.tag_ranges(tag)
            for index in range(0, len(ranges), 2):
                detached.tag_add(
                    tag, str(ranges[index]), str(ranges[index + 1]))
        detached.see("end")
        detached.configure(state="disabled")
        self.log_window_text = detached
        self.log_window_icon_handle = _install_daisy_window_icon(window)
        window.protocol("WM_DELETE_WINDOW", self._close_log_window)
        window.bind("<Escape>", lambda _event: self._close_log_window())
        self._apply_font_to_tree(window)

    def _clear_log(self) -> None:
        for widget in self._active_log_widgets():
            widget.configure(state="normal")
            widget.delete("1.0", "end")
            widget.configure(state="disabled")

    def _clear_tool_cache(self) -> None:
        if self.process is not None or self.worker_starting or self.run_jobs:
            messagebox.showinfo(
                "暂不能清理缓存",
                "任务队列运行期间不能重置当前工具会话。",
                parent=self.root,
            )
            return
        if not messagebox.askyesno(
                "重置工具会话",
                "清理缓存会同时清空所有页面已填写的目录和参数、硬盘清单、"
                "运行日志与三条进度，并返回 ENV-01 初始页面。\n\n"
                "正式数据库、存储 ZIP 和报告不会被删除。确定继续吗？",
                icon="warning", parent=self.root):
            return
        session_count = (
            clear_session_tool_cache(self.detected_tools)
            + len(self.manual_tool_paths)
        )
        self.manual_tool_paths.clear()
        disk = clean_project_caches()
        self.saved_values.clear()
        self.storage_disk_choices = ()
        self.storage_disk_options = ()
        self.environment_missing_names = ()
        self.missing_installable_tools = ()
        if self.mini_mode:
            self._leave_mini_mode()
        self._set_task_toolbar_expanded(True)
        self._set_settings_expanded(True)
        self._set_progress_expanded(False)
        self._set_log_expanded(False)
        self._set_command_preview_expanded(False)
        self._clear_log()
        self._refresh_tool_path_menu_labels()
        self._select_task("env_check", save_current=False)
        self._set_status("就绪")

        removed = (
            session_count + len(disk.directories) + len(disk.files)
        )
        summary = (
            "工具界面已恢复为首次启动状态。\n"
            f"已清理 {removed} 项可重建缓存。"
        )
        if disk.errors:
            messagebox.showwarning(
                "工具已重置，部分缓存未清理",
                summary + "\n\n" + "\n".join(disk.errors),
                parent=self.root,
            )
        else:
            messagebox.showinfo(
                "工具已重置", summary, parent=self.root)

    def _append_log(self, text: str, tag: str | None = None) -> None:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        for widget in self._active_log_widgets():
            widget.configure(state="normal")
            widget.insert("end", text, tag or ())
            line_count = int(widget.index("end-1c").split(".")[0])
            if line_count > 10_000:
                widget.delete("1.0", "1000.0")
            widget.see("end")
            widget.configure(state="disabled")

    def _set_status(self, text: str, colour: str | None = None) -> None:
        background = status_badge_background(self.task.key, colour)
        self.status_label.configure(
            text=text, bg=background, fg="white")

    def _refresh_tool_cache_labels(self) -> None:
        self.clear_cache_button.configure(
            state=(
                "normal"
                if self.process is None
                and not self.worker_starting
                and not self.run_jobs
                else "disabled"
            ))
        self._refresh_environment_actions()

    def _refresh_environment_actions(self) -> None:
        action_state = (
            "normal"
            if self.process is None
            and not self.worker_starting
            and not self.run_jobs
            else "disabled"
        )
        for tool_name, button in self.install_tool_buttons.items():
            button.configure(
                text=f"安装 {_TOOL_DISPLAY_NAMES[tool_name]}",
                state=action_state,
            )
        storage_button = getattr(self, "storage_detect_button", None)
        if storage_button is not None:
            storage_button.configure(state=action_state)
        if hasattr(self, "admin_mode_switch"):
            already_admin = bool(getattr(self, "is_administrator", False))
            self.admin_mode_switch.set_mode(
                value=already_admin,
                enabled=(
                    action_state == "normal"
                    and os.name == "nt"
                    and not already_admin
                ),
            )
        self.root.after_idle(self._layout_action_buttons)

    def _apply_environment_inventory(
        self, payload: dict[str, object],
    ) -> None:
        tools = payload.get("tools")
        if isinstance(tools, dict):
            self._cache_detected_tools({"tools": tools})
        raw_missing = payload.get("missing")
        missing_names: list[str] = []
        installable: list[str] = []
        if isinstance(raw_missing, list):
            for item in raw_missing:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "")
                if name not in _TOOL_DISPLAY_NAMES:
                    continue
                if name not in missing_names:
                    missing_names.append(name)
                if (item.get("installable") is True
                        and name in _INSTALLABLE_TOOL_PACKAGES
                        and name not in installable):
                    installable.append(name)
        self.environment_missing_names = tuple(missing_names)
        self.missing_installable_tools = tuple(installable)
        self._refresh_tool_cache_labels()

    def _apply_storage_inventory(
        self, payload: dict[str, object],
    ) -> None:
        rebuild_form = (
            getattr(getattr(self, "task", None), "key", None)
            == "storage_collect"
        )
        if rebuild_form and getattr(self, "values", None):
            self.saved_values["storage_collect"] = self._collect_values()
        self.storage_disk_options = storage_disk_options(
            payload.get("targets"))
        self.storage_disk_choices = tuple(
            (option.display, option.value)
            for option in self.storage_disk_options
            if option.selectable
        )
        self.saved_values.setdefault("storage_collect", {}).pop(
            "disk_number", None)
        if rebuild_form:
            self._build_form()

    def _restore_storage_selection_after_detection(self) -> None:
        """检测成功后返回硬盘选择区；失败路径保留日志供诊断。"""
        if self.mini_mode:
            self._leave_mini_mode()
        self._set_settings_expanded(True)
        self._set_progress_expanded(False)
        self._set_log_expanded(False)
        found_count = len(self.storage_disk_options)
        selectable_count = sum(
            option.selectable for option in self.storage_disk_options)
        if selectable_count:
            self._set_status(
                f"硬盘检测完成：请选择 {selectable_count} 块可登记硬盘。",
                _SUCCESS,
            )
            title = "硬盘检测完成"
            message = (
                f"已识别 {found_count} 块物理硬盘，其中 {selectable_count} 块"
                "可登记。\n\n已返回任务设置，请选择需要登记的硬盘；点击"
                "“开始任务”后会再次收起设置并展开进度与日志。"
            )
            show_dialog = messagebox.showinfo
        else:
            self._set_status(
                "硬盘检测完成，但没有找到可登记的硬盘。", _WARNING)
            title = "没有可登记的硬盘"
            message = (
                f"已识别 {found_count} 块物理硬盘，但没有硬盘同时满足联机"
                "且已关联 smartctl 的登记条件。\n\n已返回任务设置，可查看"
                "硬盘清单中的具体原因或重新检测。"
            )
            show_dialog = messagebox.showwarning

        def finish_transition() -> None:
            try:
                if not self.root.winfo_exists():
                    return
                self._position_form_scroll(0.0)
                show_dialog(title, message, parent=self.root)
            except tk.TclError:
                return

        self.root.after_idle(finish_transition)

    def _cache_detected_tools(self, payload: dict[str, object]) -> None:
        tools = payload.get("tools")
        if not isinstance(tools, dict):
            return
        changed = False
        for name, raw_info in tools.items():
            if name not in _TOOL_FIELD_BY_NAME or not isinstance(raw_info, dict):
                continue
            path = str(raw_info.get("path") or "").strip()
            if not path or raw_info.get("verified") is not True:
                continue
            self.detected_tools[name] = {
                "path": path,
                "version": str(raw_info.get("version") or ""),
                "resolution": str(
                    raw_info.get("resolution") or "auto_discovery"),
                "verified": True,
                "source_task": self.process_task_key or self.task.key,
            }
            changed = True
        if changed:
            self._refresh_tool_cache_labels()
            self._update_preview()

    def _stop_work_progress(self) -> None:
        if self._work_progress_indeterminate:
            self.progress_work_bar.stop()
            self._work_progress_indeterminate = False

    def _set_work_indeterminate(self) -> None:
        self._stop_work_progress()
        self.progress_work_bar.configure(
            mode="indeterminate", maximum=100, value=0,
            style="Work.Horizontal.TProgressbar",
        )
        self.progress_work_bar.start(12)
        self._work_progress_indeterminate = True
        self.progress_percent_label.configure(text="…", fg=_GREEN_DARK)

    def _set_work_fraction(self, value: float, *,
                           style: str = "Work") -> None:
        self._stop_work_progress()
        value = max(0.0, min(100.0, float(value)))
        self.progress_work_bar.configure(
            mode="determinate", maximum=100, value=value,
            style=f"{style}.Horizontal.TProgressbar",
        )
        self.progress_percent_label.configure(
            text=f"{value:.0f}%",
            fg=_DANGER if style == "Danger" else
            _WARNING if style == "Warning" else _GREEN_DARK,
        )

    def _reset_progress(self, task_title: str) -> None:
        self._stop_work_progress()
        self.current_stage_index = 0
        self.current_stage_total = 0
        self.progress_target_label.configure(text="尚未选择", fg=_MUTED)
        self.queue_progress_bar.configure(
            mode="determinate", maximum=100, value=0,
            style="Queue.Horizontal.TProgressbar",
        )
        self.queue_detail_label.configure(text="等待队列", fg=_MUTED)
        self.queue_percent_label.configure(text="0%", fg=_GREEN_DEEP)
        self.progress_stage_bar.configure(
            mode="determinate", maximum=100, value=0,
            style="Stage.Horizontal.TProgressbar",
        )
        self.progress_work_bar.configure(
            mode="determinate", maximum=100, value=0,
            style="Work.Horizontal.TProgressbar",
        )
        self.progress_stage_label.configure(
            text=f"{task_title} · 等待开始", fg=_MUTED)
        self.progress_detail_label.configure(text="尚未运行", fg=_MUTED)
        self.progress_percent_label.configure(text="0%", fg=_GREEN_DARK)

    def _queue_total(self) -> int:
        return max(1, len(self.run_jobs))

    def _queue_prefix(self) -> str:
        total = len(self.run_jobs)
        if total <= 0 or self.run_job_index < 0:
            return ""
        label = self.run_jobs[self.run_job_index].label
        return f"队列 {self.run_job_index + 1}/{total} · {label} · "

    def _prepare_queue_progress(self) -> None:
        total = len(self.run_jobs)
        self.queue_progress_bar.configure(
            mode="determinate", maximum=100, value=0,
            style="Queue.Horizontal.TProgressbar",
        )
        self.queue_detail_label.configure(
            text=f"0/{max(1, total)} · 队列已准备",
            fg=_MUTED,
        )
        self.queue_percent_label.configure(text="0%", fg=_GREEN_DEEP)

    def _update_queue_progress(
        self, current_fraction: float = 0.0, detail: str | None = None,
    ) -> None:
        total = len(self.run_jobs)
        if total <= 0:
            return
        completed = max(0, self.run_job_index)
        value = queue_progress_fraction(
            completed, total, current_fraction)
        if detail is None:
            if 0 <= self.run_job_index < total:
                label = self._short_progress_text(
                    self.run_jobs[self.run_job_index].label, 74)
                detail = f"{self.run_job_index + 1}/{total} · {label}"
            else:
                detail = f"0/{total} · 队列已准备"
        self.queue_progress_bar.configure(
            mode="determinate", maximum=100, value=value,
            style="Queue.Horizontal.TProgressbar",
        )
        self.queue_detail_label.configure(text=detail, fg=_TEXT)
        self.queue_percent_label.configure(
            text=f"{value:.0f}%", fg=_GREEN_DEEP)

    def _begin_progress(self) -> None:
        self.current_stage_index = 0
        self.current_stage_total = 0
        self._hide_current_file()
        self_test = self.process_task_key == _PROJECT_SELF_TEST_KEY
        installing = self.process_task_key == _DEPENDENCY_INSTALL_KEY
        detecting_storage = self.process_task_key == "storage_list"
        title = (
            "DAISY功能自检" if self_test else
            "安装缺失工具" if installing else
            "检测物理硬盘" if detecting_storage else
            task_display_title(self.task.key)
        )
        self.progress_stage_bar.configure(
            mode="determinate", maximum=100, value=0,
            style="Stage.Horizontal.TProgressbar",
        )
        self._update_queue_progress(0.0)
        self.progress_stage_label.configure(
            text=f"{title} · 正在启动",
            fg=_GREEN_DARK,
        )
        self.progress_detail_label.configure(
            text=(
                "正在运行 unittest；详细结果见实时日志…"
                if self_test else
                "正在等待 WinGet 输出…"
                if installing else
                "正在查询 Windows 存储接口与 smartctl…"
                if detecting_storage else
                "等待任务报告阶段…"
            ),
            fg=_MUTED,
        )
        self._set_work_indeterminate()

    @staticmethod
    def _short_progress_text(value: object, limit: int = 110) -> str:
        text = str(value or "")
        return text if len(text) <= limit else text[:limit - 1] + "…"

    @staticmethod
    def _middle_progress_text(value: object, limit: int = 110) -> str:
        text = str(value or "")
        if len(text) <= limit:
            return text
        left = max(1, (limit - 1) // 2)
        right = max(1, limit - left - 1)
        return text[:left] + "…" + text[-right:]

    def _set_current_file(self, value: object) -> None:
        full_text = str(value or "").strip()
        if not full_text:
            self._hide_current_file()
            return
        self.current_file_label.configure(
            text=self._middle_progress_text(full_text))
        self.current_file_tooltip.text = full_text
        self.current_file_title_label.grid(
            row=1, column=0, sticky="w", padx=(0, 10), pady=(0, 7))
        self.current_file_label.grid(
            row=1, column=1, columnspan=2, sticky="ew", pady=(0, 7))

    def _hide_current_file(self) -> None:
        if not hasattr(self, "current_file_label"):
            return
        self.current_file_title_label.grid_remove()
        self.current_file_label.grid_remove()
        self.current_file_label.configure(text="")
        self.current_file_tooltip.text = ""

    def _send_scan_control(
        self,
        action: str,
        *,
        worker_pid: int | None = None,
        decision: str | None = None,
    ) -> int | None:
        """只向当前 GUI 精确持有的扫描子进程 stdin 写入控制消息。"""
        process = self.process
        if (self.process_task_key not in _SCAN_TASK_KEYS
                or process is None or process.stdin is None
                or process.stdin.closed):
            return None
        sequence = self.scan_control_sequence + 1
        try:
            encoded = dbrun.encode_control_command(dbrun.ControlCommand(
                sequence=sequence,
                action=action,
                worker_pid=worker_pid,
                decision=decision,
            ))
            process.stdin.write(encoded)
            process.stdin.flush()
        except (BrokenPipeError, OSError, ValueError) as exc:
            self._append_log(f"控制请求发送失败：{exc}\n", "error")
            self._set_status("无法向当前扫描发送控制请求；任务仍保持原状态。", _DANGER)
            return None
        self.scan_control_sequence = sequence
        return sequence

    def _close_scan_control_input(self) -> None:
        """关闭父端持有的当前扫描 stdin，使终态子进程可立即退出。"""
        process = self.process
        if process is None or process.stdin is None or process.stdin.closed:
            return
        try:
            process.stdin.close()
        except OSError:
            pass

    def _pause_or_continue_scan(self) -> None:
        if self.scan_control_state == "paused":
            action, pending, label = "continue", "resume_requested", "继续"
        elif self.scan_control_state == "running":
            action, pending, label = "pause", "pause_requested", "暂停"
        else:
            return
        previous = self.scan_control_state
        if self._send_scan_control(action) is None:
            return
        self.scan_control_previous_state = previous
        self.scan_control_state = pending
        self._set_status(f"已请求{label}；正在等待安全边界确认…", _WARNING)
        self._refresh_scan_controls()

    def _request_save_scan_progress(self) -> bool:
        if self.scan_control_state not in (
                "running", "pause_requested", "paused", "resume_requested"):
            return False
        previous = self.scan_control_state
        if self._send_scan_control("save_exit") is None:
            return False
        self.scan_control_previous_state = previous
        self.save_exit_requested = True
        self.scan_control_state = "save_exit_requested"
        self._set_status("正在安全保存进度并结束当前扫描…", _WARNING)
        self._refresh_scan_controls()
        return True

    def _save_scan_progress(self) -> None:
        if self.process_task_key not in _SCAN_TASK_KEYS:
            return
        detail = (
            "已完成工作会提交到 partial，当前文件会在恢复后从安全边界重做；"
            "本次任务进程将结束，DAISY 下次启动会显示恢复入口。"
        )
        if len(self.run_jobs) > 1:
            detail += "队列中尚未开始的目录也会取消。"
        if not messagebox.askyesno(
                "保存进度并退出任务",
                detail + "\n\n确定继续吗？",
                icon="warning", parent=self.root):
            return
        self._request_save_scan_progress()

    def _close_timeout_dialog(self) -> None:
        dialog = self.timeout_dialog
        self.timeout_dialog = None
        self.timeout_dialog_label = None
        self.timeout_worker_pid = None
        if dialog is None:
            return
        try:
            if dialog.winfo_exists():
                dialog.destroy()
        except tk.TclError:
            pass

    def _resolve_timeout_dialog(self, decision: str) -> None:
        worker_pid = self.timeout_worker_pid
        if worker_pid is None:
            self._close_timeout_dialog()
            return
        if self._send_scan_control(
                "timeout_decision",
                worker_pid=worker_pid,
                decision=decision,
        ) is not None:
            labels = {
                "continue_waiting": "继续等待",
                "skip_and_record": "跳过并记录",
                "stop_and_resume": "停止并保留续传",
            }
            self._append_log(
                f"已向当前哈希 worker 请求：{labels[decision]}。\n",
                "warning",
            )
        self._close_timeout_dialog()

    def _show_timeout_dialog(self, payload: dict[str, object]) -> None:
        try:
            worker_pid = int(payload.get("worker_pid") or 0)
        except (TypeError, ValueError):
            return
        if worker_pid <= 0:
            return
        file_name = str(payload.get("file") or "未知文件")
        threshold = payload.get("threshold_seconds") or "?"
        count = payload.get("threshold_count") or 1
        detail = (
            f"文件连续无进展达到 {threshold}s（第 {count} 次）：\n"
            f"{file_name}\n\n不选择时继续等待；本窗口不会阻塞主界面。"
        )
        if self.timeout_dialog is not None:
            if self.timeout_worker_pid == worker_pid:
                assert self.timeout_dialog_label is not None
                self.timeout_dialog_label.configure(text=detail)
                self.timeout_dialog.deiconify()
                self.timeout_dialog.lift()
                return
            self._close_timeout_dialog()

        dialog = tk.Toplevel(self.root)
        self.timeout_dialog = dialog
        self.timeout_worker_pid = worker_pid
        dialog.title("哈希读取等待")
        dialog.configure(bg=_SURFACE)
        dialog.transient(self.root)
        dialog.protocol("WM_DELETE_WINDOW", self._close_timeout_dialog)
        label = tk.Label(
            dialog, text=detail, bg=_SURFACE, fg=_TEXT,
            font=self._font_tuple(9), justify="left", anchor="w",
            wraplength=520, padx=16, pady=14,
        )
        self.timeout_dialog_label = label
        label.pack(fill="x")
        attach_tooltip(label, file_name)
        actions = tk.Frame(dialog, bg=_SURFACE)
        actions.pack(fill="x", padx=16, pady=(0, 14))
        for button_text, decision in (
                ("继续等待", "continue_waiting"),
                ("跳过并记录", "skip_and_record"),
                ("停止并保留续传", "stop_and_resume")):
            ttk.Button(
                actions, text=button_text,
                style=("Stop.TButton" if decision == "stop_and_resume"
                       else "Secondary.TButton"),
                command=lambda value=decision:
                self._resolve_timeout_dialog(value),
            ).pack(side="left", padx=(0, 8))
        dialog.update_idletasks()
        work_area = _monitor_work_area_for_window(self.root)
        width = min(620, max(460, dialog.winfo_reqwidth()))
        height = min(320, max(190, dialog.winfo_reqheight()))
        width, height, x, y = fit_window_to_work_area(
            (width, height),
            (self.root.winfo_x() + 60, self.root.winfo_y() + 80),
            work_area,
        )
        dialog.geometry(_window_geometry_string(width, height, x, y))

    def _apply_scan_control_receipt(
        self, payload: dict[str, object],
    ) -> None:
        action = str(payload.get("action") or "")
        accepted = payload.get("accepted") is True
        if accepted:
            return
        reason = str(payload.get("reason") or "rejected")
        self._append_log(
            f"扫描控制未被接受：{action}（{reason}）。\n", "warning")
        if action in ("pause", "continue"):
            self.scan_control_state = self.scan_control_previous_state
        elif action == "save_exit":
            self.save_exit_requested = False
            self.close_after_stop = False
            self.scan_control_state = self.scan_control_previous_state
        elif action == "stop":
            self.stop_requested = False
            self.scan_control_state = self.scan_control_previous_state
        self._set_status("控制请求未生效；请查看运行日志。", _WARNING)
        self._refresh_scan_controls()

    def _apply_gui_event(self, payload: dict[str, object]) -> None:
        event_name = payload.get("event")
        if event_name == "control_receipt":
            self._apply_scan_control_receipt(payload)
            return
        if event_name == "control_rejected":
            self._append_log(
                "扫描控制消息被拒绝："
                f"{payload.get('detail') or payload.get('code') or '未知原因'}\n",
                "warning",
            )
            return
        if event_name == "run_paused":
            self.scan_control_state = "paused"
            self._set_status("扫描已在安全边界暂停。", _WARNING)
            self._refresh_scan_controls()
            return
        if event_name == "run_resumed":
            self.scan_control_state = "running"
            self._set_status(f"{self._queue_prefix()}扫描已继续运行。")
            self._refresh_scan_controls()
            return
        if event_name == "run_saved":
            self.save_exit_requested = True
            self.scan_control_state = "saved"
            self._set_status("扫描进度已安全保存，正在结束本任务进程。", _WARNING)
            self._close_scan_control_input()
            self._refresh_scan_controls()
            return
        if event_name == "run_stopped":
            self.stop_requested = True
            self.scan_control_state = "stopped"
            self._set_status("扫描已安全停止，正在结束本任务进程。", _WARNING)
            self._close_scan_control_input()
            self._refresh_scan_controls()
            return
        if event_name == "current_item":
            self._set_current_file(payload.get("item"))
            return
        if event_name == "threshold_reached":
            self._show_timeout_dialog(payload)
            return
        if event_name in ("threshold_decided", "stall_decided"):
            try:
                worker_pid = int(payload.get("worker_pid") or 0)
            except (TypeError, ValueError):
                worker_pid = 0
            keep_open = (
                event_name == "threshold_decided"
                and payload.get("decision") == "continue_waiting"
            )
            if worker_pid == self.timeout_worker_pid and not keep_open:
                self._close_timeout_dialog()
            return
        if event_name in ("stage_finished", "stage_skipped"):
            if payload.get("stage") == "hash":
                self._close_timeout_dialog()
            return
        if event_name == "run_result":
            self._close_scan_control_input()
            self.scan_run_result = dict(payload)
            state = str(payload.get("state") or "")
            task_key = self.process_task_key or self.task.key
            if state == "save_exit":
                partial = str(payload.get("partial") or "")
                self.save_exit_requested = True
                self.scan_control_state = "saved"
                self._add_recovery_scan(task_key, partial)
            elif state == "stopped":
                self.stop_requested = True
                self.scan_control_state = "stopped"
            elif state == "published":
                self.scan_control_state = "published"
                if self.run_jobs and self.run_job_index >= 0:
                    resume = str(self.run_jobs[
                        self.run_job_index].values.get("resume") or "")
                    if resume:
                        self._remove_recovery_scan(resume)
            self._refresh_scan_controls()
            return
        if event_name in ("run_failed", "run_interrupted"):
            self._close_scan_control_input()
        if event_name == "environment_inventory":
            self._apply_environment_inventory(payload)
            return
        if event_name == "tools_detected":
            self._cache_detected_tools(payload)
            return
        if event_name == "storage_inventory":
            self._apply_storage_inventory(payload)
            return
        if event_name == "progress_start":
            stage_idx = max(1, int(payload.get("stage_idx") or 1))
            stage_total = max(stage_idx, int(payload.get("stage_total") or 1))
            self.current_stage_index = stage_idx
            self.current_stage_total = stage_total
            name = self._short_progress_text(payload.get("name") or "处理中")
            task_fraction = (stage_idx - 1) / stage_total
            self.progress_stage_bar.configure(
                mode="determinate", maximum=100,
                value=task_fraction * 100,
                style="Stage.Horizontal.TProgressbar",
            )
            self._update_queue_progress(task_fraction)
            self.progress_stage_label.configure(
                text=f"阶段 {stage_idx}/{stage_total} · {name}",
                fg=_GREEN_DARK,
            )
            self.progress_detail_label.configure(text="正在处理…", fg=_MUTED)
            self._set_work_indeterminate()
            return
        if event_name == "progress_update":
            detail, fraction = progress_detail(payload)
            self.progress_detail_label.configure(
                text=self._short_progress_text(detail), fg=_TEXT)
            if fraction is None:
                if not self._work_progress_indeterminate:
                    self._set_work_indeterminate()
            else:
                self._set_work_fraction(fraction)
                if self.current_stage_total:
                    task_fraction = (
                        self.current_stage_index - 1 + fraction / 100
                    ) / self.current_stage_total
                    self._update_queue_progress(task_fraction)
            return
        if event_name in ("progress_finish", "progress_skip"):
            stage_idx = max(1, int(payload.get("stage_idx") or 1))
            stage_total = max(stage_idx, int(payload.get("stage_total") or 1))
            self.current_stage_index = stage_idx
            self.current_stage_total = stage_total
            name = self._short_progress_text(payload.get("name") or "阶段")
            task_fraction = stage_idx / stage_total
            self.progress_stage_bar.configure(
                mode="determinate", maximum=100,
                value=task_fraction * 100,
                style="Stage.Horizontal.TProgressbar",
            )
            self._update_queue_progress(task_fraction)
            if event_name == "progress_skip":
                detail = "已跳过：" + str(payload.get("reason") or "当前配置")
            else:
                detail = str(payload.get("summary") or "完成")
                elapsed = payload.get("elapsed")
                if elapsed is not None:
                    detail += f" · 用时 {_format_duration(elapsed)}"
            self.progress_stage_label.configure(
                text=f"阶段 {stage_idx}/{stage_total} · {name}",
                fg=_GREEN_DARK,
            )
            self.progress_detail_label.configure(
                text=self._short_progress_text(detail), fg=_TEXT)
            self._set_work_fraction(100)

    def _finish_progress(self, returncode: int | None, elapsed: float) -> None:
        self._stop_work_progress()
        self_test = self.process_task_key == _PROJECT_SELF_TEST_KEY
        installing = self.process_task_key == _DEPENDENCY_INSTALL_KEY
        if self.save_exit_requested:
            style, colour, detail = (
                "Warning", _WARNING,
                f"进度已保存，可在下次启动后恢复 · {_format_duration(elapsed)}")
            self.progress_work_bar.configure(
                style=f"{style}.Horizontal.TProgressbar")
            self.progress_percent_label.configure(text="已保存", fg=colour)
        elif returncode == 0 and not self.stop_requested:
            style, colour, detail = (
                "Success", _SUCCESS,
                (
                    f"DAISY 功能自检通过 · 总用时 {_format_duration(elapsed)}"
                    if self_test else
                    f"安装命令完成 · 用时 {_format_duration(elapsed)}"
                    if installing else
                    f"任务完成 · 总用时 {_format_duration(elapsed)}"
                ))
            self.progress_stage_bar.configure(value=100)
            self._set_work_fraction(100, style=style)
            self.progress_percent_label.configure(text="完成", fg=colour)
        elif (returncode == 1 and not self.stop_requested
              and not self_test and not installing):
            style, colour, detail = (
                "Warning", _WARNING,
                f"任务完成，但结果需要检查 · {_format_duration(elapsed)}")
            self.progress_stage_bar.configure(value=100)
            self._set_work_fraction(100, style=style)
            self.progress_percent_label.configure(text="检查", fg=colour)
        elif self.stop_requested:
            style, colour, detail = (
                "Warning", _WARNING,
                f"任务已停止 · {_format_duration(elapsed)}")
            self.progress_work_bar.configure(
                style=f"{style}.Horizontal.TProgressbar")
            self.progress_percent_label.configure(text="停止", fg=colour)
        else:
            style, colour, detail = (
                "Danger", _DANGER,
                f"任务失败 · {_format_duration(elapsed)}")
            self.progress_work_bar.configure(
                style=f"{style}.Horizontal.TProgressbar")
            self.progress_percent_label.configure(text="失败", fg=colour)
        self.progress_stage_bar.configure(
            style=f"{style}.Horizontal.TProgressbar")
        self.progress_stage_label.configure(text=detail, fg=colour)
        self.progress_detail_label.configure(text=detail, fg=colour)

    def _confirmation(self, values: dict[str, object],
                      job_count: int = 1) -> bool:
        warnings: list[str] = []
        active_keys = active_field_keys(self.task.key, values)
        if (self.task.key in _STG_ADMIN_TASKS
                and not self.is_administrator):
            warnings.append(
                "当前不是管理员模式。此功能需要管理员权限才能完整运行；"
                "建议取消本次运行，开启顶部管理员模式开关，并按提示重新启动 "
                "DAISY。若继续，将以非管理员模式运行，可能只得到不完整诊断"
                "或失败。"
            )
        target_summary = root_confirmation_text(self.task.key, values)
        if target_summary:
            warnings.append(target_summary)
        if self.task.key == "full_scan":
            warnings.append(
                "完整档案扫描可能持续几小时到几天；停止时可能保留可续传的 "
                "partial 快照。")
            if values.get("start_mode") == "resume":
                warnings.append("续传会沿用 partial 快照内记录的原扫描配置。")
            else:
                if values.get("hash_mode") == "full":
                    warnings.append("完整 SHA-256 会读取每个文件的全部内容。")
                if values.get("metadata_storage", "complete") == "normalized":
                    warnings.append(
                        "已选择基础元数据；仍会生成规范化字段，但不会保留"
                        "外部工具原始全文，未来重新解释能力会减少。")
                if not values.get("collect_file_id", True):
                    warnings.append(
                        "已关闭 NTFS File ID 采集；移动／重命名判定证据会减少。")
        if (self.task.key == "check_hash"
                and values.get("check_scope") == "full"):
            warnings.append("全量哈希核对会读取所有有基准哈希的文件。")
        if self.task.key == "storage_collect":
            disk_numbers = _lines(values.get("disk_number"))
            disk_list = "\n".join(
                f"• PhysicalDrive{number}" for number in disk_numbers)
            warnings.extend((
                "将对以下物理硬盘分别运行硬盘信息登记，并分别生成独立 ZIP："
                f"\n{disk_list}\n程序会在每次采集前重新核对设备身份。",
                "完整 smartctl 与 Windows 存储查询可能唤醒休眠硬盘，"
                "但不会启动自检或修改硬盘设置。",
                "生成的 ZIP 可能包含序列号、卷标、挂载路径、计算机名与"
                " BitLocker 状态，请勿未经检查公开分享。",
            ))
        if "force" in active_keys and values.get("force"):
            warnings.append("已启用文件名指纹缺失时的降级准入。")
        if not warnings:
            return True
        return messagebox.askyesno(
            "确认开始任务",
            "\n\n".join(warnings) + "\n\n确定继续吗？",
            icon="warning", parent=self.root,
        )

    def _begin_run_jobs(self, task_key: str, jobs: list[RunJob]) -> None:
        """锁定界面并启动同一任务的一项或多项目标。"""
        if task_key == "storage_list":
            self.storage_disk_choices = ()
            self.storage_disk_options = ()
            self.saved_values.setdefault("storage_collect", {}).pop(
                "disk_number", None)
        self.process_task_key = task_key
        self.stop_requested = False
        self.save_exit_requested = False
        self.scan_control_state = (
            "starting" if task_key in _SCAN_TASK_KEYS else "idle")
        self.scan_control_sequence = 0
        self.scan_run_result = None
        self.scan_control_previous_state = "idle"
        self.run_jobs = jobs
        self.run_job_index = -1
        self.run_results = []
        self.run_outcomes = []
        self.run_queue_started = time.monotonic()
        self.run_button.configure(state="disabled")
        for button in self.install_tool_buttons.values():
            button.configure(state="disabled")
        self.admin_mode_switch.set_mode(
            value=self.is_administrator, enabled=False)
        self._set_stop_state("disabled")
        self._refresh_scan_controls()
        self._set_recovery_card_state()
        self._set_settings_expanded(False)
        self._set_progress_expanded(True)
        self._set_log_expanded(True)
        self._prepare_queue_progress()
        self._refresh_mini_action()
        self._set_task_navigation_state("disabled")
        self._refresh_environment_actions()
        if task_key == _PROJECT_SELF_TEST_KEY:
            self._set_status(f"队列已准备：{len(jobs)} 项 · DAISY 功能自检。")
        else:
            self._set_status(f"队列已准备：{len(jobs)} 项。")
        self._start_next_job()

    def _run_self_test(self) -> None:
        if self.process is not None or self.run_jobs:
            return
        missing = project_self_test_missing_files()
        if missing:
            messagebox.showerror(
                "DAISY 功能自检不可用",
                "缺少正式测试文件：\n"
                + "\n".join("• " + name for name in missing),
                parent=self.root,
            )
            return
        confirmed = messagebox.askyesno(
            "运行 DAISY 功能自检",
            f"将运行 Script\\Test 中的全部 unittest；当前版本 {_version()}。"
            "\n\n"
            "测试不会使用 GUI 表单中的档案目录；夹具在系统临时目录中"
            "创建并清理，也不会访问真实硬盘。部分集成测试会调用 ExifTool、"
            "ffprobe 与 7-Zip，"
            "建议先完成 ENV-01 运行环境检测。\n\n确定继续吗？",
            icon="question", parent=self.root,
        )
        if not confirmed:
            return
        self._begin_run_jobs(
            _PROJECT_SELF_TEST_KEY, [RunJob("DAISY功能自检", {})])

    def _run_storage_inventory(self) -> None:
        """在硬盘信息登记页运行内部列盘步骤并刷新目标清单。"""
        if self.process is not None or self.worker_starting or self.run_jobs:
            return
        if (not self.is_administrator
                and not messagebox.askyesno(
                    "检测硬盘需要管理员权限",
                    "检测物理硬盘需要管理员权限才能获得完整结果。建议先开启"
                    "顶部管理员模式开关，并按提示重新启动 DAISY。\n\n"
                    "若继续，将以非管理员模式运行，可能只得到不完整诊断或"
                    "失败。确定继续吗？",
                    icon="warning", parent=self.root)):
            return
        self._save_current_values()
        self.storage_disk_choices = ()
        self.storage_disk_options = ()
        self.saved_values.setdefault("storage_collect", {}).pop(
            "disk_number", None)
        self._build_form()
        self._begin_run_jobs(
            "storage_list", [RunJob("检测物理硬盘", {})])

    def _install_tool(self, tool_name: str) -> None:
        """仅安装环境检测页明确选择的一项白名单工具。"""
        if self.process is not None or self.run_jobs or self.worker_starting:
            return
        if tool_name not in _INSTALLABLE_TOOL_PACKAGES:
            messagebox.showinfo(
                "没有可安装项",
                "该工具不在 GUI 安装白名单中。",
                parent=self.root,
            )
            return
        winget = discover_winget()
        if not winget:
            messagebox.showerror(
                "WinGet 不可用",
                "未找到 winget.exe。请先从 Microsoft Store 安装或更新"
                "“应用安装程序”（App Installer），然后重新打开 DAISY。",
                parent=self.root,
            )
            return
        display_name, package_id = _INSTALLABLE_TOOL_PACKAGES[tool_name]
        confirmed = messagebox.askyesno(
            f"下载并安装 {display_name}",
            f"将只通过 WinGet 的 winget 源下载并安装：\n"
            f"• {display_name}（{package_id}）"
            "\n\n即使已经检测到该工具，也可以独立执行；当前是否已安装"
            "及是否存在可用更新由 WinGet 判断。此操作会修改本机软件安装"
            "状态，并接受对应的"
            "源协议与软件包协议。安装输出会显示在运行日志中；"
            "完成后 DAISY 将自动重新检测环境。\n\n确定继续吗？",
            icon="question", parent=self.root,
        )
        if not confirmed:
            return
        job = RunJob(
            display_name,
            {"tool_name": tool_name, "winget_path": winget},
        )
        self._begin_run_jobs(_DEPENDENCY_INSTALL_KEY, [job])

    def _run(self) -> None:
        if self.process is not None or self.run_jobs:
            return
        if self.task.key == _PROJECT_SELF_TEST_KEY:
            self._run_self_test()
            return
        values = self._collect_values()
        effective, _tool_sources = self._effective_values(values)
        issues = validate_values(self.task.key, effective)
        if issues:
            messagebox.showerror(
                "参数需要修正", "\n".join("• " + issue for issue in issues),
                parent=self.root,
            )
            return
        jobs = build_run_jobs(self.task.key, values)
        if not self._confirmation(effective, len(jobs)):
            return
        self.saved_values[self.task.key] = values
        self._begin_run_jobs(self.task.key, jobs)

    def _start_next_job(self) -> None:
        next_index = self.run_job_index + 1
        if (self.stop_requested or self.save_exit_requested
                or next_index >= len(self.run_jobs)):
            return
        self.run_job_index = next_index
        job = self.run_jobs[next_index]
        task_key = self.process_task_key or self.task.key
        self.progress_target_label.configure(
            text=run_job_target_text(task_key, job), fg=_TEXT)
        self._hide_current_file()
        self._close_timeout_dialog()
        self.scan_control_sequence = 0
        self.scan_run_result = None
        if task_key in _SCAN_TASK_KEYS:
            self.scan_control_state = "starting"
            self.scan_control_previous_state = "starting"
            self._refresh_scan_controls()
        if task_key == _PROJECT_SELF_TEST_KEY:
            effective: dict[str, object] = {}
            tool_sources: dict[str, str] = {}
            command = project_self_test_command()
            command_text = project_self_test_preview()
        elif task_key == _DEPENDENCY_INSTALL_KEY:
            effective = {}
            tool_sources = {}
            command = dependency_install_command(
                str(job.values["tool_name"]),
                str(job.values["winget_path"]),
            )
            command_text = subprocess.list2cmdline(command)
        else:
            effective, tool_sources = merge_session_tool_paths(
                task_key, job.values, self.detected_tools,
                manual_paths=self.manual_tool_paths,
            )
            tool_args = build_tool_args(task_key, effective)
            command = [_console_python(), "-u", _MAIN] + tool_args
            command_text = preview_commands(task_key, effective)[0][1]
        total = len(self.run_jobs)
        action = (
            "正在运行 DAISY 功能自检"
            if task_key == _PROJECT_SELF_TEST_KEY else
            f"正在下载并安装 {job.label}"
            if task_key == _DEPENDENCY_INSTALL_KEY else
            f"正在启动 {job.label}"
        )
        self._set_status(
            f"队列 {next_index + 1}/{total} · {action}…")
        self._begin_progress()
        heading = f"队列 {next_index + 1}/{total}「{job.label}」"
        self._append_log(
            f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] "
            f"开始 {heading}：{command_text}\n",
            "meta",
        )
        self.worker_starting = True
        worker = threading.Thread(
            target=self._worker,
            args=(command, tool_sources, task_key in _SCAN_TASK_KEYS),
            daemon=True,
        )
        worker.start()

    def _worker(
        self,
        command: list[str],
        tool_sources: dict[str, str],
        control_stdin: bool,
    ) -> None:
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PYTHONUNBUFFERED"] = "1"
        if self.process_task_key in (
                _PROJECT_SELF_TEST_KEY, _DEPENDENCY_INSTALL_KEY):
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            env.pop("DAISY_GUI_PROGRESS", None)
            env.pop("DAISY_TOOL_SOURCES", None)
        else:
            env["DAISY_GUI_PROGRESS"] = "1"
            env["DAISY_TOOL_SOURCES"] = json.dumps(
                tool_sources, ensure_ascii=True, separators=(",", ":"))
        creationflags = 0
        if os.name == "nt":
            creationflags = (
                subprocess.CREATE_NEW_PROCESS_GROUP
                | subprocess.CREATE_NO_WINDOW
            )
        try:
            process = subprocess.Popen(
                command, cwd=_BASE,
                stdin=(subprocess.PIPE if control_stdin
                       else subprocess.DEVNULL),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                env=env, creationflags=creationflags,
            )
        except OSError as exc:
            self.events.put(("start_error", str(exc)))
            return
        self.events.put(("started", process, time.monotonic()))
        if self.stop_requested and not control_stdin:
            self._terminate_process(process)
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        stream_buffer = ""
        assert process.stdout is not None
        try:
            while True:
                block = process.stdout.read1(4096)
                if not block:
                    break
                output = decoder.decode(block)
                if output:
                    stream_buffer, parsed = parse_gui_stream(
                        stream_buffer, output)
                    for parsed_event in parsed:
                        self.events.put(parsed_event)
            tail = decoder.decode(b"", final=True)
            stream_buffer, parsed = parse_gui_stream(
                stream_buffer, tail, final=True)
            for parsed_event in parsed:
                self.events.put(parsed_event)
        finally:
            process.stdout.close()
        returncode = process.wait()
        if process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        self.events.put(("done", returncode, time.monotonic()))

    def _poll_events(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                kind = event[0]
                if kind == "started":
                    self.worker_starting = False
                    self.process = event[1]
                    self.process_started = event[2]
                    if self.process_task_key in _SCAN_TASK_KEYS:
                        self.scan_control_state = "running"
                        self._refresh_scan_controls()
                        if self.close_after_stop or self.save_exit_requested:
                            if not self._request_save_scan_progress():
                                self.close_after_stop = False
                                self.save_exit_requested = False
                        elif self.stop_requested:
                            if self._send_scan_control("stop") is None:
                                self.stop_requested = False
                                self.scan_control_state = "running"
                                self._refresh_scan_controls()
                        else:
                            self._set_status(
                                f"{self._queue_prefix()}运行中"
                                f"（PID {self.process.pid}）…")
                    elif self.close_after_stop:
                        self._set_stop_state("disabled")
                        self._set_status("正在停止任务，随后关闭窗口…", _WARNING)
                    else:
                        self._set_stop_state("normal")
                        self._set_status(
                            f"{self._queue_prefix()}运行中"
                            f"（PID {self.process.pid}）…")
                    self._refresh_mini_action()
                elif kind == "output":
                    self._append_log(event[1])
                elif kind == "gui_event":
                    self._apply_gui_event(event[1])
                elif kind == "start_error":
                    self.worker_starting = False
                    self.save_exit_requested = False
                    self._append_log(
                        f"启动失败：{event[1]}\n", "error")
                    self._finish_ui(None)
                elif kind == "done":
                    self.worker_starting = False
                    self._finish_ui(event[1], event[2])
        except queue.Empty:
            pass
        self.root.after(80, self._poll_events)

    def _finish_queue_progress(self) -> None:
        self._stop_work_progress()
        total = max(1, len(self.run_jobs))
        processed = len(self.run_results)
        successes = sum(code == 0 for code in self.run_results)
        failures = processed - successes
        elapsed = time.monotonic() - self.run_queue_started
        value = processed / total * 100
        if self.save_exit_requested:
            style, colour = "Warning", _WARNING
            detail = (
                f"队列已保存退出 · 已处理 {processed}/{total} · "
                f"用时 {_format_duration(elapsed)}")
        elif self.stop_requested:
            style, colour = "Warning", _WARNING
            detail = (
                f"队列已停止 · 已处理 {processed}/{total} · "
                f"用时 {_format_duration(elapsed)}")
        elif failures:
            style, colour = "Warning", _WARNING
            detail = (
                f"队列完成 · 成功 {successes} · 失败 {failures} · "
                f"用时 {_format_duration(elapsed)}")
            value = 100
        else:
            style, colour = "Success", _SUCCESS
            detail = (
                f"队列完成 · {successes}/{total} 成功 · "
                f"用时 {_format_duration(elapsed)}")
            value = 100
        self.queue_progress_bar.configure(
            mode="determinate", maximum=100, value=value,
            style=f"{style}.Horizontal.TProgressbar",
        )
        self.queue_percent_label.configure(
            text=f"{value:.0f}%", fg=colour)
        self.queue_detail_label.configure(text=detail, fg=colour)
        self.progress_stage_bar.configure(
            style=f"{style}.Horizontal.TProgressbar")
        self.progress_work_bar.configure(
            style=f"{style}.Horizontal.TProgressbar")
        if not self.stop_requested and not self.save_exit_requested:
            self.progress_stage_bar.configure(value=100)
            self._set_work_fraction(100, style=style)
        self.progress_percent_label.configure(
            text="已保存" if self.save_exit_requested else
            "停止" if self.stop_requested else
            "检查" if failures else "完成",
            fg=colour,
        )
        self.progress_stage_label.configure(text=detail, fg=colour)
        self.progress_detail_label.configure(text=detail, fg=colour)

    def _finalize_run(self, last_elapsed: float) -> None:
        total = max(1, len(self.run_jobs))
        returncode = self.run_results[-1] if self.run_results else None
        self_test = self.process_task_key == _PROJECT_SELF_TEST_KEY
        installing = self.process_task_key == _DEPENDENCY_INSTALL_KEY
        detecting_storage = self.process_task_key == "storage_list"
        saved = self.save_exit_requested or any(
            outcome == "save_exit" for outcome in self.run_outcomes)
        stopped = self.stop_requested
        storage_detection_succeeded = (
            detecting_storage and returncode == 0 and not stopped)
        offer_result_directory = should_offer_result_directory(
            self.run_results,
            stopped=stopped,
            maintenance=self_test or installing,
        ) and self.process_task_key in _RESULT_DIRECTORY_TASKS
        result_directory = (
            self._output_path() if offer_result_directory else ""
        )
        if total <= 1:
            if returncode is None:
                self._set_status(
                    (
                        "DAISY 功能自检未能启动。" if self_test else
                        "安装命令未能启动。" if installing else
                        "任务未能启动。"
                    ),
                    _DANGER,
                )
            elif saved:
                self._set_status(
                    "扫描进度已保存；下次启动可从恢复卡片继续。",
                    _WARNING,
                )
            elif self.stop_requested:
                self._set_status(
                    (
                        "DAISY 功能自检已停止；请检查日志。"
                        if self_test else
                        "安装已停止；请检查日志与本机软件状态。"
                        if installing else
                        "任务已停止；请检查日志与 partial 产物。"
                    ),
                    _WARNING,
                )
            elif returncode == 0:
                self._set_status(
                    (
                        "DAISY 功能自检通过。" if self_test else
                        "安装命令完成。" if installing else
                        "任务完成。"
                    ),
                    _SUCCESS,
                )
            elif returncode == 1 and not self_test and not installing:
                self._set_status("任务完成，但结果需要检查。", _WARNING)
            else:
                self._set_status(
                    (
                        "DAISY 功能自检失败；请查看日志。"
                        if self_test else
                        "安装失败；请查看日志。"
                        if installing else
                        "任务失败；请查看日志。"
                    ),
                    _DANGER,
                )
            self._finish_progress(returncode, last_elapsed)
        else:
            successes = sum(code == 0 for code in self.run_results)
            failures = sum(
                code not in (0, None) for code in self.run_results)
            start_failures = sum(
                code is None for code in self.run_results)
            failures += start_failures
            processed = len(self.run_results)
            if saved:
                remaining = max(0, total - processed)
                summary = (
                    f"队列已保存退出：已处理 {processed} 项，"
                    f"未启动 {remaining} 项。")
                colour, tag = _WARNING, "warning"
            elif self.stop_requested:
                remaining = max(0, total - processed)
                summary = (
                    f"队列已停止：成功 {successes} 项，"
                    f"未启动 {remaining} 项。")
                colour, tag = _WARNING, "warning"
            elif failures:
                summary = (
                    f"队列完成：成功 {successes} 项，失败 {failures} 项。")
                colour, tag = _WARNING, "warning"
            else:
                summary = f"队列完成：{successes}/{total} 项全部成功。"
                colour, tag = _SUCCESS, "success"
            self._append_log(f"\n{summary}\n", tag)
            self._set_status(summary, colour)
            self._finish_queue_progress()

        self.run_button.configure(
            state=(
                "disabled"
                if (self.task.key == _PROJECT_SELF_TEST_KEY
                    and project_self_test_missing_files())
                else "normal"
            ))
        self._set_stop_state("disabled")
        self.scan_control_state = "idle"
        self._refresh_scan_controls()
        self._set_task_navigation_state("normal")
        self.process_task_key = None
        self.stop_requested = False
        self.save_exit_requested = False
        self.run_jobs = []
        self.run_job_index = -1
        self.run_results = []
        self.run_outcomes = []
        self.run_queue_started = 0.0
        self.worker_starting = False
        self.scan_run_result = None
        self.scan_control_sequence = 0
        self.scan_control_previous_state = "idle"
        self._hide_current_file()
        self._close_timeout_dialog()
        self._refresh_mini_action()
        self._refresh_tool_cache_labels()
        self._set_recovery_card_state()
        if storage_detection_succeeded:
            self._restore_storage_selection_after_detection()
        if self.close_after_stop:
            self.close_after_stop = False
            self.root.after_idle(self._destroy_root)
        elif installing and not stopped:
            refresh_windows_process_path()
            self.environment_missing_names = ()
            self.missing_installable_tools = ()
            self._refresh_tool_cache_labels()
            self._append_log(
                "\nWinGet 安装流程已结束，正在重新检测本机环境…\n",
                "meta",
            )
            self.root.after(250, self._run)
        elif result_directory:
            self.root.after_idle(
                lambda path=result_directory:
                self._offer_open_result_directory(path))


    def _finish_ui(
        self, returncode: int | None, finished: float | None = None,
    ) -> None:
        elapsed = (
            (finished or time.monotonic()) - self.process_started
            if self.process_started else 0.0
        )
        outcome = (
            str(self.scan_run_result.get("state") or "")
            if self.scan_run_result is not None else None
        )
        self.process = None
        self.process_started = 0.0
        self._set_stop_state("disabled")
        self.run_results.append(returncode)
        self.run_outcomes.append(outcome)
        total = max(1, len(self.run_jobs))
        job = (
            self.run_jobs[self.run_job_index]
            if self.run_jobs and self.run_job_index >= 0 else None)
        self._update_queue_progress(
            1.0,
            f"已处理 {len(self.run_results)}/{total} · {job.label}"
            if job else f"已处理 {len(self.run_results)}/{total}",
        )
        self_test = self.process_task_key == _PROJECT_SELF_TEST_KEY
        item = (
            f"队列 {self.run_job_index + 1}/{total}「{job.label}」"
            if job else
            ("DAISY 功能自检" if self_test else "任务"))
        if returncode is None:
            self._append_log(f"\n{item}未能启动。\n", "error")
        elif outcome == "save_exit" or self.save_exit_requested:
            self._append_log(
                f"\n{item}已保存进度并退出（退出码 {returncode}，"
                f"用时 {elapsed:.1f}s）。\n", "warning")
        elif outcome == "stopped" or self.stop_requested:
            self._append_log(
                f"\n{item}已停止（退出码 {returncode}，"
                f"用时 {elapsed:.1f}s）。\n", "warning")
        elif returncode == 0:
            self._append_log(
                f"\n{item}完成（用时 {elapsed:.1f}s）。\n", "success")
        elif (returncode == 1 and total <= 1 and not self_test
              and self.process_task_key != _DEPENDENCY_INSTALL_KEY):
            self._append_log(
                f"\n{item}完成，但发现差异或异常（退出码 1，用时 "
                f"{elapsed:.1f}s）。\n", "warning")
        else:
            self._append_log(
                f"\n{item}失败（退出码 {returncode}，"
                f"用时 {elapsed:.1f}s）。\n", "error")

        self._refresh_tool_cache_labels()
        has_next = self.run_job_index + 1 < len(self.run_jobs)
        if (not self.stop_requested and not self.save_exit_requested
                and has_next):
            if returncode != 0:
                self._append_log("单项失败；继续运行下一目录。\n", "warning")
            self.root.after(80, self._start_next_job)
            return
        self._finalize_run(elapsed)

    def _stop(self) -> None:
        process = self.process
        if process is None:
            return
        if self.process_task_key == _PROJECT_SELF_TEST_KEY:
            prompt = (
                "这会中断当前 DAISY 功能自检；测试夹具仍会由测试清理流程处理。"
                "\n\n确定停止吗？"
            )
        elif self.process_task_key == _DEPENDENCY_INSTALL_KEY:
            prompt = (
                "这会中断当前 WinGet 进程；已经完成安装的软件不会回滚，"
                "尚未开始的工具会取消。\n\n确定停止吗？"
            )
        elif self.process_task_key == "storage_collect":
            prompt = (
                "这会中断当前只读硬盘采集，可能在存储档案目录留下可安全"
                "删除的 .partial.zip；不会修改硬盘。\n\n确定停止吗？"
            )
        elif self.process_task_key == "storage_list":
            prompt = (
                "这会中断当前只读硬盘检测；不会修改硬盘。"
                "\n\n确定停止吗？"
            )
        else:
            prompt = (
                "停止可能留下 partial、WAL、lock 或未完成报告；完整扫描通常可从 "
                "partial 快照续传。\n\n确定停止吗？"
            )
        if len(self.run_jobs) > 1:
            prompt = (
                "这会终止当前目录，并取消队列中尚未开始的目录。\n\n"
                + prompt
            )
        if not messagebox.askyesno(
            "确认停止任务", prompt,
            icon="warning", parent=self.root,
        ):
            return
        if self.process_task_key in _SCAN_TASK_KEYS:
            previous = self.scan_control_state
            if self._send_scan_control("stop") is None:
                return
            self.scan_control_previous_state = previous
            self.stop_requested = True
            self.scan_control_state = "stop_requested"
            self._set_stop_state("disabled")
            status = (
                "正在安全停止当前目录并取消剩余队列…"
                if len(self.run_jobs) > 1 else
                "正在安全停止并保留审计证据…"
            )
            self._set_status(status, _WARNING)
            self._refresh_scan_controls()
            return
        self.stop_requested = True
        self._set_stop_state("disabled")
        status = (
            "正在停止当前目录并取消剩余队列…"
            if len(self.run_jobs) > 1 else "正在请求停止…"
        )
        self._set_status(status, _WARNING)
        threading.Thread(
            target=self._terminate_process, args=(process,), daemon=True,
        ).start()

    def _terminate_process(self, process: subprocess.Popen[bytes]) -> None:
        try:
            if os.name == "nt":
                process.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                process.send_signal(signal.SIGINT)
            process.wait(timeout=5)
            return
        except (OSError, subprocess.TimeoutExpired):
            pass
        try:
            process.terminate()
            process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
            except OSError:
                pass

    def _on_close(self) -> None:
        process = self.process
        active = (
            process is not None
            or bool(self.run_jobs)
            or bool(getattr(self, "worker_starting", False))
        )
        if (active or getattr(
                self, "confirm_close_when_idle", True)) and not (
                messagebox.askyesno(
                    "确认退出",
                    "确定关闭 DAISY 吗？",
                    icon="question", parent=self.root,
                )):
            return
        if not active:
            self._save_gui_preferences()
            self._destroy_root()
            return

        scan_active = getattr(
            self, "process_task_key", None) in _SCAN_TASK_KEYS
        detail = (
            "关闭界面前会安全保存当前扫描进度、结束本任务进程并释放锁；"
            "下次启动会显示恢复入口。确定继续吗？"
            if scan_active else
            "关闭界面会停止当前任务，并可能留下未完成产物。确定继续吗？"
        )
        if len(self.run_jobs) > 1:
            detail = (
                ("关闭界面前会保存当前扫描进度，并取消队列中尚未启动的"
                 "目录；下次启动会显示当前 partial 的恢复入口。确定继续吗？")
                if scan_active else
                ("关闭界面会停止当前目录，并取消队列中尚未启动的目录；"
                 "也可能留下未完成产物。确定继续吗？")
            )
        if not messagebox.askyesno(
            "再次确认退出", detail,
            icon="warning", parent=self.root,
        ):
            return
        self._save_gui_preferences()
        self._set_stop_state("disabled")
        if process is not None:
            self.close_after_stop = True
            if scan_active:
                if not self._request_save_scan_progress():
                    self.close_after_stop = False
                return
            self.stop_requested = True
            self._set_status("正在停止任务，随后关闭窗口…", _WARNING)
            threading.Thread(
                target=self._terminate_process, args=(process,), daemon=True,
            ).start()
            return
        if self.worker_starting:
            self.close_after_stop = True
            if scan_active:
                self.save_exit_requested = True
                self._set_status("任务启动后将立即保存进度并关闭窗口…", _WARNING)
            else:
                self.stop_requested = True
                self._set_status("正在取消启动，随后关闭窗口…", _WARNING)
            return
        self.stop_requested = True
        self._destroy_root()

    def _destroy_root(self) -> None:
        """取消当前 Tk 解释器的定时器后销毁窗口，避免残留 Tcl 回调。"""
        try:
            pending = self.root.tk.call("after", "info")
            callback_ids = (
                pending if isinstance(pending, (tuple, list))
                else self.root.tk.splitlist(pending)
            )
            for callback_id in callback_ids:
                while isinstance(callback_id, (tuple, list)):
                    if not callback_id:
                        break
                    callback_id = callback_id[0]
                if not callback_id:
                    continue
                try:
                    self.root.tk.call(
                        "after", "cancel", str(callback_id))
                except (tk.TclError, TypeError):
                    pass
            self.root.destroy()
        except (AttributeError, tk.TclError, TypeError):
            try:
                self.root.destroy()
            except (AttributeError, tk.TclError):
                pass


def _enable_dpi_awareness() -> None:
    if os.name != "nt":
        return
    import ctypes
    try:
        # Per-Monitor V2：在 125%／150% 缩放及跨屏移动时保持文字清晰。
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(
                ctypes.c_void_p(-4)):
            return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass


def main() -> int:
    _enable_dpi_awareness()
    root = tk.Tk()
    DaisyApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
