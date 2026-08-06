"""RAW 深度校验工作 JSONL、最终伴随 JSON 与 Issues 板块投影。

本模块不打开 SQLite。工作日志以 snapshot UUID 和冻结配置摘要绑定；valid／unsupported
记录不保存路径，只有 invalid／timeout／error 在最终报告中保留问题路径。
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
import threading
import uuid
from typing import Iterable, Mapping

import Script_DAISY_Lib_DBS_01_Core as core
import Script_DAISY_Lib_DBS_13_Raw as dbraw


RAW_WORK_CONTRACT = "daisy-raw-verification-work-v1"
RAW_REPORT_CONTRACT = "daisy-raw-verification-v1"
RAW_RESULT_STATUSES = frozenset((
    "valid", "unsupported", "invalid", "timeout", "error",
))
RAW_ISSUE_STATUSES = frozenset(("invalid", "timeout", "error"))


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256_text(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("RAW 证据写入未取得进展")
        view = view[written:]


@dataclass(frozen=True)
class RawEvidenceBinding:
    snapshot_uuid: str
    format_mode: str
    format_sample_percent: float
    rawpy_version: str
    libraw_version: str | None = None

    def __post_init__(self) -> None:
        if not str(self.snapshot_uuid).strip():
            raise ValueError("RAW 证据绑定缺少 snapshot_uuid")
        if self.format_mode not in ("sample", "all"):
            raise ValueError("RAW 深检只能绑定 sample／all 格式范围")
        percent = float(self.format_sample_percent)
        if not 0.0 <= percent <= 100.0:
            raise ValueError("RAW 证据格式抽样比例必须在 0～100")
        if self.format_mode == "all" and percent != 100.0:
            raise ValueError("RAW 全量格式范围必须记录 100%")
        if not str(self.rawpy_version).strip():
            raise ValueError("RAW 证据绑定缺少 rawpy 版本")

    def as_dict(self) -> dict[str, object]:
        return {
            "snapshot_uuid": str(self.snapshot_uuid),
            "format_mode": self.format_mode,
            "format_sample_percent": float(self.format_sample_percent),
            "rawpy_version": str(self.rawpy_version),
            "libraw_version": (
                str(self.libraw_version)
                if self.libraw_version is not None else None),
        }

    @property
    def fingerprint(self) -> str:
        return _sha256_text(self.as_dict())


def raw_working_evidence_path(partial_path: str) -> str:
    path = os.path.abspath(partial_path)
    suffix = ".partial.sqlite"
    if not path.casefold().endswith(suffix):
        raise ValueError("RAW 工作证据要求 .partial.sqlite 路径")
    return path[:-len(suffix)] + ".raw_verification.jsonl"


def raw_report_path(snapshot_path: str) -> str:
    path = os.path.abspath(snapshot_path)
    stem, extension = os.path.splitext(path)
    if extension.casefold() != ".sqlite":
        raise ValueError("RAW 最终伴随报告要求 .sqlite 快照路径")
    return stem + "_Raw_Verification.json"


def _validate_result_record(record: Mapping[str, object]) -> None:
    if record.get("record") != "result":
        raise core.PreflightError("RAW JSONL 存在未知记录类型")
    try:
        sequence = int(record["sequence"])
        entry_id = int(record["entry_id"])
        size_bytes = int(record["size_bytes"])
    except (KeyError, TypeError, ValueError) as exc:
        raise core.PreflightError("RAW JSONL 结果身份字段无效") from exc
    if sequence <= 0 or entry_id <= 0 or size_bytes < 0:
        raise core.PreflightError("RAW JSONL 结果身份数值越界")
    if not str(record.get("modified_at_utc") or ""):
        raise core.PreflightError("RAW JSONL 结果缺少 mtime 身份")
    status = str(record.get("status") or "")
    if status not in RAW_RESULT_STATUSES:
        raise core.PreflightError(f"RAW JSONL 结果状态无效：{status!r}")
    path = record.get("path")
    detail = record.get("detail")
    if status in ("valid", "unsupported"):
        if path is not None or detail is not None:
            raise core.PreflightError(
                "RAW valid／unsupported 记录不得保存路径或明细")
    else:
        if not isinstance(path, str) or not path:
            raise core.PreflightError("RAW 问题记录缺少逻辑路径")
    if status == "valid":
        for key in (
                "width", "height", "channels", "pixel_count",
                "decoded_bytes"):
            try:
                value = int(record[key])
            except (KeyError, TypeError, ValueError) as exc:
                raise core.PreflightError(
                    f"RAW valid 记录缺少 {key}") from exc
            if value <= 0:
                raise core.PreflightError(
                    f"RAW valid 记录的 {key} 必须大于 0")


class RawEvidenceJournal:
    """单 lease 写入的 RAW JSONL；每次 append 都 flush＋fsync。"""

    def __init__(
        self,
        path: str,
        binding: RawEvidenceBinding,
        *,
        create: bool = True,
    ) -> None:
        self.path = os.path.abspath(path)
        self.binding = binding
        self._lock = threading.RLock()
        self._records: list[dict[str, object]] = []
        self.truncated_tail_repaired = False
        if os.path.exists(self.path):
            self._load_existing()
        elif create:
            self._create()
        else:
            raise core.PreflightError(f"RAW 工作证据不存在：{self.path}")

    @property
    def records(self) -> tuple[dict[str, object], ...]:
        with self._lock:
            return tuple(dict(record) for record in self._records)

    def latest_by_entry(self) -> dict[int, dict[str, object]]:
        with self._lock:
            result: dict[int, dict[str, object]] = {}
            for record in self._records:
                result[int(record["entry_id"])] = dict(record)
            return result

    def matches_terminal(
        self,
        entry_id: int,
        *,
        size_bytes: int,
        modified_at_utc: str,
    ) -> bool:
        record = self.latest_by_entry().get(int(entry_id))
        return bool(
            record is not None
            and int(record["size_bytes"]) == int(size_bytes)
            and str(record["modified_at_utc"]) == str(modified_at_utc)
            and str(record["status"]) in RAW_RESULT_STATUSES
        )

    def _header(self) -> dict[str, object]:
        return {
            "contract": RAW_WORK_CONTRACT,
            "record": "header",
            "binding": self.binding.as_dict(),
            "binding_sha256": self.binding.fingerprint,
            "created_at_utc": core.now_utc_iso(),
        }

    def _create(self) -> None:
        directory = os.path.dirname(self.path)
        os.makedirs(directory, exist_ok=True)
        descriptor = None
        created = False
        try:
            descriptor = os.open(
                self.path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY
                | getattr(os, "O_BINARY", 0),
            )
            created = True
            payload = (_canonical_json(self._header()) + "\n").encode("utf-8")
            _write_all(descriptor, payload)
            os.fsync(descriptor)
        except FileExistsError:
            self._load_existing()
            return
        except Exception:
            if created:
                try:
                    os.remove(self.path)
                except OSError:
                    pass
            raise
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def _validate_header(self, header: object) -> None:
        if not isinstance(header, dict) \
                or header.get("contract") != RAW_WORK_CONTRACT \
                or header.get("record") != "header":
            raise core.PreflightError("RAW 工作证据头部契约无效")
        if header.get("binding_sha256") != self.binding.fingerprint \
                or header.get("binding") != self.binding.as_dict():
            raise core.PreflightError(
                "RAW 工作证据与 snapshot UUID／冻结配置不一致")

    def _load_existing(self) -> None:
        with open(self.path, "rb") as handle:
            data = handle.read()
        if data.startswith(b"\xef\xbb\xbf"):
            raise core.PreflightError("RAW 工作 JSONL 不允许 UTF-8 BOM")
        if b"\r" in data:
            raise core.PreflightError("RAW 工作 JSONL 只允许 LF 换行")
        last_newline = data.rfind(b"\n")
        if last_newline < 0:
            raise core.PreflightError("RAW 工作 JSONL 缺少完整头部")
        complete = data[:last_newline + 1]
        lines = complete.splitlines()
        if not lines:
            raise core.PreflightError("RAW 工作 JSONL 为空")
        try:
            header = json.loads(lines[0].decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise core.PreflightError("RAW 工作 JSONL 头部无法解析") from exc
        self._validate_header(header)

        records: list[dict[str, object]] = []
        previous_sequence = 0
        for line in lines[1:]:
            if not line:
                raise core.PreflightError("RAW 工作 JSONL 含空记录")
            try:
                record = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise core.PreflightError(
                    "RAW 工作 JSONL 含损坏的完整记录") from exc
            if not isinstance(record, dict):
                raise core.PreflightError("RAW 工作 JSONL 记录不是对象")
            _validate_result_record(record)
            sequence = int(record["sequence"])
            if sequence <= previous_sequence:
                raise core.PreflightError("RAW 工作 JSONL sequence 未严格递增")
            previous_sequence = sequence
            records.append(dict(record))

        if len(complete) != len(data):
            # 已先验证头部归属；只截断该 RAW journal 的末尾半行。
            with open(self.path, "r+b") as handle:
                handle.truncate(len(complete))
                handle.flush()
                os.fsync(handle.fileno())
            self.truncated_tail_repaired = True
        self._records = records

    def append_result(
        self,
        *,
        entry_id: int,
        logical_path: str,
        size_bytes: int,
        modified_at_utc: str,
        outcome: dbraw.RawDecodeOutcome,
    ) -> dict[str, object]:
        if outcome.outcome not in ("completed", "timeout", "crashed"):
            raise ValueError("暂停／停止的 RAW worker 不能写入终态证据")
        status = str(outcome.status or "error")
        if status not in RAW_RESULT_STATUSES:
            status = "error"
        with self._lock:
            record: dict[str, object] = {
                "record": "result",
                "sequence": (
                    int(self._records[-1]["sequence"]) + 1
                    if self._records else 1),
                "entry_id": int(entry_id),
                "size_bytes": int(size_bytes),
                "modified_at_utc": str(modified_at_utc),
                "status": status,
                "code": outcome.code,
                "detail": None,
                "path": None,
                "rawpy_version": outcome.rawpy_version,
                "libraw_version": outcome.libraw_version,
                "elapsed_seconds": round(float(outcome.elapsed_seconds), 6),
                "threshold_seconds": float(outcome.threshold_seconds),
                "threshold_count": int(outcome.threshold_count),
                "worker_exitcode": outcome.worker_exitcode,
                "worker_reaped": bool(outcome.worker_reaped),
                "recorded_at_utc": core.now_utc_iso(),
            }
            if status in RAW_ISSUE_STATUSES:
                record["path"] = str(logical_path)
                record["detail"] = (
                    str(outcome.detail)[:2048]
                    if outcome.detail is not None else None)
            if status == "valid":
                record.update({
                    "width": int(outcome.width or 0),
                    "height": int(outcome.height or 0),
                    "channels": int(outcome.channels or 0),
                    "pixel_count": int(outcome.pixel_count or 0),
                    "decoded_bytes": int(outcome.decoded_bytes or 0),
                })
            _validate_result_record(record)
            payload = (_canonical_json(record) + "\n").encode("utf-8")
            descriptor = os.open(
                self.path,
                os.O_APPEND | os.O_WRONLY | getattr(os, "O_BINARY", 0),
            )
            try:
                _write_all(descriptor, payload)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            self._records.append(record)
            return dict(record)


def build_raw_report(
    journal: RawEvidenceJournal,
    selected_entry_ids: Iterable[int],
    *,
    raw_candidate_total: int,
    snapshot_filename: str | None = None,
    database_identity: Mapping[str, object] | None = None,
) -> dict[str, object]:
    selected = tuple(dict.fromkeys(int(value) for value in selected_entry_ids))
    if any(value <= 0 for value in selected):
        raise ValueError("RAW 选中 entry_id 必须为正整数")
    if int(raw_candidate_total) < len(selected):
        raise ValueError("RAW 候选总数不能小于选中数")
    selected_set = set(selected)
    latest = journal.latest_by_entry()
    unexpected = sorted(set(latest) - selected_set)
    if unexpected:
        raise core.PreflightError(
            f"RAW 工作证据含当前选择外 entry_id：{unexpected[:10]}")
    counts = {status: 0 for status in RAW_RESULT_STATUSES}
    problems: list[dict[str, object]] = []
    decoded_pixels = 0
    decoded_bytes = 0
    for entry_id in selected:
        record = latest.get(entry_id)
        if record is None:
            continue
        status = str(record["status"])
        counts[status] += 1
        if status == "valid":
            decoded_pixels += int(record["pixel_count"])
            decoded_bytes += int(record["decoded_bytes"])
        elif status in RAW_ISSUE_STATUSES:
            problems.append({
                "entry_id": entry_id,
                "path": record["path"],
                "status": status,
                "code": record.get("code"),
                "detail": record.get("detail"),
                "size_bytes": int(record["size_bytes"]),
                "modified_at_utc": record["modified_at_utc"],
                "rawpy_version": record.get("rawpy_version"),
                "libraw_version": record.get("libraw_version"),
                "worker_exitcode": record.get("worker_exitcode"),
                "worker_reaped": bool(record.get("worker_reaped")),
            })
    problems.sort(key=lambda row: (
        str(row["path"]).casefold(), str(row["status"])))
    processed = sum(counts.values())
    state = "executed" if processed == len(selected) else "incomplete"
    conclusion = (
        "incomplete" if state == "incomplete"
        else "issues_found" if problems else "passed")
    return {
        "contract": RAW_REPORT_CONTRACT,
        "generated_at_utc": core.now_utc_iso(),
        "snapshot": {
            "filename": snapshot_filename,
            "snapshot_uuid": journal.binding.snapshot_uuid,
            "database_identity": dict(database_identity or {}),
        },
        "binding": journal.binding.as_dict(),
        "binding_sha256": journal.binding.fingerprint,
        "state": state,
        "conclusion": conclusion,
        "selection": {
            "format_mode": journal.binding.format_mode,
            "format_sample_percent": (
                journal.binding.format_sample_percent),
            "raw_candidate_total": int(raw_candidate_total),
            "raw_selected_total": len(selected),
            "processed": processed,
            "not_processed": len(selected) - processed,
            "coverage_note": (
                "RAW 范围继承本次格式校验选择；sample 不代表全部 RAW。"
            ),
        },
        "counts": counts,
        "decode_summary": {
            "decoded_pixels": decoded_pixels,
            "decoded_bytes": decoded_bytes,
        },
        "problems": problems,
        "privacy": {
            "valid_paths_retained": False,
            "unsupported_paths_retained": False,
            "problem_paths_retained": True,
        },
    }


def validate_raw_report(report: Mapping[str, object]) -> None:
    if report.get("contract") != RAW_REPORT_CONTRACT:
        raise core.PreflightError("RAW 最终 JSON 契约无效")
    if report.get("state") not in ("executed", "incomplete"):
        raise core.PreflightError("RAW 最终 JSON 状态无效")
    counts = report.get("counts")
    problems = report.get("problems")
    if not isinstance(counts, dict) or not isinstance(problems, list):
        raise core.PreflightError("RAW 最终 JSON 计数或问题不是正确类型")
    for status in RAW_RESULT_STATUSES:
        try:
            value = int(counts.get(status, 0))
        except (TypeError, ValueError) as exc:
            raise core.PreflightError("RAW 最终 JSON 计数无效") from exc
        if value < 0:
            raise core.PreflightError("RAW 最终 JSON 计数不能为负")
    if len(problems) != sum(
            int(counts.get(status, 0)) for status in RAW_ISSUE_STATUSES):
        raise core.PreflightError("RAW 最终 JSON 问题数与计数不一致")
    for problem in problems:
        if not isinstance(problem, dict) \
                or problem.get("status") not in RAW_ISSUE_STATUSES \
                or not problem.get("path"):
            raise core.PreflightError("RAW 最终 JSON 问题记录无效")
    serialized = _canonical_json(report)
    for forbidden in ('"status":"unsupported","path"',):
        if forbidden in serialized:
            raise core.PreflightError("RAW unsupported 不得包含路径")


def _markdown_cell(value: object) -> str:
    return str(value if value is not None else "").replace("|", "\\|") \
        .replace("\r", " ").replace("\n", " ")


def render_raw_issue_section(
    report: Mapping[str, object] | None,
) -> str:
    lines = ["## RAW 深度校验问题", ""]
    if report is None:
        lines.extend([
            "NULL（本次未执行、旧库未记录或不适用）",
            "",
        ])
        return "\n".join(lines)
    validate_raw_report(report)
    selection = report["selection"]
    problems = report["problems"]
    lines.extend([
        f"- 状态：{report['state']}",
        f"- 范围：{selection['format_mode']}；RAW 候选 "
        f"{selection['raw_candidate_total']}；选中 "
        f"{selection['raw_selected_total']}；已处理 {selection['processed']}",
        f"- unsupported：{report['counts']['unsupported']}（仅计数，不列路径）",
        "",
    ])
    if not problems:
        if report["state"] == "executed":
            lines.extend(["0（已执行，未发现 RAW 深度校验问题）", ""])
        else:
            lines.extend(["NULL（执行未完成，不能宣称无问题）", ""])
        return "\n".join(lines)
    lines.extend([
        "| 状态 | 路径 | 代码 | 说明 |",
        "|---|---|---|---|",
    ])
    for row in problems:
        lines.append(
            f"| {_markdown_cell(row['status'])} | "
            f"`{_markdown_cell(row['path'])}` | "
            f"{_markdown_cell(row.get('code'))} | "
            f"{_markdown_cell(row.get('detail'))} |"
        )
    lines.append("")
    return "\n".join(lines)


def publish_raw_report(
    report: Mapping[str, object],
    snapshot_path: str,
    *,
    output_path: str | None = None,
) -> str:
    """以 UTF-8 无 BOM／LF、staging、校验和 no-clobber 发布伴随 JSON。"""
    validate_raw_report(report)
    target = os.path.abspath(output_path or raw_report_path(snapshot_path))
    directory = os.path.dirname(target)
    os.makedirs(directory, exist_ok=True)
    if os.path.exists(target):
        raise core.PreflightError(f"RAW 伴随报告已存在且不会覆盖：{target}")
    name = os.path.basename(target)
    staging = os.path.join(
        directory, f".{name}.{uuid.uuid4().hex}.partial")
    payload = (
        json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    descriptor = None
    created = False
    try:
        descriptor = os.open(
            staging,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY
            | getattr(os, "O_BINARY", 0),
        )
        created = True
        _write_all(descriptor, payload)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        with open(staging, encoding="utf-8", newline="") as handle:
            verified = json.load(handle)
        validate_raw_report(verified)
        if b"\r" in payload or payload.startswith(b"\xef\xbb\xbf"):
            raise core.PreflightError("RAW 伴随 JSON 编码／换行不符合契约")
        try:
            if os.name == "nt":
                os.rename(staging, target)
            else:
                os.link(staging, target)
                os.unlink(staging)
        except OSError as exc:
            if os.path.exists(target):
                raise core.PreflightError(
                    f"RAW 伴随报告发布冲突且不会覆盖：{target}") from exc
            raise
        created = False
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if created and os.path.exists(staging):
            try:
                os.remove(staging)
            except OSError:
                pass
    return target
