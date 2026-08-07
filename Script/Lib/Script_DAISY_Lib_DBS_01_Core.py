"""DAISY DBS 核心模块。

现行语义说明：Spec/Spec_DAISY_Technical.md
硬约束：对档案绝对只读；全 I/O UTF-8；时间戳 UTC 100ns；路径键规则 v1；盘符无关。
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import socket
import sqlite3
import sys
import time
import unicodedata
import uuid

PROJECT_NAME = "DAISY"
PROJECT_FULL_NAME = "Database for Archive Integrity by Suzuran Ye"
PROJECT_AUTHOR = "Suzuran Ye"
PROJECT_CONTACT = "151104858+SuzuranYe@users.noreply.github.com"
SCANNER_VERSION = "1.6.0"      # 包版本
SCHEMA_VERSION = 3
READABLE_SCHEMA_VERSIONS = frozenset({SCHEMA_VERSION})
MIN_READER_VERSION = "1.4.1"
DATA_CONTRACT = "daisy-snapshot-v3"
PATH_KEY_RULE = 1
FILENAME_LAYOUT_VERSION = 2

WINDOWS_WORKER_ERROR_MODE_FLAGS = (
    0x0001   # SEM_FAILCRITICALERRORS
    | 0x0002  # SEM_NOGPFAULTERRORBOX
    | 0x8000  # SEM_NOOPENFILEERRORBOX
)


def configure_windows_worker_error_mode(
    *,
    _platform: str | None = None,
    _get_error_mode=None,
    _set_error_mode=None,
) -> dict[str, object]:
    """请求 Windows 让任务 worker 后代以返回码报告 native 故障。

    该函数只应在非 Tk 的任务进程启动处调用。注入参数仅供不改变测试进程
    实际错误模式的单元测试使用。
    """
    current_platform = sys.platform if _platform is None else _platform
    required = WINDOWS_WORKER_ERROR_MODE_FLAGS
    if current_platform != "win32":
        return {
            "status": "not_applicable",
            "required_flags": required,
            "previous_mode": None,
            "effective_mode": None,
            "detail": None,
        }
    if (_get_error_mode is None) != (_set_error_mode is None):
        raise ValueError("错误模式测试 API 必须同时提供 get 与 set")
    try:
        if _get_error_mode is None:
            import ctypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            getter = kernel32.GetErrorMode
            getter.argtypes = []
            getter.restype = ctypes.c_uint
            setter = kernel32.SetErrorMode
            setter.argtypes = [ctypes.c_uint]
            setter.restype = ctypes.c_uint
        else:
            getter = _get_error_mode
            setter = _set_error_mode
        before = int(getter()) & 0xFFFFFFFF
        setter(before | required)
        after = int(getter()) & 0xFFFFFFFF
    except Exception as exc:
        # 平台 API 的任何失败都只能降级提示，不能阻止扫描任务启动。
        return {
            "status": "error",
            "required_flags": required,
            "previous_mode": None,
            "effective_mode": None,
            "detail": f"{type(exc).__name__}: {exc}",
        }
    missing = required & ~after
    return {
        "status": "configured" if not missing else "degraded",
        "required_flags": required,
        "previous_mode": before,
        "effective_mode": after,
        "detail": (
            None if not missing
            else f"Windows 错误模式缺少标志 0x{missing:08X}"
        ),
    }


def report_metadata(tool_name: str) -> dict[str, str]:
    """返回报告文件共用的生成工具身份，不涉及数据库 schema。"""
    return {
        "tool_name": f"{PROJECT_NAME} {str(tool_name).strip()}",
        "tool_version": SCANNER_VERSION,
        "tool_author": PROJECT_AUTHOR,
    }


def report_markdown_lines(tool_name: str) -> list[str]:
    """返回可直接插入 Markdown 报告标题后的工具署名。"""
    identity = report_metadata(tool_name)
    return [
        f"- 工具：`{identity['tool_name']}`",
        f"- 版本：`{identity['tool_version']}`",
        f"- 作者：`{identity['tool_author']}`",
    ]

# 支持格式映射；配置可扩展，新增格式须附样本实测
EXT_TO_KIND = {
    **dict.fromkeys(["cr2", "cr3", "nef", "arw", "raf", "orf", "rw2", "dng"], "photo_raw"),
    **dict.fromkeys(["jpg", "jpeg", "jfif"], "photo_jpeg"),
    "gif": "image_gif",
    **dict.fromkeys(["tif", "tiff", "psd", "psb", "png"], "photo_working"),
    **dict.fromkeys(["mp4", "mov", "lrf"], "video_mp4"),
    "crm": "video_crm",
    **dict.fromkeys(["wav", "mp3", "aac"], "audio"),
    **dict.fromkeys(["zip", "7z", "rar", "tar", "gz", "bz2", "xz"], "archive"),
    **dict.fromkeys(["pdf", "doc", "docx", "xlsx", "pptx"], "document"),
}


class PreflightError(RuntimeError):
    """预检失败：环境不满足运行前提。"""


class StageControlBoundary(RuntimeError):
    """新运行层请求在当前可提交的文件／目录边界停止领取工作。"""


def require_readable_schema_version(
    schema_version: int, artifact: str = "快照",
) -> int:
    """拒绝本工具未声明可读的数据库契约版本。"""
    if schema_version not in READABLE_SCHEMA_VERSIONS:
        supported = "、".join(
            str(v) for v in sorted(READABLE_SCHEMA_VERSIONS))
        raise PreflightError(
            f"{artifact} schema_version={schema_version} 非本工具可读范围"
            f"（{supported}）")
    return schema_version


def require_sqlite_integrity(
    con: sqlite3.Connection, artifact: str = "数据库",
) -> None:
    """验证 SQLite 结构与外键；任何失败都统一为预检错误。"""
    try:
        row = con.execute("PRAGMA quick_check").fetchone()
        if row is None or row[0] != "ok":
            detail = None if row is None else row[0]
            raise PreflightError(f"{artifact} SQLite 完整性检查失败：{detail}")
        fk_error = con.execute("PRAGMA foreign_key_check").fetchone()
        if fk_error is not None:
            raise PreflightError(
                f"{artifact} SQLite 外键检查失败：{tuple(fk_error)}")
    except sqlite3.Error as exc:
        raise PreflightError(f"{artifact} SQLite 无法读取：{exc}") from exc


def require_sealed_snapshot(
    con: sqlite3.Connection, artifact: str = "快照",
) -> int:
    """只接纳 schema 3 当前结构、完整封存且数据库完整的快照。"""
    try:
        row = con.execute(
            "SELECT schema_version,scan_status,database_integrity"
            " FROM snapshot_info WHERE id=1").fetchone()
    except sqlite3.Error as exc:
        raise PreflightError(
            f"{artifact} 不是 schema 3 快照结构：{exc}") from exc
    if row is None:
        raise PreflightError(f"{artifact} 缺少 snapshot_info")
    schema_version, scan_status, database_integrity = row
    require_readable_schema_version(schema_version, artifact)
    if scan_status != "complete":
        raise PreflightError(
            f"{artifact} 扫描未完整结束（scan_status={scan_status}）")
    if database_integrity != "ok":
        raise PreflightError(
            f"{artifact} 未声明 database_integrity=ok"
            f"（当前为 {database_integrity}）")
    require_sqlite_integrity(con, artifact)
    return schema_version


def force_utf8_io() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def make_path_key(rel_path: str) -> str:
    """路径比较键规则 v1：NFC → casefold → 分隔符统一 '/'。"""
    return unicodedata.normalize("NFC", rel_path).casefold().replace("\\", "/")


def ns_to_utc_iso(ns: int) -> str:
    """纳秒时间戳 → UTC ISO 8601，固定 7 位小数（100ns，NTFS 原生粒度）。"""
    sec, frac_ns = divmod(ns, 1_000_000_000)
    t = time.gmtime(sec)
    return (f"{t.tm_year:04d}-{t.tm_mon:02d}-{t.tm_mday:02d}"
            f"T{t.tm_hour:02d}:{t.tm_min:02d}:{t.tm_sec:02d}"
            f".{frac_ns // 100:07d}Z")


def now_utc_iso() -> str:
    return ns_to_utc_iso(time.time_ns())


def local_utc_offset_min() -> int:
    lt = time.localtime()
    return int(lt.tm_gmtoff // 60)


def id_hex(value: int) -> str | None:
    """卷/文件标识 → 小写十六进制 TEXT（无前缀、不补零）；0 视为未采集。"""
    return format(value, "x") if value else None


def media_kind_for(extension: str) -> str:
    return EXT_TO_KIND.get(extension.lower(), "other")


def extension_of(name: str) -> str:
    _, dot, ext = name.rpartition(".")
    return ext.lower() if dot else ""


def to_extended_path(path: str) -> str:
    r"""绝对路径 → \\?\ 扩展长度形式（不依赖注册表长路径开关）。"""
    p = os.path.abspath(path)
    if p.startswith("\\\\?\\"):
        return p
    if p.startswith("\\\\"):                    # UNC
        return "\\\\?\\UNC\\" + p[2:]
    return "\\\\?\\" + p


def snapshot_profile_tokens(kind: str, hash_mode: str = "full",
                            raw_payload: bool = True,
                            file_id: bool = True) -> list[str]:
    """返回只描述快照证据能力的稳定文件名标记。"""
    normalized = kind.casefold()
    if normalized == "full":
        if hash_mode not in ("none", "incremental", "full"):
            raise ValueError(f"未知哈希模式：{hash_mode}")
        tokens = []
        if hash_mode == "none":
            tokens.append("No-Hash")
        elif hash_mode == "incremental":
            tokens.append("Hash-Inc")
        if not raw_payload:
            tokens.append("Basic-Metadata")
        if not file_id:
            tokens.append("No-FID")
        return tokens
    if normalized == "quick":
        # Quick 已经明确蕴含无哈希、无元数据原文，不重复制造冗余标记。
        return [] if file_id else ["No-FID"]
    return []


def snapshot_name(labels: list[str], kind: str,
                  profile_tokens: list[str] | tuple[str, ...] = ()) -> str:
    """最终产物短基名：根标签_类型_[偏差标记_]日期_时-分-秒。

    多 root 以 + 连接；文件名时间戳用本地时间，权威时间在库内 UTC。
    最终发布时还会追加数据库 SHA-256 高 32 bit；问题状态只保存在库内，
    并在必要时生成同目录 Issues.md，不进入数据库文件名。"""
    safe = "+".join(labels)
    for ch in '<>:"/\\|?*':
        safe = safe.replace(ch, "_")
    stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
    identity = "_".join([safe, kind, *profile_tokens])
    return f"{identity}_{stamp}"


def snapshot_working_name(snapshot_stem: str) -> str:
    """给运行态 partial／报告生成唯一内部基名；不会保留到封存快照名。"""
    if not snapshot_stem or os.path.basename(snapshot_stem) != snapshot_stem:
        raise PreflightError(f"snapshot_stem 必须是不含路径的非空基名：{snapshot_stem!r}")
    micro = time.time_ns() // 1000 % 1_000_000
    return f"{snapshot_stem}.{micro:06d}_{uuid.uuid4().hex[:8]}"


def resolve_snapshot_publish_stem(partial_path: str, snapshot_stem: str) -> str:
    """把库内记录的最终基名安全解析到 partial 所在目录。"""
    if not snapshot_stem or os.path.basename(snapshot_stem) != snapshot_stem:
        raise PreflightError(f"snapshot_stem 必须是不含路径的非空基名：{snapshot_stem!r}")
    return os.path.join(os.path.dirname(os.path.abspath(partial_path)), snapshot_stem)


def parse_root_spec(spec: str) -> tuple[str, str]:
    """解析 --root 'label=路径' 或 '路径'；默认 label＝根文件夹名且与盘符无关。"""
    if "=" in spec and not re.match(r"^[A-Za-z]:[\\/]", spec):
        label, _, path = spec.partition("=")
        label = label.strip()
    else:
        label, path = "", spec
    path = os.path.abspath(path.strip().rstrip("\\/") or path.strip())
    if not label:
        label = os.path.basename(path)
    if not label:
        raise PreflightError(f"root 无法取得默认 label（盘根？）：{spec!r}——请显式命名 label=路径")
    return label, path


def resolve_current_root_specs(labels: list[str],
                               specs: list[str]) -> dict[str, str]:
    """把当前根目录参数解析为快照 root label 到现路径的完整映射。"""
    if not specs:
        raise PreflightError("必须用 --root 指定当前档案根目录")

    known_labels = set(labels)
    mapping: dict[str, str] = {}
    direct_paths: list[str] = []
    for raw_spec in specs:
        spec = str(raw_spec or "").strip()
        if not spec:
            raise PreflightError("当前根目录不能为空")
        if "=" not in spec or os.path.isabs(spec.strip('"')):
            direct_paths.append(spec)
            continue
        label, _separator, path = spec.partition("=")
        label = label.strip()
        path = path.strip()
        if not label or not path:
            raise PreflightError(f"根目录映射应为 label=路径：{spec}")
        if label in mapping:
            raise PreflightError(f"根目录 label 重复：{label}")
        mapping[label] = path

    if direct_paths:
        if len(labels) != 1 or len(direct_paths) != 1 or mapping:
            raise PreflightError(
                "不带 label 的 --root 只适用于单根快照；"
                "多根快照请逐项使用 label=当前路径")
        mapping[labels[0]] = direct_paths[0]

    unknown = sorted(set(mapping) - known_labels)
    if unknown:
        raise PreflightError(
            "快照中不存在以下 root label：" + "、".join(unknown))
    missing = [label for label in labels if label not in mapping]
    if missing:
        raise PreflightError(
            "尚未指定以下当前根目录：" + "、".join(missing))

    return {
        label: os.path.abspath(
            os.path.expandvars(
                os.path.expanduser(path.strip().strip('"'))))
        for label, path in mapping.items()
    }


def validate_root(path: str) -> None:
    """root 必须是存在的档案根文件夹；拒绝直接扫描盘根。"""
    p = os.path.abspath(path)
    drive, tail = os.path.splitdrive(p)
    if tail in ("\\", "/", ""):
        raise PreflightError(f"盘根不接受为 root（扫描单位是档案根文件夹）：{p}")
    if not os.path.isdir(to_extended_path(p)):
        raise PreflightError(f"root 不存在或不是目录：{p}")


def parse_version_tuple(text: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", text)[:2])


def parse_ffprobe_version(banner: str) -> str:
    m = re.search(r"ffprobe version (\d+(?:\.\d+)*)", banner)
    if not m:
        raise PreflightError(f"无法解析 ffprobe 版本：{banner!r}")
    return m.group(1)


def parse_sevenzip_version(banner: str) -> str:
    m = re.search(r"7-Zip\s+(\d+\.\d+)", banner)
    if not m:
        raise PreflightError(f"无法解析 7-Zip 版本：{banner!r}")
    return m.group(1)


# === 快照数据库 DDL（精确定义以本处为准） ===
SNAPSHOT_DDL = r"""
PRAGMA foreign_keys = ON;

