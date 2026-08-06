"""DAISY v1.6.0 快照 Issues 分板块只读分析与 Markdown 渲染。"""
from __future__ import annotations

from dataclasses import dataclass
import os
import re
import sqlite3

import Script_DAISY_Lib_DBS_01_Core as core
import Script_DAISY_Lib_DBS_05_Reader as dbreader
import Script_DAISY_Lib_DBS_06_Verify as dbverify


ISSUE_SECTIONS = (
    ("enumeration", "枚举问题"),
    ("hash", "哈希问题"),
    ("metadata", "Exif／元数据问题"),
    ("format", "格式校验问题"),
    ("raw", "RAW 深度校验问题"),
    ("performance", "读取性能异常候选"),
    ("runtime", "运行／证据问题"),
)
DETAIL_LIMIT = 200
WARNING_DENSITY_THRESHOLD = 100
_COPY_INDEX_RE = re.compile(r"(?i)(copy)\d+")
_LEVEL_TITLES = (
    ("need_action", "需要处理"),
    ("candidate", "待复核候选"),
    ("information", "信息性诊断"),
)
_INFORMATION_LABELS = {
    "failed_roots": "枚举失败根目录",
    "problem_directories": "枚举异常目录",
    "hash_coverage": "哈希覆盖",
    "error_records": "错误记录",
    "current_attempt_records": "当前失败尝试记录",
    "status_only_files": "仅状态异常文件",
    "unsupported_or_unrecognized_files": "未支持／未识别文件（仅统计）",
    "diagnostic_records": "诊断记录总数",
    "diagnostic_total_files": "诊断涉及文件总数",
    "reportable_diagnostic_records": "需呈现诊断记录",
    "reportable_diagnostic_files": "需呈现诊断影响文件",
    "normalized_diagnostic_families": "归一化诊断家族",
    "folded_minor_records": "折叠 [minor] warning",
    "folded_warning_records": "折叠普通 warning",
    "folded_validation_records": "折叠 validation",
    "high_density_warning_files": "高密度 warning 候选文件",
    "unsupported_files": "unsupported 文件（仅统计）",
    "raw_candidate_files": "RAW 候选文件",
    "raw_selected_files": "RAW 选中文件",
    "raw_processed_files": "RAW 已处理文件",
    "raw_unsupported_files": "RAW unsupported 文件（仅统计）",
    "high_confidence_records": "高置信度候选记录",
    "low_confidence_files": "低置信度候选（仅留库）",
    "failed_or_abandoned_sessions": "失败／异常结束 session",
    "failed_stages": "当前失败阶段",
}
_FOLDED_INFORMATION_KEYS = frozenset((
    "unsupported_or_unrecognized_files",
    "unsupported_files",
    "folded_minor_records",
    "folded_warning_records",
    "folded_validation_records",
    "low_confidence_files",
))


@dataclass(frozen=True)
class IssueSection:
    section_id: str
    title: str
    execution: str
    reason: str | None
    issue_files: int | None
    issue_records: int | None
    details: tuple[dict[str, object], ...]
    information: dict[str, object]

    def __post_init__(self) -> None:
        if self.execution not in ("executed", "null"):
            raise ValueError(f"未知 Issues 执行状态：{self.execution}")
        if self.execution == "null" and (
                self.issue_files is not None
                or self.issue_records is not None):
            raise ValueError("NULL 板块不能伪造 0 条结果")

    @property
    def reportable(self) -> bool:
        return self.execution == "executed" and bool(
            self.issue_files or self.issue_records)

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.section_id,
            "title": self.title,
            "execution": self.execution,
            "reason": self.reason,
            "issue_files": self.issue_files,
            "issue_records": self.issue_records,
            "details": [dict(row) for row in self.details],
            "information": dict(self.information),
        }


def normalize_diagnostic_family(value: object) -> str:
    """把 ExifTool 动态 CopyN 字段归一化为 Copy#，避免分类爆炸。"""
    return _COPY_INDEX_RE.sub(r"\1#", str(value or ""))


def _capability_queryable(
    descriptor: dbreader.DatabaseDescriptor,
    capability_id: str,
) -> bool:
    return descriptor.capability(capability_id).state in ("available", "empty")


def _count(con: sqlite3.Connection, sql: str, parameters=()) -> int:
    return int(con.execute(sql, parameters).fetchone()[0])


def _detail(
    root_label: object,
    relative_path: object,
    status: object,
    detail: object,
    *,
    level: str = "need_action",
    statuses: object = "",
    advice: object = "",
) -> dict[str, object]:
    if level not in {item[0] for item in _LEVEL_TITLES}:
        raise ValueError(f"未知 Issues 明细层级：{level}")
    return {
        "root_label": str(root_label or ""),
        "relative_path": str(relative_path or ""),
        "status": str(status or ""),
        "detail": normalize_diagnostic_family(detail)[:2000],
        "level": level,
        "statuses": str(statuses or ""),
        "advice": str(advice or ""),
    }


def _null_section(
    section_id: str,
    title: str,
    reason: str,
    *,
    information: dict[str, object] | None = None,
) -> IssueSection:
    return IssueSection(
        section_id,
        title,
        "null",
        reason,
        None,
        None,
        (),
        dict(information or {}),
    )


