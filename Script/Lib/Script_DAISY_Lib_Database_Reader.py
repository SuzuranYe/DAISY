"""DAISY 统一只读数据库识别与能力探测层。

本模块只负责读取数据库事实：类型、schema、封存状态、实际表列和稳定能力。
它不迁移、不补列、不创建索引，也不把旧数据库缺失的未来能力伪装成空结果。
文件名指纹和各命令的业务参数仍由调用方负责。
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from typing import Iterable, Iterator
import zlib

import Script_DAISY_Lib_Snapshot_Core as core
import Script_DAISY_Lib_Scan_State as state_contract


CAPABILITY_STATES = frozenset((
    "available", "empty", "unavailable", "incompatible", "invalid",
))
DATABASE_TYPES = frozenset(("snapshot", "diff"))
READABLE_SNAPSHOT_SCHEMAS = frozenset((3, 4))

_SCHEMA4_CORE_TABLES = frozenset((
    "archive_members",
    "archive_metadata",
    "audio_streams",
    "dirs",
    "document_metadata",
    "entries",
    "errors",
    "hashes",
    "metadata_diagnostics",
    "photo_metadata",
    "raw_payloads",
    "roots",
    "run_events",
    "snapshot_info",
    "snapshot_manifest",
    "video_gps_points",
    "video_metadata",
    "video_streams",
    "working_metadata",
))


_SCHEMA4_REQUIRED = {
    "run_sessions": (
        "session_id", "session_number", "parent_session_id", "session_kind",
        "session_status", "started_at_utc", "updated_at_utc", "ended_at_utc",
        "hostname", "pid", "process_start_token", "lease_id",
        "lease_acquired_at_utc", "lease_heartbeat_at_utc",
        "lease_expires_at_utc", "scanner_version", "resume_contract",
        "config_json", "tools_json", "end_reason",
    ),
    "snapshot_runtime": (
        "id", "snapshot_uuid", "schema_version", "data_contract",
        "min_reader_version", "resume_contract", "projection_contract",
        "filename_layout_version", "run_state", "state_revision",
        "resume_hint", "active_session_id", "current_stage", "created_at_utc",
        "updated_at_utc", "last_checkpoint_at_utc", "output_dir",
        "partial_path", "publish_stem_path", "event_log_path",
        "published_path_pattern", "last_error_code", "last_error_message",
    ),
    "stage_checkpoints": (
        "stage", "stage_order", "state", "session_id", "items_done",
        "items_total", "bytes_done", "bytes_total", "error_count",
        "current_entry_id", "started_at_utc", "updated_at_utc",
        "finished_at_utc", "checkpoint_json",
    ),
    "run_state_events": (
        "event_id", "session_id", "session_event_seq", "occurred_at_utc",
        "event", "from_state", "to_state", "state_revision", "payload_json",
    ),
    "entry_attempts": (
        "attempt_id", "entry_id", "session_id", "stage", "attempt_number",
        "status", "tool_name", "tool_version", "started_at_utc",
        "last_progress_at_utc", "ended_at_utc", "source_size_bytes",
        "source_modified_at_utc", "bytes_read", "final_offset", "stall_count",
        "max_stall_seconds", "decision", "decision_source", "end_reason",
        "error_code", "error_message", "result_json",
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
}


@dataclass(frozen=True)
class DatabaseCapability:
    """一个稳定模块在当前数据库中的可解释状态。"""

    capability_id: str
    title: str
    state: str
    row_count: int | None = None
    reason: str | None = None
    queryable: bool = False

    def __post_init__(self) -> None:
        if self.state not in CAPABILITY_STATES:
            raise ValueError(f"未知能力状态：{self.state}")

    @property
    def selectable(self) -> bool:
        """只有实际有记录的模块才进入“全选”。"""
        return self.state == "available"

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.capability_id,
            "title": self.title,
            "state": self.state,
            "row_count": self.row_count,
            "reason": self.reason,
            "selectable": self.selectable,
            "queryable": self.queryable,
        }


@dataclass(frozen=True)
class DatabaseDescriptor:
    """一次只读识别得到的数据库身份与能力快照。"""

    path: str
    database_type: str
    schema_version: int
    source_version: str | None
    lifecycle: str
    status: str | None
    database_integrity: str | None
    sqlite_integrity: str | None
    path_key_rule: int | None
    data_contract: str | None
    min_reader_version: str | None
    tables: frozenset[str]
    columns: dict[str, frozenset[str]]
    capabilities: dict[str, DatabaseCapability]
    identity: dict[str, object]
    warnings: tuple[str, ...] = ()

    @property
    def sealed(self) -> bool:
        return self.lifecycle == "sealed"

    @property
    def kind(self) -> str:
        return self.database_type if self.sealed else self.lifecycle

    def capability(self, capability_id: str) -> DatabaseCapability:
        try:
            return self.capabilities[capability_id]
        except KeyError as exc:
            raise KeyError(f"未知能力：{capability_id}") from exc

    def as_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "database_type": self.database_type,
            "kind": self.kind,
            "schema_version": self.schema_version,
            "source_version": self.source_version,
            "lifecycle": self.lifecycle,
            "status": self.status,
            "database_integrity": self.database_integrity,
            "sqlite_integrity": self.sqlite_integrity,
            "path_key_rule": self.path_key_rule,
            "data_contract": self.data_contract,
            "min_reader_version": self.min_reader_version,
            "tables": sorted(self.tables),
            "columns": {
                name: sorted(values)
                for name, values in sorted(self.columns.items())
            },
            "capabilities": {
                key: value.as_dict()
                for key, value in self.capabilities.items()
            },
            "identity": dict(self.identity),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class DatabaseProbe:
    """供 GUI 使用的不抛异常探测结果。"""

    state: str
    descriptor: DatabaseDescriptor | None = None
    error: str | None = None

    @property
    def valid(self) -> bool:
        return self.state == "valid" and self.descriptor is not None

    def as_dict(self) -> dict[str, object]:
        return {
            "state": self.state,
            "error": self.error,
            "database": (
                self.descriptor.as_dict() if self.descriptor else None
            ),
        }


@dataclass(frozen=True)
class ProjectionRow:
    """不含运行身份与观察时间的稳定业务投影行。"""

    section: str
    key: tuple[object, ...]
    values: tuple[object, ...]


SNAPSHOT_DIFF_PROJECTION = "daisy-diff-input-v1"

_DIFF_PROJECTION_CAPABILITIES = (
    "files",
    "directories",
    "hashes",
    "raw_payloads",
    "format_checks",
    "run_sessions",
    "entry_attempts",
    "read_performance",
)

_DIFF_PROJECTION_REQUIRED = {
    "roots": (
        "root_id", "root_label", "root_path", "enum_status",
    ),
    "dirs": (
        "root_id", "rel_path", "path_key", "enum_status",
    ),
    "entries": (
        "entry_id", "root_id", "rel_path", "path_key", "size_bytes",
        "modified_at_utc", "attributes", "is_placeholder", "meta_status",
        "hash_status", "volume_serial", "file_index_hex",
    ),
}

_DIFF_HASH_COLUMNS = frozenset((
    "entry_id", "algorithm", "hash_hex", "origin",
    "source_snapshot_uuid", "source_computed_at_utc",
    "finished_at_utc", "status",
))

_DIFF_RAW_PAYLOAD_COLUMNS = frozenset((
    "entry_id", "provider", "payload_zlib", "payload_sha256",
    "provider_version",
))

_VOLATILE_EXIFTOOL_TAGS = frozenset((
    "sourcefile",
    "directory",
    "fileaccessdate",
))


@dataclass(frozen=True)
class _CapabilitySpec:
    capability_id: str
    title: str
    required: tuple[tuple[str, tuple[str, ...]], ...]
    count_tables: tuple[str, ...] = ()
    count_sql: str | None = None


_SNAPSHOT_CORE_COLUMNS = (
    "id", "snapshot_uuid", "schema_version", "path_key_rule",
    "scan_status", "database_integrity", "hash_coverage", "scanner_version",
)
_DIFF_CORE_COLUMNS = (
    "id", "diff_uuid", "schema_version", "old_schema_version",
    "new_schema_version", "old_snapshot_uuid", "new_snapshot_uuid",
    "tool_version",
)


SNAPSHOT_CAPABILITY_SPECS = (
    _CapabilitySpec(
        "overview", "数据概览",
        (("snapshot_info", _SNAPSHOT_CORE_COLUMNS),),
        ("snapshot_info",),
    ),
    _CapabilitySpec(
        "issues", "问题摘要",
        (
            ("roots", ("root_id", "root_label", "enum_status")),
            ("dirs", ("dir_id", "root_id", "enum_status")),
            ("entries", ("entry_id", "meta_status", "hash_status")),
            ("errors", ("error_pk", "stage", "error_code", "message")),
        ),
        count_sql=(
            "SELECT"
            " (SELECT COUNT(*) FROM errors) +"
            " (SELECT COUNT(*) FROM dirs WHERE enum_status<>'ok') +"
            " (SELECT COUNT(*) FROM roots WHERE enum_status='failed') +"
            " (SELECT COUNT(*) FROM entries WHERE"
            "  meta_status IN ('error','timeout','unstable')"
            "  OR hash_status IN ('error','unstable'))"
        ),
    ),
    _CapabilitySpec(
        "files", "文件清单",
        (
            ("roots", ("root_id", "root_path", "root_label")),
            ("entries", (
                "entry_id", "root_id", "rel_path", "path_key", "size_bytes",
                "modified_at_utc", "meta_status", "hash_status",
            )),
        ),
        ("entries",),
    ),
    _CapabilitySpec(
        "directories", "目录清单",
        (("dirs", ("dir_id", "root_id", "rel_path", "enum_status")),),
        ("dirs",),
    ),
    _CapabilitySpec(
        "hashes", "逐文件哈希",
        (("hashes", (
            "entry_id", "algorithm", "hash_hex", "origin", "status",
        )),),
        ("hashes",),
    ),
    _CapabilitySpec(
        "photo_metadata", "照片信息",
        (("photo_metadata", ("entry_id",)),),
        ("photo_metadata",),
    ),
    _CapabilitySpec(
        "video_metadata", "视频信息",
        (("video_metadata", ("entry_id",)),),
        ("video_metadata",),
    ),
    _CapabilitySpec(
        "video_gps", "视频定位",
        (("video_gps_points", ("entry_id", "point_index")),),
        ("video_gps_points",),
    ),
    _CapabilitySpec(
        "media_streams", "媒体轨道",
        (
            ("video_streams", ("entry_id", "stream_index")),
            ("audio_streams", ("entry_id", "stream_index")),
        ),
        ("video_streams", "audio_streams"),
    ),
    _CapabilitySpec(
        "working_metadata", "工作文件",
        (("working_metadata", ("entry_id",)),),
        ("working_metadata",),
    ),
    _CapabilitySpec(
        "document_metadata", "文档信息",
        (("document_metadata", ("entry_id",)),),
        ("document_metadata",),
    ),
    _CapabilitySpec(
        "archives", "压缩归档",
        (
            ("archive_metadata", ("entry_id",)),
            ("archive_members", ("entry_id", "member_path")),
        ),
        ("archive_metadata", "archive_members"),
    ),
    _CapabilitySpec(
        "raw_payloads", "原始数据",
        (("raw_payloads", (
            "entry_id", "provider", "payload_zlib", "payload_sha256",
        )),),
        ("raw_payloads",),
    ),
    _CapabilitySpec(
        "diagnostics", "诊断证据",
        (
            ("errors", ("error_pk", "error_code", "message")),
            ("metadata_diagnostics", (
                "diagnostic_pk", "severity", "diagnostic_code", "message",
            )),
        ),
        ("errors", "metadata_diagnostics"),
    ),
    _CapabilitySpec(
        "run_history", "运行历史",
        (
            ("snapshot_manifest", ("manifest_version", "manifest_json")),
            ("run_events", ("event_seq", "event", "payload_json")),
        ),
        ("snapshot_manifest", "run_events"),
    ),
    _CapabilitySpec(
        "run_sessions", "运行会话",
        (("run_sessions", ("session_id",)),),
        ("run_sessions",),
    ),
    _CapabilitySpec(
        "entry_attempts", "尝试记录",
        (("entry_attempts", ("attempt_id", "entry_id", "stage")),),
        ("entry_attempts",),
    ),
    _CapabilitySpec(
        "read_performance", "读取性能",
        (("read_performance", ("entry_id", "elapsed_seconds")),),
        ("read_performance",),
    ),
    _CapabilitySpec(
        "format_checks", "格式校验",
        (("format_checks", ("entry_id", "status", "validator")),),
        ("format_checks",),
    ),
)


DIFF_CAPABILITY_SPECS = (
    _CapabilitySpec(
        "overview", "对比概览",
        (("diff_info", _DIFF_CORE_COLUMNS),),
        ("diff_info",),
    ),
    _CapabilitySpec(
        "file_changes", "文件变化",
        (("diff_entries", (
            "diff_entry_id", "status", "evidence", "old_rel_path",
            "new_rel_path",
        )),),
        ("diff_entries",),
    ),
    _CapabilitySpec(
        "directory_changes", "目录变化",
        (("diff_dirs", (
            "diff_dir_id", "status", "old_rel_path", "new_rel_path",
        )),),
        ("diff_dirs",),
    ),
    _CapabilitySpec(
        "content_groups", "内容分组",
        (("diff_hash_groups", (
            "group_id", "hash_hex", "old_count", "new_count",
        )),),
        ("diff_hash_groups",),
    ),
    _CapabilitySpec(
        "enumeration_gaps", "枚举缺口",
        (("diff_subtrees", (
            "subtree_id", "side", "rel_path", "enum_status",
        )),),
        ("diff_subtrees",),
    ),
    _CapabilitySpec(
        "evidence_notes", "证据说明",
        (
            ("diff_info", (
                "old_schema_version", "new_schema_version",
                "old_hash_coverage", "new_hash_coverage", "forced",
            )),
            ("diff_entries", ("status", "evidence", "reason")),
        ),
        ("diff_info",),
    ),
)


_SNAPSHOT_METADATA_CAPABILITIES = (
    "photo_metadata",
    "video_metadata",
    "video_gps",
    "media_streams",
    "working_metadata",
    "document_metadata",
    "archives",
)

_BUSINESS_CAPABILITIES = (
    "overview",
    "issues",
    "files",
    "directories",
    "hashes",
    "photo_metadata",
    "video_metadata",
    "video_gps",
    "media_streams",
    "working_metadata",
    "document_metadata",
    "archives",
    "raw_payloads",
    "diagnostics",
    "format_checks",
)

_SCHEMA4_RUN_HISTORY_TABLES = (
    "snapshot_manifest",
    "run_events",
    "run_sessions",
    "entry_attempts",
    "read_performance",
    "format_checks",
    "run_state_events",
    "stage_checkpoints",
    "snapshot_runtime",
)

_ENTRY_PROJECTION_TABLES = (
    ("hashes", "hashes", (
        "hash_pk", "entry_id", "source_snapshot_uuid",
        "source_computed_at_utc", "started_at_utc", "finished_at_utc",
    )),
    ("photo_metadata", "photo_metadata", (
        "entry_id", "parsed_at_utc",
    )),
    ("video_metadata", "video_metadata", (
        "entry_id", "parsed_at_utc",
    )),
    ("video_gps", "video_gps_points", (
        "gps_point_pk", "entry_id",
    )),
    ("media_streams", "video_streams", (
        "stream_pk", "entry_id",
    )),
    ("media_streams", "audio_streams", (
        "stream_pk", "entry_id",
    )),
    ("working_metadata", "working_metadata", (
        "entry_id", "parsed_at_utc",
    )),
    ("document_metadata", "document_metadata", (
        "entry_id", "parsed_at_utc",
    )),
    ("archives", "archive_metadata", (
        "entry_id", "parsed_at_utc",
    )),
    ("archives", "archive_members", (
        "entry_id",
    )),
    ("raw_payloads", "raw_payloads", (
        "payload_pk", "entry_id", "parsed_at_utc",
    )),
    ("diagnostics", "metadata_diagnostics", (
        "diagnostic_pk", "entry_id", "observed_at_utc",
    )),
    ("format_checks", "format_checks", (
        "entry_id", "attempt_id", "checked_at_utc", "result_revision",
    )),
)


def _quote_identifier(value: str) -> str:
    return '"' + str(value).replace('"', '""') + '"'


def require_capabilities(
    descriptor: DatabaseDescriptor,
    *capability_ids: str,
) -> tuple[DatabaseCapability, ...]:
    """要求模块可解释；真实空表仍是合法能力，不等同于未记录。"""
    accepted = []
    for capability_id in capability_ids:
        try:
            capability = descriptor.capability(capability_id)
        except KeyError as exc:
            raise core.PreflightError(
                f"数据库读取器没有登记能力 {capability_id}") from exc
        if capability.state not in ("available", "empty"):
            detail = f"：{capability.reason}" if capability.reason else ""
            raise core.PreflightError(
                f"数据库能力 {capability_id} 为 {capability.state}{detail}")
        accepted.append(capability)
    return tuple(accepted)


def require_queryable_capabilities(
    descriptor: DatabaseDescriptor,
    *capability_ids: str,
) -> tuple[DatabaseCapability, ...]:
    """要求物理投影可安全查询，但允许运行配置明确跳过的 schema 3 空表。"""
    specs = (
        SNAPSHOT_CAPABILITY_SPECS
        if descriptor.database_type == "snapshot"
        else DIFF_CAPABILITY_SPECS
    )
    by_id = {spec.capability_id: spec for spec in specs}
    accepted = []
    for capability_id in capability_ids:
        try:
            capability = descriptor.capability(capability_id)
            spec = by_id[capability_id]
        except KeyError as exc:
            raise core.PreflightError(
                f"数据库读取器没有登记能力 {capability_id}") from exc
        missing_tables = [
            table for table, _required in spec.required
            if table not in descriptor.tables
        ]
        missing_columns = [
            f"{table}.{column}"
            for table, required in spec.required
            if table in descriptor.tables
            for column in sorted(
                set(required) - set(descriptor.columns.get(table, ())))
        ]
        if missing_tables:
            raise core.PreflightError(
                f"数据库能力 {capability_id} 缺少表："
                + "、".join(missing_tables))
        if missing_columns:
            raise core.PreflightError(
                f"数据库能力 {capability_id} 缺少列："
                + "、".join(missing_columns))
        if not capability.queryable:
            detail = f"：{capability.reason}" if capability.reason else ""
            raise core.PreflightError(
                f"数据库能力 {capability_id} 不可安全查询"
                f"（{capability.state}）{detail}")
        accepted.append(capability)
    return tuple(accepted)


def _schema_inventory(
    con: sqlite3.Connection,
) -> tuple[frozenset[str], dict[str, frozenset[str]]]:
    try:
        tables = frozenset(
            str(row[0]) for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
                " AND name NOT LIKE 'sqlite_%' ORDER BY name")
        )
        columns = {}
        for table in tables:
            rows = con.execute(
                f"PRAGMA table_info({_quote_identifier(table)})").fetchall()
            columns[table] = frozenset(str(row[1]) for row in rows)
        return tables, columns
    except sqlite3.Error as exc:
        raise core.PreflightError(f"数据库结构无法读取：{exc}") from exc


def _require_structure(
    tables: frozenset[str],
    columns: dict[str, frozenset[str]],
    table: str,
    required_columns: Iterable[str],
    artifact: str,
) -> None:
    if table not in tables:
        raise core.PreflightError(f"{artifact} 缺少身份表 {table}")
    missing = sorted(set(required_columns) - set(columns.get(table, ())))
    if missing:
        raise core.PreflightError(
            f"{artifact} 的 {table} 缺少必要列：{'、'.join(missing)}")


def _require_snapshot_schema(schema_version: object, artifact: str) -> int:
    try:
        version = int(schema_version)
    except (TypeError, ValueError) as exc:
        raise core.PreflightError(
            f"{artifact} schema_version={schema_version!r} 无法解释") from exc
    if version not in READABLE_SNAPSHOT_SCHEMAS:
        supported = "、".join(
            str(value) for value in sorted(READABLE_SNAPSHOT_SCHEMAS))
        raise core.PreflightError(
            f"{artifact} schema_version={version} 非 Reader 可读范围"
            f"（{supported}）")
    return version


def _require_schema4_structure(
    tables: frozenset[str],
    columns: dict[str, frozenset[str]],
) -> None:
    missing_core = sorted(_SCHEMA4_CORE_TABLES - tables)
    if missing_core:
        raise core.PreflightError(
            "schema_version=4 结构不完整：缺少 schema 3 业务表："
            + "、".join(missing_core))
    for table, required_columns in _SCHEMA4_REQUIRED.items():
        try:
            _require_structure(
                tables,
                columns,
                table,
                required_columns,
                "schema_version=4 快照",
            )
        except core.PreflightError as exc:
            raise core.PreflightError(
                f"schema_version=4 结构不完整：{exc}") from exc


def _schema4_runtime(
    con: sqlite3.Connection,
    snapshot_uuid: object,
    scan_status: object,
    database_integrity: object,
) -> dict[str, object]:
    try:
        row = con.execute(
            "SELECT snapshot_uuid,schema_version,data_contract,"
            " min_reader_version,resume_contract,projection_contract,"
            " filename_layout_version,run_state,state_revision,resume_hint,"
            " active_session_id,current_stage,output_dir,partial_path,"
            " publish_stem_path,event_log_path,published_path_pattern"
            " FROM snapshot_runtime WHERE id=1"
        ).fetchone()
    except sqlite3.Error as exc:
        raise core.PreflightError(
            f"schema_version=4 运行身份无法读取：{exc}") from exc
    if row is None:
        raise core.PreflightError(
            "schema_version=4 快照缺少 snapshot_runtime id=1")
    keys = (
        "snapshot_uuid", "schema_version", "data_contract",
        "min_reader_version", "resume_contract", "projection_contract",
        "filename_layout_version", "run_state", "state_revision",
        "resume_hint", "active_session_id", "current_stage", "output_dir",
        "partial_path", "publish_stem_path", "event_log_path",
        "published_path_pattern",
    )
    runtime = dict(zip(keys, tuple(row)))
    expected = {
        "snapshot_uuid": str(snapshot_uuid),
        "schema_version": state_contract.SCHEMA_VERSION,
        "data_contract": state_contract.DATA_CONTRACT,
        "min_reader_version": state_contract.MIN_READER_VERSION,
        "resume_contract": state_contract.RESUME_CONTRACT,
        "projection_contract": state_contract.PROJECTION_CONTRACT,
        "filename_layout_version": state_contract.FILENAME_LAYOUT_VERSION,
    }
    mismatches = [
        f"{key}={runtime[key]!r}（应为 {value!r}）"
        for key, value in expected.items()
        if runtime[key] != value
    ]
    if mismatches:
        raise core.PreflightError(
            "schema_version=4 契约身份不一致：" + "；".join(mismatches))
    run_state = str(runtime["run_state"])
    if run_state not in state_contract.RUN_STATES:
        raise core.PreflightError(
            f"schema_version=4 run_state 无法解释：{run_state}")
    if runtime["resume_hint"] not in state_contract.RESUME_HINTS:
        raise core.PreflightError(
            "schema_version=4 resume_hint 无法解释："
            + str(runtime["resume_hint"]))
    try:
        revision = int(runtime["state_revision"])
    except (TypeError, ValueError) as exc:
        raise core.PreflightError(
            "schema_version=4 state_revision 无法解释") from exc
    if revision <= 0:
        raise core.PreflightError(
            "schema_version=4 state_revision 必须大于 0")

    coarse_status = str(scan_status)
    coarse_integrity = str(database_integrity)
    if run_state in ("sealed_unpublished", "published"):
        expected_coarse = ("complete", "ok")
        valid_coarse = (coarse_status, coarse_integrity) == expected_coarse
    elif run_state in (
            "paused", "stopped", "failed_recoverable"):
        expected_coarse = ("interrupted", "pending")
        valid_coarse = (coarse_status, coarse_integrity) == expected_coarse
    elif run_state == "failed_terminal":
        expected_coarse = ("interrupted", "pending／failed")
        valid_coarse = (
            coarse_status == "interrupted"
            and coarse_integrity in ("pending", "failed")
        )
    else:
        expected_coarse = ("running", "pending")
        valid_coarse = (coarse_status, coarse_integrity) == expected_coarse
    if not valid_coarse:
        raise core.PreflightError(
            "schema_version=4 粗粒度状态与 run_state 不一致："
            f"run_state={run_state}，scan_status={coarse_status}，"
            f"database_integrity={coarse_integrity}，应为 {expected_coarse}")
    return runtime


def _validate_published_filename(
    path: str,
    runtime: dict[str, object],
    warnings: list[str],
    *,
    verify_fingerprint: bool = True,
) -> None:
    pattern_value = runtime.get("published_path_pattern")
    if not pattern_value:
        raise core.PreflightError(
            "schema_version=4 published 快照缺少 published_path_pattern")
    pattern = os.path.abspath(str(pattern_value))
    placeholder = "<SHA256-high32-uppercase>"
    if pattern.count(placeholder) != 1:
        raise core.PreflightError(
            "schema_version=4 published_path_pattern 无法解释")
    if path == "<connection>":
        warnings.append("连接未提供文件路径，未核对 published 文件名指纹")
        return
    expected_prefix, expected_suffix = os.path.basename(pattern).split(
        placeholder)
    basename = os.path.basename(path)
    if not (
            basename.startswith(expected_prefix)
            and basename.endswith(expected_suffix)
            and len(basename) == len(expected_prefix) + 8 + len(expected_suffix)):
        raise core.PreflightError(
            "schema_version=4 published 文件名不符合冻结路径模式："
            f"{basename}；模式：{os.path.basename(pattern)}")
    if os.path.normcase(os.path.dirname(pattern)) != os.path.normcase(
            os.path.dirname(os.path.abspath(path))):
        warnings.append("published 快照已离开原输出目录；文件名模式仍已核对")
    if not verify_fingerprint:
        warnings.append(
            "快速识别未核对 published 内容指纹；正式读取前必须完整复核")
        return
    fingerprint = core.filename_sha256_high32_matches(path)
    if fingerprint is not True:
        detail = "缺少摘要后缀" if fingerprint is None else "摘要后缀不匹配"
        raise core.PreflightError(
            f"schema_version=4 published 文件名{detail}：{basename}")


def _capability_map(
    con: sqlite3.Connection,
    tables: frozenset[str],
    columns: dict[str, frozenset[str]],
    specs: tuple[_CapabilitySpec, ...],
    lifecycle: str,
    state_overrides: dict[str, tuple[str, str]] | None = None,
) -> dict[str, DatabaseCapability]:
    capabilities: dict[str, DatabaseCapability] = {}
    for spec in specs:
        if lifecycle != "sealed":
            capabilities[spec.capability_id] = DatabaseCapability(
                spec.capability_id, spec.title, "invalid", None,
                "数据库尚未完整封存",
            )
            continue
        missing_tables = [
            table for table, _required in spec.required
            if table not in tables
        ]
        if missing_tables:
            capabilities[spec.capability_id] = DatabaseCapability(
                spec.capability_id, spec.title, "unavailable", None,
                "数据库未记录表：" + "、".join(missing_tables),
            )
            continue
        missing_columns = []
        for table, required in spec.required:
            for column in sorted(set(required) - set(columns[table])):
                missing_columns.append(f"{table}.{column}")
        if missing_columns:
            capabilities[spec.capability_id] = DatabaseCapability(
                spec.capability_id, spec.title, "incompatible", None,
                "现有结构无法安全解释，缺少列：" + "、".join(missing_columns),
            )
            continue
        override = (state_overrides or {}).get(spec.capability_id)
        if override is not None:
            state, reason = override
            capabilities[spec.capability_id] = DatabaseCapability(
                spec.capability_id, spec.title, state, None, reason, True,
            )
            continue
        row_count = None
        if spec.count_tables or spec.count_sql:
            try:
                if spec.count_sql:
                    row_count = int(con.execute(spec.count_sql).fetchone()[0])
                else:
                    row_count = sum(
                        int(con.execute(
                            f"SELECT COUNT(*) FROM {_quote_identifier(table)}"
                        ).fetchone()[0])
                        for table in spec.count_tables
                    )
            except (sqlite3.Error, TypeError, ValueError) as exc:
                capabilities[spec.capability_id] = DatabaseCapability(
                    spec.capability_id, spec.title, "incompatible", None,
                    f"记录数无法安全读取：{exc}",
                )
                continue
        state = "empty" if row_count == 0 else "available"
        capabilities[spec.capability_id] = DatabaseCapability(
            spec.capability_id, spec.title, state, row_count, None, True,
        )
    return capabilities


def _schema4_run_history_capability(
    con: sqlite3.Connection,
) -> DatabaseCapability:
    """把 schema 4 的续传／运行表纳入统一运行历史能力。"""
    try:
        row_count = sum(
            int(con.execute(
                f"SELECT COUNT(*) FROM {_quote_identifier(table)}"
            ).fetchone()[0])
            for table in _SCHEMA4_RUN_HISTORY_TABLES
        )
    except (sqlite3.Error, TypeError, ValueError) as exc:
        return DatabaseCapability(
            "run_history",
            "运行历史",
            "incompatible",
            None,
            f"schema 4 运行历史记录数无法安全读取：{exc}",
        )
    return DatabaseCapability(
        "run_history",
        "运行历史",
        "empty" if row_count == 0 else "available",
        row_count,
        None,
        True,
    )


def _nested_object(
    parent: dict[str, object], key: str, label: str, warnings: list[str],
) -> dict[str, object]:
    value = parent.get(key)
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    warnings.append(f"{label} 不是 JSON 对象")
    return {}


def _snapshot_capability_overrides(
    config: dict[str, object],
    manifest: dict[str, object],
    hash_coverage: object,
    schema_version: int,
    warnings: list[str],
) -> tuple[dict[str, tuple[str, str]], dict[str, object]]:
    """按 v1.4.1 内嵌执行证据区分“执行后为空”和“根本未执行”。"""
    manifest_config = _nested_object(
        manifest, "config", "manifest.config", warnings)
    profile = _nested_object(
        manifest, "effective_profile", "manifest.effective_profile", warnings)

    scan_kind_value = (
        profile.get("scan_kind")
        or config.get("phase")
        or manifest_config.get("phase")
    )
    if scan_kind_value is None and (
            config.get("quick") is True
            or manifest_config.get("quick") is True):
        scan_kind_value = "quick"
    scan_kind = (
        str(scan_kind_value).strip().casefold()
        if scan_kind_value is not None else None
    )
    if scan_kind not in ("full", "quick"):
        warnings.append(
            "无法从 config／manifest 确定扫描类型，元数据能力不能安全解释")
        scan_kind = None

    metadata_storage_value = (
        profile.get("metadata_storage")
        or config.get("metadata_storage")
        or manifest_config.get("metadata_storage")
    )
    metadata_storage = (
        str(metadata_storage_value).strip().casefold()
        if metadata_storage_value is not None else None
    )
    if metadata_storage not in (None, "complete", "normalized"):
        warnings.append(
            f"metadata_storage 无法解释：{metadata_storage_value!r}")
        metadata_storage = None

    overrides: dict[str, tuple[str, str]] = {}
    normalized_coverage = str(hash_coverage or "").strip().casefold()
    if normalized_coverage == "none":
        overrides["hashes"] = (
            "unavailable",
            "本次快照未执行哈希阶段（hash_coverage=none）",
        )

    if scan_kind == "quick":
        for capability_id in _SNAPSHOT_METADATA_CAPABILITIES:
            overrides[capability_id] = (
                "unavailable",
                "Quick 快照未执行元数据阶段",
            )
    elif scan_kind is None:
        for capability_id in _SNAPSHOT_METADATA_CAPABILITIES:
            overrides[capability_id] = (
                "incompatible",
                "缺少可解释的扫描类型证据",
            )

    raw_retained = profile.get("raw_payload_retained")
    if scan_kind == "quick" or raw_retained is False:
        overrides["raw_payloads"] = (
            "unavailable",
            "本次快照未保留原始元数据载荷",
        )
    elif raw_retained is True:
        pass
    elif scan_kind == "full" and metadata_storage == "complete":
        pass
    elif scan_kind == "full" and metadata_storage == "normalized":
        overrides["raw_payloads"] = (
            "unavailable",
            "本次快照只保留规范化元数据，未保留原始载荷",
        )
    else:
        overrides["raw_payloads"] = (
            "incompatible",
            "缺少可解释的原始载荷保留策略",
        )

    format_value = (
        profile.get("format_validation")
        or config.get("format_validation")
        or manifest_config.get("format_validation")
    )
    format_mode = (
        str(format_value).strip().casefold()
        if format_value is not None else None
    )
    if schema_version == 4:
        if format_mode in ("off", "none", "disabled", "false", "0"):
            overrides["format_checks"] = (
                "unavailable",
                "本次快照未执行格式校验",
            )
        elif format_mode in (
                "sample", "all", "full", "sampled", "enabled", "true", "1"):
            pass
        else:
            overrides["format_checks"] = (
                "incompatible",
                "schema 4 缺少可解释的格式校验执行配置",
            )

    evidence = {
        "scan_kind": scan_kind,
        "hash_coverage": normalized_coverage or None,
        "metadata_storage": metadata_storage,
        "raw_payload_retained": (
            raw_retained if isinstance(raw_retained, bool) else None
        ),
        "format_validation": format_mode,
    }
    return overrides, evidence


def _read_json_object(
    value: object, label: str, warnings: list[str],
) -> dict[str, object]:
    if value in (None, ""):
        return {}
    try:
        result = json.loads(str(value))
    except (TypeError, ValueError) as exc:
        warnings.append(f"{label} 不是有效 JSON：{exc}")
        return {}
    if not isinstance(result, dict):
        warnings.append(f"{label} 不是 JSON 对象")
        return {}
    return result


def inspect_connection(
    con: sqlite3.Connection,
    path: str = "<connection>",
    *,
    expected_type: str | None = None,
    require_sealed: bool = True,
    verify_integrity: bool = True,
    verify_artifact_fingerprint: bool = True,
) -> DatabaseDescriptor:
    """在既有连接上执行统一探测；调用方仍负责关闭连接。"""
    if expected_type is not None and expected_type not in DATABASE_TYPES:
        raise ValueError(f"未知数据库类型：{expected_type}")
    try:
        con.execute("PRAGMA query_only=ON")
    except sqlite3.Error as exc:
        raise core.PreflightError(f"数据库无法切换为只读查询：{exc}") from exc
    tables, columns = _schema_inventory(con)
    has_snapshot = "snapshot_info" in tables
    has_diff = "diff_info" in tables
    if has_snapshot == has_diff:
        if has_snapshot:
            reason = "同时存在 snapshot_info 和 diff_info，身份冲突"
        else:
            reason = "缺少 snapshot_info 或 diff_info 身份表"
        raise core.PreflightError(f"无法识别 DAISY 数据库：{reason}")
    database_type = "snapshot" if has_snapshot else "diff"
    if expected_type and database_type != expected_type:
        raise core.PreflightError(
            f"数据库类型不符：需要 {expected_type}，实际为 {database_type}")

    warnings: list[str] = []
    normalized_path = (
        os.path.abspath(path) if path != "<connection>" else path
    )
    if database_type == "snapshot":
        _require_structure(
            tables, columns, "snapshot_info", _SNAPSHOT_CORE_COLUMNS,
            "快照数据库",
        )
        try:
            row = con.execute(
                "SELECT snapshot_uuid,schema_version,path_key_rule,scan_status,"
                " database_integrity,hash_coverage,scanner_version,config_json"
                " FROM snapshot_info WHERE id=1").fetchone()
        except sqlite3.Error as exc:
            raise core.PreflightError(f"快照身份无法读取：{exc}") from exc
        if row is None:
            raise core.PreflightError("快照数据库缺少 snapshot_info id=1")
        (snapshot_uuid, schema_version, path_key_rule, scan_status,
         database_integrity, hash_coverage, source_version,
         config_json) = tuple(row)
        schema_version = _require_snapshot_schema(
            schema_version, "快照数据库")
        if path_key_rule != core.PATH_KEY_RULE:
            raise core.PreflightError(
                f"快照 path_key_rule={path_key_rule} 非本工具支持的"
                f" {core.PATH_KEY_RULE}")
        runtime: dict[str, object] | None = None
        if schema_version == 4:
            _require_schema4_structure(tables, columns)
            runtime = _schema4_runtime(
                con, snapshot_uuid, scan_status, database_integrity)
            run_state = str(runtime["run_state"])
            if run_state == "published":
                lifecycle = "sealed"
            elif run_state == "sealed_unpublished":
                lifecycle = "sealed_unpublished"
            elif run_state == "failed_terminal":
                lifecycle = "invalid"
            else:
                lifecycle = "partial"
        else:
            lifecycle = (
                "sealed" if scan_status == "complete"
                and database_integrity == "ok" else "partial"
            )
            if scan_status == "complete" and database_integrity != "ok":
                lifecycle = "invalid"
        if require_sealed and lifecycle != "sealed":
            runtime_detail = (
                f"，run_state={runtime['run_state']}" if runtime else ""
            )
            raise core.PreflightError(
                "快照尚未完整封存："
                f"scan_status={scan_status}，"
                f"database_integrity={database_integrity}{runtime_detail}")
        config = _read_json_object(config_json, "config_json", warnings)
        manifest: dict[str, object] = {}
        if ("snapshot_manifest" in tables
                and "manifest_json" in columns["snapshot_manifest"]):
            manifest_row = con.execute(
                "SELECT manifest_json FROM snapshot_manifest WHERE id=1"
            ).fetchone()
            if manifest_row is not None:
                manifest = _read_json_object(
                    manifest_row[0], "manifest_json", warnings)
        if runtime is not None:
            data_contract = runtime["data_contract"]
            min_reader = runtime["min_reader_version"]
        else:
            data_contract = manifest.get("data_contract") or config.get(
                "data_contract")
            min_reader = manifest.get("min_reader_version") or config.get(
                "min_reader_version")
        capability_overrides, execution_evidence = \
            _snapshot_capability_overrides(
                config, manifest, hash_coverage, schema_version, warnings)
        identity = {
            "snapshot_uuid": snapshot_uuid,
            "scanner_version": source_version,
            "scan_status": scan_status,
            "database_integrity": database_integrity,
            **execution_evidence,
        }
        if runtime is not None:
            identity.update({
                "run_state": runtime["run_state"],
                "state_revision": runtime["state_revision"],
                "resume_hint": runtime["resume_hint"],
                "active_session_id": runtime["active_session_id"],
                "resume_contract": runtime["resume_contract"],
                "projection_contract": runtime["projection_contract"],
                "filename_layout_version": runtime[
                    "filename_layout_version"],
            })
            if runtime["run_state"] == "published":
                _validate_published_filename(
                    normalized_path,
                    runtime,
                    warnings,
                    verify_fingerprint=verify_artifact_fingerprint,
                )
        capabilities = _capability_map(
            con, tables, columns, SNAPSHOT_CAPABILITY_SPECS, lifecycle,
            capability_overrides)
        if schema_version == 4 and lifecycle == "sealed":
            capabilities["run_history"] = \
                _schema4_run_history_capability(con)
        status = (
            str(runtime["run_state"]) if runtime is not None
            else str(scan_status)
        )
    else:
        _require_structure(
            tables, columns, "diff_info", _DIFF_CORE_COLUMNS,
            "Diff 数据库",
        )
        try:
            row = con.execute(
                "SELECT diff_uuid,schema_version,old_schema_version,"
                " new_schema_version,old_snapshot_uuid,new_snapshot_uuid,"
                " tool_version FROM diff_info WHERE id=1").fetchone()
        except sqlite3.Error as exc:
            raise core.PreflightError(f"Diff 身份无法读取：{exc}") from exc
        if row is None:
            raise core.PreflightError("Diff 数据库缺少 diff_info id=1")
        (diff_uuid, schema_version, old_schema_version, new_schema_version,
         old_uuid, new_uuid, source_version) = tuple(row)
        core.require_readable_schema_version(schema_version, "Diff 数据库")
        _require_snapshot_schema(old_schema_version, "Diff 旧侧快照")
        _require_snapshot_schema(new_schema_version, "Diff 新侧快照")
        is_partial_name = normalized_path.casefold().endswith(
            ".partial.sqlite")
        lifecycle = "partial" if is_partial_name else "sealed"
        if require_sealed and lifecycle != "sealed":
            raise core.PreflightError("Diff 数据库尚未完整发布")
        database_integrity = None
        path_key_rule = None
        data_contract = None
        min_reader = None
        identity = {
            "diff_uuid": diff_uuid,
            "tool_version": source_version,
            "old_schema_version": old_schema_version,
            "new_schema_version": new_schema_version,
            "old_snapshot_uuid": old_uuid,
            "new_snapshot_uuid": new_uuid,
        }
        capabilities = _capability_map(
            con, tables, columns, DIFF_CAPABILITY_SPECS, lifecycle)
        status = "complete" if lifecycle == "sealed" else "partial"

    sqlite_integrity = None
    if verify_integrity:
        core.require_sqlite_integrity(
            con, "快照数据库" if database_type == "snapshot" else "Diff 数据库")
        sqlite_integrity = "ok"
    return DatabaseDescriptor(
        path=normalized_path,
        database_type=database_type,
        schema_version=int(schema_version),
        source_version=(str(source_version) if source_version is not None
                        else None),
        lifecycle=lifecycle,
        status=status,
        database_integrity=(
            str(database_integrity) if database_integrity is not None else None
        ),
        sqlite_integrity=sqlite_integrity,
        path_key_rule=(
            int(path_key_rule) if path_key_rule is not None else None
        ),
        data_contract=(str(data_contract) if data_contract is not None else None),
        min_reader_version=(
            str(min_reader) if min_reader is not None else None
        ),
        tables=tables,
        columns=columns,
        capabilities=capabilities,
        identity=identity,
        warnings=tuple(warnings),
    )


def _diff_projection_capabilities(
    descriptor: DatabaseDescriptor,
) -> dict[str, dict[str, object]]:
    """冻结 Diff 输入会用到的能力状态，并校验专用投影列。"""
    result: dict[str, dict[str, object]] = {}
    for capability_id in _DIFF_PROJECTION_CAPABILITIES:
        capability = descriptor.capability(capability_id)
        result[capability_id] = {
            "state": capability.state,
            "row_count": capability.row_count,
            "reason": capability.reason,
            "queryable": capability.queryable,
        }
    for capability_id, table, required in (
        ("hashes", "hashes", _DIFF_HASH_COLUMNS),
        ("raw_payloads", "raw_payloads", _DIFF_RAW_PAYLOAD_COLUMNS),
    ):
        if table not in descriptor.tables:
            continue
        missing = sorted(
            required - descriptor.columns.get(table, frozenset()))
        if not missing:
            continue
        result[capability_id] = {
            "state": "incompatible",
            "row_count": None,
            "reason": (
                "Diff 规范化投影缺少列："
                + "、".join(f"{table}.{column}" for column in missing)
            ),
            "queryable": False,
        }
    return result


def snapshot_root_labels(
    con: sqlite3.Connection,
    descriptor: DatabaseDescriptor | None = None,
) -> tuple[str, ...]:
    """只读返回稳定 root 标签，供命名／预览使用，避免调用方复制 SQL。"""
    current = descriptor or inspect_connection(con)
    if current.database_type != "snapshot" or not current.sealed:
        raise core.PreflightError("root 标签只接受完整封存快照")
    _require_structure(
        current.tables,
        current.columns,
        "roots",
        ("root_label",),
        "快照 root 标签",
    )
    return tuple(
        str(row[0]) for row in con.execute(
            "SELECT root_label FROM roots ORDER BY root_label COLLATE BINARY")
    )


def snapshot_diff_projection(
    con: sqlite3.Connection,
    descriptor: DatabaseDescriptor | None = None,
) -> dict[str, object]:
    """读取版本化 Diff 输入投影；schema 分支只允许存在于 Reader。"""
    current = descriptor or inspect_connection(con)
    if current.database_type != "snapshot" or not current.sealed:
        raise core.PreflightError("Diff 规范化投影只接受完整封存快照")
    for table, required in _DIFF_PROJECTION_REQUIRED.items():
        _require_structure(
            current.tables,
            current.columns,
            table,
            required,
            "Diff 规范化投影",
        )
    capabilities = _diff_projection_capabilities(current)

    roots = {
        int(root_id): {
            "label": str(label),
            "path": str(path),
            "enum": str(enum_status),
        }
        for root_id, label, path, enum_status in con.execute(
            "SELECT root_id,root_label,root_path,enum_status"
            " FROM roots ORDER BY root_id"
        )
    }
    directories: dict[int, dict[str, dict[str, object]]] = {}
    for root_id, rel_path, path_key, enum_status in con.execute(
            "SELECT root_id,rel_path,path_key,enum_status"
            " FROM dirs ORDER BY root_id,path_key,rel_path"):
        directories.setdefault(int(root_id), {})[str(path_key)] = {
            "rel": str(rel_path),
            "enum": str(enum_status),
        }

    hashes: dict[int, dict[str, object]] = {}
    hash_capability = capabilities["hashes"]
    if hash_capability["state"] in ("available", "empty"):
        for (entry_id, hash_hex, origin, source_uuid, source_time,
             finished_time, status) in con.execute(
                "SELECT entry_id,hash_hex,origin,source_snapshot_uuid,"
                " source_computed_at_utc,finished_at_utc,status FROM hashes"
                " WHERE algorithm='sha256' ORDER BY entry_id"):
            hashes[int(entry_id)] = {
                "hex": str(hash_hex),
                "origin": str(origin),
                "src": (source_uuid, source_time),
                "fin": finished_time,
                "status": str(status),
            }

    payloads: dict[int, dict[str, tuple[object, object]]] = {}
    payload_capability = capabilities["raw_payloads"]
    if payload_capability["state"] in ("available", "empty"):
        for entry_id, provider, payload_sha, provider_version in con.execute(
                "SELECT entry_id,provider,payload_sha256,provider_version"
                " FROM raw_payloads ORDER BY entry_id,provider"):
            payloads.setdefault(int(entry_id), {})[str(provider)] = (
                payload_sha,
                provider_version,
            )

    snapshot_uuid = str(current.identity["snapshot_uuid"])
    entries: dict[int, dict[str, list[dict[str, object]]]] = {}
    hash_count: dict[str, int] = {}
    hash_fileids: dict[str, list[tuple[object, object]]] = {}
    hash_events: dict[str, set[tuple[object, object]]] = {}
    for (entry_id, root_id, rel_path, path_key, size_bytes, modified_at,
         attributes, is_placeholder, meta_status, hash_status,
         volume_serial, file_index_hex) in con.execute(
            "SELECT entry_id,root_id,rel_path,path_key,size_bytes,"
            " modified_at_utc,attributes,is_placeholder,meta_status,"
            " hash_status,volume_serial,file_index_hex FROM entries"
            " ORDER BY root_id,path_key,rel_path,entry_id"):
        normalized_entry_id = int(entry_id)
        hash_record = hashes.get(normalized_entry_id)
        valid_hash = (
            hash_record
            if hash_record is not None
            and hash_record["status"] == "valid"
            else None
        )
        entry = {
            "eid": normalized_entry_id,
            "rid": int(root_id),
            "rel": str(rel_path),
            "pk": str(path_key),
            "size": size_bytes,
            "mtime": modified_at,
            "attrs": attributes,
            "ph": is_placeholder,
            "unstable": (
                meta_status == "unstable"
                or hash_status == "unstable"
                or (
                    hash_record is not None
                    and hash_record["status"] == "unstable"
                )
            ),
            "hash": valid_hash,
            "payloads": payloads.get(normalized_entry_id) or {},
            "vs": volume_serial,
            "fih": file_index_hex,
        }
        entries.setdefault(int(root_id), {}).setdefault(
            str(path_key), []).append(entry)
        if valid_hash is None:
            continue
        hash_hex = str(valid_hash["hex"])
        hash_count[hash_hex] = hash_count.get(hash_hex, 0) + 1
        event = (
            (snapshot_uuid, valid_hash["fin"])
            if valid_hash["origin"] == "computed"
            else tuple(valid_hash["src"])
        )
        hash_events.setdefault(hash_hex, set()).add(event)
        if volume_serial and file_index_hex:
            hash_fileids.setdefault(hash_hex, []).append(
                (volume_serial, file_index_hex))

    return {
        "projection": SNAPSHOT_DIFF_PROJECTION,
        "path": current.path,
        "file": os.path.basename(current.path),
        "uuid": snapshot_uuid,
        "schema": current.schema_version,
        "pk_rule": current.path_key_rule,
        "coverage": current.identity.get("hash_coverage"),
        "roots": roots,
        "dirs": directories,
        "entries": entries,
        "hash_count": hash_count,
        "hash_fileids": hash_fileids,
        "hash_events": hash_events,
        "n_payload": (
            payload_capability["row_count"]
            if payload_capability["state"] in ("available", "empty")
            else None
        ),
        "capabilities": capabilities,
    }


def _drop_volatile_exiftool_tags(value: object) -> object:
    """复制 JSON 结构，同时移除访问时间与提取目标路径等环境字段。"""
    if isinstance(value, dict):
        return {
            key: _drop_volatile_exiftool_tags(item)
            for key, item in value.items()
            if not (
                isinstance(key, str)
                and key.rsplit(":", 1)[-1].casefold()
                in _VOLATILE_EXIFTOOL_TAGS
            )
        }
    if isinstance(value, list):
        return [_drop_volatile_exiftool_tags(item) for item in value]
    return value


def snapshot_exiftool_comparison_digest(
    con: sqlite3.Connection,
    entry_id: int,
) -> str | None:
    """返回排除环境字段后的 ExifTool 比较摘要；异常时保守返回 None。"""
    try:
        row = con.execute(
            "SELECT payload_zlib FROM raw_payloads"
            " WHERE entry_id=? AND provider='exiftool'",
            (int(entry_id),),
        ).fetchone()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    try:
        raw = zlib.decompress(row[0])
        document = json.loads(raw.decode("utf-8"))
        stable = _drop_volatile_exiftool_tags(document)
        canonical = json.dumps(
            stable,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (OSError, TypeError, ValueError, UnicodeError, zlib.error):
        return None
    return hashlib.sha256(canonical).hexdigest()


def _projection_columns(
    con: sqlite3.Connection,
    table: str,
    excluded: Iterable[str],
) -> tuple[str, ...]:
    excluded_set = set(excluded)
    return tuple(
        str(row[1]) for row in con.execute(
            f"PRAGMA table_info({_quote_identifier(table)})")
        if str(row[1]) not in excluded_set
    )


def _qualified_columns(alias: str, columns: Iterable[str]) -> str:
    return ",".join(
        f"{alias}.{_quote_identifier(column)}" for column in columns)


def _projection_order(alias: str, columns: Iterable[str]) -> str:
    values = [
        f"{alias}.{_quote_identifier(column)}" for column in columns]
    return ("," + ",".join(values)) if values else ""


def _iter_entry_projection_table(
    con: sqlite3.Connection,
    section: str,
    table: str,
    excluded: Iterable[str],
) -> Iterator[ProjectionRow]:
    columns = _projection_columns(con, table, excluded)
    selected = _qualified_columns("t", columns)
    selected_sql = "," + selected if selected else ""
    sql = (
        "SELECT r.root_label,e.rel_path"
        + selected_sql
        + f" FROM {_quote_identifier(table)} t"
        " JOIN entries e ON e.entry_id=t.entry_id"
        " JOIN roots r ON r.root_id=e.root_id"
        " ORDER BY r.root_label COLLATE BINARY,"
        " e.path_key COLLATE BINARY,e.rel_path COLLATE BINARY"
        + _projection_order("t", columns)
    )
    for row in con.execute(sql):
        values = tuple(row)
        yield ProjectionRow(section, values[:2], values[2:])


def iter_snapshot_business_projection(
    con: sqlite3.Connection,
    descriptor: DatabaseDescriptor | None = None,
) -> Iterator[ProjectionRow]:
    """流式输出跨 session 稳定业务投影；不含身份、attempt 或观察时间。"""
    current = descriptor or inspect_connection(con)
    if current.database_type != "snapshot" or not current.sealed:
        raise core.PreflightError("业务投影只接受完整封存快照")

    for capability_id in _BUSINESS_CAPABILITIES:
        capability = current.capability(capability_id)
        yield ProjectionRow(
            "capabilities",
            (capability_id,),
            (capability.state, capability.row_count),
        )

    identity = current.identity
    row = con.execute(
        "SELECT hash_coverage,has_file_issues,has_unstable_entries,"
        " has_enumeration_gaps FROM snapshot_info WHERE id=1"
    ).fetchone()
    yield ProjectionRow(
        "snapshot",
        ("business_profile",),
        (
            current.path_key_rule,
            row[0],
            int(row[1]),
            int(row[2]),
            int(row[3]),
            identity.get("scan_kind"),
            identity.get("metadata_storage"),
            identity.get("raw_payload_retained"),
            identity.get("format_validation"),
        ),
    )

    if "roots" in current.tables:
        columns = _projection_columns(con, "roots", ("root_id",))
        selected = _qualified_columns("r", columns)
        sql = (
            "SELECT " + selected + " FROM roots r"
            " ORDER BY r.root_label COLLATE BINARY,r.root_path COLLATE BINARY"
            + _projection_order("r", columns)
        )
        for root in con.execute(sql):
            values = tuple(root)
            yield ProjectionRow("roots", values[:2], values[2:])

    directories = current.capability("directories")
    if directories.state in ("available", "empty"):
        columns = _projection_columns(
            con,
            "dirs",
            ("dir_id", "root_id", "parent_dir_id", "rel_path",
             "observed_at_utc"),
        )
        selected = _qualified_columns("d", columns)
        selected_sql = "," + selected if selected else ""
        sql = (
            "SELECT r.root_label,d.rel_path,p.rel_path"
            + selected_sql
            + " FROM dirs d JOIN roots r ON r.root_id=d.root_id"
            " LEFT JOIN dirs p ON p.dir_id=d.parent_dir_id"
            " ORDER BY r.root_label COLLATE BINARY,d.path_key COLLATE BINARY,"
            " d.rel_path COLLATE BINARY"
            + _projection_order("d", columns)
        )
        for directory in con.execute(sql):
            values = tuple(directory)
            yield ProjectionRow("directories", values[:2], values[2:])

    files = current.capability("files")
    if files.state in ("available", "empty"):
        columns = _projection_columns(
            con,
            "entries",
            ("entry_id", "root_id", "dir_id", "rel_path",
             "observed_at_utc"),
        )
        selected = _qualified_columns("e", columns)
        selected_sql = "," + selected if selected else ""
        sql = (
            "SELECT r.root_label,e.rel_path"
            + selected_sql
            + " FROM entries e JOIN roots r ON r.root_id=e.root_id"
            " ORDER BY r.root_label COLLATE BINARY,e.path_key COLLATE BINARY,"
            " e.rel_path COLLATE BINARY"
            + _projection_order("e", columns)
        )
        for entry in con.execute(sql):
            values = tuple(entry)
            yield ProjectionRow("entries", values[:2], values[2:])

    for capability_id, table, excluded in _ENTRY_PROJECTION_TABLES:
        capability = current.capability(capability_id)
        if capability.state not in ("available", "empty"):
            continue
        yield from _iter_entry_projection_table(
            con, table, table, excluded)

    diagnostics = current.capability("diagnostics")
    if diagnostics.state in ("available", "empty"):
        sql = (
            "SELECT COALESCE(re.root_label,rd.root_label),"
            " COALESCE(e.rel_path,d.rel_path),x.stage,x.error_code,x.message"
            " FROM errors x"
            " LEFT JOIN entries e ON e.entry_id=x.entry_id"
            " LEFT JOIN roots re ON re.root_id=e.root_id"
            " LEFT JOIN dirs d ON d.dir_id=x.dir_id"
            " LEFT JOIN roots rd ON rd.root_id=d.root_id"
            " ORDER BY 1 COLLATE BINARY,2 COLLATE BINARY,"
            " x.stage COLLATE BINARY,x.error_code COLLATE BINARY,"
            " x.message COLLATE BINARY"
        )
        for error in con.execute(sql):
            values = tuple(error)
            yield ProjectionRow("errors", values[:2], values[2:])


def _encoded_projection_value(value: object) -> list[object]:
    if value is None:
        return ["null"]
    if isinstance(value, (bytes, bytearray, memoryview)):
        content = bytes(value)
        return ["bytes", len(content), hashlib.sha256(content).hexdigest()]
    if isinstance(value, bool):
        return ["bool", value]
    if isinstance(value, int):
        return ["int", str(value)]
    if isinstance(value, float):
        return ["float", value.hex()]
    return ["text", str(value)]


def snapshot_business_projection_digest(
    con: sqlite3.Connection,
    descriptor: DatabaseDescriptor | None = None,
) -> str:
    """以类型稳定的逐行编码计算业务投影摘要，不整表载入内存。"""
    digest = hashlib.sha256()
    for row in iter_snapshot_business_projection(con, descriptor):
        record = [
            row.section,
            [_encoded_projection_value(value) for value in row.key],
            [_encoded_projection_value(value) for value in row.values],
        ]
        payload = json.dumps(
            record, ensure_ascii=False, separators=(",", ":"))
        digest.update(payload.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _connect_read_only(path: str) -> tuple[sqlite3.Connection, str]:
    normalized = os.path.abspath(os.fspath(path))
    if not os.path.isfile(normalized):
        raise core.PreflightError(f"数据库不存在：{normalized}")
    con = None
    try:
        uri = Path(normalized).resolve(strict=True).as_uri() + "?mode=ro"
        con = sqlite3.connect(uri, uri=True)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA query_only=ON")
        con.execute("PRAGMA busy_timeout=1000")
        return con, normalized
    except (OSError, sqlite3.Error, ValueError) as exc:
        if con is not None:
            con.close()
        raise core.PreflightError(f"数据库无法只读打开：{normalized}：{exc}") from exc


def open_database(
    path: str,
    *,
    expected_type: str | None = None,
    require_sealed: bool = True,
    verify_integrity: bool = True,
    verify_artifact_fingerprint: bool = True,
) -> tuple[sqlite3.Connection, DatabaseDescriptor]:
    """只读打开并统一探测；失败时保证关闭连接。"""
    con, normalized = _connect_read_only(path)
    try:
        descriptor = inspect_connection(
            con,
            normalized,
            expected_type=expected_type,
            require_sealed=require_sealed,
            verify_integrity=verify_integrity,
            verify_artifact_fingerprint=verify_artifact_fingerprint,
        )
    except Exception:
        con.close()
        raise
    return con, descriptor


def inspect_database(
    path: str,
    *,
    expected_type: str | None = None,
    require_sealed: bool = True,
    verify_integrity: bool = True,
    verify_artifact_fingerprint: bool = True,
) -> DatabaseDescriptor:
    """只读识别数据库并在返回前关闭连接。"""
    con, descriptor = open_database(
        path,
        expected_type=expected_type,
        require_sealed=require_sealed,
        verify_integrity=verify_integrity,
        verify_artifact_fingerprint=verify_artifact_fingerprint,
    )
    con.close()
    return descriptor


def probe_database(
    path: str,
    *,
    expected_type: str | None = None,
    require_sealed: bool = True,
    verify_integrity: bool = True,
    verify_artifact_fingerprint: bool = True,
) -> DatabaseProbe:
    """返回适合 GUI 展示的结果，不向界面泄漏 SQLite 异常堆栈。"""
    try:
        descriptor = inspect_database(
            path,
            expected_type=expected_type,
            require_sealed=require_sealed,
            verify_integrity=verify_integrity,
            verify_artifact_fingerprint=verify_artifact_fingerprint,
        )
    except (core.PreflightError, OSError, sqlite3.Error, ValueError) as exc:
        return DatabaseProbe("invalid", None, str(exc))
    return DatabaseProbe("valid", descriptor, None)