CREATE TABLE snapshot_info (
    id                     INTEGER PRIMARY KEY CHECK (id = 1),
    snapshot_uuid          TEXT    NOT NULL UNIQUE,
    schema_version         INTEGER NOT NULL,
    path_key_rule          INTEGER NOT NULL,
    scan_status            TEXT    NOT NULL CHECK (scan_status IN ('running','complete','interrupted')),
    database_integrity     TEXT    NOT NULL DEFAULT 'pending'
                           CHECK (database_integrity IN ('pending','ok','failed')),
    has_file_issues        INTEGER NOT NULL DEFAULT 0 CHECK (has_file_issues IN (0,1)),
    has_unstable_entries   INTEGER NOT NULL DEFAULT 0 CHECK (has_unstable_entries IN (0,1)),
    has_enumeration_gaps   INTEGER NOT NULL DEFAULT 0 CHECK (has_enumeration_gaps IN (0,1)),
    hash_coverage          TEXT    NOT NULL CHECK (hash_coverage IN ('none','incremental','full')),
    started_at_utc         TEXT    NOT NULL,
    finished_at_utc        TEXT,
    local_utc_offset_min   INTEGER NOT NULL,
    hostname               TEXT    NOT NULL,
    os_version             TEXT    NOT NULL,
    scanner_version        TEXT    NOT NULL,
    exiftool_version       TEXT,
    ffprobe_version        TEXT,
    sevenzip_version       TEXT,
    hash_algorithm         TEXT    NOT NULL DEFAULT 'sha256',
    hash_chunk_bytes       INTEGER,
    previous_snapshot_uuid TEXT,
    config_json            TEXT    NOT NULL,
    counts_json            TEXT
);

-- v1.4.1 状态契约为 schema_version=3，不读取更早结构。
CREATE TABLE snapshot_manifest (
    id                INTEGER PRIMARY KEY CHECK (id = 1),
    manifest_version  INTEGER NOT NULL,
    manifest_json     TEXT    NOT NULL,
    embedded_at_utc   TEXT    NOT NULL
);

CREATE TABLE run_events (
    event_seq          INTEGER PRIMARY KEY,
    occurred_at_utc    TEXT    NOT NULL,
    event              TEXT    NOT NULL,
    payload_json       TEXT    NOT NULL
);

CREATE TABLE roots (
    root_id       INTEGER PRIMARY KEY,
    root_path     TEXT    NOT NULL,
    root_label    TEXT    NOT NULL UNIQUE,
    volume_serial TEXT,
    filesystem    TEXT,
    bus_type      TEXT,
    enum_status   TEXT    NOT NULL DEFAULT 'pending'
                  CHECK (enum_status IN ('pending','ok','failed'))
);

CREATE TABLE dirs (
    dir_id          INTEGER PRIMARY KEY,
    root_id         INTEGER NOT NULL REFERENCES roots(root_id),
    rel_path        TEXT    NOT NULL,
    path_key        TEXT    NOT NULL,
    parent_dir_id   INTEGER REFERENCES dirs(dir_id),
    enum_status     TEXT    NOT NULL
                    CHECK (enum_status IN ('ok','access_denied','io_error',
                           'skipped_reparse','skipped_excluded','timeout','not_enumerated')),
    error_message   TEXT,
    file_count      INTEGER,
    subdir_count    INTEGER,
    attributes      INTEGER,
    observed_at_utc TEXT    NOT NULL,
    UNIQUE (root_id, rel_path)
);

CREATE INDEX idx_dirs_pathkey ON dirs(root_id, path_key);

CREATE TABLE entries (
    entry_id        INTEGER PRIMARY KEY,
    root_id         INTEGER NOT NULL REFERENCES roots(root_id),
    dir_id          INTEGER NOT NULL REFERENCES dirs(dir_id),
    rel_path        TEXT    NOT NULL,
    path_key        TEXT    NOT NULL,
    name            TEXT    NOT NULL,
    extension       TEXT    NOT NULL,
    media_kind      TEXT    NOT NULL
                    CHECK (media_kind IN ('photo_raw','photo_jpeg','image_gif','photo_working','video_mp4','video_crm','audio','archive','document','other')),
    size_bytes      INTEGER NOT NULL,
    created_at_utc  TEXT,
    modified_at_utc TEXT    NOT NULL,
    attributes      INTEGER NOT NULL,
    is_placeholder  INTEGER NOT NULL DEFAULT 0,
    hard_link_count INTEGER,
    volume_serial   TEXT,
    file_index_hex  TEXT,
    observed_at_utc TEXT    NOT NULL,
    meta_status     TEXT    NOT NULL DEFAULT 'pending'
                    CHECK (meta_status IN ('pending','processing','done','error',
                           'timeout','unstable','skipped','not_applicable')),
    hash_status     TEXT    NOT NULL DEFAULT 'pending'
                    CHECK (hash_status IN ('pending','processing','done','error',
                           'unstable','skipped')),
    UNIQUE (root_id, rel_path)
);

CREATE INDEX idx_entries_pathkey ON entries(root_id, path_key);
CREATE INDEX idx_entries_size    ON entries(size_bytes);
CREATE INDEX idx_entries_kind    ON entries(media_kind);
CREATE INDEX idx_entries_dir     ON entries(dir_id);
CREATE INDEX idx_entries_fileid  ON entries(volume_serial, file_index_hex);

CREATE TABLE photo_metadata (
    entry_id              INTEGER PRIMARY KEY REFERENCES entries(entry_id),
    capture_time_raw      TEXT,
    capture_time_source   TEXT,
    capture_tz_offset_min INTEGER,
    capture_time_utc      TEXT,
    camera_make           TEXT,
    camera_model          TEXT,
    camera_serial         TEXT,
    lens_model            TEXT,
    lens_serial           TEXT,
    width                 INTEGER,
    height                INTEGER,
    orientation           TEXT,
    iso                   INTEGER,
    f_number              REAL,
    exposure_time         TEXT,
    exposure_compensation REAL,
    focal_length_mm       REAL,
    focal_length_35mm     REAL,
    white_balance         TEXT,
    color_temperature     INTEGER,
    color_space           TEXT,
    icc_profile           TEXT,
    software              TEXT,
    bit_depth             INTEGER,
    gps_latitude          REAL,
    gps_longitude         REAL,
    gps_altitude          REAL,
    parser                TEXT    NOT NULL,
    parser_version        TEXT    NOT NULL,
    parsed_at_utc         TEXT    NOT NULL
);

CREATE TABLE video_metadata (
    entry_id              INTEGER PRIMARY KEY REFERENCES entries(entry_id),
    container_format      TEXT,
    duration_seconds      REAL,
    bit_rate              INTEGER,
    stream_count          INTEGER,
    timecode              TEXT,
    capture_time_raw      TEXT,
    capture_time_source   TEXT,
    capture_tz_offset_min INTEGER,
    capture_time_utc      TEXT,
    camera_make           TEXT,
    camera_model          TEXT,
    camera_serial         TEXT,
    lens_model            TEXT,
    lens_serial           TEXT,
    iso                   INTEGER,
    white_balance         TEXT,
    shutter               TEXT,
    gamma                 TEXT,
    color_gamut           TEXT,
    encoder               TEXT,
    title                 TEXT,
    author                TEXT,
    album                 TEXT,
    copyright             TEXT,
    parser                TEXT    NOT NULL,
    parser_version        TEXT    NOT NULL,
    parsed_at_utc         TEXT    NOT NULL
);