def _enumeration_section(
    con: sqlite3.Connection,
    row_limit: int,
) -> IssueSection:
    root_count = _count(
        con, "SELECT COUNT(*) FROM roots WHERE enum_status='failed'")
    dir_count = _count(
        con, "SELECT COUNT(*) FROM dirs WHERE enum_status<>'ok'")
    details = []
    for row in con.execute(
        "SELECT root_label,'',enum_status,'根目录枚举失败'"
        " FROM roots WHERE enum_status='failed' ORDER BY root_label LIMIT ?",
        (row_limit,),
    ):
        details.append(_detail(
            *row,
            statuses=f"枚举={row[2]}",
            advice="确认根目录可访问后重试枚举",
        ))
    remaining = max(0, row_limit - len(details))
    if remaining:
        for row in con.execute(
            "SELECT r.root_label,d.rel_path,d.enum_status,d.error_message"
            " FROM dirs d JOIN roots r ON r.root_id=d.root_id"
            " WHERE d.enum_status<>'ok'"
            " ORDER BY r.root_label,d.path_key LIMIT ?",
            (remaining,),
        ):
            details.append(_detail(
                *row,
                statuses=f"枚举={row[2]}",
                advice="确认目录权限与可用性后重试枚举",
            ))
    total = root_count + dir_count
    return IssueSection(
        "enumeration",
        "枚举问题",
        "executed",
        None,
        0,
        total,
        tuple(details),
        {
            "failed_roots": root_count,
            "problem_directories": dir_count,
            "detail_total": total,
            "evidence_tables": "roots、dirs",
        },
    )


def _hash_section(
    con: sqlite3.Connection,
    descriptor: dbreader.DatabaseDescriptor,
    row_limit: int,
) -> IssueSection:
    coverage = str(descriptor.identity.get("hash_coverage") or "none")
    capability = descriptor.capability("hashes")
    if coverage == "none" or capability.state not in ("available", "empty"):
        return _null_section(
            "hash",
            "哈希问题",
            capability.reason or "本次扫描未执行内容哈希",
        )
    attempt_clause = "0"
    attempt_problem_statuses = (
        "'invalid','error','timeout','unstable','skipped_policy',"
        "'cancelled','abandoned'"
    )
    has_attempts = _capability_queryable(descriptor, "entry_attempts")
    if has_attempts:
        attempt_clause = (
            "EXISTS (SELECT 1 FROM entry_attempts a"
            " WHERE a.entry_id=e.entry_id"
            " AND a.stage IN ('hash','verify_hash')"
            f" AND a.status IN ({attempt_problem_statuses})"
            " AND NOT EXISTS (SELECT 1 FROM entry_attempts newer"
            "  WHERE newer.entry_id=a.entry_id AND newer.stage=a.stage"
            "  AND newer.attempt_number>a.attempt_number))"
        )
    condition = (
        "(e.hash_status IN ('error','unstable')"
        " OR EXISTS (SELECT 1 FROM errors x WHERE x.entry_id=e.entry_id"
        " AND x.stage IN ('hash','verify_hash'))"
        f" OR {attempt_clause})"
    )
    issue_files = _count(
        con, f"SELECT COUNT(*) FROM entries e WHERE {condition}")
    error_records = _count(
        con,
        "SELECT COUNT(*) FROM errors"
        " WHERE stage IN ('hash','verify_hash')",
    )
    attempt_records = 0
    if has_attempts:
        attempt_records = _count(
            con,
            "SELECT COUNT(*) FROM entry_attempts a"
            " WHERE a.stage IN ('hash','verify_hash')"
            f" AND a.status IN ({attempt_problem_statuses})"
            " AND NOT EXISTS (SELECT 1 FROM entry_attempts newer"
            "  WHERE newer.entry_id=a.entry_id AND newer.stage=a.stage"
            "  AND newer.attempt_number>a.attempt_number)",
        )
    status_only_files = _count(
        con,
        "SELECT COUNT(*) FROM entries e"
        f" WHERE {condition}"
        " AND NOT EXISTS (SELECT 1 FROM errors x"
        "  WHERE x.entry_id=e.entry_id"
        "  AND x.stage IN ('hash','verify_hash'))"
        f" AND NOT {attempt_clause}",
    )
    details = []
    attempt_text = "''"
    if has_attempts:
        attempt_text = (
            "COALESCE((SELECT COALESCE(a.error_code,a.status) ||"
            " CASE WHEN a.error_message IS NULL THEN ''"
            " ELSE ': ' || a.error_message END FROM entry_attempts a"
            " WHERE a.entry_id=e.entry_id"
            " AND a.stage IN ('hash','verify_hash')"
            f" AND a.status IN ({attempt_problem_statuses})"
            " AND NOT EXISTS (SELECT 1 FROM entry_attempts newer"
            "  WHERE newer.entry_id=a.entry_id AND newer.stage=a.stage"
            "  AND newer.attempt_number>a.attempt_number)"
            " ORDER BY a.attempt_id DESC LIMIT 1),'')"
        )
    format_status = "'NULL'"
    if _capability_queryable(descriptor, "format_checks"):
        format_status = (
            "COALESCE((SELECT f.status FROM format_checks f"
            " WHERE f.entry_id=e.entry_id),'NULL')"
        )
    for row in con.execute(
        "SELECT r.root_label,e.rel_path,e.hash_status,e.meta_status,"
        f" {format_status},"
        " COALESCE((SELECT x.error_code || ': ' || x.message"
        "  FROM errors x WHERE x.entry_id=e.entry_id"
        "  AND x.stage IN ('hash','verify_hash')"
        "  ORDER BY x.error_pk DESC LIMIT 1),''),"
        f" {attempt_text}"
        " FROM entries e JOIN roots r ON r.root_id=e.root_id"
        f" WHERE {condition}"
        " ORDER BY r.root_label,e.path_key LIMIT ?",
        (row_limit,),
    ):
        messages = tuple(dict.fromkeys(
            str(value) for value in row[5:] if str(value or "").strip()
        ))
        advice = (
            "确认扫描期间文件未变化后重新扫描"
            if row[2] == "unstable"
            else "检查源文件可读性并重试哈希"
        )
        details.append(_detail(
            *row[:3],
            "；".join(messages),
            statuses=f"元数据={row[3]}；哈希={row[2]}；格式={row[4]}",
            advice=advice,
        ))
    return IssueSection(
        "hash",
        "哈希问题",
        "executed",
        None,
        issue_files,
        error_records + attempt_records + status_only_files,
        tuple(details),
        {
            "hash_coverage": coverage,
            "error_records": error_records,
            "current_attempt_records": attempt_records,
            "status_only_files": status_only_files,
            "detail_total": issue_files,
            "evidence_tables": "entries、hashes、errors、entry_attempts（若有）",
        },
    )


