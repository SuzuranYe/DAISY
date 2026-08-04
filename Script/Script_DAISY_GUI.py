r"""Script_DAISY_GUI：DAISY 的零依赖 Windows 图形界面。

GUI 只负责收集参数、预览命令和管理子进程；扫描、核验、对比与导出仍由
Script\Script_DAISY_MAIN.py 的既有子命令完成，避免形成第二套业务逻辑。
"""
from __future__ import annotations

import codecs
import json
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
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_BASE = os.path.dirname(_SCRIPT_DIR)
_LIB_DIR = os.path.join(_SCRIPT_DIR, "Lib")
_TEST_DIR = os.path.join(_SCRIPT_DIR, "Test")
_MAIN = os.path.join(_SCRIPT_DIR, "Script_DAISY_MAIN.py")
sys.path.insert(0, _LIB_DIR)

import Script_DAISY_Lib_01_Core as core


_DEFAULT_OUTPUT_ROOT = os.path.join(_BASE, "Output")
_DEFAULT_REPORTS_DIR = os.path.join(_DEFAULT_OUTPUT_ROOT, "Reports")
_DEFAULT_SNAPSHOTS_DIR = os.path.join(_DEFAULT_OUTPUT_ROOT, "Snapshots")
_DEFAULT_DIFFS_DIR = os.path.join(_DEFAULT_OUTPUT_ROOT, "Diffs")
_DEFAULT_STORAGE_DIR = os.path.join(_DEFAULT_OUTPUT_ROOT, "Storage")
_GUI_EVENT_PREFIX = "@@DAISY_GUI@@"
_PROJECT_SELF_TEST_KEY = "project_self_test"
_DEPENDENCY_INSTALL_KEY = "dependency_install"
_PROJECT_TEST_PATTERN = "Script_DAISY_Test_*.py"
_PROJECT_TEST_FILES = (
    "Script_DAISY_Test_Unit.py",
    "Script_DAISY_Test_No_Clobber.py",
    "Script_DAISY_Test_Tree.py",
)
_PROJECT_GITHUB_URL = "https://github.com/SuzuranYe/DAISY"
_MAX_ROOT_DIRECTORIES = 9
_ROOT_BATCH_TASKS = frozenset(("full_scan", "quick_scan"))
_ROOT_BATCH_SEPARATE = "separate"
_ROOT_BATCH_COMBINED = "combined"

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
_PROJECT_CACHE_DIR_NAMES = frozenset((
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
))
_PROJECT_CACHE_FILE_SUFFIXES = (".pyc", ".pyo")
_CACHE_SCAN_EXCLUDED_DIR_NAMES = frozenset((
    ".git", ".venv", "venv", "node_modules", "output",
))

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
    "storage_verify": (_GREEN_DARK, _GREEN_DEEP, _GREEN),
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
    path_exists=os.path.isfile,
) -> tuple[dict[str, object], dict[str, str]]:
    """按手动覆盖→本窗口缓存→运行时自动发现合并工具路径。"""
    effective = dict(values)
    sources: dict[str, str] = {}
    for name in _TASK_TOOL_NAMES.get(task_key, ()):
        field = _TOOL_FIELD_BY_NAME[name]
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
    toggle_text: str = "启用"
    section: str = "任务参数"
    advanced: bool = False
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


_SQLITE_TYPES = (
    ("SQLite 数据库", "*.sqlite"),
    ("全部文件", "*.*"),
)
_PARTIAL_TYPES = (
    ("未完成快照", "*.partial.sqlite"),
    ("SQLite 数据库", "*.sqlite"),
    ("全部文件", "*.*"),
)
_ZIP_TYPES = (
    ("存储档案 ZIP", "*.zip"),
    ("全部文件", "*.*"),
)
_EXE_TYPES = (
    ("可执行文件", "*.exe"),
    ("全部文件", "*.*"),
)

_FULL_NEW = (("start_mode", ("new",)),)
_FULL_RESUME = (("start_mode", ("resume",)),)
_FULL_INCREMENTAL = (
    ("start_mode", ("new",)),
    ("hash_mode", ("incremental",)),
)
_FULL_HASHED = (
    ("start_mode", ("new",)),
    ("hash_mode", ("incremental", "full")),
)
_FULL_POWERSHELL = (("hash_mode", ("incremental", "full")),)
_FORMAT_SAMPLE = (("check_scope", ("sample",)),)
_HASH_SAMPLE = (("check_scope", ("sample",)),)


