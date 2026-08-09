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
from dataclasses import dataclass, replace


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
from tkinter import filedialog, font as tkfont, messagebox, ttk

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_BASE = os.path.dirname(_SCRIPT_DIR)
_LIB_DIR = os.path.join(_SCRIPT_DIR, "Lib")
_TEST_DIR = os.path.join(_SCRIPT_DIR, "Test")
_MAIN = os.path.join(_SCRIPT_DIR, "Script_DAISY_MAIN.py")
sys.path.insert(0, _LIB_DIR)

import Script_DAISY_Lib_DBS_01_Core as core
import Script_DAISY_Lib_DBS_02_Meta as metadata
import Script_DAISY_Lib_DBS_07_Parse as dbparse
import Script_DAISY_Lib_DBS_08_State as dbstate
import Script_DAISY_Lib_DBS_09_Run as dbrun
import Script_DAISY_Lib_ENV_01_Capabilities as envcap
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
_DEPENDENCY_VERSION_CHECK_KEY = "dependency_version_check"
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
    "Script_DAISY_Test_DBS_Verify_Tools.py",
    "Script_DAISY_Test_DBS_Verify_CLI.py",
    "Script_DAISY_Test_DBS_Verify_Compatibility.py",
    "Script_DAISY_Test_DBS_Diff_Compatibility.py",
    "Script_DAISY_Test_DBS_Verify_Raw.py",
    "Script_DAISY_Test_DBS_Raw.py",
    "Script_DAISY_Test_DBS_Raw_Evidence.py",
    "Script_DAISY_Test_DBS_Scan_Raw.py",
    "Script_DAISY_Test_DBS_Parse.py",
    "Script_DAISY_Test_DBS_Parse_Planning.py",
    "Script_DAISY_Test_DBS_Parse_Projection.py",
    "Script_DAISY_Test_DBS_Parse_Run.py",
    "Script_DAISY_Test_DBS_Parse_Human.py",
    "Script_DAISY_Test_DBS_Parse_CLI.py",
    "Script_DAISY_Test_DBS_State.py",
    "Script_DAISY_Test_DBS_Hash_Worker.py",
    "Script_DAISY_Test_DBS_Run.py",
    "Script_DAISY_Test_DBS_Tool_Recovery.py",
    "Script_DAISY_Test_GUI_Scan.py",
)
_PROJECT_GITHUB_URL = "https://github.com/SuzuranYe/DAISY"
_PROJECT_CONTACT = "151104858+SuzuranYe@users.noreply.github.com"
_MAX_ROOT_DIRECTORIES = 9
_LEGACY_SCAN_TASK_KEYS = frozenset(("full_scan", "quick_scan"))
_SCAN_TASK_KEYS = frozenset((*_LEGACY_SCAN_TASK_KEYS, "scan"))
_ROOT_BATCH_TASKS = _SCAN_TASK_KEYS
_CONTROL_TASK_KEYS = frozenset((*_SCAN_TASK_KEYS, "verify"))
_CONTROL_ACTION_LABELS = {
    "pause": "暂停",
    "continue": "继续",
    "save_exit": "保存并退出",
    "stop": "停止",
    "timeout_decision": "超时处置",
}
_CONTROL_REASON_LABELS = {
    "run_ended": "任务已经结束",
    "not_running": "任务当前未在运行",
    "not_paused": "任务当前未暂停",
    "not_valid_while_paused": "当前操作不能在暂停状态下执行",
    "action_already_decided": "已有控制请求等待处理",
    "paused_action_already_decided": "暂停后的下一步操作已经确定",
    "worker_or_decision_mismatch": "当前文件或处置选项已经变化",
    "invalid_decision": "处置选项无效",
    "verification_not_resumable": "核验任务不支持跨重启续传",
    "unsupported_action": "当前任务不支持该操作",
}
_CONTROL_REJECTION_LABELS = {
    "line_too_long": "控制消息超过长度上限",
    "unterminated_line": "控制消息缺少完整行边界",
    "invalid_message": "控制消息格式无效",
    "stale_sequence": "控制消息已经过期",
    "command_callback_failed": "控制消息处理失败",
    "queue_full": "控制消息队列已满",
    "stream_failed": "控制通道读取失败",
}
_LEGACY_TASK_PAGE_MAP = {
    "full_scan": "scan",
    "quick_scan": "scan",
    "check_hash": "verify",
    "check_format": "verify",
    "export_report": "parse_db",
}
_ROOT_BATCH_SEPARATE = "separate"
_ROOT_BATCH_COMBINED = "combined"
_DEFAULT_WINDOW_SIZE = (1920, 1080)
_WINDOW_WORK_MARGIN = (32, 40)
_UI_FONT_FAMILY = "Microsoft YaHei UI"
_UI_BODY_FONT_SIZE = 10
_UI_FONT_FAMILY_CANDIDATES = (
    "Microsoft YaHei UI", "Noto Sans SC", "Microsoft JhengHei UI",
    "Segoe UI",
)
_UI_FONT_SIZE_OPTIONS = (
    ("标准", 0), ("较大", 1), ("特大", 2),
)
_WINDOW_SIZE_OPTIONS = (
    ("1920 × 1080", (1920, 1080)),
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
    "ffprobe": ("ffprobe (FFmpeg)", "Gyan.FFmpeg"),
    "sevenzip": ("7-Zip", "7zip.7zip"),
    "smartctl": ("smartctl", "smartmontools.smartmontools"),
}
_INSTALLABLE_PYTHON_CAPABILITIES = {
    "rawpy": ("rawpy/LibRaw", "rawpy"),
}
_INSTALL_BUTTON_LABELS = {
    "exiftool": "ExifTool",
    "ffprobe": "ffprobe",
    "sevenzip": "7-Zip",
    "smartctl": "smartctl",
    "rawpy": "rawpy/LibRaw",
}
_ENVIRONMENT_BUTTON_LABELS = {
    "exiftool": "ExifTool",
    "ffprobe": "ffprobe",
    "sevenzip": "7-Zip",
    "powershell": "PowerShell",
    "smartctl": "smartctl",
    "rawpy": "rawpy/LibRaw",
}
_ENVIRONMENT_STATUS_ORDER = (
    "exiftool", "ffprobe", "sevenzip", "powershell", "smartctl", "rawpy",
)
_ENVIRONMENT_COLUMN_COUNT = len(_ENVIRONMENT_STATUS_ORDER)
_TASK_TOOL_NAMES = {
    "env_check": (
        "exiftool", "ffprobe", "sevenzip", "powershell", "smartctl"),
    "full_scan": ("exiftool", "ffprobe", "sevenzip", "powershell"),
    "scan": ("exiftool", "ffprobe", "sevenzip", "powershell"),
    "check_format": ("exiftool", "ffprobe", "sevenzip"),
    "check_hash": ("powershell",),
    "verify": ("exiftool", "ffprobe", "sevenzip"),
    "storage_list": ("smartctl", "powershell"),
    "storage_collect": ("smartctl", "powershell"),
}
_RESULT_DIRECTORY_TASKS = frozenset((
    "env_check", "full_scan", "quick_scan", "diff", "check_hash",
    "check_format", "export_report", "scan", "verify", "parse_db",
    "storage_collect",
))
_STG_ADMIN_TASKS = frozenset(("storage_list", "storage_collect"))
_PROJECT_CACHE_DIR_NAMES = frozenset((
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
))
_PROJECT_CACHE_FILE_SUFFIXES = (".pyc", ".pyo")
_CACHE_SCAN_EXCLUDED_DIR_NAMES = frozenset((
    ".git", ".venv", "venv", "node_modules", "output",
))


def raw_runtime_capability_status(
    capabilities: dict[str, dict[str, object]] | None,
) -> tuple[bool, str]:
    """返回 GUI 与 CLI 一致的 RAW 能力准入结果和直接可显示原因。"""
    raw = (
        capabilities.get(envcap.RAW_CAPABILITY_ID)
        if isinstance(capabilities, dict) else None
    )
    if not isinstance(raw, dict):
        return False, "尚未检测；请先运行「运行环境检测」"
    details = raw.get("details")
    available = (
        raw.get("state") == "available"
        and raw.get("available") is True
        and raw.get("isolated") is True
        and isinstance(details, dict)
        and details.get("worker_reaped") is True
        and bool(raw.get("version"))
    )
    if available:
        version = str(raw.get("version"))
        libraw = str(details.get("libraw_version") or "").strip()
        suffix = f"；LibRaw {libraw}" if libraw else ""
        return True, f"rawpy {version}{suffix}（解码能力可用）"
    state = str(raw.get("state") or "unavailable")
    reason = str(raw.get("reason") or "隔离能力证据不完整").strip()
    state_label = {
        "unavailable": "不可用",
        "incompatible": "版本或依赖不兼容",
        "crashed": "探测进程异常退出",
        "timeout": "探测超时",
    }.get(state, "状态未知")
    return False, f"{state_label}：{reason}"


def default_gui_preferences() -> dict[str, object]:
    """返回本地 GUI 用户配置默认值。"""
    return {
        "version": 3,
        "window_size": list(_DEFAULT_WINDOW_SIZE),
        "font_family": _UI_FONT_FAMILY,
        "font_size_delta": 0,
        "completion_sound_enabled": False,
        "result_directory_prompt_enabled": False,
        "last_task_key": "env_check",
        "manual_tool_paths": {},
        "task_options": {},
        "recovery_scans": [],
    }


def load_gui_preferences(
    path: str = _GUI_SETTINGS_PATH,
) -> dict[str, object]:
    """容错读取 GUI 用户配置；文件损坏时回到安全默认值。"""
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

    completion_sound = loaded.get("completion_sound_enabled")
    if isinstance(completion_sound, bool):
        preferences["completion_sound_enabled"] = completion_sound

    result_directory_prompt = loaded.get(
        "result_directory_prompt_enabled")
    if isinstance(result_directory_prompt, bool):
        preferences["result_directory_prompt_enabled"] = (
            result_directory_prompt)

    last_task_key = loaded.get("last_task_key")
    if isinstance(last_task_key, str):
        last_task_key = _LEGACY_TASK_PAGE_MAP.get(
            last_task_key, last_task_key)
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
            scan_mode = str(item.get("scan_mode") or "").strip().casefold()
            if task_key == "full_scan":
                scan_mode = "full"
            elif task_key == "quick_scan":
                scan_mode = "quick"
            if scan_mode not in ("full", "quick"):
                scan_mode = "full"
            partial = partial.strip()
            canonical = os.path.normcase(os.path.abspath(partial))
            if (not partial.lower().endswith(".partial.sqlite")
                    or not partial or canonical in seen_paths):
                continue
            validated.append({
                "task_key": "scan",
                "scan_mode": scan_mode,
                "partial": partial,
            })
            seen_paths.add(canonical)
        preferences["recovery_scans"] = validated
    preferences["manual_tool_paths"] = _validated_manual_tool_paths(
        loaded.get("manual_tool_paths"))
    preferences["task_options"] = _validated_task_options(
        loaded.get("task_options"))
    return preferences


def save_gui_preferences(
    preferences: dict[str, object], path: str = _GUI_SETTINGS_PATH,
) -> None:
    """以 UTF-8/LF 原子保存本地 GUI 用户配置。"""
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
_ACTION_GREEN = "#b7d5c9"
_AMBER = "#eca93b"
_AMBER_DARK = "#9a6519"
_AMBER_DEEP = "#70470f"
_AMBER_SOFT = "#f1ddb2"
_OLIVE = "#aebd70"
_OLIVE_DEEP = "#45552a"
_OLIVE_SOFT = "#dce3bd"
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
    "scan": (_GREEN_DARK, _GREEN_DEEP, _GREEN),
    "verify": (_GREEN_DARK, _GREEN_DEEP, _GREEN),
    "parse_db": (_GREEN_DARK, _GREEN_DEEP, _GREEN),
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


def should_play_completion_sound(
    returncodes: list[int | None] | tuple[int | None, ...],
    outcomes: list[str | None] | tuple[str | None, ...],
    *,
    task_key: str | None,
    stopped: bool,
    saved: bool,
) -> bool:
    """仅在整批业务任务正常结束后播放一次完成提示音。"""
    return (
        bool(returncodes)
        and task_key not in (
            None, "storage_list", _DEPENDENCY_VERSION_CHECK_KEY,
            _DEPENDENCY_INSTALL_KEY,
        )
        and not stopped
        and not saved
        and all(code in (0, 1) for code in returncodes)
        and not any(outcome in {
            "failed", "failed_recoverable", "save_exit", "stopped",
        } for outcome in outcomes)
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
    """硬盘清单中的一项；脱机或资料不完整的设备只展示、不可选择。"""

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
    """把 STG-11 事件转换为包含可用与不可用设备的稳定硬盘清单。"""
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
            ("／".join(explorer_names) or "无盘符或无卷标")[:64],
            model[:64],
            size_text,
        ))
        online = windows_available and disk.get("is_offline") is not True
        registrable = windows_available and smart_available
        if not windows_available:
            reason = "未取得 Windows 硬盘信息"
        elif not online:
            reason = "已脱机"
        elif not smart_available:
            reason = "无法读取 SMART 信息"
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
    """兼容旧调用：返回硬盘清单中联机且信息完整的设备。"""
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
    """把结构化进度转为紧凑说明与真实百分比。"""
    parts: list[str] = []
    done = payload.get("done")
    total = payload.get("total")
    bytes_done = payload.get("bytes_done")
    bytes_total = payload.get("bytes_total")

    if total not in (None, 0):
        parts.append(f"{int(done or 0):,}/{int(total):,} 项")
    elif done is not None:
        parts.append(f"{int(done):,} 项")

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
        parts.append(f"{float(rate):,.0f} 项／秒")

    eta = payload.get("eta")
    if eta is not None:
        parts.append(f"预计剩余 {_format_duration(eta)}")
    elapsed = payload.get("elapsed")
    if elapsed is not None:
        parts.append(f"已用时 {_format_duration(elapsed)}")
    source_errors = int(payload.get("source_error") or 0)
    tool_errors = int(payload.get("tool_error") or 0)
    if source_errors or tool_errors:
        if source_errors:
            parts.append(f"源文件问题 {source_errors:,}")
        if tool_errors:
            parts.append(f"工具故障 {tool_errors:,}")
    else:
        errors = int(payload.get("errors") or 0)
        if errors:
            parts.append(f"异常记录 {errors:,}")
    not_applicable = int(payload.get("not_applicable") or 0)
    if not_applicable:
        parts.append(f"不适用 {not_applicable:,}")
    skipped = int(payload.get("skipped") or 0)
    if skipped:
        parts.append(f"跳过 {skipped:,}")
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


def dependency_latest_version_command(
    tool_name: str, winget_path: str = "winget.exe",
) -> list[str]:
    """返回固定 WinGet 包的软件源版本查询命令，不执行安装。"""
    try:
        _display, package_id = _INSTALLABLE_TOOL_PACKAGES[tool_name]
    except KeyError as exc:
        raise ValueError(f"不允许查询未知工具：{tool_name}") from exc
    return [
        winget_path,
        "show",
        "--exact",
        "--id", package_id,
        "--source", "winget",
        "--accept-source-agreements",
        "--disable-interactivity",
    ]


def python_capability_install_command(
    capability_name: str, python_path: str | None = None,
) -> list[str]:
    """返回当前 Python 环境中固定白名单能力的 pip 安装命令。"""
    try:
        _display, package_name = _INSTALLABLE_PYTHON_CAPABILITIES[
            capability_name]
    except KeyError as exc:
        raise ValueError(
            f"不允许安装未知 Python 能力：{capability_name}") from exc
    return [
        python_path or _console_python(),
        "-m", "pip", "install",
        "--upgrade",
        "--upgrade-strategy", "only-if-needed",
        package_name,
    ]


def python_capability_latest_version_command(
    capability_name: str, python_path: str | None = None,
) -> list[str]:
    """返回固定 Python 包的软件源版本查询命令，不修改当前环境。"""
    try:
        _display, package_name = _INSTALLABLE_PYTHON_CAPABILITIES[
            capability_name]
    except KeyError as exc:
        raise ValueError(
            f"不允许查询未知 Python 能力：{capability_name}") from exc
    return [
        python_path or _console_python(),
        "-m", "pip", "index", "versions", package_name,
        "--disable-pip-version-check",
    ]


def _version_token(value: object) -> str | None:
    """验证软件源输出中的单个版本 token，拒绝标题和说明文本。"""
    token = str(value or "").strip().strip(",")
    if not re.fullmatch(
            r"[vV]?\d[0-9A-Za-z]*(?:[._+\-][0-9A-Za-z]+)*", token):
        return None
    return token


def parse_winget_latest_version(output: object) -> str | None:
    """从中／英文 ``winget show`` 输出读取默认返回的最新版本。"""
    text = re.sub(
        r"\x1b\[[0-?]*[ -/]*[@-~]", "", str(output or ""))
    lines = [
        line.replace("\b", "").strip()
        for line in text.replace("\r", "\n").split("\n")
    ]
    for line in lines:
        match = re.match(
            r"^(?:Version|版本)\s*[:：]\s*(\S+)",
            line, flags=re.IGNORECASE,
        )
        if match:
            version = _version_token(match.group(1))
            if version:
                return version
    for index, line in enumerate(lines[:-1]):
        if line.casefold() not in ("version", "版本"):
            continue
        next_index = index + 1
        while next_index < len(lines) and not lines[next_index]:
            next_index += 1
        if (next_index < len(lines)
                and re.fullmatch(r"-{3,}", lines[next_index])):
            next_index += 1
        while next_index < len(lines):
            if lines[next_index]:
                return _version_token(lines[next_index].split()[0])
            next_index += 1
    return None


def parse_pip_latest_version(output: object) -> str | None:
    """从 ``pip index versions`` 输出读取索引声明的最新版本。"""
    text = re.sub(
        r"\x1b\[[0-?]*[ -/]*[@-~]", "", str(output or ""))
    for pattern in (
        r"(?im)^\s*LATEST\s*:\s*(\S+)",
        r"(?im)^\s*rawpy\s*\(([^)]+)\)",
        r"(?im)^\s*Available versions\s*:\s*([^,\s]+)",
    ):
        match = re.search(pattern, text)
        if match:
            version = _version_token(match.group(1))
            if version:
                return version
    return None


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


@dataclass
class InstallVersionReport:
    """保存一次安装前后的版本证据，等待统一环境复检后再报告。"""

    tool_name: str
    display_name: str
    before_version: str
    latest_version: str | None = None
    install_returncode: int | None = None
    after_version: str | None = None
    inventory_received: bool = False