def _metadata_section(
    con: sqlite3.Connection,
    descriptor: dbreader.DatabaseDescriptor,
    row_limit: int,
) -> IssueSection:
    scan_kind = str(descriptor.identity.get("scan_kind") or "")
    if scan_kind == "quick":
        return _null_section(
            "metadata", "Exif／元数据问题", "Quick 未执行元数据提取")
    has_diagnostics = _capability_queryable(descriptor, "diagnostics")
    diagnostic_action = "0"
    diagnostic_candidate = "0"
    diagnostic_reportable = "0"
    hidden_diagnostic_clause = "0"
    high_density_clause = "0"
    if has_diagnostics:
        diagnostic_action = (
            "EXISTS (SELECT 1 FROM metadata_diagnostics d"
            " WHERE d.entry_id=e.entry_id"
            " AND daisy_diagnostic_class(d.severity,d.diagnostic_code,"
            " d.message)='need_action')"
        )
        diagnostic_candidate = (
            "EXISTS (SELECT 1 FROM metadata_diagnostics d"
            " WHERE d.entry_id=e.entry_id"
            " AND daisy_diagnostic_class(d.severity,d.diagnostic_code,"
            " d.message)='candidate')"
        )
        diagnostic_reportable = (
            f"({diagnostic_action} OR {diagnostic_candidate})"
        )
        hidden_diagnostic_clause = (
            "EXISTS (SELECT 1 FROM metadata_diagnostics d"
            " WHERE d.entry_id=e.entry_id"
            " AND daisy_diagnostic_class(d.severity,d.diagnostic_code,"
            " d.message)='unsupported')"
        )
        high_density_clause = (
            "EXISTS (SELECT 1 FROM metadata_diagnostics dense"
            " WHERE dense.entry_id=e.entry_id"
            " AND daisy_diagnostic_class(dense.severity,"
            " dense.diagnostic_code,dense.message) IN ('minor','warning')"
            " GROUP BY dense.entry_id"
            f" HAVING COUNT(*)>={WARNING_DENSITY_THRESHOLD})"
        )
    hidden_errors = (
        "EXISTS (SELECT 1 FROM errors hidden"
        " WHERE hidden.entry_id=e.entry_id"
        " AND hidden.stage='metadata'"
        " AND daisy_issue_visible(hidden.error_code,hidden.message)=0)"
    )
    visible_errors = (
        "EXISTS (SELECT 1 FROM errors visible"
        " WHERE visible.entry_id=e.entry_id"
        " AND visible.stage='metadata'"
        " AND daisy_issue_visible(visible.error_code,visible.message)=1)"
    )
    only_unrecognized = (
        "(e.meta_status='error'"
        f" AND ({hidden_errors} OR {hidden_diagnostic_clause})"
        f" AND NOT {visible_errors} AND NOT {diagnostic_reportable})"
    )
    condition = (
        "(e.meta_status IN ('timeout','unstable')"
        f" OR (e.meta_status='error' AND NOT {only_unrecognized})"
        f" OR {visible_errors} OR {diagnostic_reportable}"
        f" OR {high_density_clause})"
    )
    issue_files = _count(
        con, f"SELECT COUNT(*) FROM entries e WHERE {condition}")
    unsupported = _count(
        con,
        "SELECT COUNT(*) FROM entries e WHERE"
        f" ({hidden_errors} OR {hidden_diagnostic_clause})",
    )
    error_records = _count(
        con,
        "SELECT COUNT(*) FROM errors x WHERE x.stage='metadata'"
        " AND daisy_issue_visible(x.error_code,x.message)=1",
    )
    diagnostic_records = 0
    diagnostic_total_files = 0
    reportable_diagnostics = 0
    reportable_diagnostic_files = 0
    minor_records = 0
    warning_records = 0
    validation_records = 0
    high_density_files = 0
    diagnostic_families = 0
    if has_diagnostics:
        diagnostic_records = _count(
            con, "SELECT COUNT(*) FROM metadata_diagnostics")
        diagnostic_total_files = _count(
            con,
            "SELECT COUNT(DISTINCT entry_id) FROM metadata_diagnostics",
        )
        reportable_diagnostics = _count(
            con,
            "SELECT COUNT(*) FROM metadata_diagnostics d"
            " WHERE daisy_diagnostic_class(d.severity,d.diagnostic_code,"
            " d.message) IN ('need_action','candidate')",
        )
        reportable_diagnostic_files = _count(
            con,
            "SELECT COUNT(DISTINCT d.entry_id)"
            " FROM metadata_diagnostics d"
            " WHERE daisy_diagnostic_class(d.severity,d.diagnostic_code,"
            " d.message) IN ('need_action','candidate')",
        )
        minor_records = _count(
            con,
            "SELECT COUNT(*) FROM metadata_diagnostics d"
            " WHERE daisy_diagnostic_class(d.severity,d.diagnostic_code,"
            " d.message)='minor'",
        )
        warning_records = _count(
            con,
            "SELECT COUNT(*) FROM metadata_diagnostics d"
            " WHERE daisy_diagnostic_class(d.severity,d.diagnostic_code,"
            " d.message)='warning'",
        )
        validation_records = _count(
            con,
            "SELECT COUNT(*) FROM metadata_diagnostics d"
            " WHERE daisy_diagnostic_class(d.severity,d.diagnostic_code,"
            " d.message)='validation'",
        )
        high_density_files = _count(
            con,
            "SELECT COUNT(*) FROM (SELECT d.entry_id"
            " FROM metadata_diagnostics d"
            " WHERE daisy_diagnostic_class(d.severity,d.diagnostic_code,"
            " d.message) IN ('minor','warning') GROUP BY d.entry_id"
            f" HAVING COUNT(*)>={WARNING_DENSITY_THRESHOLD})",
        )
        diagnostic_families = _count(
            con,
            "SELECT COUNT(DISTINCT daisy_normalize_family("
            " d.diagnostic_code)) FROM metadata_diagnostics d"
            " WHERE daisy_diagnostic_class(d.severity,d.diagnostic_code,"
            " d.message) IN ('need_action','candidate')",
        )
    status_only_files = _count(
        con,
        "SELECT COUNT(*) FROM entries e"
        f" WHERE {condition} AND NOT {visible_errors}"
        f" AND NOT {diagnostic_reportable}"
        f" AND NOT {high_density_clause}",
    )
    details = []
    diagnostic_text = "''"
    action_diagnostic_count = "0"
    candidate_diagnostic_count = "0"
    minor_diagnostic_count = "0"
    if has_diagnostics:
        diagnostic_text = (
            "COALESCE((SELECT d.diagnostic_code || ': ' || d.message"
            " FROM metadata_diagnostics d WHERE d.entry_id=e.entry_id"
            " AND daisy_diagnostic_class(d.severity,d.diagnostic_code,"
            " d.message) IN ('need_action','candidate')"
            " ORDER BY CASE daisy_diagnostic_class(d.severity,"
            " d.diagnostic_code,d.message) WHEN 'need_action' THEN 0"
            " ELSE 1 END,d.diagnostic_pk DESC LIMIT 1),'')"
        )
        action_diagnostic_count = (
            "(SELECT COUNT(*) FROM metadata_diagnostics d"
            " WHERE d.entry_id=e.entry_id"
            " AND daisy_diagnostic_class(d.severity,d.diagnostic_code,"
            " d.message)='need_action')"
        )
        candidate_diagnostic_count = (
            "(SELECT COUNT(*) FROM metadata_diagnostics d"
            " WHERE d.entry_id=e.entry_id"
            " AND daisy_diagnostic_class(d.severity,d.diagnostic_code,"
            " d.message)='candidate')"
        )
        minor_diagnostic_count = (
            "(SELECT COUNT(*) FROM metadata_diagnostics d"
            " WHERE d.entry_id=e.entry_id"
            " AND daisy_diagnostic_class(d.severity,d.diagnostic_code,"
            " d.message) IN ('minor','warning'))"
        )
    format_status = "'NULL'"
    if _capability_queryable(descriptor, "format_checks"):
        format_status = (
            "COALESCE((SELECT f.status FROM format_checks f"
            " WHERE f.entry_id=e.entry_id),'NULL')"
        )
    for row in con.execute(
        "SELECT r.root_label,e.rel_path,e.meta_status,e.hash_status,"
        f" {format_status},COALESCE("
        " (SELECT x.error_code || ': ' || x.message FROM errors x"
        "  WHERE x.entry_id=e.entry_id AND x.stage='metadata'"
        "  AND daisy_issue_visible(x.error_code,x.message)=1"
        "  ORDER BY x.error_pk DESC LIMIT 1),''),"
        f" {diagnostic_text},{action_diagnostic_count},"
        f" {candidate_diagnostic_count},{minor_diagnostic_count},"
        " CASE WHEN (e.meta_status IN ('timeout','unstable')"
        f" OR (e.meta_status='error' AND NOT {only_unrecognized})"
        f" OR {visible_errors} OR {diagnostic_action}) THEN 1 ELSE 0 END"
        " FROM entries e JOIN roots r ON r.root_id=e.root_id"
        f" WHERE {condition}"
        " ORDER BY 11 DESC,r.root_label,e.path_key LIMIT ?",
        (row_limit,),
    ):
        messages = tuple(dict.fromkeys(
            str(value) for value in row[5:7] if str(value or "").strip()
        ))
        folded_warning_count = int(row[9])
        if (not messages
                and folded_warning_count >= WARNING_DENSITY_THRESHOLD):
            messages = (
                "同一文件折叠的普通／[minor] warning 达 "
                f"{folded_warning_count} 条",
            )
        action = bool(row[10])
        level = "need_action" if action else "candidate"
        if row[2] == "timeout":
            advice = "确认工具与文件可读取后重试元数据提取"
        elif row[2] == "unstable":
            advice = "确认扫描期间文件未变化后重新扫描"
        elif action:
            advice = "检查源文件、错误码与 metadata_diagnostics 后重试"
        else:
            advice = "人工复核候选；完整诊断保留在 metadata_diagnostics"
        details.append(_detail(
            *row[:3],
            "；".join(messages),
            level=level,
            statuses=(
                f"元数据={row[2]}；哈希={row[3]}；格式={row[4]}；"
                f"呈现诊断={int(row[7]) + int(row[8])}"
            ),
            advice=advice,
        ))
    return IssueSection(
        "metadata",
        "Exif／元数据问题",
        "executed",
        None,
        issue_files,
        error_records + reportable_diagnostics + high_density_files
        + status_only_files,
        tuple(details),
        {
            "unsupported_or_unrecognized_files": unsupported,
            "diagnostic_records": diagnostic_records,
            "diagnostic_total_files": diagnostic_total_files,
            "reportable_diagnostic_records": reportable_diagnostics,
            "reportable_diagnostic_files": reportable_diagnostic_files,
            "normalized_diagnostic_families": diagnostic_families,
            "folded_minor_records": minor_records,
            "folded_warning_records": warning_records,
            "folded_validation_records": validation_records,
            "high_density_warning_files": high_density_files,
            "error_records": error_records,
            "status_only_files": status_only_files,
            "detail_total": issue_files,
            "evidence_tables": "entries、errors、metadata_diagnostics",
        },
    )