TASKS = (
    TaskSpec(
        "env_check",
        "env-check",
        "ENV-01  环境检测",
        "环境检测",
        "检查 ExifTool、ffprobe、7-Zip、PowerShell 与 smartctl 的发现、"
        "版本和只读冒烟结果，并执行 SHA-256 自检。本页不读取档案进行性能"
        "测试，也不保存全局设置。",
        "只读检查 · 不读取档案 · 不保存设置",
        (
            FieldSpec(
                "output_dir", "环境报告目录", "--output-dir", "dir",
                _DEFAULT_REPORTS_DIR,
                help="默认指向项目内 Output\\Reports；也可选择其它完整路径。",
                section="输出",
            ),
            FieldSpec(
                "exiftool_path", "ExifTool 路径覆盖", "--exiftool-path",
                "file", help="通常留空；手动指定成功后也会更新本窗口缓存。",
                filetypes=_EXE_TYPES, section="工具路径覆盖", advanced=True,
            ),
            FieldSpec(
                "ffprobe_path", "ffprobe 路径覆盖", "--ffprobe-path", "file",
                help="通常留空；手动指定成功后也会更新本窗口缓存。",
                filetypes=_EXE_TYPES, section="工具路径覆盖", advanced=True,
            ),
            FieldSpec(
                "sevenzip_path", "7-Zip 路径覆盖", "--sevenzip-path",
                "file", help="通常留空；手动指定成功后也会更新本窗口缓存。",
                filetypes=_EXE_TYPES, section="工具路径覆盖", advanced=True,
            ),
            FieldSpec(
                "powershell_path", "PowerShell 路径覆盖",
                "--powershell-path", "file",
                help="通常留空；会依次检查 PATH 与 Windows 常规安装位置。",
                filetypes=_EXE_TYPES, section="工具路径覆盖", advanced=True,
            ),
            FieldSpec(
                "smartctl_path", "smartctl 路径覆盖", "--smartctl-path",
                "file", help="通常留空；用于 STG 物理硬盘登记与只读核验。",
                filetypes=_EXE_TYPES, section="工具路径覆盖", advanced=True,
            ),
        ),
    ),
    TaskSpec(
        _PROJECT_SELF_TEST_KEY,
        "",
        "DBS-91  数据库自检",
        "数据库自检",
        "运行 Script\\Test 中的全部自动化测试，重点验证 SQLite schema、"
        "数据库约束、快照、Diff 与关键工作流，同时覆盖 GUI 参数映射。"
        "测试夹具只写入系统临时目录，不使用表单中的档案目录，也不生成"
        "正式快照。",
        "数据库测试 · 临时夹具 · 不读取私人档案",
        (),
    ),
    TaskSpec(
        "full_scan",
        "full-scan",
        "DBS-11  完整扫描",
        "完整扫描",
        "登记文件树、元数据与哈希，默认完整 SHA-256；生成可续传、封存后"
        "不可变的单文件 SQLite 快照。适合首次建账和周期性完整复核。",
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
                section="输入", active_when=_FULL_NEW,
            ),
            FieldSpec(
                "root_batch_mode", "多目录生成方式", None, "choice",
                _ROOT_BATCH_SEPARATE,
                choices=(
                    ("分别生成：每个目录一个数据库（默认）",
                     _ROOT_BATCH_SEPARATE),
                    ("合并生成：所有目录一个数据库", _ROOT_BATCH_COMBINED),
                ),
                help="分别模式按添加顺序逐项运行，单项失败后继续下一项；"
                     "停止会终止整个队列。",
                section="输入", active_when=_FULL_NEW,
            ),
            FieldSpec(
                "resume", "partial 快照", "--resume", "file",
                required=True,
                help="必须指向 .partial.sqlite；其内部参数是本次续传的权威配置。",
                filetypes=_PARTIAL_TYPES, section="输入",
                active_when=_FULL_RESUME,
            ),
            FieldSpec(
                "output_dir", "快照目录", "--output-dir", "dir",
                _DEFAULT_SNAPSHOTS_DIR,
                help="默认指向项目内 Output\\Snapshots；也可选择其它完整路径。",
                section="输出", active_when=_FULL_NEW,
            ),
            FieldSpec(
                "metadata_storage", "元数据范围",
                "--metadata-storage", "choice", "complete",
                choices=(
                    ("全量元数据：基础字段＋原始工具输出（默认）", "complete"),
                    ("基础元数据：仅保留规范化常用字段", "normalized"),
                ),
                help="基础元数据通过 ExifTool 生成有映射类型的规范化字段，"
                     "视频和音频还通过 ffprobe 生成容器与流字段；GIF 在"
                     "基础范围只使用 ExifTool。全量元数据在此基础上，"
                     "为本地所有文件保留 ExifTool 原文，并为视频、音频"
                     "和 GIF 保留 ffprobe 原文。基础范围可显著缩小快照，"
                     "但以后无法重新解释原始输出，也无法判定 "
                     "metadata_extraction_changed。",
                section="快照内容", active_when=_FULL_NEW,
            ),
            FieldSpec(
                "collect_file_id", "NTFS File ID",
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
                section="哈希与稳定性", active_when=_FULL_NEW,
            ),
            FieldSpec(
                "previous_snapshot", "上一封存快照", "--previous-snapshot",
                "file", required=True,
                help="仅增量哈希使用；作为可复用哈希的来源。",
                filetypes=_SQLITE_TYPES, section="哈希与稳定性",
                active_when=_FULL_INCREMENTAL,
            ),
            FieldSpec(
                "verify_percent", "独立哈希抽验比例（%）",
                "--verify-sample-percent", default="1.0",
                help=(
                    "主 SHA-256 完成后，按比例抽取本次实际计算且有效的条目，"
                    "再由 PowerShell Get-FileHash 独立重算；默认 1%，至少 "
                    "100 个（不足则全验）。这不是主哈希的覆盖比例。"
                ),
                section="扫描稳定性", advanced=True,
                active_when=_FULL_HASHED,
            ),
            FieldSpec(
                "map_root", "增量根标签映射", "--map-root", "multiline",
                help="可选；每行“旧label=新label”。",
                section="增量复用", advanced=True,
                active_when=_FULL_INCREMENTAL,
            ),
            FieldSpec(
                "exiftool_path", "ExifTool 路径", "--exiftool-path", "file",
                help="留空时优先使用本窗口已验证路径，其次自动发现；填写则手动覆盖。",
                filetypes=_EXE_TYPES,
                section="工具路径覆盖", advanced=True,
            ),
            FieldSpec(
                "ffprobe_path", "ffprobe 路径", "--ffprobe-path", "file",
                help="留空时优先使用本窗口已验证路径，其次自动发现；填写则手动覆盖。",
                filetypes=_EXE_TYPES,
                section="工具路径覆盖", advanced=True,
            ),
            FieldSpec(
                "sevenzip_path", "7-Zip 路径", "--sevenzip-path", "file",
                help="留空时优先使用本窗口已验证路径，其次自动发现；填写则手动覆盖。",
                filetypes=_EXE_TYPES,
                section="工具路径覆盖", advanced=True,
            ),
            FieldSpec(
                "powershell_path", "PowerShell 路径", "--powershell-path",
                "file",
                help="独立哈希抽验使用；留空时优先继承已验证路径，其次自动发现。",
                filetypes=_EXE_TYPES, section="工具路径覆盖", advanced=True,
                active_when=_FULL_POWERSHELL,
            ),
        ),
    ),
    TaskSpec(
        "quick_scan",
        "quick-scan",
        "DBS-12  快速扫描",
        "快速扫描",
        "只登记文件树、大小、时间与文件标识，不读取文件内容，也不依赖外部"
        "工具。适合接盘后快速确认目录结构。",
        "档案只读 · 快速 · 生成快照",
        (
            FieldSpec(
                "roots", "档案根目录", "--root", "multidir", required=True,
                help="使用“添加目录”建立列表；可修改为“label=路径”，也可用 ×"
                     " 单独移除，最多 9 个。",
                section="输入",
            ),
            FieldSpec(
                "root_batch_mode", "多目录生成方式", None, "choice",
                _ROOT_BATCH_SEPARATE,
                choices=(
                    ("分别生成：每个目录一个数据库（默认）",
                     _ROOT_BATCH_SEPARATE),
                    ("合并生成：所有目录一个数据库", _ROOT_BATCH_COMBINED),
                ),
                help="分别模式按添加顺序逐项运行，单项失败后继续下一项；"
                     "停止会终止整个队列。",
                section="输入",
            ),
            FieldSpec(
                "output_dir", "快照目录", "--output-dir", "dir",
                _DEFAULT_SNAPSHOTS_DIR,
                help="默认指向项目内 Output\\Snapshots；也可选择其它完整路径。",
                section="输出",
            ),
            FieldSpec(
                "collect_file_id", "NTFS File ID", "--no-file-id",
                "choice_flag", True,
                choices=(
                    ("采集（默认）", True),
                    ("不采集（No-FID）", False),
                ),
                flag_value=False,
                help="建议采集；选择“不采集”可提高兼容性，但会降低移动／"
                     "重命名判定证据。",
                section="快照内容",
            ),
        ),
    ),
    TaskSpec(
        "check_format",
        "check-format",
        "DBS-32  文件结构核验",
        "文件结构核验",
        "依据封存快照定位当前文件，调用对应后端检查照片、视频、音频、文档和"
        "压缩包是否可读。",
        "档案只读 · 生成 CSV/Markdown 报告",
        (
            FieldSpec(
                "snapshot", "封存快照", "--snapshot", "file",
                required=True, filetypes=_SQLITE_TYPES, section="输入",
            ),
            FieldSpec(
                "root_map", "当前档案根目录", "--root", "multimapdir",
                required=True,
                help="必须指定。单根快照可直接添加当前文件夹；多根快照需逐项"
                     "填写“label=当前路径”，label 必须与快照一致。",
                section="当前档案位置",
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
                "sample_percent", "抽样比例（%）", "--sample-percent",
                default="10.0", help="必须大于 0 且不超过 100。",
                section="校验范围", active_when=_FORMAT_SAMPLE,
            ),
            FieldSpec(
                "report_dir", "报告目录", "--report-dir", "dir",
                _DEFAULT_REPORTS_DIR,
                help="默认指向项目内 Output\\Reports；也可选择其它完整路径。",
                section="输出",
            ),
            FieldSpec(
                "exiftool_path", "ExifTool 路径", "--exiftool-path", "file",
                help="留空时优先使用本窗口已验证路径，其次自动发现；填写则手动覆盖。",
                filetypes=_EXE_TYPES,
                section="工具路径覆盖", advanced=True,
            ),
            FieldSpec(
                "ffprobe_path", "ffprobe 路径", "--ffprobe-path", "file",
                help="留空时优先使用本窗口已验证路径，其次自动发现；填写则手动覆盖。",
                filetypes=_EXE_TYPES,
                section="工具路径覆盖", advanced=True,
            ),
            FieldSpec(
                "sevenzip_path", "7-Zip 路径", "--sevenzip-path", "file",
                help="留空时优先使用本窗口已验证路径，其次自动发现；填写则手动覆盖。",
                filetypes=_EXE_TYPES,
                section="工具路径覆盖", advanced=True,
            ),
            FieldSpec(
                "force", "文件名指纹缺失时降级继续", "--force", "bool",
                help="仅允许指纹缺失；指纹与实际字节不符仍会拒绝。",
                section="故障恢复", advanced=True,
            ),
        ),
    ),
    TaskSpec(
        "check_hash",
        "check-hash",
        "DBS-31  内容一致性核验",
        "内容一致性核验",
        "将封存快照与当前磁盘核对：先检查文件状态，再抽样或全量重新计算"
        " SHA-256。",
        "档案只读 · 生成 JSON 报告",
        (
            FieldSpec(
                "snapshot", "封存快照", "--snapshot", "file",
                required=True, filetypes=_SQLITE_TYPES, section="输入",
            ),
            FieldSpec(
                "root_map", "当前档案根目录", "--root", "multimapdir",
                required=True,
                help="必须指定。单根快照可直接添加当前文件夹；多根快照需逐项"
                     "填写“label=当前路径”，label 必须与快照一致。",
                section="当前档案位置",
            ),
            FieldSpec(
                "check_scope", "巡检范围", "--full", "choice_flag", "sample",
                choices=(
                    ("按比例抽样（默认）", "sample"),
                    ("全量重新计算 SHA-256", "full"),
                ),
                flag_value="full",
                help="全量模式会读取所有有基准哈希的文件。",
                section="巡检范围",
            ),
            FieldSpec(
                "sample_percent", "哈希抽样比例（%）", "--sample-percent",
                default="1.0", help="默认抽查 1% 的可哈希文件。",
                section="巡检范围", advanced=True,
                active_when=_HASH_SAMPLE,
            ),
            FieldSpec(
                "powershell_path", "PowerShell 路径", "--powershell-path",
                "file",
                help="留空时优先继承 11／31 已验证路径，其次自动发现；填写则手动覆盖。",
                filetypes=_EXE_TYPES, section="工具路径覆盖", advanced=True,
            ),
            FieldSpec(
                "force", "文件名指纹缺失时降级继续", "--force", "bool",
                help="仅允许指纹缺失；指纹与实际字节不符仍会拒绝。",
                section="故障恢复", advanced=True,
            ),
            FieldSpec(
                "report", "报告 JSON 路径", "--report", "save",
                help="可选；留空时写入 Output/Reports。",
                filetypes=(("JSON 报告", "*.json"), ("全部文件", "*.*")),
                section="输出",
            ),
        ),
    ),
    TaskSpec(
        "diff",
        "diff",
        "DBS-21  快照变更分析",
        "快照变更分析",
        "以旧快照为基准，与新快照进行 11 状态分类和证据等级判定，生成权威"
        " Diff 数据库。",
        "输入只读 · 生成 Diff 数据库",
        (
            FieldSpec(
                "old", "旧（基准）快照", "--old", "file",
                required=True, filetypes=_SQLITE_TYPES, section="输入",
            ),
            FieldSpec(
                "new", "新快照", "--new", "file",
                required=True, filetypes=_SQLITE_TYPES, section="输入",
            ),
            FieldSpec(
                "output_dir", "Diff 目录", "--output-dir", "dir",
                _DEFAULT_DIFFS_DIR,
                help="默认指向项目内 Output\\Diffs；也可选择其它完整路径。",
                section="输出",
            ),
            FieldSpec(
                "map_root", "根标签映射", "--map-root", "multiline",
                help="可选；每行“旧label=新label”。单根异名通常可自动配对。",
                section="根标签配对", advanced=True,
            ),
            FieldSpec(
                "force", "文件名指纹缺失时降级继续", "--force", "bool",
                help="降级结果会生成同目录 Issues.md；指纹不符仍会拒绝。",
                section="故障恢复", advanced=True,
            ),
        ),
    ),
    TaskSpec(
        "export_report",
        "export-report",
        "DBS-41  导出报告",
        "导出报告",
        "从封存快照或 Diff 数据库生成 CSV 与 Markdown 报告。输入数据库保持"
        "只读，报告可随时删除并重新生成。",
        "输入只读 · 生成 CSV/Markdown",
        (
            FieldSpec(
                "source_type", "输入类型", None, "choice", "snapshot",
                choices=(
                    ("封存快照（默认）", "snapshot"),
                    ("Diff 数据库", "diff"),
                ),
                section="输入",
            ),
            FieldSpec(
                "source_path", "输入数据库", None, "file",
                required=True, filetypes=_SQLITE_TYPES, section="输入",
            ),
            FieldSpec(
                "output_dir", "报告目录", "--output-dir", "dir",
                _DEFAULT_REPORTS_DIR,
                help="默认指向项目内 Output\\Reports；也可选择其它完整路径。",
                section="输出",
            ),
        ),
    ),
    TaskSpec(
        "storage_list",
        "storage-list",
        "STG-11  物理硬盘清单",
        "物理硬盘清单",
        "联合 Windows 存储接口与 smartctl 列出物理硬盘、分区卷标和设备"
        "关联。请先运行本项，再按 PhysicalDrive 编号执行信息登记。",
        "物理盘只读 · 不生成产物 · 可能唤醒硬盘",
        (
            FieldSpec(
                "smartctl_path", "smartctl 路径", "--smartctl-path", "file",
                help="留空时优先使用本窗口已验证路径，其次自动发现。",
                filetypes=_EXE_TYPES,
                section="工具路径覆盖", advanced=True,
            ),
            FieldSpec(
                "powershell_path", "PowerShell 路径", "--powershell-path",
                "file", help="留空时优先使用本窗口已验证路径，其次自动发现。",
                filetypes=_EXE_TYPES,
                section="工具路径覆盖", advanced=True,
            ),
        ),
    ),
    TaskSpec(
        "storage_collect",
        "storage-collect",
        "STG-12  硬盘信息登记",
        "硬盘信息登记",
        "重新确认指定物理盘身份，只读采集完整 Windows 存储资料和 smartctl"
        " 原始证据，并生成带文件名指纹的独立 ZIP 档案。",
        "物理盘只读 · 可能唤醒硬盘 · 生成 ZIP",
        (
            FieldSpec(
                "disk_number", "物理硬盘编号", "--disk-number",
                required=True,
                help="填写 STG-11 所列 PhysicalDrive 后面的非负整数。",
                section="采集目标",
            ),
            FieldSpec(
                "output_dir", "存储档案目录", "--output-dir", "dir",
                _DEFAULT_STORAGE_DIR,
                help="默认指向项目内 Output\\Storage；每块硬盘生成独立 ZIP。",
                section="输出",
            ),
            FieldSpec(
                "summary_txt", "同时输出简化报告", "--summary-txt", "bool",
                False, toggle_text="生成 ZIP 外部 TXT",
                help="默认关闭；完整结构化资料始终保存在 ZIP 的 JSON 成员中。",
                section="输出",
            ),
            FieldSpec(
                "smartctl_path", "smartctl 路径", "--smartctl-path", "file",
                help="留空时优先使用本窗口已验证路径，其次自动发现。",
                filetypes=_EXE_TYPES,
                section="工具路径覆盖", advanced=True,
            ),
            FieldSpec(
                "powershell_path", "PowerShell 路径", "--powershell-path",
                "file", help="留空时优先使用本窗口已验证路径，其次自动发现。",
                filetypes=_EXE_TYPES,
                section="工具路径覆盖", advanced=True,
            ),
        ),
    ),
    TaskSpec(
        "storage_verify",
        "storage-verify",
        "STG-21  硬盘归档核验",
        "硬盘归档核验",
        "核验硬盘信息 ZIP 的文件名 SHA-256 指纹、存储归档 schema、固定"
        "成员清单、Manifest 声明与 ZIP CRC，不读取真实硬盘。",
        "归档只读 · 不访问硬盘 · 不生成产物",
        (
            FieldSpec(
                "archive", "硬盘信息档案", None, "file", required=True,
                filetypes=_ZIP_TYPES, section="输入",
            ),
        ),
    ),
)