def run_job_target_text(task_key: str, job: RunJob) -> str:
    """返回进度区使用的完整当前目标；目录任务不缩写路径。"""
    field_key = {
        "full_scan": "roots",
        "quick_scan": "roots",
        "scan": "roots",
        "check_hash": "root_map",
        "check_format": "root_map",
        "verify": "root_map",
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
    if task_key in _SCAN_TASK_KEYS:
        resume = str(job.values.get("resume") or "").strip()
        if resume:
            return f"续传快照：{_absolute(resume)}"
    if task_key == "storage_collect":
        disk_number = str(job.values.get("disk_number") or "").strip()
        if disk_number:
            return f"PhysicalDrive{disk_number}"
    if task_key == "storage_list":
        return "本机硬盘"
    for field in (
            "database", "source_path", "snapshot", "old", "archive"):
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
_TOOL_PATH_HELP = (
    "未指定时，先使用已检测并验证的路径；任务启动时再次验证，"
    "仍不可用才检查 PATH 和常见安装位置。"
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
_FULL_RAW = (
    ("start_mode", ("new",)),
    ("format_validation", ("sample", "all")),
)
_FORMAT_SAMPLE = (("check_scope", ("sample",)),)
_HASH_SAMPLE = (("check_scope", ("sample",)),)


TASKS = (
    TaskSpec(
        "env_check",
        "env-check",
        "ENV-01  运行环境检测",
        "运行环境检测",
        "检测各功能所需工具和可选能力。",
        "工具状态 · 版本信息 · 安装与更新",
        (
            FieldSpec(
                "output_dir", "环境报告目录", "--output-dir", "dir",
                _DEFAULT_REPORTS_DIR,
                help="默认保存到 Output\\Reports；可改为其他目录。",
                section="结果输出",
            ),
            FieldSpec(
                "exiftool_path", "ExifTool", "--exiftool-path",
                "file", help=_TOOL_PATH_HELP,
                filetypes=_EXE_TYPES, section="工具路径", top_menu=True,
            ),
            FieldSpec(
                "ffprobe_path", "ffprobe", "--ffprobe-path", "file",
                help=_TOOL_PATH_HELP,
                filetypes=_EXE_TYPES, section="工具路径", top_menu=True,
            ),
            FieldSpec(
                "sevenzip_path", "7-Zip", "--sevenzip-path",
                "file", help=_TOOL_PATH_HELP,
                filetypes=_EXE_TYPES, section="工具路径", top_menu=True,
            ),
            FieldSpec(
                "powershell_path", "PowerShell",
                "--powershell-path", "file",
                help=_TOOL_PATH_HELP,
                filetypes=_EXE_TYPES, section="工具路径", top_menu=True,
            ),
            FieldSpec(
                "smartctl_path", "smartctl", "--smartctl-path",
                "file", help=_TOOL_PATH_HELP,
                filetypes=_EXE_TYPES, section="工具路径", top_menu=True,
            ),
        ),
    ),
    TaskSpec(
        _PROJECT_SELF_TEST_KEY,
        "",
        "DBS-91  DAISY 功能自检",
        "DAISY 功能自检",
        "运行项目自动化测试并汇总结果。",
        "项目测试 · 临时夹具 · 结果汇总",
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
                    ("全新生成", "new"),
                    ("使用续传", "resume"),
                ),
                help="续传时，哈希、工具原始输出与 NTFS-ID 等设置沿用"
                     "未完成快照中的原配置。",
                section="启动方式",
            ),
            FieldSpec(
                "roots", "档案根目录", "--root", "multidir", required=True,
                help="点击「添加」建立列表；最多 9 个。需要自定义根目录名时，"
                     "可写为「根目录名=路径」。",
                section="任务输入", active_when=_FULL_NEW,
            ),
            FieldSpec(
                "root_batch_mode", "生成方式", None, "choice",
                _ROOT_BATCH_SEPARATE,
                choices=(
                    ("分别生成", _ROOT_BATCH_SEPARATE),
                    ("合并生成", _ROOT_BATCH_COMBINED),
                ),
                help="分别生成时按添加顺序逐项运行，单项失败后继续下一项；"
                     "停止会终止整个队列。",
                section="任务输入", active_when=_FULL_NEW,
            ),
            FieldSpec(
                "resume", "续传快照", "--resume", "file",
                required=True,
                help="选择未完成快照 (.partial.sqlite)；扫描参数沿用该快照。",
                filetypes=_PARTIAL_TYPES, section="任务输入",
                active_when=_FULL_RESUME,
            ),
            FieldSpec(
                "output_dir", "快照保存目录", "--output-dir", "dir",
                _DEFAULT_SNAPSHOTS_DIR,
                help="默认保存到 Output\\Snapshots；可改为其他目录。",
                section="结果输出", active_when=_FULL_NEW,
            ),
            FieldSpec(
                "metadata_storage", "元数据范围",
                "--metadata-storage", "choice", "complete",
                choices=(
                    ("全量元数据", "complete"),
                    ("基础元数据", "normalized"),
                ),
                help=(
                    "基础元数据保留规范化常用字段；音视频记录容器与流，"
                    "GIF 使用 ExifTool。全量元数据另存工具原始输出，"
                    "便于日后重新解释。"
                ),
                section="快照内容", active_when=_FULL_NEW,
            ),
            FieldSpec(
                "format_validation", "格式校验", "--format-validation",
                "choice", "off",
                choices=(
                    ("关闭", "off"),
                    ("抽样校验", "sample"),
                    ("全部校验", "all"),
                ),
                help="完整扫描可选格式校验；默认关闭，启用后会增加扫描时间。",
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
                "raw_deep_validation", "RAW 深度校验",
                "--raw-deep-validation", "choice_flag", False,
                choices=(
                    ("关闭", False),
                    ("启用隔离深度解码", True),
                ),
                flag_value=True,
                help=(
                    "仅处理本次格式校验范围内的 RAW；由独立 rawpy/LibRaw "
                    "子进程实际解码。默认关闭，能力不可用时会显示原因。"
                ),
                section="快照内容", top_menu=True,
                active_when=_FULL_RAW,
            ),
            FieldSpec(
                "collect_file_id", "NTFS-ID",
                "--no-file-id", "choice_flag", True,
                choices=(
                    ("采集", True),
                    ("不采集", False),
                ),
                flag_value=False,
                help="采集 NTFS 卷序列号与文件索引，用于辅助判断移动和重命名。",
                section="快照内容", active_when=_FULL_NEW,
            ),
            FieldSpec(
                "hash_mode", "哈希模式", "--hash", "choice", "full",
                choices=(
                    ("不计算哈希", "none"),
                    ("复用上一快照", "incremental"),
                    ("完整 SHA-256", "full"),
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
                "verify_percent", "哈希复检",
                "--verify-sample-percent", default="1.0",
                help=(
                    "主 SHA-256 完成后，抽取本次实际计算且有效的条目，"
                    "再由 PowerShell Get-FileHash 独立复检；默认 1%，至少 "
                    "100 个（不足则全部复检）。这不是主哈希的覆盖比例。"
                ),
                section="哈希比例", top_menu=True,
                active_when=_FULL_HASHED,
            ),
            FieldSpec(
                "map_root", "对应关系", "--map-root", "root_label_map",
                help="将上一快照的根目录名对应到本次根目录名；单根异名通常无需设置。",
                section="增量复用",
                active_when=_FULL_INCREMENTAL,
            ),
            FieldSpec(
                "exiftool_path", "ExifTool", "--exiftool-path", "file",
                help=_TOOL_PATH_HELP,
                filetypes=_EXE_TYPES,
                section="工具路径", top_menu=True,
                active_when=_FULL_NEW,
            ),
            FieldSpec(
                "ffprobe_path", "ffprobe", "--ffprobe-path", "file",
                help=_TOOL_PATH_HELP,
                filetypes=_EXE_TYPES,
                section="工具路径", top_menu=True,
                active_when=_FULL_NEW,
            ),
            FieldSpec(
                "sevenzip_path", "7-Zip", "--sevenzip-path", "file",
                help=_TOOL_PATH_HELP,
                filetypes=_EXE_TYPES,
                section="工具路径", top_menu=True,
                active_when=_FULL_NEW,
            ),
            FieldSpec(
                "powershell_path", "PowerShell", "--powershell-path",
                "file",
                help="用于独立哈希复检；" + _TOOL_PATH_HELP,
                filetypes=_EXE_TYPES, section="工具路径", top_menu=True,
                active_when=_FULL_POWERSHELL,
            ),
            FieldSpec(
                "timeout_action", "超时处置", "--timeout-action",
                "choice", "continue_waiting",
                choices=(
                    ("继续等待", "continue_waiting"),
                    ("跳过并记录", "skip_and_record"),
                    ("停止并保留续传", "stop_and_resume"),
                ),
                help="文件长时间无进展时采用的默认操作；弹窗仍可临时改选。",
                section="高级设置", top_menu=True,
                active_when=_FULL_NEW,
            ),
            FieldSpec(
                "retry_mode", "重试范围", "--retry-mode", "choice",
                "pending",
                choices=(
                    ("仅未处理", "pending"),
                    ("瞬时失败", "transient"),
                    ("全部未成功", "all-unsuccessful"),
                ),
                help="续传时选择要重试的哈希条目：未处理、瞬时失败或全部未成功。",
                section="故障恢复", active_when=_FULL_RESUME,
            ),
            FieldSpec(
                "show_current_file", "显示当前文件", "--show-current-file",
                "choice_flag", False,
                choices=(("关闭", False), ("显示", True)),
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
        "快速登记目录与文件属性，生成轻量封存快照。",
        "档案只读 · 快速 · 生成快照",
        (
            FieldSpec(
                "start_mode", "启动方式", None, "choice", "new",
                choices=(
                    ("全新生成", "new"),
                    ("使用续传", "resume"),
                ),
                help="续传时沿用未完成快照中冻结的快速扫描配置。",
                section="启动方式",
            ),
            FieldSpec(
                "roots", "档案根目录", "--root", "multidir", required=True,
                help="点击「添加」建立列表；最多 9 个。需要自定义根目录名时，"
                     "可写为「根目录名=路径」。",
                section="任务输入", active_when=_QUICK_NEW,
            ),
            FieldSpec(
                "root_batch_mode", "生成方式", None, "choice",
                _ROOT_BATCH_SEPARATE,
                choices=(
                    ("分别生成", _ROOT_BATCH_SEPARATE),
                    ("合并生成", _ROOT_BATCH_COMBINED),
                ),
                help="分别生成时按添加顺序逐项运行，单项失败后继续下一项；"
                     "停止会终止整个队列。",
                section="任务输入", active_when=_QUICK_NEW,
            ),
            FieldSpec(
                "resume", "续传快照", "--resume", "file", required=True,
                help="必须选择数据库结构版本 4 的未完成快照 (.partial.sqlite)；"
                     "续传时不覆盖冻结参数。",
                filetypes=_PARTIAL_TYPES, section="任务输入",
                active_when=_QUICK_RESUME,
            ),
            FieldSpec(
                "output_dir", "快照保存目录", "--output-dir", "dir",
                _DEFAULT_SNAPSHOTS_DIR,
                help="默认保存到 Output\\Snapshots；可改为其他目录。",
                section="结果输出", active_when=_QUICK_NEW,
            ),
            FieldSpec(
                "collect_file_id", "NTFS-ID", "--no-file-id",
                "choice_flag", True,
                choices=(
                    ("采集", True),
                    ("不采集", False),
                ),
                flag_value=False,
                help="采集 NTFS 卷序列号与文件索引，用于辅助判断移动和重命名。",
                section="快照内容", active_when=_QUICK_NEW,
            ),
        ),
    ),
    TaskSpec(
        "check_format",
        "check-format",
        "DBS-32  格式校验",
        "格式校验",
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
                help="必须指定。单根快照可直接添加当前档案目录；多根快照需逐项"
                     "填写「根目录名=当前路径」，根目录名必须与快照一致。",
                section="档案位置",
            ),
            FieldSpec(
                "check_scope", "校验范围", None, "choice", "full",
                choices=(
                    ("全部校验", "full"),
                    ("比例抽样", "sample"),
                ),
                help="抽样适合快速排查；需要完整覆盖时选择全部。",
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
                help="默认保存到 Output\\Reports；可改为其他目录。",
                section="结果输出",
            ),
            FieldSpec(
                "exiftool_path", "ExifTool", "--exiftool-path", "file",
                help=_TOOL_PATH_HELP,
                filetypes=_EXE_TYPES,
                section="工具路径", top_menu=True,
            ),
            FieldSpec(
                "ffprobe_path", "ffprobe", "--ffprobe-path", "file",
                help=_TOOL_PATH_HELP,
                filetypes=_EXE_TYPES,
                section="工具路径", top_menu=True,
            ),
            FieldSpec(
                "sevenzip_path", "7-Zip", "--sevenzip-path", "file",
                help=_TOOL_PATH_HELP,
                filetypes=_EXE_TYPES,
                section="工具路径", top_menu=True,
            ),
            FieldSpec(
                "force", "指纹降级", "--force",
                "choice_flag", False,
                choices=(("不启用", False), ("启用", True)),
                flag_value=True,
                help="仅允许指纹缺失；指纹与实际字节不符仍会拒绝。",
                section="故障恢复",
            ),
        ),
    ),
    TaskSpec(
        "check_hash",
        "check-hash",
        "DBS-31  哈希核验",
        "哈希核验",
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
                help="必须指定。单根快照可直接添加当前档案目录；多根快照需逐项"
                     "填写「根目录名=当前路径」，根目录名必须与快照一致。",
                section="档案位置",
            ),
            FieldSpec(
                "check_scope", "核验范围", "--full", "choice_flag", "sample",
                choices=(
                    ("比例抽样", "sample"),
                    ("全量核验", "full"),
                ),
                flag_value="full",
                help="全量模式会读取所有有基准哈希的文件。",
                section="核验范围",
            ),
            FieldSpec(
                "sample_percent", "哈希抽样", "--sample-percent",
                default="1.0", help="默认抽查 1% 的可哈希文件。",
                section="哈希比例", top_menu=True,
                active_when=_HASH_SAMPLE,
            ),
            FieldSpec(
                "powershell_path", "PowerShell", "--powershell-path",
                "file",
                help="用于独立哈希核验；" + _TOOL_PATH_HELP,
                filetypes=_EXE_TYPES, section="工具路径", top_menu=True,
            ),
            FieldSpec(
                "force", "指纹降级", "--force",
                "choice_flag", False,
                choices=(("不启用", False), ("启用", True)),
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
        "DBS-21  档案快照对比",
        "档案快照对比",
        "比较两份快照，记录文件增删、变化、移动与复制。",
        "快照对比 · 变化分类 · Diff 数据库",
        (
            FieldSpec(
                "old", "基准快照", "--old", "file",
                required=True, filetypes=_SQLITE_TYPES, section="对比输入",
                help="选择作为对比起点的封存快照。",
            ),
            FieldSpec(
                "new", "对比快照", "--new", "file",
                required=True, filetypes=_SQLITE_TYPES, section="对比输入",
                help="选择要与基准快照比较的封存快照。",
            ),
            FieldSpec(
                "output_dir", "对比结果目录", "--output-dir", "dir",
                _DEFAULT_DIFFS_DIR,
                help="默认保存到 Output\\Diffs；可改为其他目录。",
                section="结果输出",
            ),
            FieldSpec(
                "map_root", "根目录名配对", "--map-root", "root_label_map",
                help=(
                    "将基准快照与对比快照的根目录名配对；"
                    "单根目录通常会自动对应。"
                ),
                section="根目录名配对",
            ),
            FieldSpec(
                "force", "指纹降级", "--force",
                "choice_flag", False,
                choices=(("不启用", False), ("启用", True)),
                flag_value=True,
                help="仅允许缺少文件名指纹的旧库继续对比；指纹不一致仍拒绝，"
                     "并生成同目录下以 _Issues.md 结尾的问题报告。",
                section="高级设置", top_menu=True,
            ),
        ),
    ),
    TaskSpec(
        "export_report",
        "export-report",
        "DBS-41  旧版报告导出",
        "旧版报告导出",
        "从快照或 Diff 数据库导出冻结格式报告。",
        "兼容入口 · 冻结格式 · 只读输入",
        (
            FieldSpec(
                "source_type", "输入类型", None, "choice", "snapshot",
                choices=(
                    ("封存快照", "snapshot"),
                    ("Diff 数据库", "diff"),
                ),
                help=(
                    "快照导出 Tree、Summary 等清单与诊断 CSV；Diff 导出 "
                    "Diff_summary.md、Diff_details.csv、Diff_subtrees.csv 等。"
                    "两者均附中文 XLSX 工作簿。"
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
                help="默认保存到 Output\\Reports；可改为其他目录。",
                section="结果输出",
            ),
        ),
    ),
    TaskSpec(
        "storage_list",
        "storage-list",
        "内部步骤：检测硬盘",
        "检测硬盘",
        "读取本机硬盘、分区和卷，并标明可用的 SMART 读取目标。",
        "管理员权限 · 只读检测 · 硬盘清单",
        (
            FieldSpec(
                "smartctl_path", "smartctl", "--smartctl-path", "file",
                help=_TOOL_PATH_HELP,
                filetypes=_EXE_TYPES,
                section="工具路径", top_menu=True,
            ),
            FieldSpec(
                "powershell_path", "PowerShell", "--powershell-path",
                "file", help=_TOOL_PATH_HELP,
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
        "采集硬盘、分区、卷与 SMART 信息，生成硬盘档案。",
        "只读采集 · 分区与卷 · SMART 信息",
        (
            FieldSpec(
                "disk_number", "硬盘选择", "--disk-number",
                "disk_pool", required=True,
                help="先点击「检测硬盘」，再选择要登记的联机硬盘；"
                     "重新检测会清除旧选择。",
                section="采集目标",
            ),
            FieldSpec(
                "output_dir", "硬盘档案目录", "--output-dir", "dir",
                _DEFAULT_STORAGE_DIR,
                help="默认保存到 Output\\Storage；每块硬盘生成一个 ZIP。",
                section="结果输出",
            ),
            FieldSpec(
                "summary_txt", "简化报告", "--summary-txt",
                "choice_flag", True,
                choices=(
                    ("不生成", False),
                    ("生成", True),
                ),
                flag_value=True,
                help="同时生成便于阅读的 TXT；完整结构化数据保存在 ZIP 中。",
                section="结果输出",
            ),
            FieldSpec(
                "smartctl_path", "smartctl", "--smartctl-path", "file",
                help=_TOOL_PATH_HELP,
                filetypes=_EXE_TYPES,
                section="工具路径", top_menu=True,
            ),
            FieldSpec(
                "powershell_path", "PowerShell", "--powershell-path",
                "file", help=_TOOL_PATH_HELP,
                filetypes=_EXE_TYPES,
                section="工具路径", top_menu=True,
            ),
        ),
    ),
)


def _task_definition(task_key: str) -> TaskSpec:
    return next(task for task in TASKS if task.key == task_key)


_UNIFIED_SCAN_FULL_ONLY = frozenset((
    "metadata_exiftool_mode", "metadata_ffprobe_mode", "metadata_storage",
    "hash_mode", "verify_percent", "exiftool_path", "ffprobe_path",
    "sevenzip_path", "powershell_path", "timeout_action",
    "retry_mode", "show_current_file",
))


def _unified_scan_fields() -> tuple[FieldSpec, ...]:
    fields: list[FieldSpec] = [FieldSpec(
        "scan_mode", "扫描模式", None, "choice_buttons", "",
        required=True,
        choices=(("完整扫描", "full"), ("快速扫描", "quick")),
        help="选择完整扫描或快速扫描；随后显示对应设置。",
        section="扫描模式",
    )]
    for original in _task_definition("full_scan").fields:
        spec = original
        if spec.key == "metadata_storage":
            fields.extend((
                FieldSpec(
                    "metadata_exiftool_mode", "ExifTool",
                    "--metadata-exiftool-mode", "choice", "complete",
                    choices=(
                        ("ExifTool 全量", "complete"),
                        ("ExifTool 基础", "normalized"),
                        ("ExifTool 关闭", "off"),
                    ),
                    section="采集内容",
                    top_menu=True,
                    active_when=(
                        ("scan_mode", ("full",)), *_FULL_NEW),
                ),
                FieldSpec(
                    "metadata_ffprobe_mode", "ffprobe",
                    "--metadata-ffprobe-mode", "choice", "complete",
                    choices=(
                        ("ffprobe 全量", "complete"),
                        ("ffprobe 基础", "normalized"),
                        ("ffprobe 关闭", "off"),
                    ),
                    section="采集内容", top_menu=True,
                    active_when=(
                        ("scan_mode", ("full",)), *_FULL_NEW),
                ),
            ))
        if spec.key in (
                "format_validation", "format_sample_percent",
                "raw_deep_validation", "previous_snapshot", "map_root"):
            continue
        if spec.key == "start_mode":
            spec = replace(
                spec,
                label="生成方式", kind="choice_buttons", default="",
                required=True,
                choices=(("全新生成", "new"),
                         ("使用续传", "resume")),
                help="全新生成建立新快照；使用续传继续未完成快照。",
                section="扫描模式",
            )
        elif spec.key == "root_batch_mode":
            spec = replace(
                spec,
                label="建库方式", kind="choice_buttons",
                choices=(
                    ("分别生成", _ROOT_BATCH_SEPARATE),
                    ("合并生成", _ROOT_BATCH_COMBINED),
                ),
                help=(
                    "分别生成时每个目录一个快照；合并生成时多个目录写入"
                    "同一快照。"
                ),
            )
        if spec.key in ("resume", "retry_mode"):
            spec = replace(spec, section="续传设置")
        elif spec.key in ("roots", "root_batch_mode", "output_dir"):
            spec = replace(spec, section="任务设置")
        elif spec.key in (
                "metadata_storage", "collect_file_id", "hash_mode",
                "verify_percent"):
            spec = replace(spec, section="采集内容")
        if spec.key == "metadata_storage":
            spec = replace(
                spec,
                label="元数据", flag=None, kind="metadata_controls",
                choices=(
                    ("基础", "normalized"),
                    ("全量", "complete"),
                ),
                help=(
                    "点击 ExifTool 或 ffprobe 按钮可依次切换全量、基础和关闭。"
                    "全量另存工具原始输出；基础只保留规范化字段。"
                ),
            )
        elif spec.key == "hash_mode":
            spec = replace(
                spec,
                label="哈希", kind="value_toggle",
                choices=(
                    ("不采集", "none"),
                    ("SHA-256", "full"),
                ),
                help=(
                    "选择 SHA-256 时为每个文件计算哈希；选择不采集时不计算哈希。"
                ),
            )
        if spec.key in _UNIFIED_SCAN_FULL_ONLY:
            spec = replace(
                spec,
                active_when=(("scan_mode", ("full",)), *spec.active_when),
            )
        else:
            spec = replace(
                spec,
                active_when=(
                    ("scan_mode", ("full", "quick")), *spec.active_when),
            )
        fields.append(spec)
    return tuple(fields)


_VERIFY_EXIFTOOL_ENABLED = (("verify_exiftool", (True,)),)
_VERIFY_FFPROBE_ENABLED = (("verify_ffprobe", (True,)),)
_VERIFY_SEVENZIP_ENABLED = (("verify_sevenzip", (True,)),)


TASKS = (*TASKS,
    TaskSpec(
        "scan", "scan", "DBS-10  档案扫描建库", "档案扫描建库",
        "扫描档案目录，生成可对比、可续传的快照。",
        "完整／快速 · 文件清单 · 快照数据库",
        _unified_scan_fields(),
    ),
    TaskSpec(
        "verify", "verify", "DBS-30  档案数据核验", "档案数据核验",
        "按快照核对现有文件，检查格式、容器与 RAW 解码。",
        "文件状态 · 格式与容器 · 问题报告",
        (
            FieldSpec(
                "snapshot", "封存快照", "--snapshot", "file",
                required=True, filetypes=_SQLITE_TYPES, section="核验输入",
                help="选择提供文件清单和原始文件属性的封存快照。",
            ),
            FieldSpec(
                "root_map", "档案根目录", "--root", "multimapdir",
                required=True,
                help="必须指定。单根快照可直接添加当前档案目录；多根快照需逐项填写"
                     "「根目录名=当前路径」。",
                section="核验输入",
            ),
            FieldSpec(
                "verify_builtin", "核验项目", None,
                "verification_tools", True,
                choices=(("关闭", False), ("开启", True)),
                help=(
                    "可用项目默认开启；每项只检查适用文件，"
                    "不支持的文件类型只计入汇总，不列为问题。"
                ),
                section="数据核验",
            ),
            FieldSpec(
                "verify_exiftool", "ExifTool", None, "choice_flag", True,
                choices=(("关闭", False), ("开启", True)),
                section="数据核验", top_menu=True,
            ),
            FieldSpec(
                "verify_ffprobe", "ffprobe", None, "choice_flag", True,
                choices=(("关闭", False), ("开启", True)),
                section="数据核验", top_menu=True,
            ),
            FieldSpec(
                "verify_sevenzip", "7-Zip", None, "choice_flag", True,
                choices=(("关闭", False), ("开启", True)),
                section="数据核验", top_menu=True,
            ),
            FieldSpec(
                "raw_deep_validation", "RAW 深度校验", None,
                "choice_flag", True,
                choices=(("关闭", False), ("开启", True)),
                help=(
                    "使用独立 rawpy/LibRaw 子进程实际解码；能力可用时默认开启，"
                    "不可用时禁用并显示原因。"
                ),
                section="数据核验", top_menu=True,
            ),
            FieldSpec(
                "timeout_action", "超时处置", "--timeout-action",
                "choice", "continue_waiting",
                choices=(("继续等待", "continue_waiting"),
                         ("跳过并记录", "skip_and_record"),
                         ("停止并保留结果", "stop_and_resume")),
                help="达到动态阈值后的默认处置；停止时保留已完成结果并生成报告，但不提供"
                     "跨重启续传。未选择时继续等待。",
                section="高级设置", top_menu=True,
            ),
            FieldSpec(
                "show_current_file", "显示当前文件", "--show-current-file",
                "choice_flag", False,
                choices=(("关闭", False), ("显示", True)),
                flag_value=True,
                help="在进度区显示正在核验的相对路径。",
                section="高级设置", top_menu=True,
            ),
            FieldSpec(
                "report_dir", "核验报告目录", "--report-dir", "dir",
                _DEFAULT_REPORTS_DIR,
                help="默认保存到 Output\\Reports；Markdown 按核验类型分板块，"
                     "JSON 保存结构化证据。",
                section="结果输出",
            ),
            FieldSpec(
                "exiftool_path", "ExifTool", "--exiftool-path", "file",
                help=_TOOL_PATH_HELP,
                filetypes=_EXE_TYPES, section="工具路径", top_menu=True,
                active_when=_VERIFY_EXIFTOOL_ENABLED,
            ),
            FieldSpec(
                "ffprobe_path", "ffprobe", "--ffprobe-path", "file",
                help=_TOOL_PATH_HELP,
                filetypes=_EXE_TYPES, section="工具路径", top_menu=True,
                active_when=_VERIFY_FFPROBE_ENABLED,
            ),
            FieldSpec(
                "sevenzip_path", "7-Zip", "--sevenzip-path", "file",
                help=_TOOL_PATH_HELP,
                filetypes=_EXE_TYPES, section="工具路径", top_menu=True,
                active_when=_VERIFY_SEVENZIP_ENABLED,
            ),
            FieldSpec(
                "force", "指纹降级", "--force", "choice_flag", False,
                choices=(("不启用", False), ("启用", True)),
                flag_value=True,
                help="仅允许缺少文件名指纹的旧库继续核验；指纹不一致仍拒绝。",
                section="高级设置", top_menu=True,
            ),
        ),
    ),
    TaskSpec(
        "parse_db", "parse-db", "DBS-41  档案数据解析", "档案数据解析",
        "解析快照或 Diff，按所选数据模块和格式导出。",
        "数据模块 · 四种格式 · 独立导出",
        (
            FieldSpec(
                "database", "输入数据库", "--database", "parse_database",
                required=True, filetypes=_SQLITE_TYPES, section="解析输入",
                help="选择 DAISY 支持的封存快照或 Diff 数据库；兼容 "
                     "v1.4.1（数据库结构版本 3）。",
            ),
            FieldSpec(
                "preset", "导出范围", "--preset", "choice_buttons",
                "full-audit",
                choices=(("摘要内容", "human-summary"),
                         ("全部内容", "full-audit"),
                         ("自定义", "custom")),
                help="摘要内容仅选择概览和关键证据；全部内容选择所有可用的数据模块；"
                     "自定义可逐项调整。",
                section="导出内容",
            ),
            FieldSpec(
                "parse_modules", "数据模块", "--include", "parse_modules",
                help="列出当前数据库的数据模块；不可用的数据模块会说明原因。",
                section="导出内容",
            ),
            FieldSpec(
                "formats", "输出格式", "--format", "multi_choice",
                "html\nxlsx\ncsv\njsonl", required=True,
                choices=(("HTML 阅读报告", "html"),
                         ("Excel 工作簿", "xlsx"),
                         ("CSV 数据表", "csv"),
                         ("JSONL 数据流", "jsonl")),
                help="HTML 便于阅读，Excel 便于筛选；CSV 和 JSONL 供脚本及进一步分析。",
                section="输出设置",
            ),
            FieldSpec(
                "output_dir", "数据导出目录", "--output-dir", "dir",
                _DEFAULT_REPORTS_DIR,
                help="默认保存到 Output\\Reports；可改为其他目录。",
                section="结果输出",
            ),
        ),
    ),
)

TASK_BY_KEY = {task.key: task for task in TASKS}
_RESTORABLE_TASK_KEYS = frozenset(
    task.key for task in TASKS if task.key != "storage_list")
_TASK_MENU_SECTIONS = (
    ("档案", ("scan", "diff", "verify", "parse_db")),
    ("设备", ("storage_collect",)),
    ("环境", ("env_check",)),
)
_TASK_MENU_SECTION_COLOURS = {
    "档案": ("Archive", _AMBER, _AMBER_DEEP, _AMBER_SOFT),
    "设备": ("Device", _RED, _RED_DEEP, _RED_SOFT),
    "环境": ("Environment", _GREEN, _GREEN_DEEP, _GREEN_SOFT),
}
_TASK_MENU_SEPARATOR_AFTER: frozenset[str] = frozenset()
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
_TASK_TOOLBAR_KEYS = (
    "scan", "diff", "verify", "parse_db", "storage_collect", "env_check",
)
_TASK_TOOLBAR_LABELS = {
    "scan": "扫描建库",
    "diff": "快照对比",
    "verify": "数据核验",
    "parse_db": "数据解析",
    "storage_collect": "硬盘登记",
    "env_check": "环境检测",
}
_TASK_DISPLAY_TITLES = {
    "env_check": "运行环境检测",
    "scan": "档案扫描建库",
    "diff": "档案快照对比",
    "verify": "档案数据核验",
    "parse_db": "档案数据解析",
    "storage_collect": "硬盘信息登记",
}


def task_display_title(task_key: str) -> str:
    """返回设置卡、进度和报告使用的完整功能标题。"""
    return _TASK_DISPLAY_TITLES.get(task_key, TASK_BY_KEY[task_key].title)


_STANDARD_BUTTON_WIDTH = 14
_STANDARD_BUTTON_PADDING = (12, 6)
_SIX_COLUMN_BUTTON_WIDTH = 12
_TASK_TOOLBAR_BUTTON_WIDTH = _SIX_COLUMN_BUTTON_WIDTH
_TASK_TOOLBAR_BUTTON_PADDING = (4, 5)
_TASK_TOOLBAR_MINIMUM_WIDTH = 1100
_TASK_TOOLBAR_LABEL_COLOUR = _TEXT
_TASK_TOOLBAR_BACKGROUND = "#edd7ad"
_TASK_TOOLBAR_HOVER = "#e2c38b"
_TASK_TOOLBAR_SELECTED = "#d7b36b"
_TASK_TOOLBAR_SELECTED_HOVER = "#c99f50"
_TASK_TOOLBAR_FOREGROUND = _AMBER_DEEP
_BLOCK_SELECTION_BACKGROUND = _GREEN
_BLOCK_SELECTION_HOVER = _GREEN
_BLOCK_SELECTION_FOREGROUND = _GREEN_DEEP
_UNIFIED_ACTION_BACKGROUND = _GREEN_DARK
_UNIFIED_ACTION_FOREGROUND = "white"
_RUN_BUTTON_TEXT = "开始"
_COLLAPSED_PANEL_TITLE_FONT = ("Microsoft YaHei UI", 9, "bold")
# 全局布局只使用五档可见间距；控件内部的光学微调不属于布局间距。
_SPACING_COMPACT = 4
_SPACING_INLINE = 8
_SPACING_STANDARD = 12
_SPACING_SECTION = 16
_SPACING_OUTER = 24
_PANEL_HEADER_PADX = _SPACING_SECTION
_PANEL_ACTION_BUTTON_WIDTH = _STANDARD_BUTTON_WIDTH
_STANDARD_BUTTON_GAP = _SPACING_STANDARD
_PANEL_ACTION_BUTTON_GAP = _STANDARD_BUTTON_GAP
_PANEL_GAP = _SPACING_STANDARD
_INLINE_CONTROL_GAP = _SPACING_INLINE
_FORM_FIELD_GAP = _SPACING_STANDARD
_FORM_FIELD_PADY = _FORM_FIELD_GAP // 2
_FORM_SECTION_PADY = (_SPACING_COMPACT, 0)
_FORM_ACTION_BUTTON_WIDTH = _STANDARD_BUTTON_WIDTH
_FILE_PICKER_BUTTON_WIDTH = 12
_FILE_PICKER_BUTTON_PADDING = (10, 5)
_ENVIRONMENT_BUTTON_WIDTH = _SIX_COLUMN_BUTTON_WIDTH
_ENVIRONMENT_BUTTON_PADDING = (4, 4)
_FORM_FIELD_TITLE_MAX_CHARS = 6
_FORM_FIELD_ASCII_TITLE_MAX_CHARS = 12
_BOOLEAN_BUTTON_WIDTH = _STANDARD_BUTTON_WIDTH
_SCAN_MODE_BUTTON_WIDTH = _STANDARD_BUTTON_WIDTH
_FORM_SINGLE_ROW_HEIGHT = 58
_FORM_SCROLL_OVERFLOW_TOLERANCE = 2
_VARIABLE_HEIGHT_FIELD_KINDS = frozenset((
    "disk_pool", "parse_modules",
    "multidir", "multimapdir", "multiline", "root_label_map",
))
_PERSISTABLE_TASK_OPTION_KINDS = frozenset((
    "bool", "inverse_bool", "choice", "choice_flag", "choice_buttons",
    "metadata_tools", "metadata_controls", "verification_tools",
    "value_toggle", "multi_choice",
))
_NONPERSISTENT_TASK_OPTION_KEYS = frozenset((
    "start_mode", "retry_mode",
))
_PERSISTABLE_NUMERIC_OPTION_KEYS: frozenset[str] = frozenset()
_STORAGE_DISK_CHECKBOX_SIZE = 20
_COLLAPSED_SETTINGS_HEADER_PADY = (8, 8)


def _validated_manual_tool_paths(raw: object) -> dict[str, str]:
    """只接受固定工具名和绝对路径；路径是否仍存在留到任务预检。"""
    if not isinstance(raw, dict):
        return {}
    validated: dict[str, str] = {}
    for tool_name in _TOOL_PATH_MENU_ORDER:
        value = raw.get(tool_name)
        if not isinstance(value, str):
            continue
        path = value.strip()
        if path and len(path) <= 32767 and os.path.isabs(path):
            validated[tool_name] = os.path.normpath(path)
    return validated


def _validated_choice_value(
    spec: FieldSpec, value: object,
) -> object | None:
    for _label, allowed in spec.choices:
        if type(value) is type(allowed) and value == allowed:
            return value
        if isinstance(allowed, str) and isinstance(value, str) \
                and value == allowed:
            return value
    return None


def _validated_task_options(raw: object) -> dict[str, dict[str, object]]:
    """过滤可持久化任务选项；不接受档案、数据库或输出路径。"""
    if not isinstance(raw, dict):
        return {}
    validated: dict[str, dict[str, object]] = {}
    for task_key, raw_values in raw.items():
        if (not isinstance(task_key, str)
                or task_key not in _RESTORABLE_TASK_KEYS
                or not isinstance(raw_values, dict)):
            continue
        task = TASK_BY_KEY[task_key]
        task_values: dict[str, object] = {}
        for spec in task.fields:
            if spec.key in _NONPERSISTENT_TASK_OPTION_KEYS:
                continue
            value = raw_values.get(spec.key)
            if spec.kind in _PERSISTABLE_TASK_OPTION_KINDS:
                if spec.kind == "multi_choice":
                    allowed = {str(item) for _label, item in spec.choices}
                    selected = _lines(value)
                    if selected and all(item in allowed for item in selected):
                        task_values[spec.key] = "\n".join(selected)
                    continue
                if spec.kind in (
                        "bool", "inverse_bool", "metadata_tools",
                        "verification_tools"):
                    if isinstance(value, bool):
                        task_values[spec.key] = value
                    continue
                selected = _validated_choice_value(spec, value)
                if selected is not None:
                    task_values[spec.key] = selected
                continue
            if spec.key not in _PERSISTABLE_NUMERIC_OPTION_KEYS:
                continue
            if not isinstance(value, (str, int, float)) \
                    or isinstance(value, bool):
                continue
            text = str(value).strip()
            try:
                number = float(text)
            except ValueError:
                continue
            if math.isfinite(number) and 0 <= number <= 100:
                task_values[spec.key] = text
        if task_values:
            validated[task_key] = task_values
    return validated


def _lines(value: object) -> list[str]:
    return [line.strip() for line in str(value or "").splitlines()
            if line.strip()]


def _task_values(task: TaskSpec,
                 values: dict[str, object]) -> dict[str, object]:
    merged = {spec.key: spec.default for spec in task.fields}
    merged.update(values)
    return merged

def _root_job_label(root_spec: str) -> str:
    """返回适合队列显示的根目录名，不访问目录内容。"""
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
    active = all(values.get(key) in allowed
                 for key, allowed in spec.active_when)
    if not active:
        return False
    if spec.key == "timeout_action":
        hash_mode = values.get("hash_scope", values.get("hash_mode"))
        format_mode = values.get(
            "format_scope", values.get("format_validation"))
        return (
            hash_mode in ("sample", "all", "incremental", "full")
            or format_mode in ("sample", "all")
            or any(bool(values.get(key)) for key in (
                "verify_builtin", "verify_exiftool", "verify_ffprobe",
                "verify_sevenzip", "raw_deep_validation",
            ))
        )
    return True


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
        mode = (
            str(values.get("scan_mode") or "")
            if task_key == "scan" else
            "full" if task_key == "full_scan" else "quick"
        )
        if task_key == "scan" and mode not in ("full", "quick"):
            return args
        args += ["--mode", mode]
        if values.get("start_mode") == "resume":
            resume = str(values.get("resume") or "").strip()
            if resume:
                args += ["--resume", _absolute(resume), "--manual-resume"]
            retry_mode = str(values.get("retry_mode") or "").strip()
            if mode == "full" and retry_mode:
                args += ["--retry-mode", retry_mode]
            if mode == "full" and bool(values.get("show_current_file")):
                args.append("--show-current-file")
            args.append("--control-stdin")
            return args
    if task_key == "verify":
        # 档案数据核验页不再提供独立哈希核验；显式覆盖 DBS-30 的历史默认值。
        args += ["--hash", "off"]
        selected_format_tools = tuple(
            tool_id
            for key, tool_id in (
                ("verify_builtin", "builtin"),
                ("verify_exiftool", "exiftool"),
                ("verify_ffprobe", "ffprobe"),
                ("verify_sevenzip", "sevenzip"),
            )
            if bool(values.get(key))
        )
        args += ["--format", "all" if selected_format_tools else "off"]
        for tool_id in selected_format_tools:
            args += ["--format-tool", tool_id]
        if bool(values.get("raw_deep_validation")):
            args.append("--raw-deep-validation")
    if task_key == "parse_db":
        database = str(values.get("database") or "").strip()
        if database:
            args += ["--database", _absolute(database)]
        preset = str(values.get("preset") or "human-summary").strip()
        if preset:
            args += ["--preset", preset]
        if preset == "custom":
            for module_id in _lines(values.get("parse_modules")):
                args += ["--include", module_id]
        for format_id in _lines(values.get("formats")):
            args += ["--format", format_id]
        output_dir = str(values.get("output_dir") or "").strip()
        if output_dir:
            args += ["--output-dir", _absolute(output_dir)]
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
        if task_key in ("full_scan", "scan") \
                and spec.key in ("exiftool_path", "ffprobe_path"):
            tool_name = (
                "exiftool" if spec.key == "exiftool_path" else "ffprobe")
            format_enabled = values.get("format_validation") \
                in ("sample", "all")
            metadata_enabled = (
                str(values.get(
                    f"metadata_{tool_name}_mode", "complete")) != "off"
                if task_key == "scan" else
                bool(values.get(f"metadata_{tool_name}", True))
            )
            if not metadata_enabled and not format_enabled:
                continue
        value = values.get(spec.key, spec.default)
        if spec.kind == "bool":
            if bool(value):
                args.append(spec.flag)
        elif spec.kind == "inverse_bool":
            if not bool(value):
                args.append(spec.flag)
        elif spec.kind in ("choice_flag", "metadata_tools"):
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
        elif spec.kind in (
                "multidir", "multiline", "root_label_map",
                "multi_choice", "parse_modules"):
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
    if task_key in _CONTROL_TASK_KEYS:
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
    scan_mode = (
        "full" if task_key == "full_scan" else
        "quick" if task_key == "quick_scan" else
        str(values.get("scan_mode") or "")
    )
    mode_label = "完整扫描" if scan_mode == "full" else "快速扫描"
    mode = str(values.get("root_batch_mode") or _ROOT_BATCH_SEPARATE)
    if mode == _ROOT_BATCH_COMBINED and len(roots) > 1:
        heading = (
            f"将按{mode_label}模式处理以下文件夹，并合并生成一个数据库："
        )
    elif len(roots) > 1:
        heading = (
            f"将按{mode_label}模式处理以下文件夹，每个文件夹分别生成一个数据库："
        )
    else:
        heading = f"将按{mode_label}模式处理以下文件夹，并生成一个数据库："
    return heading + "\n" + "\n".join(f"• {path}" for path in roots)


def validate_values(
    task_key: str,
    values: dict[str, object],
    *,
    parse_inspection: dbparse.ParseDatabaseInspection | None = None,
) -> list[str]:
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
            issues.append(f"请填写「{spec.label}」。")

    if task_key in _SCAN_TASK_KEYS:
        resume = str(values.get("resume") or "").strip()
        if (values.get("start_mode") == "resume" and resume
                and not resume.lower().endswith(".partial.sqlite")):
            issues.append("续传文件必须是扩展名为 .partial.sqlite 的未完成快照。")
    if task_key == "storage_collect":
        disk_numbers = _lines(values.get("disk_number"))
        if any(not re.fullmatch(r"\d+", number) for number in disk_numbers):
            issues.append("「硬盘选择」包含无效编号，请重新检测并选择。")

    numeric_rules = {
        ("full_scan", "verify_percent"): (0.0, 100.0, True, False),
        ("full_scan", "format_sample_percent"): (
            0.0, 100.0, False, False),
        ("check_format", "sample_percent"): (0.0, 100.0, False, False),
        ("check_hash", "sample_percent"): (0.0, 100.0, False, False),
        ("scan", "verify_percent"): (0.0, 100.0, True, False),
        ("scan", "format_sample_percent"): (
            0.0, 100.0, False, False),
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
            issues.append(f"「{fields[key].label}」必须是数字。")
            continue
        label = fields[key].label
        if not math.isfinite(number):
            issues.append(f"「{label}」必须是有限数字。")
            continue
        if integer_only and not number.is_integer():
            issues.append(f"「{label}」必须是整数。")
            continue
        if number < low or (number == low and not allow_zero):
            op = "不小于" if allow_zero else "大于"
            issues.append(f"「{label}」必须{op} {low:g}。")
        if high is not None and number > high:
            issues.append(f"「{label}」不能大于 {high:g}。")

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
                        f"档案根目录应为「根目录名=路径」：{root_spec}")
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
                issues.append(f"合并生成时根目录名不能重复：{label}")
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
                "不带根目录名的当前路径只适用于单根快照；"
                "多根快照应逐项使用「根目录名=当前路径」。")
        for root_spec in root_specs:
            is_mapping = "=" in root_spec and not os.path.isabs(
                root_spec.strip().strip('"'))
            label, sep, path = (
                root_spec.partition("=")
                if is_mapping else ("", "", root_spec))
            if sep and (not label.strip() or not path.strip()):
                issues.append(f"根目录对应应为「根目录名=路径」：{root_spec}")
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
                issues.append(
                    "根目录对应必须同时填写基准与对比快照的文件夹名："
                    f"{mapping}")

    input_files = {
        "previous_snapshot", "resume", "snapshot", "old", "new",
        "source_path", "database", "exiftool_path", "ffprobe_path",
        "sevenzip_path",
        "powershell_path", "smartctl_path", "archive",
    }
    for key in input_files:
        if key not in active_keys:
            continue
        raw = str(values.get(key) or "").strip()
        if raw and not os.path.isfile(_absolute(raw)):
            issues.append(f"文件不存在：{raw}")

    if task_key == "parse_db" and not issues:
        database = _absolute(str(values.get("database") or ""))
        inspected_path = (
            os.path.abspath(parse_inspection.descriptor.path)
            if parse_inspection is not None else ""
        )
        if (parse_inspection is None
                or os.path.normcase(inspected_path)
                != os.path.normcase(database)):
            issues.append("请先解析当前输入数据库，再设置导出范围和数据模块。")
        else:
            preset = str(values.get("preset") or "human-summary")
            include = (
                _lines(values.get("parse_modules"))
                if preset == "custom" else ()
            )
            try:
                dbparse.plan_parse_export(
                    parse_inspection,
                    preset=preset,
                    include=include,
                    formats=_lines(values.get("formats")),
                )
            except core.PreflightError as exc:
                issues.append(str(exc))
    return issues


_TOOLTIP_MIN_TEXT_WIDTH = 240
_TOOLTIP_MAX_TEXT_WIDTH = 480
_TOOLTIP_PREFERRED_BREAK_AFTER = frozenset(
    " \t，。；：！？、/\\")
_TOOLTIP_NO_LINE_START = frozenset("，。；：！？、）】》〉」』”’")
_TOOLTIP_NO_LINE_END = frozenset("（【《〈「『“‘")


def _tooltip_display_text(
    text: str, font: tkfont.Font, max_width: int,
) -> str:
    """按实际字体像素宽度为悬停提示稳定分行。"""
    max_width = max(1, int(max_width))
    wrapped: list[str] = []
    normalized = str(text).replace("\r\n", "\n").replace("\r", "\n")

    def preferred_break(text_value: str) -> int:
        return max(
            (index + 1 for index, character in enumerate(text_value)
             if character in _TOOLTIP_PREFERRED_BREAK_AFTER),
            default=0,
        )

    for paragraph in normalized.split("\n"):
        if not paragraph:
            wrapped.append("")
            continue
        paragraph_line_start = len(wrapped)
        current = ""
        break_at = 0
        for character in paragraph:
            if (
                not current
                and character in _TOOLTIP_NO_LINE_START
                and len(wrapped) > paragraph_line_start
            ):
                wrapped[-1] += character
                continue
            candidate = current + character
            if not current or font.measure(candidate) <= max_width:
                current = candidate
                if character in _TOOLTIP_PREFERRED_BREAK_AFTER:
                    break_at = len(current)
                continue

            if character in _TOOLTIP_NO_LINE_START:
                if break_at:
                    line = current[:break_at].rstrip()
                    remainder = current[break_at:].lstrip()
                    if line:
                        wrapped.append(line)
                        current = remainder + character
                        break_at = preferred_break(current)
                        continue
                wrapped.append((current + character).rstrip())
                current = ""
                break_at = 0
                continue

            use_preferred = False
            if break_at:
                preferred_line = current[:break_at].rstrip()
                use_preferred = bool(preferred_line) and (
                    font.measure(preferred_line) >= max_width * 0.48)
            if use_preferred:
                line = current[:break_at].rstrip()
                remainder = current[break_at:].lstrip()
            else:
                line = current.rstrip()
                remainder = ""
                while line and line[-1] in _TOOLTIP_NO_LINE_END:
                    remainder = line[-1] + remainder
                    line = line[:-1]
                if not line:
                    line = current.rstrip()
                    remainder = ""
            if line:
                wrapped.append(line)
            current = remainder + character
            break_at = preferred_break(current)
        if current or len(wrapped) == paragraph_line_start:
            wrapped.append(current.rstrip())
        if len(wrapped) - paragraph_line_start >= 2:
            previous = wrapped[-2]
            last = wrapped[-1]
            minimum_last_width = max_width * 0.12
            while (
                previous
                and last
                and font.measure(last) < minimum_last_width
            ):
                moved = previous[-1]
                previous = previous[:-1].rstrip()
                while (
                    moved[0] in _TOOLTIP_NO_LINE_START
                    and previous
                ):
                    moved = previous[-1] + moved
                    previous = previous[:-1].rstrip()
                while previous and previous[-1] in _TOOLTIP_NO_LINE_END:
                    moved = previous[-1] + moved
                    previous = previous[:-1].rstrip()
                if not previous or moved[0] in _TOOLTIP_NO_LINE_START:
                    break
                last = moved + last
            wrapped[-2] = previous or wrapped[-2]
            wrapped[-1] = last
    return "\n".join(wrapped)


class ToolTip:
    """为按钮提供延迟出现、自动避开屏幕边缘的简短说明。"""

    def __init__(self, widget: tk.Misc, text: str, delay_ms: int = 480) -> None:
        self.widget = widget
        self.text = text
        self.delay_ms = delay_ms
        self._after_id: str | None = None
        self._window: tk.Toplevel | None = None
        self._display_text = ""
        self._measure_font: tkfont.Font | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide_active, add="+")
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
            top_level = self.widget.winfo_toplevel()
        except (AttributeError, tk.TclError, TypeError):
            return
        active = getattr(top_level, "_daisy_active_tooltip", None)
        if isinstance(active, ToolTip) and active is not self:
            active._hide()
        window = tk.Toplevel(self.widget)
        self._window = window
        top_level._daisy_active_tooltip = self  # type: ignore[attr-defined]
        window.wm_overrideredirect(True)
        try:
            window.attributes("-topmost", True)
        except tk.TclError:
            pass
        work_area = _monitor_work_area_for_window(self.widget)
        font_family = getattr(
            top_level, "_daisy_font_family", _UI_FONT_FAMILY)
        font_size_delta = int(getattr(
            top_level, "_daisy_font_size_delta", 0))
        font_spec = (
            font_family, _UI_BODY_FONT_SIZE + font_size_delta)
        text_width = max(
            _TOOLTIP_MIN_TEXT_WIDTH,
            min(_TOOLTIP_MAX_TEXT_WIDTH, work_area.width - 32),
        )
        self._measure_font = tkfont.Font(root=window, font=font_spec)
        self._display_text = _tooltip_display_text(
            self.text, self._measure_font, text_width)
        label = tk.Label(
            window, text=self._display_text, bg=_TEXT, fg="white",
            font=font_spec,
            justify="left",
            relief="solid", bd=1, padx=9, pady=6,
            wraplength=0,
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

    def _hide_active(self, _event: tk.Event | None = None) -> None:
        """按下任意关联控件时立即收起当前窗口的提示。"""
        self._cancel()
        try:
            top_level = self.widget.winfo_toplevel()
            active = getattr(top_level, "_daisy_active_tooltip", None)
        except (AttributeError, tk.TclError, TypeError):
            active = None
        if isinstance(active, ToolTip):
            active._hide()
        if active is not self:
            self._hide()

    def _hide(self, _event: tk.Event | None = None) -> None:
        self._cancel()
        try:
            top_level = self.widget.winfo_toplevel()
            if getattr(top_level, "_daisy_active_tooltip", None) is self:
                top_level._daisy_active_tooltip = None  # type: ignore[attr-defined]
        except (AttributeError, tk.TclError, TypeError):
            pass
        if self._window is None:
            return
        try:
            self._window.destroy()
        except tk.TclError:
            pass
        self._window = None
        self._display_text = ""
        self._measure_font = None


def attach_tooltip(widget: tk.Misc, text: str) -> ToolTip:
    """附加并保留唯一悬停提示；重复调用时仅更新文字。"""
    existing = getattr(widget, "_daisy_tooltip", None)
    if isinstance(existing, ToolTip):
        if existing.text != text:
            existing._hide()
        existing.text = text
        return existing
    tooltip = ToolTip(widget, text)
    widget._daisy_tooltip = tooltip  # type: ignore[attr-defined]
    return tooltip


class AdminModeButton(tk.Frame):
    """硬盘信息登记页的管理员模式按钮；点击后由外部完成 UAC 重启。"""

    def __init__(
        self, master: tk.Misc, *, value: bool = False,
        enabled: bool = True, command=None, background: str = _SURFACE,
    ) -> None:
        super().__init__(master, bg=background)
        self._value = bool(value)
        self._enabled = bool(enabled)
        self._command = command
        self.button = tk.Button(
            self, text="管理员模式", width=_STANDARD_BUTTON_WIDTH,
            relief="flat", bd=0, highlightthickness=1,
            font=("Microsoft YaHei UI", _UI_BODY_FONT_SIZE),
            anchor="center", justify="center",
            padx=_STANDARD_BUTTON_PADDING[0],
            pady=_STANDARD_BUTTON_PADDING[1],
            takefocus=True, command=self._activate,
        )
        self.button.pack()
        self._refresh()

    @property
    def value(self) -> bool:
        return self._value

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def tooltip_widgets(self) -> tuple[tk.Misc, ...]:
        return (self, self.button)

    def set_mode(
        self, *, value: bool | None = None,
        enabled: bool | None = None,
    ) -> None:
        if value is not None:
            self._value = bool(value)
        if enabled is not None:
            self._enabled = bool(enabled)
        self._refresh()

    def _activate(self, _event: tk.Event | None = None) -> None:
        if not self._enabled:
            return
        if callable(self._command):
            self._command(True)

    def _refresh(self) -> None:
        if self._value:
            background, foreground = _GREEN_DARK, "white"
        elif self._enabled:
            background, foreground = _AMBER, _AMBER_DEEP
        else:
            background, foreground = _CONTROL, _MUTED
        self.button.configure(
            state="normal" if self._enabled and not self._value else "disabled",
            bg=background, fg=foreground,
            activebackground=(
                _GREEN_DEEP if self._value else _AMBER_SOFT),
            activeforeground=foreground,
            disabledforeground=foreground,
            highlightbackground=background,
            highlightcolor=background,
            cursor="hand2" if self._enabled and not self._value else "arrow",
        )


class DirectoryListEditor(tk.Frame):
    """最多九项的目录列表；每项可编辑标签并单独移除。"""

    def __init__(self, master: tk.Misc, *, initial: object = "",
                 title: str = "档案根目录", on_change=None,
                 max_items: int = _MAX_ROOT_DIRECTORIES) -> None:
        super().__init__(
            master, bg=_SURFACE,
        )
        self.title = title
        self.on_change = on_change
        self.max_items = max_items
        self._items = _lines(initial)[:max_items]
        self._variables: list[tk.StringVar] = []
        self._last_directory = _BASE
        self.grid_columnconfigure(0, weight=1)

        self.rows = tk.Frame(self, bg=_SURFACE)
        self.rows.grid(row=1, column=0, sticky="ew", pady=(2, 4))
        self.rows.grid_columnconfigure(0, weight=1)

        footer = tk.Frame(self, bg=_SURFACE)
        footer.grid(row=0, column=0, sticky="ew", pady=(0, 2))
        footer.grid_columnconfigure(1, weight=1)
        self.add_button = ttk.Button(
            footer, text="添加", style="FilePicker.TButton",
            width=_FILE_PICKER_BUTTON_WIDTH,
            command=self.add_directory,
        )
        self.add_button.grid(row=0, column=0, sticky="w")
        attach_tooltip(
            self.add_button,
            f"选择并加入一个{self.title}；最多可添加 {self.max_items} 个目录。",
        )
        self.count_label = tk.Label(
            footer, bg=_SURFACE, fg=_MUTED,
            font=("Microsoft YaHei UI", 8), anchor="w",
        )
        self.count_label.grid(
            row=0, column=1, sticky="w",
            padx=(_INLINE_CONTROL_GAP, 0))
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
            self.rows.grid_remove()
        else:
            self.rows.grid()
        for index, item in enumerate(self._items):
            row = tk.Frame(self.rows, bg=_SURFACE)
            row.grid(row=index, column=0, sticky="ew", pady=(0, 4))
            row.grid_columnconfigure(1, weight=1)
            tk.Label(
                row, text=f"{index + 1}", width=2,
                bg=_CONTROL, fg=_GREEN_DEEP,
                font=("Microsoft YaHei UI", 8, "bold"),
            ).grid(
                row=0, column=0, sticky="ns",
                padx=(0, _INLINE_CONTROL_GAP),
            )
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
            remove_button.grid(
                row=0, column=2, padx=(_INLINE_CONTROL_GAP, 0))
            attach_tooltip(remove_button, f"从列表中移除第 {index + 1} 个目录。")
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
                "目录已经添加", f"该目录已在列表中：\n{value}",
                parent=self.winfo_toplevel())
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


class RootLabelMapEditor(tk.Frame):
    """以左右配对表编辑快照根目录名，避免要求用户记忆语法。"""

    def __init__(
        self, master: tk.Misc, *, initial: object = "", on_change=None,
        max_items: int = _MAX_ROOT_DIRECTORIES,
    ) -> None:
        super().__init__(master, bg=_SURFACE)
        self.on_change = on_change
        self.max_items = max_items
        self._items = [
            tuple(part.strip() for part in line.partition("=")[::2])
            if "=" in line else (line.strip(), "")
            for line in _lines(initial)
        ][:max_items]
        self._variables: list[tuple[tk.StringVar, tk.StringVar]] = []
        self.grid_columnconfigure(0, weight=1, uniform="root_label_map")
        self.grid_columnconfigure(1, minsize=32)
        self.grid_columnconfigure(2, weight=1, uniform="root_label_map")

        self.hint_label = tk.Label(
            self,
            text=(
                "填写快照中记录的根目录名。通常是建库时所选文件夹名；"
                "如曾自定义，则填写自定义名称。单根快照通常无需设置。"
            ),
            bg=_SURFACE, fg=_MUTED,
            font=("Microsoft YaHei UI", 9), anchor="w",
        )
        self.hint_label.grid(
            row=0, column=0, columnspan=4, sticky="ew", pady=(0, 5))
        self.old_header = tk.Label(
            self, text="基准根目录名", bg=_SURFACE, fg=_TEXT,
            font=("Microsoft YaHei UI", 9, "bold"), anchor="w",
        )
        self.old_header.grid(row=1, column=0, sticky="w", pady=(0, 3))
        self.new_header = tk.Label(
            self, text="对比根目录名", bg=_SURFACE, fg=_TEXT,
            font=("Microsoft YaHei UI", 9, "bold"), anchor="w",
        )
        self.new_header.grid(row=1, column=2, sticky="w", pady=(0, 3))

        self.old_input = tk.StringVar()
        self.new_input = tk.StringVar()
        self.old_entry = ttk.Entry(self, textvariable=self.old_input)
        self.old_entry.grid(row=2, column=0, sticky="ew")
        self.arrow_label = tk.Label(
            self, text="→", bg=_SURFACE, fg=_AMBER_DEEP,
            font=("Microsoft YaHei UI", 10, "bold"), anchor="center",
        )
        self.arrow_label.grid(row=2, column=1, sticky="ew")
        self.new_entry = ttk.Entry(self, textvariable=self.new_input)
        self.new_entry.grid(row=2, column=2, sticky="ew")
        self.add_button = ttk.Button(
            self, text="添加配对", style="FormAction.TButton",
            width=_FORM_ACTION_BUTTON_WIDTH, command=self.add_pair,
        )
        self.add_button.grid(
            row=2, column=3,
            padx=(_INLINE_CONTROL_GAP, 0), sticky="e")
        self.old_entry.bind("<Return>", lambda _event: self.add_pair())
        self.new_entry.bind("<Return>", lambda _event: self.add_pair())

        self.rows = tk.Frame(self, bg=_SURFACE)
        self.rows.grid(row=3, column=0, columnspan=4, sticky="ew", pady=(5, 0))
        self.rows.grid_columnconfigure(0, weight=1)
        self._render_rows()

    @property
    def tooltip_widgets(self) -> tuple[tk.Misc, ...]:
        static_widgets: tuple[tk.Misc, ...] = (
            self, self.hint_label, self.old_header, self.new_header,
            self.old_entry, self.arrow_label, self.new_entry, self.add_button,
        )
        return (*static_widgets, *self.rows.winfo_children())

    def _notify(self) -> None:
        if callable(self.on_change):
            self.on_change()

    def _sync_items(self) -> None:
        if self._variables:
            self._items = [
                (old.get().strip(), new.get().strip())
                for old, new in self._variables
            ]

    def _render_rows(self) -> None:
        for child in self.rows.winfo_children():
            child.destroy()
        self._variables = []
        if not self._items:
            self.rows.grid_remove()
        else:
            self.rows.grid()
        for index, (old_label, new_label) in enumerate(self._items):
            row = tk.Frame(self.rows, bg=_SURFACE)
            row.grid(row=index, column=0, sticky="ew", pady=(0, 4))
            row.grid_columnconfigure(1, weight=1, uniform="root_label_pair")
            row.grid_columnconfigure(3, weight=1, uniform="root_label_pair")
            tk.Label(
                row, text=f"{index + 1}", width=2,
                bg=_CONTROL, fg=_AMBER_DEEP,
                font=("Microsoft YaHei UI", 8, "bold"),
            ).grid(
                row=0, column=0, sticky="ns",
                padx=(0, _INLINE_CONTROL_GAP),
            )
            old_variable = tk.StringVar(value=old_label)
            new_variable = tk.StringVar(value=new_label)
            old_variable.trace_add(
                "write", lambda *_args, i=index, v=old_variable:
                self._edited(i, 0, v))
            new_variable.trace_add(
                "write", lambda *_args, i=index, v=new_variable:
                self._edited(i, 1, v))
            self._variables.append((old_variable, new_variable))
            ttk.Entry(row, textvariable=old_variable).grid(
                row=0, column=1, sticky="ew")
            tk.Label(
                row, text="→", bg=_SURFACE, fg=_AMBER_DEEP,
                font=("Microsoft YaHei UI", 10, "bold"),
            ).grid(row=0, column=2, padx=_INLINE_CONTROL_GAP)
            ttk.Entry(row, textvariable=new_variable).grid(
                row=0, column=3, sticky="ew")
            remove_button = ttk.Button(
                row, text="×", width=3, style="Remove.TButton",
                command=lambda i=index: self.remove(i),
            )
            remove_button.grid(
                row=0, column=4, padx=(_INLINE_CONTROL_GAP, 0))
            attach_tooltip(
                remove_button, f"移除第 {index + 1} 组根目录配对。")
        self.add_button.configure(
            state="disabled" if len(self._items) >= self.max_items else "normal")

    def _edited(
        self, index: int, side: int, variable: tk.StringVar,
    ) -> None:
        if index >= len(self._items):
            return
        pair = list(self._items[index])
        pair[side] = variable.get()
        self._items[index] = (pair[0], pair[1])
        self._notify()

    def add_pair(self) -> bool:
        old_label = self.old_input.get().strip()
        new_label = self.new_input.get().strip()
        if not old_label or not new_label:
            messagebox.showwarning(
                "根目录名未配齐",
                "请同时填写基准与对比快照的根目录名。",
                parent=self.winfo_toplevel(),
            )
            (self.old_entry if not old_label else self.new_entry).focus_set()
            return False
        self._sync_items()
        if len(self._items) >= self.max_items:
            messagebox.showwarning(
                "配对数量已达上限",
                f"最多只能添加 {self.max_items} 组根目录配对。",
                parent=self.winfo_toplevel(),
            )
            return False
        if any(old_label == existing_old
               for existing_old, _existing_new in self._items):
            messagebox.showinfo(
                "基准根目录已经添加",
                f"该基准根目录名已在配对列表中：\n{old_label}",
                parent=self.winfo_toplevel(),
            )
            return False
        self._items.append((old_label, new_label))
        self.old_input.set("")
        self.new_input.set("")
        self._render_rows()
        self._notify()
        self.old_entry.focus_set()
        return True

    def remove(self, index: int) -> None:
        self._sync_items()
        if 0 <= index < len(self._items):
            self._items.pop(index)
            self._render_rows()
            self._notify()

    def get(self) -> str:
        self._sync_items()
        return "\n".join(
            f"{old_label}={new_label}"
            for old_label, new_label in self._items
            if old_label or new_label
        )


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
        self.slot_frames: list[tk.Frame] = []
        self._checkbox_images: dict[str, tk.PhotoImage] = {}
        self.grid_columnconfigure(0, weight=1)

        actions = tk.Frame(self, bg=_SURFACE)
        actions.grid(
            row=0, column=0, sticky="ew",
            padx=_SPACING_INLINE, pady=(5, 2))
        actions.grid_columnconfigure(2, weight=1)
        self.select_all_button = ttk.Button(
            actions, text="全选", style="FormAction.TButton",
            width=_FORM_ACTION_BUTTON_WIDTH,
            command=self.select_all_online,
        )
        self.select_all_button.grid(
            row=0, column=0, sticky="w",
            padx=(0, _STANDARD_BUTTON_GAP))
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
            "选择本次清单中所有联机且信息完整的硬盘。",
        )
        attach_tooltip(
            self.clear_selection_button,
            "取消本次清单中的全部选择。",
        )

        rows = tk.Frame(self, bg=_SURFACE)
        rows.grid(
            row=1, column=0, sticky="ew",
            padx=_SPACING_INLINE, pady=(1, 5))
        rows.grid_columnconfigure(0, weight=1)
        if not options:
            tk.Label(
                rows, text="尚无硬盘清单，请先点击「检测硬盘」。",
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
            rows.grid_rowconfigure(
                row_index, minsize=44, uniform="storage_disk_slot")
            row = tk.Frame(
                rows, bg=_FIELD,
                highlightbackground=_BORDER, highlightthickness=1,
            )
            row.grid(
                row=row_index, column=0, sticky="nsew",
                pady=(0, _SPACING_COMPACT),
            )
            row.grid_columnconfigure(0, weight=1)
            row.grid_rowconfigure(0, weight=1)
            self.slot_frames.append(row)
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
                bg=_FIELD, activebackground=_FIELD,
                fg=_TEXT, activeforeground=_TEXT,
                disabledforeground=_MUTED, selectcolor=_FIELD,
                font=("Microsoft YaHei UI", 9), anchor="w",
                justify="left", wraplength=650,
                highlightthickness=0, bd=0, relief="flat",
                offrelief="flat", overrelief="flat", padx=8, pady=6,
            )
            checkbox.grid(row=0, column=0, sticky="nsew")
            self.checkboxes.append(checkbox)
            status_colour = _GREEN_DEEP if option.selectable else _MUTED
            tk.Label(
                row, text=option.reason, bg=_FIELD, fg=status_colour,
                font=("Microsoft YaHei UI", 8), anchor="e",
            ).grid(row=0, column=1, sticky="e", padx=(10, 8))
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


