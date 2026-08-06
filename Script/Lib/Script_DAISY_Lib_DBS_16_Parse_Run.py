"""DAISY 数据库解析的流式技术导出与原子发布执行层。

本模块只消费统一 Reader 和版本化投影。输入数据库始终以只读 URI 打开；一次任务
持有一致读取事务，CSV／JSONL 共享同一次模块遍历。所有产物先写入输出目录内的唯一
staging，关闭、摘要和 manifest 完成后才以 no-clobber 目录重命名发布。
"""
from __future__ import annotations

from contextlib import ExitStack
import csv
from dataclasses import dataclass
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
from typing import Callable

import Script_DAISY_Lib_DBS_01_Core as core
import Script_DAISY_Lib_DBS_05_Reader as dbreader
import Script_DAISY_Lib_DBS_07_Parse as dbparse
import Script_DAISY_Lib_DBS_15_Parse_Projection as projection
import Script_DAISY_Lib_DBS_17_Parse_Human as human


REPORT_CONTRACT = "daisy-parse-report-v1"
JSONL_CONTRACT = "daisy-parse-jsonl-v1"
MANIFEST_NAME = "Report_manifest.json"
TECHNICAL_FORMATS = frozenset(("csv", "jsonl"))
SUPPORTED_FORMATS = frozenset(("html", "xlsx", "csv", "jsonl"))
_STAGING_PREFIX = ".daisy-parse-staging-"
_UTC_RE = re.compile(
    r"\A(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})"
    r"(?:\.(\d{1,9}))?Z\Z"
)


class ParseExportCancelled(Exception):
    """数据库解析在安全批次边界被调用方取消。"""


@dataclass(frozen=True)
class ParseInputIdentity:
    sha256: str
    size_bytes: int
    mtime_ns: int

    def as_dict(self) -> dict[str, object]:
        return {
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "mtime_ns": self.mtime_ns,
            "mtime_utc": core.ns_to_utc_iso(self.mtime_ns),
        }


@dataclass(frozen=True)
class ParseArtifact:
    module_id: str
    format_id: str
    relative_path: str
    row_count: int
    fields: tuple[str, ...]
    sha256: str
    size_bytes: int

    def as_dict(self) -> dict[str, object]:
        return {
            "module_id": self.module_id,
            "format": self.format_id,
            "path": self.relative_path,
            "rows": self.row_count,
            "fields": list(self.fields),
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True)
class ParseProgress:
    phase: str
    module_id: str | None
    module_index: int
    module_total: int
    rows_done: int
    message: str


@dataclass(frozen=True)
class ParseExportResult:
    report_directory: str
    manifest_path: str
    database_type: str
    schema_version: int
    input_identity: ParseInputIdentity
    artifacts: tuple[ParseArtifact, ...]


CancelCheck = Callable[[], bool] | None
ProgressCallback = Callable[[ParseProgress], None] | None


def _check_cancel(cancel_check: CancelCheck) -> None:
    if cancel_check is not None and cancel_check():
        raise ParseExportCancelled("数据库解析已取消")


def _notify(
    callback: ProgressCallback,
    *,
    phase: str,
    module_id: str | None,
    module_index: int,
    module_total: int,
    rows_done: int,
    message: str,
) -> None:
    if callback is not None:
        callback(ParseProgress(
            phase=phase,
            module_id=module_id,
            module_index=module_index,
            module_total=module_total,
            rows_done=rows_done,
            message=message,
        ))


def _sha256_file(
    path: str,
    *,
    cancel_check: CancelCheck = None,
) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while block := handle.read(1024 * 1024):
            _check_cancel(cancel_check)
            digest.update(block)
    return digest.hexdigest()


def _input_identity(
    path: str,
    *,
    cancel_check: CancelCheck = None,
) -> ParseInputIdentity:
    before = os.stat(path)
    digest = _sha256_file(path, cancel_check=cancel_check)
    after = os.stat(path)
    if (
        before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
    ):
        raise core.PreflightError(
            "输入数据库在计算 SHA-256 期间发生变化")
    return ParseInputIdentity(
        sha256=digest,
        size_bytes=int(after.st_size),
        mtime_ns=int(after.st_mtime_ns),
    )