def _format_section(
    con: sqlite3.Connection,
    descriptor: dbreader.DatabaseDescriptor,
    row_limit: int,
) -> IssueSection:
    capability = descriptor.capability("format_checks")
    if capability.state not in ("available", "empty"):
        return _null_section(
            "format",
            "格式校验问题",
            capability.reason or "本次未执行或未记录格式校验",
        )
    problem_statuses = "'invalid','timeout','error','unstable'"
    issue_files = _count(
        con,
        "SELECT COUNT(DISTINCT entry_id) FROM format_checks"
        f" WHERE status IN ({problem_statuses})",
    )
    unsupported = _count(
        con,
        "SELECT COUNT(DISTINCT entry_id) FROM format_checks"
        " WHERE status='unsupported'",
    )
    details = []
    for row in con.execute(
        "SELECT r.root_label,e.rel_path,f.status,e.meta_status,e.hash_status,"
        " f.validator || CASE WHEN f.detail IS NULL THEN ''"
        " ELSE ': ' || f.detail END"
        " FROM format_checks f JOIN entries e ON e.entry_id=f.entry_id"
        " JOIN roots r ON r.root_id=e.root_id"
        f" WHERE f.status IN ({problem_statuses})"
        " ORDER BY r.root_label,e.path_key LIMIT ?",
        (row_limit,),
    ):
        advice = (
            "确认扫描期间文件未变化后重新校验"
            if row[2] == "unstable"
            else "用对应校验器复核文件结构，必要时从可靠副本恢复"
        )
        details.append(_detail(
            *row[:3],
            row[5],
            statuses=f"元数据={row[3]}；哈希={row[4]}；格式={row[2]}",
            advice=advice,
        ))
    return IssueSection(
        "format",
        "格式校验问题",
        "executed",
        None,
        issue_files,
        issue_files,
        tuple(details),
        {
            "unsupported_files": unsupported,
            "detail_total": issue_files,
            "evidence_tables": "format_checks、entry_attempts",
        },
    )