class ChoiceButtonGroup(tk.Frame):
    """用一组紧凑按钮选择模式；重复点击不会改变尺寸或清空文字。"""

    def __init__(
        self,
        master: tk.Misc,
        *,
        choices: tuple[tuple[str, object], ...],
        initial: object = "",
        on_change=None,
    ) -> None:
        super().__init__(master, bg=_SURFACE)
        self.choices = choices
        self.on_change = on_change
        allowed = {str(value) for _label, value in choices}
        initial_value = str(initial or "")
        self.variable = tk.StringVar(
            value=initial_value if initial_value in allowed else "")
        self.buttons: dict[str, tk.Button] = {}
        for column, (label, raw_value) in enumerate(choices):
            value = str(raw_value)
            button = tk.Button(
                self, text=label, width=_SCAN_MODE_BUTTON_WIDTH,
                relief="flat", bd=0, highlightthickness=1,
                font=("Microsoft YaHei UI", _UI_BODY_FONT_SIZE),
                anchor="center",
                padx=_STANDARD_BUTTON_PADDING[0],
                pady=_STANDARD_BUTTON_PADDING[1],
                takefocus=True,
                command=lambda selected=value: self._choose(selected),
            )
            button.grid(
                row=0, column=column, sticky="w",
                padx=(0, _STANDARD_BUTTON_GAP
                      if column < len(choices) - 1 else 0),
            )
            self.buttons[value] = button
        self.tooltip_widgets = (self, *self.buttons.values())
        self._refresh()

    def _choose(self, value: str) -> None:
        changed = self.variable.get() != value
        self.variable.set(value)
        self._refresh()
        if changed and callable(self.on_change):
            self.on_change()

    def _refresh(self) -> None:
        selected_value = self.variable.get()
        for value, button in self.buttons.items():
            selected = value == selected_value
            background = _BLOCK_SELECTION_BACKGROUND if selected else _CONTROL
            foreground = _BLOCK_SELECTION_FOREGROUND if selected else _TEXT
            button.configure(
                bg=background,
                fg=foreground,
                activebackground=(
                    _BLOCK_SELECTION_HOVER if selected else _CONTROL_HOVER),
                activeforeground=(
                    _BLOCK_SELECTION_FOREGROUND if selected else _TEXT),
                highlightbackground=(
                    _BLOCK_SELECTION_BACKGROUND if selected else _BORDER),
                highlightcolor=(
                    _BLOCK_SELECTION_BACKGROUND if selected else _BORDER),
            )

    def get(self) -> str:
        return self.variable.get()


class BooleanToggleButton(tk.Frame):
    """固定尺寸二元按钮；状态变化不改变按钮外框。"""

    def __init__(
        self,
        master: tk.Misc,
        *,
        choices: tuple[tuple[str, object], ...],
        initial: object = False,
        on_change=None,
        enabled: bool = True,
    ) -> None:
        super().__init__(master, bg=_SURFACE)
        by_value = {bool(value): label for label, value in choices}
        self.false_label = by_value.get(False, "关闭")
        self.true_label = by_value.get(True, "启用")
        self.variable = tk.BooleanVar(value=bool(initial))
        self.on_change = on_change
        self.enabled = bool(enabled)
        self.button = tk.Button(
            self, relief="flat", bd=0, highlightthickness=1,
            font=("Microsoft YaHei UI", _UI_BODY_FONT_SIZE),
            anchor="center", justify="center",
            width=_BOOLEAN_BUTTON_WIDTH,
            padx=_STANDARD_BUTTON_PADDING[0],
            pady=_STANDARD_BUTTON_PADDING[1],
            takefocus=True,
            command=self._toggle,
        )
        self.button.pack(anchor="w")
        self.tooltip_widgets = (self, self.button)
        self._refresh()

    def _toggle(self) -> None:
        if not self.enabled:
            return
        self.variable.set(not self.variable.get())
        self._refresh()
        if callable(self.on_change):
            self.on_change()

    def _refresh(self) -> None:
        selected = bool(self.variable.get())
        if not self.enabled:
            background, foreground = _CONTROL, _MUTED
            state = "disabled"
        elif selected:
            background, foreground = _GREEN_DARK, "white"
            state = "normal"
        else:
            background, foreground = _AMBER, _AMBER_DEEP
            state = "normal"
        self.button.configure(
            text=self.true_label if selected else self.false_label,
            state=state,
            bg=background,
            fg=foreground,
            activebackground=(
                _GREEN_DEEP if selected else _AMBER_SOFT),
            activeforeground=foreground,
            disabledforeground=foreground,
            highlightbackground=background,
            highlightcolor=background,
        )

    def get(self) -> bool:
        return bool(self.variable.get())


class ValueToggleButton(tk.Frame):
    """固定尺寸二态值按钮；关闭态与开启态可映射到非布尔 CLI 值。"""

    def __init__(
        self,
        master: tk.Misc,
        *,
        choices: tuple[tuple[str, object], ...],
        initial: object,
        on_change=None,
    ) -> None:
        if len(choices) != 2:
            raise ValueError("ValueToggleButton 必须且只能有两个选项")
        super().__init__(master, bg=_SURFACE)
        (self.off_label, off_value), (self.on_label, on_value) = choices
        self.off_value = str(off_value)
        self.on_value = str(on_value)
        initial_value = str(initial)
        self.variable = tk.StringVar(
            value=(initial_value if initial_value in (
                self.off_value, self.on_value) else self.off_value),
        )
        self.on_change = on_change
        self.button = tk.Button(
            self, relief="flat", bd=0, highlightthickness=1,
            font=("Microsoft YaHei UI", _UI_BODY_FONT_SIZE),
            anchor="center", width=_BOOLEAN_BUTTON_WIDTH,
            padx=_STANDARD_BUTTON_PADDING[0],
            pady=_STANDARD_BUTTON_PADDING[1], takefocus=True,
            command=self._toggle,
        )
        self.button.pack(anchor="w")
        self.tooltip_widgets = (self, self.button)
        self._refresh()

    def _toggle(self) -> None:
        self.variable.set(
            self.off_value
            if self.variable.get() == self.on_value else self.on_value)
        self._refresh()
        if callable(self.on_change):
            self.on_change()

    def _refresh(self) -> None:
        selected = self.variable.get() == self.on_value
        background = _GREEN_DARK if selected else _AMBER
        foreground = "white" if selected else _AMBER_DEEP
        self.button.configure(
            text=self.on_label if selected else self.off_label,
            bg=background, fg=foreground,
            activebackground=_GREEN_DEEP if selected else _AMBER_SOFT,
            activeforeground=foreground,
            disabledforeground=foreground,
            highlightbackground=background,
            highlightcolor=background,
        )

    def get(self) -> str:
        return self.variable.get()


class MetadataToolButtonGroup(tk.Frame):
    """以两个循环按钮分别选择 ExifTool 与 ffprobe 的采集范围。"""

    def __init__(
        self,
        master: tk.Misc,
        *,
        exiftool_mode: object,
        ffprobe_mode: object,
        on_change=None,
    ) -> None:
        super().__init__(master, bg=_SURFACE)
        self.on_change = on_change
        self.exiftool_button = MetadataToolModeButton(
            self,
            tool_label="ExifTool",
            initial=exiftool_mode,
            on_change=self._notify,
        )
        self.ffprobe_button = MetadataToolModeButton(
            self,
            tool_label="ffprobe",
            initial=ffprobe_mode,
            on_change=self._notify,
        )
        self.grid_columnconfigure(1, minsize=_STANDARD_BUTTON_GAP)
        self.exiftool_button.grid(row=0, column=0, sticky="w")
        self.ffprobe_button.grid(row=0, column=2, sticky="w")
        self.tooltip_widgets = (
            self,
            *self.exiftool_button.tooltip_widgets,
            *self.ffprobe_button.tooltip_widgets,
        )

    def _notify(self) -> None:
        if callable(self.on_change):
            self.on_change()

    def get_values(self) -> dict[str, object]:
        exiftool_mode = self.exiftool_button.get()
        ffprobe_mode = self.ffprobe_button.get()
        return {
            "metadata_storage": (
                "complete"
                if "complete" in (exiftool_mode, ffprobe_mode)
                else "normalized"
            ),
            "metadata_exiftool": exiftool_mode != "off",
            "metadata_ffprobe": ffprobe_mode != "off",
            "metadata_exiftool_mode": exiftool_mode,
            "metadata_ffprobe_mode": ffprobe_mode,
        }


class MetadataToolModeButton(tk.Frame):
    """单按钮循环切换元数据工具的全量、基础与关闭状态。"""

    _MODE_ORDER = ("complete", "normalized", "off")
    _MODE_LABELS = {
        "complete": "全量",
        "normalized": "基础",
        "off": "关闭",
    }

    def __init__(
        self, master: tk.Misc, *, tool_label: str, initial: object,
        on_change=None,
    ) -> None:
        super().__init__(master, bg=_SURFACE)
        initial_mode = str(initial or "complete")
        if initial_mode not in self._MODE_ORDER:
            initial_mode = "complete"
        self.tool_label = tool_label
        self.variable = tk.StringVar(value=initial_mode)
        self.on_change = on_change
        self.button = tk.Button(
            self, width=_STANDARD_BUTTON_WIDTH,
            relief="flat", bd=0, highlightthickness=1,
            font=("Microsoft YaHei UI", _UI_BODY_FONT_SIZE),
            anchor="center", justify="center",
            padx=_STANDARD_BUTTON_PADDING[0],
            pady=_STANDARD_BUTTON_PADDING[1],
            takefocus=True, command=self._advance,
        )
        self.button.pack()
        self.tooltip_widgets = (self, self.button)
        self._refresh()

    def _advance(self) -> None:
        current = self.variable.get()
        index = self._MODE_ORDER.index(current)
        self.variable.set(self._MODE_ORDER[(index + 1) % len(
            self._MODE_ORDER)])
        self._refresh()
        if callable(self.on_change):
            self.on_change()

    def _refresh(self) -> None:
        mode = self.variable.get()
        if mode == "complete":
            background, foreground = _GREEN_DARK, "white"
            active_background = _GREEN_DEEP
        elif mode == "normalized":
            background, foreground = _OLIVE, _OLIVE_DEEP
            active_background = _OLIVE_SOFT
        else:
            background, foreground = _AMBER, _AMBER_DEEP
            active_background = _AMBER_SOFT
        self.button.configure(
            text=f"{self.tool_label} {self._MODE_LABELS[mode]}",
            bg=background, fg=foreground,
            activebackground=active_background,
            activeforeground=foreground,
            highlightbackground=background,
            highlightcolor=background,
        )

    def get(self) -> str:
        return self.variable.get()


class VerificationToolButtonGroup(tk.Frame):
    """优先一行显示五个等尺寸按钮，空间不足时按完整按钮自动换行。"""

    def __init__(
        self,
        master: tk.Misc,
        *,
        initial: dict[str, object],
        raw_enabled: bool,
        raw_reason: str,
        on_change=None,
    ) -> None:
        super().__init__(master, bg=_SURFACE)
        self.on_change = on_change
        self.controls: dict[str, BooleanToggleButton] = {}
        self._ordered_controls: list[BooleanToggleButton] = []
        self._layout_columns = 0
        self._requested_raw = bool(initial.get("raw_deep_validation", True))
        definitions = (
            (
                "verify_builtin", "内置格式校验", True,
                "使用 DAISY 内置校验器检查 ZIP/OOXML 的 CRC，以及 PDF 的基本结构。",
            ),
            (
                "verify_exiftool", "ExifTool", True,
                "使用 ExifTool 检查适用图片、RAW 和媒体文件的格式与元数据结构。",
            ),
            (
                "verify_ffprobe", "ffprobe", True,
                "使用 ffprobe 检查 GIF、视频和音频的容器与媒体流。",
            ),
            (
                "verify_sevenzip", "7-Zip", True,
                "使用 7-Zip 检查压缩包和旧 Office OLE 容器。",
            ),
            (
                "raw_deep_validation", "rawpy/LibRaw", raw_enabled,
                "使用独立的 rawpy/LibRaw 子进程检查 RAW 文件能否实际解码。"
                + ("" if raw_enabled else f" 当前不可用：{raw_reason}"),
            ),
        )
        for key, label, enabled, help_text in definitions:
            control = BooleanToggleButton(
                self,
                choices=((label, False), (label, True)),
                initial=(
                    self._requested_raw
                    if key == "raw_deep_validation" and enabled else
                    False
                    if key == "raw_deep_validation" else
                    bool(initial.get(key, True))
                ),
                on_change=self._notify,
                enabled=enabled,
            )
            for target in control.tooltip_widgets:
                attach_tooltip(target, help_text)
            self.controls[key] = control
            self._ordered_controls.append(control)
        # 先使用两列，避免控件的单排请求宽度反向撑破窄表单；首次 Configure
        # 会按实际可用宽度恢复默认五列，或保留必要的响应式换行。
        self._place_controls(2)
        self.bind("<Configure>", self._fit_to_width)
        self.tooltip_widgets = (
            self,
            *(target
              for control in self.controls.values()
              for target in control.tooltip_widgets),
        )

    def _place_controls(self, columns: int) -> None:
        columns = max(1, min(int(columns), len(self._ordered_controls)))
        if columns == self._layout_columns:
            return
        for control in self._ordered_controls:
            control.grid_forget()
        for column in range(len(self._ordered_controls) * 2 - 1):
            self.grid_columnconfigure(column, minsize=0)
        rows = (
            len(self._ordered_controls) + columns - 1
        ) // columns
        for index, control in enumerate(self._ordered_controls):
            row, column = divmod(index, columns)
            control.grid(
                row=row, column=column * 2, sticky="w",
                pady=(0, _STANDARD_BUTTON_GAP if row < rows - 1 else 0),
            )
        for column in range(columns - 1):
            self.grid_columnconfigure(
                column * 2 + 1, minsize=_STANDARD_BUTTON_GAP)
        self._layout_columns = columns

    def _fit_to_width(self, _event: tk.Event | None = None) -> None:
        if not self._ordered_controls:
            return
        try:
            available = max(1, self.winfo_width())
            button_width = max(
                control.winfo_reqwidth()
                for control in self._ordered_controls
            )
        except tk.TclError:
            return
        columns = max(1, min(
            len(self._ordered_controls),
            (available + _STANDARD_BUTTON_GAP)
            // max(1, button_width + _STANDARD_BUTTON_GAP),
        ))
        self._place_controls(columns)

    def _notify(self) -> None:
        raw_control = self.controls.get("raw_deep_validation")
        if raw_control is not None and raw_control.enabled:
            self._requested_raw = raw_control.get()
        if callable(self.on_change):
            self.on_change()

    def get_values(self) -> dict[str, bool]:
        return {
            key: control.get() if control.enabled else False
            for key, control in self.controls.items()
        }

    def get_persisted_values(self) -> dict[str, bool]:
        values = self.get_values()
        values["raw_deep_validation"] = self._requested_raw
        return values