def _utc_name_token(value: str) -> str:
    match = _UTC_RE.fullmatch(str(value))
    if match is None:
        raise ValueError(f"报告时间不是 UTC ISO 8601：{value!r}")
    year, month, day, hour, minute, second, fraction = match.groups()
    fraction = (fraction or "0")[:6].ljust(6, "0")
    return (
        f"{year}{month}{day}T{hour}{minute}{second}."
        f"{fraction}Z"
    )


def _safe_database_stem(path: str) -> str:
    stem = os.path.splitext(os.path.basename(path))[0]
    for character in '<>:"/\\|?*':
        stem = stem.replace(character, "_")
    stem = stem.rstrip(" .")
    return stem or "Database"


def _ensure_immediate_child(path: str, parent: str, prefix: str) -> None:
    normalized_path = os.path.abspath(path)
    normalized_parent = os.path.abspath(parent)
    if os.path.dirname(normalized_path) != normalized_parent:
        raise RuntimeError("解析 staging 不在预期输出目录的直接子级")
    if not os.path.basename(normalized_path).startswith(prefix):
        raise RuntimeError("解析 staging 名称不属于当前执行层")


def _remove_owned_staging(staging: str, output_dir: str) -> None:
    _ensure_immediate_child(staging, output_dir, _STAGING_PREFIX)
    if not os.path.lexists(staging):
        return
    if os.path.islink(staging):
        raise RuntimeError("拒绝递归清理被替换为链接的解析 staging")
    shutil.rmtree(staging)


def _artifact_identity(
    path: str,
    *,
    cancel_check: CancelCheck = None,
) -> tuple[str, int]:
    return (
        _sha256_file(path, cancel_check=cancel_check),
        int(os.stat(path).st_size),
    )


def _database_identity(
    descriptor: dbreader.DatabaseDescriptor,
) -> dict[str, object]:
    identity_key = (
        "snapshot_uuid"
        if descriptor.database_type == "snapshot" else "diff_uuid"
    )
    return {
        "database_type": descriptor.database_type,
        "schema_version": descriptor.schema_version,
        "uuid": descriptor.identity.get(identity_key),
    }


def _compatibility_mode(
    descriptor: dbreader.DatabaseDescriptor,
) -> str:
    if descriptor.database_type == "snapshot":
        return (
            "v1.4.1-compatible"
            if descriptor.schema_version == 3 else "v1.6.0-native"
        )
    schemas = (
        int(descriptor.identity["old_schema_version"]),
        int(descriptor.identity["new_schema_version"]),
    )
    return "v1.4.1-compatible" if schemas == (3, 3) else "cross-version"


def _validate_plan(
    descriptor: dbreader.DatabaseDescriptor,
    plan: dbparse.ParseExportPlan,
) -> dict[str, dbparse.ParseModuleStatus]:
    if len(set(plan.module_ids)) != len(plan.module_ids):
        raise core.PreflightError("解析计划包含重复模块")
    if len(set(plan.format_ids)) != len(plan.format_ids):
        raise core.PreflightError("解析计划包含重复格式")
    unsupported = sorted(set(plan.format_ids) - SUPPORTED_FORMATS)
    if unsupported:
        raise core.PreflightError(
            "数据库解析执行层不支持格式：" + "、".join(unsupported))
    statuses = {
        status.spec.module_id: status
        for status in dbparse.parse_module_statuses(descriptor)
    }
    for module_id in plan.module_ids:
        status = statuses.get(module_id)
        if status is None:
            raise core.PreflightError(
                f"当前数据库没有解析模块：{module_id}")
        if not status.selectable:
            detail = f"：{status.reason}" if status.reason else ""
            raise core.PreflightError(
                f"解析模块 {module_id} 为 {status.state}，不可导出{detail}")
    if not plan.module_ids:
        raise core.PreflightError("解析计划未选择任何模块")
    if not plan.format_ids:
        raise core.PreflightError("解析计划未选择任何格式")
    for format_id in plan.format_ids:
        expected = tuple(
            module_id for module_id in plan.module_ids
            if format_id in statuses[module_id].spec.formats
        )
        actual = tuple(plan.format_modules.get(format_id, ()))
        if actual != expected:
            raise core.PreflightError(
                f"解析计划的 {format_id} 模块映射与当前能力不一致")
    for module_id in plan.module_ids:
        if not any(
            module_id in plan.format_modules.get(format_id, ())
            for format_id in plan.format_ids
        ):
            raise core.PreflightError(
                f"解析模块 {module_id} 没有可执行的输出格式")
    return statuses


