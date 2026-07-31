r"""Script_DAISY_GUI：DAISY 的零依赖 Windows 图形界面。

GUI 只负责收集参数、预览命令和管理子进程；扫描、核验、对比与导出仍由
Script\Script_DAISY_MAIN.py 的既有子命令完成，避免形成第二套业务逻辑。
"""
from __future__ import annotations

import base64
import codecs
import json
import math
import os
import queue
import re
import signal
import shutil
import struct
import subprocess
import sys
import threading
import time
import zlib
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
_GUI_EVENT_PREFIX = "@@DAISY_GUI@@"
_PROJECT_SELF_TEST_KEY = "project_self_test"
_DEPENDENCY_INSTALL_KEY = "dependency_install"
_PROJECT_TEST_PATTERN = "Script_DAISY_Test_*.py"
_PROJECT_TEST_FILES = (
    "Script_DAISY_Test_Unit.py",
    "Script_DAISY_Test_No_Clobber.py",
    "Script_DAISY_Test_Tree.py",
)
_MAX_ROOT_DIRECTORIES = 9
_ROOT_BATCH_TASKS = frozenset(("full_scan", "quick_scan"))
_ROOT_BATCH_SEPARATE = "separate"
_ROOT_BATCH_COMBINED = "combined"

_TOOL_FIELD_BY_NAME = {
    "exiftool": "exiftool_path",
    "ffprobe": "ffprobe_path",
    "sevenzip": "sevenzip_path",
    "powershell": "powershell_path",
}
_TOOL_DISPLAY_NAMES = {
    "exiftool": "ExifTool",
    "ffprobe": "ffprobe",
    "sevenzip": "7-Zip",
    "powershell": "PowerShell",
}
_INSTALLABLE_TOOL_PACKAGES = {
    "exiftool": ("ExifTool", "OliverBetz.ExifTool"),
    "ffprobe": ("ffprobe（FFmpeg）", "Gyan.FFmpeg"),
    "sevenzip": ("7-Zip", "7zip.7zip"),
}
_TASK_TOOL_NAMES = {
    "env_check": ("exiftool", "ffprobe", "sevenzip", "powershell"),
    "full_scan": ("exiftool", "ffprobe", "sevenzip", "powershell"),
    "check_format": ("exiftool", "ffprobe", "sevenzip"),
    "check_hash": ("powershell",),
}
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
_SIDEBAR = "#ded3bd"
_SIDEBAR_HOVER = "#d1c4aa"
_SIDEBAR_TEXT = "#35372f"
_SIDEBAR_MUTED = "#6f7065"
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
    "full_scan": (_GREEN_DARK, _GREEN_DEEP, _GREEN),
    "quick_scan": (_GREEN_DARK, _GREEN_DEEP, _GREEN),
    "check_format": (_AMBER_DARK, _AMBER_DEEP, _AMBER),
    "check_hash": (_AMBER_DARK, _AMBER_DEEP, _AMBER),
    "diff": (_AMBER_DARK, _AMBER_DEEP, _AMBER),
    "export_report": (_AMBER_DARK, _AMBER_DEEP, _AMBER),
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


_BRAND_NAME_SEGMENTS = (
    ("D", True),
    ("atabase for ", False),
    ("A", True),
    ("rchive ", False),
    ("I", True),
    ("ntegrity by ", False),
    ("S", True),
    ("uzuran ", False),
    ("Y", True),
    ("e", False),
)


def build_mobius_logo(parent: tk.Misc, compact: bool = False) -> tk.Canvas:
    """绘制无外部图片依赖的极简莫比乌斯环标志。"""
    width, height = ((38, 24) if compact else (44, 28))
    scale_x = width / 44
    scale_y = height / 28
    point_values = (
        3, 14, 7, 6, 14, 5, 22, 14,
        30, 23, 37, 22, 41, 14,
        37, 6, 30, 5, 22, 14,
        14, 23, 7, 22, 3, 14,
    )
    points = [
        value * (scale_x if index % 2 == 0 else scale_y)
        for index, value in enumerate(point_values)
    ]
    canvas = tk.Canvas(
        parent, width=width, height=height, bg=_SURFACE,
        highlightthickness=0, bd=0,
    )
    canvas.create_line(
        *points, smooth=True, splinesteps=36,
        fill=_GREEN_DEEP, width=5 if compact else 6,
        capstyle="round", joinstyle="round",
        tags=("mobius_logo",),
    )
    crossing_values = (18, 10, 26, 18)
    crossing = [
        value * (scale_x if index % 2 == 0 else scale_y)
        for index, value in enumerate(crossing_values)
    ]
    canvas.create_line(
        *crossing, fill=_SURFACE, width=7 if compact else 8,
        capstyle="round", tags=("mobius_cut",),
    )
    canvas.create_line(
        *crossing, fill=_GREEN_DEEP, width=3 if compact else 4,
        capstyle="round", tags=("mobius_twist",),
    )
    symbol_width = 2 if compact else 3
    symbol_extent_x = 3 * scale_x
    symbol_extent_y = 3 * scale_y
    for center_x, tag in ((10 * scale_x, "mobius_positive"),
                          (34 * scale_x, "mobius_negative")):
        canvas.create_line(
            center_x - symbol_extent_x, 14 * scale_y,
            center_x + symbol_extent_x, 14 * scale_y,
            fill=_GREEN_DEEP, width=symbol_width,
            capstyle="round", tags=(tag,),
        )
    canvas.create_line(
        10 * scale_x, 14 * scale_y - symbol_extent_y,
        10 * scale_x, 14 * scale_y + symbol_extent_y,
        fill=_GREEN_DEEP, width=symbol_width,
        capstyle="round", tags=("mobius_positive",),
    )
    return canvas


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    """生成一个 PNG chunk。"""
    checksum = zlib.crc32(kind + payload) & 0xFFFFFFFF
    return (
        struct.pack(">I", len(payload))
        + kind + payload + struct.pack(">I", checksum)
    )