class MultiChoicePool(tk.Frame):
    """用固定尺寸按钮显示一组可同时启用的稳定值。"""

    def __init__(
        self,
        master: tk.Misc,
        *,
        choices: tuple[tuple[str, object], ...],
        initial: object = "",
        on_change=None,
    ) -> None:
        super().__init__(master, bg=_SURFACE)
        selected = set(_lines(initial))
        self.choices = choices
        self.on_change = on_change
        self.variables: dict[str, tk.BooleanVar] = {}
        self.buttons: dict[str, tk.Button] = {}
        for column, (label, raw_value) in enumerate(choices):
            value = str(raw_value)
            variable = tk.BooleanVar(value=value in selected)
            self.variables[value] = variable
            button = tk.Button(
                self, text=label, width=_STANDARD_BUTTON_WIDTH,
                relief="flat", bd=0, highlightthickness=1,
                font=("Microsoft YaHei UI", _UI_BODY_FONT_SIZE),
                anchor="center", justify="center",
                padx=_STANDARD_BUTTON_PADDING[0],
                pady=_STANDARD_BUTTON_PADDING[1], takefocus=True,
                command=lambda selected_value=value:
                self._toggle(selected_value),
            )
            button.grid(
                row=0, column=column, sticky="w",
                padx=(0, _STANDARD_BUTTON_GAP
                      if column < len(choices) - 1 else 0),
            )
            self.buttons[value] = button
        self.tooltip_widgets = (self, *self.buttons.values())
        self._refresh()

    def _toggle(self, value: str) -> None:
        variable = self.variables[value]
        variable.set(not variable.get())
        self._refresh()
        self._notify()

    def _refresh(self) -> None:
        for value, button in self.buttons.items():
            selected = bool(self.variables[value].get())
            background = _GREEN_DARK if selected else _AMBER
            foreground = "white" if selected else _AMBER_DEEP
            button.configure(
                bg=background, fg=foreground,
                activebackground=(
                    _GREEN_DEEP if selected else _AMBER_SOFT),
                activeforeground=foreground,
                highlightbackground=background,
                highlightcolor=background,
            )

    def _notify(self) -> None:
        if callable(self.on_change):
            self.on_change()

    def get(self) -> str:
        return "\n".join(
            str(value) for _label, value in self.choices
            if self.variables[str(value)].get()
        )


