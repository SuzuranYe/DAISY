"""DAISY DBS 统一只读数据库识别与能力探测层。

本模块只负责读取数据库事实：类型、schema、封存状态、实际表列和稳定能力。
它不迁移、不补列、不创建索引，也不把旧数据库缺失的未来能力伪装成空结果。
文件名指纹和各命令的业务参数仍由调用方负责。
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import sqlite3
from typing import Iterable

import Script_DAISY_Lib_DBS_01_Core as core


CAPABILITY_STATES = frozenset((
    "available", "empty", "unavailable", "incompatible", "invalid",
))
DATABASE_TYPES = frozenset(("snapshot", "diff"))


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

    evidence = {
        "scan_kind": scan_kind,
        "hash_coverage": normalized_coverage or None,
        "metadata_storage": metadata_storage,
        "raw_payload_retained": (
            raw_retained if isinstance(raw_retained, bool) else None
        ),
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
        core.require_readable_schema_version(schema_version, "快照数据库")
        if path_key_rule != core.PATH_KEY_RULE:
            raise core.PreflightError(
                f"快照 path_key_rule={path_key_rule} 非本工具支持的"
                f" {core.PATH_KEY_RULE}")
        lifecycle = (
            "sealed" if scan_status == "complete"
            and database_integrity == "ok" else "partial"
        )
        if scan_status == "complete" and database_integrity != "ok":
            lifecycle = "invalid"
        if require_sealed and lifecycle != "sealed":
            raise core.PreflightError(
                "快照尚未完整封存："
                f"scan_status={scan_status}，"
                f"database_integrity={database_integrity}")
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
        data_contract = manifest.get("data_contract") or config.get(
            "data_contract")
        min_reader = manifest.get("min_reader_version") or config.get(
            "min_reader_version")
        capability_overrides, execution_evidence = \
            _snapshot_capability_overrides(
                config, manifest, hash_coverage, warnings)
        identity = {
            "snapshot_uuid": snapshot_uuid,
            "scanner_version": source_version,
            "scan_status": scan_status,
            "database_integrity": database_integrity,
            **execution_evidence,
        }
        capabilities = _capability_map(
            con, tables, columns, SNAPSHOT_CAPABILITY_SPECS, lifecycle,
            capability_overrides)
        status = str(scan_status)
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
        core.require_readable_schema_version(
            old_schema_version, "Diff 旧侧快照")
        core.require_readable_schema_version(
            new_schema_version, "Diff 新侧快照")
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
) -> DatabaseDescriptor:
    """只读识别数据库并在返回前关闭连接。"""
    con, descriptor = open_database(
        path,
        expected_type=expected_type,
        require_sealed=require_sealed,
        verify_integrity=verify_integrity,
    )
    con.close()
    return descriptor


def probe_database(
    path: str,
    *,
    expected_type: str | None = None,
    require_sealed: bool = True,
    verify_integrity: bool = True,
) -> DatabaseProbe:
    """返回适合 GUI 展示的结果，不向界面泄漏 SQLite 异常堆栈。"""
    try:
        descriptor = inspect_database(
            path,
            expected_type=expected_type,
            require_sealed=require_sealed,
            verify_integrity=verify_integrity,
        )
    except (core.PreflightError, OSError, sqlite3.Error, ValueError) as exc:
        return DatabaseProbe("invalid", None, str(exc))
    return DatabaseProbe("valid", descriptor, None)