CREATE TABLE video_gps_points (
    gps_point_pk      INTEGER PRIMARY KEY,
    entry_id          INTEGER NOT NULL REFERENCES entries(entry_id),
    point_index       INTEGER NOT NULL CHECK (point_index >= 0),
    timestamp_seconds REAL CHECK (timestamp_seconds IS NULL OR
                                  timestamp_seconds >= 0),
    gps_latitude      REAL NOT NULL CHECK (gps_latitude BETWEEN -90.0 AND 90.0),
    gps_longitude     REAL NOT NULL CHECK (gps_longitude BETWEEN -180.0 AND 180.0),
    gps_altitude      REAL,
    source            TEXT NOT NULL CHECK (source <> ''),
    raw_value         TEXT NOT NULL CHECK (raw_value <> ''),
    UNIQUE (entry_id, source, point_index)
);

CREATE INDEX idx_video_gps_entry_time
    ON video_gps_points(entry_id, timestamp_seconds, point_index);

CREATE TABLE video_streams (
    stream_pk        INTEGER PRIMARY KEY,
    entry_id         INTEGER NOT NULL REFERENCES entries(entry_id),
    stream_index     INTEGER NOT NULL,
    codec_name       TEXT,
    codec_tag        TEXT,
    profile          TEXT,
    width            INTEGER,
    height           INTEGER,
    r_frame_rate     TEXT,
    avg_frame_rate   TEXT,
    pix_fmt          TEXT,
    bit_depth        INTEGER,
    color_space      TEXT,
    color_transfer   TEXT,
    color_primaries  TEXT,
    bit_rate         INTEGER,
    nb_frames        INTEGER,
    duration_seconds REAL,
    UNIQUE (entry_id, stream_index)
);

CREATE TABLE audio_streams (
    stream_pk        INTEGER PRIMARY KEY,
    entry_id         INTEGER NOT NULL REFERENCES entries(entry_id),
    stream_index     INTEGER NOT NULL,
    codec_name       TEXT,
    profile          TEXT,
    sample_rate      INTEGER,
    channels         INTEGER,
    channel_layout   TEXT,
    bit_rate         INTEGER,
    duration_seconds REAL,
    UNIQUE (entry_id, stream_index)
);

CREATE TABLE working_metadata (
    entry_id       INTEGER PRIMARY KEY REFERENCES entries(entry_id),
    file_variant   TEXT,
    creator_app    TEXT,
    color_mode     TEXT,
    bit_depth      INTEGER,
    width          INTEGER,
    height         INTEGER,
    layer_count    INTEGER,
    layer_names    TEXT,
    has_thumbnail  INTEGER,
    parser         TEXT    NOT NULL,
    parser_version TEXT    NOT NULL,
    parsed_at_utc  TEXT    NOT NULL
);

CREATE TABLE document_metadata (
    entry_id          INTEGER PRIMARY KEY REFERENCES entries(entry_id),
    doc_format        TEXT,
    title             TEXT,
    author            TEXT,
    last_modified_by  TEXT,
    creator_app       TEXT,
    created_prop_raw  TEXT,
    modified_prop_raw TEXT,
    page_count        INTEGER,
    is_encrypted      INTEGER,
    parser            TEXT    NOT NULL,
    parser_version    TEXT    NOT NULL,
    parsed_at_utc     TEXT    NOT NULL
);

CREATE TABLE archive_metadata (
    entry_id           INTEGER PRIMARY KEY REFERENCES entries(entry_id),
    archive_format     TEXT    NOT NULL,
    member_count       INTEGER,
    uncompressed_bytes INTEGER,
    compressed_bytes   INTEGER,
    has_encrypted      INTEGER,
    parser             TEXT    NOT NULL,
    parser_version     TEXT    NOT NULL,
    parsed_at_utc      TEXT    NOT NULL
);

CREATE TABLE archive_members (
    entry_id        INTEGER NOT NULL REFERENCES entries(entry_id),
    member_index    INTEGER NOT NULL,
    member_path     TEXT    NOT NULL,
    is_dir          INTEGER NOT NULL DEFAULT 0,
    size_bytes      INTEGER,
    packed_bytes    INTEGER,
    crc32_hex       TEXT,
    method          TEXT,
    flag_bits       INTEGER,
    host_os         TEXT,
    create_version  INTEGER,
    extract_version INTEGER,
    header_offset   INTEGER,
    modified_raw    TEXT,
    attributes      TEXT,
    encrypted       INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (entry_id, member_index)
);

CREATE TABLE raw_payloads (
    payload_pk         INTEGER PRIMARY KEY,
    entry_id           INTEGER NOT NULL REFERENCES entries(entry_id),
    provider           TEXT    NOT NULL CHECK (provider IN ('exiftool','ffprobe')),
    payload_zlib       BLOB    NOT NULL,
    payload_sha256     TEXT    NOT NULL,
    uncompressed_bytes INTEGER NOT NULL,
    provider_version   TEXT    NOT NULL,
    profile_version    INTEGER NOT NULL,
    parsed_at_utc      TEXT    NOT NULL,
    UNIQUE (entry_id, provider)
);

CREATE TABLE metadata_diagnostics (
    diagnostic_pk  INTEGER PRIMARY KEY,
    entry_id       INTEGER NOT NULL REFERENCES entries(entry_id),
    provider       TEXT    NOT NULL CHECK (provider <> ''),
    severity       TEXT    NOT NULL
                   CHECK (severity IN ('warning','error','validation')),
    diagnostic_code TEXT   NOT NULL CHECK (diagnostic_code <> ''),
    field_name     TEXT,
    message        TEXT    NOT NULL,
    raw_value      TEXT,
    observed_at_utc TEXT   NOT NULL
);

CREATE INDEX idx_metadata_diagnostics_entry
    ON metadata_diagnostics(entry_id, severity);

CREATE TABLE hashes (
    hash_pk                INTEGER PRIMARY KEY,
    entry_id               INTEGER NOT NULL REFERENCES entries(entry_id),
    algorithm              TEXT    NOT NULL DEFAULT 'sha256',
    hash_hex               TEXT,
    origin                 TEXT    NOT NULL CHECK (origin IN ('computed','reused')),
    source_snapshot_uuid   TEXT,
    source_computed_at_utc TEXT,
    reuse_basis            TEXT,
    size_bytes             INTEGER NOT NULL,
    bytes_read             INTEGER,
    chunk_bytes            INTEGER,
    started_at_utc         TEXT,
    finished_at_utc        TEXT,
    pre_size               INTEGER,
    pre_mtime_utc          TEXT,
    post_size              INTEGER,
    post_mtime_utc         TEXT,
    status                 TEXT    NOT NULL
                           CHECK (status IN ('valid','failed','unstable','skipped')),
    failure_reason         TEXT,
    tool                   TEXT    NOT NULL,
    tool_version           TEXT    NOT NULL,
    UNIQUE (entry_id, algorithm),
    CHECK (origin <> 'reused'
           OR (source_snapshot_uuid IS NOT NULL AND source_computed_at_utc IS NOT NULL)),
    CHECK (origin <> 'computed' OR status <> 'valid' OR bytes_read IS NOT NULL),
    CHECK (status <> 'valid' OR hash_hex IS NOT NULL)
);

CREATE INDEX idx_hashes_hex ON hashes(hash_hex);

CREATE TABLE errors (
    error_pk        INTEGER PRIMARY KEY,
    entry_id        INTEGER REFERENCES entries(entry_id),
    dir_id          INTEGER REFERENCES dirs(dir_id),
    stage           TEXT    NOT NULL
                    CHECK (stage IN ('precheck','enumerate','stat','metadata','hash','rescan','finalize')),
    error_code      TEXT    NOT NULL,
    message         TEXT,
    occurred_at_utc TEXT    NOT NULL
);

CREATE INDEX idx_errors_entry ON errors(entry_id);

CREATE VIEW v_file_manifest AS
SELECT e.entry_id, r.root_label, r.root_path, e.rel_path, e.name, e.extension,
       e.media_kind, e.size_bytes, e.created_at_utc, e.modified_at_utc,
       e.is_placeholder, e.meta_status, e.hash_status,
       h.hash_hex, h.origin AS hash_origin, h.status AS hash_record_status
FROM entries e
JOIN roots r ON r.root_id = e.root_id
LEFT JOIN hashes h ON h.entry_id = e.entry_id AND h.algorithm = 'sha256';

