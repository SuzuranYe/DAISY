"""DAISY 档案数据解析的版本化、流式模块投影。

本模块只消费统一 Reader 已准入的只读连接。大表使用 ``fetchmany``，不创建
临时表／索引，不读取快照记录的源文件路径。工具原始输出按行解压并验证长度、
SHA-256 和 UTF-8 JSON；SQLite 内部 entry_id 不作为导出身份。
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import sqlite3
from typing import Callable, Iterator
import zlib

import Script_DAISY_Lib_DBS_01_Core as core
import Script_DAISY_Lib_DBS_05_Reader as dbreader
import Script_DAISY_Lib_DBS_07_Parse as dbparse
import Script_DAISY_Lib_DBS_10_Issues as dbissues


PROJECTION_CONTRACT = "daisy-parse-projection-v1"
DEFAULT_BATCH_ROWS = 512


class ParseProjectionCancelled(Exception):
    """调用方在批次边界取消解析。"""


@dataclass(frozen=True)
class ParseProjectionSpec:
    module_id: str
    database_type: str
    fields: tuple[str, ...]
    projection_version: str = PROJECTION_CONTRACT


CancelCheck = Callable[[], bool] | None


def _check_cancel(cancel_check: CancelCheck) -> None:
    if cancel_check is not None and cancel_check():
        raise ParseProjectionCancelled("档案数据解析已取消")


def _logical_path_sql(root: str = "r", entry: str = "e") -> str:
    return (
        f"CASE WHEN {entry}.rel_path='' THEN {root}.root_label"
        f" ELSE {root}.root_label || '\\' || {entry}.rel_path END"
    )


def _json_text(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _value_type(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, (dict, list, tuple)):
        return "json"
    return "text"


def _display_value(value: object) -> object:
    if isinstance(value, (dict, list, tuple)):
        return _json_text(value)
    return value


def _iter_query(
    con: sqlite3.Connection,
    sql: str,
    fields: tuple[str, ...],
    *,
    batch_rows: int,
    cancel_check: CancelCheck,
) -> Iterator[dict[str, object]]:
    _check_cancel(cancel_check)
    cursor = con.execute(sql)
    actual = tuple(str(column[0]) for column in cursor.description)
    if actual != fields:
        raise core.PreflightError(
            "解析投影字段与 SQL 不一致："
            f"预期={fields!r}，实际={actual!r}")
    while True:
        _check_cancel(cancel_check)
        rows = cursor.fetchmany(batch_rows)
        if not rows:
            break
        for row in rows:
            _check_cancel(cancel_check)
            yield dict(zip(fields, tuple(row)))


def _entry_table_query(
    table: str,
    columns: tuple[str, ...],
    *,
    order_suffix: str = "",
) -> tuple[tuple[str, ...], str]:
    fields = ("root_label", "rel_path", "logical_path", *columns)
    selected = ",".join(f't."{column}"' for column in columns)
    sql = (
        "SELECT r.root_label AS root_label,e.rel_path AS rel_path,"
        f"{_logical_path_sql()} AS logical_path"
        + (f",{selected}" if selected else "")
        + f" FROM {table} t"
        " JOIN entries e ON e.entry_id=t.entry_id"
        " JOIN roots r ON r.root_id=e.root_id"
        " ORDER BY r.root_label,e.rel_path"
        + order_suffix
    )
    return fields, sql


_OVERVIEW_FIELDS = ("section", "key", "label", "value", "value_type")
_FILES_FIELDS = (
    "root_label", "rel_path", "logical_path", "name", "extension",
    "media_kind", "size_bytes", "created_at_utc", "modified_at_utc",
    "attributes", "is_placeholder", "hard_link_count", "volume_serial",
    "file_index_hex", "observed_at_utc", "meta_status", "hash_status",
)
_DIRECTORIES_FIELDS = (
    "root_label", "rel_path", "logical_path", "parent_rel_path",
    "enum_status", "error_message", "file_count", "subdir_count",
    "attributes", "observed_at_utc",
)
_HASH_FIELDS = (
    "root_label", "rel_path", "logical_path", "algorithm", "hash_hex",
    "origin", "source_snapshot_uuid", "source_computed_at_utc",
    "reuse_basis", "size_bytes", "bytes_read", "chunk_bytes",
    "started_at_utc", "finished_at_utc", "pre_size", "pre_mtime_utc",
    "post_size", "post_mtime_utc", "status", "failure_reason", "tool",
    "tool_version",
)
_PHOTO_COLUMNS = (
    "capture_time_raw", "capture_time_source", "capture_tz_offset_min",
    "capture_time_utc", "camera_make", "camera_model", "camera_serial",
    "lens_model", "lens_serial", "width", "height", "orientation", "iso",
    "f_number", "exposure_time", "exposure_compensation",
    "focal_length_mm", "focal_length_35mm", "white_balance",
    "color_temperature", "color_space", "icc_profile", "software",
    "bit_depth", "gps_latitude", "gps_longitude", "gps_altitude", "parser",
    "parser_version", "parsed_at_utc",
)
_VIDEO_COLUMNS = (
    "container_format", "duration_seconds", "bit_rate", "stream_count",
    "timecode", "capture_time_raw", "capture_time_source",
    "capture_tz_offset_min", "capture_time_utc", "camera_make",
    "camera_model", "camera_serial", "lens_model", "lens_serial", "iso",
    "white_balance", "shutter", "gamma", "color_gamut", "encoder", "title",
    "author", "album", "copyright", "parser", "parser_version",
    "parsed_at_utc",
)
_GPS_COLUMNS = (
    "point_index", "timestamp_seconds", "gps_latitude", "gps_longitude",
    "gps_altitude", "source", "raw_value",
)
_WORKING_COLUMNS = (
    "file_variant", "creator_app", "color_mode", "bit_depth", "width",
    "height", "layer_count", "layer_names", "has_thumbnail", "parser",
    "parser_version", "parsed_at_utc",
)
_DOCUMENT_COLUMNS = (
    "doc_format", "title", "author", "last_modified_by", "creator_app",
    "created_prop_raw", "modified_prop_raw", "page_count", "is_encrypted",
    "parser", "parser_version", "parsed_at_utc",
)
_MEDIA_STREAM_FIELDS = (
    "record_type", "root_label", "rel_path", "logical_path", "stream_index",
    "codec_name", "profile", "codec_tag", "width", "height", "r_frame_rate",
    "avg_frame_rate", "pix_fmt", "bit_depth", "color_space",
    "color_transfer", "color_primaries", "sample_rate", "channels",
    "channel_layout", "bit_rate", "nb_frames", "duration_seconds",
)
_ARCHIVE_FIELDS = (
    "record_type", "root_label", "rel_path", "logical_path",
    "archive_format", "member_count", "uncompressed_bytes",
    "compressed_bytes", "has_encrypted", "parser", "parser_version",
    "parsed_at_utc", "member_index", "member_path", "is_dir", "size_bytes",
    "packed_bytes", "crc32_hex", "method", "flag_bits", "host_os",
    "create_version", "extract_version", "header_offset", "modified_raw",
    "attributes", "encrypted",
)
_RAW_FIELDS = (
    "root_label", "rel_path", "logical_path", "provider",
    "provider_version", "profile_version", "payload_sha256",
    "uncompressed_bytes", "parsed_at_utc", "payload",
)
_DIAGNOSTIC_FIELDS = (
    "record_type", "root_label", "rel_path", "logical_path", "provider",
    "stage", "severity", "code", "field_name", "message", "raw_value",
    "occurred_at_utc",
)
_ISSUE_FIELDS = (
    "section_id", "title", "execution", "issue_files", "issue_records",
    "unsupported_files", "low_confidence_records", "reason",
    "information_json", "details_json",
)
_RUN_HISTORY_FIELDS = (
    "record_type", "record_key", "session_id", "entry_path", "stage",
    "status", "event", "occurred_at_utc", "started_at_utc",
    "updated_at_utc", "ended_at_utc", "tool_name", "tool_version",
    "bytes_read", "elapsed_seconds", "decision", "reason", "data_json",
)
_DIFF_FILE_FIELDS = (
    "path_key", "old_root_label", "new_root_label", "old_rel_path",
    "new_rel_path", "old_path", "new_path", "status", "evidence", "reason",
    "old_size", "new_size", "old_mtime_utc", "new_mtime_utc",
    "old_hash_hex", "new_hash_hex", "old_hash_origin", "new_hash_origin",
    "metadata_changed", "group_hash",
)
_DIFF_DIRECTORY_FIELDS = (
    "path_key", "old_root_label", "new_root_label", "old_rel_path",
    "new_rel_path", "old_path", "new_path", "status", "old_enum_status",
    "new_enum_status", "reason",
)
_DIFF_GROUP_FIELDS = (
    "hash_hex", "old_count", "new_count", "old_hardlink_sets",
    "new_hardlink_sets", "classification",
)
_DIFF_GAP_FIELDS = (
    "side", "root_label", "rel_path", "logical_path", "enum_status",
    "affected_estimate",
)
_EVIDENCE_FIELDS = ("topic", "key", "value", "state", "reason")


_DEFINITIONS = {
    ("snapshot", "overview"): ParseProjectionSpec(
        "overview", "snapshot", _OVERVIEW_FIELDS),
    ("snapshot", "issues"): ParseProjectionSpec(
        "issues", "snapshot", _ISSUE_FIELDS),
    ("snapshot", "files"): ParseProjectionSpec(
        "files", "snapshot", _FILES_FIELDS),
    ("snapshot", "directories"): ParseProjectionSpec(
        "directories", "snapshot", _DIRECTORIES_FIELDS),
    ("snapshot", "hashes"): ParseProjectionSpec(
        "hashes", "snapshot", _HASH_FIELDS),
    ("snapshot", "photo_metadata"): ParseProjectionSpec(
        "photo_metadata", "snapshot",
        ("root_label", "rel_path", "logical_path", *_PHOTO_COLUMNS)),
    ("snapshot", "video_metadata"): ParseProjectionSpec(
        "video_metadata", "snapshot",
        ("root_label", "rel_path", "logical_path", *_VIDEO_COLUMNS)),
    ("snapshot", "video_gps"): ParseProjectionSpec(
        "video_gps", "snapshot",
        ("root_label", "rel_path", "logical_path", *_GPS_COLUMNS)),
    ("snapshot", "media_streams"): ParseProjectionSpec(
        "media_streams", "snapshot", _MEDIA_STREAM_FIELDS),
    ("snapshot", "working_metadata"): ParseProjectionSpec(
        "working_metadata", "snapshot",
        ("root_label", "rel_path", "logical_path", *_WORKING_COLUMNS)),
    ("snapshot", "document_metadata"): ParseProjectionSpec(
        "document_metadata", "snapshot",
        ("root_label", "rel_path", "logical_path", *_DOCUMENT_COLUMNS)),
    ("snapshot", "archives"): ParseProjectionSpec(
        "archives", "snapshot", _ARCHIVE_FIELDS),
    ("snapshot", "raw_payloads"): ParseProjectionSpec(
        "raw_payloads", "snapshot", _RAW_FIELDS),
    ("snapshot", "diagnostics"): ParseProjectionSpec(
        "diagnostics", "snapshot", _DIAGNOSTIC_FIELDS),
    ("snapshot", "run_history"): ParseProjectionSpec(
        "run_history", "snapshot", _RUN_HISTORY_FIELDS),
    ("diff", "overview"): ParseProjectionSpec(
        "overview", "diff", _OVERVIEW_FIELDS),
    ("diff", "file_changes"): ParseProjectionSpec(
        "file_changes", "diff", _DIFF_FILE_FIELDS),
    ("diff", "directory_changes"): ParseProjectionSpec(
        "directory_changes", "diff", _DIFF_DIRECTORY_FIELDS),
    ("diff", "content_groups"): ParseProjectionSpec(
        "content_groups", "diff", _DIFF_GROUP_FIELDS),
    ("diff", "enumeration_gaps"): ParseProjectionSpec(
        "enumeration_gaps", "diff", _DIFF_GAP_FIELDS),
    ("diff", "evidence_notes"): ParseProjectionSpec(
        "evidence_notes", "diff", _EVIDENCE_FIELDS),
}


def projection_catalog(
    database_type: str,
) -> tuple[ParseProjectionSpec, ...]:
    modules = dbparse.parse_modules(database_type)
    result = tuple(
        _DEFINITIONS[(database_type, module.module_id)] for module in modules)
    if {item.module_id for item in result} != {
            module.module_id for module in modules}:
        raise RuntimeError("解析投影目录与模块注册表不一致")
    return result


def projection_definition(
    database_type: str,
    module_id: str,
) -> ParseProjectionSpec:
    try:
        return _DEFINITIONS[(database_type, module_id)]
    except KeyError as exc:
        raise core.PreflightError(
            f"数据库类型 {database_type} 没有解析投影 {module_id}") from exc


def _overview_row(
    section: str,
    key: str,
    label: str,
    value: object,
) -> dict[str, object]:
    return {
        "section": section,
        "key": key,
        "label": label,
        "value": _display_value(value),
        "value_type": _value_type(value),
    }


def _iter_snapshot_overview(
    con: sqlite3.Connection,
    descriptor: dbreader.DatabaseDescriptor,
    *,
    batch_rows: int,
    cancel_check: CancelCheck,
) -> Iterator[dict[str, object]]:
    del batch_rows
    for key, label, value in (
        ("database_type", "数据库类型", descriptor.database_type),
        ("schema_version", "数据库结构版本", descriptor.schema_version),
        ("source_version", "数据库生成程序版本", descriptor.source_version),
        ("lifecycle", "封存状态", descriptor.lifecycle),
        ("sqlite_integrity", "SQLite 完整性", descriptor.sqlite_integrity),
        ("snapshot_uuid", "快照 UUID", descriptor.identity.get(
            "snapshot_uuid")),
        ("hash_coverage", "哈希覆盖", descriptor.identity.get(
            "hash_coverage")),
        ("scan_kind", "扫描模式", descriptor.identity.get("scan_kind")),
        ("metadata_storage", "元数据范围", descriptor.identity.get(
            "metadata_storage")),
        ("format_validation", "格式校验", descriptor.identity.get(
            "format_validation")),
    ):
        _check_cancel(cancel_check)
        yield _overview_row("identity", key, label, value)
    counts = con.execute(
        "SELECT COUNT(*),COALESCE(SUM(size_bytes),0) FROM entries"
    ).fetchone()
    dirs = con.execute("SELECT COUNT(*) FROM dirs").fetchone()[0]
    for key, label, value in (
        ("files", "文件数", int(counts[0])),
        ("directories", "目录数", int(dirs)),
        ("bytes", "总字节数", int(counts[1])),
    ):
        yield _overview_row("counts", key, label, value)
    for media_kind, file_count, size_bytes in con.execute(
            "SELECT media_kind,COUNT(*),COALESCE(SUM(size_bytes),0)"
            " FROM entries GROUP BY media_kind ORDER BY media_kind"):
        yield _overview_row(
            "media_kind", f"{media_kind}.files", f"{media_kind} 文件数",
            int(file_count))
        yield _overview_row(
            "media_kind", f"{media_kind}.bytes", f"{media_kind} 字节数",
            int(size_bytes))
    for root_label, root_path, enum_status in con.execute(
            "SELECT root_label,root_path,enum_status FROM roots"
            " ORDER BY root_label COLLATE BINARY"):
        yield _overview_row(
            "root", f"{root_label}.path", f"根目录 {root_label}", root_path)
        yield _overview_row(
            "root", f"{root_label}.enum_status", f"根目录 {root_label} 状态",
            enum_status)
    row = con.execute(
        "SELECT counts_json FROM snapshot_info WHERE id=1").fetchone()
    if row is not None and row[0]:
        try:
            declared = json.loads(str(row[0]))
        except (TypeError, ValueError) as exc:
            raise core.PreflightError(
                f"snapshot_info.counts_json 无法解析：{exc}") from exc
        yield _overview_row(
            "declared", "counts_json", "封存声明计数", declared)


def _iter_snapshot_issues(
    con: sqlite3.Connection,
    descriptor: dbreader.DatabaseDescriptor,
    *,
    batch_rows: int,
    cancel_check: CancelCheck,
) -> Iterator[dict[str, object]]:
    del batch_rows
    analysis = dbissues.analyze_snapshot_issue_connection(
        con,
        descriptor=descriptor,
        database="<parse-transaction>",
        row_limit=dbissues.DETAIL_LIMIT,
    )
    for section in analysis["sections"]:
        _check_cancel(cancel_check)
        information = dict(section.get("information") or {})
        unsupported = sum(
            int(information.get(key) or 0) for key in (
                "unsupported_or_unrecognized_files",
                "unsupported_files",
                "raw_unsupported_files",
            )
        )
        yield {
            "section_id": section.get("id"),
            "title": section.get("title"),
            "execution": section.get("execution"),
            "issue_files": section.get("issue_files"),
            "issue_records": section.get("issue_records"),
            "unsupported_files": unsupported,
            "low_confidence_records": information.get(
                "low_confidence_files"),
            "reason": section.get("reason"),
            "information_json": _json_text(information),
            "details_json": _json_text(section.get("details") or []),
        }


def _iter_files(
    con: sqlite3.Connection,
    descriptor: dbreader.DatabaseDescriptor,
    *,
    batch_rows: int,
    cancel_check: CancelCheck,
) -> Iterator[dict[str, object]]:
    del descriptor
    sql = (
        "SELECT r.root_label AS root_label,e.rel_path AS rel_path,"
        f"{_logical_path_sql()} AS logical_path,e.name,e.extension,"
        "e.media_kind,e.size_bytes,e.created_at_utc,e.modified_at_utc,"
        "e.attributes,e.is_placeholder,e.hard_link_count,e.volume_serial,"
        "e.file_index_hex,e.observed_at_utc,e.meta_status,e.hash_status"
        " FROM entries e JOIN roots r ON r.root_id=e.root_id"
        " ORDER BY r.root_label,e.rel_path"
    )
    yield from _iter_query(
        con, sql, _FILES_FIELDS, batch_rows=batch_rows,
        cancel_check=cancel_check)


def _iter_directories(
    con: sqlite3.Connection,
    descriptor: dbreader.DatabaseDescriptor,
    *,
    batch_rows: int,
    cancel_check: CancelCheck,
) -> Iterator[dict[str, object]]:
    del descriptor
    sql = (
        "SELECT r.root_label AS root_label,d.rel_path AS rel_path,"
        "CASE WHEN d.rel_path='' THEN r.root_label"
        " ELSE r.root_label || '\\' || d.rel_path END AS logical_path,"
        "p.rel_path AS parent_rel_path,d.enum_status,d.error_message,"
        "d.file_count,d.subdir_count,d.attributes,d.observed_at_utc"
        " FROM dirs d JOIN roots r ON r.root_id=d.root_id"
        " LEFT JOIN dirs p ON p.dir_id=d.parent_dir_id"
        " ORDER BY r.root_label,d.rel_path"
    )
    yield from _iter_query(
        con, sql, _DIRECTORIES_FIELDS, batch_rows=batch_rows,
        cancel_check=cancel_check)


def _iter_hashes(
    con: sqlite3.Connection,
    descriptor: dbreader.DatabaseDescriptor,
    *,
    batch_rows: int,
    cancel_check: CancelCheck,
) -> Iterator[dict[str, object]]:
    del descriptor
    columns = _HASH_FIELDS[3:]
    selected = ",".join(f'h."{column}"' for column in columns)
    sql = (
        "SELECT r.root_label AS root_label,e.rel_path AS rel_path,"
        f"{_logical_path_sql()} AS logical_path,{selected}"
        " FROM hashes h JOIN entries e ON e.entry_id=h.entry_id"
        " JOIN roots r ON r.root_id=e.root_id"
        " ORDER BY r.root_label,e.rel_path,h.algorithm"
    )
    yield from _iter_query(
        con, sql, _HASH_FIELDS, batch_rows=batch_rows,
        cancel_check=cancel_check)


def _iter_entry_table(
    con: sqlite3.Connection,
    table: str,
    columns: tuple[str, ...],
    fields: tuple[str, ...],
    *,
    batch_rows: int,
    cancel_check: CancelCheck,
    order_suffix: str = "",
) -> Iterator[dict[str, object]]:
    expected, sql = _entry_table_query(
        table, columns, order_suffix=order_suffix)
    if expected != fields:
        raise RuntimeError(f"{table} 投影字段注册错误")
    yield from _iter_query(
        con, sql, fields, batch_rows=batch_rows,
        cancel_check=cancel_check)


def _iter_media_streams(
    con: sqlite3.Connection,
    descriptor: dbreader.DatabaseDescriptor,
    *,
    batch_rows: int,
    cancel_check: CancelCheck,
) -> Iterator[dict[str, object]]:
    del descriptor
    path = _logical_path_sql()
    video_sql = (
        "SELECT 'video' AS record_type,r.root_label,e.rel_path,"
        f"{path} AS logical_path,t.stream_index,t.codec_name,t.profile,"
        "t.codec_tag,t.width,t.height,t.r_frame_rate,t.avg_frame_rate,"
        "t.pix_fmt,t.bit_depth,t.color_space,t.color_transfer,"
        "t.color_primaries,NULL AS sample_rate,NULL AS channels,"
        "NULL AS channel_layout,t.bit_rate,t.nb_frames,t.duration_seconds"
        " FROM video_streams t JOIN entries e ON e.entry_id=t.entry_id"
        " JOIN roots r ON r.root_id=e.root_id"
        " ORDER BY r.root_label,e.rel_path,t.stream_index"
    )
    audio_sql = (
        "SELECT 'audio' AS record_type,r.root_label,e.rel_path,"
        f"{path} AS logical_path,t.stream_index,t.codec_name,t.profile,"
        "NULL AS codec_tag,NULL AS width,NULL AS height,NULL AS r_frame_rate,"
        "NULL AS avg_frame_rate,NULL AS pix_fmt,NULL AS bit_depth,"
        "NULL AS color_space,NULL AS color_transfer,NULL AS color_primaries,"
        "t.sample_rate,t.channels,t.channel_layout,t.bit_rate,NULL AS nb_frames,"
        "t.duration_seconds FROM audio_streams t"
        " JOIN entries e ON e.entry_id=t.entry_id"
        " JOIN roots r ON r.root_id=e.root_id"
        " ORDER BY r.root_label,e.rel_path,t.stream_index"
    )
    yield from _iter_query(
        con, video_sql, _MEDIA_STREAM_FIELDS, batch_rows=batch_rows,
        cancel_check=cancel_check)
    yield from _iter_query(
        con, audio_sql, _MEDIA_STREAM_FIELDS, batch_rows=batch_rows,
        cancel_check=cancel_check)


def _iter_archives(
    con: sqlite3.Connection,
    descriptor: dbreader.DatabaseDescriptor,
    *,
    batch_rows: int,
    cancel_check: CancelCheck,
) -> Iterator[dict[str, object]]:
    del descriptor
    path = _logical_path_sql()
    archive_sql = (
        "SELECT 'archive' AS record_type,r.root_label,e.rel_path,"
        f"{path} AS logical_path,t.archive_format,t.member_count,"
        "t.uncompressed_bytes,t.compressed_bytes,t.has_encrypted,t.parser,"
        "t.parser_version,t.parsed_at_utc,NULL AS member_index,"
        "NULL AS member_path,NULL AS is_dir,NULL AS size_bytes,"
        "NULL AS packed_bytes,NULL AS crc32_hex,NULL AS method,"
        "NULL AS flag_bits,NULL AS host_os,NULL AS create_version,"
        "NULL AS extract_version,NULL AS header_offset,NULL AS modified_raw,"
        "NULL AS attributes,NULL AS encrypted"
        " FROM archive_metadata t JOIN entries e ON e.entry_id=t.entry_id"
        " JOIN roots r ON r.root_id=e.root_id"
        " ORDER BY r.root_label,e.rel_path"
    )
    member_sql = (
        "SELECT 'member' AS record_type,r.root_label,e.rel_path,"
        f"{path} AS logical_path,NULL AS archive_format,NULL AS member_count,"
        "NULL AS uncompressed_bytes,NULL AS compressed_bytes,"
        "NULL AS has_encrypted,NULL AS parser,NULL AS parser_version,"
        "NULL AS parsed_at_utc,t.member_index,t.member_path,t.is_dir,"
        "t.size_bytes,t.packed_bytes,t.crc32_hex,t.method,t.flag_bits,"
        "t.host_os,t.create_version,t.extract_version,t.header_offset,"
        "t.modified_raw,t.attributes,t.encrypted"
        " FROM archive_members t JOIN entries e ON e.entry_id=t.entry_id"
        " JOIN roots r ON r.root_id=e.root_id"
        " ORDER BY r.root_label,e.rel_path,t.member_index"
    )
    yield from _iter_query(
        con, archive_sql, _ARCHIVE_FIELDS, batch_rows=batch_rows,
        cancel_check=cancel_check)
    yield from _iter_query(
        con, member_sql, _ARCHIVE_FIELDS, batch_rows=batch_rows,
        cancel_check=cancel_check)


def _iter_raw_payloads(
    con: sqlite3.Connection,
    descriptor: dbreader.DatabaseDescriptor,
    *,
    batch_rows: int,
    cancel_check: CancelCheck,
) -> Iterator[dict[str, object]]:
    del descriptor
    sql = (
        "SELECT r.root_label,e.rel_path,"
        f"{_logical_path_sql()} AS logical_path,t.provider,"
        "t.provider_version,t.profile_version,t.payload_sha256,"
        "t.uncompressed_bytes,t.parsed_at_utc,t.payload_zlib"
        " FROM raw_payloads t JOIN entries e ON e.entry_id=t.entry_id"
        " JOIN roots r ON r.root_id=e.root_id"
        " ORDER BY r.root_label,e.rel_path,t.provider"
    )
    cursor = con.execute(sql)
    while True:
        _check_cancel(cancel_check)
        rows = cursor.fetchmany(batch_rows)
        if not rows:
            break
        for row in rows:
            _check_cancel(cancel_check)
            (root_label, rel_path, logical_path, provider, provider_version,
             profile_version, payload_sha256, uncompressed_bytes,
             parsed_at_utc, payload_zlib) = tuple(row)
            label = f"{logical_path}／{provider}"
            try:
                raw = zlib.decompress(bytes(payload_zlib))
            except (TypeError, ValueError, zlib.error) as exc:
                raise core.PreflightError(
                    f"工具原始输出无法解压（{label}）：{exc}") from exc
            if len(raw) != int(uncompressed_bytes):
                raise core.PreflightError(
                    f"工具原始输出长度不符（{label}）："
                    f"声明={uncompressed_bytes}，实际={len(raw)}")
            actual_sha = hashlib.sha256(raw).hexdigest()
            if actual_sha.casefold() != str(payload_sha256).casefold():
                raise core.PreflightError(
                    f"工具原始输出 SHA-256 不符（{label}）")
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeError, ValueError) as exc:
                raise core.PreflightError(
                    f"工具原始输出不是有效 UTF-8 JSON（{label}）：{exc}") from exc
            yield dict(zip(_RAW_FIELDS, (
                root_label, rel_path, logical_path, provider,
                provider_version, profile_version, payload_sha256,
                uncompressed_bytes, parsed_at_utc, payload,
            )))


def _iter_diagnostics(
    con: sqlite3.Connection,
    descriptor: dbreader.DatabaseDescriptor,
    *,
    batch_rows: int,
    cancel_check: CancelCheck,
) -> Iterator[dict[str, object]]:
    del descriptor
    path = (
        "CASE WHEN e.rel_path IS NULL THEN NULL WHEN e.rel_path=''"
        " THEN r.root_label ELSE r.root_label || '\\' || e.rel_path END"
    )
    error_sql = (
        "SELECT 'error' AS record_type,r.root_label,e.rel_path,"
        f"{path} AS logical_path,NULL AS provider,er.stage,NULL AS severity,"
        "er.error_code AS code,NULL AS field_name,er.message,NULL AS raw_value,"
        "er.occurred_at_utc FROM errors er"
        " LEFT JOIN entries e ON e.entry_id=er.entry_id"
        " LEFT JOIN roots r ON r.root_id=e.root_id ORDER BY er.error_pk"
    )
    diagnostic_sql = (
        "SELECT 'metadata_diagnostic' AS record_type,r.root_label,e.rel_path,"
        f"{path} AS logical_path,d.provider,NULL AS stage,d.severity,"
        "d.diagnostic_code AS code,d.field_name,d.message,d.raw_value,"
        "d.observed_at_utc AS occurred_at_utc"
        " FROM metadata_diagnostics d"
        " JOIN entries e ON e.entry_id=d.entry_id"
        " JOIN roots r ON r.root_id=e.root_id"
        " ORDER BY d.diagnostic_pk"
    )
    yield from _iter_query(
        con, error_sql, _DIAGNOSTIC_FIELDS, batch_rows=batch_rows,
        cancel_check=cancel_check)
    yield from _iter_query(
        con, diagnostic_sql, _DIAGNOSTIC_FIELDS, batch_rows=batch_rows,
        cancel_check=cancel_check)


_RUN_TABLES = (
    ("snapshot_manifest", "manifest", "id", "embedded_at_utc"),
    ("run_events", "run_event", "event_seq", "occurred_at_utc"),
    ("run_sessions", "session", "session_id", "started_at_utc"),
    ("entry_attempts", "entry_attempt", "attempt_id", "started_at_utc"),
    ("read_performance", "read_performance", "performance_id",
     "recorded_at_utc"),
    ("format_checks", "format_check", "entry_id", "checked_at_utc"),
    ("run_state_events", "state_event", "event_id", "occurred_at_utc"),
    ("stage_checkpoints", "stage_checkpoint", "stage", "stage_order"),
    ("snapshot_runtime", "runtime", "id", "id"),
)

_RUN_TABLE_COLUMNS = {
    "snapshot_manifest": (
        "id", "manifest_version", "manifest_json", "embedded_at_utc",
    ),
    "run_events": (
        "event_seq", "occurred_at_utc", "event", "payload_json",
    ),
    "run_sessions": (
        "session_id", "session_number", "parent_session_id",
        "session_kind", "session_status", "started_at_utc",
        "updated_at_utc", "ended_at_utc", "hostname", "pid",
        "process_start_token", "lease_id", "lease_acquired_at_utc",
        "lease_heartbeat_at_utc", "lease_expires_at_utc",
        "scanner_version", "resume_contract", "config_json", "tools_json",
        "end_reason",
    ),
    "entry_attempts": (
        "attempt_id", "entry_id", "session_id", "stage",
        "attempt_number", "status", "tool_name", "tool_version",
        "started_at_utc", "last_progress_at_utc", "ended_at_utc",
        "source_size_bytes", "source_modified_at_utc", "bytes_read",
        "final_offset", "stall_count", "max_stall_seconds", "decision",
        "decision_source", "end_reason", "error_code", "error_message",
        "result_json",
    ),
    "read_performance": (
        "performance_id", "attempt_id", "entry_id", "session_id", "stage",
        "origin", "size_bytes", "bytes_read", "elapsed_seconds",
        "active_read_seconds", "stall_count", "longest_stall_seconds",
        "first_stall_offset", "last_stall_offset", "final_offset",
        "ended_reason", "candidate_confidence", "candidate_reason",
        "recorded_at_utc",
    ),
    "format_checks": (
        "entry_id", "attempt_id", "status", "coverage", "validator",
        "tool_name", "tool_version", "stat_match", "detail",
        "checked_at_utc", "result_revision",
    ),
    "run_state_events": (
        "event_id", "session_id", "session_event_seq", "occurred_at_utc",
        "event", "from_state", "to_state", "state_revision",
        "payload_json",
    ),
    "stage_checkpoints": (
        "stage", "stage_order", "state", "session_id", "items_done",
        "items_total", "bytes_done", "bytes_total", "error_count",
        "current_entry_id", "started_at_utc", "updated_at_utc",
        "finished_at_utc", "checkpoint_json",
    ),
    "snapshot_runtime": (
        "id", "snapshot_uuid", "schema_version", "data_contract",
        "min_reader_version", "resume_contract", "projection_contract",
        "filename_layout_version", "run_state", "state_revision",
        "resume_hint", "active_session_id", "current_stage",
        "created_at_utc", "updated_at_utc", "last_checkpoint_at_utc",
        "output_dir", "partial_path", "publish_stem_path",
        "event_log_path", "published_path_pattern", "last_error_code",
        "last_error_message",
    ),
}


def _first(record: dict[str, object], *keys: str) -> object:
    for key in keys:
        value = record.get(key)
        if value is not None:
            return value
    return None


def _run_record_key(
    record_type: str,
    record: dict[str, object],
    entry_path: str | None,
) -> object:
    """建立不依赖 SQLite entry_id 的可读记录键。"""
    if record_type == "manifest":
        return "manifest"
    if record_type == "run_event":
        return f"event:{record.get('event_seq')}"
    if record_type == "session":
        return record.get("session_id")
    if record_type == "entry_attempt":
        return (
            f"{entry_path}|{record.get('stage')}|"
            f"attempt={record.get('attempt_number')}"
            if entry_path is not None else None
        )
    if record_type == "read_performance":
        attempt_number = record.get("_attempt_number")
        return (
            f"{entry_path}|{record.get('stage')}|attempt={attempt_number}"
            if entry_path is not None else None
        )
    if record_type == "format_check":
        return entry_path
    if record_type == "state_event":
        return (
            f"{record.get('session_id')}|"
            f"event={record.get('session_event_seq')}"
        )
    if record_type == "stage_checkpoint":
        return record.get("stage")
    if record_type == "runtime":
        return record.get("snapshot_uuid")
    raise RuntimeError(f"未知运行历史记录类型：{record_type}")


def _iter_run_history(
    con: sqlite3.Connection,
    descriptor: dbreader.DatabaseDescriptor,
    *,
    batch_rows: int,
    cancel_check: CancelCheck,
) -> Iterator[dict[str, object]]:
    for table, record_type, _key_column, order_column in _RUN_TABLES:
        if table not in descriptor.tables:
            continue
        columns = _RUN_TABLE_COLUMNS[table]
        actual_columns = descriptor.columns.get(table, ())
        missing = tuple(
            column for column in columns if column not in actual_columns)
        if missing:
            raise core.PreflightError(
                f"运行历史表 {table} 缺少投影列：{'、'.join(missing)}")
        selected = ",".join(f't."{column}"' for column in columns)
        has_entry = "entry_id" in columns
        attempt_select = (
            ",a.attempt_number AS _attempt_number"
            if table == "read_performance" else ""
        )
        attempt_join = (
            " LEFT JOIN entry_attempts a ON a.attempt_id=t.attempt_id"
            if table == "read_performance" else ""
        )
        if has_entry:
            sql = (
                "SELECT r.root_label AS _root_label,e.rel_path AS _rel_path,"
                f"{selected}{attempt_select} FROM {table} t"
                " LEFT JOIN entries e ON e.entry_id=t.entry_id"
                " LEFT JOIN roots r ON r.root_id=e.root_id"
                + attempt_join
                + f' ORDER BY t."{order_column}"'
            )
        else:
            sql = (
                f"SELECT {selected} FROM {table} t"
                f' ORDER BY t."{order_column}"'
            )
        cursor = con.execute(sql)
        names = tuple(str(column[0]) for column in cursor.description)
        while True:
            _check_cancel(cancel_check)
            rows = cursor.fetchmany(batch_rows)
            if not rows:
                break
            for row in rows:
                _check_cancel(cancel_check)
                record = dict(zip(names, tuple(row)))
                root_label = record.pop("_root_label", None)
                rel_path = record.pop("_rel_path", None)
                entry_path = (
                    None if rel_path is None else
                    None if root_label is None else
                    str(root_label) if rel_path == "" else
                    f"{root_label}\\{rel_path}"
                )
                record_key = _run_record_key(
                    record_type, record, entry_path)
                record.pop("entry_id", None)
                record.pop("_attempt_number", None)
                status = _first(
                    record, "session_status", "status", "state",
                    "run_state")
                reason = _first(
                    record, "error_message", "candidate_reason", "end_reason",
                    "last_error_message", "detail")
                yield dict(zip(_RUN_HISTORY_FIELDS, (
                    record_type,
                    record_key,
                    record.get("session_id"),
                    entry_path,
                    record.get("stage"),
                    status,
                    record.get("event"),
                    _first(record, "occurred_at_utc", "recorded_at_utc",
                           "checked_at_utc", "embedded_at_utc"),
                    record.get("started_at_utc"),
                    record.get("updated_at_utc"),
                    record.get("ended_at_utc"),
                    _first(record, "tool_name", "tool"),
                    record.get("tool_version"),
                    record.get("bytes_read"),
                    record.get("elapsed_seconds"),
                    record.get("decision"),
                    reason,
                    _json_text(record),
                )))


def _diff_path_sql(label: str, relative: str) -> str:
    return (
        f"CASE WHEN {relative} IS NULL THEN NULL WHEN {relative}=''"
        f" THEN {label} ELSE {label} || '\\' || {relative} END"
    )


def _iter_diff_overview(
    con: sqlite3.Connection,
    descriptor: dbreader.DatabaseDescriptor,
    *,
    batch_rows: int,
    cancel_check: CancelCheck,
) -> Iterator[dict[str, object]]:
    del batch_rows
    columns = (
        "diff_uuid", "schema_version", "old_schema_version",
        "new_schema_version", "old_snapshot_uuid", "new_snapshot_uuid",
        "old_snapshot_file", "new_snapshot_file", "old_hash_coverage",
        "new_hash_coverage", "root_mapping_json", "forced", "tool_version",
        "created_at_utc", "counts_json",
    )
    cursor = con.execute(
        "SELECT " + ",".join(columns) + " FROM diff_info WHERE id=1")
    row = cursor.fetchone()
    if row is None:
        raise core.PreflightError("Diff 概览缺少 diff_info id=1")
    info = dict(zip(columns, tuple(row)))
    labels = {
        "diff_uuid": "Diff UUID",
        "schema_version": "数据库结构版本",
        "old_schema_version": "基准结构版本",
        "new_schema_version": "对比结构版本",
        "old_snapshot_uuid": "基准快照 UUID",
        "new_snapshot_uuid": "对比快照 UUID",
        "old_snapshot_file": "基准快照文件",
        "new_snapshot_file": "对比快照文件",
        "old_hash_coverage": "基准哈希覆盖",
        "new_hash_coverage": "对比哈希覆盖",
        "forced": "是否允许文件名指纹缺失",
        "tool_version": "Diff 数据库生成程序版本",
        "created_at_utc": "Diff 数据库生成时间 (UTC)",
        "root_mapping_json": "根目录名对应关系",
        "counts_json": "封存声明计数",
    }
    for key in (
        "diff_uuid", "schema_version", "old_schema_version",
        "new_schema_version", "old_snapshot_uuid", "new_snapshot_uuid",
        "old_snapshot_file", "new_snapshot_file", "old_hash_coverage",
        "new_hash_coverage", "forced", "tool_version", "created_at_utc",
    ):
        _check_cancel(cancel_check)
        yield _overview_row(
            "identity", key, labels.get(key, key), info.get(key))
    for key in ("root_mapping_json", "counts_json"):
        value = info.get(key)
        if value:
            try:
                value = json.loads(str(value))
            except (TypeError, ValueError) as exc:
                raise core.PreflightError(
                    f"diff_info.{key} 无法解析：{exc}") from exc
        yield _overview_row(
            "declared", key, labels.get(key, key), value)
    for status, count in con.execute(
            "SELECT status,COUNT(*) FROM diff_entries"
            " GROUP BY status ORDER BY status"):
        yield _overview_row(
            "file_status", str(status),
            dbparse._EXCEL_VALUE_NAMES.get(str(status), str(status)),
            int(count))
    yield _overview_row(
        "compatibility", "mode", "兼容模式",
        dbparse.compatibility_mode(descriptor),
    )


def _iter_diff_files(
    con: sqlite3.Connection,
    descriptor: dbreader.DatabaseDescriptor,
    *,
    batch_rows: int,
    cancel_check: CancelCheck,
) -> Iterator[dict[str, object]]:
    del descriptor
    sql = (
        "SELECT d.path_key,d.old_root_label,d.new_root_label,d.old_rel_path,"
        "d.new_rel_path,"
        f"{_diff_path_sql('d.old_root_label', 'd.old_rel_path')} AS old_path,"
        f"{_diff_path_sql('d.new_root_label', 'd.new_rel_path')} AS new_path,"
        "d.status,d.evidence,d.reason,d.old_size,d.new_size,d.old_mtime_utc,"
        "d.new_mtime_utc,d.old_hash_hex,d.new_hash_hex,d.old_hash_origin,"
        "d.new_hash_origin,d.metadata_changed,g.hash_hex AS group_hash"
        " FROM diff_entries d LEFT JOIN diff_hash_groups g"
        " ON g.group_id=d.group_id ORDER BY d.status,d.path_key"
    )
    yield from _iter_query(
        con, sql, _DIFF_FILE_FIELDS, batch_rows=batch_rows,
        cancel_check=cancel_check)


def _iter_diff_directories(
    con: sqlite3.Connection,
    descriptor: dbreader.DatabaseDescriptor,
    *,
    batch_rows: int,
    cancel_check: CancelCheck,
) -> Iterator[dict[str, object]]:
    del descriptor
    sql = (
        "SELECT d.path_key,d.old_root_label,d.new_root_label,d.old_rel_path,"
        "d.new_rel_path,"
        f"{_diff_path_sql('d.old_root_label', 'd.old_rel_path')} AS old_path,"
        f"{_diff_path_sql('d.new_root_label', 'd.new_rel_path')} AS new_path,"
        "d.status,d.old_enum_status,d.new_enum_status,d.reason"
        " FROM diff_dirs d ORDER BY d.path_key"
    )
    yield from _iter_query(
        con, sql, _DIFF_DIRECTORY_FIELDS, batch_rows=batch_rows,
        cancel_check=cancel_check)


def _iter_diff_groups(
    con: sqlite3.Connection,
    descriptor: dbreader.DatabaseDescriptor,
    *,
    batch_rows: int,
    cancel_check: CancelCheck,
) -> Iterator[dict[str, object]]:
    del descriptor
    sql = (
        "SELECT hash_hex,old_count,new_count,old_hardlink_sets,"
        "new_hardlink_sets,classification FROM diff_hash_groups"
        " ORDER BY hash_hex"
    )
    yield from _iter_query(
        con, sql, _DIFF_GROUP_FIELDS, batch_rows=batch_rows,
        cancel_check=cancel_check)


def _iter_diff_gaps(
    con: sqlite3.Connection,
    descriptor: dbreader.DatabaseDescriptor,
    *,
    batch_rows: int,
    cancel_check: CancelCheck,
) -> Iterator[dict[str, object]]:
    del descriptor
    sql = (
        "SELECT side,root_label,rel_path,"
        "CASE WHEN rel_path='' THEN root_label"
        " ELSE root_label || '\\' || rel_path END AS logical_path,"
        "enum_status,affected_estimate FROM diff_subtrees"
        " ORDER BY side,root_label,rel_path"
    )
    yield from _iter_query(
        con, sql, _DIFF_GAP_FIELDS, batch_rows=batch_rows,
        cancel_check=cancel_check)


def _iter_evidence_notes(
    con: sqlite3.Connection,
    descriptor: dbreader.DatabaseDescriptor,
    *,
    batch_rows: int,
    cancel_check: CancelCheck,
) -> Iterator[dict[str, object]]:
    del descriptor, batch_rows
    row = con.execute(
        "SELECT old_schema_version,new_schema_version,old_hash_coverage,"
        "new_hash_coverage,forced,counts_json FROM diff_info WHERE id=1"
    ).fetchone()
    if row is None:
        raise core.PreflightError("证据说明缺少 diff_info id=1")
    old_schema, new_schema, old_hash, new_hash, forced, counts_text = row
    for key, value in (
        ("old_schema_version", old_schema),
        ("new_schema_version", new_schema),
        ("old_hash_coverage", old_hash),
        ("new_hash_coverage", new_hash),
        ("forced", forced),
    ):
        _check_cancel(cancel_check)
        yield {
            "topic": "input",
            "key": key,
            "value": _display_value(value),
            "state": None,
            "reason": None,
        }
    try:
        counts = json.loads(str(counts_text)) if counts_text else {}
    except (TypeError, ValueError) as exc:
        raise core.PreflightError(
            f"Diff counts_json 无法解析：{exc}") from exc
    for capability_id, capability in sorted(
            dict(counts.get("comparison_capabilities") or {}).items()):
        yield {
            "topic": "capability",
            "key": capability_id,
            "value": _json_text({
                "old": capability.get("old"),
                "new": capability.get("new"),
            }),
            "state": capability.get("state"),
            "reason": capability.get("reason"),
        }
    for topic in ("metadata_evidence", "payload_rows", "snapshot_schemas"):
        if topic in counts:
            yield {
                "topic": "summary",
                "key": topic,
                "value": _display_value(counts[topic]),
                "state": None,
                "reason": None,
            }


_ITERATORS = {
    ("snapshot", "overview"): _iter_snapshot_overview,
    ("snapshot", "issues"): _iter_snapshot_issues,
    ("snapshot", "files"): _iter_files,
    ("snapshot", "directories"): _iter_directories,
    ("snapshot", "hashes"): _iter_hashes,
    ("snapshot", "media_streams"): _iter_media_streams,
    ("snapshot", "archives"): _iter_archives,
    ("snapshot", "raw_payloads"): _iter_raw_payloads,
    ("snapshot", "diagnostics"): _iter_diagnostics,
    ("snapshot", "run_history"): _iter_run_history,
    ("diff", "overview"): _iter_diff_overview,
    ("diff", "file_changes"): _iter_diff_files,
    ("diff", "directory_changes"): _iter_diff_directories,
    ("diff", "content_groups"): _iter_diff_groups,
    ("diff", "enumeration_gaps"): _iter_diff_gaps,
    ("diff", "evidence_notes"): _iter_evidence_notes,
}


def _entry_iterator(table: str, columns: tuple[str, ...]):
    def iterate(
        con: sqlite3.Connection,
        descriptor: dbreader.DatabaseDescriptor,
        *,
        batch_rows: int,
        cancel_check: CancelCheck,
    ) -> Iterator[dict[str, object]]:
        del descriptor
        definition = _DEFINITIONS[("snapshot", {
            "photo_metadata": "photo_metadata",
            "video_metadata": "video_metadata",
            "video_gps_points": "video_gps",
            "working_metadata": "working_metadata",
            "document_metadata": "document_metadata",
        }[table])]
        order_suffix = (
            ",t.timestamp_seconds,t.point_index"
            if table == "video_gps_points" else ""
        )
        yield from _iter_entry_table(
            con,
            table,
            columns,
            definition.fields,
            batch_rows=batch_rows,
            cancel_check=cancel_check,
            order_suffix=order_suffix,
        )
    return iterate


_ITERATORS.update({
    ("snapshot", "photo_metadata"): _entry_iterator(
        "photo_metadata", _PHOTO_COLUMNS),
    ("snapshot", "video_metadata"): _entry_iterator(
        "video_metadata", _VIDEO_COLUMNS),
    ("snapshot", "video_gps"): _entry_iterator(
        "video_gps_points", _GPS_COLUMNS),
    ("snapshot", "working_metadata"): _entry_iterator(
        "working_metadata", _WORKING_COLUMNS),
    ("snapshot", "document_metadata"): _entry_iterator(
        "document_metadata", _DOCUMENT_COLUMNS),
})


def iter_module_rows(
    con: sqlite3.Connection,
    descriptor: dbreader.DatabaseDescriptor,
    module_id: str,
    *,
    batch_rows: int = DEFAULT_BATCH_ROWS,
    cancel_check: CancelCheck = None,
) -> Iterator[dict[str, object]]:
    """流式输出一个 available 模块的稳定行；调用方持有一致只读事务。"""
    if batch_rows <= 0:
        raise ValueError("batch_rows 必须大于 0")
    status = {
        item.spec.module_id: item
        for item in dbparse.parse_module_statuses(descriptor)
    }.get(module_id)
    if status is None:
        raise core.PreflightError(
            f"当前数据库类型没有解析模块：{module_id}")
    if not status.selectable:
        detail = f"：{status.reason}" if status.reason else ""
        state_label = dbparse.PARSE_MODULE_STATE_LABELS.get(
            status.state, status.state)
        raise core.PreflightError(
            f"解析模块 {module_id} 的状态为「{state_label}」，"
            f"不可读取{detail}")
    try:
        iterator = _ITERATORS[(descriptor.database_type, module_id)]
    except KeyError as exc:
        raise RuntimeError(f"解析模块 {module_id} 缺少投影实现") from exc
    yield from iterator(
        con,
        descriptor,
        batch_rows=batch_rows,
        cancel_check=cancel_check,
    )