def _paint_disc(
    mask: bytearray, side: int, center_x: float, center_y: float,
    radius: float, value: int,
) -> None:
    """在超采样 alpha mask 上绘制或擦除实心圆。"""
    x0 = max(0, int(center_x - radius - 1))
    x1 = min(side - 1, int(center_x + radius + 1))
    y0 = max(0, int(center_y - radius - 1))
    y1 = min(side - 1, int(center_y + radius + 1))
    radius_sq = radius * radius
    for y in range(y0, y1 + 1):
        dy_sq = (y + 0.5 - center_y) ** 2
        for x in range(x0, x1 + 1):
            if (x + 0.5 - center_x) ** 2 + dy_sq <= radius_sq:
                mask[y * side + x] = value


def _paint_path(
    mask: bytearray, side: int, points: list[tuple[float, float]],
    radius: float, value: int,
) -> None:
    """用相邻圆覆盖生成平滑、圆头的粗路径。"""
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        distance = math.hypot(x1 - x0, y1 - y0)
        steps = max(1, math.ceil(distance))
        for step in range(steps + 1):
            ratio = step / steps
            _paint_disc(
                mask, side,
                x0 + (x1 - x0) * ratio,
                y0 + (y1 - y0) * ratio,
                radius, value,
            )


def _mobius_points(
    side: int, start: float, stop: float, count: int,
) -> list[tuple[float, float]]:
    """返回单色 Möbius 标志使用的双叶曲线点。"""
    center = side / 2
    radius_x = side * 0.39
    radius_y = side * 0.25
    return [
        (
            center + radius_x * math.sin(t),
            center - radius_y * math.sin(2 * t),
        )
        for t in (
            start + (stop - start) * index / (count - 1)
            for index in range(count)
        )
    ]


def mobius_icon_png(size: int = 64) -> bytes:
    """生成平滑、透明背景、单色的窗口 Möbius PNG。"""
    if not 16 <= size <= 256:
        raise ValueError("窗口图标尺寸必须在 16～256 px 之间")
    supersample = 4
    side = size * supersample
    mask = bytearray(side * side)
    stroke_radius = side * 0.052
    _paint_path(
        mask, side, _mobius_points(side, 0.0, math.tau, 257),
        stroke_radius, 255,
    )

    # 中心先擦除下穿分支，再重绘上跨分支，保留莫比乌斯交叠关系。
    crossing_span = 0.12
    _paint_path(
        mask, side,
        _mobius_points(
            side, math.pi - crossing_span,
            math.pi + crossing_span, 33,
        ),
        stroke_radius + side * 0.012, 0,
    )
    _paint_path(
        mask, side,
        _mobius_points(side, -crossing_span, crossing_span, 33),
        stroke_radius, 255,
    )

    # 正负极保持为标志的一部分，但只使用与环相同的前景色。
    symbol_radius = side * 0.015
    symbol_half = side * 0.055
    center = side / 2
    for symbol_x in (side * 0.27, side * 0.73):
        _paint_path(
            mask, side,
            [(symbol_x - symbol_half, center),
             (symbol_x + symbol_half, center)],
            symbol_radius, 255,
        )
    _paint_path(
        mask, side,
        [(side * 0.27, center - symbol_half),
         (side * 0.27, center + symbol_half)],
        symbol_radius, 255,
    )

    foreground = tuple(
        int(_GREEN_DEEP[index:index + 2], 16)
        for index in (1, 3, 5)
    )
    rows = bytearray()
    area = supersample * supersample
    for y in range(size):
        rows.append(0)
        for x in range(size):
            alpha_sum = 0
            for sample_y in range(supersample):
                offset = (y * supersample + sample_y) * side
                for sample_x in range(supersample):
                    alpha_sum += mask[
                        offset + x * supersample + sample_x]
            alpha = round(alpha_sum / area)
            rows.extend((*foreground, alpha))

    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(bytes(rows), level=9))
        + _png_chunk(b"IEND", b"")
    )


def build_mobius_window_icons(root: tk.Misc) -> tuple[tk.PhotoImage, ...]:
    """设置标题栏／任务栏图标，并返回需由应用持有的图像引用。"""
    icons = tuple(
        tk.PhotoImage(
            master=root,
            data=base64.b64encode(mobius_icon_png(size)).decode("ascii"),
        )
        for size in (64, 32, 16)
    )
    root.iconphoto(True, *icons)
    return icons


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
        "10  环境检测",
        "环境检测",
        "检查 ExifTool、ffprobe、7-Zip 与 PowerShell 的发现、版本和只读"
        "冒烟结果，并执行 SHA-256 自检。本页不读取档案进行性能测试，也不"
        "保存全局设置。",
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
        ),
    ),
    TaskSpec(
        "full_scan",
        "full-scan",
        "11  完整扫描",
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
                "allow_abnormal_source", "允许异常快照作为增量来源",
                "--allow-abnormal-source", "bool",
                help="降级选项；本次产物会强制标记为 _Abnormal。",
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
        "12  快速扫描",
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
        "23  格式校验",
        "格式校验",
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
        "22  哈希校验",
        "哈希校验",
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
                section="巡检范围", active_when=_HASH_SAMPLE,
            ),
            FieldSpec(
                "powershell_path", "PowerShell 路径", "--powershell-path",
                "file",
                help="留空时优先继承 11／22 已验证路径，其次自动发现；填写则手动覆盖。",
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
        "21  快照对比",
        "快照对比",
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
                help="降级结果会标记为 _Abnormal；指纹不符仍会拒绝。",
                section="故障恢复", advanced=True,
            ),
        ),
    ),
    TaskSpec(
        "export_report",
        "export-report",
        "31  导出报告",
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
)