def _performance_section(
    con: sqlite3.Connection,
    descriptor: dbreader.DatabaseDescriptor,
    row_limit: int,
) -> IssueSection:
    capability = descriptor.capability("read_performance")
    scan_kind = str(descriptor.identity.get("scan_kind") or "")
    if descriptor.schema_version < 4 or scan_kind == "quick" \
            or capability.state not in ("available", "empty"):
        return _null_section(
            "performance",
            "读取性能异常候选",
            capability.reason or "旧库或本次任务未记录读取性能",
        )
    high = _count(
        con,
        "SELECT COUNT(DISTINCT entry_id) FROM read_performance"
        " WHERE candidate_confidence='high'",
    )
    high_records = _count(
        con,
        "SELECT COUNT(*) FROM read_performance"
        " WHERE candidate_confidence='high'",
    )
    low = _count(
        con,
        "SELECT COUNT(DISTINCT entry_id) FROM read_performance"
        " WHERE candidate_confidence='low'",
    )
    details = []
    for row in con.execute(
        "SELECT r.root_label,e.rel_path,'high',p.candidate_reason,"
        " p.size_bytes,p.bytes_read,p.elapsed_seconds,"
        " p.active_read_seconds,p.stall_count,p.longest_stall_seconds,"
        " p.final_offset,p.session_id,p.stage,p.origin"
        " FROM read_performance p"
        " JOIN entries e ON e.entry_id=p.entry_id"
        " JOIN roots r ON r.root_id=e.root_id"
        " WHERE p.candidate_confidence='high'"
        " ORDER BY r.root_label,e.path_key LIMIT ?",
        (row_limit,),
    ):
        elapsed = float(row[6])
        throughput = (
            float(row[5]) / elapsed / (1024 * 1024)
            if elapsed > 0 else None
        )
        throughput_text = (
            "不可计算" if throughput is None else f"{throughput:.3f} MiB/s"
        )
        evidence = (
            f"{row[3] or '数据库未记录候选依据'}；大小={row[4]} B；"
            f"读取={row[5]} B；总耗时={elapsed:.3f} s；"
            f"活跃读取={float(row[7]):.3f} s；平均吞吐={throughput_text}；"
            f"stall={row[8]}；最长 stall={float(row[9]):.3f} s；"
            f"最终偏移={row[10]}；session={row[11]}"
        )
        details.append(_detail(
            *row[:3],
            evidence,
            level="candidate",
            statuses=f"候选=high；阶段={row[12]}；来源={row[13]}",
            advice="需要人工复核，不能据此认定物理坏区或设备故障",
        ))
    return IssueSection(
        "performance",
        "读取性能异常候选",
        "executed",
        None,
        high,
        high_records,
        tuple(details),
        {
            "high_confidence_records": high_records,
            "low_confidence_files": low,
            "detail_total": high_records,
            "evidence_tables": "read_performance、entry_attempts",
        },
    )