CREATE VIEW v_dir_problems AS
SELECT d.dir_id, r.root_label, d.rel_path, d.enum_status, d.error_message
FROM dirs d
JOIN roots r ON r.root_id = d.root_id
WHERE d.enum_status NOT IN ('ok');
"""


# === 文件属性位（Windows） ===
ATTR_REPARSE_POINT = 0x400
ATTR_PLACEHOLDER_MASK = 0x1000 | 0x40000 | 0x400000   # OFFLINE | RECALL_ON_OPEN | RECALL_ON_DATA_ACCESS

HASH_CHUNK_BYTES = 4 * 1024 * 1024


_FILENAME_SHA256_HIGH32_RE = re.compile(
    r"_([0-9A-F]{8})\.sqlite\Z")
_FILENAME_WITHOUT_FINGERPRINT_RE = re.compile(
    r"_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.\d{6}_"
    r"[0-9a-f]{8}\.sqlite\Z")


def filename_sha256_high32(snapshot_path: str) -> str | None:
    """读取末尾高 32 bit 指纹，并避免把无指纹产物的 runid 误认成它。"""
    basename = os.path.basename(snapshot_path)
    if _FILENAME_WITHOUT_FINGERPRINT_RE.search(basename):
        return None
    match = _FILENAME_SHA256_HIGH32_RE.search(basename)
    return match.group(1) if match else None


def filename_sha256_high32_matches(snapshot_path: str) -> bool | None:
    """无指纹返回 None；否则复算 SHA-256 并比较前 8 个十六进制字符。"""
    expected = filename_sha256_high32(snapshot_path)
    if expected is None:
        return None
    return sha256_file(snapshot_path)[:8].upper() == expected


# === ScanLock：partial 的唯一 owner ===
def _pid_alive(pid: int) -> bool:
    if sys.platform != "win32":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    import ctypes
    import ctypes.wintypes
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    kernel32 = ctypes.windll.kernel32
    open_process = kernel32.OpenProcess
    open_process.argtypes = (
        ctypes.wintypes.DWORD, ctypes.wintypes.BOOL,
        ctypes.wintypes.DWORD,
    )
    open_process.restype = ctypes.wintypes.HANDLE
    get_exit_code = kernel32.GetExitCodeProcess
    get_exit_code.argtypes = (
        ctypes.wintypes.HANDLE,
        ctypes.POINTER(ctypes.wintypes.DWORD),
    )
    get_exit_code.restype = ctypes.wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (ctypes.wintypes.HANDLE,)
    close_handle.restype = ctypes.wintypes.BOOL
    h = open_process(
        PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return False
    try:
        exit_code = ctypes.wintypes.DWORD()
        if not get_exit_code(h, ctypes.byref(exit_code)):
            # 查询失败时保守视为存活，避免错误接管仍可能活动的 owner。
            return True
        return int(exit_code.value) == STILL_ACTIVE
    finally:
        close_handle(h)


def _lock_path(partial_path: str) -> str:
    return partial_path + ".lock"


def acquire_scan_lock(partial_path: str, takeover: bool = False) -> None:
    """独占获取 partial 的所有权锁。takeover=True（--resume）时允许接管
    已死 owner 的锁；owner 仍存活则拒绝。"""
    lp = _lock_path(partial_path)
    payload = json.dumps({"run_uuid": uuid.uuid4().hex,
                          "host": socket.gethostname(), "pid": os.getpid(),
                          "acquired_at_utc": now_utc_iso()},
                         ensure_ascii=False)
    try:
        fd = os.open(lp, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(payload + "\n")
        return
    except FileExistsError:
        pass
    try:
        with open(lp, encoding="utf-8") as f:
            owner = json.loads(f.read())
        owner_pid = int(owner.get("pid", -1))
    except (OSError, ValueError):
        owner_pid = -1                    # 锁文件损坏视为可疑残留
    if owner_pid > 0 and _pid_alive(owner_pid):
        raise PreflightError(
            f"该 partial 正被进程 {owner_pid} 持有（ScanLock）：{lp}")
    if not takeover:
        raise PreflightError(
            f"存在残留 ScanLock（owner 已失效）：{lp}\n"
            f"  确认无其他扫描后可用 --resume 接管，或删除该 .lock 后重试")
    with open(lp, "w", encoding="utf-8", newline="\n") as f:
        f.write(payload + "\n")           # 接管：owner 已证实失效


def release_scan_lock(partial_path: str) -> None:
    try:
        os.remove(_lock_path(partial_path))
    except OSError:
        pass


def sha256_file(path: str, chunk: int = HASH_CHUNK_BYTES) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def volume_info(path: str) -> tuple[str | None, str | None]:
    """(卷序列号 hex＝st_dev 来源, 文件系统名)。fs 名经 GetVolumeInformationW，失败为 None。"""
    serial = None
    try:
        serial = id_hex(os.stat(to_extended_path(path)).st_dev)
    except OSError:
        pass
    fs_name = None
    if sys.platform == "win32":
        try:
            import ctypes
            drive = os.path.splitdrive(os.path.abspath(path))[0] + "\\"
            buf = ctypes.create_unicode_buffer(64)
            ok = ctypes.windll.kernel32.GetVolumeInformationW(
                ctypes.c_wchar_p(drive), None, 0, None, None, None, buf, 64)
            if ok:
                fs_name = buf.value or None
        except Exception:
            pass
    return serial, fs_name


def create_partial_snapshot(partial_path: str,
                            roots: list[tuple[str, str]],
                            config: dict,
                            tool_versions: dict | None = None) -> sqlite3.Connection:
    """建 partial 快照库：DDL＋snapshot_info(running)＋roots。roots=[(label, path), ...]。"""
    labels = [lb for lb, _ in roots]
    paths = [os.path.normcase(os.path.abspath(p)) for _, p in roots]
    if len(set(labels)) != len(labels):
        raise PreflightError(f"root label 重复：{labels}")
    if len(set(paths)) != len(paths):
        raise PreflightError("root 路径重复")
    for _, p in roots:
        validate_root(p)
    config = dict(config)
    config.setdefault("metadata_storage", "complete")
    config.setdefault("filename_layout_version", FILENAME_LAYOUT_VERSION)
    config["data_contract"] = DATA_CONTRACT
    config["min_reader_version"] = MIN_READER_VERSION
    tv = tool_versions or {}
    # partial 独占预留：路径已存在立即失败，绝不打开续用
    try:
        fd = os.open(partial_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except FileExistsError:
        raise PreflightError(
            f"partial 已存在（并发运行或中断残留）：{partial_path}\n"
            f"  确认无其他扫描后，用 --resume 续传或删除该组残留后重试")
    acquire_scan_lock(partial_path)
    con = sqlite3.connect(partial_path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.executescript(SNAPSHOT_DDL)
    con.execute(
        "INSERT INTO snapshot_info (id, snapshot_uuid, schema_version, path_key_rule,"
        " scan_status, hash_coverage, started_at_utc, local_utc_offset_min, hostname,"
        " os_version, scanner_version, exiftool_version, ffprobe_version,"
        " sevenzip_version, hash_algorithm, hash_chunk_bytes, config_json)"
        " VALUES (1, ?, ?, ?, 'running', 'none', ?, ?, ?, ?, ?, ?, ?, ?, 'sha256', ?, ?)",
        (uuid.uuid4().hex, SCHEMA_VERSION, PATH_KEY_RULE, now_utc_iso(),
         local_utc_offset_min(), socket.gethostname(), platform.platform(),
         SCANNER_VERSION, tv.get("exiftool"), tv.get("ffprobe"), tv.get("sevenzip"),
         HASH_CHUNK_BYTES, json.dumps(config, ensure_ascii=False)))
    for i, (label, path) in enumerate(roots, start=1):
        serial, fs_name = volume_info(path)
        con.execute("INSERT INTO roots (root_id, root_path, root_label, volume_serial,"
                    " filesystem) VALUES (?, ?, ?, ?, ?)",
                    (i, os.path.abspath(path), label, serial, fs_name))
    con.commit()
    return con


def _parent_rel(rel_dir):
    if rel_dir == "":
        return None
    head, _, _ = rel_dir.rpartition("\\")
    return head        # 顶层子目录的父即根 ""


def enumerate_and_reconcile(con: sqlite3.Connection,
                            collect_file_id: bool = True,
                            exclude_paths: set | None = None,
                            exclude_dirs: set | None = None,
                            on_progress=None,
                            max_files: int | None = None,
                            should_stop=None) -> dict:
    """流式枚举全部 root → 与既有登记对账（可重跑；size/mtime 变者重置状态）。

    exclude_dirs：整子树排除，防止位于 root 内的当次输出目录被重新登记。
    仅限本工具自身产物目录——档案内容一律无差别登记，不做模式排除。
    max_files 为内部测试钩子：达到即抛 KeyboardInterrupt 模拟中断。
    """
    exclude = {os.path.normcase(p) for p in (exclude_paths or set())}
    exdirs = {os.path.normcase(os.path.abspath(p))
              for p in (exclude_dirs or set())}
    con.execute("DROP TABLE IF EXISTS t_dirs")
    con.execute("DROP TABLE IF EXISTS t_entries")
    con.execute("CREATE TEMP TABLE t_dirs (root_id INT, rel_path TEXT, path_key TEXT,"
                " parent_rel TEXT, enum_status TEXT, error_message TEXT,"
                " file_count INT, subdir_count INT, attributes INT,"
                " observed_at_utc TEXT)")
    con.execute("CREATE TEMP TABLE t_entries (root_id INT, dir_rel TEXT, rel_path TEXT,"
                " path_key TEXT, name TEXT, extension TEXT, media_kind TEXT,"
                " size_bytes INT, created_at_utc TEXT, modified_at_utc TEXT,"
                " attributes INT, is_placeholder INT, hard_link_count INT,"
                " volume_serial TEXT, file_index_hex TEXT, observed_at_utc TEXT)")
    stats = {"files": 0, "dirs": 0, "dir_errors": 0, "placeholders": 0, "bytes": 0}
    d_buf = []
    e_buf = []

    def flush():
        if d_buf:
            con.executemany("INSERT INTO t_dirs VALUES (?,?,?,?,?,?,?,?,?,?)", d_buf)
            d_buf.clear()
        if e_buf:
            con.executemany(
                "INSERT INTO t_entries VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", e_buf)
            e_buf.clear()

    interrupted = None
    for root_id, root_path in con.execute(
            "SELECT root_id, root_path FROM roots ORDER BY root_id").fetchall():
        root_ok = True
        stack = [("", to_extended_path(root_path))]
        try:
            while stack:
                if should_stop is not None and should_stop():
                    raise StageControlBoundary(
                        "enumerate controlled stage boundary")
                rel_dir, ext_dir = stack.pop()
                try:
                    dir_attrs = os.stat(ext_dir, follow_symlinks=False).st_file_attributes
                except OSError:
                    dir_attrs = None
                try:
                    it = list(os.scandir(ext_dir))
                except PermissionError as exc:
                    d_buf.append((root_id, rel_dir, make_path_key(rel_dir),
                                  _parent_rel(rel_dir), "access_denied", str(exc),
                                  None, None, dir_attrs, now_utc_iso()))
                    stats["dir_errors"] += 1
                    if rel_dir == "":
                        root_ok = False
                    continue
                except OSError as exc:
                    d_buf.append((root_id, rel_dir, make_path_key(rel_dir),
                                  _parent_rel(rel_dir), "io_error", str(exc),
                                  None, None, dir_attrs, now_utc_iso()))
                    stats["dir_errors"] += 1
                    if rel_dir == "":
                        root_ok = False
                    continue
                files = subdirs = 0
                for entry in it:
                    if should_stop is not None and should_stop():
                        raise StageControlBoundary(
                            "enumerate controlled stage boundary")
                    rel = entry.name if rel_dir == "" else rel_dir + "\\" + entry.name
                    try:
                        st = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    attrs = getattr(st, "st_file_attributes", 0)
                    if entry.is_dir(follow_symlinks=False):
                        if (exdirs and os.path.normcase(
                                os.path.join(root_path, rel)) in exdirs):
                            continue    # 本工具输出目录子树不入清点
                        subdirs += 1
                        if attrs & ATTR_REPARSE_POINT:      # 不跟随联接点/符号链接
                            d_buf.append((root_id, rel, make_path_key(rel), rel_dir,
                                          "skipped_reparse", None, None, None,
                                          attrs, now_utc_iso()))
                            continue
                        stack.append((rel, ext_dir + "\\" + entry.name))
                        continue
                    full_norm = os.path.normcase(os.path.join(root_path, rel))
                    if full_norm in exclude:
                        continue
                    files += 1
                    placeholder = 1 if attrs & ATTR_PLACEHOLDER_MASK else 0
                    vol = fid = None
                    nlink = None
                    created = getattr(st, "st_birthtime_ns", 0) or st.st_ctime_ns
                    if collect_file_id and not placeholder:
                        try:
                            st2 = os.stat(ext_dir + "\\" + entry.name,
                                          follow_symlinks=False)
                            vol, fid = id_hex(st2.st_dev), id_hex(st2.st_ino)
                            nlink = st2.st_nlink or None
                        except OSError:
                            pass
                    e_buf.append((root_id, rel_dir, rel, make_path_key(rel),
                                  entry.name, extension_of(entry.name),
                                  media_kind_for(extension_of(entry.name)),
                                  st.st_size,
                                  ns_to_utc_iso(created) if created else None,
                                  ns_to_utc_iso(st.st_mtime_ns), attrs, placeholder,
                                  nlink, vol, fid, now_utc_iso()))
                    stats["files"] += 1
                    stats["bytes"] += st.st_size
                    stats["placeholders"] += placeholder
                    if on_progress and stats["files"] % 500 == 0:
                        on_progress(stats)
                    if max_files is not None and stats["files"] >= max_files:
                        raise KeyboardInterrupt("test hook: max_files")
                d_buf.append((root_id, rel_dir, make_path_key(rel_dir),
                              _parent_rel(rel_dir), "ok", None, files, subdirs,
                              dir_attrs, now_utc_iso()))
                stats["dirs"] += 1
                if len(d_buf) + len(e_buf) >= 1000:
                    flush()
        except (KeyboardInterrupt, StageControlBoundary) as exc:
            interrupted = exc
        flush()
        con.execute("UPDATE roots SET enum_status=? WHERE root_id=?",
                    ("failed" if not root_ok else
                     "pending" if interrupted is not None else "ok", root_id))
        if interrupted is not None:
            break
    flush()
    if interrupted is not None:
        con.commit()
        raise interrupted

    con.execute("""
        INSERT INTO dirs (root_id, rel_path, path_key, enum_status, error_message,
                          file_count, subdir_count, attributes, observed_at_utc)
        SELECT root_id, rel_path, path_key, enum_status, error_message,
               file_count, subdir_count, attributes, observed_at_utc FROM t_dirs
        WHERE true
        ON CONFLICT(root_id, rel_path) DO UPDATE SET
          path_key=excluded.path_key, enum_status=excluded.enum_status,
          error_message=excluded.error_message, file_count=excluded.file_count,
          subdir_count=excluded.subdir_count, attributes=excluded.attributes,
          observed_at_utc=excluded.observed_at_utc""")
    con.execute("""
        UPDATE dirs SET parent_dir_id = (
            SELECT p.dir_id FROM dirs p
            WHERE p.root_id = dirs.root_id AND p.rel_path = (
                SELECT t.parent_rel FROM t_dirs t
                WHERE t.root_id = dirs.root_id AND t.rel_path = dirs.rel_path))""")
    con.execute("""DELETE FROM entries WHERE NOT EXISTS (
        SELECT 1 FROM t_entries t
        WHERE t.root_id = entries.root_id AND t.rel_path = entries.rel_path)""")
    con.execute("""DELETE FROM dirs WHERE NOT EXISTS (
        SELECT 1 FROM t_dirs t
        WHERE t.root_id = dirs.root_id AND t.rel_path = dirs.rel_path)""")
    con.execute("""
        INSERT INTO entries (root_id, dir_id, rel_path, path_key, name, extension,
                             media_kind, size_bytes, created_at_utc, modified_at_utc,
                             attributes, is_placeholder, hard_link_count,
                             volume_serial, file_index_hex, observed_at_utc)
        SELECT t.root_id, d.dir_id, t.rel_path, t.path_key, t.name, t.extension,
               t.media_kind, t.size_bytes, t.created_at_utc, t.modified_at_utc,
               t.attributes, t.is_placeholder, t.hard_link_count,
               t.volume_serial, t.file_index_hex, t.observed_at_utc
        FROM t_entries t JOIN dirs d
          ON d.root_id = t.root_id AND d.rel_path = t.dir_rel
        WHERE true
        ON CONFLICT(root_id, rel_path) DO UPDATE SET
          meta_status = CASE WHEN entries.size_bytes <> excluded.size_bytes
                              OR entries.modified_at_utc <> excluded.modified_at_utc
                             THEN 'pending' ELSE entries.meta_status END,
          hash_status = CASE WHEN entries.size_bytes <> excluded.size_bytes
                              OR entries.modified_at_utc <> excluded.modified_at_utc
                             THEN 'pending' ELSE entries.hash_status END,
          dir_id=excluded.dir_id, path_key=excluded.path_key, name=excluded.name,
          extension=excluded.extension, media_kind=excluded.media_kind,
          size_bytes=excluded.size_bytes, created_at_utc=excluded.created_at_utc,
          modified_at_utc=excluded.modified_at_utc, attributes=excluded.attributes,
          is_placeholder=excluded.is_placeholder,
          hard_link_count=excluded.hard_link_count,
          volume_serial=excluded.volume_serial,
          file_index_hex=excluded.file_index_hex,
          observed_at_utc=excluded.observed_at_utc""")
    con.execute("DROP TABLE t_dirs")
    con.execute("DROP TABLE t_entries")
    con.commit()
    return stats


def rescan_check(
    con: sqlite3.Connection,
    *,
    should_stop=None,
    on_progress=None,
) -> int:
    """全量复扫 size/mtime，与登记比对；变化或消失者标 unstable。返回变化数。"""
    roots = dict(con.execute("SELECT root_id, root_path FROM roots").fetchall())
    changed_ids = []
    if should_stop is not None and should_stop():
        raise StageControlBoundary("rescan controlled stage boundary")
    total = None
    if on_progress is not None:
        total = con.execute(
            "SELECT COUNT(*) FROM entries WHERE is_placeholder = 0"
        ).fetchone()[0]

    def commit_changes() -> None:
        con.executemany("UPDATE entries SET meta_status='unstable',"
                        " hash_status='unstable' WHERE entry_id=?",
                        [(entry_id,) for entry_id in changed_ids])
        con.commit()

    cursor = con.execute(
        "SELECT entry_id, root_id, rel_path, size_bytes, modified_at_utc"
        " FROM entries WHERE is_placeholder = 0"
    )
    try:
        for index, (entry_id, root_id, rel, size, mtime) in enumerate(
                cursor, 1):
            if should_stop is not None and should_stop():
                active_cursor = cursor
                cursor = None
                active_cursor.close()
                commit_changes()
                raise StageControlBoundary(
                    "rescan controlled stage boundary")
            try:
                st = os.stat(
                    to_extended_path(os.path.join(roots[root_id], rel)),
                    follow_symlinks=False,
                )
                if st.st_size != size \
                        or ns_to_utc_iso(st.st_mtime_ns) != mtime:
                    changed_ids.append(entry_id)
            except OSError:
                changed_ids.append(entry_id)
            if on_progress is not None:
                on_progress(index, total, len(changed_ids))
    finally:
        if cursor is not None:
            cursor.close()
    commit_changes()
    if should_stop is not None and should_stop():
        raise StageControlBoundary("rescan controlled stage boundary")
    return len(changed_ids)


def effective_snapshot_profile(
    con: sqlite3.Connection, hash_coverage: str,
) -> dict:
    """从实际数据库内容生成面向用户的有效能力说明。"""
    config_text, = con.execute(
        "SELECT config_json FROM snapshot_info WHERE id=1").fetchone()
    config = json.loads(config_text)
    scan_kind = str(config.get("phase") or "full")
    hash_mode = str(config.get("hash") or hash_coverage)
    metadata_storage = config.get("metadata_storage")
    if metadata_storage not in ("complete", "normalized"):
        raise PreflightError(
            f"metadata_storage 配置无效：{metadata_storage!r}")
    raw_payload = scan_kind == "full" and metadata_storage == "complete"
    file_id = not bool(config.get("no_file_id", False))
    profile = {
        "scan_kind": scan_kind,
        "hash_mode_requested": hash_mode,
        "hash_coverage_actual": hash_coverage,
        "metadata_storage": metadata_storage,
        "raw_payload_retained": raw_payload,
        "raw_payload_rows": con.execute(
            "SELECT COUNT(*) FROM raw_payloads").fetchone()[0],
        "file_id_collected": file_id,
        "file_id_rows": con.execute(
            "SELECT COUNT(*) FROM entries"
            " WHERE volume_serial IS NOT NULL AND file_index_hex IS NOT NULL"
        ).fetchone()[0],
        "hash_valid_rows": con.execute(
            "SELECT COUNT(*) FROM hashes WHERE status='valid'"
        ).fetchone()[0],
        "entries_total": con.execute(
            "SELECT COUNT(*) FROM entries").fetchone()[0],
        "filename_tokens": snapshot_profile_tokens(
            scan_kind, hash_mode=hash_mode,
            raw_payload=raw_payload, file_id=file_id),
    }
    if config.get("profile_version") is not None:
        profile["metadata_profile_version"] = config["profile_version"]
    return profile


def _read_run_events(path: str | None) -> list[dict]:
    if not path or not os.path.isfile(path):
        return []
    records: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise PreflightError(
                    f"事件日志第 {line_no} 行不是有效 JSON：{path}") from exc
            if not isinstance(record, dict) or not record.get("event"):
                raise PreflightError(
                    f"事件日志第 {line_no} 行缺少 event：{path}")
            records.append(record)
    return records


def _embed_snapshot_evidence(
    con: sqlite3.Connection, final_stem_path: str, hash_coverage: str,
    counts: dict, finished_at_utc: str, event_log_path: str | None,
    manifest: dict | None,
) -> None:
    """把成功运行的 manifest 与事件时间线封入 SQLite。"""
    records = _read_run_events(event_log_path)
    con.execute("DELETE FROM run_events")
    for seq, record in enumerate(records, 1):
        payload = {
            key: value for key, value in record.items()
            if key not in ("ts", "event")
        }
        con.execute(
            "INSERT INTO run_events"
            " (event_seq,occurred_at_utc,event,payload_json)"
            " VALUES (?,?,?,?)",
            (seq, str(record.get("ts") or finished_at_utc),
             str(record["event"]),
             json.dumps(payload, ensure_ascii=False)),
        )
    snapshot_stem = os.path.basename(final_stem_path)
    filename_pattern = snapshot_stem + "_<SHA256-high32-uppercase>.sqlite"
    final_event = {
        "snapshot_stem": snapshot_stem,
        "filename_pattern": filename_pattern,
        "hash_storage": "filename_suffix_sha256_high32",
    }
    con.execute(
        "INSERT INTO run_events"
        " (event_seq,occurred_at_utc,event,payload_json) VALUES (?,?,?,?)",
        (len(records) + 1, finished_at_utc, "snapshot_sealed",
         json.dumps(final_event, ensure_ascii=False)),
    )

    (snapshot_uuid, schema_version, path_key_rule, started_at_utc,
     scanner_version, config_text) = con.execute(
        "SELECT snapshot_uuid,schema_version,path_key_rule,started_at_utc,"
        " scanner_version,config_json FROM snapshot_info WHERE id=1"
    ).fetchone()
    config = json.loads(config_text)
    labels = [
        row[0] for row in con.execute(
            "SELECT root_label FROM roots ORDER BY root_id")]
    filename_layout_version = config.get(
        "filename_layout_version", FILENAME_LAYOUT_VERSION)
    document = dict(manifest or {})
    document.update({
        "manifest_version": 1,
        "snapshot_stem": snapshot_stem,
        "snapshot_filename_pattern": filename_pattern,
        "filename_layout_version": filename_layout_version,
        "snapshot_uuid": snapshot_uuid,
        "schema_version": schema_version,
        "data_contract": config.get("data_contract", DATA_CONTRACT),
        "min_reader_version": config.get(
            "min_reader_version", MIN_READER_VERSION),
        "path_key_rule": path_key_rule,
        "scanner_version": scanner_version,
        "started_at_utc": started_at_utc,
        "finished_at_utc": finished_at_utc,
        "root_labels": labels,
        "integrity": {
            "algorithm": "sha256",
            "storage": "filename_suffix_sha256_high32",
            "token_format": "<first-8-hex-uppercase>",
            "bit_selection": "most_significant_32",
            "hex_case": "upper",
            "retained_bits": 32,
            "full_digest_retained": False,
        },
        "config": config,
        "effective_profile": effective_snapshot_profile(con, hash_coverage),
        "counts": counts,
        "status": {
            "database_integrity": counts["database_integrity"],
            "scan_status": counts["scan_status"],
            "has_file_issues": counts["has_file_issues"],
            "has_unstable_entries": counts["has_unstable_entries"],
            "has_enumeration_gaps": counts["has_enumeration_gaps"],
        },
        "events": {
            "storage": "run_events",
            "count": len(records) + 1,
        },
    })
    if config.get("profile_version") is not None:
        document["normalized_metadata_profile_version"] = \
            config["profile_version"]
    # 数据库不能在自身内部保存最终字节哈希；文件名只保留 SHA-256 高 32 bit。
    document.pop("snapshot_sha256", None)
    document.pop("external_sha256", None)
    con.execute("DELETE FROM snapshot_manifest")
    con.execute(
        "INSERT INTO snapshot_manifest"
        " (id,manifest_version,manifest_json,embedded_at_utc)"
        " VALUES (1,1,?,?)",
        (json.dumps(document, ensure_ascii=False), finished_at_utc),
    )


def finalize_snapshot(con: sqlite3.Connection, partial_path: str,
                      hash_coverage: str, *,
                      publish_stem_path: str | None = None,
                      manifest: dict | None = None,
                      event_log_path: str | None = None) -> str:
    """封存：残留检查→状态→嵌入证据→integrity→关闭→指纹命名发布。"""
    if hash_coverage not in ("none", "incremental", "full"):
        raise PreflightError(
            "hash_coverage 必须是 none、incremental 或 full")
    if not partial_path.endswith(".partial.sqlite"):
        raise PreflightError(f"partial 命名不符：{partial_path}")
    residue, = con.execute(
        "SELECT COUNT(*) FROM entries WHERE meta_status IN ('pending','processing')"
        " OR hash_status IN ('pending','processing')").fetchone()
    if residue:
        raise PreflightError(f"封存被拒：存在 {residue} 个 pending/processing 残留条目")
    counts = collect_snapshot_counts(con)
    has_issues = snapshot_issue_report_required(counts)
    working_stem_path = partial_path[:-len(".partial.sqlite")]
    publish_stem_path = os.path.abspath(
        publish_stem_path or working_stem_path)
    partial_dir = os.path.normcase(
        os.path.dirname(os.path.abspath(partial_path)))
    if (os.path.normcase(os.path.dirname(publish_stem_path)) != partial_dir
            or not os.path.basename(publish_stem_path)):
        raise PreflightError(
            "publish_stem_path 必须位于 partial 同一目录且不能包含子目录")
    final_stem_path = publish_stem_path
    finished_at_utc = now_utc_iso()
    con.execute(
        "UPDATE snapshot_info SET scan_status='complete',"
        " database_integrity='ok',has_file_issues=?,"
        " has_unstable_entries=?,has_enumeration_gaps=?,"
        " finished_at_utc=?,hash_coverage=?,counts_json=?",
        (int(counts["has_file_issues"]),
         int(counts["has_unstable_entries"]),
         int(counts["has_enumeration_gaps"]),
         finished_at_utc, hash_coverage,
         json.dumps(counts, ensure_ascii=False)))
    _embed_snapshot_evidence(
        con, final_stem_path, hash_coverage, counts, finished_at_utc,
        event_log_path, manifest)
    con.commit()
    ok, = con.execute("PRAGMA integrity_check").fetchone()
    if ok != "ok":
        raise PreflightError(f"SQLite 完整性检查失败：{ok}")
    fk_error = con.execute("PRAGMA foreign_key_check").fetchone()
    if fk_error is not None:
        raise PreflightError(f"SQLite 外键检查失败：{tuple(fk_error)}")
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    # 封存件切回 DELETE journal 模式，避免后续读取在不可变快照旁生成
    # -shm/-wal 辅助文件
    con.execute("PRAGMA journal_mode=DELETE")
    con.close()
    # 文件关闭后字节才最终稳定；把 SHA-256 前 8 个十六进制字符大写后置。
    digest = sha256_file(partial_path)
    final_path = final_stem_path + f"_{digest[:8].upper()}.sqlite"
    issue_markdown = (render_snapshot_issue_report(
        partial_path, os.path.basename(final_path)) if has_issues else None)
    publish_sqlite_artifact(partial_path, final_path, issue_markdown)
    release_scan_lock(partial_path)
    return final_path


def _table_exists(con: sqlite3.Connection, table: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def ensure_metadata_diagnostics_table(con: sqlite3.Connection) -> None:
    """确保当前扫描库存在元数据诊断表。"""
    con.execute(
        "CREATE TABLE IF NOT EXISTS metadata_diagnostics ("
        " diagnostic_pk INTEGER PRIMARY KEY,"
        " entry_id INTEGER NOT NULL REFERENCES entries(entry_id),"
        " provider TEXT NOT NULL CHECK (provider <> ''),"
        " severity TEXT NOT NULL"
        "  CHECK (severity IN ('warning','error','validation')),"
        " diagnostic_code TEXT NOT NULL CHECK (diagnostic_code <> ''),"
        " field_name TEXT, message TEXT NOT NULL, raw_value TEXT,"
        " observed_at_utc TEXT NOT NULL)"
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_metadata_diagnostics_entry"
        " ON metadata_diagnostics(entry_id, severity)"
    )


def collect_snapshot_counts(con: sqlite3.Connection) -> dict:
    """生成封存计数和彼此独立的 v1.4.1 状态。"""
    counts = {
        "dirs": con.execute("SELECT COUNT(*) FROM dirs").fetchone()[0],
        "entries": con.execute("SELECT COUNT(*) FROM entries").fetchone()[0],
        "bytes": con.execute(
            "SELECT COALESCE(SUM(size_bytes),0) FROM entries").fetchone()[0],
        "by_kind": dict(con.execute(
            "SELECT media_kind, COUNT(*) FROM entries GROUP BY media_kind")),
        "meta_status": dict(con.execute(
            "SELECT meta_status, COUNT(*) FROM entries GROUP BY meta_status")),
        "hash_status": dict(con.execute(
            "SELECT hash_status, COUNT(*) FROM entries GROUP BY hash_status")),
        "dir_errors": con.execute(
            "SELECT COUNT(*) FROM dirs WHERE enum_status NOT IN ('ok')").fetchone()[0],
        "roots_failed": con.execute(
            "SELECT COUNT(*) FROM roots WHERE enum_status='failed'").fetchone()[0],
        "error_records": con.execute(
            "SELECT COUNT(*) FROM errors").fetchone()[0],
    }
    if _table_exists(con, "metadata_diagnostics"):
        counts["metadata_diagnostics"] = dict(con.execute(
            "SELECT severity, COUNT(*) FROM metadata_diagnostics"
            " GROUP BY severity"))
    meta_status = counts["meta_status"]
    hash_status = counts["hash_status"]
    counts["database_integrity"] = "ok"
    counts["scan_status"] = "complete"
    counts["has_file_issues"] = bool(
        meta_status.get("error") or meta_status.get("timeout"))
    counts["has_unstable_entries"] = bool(
        meta_status.get("unstable") or hash_status.get("unstable"))
    counts["has_enumeration_gaps"] = bool(
        counts["dir_errors"] or counts["roots_failed"])
    return counts


def snapshot_issue_report_required(counts: dict) -> bool:
    """源文件／扫描证据有问题时生成报告；不代表 SQLite 损坏。"""
    return bool(
        counts.get("has_file_issues")
        or counts.get("has_unstable_entries")
        or counts.get("has_enumeration_gaps")
        or (counts.get("hash_status") or {}).get("error"))


ISSUE_REPORT_SUFFIX = "_Issues.md"
ISSUE_REPORT_ROW_LIMIT = 500
_NON_ISSUE_FORMAT_MESSAGES = frozenset((
    "unknown file type",
    "unrecognized file type",
    "unsupported file type",
))


def issue_record_is_visible(error_code: object, message: object) -> bool:
    """Issues.md 忽略单纯格式未识别；数据库原始证据保持不变。"""
    if str(error_code or "").strip().casefold() != "exiftool_reported_error":
        return True
    normalized = " ".join(str(message or "").strip().casefold().split())
    normalized = normalized.rstrip(".。")
    return normalized not in _NON_ISSUE_FORMAT_MESSAGES


def artifact_issue_report_path(artifact_path: str) -> str:
    """返回 SQLite 产物同目录的问题报告路径。"""
    stem, extension = os.path.splitext(os.path.abspath(artifact_path))
    if extension.casefold() != ".sqlite":
        raise PreflightError(f"问题报告只支持 SQLite 产物：{artifact_path}")
    return stem + ISSUE_REPORT_SUFFIX


def markdown_cell(value, max_chars: int = 500) -> str:
    """把数据库文本安全压平为 Markdown 表格单元格。"""
    if value is None:
        return ""
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\n", "<br>").replace("|", "\\|")
    if len(text) > max_chars:
        text = text[:max_chars - 1] + "…"
    return text


def _write_text_exclusive(path: str, content: str) -> None:
    """以 UTF-8、LF、no-clobber 方式创建文本文件。"""
    fd = None
    created = False
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        created = True
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            fd = None
            handle.write(content.replace("\r\n", "\n").replace("\r", "\n"))
    except Exception:
        if fd is not None:
            os.close(fd)
        if created:
            try:
                os.remove(path)
            except OSError:
                pass
        raise


def publish_sqlite_artifact(working_path: str, final_path: str,
                            issue_markdown: str | None = None) -> str | None:
    """原子 no-replace 发布 SQLite，并可同时创建同目录 Issues.md。

    两个最终目标中的任一个已存在都会拒绝发布。报告先以 O_EXCL 创建；若
    SQLite 原子改名失败，只清理由本次调用新建的报告，工作数据库仍保留。
    """
    working_path = os.path.abspath(working_path)
    final_path = os.path.abspath(final_path)
    if os.path.exists(final_path):
        raise PreflightError(
            f"发布冲突：目标已存在，旧产物保持不动：{final_path}\n"
            f"  本次运行结果保留于：{working_path}")
    issue_path = (artifact_issue_report_path(final_path)
                  if issue_markdown is not None else None)
    report_created = False
    if issue_path:
        try:
            _write_text_exclusive(issue_path, issue_markdown)
            report_created = True
        except FileExistsError as exc:
            raise PreflightError(
                f"发布冲突：问题报告已存在，旧产物保持不动：{issue_path}\n"
                f"  本次运行结果保留于：{working_path}") from exc
        except OSError as exc:
            raise PreflightError(
                f"问题报告无法创建：{issue_path}：{exc}\n"
                f"  本次运行结果保留于：{working_path}") from exc
    try:
        # Windows 的 os.rename 目标存在即 FileExistsError；不使用覆盖语义。
        os.rename(working_path, final_path)
    except OSError as exc:
        if report_created:
            try:
                os.remove(issue_path)
            except OSError:
                pass
        raise PreflightError(
            f"发布失败，目标保持不动：{final_path}：{exc}\n"
            f"  本次运行结果保留于：{working_path}") from exc
    return issue_path


def _append_markdown_table(lines: list[str], headers: tuple[str, ...],
                           rows: list[tuple]) -> None:
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join("---" for _ in headers) + " |")
    for row in rows:
        lines.append("| " + " | ".join(markdown_cell(v) for v in row) + " |")


def render_snapshot_issue_report(
    snapshot_path: str,
    artifact_filename: str | None = None,
    row_limit: int = ISSUE_REPORT_ROW_LIMIT,
) -> str | None:
    """从封存前 SQLite 生成可读问题摘要，不修改数据库。"""
    path = os.path.abspath(snapshot_path)
    # 局部导入避免 Core／Reader 的模块初始化环，同时让 Issues 与其它消费者
    # 共用同一类型、schema、封存状态和能力探测入口。
    import Script_DAISY_Lib_DBS_05_Reader as dbreader
    con, descriptor = dbreader.open_database(
        path, expected_type="snapshot")
    try:
        dbreader.require_capabilities(
            descriptor, "overview", "issues", "diagnostics")
    except Exception:
        con.close()
        raise
    con.row_factory = sqlite3.Row
    try:
        info = con.execute(
            "SELECT snapshot_uuid,scanner_version,scan_status,"
            " database_integrity,has_file_issues,has_unstable_entries,"
            " has_enumeration_gaps,counts_json"
            " FROM snapshot_info WHERE id=1").fetchone()
        if info is None:
            raise PreflightError("无法生成问题报告：snapshot_info 缺失")
        counts = json.loads(info["counts_json"] or "{}")
        meta = counts.get("meta_status") or {}
        hashes = counts.get("hash_status") or {}
        diagnostics = counts.get("metadata_diagnostics") or {}
        con.create_function(
            "daisy_issue_visible", 2,
            lambda code, message: int(issue_record_is_visible(code, message)),
        )
        only_unrecognized_format = (
            "(e.meta_status='error'"
            " AND e.hash_status NOT IN ('error','unstable')"
            " AND EXISTS (SELECT 1 FROM errors ignored"
            "  WHERE ignored.entry_id=e.entry_id"
            "  AND daisy_issue_visible(ignored.error_code,ignored.message)=0)"
            " AND NOT EXISTS (SELECT 1 FROM errors visible"
            "  WHERE visible.entry_id=e.entry_id"
            "  AND daisy_issue_visible(visible.error_code,visible.message)=1))"
        )
        ignored_format_total, = con.execute(
            "SELECT COUNT(DISTINCT entry_id) FROM errors"
            " WHERE daisy_issue_visible(error_code,message)=0").fetchone()
        error_total, = con.execute(
            "SELECT COUNT(*) FROM errors"
            " WHERE daisy_issue_visible(error_code,message)=1").fetchone()
        error_rows = [tuple(row) for row in con.execute(
            "SELECT x.stage,x.error_code,"
            " COALESCE(er.root_label,dr.root_label,''),"
            " COALESCE(e.rel_path,d.rel_path,''),x.message"
            " FROM errors x"
            " LEFT JOIN entries e ON e.entry_id=x.entry_id"
            " LEFT JOIN roots er ON er.root_id=e.root_id"
            " LEFT JOIN dirs d ON d.dir_id=x.dir_id"
            " LEFT JOIN roots dr ON dr.root_id=d.root_id"
            " WHERE daisy_issue_visible(x.error_code,x.message)=1"
            " ORDER BY x.error_pk LIMIT ?", (row_limit,))]
        status_total, = con.execute(
            "SELECT COUNT(*) FROM entries e WHERE ("
            " e.meta_status IN ('error','timeout','unstable')"
            " OR e.hash_status IN ('error','unstable'))"
            f" AND NOT {only_unrecognized_format}").fetchone()
        status_rows = [tuple(row) for row in con.execute(
            "SELECT r.root_label,e.rel_path,e.meta_status,e.hash_status"
            " FROM entries e JOIN roots r ON r.root_id=e.root_id"
            " WHERE (e.meta_status IN ('error','timeout','unstable')"
            " OR e.hash_status IN ('error','unstable'))"
            f" AND NOT {only_unrecognized_format}"
            " ORDER BY r.root_label,e.path_key LIMIT ?", (row_limit,))]
        reportable_meta_error, = con.execute(
            "SELECT COUNT(*) FROM entries e WHERE e.meta_status='error'"
            f" AND NOT {only_unrecognized_format}").fetchone()
        dir_total, = con.execute(
            "SELECT COUNT(*) FROM dirs WHERE enum_status<>'ok'").fetchone()
        dir_rows = [tuple(row) for row in con.execute(
            "SELECT r.root_label,d.rel_path,d.enum_status,d.error_message"
            " FROM dirs d JOIN roots r ON r.root_id=d.root_id"
            " WHERE d.enum_status<>'ok'"
            " ORDER BY r.root_label,d.path_key LIMIT ?", (row_limit,))]
        root_rows = [tuple(row) for row in con.execute(
            "SELECT root_label,enum_status FROM roots WHERE enum_status='failed'"
            " ORDER BY root_label")]
        diagnostic_group_rows = []
        evidence_gap_rows = []
        evidence_gap_total = 0
        if _table_exists(con, "metadata_diagnostics"):
            diagnostic_group_rows = [tuple(row) for row in con.execute(
                "SELECT severity,diagnostic_code,COALESCE(field_name,''),COUNT(*)"
                " FROM metadata_diagnostics"
                " WHERE severity<>'error' OR"
                " daisy_issue_visible(diagnostic_code,message)=1"
                " GROUP BY severity,diagnostic_code,field_name"
                " ORDER BY severity,diagnostic_code,field_name")]
            evidence_gap_total, = con.execute(
                "SELECT COUNT(*) FROM metadata_diagnostics WHERE"
                " severity='validation' AND diagnostic_code LIKE"
                " 'historical_%_payload_unavailable'").fetchone()
            evidence_gap_rows = [tuple(row) for row in con.execute(
                "SELECT r.root_label,e.rel_path,d.diagnostic_code,d.message"
                " FROM metadata_diagnostics d"
                " JOIN entries e ON e.entry_id=d.entry_id"
                " JOIN roots r ON r.root_id=e.root_id"
                " WHERE d.severity='validation' AND d.diagnostic_code LIKE"
                " 'historical_%_payload_unavailable'"
                " ORDER BY r.root_label,e.path_key,d.diagnostic_code LIMIT ?",
                (row_limit,))]
        reportable_source_issues = bool(status_total)
        summary_rows = [
            ("数据库完整性", info["database_integrity"]),
            ("扫描状态", info["scan_status"]),
            ("本报告存在源文件问题", int(reportable_source_issues)),
            ("存在 unstable 条目", info["has_unstable_entries"]),
            ("存在枚举缺口", info["has_enumeration_gaps"]),
            ("失败根目录", counts.get("roots_failed", 0)),
            ("目录枚举问题", counts.get("dir_errors", 0)),
            ("元数据 error（需关注）", reportable_meta_error),
            ("元数据 timeout", meta.get("timeout", 0)),
            ("元数据 unstable", meta.get("unstable", 0)),
            ("哈希 error", hashes.get("error", 0)),
            ("哈希 unstable", hashes.get("unstable", 0)),
            ("错误记录（需关注）", error_total),
            ("未列为问题的格式未识别文件", ignored_format_total),
            ("元数据 warning", diagnostics.get("warning", 0)),
            ("元数据 validation", diagnostics.get("validation", 0)),
        ]
    except (json.JSONDecodeError, sqlite3.Error) as exc:
        raise PreflightError(f"无法生成问题报告：{exc}") from exc
    finally:
        con.close()

    if not (error_total or status_total or dir_total or root_rows):
        return None

    lines = [
        "# DAISY 问题报告",
        "",
        *report_markdown_lines("DBS 快照问题报告"),
        f"- 数据库：`{artifact_filename or os.path.basename(path)}`",
        f"- 快照 UUID：`{info['snapshot_uuid']}`",
        f"- 原扫描器版本：`{info['scanner_version']}`",
        f"- 报告生成器版本：`{SCANNER_VERSION}`",
        f"- 报告生成时间：`{now_utc_iso()}`",
        "- 结论：SQLite 数据库完整性正常且扫描已完整封存；本报告描述的是源文件或扫描证据问题，不表示数据库损坏。",
        "- 命名：问题状态不写入数据库文件名。",
        "",
        "## 汇总",
        "",
    ]
    _append_markdown_table(lines, ("项目", "数量"), summary_rows)
    if diagnostic_group_rows:
        lines.extend(["", "## 元数据诊断分类", ""])
        _append_markdown_table(
            lines, ("等级", "诊断码", "字段", "数量"),
            diagnostic_group_rows)
    if evidence_gap_rows:
        lines.extend(["", "## 历史证据缺口", ""])
        _append_markdown_table(
            lines, ("根标签", "相对路径", "诊断码", "信息"),
            evidence_gap_rows)
        if evidence_gap_total > len(evidence_gap_rows):
            lines.append(
                f"\n仅列出前 {len(evidence_gap_rows)}／{evidence_gap_total} 条。")
    if root_rows:
        lines.extend(["", "## 失败根目录", ""])
        _append_markdown_table(lines, ("根标签", "状态"), root_rows)
    if dir_rows:
        lines.extend(["", "## 目录枚举问题", ""])
        _append_markdown_table(
            lines, ("根标签", "相对路径", "状态", "信息"), dir_rows)
        if dir_total > len(dir_rows):
            lines.append(f"\n仅列出前 {len(dir_rows)}／{dir_total} 条。")
    if status_rows:
        lines.extend(["", "## 问题条目状态", ""])
        _append_markdown_table(
            lines, ("根标签", "相对路径", "元数据状态", "哈希状态"),
            status_rows)
        if status_total > len(status_rows):
            lines.append(f"\n仅列出前 {len(status_rows)}／{status_total} 条。")
    if error_rows:
        lines.extend(["", "## 错误明细", ""])
        _append_markdown_table(
            lines, ("阶段", "错误码", "根标签", "相对路径", "信息"),
            error_rows)
        if error_total > len(error_rows):
            lines.append(f"\n仅列出前 {len(error_rows)}／{error_total} 条。")
    lines.extend([
        "",
        "## 说明",
        "",
        "单纯由 ExifTool 返回“格式未识别”的文件不列为问题；相关状态、诊断和"
        "错误记录仍完整保留在 SQLite 中。",
        "`warning` 与 `validation` 会在汇总中保留，但它们本身不会让扫描判为失败。",
        "如需完整逐表结果，请从该 SQLite 使用 `export-report` 导出。",
        "",
    ])
    return "\n".join(lines)


import subprocess

# === 预检（正式任务共享同一实现） ===
TOOL_MIN_VERSION = {"exiftool": (13,), "ffprobe": (8,), "sevenzip": (24,)}
WINGET_HINT = {
    "exiftool": "winget install OliverBetz.ExifTool",
    "ffprobe": "winget install Gyan.FFmpeg",
    "sevenzip": "winget install 7zip.7zip",
}
_PF = os.environ.get("ProgramFiles", r"C:\Program Files")
_PF86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
STD_CANDIDATES = {
    "exiftool": [os.path.join(_PF, "exiftool", "exiftool.exe")],
    "ffprobe": [os.path.join(_PF, "ffmpeg", "bin", "ffprobe.exe")],
    "sevenzip": [os.path.join(_PF, "7-Zip", "7z.exe"),
                 os.path.join(_PF86, "7-Zip", "7z.exe")],
}
_TOOL_EXE = {"exiftool": "exiftool", "ffprobe": "ffprobe", "sevenzip": "7z"}


def build_tiny_png() -> bytes:
    """运行时构造合法 1×1 灰度 PNG（冒烟样本，CRC 现算）。"""
    import struct
    import zlib as _z

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", _z.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0)
    idat = _z.compress(b"\x00\x80")
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", idat) + chunk(b"IEND", b""))


def discover_tool(name: str, explicit: str | None) -> str:
    if explicit:
        if os.path.isfile(explicit):
            return os.path.abspath(explicit)
        raise PreflightError(f"{name} 显式路径不存在：{explicit}\n  安装：{WINGET_HINT[name]}")
    import shutil as _sh
    found = _sh.which(_TOOL_EXE[name])
    if found:
        return os.path.abspath(found)
    for cand in STD_CANDIDATES[name]:
        if os.path.isfile(cand):
            return cand
    raise PreflightError(
        f"未找到必备工具 {name}（PATH 与常规安装位置均无）\n  安装：{WINGET_HINT[name]}")


def _run(args: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
    configure_windows_worker_error_mode()
    return subprocess.run(args, capture_output=True, timeout=timeout)


def tool_version(name: str, path: str) -> str:
    if name == "exiftool":
        out = _run([path, "-ver"]).stdout.decode("utf-8", "replace").strip()
        if not re.fullmatch(r"\d+\.\d+", out):
            raise PreflightError(f"exiftool -ver 输出异常：{out!r}")
        return out
    if name == "ffprobe":
        out = _run([path, "-version"]).stdout.decode("utf-8", "replace")
        return parse_ffprobe_version(out.splitlines()[0] if out else "")
    out = (_run([path]).stdout.decode("utf-8", "replace") or "")
    for line in out.splitlines():
        if "7-Zip" in line:
            return parse_sevenzip_version(line)
    raise PreflightError(f"无法取得 7-Zip 版本（{path}）")


def tool_resolution_source(name: str, explicit: bool) -> str:
    """返回本次工具路径来源；GUI 可通过环境变量补充 session_cache。"""
    try:
        hints = json.loads(os.environ.get("DAISY_TOOL_SOURCES", "{}"))
    except json.JSONDecodeError:
        hints = {}
    hinted = hints.get(name) if isinstance(hints, dict) else None
    if hinted in ("manual", "session_cache", "auto_discovery"):
        return hinted
    return "manual" if explicit else "auto_discovery"


def resolved_tool_info(name: str, path: str, *, explicit: bool,
                       version: str | None = None) -> dict:
    """构造通过版本校验的可缓存工具记录。"""
    resolved = os.path.abspath(path)
    ver = version if version is not None else tool_version(name, resolved)
    minimum = TOOL_MIN_VERSION.get(name)
    if minimum and parse_version_tuple(ver) < minimum:
        raise PreflightError(
            f"{name} 版本过低：{ver}（需 ≥ {'.'.join(map(str, minimum))}）"
            f"\n  升级：{WINGET_HINT[name]}")
    return {
        "path": resolved,
        "version": ver,
        "resolution": tool_resolution_source(name, explicit),
        "verified": True,
    }


# ExifTool 只读防护黑名单——单一权威源，由 Lib_02 导入引用。
# 「对档案绝对只读」的承重件：任何新写参数一经发现即补入此处。
EXIFTOOL_BANNED_ARGS = frozenset({
    "-overwrite_original", "-overwrite_original_in_place",
    "-delete_original", "-restore_original", "-tagsfromfile",
    "-geotag", "-srcfile", "-o", "-out", "-w", "-csv=", "-json=",
})


def _exiftool_argfile_run(path: str, args: list[str], timeout: int = 60):
    """经 UTF-8 argfile 调用；仅允许白名单读取参数。"""
    for a in args:
        if "=" in a and not a.startswith(("-charset", "filename=")):
            raise PreflightError(f"ExifTool 写语法被只读防护拦截：{a!r}")
        if a.lower() in EXIFTOOL_BANNED_ARGS:
            raise PreflightError(f"ExifTool 写参数被只读防护拦截：{a!r}")
    import tempfile as _tf
    fd, argfile = _tf.mkstemp(suffix=".args", text=False)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(args) + "\n")
        return _run([path, "-@", argfile], timeout=timeout)
    finally:
        try:
            os.unlink(argfile)
        except OSError:
            pass


def run_preflight(explicit: dict | None = None,
                  output_dir: str | None = None,
                  min_free_bytes: int = 1 << 30) -> dict:
    """六项预检；任一失败抛 PreflightError（附 winget 命令）。返回 {name:{path,version}}。"""
    explicit = explicit or {}
    tools: dict = {}
    for name in ("exiftool", "ffprobe", "sevenzip"):
        path = discover_tool(name, explicit.get(name))
        tools[name] = resolved_tool_info(
            name, path, explicit=bool(explicit.get(name)))

    if hashlib.sha256(b"abc").hexdigest() != (
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"):
        raise PreflightError("SHA-256 实现未通过 NIST 标准向量")

    import tempfile as _tf
    import zipfile as _zf
    with _tf.TemporaryDirectory() as td:
        png = os.path.join(td, "smoke.png")
        with open(png, "wb") as f:
            f.write(build_tiny_png())
        before = sha256_file(png)
        r = _exiftool_argfile_run(tools["exiftool"]["path"],
                                  ["-charset", "filename=utf8", "-j", png])
        if r.returncode != 0 or not r.stdout:
            raise PreflightError("exiftool 冒烟测试失败")
        json.loads(r.stdout.decode("utf-8"))
        r = _run([tools["ffprobe"]["path"], "-v", "error", "-print_format", "json",
                  "-show_format", png])
        if r.returncode != 0:
            raise PreflightError("ffprobe 冒烟测试失败")
        zp = os.path.join(td, "smoke.zip")
        with _zf.ZipFile(zp, "w") as z:
            z.write(png, "smoke.png")
        r = _run([tools["sevenzip"]["path"], "t", zp])
        if r.returncode != 0:
            raise PreflightError("7-Zip 冒烟测试失败")
        if sha256_file(png) != before:
            raise PreflightError("只读断言失败：冒烟样本在工具运行后发生变化！")

    if output_dir is not None:
        os.makedirs(output_dir, exist_ok=True)
        import shutil as _sh
        free = _sh.disk_usage(output_dir).free
        if free < min_free_bytes:
            raise PreflightError(
                f"输出目录剩余空间不足：{free/1e9:.2f} GB < {min_free_bytes/1e9:.2f} GB")
    emit_gui_event("tools_detected", tools=tools)
    return tools


# === 事件日志与控制台进度 ===
GUI_EVENT_PREFIX = "@@DAISY_GUI@@"


def gui_events_enabled() -> bool:
    return os.environ.get("DAISY_GUI_PROGRESS") == "1"


def emit_gui_event(event: str, **payload) -> None:
    """只在 GUI 子进程模式输出一行机器可读事件。"""
    if not gui_events_enabled():
        return
    record = {"event": event, **payload}
    print(
        GUI_EVENT_PREFIX
        + json.dumps(record, ensure_ascii=False, separators=(",", ":")),
        flush=True,
    )


class EventLog:
    def __init__(self, path: str):
        self._f = open(path, "a", encoding="utf-8", newline="\n")

    def emit(self, event: str, **payload) -> None:
        rec = {"ts": now_utc_iso(), "event": event, **payload}
        self._f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self._f.flush()

    def close(self) -> None:
        self._f.close()


class Progress:
    """单行刷新进度：[k/N] 阶段 | 计数 | 速率 | 已用 | 错误。"""

    def __init__(self, stage_idx: int, stage_total: int, name: str, quiet: bool = False):
        self.stage_idx = stage_idx
        self.stage_total = stage_total
        self.name = name
        self.prefix = f"[{stage_idx}/{stage_total}] {name}"
        self.quiet = quiet
        self.t0 = time.monotonic()
        self._last = 0.0
        if not quiet:
            print(f"{self.prefix} …", flush=True)
        emit_gui_event(
            "progress_start",
            stage_idx=stage_idx,
            stage_total=stage_total,
            name=name,
        )

    def update(self, done: int, total: int | None = None,
               bytes_done: int | None = None, errors: int = 0,
               bytes_total: int | None = None) -> None:
        now = time.monotonic()
        if (now - self._last) < 1.0:   # 每秒至多刷新一次
            return
        self._last = now
        elapsed = now - self.t0
        rate = done / elapsed if elapsed > 0 else 0
        bytes_rate = None
        eta = None
        parts = [self.prefix,
                 f"{done:,}/{total:,}" if total else f"{done:,}",
                 f"{rate:,.0f}/s"]
        if bytes_done is not None and bytes_total:
            # 哈希阶段以字节口径计算速率与 ETA
            bytes_rate = bytes_done / elapsed if elapsed > 0 else 0
            parts.append(f"{bytes_done/1e9:.2f}/{bytes_total/1e9:.2f} GB")
            if bytes_rate > 0:
                parts.append(f"{bytes_rate/1e6:.0f} MB/s")
                eta = max(0.0, (bytes_total - bytes_done) / bytes_rate)
                parts.append(f"ETA {int(eta//3600):02d}:"
                             f"{int(eta%3600//60):02d}:{int(eta%60):02d}")
        else:
            if bytes_done is not None:
                parts.append(f"{bytes_done/1e9:.2f} GB")
            if total and rate > 0:
                eta = max(0.0, (total - done) / rate)
                parts.append(f"ETA {int(eta//3600):02d}:"
                             f"{int(eta%3600//60):02d}:{int(eta%60):02d}")
        parts.append(f"已用 {int(elapsed//60):02d}:{int(elapsed%60):02d}")
        if errors:
            parts.append(f"错误 {errors}")
        emit_gui_event(
            "progress_update",
            stage_idx=self.stage_idx,
            stage_total=self.stage_total,
            name=self.name,
            done=done,
            total=total,
            bytes_done=bytes_done,
            bytes_total=bytes_total,
            errors=errors,
            elapsed=elapsed,
            rate=rate,
            bytes_rate=bytes_rate,
            eta=eta,
        )
        if self.quiet or gui_events_enabled():
            return
        print("\r" + " | ".join(parts) + "    ", end="", flush=True)

    def finish(self, summary: str) -> None:
        elapsed = time.monotonic() - self.t0
        emit_gui_event(
            "progress_finish",
            stage_idx=self.stage_idx,
            stage_total=self.stage_total,
            name=self.name,
            summary=summary,
            elapsed=elapsed,
        )
        if not self.quiet:
            print(f"\r{self.prefix} 完成：{summary}（{elapsed:.1f}s）" + " " * 20,
                  flush=True)