class _CsvSink:
    def __init__(
        self,
        path: str,
        fields: tuple[str, ...],
        stack: ExitStack,
    ) -> None:
        handle = stack.enter_context(open(
            path,
            "x",
            encoding="utf-8",
            newline="",
        ))
        self.writer = csv.DictWriter(
            handle,
            fieldnames=list(fields),
            extrasaction="raise",
            lineterminator="\n",
        )
        self.writer.writeheader()

    def write(self, row: dict[str, object]) -> None:
        self.writer.writerow(row)


class _JsonlSink:
    def __init__(
        self,
        path: str,
        module_id: str,
        database_identity: dict[str, object],
        stack: ExitStack,
    ) -> None:
        self.handle = stack.enter_context(open(
            path,
            "x",
            encoding="utf-8",
            newline="\n",
        ))
        self.module_id = module_id
        self.database_identity = database_identity

    def write(self, row: dict[str, object]) -> None:
        value = {
            "contract": JSONL_CONTRACT,
            "database": self.database_identity,
            "module_id": self.module_id,
            "record": row,
        }
        self.handle.write(json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
        ))
        self.handle.write("\n")


def _module_formats(
    plan: dbparse.ParseExportPlan,
    module_id: str,
) -> tuple[str, ...]:
    return tuple(
        format_id for format_id in plan.format_ids
        if module_id in plan.format_modules.get(format_id, ())
    )


def _compatibility_notes(
    status: dbparse.ParseModuleStatus,
) -> list[dict[str, object]]:
    return [
        dict(capability)
        for capability in status.optional_capabilities
        if capability.get("state") != "available"
    ]