def _runtime_section(
    con: sqlite3.Connection,
    descriptor: dbreader.DatabaseDescriptor,
    row_limit: int,
) -> IssueSection:
    if descriptor.schema_version < 4:
        return _null_section(
            "runtime",
            "运行／证据问题",
            "v1.4.1/schema 3 未记录 session 与阶段恢复证据",
        )
    session_count = _count(
        con,
        "SELECT COUNT(*) FROM run_sessions"
        " WHERE session_status IN ('failed','abandoned')",
    )
    stage_count = _count(
        con,
        "SELECT COUNT(*) FROM stage_checkpoints"
        " WHERE state IN ('failed_recoverable','failed_terminal')",
    )
    details = []
    for row in con.execute(
            "SELECT '',stage,state,checkpoint_json FROM stage_checkpoints"
            " WHERE state IN ('failed_recoverable','failed_terminal')"
            " ORDER BY stage LIMIT ?",
            (row_limit,),
        ):
        details.append(_detail(
            *row,
            statuses=f"阶段={row[2]}",
            advice="检查 checkpoint 与运行事件后恢复或重新执行该阶段",
        ))
    remaining = max(0, row_limit - len(details))
    if remaining:
        for row in con.execute(
            "SELECT '',session_id,session_status,COALESCE(end_reason,'')"
            " FROM run_sessions"
            " WHERE session_status IN ('failed','abandoned')"
            " ORDER BY session_number LIMIT ?",
            (remaining,),
        ):
            details.append(_detail(
                *row,
                level="candidate",
                statuses=f"session={row[2]}",
                advice="这是历史运行证据；确认后续 session 已完整恢复",
            ))
    total = session_count + stage_count
    return IssueSection(
        "runtime",
        "运行／证据问题",
        "executed",
        None,
        0,
        total,
        tuple(details),
        {
            "failed_or_abandoned_sessions": session_count,
            "failed_stages": stage_count,
            "detail_total": total,
            "evidence_tables": "run_sessions、stage_checkpoints、run_state_events",
        },
    )


def _validate_row_limit(row_limit: int) -> int:
    if isinstance(row_limit, bool) or not isinstance(row_limit, int) \
            or row_limit <= 0:
        raise ValueError("Issues row_limit 必须是正整数")
    return row_limit