TASK_BY_KEY = {task.key: task for task in TASKS}
_SIDEBAR_TASK_ORDER = (
    "env_check",
    "full_scan",
    "quick_scan",
    "diff",
    "check_hash",
    "check_format",
    "export_report",
)
_SIDEBAR_GROUPS = {
    "env_check": ("准备", _GREEN_DARK),
    "full_scan": ("运行", _GREEN_DARK),
    "diff": ("分析", _AMBER_DARK),
    "export_report": ("导出", _AMBER_DARK),
}


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
                bg=_AMBER_SOFT, fg=_AMBER_DARK,
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
            ttk.Button(
                row, text="×", width=3, style="Remove.TButton",
                command=lambda i=index: self.remove(i),
            ).grid(row=0, column=2, padx=(6, 0))
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
    """返回项目自检缺少的正式测试文件名，不读取任何档案目录。"""
    return [
        name for name in _PROJECT_TEST_FILES
        if not os.path.isfile(os.path.join(_TEST_DIR, name))
    ]


def project_self_test_command(python_executable: str | None = None) -> list[str]:
    """返回 GUI 项目自检实际使用的 unittest discovery 命令。"""
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


def initial_log_sash_position(total_height: int,
                              desired_log_height: int,
                              form_min_height: int,
                              sash_width: int = 5) -> int:
    """返回垂直分栏初始分隔线位置，优先保证日志区的可读高度。"""
    usable_height = max(0, total_height - sash_width)
    return max(form_min_height, usable_height - desired_log_height)


def _version() -> str:
    return "v" + core.SCANNER_VERSION


def project_window_title() -> str:
    return (f"{core.PROJECT_NAME} {_version()} - "
            f"{core.PROJECT_FULL_NAME} - Author: {core.PROJECT_AUTHOR}")


class DaisyApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.task = TASKS[0]
        self.values: dict[
            str, tk.Variable | tk.Text | DirectoryListEditor] = {}
        self.saved_values: dict[str, dict[str, object]] = {}
        self.advanced_visible: dict[str, bool] = {}
        self.nav_buttons: dict[str, tk.Button] = {}
        self.detected_tools: dict[str, dict[str, object]] = {}
        self.environment_missing_names: tuple[str, ...] = ()
        self.missing_installable_tools: tuple[str, ...] = ()
        self._work_progress_indeterminate = False
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
        self._select_task(self.task.key, save_current=False)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.bind("<Control-Return>", lambda _e: self._run())
        self.root.bind("<Control-l>", lambda _e: self._clear_log())
        self.root.after(80, self._poll_events)

    def _configure_window(self) -> None:
        self.root.title(project_window_title())
        self.window_icons = build_mobius_window_icons(self.root)
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        width, height = window_size_for_screen(screen_width, screen_height)
        x = max(0, (screen_width - width) // 2)
        y = max(0, (screen_height - height) // 2)
        self.compact_layout = width < 1080 or height < 700
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.root.minsize(min(1040, width), min(680, height))
        self.root.configure(bg=_BG)
        self.root.option_add("*Font", ("Microsoft YaHei UI", 10))

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
            "Badge.TLabel", foreground=_AMBER_DARK,
            background=_AMBER_SOFT,
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
            "Stop.TButton", background=_DANGER_SOFT, foreground=_DANGER,
            padding=(15, 8), font=("Microsoft YaHei UI", 10, "bold"),
            borderwidth=1, bordercolor=_DANGER_BORDER,
            lightcolor=_DANGER_BORDER, darkcolor=_DANGER_BORDER,
        )
        style.map(
            "Stop.TButton", background=[("active", _DANGER_HOVER)])
        style.configure(
            "Secondary.TButton", background=_CONTROL, foreground=_TEXT,
            padding=(12, 8), font=("Microsoft YaHei UI", 10),
            borderwidth=1, bordercolor=_BORDER,
            lightcolor=_BORDER, darkcolor=_BORDER,
        )
        style.map(
            "Secondary.TButton", background=[("active", _CONTROL_HOVER)])
        style.configure(
            "Daisy.Vertical.TScrollbar",
            background=_GREEN_DARK, troughcolor=_CONTROL,
            bordercolor=_BORDER, lightcolor=_GREEN_DARK,
            darkcolor=_GREEN_DARK, arrowcolor=_MUTED,
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
                ("pressed", _GREEN_DEEP),
                ("active", _GREEN),
            ],
        )
        for name, colour in (
                ("Stage", _AMBER),
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
        self._apply_task_accent(self.task.key)

    def _apply_task_accent(self, task_key: str) -> None:
        colour, pressed_colour, active_colour = task_accent_colours(task_key)
        self.style.configure(
            "Primary.TButton",
            background=colour,
            bordercolor=pressed_colour,
            lightcolor=colour,
            darkcolor=colour,
        )
        self.style.map(
            "Primary.TButton",
            background=[
                ("disabled", "#afbeb6"),
                ("pressed", pressed_colour),
                ("active", active_colour),
            ],
        )
        self.style.configure(
            "Daisy.Vertical.TScrollbar",
            background=colour,
            lightcolor=colour,
            darkcolor=colour,
        )
        self.style.map(
            "Daisy.Vertical.TScrollbar",
            background=[
                ("pressed", pressed_colour),
                ("active", active_colour),
            ],
        )

    def _build_shell(self) -> None:
        header_height = 78 if self.compact_layout else 86
        sidebar_width = 194 if self.compact_layout else 222
        content_pad = 12 if self.compact_layout else 18
        nav_pady = 7 if self.compact_layout else 9
        header = tk.Frame(self.root, bg=_SURFACE, height=header_height,
                          highlightbackground=_BORDER, highlightthickness=1)
        header.pack(fill="x", side="top")
        header.pack_propagate(False)

        brand_x = 20 if self.compact_layout else 24
        brand = tk.Frame(header, bg=_SURFACE)
        brand.pack(side="left", fill="y", padx=(brand_x, 0))
        logo = build_mobius_logo(brand, self.compact_layout)
        logo.pack(side="left", anchor="center")
        expansion = tk.Frame(brand, bg=_SURFACE)
        expansion.pack(
            side="left", anchor="center", padx=(10, 0), pady=(5, 0))
        expansion_size = 8 if self.compact_layout else 9
        for text, emphasized in _BRAND_NAME_SEGMENTS:
            tk.Label(
                expansion, text=text, bg=_SURFACE,
                fg=_GREEN_DEEP if emphasized else _MUTED,
                font=(
                    ("Segoe UI", expansion_size, "bold")
                    if emphasized else ("Segoe UI", expansion_size)
                ),
                anchor="w", bd=0, padx=0, pady=0,
            ).pack(side="left")

        identity = tk.Frame(header, bg=_SURFACE)
        identity.pack(
            side="right", fill="y",
            padx=18 if self.compact_layout else 24,
            pady=11 if self.compact_layout else 13,
        )
        tk.Label(
            identity, text=f"Author: {core.PROJECT_AUTHOR}",
            bg=_SURFACE, fg=_MUTED,
            font=("Segoe UI", 8), anchor="w",
        ).pack(side="left", anchor="center", padx=(0, 10))
        tk.Label(
            identity, text=_version(), bg=_GREEN_SOFT, fg=_GREEN_DEEP,
            font=("Segoe UI", 9, "bold"), padx=9, pady=4,
        ).pack(side="left", anchor="center")

        colour_strip = tk.Frame(self.root, bg=_BG, height=4)
        colour_strip.pack(fill="x", side="top")
        colour_strip.pack_propagate(False)
        for colour in (_GREEN, _AMBER, _RED):
            tk.Frame(colour_strip, bg=colour).pack(
                side="left", fill="both", expand=True)

        body = tk.Frame(self.root, bg=_BG)
        body.pack(fill="both", expand=True)

        sidebar = tk.Frame(body, bg=_SIDEBAR, width=sidebar_width)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        tk.Label(
            sidebar, text="工作台", bg=_SIDEBAR, fg=_ACCENT_DARK,
            font=("Microsoft YaHei UI", 9, "bold"), anchor="w",
        ).pack(fill="x", padx=20, pady=(22, 8))

        for task_key in _SIDEBAR_TASK_ORDER:
            task = TASK_BY_KEY[task_key]
            if task.key in _SIDEBAR_GROUPS:
                group_label, group_colour = _SIDEBAR_GROUPS[task.key]
                tk.Label(
                    sidebar, text=group_label, bg=_SIDEBAR,
                    fg=group_colour, font=("Microsoft YaHei UI", 8, "bold"),
                    anchor="w",
                ).pack(fill="x", padx=20, pady=(13, 4))
            button = tk.Button(
                sidebar, text=task.nav, command=lambda key=task.key:
                self._select_task(key),
                bg=_SIDEBAR, fg=_SIDEBAR_TEXT,
                activebackground=_SIDEBAR_HOVER,
                activeforeground=_SIDEBAR_TEXT,
                disabledforeground=_SIDEBAR_MUTED,
                relief="flat", bd=0, anchor="w",
                padx=17 if self.compact_layout else 20, pady=nav_pady,
                font=("Microsoft YaHei UI",
                      9 if self.compact_layout else 10), cursor="hand2",
            )
            button.pack(fill="x")
            self.nav_buttons[task.key] = button

        shortcut_panel = tk.Frame(sidebar, bg=_SIDEBAR)
        shortcut_panel.pack(
            side="bottom", anchor="w", padx=20, pady=(6, 10))
        for row, (shortcut, action) in enumerate((
                ("Ctrl+Enter", "开始"),
                ("Ctrl+L", "清空日志"),
        )):
            tk.Label(
                shortcut_panel, text=shortcut,
                bg=_SIDEBAR, fg=_SIDEBAR_MUTED,
                font=("Consolas", 8), anchor="w",
            ).grid(row=row, column=0, sticky="w")
            tk.Label(
                shortcut_panel, text=action,
                bg=_SIDEBAR, fg=_SIDEBAR_MUTED,
                font=("Microsoft YaHei UI", 8), anchor="w",
            ).grid(row=row, column=1, sticky="w", padx=(8, 0))

        content = tk.Frame(body, bg=_BG)
        content.pack(
            side="left", fill="both", expand=True,
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
        title_row.pack(fill="x", padx=22, pady=(18, 4))
        self.title_label = tk.Label(
            title_row, bg=_SURFACE, fg=_TEXT,
            font=("Microsoft YaHei UI",
                  14 if self.compact_layout else 16, "bold"), anchor="w",
        )
        self.title_label.pack(side="left")
        self.badge_label = tk.Label(
            title_row, bg=_AMBER_SOFT, fg=_AMBER_DARK,
            font=("Microsoft YaHei UI", 9, "bold"), padx=9, pady=4,
        )
        self.badge_label.pack(side="right")
        self.desc_label = tk.Label(
            self.task_card, bg=_SURFACE, fg=_MUTED,
            font=("Microsoft YaHei UI", 9), anchor="w", justify="left",
            wraplength=820,
        )
        self.desc_label.pack(fill="x", padx=22, pady=(0, 12))
        self.task_card.bind(
            "<Configure>",
            lambda e: self.desc_label.configure(
                wraplength=max(420, e.width - 44)),
        )

        separator = tk.Frame(self.task_card, bg=_BORDER, height=1)
        separator.pack(fill="x")

        self.main_pane = tk.PanedWindow(
            self.task_card, orient="vertical", bg=_BORDER, sashwidth=5,
            bd=0, relief="flat", showhandle=False,
        )
        self.main_pane.pack(fill="both", expand=True)
        self.form_pane_min_height = 150 if self.compact_layout else 180
        self.log_pane_min_height = 105 if self.compact_layout else 160
        self.log_pane_initial_height = 150 if self.compact_layout else 180

        form_host = tk.Frame(self.main_pane, bg=_SURFACE)
        self.main_pane.add(
            form_host,
            minsize=self.form_pane_min_height,
            stretch="always",
        )
        self.form_canvas = tk.Canvas(
            form_host, bg=_SURFACE, highlightthickness=0, bd=0,
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

        log_frame = tk.Frame(self.main_pane, bg=_LOG_BG)
        self.main_pane.add(
            log_frame, minsize=self.log_pane_min_height,
            height=self.log_pane_initial_height, stretch="never",
        )
        log_header = tk.Frame(log_frame, bg=_LOG_HEADER, height=38)
        log_header.pack(fill="x")
        tk.Label(
            log_header, text="运行日志", bg=_LOG_HEADER, fg=_MUTED,
            font=("Microsoft YaHei UI", 9, "bold"),
        ).pack(side="left", padx=14, pady=9)
        self.log = tk.Text(
            log_frame, bg=_LOG_BG, fg=_LOG_TEXT, insertbackground=_TEXT,
            selectbackground=_LOG_SELECT, relief="flat", bd=0,
            font=("Consolas", 9), wrap="word", padx=13, pady=10,
            state="disabled",
        )
        log_scroll = ttk.Scrollbar(
            log_frame, orient="vertical", command=self.log.yview,
            style="Daisy.Vertical.TScrollbar",
        )
        self.log.configure(yscrollcommand=log_scroll.set)
        log_scroll.pack(side="right", fill="y")
        self.log.pack(fill="both", expand=True)
        self.log.tag_configure("meta", foreground=_AMBER_DARK)
        self.log.tag_configure("success", foreground=_SUCCESS)
        self.log.tag_configure("warning", foreground=_WARNING)
        self.log.tag_configure("error", foreground=_DANGER)

        progress_panel = tk.Frame(
            content, bg=_SURFACE, highlightbackground=_BORDER,
            highlightthickness=1,
        )
        progress_panel.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        progress_inner = tk.Frame(progress_panel, bg=_SURFACE)
        progress_inner.pack(
            fill="x", padx=12 if self.compact_layout else 15,
            pady=8 if self.compact_layout else 10,
        )
        progress_inner.grid_columnconfigure(1, weight=1)

        tk.Label(
            progress_inner, text="任务阶段", bg=_SURFACE, fg=_AMBER_DARK,
            font=("Microsoft YaHei UI", 8, "bold"), anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=(0, 10))
        self.progress_stage_label = tk.Label(
            progress_inner, text="等待开始", bg=_SURFACE, fg=_MUTED,
            font=("Microsoft YaHei UI", 8), anchor="w",
        )
        self.progress_stage_label.grid(row=0, column=1, sticky="ew")
        self.progress_stage_bar = ttk.Progressbar(
            progress_inner, mode="determinate", maximum=100, value=0,
            style="Stage.Horizontal.TProgressbar",
        )
        self.progress_stage_bar.grid(
            row=1, column=0, columnspan=3, sticky="ew", pady=(4, 6))

        tk.Label(
            progress_inner, text="本阶段", bg=_SURFACE, fg=_GREEN_DARK,
            font=("Microsoft YaHei UI", 8, "bold"), anchor="w",
        ).grid(row=2, column=0, sticky="w", padx=(0, 10))
        self.progress_detail_label = tk.Label(
            progress_inner, text="尚未运行", bg=_SURFACE, fg=_MUTED,
            font=("Microsoft YaHei UI", 8), anchor="w",
        )
        self.progress_detail_label.grid(row=2, column=1, sticky="ew")
        self.progress_percent_label = tk.Label(
            progress_inner, text="0%", bg=_SURFACE, fg=_GREEN_DARK,
            font=("Segoe UI", 8, "bold"), anchor="e",
        )
        self.progress_percent_label.grid(row=2, column=2, sticky="e")
        self.progress_work_bar = ttk.Progressbar(
            progress_inner, mode="determinate", maximum=100, value=0,
            style="Work.Horizontal.TProgressbar",
        )
        self.progress_work_bar.grid(
            row=3, column=0, columnspan=3, sticky="ew", pady=(4, 5))
        self.tool_cache_label = tk.Label(
            progress_inner, text="",
            bg=_SURFACE, fg=_MUTED,
            font=("Microsoft YaHei UI", 8), anchor="w", justify="left",
        )
        self.tool_cache_label.grid(
            row=4, column=0, columnspan=3, sticky="ew")
        self.tool_cache_label.grid_remove()
        progress_inner.bind(
            "<Configure>",
            lambda e: self.tool_cache_label.configure(
                wraplength=max(260, e.width - 4)),
        )

        command_panel = tk.Frame(content, bg=_BG)
        command_panel.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        tk.Label(
            command_panel, text="命令预览", bg=_BG, fg=_MUTED,
            font=("Microsoft YaHei UI", 9),
        ).pack(anchor="w")
        preview_row = tk.Frame(command_panel, bg=_BG)
        preview_row.pack(fill="x", pady=(5, 9))
        self.preview_var = tk.StringVar()
        preview_entry = ttk.Entry(
            preview_row, textvariable=self.preview_var, state="readonly",
        )
        preview_entry.pack(side="left", fill="x", expand=True)
        ttk.Button(
            preview_row, text="复制", style="Browse.TButton",
            command=self._copy_command,
        ).pack(side="left", padx=(8, 0))

        actions = tk.Frame(command_panel, bg=_BG)
        actions.pack(fill="x")
        status_area = tk.Frame(actions, bg=_BG)
        status_area.pack(side="left", fill="both", expand=True)
        self.status_label = tk.Label(
            status_area, text="就绪", bg=_GREEN_DARK, fg="white",
            font=("Microsoft YaHei UI", 9, "bold"), anchor="w",
            padx=10, pady=5,
        )
        self.status_label.pack(side="left", anchor="center")
        self.open_output_button = ttk.Button(
            actions, text="打开结果目录", style="Secondary.TButton",
            command=self._open_output,
        )
        self.open_output_button.pack(side="right", padx=(8, 0))
        self.clear_log_button = ttk.Button(
            actions, text="清空日志", style="Secondary.TButton",
            command=self._clear_log,
        )
        self.clear_log_button.pack(side="right", padx=(8, 0))
        self.clear_cache_button = ttk.Button(
            actions, text="清理缓存", style="Secondary.TButton",
            command=self._clear_tool_cache, state="disabled",
        )
        self.clear_cache_button.pack(side="right", padx=(8, 0))
        self.stop_button = ttk.Button(
            actions, text="停止", style="Stop.TButton",
            command=self._stop, state="disabled",
        )
        self.stop_button.pack(side="right", padx=(8, 0))
        self.run_button = ttk.Button(
            actions, text="开始任务", style="Primary.TButton",
            command=self._run,
        )
        self.run_button.pack(side="right")
        self.self_test_button = ttk.Button(
            actions, text="运行项目自检", style="Secondary.TButton",
            command=self._run_self_test,
        )
        self.install_tools_button = ttk.Button(
            actions, text="下载并安装缺失工具",
            style="Secondary.TButton",
            command=self._install_missing_tools,
        )
        self.root.after_idle(self._position_initial_log_sash)

    def _position_initial_log_sash(self) -> None:
        total_height = self.main_pane.winfo_height()
        if total_height <= 1:
            return
        sash_width = int(self.main_pane.cget("sashwidth"))
        sash_y = initial_log_sash_position(
            total_height,
            self.log_pane_initial_height,
            self.form_pane_min_height,
            sash_width,
        )
        self.main_pane.sash_place(0, 0, sash_y)

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

    def _select_task(self, task_key: str, save_current: bool = True) -> None:
        if save_current:
            self._save_current_values()
        self.task = TASK_BY_KEY[task_key]
        selected_colour, selected_hover, _active_colour = (
            task_accent_colours(task_key))
        self._apply_task_accent(task_key)
        for key, button in self.nav_buttons.items():
            selected = key == task_key
            button.configure(
                bg=selected_colour if selected else _SIDEBAR,
                activebackground=(
                    selected_hover if selected else _SIDEBAR_HOVER),
                activeforeground="white" if selected else _SIDEBAR_TEXT,
                fg="white" if selected else _SIDEBAR_TEXT,
            )
        self.title_label.configure(text=self.task.title)
        self.badge_label.configure(text=self.task.badge)
        self.desc_label.configure(text=self.task.description)
        self._build_form()
        self._refresh_tool_cache_labels()
        if task_key == "env_check":
            missing_tests = project_self_test_missing_files()
            self.self_test_button.configure(
                text="项目自检不可用" if missing_tests else "运行项目自检",
                state=(
                    "disabled"
                    if missing_tests or self.process is not None or self.run_jobs
                    else "normal"
                ),
            )
            if not self.self_test_button.winfo_manager():
                self.self_test_button.pack(side="right", padx=(0, 8))
        else:
            self.self_test_button.pack_forget()
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
                    ttk.Button(
                        cell, text="添加目录", style="Browse.TButton",
                        command=lambda s=spec, w=widget:
                        self._append_directory(s, w),
                    ).grid(row=0, column=1, sticky="n", padx=(8, 0))
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
                    ttk.Button(
                        cell, text="浏览", style="Browse.TButton",
                        command=lambda s=spec, v=var: self._browse(s, v),
                    ).grid(row=0, column=1, padx=(8, 0))

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
                fg=_AMBER_DARK if configured_advanced else _MUTED,
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
        if not self.values:
            return
        try:
            effective, _sources = self._effective_values()
            self.preview_var.set(
                preview_command(self.task.key, effective))
        except (KeyError, tk.TclError):
            pass

    def _copy_command(self) -> None:
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
        return os.path.join(_BASE, "Output")

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
        summary = session_tool_cache_summary(
            self.task.key, self.detected_tools)
        if self.task.key == "env_check" and self.environment_missing_names:
            missing = "、".join(
                _TOOL_DISPLAY_NAMES.get(name, name)
                for name in self.environment_missing_names)
            summary = (
                summary + "\n" if summary else ""
            ) + f"缺失或不可用：{missing}"
        self.tool_cache_label.configure(text=summary)
        if summary:
            self.tool_cache_label.grid()
        else:
            self.tool_cache_label.grid_remove()
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
        if (self.task.key == "env_check"
                and self.missing_installable_tools):
            count = len(self.missing_installable_tools)
            self.install_tools_button.configure(
                text=f"下载并安装缺失工具（{count}）",
                state=(
                    "normal"
                    if self.process is None
                    and not self.worker_starting
                    and not self.run_jobs
                    else "disabled"
                ),
            )
            if not self.install_tools_button.winfo_manager():
                self.install_tools_button.pack(side="right", padx=(0, 8))
        else:
            self.install_tools_button.pack_forget()

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

    def _queue_stage_value(self, fraction: float) -> float:
        fraction = max(0.0, min(1.0, fraction))
        total = self._queue_total()
        if len(self.run_jobs) <= 1:
            return fraction * 100
        return (self.run_job_index + fraction) / total * 100

    def _begin_progress(self) -> None:
        start_value = self._queue_stage_value(0.0)
        self_test = self.process_task_key == _PROJECT_SELF_TEST_KEY
        installing = self.process_task_key == _DEPENDENCY_INSTALL_KEY
        title = (
            "项目自检" if self_test else
            "安装缺失工具" if installing else
            self.task.title
        )
        self.progress_stage_bar.configure(
            mode="determinate", maximum=100, value=start_value,
            style="Stage.Horizontal.TProgressbar",
        )
        self.progress_stage_label.configure(
            text=f"{self._queue_prefix()}{title} · 正在启动",
            fg=_AMBER_DARK,
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
            name = self._short_progress_text(payload.get("name") or "处理中")
            self.progress_stage_bar.configure(
                mode="determinate", maximum=100,
                value=self._queue_stage_value((stage_idx - 1) / stage_total),
                style="Stage.Horizontal.TProgressbar",
            )
            self.progress_stage_label.configure(
                text=f"{self._queue_prefix()}阶段 {stage_idx}/{stage_total} · {name}",
                fg=_AMBER_DARK,
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
            return
        if event_name in ("progress_finish", "progress_skip"):
            stage_idx = max(1, int(payload.get("stage_idx") or 1))
            stage_total = max(stage_idx, int(payload.get("stage_total") or 1))
            name = self._short_progress_text(payload.get("name") or "阶段")
            self.progress_stage_bar.configure(
                mode="determinate", maximum=100,
                value=self._queue_stage_value(stage_idx / stage_total),
                style="Stage.Horizontal.TProgressbar",
            )
            if event_name == "progress_skip":
                detail = "已跳过：" + str(payload.get("reason") or "当前配置")
            else:
                detail = str(payload.get("summary") or "完成")
                elapsed = payload.get("elapsed")
                if elapsed is not None:
                    detail += f" · 用时 {_format_duration(elapsed)}"
            self.progress_stage_label.configure(
                text=f"{self._queue_prefix()}阶段 {stage_idx}/{stage_total} · {name}",
                fg=_AMBER_DARK,
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
                    f"项目自检通过 · 总用时 {_format_duration(elapsed)}"
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
        if "force" in active_keys and values.get("force"):
            warnings.append("已启用文件名指纹缺失时的降级准入。")
        if ("allow_abnormal_source" in active_keys
                and values.get("allow_abnormal_source")):
            warnings.append("已允许异常快照作为增量复用来源。")
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
        self.self_test_button.configure(state="disabled")
        self.install_tools_button.configure(state="disabled")
        self.stop_button.configure(state="disabled")
        for button in self.nav_buttons.values():
            button.configure(state="disabled")
        if task_key == _PROJECT_SELF_TEST_KEY:
            self._set_status("正在启动项目自检…")
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
                "项目自检不可用",
                "缺少正式测试文件：\n"
                + "\n".join("• " + name for name in missing),
                parent=self.root,
            )
            return
        confirmed = messagebox.askyesno(
            "运行项目自检",
            f"将运行 Script\\Test 中的全部 unittest；当前版本 {_version()}。"
            "\n\n"
            "测试不会使用 GUI 表单中的档案目录；夹具在系统临时目录中"
            "创建并清理。部分集成测试会调用 ExifTool、ffprobe 与 7-Zip，"
            "建议先完成本页环境检测。\n\n确定继续吗？",
            icon="question", parent=self.root,
        )
        if not confirmed:
            return
        self._begin_run_jobs(
            _PROJECT_SELF_TEST_KEY, [RunJob("项目自检", {})])

    def _install_missing_tools(self) -> None:
        if self.process is not None or self.run_jobs or self.worker_starting:
            return
        tool_names = tuple(
            name for name in self.missing_installable_tools
            if name in _INSTALLABLE_TOOL_PACKAGES)
        if not tool_names:
            messagebox.showinfo(
                "没有可安装项",
                "请先运行环境检测；当前没有检测到可由 GUI 安装的缺失工具。",
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
        package_lines = [
            f"• {_INSTALLABLE_TOOL_PACKAGES[name][0]}"
            f"（{_INSTALLABLE_TOOL_PACKAGES[name][1]}）"
            for name in tool_names
        ]
        confirmed = messagebox.askyesno(
            "下载并安装缺失工具",
            "将通过 WinGet 的 winget 源逐项下载并安装：\n"
            + "\n".join(package_lines)
            + "\n\n此操作会修改本机软件安装状态，并接受对应的"
            "源协议与软件包协议。安装输出会显示在运行日志中；"
            "完成后 DAISY 将自动重新检测环境。\n\n确定继续吗？",
            icon="question", parent=self.root,
        )
        if not confirmed:
            return
        jobs = [
            RunJob(
                _INSTALLABLE_TOOL_PACKAGES[name][0],
                {"tool_name": name, "winget_path": winget},
            )
            for name in tool_names
        ]
        self._begin_run_jobs(_DEPENDENCY_INSTALL_KEY, jobs)

    def _run(self) -> None:
        if self.process is not None or self.run_jobs:
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
            self._set_status("项目自检运行中…")
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
                        self.stop_button.configure(state="disabled")
                        self._set_status("正在停止任务，随后关闭窗口…", _WARNING)
                    else:
                        self.stop_button.configure(state="normal")
                        self._set_status(
                            f"{self._queue_prefix()}运行中"
                            f"（PID {self.process.pid}）…")
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
            style, colour, percent = "Warning", _WARNING, "停止"
            detail = (
                f"队列已停止 · 已处理 {processed}/{total} · "
                f"用时 {_format_duration(elapsed)}")
        elif failures:
            style, colour, percent = "Warning", _WARNING, "检查"
            detail = (
                f"队列完成 · 成功 {successes} · 失败 {failures} · "
                f"用时 {_format_duration(elapsed)}")
            value = 100
        else:
            style, colour, percent = "Success", _SUCCESS, "完成"
            detail = (
                f"队列完成 · {successes}/{total} 成功 · "
                f"用时 {_format_duration(elapsed)}")
            value = 100
        self.progress_stage_bar.configure(
            mode="determinate", maximum=100, value=value,
            style=f"{style}.Horizontal.TProgressbar",
        )
        self._set_work_fraction(value, style=style)
        self.progress_percent_label.configure(text=percent, fg=colour)
        self.progress_stage_label.configure(text=detail, fg=colour)
        self.progress_detail_label.configure(text=detail, fg=colour)

    def _finalize_run(self, last_elapsed: float) -> None:
        total = max(1, len(self.run_jobs))
        returncode = self.run_results[-1] if self.run_results else None
        self_test = self.process_task_key == _PROJECT_SELF_TEST_KEY
        installing = self.process_task_key == _DEPENDENCY_INSTALL_KEY
        stopped = self.stop_requested
        if total <= 1:
            if returncode is None:
                self._set_status(
                    (
                        "项目自检未能启动。" if self_test else
                        "安装命令未能启动。" if installing else
                        "任务未能启动。"
                    ),
                    _DANGER,
                )
            elif self.stop_requested:
                self._set_status(
                    (
                        "项目自检已停止；请检查日志。"
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
                        "项目自检通过。" if self_test else
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
                        "项目自检失败；请查看日志。"
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

        self.run_button.configure(state="normal")
        self.self_test_button.configure(
            state=(
                "normal"
                if (self.task.key == "env_check"
                    and not project_self_test_missing_files())
                else "disabled"
            ))
        self.stop_button.configure(state="disabled")
        for button in self.nav_buttons.values():
            button.configure(state="normal")
        self.process_task_key = None
        self.stop_requested = False
        self.run_jobs = []
        self.run_job_index = -1
        self.run_results = []
        self.run_queue_started = 0.0
        self.worker_starting = False
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


    def _finish_ui(
        self, returncode: int | None, finished: float | None = None,
    ) -> None:
        elapsed = (
            (finished or time.monotonic()) - self.process_started
            if self.process_started else 0.0
        )
        self.process = None
        self.process_started = 0.0
        self.stop_button.configure(state="disabled")
        self.run_results.append(returncode)
        total = max(1, len(self.run_jobs))
        job = (
            self.run_jobs[self.run_job_index]
            if self.run_jobs and self.run_job_index >= 0 else None)
        self_test = self.process_task_key == _PROJECT_SELF_TEST_KEY
        item = (
            f"队列 {self.run_job_index + 1}/{total}「{job.label}」"
            if total > 1 and job else
            ("项目自检" if self_test else "任务"))
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
                "这会中断当前项目自检；测试夹具仍会由测试清理流程处理。"
                "\n\n确定停止吗？"
            )
        elif self.process_task_key == _DEPENDENCY_INSTALL_KEY:
            prompt = (
                "这会中断当前 WinGet 进程；已经完成安装的软件不会回滚，"
                "尚未开始的工具会取消。\n\n确定停止吗？"
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
        self.stop_button.configure(state="disabled")
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
        active = process is not None or bool(self.run_jobs)
        if active:
            detail = "关闭界面会停止当前任务，并可能留下未完成产物。确定关闭吗？"
            if len(self.run_jobs) > 1:
                detail = (
                    "关闭界面会停止当前目录，并取消队列中尚未启动的目录；"
                    "也可能留下未完成产物。确定关闭吗？"
                )
            if not messagebox.askyesno(
                "任务仍在运行", detail,
                icon="warning", parent=self.root,
            ):
                return
            self.stop_requested = True
            self.stop_button.configure(state="disabled")
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