class ParseModulePool(tk.Frame):
    """按 Reader 能力状态展示解析模块；不可用项保持禁用。"""

    _STATE_LABELS = dbparse.PARSE_MODULE_STATE_LABELS

    def __init__(
        self,
        master: tk.Misc,
        *,
        inspection: dbparse.ParseDatabaseInspection | None,
        preset: str,
        initial: object = "",
        on_change=None,
    ) -> None:
        super().__init__(
            master, bg=_SURFACE, highlightbackground=_BORDER,
            highlightthickness=1,
        )
        self.inspection = inspection
        self.preset = str(preset or "full-audit")
        self.editable = self.preset == "custom"
        self.on_change = on_change
        self.variables: dict[str, tk.BooleanVar] = {}
        self.buttons: dict[str, tk.Button] = {}
        self.cards: list[tk.Button] = []
        if inspection is None:
            tk.Label(
                self, text="请先选择并解析输入数据库。",
                bg=_SURFACE, fg=_MUTED, anchor="w",
                font=("Microsoft YaHei UI", 9),
            ).pack(fill="x", padx=10, pady=9)
            return

        requested = set(_lines(initial))
        if not self.editable:
            requested = {
                module.spec.module_id for module in inspection.modules
                if module.selectable and self.preset in module.spec.presets
            }

        self.actions = tk.Frame(self, bg=_SURFACE)
        all_button = ttk.Button(
            self.actions, text="全选", style="FormAction.TButton",
            width=_FORM_ACTION_BUTTON_WIDTH, command=self.select_all,
        )
        all_button.pack(
            side="left", padx=(0, _STANDARD_BUTTON_GAP))
        clear_button = ttk.Button(
            self.actions, text="取消选择", style="FormAction.TButton",
            width=_FORM_ACTION_BUTTON_WIDTH, command=self.clear_selection,
        )
        clear_button.pack(side="left")
        if self.editable:
            self.actions.pack(fill="x", padx=7, pady=(4, 2))

        self.card_host = tk.Frame(self, bg=_SURFACE)
        self.card_host.pack(
            fill="x", padx=_SPACING_INLINE, pady=(0, 6))
        for module in inspection.modules:
            module_id = module.spec.module_id
            selected = module.selectable and module_id in requested
            variable = tk.BooleanVar(value=selected)
            self.variables[module_id] = variable
            button = tk.Button(
                self.card_host, text=module.spec.title,
                width=_STANDARD_BUTTON_WIDTH,
                relief="flat", bd=0, highlightthickness=1,
                font=("Microsoft YaHei UI", _UI_BODY_FONT_SIZE),
                anchor="center", justify="center",
                padx=_STANDARD_BUTTON_PADDING[0],
                pady=_STANDARD_BUTTON_PADDING[1],
                takefocus=True,
                command=lambda item=module: self._toggle_module(item),
            )
            button._daisy_module = module  # type: ignore[attr-defined]
            self.buttons[module_id] = button
            self.cards.append(button)
            self._refresh_module_button(module)
            detail_parts = [module.spec.description.rstrip("。")]
            if module.row_count is not None:
                detail_parts.append(f"共 {module.row_count:,} 条记录")
            if not module.selectable:
                detail_parts.append(
                    self._STATE_LABELS.get(module.state, module.state))
            if module.reason:
                detail_parts.append(str(module.reason).rstrip("。"))
            detail = "；".join(part for part in detail_parts if part) + "。"
            attach_tooltip(button, detail)
        self.card_host.bind("<Configure>", self._layout_cards)
        self.after_idle(self._layout_cards)

    def invalidate(self) -> None:
        """输入路径变化后原位清除旧识别结果，不读取新数据库。"""
        if self.inspection is None:
            return
        self.inspection = None
        self.editable = False
        self.actions.pack_forget()
        for child in self.card_host.winfo_children():
            child.destroy()
        self.variables.clear()
        self.buttons.clear()
        self.cards.clear()
        tk.Label(
            self.card_host, text="请点击「解析数据库」识别当前输入。",
            bg=_SURFACE, fg=_MUTED, anchor="w",
            font=("Microsoft YaHei UI", 9),
        ).pack(fill="x", padx=2, pady=7)

    def set_preset(
        self, preset: str, *, initial: object | None = None,
    ) -> None:
        """原位切换导出预设，保留模块控件，避免整页销毁造成闪烁。"""
        self.preset = str(preset or "full-audit")
        self.editable = self.preset == "custom"
        if self.inspection is None:
            return
        requested = set(_lines(self.get() if initial is None else initial))
        if not self.editable:
            requested = {
                module.spec.module_id
                for module in self.inspection.modules
                if module.selectable and self.preset in module.spec.presets
            }
        for module in self.inspection.modules:
            module_id = module.spec.module_id
            self.variables[module_id].set(
                module.selectable and module_id in requested)
            self._refresh_module_button(module)
        if self.editable:
            if not self.actions.winfo_manager():
                self.actions.pack(
                    fill="x", padx=7, pady=(4, 2),
                    before=self.card_host,
                )
        else:
            self.actions.pack_forget()
        self.after_idle(self._layout_cards)

    def _toggle_module(self, module: dbparse.ParseModuleStatus) -> None:
        if not self.editable or not module.selectable:
            return
        variable = self.variables[module.spec.module_id]
        variable.set(not variable.get())
        self._refresh_module_button(module)
        self._notify()

    def _refresh_module_button(
        self, module: dbparse.ParseModuleStatus,
    ) -> None:
        module_id = module.spec.module_id
        button = self.buttons[module_id]
        selected = bool(self.variables[module_id].get())
        if not module.selectable:
            background, foreground = _CONTROL, _MUTED
            active_background, border = _CONTROL_HOVER, _BORDER
            state = "disabled"
        elif selected:
            background, foreground = _GREEN_DARK, "white"
            active_background, border = _GREEN_DEEP, _GREEN_DARK
            state = "normal" if self.editable else "disabled"
        else:
            background, foreground = _AMBER, _AMBER_DEEP
            active_background, border = _AMBER_SOFT, _AMBER
            state = "normal" if self.editable else "disabled"
        button.configure(
            state=state,
            bg=background, fg=foreground,
            activebackground=active_background,
            activeforeground=foreground,
            disabledforeground=foreground,
            highlightbackground=border,
            highlightcolor=border,
        )

    def _layout_cards(self, event: tk.Event | None = None) -> None:
        width = int(event.width) if event is not None else self.card_host.winfo_width()
        required = max(
            (card.winfo_reqwidth() for card in self.cards), default=160)
        columns = max(1, min(8, max(1, width) // max(1, required + 4)))
        for column in range(8):
            self.card_host.grid_columnconfigure(
                column, weight=1 if column < columns else 0,
                uniform="parse_module" if column < columns else "",
            )
        for index, card in enumerate(self.cards):
            card.grid_forget()
            card.grid(
                row=index // columns, column=index % columns, sticky="nsew",
                padx=(0, 4 if index % columns < columns - 1 else 0),
                pady=(0, 4),
            )

    def _notify(self) -> None:
        if callable(self.on_change):
            self.on_change()

    def select_all(self) -> None:
        if self.inspection is None or not self.editable:
            return
        for module in self.inspection.modules:
            self.variables[module.spec.module_id].set(module.selectable)
            self._refresh_module_button(module)
        self._notify()

    def clear_selection(self) -> None:
        if not self.editable:
            return
        assert self.inspection is not None
        for module in self.inspection.modules:
            self.variables[module.spec.module_id].set(False)
            self._refresh_module_button(module)
        self._notify()

    def get(self) -> str:
        if self.inspection is None:
            return ""
        return "\n".join(
            module.spec.module_id for module in self.inspection.modules
            if self.variables[module.spec.module_id].get()
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
    widths: tuple[int, ...], available: int,
    gap: int = _STANDARD_BUTTON_GAP,
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
    return f"{core.PROJECT_NAME} {_version()} · {core.PROJECT_FULL_NAME}"


def about_message() -> str:
    """返回关于窗口的版本、功能域与兼容边界。"""
    return (
        f"{core.PROJECT_NAME} {_version()}\n"
        f"{core.PROJECT_FULL_NAME}\n"
        f"作者：{core.PROJECT_AUTHOR}\n"
        f"联系：{_PROJECT_CONTACT}\n\n"
        "环境：检测运行所需工具和可选能力。\n"
        "档案：建立与对比快照，按快照核验源文件并解析数据库。\n"
        "硬盘：只读登记硬盘、分区、卷与 SMART 信息并生成独立 ZIP。\n\n"
        "数据库与归档格式\n"
        f"统一扫描数据库结构版本：{dbstate.SCHEMA_VERSION}\n"
        f"旧版兼容快照结构版本：{core.SCHEMA_VERSION}\n"
        f"DBS 元数据配置版本：{metadata.PROFILE_VERSION}\n"
        f"DBS 文件名布局：{core.FILENAME_LAYOUT_VERSION}\n"
        f"STG 归档结构版本：{storage_core.ARCHIVE_SCHEMA_VERSION}\n"
        f"STG 文件名布局：{storage_core.FILENAME_LAYOUT_VERSION}\n\n"
        "兼容性\n"
        f"DBS 封存快照只读兼容基线：v{core.MIN_READER_VERSION}\n"
        "统一扫描续传：按数据库结构版本 4 的续传规则检查\n"
        "旧版兼容入口：仅续传数据库生成程序版本相同的结构版本 3 未完成快照\n\n"
        "快照数据库与硬盘档案彼此独立。业务数据留在本机；源档案和硬盘"
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
        self.completion_sound_enabled = bool(
            self.gui_preferences["completion_sound_enabled"])
        self.result_directory_prompt_enabled = bool(
            self.gui_preferences["result_directory_prompt_enabled"])
        self.recovery_scans = list(
            self.gui_preferences.get("recovery_scans") or [])
        self.task = TASK_BY_KEY[str(
            self.gui_preferences["last_task_key"])]
        self.values: dict[
            str, tk.Variable | tk.Text | DirectoryListEditor
            | RootLabelMapEditor
            | StorageDiskPool | ChoiceButtonGroup | BooleanToggleButton
            | ValueToggleButton
            | MetadataToolButtonGroup
            | MultiChoicePool | ParseModulePool] = {}
        self.saved_values = {
            str(task_key): dict(values)
            for task_key, values in dict(
                self.gui_preferences.get("task_options") or {}).items()
            if isinstance(values, dict)
        }
        self.task_menu_entries: dict[str, tuple[tk.Menu, int]] = {}
        self.task_toolbar_buttons: dict[str, tk.Button] = {}
        self._task_toolbar_layout_ready = False
        self.detected_tools: dict[str, dict[str, object]] = {}
        self.runtime_capabilities: dict[
            str, dict[str, object]
        ] = {}
        self.manual_tool_paths = dict(
            self.gui_preferences.get("manual_tool_paths") or {})
        self.install_tool_buttons: dict[str, tk.Button] = {}
        self.environment_install_buttons: dict[str, tk.Button] = {}
        self.environment_status_buttons: dict[str, tk.Button] = {}
        self.environment_status_tooltips: dict[str, ToolTip] = {}
        self.environment_missing_names: tuple[str, ...] = ()
        self.environment_missing_reasons: dict[str, str] = {}
        self.missing_installable_tools: tuple[str, ...] = ()
        self.is_administrator = is_windows_administrator()
        self.admin_mode_button: AdminModeButton | None = None
        self.storage_disk_choices: tuple[tuple[str, str], ...] = ()
        self.storage_disk_options: tuple[StorageDiskOption, ...] = ()
        self.parse_inspection: dbparse.ParseDatabaseInspection | None = None
        self.parse_inspection_path = ""
        self.parse_detection_generation = 0
        self.parse_detection_active = False
        self.parse_detect_button: ttk.Button | None = None
        self.parse_detection_detail_label: tk.Label | None = None
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
        self.dependency_version_query_output = ""
        self.pending_install_version_report: InstallVersionReport | None = None
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
        self.open_result_flash_after_id: str | None = None
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
        self.root.option_add(
            "*Font", (selected_family, _UI_BODY_FONT_SIZE))
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
        normalized_size = max(_UI_BODY_FONT_SIZE, int(base_size))
        value: list[object] = [
            self.ui_font_family,
            normalized_size + self.ui_font_size_delta,
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
            "TButton": (10, "normal"),
            "TLabel": (10, "normal"),
            "Muted.TLabel": (10, "normal"),
            "Badge.TLabel": (10, "bold"),
            "TEntry": (10, "normal"),
            "TCombobox": (10, "normal"),
            "Daisy.TCombobox": (10, "normal"),
            "Browse.TButton": (10, "normal"),
            "FormAction.TButton": (10, "normal"),
            "DiscoveryAction.TButton": (10, "normal"),
            "FilePicker.TButton": (10, "normal"),
            "Remove.TButton": (10, "normal"),
            "Primary.TButton": (10, "normal"),
            "Stop.TButton": (10, "normal"),
            "Secondary.TButton": (10, "normal"),
            "Mini.TButton": (10, "normal"),
            "PanelHeader.TButton": (10, "normal"),
            "MiniStop.TButton": (10, "normal"),
        }
        for style_name, (size, weight) in style_specs.items():
            self.style.configure(
                style_name, font=self._font_tuple(size, weight))

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
                normalized_size = max(_UI_BODY_FONT_SIZE, int(size))
                font_parts[1] = normalized_size + self.ui_font_size_delta
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
        self.root.option_add(
            "*Font", (self.ui_font_family, _UI_BODY_FONT_SIZE))
        self._apply_font_to_tree(self.root)
        for font_name, base_size in self._named_font_base_sizes.items():
            try:
                tkfont.nametofont(font_name, root=self.root).configure(
                    family=self.ui_font_family,
                    size=(max(_UI_BODY_FONT_SIZE, base_size)
                          + self.ui_font_size_delta),
                )
            except tk.TclError:
                continue
        self._apply_style_fonts()
        self._apply_menu_fonts()
        if hasattr(self, "form_inner"):
            self._configure_form_label_column()
        self.settings_title_expanded_font = self._font_tuple(
            14 if self.compact_layout else 16, "bold")
        self.title_label.configure(font=(
            self.settings_title_expanded_font
            if self.settings_expanded else self._font_tuple(9, "bold")
        ))
        self.root.update_idletasks()
        if hasattr(self, "task_toolbar_body"):
            self._fit_task_toolbar_buttons()

    def _save_gui_preferences(self) -> None:
        option_source = {
            str(task_key): dict(values)
            for task_key, values in getattr(
                self, "saved_values", {}).items()
            if isinstance(values, dict)
        }
        if getattr(self, "values", None) and hasattr(self, "task"):
            try:
                option_source[self.task.key] = self._collect_persistable_values()
            except (AttributeError, tk.TclError, TypeError, ValueError):
                pass
        self.gui_preferences.update({
            "window_size": list(self.default_window_size),
            "font_family": self.ui_font_family,
            "font_size_delta": self.ui_font_size_delta,
            "completion_sound_enabled": self.completion_sound_enabled,
            "result_directory_prompt_enabled": (
                getattr(self, "result_directory_prompt_enabled", False)),
            "last_task_key": self.task.key,
            "manual_tool_paths": _validated_manual_tool_paths(
                getattr(self, "manual_tool_paths", {})),
            "task_options": _validated_task_options(
                option_source),
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
            text=f"可续传 · {task_name}")
        display = self._middle_progress_text(
            os.path.basename(partial), 22)
        self.recovery_path_label.configure(text=display)
        self.recovery_path_tooltip.text = partial
        if not self.recovery_card.winfo_manager():
            self.recovery_card.pack(
                side="right", padx=(0, _STANDARD_BUTTON_GAP),
                before=self.settings_actions,
            )
        self._set_recovery_card_state()

    def _set_recovery_card_state(self) -> None:
        if not hasattr(self, "recovery_use_button"):
            return
        state = "disabled" if self._task_is_active() else "normal"
        self.recovery_use_button.configure(state=state)
        self.recovery_ignore_button.configure(state=state)

    def _add_recovery_scan(
        self, task_key: str, partial: str, scan_mode: str | None = None,
    ) -> None:
        if task_key not in _SCAN_TASK_KEYS or not partial:
            return
        if task_key == "full_scan":
            scan_mode = "full"
        elif task_key == "quick_scan":
            scan_mode = "quick"
        elif scan_mode not in ("full", "quick"):
            if self.run_jobs and self.run_job_index >= 0:
                scan_mode = str(self.run_jobs[
                    self.run_job_index].values.get("scan_mode") or "full")
            else:
                scan_mode = str(self.saved_values.get(
                    "scan", {}).get("scan_mode") or "full")
        if scan_mode not in ("full", "quick"):
            scan_mode = "full"
        normalized = os.path.abspath(partial)
        self.recovery_scans = [
            record for record in self.recovery_scans
            if not self._same_recovery_path(
                str(record.get("partial") or ""), normalized)
        ]
        self.recovery_scans.append({
            "task_key": "scan",
            "scan_mode": scan_mode,
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
        task_key = "scan"
        scan_mode = str(record.get("scan_mode") or "full")
        if scan_mode not in ("full", "quick"):
            scan_mode = "full"
        partial = str(record["partial"])
        if not messagebox.askyesno(
                "准备续传扫描",
                "这只会打开扫描页并填入未完成快照，不会立即开始任务或读取源档案。"
                f"\n\n{partial}\n\n继续吗？",
                icon="question", parent=self.root):
            return
        self.saved_values[task_key] = {
            "scan_mode": scan_mode,
            "start_mode": "resume",
            "resume": partial,
        }
        self._select_task(task_key)
        self._set_settings_expanded(True)
        self._set_status("续传设置已准备；核对后点击「开始任务」。", _WARNING)

    def _dismiss_latest_recovery(self) -> None:
        if not self.recovery_scans or self._task_is_active():
            return
        partial = str(self.recovery_scans[-1]["partial"])
        if not messagebox.askyesno(
                "忽略续传提示",
                "只移除 DAISY 的续传提示，不会删除未完成快照文件。"
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

    def _set_completion_sound(
        self, enabled: bool, *, persist: bool = True,
    ) -> None:
        self.completion_sound_enabled = bool(enabled)
        if hasattr(self, "completion_sound_enabled_var"):
            self.completion_sound_enabled_var.set(
                self.completion_sound_enabled)
        if persist:
            self._save_gui_preferences()

    def _set_result_directory_prompt(
        self, enabled: bool, *, persist: bool = True,
    ) -> None:
        """设置任务结束后是否询问打开结果目录。"""
        self.result_directory_prompt_enabled = bool(enabled)
        if hasattr(self, "result_directory_prompt_enabled_var"):
            self.result_directory_prompt_enabled_var.set(
                self.result_directory_prompt_enabled)
        if persist:
            self._save_gui_preferences()

    def _reset_current_task_settings(self) -> None:
        """恢复当前任务页默认值，不触及全局设置或业务文件。"""
        if self._task_is_active() or not self.task.fields:
            return
        self.saved_values.pop(self.task.key, None)
        if self.task.key == "parse_db":
            self.parse_inspection = None
            self.parse_inspection_path = ""
        self._build_form()
        self._refresh_scan_advanced_values()
        self._refresh_verify_advanced_values()
        self._refresh_diff_advanced_values()
        self._save_gui_preferences()
        self._set_status("当前页面已恢复默认。")

    def _reset_software_settings(self) -> None:
        """恢复 GUI 用户配置；不删除业务产物，也不卸载任何依赖。"""
        if self._task_is_active():
            messagebox.showinfo(
                "任务运行中",
                "请等待当前任务结束后再重置软件设置。",
                parent=self.root,
            )
            return
        if not messagebox.askyesno(
            "重置软件设置",
            "这会恢复默认窗口、字体和提示音，"
            "清除已保存的任务选项、手动工具路径与续传提示。\n\n"
            "不会卸载任何工具，也不会删除源档案、快照、报告或其他任务产物。"
            "确定继续吗？",
            icon="warning", parent=self.root,
        ):
            return
        defaults = default_gui_preferences()
        self.gui_preferences = defaults
        self.saved_values = {}
        self.manual_tool_paths = {}
        self.recovery_scans = []
        raw_size = defaults["window_size"]
        self.default_window_size = (int(raw_size[0]), int(raw_size[1]))
        self.ui_font_family = str(defaults["font_family"])
        self.ui_font_size_delta = int(defaults["font_size_delta"])
        self.completion_sound_enabled = bool(
            defaults["completion_sound_enabled"])
        self.result_directory_prompt_enabled = bool(
            defaults["result_directory_prompt_enabled"])
        self.default_window_size_var.set(
            f"{self.default_window_size[0]}x{self.default_window_size[1]}")
        self.ui_font_family_var.set(self.ui_font_family)
        self.ui_font_size_var.set(self.ui_font_size_delta)
        self.completion_sound_enabled_var.set(
            self.completion_sound_enabled)
        self.result_directory_prompt_enabled_var.set(
            self.result_directory_prompt_enabled)
        self._apply_interface_font_preferences()
        self._set_default_window_size(
            self.default_window_size, persist=False)
        self._refresh_tool_path_menu_labels()
        self._refresh_recovery_card()
        self._select_task("env_check", save_current=False)
        self._save_gui_preferences()
        self._set_status(
            "软件设置已恢复默认；源档案、已有结果和已安装工具未改变。")

    def _play_completion_sound(self) -> None:
        """播放非阻塞系统提示音；不可用时回退到 Tk 响铃。"""
        system_error: BaseException | None = None
        if os.name == "nt":
            try:
                import winsound
                winsound.PlaySound(
                    "SystemAsterisk",
                    winsound.SND_ALIAS
                    | winsound.SND_ASYNC
                    | winsound.SND_NODEFAULT,
                )
                return
            except (ImportError, OSError, RuntimeError) as exc:
                system_error = exc
        try:
            self.root.bell()
        except (AttributeError, OSError, RuntimeError, tk.TclError) as exc:
            detail = system_error or exc
            self._append_log(
                f"\n任务完成提示音播放失败：{detail}\n", "warning")

    def _configure_styles(self) -> None:
        self.style = ttk.Style(self.root)
        style = self.style
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("TFrame", background=_SURFACE)
        style.configure(
            "TButton", font=("Microsoft YaHei UI", _UI_BODY_FONT_SIZE))
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
            foreground=[
                ("disabled", _MUTED),
                ("readonly", _TEXT),
                ("!disabled", _TEXT),
            ],
            fieldbackground=[("readonly", _FIELD)],
            background=[("readonly", _FIELD), ("active", _CONTROL_HOVER)],
            selectbackground=[
                ("disabled", _FIELD),
                ("readonly", _FIELD),
                ("!disabled", _FIELD),
            ],
            selectforeground=[
                ("disabled", _MUTED),
                ("readonly", _TEXT),
                ("!disabled", _TEXT),
            ],
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
            foreground=[
                ("disabled", _MUTED),
                ("readonly", _TEXT),
                ("!disabled", _TEXT),
            ],
            fieldbackground=[("readonly", _FIELD)],
            background=[("readonly", _FIELD), ("active", _CONTROL_HOVER)],
            selectbackground=[
                ("disabled", _FIELD),
                ("readonly", _FIELD),
                ("!disabled", _FIELD),
            ],
            selectforeground=[
                ("disabled", _MUTED),
                ("readonly", _TEXT),
                ("!disabled", _TEXT),
            ],
        )
        style.configure(
            "Browse.TButton", background=_CONTROL, foreground=_TEXT,
            bordercolor=_BORDER, lightcolor=_BORDER, darkcolor=_BORDER,
            padding=_STANDARD_BUTTON_PADDING,
            font=("Microsoft YaHei UI", 10),
        )
        style.map(
            "Browse.TButton", background=[("active", _CONTROL_HOVER)])
        style.configure(
            "FormAction.TButton", background=_CONTROL, foreground=_TEXT,
            bordercolor=_BORDER, lightcolor=_BORDER, darkcolor=_BORDER,
            padding=_STANDARD_BUTTON_PADDING,
            font=("Microsoft YaHei UI", 10),
        )
        style.map(
            "FormAction.TButton",
            background=[("active", _CONTROL_HOVER)],
        )
        style.configure(
            "DiscoveryAction.TButton",
            background=_GREEN_SOFT, foreground=_GREEN_DEEP,
            bordercolor=_GREEN, lightcolor=_GREEN, darkcolor=_GREEN,
            padding=_STANDARD_BUTTON_PADDING,
            font=("Microsoft YaHei UI", 10),
        )
        style.map(
            "DiscoveryAction.TButton",
            background=[("active", _GREEN)],
            foreground=[("disabled", _MUTED)],
        )
        style.configure(
            "FilePicker.TButton", background=_CONTROL, foreground=_TEXT,
            bordercolor=_BORDER, lightcolor=_BORDER, darkcolor=_BORDER,
            padding=_FILE_PICKER_BUTTON_PADDING,
            font=("Microsoft YaHei UI", 9),
        )
        style.map(
            "FilePicker.TButton",
            background=[("active", _CONTROL_HOVER)],
        )
        style.configure(
            "Remove.TButton", background=_CONTROL, foreground=_TEXT,
            bordercolor=_BORDER,
            padding=(6, _STANDARD_BUTTON_PADDING[1]),
            font=("Microsoft YaHei UI", _UI_BODY_FONT_SIZE),
        )
        style.map(
            "Remove.TButton",
            background=[("active", _CONTROL_HOVER)],
        )
        style.configure(
            "Primary.TButton", background=_ACCENT, foreground="white",
            padding=_STANDARD_BUTTON_PADDING,
            font=("Microsoft YaHei UI", _UI_BODY_FONT_SIZE),
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
            padding=_STANDARD_BUTTON_PADDING,
            font=("Microsoft YaHei UI", _UI_BODY_FONT_SIZE),
            borderwidth=0, bordercolor=_AMBER_SOFT,
            lightcolor=_AMBER_SOFT, darkcolor=_AMBER_SOFT,
            relief="flat",
        )
        style.map(
            "Stop.TButton", background=[("active", _AMBER)])
        style.configure(
            "Secondary.TButton", background=_CONTROL, foreground=_TEXT,
            padding=_STANDARD_BUTTON_PADDING,
            font=("Microsoft YaHei UI", 10),
            borderwidth=1, bordercolor=_BORDER,
            lightcolor=_BORDER, darkcolor=_BORDER,
        )
        style.map(
            "Secondary.TButton", background=[("active", _CONTROL_HOVER)])
        style.configure(
            "Mini.TButton", background=_CONTROL, foreground=_TEXT,
            padding=_STANDARD_BUTTON_PADDING,
            font=("Microsoft YaHei UI", 10),
            borderwidth=1, bordercolor=_BORDER,
            lightcolor=_BORDER, darkcolor=_BORDER,
        )
        style.map(
            "Mini.TButton", background=[("active", _CONTROL_HOVER)])
        style.configure(
            "PanelHeader.TButton", background=_CONTROL, foreground=_TEXT,
            padding=_STANDARD_BUTTON_PADDING,
            font=("Microsoft YaHei UI", 10),
            borderwidth=1, bordercolor=_BORDER,
            lightcolor=_BORDER, darkcolor=_BORDER,
        )
        style.map(
            "PanelHeader.TButton",
            background=[("active", _CONTROL_HOVER)],
        )
        style.configure(
            "MiniStop.TButton", background=_AMBER_SOFT,
            foreground=_AMBER_DEEP,
            padding=_STANDARD_BUTTON_PADDING,
            font=("Microsoft YaHei UI", _UI_BODY_FONT_SIZE),
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
            "font": self._font_tuple(_UI_BODY_FONT_SIZE),
            "activebackground": _UNIFIED_ACTION_BACKGROUND,
            "activeforeground": _UNIFIED_ACTION_FOREGROUND,
            "disabledforeground": _MUTED,
            "activeborderwidth": 0,
            "borderwidth": 1,
            "relief": "flat",
        }
        menu = tk.Menu(
            self.root,
            **{**base_menu_options, "background": _MENU_BACKGROUND},
        )

        file_menu = tk.Menu(menu, **base_menu_options)
        file_menu.add_command(
            label="项目目录", command=self._open_project_directory)
        file_menu.add_command(
            label="结果目录", command=self._open_output)
        file_menu.add_separator()
        file_menu.add_command(label="退出", command=self._on_close)
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
        menu.add_cascade(label="功能", menu=panel_menu)

        settings_menu = tk.Menu(menu, **base_menu_options)
        self.settings_menu = settings_menu
        self.settings_locked_menu_entries: list[int] = []

        advanced_menu = tk.Menu(menu, **base_menu_options)
        self.advanced_menu = advanced_menu
        self.advanced_locked_menu_entries: list[int] = []
        self.command_preview_visible_var = tk.BooleanVar(value=False)
        tool_path_menu = tk.Menu(settings_menu, **base_menu_options)
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
            label="清除手动路径",
            command=self._clear_manual_tool_paths,
        )

        scan_behavior_menu = tk.Menu(
            advanced_menu, **base_menu_options)
        self.scan_behavior_menu = scan_behavior_menu
        timeout_menu = tk.Menu(
            scan_behavior_menu, **base_menu_options)
        self.scan_timeout_action_var = tk.StringVar(
            value="continue_waiting")
        for label, value in (
                ("继续等待", "continue_waiting"),
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
            label="超时处置", menu=timeout_menu)
        self.scan_show_current_file_var = tk.BooleanVar(value=False)
        scan_behavior_menu.add_checkbutton(
            label="显示当前文件",
            variable=self.scan_show_current_file_var,
            command=lambda: self._set_scan_advanced_value(
                "show_current_file",
                bool(self.scan_show_current_file_var.get()),
            ),
            selectcolor=_UNIFIED_ACTION_BACKGROUND,
        )
        advanced_menu.add_cascade(
            label="扫描选项", menu=scan_behavior_menu)
        scan_behavior_index = advanced_menu.index("end")
        if scan_behavior_index is not None:
            self.advanced_locked_menu_entries.append(
                int(scan_behavior_index))

        verify_behavior_menu = tk.Menu(
            advanced_menu, **base_menu_options)
        self.verify_behavior_menu = verify_behavior_menu
        verify_timeout_menu = tk.Menu(
            verify_behavior_menu, **base_menu_options)
        self.verify_timeout_action_var = tk.StringVar(
            value="continue_waiting")
        for label, value in (
                ("继续等待", "continue_waiting"),
                ("跳过并记录", "skip_and_record"),
                ("停止并保留结果", "stop_and_resume")):
            verify_timeout_menu.add_radiobutton(
                label=label,
                variable=self.verify_timeout_action_var,
                value=value,
                command=lambda selected=value:
                self._set_verify_advanced_value(
                    "timeout_action", selected),
                selectcolor=_UNIFIED_ACTION_BACKGROUND,
            )
        verify_behavior_menu.add_cascade(
            label="超时处置", menu=verify_timeout_menu)
        self.verify_show_current_file_var = tk.BooleanVar(value=False)
        verify_behavior_menu.add_checkbutton(
            label="显示当前文件",
            variable=self.verify_show_current_file_var,
            command=lambda: self._set_verify_advanced_value(
                "show_current_file",
                bool(self.verify_show_current_file_var.get()),
            ),
            selectcolor=_UNIFIED_ACTION_BACKGROUND,
        )
        self.verify_force_var = tk.BooleanVar(value=False)
        verify_behavior_menu.add_checkbutton(
            label="允许缺少指纹",
            variable=self.verify_force_var,
            command=lambda: self._set_verify_advanced_value(
                "force", bool(self.verify_force_var.get())),
            selectcolor=_UNIFIED_ACTION_BACKGROUND,
        )
        advanced_menu.add_cascade(
            label="核验选项", menu=verify_behavior_menu)
        verify_behavior_index = advanced_menu.index("end")
        if verify_behavior_index is not None:
            self.advanced_locked_menu_entries.append(
                int(verify_behavior_index))
        diff_behavior_menu = tk.Menu(
            advanced_menu, **base_menu_options)
        self.diff_behavior_menu = diff_behavior_menu
        self.diff_force_var = tk.BooleanVar(value=False)
        diff_behavior_menu.add_checkbutton(
            label="允许缺少指纹",
            variable=self.diff_force_var,
            command=lambda: self._set_diff_advanced_value(
                "force", bool(self.diff_force_var.get())),
            selectcolor=_UNIFIED_ACTION_BACKGROUND,
        )
        advanced_menu.add_cascade(
            label="对比选项", menu=diff_behavior_menu)
        diff_behavior_index = advanced_menu.index("end")
        if diff_behavior_index is not None:
            self.advanced_locked_menu_entries.append(
                int(diff_behavior_index))
        advanced_menu.add_separator()
        advanced_menu.add_command(
            label="命令预览",
            command=lambda: self._set_command_preview_expanded(
                not self.command_preview_expanded),
        )
        self.command_preview_menu_index = int(
            advanced_menu.index("end"))
        advanced_menu.add_separator()
        advanced_menu.add_command(
            label="功能自检",
            command=lambda: self._select_task(_PROJECT_SELF_TEST_KEY),
        )
        self.database_self_test_menu_index = int(
            advanced_menu.index("end"))
        self.advanced_locked_menu_entries.append(
            self.database_self_test_menu_index)
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
            label="窗口大小", menu=window_size_menu)

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
                label=label,
                variable=self.ui_font_size_var,
                value=size_delta,
                command=lambda selected=size_delta:
                self._set_ui_font(size_delta=selected),
            )
        font_menu.add_cascade(label="字号", menu=font_size_menu)
        settings_menu.add_cascade(label="界面字体", menu=font_menu)

        settings_menu.add_cascade(
            label="工具路径", menu=tool_path_menu)
        tool_path_index = settings_menu.index("end")
        if tool_path_index is not None:
            self.settings_locked_menu_entries.append(int(tool_path_index))

        settings_menu.add_separator()
        self.completion_sound_enabled_var = tk.BooleanVar(
            value=self.completion_sound_enabled)
        settings_menu.add_checkbutton(
            label="完成提示音",
            variable=self.completion_sound_enabled_var,
            command=lambda: self._set_completion_sound(
                self.completion_sound_enabled_var.get()),
        )
        self.result_directory_prompt_enabled_var = tk.BooleanVar(
            value=getattr(
                self, "result_directory_prompt_enabled", False))
        settings_menu.add_checkbutton(
            label="结果目录提示",
            variable=self.result_directory_prompt_enabled_var,
            command=lambda: self._set_result_directory_prompt(
                self.result_directory_prompt_enabled_var.get()),
        )
        settings_menu.add_separator()
        settings_menu.add_command(
            label="恢复设置…",
            command=self._reset_software_settings,
        )

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
            ("task_toolbar", "功能栏", self._toggle_task_toolbar),
            ("settings", "设置区", self._toggle_settings_panel),
            ("progress", "进度区", self._toggle_progress_panel),
            ("log", "日志区", self._toggle_log_panel),
        ):
            view_menu.add_command(
                label=(
                    ("隐藏" if panel_states[panel_key] else "显示")
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
            label="小窗模式", command=self._toggle_mini_mode)
        self.view_mini_mode_menu_index = int(view_menu.index("end"))
        menu.add_cascade(label="视图", menu=view_menu)
        menu.add_cascade(label="设置", menu=settings_menu)
        menu.add_cascade(label="高级", menu=advanced_menu)

        help_menu = tk.Menu(menu, **base_menu_options)
        help_menu.add_command(label="关于", command=self._show_about)
        help_menu.add_command(label="联系作者", command=self._show_author_contact)
        help_menu.add_separator()
        help_menu.add_command(
            label="GitHub 主页", command=self._open_github)
        menu.add_cascade(label="帮助", menu=help_menu)

        self.app_menu = menu
        self.root.configure(menu=menu)

    def _apply_menu_fonts(self) -> None:
        """让菜单栏和全部子菜单跟随正文大小与用户字体设置。"""
        root_menu = getattr(self, "app_menu", None)
        if root_menu is None:
            return
        pending = [root_menu]
        visited: set[str] = set()
        while pending:
            menu = pending.pop()
            menu_name = str(menu)
            if menu_name in visited:
                continue
            visited.add(menu_name)
            try:
                menu.configure(font=self._font_tuple(_UI_BODY_FONT_SIZE))
                end = menu.index("end")
            except (AttributeError, tk.TclError):
                continue
            if end is None:
                continue
            for index in range(int(end) + 1):
                try:
                    if menu.type(index) != "cascade":
                        continue
                    child_name = str(menu.entrycget(index, "menu"))
                    if not child_name:
                        continue
                    pending.append(self.root.nametowidget(child_name))
                except (AttributeError, KeyError, tk.TclError):
                    continue

    def _build_task_toolbar(self) -> None:
        """按工作流建立固定单排、等宽的六个简短功能入口。"""
        panel = tk.Frame(
            self.root, bg=_SURFACE,
            highlightbackground=_BORDER, highlightthickness=1,
        )
        self.task_toolbar_panel = panel
        panel.pack(fill="x", side="top")

        header = tk.Frame(panel, bg=_SURFACE)
        form_pad = (
            _SPACING_SECTION if self.compact_layout else _SPACING_OUTER)
        self.task_toolbar_horizontal_pad = (
            self.content_pad + form_pad + _SPACING_STANDARD + 1)
        header.pack(
            fill="x", padx=self.task_toolbar_horizontal_pad,
            pady=(_SPACING_COMPACT, _SPACING_COMPACT),
        )
        tk.Label(
            header, text="功能模块", bg=_SURFACE,
            fg=_TASK_TOOLBAR_LABEL_COLOUR,
            font=("Microsoft YaHei UI", 9, "bold"),
        ).pack(side="left")
        self.task_toolbar_toggle_button = ttk.Button(
            header, text="收起模块", style="Mini.TButton",
            width=_PANEL_ACTION_BUTTON_WIDTH,
            command=self._toggle_task_toolbar,
        )
        self.task_toolbar_toggle_button.pack(side="right")
        attach_tooltip(
            self.task_toolbar_toggle_button,
            "展开或收起顶部功能模块。",
        )
        self.clear_cache_button = ttk.Button(
            header, text="重置会话", style="Mini.TButton",
            width=_PANEL_ACTION_BUTTON_WIDTH,
            command=self._clear_tool_cache,
        )
        self.clear_cache_button.pack(
            side="right", padx=(0, _STANDARD_BUTTON_GAP))
        attach_tooltip(
            self.clear_cache_button,
            "清空当前会话的表单、工具路径、硬盘清单、日志、进度和可重建缓存。",
        )

        body = tk.Frame(panel, bg=_SURFACE)
        self.task_toolbar_body = body
        body.pack(
            fill="x", padx=self.task_toolbar_horizontal_pad,
            pady=(0, _SPACING_INLINE),
        )
        self.task_toolbar_section_labels: dict[str, tk.Label] = {}
        for column in range(len(_TASK_TOOLBAR_KEYS)):
            body.grid_columnconfigure(column, weight=0)
        body.grid_anchor("w")
        for task_key in _TASK_TOOLBAR_KEYS:
            task = TASK_BY_KEY[task_key]
            button = tk.Button(
                body, text=_TASK_TOOLBAR_LABELS[task_key],
                width=_TASK_TOOLBAR_BUTTON_WIDTH,
                relief="flat", bd=0, highlightthickness=1,
                highlightbackground=_BORDER, highlightcolor=_BORDER,
                bg=_TASK_TOOLBAR_BACKGROUND,
                fg=_TASK_TOOLBAR_FOREGROUND,
                activebackground=_TASK_TOOLBAR_HOVER,
                activeforeground=_TASK_TOOLBAR_FOREGROUND,
                disabledforeground=_MUTED,
                font=("Microsoft YaHei UI", _UI_BODY_FONT_SIZE),
                anchor="center", justify="center",
                padx=_TASK_TOOLBAR_BUTTON_PADDING[0],
                pady=_TASK_TOOLBAR_BUTTON_PADDING[1],
                takefocus=False,
                command=lambda key=task_key:
                self._select_task_from_toolbar(key),
            )
            self.task_toolbar_buttons[task_key] = button
            tooltip = task.description
            if task_key in _STG_ADMIN_TASKS:
                tooltip += " 建议使用管理员模式，以获取更完整的硬盘信息。"
            attach_tooltip(
                button, tooltip)
        body.bind("<Configure>", self._fit_task_toolbar_buttons)
        self.root.after_idle(self._layout_task_toolbar)

    @staticmethod
    def _create_task_action_button(
        master: tk.Misc,
        *,
        text: str,
        tone: str,
        command,
        state: str = "normal",
    ) -> tk.Button:
        """创建与表单模式按钮几何完全一致的任务操作按钮。"""
        palettes = {
            "primary": (
                _GREEN_DARK, "white", _GREEN_DEEP, "white", _GREEN_DARK),
            "control": (
                _ACTION_GREEN, _GREEN_DEEP, _GREEN, _GREEN_DEEP, _GREEN),
            "stop": (
                _AMBER, _AMBER_DEEP, _AMBER_DARK, "white", _AMBER_DARK),
            "result": (
                _TASK_TOOLBAR_BACKGROUND, _AMBER_DEEP,
                _TASK_TOOLBAR_HOVER, _AMBER_DEEP, _TASK_TOOLBAR_HOVER),
            "secondary": (
                _CONTROL, _TEXT, _CONTROL_HOVER, _TEXT, _BORDER),
        }
        background, foreground, active_background, active_foreground, border = (
            palettes[tone])
        return tk.Button(
            master, text=text, command=command, state=state,
            width=_STANDARD_BUTTON_WIDTH,
            relief="flat", bd=0, highlightthickness=1,
            highlightbackground=border, highlightcolor=border,
            bg=background, fg=foreground,
            activebackground=active_background,
            activeforeground=active_foreground,
            disabledforeground=foreground,
            font=("Microsoft YaHei UI", _UI_BODY_FONT_SIZE),
            anchor="center", justify="center",
            padx=_STANDARD_BUTTON_PADDING[0],
            pady=_STANDARD_BUTTON_PADDING[1],
            takefocus=True,
        )

    def _build_shell(self) -> None:
        content_pad = (
            _SPACING_STANDARD if self.compact_layout else _SPACING_SECTION)
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
        title_row.pack(
            fill="x", padx=_SPACING_OUTER,
            pady=(_SPACING_INLINE, _SPACING_COMPACT),
        )
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
        self.settings_actions = tk.Frame(title_row, bg=_SURFACE)
        self.settings_actions.pack(side="right")
        self.settings_actions.grid_columnconfigure(
            0, weight=1, uniform="settings_header_action")
        self.settings_actions.grid_columnconfigure(
            1, minsize=_PANEL_ACTION_BUTTON_GAP)
        self.settings_actions.grid_columnconfigure(
            2, weight=1, uniform="settings_header_action")
        self.reset_current_settings_button = ttk.Button(
            self.settings_actions, text="恢复默认",
            style="PanelHeader.TButton",
            width=_PANEL_ACTION_BUTTON_WIDTH,
            command=self._reset_current_task_settings,
        )
        self.reset_current_settings_button.grid(
            row=0, column=0, sticky="ew")
        attach_tooltip(
            self.reset_current_settings_button,
            "恢复当前功能页的默认值。",
        )
        self.settings_toggle_button = ttk.Button(
            self.settings_actions, text="收起设置",
            style="PanelHeader.TButton",
            width=_PANEL_ACTION_BUTTON_WIDTH,
            command=self._toggle_settings_panel,
        )
        self.settings_toggle_button.grid(row=0, column=2, sticky="ew")
        attach_tooltip(
            self.settings_toggle_button,
            "显示或隐藏当前功能的说明和设置。",
        )

        self.recovery_card = tk.Frame(
            title_row, bg=_AMBER_SOFT,
            highlightbackground=_AMBER, highlightthickness=1,
        )
        self.recovery_title_label = tk.Label(
            self.recovery_card, text="未完成扫描", bg=_AMBER_SOFT,
            fg=_AMBER_DEEP, font=("Microsoft YaHei UI", 8, "bold"),
            anchor="w",
        )
        self.recovery_title_label.pack(side="left", padx=(8, 4), pady=4)
        self.recovery_path_label = tk.Label(
            self.recovery_card, bg=_AMBER_SOFT, fg=_TEXT,
            font=("Microsoft YaHei UI", 8), anchor="w",
        )
        self.recovery_path_label.pack(
            side="left", padx=(0, _INLINE_CONTROL_GAP), pady=4)
        self.recovery_path_tooltip = attach_tooltip(
            self.recovery_path_label, "")
        self.recovery_ignore_button = ttk.Button(
            self.recovery_card, text="忽略", style="PanelHeader.TButton",
            width=_STANDARD_BUTTON_WIDTH,
            command=self._dismiss_latest_recovery,
        )
        self.recovery_ignore_button.pack(
            side="right", padx=(0, _SPACING_COMPACT), pady=3)
        attach_tooltip(
            self.recovery_ignore_button,
            "移除此续传提示；不会删除未完成快照。",
        )
        self.recovery_use_button = ttk.Button(
            self.recovery_card, text="准备续传",
            style="PanelHeader.TButton",
            width=_STANDARD_BUTTON_WIDTH,
            command=self._prepare_latest_recovery,
        )
        self.recovery_use_button.pack(
            side="right", padx=(0, _STANDARD_BUTTON_GAP), pady=3)
        attach_tooltip(
            self.recovery_use_button,
            "打开扫描页并填入未完成快照；不会立即开始任务或读取源档案。",
        )

        self.settings_body = tk.Frame(self.task_card, bg=_SURFACE)
        self.settings_body.pack(fill="both", expand=True)
        self.desc_label = tk.Label(
            self.settings_body, bg=_SURFACE, fg=_GREEN_DEEP,
            font=("Microsoft YaHei UI", 9), anchor="w", justify="left",
            wraplength=820,
        )
        self.desc_label.pack(
            fill="x", padx=_SPACING_OUTER,
            pady=(0, _SPACING_INLINE),
        )
        self.task_card.bind(
            "<Configure>",
            lambda e: self.desc_label.configure(
                wraplength=max(420, e.width - 44)),
        )

        separator = tk.Frame(self.settings_body, bg=_BORDER, height=1)
        separator.pack(fill="x")

        form_host = tk.Frame(self.settings_body, bg=_SURFACE)
        form_host.pack(fill="both", expand=True)
        self.form_host = form_host
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
        self.root.bind(
            "<MouseWheel>", self._route_form_scroll, add="+",
        )
        self.root.bind("<Button-4>", self._route_form_scroll, add="+")
        self.root.bind("<Button-5>", self._route_form_scroll, add="+")

        progress_panel = tk.Frame(
            content, bg=_LOG_BG, highlightbackground=_BORDER,
            highlightthickness=1,
        )
        self.progress_panel = progress_panel
        progress_panel.grid(
            row=1, column=0, sticky="ew", pady=(_PANEL_GAP, 0))
        progress_inner = tk.Frame(progress_panel, bg=_LOG_BG)
        self.progress_inner = progress_inner
        progress_inner.pack(
            fill="x", padx=_PANEL_HEADER_PADX,
            pady=(_SPACING_INLINE
                  if self.compact_layout else _SPACING_STANDARD),
        )

        progress_header = tk.Frame(progress_inner, bg=_LOG_HEADER)
        self.progress_header = progress_header
        progress_header.pack(fill="x")
        self.progress_title_label = tk.Label(
            progress_header, text="运行进度", bg=_LOG_HEADER, fg=_TEXT,
            font=("Microsoft YaHei UI", 9, "bold"), anchor="w",
        )
        self.progress_title_label.pack(side="left")
        progress_actions = tk.Frame(progress_header, bg=_LOG_HEADER)
        progress_actions.pack(side="right")
        progress_actions.grid_columnconfigure(
            0, weight=1, uniform="panel_header_action")
        progress_actions.grid_columnconfigure(
            1, minsize=_PANEL_ACTION_BUTTON_GAP)
        progress_actions.grid_columnconfigure(
            2, weight=1, uniform="panel_header_action")
        self.mini_mode_button = ttk.Button(
            progress_actions, text="小窗模式", style="PanelHeader.TButton",
            width=_PANEL_ACTION_BUTTON_WIDTH,
            command=self._toggle_mini_mode, state="normal",
        )
        self.mini_mode_button.grid(row=0, column=0, sticky="ew")
        attach_tooltip(
            self.mini_mode_button,
            "进入只显示进度和运行控制的小窗。",
        )
        self.progress_toggle_button = ttk.Button(
            progress_actions, text="收起进度", style="PanelHeader.TButton",
            width=_PANEL_ACTION_BUTTON_WIDTH,
            command=self._toggle_progress_panel,
        )
        self.progress_toggle_button.grid(row=0, column=2, sticky="ew")
        attach_tooltip(
            self.progress_toggle_button,
            "显示或隐藏任务队列和进度。",
        )
        self.mini_stop_button = ttk.Button(
            progress_header, text="停止", style="MiniStop.TButton",
            width=_STANDARD_BUTTON_WIDTH,
            command=self._stop, state="disabled",
        )
        attach_tooltip(
            self.mini_stop_button,
            "停止当前任务，并取消队列中尚未开始的任务项。",
        )
        self.mini_save_button = ttk.Button(
            progress_header, text="保存并退出", style="Mini.TButton",
            width=_STANDARD_BUTTON_WIDTH,
            command=self._save_scan_progress, state="disabled",
        )
        attach_tooltip(
            self.mini_save_button,
            "安全保存已完成进度并结束当前扫描；下次启动时显示续传提示。",
        )
        self.mini_pause_button = ttk.Button(
            progress_header, text="暂停", style="Mini.TButton",
            width=_STANDARD_BUTTON_WIDTH,
            command=self._pause_or_continue_scan, state="disabled",
        )
        attach_tooltip(
            self.mini_pause_button,
            "暂停当前任务；再次点击可继续。当前文件可能从起点重试。",
        )

        progress_body = tk.Frame(progress_inner, bg=_LOG_BG)
        self.progress_body = progress_body
        progress_body.pack(fill="x", pady=(_SPACING_INLINE, 0))
        progress_body.grid_columnconfigure(1, weight=1)

        tk.Label(
            progress_body, text="当前目标", bg=_LOG_BG, fg=_GREEN_DARK,
            font=("Microsoft YaHei UI", 8, "bold"), anchor="nw",
        ).grid(
            row=0, column=0, sticky="nw",
            padx=(0, _STANDARD_BUTTON_GAP),
            pady=(0, _SPACING_INLINE),
        )
        self.progress_target_label = tk.Label(
            progress_body, text="尚未选择", bg=_LOG_BG, fg=_MUTED,
            font=("Microsoft YaHei UI", 8), anchor="w", justify="left",
            wraplength=760,
        )
        self.progress_target_label.grid(
            row=0, column=1, columnspan=2, sticky="ew",
            pady=(0, _SPACING_INLINE))
        progress_body.bind(
            "<Configure>",
            lambda event: self.progress_target_label.configure(
                wraplength=max(260, event.width - 90)),
        )

        self.current_file_title_label = tk.Label(
            progress_body, text="当前文件", bg=_LOG_BG, fg=_GREEN_DARK,
            font=("Microsoft YaHei UI", 8, "bold"), anchor="w",
        )
        self.current_file_label = tk.Label(
            progress_body, text="", bg=_LOG_BG, fg=_TEXT,
            font=("Microsoft YaHei UI", 8), anchor="w",
        )
        self.current_file_tooltip = attach_tooltip(
            self.current_file_label, "")

        self.queue_title_label = tk.Label(
            progress_body, text="任务队列", bg=_LOG_BG, fg=_GREEN_DEEP,
            font=("Microsoft YaHei UI", 8, "bold"), anchor="w",
        )
        self.queue_title_label.grid(
            row=2, column=0, sticky="w",
            padx=(0, _STANDARD_BUTTON_GAP))
        self.queue_detail_label = tk.Label(
            progress_body, text="等待队列", bg=_LOG_BG, fg=_MUTED,
            font=("Microsoft YaHei UI", 8), anchor="w",
        )
        self.queue_detail_label.grid(row=2, column=1, sticky="ew")
        self.queue_percent_label = tk.Label(
            progress_body, text="0%", bg=_LOG_BG, fg=_GREEN_DEEP,
            font=("Microsoft YaHei UI", 8, "bold"), anchor="e",
        )
        self.queue_percent_label.grid(row=2, column=2, sticky="e")
        self.queue_progress_bar = ttk.Progressbar(
            progress_body, mode="determinate", maximum=100, value=0,
            style="Queue.Horizontal.TProgressbar",
        )
        self.queue_progress_bar.grid(
            row=3, column=0, columnspan=3, sticky="ew",
            pady=(_SPACING_COMPACT, _SPACING_INLINE))

        tk.Label(
            progress_body, text="任务阶段", bg=_LOG_BG, fg=_GREEN_DARK,
            font=("Microsoft YaHei UI", 8, "bold"), anchor="w",
        ).grid(
            row=4, column=0, sticky="w",
            padx=(0, _STANDARD_BUTTON_GAP))
        self.progress_stage_label = tk.Label(
            progress_body, text="等待开始", bg=_LOG_BG, fg=_MUTED,
            font=("Microsoft YaHei UI", 8), anchor="w",
        )
        self.progress_stage_label.grid(
            row=4, column=1, columnspan=2, sticky="ew")
        self.progress_stage_bar = ttk.Progressbar(
            progress_body, mode="determinate", maximum=100, value=0,
            style="Stage.Horizontal.TProgressbar",
        )
        self.progress_stage_bar.grid(
            row=5, column=0, columnspan=3, sticky="ew",
            pady=(_SPACING_COMPACT, _SPACING_INLINE))

        tk.Label(
            progress_body, text="本阶段", bg=_LOG_BG, fg=_GREEN_DARK,
            font=("Microsoft YaHei UI", 8, "bold"), anchor="w",
        ).grid(
            row=6, column=0, sticky="w",
            padx=(0, _STANDARD_BUTTON_GAP))
        self.progress_detail_label = tk.Label(
            progress_body, text="尚未运行", bg=_LOG_BG, fg=_MUTED,
            font=("Microsoft YaHei UI", 8), anchor="w",
        )
        self.progress_detail_label.grid(row=6, column=1, sticky="ew")
        self.progress_percent_label = tk.Label(
            progress_body, text="0%", bg=_LOG_BG, fg=_GREEN_DARK,
            font=("Microsoft YaHei UI", 8, "bold"), anchor="e",
        )
        self.progress_percent_label.grid(row=6, column=2, sticky="e")
        self.progress_work_bar = ttk.Progressbar(
            progress_body, mode="determinate", maximum=100, value=0,
            style="Work.Horizontal.TProgressbar",
        )
        self.progress_work_bar.grid(
            row=7, column=0, columnspan=3, sticky="ew",
            pady=(_SPACING_COMPACT, _SPACING_INLINE))

        log_panel = tk.Frame(
            content, bg=_LOG_BG, highlightbackground=_BORDER,
            highlightthickness=1,
        )
        self.log_panel = log_panel
        log_panel.grid(
            row=2, column=0, sticky="ew", pady=(_PANEL_GAP, 0))
        log_header = tk.Frame(log_panel, bg=_LOG_HEADER)
        log_header.pack(fill="x")
        tk.Label(
            log_header, text="运行日志", bg=_LOG_HEADER, fg=_TEXT,
            font=("Microsoft YaHei UI", 9, "bold"),
        ).pack(
            side="left", padx=_PANEL_HEADER_PADX,
            pady=_SPACING_INLINE)
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
            "清空主界面与独立窗口中的运行日志。",
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
            "展开或收起运行日志。",
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
        command_panel.grid(
            row=3, column=0, sticky="ew", pady=(_PANEL_GAP, 0))
        command_preview_body = tk.Frame(command_panel, bg=_BG)
        self.command_preview_body = command_preview_body
        tk.Label(
            command_preview_body, text="命令预览", bg=_BG, fg=_MUTED,
            font=("Microsoft YaHei UI", 9),
        ).pack(anchor="w")
        preview_row = tk.Frame(command_preview_body, bg=_BG)
        preview_row.pack(
            fill="x", pady=(_SPACING_COMPACT, _SPACING_INLINE))
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
        self.copy_button.pack(
            side="left", padx=(_INLINE_CONTROL_GAP, 0))
        attach_tooltip(
            self.copy_button,
            "复制当前页面生成的命令预览。",
        )

        actions = tk.Frame(command_panel, bg=_BG)
        self.command_actions = actions
        actions.pack(fill="x")
        status_area = tk.Frame(actions, bg=_BG)
        status_area.pack(fill="x")
        self.status_label = tk.Label(
            status_area, text="就绪", bg=_GREEN_DARK, fg="white",
            font=("Microsoft YaHei UI", 9, "bold"), anchor="w",
            padx=_SPACING_STANDARD, pady=5,
        )
        self.status_label.pack(side="left", anchor="center")

        action_button_area = tk.Frame(actions, bg=_BG)
        self.action_button_area = action_button_area
        action_button_area.pack(
            fill="x", pady=(_SPACING_INLINE, 0))
        execution_action_area = tk.Frame(action_button_area, bg=_BG)
        self.execution_action_area = execution_action_area
        execution_action_area.pack(fill="x")
        execution_action_area.grid_columnconfigure(0, weight=1)
        self.stop_button = self._create_task_action_button(
            execution_action_area, text="停止", tone="control",
            command=self._stop, state="disabled")
        self.save_scan_button = self._create_task_action_button(
            execution_action_area, text="保存并退出", tone="control",
            command=self._save_scan_progress, state="disabled")
        self.pause_scan_button = self._create_task_action_button(
            execution_action_area, text="暂停", tone="control",
            command=self._pause_or_continue_scan, state="disabled")
        self.run_button = self._create_task_action_button(
            execution_action_area, text=_RUN_BUTTON_TEXT, tone="primary",
            command=self._run)
        self.open_output_button = self._create_task_action_button(
            execution_action_area, text="打开结果目录", tone="result",
            command=self._open_output)
        self.execution_buttons = (
            self.pause_scan_button, self.save_scan_button,
            self.run_button, self.stop_button, self.open_output_button,
        )
        for button, tooltip in (
            (self.run_button,
             "检查当前设置并开始任务。"),
            (self.stop_button,
             "停止当前任务，并取消队列中尚未开始的任务项。"),
            (self.pause_scan_button,
             "暂停当前任务；再次点击可继续。当前文件可能从起点重试。"),
            (self.save_scan_button,
             "安全保存已完成进度并结束扫描；下次启动时显示续传提示。"),
            (self.open_output_button,
             "在资源管理器中打开当前任务对应的结果目录。"),
        ):
            attach_tooltip(button, tooltip)
        action_button_area.bind("<Configure>", self._layout_action_buttons)
        self.root.after_idle(self._layout_action_buttons)

    def _layout_task_toolbar(
        self, _event: tk.Event | None = None,
    ) -> None:
        """六个功能入口始终保持同一行、同一宽度和同一高度。"""
        if getattr(self, "_task_toolbar_layout_ready", False):
            return
        for button in self.task_toolbar_buttons.values():
            button.grid_forget()
        last_column = len(_TASK_TOOLBAR_KEYS) - 1
        for column, task_key in enumerate(_TASK_TOOLBAR_KEYS):
            self.task_toolbar_buttons[task_key].grid(
                row=0, column=column,
                padx=(0, _STANDARD_BUTTON_GAP
                      if column < last_column else 0),
                pady=0,
            )
        self._task_toolbar_layout_ready = True
        self._fit_task_toolbar_buttons()
        self._sync_task_toolbar_minimum_width()

    def _fit_task_toolbar_buttons(
        self, _event: tk.Event | None = None,
    ) -> None:
        """在保持六按钮单排和短标题的前提下压缩横向内部留白。"""
        buttons = list(self.task_toolbar_buttons.values())
        if not buttons:
            return
        try:
            available = self.task_toolbar_body.winfo_width()
        except tk.TclError:
            return
        if available <= 1:
            available = max(
                1,
                self.root.winfo_width()
                - self.task_toolbar_horizontal_pad * 2,
            )
        per_button = max(
            1,
            (available - _STANDARD_BUTTON_GAP * (len(buttons) - 1))
            // len(buttons),
        )
        base_width = 1
        for button in buttons:
            try:
                current_padding = int(button.cget("padx"))
            except (tk.TclError, TypeError, ValueError):
                current_padding = _TASK_TOOLBAR_BUTTON_PADDING[0]
            base_width = max(
                base_width,
                button.winfo_reqwidth() - current_padding * 2,
            )
        horizontal_padding = min(
            _TASK_TOOLBAR_BUTTON_PADDING[0],
            max(0, (per_button - base_width) // 2),
        )
        for button in buttons:
            try:
                if int(button.cget("padx")) != horizontal_padding:
                    button.configure(padx=horizontal_padding)
            except (tk.TclError, TypeError, ValueError):
                continue

    def _sync_task_toolbar_minimum_width(self) -> None:
        """普通界面至少保留 1100 px；模块按钮在该宽度内自适应留白。"""
        self.root.update_idletasks()
        base_width, base_height = self.normal_min_size
        width_cap = int(getattr(self, "normal_width_cap", 1200))
        self.normal_min_size = (
            min(max(base_width, _TASK_TOOLBAR_MINIMUM_WIDTH), width_cap),
            base_height,
        )
        if not self.mini_mode:
            self.root.minsize(*self._normal_minimum_size())

    def _refresh_view_menu_labels(self) -> None:
        """让可折叠面板菜单显示下一步会执行的动作。"""
        if not hasattr(self, "view_panel_menu_entries"):
            return
        states = {
            "task_toolbar": (self.task_toolbar_expanded, "功能栏"),
            "settings": (self.settings_expanded, "设置区"),
            "progress": (self.progress_expanded, "进度区"),
            "log": (self.log_expanded, "日志区"),
        }
        for panel_key, entry_index in self.view_panel_menu_entries.items():
            expanded, label = states[panel_key]
            self.view_menu.entryconfigure(
                entry_index,
                label=("隐藏" if expanded else "显示") + label,
            )
        if hasattr(self, "view_mini_mode_menu_index"):
            self.view_menu.entryconfigure(
                self.view_mini_mode_menu_index,
                label=(
                    "完整界面" if self.mini_mode else "小窗模式"
                ),
            )

    def _set_task_toolbar_expanded(self, expanded: bool) -> None:
        self.task_toolbar_expanded = expanded
        if expanded:
            if not self.task_toolbar_body.winfo_manager():
                self.task_toolbar_body.pack(
                    fill="x", padx=self.task_toolbar_horizontal_pad,
                    pady=(0, _SPACING_INLINE))
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
        """右侧固定为开始／停止、暂停、结果目录；暂停后再显示保存。"""
        for button in self.execution_buttons:
            button.grid_forget()
        controls = [
            self.open_output_button, self.pause_scan_button, self.run_button,
        ]
        task_key = getattr(self, "process_task_key", None)
        state = getattr(self, "scan_control_state", "idle")
        if (task_key in _SCAN_TASK_KEYS
                and state in (
                    "pause_requested", "paused", "resume_requested")):
            controls.insert(0, self.save_scan_button)
        for column, button in enumerate(controls, start=1):
            button.grid(
                row=0, column=column, sticky="e",
                padx=(
                    0,
                    _STANDARD_BUTTON_GAP
                    if column < len(controls) else 0,
                ),
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
            padx=(_SPACING_OUTER if expanded else _PANEL_HEADER_PADX),
            pady=((_SPACING_INLINE, _SPACING_COMPACT) if expanded
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
                self.progress_body.pack(
                    fill="x", pady=(_SPACING_INLINE, 0))
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
                label="命令预览",
            )
        if not self.mini_mode:
            self.root.minsize(*self._normal_minimum_size())

    def _task_is_active(self) -> bool:
        return bool(
            self.process is not None or self.worker_starting
            or self.run_jobs
            or bool(getattr(self, "parse_detection_active", False))
        )

    def _refresh_mini_action(self) -> None:
        self.mini_mode_button.configure(
            text="返回完整界面" if self.mini_mode else "小窗模式",
            state="normal",
        )
        tooltip = getattr(self.mini_mode_button, "_daisy_tooltip", None)
        if tooltip is not None:
            tooltip.text = (
                "返回包含全部设置、进度和日志的完整界面。"
                if self.mini_mode else
                "进入只显示进度和运行控制的小窗。"
            )
        self._refresh_view_menu_labels()

    def _set_stop_state(self, state: str) -> None:
        self.stop_button.configure(state=state)
        self.mini_stop_button.configure(state=state)
        if getattr(self, "process_task_key", None) is not None:
            self._set_run_action_mode(True, state=state)

    def _set_run_action_mode(
        self, running: bool, *, state: str = "normal",
    ) -> None:
        """让同一按钮在空闲和运行状态间切换，不改变右侧锚点。"""
        palettes = {
            "primary": (
                _GREEN_DARK, "white", _GREEN_DEEP, "white", _GREEN_DARK),
            "stop": (
                _AMBER, _AMBER_DEEP, _AMBER_DARK, "white", _AMBER_DARK),
        }
        tone = "stop" if running else "primary"
        background, foreground, active_background, active_foreground, border = (
            palettes[tone])
        self.run_button.configure(
            text="停止" if running else _RUN_BUTTON_TEXT,
            command=self._stop if running else self._run,
            state=state,
            bg=background, fg=foreground,
            activebackground=active_background,
            activeforeground=active_foreground,
            disabledforeground=foreground,
            highlightbackground=border,
            highlightcolor=border,
        )
        tooltip = getattr(self.run_button, "_daisy_tooltip", None)
        if tooltip is not None:
            tooltip.text = (
                "停止当前任务，并取消队列中尚未开始的任务项。"
                if running else "检查当前设置并开始任务。"
            )

    def _refresh_scan_controls(self) -> None:
        """按统一可控任务状态同步主窗口与小窗控制按钮。"""
        if not hasattr(self, "pause_scan_button"):
            return
        task_key = getattr(self, "process_task_key", None)
        control_active = (
            task_key in _CONTROL_TASK_KEYS
            and getattr(self, "process", None) is not None
        )
        scan_active = control_active and task_key in _SCAN_TASK_KEYS
        state = self.scan_control_state
        pause_text = (
            "继续"
            if state in ("pause_requested", "paused", "resume_requested")
            else "暂停"
        )
        pause_state = (
            "normal" if control_active and state in ("running", "paused")
            else "disabled"
        )
        save_state = (
            "normal"
            if scan_active and state in ("pause_requested", "paused")
            else "disabled"
        )
        stop_state = (
            "normal" if control_active and state in ("running", "paused")
            else "disabled"
        )
        for button in (self.pause_scan_button, self.mini_pause_button):
            button.configure(text=pause_text, state=pause_state)
        for button in (self.save_scan_button, self.mini_save_button):
            button.configure(state=save_state)
        if task_key in _CONTROL_TASK_KEYS:
            self._set_stop_state(stop_state)
        self._layout_action_buttons()
        if getattr(self, "mini_mode", False):
            if (task_key in _SCAN_TASK_KEYS
                    and state in (
                        "pause_requested", "paused", "resume_requested")):
                if not self.mini_save_button.winfo_manager():
                    self.mini_save_button.pack(
                        side="right", padx=(0, _STANDARD_BUTTON_GAP),
                        before=self.mini_stop_button,
                    )
            else:
                self.mini_save_button.pack_forget()

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
        self.content.pack_configure(
            padx=_SPACING_STANDARD, pady=_SPACING_STANDARD)
        self.mini_stop_button.pack(
            side="right", padx=(0, _STANDARD_BUTTON_GAP))
        self.mini_save_button.pack(
            side="right", padx=(0, _STANDARD_BUTTON_GAP))
        self.mini_pause_button.pack(
            side="right", padx=(0, _STANDARD_BUTTON_GAP))
        self._refresh_scan_controls()
        self._refresh_mini_action()

        self.root.update_idletasks()
        work_area = _monitor_work_area_for_window(self.root)
        width = max(420, min(680, work_area.width - 32))
        requested_height = (
            self.progress_panel.winfo_reqheight() + 2 * _SPACING_STANDARD)
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
        self.progress_panel.grid_configure(
            row=1, pady=(_PANEL_GAP, 0))
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

    @staticmethod
    def _widget_is_within(widget: tk.Misc, container: tk.Misc) -> bool:
        """判断事件控件是否位于指定容器内，不依赖瞬时 Enter/Leave。"""
        current: tk.Misc | None = widget
        while current is not None:
            if current is container:
                return True
            current = getattr(current, "master", None)
        return False

    def _route_form_scroll(self, event: tk.Event) -> str | None:
        """只把设置卡片内的滚轮事件转交给表单 Canvas。"""
        widget = getattr(event, "widget", None)
        card = getattr(self, "task_card", None)
        if widget is None or card is None \
                or not self._widget_is_within(widget, card):
            return None
        return self._scroll_form(event)

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
        heights: list[int] = []
        inner = getattr(self, "form_inner", None)
        if inner is not None:
            try:
                inner.update_idletasks()
                heights.extend((
                    int(inner.winfo_reqheight()),
                    int(inner.winfo_height()),
                ))
            except (AttributeError, tk.TclError, TypeError, ValueError):
                pass
        try:
            bounds = self.form_canvas.bbox("all")
            if bounds is not None:
                heights.append(int(bounds[3]) - int(bounds[1]))
        except (AttributeError, tk.TclError, TypeError, ValueError):
            pass
        return max((0, *heights)) if heights else -1

    def _sync_form_scroll_region(self) -> None:
        """仅在表单真实溢出时启用滚动，并把未溢出页面锁在顶部。"""
        self._form_scroll_sync_after_id = None
        try:
            viewport_width = max(1, int(self.form_canvas.winfo_width()))
            viewport_height = max(1, int(self.form_canvas.winfo_height()))
            content_height = max(0, self._form_content_height())
            overflow = (
                content_height
                > viewport_height + _FORM_SCROLL_OVERFLOW_TOLERANCE
            )
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
        return (
            content_height
            <= viewport_height + _FORM_SCROLL_OVERFLOW_TOLERANCE
        )

    def _position_form_scroll(self, fraction: float) -> None:
        self._sync_form_scroll_region()
        target = 0.0 if self._form_content_fits_viewport() else max(
            0.0, min(1.0, float(fraction)))
        self.form_canvas.yview_moveto(target)

    def _save_current_values(self) -> None:
        if self.values:
            self.saved_values[self.task.key] = self._collect_persistable_values()

    def _scan_settings_task_key(self) -> str:
        """旧完整扫描页保留原设置槽；新界面统一写入 scan。"""
        return "full_scan" if self.task.key == "full_scan" else "scan"

    def _set_scan_advanced_value(self, key: str, value: object) -> None:
        if self._task_is_active() or key not in (
                "timeout_action", "show_current_file"):
            self._refresh_scan_advanced_values()
            return
        settings_task_key = self._scan_settings_task_key()
        saved = self.saved_values.setdefault(settings_task_key, {})
        saved[key] = value
        self._refresh_scan_advanced_values()
        if self.task.key in ("scan", "full_scan"):
            self._update_preview()

    def _refresh_scan_advanced_values(self) -> None:
        if not hasattr(self, "scan_timeout_action_var"):
            return
        values = _task_values(
            TASK_BY_KEY[self._scan_settings_task_key()],
            self.saved_values.get(self._scan_settings_task_key(), {}),
        )
        self.scan_timeout_action_var.set(str(
            values.get("timeout_action") or "continue_waiting"))
        self.scan_show_current_file_var.set(bool(
            values.get("show_current_file", False)))

    def _set_verify_advanced_value(self, key: str, value: object) -> None:
        if self._task_is_active() or key not in (
                "timeout_action", "show_current_file", "force"):
            self._refresh_verify_advanced_values()
            return
        self.saved_values.setdefault("verify", {})[key] = value
        self._refresh_verify_advanced_values()
        if self.task.key == "verify":
            self._update_preview()

    def _refresh_verify_advanced_values(self) -> None:
        if not hasattr(self, "verify_timeout_action_var"):
            return
        values = _task_values(
            TASK_BY_KEY["verify"], self.saved_values.get("verify", {}))
        self.verify_timeout_action_var.set(str(
            values.get("timeout_action") or "continue_waiting"))
        self.verify_show_current_file_var.set(bool(
            values.get("show_current_file", False)))
        self.verify_force_var.set(bool(values.get("force", False)))

    def _set_diff_advanced_value(self, key: str, value: object) -> None:
        if self._task_is_active() or key != "force":
            self._refresh_diff_advanced_values()
            return
        self.saved_values.setdefault("diff", {})[key] = value
        self._refresh_diff_advanced_values()
        if self.task.key == "diff":
            self._update_preview()

    def _refresh_diff_advanced_values(self) -> None:
        if not hasattr(self, "diff_force_var"):
            return
        values = _task_values(
            TASK_BY_KEY["diff"], self.saved_values.get("diff", {}))
        self.diff_force_var.set(bool(values.get("force", False)))

    def _select_task_from_toolbar(self, task_key: str) -> None:
        """切换功能模块，并移除按钮焦点框。"""
        self._select_task(task_key)
        self.root.focus_set()

    def _refresh_task_navigation_selection(self) -> None:
        """同步功能菜单与顶部按钮的当前任务高亮。"""
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
                    _UI_BODY_FONT_SIZE,
                    "bold" if selected else "normal"),
            )
        for task_key, button in self.task_toolbar_buttons.items():
            selected = task_key == self.task.key
            background = (
                _TASK_TOOLBAR_SELECTED
                if selected else _TASK_TOOLBAR_BACKGROUND)
            hover = (
                _TASK_TOOLBAR_SELECTED_HOVER
                if selected else _TASK_TOOLBAR_HOVER)
            button.configure(
                bg=background,
                fg=_TASK_TOOLBAR_FOREGROUND,
                activebackground=hover,
                activeforeground=_TASK_TOOLBAR_FOREGROUND,
                highlightbackground=_BORDER,
                highlightcolor=_BORDER,
            )

    def _set_task_navigation_state(self, state: str) -> None:
        """运行期间锁定任务和参数入口，保留命令预览开关。"""
        for task_menu, entry_index in self.task_menu_entries.values():
            task_menu.entryconfigure(entry_index, state=state)
        for task_key, button in self.task_toolbar_buttons.items():
            button.configure(state=state)
        if hasattr(self, "clear_cache_button"):
            self.clear_cache_button.configure(state=state)
        if hasattr(self, "advanced_menu"):
            for entry_index in getattr(
                    self, "advanced_locked_menu_entries", ()):
                self.advanced_menu.entryconfigure(entry_index, state=state)
        if hasattr(self, "settings_menu"):
            for entry_index in getattr(
                    self, "settings_locked_menu_entries", ()):
                self.settings_menu.entryconfigure(entry_index, state=state)
        if hasattr(self, "reset_current_settings_button"):
            self.reset_current_settings_button.configure(
                state=(state if self.task.fields else "disabled"))

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
        self._refresh_scan_advanced_values()
        self._refresh_verify_advanced_values()
        self._refresh_diff_advanced_values()
        self._refresh_tool_cache_labels()
        active = self._task_is_active()
        self.reset_current_settings_button.configure(
            state=("normal" if self.task.fields and not active else "disabled"))
        missing_tests = (
            project_self_test_missing_files()
            if task_key == _PROJECT_SELF_TEST_KEY else ()
        )
        self._set_run_action_mode(
            False,
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
                        "建议使用管理员模式，以获取更完整的硬盘信息。",
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
            return (("请先检测硬盘", ""),)
        return (("请选择本次检测到的硬盘", ""), *discovered)

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

        button_grid = tk.Frame(panel, bg=_SURFACE)
        button_grid.grid(
            row=1, column=0, sticky="ew", padx=12, pady=(4, 8))
        for column in range(_ENVIRONMENT_COLUMN_COUNT):
            button_grid.grid_columnconfigure(
                column * 2, weight=0, uniform="environment_install")
            if column < _ENVIRONMENT_COLUMN_COUNT - 1:
                button_grid.grid_columnconfigure(
                    column * 2 + 1, minsize=_STANDARD_BUTTON_GAP)
        install_entries = {
            name: (display_name, "winget")
            for name, (display_name, _package_id)
            in _INSTALLABLE_TOOL_PACKAGES.items()
        }
        install_entries.update({
            name: (display_name, "pip")
            for name, (display_name, _package_name)
            in _INSTALLABLE_PYTHON_CAPABILITIES.items()
        })
        button_options = {
            "relief": "flat",
            "bd": 0,
            "highlightthickness": 1,
            "font": ("Microsoft YaHei UI", _UI_BODY_FONT_SIZE),
            "anchor": "center",
            "justify": "center",
            "width": _ENVIRONMENT_BUTTON_WIDTH,
            "height": 2,
            "padx": _ENVIRONMENT_BUTTON_PADDING[0],
            "pady": _ENVIRONMENT_BUTTON_PADDING[1],
            "bg": _CONTROL,
            "fg": _TEXT,
            "activebackground": _CONTROL_HOVER,
            "activeforeground": _TEXT,
            "disabledforeground": _MUTED,
            "highlightbackground": _BORDER,
            "highlightcolor": _BORDER,
            "takefocus": True,
        }
        for column, dependency_name in enumerate(_ENVIRONMENT_STATUS_ORDER):
            if dependency_name not in install_entries:
                button = tk.Button(
                    button_grid,
                    text=(
                        f"{_ENVIRONMENT_BUTTON_LABELS[dependency_name]}\n"
                        "系统提供"
                    ),
                    state="disabled",
                    **button_options,
                )
                button.grid(
                    row=0, column=column * 2, sticky="w",
                )
                self.environment_install_buttons[dependency_name] = button
                attach_tooltip(
                    button,
                    "PowerShell 由 Windows 系统提供。",
                )
                continue
            display_name, installer = install_entries[dependency_name]
            command = (
                (lambda name=dependency_name:
                 self._install_python_capability(name))
                if installer == "pip" else
                (lambda name=dependency_name: self._install_tool(name))
            )
            button = tk.Button(
                button_grid,
                text=(
                    f"{_INSTALL_BUTTON_LABELS[dependency_name]}\n"
                    "安装或更新"
                ),
                command=command,
                **button_options,
            )
            button.grid(
                row=0, column=column * 2, sticky="w",
            )
            self.install_tool_buttons[dependency_name] = button
            self.environment_install_buttons[dependency_name] = button
            attach_tooltip(
                button,
                (
                    f"先查询软件源最新版本；确认后使用当前 Python 的 pip 安装或更新"
                    f" {display_name}。"
                    if installer == "pip" else
                    f"先查询软件源最新版本；确认后使用 WinGet 安装或更新 {display_name}。"
                ),
            )
        return row + 1

    def _environment_status(
        self, dependency_name: str,
    ) -> tuple[str, str, str]:
        """返回环境状态按钮的状态、完整标签和详细说明。"""
        display_name = _ENVIRONMENT_BUTTON_LABELS[dependency_name]
        if dependency_name == "rawpy":
            raw = self.runtime_capabilities.get(envcap.RAW_CAPABILITY_ID)
            if not isinstance(raw, dict):
                return (
                    "pending", f"{display_name}\n等待检测",
                    "尚未检测 rawpy/LibRaw。点击后检测全部环境项目。",
                )
            available, reason = raw_runtime_capability_status(
                self.runtime_capabilities)
            reason = str(reason).rstrip("。；; ")
            return (
                "available" if available else "missing",
                f"{display_name}\n{'可用' if available else '不可用'}",
                f"rawpy/LibRaw：{reason}。点击后重新检测全部环境项目。",
            )

        info = self.detected_tools.get(dependency_name) or {}
        path = str(info.get("path") or "").strip()
        if path and info.get("verified") is True:
            version = str(info.get("version") or "版本未知")
            return (
                "available", f"{display_name}\n可用",
                f"{display_name} 已检测并验证可用。版本：{version}。路径：{path}。"
                "点击后重新检测全部环境项目。",
            )
        if dependency_name in self.environment_missing_names:
            reason = self.environment_missing_reasons.get(
                dependency_name, "环境检测未找到该工具。")
            reason = str(reason).rstrip("。；; ")
            return (
                "missing", f"{display_name}\n不可用",
                f"{display_name} 无法使用：{reason}。"
                "点击后重新检测全部环境项目。",
            )
        return (
            "pending", f"{display_name}\n等待检测",
            f"尚未检测 {display_name}。点击后检测全部环境项目。",
        )

    def _refresh_environment_status_buttons(self) -> None:
        """同步六个等尺寸状态按钮，不触发新的环境探测。"""
        for dependency_name, button in getattr(
                self, "environment_status_buttons", {}).items():
            state, label, detail = self._environment_status(dependency_name)
            if state == "available":
                background = _GREEN_DARK
                foreground = "white"
                active_background = _GREEN_DEEP
                border = _GREEN_DARK
            elif state == "missing":
                background = _DANGER_SOFT
                foreground = _RED_DEEP
                active_background = _DANGER_HOVER
                border = _DANGER_BORDER
            else:
                background = _CONTROL
                foreground = _MUTED
                active_background = _CONTROL_HOVER
                border = _BORDER
            button.configure(
                text=label,
                bg=background,
                fg=foreground,
                activebackground=active_background,
                activeforeground=foreground,
                highlightbackground=border,
                highlightcolor=border,
            )
            tooltip = self.environment_status_tooltips.get(dependency_name)
            if tooltip is not None:
                tooltip.text = detail

    def _build_environment_status(
        self, row: int, form_pad: int,
    ) -> int:
        """用一行等尺寸按钮显示各环境项目的检测状态。"""
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
            header, text="检测状态", bg=_SURFACE, fg=_GREEN_DEEP,
            font=("Microsoft YaHei UI", 9, "bold"), anchor="w",
        ).pack(side="left", padx=(8, 0))

        button_grid = tk.Frame(panel, bg=_SURFACE)
        button_grid.grid(
            row=1, column=0, sticky="ew", padx=12, pady=(4, 8))
        for column, dependency_name in enumerate(_ENVIRONMENT_STATUS_ORDER):
            button_grid.grid_columnconfigure(
                column * 2, weight=0, uniform="environment_status")
            if column < _ENVIRONMENT_COLUMN_COUNT - 1:
                button_grid.grid_columnconfigure(
                    column * 2 + 1, minsize=_PANEL_ACTION_BUTTON_GAP)
            button = tk.Button(
                button_grid,
                relief="flat", bd=0, highlightthickness=1,
                font=("Microsoft YaHei UI", _UI_BODY_FONT_SIZE),
                anchor="center", justify="center",
                width=_ENVIRONMENT_BUTTON_WIDTH,
                height=2,
                padx=_ENVIRONMENT_BUTTON_PADDING[0],
                pady=_ENVIRONMENT_BUTTON_PADDING[1],
                takefocus=True, cursor="hand2", command=self._run,
            )
            button.grid(
                row=0, column=column * 2, sticky="w",
            )
            self.environment_status_buttons[dependency_name] = button
            self.environment_status_tooltips[dependency_name] = attach_tooltip(
                button, "尚未检测。")
        self._refresh_environment_status_buttons()
        return row + 1

    def _build_admin_requirement_notice(
        self, row: int, form_pad: int,
    ) -> int:
        already_admin = bool(self.is_administrator)
        background = _GREEN_SOFT if already_admin else _AMBER_SOFT
        border = _GREEN if already_admin else _AMBER
        heading_colour = _GREEN_DEEP if already_admin else _AMBER_DEEP
        panel = tk.Frame(
            self.form_inner, bg=background,
            highlightbackground=border, highlightthickness=1,
        )
        panel.grid(
            row=row, column=0, columnspan=2, sticky="ew",
            padx=form_pad, pady=(5, 2),
        )
        header = tk.Frame(panel, bg=background)
        header.pack(fill="x", padx=12, pady=(6, 2))
        tk.Label(
            header,
            text=("管理员权限已启用" if already_admin else
                  "管理员权限未启用"),
            bg=background, fg=heading_colour,
            font=("Microsoft YaHei UI", 9, "bold"), anchor="w",
        ).pack(side="left")
        self.admin_mode_button = AdminModeButton(
            header,
            value=already_admin,
            enabled=(os.name == "nt" and not already_admin),
            command=self._request_admin_mode,
            background=background,
        )
        self.admin_mode_button.pack(side="right")
        admin_tooltip = (
            "当前已启用管理员权限。关闭 DAISY 后以普通方式重新打开，即可回到普通权限。"
            if already_admin else
            "通过 Windows UAC 以管理员权限重新启动 DAISY。"
        )
        for widget in self.admin_mode_button.tooltip_widgets:
            attach_tooltip(widget, admin_tooltip)
        detail = tk.Label(
            panel,
            text=(
                "允许执行需要管理员权限的 Windows 存储与 SMART 查询。"
                if already_admin else
                "未启用时，部分 Windows 存储或 SMART 信息可能无法读取。"
            ),
            bg=background, fg=_TEXT,
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
            padx=form_pad, pady=(3, 1),
        )
        panel.grid_columnconfigure(1, weight=1)
        found_count = len(self.storage_disk_options)
        selectable_count = sum(
            option.selectable for option in self.storage_disk_options)
        detail = tk.Label(
            panel,
            text=(
                f"检测到 {found_count} 块硬盘，其中 {selectable_count} 块"
                "可登记；选择后可开始任务。"
                if found_count else
                "读取本机硬盘信息，再选择要登记的硬盘。"
            ),
            bg=_SURFACE, fg=_TEXT,
            font=("Microsoft YaHei UI", 9), anchor="w",
            justify="left", wraplength=720,
        )
        self.storage_detect_button = ttk.Button(
            panel,
            text="重新检测硬盘" if found_count else "检测硬盘",
            style="DiscoveryAction.TButton",
            width=_FORM_ACTION_BUTTON_WIDTH,
            command=self._run_storage_inventory,
        )
        self.storage_detect_button.grid(
            row=0, column=0, sticky="w",
            padx=(_SPACING_STANDARD, _SPACING_STANDARD), pady=3)
        detail.grid(
            row=0, column=1, sticky="ew",
            padx=(0, _SPACING_STANDARD), pady=3)
        attach_tooltip(
            self.storage_detect_button,
            "读取本机硬盘、分区和卷，识别可读取 SMART 信息的硬盘，并刷新下方清单。",
        )
        panel.bind(
            "<Configure>",
            lambda event, label=detail: label.configure(
                wraplength=max(
                    260,
                    event.width
                    - self.storage_detect_button.winfo_reqwidth() - 46,
                )),
        )
        return row + 1

    def _build_parse_database_detection(
        self, row: int, form_pad: int,
    ) -> int:
        """在数据库路径下方建立独立的只读解析操作行。"""
        panel = tk.Frame(
            self.form_inner, bg=_SURFACE,
            highlightbackground=_BORDER, highlightthickness=1,
        )
        panel.grid(
            row=row, column=0, columnspan=2, sticky="ew",
            padx=form_pad, pady=(3, 1),
        )
        panel.grid_columnconfigure(1, weight=1)
        source = self.values.get("database")
        database = str(source.get() or "") if source is not None else ""
        inspection = self._matching_parse_inspection(database)
        if inspection is None:
            detail_text = (
                "选择数据库，再点击「解析数据库」查看可用的数据模块。"
            )
        else:
            descriptor = inspection.descriptor
            type_label = (
                "封存快照" if descriptor.database_type == "snapshot"
                else "Diff 数据库"
            )
            available = inspection.module_state_counts.get("available", 0)
            detail_text = (
                f"已解析{type_label}；"
                f"{self._parse_database_version_text(descriptor)}；"
                f"数据库结构版本 {descriptor.schema_version}；"
                f"可用数据模块 {available} 项。数据库变化后请重新解析。"
            )
        self.parse_detect_button = ttk.Button(
            panel, text="解析数据库", style="DiscoveryAction.TButton",
            width=_FORM_ACTION_BUTTON_WIDTH,
            command=self._detect_parse_database,
        )
        self.parse_detect_button.grid(
            row=0, column=0, sticky="w",
            padx=(_SPACING_STANDARD, _SPACING_STANDARD), pady=3)
        detail = tk.Label(
            panel, text=detail_text, bg=_SURFACE, fg=_TEXT,
            font=("Microsoft YaHei UI", 9), anchor="w",
            justify="left", wraplength=720,
        )
        detail.grid(
            row=0, column=1, sticky="ew",
            padx=(0, _SPACING_STANDARD), pady=3)
        self.parse_detection_detail_label = detail
        attach_tooltip(
            self.parse_detect_button,
            "只读识别数据库类型、结构版本和可用数据模块。",
        )
        panel.bind(
            "<Configure>",
            lambda event, label=detail: label.configure(
                wraplength=max(
                    260,
                    event.width
                    - self.parse_detect_button.winfo_reqwidth() - 46,
                )),
        )
        return row + 1

    def _configure_form_label_column(
        self, form_pad: int | None = None,
    ) -> None:
        """按全局六字标题体系固定标签列，避免切页时输入区左右跳动。"""
        if form_pad is None:
            form_pad = (
                _SPACING_SECTION
                if self.compact_layout else _SPACING_OUTER)
        label_gap = (
            _SPACING_STANDARD
            if self.compact_layout else _SPACING_SECTION)
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
        previous_inner = self.form_inner
        next_inner = tk.Frame(self.form_canvas, bg=_SURFACE)
        next_inner.bind(
            "<Configure>", self._schedule_form_scroll_sync,
        )
        self.form_inner = next_inner
        self.values = {}
        self.install_tool_buttons = {}
        self.environment_install_buttons = {}
        self.environment_status_buttons = {}
        self.environment_status_tooltips = {}
        self.admin_mode_button = None
        self.storage_detect_button = None
        self.parse_detect_button = None
        self.parse_detection_detail_label = None
        form_pad = (
            _SPACING_SECTION
            if self.compact_layout else _SPACING_OUTER)
        label_gap = (
            _SPACING_STANDARD
            if self.compact_layout else _SPACING_SECTION)
        saved = _task_values(
            self.task, self.saved_values.get(self.task.key, {}))
        active_specs = [
            spec for spec in self.task.fields
            if _field_active(spec, saved) and not spec.top_menu
        ]
        section_field_counts: dict[str, int] = {}
        for spec in active_specs:
            section_field_counts[spec.section] = (
                section_field_counts.get(spec.section, 0) + 1)
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
                    "将运行 Script\\Test 中的全部自动化测试，并在系统"
                    "临时目录创建测试夹具。"
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

        if self.task.key in _STG_ADMIN_TASKS:
            row = self._build_admin_requirement_notice(row, form_pad)
        if self.task.key == "storage_collect":
            row = self._build_storage_detection(row, form_pad)
        if self.task.key == "env_check":
            row = self._build_environment_status(row, form_pad)
            row = self._build_environment_installation(row, form_pad)

        current_section: str | None = None
        section_colour = _NAV_COLOURS.get(
            self.task.key, (_ACCENT, _ACCENT_DARK))[0]
        for spec in active_specs:
            if spec.section != current_section:
                current_section = spec.section
                # 单字段分区和“分区名＝首字段名”不增加信息，只会在 1080p
                # 中重复占用一整行；多字段分组仍保留清晰的彩色标题。
                if (section_field_counts[current_section] > 1
                        and current_section != spec.label):
                    section = tk.Frame(self.form_inner, bg=_SURFACE)
                    section.grid(
                        row=row, column=0, columnspan=2, sticky="ew",
                        padx=form_pad, pady=_FORM_SECTION_PADY,
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
            field_help = spec.help
            field_enabled = True
            if spec.key == "raw_deep_validation":
                field_enabled, raw_reason = raw_runtime_capability_status(
                    self.runtime_capabilities)
                if not field_enabled:
                    current = False
                    field_help = f"{spec.help} 当前不可用：{raw_reason}"
            variable_height = spec.kind in _VARIABLE_HEIGHT_FIELD_KINDS
            if not variable_height:
                self.form_inner.grid_rowconfigure(
                    row, minsize=_FORM_SINGLE_ROW_HEIGHT,
                    uniform="form_single_row",
                )
            field_label = tk.Label(
                self.form_inner, text=spec.label, bg=_SURFACE, fg=_TEXT,
                font=("Microsoft YaHei UI", 9, "bold"), anchor="e",
                justify="right",
            )
            field_label.grid(
                row=row, column=0,
                sticky="ne" if variable_height else "e",
                padx=(form_pad, label_gap),
                pady=(
                    (_FORM_FIELD_PADY + 5, _FORM_FIELD_PADY)
                    if variable_height else _FORM_FIELD_PADY
                ),
            )

            cell = tk.Frame(self.form_inner, bg=_SURFACE)
            cell.grid(row=row, column=1, sticky="ew", padx=(0, form_pad),
                      pady=_FORM_FIELD_PADY)
            cell.grid_columnconfigure(0, weight=1)

            if spec.kind == "disk_pool":
                widget = StorageDiskPool(
                    cell, options=self.storage_disk_options,
                    initial=current, on_change=self._update_preview,
                )
                widget.grid(row=0, column=0, columnspan=2, sticky="ew")
                self.values[spec.key] = widget
            elif spec.kind == "parse_modules":
                widget = ParseModulePool(
                    cell,
                    inspection=self._matching_parse_inspection(
                        saved.get("database")),
                    preset=str(saved.get("preset") or "full-audit"),
                    initial=current,
                    on_change=self._update_preview,
                )
                widget.grid(row=0, column=0, columnspan=3, sticky="ew")
                self.values[spec.key] = widget
            elif spec.kind == "multi_choice":
                widget = MultiChoicePool(
                    cell, choices=spec.choices, initial=current,
                    on_change=self._update_preview,
                )
                widget.grid(row=0, column=0, columnspan=3, sticky="ew")
                self.values[spec.key] = widget
            elif spec.kind == "metadata_controls":
                legacy_storage = str(current or "complete")
                legacy_exiftool = bool(
                    saved.get("metadata_exiftool", True))
                legacy_ffprobe = bool(
                    saved.get("metadata_ffprobe", True))
                widget = MetadataToolButtonGroup(
                    cell,
                    exiftool_mode=saved.get(
                        "metadata_exiftool_mode",
                        legacy_storage if legacy_exiftool else "off"),
                    ffprobe_mode=saved.get(
                        "metadata_ffprobe_mode",
                        legacy_storage if legacy_ffprobe else "off"),
                    on_change=self._update_preview,
                )
                widget.grid(row=0, column=0, columnspan=3, sticky="ew")
                self.values[spec.key] = widget
            elif spec.kind == "verification_tools":
                raw_available, raw_reason = raw_runtime_capability_status(
                    self.runtime_capabilities)
                widget = VerificationToolButtonGroup(
                    cell,
                    initial={
                            key: saved.get(key, True)
                        for key in (
                            "verify_builtin", "verify_exiftool",
                            "verify_ffprobe", "verify_sevenzip",
                            "raw_deep_validation",
                        )
                    },
                    raw_enabled=raw_available,
                    raw_reason=raw_reason,
                    on_change=self._update_preview,
                )
                widget.grid(row=0, column=0, columnspan=3, sticky="ew")
                self.values[spec.key] = widget
            elif spec.kind == "value_toggle":
                widget = ValueToggleButton(
                    cell, choices=self._field_choices(spec),
                    initial=current,
                )
                widget._daisy_field_key = spec.key  # type: ignore[attr-defined]
                widget.on_change = (
                    lambda control=widget:
                    self._value_toggle_changed(control))
                widget.grid(row=0, column=0, columnspan=3, sticky="w")
                self.values[spec.key] = widget
                if field_help:
                    for target in widget.tooltip_widgets:
                        attach_tooltip(target, field_help)
            elif (
                spec.kind == "choice_buttons"
                or (
                    spec.kind in ("choice", "choice_flag", "disk_choice")
                    and {
                        value for _label, value in self._field_choices(spec)
                    } != {False, True}
                )
            ):
                widget = ChoiceButtonGroup(
                    cell, choices=self._field_choices(spec), initial=current)
                widget._daisy_field_key = spec.key  # type: ignore[attr-defined]
                widget.on_change = (
                    lambda control=widget: self._choice_button_changed(
                        control))
                widget.grid(row=0, column=0, columnspan=3, sticky="ew")
                self.values[spec.key] = widget
            elif (spec.kind == "choice_flag"
                  and {value for _label, value in self._field_choices(spec)}
                  == {False, True}):
                widget = BooleanToggleButton(
                    cell, choices=self._field_choices(spec),
                    initial=current, on_change=self._update_preview,
                    enabled=field_enabled,
                )
                widget._daisy_field_key = spec.key  # type: ignore[attr-defined]
                widget.grid(row=0, column=0, columnspan=3, sticky="ew")
                self.values[spec.key] = widget
                if field_help:
                    for target in widget.tooltip_widgets:
                        attach_tooltip(target, field_help)
            elif spec.kind in ("multidir", "multimapdir"):
                widget = DirectoryListEditor(
                    cell, initial=current, title=spec.label,
                    on_change=self._update_preview,
                )
                widget.grid(
                    row=0, column=0, columnspan=2, sticky="ew")
                self.values[spec.key] = widget
            elif spec.kind == "root_label_map":
                widget = RootLabelMapEditor(
                    cell, initial=current, on_change=self._update_preview)
                widget.grid(row=0, column=0, columnspan=3, sticky="ew")
                self.values[spec.key] = widget
            elif spec.kind == "multiline":
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
            else:
                var = tk.StringVar(value=str(current or ""))
                var.trace_add("write", lambda *_args: self._update_preview())
                widget = ttk.Entry(cell, textvariable=var)
                has_action = spec.kind in (
                    "dir", "file", "save", "parse_database")
                entry_column = 1 if has_action else 0
                if has_action:
                    cell.grid_columnconfigure(0, weight=0)
                    cell.grid_columnconfigure(entry_column, weight=1)
                widget.grid(row=0, column=entry_column, sticky="ew")
                self.values[spec.key] = var
                if spec.kind == "dir":
                    widget.bind(
                        "<FocusOut>",
                        lambda _event, variable=var:
                        self._normalize_directory_variable(variable),
                    )
                if spec.kind == "parse_database":
                    widget.bind(
                        "<FocusOut>", self._parse_database_focus_out)
                if spec.kind in ("dir", "file", "save", "parse_database"):
                    browse_button = ttk.Button(
                        cell,
                        text=("选择"
                              if spec.kind == "parse_database" else "浏览"),
                        style="FilePicker.TButton",
                        width=_FILE_PICKER_BUTTON_WIDTH,
                        command=lambda s=spec, v=var: self._browse(s, v),
                    )
                    browse_button.grid(
                        row=0, column=0, sticky="w",
                        padx=(0, _INLINE_CONTROL_GAP))
                    attach_tooltip(
                        browse_button,
                        (
                            f"选择「{spec.label}」的保存位置。"
                            if spec.kind == "save" else
                            f"选择「{spec.label}」。"
                        ),
                    )
            if field_help:
                attach_tooltip(field_label, field_help)
                attach_tooltip(cell, field_help)
                if not isinstance(widget, VerificationToolButtonGroup):
                    tooltip_targets = getattr(
                        widget, "tooltip_widgets", (widget,))
                    for target in tooltip_targets:
                        attach_tooltip(target, field_help)
            row += 1
            if self.task.key == "parse_db" and spec.kind == "parse_database":
                row = self._build_parse_database_detection(row, form_pad)

        self._apply_font_to_tree(self.form_inner)
        self.form_inner.update_idletasks()
        try:
            self.form_canvas.itemconfigure(
                self.form_window,
                window=self.form_inner,
                width=max(1, int(self.form_canvas.winfo_width())),
            )
        except (AttributeError, tk.TclError, TypeError, ValueError):
            self.form_inner.destroy()
            self.form_inner = previous_inner
            raise
        previous_inner.destroy()
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
        expected_display: str | None = None
        if field_key is not None and isinstance(event.widget, ttk.Combobox):
            spec = next((
                field for field in self.task.fields
                if field.key == field_key
            ), None)
            choices = self._field_choices(spec) if spec is not None else ()
            try:
                selected_index = int(event.widget.current())
            except (tk.TclError, TypeError, ValueError):
                selected_index = -1
            if 0 <= selected_index < len(choices):
                expected_display, selected_value = choices[selected_index]
                collected[field_key] = selected_value
        self.saved_values[task_key] = collected
        if field_key is not None and (
                collected.get(field_key) == previous.get(field_key)):
            if expected_display is None:
                spec = next((
                    field for field in self.task.fields
                    if field.key == field_key
                ), None)
                if spec is not None:
                    expected_display = self._choice_display(
                        spec, collected.get(field_key))
            if expected_display:
                event.widget.set(expected_display)
                try:
                    event.widget.selection_clear()
                    event.widget.icursor(tk.END)
                except tk.TclError:
                    pass
                self.root.after_idle(
                    lambda widget=event.widget, text=expected_display:
                    self._restore_choice_display(widget, text)
                )
            self._update_preview()
            return

        # 等待 ComboboxSelected 事件完成后再重建条件字段，避免 Tcl/Tk 在原
        # 控件销毁后继续刷新选择状态，导致重复选择当前项时显示为空。
        self.root.after_idle(
            lambda key=task_key, fraction=scroll_fraction:
            self._build_form(fraction) if self.task.key == key else None
        )

    @staticmethod
    def _restore_choice_display(
        widget: ttk.Combobox, display_text: str,
    ) -> None:
        """重选当前项后恢复正常文字色，不保留编辑框选区。"""
        try:
            if not widget.winfo_exists():
                return
            widget.set(display_text)
            widget.selection_clear()
            widget.icursor(tk.END)
            widget.winfo_toplevel().focus_set()
        except tk.TclError:
            return

    def _value_toggle_changed(self, source: ValueToggleButton) -> None:
        """同步非布尔二态按钮，并保持当前表单几何结构稳定。"""
        task_key = self.task.key
        self.saved_values[task_key] = self._collect_values()
        self._refresh_scan_advanced_values()
        self._update_preview()

    def _choice_button_changed(self, source: ChoiceButtonGroup) -> None:
        """模式按钮变化后保存当前值，再展开对应条件字段。"""
        task_key = self.task.key
        scroll_fraction = self.form_canvas.yview()[0]
        collected = self._collect_values()
        self.saved_values[task_key] = collected
        field_key = getattr(source, "_daisy_field_key", None)
        if task_key == "parse_db" and field_key == "preset":
            module_pool = self.values.get("parse_modules")
            if isinstance(module_pool, ParseModulePool):
                module_pool.set_preset(
                    source.get(), initial=collected.get("parse_modules"))
                self.saved_values[task_key] = self._collect_values()
            self._update_preview()
            return
        layout_controllers = {
            dependency_key
            for spec in self.task.fields
            if not spec.top_menu
            for dependency_key, _allowed_values in spec.active_when
        }
        if field_key not in layout_controllers:
            self._refresh_scan_advanced_values()
            self._update_preview()
            return
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

    def _matching_parse_inspection(
        self, database: object,
    ) -> dbparse.ParseDatabaseInspection | None:
        inspection = getattr(self, "parse_inspection", None)
        raw = str(database or "").strip()
        if inspection is None or not raw:
            return None
        return inspection if os.path.normcase(
            os.path.abspath(inspection.descriptor.path)
        ) == os.path.normcase(_absolute(raw)) else None

    @staticmethod
    def _parse_database_version_text(descriptor) -> str:
        """返回明确的快照／Diff 来源版本，不从文件名猜测。"""
        raw = str(getattr(descriptor, "source_version", None) or "").strip()
        if not raw or raw.casefold() in {"unknown", "none", "null", "未知"}:
            display = "未知"
        else:
            display = raw if raw.casefold().startswith("v") else f"v{raw}"
        label = (
            "快照版本"
            if getattr(descriptor, "database_type", None) == "snapshot"
            else "Diff 版本"
        )
        return f"{label} {display}"

    def _invalidate_parse_database_selection(self) -> None:
        """路径变化时清除旧识别结果；只有解析按钮可以读取数据库。"""
        if self.task.key != "parse_db" or self.parse_detection_active:
            return
        source = self.values.get("database")
        raw = str(source.get() or "").strip() if source is not None else ""
        if self._matching_parse_inspection(raw) is not None:
            return
        values = self._collect_values()
        values.pop("parse_modules", None)
        self.saved_values["parse_db"] = values
        self.parse_inspection = None
        self.parse_inspection_path = ""
        module_pool = self.values.get("parse_modules")
        if isinstance(module_pool, ParseModulePool):
            module_pool.invalidate()
        detail = getattr(self, "parse_detection_detail_label", None)
        if detail is not None:
            detail.configure(text=(
                "已选择数据库；点击「解析数据库」后识别版本和数据模块。"
                if raw else
                "选择数据库，再点击「解析数据库」查看数据模块。"
            ))
        self._update_preview()

    def _parse_database_focus_out(self, _event: tk.Event) -> None:
        self._invalidate_parse_database_selection()

    def _detect_parse_database(self) -> None:
        """在后台只读识别数据库，绝不在 Tk 主线程执行 SQLite 探测。"""
        if self.task.key != "parse_db" or self._task_is_active():
            return
        values = self._collect_values()
        raw = str(values.get("database") or "").strip()
        if not raw:
            messagebox.showerror(
                "尚未选择数据库", "请先选择需要解析的 SQLite 数据库。",
                parent=self.root,
            )
            return
        database = _absolute(raw)
        if not os.path.isfile(database):
            messagebox.showerror(
                "数据库不存在", f"找不到输入数据库：\n{database}",
                parent=self.root,
            )
            return

        previous_path = str(getattr(self, "parse_inspection_path", ""))
        self.saved_values["parse_db"] = values
        if (previous_path and os.path.normcase(previous_path)
                != os.path.normcase(database)):
            self.saved_values["parse_db"].pop("parse_modules", None)
        self.parse_inspection = None
        self.parse_inspection_path = ""
        self.parse_detection_generation += 1
        generation = self.parse_detection_generation
        self.parse_detection_active = True
        if self.parse_detect_button is not None:
            self.parse_detect_button.configure(state="disabled")
        self.run_button.configure(state="disabled")
        self._set_task_navigation_state("disabled")
        self._set_settings_expanded(False)
        self._set_progress_expanded(True)
        self._set_log_expanded(True)
        self._reset_progress("解析数据库")
        self.progress_target_label.configure(text=database, fg=_TEXT)
        self.progress_stage_label.configure(
            text="解析数据库 · 正在分析", fg=_GREEN_DARK)
        self.progress_detail_label.configure(
            text="正在读取数据库类型、结构版本和可用数据模块…", fg=_MUTED)
        self._set_work_indeterminate()
        self._set_status("正在解析数据库…")
        self._append_log(
            f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] "
            f"开始解析数据库：{database}\n",
            "meta",
        )

        def inspect() -> None:
            try:
                result = dbparse.inspect_parse_database(
                    database, verify_integrity=False)
            except Exception as exc:
                self.events.put((
                    "parse_detection_done", generation, database,
                    None, f"{type(exc).__name__}：{exc}",
                ))
            else:
                self.events.put((
                    "parse_detection_done", generation, database,
                    result, None,
                ))

        threading.Thread(target=inspect, daemon=True).start()

    def _finish_parse_database_detection(
        self,
        generation: int,
        database: str,
        inspection: dbparse.ParseDatabaseInspection | None,
        error: str | None,
    ) -> None:
        if generation != self.parse_detection_generation:
            return
        self.parse_detection_active = False
        self._stop_work_progress()
        self._set_task_navigation_state("normal")
        if error is not None or inspection is None:
            self.parse_inspection = None
            self.parse_inspection_path = ""
            self._set_work_fraction(100, style="Danger")
            self.progress_stage_bar.configure(
                value=100, style="Danger.Horizontal.TProgressbar")
            self.progress_stage_label.configure(
                text="解析数据库失败", fg=_DANGER)
            self.progress_detail_label.configure(
                text=self._short_progress_text(error or "未提供失败原因"),
                fg=_DANGER,
            )
            self._append_log(
                f"解析数据库失败：{error or '未提供失败原因'}\n", "error")
            if self.task.key == "parse_db":
                self._build_form()
            self._set_settings_expanded(True)
            self._set_progress_expanded(True)
            self._set_log_expanded(True)
            self._set_status("解析数据库失败；请检查文件与日志。", _DANGER)
            self.run_button.configure(state="normal")
            if self.parse_detect_button is not None:
                self.parse_detect_button.configure(state="normal")
            messagebox.showerror(
                "解析数据库失败", error or "未提供失败原因", parent=self.root)
            return

        self.parse_inspection = inspection
        self.parse_inspection_path = database
        descriptor = inspection.descriptor
        type_label = (
            "封存快照" if descriptor.database_type == "snapshot"
            else "Diff 数据库"
        )
        counts = inspection.module_state_counts
        summary = (
            f"{type_label}；{self._parse_database_version_text(descriptor)}；"
            f"数据库结构版本 {descriptor.schema_version}；"
            f"可用数据模块 {counts.get('available', 0)} 项"
        )
        self._set_work_fraction(100, style="Success")
        self.progress_stage_bar.configure(
            value=100, style="Success.Horizontal.TProgressbar")
        self.progress_stage_label.configure(text="数据库已解析", fg=_SUCCESS)
        self.progress_detail_label.configure(text=summary, fg=_SUCCESS)
        self._append_log(f"数据库已解析：{summary}\n", "success")
        if self.task.key == "parse_db":
            self._build_form()
        self._set_settings_expanded(True)
        self._set_progress_expanded(False)
        self._set_log_expanded(False)
        self._set_status(
            f"已解析{summary}；可调整导出范围、数据模块和输出格式。",
            _SUCCESS,
        )
        self.run_button.configure(state="normal")
        self._position_form_scroll(0.0)

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
            if (self.task.key == "parse_db"
                    and spec.kind == "parse_database"):
                self._invalidate_parse_database_selection()

    def _collect_values(self) -> dict[str, object]:
        result = _task_values(
            self.task, self.saved_values.get(self.task.key, {}))
        specs = {spec.key: spec for spec in self.task.fields}
        for key, source in self.values.items():
            spec = specs[key]
            if isinstance(source, (
                    MetadataToolButtonGroup, VerificationToolButtonGroup)):
                result.update(source.get_values())
            elif isinstance(source, (
                    DirectoryListEditor, RootLabelMapEditor,
                    StorageDiskPool, ChoiceButtonGroup,
                    BooleanToggleButton, ValueToggleButton,
                    MultiChoicePool, ParseModulePool)):
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

    def _collect_persistable_values(self) -> dict[str, object]:
        """收集可保存的配置值，并保留暂不可用能力的请求状态。"""
        result = self._collect_values()
        for source in self.values.values():
            if isinstance(source, VerificationToolButtonGroup):
                result.update(source.get_persisted_values())
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
        if (self.task.key in _SCAN_TASK_KEYS
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

    def _tool_path_menu_label(self, tool_name: str) -> str:
        display_name = _TOOL_DISPLAY_NAMES[tool_name]
        manual_paths = getattr(self, "manual_tool_paths", {})
        selected = str(manual_paths.get(tool_name) or "").strip()
        if selected:
            status = "手动指定"
        else:
            detected_tools = getattr(self, "detected_tools", {})
            detected = detected_tools.get(tool_name) or {}
            detected_path = str(detected.get("path") or "").strip()
            if detected_path and detected.get("verified") is True:
                status = "已检测"
            else:
                status = "未检测"
        return f"{display_name} · {status}"

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
        self._save_gui_preferences()
        self._set_status(
            f"已指定 {display_name} 路径；任务启动时会验证。")

    def _clear_manual_tool_paths(self) -> None:
        if self._task_is_active():
            return
        count = len(self.manual_tool_paths)
        self.manual_tool_paths.clear()
        self._refresh_tool_path_menu_labels()
        self._update_preview()
        self._save_gui_preferences()
        self._set_status(
            f"已清除 {count} 项手动工具路径。"
            if count else "当前没有手动工具路径。"
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
                "将触发 Windows UAC 确认；管理员进程启动成功后，当前窗口才会关闭。"
                "重启后会返回"
                "当前功能页面；已保存的非路径选项和手动工具路径继续保留，"
                "档案、数据库、输出目录、硬盘选择和本窗口日志不保留。\n\n"
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

    def _cancel_open_result_flash(self) -> None:
        after_id = getattr(self, "open_result_flash_after_id", None)
        self.open_result_flash_after_id = None
        if after_id is not None:
            try:
                self.root.after_cancel(after_id)
            except tk.TclError:
                pass
        self._set_open_result_button_highlighted(False)

    def _set_open_result_button_highlighted(
        self, highlighted: bool,
    ) -> bool:
        """在色带绿色提示态与米黄色常态之间切换结果按钮。"""
        button = getattr(self, "open_output_button", None)
        if button is None:
            return False
        palette = (
            (_GREEN, _GREEN_DEEP, _GREEN_DARK, "white", _GREEN_DARK)
            if highlighted else
            (
                _TASK_TOOLBAR_BACKGROUND, _AMBER_DEEP,
                _TASK_TOOLBAR_HOVER, _AMBER_DEEP, _TASK_TOOLBAR_HOVER,
            )
        )
        background, foreground, active_background, active_foreground, border = (
            palette)
        try:
            button.configure(
                bg=background, fg=foreground,
                activebackground=active_background,
                activeforeground=active_foreground,
                highlightbackground=border,
                highlightcolor=border,
            )
        except tk.TclError:
            return False
        return True

    def _advance_open_result_flash(self, step: int) -> None:
        """执行两次绿色脉冲；每次回到米黄色后才算一次完整闪烁。"""
        self.open_result_flash_after_id = None
        sequence = (True, False, True, False)
        if step >= len(sequence):
            return
        if not self._set_open_result_button_highlighted(sequence[step]):
            return
        if step + 1 < len(sequence):
            self.open_result_flash_after_id = self.root.after(
                230,
                lambda next_step=step + 1:
                self._advance_open_result_flash(next_step),
            )

    def _flash_open_result_button(self) -> None:
        """任务产出结果后让目录按钮闪烁两次，不抢焦点、不弹窗。"""
        self._cancel_open_result_flash()
        try:
            self._advance_open_result_flash(0)
        except tk.TclError:
            self.open_result_flash_after_id = None

    def _offer_open_result_directory(self, path: str) -> None:
        """任务完成后询问是否打开本次结果目录。"""
        if not os.path.isdir(path):
            return
        if not messagebox.askyesno(
            "任务已完成",
            f"是否打开结果目录？\n\n{path}",
                icon="question", parent=self.root):
            return
        try:
            os.startfile(path)
        except OSError as exc:
            messagebox.showerror(
                "无法打开结果目录", str(exc), parent=self.root)

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
        ).pack(
            side="left", padx=_PANEL_HEADER_PADX,
            pady=_SPACING_INLINE)
        ttk.Button(
            header, text="关闭", style="PanelHeader.TButton",
            width=_STANDARD_BUTTON_WIDTH,
            command=self._close_log_window,
        ).pack(
            side="right",
            padx=(_STANDARD_BUTTON_GAP, _SPACING_STANDARD),
            pady=_SPACING_INLINE,
        )
        ttk.Button(
            header, text="清空日志", style="PanelHeader.TButton",
            width=_STANDARD_BUTTON_WIDTH,
            command=self._clear_log,
        ).pack(side="right", pady=_SPACING_INLINE)

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
                "暂不能重置会话",
                "任务队列运行期间不能重置当前会话。",
                parent=self.root,
            )
            return
        if not messagebox.askyesno(
                "重置当前会话",
                "这会清空所有页面已填写的目录和参数、手动工具路径、硬盘清单、"
                "运行日志、进度和可重建缓存，并返回运行环境检测页。\n\n"
                "已生成的快照、硬盘档案 ZIP 和导出结果不会被删除。确定继续吗？",
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
        self.environment_missing_reasons = {}
        self.missing_installable_tools = ()
        self.runtime_capabilities.clear()
        if getattr(self, "mini_mode", False):
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
        summary = f"当前会话已重置；已清理 {removed} 项可重建缓存。"
        if disk.errors:
            messagebox.showwarning(
                "部分缓存未清理",
                summary + "\n\n" + "\n".join(disk.errors),
                parent=self.root,
            )
        else:
            messagebox.showinfo(
                "会话已重置", summary, parent=self.root)

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
        self._refresh_environment_status_buttons()
        self._refresh_environment_actions()
        self._refresh_tool_path_menu_labels()

    def _refresh_environment_actions(self) -> None:
        action_state = (
            "normal"
            if self.process is None
            and not self.worker_starting
            and not self.run_jobs
            else "disabled"
        )
        for button in self.install_tool_buttons.values():
            button.configure(state=action_state)
        for button in getattr(
                self, "environment_status_buttons", {}).values():
            button.configure(state=action_state)
        storage_button = getattr(self, "storage_detect_button", None)
        if storage_button is not None:
            storage_button.configure(state=action_state)
        admin_button = getattr(self, "admin_mode_button", None)
        if admin_button is not None:
            already_admin = bool(getattr(self, "is_administrator", False))
            admin_button.set_mode(
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
        capabilities = payload.get("capabilities")
        if isinstance(capabilities, dict):
            self._apply_runtime_capabilities({
                "capabilities": capabilities,
            })
        raw_missing = payload.get("missing")
        missing_names: list[str] = []
        missing_reasons: dict[str, str] = {}
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
                missing_reasons[name] = str(
                    item.get("reason") or "环境检测未找到该工具。")
                if (item.get("installable") is True
                        and name in _INSTALLABLE_TOOL_PACKAGES
                        and name not in installable):
                    installable.append(name)
        self.environment_missing_names = tuple(missing_names)
        self.environment_missing_reasons = missing_reasons
        self.missing_installable_tools = tuple(installable)
        version_report = getattr(
            self, "pending_install_version_report", None)
        if (version_report is not None
                and getattr(self, "process_task_key", None) == "env_check"):
            version_report.after_version = (
                self._install_version_from_inventory(
                    version_report.tool_name, payload))
            version_report.inventory_received = True
        self._refresh_tool_cache_labels()

    def _apply_runtime_capabilities(
        self, payload: dict[str, object],
    ) -> None:
        capabilities = payload.get("capabilities")
        if not isinstance(capabilities, dict):
            return
        raw = capabilities.get(envcap.RAW_CAPABILITY_ID)
        if not isinstance(raw, dict) or raw.get("state") not in (
                envcap.RUNTIME_CAPABILITY_STATES):
            return
        details = raw.get("details")
        normalized = dict(raw)
        normalized["details"] = (
            dict(details) if isinstance(details, dict) else {})
        if not hasattr(self, "runtime_capabilities"):
            self.runtime_capabilities = {}
        if not hasattr(self, "saved_values"):
            self.saved_values = {}
        self.runtime_capabilities[envcap.RAW_CAPABILITY_ID] = normalized
        current_task_key = getattr(getattr(self, "task", None), "key", None)
        rebuild_form = (
            hasattr(self, "form_inner")
            and current_task_key == "verify"
        )
        if rebuild_form and getattr(self, "values", None):
            self.saved_values[str(current_task_key)] = (
                self._collect_persistable_values())
        self._refresh_environment_status_buttons()
        if rebuild_form:
            self._build_form()
        else:
            self._update_preview()

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
                f"检测到 {found_count} 块硬盘，其中 {selectable_count} 块"
                "可登记。\n\n设置区已展开。请选择硬盘，然后点击「开始任务」。"
            )
            show_dialog = messagebox.showinfo
        else:
            self._set_status(
                "硬盘检测完成，但没有找到可登记的硬盘。", _WARNING)
            title = "没有可登记的硬盘"
            message = (
                f"检测到 {found_count} 块硬盘，但没有硬盘同时满足联机"
                "且登记信息完整的条件。\n\n设置区已展开，可查看"
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
        checking_version = (
            self.process_task_key == _DEPENDENCY_VERSION_CHECK_KEY)
        installing = self.process_task_key == _DEPENDENCY_INSTALL_KEY
        detecting_storage = self.process_task_key == "storage_list"
        title = (
            "DAISY 功能自检" if self_test else
            "查询软件版本" if checking_version else
            "安装或更新工具" if installing else
            "检测硬盘" if detecting_storage else
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
                "正在等待软件源返回最新版本…"
                if checking_version else
                "正在等待安装程序输出…"
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
        """只向当前 GUI 精确持有的可控子进程 stdin 写入控制消息。"""
        process = self.process
        if (self.process_task_key not in _CONTROL_TASK_KEYS
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
            self._set_status("无法向当前任务发送控制请求；任务仍保持原状态。", _DANGER)
            return None
        self.scan_control_sequence = sequence
        return sequence

    def _close_scan_control_input(self) -> None:
        """关闭父端持有的控制 stdin，使终态子进程可立即退出。"""
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
            "已完成工作会写入未完成快照，当前文件会在续传后从安全边界重做；"
            "本次任务进程将结束，DAISY 下次启动时会显示续传提示。"
        )
        if len(self.run_jobs) > 1:
            detail += "队列中尚未开始的目录也会取消。"
        if not messagebox.askyesno(
                "确认保存并退出",
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
                "stop_and_resume": (
                    "停止并保留结果"
                    if self.process_task_key == "verify"
                    else "停止并保留续传"
                ),
            }
            self._append_log(
                f"已向当前工作进程发送处置：{labels[decision]}。\n",
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
            f"文件连续无进展达到 {threshold} 秒（第 {count} 次）：\n"
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
        dialog.title("文件处理无进展")
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
        stop_text = (
            "停止并保留结果"
            if self.process_task_key == "verify"
            else "停止并保留续传"
        )
        button_specs = (
                ("继续等待", "continue_waiting"),
                ("跳过并记录", "skip_and_record"),
                (stop_text, "stop_and_resume"),
        )
        for index, (button_text, decision) in enumerate(button_specs):
            ttk.Button(
                actions, text=button_text,
                style=("Stop.TButton" if decision == "stop_and_resume"
                       else "Secondary.TButton"),
                width=_STANDARD_BUTTON_WIDTH,
                command=lambda value=decision:
                self._resolve_timeout_dialog(value),
            ).pack(
                side="left",
                padx=(
                    0,
                    _STANDARD_BUTTON_GAP
                    if index < len(button_specs) - 1 else 0,
                ),
            )
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
        reason = str(payload.get("reason") or "")
        action_label = _CONTROL_ACTION_LABELS.get(action, action or "未知操作")
        reason_label = _CONTROL_REASON_LABELS.get(reason, reason or "未知原因")
        self._append_log(
            f"任务控制未被接受：{action_label}（{reason_label}）。\n",
            "warning",
        )
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
            code = str(payload.get("code") or "")
            detail = str(payload.get("detail") or "").strip()
            explanation = _CONTROL_REJECTION_LABELS.get(
                code, detail or code or "未知原因")
            self._append_log(
                f"任务控制消息被拒绝：{explanation}。\n",
                "warning",
            )
            return
        if event_name == "run_paused":
            self.scan_control_state = "paused"
            self._set_status("任务已在安全边界暂停。", _WARNING)
            self._refresh_scan_controls()
            return
        if event_name == "run_resumed":
            self.scan_control_state = "running"
            self._set_status(f"{self._queue_prefix()}任务已继续运行。")
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
            self._set_status("任务已安全停止，正在结束本任务进程。", _WARNING)
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
            self._close_timeout_dialog()
            return
        if event_name == "tool_circuit_open":
            self._close_timeout_dialog()
            tool = str(payload.get("tool") or "外部工具")
            pending = int(payload.get("not_processed") or 0)
            self._append_log(
                f"{tool} 连续故障，元数据阶段已停止；"
                f"{pending:,} 个条目保留为待续传，未批量归因给源文件。\n",
                "error",
            )
            self._set_status(
                "外部工具连续故障；未完成快照已保留，可稍后续传。",
                _DANGER,
            )
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
                if task_key in _SCAN_TASK_KEYS:
                    self._add_recovery_scan(task_key, partial)
            elif state == "stopped":
                self.stop_requested = True
                self.scan_control_state = "stopped"
            elif state == "failed_recoverable":
                partial = str(payload.get("partial") or "")
                self.scan_control_state = "failed_recoverable"
                if task_key in _SCAN_TASK_KEYS and partial:
                    self._add_recovery_scan(task_key, partial)
            elif state == "failed":
                self.scan_control_state = "failed"
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
        if event_name == "runtime_capabilities":
            self._apply_runtime_capabilities(payload)
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
        if event_name in ("progress_finish", "progress_skip", "progress_fail"):
            stage_idx = max(1, int(payload.get("stage_idx") or 1))
            stage_total = max(stage_idx, int(payload.get("stage_total") or 1))
            self.current_stage_index = stage_idx
            self.current_stage_total = stage_total
            name = self._short_progress_text(payload.get("name") or "阶段")
            if event_name == "progress_fail":
                stage_fraction = progress_fraction(
                    payload.get("done"), payload.get("total")) or 0.0
                task_fraction = (
                    stage_idx - 1 + stage_fraction / 100
                ) / stage_total
            else:
                task_fraction = stage_idx / stage_total
            self.progress_stage_bar.configure(
                mode="determinate", maximum=100,
                value=task_fraction * 100,
                style=(
                    "Danger.Horizontal.TProgressbar"
                    if event_name == "progress_fail" else
                    "Stage.Horizontal.TProgressbar"
                ),
            )
            self._update_queue_progress(task_fraction)
            if event_name == "progress_skip":
                detail = "已跳过：" + str(payload.get("reason") or "当前配置")
            else:
                detail = str(payload.get("summary") or (
                    "阶段未完成"
                    if event_name == "progress_fail" else "完成"))
                elapsed = payload.get("elapsed")
                if elapsed is not None:
                    detail += f" · 用时 {_format_duration(elapsed)}"
            self.progress_stage_label.configure(
                text=f"阶段 {stage_idx}/{stage_total} · {name}",
                fg=_DANGER if event_name == "progress_fail" else _GREEN_DARK,
            )
            self.progress_detail_label.configure(
                text=self._short_progress_text(detail),
                fg=_DANGER if event_name == "progress_fail" else _TEXT)
            self._set_work_fraction(
                stage_fraction if event_name == "progress_fail" else 100,
                style="Danger" if event_name == "progress_fail" else "Work",
            )

    def _finish_progress(self, returncode: int | None, elapsed: float) -> None:
        self._stop_work_progress()
        self_test = self.process_task_key == _PROJECT_SELF_TEST_KEY
        checking_version = (
            self.process_task_key == _DEPENDENCY_VERSION_CHECK_KEY)
        installing = self.process_task_key == _DEPENDENCY_INSTALL_KEY
        if self.save_exit_requested:
            style, colour, detail = (
                "Warning", _WARNING,
                f"进度已保存，可在下次启动后续传 · {_format_duration(elapsed)}")
            self.progress_work_bar.configure(
                style=f"{style}.Horizontal.TProgressbar")
            self.progress_percent_label.configure(text="已保存", fg=colour)
        elif returncode == 0 and not self.stop_requested:
            style, colour, detail = (
                "Success", _SUCCESS,
                (
                    f"DAISY 功能自检通过 · 总用时 {_format_duration(elapsed)}"
                    if self_test else
                    f"最新版本查询完成 · 用时 {_format_duration(elapsed)}"
                    if checking_version else
                    f"安装命令完成 · 用时 {_format_duration(elapsed)}"
                    if installing else
                    f"任务完成 · 总用时 {_format_duration(elapsed)}"
                ))
            self.progress_stage_bar.configure(value=100)
            self._set_work_fraction(100, style=style)
            self.progress_percent_label.configure(text="完成", fg=colour)
        elif (returncode == 1 and not self.stop_requested
              and not self_test and not checking_version and not installing):
            style, colour, detail = (
                "Warning", _WARNING,
                f"任务完成，但结果需要检查 · {_format_duration(elapsed)}")
            self.progress_stage_bar.configure(value=100)
            self._set_work_fraction(100, style=style)
            self.progress_percent_label.configure(text="需检查", fg=colour)
        elif self.stop_requested:
            style, colour, detail = (
                "Warning", _WARNING,
                (
                    f"最新版本查询已停止 · {_format_duration(elapsed)}"
                    if checking_version else
                    f"任务已停止 · {_format_duration(elapsed)}"
                ))
            self.progress_work_bar.configure(
                style=f"{style}.Horizontal.TProgressbar")
            self.progress_percent_label.configure(text="停止", fg=colour)
        else:
            style, colour, detail = (
                "Danger", _DANGER,
                (
                    f"最新版本查询失败 · {_format_duration(elapsed)}"
                    if checking_version else
                    f"任务失败 · {_format_duration(elapsed)}"
                ))
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
                "当前不是管理员模式。管理员权限通常能获得更完整的硬盘信息；"
                "部分 Windows 或 SMART 查询在非管理员模式下可能失败。建议先点击"
                "页面内的管理员模式按钮并按提示重新启动 DAISY。"
            )
        target_summary = root_confirmation_text(self.task.key, values)
        if target_summary:
            warnings.append(target_summary)
        full_scan_selected = (
            self.task.key == "full_scan"
            or (self.task.key == "scan"
                and values.get("scan_mode", "full") == "full")
        )
        if full_scan_selected:
            warnings.append(
                "完整扫描可能持续几小时到几天；停止时可能保留可续传的"
                "未完成快照 (.partial.sqlite)。")
            if values.get("start_mode") == "resume":
                warnings.append("续传会沿用未完成快照中记录的原扫描配置。")
            else:
                if values.get("hash_mode") == "full":
                    warnings.append("完整 SHA-256 会读取每个文件的全部内容。")
                metadata_modes = (
                    (str(values.get("metadata_storage", "complete")),)
                    if self.task.key == "full_scan" else
                    (
                        str(values.get(
                            "metadata_exiftool_mode", "complete")),
                        str(values.get(
                            "metadata_ffprobe_mode", "complete")),
                    )
                )
                if "normalized" in metadata_modes:
                    warnings.append(
                        "至少一个元数据工具选择了基础范围；仍会生成规范化字段，"
                        "但不会保留该工具的原始输出；以后不能只依靠该快照"
                        "按新规则重新解释这部分输出。")
                if not values.get("collect_file_id", True):
                    warnings.append(
                        "已关闭 NTFS-ID 采集；移动或重命名的判定证据会减少。")
        if (self.task.key == "check_hash"
                and values.get("check_scope") == "full"):
            warnings.append("全量哈希核对会读取所有有基准哈希的文件。")
        if self.task.key == "parse_db":
            inspection = self._matching_parse_inspection(
                values.get("database"))
            if inspection is not None:
                preset = str(values.get("preset") or "human-summary")
                include = (_lines(values.get("parse_modules"))
                           if preset == "custom" else ())
                try:
                    plan = dbparse.plan_parse_export(
                        inspection, preset=preset, include=include,
                        formats=_lines(values.get("formats")))
                except core.PreflightError:
                    plan = None
                if plan is not None:
                    warnings.extend(plan.privacy_notices)
        if self.task.key == "storage_collect":
            disk_numbers = _lines(values.get("disk_number"))
            disk_list = "\n".join(
                f"• PhysicalDrive{number}" for number in disk_numbers)
            warnings.extend((
                "将对以下硬盘分别运行硬盘信息登记，并分别生成独立 ZIP："
                f"\n{disk_list}\n程序会在每次采集前重新核对设备身份。",
                "详细 SMART 与 Windows 存储查询可能唤醒休眠硬盘，"
                "但不会启动自检或修改硬盘设置。",
                "生成的 ZIP 可能包含序列号、卷标、挂载路径、计算机名与"
                " BitLocker 状态，请勿未经检查公开分享。",
            ))
        if "force" in active_keys and values.get("force"):
            warnings.append(
                "已允许缺少文件名指纹的旧数据库继续处理；"
                "指纹不一致时仍会拒绝。")
        if not warnings:
            return True
        return messagebox.askyesno(
            "确认开始任务",
            "\n\n".join(warnings) + "\n\n确定继续吗？",
            icon="warning", parent=self.root,
        )

    def _begin_run_jobs(self, task_key: str, jobs: list[RunJob]) -> None:
        """锁定界面并启动同一任务的一项或多项目标。"""
        self._cancel_open_result_flash()
        if task_key == _DEPENDENCY_VERSION_CHECK_KEY:
            self.dependency_version_query_output = ""
        if task_key == "storage_list":
            self.storage_disk_choices = ()
            self.storage_disk_options = ()
            self.saved_values.setdefault("storage_collect", {}).pop(
                "disk_number", None)
        self.process_task_key = task_key
        self.stop_requested = False
        self.save_exit_requested = False
        self.scan_control_state = (
            "starting" if task_key in _CONTROL_TASK_KEYS else "idle")
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
        admin_button = getattr(self, "admin_mode_button", None)
        if admin_button is not None:
            admin_button.set_mode(
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
            f"将运行 Script\\Test 中的全部自动化测试；当前版本 {_version()}。"
            "\n\n"
            "测试不会读取 GUI 表单中的档案目录；测试夹具在工作区测试目录或"
            "系统临时目录中创建并清理，也不会访问真实硬盘。部分集成测试会调用 ExifTool、"
            "ffprobe 与 7-Zip，"
            "建议先运行「运行环境检测」。\n\n确定继续吗？",
            icon="question", parent=self.root,
        )
        if not confirmed:
            return
        self._begin_run_jobs(
            _PROJECT_SELF_TEST_KEY, [RunJob("DAISY 功能自检", {})])

    def _run_storage_inventory(self) -> None:
        """在硬盘信息登记页运行内部列盘步骤并刷新目标清单。"""
        if self.process is not None or self.worker_starting or self.run_jobs:
            return
        if (not self.is_administrator
                and not messagebox.askyesno(
                    "建议使用管理员模式",
                    "管理员权限通常能获得更完整的硬盘检测结果。建议先点击"
                    "页面内的管理员模式按钮，并按提示重新启动 DAISY。\n\n"
                    "若继续，将以非管理员模式运行，部分 Windows 存储或 SMART "
                    "信息可能无法读取。确定继续吗？",
                    icon="warning", parent=self.root)):
            return
        self._save_current_values()
        self.storage_disk_choices = ()
        self.storage_disk_options = ()
        self.saved_values.setdefault("storage_collect", {}).pop(
            "disk_number", None)
        self._build_form()
        self._begin_run_jobs(
            "storage_list", [RunJob("检测硬盘", {})])

    def _current_install_version(self, tool_name: str) -> str:
        """读取当前窗口已经完成的探测结果，不在 Tk 线程启动外部工具。"""
        if tool_name == "rawpy":
            capabilities = getattr(self, "runtime_capabilities", {})
            raw = capabilities.get(envcap.RAW_CAPABILITY_ID)
            if not isinstance(raw, dict):
                return "尚未检测"
            version = str(raw.get("version") or "").strip()
            if raw.get("state") == "available":
                return version or "版本未知"
            if version:
                return f"{version}（当前不可用）"
            return (
                "未检测到"
                if raw.get("state") == "unavailable" else "当前不可用"
            )

        detected_tools = getattr(self, "detected_tools", {})
        info = detected_tools.get(tool_name) or {}
        if info.get("verified") is True and str(
                info.get("path") or "").strip():
            version = str(info.get("version") or "").strip()
            return version or "版本未知"
        if tool_name in getattr(self, "environment_missing_names", ()):
            return "未检测到"
        return "尚未检测"

    @staticmethod
    def _install_version_from_inventory(
        tool_name: str, payload: dict[str, object],
    ) -> str:
        """只使用本次复检载荷生成安装后版本，避免误用旧会话缓存。"""
        if tool_name == "rawpy":
            capabilities = payload.get("capabilities")
            raw = (
                capabilities.get(envcap.RAW_CAPABILITY_ID)
                if isinstance(capabilities, dict) else None
            )
            if not isinstance(raw, dict):
                return "复检未返回结果"
            version = str(raw.get("version") or "").strip()
            if raw.get("state") == "available":
                return version or "版本未知"
            if version:
                return f"{version}（当前不可用）"
            return (
                "未检测到"
                if raw.get("state") == "unavailable" else "当前不可用"
            )

        tools = payload.get("tools")
        info = tools.get(tool_name) if isinstance(tools, dict) else None
        if (isinstance(info, dict)
                and info.get("verified") is True
                and str(info.get("path") or "").strip()):
            version = str(info.get("version") or "").strip()
            return version or "版本未知"
        missing = payload.get("missing")
        if isinstance(missing, list) and any(
            isinstance(item, dict) and item.get("name") == tool_name
            for item in missing
        ):
            return "未检测到"
        return "复检未返回结果"

    def _begin_install_version_report(
        self, tool_name: str, display_name: str, before_version: str,
        latest_version: str,
    ) -> None:
        self.pending_install_version_report = InstallVersionReport(
            tool_name=tool_name,
            display_name=display_name,
            before_version=before_version,
            latest_version=latest_version,
        )

    def _finish_install_version_report(
        self, *, recheck_returncode: int | None,
        after_override: str | None = None,
        update_status: bool = True,
    ) -> None:
        report = getattr(self, "pending_install_version_report", None)
        if report is None:
            return
        after_version = after_override or report.after_version
        if after_version is None:
            after_version = (
                "复检失败"
                if recheck_returncode != 0 or not report.inventory_received
                else "复检未返回结果"
            )
        if report.install_returncode != 0:
            result = "安装命令未成功；版本以复检结果为准"
            colour, tag = _DANGER, "error"
        elif after_version in ("复检失败", "复检未返回结果", "未复检"):
            result = "未能确认安装后的版本"
            colour, tag = _WARNING, "warning"
        elif after_version == "未检测到" or after_version.endswith(
                "（当前不可用）") or after_version == "当前不可用":
            result = "安装后仍不可用"
            colour, tag = _WARNING, "warning"
        elif after_version == "版本未知":
            result = "工具当前可用，但未能识别版本"
            colour, tag = _WARNING, "warning"
        elif report.before_version in ("尚未检测", "未检测到"):
            result = "安装后已检测到版本"
            colour, tag = _SUCCESS, "success"
        elif (report.before_version == "当前不可用"
                or report.before_version.endswith("（当前不可用）")):
            result = "已恢复可用"
            colour, tag = _SUCCESS, "success"
        elif report.before_version == after_version:
            result = "版本未变化，可能已是最新版本"
            colour, tag = _SUCCESS, "success"
        else:
            result = "版本已更新"
            colour, tag = _SUCCESS, "success"
        if (report.install_returncode == 0
                and recheck_returncode not in (0, None)
                and after_version not in (
                    "复检失败", "复检未返回结果", "未复检")):
            result += "；环境复检未全部通过"
            colour, tag = _WARNING, "warning"
        self._append_log(
            "\n"
            f"{report.display_name} 安装版本复检：\n"
            f"  当前版本（安装前）：{report.before_version}\n"
            f"  软件源最新版本（查询时）："
            f"{report.latest_version or '未知'}\n"
            f"  更新后版本：{after_version}\n"
            f"  结果：{result}\n",
            tag,
        )
        if update_status:
            self._set_status(
                f"{report.display_name}：{report.before_version} → "
                f"{after_version}（{result}）。",
                colour,
            )
        self.pending_install_version_report = None

    def _finish_dependency_version_query(
        self, job: RunJob, returncode: int | None, output: str,
    ) -> None:
        """版本查询结束后展示当前版本和软件源最新版本，再由用户决定是否安装。"""
        tool_name = str(job.values.get("tool_name") or "")
        display_name = str(
            job.values.get("display_name") or job.label or tool_name)
        before_version = str(
            job.values.get("before_version") or "尚未检测")
        installer_kind = str(
            job.values.get("installer_kind") or "winget")
        if returncode != 0:
            self._append_log(
                f"{display_name} 最新版本查询失败；未进入安装确认。\n",
                "error",
            )
            self._set_status(
                f"未能获取 {display_name} 的最新版本；未执行安装。",
                _DANGER,
            )
            self._set_settings_expanded(True)
            messagebox.showerror(
                "无法获取最新版本",
                f"未能从软件源获取 {display_name} 的最新版本。"
                "\n\n没有执行安装；详细输出请查看运行日志。",
                parent=self.root,
            )
            return

        latest_version = (
            parse_pip_latest_version(output)
            if installer_kind == "pip" else
            parse_winget_latest_version(output)
        )
        if not latest_version:
            self._append_log(
                f"{display_name} 版本查询命令已结束，但输出中没有可识别的"
                "最新版本；未进入安装确认。\n",
                "error",
            )
            self._set_status(
                f"无法识别 {display_name} 的最新版本；未执行安装。",
                _DANGER,
            )
            self._set_settings_expanded(True)
            messagebox.showerror(
                "无法识别最新版本",
                f"软件源返回了 {display_name} 的查询结果，但 DAISY 无法从中"
                "识别最新版本。\n\n没有执行安装；详细输出请查看运行日志。",
                parent=self.root,
            )
            return

        source_name = "Python 包索引" if installer_kind == "pip" else "WinGet"
        self._append_log(
            f"{display_name} 版本查询完成：当前检测版本 "
            f"{before_version}；{source_name} 最新版本 {latest_version}。\n",
            "meta",
        )
        same_version = (
            before_version not in (
                "尚未检测", "未检测到", "版本未知", "当前不可用")
            and not before_version.endswith("（当前不可用）")
            and before_version.casefold() == latest_version.casefold()
        )
        if same_version:
            decision = (
                "两个版本相同，通常无需更新。是否仍让包管理器执行检查？")
        elif installer_kind == "pip":
            decision = (
                "是否安装或更新到软件源显示的版本？pip 可能同时安装或更新"
                "该包所需依赖。")
        else:
            decision = (
                "程序版本与 WinGet 包版本可能采用不同口径。是否让 WinGet "
                "判断并执行安装或更新？")
        confirmed = messagebox.askyesno(
            f"是否安装或更新 {display_name}",
            f"当前检测版本：{before_version}\n"
            f"软件源最新版本：{latest_version}\n\n"
            f"{decision}\n\n"
            "确认后才会执行安装命令并修改本机软件状态；完成后将自动"
            "重新检测实际版本。",
            icon="question", parent=self.root,
        )
        if not confirmed:
            self._append_log(
                f"已取消 {display_name} 安装；未修改软件状态。\n", "meta")
            self._set_status(
                f"已取消 {display_name} 安装；软件源最新版本为 {latest_version}。")
            self._set_settings_expanded(True)
            return

        self._begin_install_version_report(
            tool_name, display_name, before_version, latest_version)
        if installer_kind == "pip":
            install_values = {
                "tool_name": tool_name,
                "installer_kind": "pip",
                "python_path": str(job.values.get("python_path") or ""),
            }
        else:
            install_values = {
                "tool_name": tool_name,
                "winget_path": str(job.values.get("winget_path") or ""),
            }
        self._begin_run_jobs(
            _DEPENDENCY_INSTALL_KEY,
            [RunJob(display_name, install_values)],
        )

    def _install_tool(self, tool_name: str) -> None:
        """先查询固定 WinGet 包的最新版本，再询问是否安装。"""
        if self.process is not None or self.run_jobs or self.worker_starting:
            return
        if tool_name not in _INSTALLABLE_TOOL_PACKAGES:
            messagebox.showinfo(
                "没有可安装项",
                "该工具不在 DAISY 的可安装列表中。",
                parent=self.root,
            )
            return
        winget = discover_winget()
        if not winget:
            messagebox.showerror(
                "WinGet 不可用",
                "未找到 winget.exe。请先从 Microsoft Store 安装或更新"
                "「应用安装程序」(App Installer)，然后重新打开 DAISY。",
                parent=self.root,
            )
            return
        display_name, _package_id = _INSTALLABLE_TOOL_PACKAGES[tool_name]
        before_version = self._current_install_version(tool_name)
        job = RunJob(
            display_name,
            {
                "tool_name": tool_name,
                "display_name": display_name,
                "installer_kind": "winget",
                "winget_path": winget,
                "before_version": before_version,
            },
        )
        self._begin_run_jobs(_DEPENDENCY_VERSION_CHECK_KEY, [job])

    def _install_python_capability(self, capability_name: str) -> None:
        """先查询固定 Python 包的最新版本，再询问是否安装。"""
        if self.process is not None or self.run_jobs or self.worker_starting:
            return
        if capability_name not in _INSTALLABLE_PYTHON_CAPABILITIES:
            messagebox.showinfo(
                "没有可安装项",
                "该功能不在 DAISY 的可安装列表中。",
                parent=self.root,
            )
            return
        display_name, _package_name = _INSTALLABLE_PYTHON_CAPABILITIES[
            capability_name]
        python_path = _console_python()
        before_version = self._current_install_version(capability_name)
        job = RunJob(
            display_name,
            {
                "tool_name": capability_name,
                "display_name": display_name,
                "installer_kind": "pip",
                "python_path": python_path,
                "before_version": before_version,
            },
        )
        self._begin_run_jobs(_DEPENDENCY_VERSION_CHECK_KEY, [job])

    def _run(self) -> None:
        if self.process is not None or self.run_jobs:
            return
        if self.task.key == _PROJECT_SELF_TEST_KEY:
            self._run_self_test()
            return
        values = self._collect_values()
        effective, _tool_sources = self._effective_values(values)
        issues = validate_values(
            self.task.key,
            effective,
            parse_inspection=(
                self._matching_parse_inspection(effective.get("database"))
                if self.task.key == "parse_db" else None
            ),
        )
        if (self.task.key == "verify"
                and bool(effective.get("raw_deep_validation"))):
            raw_available, raw_reason = raw_runtime_capability_status(
                self.runtime_capabilities)
            if not raw_available:
                issues.append(f"RAW 深度校验不可用：{raw_reason}")
        if issues:
            messagebox.showerror(
                "参数需要修正", "\n".join("• " + issue for issue in issues),
                parent=self.root,
            )
            return
        jobs = build_run_jobs(self.task.key, values)
        if not self._confirmation(effective, len(jobs)):
            return
        self.saved_values[self.task.key] = self._collect_persistable_values()
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
        if task_key in _CONTROL_TASK_KEYS:
            self.scan_control_state = "starting"
            self.scan_control_previous_state = "starting"
            self._refresh_scan_controls()
        if task_key == _PROJECT_SELF_TEST_KEY:
            effective: dict[str, object] = {}
            tool_sources: dict[str, str] = {}
            command = project_self_test_command()
            command_text = project_self_test_preview()
        elif task_key == _DEPENDENCY_VERSION_CHECK_KEY:
            effective = {}
            tool_sources = {}
            if job.values.get("installer_kind") == "pip":
                command = python_capability_latest_version_command(
                    str(job.values["tool_name"]),
                    str(job.values["python_path"]),
                )
            else:
                command = dependency_latest_version_command(
                    str(job.values["tool_name"]),
                    str(job.values["winget_path"]),
                )
            command_text = subprocess.list2cmdline(command)
        elif task_key == _DEPENDENCY_INSTALL_KEY:
            effective = {}
            tool_sources = {}
            if job.values.get("installer_kind") == "pip":
                command = python_capability_install_command(
                    str(job.values["tool_name"]),
                    str(job.values["python_path"]),
                )
            else:
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
            f"正在查询 {job.label} 最新版本"
            if task_key == _DEPENDENCY_VERSION_CHECK_KEY else
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
        if task_key == _DEPENDENCY_INSTALL_KEY:
            version_report = getattr(
                self, "pending_install_version_report", None)
            if version_report is not None:
                self._append_log(
                    f"{version_report.display_name} 当前版本（安装前）："
                    f"{version_report.before_version}\n"
                    f"{version_report.display_name} 软件源最新版本（查询时）："
                    f"{version_report.latest_version or '未知'}\n",
                    "meta",
                )
        self.worker_starting = True
        worker = threading.Thread(
            target=self._worker,
            args=(command, tool_sources, task_key in _CONTROL_TASK_KEYS),
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
                _PROJECT_SELF_TEST_KEY, _DEPENDENCY_VERSION_CHECK_KEY,
                _DEPENDENCY_INSTALL_KEY):
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
                    if self.process_task_key in _CONTROL_TASK_KEYS:
                        self.scan_control_state = "running"
                        self._refresh_scan_controls()
                        if ((self.close_after_stop or self.save_exit_requested)
                                and self.process_task_key in _SCAN_TASK_KEYS):
                            if not self._request_save_scan_progress():
                                self.close_after_stop = False
                                self.save_exit_requested = False
                        elif self.close_after_stop or self.stop_requested:
                            self.stop_requested = True
                            if self._send_scan_control("stop") is None:
                                self.stop_requested = False
                                self.close_after_stop = False
                                self.scan_control_state = "running"
                                self._refresh_scan_controls()
                        else:
                            self._set_status(
                                f"{self._queue_prefix()}运行中"
                                f" (PID {self.process.pid})…")
                    elif self.close_after_stop:
                        self._set_stop_state("disabled")
                        self._set_status("正在停止任务，随后关闭窗口…", _WARNING)
                    else:
                        self._set_stop_state("normal")
                        self._set_status(
                            f"{self._queue_prefix()}运行中"
                            f" (PID {self.process.pid})…")
                    self._refresh_mini_action()
                elif kind == "output":
                    if (self.process_task_key
                            == _DEPENDENCY_VERSION_CHECK_KEY):
                        self.dependency_version_query_output = (
                            self.dependency_version_query_output
                            + str(event[1]))[-262_144:]
                    self._append_log(event[1])
                elif kind == "gui_event":
                    self._apply_gui_event(event[1])
                elif kind == "parse_detection_done":
                    self._finish_parse_database_detection(
                        int(event[1]), str(event[2]), event[3], event[4])
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
                f"队列已保存并退出 · 已处理 {processed}/{total} · "
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
            "需检查" if failures else "完成",
            fg=colour,
        )
        self.progress_stage_label.configure(text=detail, fg=colour)
        self.progress_detail_label.configure(text=detail, fg=colour)

    def _finalize_run(self, last_elapsed: float) -> None:
        total = max(1, len(self.run_jobs))
        returncode = self.run_results[-1] if self.run_results else None
        self_test = self.process_task_key == _PROJECT_SELF_TEST_KEY
        checking_version = (
            self.process_task_key == _DEPENDENCY_VERSION_CHECK_KEY)
        installing = self.process_task_key == _DEPENDENCY_INSTALL_KEY
        detecting_storage = self.process_task_key == "storage_list"
        version_query_job = (
            self.run_jobs[self.run_job_index]
            if (checking_version and self.run_jobs
                and 0 <= self.run_job_index < len(self.run_jobs))
            else None
        )
        version_query_output = str(getattr(
            self, "dependency_version_query_output", ""))
        finishing_install_recheck = (
            self.process_task_key == "env_check"
            and getattr(self, "pending_install_version_report", None)
            is not None
        )
        pending_install_report = getattr(
            self, "pending_install_version_report", None)
        if installing and pending_install_report is not None:
            pending_install_report.install_returncode = returncode
        saved = self.save_exit_requested or any(
            outcome == "save_exit" for outcome in self.run_outcomes)
        recoverable_failed = any(
            outcome == "failed_recoverable" for outcome in self.run_outcomes)
        failed = any(outcome == "failed" for outcome in self.run_outcomes)
        stopped = self.stop_requested
        play_completion_sound = (
            self.completion_sound_enabled
            and should_play_completion_sound(
                self.run_results,
                self.run_outcomes,
                task_key=self.process_task_key,
                stopped=stopped,
                saved=saved,
            )
        )
        storage_detection_succeeded = (
            detecting_storage and returncode == 0 and not stopped)
        offer_result_directory = should_offer_result_directory(
            self.run_results,
            stopped=stopped,
            maintenance=self_test or checking_version or installing,
        ) and self.process_task_key in _RESULT_DIRECTORY_TASKS
        result_directory = (
            self._output_path() if offer_result_directory else ""
        )
        if total <= 1:
            if returncode is None:
                self._set_status(
                    (
                        "DAISY 功能自检未能启动。" if self_test else
                        "最新版本查询未能启动。" if checking_version else
                        "安装命令未能启动。" if installing else
                        "任务未能启动。"
                    ),
                    _DANGER,
                )
            elif saved:
                self._set_status(
                    "扫描进度已保存；下次启动可通过续传提示准备继续。",
                    _WARNING,
                )
            elif self.stop_requested:
                self._set_status(
                    (
                        "DAISY 功能自检已停止；请检查日志。"
                        if self_test else
                        "最新版本查询已停止；没有执行安装。"
                        if checking_version else
                        "安装已停止；请检查日志与本机软件状态。"
                        if installing else
                        "任务已停止；请检查日志与未完成产物。"
                    ),
                    _WARNING,
                )
            elif recoverable_failed:
                self._set_status(
                    "外部工具连续故障；未完成快照已保留，可通过续传提示准备继续。",
                    _DANGER,
                )
            elif failed:
                self._set_status(
                    "外部工具连续故障，任务已停止；请查看报告中的未处理范围。",
                    _DANGER,
                )
            elif returncode == 0:
                self._set_status(
                    (
                        "DAISY 功能自检通过。" if self_test else
                        "最新版本查询完成。" if checking_version else
                        "安装命令完成。" if installing else
                        "任务完成。"
                    ),
                    _SUCCESS,
                )
            elif (returncode == 1 and not self_test
                  and not checking_version and not installing):
                self._set_status("任务完成，但结果需要检查。", _WARNING)
            else:
                self._set_status(
                    (
                        "DAISY 功能自检失败；请查看日志。"
                        if self_test else
                        "最新版本查询失败；没有执行安装。"
                        if checking_version else
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
                    f"队列已保存并退出：已处理 {processed} 项，"
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

        if installing and stopped:
            self._finish_install_version_report(
                recheck_returncode=None,
                after_override="未复检",
                update_status=False,
            )
        elif finishing_install_recheck:
            self._finish_install_version_report(
                recheck_returncode=returncode)

        idle_run_state = (
            "disabled"
            if (self.task.key == _PROJECT_SELF_TEST_KEY
                and project_self_test_missing_files())
            else "normal"
        )
        self._set_stop_state("disabled")
        self.scan_control_state = "idle"
        self._set_task_navigation_state("normal")
        self.process_task_key = None
        self.stop_requested = False
        self.save_exit_requested = False
        self.run_jobs = []
        self.run_job_index = -1
        self.run_results = []
        self.run_outcomes = []
        self.run_queue_started = 0.0
        self.dependency_version_query_output = ""
        self.worker_starting = False
        self.scan_run_result = None
        self.scan_control_sequence = 0
        self.scan_control_previous_state = "idle"
        self._set_run_action_mode(False, state=idle_run_state)
        self._refresh_scan_controls()
        self._hide_current_file()
        self._close_timeout_dialog()
        restore_settings = (
            not self.close_after_stop
            and not (installing and not stopped)
            and not (
                checking_version and returncode == 0 and not stopped)
        )
        if restore_settings:
            if self.mini_mode:
                self._leave_mini_mode()
            self._set_settings_expanded(True)
        self._refresh_mini_action()
        self._refresh_tool_cache_labels()
        self._set_recovery_card_state()
        if play_completion_sound:
            self.root.after_idle(self._play_completion_sound)
        if result_directory and os.path.isdir(result_directory):
            self.root.after_idle(self._flash_open_result_button)
        if storage_detection_succeeded:
            self._restore_storage_selection_after_detection()
        if self.close_after_stop:
            self.close_after_stop = False
            self.root.after_idle(self._destroy_root)
        elif checking_version and not stopped:
            if version_query_job is None:
                self._set_status(
                    "最新版本查询缺少任务上下文；没有执行安装。",
                    _DANGER,
                )
                self._set_settings_expanded(True)
            else:
                self._finish_dependency_version_query(
                    version_query_job, returncode, version_query_output)
        elif installing and not stopped:
            refresh_windows_process_path()
            self.environment_missing_names = ()
            self.missing_installable_tools = ()
            self._refresh_tool_cache_labels()
            self._append_log(
                "\n依赖安装流程已结束，正在重新检测全部环境项目…\n",
                "meta",
            )
            self.root.after(250, self._run)
        elif (result_directory
              and getattr(
                  self, "result_directory_prompt_enabled", False)):
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
                f"\n{item}已保存并退出（退出码 {returncode}，"
                f"用时 {elapsed:.1f} 秒）。\n", "warning")
        elif outcome == "stopped" or self.stop_requested:
            self._append_log(
                f"\n{item}已停止（退出码 {returncode}，"
                f"用时 {elapsed:.1f} 秒）。\n", "warning")
        elif outcome == "failed_recoverable":
            self._append_log(
                f"\n{item}因外部工具连续故障而停止（退出码 {returncode}，"
                f"用时 {elapsed:.1f} 秒）；未完成快照已保留，可续传。\n", "error")
        elif outcome == "failed":
            self._append_log(
                f"\n{item}因外部工具连续故障而停止（退出码 {returncode}，"
                f"用时 {elapsed:.1f} 秒）；未处理范围未被误报为文件问题。\n",
                "error",
            )
        elif returncode == 0:
            self._append_log(
                f"\n{item}完成（用时 {elapsed:.1f} 秒）。\n", "success")
        elif (returncode == 1 and total <= 1 and not self_test
              and self.process_task_key not in (
                  _DEPENDENCY_VERSION_CHECK_KEY,
                  _DEPENDENCY_INSTALL_KEY,
              )):
            self._append_log(
                f"\n{item}完成，但发现差异或异常（退出码 1，用时 "
                f"{elapsed:.1f} 秒）。\n", "warning")
        else:
            self._append_log(
                f"\n{item}失败（退出码 {returncode}，"
                f"用时 {elapsed:.1f} 秒）。\n", "error")

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
        elif self.process_task_key == _DEPENDENCY_VERSION_CHECK_KEY:
            prompt = (
                "这会中断当前版本查询；不会执行安装或修改已安装软件。"
                "\n\n确定停止吗？"
            )
        elif self.process_task_key == _DEPENDENCY_INSTALL_KEY:
            prompt = (
                "这会中断当前安装子进程；已经完成安装的软件或 Python 包"
                "不会回滚，尚未开始的安装项会取消。\n\n确定停止吗？"
            )
        elif self.process_task_key == "storage_collect":
            prompt = (
                "这会中断当前只读硬盘采集，可能在硬盘档案目录留下可安全"
                "删除的未完成 ZIP (.partial.zip)；不会修改硬盘。\n\n确定停止吗？"
            )
        elif self.process_task_key == "storage_list":
            prompt = (
                "这会中断当前只读硬盘检测；不会修改硬盘。"
                "\n\n确定停止吗？"
            )
        elif self.process_task_key == "verify":
            prompt = (
                "这会在安全边界停止当前只读核验；已经完成的检查会保留在"
                "核验报告中，未开始的检查不会记录为通过。\n\n确定停止吗？"
            )
        elif self.process_task_key in _SCAN_TASK_KEYS:
            prompt = (
                "停止任务会保留未完成快照和运行证据，但下次启动不会自动显示"
                "续传提示；仍可手动选择未完成快照继续。\n\n确定停止吗？"
            )
        elif self.process_task_key == "env_check":
            prompt = (
                "这会中断当前运行环境检测；已经得到的结果仍保留在日志中。"
                "\n\n确定停止吗？"
            )
        elif self.process_task_key == "parse_db":
            prompt = (
                "这会中断当前档案数据解析；已发布的结果不会删除，未完成结果"
                "不会标记为成功。\n\n确定停止吗？"
            )
        elif self.process_task_key == "diff":
            prompt = (
                "这会中断当前档案快照对比；已发布的结果不会删除，未完成结果"
                "不会标记为成功。\n\n确定停止吗？"
            )
        else:
            prompt = (
                "这会中断当前任务；已完成的结果不会删除，未完成结果不会标记为"
                "成功。\n\n确定停止吗？"
            )
        if len(self.run_jobs) > 1:
            target = (
                "硬盘" if self.process_task_key == "storage_collect" else
                "目录" if self.process_task_key in _SCAN_TASK_KEYS else
                "项目"
            )
            prompt = (
                f"这会终止当前{target}，并取消队列中尚未开始的{target}。\n\n"
                + prompt
            )
        if not messagebox.askyesno(
            "确认停止任务", prompt,
            icon="warning", parent=self.root,
        ):
            return
        if self.process_task_key in _CONTROL_TASK_KEYS:
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
                "正在安全停止并保留已完成证据…"
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
            or bool(getattr(self, "parse_detection_active", False))
        )
        if not active:
            self._save_gui_preferences()
            self._destroy_root()
            return

        scan_active = getattr(
            self, "process_task_key", None) in _SCAN_TASK_KEYS
        control_active = getattr(
            self, "process_task_key", None) in _CONTROL_TASK_KEYS
        detail = (
            "关闭界面前会安全保存当前扫描进度、结束本任务进程并释放锁；"
            "下次启动时会显示续传提示。确定继续吗？"
            if scan_active else
            "关闭界面前会在安全边界停止当前核验并保留已完成证据；"
            "未执行项目不会记录为通过。确定继续吗？"
            if control_active else
            "关闭界面会停止当前任务，并可能留下未完成产物。确定继续吗？"
        )
        if len(self.run_jobs) > 1:
            detail = (
                ("关闭界面前会保存当前扫描进度，并取消队列中尚未启动的"
                 "目录；下次启动时会显示当前未完成快照的续传提示。确定继续吗？")
                if scan_active else
                ("关闭界面会安全停止当前核验，并取消队列中尚未启动的"
                 "项目；已完成证据仍会保留。确定继续吗？")
                if control_active else
                ("关闭界面会停止当前目录，并取消队列中尚未启动的目录；"
                 "也可能留下未完成产物。确定继续吗？")
            )
        if not messagebox.askyesno(
            "确认退出", detail,
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
            if control_active:
                previous = self.scan_control_state
                if self._send_scan_control("stop") is None:
                    self.close_after_stop = False
                    return
                self.scan_control_previous_state = previous
                self.stop_requested = True
                self.scan_control_state = "stop_requested"
                self._set_status("正在安全停止任务，随后关闭窗口…", _WARNING)
                self._refresh_scan_controls()
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
            elif control_active:
                self.stop_requested = True
                self._set_status("任务启动后将立即安全停止并关闭窗口…", _WARNING)
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