def _analyze_snapshot_connection(
    con: sqlite3.Connection,
    descriptor: dbreader.DatabaseDescriptor,
    *,
    database: str,
    row_limit: int,
) -> dict[str, object]:
    try:
        con.create_function(
            "daisy_issue_visible",
            2,
            lambda code, message: int(
                core.issue_record_is_visible(code, message)),
        )

        def diagnostic_class(severity, code, message) -> str:
            normalized_severity = str(severity or "").casefold()
            if not core.issue_record_is_visible(code, message):
                return "unsupported"
            if normalized_severity == "validation":
                return "validation"
            if normalized_severity == "warning":
                if str(message or "").lstrip().casefold().startswith(
                        "[minor]"):
                    return "minor"
                findings = dbverify.classify_et_findings([
                    ("Warning", str(message or "")),
                ])
                return "candidate" if findings else "warning"
            if normalized_severity == "error":
                return "need_action"
            return "information"

        con.create_function(
            "daisy_diagnostic_class", 3, diagnostic_class,
            deterministic=True,
        )
        con.create_function(
            "daisy_normalize_family", 1, normalize_diagnostic_family,
            deterministic=True,
        )
        sections = (
            _enumeration_section(con, row_limit),
            _hash_section(con, descriptor, row_limit),
            _metadata_section(con, descriptor, row_limit),
            _format_section(con, descriptor, row_limit),
            _null_section(
                "raw",
                "RAW 深度校验问题",
                "本次未启用、未提供伴随证据或旧库未记录",
            ),
            _performance_section(con, descriptor, row_limit),
            _runtime_section(con, descriptor, row_limit),
        )
        info = con.execute(
            "SELECT snapshot_uuid,scanner_version,scan_status,"
            " database_integrity FROM snapshot_info WHERE id=1"
        ).fetchone()
        if info is None:
            raise core.PreflightError("Issues 分析缺少 snapshot_info id=1")
        return {
            "database": str(database),
            "schema_version": descriptor.schema_version,
            "lifecycle": descriptor.lifecycle,
            "snapshot_uuid": str(info[0]),
            "scanner_version": str(info[1]),
            "scan_status": str(info[2]),
            "database_integrity": str(info[3]),
            "sections": [section.as_dict() for section in sections],
            "has_reportable_issues": any(
                section.reportable for section in sections),
            "analyzed_at_utc": core.now_utc_iso(),
        }
    except sqlite3.Error as exc:
        raise core.PreflightError(f"Issues 只读分析失败：{exc}") from exc


def analyze_snapshot_issue_connection(
    con: sqlite3.Connection,
    *,
    database: str = "<connection>",
    row_limit: int = DETAIL_LIMIT,
) -> dict[str, object]:
    """分析调用方持有的只读发布连接；不关闭连接、不修改数据库。"""
    limit = _validate_row_limit(row_limit)
    descriptor = dbreader.inspect_connection(
        con,
        path="<connection>",
        expected_type="snapshot",
        require_sealed=True,
    )
    return _analyze_snapshot_connection(
        con,
        descriptor,
        database=database,
        row_limit=limit,
    )


def analyze_snapshot_issues(
    snapshot_path: str,
    *,
    require_sealed: bool = True,
    row_limit: int = DETAIL_LIMIT,
) -> dict[str, object]:
    """只读分析 schema 3／4 快照；NULL 与执行后 0 严格分开。"""
    limit = _validate_row_limit(row_limit)
    con, descriptor = dbreader.open_database(
        snapshot_path,
        expected_type="snapshot",
        require_sealed=require_sealed,
    )
    try:
        return _analyze_snapshot_connection(
            con,
            descriptor,
            database=os.path.basename(os.path.abspath(snapshot_path)),
            row_limit=limit,
        )
    finally:
        con.close()