TASK_BY_KEY = {task.key: task for task in TASKS}
_TASK_MENU_SECTIONS = (
    (
        "环境",
        ("env_check",),
    ),
    (
        "数据库",
        (
            "full_scan", "quick_scan", "diff",
            "check_hash", "check_format", "export_report",
            _PROJECT_SELF_TEST_KEY,
        ),
    ),
    ("硬盘", ("storage_list", "storage_collect", "storage_verify")),
)
_TASK_MENU_SECTION_COLOURS = {
    "环境": ("Env", _GREEN, _GREEN_DEEP, _GREEN_SOFT),
    "数据库": ("Database", _AMBER, _AMBER_DEEP, _AMBER_SOFT),
    "硬盘": ("Storage", _RED, _RED_DEEP, _RED_SOFT),
}
_TASK_MENU_SEPARATOR_AFTER = frozenset((
    "env_check", "quick_scan", "diff", "check_format", "export_report",
    "storage_list", "storage_collect",
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
    ("环境", "ENV", ("env_check",)),
    ("数据库", "DBS", _TASK_MENU_SECTIONS[1][1]),
    ("硬盘", "STG", _TASK_MENU_SECTIONS[2][1]),
)
_TASK_TOOLBAR_LABELS = {
    "env_check": "运行环境检测",
    "full_scan": "完整档案扫描",
    "quick_scan": "快速档案扫描",
    "diff": "快照变更分析",
    "check_hash": "内容哈希核验",
    "check_format": "文件结构核验",
    "export_report": "结果报告导出",
    _PROJECT_SELF_TEST_KEY: "数据库自校验",
    "storage_list": "物理硬盘清单",
    "storage_collect": "硬盘信息登记",
    "storage_verify": "硬盘归档核验",
}
_TASK_TOOLBAR_BUTTON_WIDTH = 12
_TASK_TOOLBAR_BUTTON_PADDING = (12, 7)
_TASK_TOOLBAR_STYLE_PREFIX = "Env"
_TASK_TOOLBAR_LABEL_COLOUR = _GREEN_DEEP
_UNIFIED_ACTION_BACKGROUND = _GREEN_DARK
_UNIFIED_ACTION_FOREGROUND = "white"
_RUN_BUTTON_TEXT = "开始任务"
_COLLAPSED_PANEL_TITLE_FONT = ("Microsoft YaHei UI", 9, "bold")
_PANEL_HEADER_PADX = 14
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
    """把 GUI 参数拆成实际子进程任务；分别模式是一根一任务。"""
    task = TASK_BY_KEY[task_key]
    merged = _task_values(task, values)
    if task_key not in _ROOT_BATCH_TASKS:
        return [RunJob(task.title, merged)]
    if (task_key == "full_scan"
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
    if task_key == "export_report":
        source_type = str(values.get("source_type") or "snapshot")
        source_path = str(values.get("source_path") or "").strip()
        if source_path:
            args += ["--" + source_type, source_path]
        output_dir = str(values.get("output_dir") or "").strip()
        if output_dir:
            args += ["--output-dir", _absolute(output_dir)]
        return args
    if task_key == "storage_verify":
        archive = str(values.get("archive") or "").strip()
        if archive:
            args.append(_absolute(archive))
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
            if text:
                args += [spec.flag, text]
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

    if task_key == "full_scan":
        resume = str(values.get("resume") or "").strip()
        if (values.get("start_mode") == "resume" and resume
                and not resume.lower().endswith(".partial.sqlite")):
            issues.append("续传文件必须以 .partial.sqlite 结尾。")

    numeric_rules = {
        ("full_scan", "verify_percent"): (0.0, 100.0, True, False),
        ("check_format", "sample_percent"): (0.0, 100.0, False, False),
        ("check_hash", "sample_percent"): (0.0, 100.0, False, False),
        ("storage_collect", "disk_number"): (0.0, None, True, True),
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
        "powershell_path",
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
        except tk.TclError:
            return
        window = tk.Toplevel(self.widget)
        self._window = window
        window.wm_overrideredirect(True)
        try:
            window.attributes("-topmost", True)
        except tk.TclError:
            pass
        label = tk.Label(
            window, text=self.text, bg=_TEXT, fg="white",
            font=("Microsoft YaHei UI", 9), justify="left",
            relief="solid", bd=1, padx=9, pady=6, wraplength=360,
        )
        label.pack()
        window.update_idletasks()
        width = window.winfo_reqwidth()
        height = window.winfo_reqheight()
        screen_width = window.winfo_screenwidth()
        screen_height = window.winfo_screenheight()
        x = min(pointer_x + 14, max(0, screen_width - width - 8))
        y = min(pointer_y + 18, max(0, screen_height - height - 8))
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
        self.rows.grid(row=1, column=0, sticky="ew", padx=7, pady=(3, 7))
        self.rows.grid_columnconfigure(0, weight=1)

        footer = tk.Frame(self, bg=_SURFACE)
        footer.grid(row=0, column=0, sticky="ew", padx=7, pady=(7, 3))
        footer.grid_columnconfigure(1, weight=1)
        self.add_button = ttk.Button(
            footer, text="添加目录", style="Browse.TButton",
            command=self.add_directory,
        )
        self.add_button.grid(row=0, column=0, sticky="w")
        attach_tooltip(
            self.add_button,
            f"选择并加入一个{self.title}；最多可添加 {self.max_items} 项。",
        )
        self.count_label = tk.Label(
            footer, bg=_SURFACE, fg=_MUTED,
            font=("Microsoft YaHei UI", 8), anchor="e",
        )
        self.count_label.grid(row=0, column=1, sticky="e")
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
            ).grid(row=0, column=0, sticky="ew", padx=7, pady=7)
        for index, item in enumerate(self._items):
            row = tk.Frame(self.rows, bg=_SURFACE)
            row.grid(row=index, column=0, sticky="ew", pady=(0, 4))
            row.grid_columnconfigure(1, weight=1)
            tk.Label(
                row, text=f"{index + 1}", width=2,
                bg=_GREEN_SOFT, fg=_GREEN_DEEP,
                font=("Segoe UI", 8, "bold"),
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


def _console_python() -> str:
    executable = os.path.abspath(sys.executable)
    if os.path.basename(executable).lower() == "pythonw.exe":
        candidate = os.path.join(os.path.dirname(executable), "python.exe")
        if os.path.isfile(candidate):
            return candidate
    return executable


def project_self_test_missing_files() -> list[str]:
    """返回数据库自检缺少的正式测试文件名，不读取任何档案目录。"""
    return [
        name for name in _PROJECT_TEST_FILES
        if not os.path.isfile(os.path.join(_TEST_DIR, name))
    ]


def project_self_test_command(python_executable: str | None = None) -> list[str]:
    """返回 GUI 数据库自检实际使用的 unittest discovery 命令。"""
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


def window_size_for_screen(screen_width: int,
                           screen_height: int) -> tuple[int, int]:
    """按当前屏幕留出边缘和任务栏空间，返回 Tk 窗口客户区尺寸。"""
    width = min(1920, max(820, screen_width - 80))
    height = min(1080, max(640, screen_height - 60))
    width = min(width, max(640, screen_width - 20))
    height = min(height, max(480, screen_height - 50))
    return width, height


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


def _install_blank_window_icon(root: tk.Misc) -> object | None:
    """在 Windows 标题栏安装透明 HICON，不影响系统窗口控制。"""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        create_icon = user32.CreateIcon
        create_icon.argtypes = (
            wintypes.HINSTANCE, ctypes.c_int, ctypes.c_int,
            wintypes.BYTE, wintypes.BYTE,
            ctypes.POINTER(wintypes.BYTE),
            ctypes.POINTER(wintypes.BYTE),
        )
        create_icon.restype = wintypes.HICON
        send_message = user32.SendMessageW
        send_message.argtypes = (
            wintypes.HWND, wintypes.UINT,
            wintypes.WPARAM, wintypes.LPARAM,
        )
        send_message.restype = ctypes.c_ssize_t
        get_parent = user32.GetParent
        get_parent.argtypes = (wintypes.HWND,)
        get_parent.restype = wintypes.HWND
        set_class_icon = user32.SetClassLongPtrW
        set_class_icon.argtypes = (
            wintypes.HWND, ctypes.c_int, ctypes.c_void_p,
        )
        set_class_icon.restype = ctypes.c_void_p

        and_mask = (wintypes.BYTE * 2)(0xFF, 0xFF)
        xor_mask = (wintypes.BYTE * 2)(0x00, 0x00)
        icon = create_icon(None, 1, 1, 1, 1, and_mask, xor_mask)
        if not icon:
            return None
        widget_handle = wintypes.HWND(root.winfo_id())
        window_handle = get_parent(widget_handle) or widget_handle
        set_class_icon(window_handle, -14, icon)
        set_class_icon(window_handle, -34, icon)
        wm_seticon = 0x0080
        send_message(window_handle, wm_seticon, 0, icon)
        send_message(window_handle, wm_seticon, 1, icon)
        return icon
    except (AttributeError, OSError, TypeError, ValueError):
        return None


class DaisyApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.task = TASKS[0]
        self.values: dict[
            str, tk.Variable | tk.Text | DirectoryListEditor] = {}
        self.saved_values: dict[str, dict[str, object]] = {}
        self.advanced_visible: dict[str, bool] = {}
        self.task_menu_entries: dict[str, tuple[tk.Menu, int]] = {}
        self.task_toolbar_buttons: dict[str, ttk.Button] = {}
        self._task_toolbar_layout_ready = False
        self.detected_tools: dict[str, dict[str, object]] = {}
        self.environment_missing_names: tuple[str, ...] = ()
        self.missing_installable_tools: tuple[str, ...] = ()
        self._work_progress_indeterminate = False
        self.current_stage_index = 0
        self.current_stage_total = 0
        self.mini_mode = False
        self.task_toolbar_expanded = True
        self.settings_expanded = True
        self.progress_expanded = True
        self.log_expanded = True
        self.command_preview_expanded = False
        self._normal_geometry = ""
        self._normal_window_state = "normal"
        self.process: subprocess.Popen[bytes] | None = None
        self.process_started = 0.0
        self.process_task_key: str | None = None
        self.run_jobs: list[RunJob] = []
        self.run_job_index = -1
        self.run_results: list[int | None] = []
        self.run_queue_started = 0.0
        self.worker_starting = False
        self.close_after_stop = False
        self.stop_requested = False
        self.events: queue.Queue[tuple] = queue.Queue()
        self._configure_window()
        self._configure_styles()
        self._build_shell()
        self._build_menu()
        self._select_task(self.task.key, save_current=False)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(80, self._poll_events)

    def _configure_window(self) -> None:
        self.root.title(project_window_title())
        self.window_icon_handle: object | None = None
        self.root.after(100, self._apply_blank_window_icon)
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        width, height = window_size_for_screen(screen_width, screen_height)
        x = max(0, (screen_width - width) // 2)
        y = max(0, (screen_height - height) // 2)
        self.compact_layout = width < 1080 or height < 700
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.normal_min_size = (min(760, width), min(680, height))
        self.root.minsize(*self.normal_min_size)
        self.root.configure(bg=_BG)
        self.root.option_add("*Font", ("Microsoft YaHei UI", 10))

    def _apply_blank_window_icon(self) -> None:
        self.window_icon_handle = _install_blank_window_icon(self.root)

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
            background=_GREEN_SOFT,
            font=("Microsoft YaHei UI", 9, "bold"), padding=(9, 4),
        )
        style.configure(
            "TEntry", fieldbackground=_FIELD, foreground=_TEXT,
            bordercolor=_BORDER, lightcolor=_BORDER, darkcolor=_BORDER,
            padding=7,
        )
        style.configure(
            "TCombobox", fieldbackground=_FIELD, background=_FIELD,
            foreground=_TEXT,
            bordercolor=_BORDER, lightcolor=_BORDER, darkcolor=_BORDER,
            padding=6,
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", _FIELD)],
            selectbackground=[("readonly", _FIELD)],
            selectforeground=[("readonly", _TEXT)],
        )
        style.configure(
            "TCheckbutton", background=_SURFACE, foreground=_TEXT,
            font=("Microsoft YaHei UI", 9),
        )
        style.map(
            "TCheckbutton",
            background=[("active", _SURFACE)],
            foreground=[("disabled", _MUTED)],
        )
        style.configure(
            "Browse.TButton", background=_CONTROL, foreground=_TEXT,
            bordercolor=_BORDER, lightcolor=_BORDER, darkcolor=_BORDER,
            padding=(10, 7), font=("Microsoft YaHei UI", 9),
        )
        style.map(
            "Browse.TButton", background=[("active", _CONTROL_HOVER)])
        style.configure(
            "Remove.TButton", background=_DANGER_SOFT, foreground=_DANGER,
            bordercolor=_DANGER_BORDER, padding=(6, 5),
            font=("Segoe UI", 9, "bold"),
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
        menu = tk.Menu(self.root, **base_menu_options)

        file_menu = tk.Menu(menu, **base_menu_options)
        file_menu.add_command(
            label="打开项目目录", command=self._open_project_directory)
        file_menu.add_command(
            label="打开当前结果目录", command=self._open_output)
        file_menu.add_separator()
        file_menu.add_command(label="退出 DAISY", command=self._on_close)
        menu.add_cascade(label="文件", menu=file_menu)

        self.task_menu_var = tk.StringVar(value=self.task.key)
        self.task_menus: dict[str, tk.Menu] = {}
        for section_label, task_keys in _TASK_MENU_SECTIONS:
            task_menu = tk.Menu(
                menu,
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
                    label=task.nav,
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
            menu.add_cascade(label=section_label, menu=task_menu)

        self.task_toolbar_visible_var = tk.BooleanVar(value=True)
        self.settings_visible_var = tk.BooleanVar(value=True)
        self.progress_visible_var = tk.BooleanVar(value=True)
        self.log_visible_var = tk.BooleanVar(value=True)
        self.command_preview_visible_var = tk.BooleanVar(value=False)
        view_menu = tk.Menu(menu, **base_menu_options)
        view_menu.add_checkbutton(
            label="显示功能模块",
            variable=self.task_toolbar_visible_var,
            command=lambda: self._set_task_toolbar_expanded(
                self.task_toolbar_visible_var.get()),
        )
        view_menu.add_separator()
        view_menu.add_checkbutton(
            label="显示任务设置", variable=self.settings_visible_var,
            command=lambda: self._set_settings_expanded(
                self.settings_visible_var.get()),
        )
        view_menu.add_checkbutton(
            label="显示运行进度", variable=self.progress_visible_var,
            command=lambda: self._set_progress_expanded(
                self.progress_visible_var.get()),
        )
        view_menu.add_checkbutton(
            label="显示运行日志", variable=self.log_visible_var,
            command=lambda: self._set_log_expanded(
                self.log_visible_var.get()),
        )
        view_menu.add_checkbutton(
            label="显示命令预览", variable=self.command_preview_visible_var,
            command=lambda: self._set_command_preview_expanded(
                self.command_preview_visible_var.get()),
        )
        menu.add_cascade(label="视图", menu=view_menu)

        help_menu = tk.Menu(menu, **base_menu_options)
        help_menu.add_command(label="关于 DAISY", command=self._show_about)
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
            pady=(6, 4),
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

        body = tk.Frame(panel, bg=_SURFACE)
        self.task_toolbar_body = body
        body.pack(
            fill="x", padx=self.task_toolbar_horizontal_pad,
            pady=(0, 8),
        )
        self.task_toolbar_section_labels: dict[str, tk.Label] = {}
        for section_label, short_label, _task_keys in _TASK_TOOLBAR_ROWS:
            self.task_toolbar_section_labels[section_label] = tk.Label(
                body, text=short_label, bg=_SURFACE,
                fg=_TASK_TOOLBAR_LABEL_COLOUR,
                font=("Microsoft YaHei UI", 9, "bold"),
                width=4, anchor="w",
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
            attach_tooltip(
                button,
                f"{task.nav}：切换到“{task.title}”页面；运行时功能模块会暂时锁定。",
            )
        self.root.after_idle(self._layout_task_toolbar)

    def _build_shell(self) -> None:
        content_pad = 12 if self.compact_layout else 18
        self.content_pad = content_pad

        colour_strip = tk.Frame(self.root, bg=_BG, height=4)
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

        self.task_card = tk.Frame(
            content, bg=_SURFACE, highlightbackground=_BORDER,
            highlightthickness=1,
        )
        self.task_card.grid(row=0, column=0, sticky="nsew")

        title_row = tk.Frame(self.task_card, bg=_SURFACE)
        self.settings_title_row = title_row
        title_row.pack(fill="x", padx=22, pady=(14, 10))
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

        self.settings_body = tk.Frame(self.task_card, bg=_SURFACE)
        self.settings_body.pack(fill="both", expand=True)
        self.desc_label = tk.Label(
            self.settings_body, bg=_SURFACE, fg=_GREEN_DEEP,
            font=("Microsoft YaHei UI", 9), anchor="w", justify="left",
            wraplength=820,
        )
        self.desc_label.pack(fill="x", padx=22, pady=(0, 12))
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
        self.form_scroll.pack(side="right", fill="y")
        self.form_canvas.pack(side="left", fill="both", expand=True)
        self.form_inner = tk.Frame(self.form_canvas, bg=_SURFACE)
        self.form_window = self.form_canvas.create_window(
            (0, 0), window=self.form_inner, anchor="nw",
        )
        self.form_inner.bind(
            "<Configure>",
            lambda _e: self.form_canvas.configure(
                scrollregion=self.form_canvas.bbox("all")),
        )
        self.form_canvas.bind(
            "<Configure>",
            lambda e: self.form_canvas.itemconfigure(
                self.form_window, width=e.width),
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
        progress_panel.grid(row=1, column=0, sticky="ew", pady=(10, 0))
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
        self.progress_toggle_button = ttk.Button(
            progress_header, text="收起进度", style="Mini.TButton",
            command=self._toggle_progress_panel,
        )
        self.progress_toggle_button.pack(side="right")
        attach_tooltip(
            self.progress_toggle_button,
            "展开或收起队列、任务阶段与本阶段进度。",
        )
        self.mini_mode_button = ttk.Button(
            progress_header, text="小窗运行", style="Mini.TButton",
            command=self._toggle_mini_mode, state="disabled",
        )
        self.mini_mode_button.pack(side="right", padx=(0, 6))
        attach_tooltip(
            self.mini_mode_button,
            "任务运行时收起其它区域，只保留进度和停止控制。",
        )
        self.mini_stop_button = ttk.Button(
            progress_header, text="停止", style="MiniStop.TButton",
            command=self._stop, state="disabled",
        )
        attach_tooltip(
            self.mini_stop_button,
            "请求停止当前任务；多项队列中尚未开始的项目也会取消。",
        )

        progress_body = tk.Frame(progress_inner, bg=_SURFACE)
        self.progress_body = progress_body
        progress_body.pack(fill="x", pady=(6, 0))
        progress_body.grid_columnconfigure(1, weight=1)

        self.queue_title_label = tk.Label(
            progress_body, text="任务队列", bg=_SURFACE, fg=_GREEN_DEEP,
            font=("Microsoft YaHei UI", 8, "bold"), anchor="w",
        )
        self.queue_title_label.grid(
            row=0, column=0, sticky="w", padx=(0, 10))
        self.queue_detail_label = tk.Label(
            progress_body, text="等待队列", bg=_SURFACE, fg=_MUTED,
            font=("Microsoft YaHei UI", 8), anchor="w",
        )
        self.queue_detail_label.grid(row=0, column=1, sticky="ew")
        self.queue_percent_label = tk.Label(
            progress_body, text="0%", bg=_SURFACE, fg=_GREEN_DEEP,
            font=("Segoe UI", 8, "bold"), anchor="e",
        )
        self.queue_percent_label.grid(row=0, column=2, sticky="e")
        self.queue_progress_bar = ttk.Progressbar(
            progress_body, mode="determinate", maximum=100, value=0,
            style="Queue.Horizontal.TProgressbar",
        )
        self.queue_progress_bar.grid(
            row=1, column=0, columnspan=3, sticky="ew", pady=(4, 7))

        tk.Label(
            progress_body, text="任务阶段", bg=_SURFACE, fg=_GREEN_DARK,
            font=("Microsoft YaHei UI", 8, "bold"), anchor="w",
        ).grid(row=2, column=0, sticky="w", padx=(0, 10))
        self.progress_stage_label = tk.Label(
            progress_body, text="等待开始", bg=_SURFACE, fg=_MUTED,
            font=("Microsoft YaHei UI", 8), anchor="w",
        )
        self.progress_stage_label.grid(
            row=2, column=1, columnspan=2, sticky="ew")
        self.progress_stage_bar = ttk.Progressbar(
            progress_body, mode="determinate", maximum=100, value=0,
            style="Stage.Horizontal.TProgressbar",
        )
        self.progress_stage_bar.grid(
            row=3, column=0, columnspan=3, sticky="ew", pady=(4, 6))

        tk.Label(
            progress_body, text="本阶段", bg=_SURFACE, fg=_GREEN_DARK,
            font=("Microsoft YaHei UI", 8, "bold"), anchor="w",
        ).grid(row=4, column=0, sticky="w", padx=(0, 10))
        self.progress_detail_label = tk.Label(
            progress_body, text="尚未运行", bg=_SURFACE, fg=_MUTED,
            font=("Microsoft YaHei UI", 8), anchor="w",
        )
        self.progress_detail_label.grid(row=4, column=1, sticky="ew")
        self.progress_percent_label = tk.Label(
            progress_body, text="0%", bg=_SURFACE, fg=_GREEN_DARK,
            font=("Segoe UI", 8, "bold"), anchor="e",
        )
        self.progress_percent_label.grid(row=4, column=2, sticky="e")
        self.progress_work_bar = ttk.Progressbar(
            progress_body, mode="determinate", maximum=100, value=0,
            style="Work.Horizontal.TProgressbar",
        )
        self.progress_work_bar.grid(
            row=5, column=0, columnspan=3, sticky="ew", pady=(4, 5))

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
        self.log_toggle_button = ttk.Button(
            log_header, text="收起日志", style="Mini.TButton",
            command=self._toggle_log_panel,
        )
        self.log_toggle_button.pack(
            side="right", padx=_PANEL_HEADER_PADX, pady=5)
        attach_tooltip(
            self.log_toggle_button,
            "展开或收起运行日志；已有日志内容不会被清除。",
        )
        log_body = tk.Frame(
            log_panel, bg=_LOG_BG,
            height=100 if self.compact_layout else 120,
        )
        self.log_body = log_body
        log_body.pack(fill="x")
        log_body.pack_propagate(False)
        self.log = tk.Text(
            log_body, bg=_LOG_BG, fg=_LOG_TEXT, insertbackground=_TEXT,
            selectbackground=_LOG_SELECT, relief="flat", bd=0,
            font=("Consolas", 9), wrap="word", padx=13, pady=10,
            state="disabled",
        )
        log_scroll = ttk.Scrollbar(
            log_body, orient="vertical", command=self.log.yview,
            style="Daisy.Vertical.TScrollbar",
        )
        self.log.configure(yscrollcommand=log_scroll.set)
        log_scroll.pack(side="right", fill="y")
        self.log.pack(fill="both", expand=True)
        self.log.tag_configure("meta", foreground=_GREEN_DEEP)
        self.log.tag_configure("success", foreground=_SUCCESS)
        self.log.tag_configure("warning", foreground=_WARNING)
        self.log.tag_configure("error", foreground=_DANGER)

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
            preview_row, text="复制", style="Browse.TButton",
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
        self.clear_log_button = ttk.Button(
            utility_action_area, text="清空日志", style="Secondary.TButton",
            command=self._clear_log,
        )
        self.clear_cache_button = ttk.Button(
            utility_action_area, text="清理缓存", style="Secondary.TButton",
            command=self._clear_tool_cache, state="disabled",
        )
        self.install_tool_buttons: dict[str, ttk.Button] = {}
        for tool_name, (display_name, _package_id) in (
                _INSTALLABLE_TOOL_PACKAGES.items()):
            button = ttk.Button(
                utility_action_area,
                text=f"下载并安装 {display_name}",
                style="Secondary.TButton",
                command=lambda name=tool_name:
                self._install_missing_tool(name),
            )
            self.install_tool_buttons[tool_name] = button
        self.utility_buttons = (
            self.open_output_button,
            self.clear_log_button,
            self.clear_cache_button,
            *self.install_tool_buttons.values(),
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
        self.run_button = ttk.Button(
            execution_action_area, text=_RUN_BUTTON_TEXT,
            style="Primary.TButton",
            command=self._run,
        )
        self.execution_buttons = (self.stop_button, self.run_button)
        for button, tooltip in (
            (self.run_button,
             "校验当前页面后开始执行对应任务。"),
            (self.stop_button,
             "请求停止当前任务；多项队列中尚未开始的项目也会取消。"),
            (self.clear_cache_button,
             "清除项目内可重建缓存和本窗口工具路径缓存，不触碰正式产物。"),
            (self.clear_log_button,
             "清空当前窗口的运行日志。"),
            (self.open_output_button,
             "在资源管理器中打开当前任务对应的结果目录。"),
        ):
            attach_tooltip(button, tooltip)
        for tool_name, button in self.install_tool_buttons.items():
            display_name = _INSTALLABLE_TOOL_PACKAGES[tool_name][0]
            attach_tooltip(
                button,
                f"仅通过 WinGet 下载并安装 {display_name}；不会连带安装"
                "其它缺失工具。",
            )
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
                padx=(0, 12), pady=(4 if row_index else 0, 0),
            )
            column = 1
            for task_key in task_keys:
                self.task_toolbar_buttons[task_key].grid(
                    row=row_index, column=column, sticky="w",
                    padx=(0, 6), pady=(4 if row_index else 0, 0),
                )
                column += 1
        self._task_toolbar_layout_ready = True
        self._sync_task_toolbar_minimum_width()

    def _sync_task_toolbar_minimum_width(self) -> None:
        """普通窗口不能缩得比完整功能模块区更窄。"""
        self.root.update_idletasks()
        toolbar_width = self.task_toolbar_panel.winfo_reqwidth()
        base_width, base_height = self.normal_min_size
        self.normal_min_size = (max(base_width, toolbar_width), base_height)
        if not self.mini_mode:
            self.root.minsize(*self._normal_minimum_size())

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

    def _toggle_task_toolbar(self) -> None:
        self._set_task_toolbar_expanded(not self.task_toolbar_expanded)

    def _normal_minimum_size(self) -> tuple[int, int]:
        width, height = self.normal_min_size
        if (self.command_preview_expanded
                and hasattr(self, "command_preview_body")):
            height += self.command_preview_body.winfo_reqheight()
        return width, height

    def _layout_action_buttons(
        self, event: tk.Event | None = None,
    ) -> None:
        """辅助操作可换行，开始与停止固定保留在独立任务控制行。"""
        for button in self.utility_buttons:
            button.grid_forget()
        for button in self.execution_buttons:
            button.grid_forget()
        visible_utilities = [
            self.open_output_button,
            self.clear_log_button,
            self.clear_cache_button,
        ]
        if self.task.key == "env_check":
            visible_utilities.extend(
                self.install_tool_buttons[name]
                for name in self.missing_installable_tools
                if name in self.install_tool_buttons
            )

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
        self.stop_button.grid(
            row=0, column=1, sticky="e", padx=(0, 8))
        self.run_button.grid(row=0, column=2, sticky="e")

    def _set_settings_expanded(self, expanded: bool) -> None:
        self.settings_expanded = expanded
        if expanded:
            if not self.settings_body.winfo_manager():
                self.settings_body.pack(fill="both", expand=True)
        else:
            self.settings_body.pack_forget()
        if not self.mini_mode:
            self.content.grid_rowconfigure(0, weight=1 if expanded else 0)
        self.title_label.configure(font=(
            self.settings_title_expanded_font
            if expanded else _COLLAPSED_PANEL_TITLE_FONT
        ))
        self.settings_title_row.pack_configure(
            padx=(22 if expanded else _PANEL_HEADER_PADX),
            pady=((14, 10) if expanded
                  else _COLLAPSED_SETTINGS_HEADER_PADY),
        )
        self.settings_toggle_button.configure(
            text="收起设置" if expanded else "展开设置")
        if hasattr(self, "settings_visible_var"):
            self.settings_visible_var.set(expanded)

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

    def _toggle_progress_panel(self) -> None:
        self._set_progress_expanded(not self.progress_expanded)

    def _set_log_expanded(self, expanded: bool) -> None:
        self.log_expanded = expanded
        if expanded:
            if not self.log_body.winfo_manager():
                self.log_body.pack(fill="x")
        else:
            self.log_body.pack_forget()
        self.log_toggle_button.configure(
            text="收起日志" if expanded else "展开日志")
        if hasattr(self, "log_visible_var"):
            self.log_visible_var.set(expanded)

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
        if not self.mini_mode:
            self.root.minsize(*self._normal_minimum_size())

    def _task_is_active(self) -> bool:
        return bool(self.process is not None or self.worker_starting
                    or self.run_jobs)

    def _refresh_mini_action(self) -> None:
        self.mini_mode_button.configure(
            text="返回完整界面" if self.mini_mode else "小窗运行",
            state=(
                "normal" if self.mini_mode or self._task_is_active()
                else "disabled"
            ),
        )

    def _set_stop_state(self, state: str) -> None:
        self.stop_button.configure(state=state)
        self.mini_stop_button.configure(state=state)

    def _toggle_mini_mode(self) -> None:
        if self.mini_mode:
            self._leave_mini_mode()
        elif self._task_is_active():
            self._enter_mini_mode()

    def _enter_mini_mode(self) -> None:
        if self.mini_mode or not self._task_is_active():
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

        self.colour_strip.pack_forget()
        self.task_toolbar_panel.pack_forget()
        self._mini_progress_was_expanded = self.progress_expanded
        self._set_progress_expanded(True)
        self.task_card.grid_remove()
        self.log_panel.grid_remove()
        self.command_panel.grid_remove()
        self.progress_panel.grid_configure(row=0, pady=0)
        self.content.grid_rowconfigure(0, weight=0)
        self.content.pack_configure(padx=10, pady=10)
        self.mini_stop_button.pack(side="right", padx=(0, 6))
        self.mini_mode = True
        self._refresh_mini_action()

        self.root.update_idletasks()
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        width = max(420, min(680, screen_width - 32))
        requested_height = self.progress_panel.winfo_reqheight() + 20
        height = max(190, min(300, requested_height))
        x = max(
            0,
            min(screen_width - width, current_x + current_width - width),
        )
        y = max(0, min(screen_height - height, current_y))
        self.root.minsize(min(520, width), height)
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.root.title(f"{core.PROJECT_NAME} {_version()} · 运行进度")

    def _leave_mini_mode(self) -> None:
        if not self.mini_mode:
            return
        self.mini_stop_button.pack_forget()
        self.progress_panel.grid_configure(row=1, pady=(10, 0))
        self.task_card.grid()
        self.log_panel.grid()
        self.command_panel.grid()
        self._set_settings_expanded(self.settings_expanded)
        self._set_progress_expanded(self._mini_progress_was_expanded)
        self.content.pack_configure(
            padx=self.content_pad, pady=self.content_pad)
        self.colour_strip.pack(fill="x", side="top", before=self.body)
        self.task_toolbar_panel.pack(
            fill="x", side="top", before=self.body)
        self.mini_mode = False
        self._refresh_mini_action()
        self._refresh_tool_cache_labels()
        self.root.title(project_window_title())
        self.root.minsize(*self._normal_minimum_size())
        if self._normal_geometry:
            self.root.geometry(self._normal_geometry)
        if self._normal_window_state != "normal":
            self.root.after_idle(
                lambda state=self._normal_window_state:
                self.root.state(state))

    def _scroll_form(self, event: tk.Event) -> str:
        units = int(-event.delta / 120)
        if units == 0 and event.delta:
            units = -1 if event.delta > 0 else 1
        if units:
            self.form_canvas.yview_scroll(units, "units")
        return "break"

    def _save_current_values(self) -> None:
        if self.values:
            self.saved_values[self.task.key] = self._collect_values()

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
                font=(
                    ("Microsoft YaHei UI", 9, "bold")
                    if selected else ("Microsoft YaHei UI", 9)
                ),
            )
        for task_key, button in self.task_toolbar_buttons.items():
            selected_suffix = (
                "Selected" if task_key == self.task.key else ""
            )
            button.configure(
                style=(f"{_TASK_TOOLBAR_STYLE_PREFIX}.TopTask"
                       f"{selected_suffix}.TButton"))

    def _set_task_navigation_state(self, state: str) -> None:
        """运行期间统一锁定或恢复两套顶部任务入口。"""
        for task_menu, entry_index in self.task_menu_entries.values():
            task_menu.entryconfigure(entry_index, state=state)
        for task_key, button in self.task_toolbar_buttons.items():
            button.configure(state=state)

    def _select_task(self, task_key: str, save_current: bool = True) -> None:
        if save_current:
            self._save_current_values()
        self.task = TASK_BY_KEY[task_key]
        if hasattr(self, "task_menu_var"):
            self.task_menu_var.set(task_key)
        self._refresh_task_navigation_selection()
        self.title_label.configure(text=self.task.title)
        self.desc_label.configure(text=self.task.description)
        self._build_form()
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
        self._refresh_environment_actions()
        if self.process is None:
            self._reset_progress(self.task.title)
            if not self.run_jobs:
                self._set_status("就绪")

    def _choice_display(self, spec: FieldSpec, value: object) -> str:
        for label, internal in spec.choices:
            if internal == value:
                return label
        return spec.choices[0][0] if spec.choices else str(value or "")

    def _build_form(self, scroll_fraction: float = 0.0) -> None:
        for child in self.form_inner.winfo_children():
            child.destroy()
        self.values = {}
        form_pad = 16 if self.compact_layout else 22
        saved = _task_values(
            self.task, self.saved_values.get(self.task.key, {}))
        advanced_visible = self.advanced_visible.get(self.task.key, False)
        active_specs = [
            spec for spec in self.task.fields if _field_active(spec, saved)
        ]
        visible_specs = [
            spec for spec in active_specs
            if advanced_visible or not spec.advanced
        ]
        self.form_inner.grid_columnconfigure(1, weight=1)
        row = 0

        if self.task.key == _PROJECT_SELF_TEST_KEY:
            info = tk.Frame(
                self.form_inner, bg=_GREEN_SOFT,
                highlightbackground=_GREEN, highlightthickness=1,
            )
            info.grid(
                row=0, column=0, columnspan=2, sticky="ew",
                padx=form_pad, pady=(18, 8),
            )
            tk.Label(
                info, text="无需设置", bg=_GREEN_SOFT, fg=_GREEN_DEEP,
                font=("Microsoft YaHei UI", 10, "bold"), anchor="w",
            ).pack(fill="x", padx=14, pady=(11, 3))
            info_text = tk.Label(
                info,
                text=(
                    "将运行 Script\\Test 中的全部 unittest。测试夹具只写入"
                    "系统临时目录，不读取表单档案，也不生成正式快照。"
                ),
                bg=_GREEN_SOFT, fg=_TEXT,
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

        advanced_specs = [spec for spec in active_specs if spec.advanced]
        current_section: str | None = None
        section_colour = _NAV_COLOURS.get(
            self.task.key, (_ACCENT, _ACCENT_DARK))[0]
        for spec in visible_specs:
            if spec.section != current_section:
                current_section = spec.section
                section = tk.Frame(self.form_inner, bg=_SURFACE)
                section.grid(
                    row=row, column=0, columnspan=2, sticky="ew",
                    padx=form_pad, pady=(13, 1),
                )
                tk.Frame(
                    section, bg=section_colour, width=4, height=18,
                ).pack(side="left", fill="y")
                tk.Label(
                    section, text=current_section,
                    bg=_SURFACE, fg=section_colour,
                    font=("Microsoft YaHei UI", 9, "bold"), anchor="w",
                ).pack(side="left", padx=(8, 0))
                row += 1

            current = saved.get(spec.key, spec.default)
            label = spec.label + ("  *" if spec.required else "")
            tk.Label(
                self.form_inner, text=label, bg=_SURFACE, fg=_TEXT,
                font=("Microsoft YaHei UI", 9, "bold"), anchor="ne",
            ).grid(
                row=row, column=0, sticky="ne",
                padx=(form_pad, 11 if self.compact_layout else 14),
                pady=(10, 0),
            )

            cell = tk.Frame(self.form_inner, bg=_SURFACE)
            cell.grid(row=row, column=1, sticky="ew", padx=(0, form_pad),
                      pady=(8, 3))
            cell.grid_columnconfigure(0, weight=1)

            if spec.kind in ("bool", "inverse_bool"):
                var = tk.BooleanVar(value=bool(current))
                widget = ttk.Checkbutton(
                    cell, text=spec.toggle_text, variable=var,
                    command=self._update_preview,
                )
                widget.grid(row=0, column=0, sticky="w", pady=3)
                self.values[spec.key] = var
            elif spec.kind in ("choice", "choice_flag"):
                var = tk.StringVar(
                    value=self._choice_display(spec, current))
                widget = ttk.Combobox(
                    cell, textvariable=var, state="readonly",
                    values=[label for label, _value in spec.choices],
                )
                widget.grid(row=0, column=0, sticky="ew")
                widget.bind("<<ComboboxSelected>>",
                            self._choice_changed)
                widget.bind("<MouseWheel>", self._scroll_form)
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
                    highlightthickness=0, font=("Consolas", 9),
                    padx=7, pady=6,
                )
                widget.grid(row=0, column=0, sticky="ew")
                widget.insert("1.0", str(current or ""))
                widget.edit_modified(False)
                widget.bind("<<Modified>>", self._text_changed)
                self.values[spec.key] = widget
                if spec.kind == "multimapdir":
                    add_directory_button = ttk.Button(
                        cell, text="添加目录", style="Browse.TButton",
                        command=lambda s=spec, w=widget:
                        self._append_directory(s, w),
                    )
                    add_directory_button.grid(
                        row=0, column=1, sticky="n", padx=(8, 0))
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
                        cell, text="浏览", style="Browse.TButton",
                        command=lambda s=spec, v=var: self._browse(s, v),
                    )
                    browse_button.grid(row=0, column=1, padx=(8, 0))
                    attach_tooltip(
                        browse_button,
                        (
                            f"选择“{spec.label}”的保存位置。"
                            if spec.kind == "save" else
                            f"选择“{spec.label}”。"
                        ),
                    )

            if spec.help:
                help_label = ttk.Label(
                    cell, text=spec.help, style="Muted.TLabel",
                    wraplength=690, justify="left",
                )
                help_label.grid(
                    row=1, column=0, columnspan=2, sticky="w",
                    pady=(4, 1),
                )
                cell.bind(
                    "<Configure>",
                    lambda e, label=help_label: label.configure(
                        wraplength=max(260, e.width - 10)),
                )
            row += 1

        if advanced_specs:
            configured_advanced = sum(
                saved.get(spec.key, spec.default) != spec.default
                for spec in advanced_specs
            )
            advanced_names = "、".join(dict.fromkeys(
                spec.section for spec in advanced_specs))
            action = "收起" if advanced_visible else "显示"
            configured_text = (
                f" · 已设置 {configured_advanced} 项"
                if configured_advanced else "")
            advanced_row = tk.Frame(self.form_inner, bg=_SURFACE)
            advanced_row.grid(
                row=row, column=0, columnspan=2, sticky="ew",
                padx=form_pad, pady=(14, 5),
            )
            tk.Label(
                advanced_row,
                text=f"高级选项：{advanced_names}",
                bg=_SURFACE,
                fg=_GREEN_DEEP if configured_advanced else _MUTED,
                font=("Microsoft YaHei UI", 8), anchor="center",
                justify="center",
            ).pack(anchor="center", pady=(0, 5))
            self.advanced_button = ttk.Button(
                advanced_row,
                text=f"{action}高级选项{configured_text}",
                style="Browse.TButton",
                command=self._toggle_advanced,
            )
            self.advanced_button.pack(anchor="center")
            attach_tooltip(
                self.advanced_button,
                "显示或收起低频参数；已经设置的高级值不会因收起而丢失。",
            )
            row += 1

        self.form_inner.grid_rowconfigure(row, minsize=10)
        self.form_canvas.update_idletasks()
        self.form_canvas.yview_moveto(scroll_fraction)
        task_key = self.task.key
        self.root.after_idle(
            lambda key=task_key, fraction=scroll_fraction:
            self.form_canvas.yview_moveto(fraction)
            if self.task.key == key else None
        )
        self._update_preview()

    def _choice_changed(self, _event: tk.Event) -> None:
        scroll_fraction = self.form_canvas.yview()[0]
        self.saved_values[self.task.key] = self._collect_values()
        self._build_form(scroll_fraction)

    def _toggle_advanced(self) -> None:
        scroll_fraction = self.form_canvas.yview()[0]
        self.saved_values[self.task.key] = self._collect_values()
        self.advanced_visible[self.task.key] = not self.advanced_visible.get(
            self.task.key, False)
        self._build_form(scroll_fraction)

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
            if isinstance(source, DirectoryListEditor):
                result[key] = source.get()
            elif isinstance(source, tk.Text):
                result[key] = source.get("1.0", "end-1c")
            else:
                value: object = source.get()
                if spec.kind in ("choice", "choice_flag"):
                    for label, internal in spec.choices:
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
            self.task.key, raw, self.detected_tools)

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
            self._set_status("数据库自检命令已复制到剪贴板。")
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
        if self.task.key == "storage_verify":
            archive = str(values.get("archive") or "").strip()
            if archive:
                return os.path.dirname(_absolute(archive))
        if self.task.key.startswith("storage_"):
            return _DEFAULT_STORAGE_DIR
        return os.path.join(_BASE, "Output")

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
            f"DAISY {_version()}\n"
            f"{core.PROJECT_FULL_NAME}\n\n"
            f"Author: {core.PROJECT_AUTHOR}\n"
            f"schema_version={core.SCHEMA_VERSION}\n\n"
            "本地档案清点、登记、核验与对比工具。",
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

    def _clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _clear_tool_cache(self) -> None:
        if self.process is not None or self.worker_starting or self.run_jobs:
            messagebox.showinfo(
                "暂不能清理缓存",
                "任务队列运行期间不能清理工具路径缓存。",
                parent=self.root,
            )
            return
        tool_names = tuple(
            _TOOL_DISPLAY_NAMES.get(name, name)
            for name in self.detected_tools)
        count = clear_session_tool_cache(self.detected_tools)
        disk = clean_project_caches()
        self._refresh_tool_cache_labels()
        self._update_preview()
        lines = ["", "缓存清理结果："]
        if count:
            lines.append(
                f"  本窗口工具路径：{count} 项"
                f"（{'、'.join(tool_names)}）")
        for path in disk.directories:
            lines.append(f"  缓存目录：{path}")
        for path in disk.files:
            lines.append(f"  缓存文件：{path}")
        for error in disk.errors:
            lines.append(f"  未清理：{error}")
        removed = count + len(disk.directories) + len(disk.files)
        if not removed and not disk.errors:
            lines.append("  未发现可清理的缓存。")
        lines.append("")
        self._append_log("\n".join(lines), "meta")
        if disk.errors:
            self._set_status(
                f"缓存清理完成，{len(disk.errors)} 项未清理", _WARNING)
        elif removed:
            self._set_status(f"已清理 {removed} 项缓存", _SUCCESS)
        else:
            self._set_status("没有可清理的缓存", _SUCCESS)

    def _append_log(self, text: str, tag: str | None = None) -> None:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        self.log.configure(state="normal")
        self.log.insert("end", text, tag or ())
        line_count = int(self.log.index("end-1c").split(".")[0])
        if line_count > 10_000:
            self.log.delete("1.0", "1000.0")
        self.log.see("end")
        self.log.configure(state="disabled")

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
        missing = set(self.missing_installable_tools)
        for tool_name, button in self.install_tool_buttons.items():
            display_name = _INSTALLABLE_TOOL_PACKAGES[tool_name][0]
            button.configure(
                text=f"下载并安装 {display_name}",
                state=action_state if tool_name in missing else "disabled",
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
        if total <= 1 or self.run_job_index < 0:
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
            text=(
                f"0/{total} · 队列已准备"
                if total > 1 else "0/1 · 单项任务已准备"
            ),
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
        self_test = self.process_task_key == _PROJECT_SELF_TEST_KEY
        installing = self.process_task_key == _DEPENDENCY_INSTALL_KEY
        title = (
            "数据库自检" if self_test else
            "安装缺失工具" if installing else
            self.task.title
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
                "等待任务报告阶段…"
            ),
            fg=_MUTED,
        )
        self._set_work_indeterminate()

    @staticmethod
    def _short_progress_text(value: object, limit: int = 110) -> str:
        text = str(value or "")
        return text if len(text) <= limit else text[:limit - 1] + "…"

    def _apply_gui_event(self, payload: dict[str, object]) -> None:
        event_name = payload.get("event")
        if event_name == "environment_inventory":
            self._apply_environment_inventory(payload)
            return
        if event_name == "tools_detected":
            self._cache_detected_tools(payload)
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
        if returncode == 0 and not self.stop_requested:
            style, colour, detail = (
                "Success", _SUCCESS,
                (
                    f"数据库自检通过 · 总用时 {_format_duration(elapsed)}"
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
        if self.task.key == "full_scan":
            warnings.append(
                "完整扫描可能持续数小时；停止时可能保留可续传的 partial 快照。")
            if job_count > 1:
                warnings.append(
                    f"将按列表顺序分别生成 {job_count} 个独立数据库。")
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
            warnings.extend((
                f"将只读登记 PhysicalDrive{values.get('disk_number')}；"
                "程序会在采集前重新核对设备身份。",
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
        self.process_task_key = task_key
        self.stop_requested = False
        self.run_jobs = jobs
        self.run_job_index = -1
        self.run_results = []
        self.run_queue_started = time.monotonic()
        self.run_button.configure(state="disabled")
        for button in self.install_tool_buttons.values():
            button.configure(state="disabled")
        self._set_stop_state("disabled")
        self._prepare_queue_progress()
        self._refresh_mini_action()
        self._set_task_navigation_state("disabled")
        if task_key == _PROJECT_SELF_TEST_KEY:
            self._set_status("正在启动数据库自检…")
        else:
            self._set_status(
                f"队列已准备：{len(jobs)} 项。" if len(jobs) > 1
                else "正在启动任务…"
            )
        self._start_next_job()

    def _run_self_test(self) -> None:
        if self.process is not None or self.run_jobs:
            return
        missing = project_self_test_missing_files()
        if missing:
            messagebox.showerror(
                "数据库自检不可用",
                "缺少正式测试文件：\n"
                + "\n".join("• " + name for name in missing),
                parent=self.root,
            )
            return
        confirmed = messagebox.askyesno(
            "运行数据库自检",
            f"将运行 Script\\Test 中的全部 unittest；当前版本 {_version()}。"
            "\n\n"
            "测试不会使用 GUI 表单中的档案目录；夹具在系统临时目录中"
            "创建并清理。部分集成测试会调用 ExifTool、ffprobe 与 7-Zip，"
            "建议先完成 ENV-01 环境检测。\n\n确定继续吗？",
            icon="question", parent=self.root,
        )
        if not confirmed:
            return
        self._begin_run_jobs(
            _PROJECT_SELF_TEST_KEY, [RunJob("数据库自检", {})])

    def _install_missing_tool(self, tool_name: str) -> None:
        """仅安装环境检测页明确选择的一项白名单工具。"""
        if self.process is not None or self.run_jobs or self.worker_starting:
            return
        if (tool_name not in _INSTALLABLE_TOOL_PACKAGES
                or tool_name not in self.missing_installable_tools):
            messagebox.showinfo(
                "没有可安装项",
                "请先运行环境检测；当前没有检测到该工具缺失，或该工具"
                "不在 GUI 安装白名单中。",
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
            "\n\n此操作会修改本机软件安装状态，并接受对应的"
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
        if self.stop_requested or next_index >= len(self.run_jobs):
            return
        self.run_job_index = next_index
        job = self.run_jobs[next_index]
        task_key = self.process_task_key or self.task.key
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
                task_key, job.values, self.detected_tools)
            tool_args = build_tool_args(task_key, effective)
            command = [_console_python(), "-u", _MAIN] + tool_args
            command_text = preview_commands(task_key, effective)[0][1]
        total = len(self.run_jobs)
        if total > 1:
            self._set_status(
                f"队列 {next_index + 1}/{total} · 正在启动 {job.label}…"
            )
        elif task_key == _PROJECT_SELF_TEST_KEY:
            self._set_status("数据库自检运行中…")
        elif task_key == _DEPENDENCY_INSTALL_KEY:
            self._set_status(
                f"正在下载并安装 {job.label}…")
        else:
            self._set_status("正在启动任务…")
        self._begin_progress()
        heading = (
            f"队列 {next_index + 1}/{total}「{job.label}」"
            if total > 1 else job.label)
        self._append_log(
            f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] "
            f"开始 {heading}：{command_text}\n",
            "meta",
        )
        self.worker_starting = True
        worker = threading.Thread(
            target=self._worker, args=(command, tool_sources), daemon=True)
        worker.start()

    def _worker(
        self, command: list[str], tool_sources: dict[str, str],
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
                command, cwd=_BASE, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                env=env, creationflags=creationflags,
            )
        except OSError as exc:
            self.events.put(("start_error", str(exc)))
            return
        self.events.put(("started", process, time.monotonic()))
        if self.stop_requested:
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
                    if self.close_after_stop:
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
        if self.stop_requested:
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
        if not self.stop_requested:
            self.progress_stage_bar.configure(value=100)
            self._set_work_fraction(100, style=style)
        self.progress_percent_label.configure(
            text="停止" if self.stop_requested else
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
        stopped = self.stop_requested
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
                        "数据库自检未能启动。" if self_test else
                        "安装命令未能启动。" if installing else
                        "任务未能启动。"
                    ),
                    _DANGER,
                )
            elif self.stop_requested:
                self._set_status(
                    (
                        "数据库自检已停止；请检查日志。"
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
                        "数据库自检通过。" if self_test else
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
                        "数据库自检失败；请查看日志。"
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
            if self.stop_requested:
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
        self._set_task_navigation_state("normal")
        self.process_task_key = None
        self.stop_requested = False
        self.run_jobs = []
        self.run_job_index = -1
        self.run_results = []
        self.run_queue_started = 0.0
        self.worker_starting = False
        self._refresh_mini_action()
        self._refresh_tool_cache_labels()
        if self.close_after_stop:
            self.close_after_stop = False
            self.root.after_idle(self.root.destroy)
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
        self.process = None
        self.process_started = 0.0
        self._set_stop_state("disabled")
        self.run_results.append(returncode)
        total = max(1, len(self.run_jobs))
        job = (
            self.run_jobs[self.run_job_index]
            if self.run_jobs and self.run_job_index >= 0 else None)
        if total > 1:
            self._update_queue_progress(
                1.0,
                f"已处理 {len(self.run_results)}/{total} · {job.label}"
                if job else f"已处理 {len(self.run_results)}/{total}",
            )
        self_test = self.process_task_key == _PROJECT_SELF_TEST_KEY
        item = (
            f"队列 {self.run_job_index + 1}/{total}「{job.label}」"
            if total > 1 and job else
            ("数据库自检" if self_test else "任务"))
        if returncode is None:
            if total > 1:
                self._append_log(f"\n{item}未能启动。\n", "error")
        elif self.stop_requested:
            self._append_log(
                f"\n{item}已停止（退出码 {returncode}，"
                f"用时 {elapsed:.1f}s）。\n", "warning")
        elif returncode == 0:
            self._append_log(
                f"\n{item}完成（用时 {elapsed:.1f}s）。\n", "success")
        elif (returncode == 1 and total <= 1 and not self_test
              and self.process_task_key != _DEPENDENCY_INSTALL_KEY):
            self._append_log(
                f"\n任务完成，但发现差异或异常（退出码 1，用时 "
                f"{elapsed:.1f}s）。\n", "warning")
        else:
            self._append_log(
                f"\n{item}失败（退出码 {returncode}，"
                f"用时 {elapsed:.1f}s）。\n", "error")

        self._refresh_tool_cache_labels()
        has_next = self.run_job_index + 1 < len(self.run_jobs)
        if not self.stop_requested and has_next:
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
                "这会中断当前数据库自检；测试夹具仍会由测试清理流程处理。"
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
        elif self.process_task_key in ("storage_list", "storage_verify"):
            prompt = (
                "这会中断当前只读查询或归档核验；不会修改硬盘或既有归档。"
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
        if not messagebox.askyesno(
            "确认退出",
            "确定关闭 DAISY 吗？",
            icon="question", parent=self.root,
        ):
            return
        if not active:
            self.root.destroy()
            return

        detail = "关闭界面会停止当前任务，并可能留下未完成产物。确定继续吗？"
        if len(self.run_jobs) > 1:
            detail = (
                "关闭界面会停止当前目录，并取消队列中尚未启动的目录；"
                "也可能留下未完成产物。确定继续吗？"
            )
        if not messagebox.askyesno(
            "再次确认退出", detail,
            icon="warning", parent=self.root,
        ):
            return
        self.stop_requested = True
        self._set_stop_state("disabled")
        if process is not None:
            self.close_after_stop = True
            self._set_status("正在停止任务，随后关闭窗口…", _WARNING)
            threading.Thread(
                target=self._terminate_process, args=(process,), daemon=True,
            ).start()
            return
        if self.worker_starting:
            self.close_after_stop = True
            self._set_status("正在取消启动，随后关闭窗口…", _WARNING)
            return
        self.root.destroy()


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
