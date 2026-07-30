"""DAISY 共享核心模块。

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
SCANNER_VERSION = "1.3.3"      # 包版本
SCHEMA_VERSION = 1
PATH_KEY_RULE = 1

# 支持格式映射；配置可扩展，新增格式须附样本实测
EXT_TO_KIND = {
    **dict.fromkeys(["cr2", "cr3", "nef", "arw", "raf", "orf", "rw2", "dng"], "photo_raw"),
    **dict.fromkeys(["jpg", "jpeg"], "photo_jpeg"),
    **dict.fromkeys(["tif", "tiff", "psd", "psb", "png"], "photo_working"),
    **dict.fromkeys(["mp4", "mov", "lrf"], "video_mp4"),
    "crm": "video_crm",
    **dict.fromkeys(["wav", "mp3"], "audio"),
    **dict.fromkeys(["zip", "7z", "rar", "tar", "gz", "bz2", "xz"], "archive"),
    **dict.fromkeys(["pdf", "docx", "xlsx", "pptx"], "document"),
}


class PreflightError(RuntimeError):
    """预检失败：环境不满足运行前提。"""


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
            tokens.append("No-Raw")
        if not file_id:
            tokens.append("No-FID")
        return tokens
    if normalized == "quick":
        # Quick 已经明确蕴含无哈希、无元数据 Payload，不重复制造冗余标记。
        return [] if file_id else ["No-FID"]
    return []


def snapshot_name(labels: list[str], kind: str,
                  profile_tokens: list[str] | tuple[str, ...] = ()) -> str:
    """快照文件基名：根文件夹名_类型_年-月-日_时-分-秒.微秒_运行id。
    多 root 以 + 连接；文件名时间戳用本地时间，权威时间在库内 UTC。
    微秒＋8 位随机 run id 用于降低碰撞概率；防覆盖的正式保障是独占预留
    与原子 no-replace 发布。"""
    safe = "+".join(labels)
    for ch in '<>:"/\\|?*':
        safe = safe.replace(ch, "_")
    stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
    micro = time.time_ns() // 1000 % 1_000_000
    identity = "_".join([safe, kind, *profile_tokens])
    return f"{identity}_{stamp}.{micro:06d}_{uuid.uuid4().hex[:8]}"


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

-- v1.3.0 附加证据表：核心登记语义未变，因此 schema_version 仍为 1；
-- 本版本不承诺读取旧版 sidecar／散置摘要产物。
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
                    CHECK (media_kind IN ('photo_raw','photo_jpeg','photo_working','video_mp4','video_crm','audio','archive','document','other')),
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
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    h = ctypes.windll.kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return False
    ctypes.windll.kernel32.CloseHandle(h)
    return True


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
                            max_files: int | None = None) -> dict:
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
        except KeyboardInterrupt as exc:
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


def rescan_check(con: sqlite3.Connection) -> int:
    """全量复扫 size/mtime，与登记比对；变化或消失者标 unstable。返回变化数。"""
    roots = dict(con.execute("SELECT root_id, root_path FROM roots").fetchall())
    changed_ids = []
    for entry_id, root_id, rel, size, mtime in con.execute(
            "SELECT entry_id, root_id, rel_path, size_bytes, modified_at_utc"
            " FROM entries WHERE is_placeholder = 0"):
        try:
            st = os.stat(to_extended_path(os.path.join(roots[root_id], rel)),
                         follow_symlinks=False)
            if st.st_size != size or ns_to_utc_iso(st.st_mtime_ns) != mtime:
                changed_ids.append(entry_id)
        except OSError:
            changed_ids.append(entry_id)
    con.executemany("UPDATE entries SET meta_status='unstable',"
                    " hash_status='unstable' WHERE entry_id=?",
                    [(i,) for i in changed_ids])
    con.commit()
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
    raw_payload = (
        scan_kind == "full" and not bool(config.get("no_raw_payload", False)))
    file_id = not bool(config.get("no_file_id", False))
    return {
        "scan_kind": scan_kind,
        "hash_mode_requested": hash_mode,
        "hash_coverage_actual": hash_coverage,
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
    document = dict(manifest or {})
    document.update({
        "manifest_version": 1,
        "snapshot_stem": snapshot_stem,
        "snapshot_filename_pattern": filename_pattern,
        "snapshot_uuid": snapshot_uuid,
        "schema_version": schema_version,
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
        "events": {
            "storage": "run_events",
            "count": len(records) + 1,
        },
    })
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
                      hash_coverage: str,
                      force_abnormal: bool = False, *,
                      manifest: dict | None = None,
                      event_log_path: str | None = None) -> str:
    """封存：残留检查→counts→嵌入证据→integrity→checkpoint→关闭→高 32 bit 指纹命名发布。

    force_abnormal：本次运行自身干净但证据来源异常（如 --allow-abnormal-source
    复用事故快照）时，由调用方强制 _Abnormal，避免异常来源无声传递。"""
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
    }
    # 非正常运行标记：counts 含任一异常即产物名加
    # _Abnormal 后缀——健康快照与事故快照在文件夹里必须一眼可辨
    abnormal = bool(
        counts["dir_errors"] or counts["roots_failed"]
        or any(counts["hash_status"].get(k) for k in ("error", "unstable"))
        or any(counts["meta_status"].get(k)
               for k in ("error", "timeout", "unstable")))
    if force_abnormal:
        counts["abnormal_source_reuse"] = 1
        abnormal = True
    counts["abnormal"] = 1 if abnormal else 0
    final_stem_path = (
        partial_path[:-len(".partial.sqlite")]
        + ("_Abnormal" if abnormal else ""))
    finished_at_utc = now_utc_iso()
    con.execute("UPDATE snapshot_info SET scan_status='complete', finished_at_utc=?,"
                " hash_coverage=?, counts_json=?",
                (finished_at_utc, hash_coverage,
                 json.dumps(counts, ensure_ascii=False)))
    _embed_snapshot_evidence(
        con, final_stem_path, hash_coverage, counts, finished_at_utc,
        event_log_path, manifest)
    con.commit()
    ok, = con.execute("PRAGMA integrity_check").fetchone()
    if ok != "ok":
        raise PreflightError(f"SQLite 完整性检查失败：{ok}")
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    # 封存件切回 DELETE journal 模式，避免后续读取在不可变快照旁生成
    # -shm/-wal 辅助文件
    con.execute("PRAGMA journal_mode=DELETE")
    con.close()
    # 文件关闭后字节才最终稳定；把 SHA-256 前 8 个十六进制字符大写后置。
    digest = sha256_file(partial_path)
    final_path = final_stem_path + f"_{digest[:8].upper()}.sqlite"
    # 原子 no-replace 发布：Windows 的 os.rename 目标存在即
    # FileExistsError（MoveFileExW 无 REPLACE 位），旧快照逐字节不动
    try:
        os.rename(partial_path, final_path)
    except FileExistsError:
        raise PreflightError(
            f"发布冲突：目标已存在，旧快照保持不动：{final_path}\n"
            f"  本次运行结果保留于：{partial_path}")
    release_scan_lock(partial_path)
    return final_path


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