def render_snapshot_issues(
    analysis: dict[str, object],
    *,
    artifact_filename: str | None = None,
    include_clean: bool = False,
    section_overrides: dict[str, dict[str, object]] | None = None,
) -> str | None:
    """渲染固定板块；只有 unsupported／低置信度时不生成 Issues。"""
    sections = list(analysis.get("sections") or [])
    by_id = {
        str(section.get("id")): dict(section)
        for section in sections if isinstance(section, dict)
    }
    overrides = dict(section_overrides or {})
    unknown = sorted(set(overrides) - {item[0] for item in ISSUE_SECTIONS})
    if unknown:
        raise ValueError(f"未知 Issues 板块覆盖：{unknown}")
    for section_id, override in overrides.items():
        if not isinstance(override, dict):
            raise ValueError(f"Issues 板块覆盖必须是对象：{section_id}")
        execution = str(override.get("execution") or "")
        if execution not in ("executed", "null"):
            raise ValueError(
                f"Issues 板块覆盖执行状态无效：{section_id}")
        issue_files = override.get("issue_files")
        issue_records = override.get("issue_records")
        if execution == "null" and (
                issue_files is not None or issue_records is not None):
            raise ValueError(f"NULL 板块覆盖不能伪造 0：{section_id}")
        if execution == "executed":
            try:
                issue_files = int(issue_files or 0)
                issue_records = int(issue_records or 0)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"Issues 板块覆盖计数无效：{section_id}") from exc
            if issue_files < 0 or issue_records < 0:
                raise ValueError(
                    f"Issues 板块覆盖计数不能为负：{section_id}")
        details = override.get("details") or []
        information = override.get("information") or {}
        if not isinstance(details, list) or not isinstance(information, dict):
            raise ValueError(
                f"Issues 板块覆盖明细或信息无效：{section_id}")
        title = dict(ISSUE_SECTIONS)[section_id]
        by_id[section_id] = {
            **override,
            "id": section_id,
            "title": title,
            "issue_files": issue_files,
            "issue_records": issue_records,
            "details": list(details),
            "information": dict(information),
        }
    reportable = any(
        section.get("execution") == "executed"
        and bool(section.get("issue_files") or section.get("issue_records"))
        for section in by_id.values()
    )
    if not include_clean and not reportable:
        return None
    lines = [
        "# DAISY 问题报告",
        "",
        *core.report_markdown_lines("DBS 快照问题报告"),
        f"- 数据库：`{artifact_filename or analysis.get('database', '')}`",
        f"- 快照 UUID：`{analysis.get('snapshot_uuid', '')}`",
        f"- 原扫描器版本：`{analysis.get('scanner_version', '')}`",
        f"- schema：`{analysis.get('schema_version', '')}`",
        "- 说明：本报告是数据库证据的人读提炼；不表示 SQLite 本身损坏。",
        "",
        "## 板块状态",
        "",
        "| 板块 | 执行状态 | 问题文件 | 问题记录 |",
        "| --- | --- | ---: | ---: |",
    ]
    for section_id, title in ISSUE_SECTIONS:
        section = by_id.get(section_id, {})
        if section.get("execution") == "executed":
            execution = "已执行"
            files = section.get("issue_files", 0)
            records = section.get("issue_records", 0)
        else:
            reason = str(section.get("reason") or "未执行或未记录")
            execution = f"NULL（{reason}）"
            files = "NULL"
            records = "NULL"
        lines.append(
            f"| {title} | {core.markdown_cell(execution)} | {files} |"
            f" {records} |")

    for section_id, title in ISSUE_SECTIONS:
        section = by_id.get(section_id, {})
        lines.extend(["", f"## {title}", ""])
        if section.get("execution") != "executed":
            lines.append(
                "- 执行状态：NULL（"
                + str(section.get("reason") or "未执行或未记录") + "）")
            lines.append("- 问题文件：NULL")
            lines.append("- 问题记录：NULL")
            continue
        lines.append("- 执行状态：已执行")
        lines.append(f"- 问题文件：{section.get('issue_files', 0)}")
        lines.append(f"- 问题记录：{section.get('issue_records', 0)}")
        information = section.get("information") or {}
        details = section.get("details") or []
        if not isinstance(details, list):
            details = []
        if isinstance(information, dict):
            detail_total = information.get(
                "detail_total", section.get("issue_files", 0))
            evidence_tables = information.get(
                "evidence_tables", "数据库对应证据表")
            lines.append(f"- 明细：已展示 {len(details)}／共 {detail_total}")
            lines.append(f"- 完整证据表：`{evidence_tables}`")
            for key, value in information.items():
                if key in _FOLDED_INFORMATION_KEYS or key in (
                        "detail_total", "evidence_tables"):
                    continue
                label = _INFORMATION_LABELS.get(str(key), str(key))
                lines.append(f"- {label}：{value}")
        if details:
            for level, level_title in _LEVEL_TITLES:
                level_rows = [
                    row for row in details
                    if isinstance(row, dict)
                    and str(row.get("level") or "need_action") == level
                ]
                if not level_rows:
                    continue
                lines.extend([
                    "",
                    f"### {level_title}",
                    "",
                    "| 根标签 | 相对路径／对象 | 当前结论 | 状态汇总 |"
                    " 错误／依据 | 建议操作 |",
                    "| --- | --- | --- | --- | --- | --- |",
                ])
                for row in level_rows:
                    values = (
                        row.get("root_label"),
                        row.get("relative_path"),
                        row.get("status"),
                        row.get("statuses"),
                        row.get("detail"),
                        row.get("advice"),
                    )
                    lines.append(
                        "| " + " | ".join(
                            core.markdown_cell(value) for value in values
                        ) + " |")
        folded_information = []
        if isinstance(information, dict):
            for key in _FOLDED_INFORMATION_KEYS:
                value = information.get(key)
                if value:
                    folded_information.append(
                        (_INFORMATION_LABELS.get(key, key), value))
        if folded_information:
            lines.extend(["", "### 信息性诊断", ""])
            for label, value in sorted(folded_information):
                lines.append(f"- {label}：{value}")
    lines.extend([
        "",
        "## 说明",
        "",
        "unknown／unsupported／unrecognized format 只显示去重总数，"
        "不列路径，也不会单独触发本报告。",
        "普通 warning、`[minor]` warning、validation 和低置信度性能样本"
        "保留在 SQLite，不在这里冒充需要处理的问题。",
        "读取性能异常仅是待复核候选，不能据此认定物理坏区或设备故障。",
        "报告明细有上限；完整逐文件证据以各板块列出的 SQLite 表为准。",
        "",
    ])
    return "\n".join(lines)


def build_snapshot_issue_report(
    snapshot_path: str,
    *,
    artifact_filename: str | None = None,
    require_sealed: bool = True,
    row_limit: int = DETAIL_LIMIT,
    include_clean: bool = False,
    section_overrides: dict[str, dict[str, object]] | None = None,
) -> str | None:
    analysis = analyze_snapshot_issues(
        snapshot_path,
        require_sealed=require_sealed,
        row_limit=row_limit,
    )
    return render_snapshot_issues(
        analysis,
        artifact_filename=artifact_filename,
        include_clean=include_clean,
        section_overrides=section_overrides,
    )


def build_snapshot_issue_report_from_connection(
    con: sqlite3.Connection,
    artifact_filename: str,
    *,
    row_limit: int = DETAIL_LIMIT,
    include_clean: bool = False,
    section_overrides: dict[str, dict[str, object]] | None = None,
) -> str | None:
    """供 schema 4 发布事务在只读副本上生成最终命名的人读报告。"""
    analysis = analyze_snapshot_issue_connection(
        con,
        database=artifact_filename,
        row_limit=row_limit,
    )
    return render_snapshot_issues(
        analysis,
        artifact_filename=artifact_filename,
        include_clean=include_clean,
        section_overrides=section_overrides,
    )