def _write_manifest(
    staging: str,
    *,
    generated_at_utc: str,
    descriptor: dbreader.DatabaseDescriptor,
    input_identity: ParseInputIdentity,
    plan: dbparse.ParseExportPlan,
    module_records: list[dict[str, object]],
    artifacts: tuple[ParseArtifact, ...],
) -> str:
    manifest_path = os.path.join(staging, MANIFEST_NAME)
    manifest = {
        "contract": REPORT_CONTRACT,
        "generated_at_utc": generated_at_utc,
        "tool": core.report_metadata("数据库解析"),
        "input": {
            "filename": os.path.basename(descriptor.path),
            **_database_identity(descriptor),
            "source_version": descriptor.source_version,
            "lifecycle": descriptor.lifecycle,
            "compatibility_mode": _compatibility_mode(descriptor),
            **input_identity.as_dict(),
        },
        "plan": plan.as_dict(),
        "modules": module_records,
        "artifacts": [artifact.as_dict() for artifact in artifacts],
        "warnings": {
            "reader": list(descriptor.warnings),
            "privacy": list(plan.privacy_notices),
            "csv": (
                "CSV 保留完整原值且不加入 Excel 专用前缀；"
                "用电子表格软件打开不可信数据时应禁用公式执行。"
                if "csv" in plan.format_ids else None
            ),
        },
    }
    with open(
        manifest_path,
        "x",
        encoding="utf-8",
        newline="\n",
    ) as handle:
        json.dump(
            manifest,
            handle,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        handle.write("\n")
    return manifest_path


def _publish_directory_no_clobber(staging: str, final_dir: str) -> None:
    if os.path.lexists(final_dir):
        raise core.PreflightError(
            f"报告发布冲突：目标已存在且不会覆盖：{final_dir}")
    try:
        os.rename(staging, final_dir)
    except FileExistsError as exc:
        raise core.PreflightError(
            f"报告发布冲突：目标已存在且不会覆盖：{final_dir}") from exc
    except OSError as exc:
        raise core.PreflightError(
            f"报告目录原子发布失败：{final_dir}：{exc}") from exc


def export_parse_report(
    database: str,
    output_dir: str,
    plan: dbparse.ParseExportPlan,
    *,
    cancel_check: CancelCheck = None,
    progress_callback: ProgressCallback = None,
    batch_rows: int = projection.DEFAULT_BATCH_ROWS,
    progress_every_rows: int = 256,
    generated_at_utc: str | None = None,
    html_preview_rows: int = human.DEFAULT_PREVIEW_ROWS,
    html_cell_chars: int = human.DEFAULT_HTML_CELL_CHARS,
    xlsx_max_rows: int = human.DEFAULT_XLSX_MAX_ROWS,
    xlsx_max_cell_chars: int = human.DEFAULT_XLSX_MAX_CELL_CHARS,
) -> ParseExportResult:
    """流式导出所选格式，并在完整验证后原子发布报告目录。"""
    if batch_rows <= 0:
        raise ValueError("batch_rows 必须大于 0")
    if progress_every_rows <= 0:
        raise ValueError("progress_every_rows 必须大于 0")
    database = os.path.abspath(os.fspath(database))
    output_dir = os.path.abspath(os.fspath(output_dir))
    generated_at_utc = generated_at_utc or core.now_utc_iso()
    timestamp_token = _utc_name_token(generated_at_utc)
    os.makedirs(output_dir, exist_ok=True)
    if not os.path.isdir(output_dir):
        raise core.PreflightError(f"报告目录不是文件夹：{output_dir}")

    _check_cancel(cancel_check)
    _notify(
        progress_callback,
        phase="fingerprint",
        module_id=None,
        module_index=0,
        module_total=len(plan.module_ids),
        rows_done=0,
        message="正在核对输入数据库 SHA-256",
    )
    input_before = _input_identity(
        database, cancel_check=cancel_check)
    con, descriptor = dbreader.open_database(
        database,
        require_sealed=True,
        verify_integrity=True,
        verify_artifact_fingerprint=True,
    )
    staging = tempfile.mkdtemp(
        prefix=_STAGING_PREFIX,
        dir=output_dir,
    )
    artifacts: list[ParseArtifact] = []
    module_records: list[dict[str, object]] = []
    human_context: human.HumanReportContext | None = None
    try:
        statuses = _validate_plan(descriptor, plan)
        human_context = human.HumanReportContext(
            staging,
            descriptor,
            plan,
            generated_at_utc,
            preview_rows=html_preview_rows,
            html_cell_chars=html_cell_chars,
            xlsx_max_rows=xlsx_max_rows,
            xlsx_max_cell_chars=xlsx_max_cell_chars,
        )
        con.execute("BEGIN")

        def progress_handler() -> int:
            return int(cancel_check is not None and cancel_check())

        con.set_progress_handler(progress_handler, 10_000)
        database_identity = _database_identity(descriptor)
        module_total = len(plan.module_ids)
        for module_index, module_id in enumerate(plan.module_ids, 1):
            _check_cancel(cancel_check)
            status = statuses[module_id]
            definition = projection.projection_definition(
                descriptor.database_type, module_id)
            formats = _module_formats(plan, module_id)
            _notify(
                progress_callback,
                phase="module",
                module_id=module_id,
                module_index=module_index,
                module_total=module_total,
                rows_done=0,
                message=f"正在导出模块 {module_id}",
            )
            technical_formats = tuple(
                format_id for format_id in formats
                if format_id in TECHNICAL_FORMATS
            )
            paths = {
                format_id: os.path.join(
                    staging, f"{module_id}.{format_id}")
                for format_id in technical_formats
            }
            with ExitStack() as stack:
                sinks = []
                for format_id in technical_formats:
                    if format_id == "csv":
                        sink = _CsvSink(
                            paths[format_id], definition.fields, stack)
                    elif format_id == "jsonl":
                        sink = _JsonlSink(
                            paths[format_id],
                            module_id,
                            database_identity,
                            stack,
                        )
                    else:
                        raise RuntimeError(
                            f"未注册的技术输出格式：{format_id}")
                    sinks.append(sink)
                sinks.extend(human_context.open_module_sinks(
                    module_id,
                    status.spec.title,
                    definition.fields,
                    formats,
                    stack,
                    module_preview_limit=status.spec.preview_limit,
                ))
                row_count = 0
                for row in projection.iter_module_rows(
                    con,
                    descriptor,
                    module_id,
                    batch_rows=batch_rows,
                    cancel_check=cancel_check,
                ):
                    for sink in sinks:
                        sink.write(row)
                    row_count += 1
                    if row_count % progress_every_rows == 0:
                        _notify(
                            progress_callback,
                            phase="module",
                            module_id=module_id,
                            module_index=module_index,
                            module_total=module_total,
                            rows_done=row_count,
                            message=f"{module_id} 已处理 {row_count} 行",
                        )
            module_artifacts = []
            for format_id in technical_formats:
                digest, size_bytes = _artifact_identity(
                    paths[format_id], cancel_check=cancel_check)
                artifact = ParseArtifact(
                    module_id=module_id,
                    format_id=format_id,
                    relative_path=os.path.basename(paths[format_id]),
                    row_count=row_count,
                    fields=definition.fields,
                    sha256=digest,
                    size_bytes=size_bytes,
                )
                artifacts.append(artifact)
                module_artifacts.append(artifact.relative_path)
            if "html" in formats:
                module_artifacts.append(human.HTML_NAME)
            if "xlsx" in formats:
                module_artifacts.append(human.XLSX_NAME)
            module_records.append({
                "module_id": module_id,
                "title": status.spec.title,
                "state": status.state,
                "projection_version": definition.projection_version,
                "fields": list(definition.fields),
                "rows": row_count,
                "formats": list(formats),
                "artifacts": module_artifacts,
                "compatibility_notes": _compatibility_notes(status),
            })
            _notify(
                progress_callback,
                phase="module",
                module_id=module_id,
                module_index=module_index,
                module_total=module_total,
                rows_done=row_count,
                message=f"模块 {module_id} 已完成，共 {row_count} 行",
            )

        for item in human_context.finalize(module_records):
            path = os.path.join(staging, item.relative_path)
            digest, size_bytes = _artifact_identity(
                path, cancel_check=cancel_check)
            artifacts.append(ParseArtifact(
                module_id="__report__",
                format_id=item.format_id,
                relative_path=item.relative_path,
                row_count=item.row_count,
                fields=(),
                sha256=digest,
                size_bytes=size_bytes,
            ))
        con.set_progress_handler(None, 0)
        con.execute("COMMIT")
        input_after = _input_identity(
            database, cancel_check=cancel_check)
        if input_after != input_before:
            raise core.PreflightError(
                "输入数据库在解析前后发生变化，报告不会发布")
        _write_manifest(
            staging,
            generated_at_utc=generated_at_utc,
            descriptor=descriptor,
            input_identity=input_before,
            plan=plan,
            module_records=module_records,
            artifacts=tuple(artifacts),
        )
        _check_cancel(cancel_check)
        stat_before_publish = os.stat(database)
        if (
            stat_before_publish.st_size != input_before.size_bytes
            or stat_before_publish.st_mtime_ns != input_before.mtime_ns
        ):
            raise core.PreflightError(
                "输入数据库在报告发布前发生变化，报告不会发布")
        final_dir = os.path.join(
            output_dir,
            f"{_safe_database_stem(database)}_Report_{timestamp_token}",
        )
        _notify(
            progress_callback,
            phase="publish",
            module_id=None,
            module_index=len(plan.module_ids),
            module_total=len(plan.module_ids),
            rows_done=sum(int(item["rows"]) for item in module_records),
            message="正在原子发布报告目录",
        )
        _publish_directory_no_clobber(staging, final_dir)
        staging = ""
        return ParseExportResult(
            report_directory=final_dir,
            manifest_path=os.path.join(final_dir, MANIFEST_NAME),
            database_type=descriptor.database_type,
            schema_version=descriptor.schema_version,
            input_identity=input_before,
            artifacts=tuple(artifacts),
        )
    except projection.ParseProjectionCancelled as exc:
        raise ParseExportCancelled(str(exc)) from exc
    except sqlite3.OperationalError as exc:
        if cancel_check is not None and cancel_check():
            raise ParseExportCancelled("数据库解析已取消") from exc
        raise core.PreflightError(f"数据库解析查询失败：{exc}") from exc
    finally:
        try:
            con.set_progress_handler(None, 0)
        except sqlite3.Error:
            pass
        try:
            if con.in_transaction:
                con.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        con.close()
        if human_context is not None:
            human_context.cleanup()
        if staging:
            _remove_owned_staging(staging, output_dir)


# 第二检查点公开过该内部名称；保留精确别名，调用方无需迁移。
export_technical_report = export_parse_report
