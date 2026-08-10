"""DAISY 快照核验功能的共用只读输入模型与业务服务。

本模块合并哈希／格式核验已有的快照准入、根目录映射、文件状态／哈希核验、
格式判据和报告落盘；旧 Module 只保留 CLI 与兼容函数名。

本次职责调整不改变哈希抽样、格式判据、报告字段、退出码或输出文件命名。
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from typing import Iterable
import zipfile

import Script_DAISY_Lib_Snapshot_Core as core
import Script_DAISY_Lib_Metadata as meta
import Script_DAISY_Lib_File_Hash as dbh
import Script_DAISY_Lib_Database_Reader as dbreader
import Script_DAISY_Lib_Tool_Runtime as toolruntime


_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_OOXML_EXTS = {"docx", "xlsx", "pptx"}
_MEDIA_KINDS = {
    "photo_raw", "photo_jpeg", "image_gif", "photo_working",
    "video_mp4", "video_crm", "audio",
}
_FFPROBE_KINDS = {"image_gif", "video_mp4", "video_crm", "audio"}
FORMAT_VALIDATION_PROFILE = "daisy-format-v1"
_FORMAT_STATUS_LABELS = {
    "valid": "有效",
    "invalid": "校验失败",
    "unsupported": "不支持",
    "missing": "缺失",
    "timeout": "超时",
    "error": "异常",
}

# 损坏模式表（v1，判据来自截断 JPG/MP4/CR3 与坏头 JPG 的探针输出）。
_CORRUPT_RE = re.compile(
    r"(format error|truncated|error reading|corrupt|unknown file type"
    r"|file is empty|processing .+ after unknown .*header"
    r"|bad atom|invalid atom|not a valid)", re.I)


def _verification_mode_text(value: object) -> str:
    mode = str(value or "")
    if mode == "full":
        return "全量"
    if mode.startswith("sample_") and mode.endswith("pct"):
        return f"抽样 {mode[len('sample_'):-len('pct')]}%"
    return mode or "未知"


@dataclass
class VerificationSnapshot:
    """一次核验所使用的封存快照、当前 root 映射和只读连接。"""

    path: str
    connection: sqlite3.Connection
    descriptor: dbreader.DatabaseDescriptor
    snapshot_uuid: str
    hash_coverage: str
    root_labels: tuple[str, ...]
    current_roots: dict[int, str]
    labels_by_root_id: dict[int, str]

    @property
    def filename(self) -> str:
        return os.path.basename(self.path)

    def physical_path(self, root_id: int, relative_path: str) -> str:
        return os.path.join(self.current_roots[root_id], relative_path)

    def logical_path(self, root_id: int, relative_path: str) -> str:
        return self.labels_by_root_id[root_id] + "\\" + relative_path

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> VerificationSnapshot:
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()


def _validate_snapshot_filename(path: str, force: bool) -> str:
    normalized = os.path.abspath(path)
    if not os.path.isfile(normalized):
        raise core.PreflightError(f"快照不存在：{normalized}")
    recorded = core.filename_sha256_high32(normalized)
    if recorded is not None:
        if recorded != core.sha256_file(normalized)[:8].upper():
            raise core.PreflightError("快照文件名指纹不符")
    elif not force:
        raise core.PreflightError(
            f"快照文件名缺少指纹（可用 --force 明确允许继续）：{normalized}")
    return normalized


def _root_specs(
    root_map: dict[str, str] | None,
    root_specs: list[str] | None,
) -> list[str]:
    if root_specs is not None:
        return list(root_specs)
    return [
        f"{label}={path}"
        for label, path in (root_map or {}).items()
    ]


def open_verification_snapshot(
    snapshot_path: str,
    *,
    root_map: dict[str, str] | None = None,
    root_specs: list[str] | None = None,
    force: bool = False,
    required_capabilities: Iterable[str] = ("files",),
) -> VerificationSnapshot:
    """按既有哈希／格式核验规则打开快照并解析当前 root 映射。"""
    normalized = _validate_snapshot_filename(snapshot_path, force)
    connection, descriptor = dbreader.open_database(
        normalized, expected_type="snapshot")
    try:
        dbreader.require_capabilities(
            descriptor, *tuple(required_capabilities))
        row = connection.execute(
            "SELECT snapshot_uuid,hash_coverage"
            " FROM snapshot_info WHERE id=1").fetchone()
        if row is None:
            raise core.PreflightError("快照数据库缺少 snapshot_info id=1")
        snapshot_uuid, hash_coverage = row
        root_rows = list(connection.execute(
            "SELECT root_id,root_label,root_path"
            " FROM roots ORDER BY root_id"))
        labels = [str(root[1]) for root in root_rows]
        current_by_label = core.resolve_current_root_specs(
            labels, _root_specs(root_map, root_specs))
        current_roots: dict[int, str] = {}
        labels_by_root_id: dict[int, str] = {}
        for root_id, label, _recorded_path in root_rows:
            current = current_by_label[label]
            if not os.path.isdir(current):
                raise core.PreflightError(
                    f"root「{label}」当前路径不存在：{current}"
                    f"（用 --root \"{label}=当前路径\" 指定）")
            current_roots[int(root_id)] = current
            labels_by_root_id[int(root_id)] = str(label)
        return VerificationSnapshot(
            path=normalized,
            connection=connection,
            descriptor=descriptor,
            snapshot_uuid=str(snapshot_uuid),
            hash_coverage=str(hash_coverage),
            root_labels=tuple(labels),
            current_roots=current_roots,
            labels_by_root_id=labels_by_root_id,
        )
    except Exception:
        connection.close()
        raise


# === 哈希核验 ===


def patrol_hash(
    snapshot_path: str,
    root_map: dict | None = None,
    sample_percent: float = 1.0,
    full: bool = False,
    powershell: str | None = None,
    force: bool = False,
    on_progress=None,
    root_specs: list[str] | None = None,
) -> dict:
    """执行哈希核验巡检并返回既有报告 dict。"""
    verification = open_verification_snapshot(
        snapshot_path,
        root_map=root_map,
        root_specs=root_specs,
        force=force,
        required_capabilities=("files", "hashes"),
    )
    con = verification.connection
    try:
        uuid_ = verification.snapshot_uuid
        coverage = verification.hash_coverage
        roots = verification.current_roots
        label_by_rid = verification.labels_by_root_id
        labels = list(verification.root_labels)

        # ① 全量 stat 核对（存在性＋size/mtime）。
        stat_missing, stat_changed = [], []
        flagged: set[int] = set()
        entries = con.execute(
            "SELECT entry_id, root_id, rel_path, size_bytes, modified_at_utc"
            " FROM entries WHERE is_placeholder = 0"
            " ORDER BY root_id, rel_path"
        ).fetchall()
        for eid, rid, rel, size, mtime in entries:
            path = os.path.join(roots[rid], rel)
            try:
                stat_result = os.stat(
                    core.to_extended_path(path), follow_symlinks=False)
            except OSError:
                stat_missing.append({
                    "path": label_by_rid[rid] + "\\" + rel,
                    "rel_path": rel,
                    "root_id": rid,
                })
                flagged.add(eid)
                continue
            mtime_now = core.ns_to_utc_iso(stat_result.st_mtime_ns)
            if stat_result.st_size != size or mtime_now != mtime:
                stat_changed.append({
                    "path": label_by_rid[rid] + "\\" + rel,
                    "rel_path": rel,
                    "root_id": rid,
                    "size_recorded": size,
                    "size_now": stat_result.st_size,
                    "mtime_recorded": mtime,
                    "mtime_now": mtime_now,
                })
                flagged.add(eid)

        # ② 哈希核对（独立实现）：valid 哈希且 stat 未变者。
        hrows = [
            row
            for row in con.execute(
                "SELECT h.entry_id, e.size_bytes, e.root_id, e.rel_path,"
                " h.hash_hex FROM hashes h"
                " JOIN entries e ON e.entry_id = h.entry_id"
                " WHERE h.algorithm='sha256' AND h.status='valid'"
                " AND e.is_placeholder = 0"
                " ORDER BY e.root_id, e.rel_path"
            )
            if row[0] not in flagged
        ]
        if full:
            chosen = hrows
        else:
            ids = {
                eid
                for eid, _size in dbh.pick_sample(
                    [(row[0], row[1]) for row in hrows],
                    sample_percent,
                    100,
                    seed=uuid_ + ":patrol",
                )
            }
            chosen = [row for row in hrows if row[0] in ids]
        hash_mismatched, hash_tool_error = [], []
        used_tools: dict[str, dict] = {}
        if chosen:
            ps_path, ps_version = dbh.discover_powershell(powershell)
            ps_info = core.resolved_tool_info(
                "powershell",
                ps_path,
                explicit=bool(powershell),
                version=ps_version,
            )
            used_tools["powershell"] = ps_info
            core.emit_gui_event(
                "tools_detected", tools={"powershell": ps_info})
            paths = [
                os.path.join(roots[rid], rel)
                for _eid, _size, rid, rel, _recorded in chosen
            ]
            got = dbh.get_filehash_batch(
                paths, powershell=ps_path, on_progress=on_progress)
            for (_eid, _size, rid, rel, recorded), independent in zip(
                chosen, got,
            ):
                if independent is None:
                    hash_tool_error.append({
                        "path": label_by_rid[rid] + "\\" + rel,
                        "rel_path": rel,
                        "root_id": rid,
                    })
                elif independent != recorded:
                    hash_mismatched.append({
                        "path": label_by_rid[rid] + "\\" + rel,
                        "rel_path": rel,
                        "root_id": rid,
                        "recorded": recorded,
                        "independent": independent,
                    })
        return {
            "snapshot": os.path.basename(snapshot_path),
            "root_labels": labels,
            "snapshot_uuid": uuid_,
            "hash_coverage": coverage,
            "mode": "full" if full else f"sample_{sample_percent}pct",
            "entries_total": len(entries),
            "stat_checked": len(entries),
            "stat_missing": stat_missing,
            "stat_changed": stat_changed,
            "hash_eligible": len(hrows),
            "hash_checked": len(chosen),
            "hash_mismatched": hash_mismatched,
            "hash_tool_error": hash_tool_error,
            "tools": used_tools,
            "checked_at_utc": core.now_utc_iso(),
            "ok": not (
                stat_missing
                or stat_changed
                or hash_mismatched
                or hash_tool_error
            ),
        }
    finally:
        verification.close()


def write_hash_report(
    report: dict,
    requested_path: str | None = None,
) -> tuple[str, str | None]:
    """按哈希核验既有命名、字段和 Markdown 内容写出报告。"""
    report["report_metadata"] = core.report_metadata("哈希核验")
    if requested_path:
        report_path = os.path.abspath(requested_path)
        os.makedirs(os.path.dirname(report_path) or ".", exist_ok=True)
    else:
        os.makedirs("Output/Reports", exist_ok=True)
        report_stem = core.snapshot_working_name(
            core.snapshot_name(report["root_labels"], "Check_Hash"))
        report_path = os.path.abspath(os.path.join(
            "Output/Reports", report_stem + ".json"))
    with open(report_path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)

    issue_report = None
    if not report["ok"]:
        issue_report = os.path.splitext(report_path)[0] + "_Issues.md"
        categories = (
            ("缺失", report["stat_missing"]),
            ("文件属性变化", report["stat_changed"]),
            ("哈希不一致", report["hash_mismatched"]),
            ("哈希工具故障", report["hash_tool_error"]),
        )
        lines = [
            "# DAISY 哈希核验问题报告",
            "",
            *core.report_markdown_lines("哈希核验"),
            "",
            f"- 快照：`{report['snapshot']}`",
            f"- 快照 UUID：`{report['snapshot_uuid']}`",
            f"- 核对时间：`{report['checked_at_utc']}`",
            f"- 模式：{_verification_mode_text(report['mode'])}",
            "",
            "## 汇总",
            "",
            "| 项目 | 数量 |",
            "| --- | --- |",
        ]
        lines.extend(
            f"| {name} | {len(rows)} |" for name, rows in categories)
        for name, rows in categories:
            if not rows:
                continue
            lines.extend(["", f"## {name}", ""])
            for row in rows[:100]:
                lines.append(
                    f"- `{core.markdown_cell(row.get('path'))}`")
            if len(rows) > 100:
                lines.append(
                    f"- …仅列出前 100/{len(rows)} 条，完整详情见 JSON 报告。")
        with open(
            issue_report, "w", encoding="utf-8", newline="\n",
        ) as handle:
            handle.write("\n".join(lines) + "\n")
    return report_path, issue_report


# === 格式校验 ===


def validate_zip(path: str) -> tuple[str, str | None]:
    """双层校验 ZIP 中央目录结构和全部成员 CRC。"""
    try:
        with open(path, "rb") as handle:
            if handle.read(8).startswith(_OLE_MAGIC):
                return "unsupported", "OLE 容器（加密 Office/旧格式），非 zip"
        with zipfile.ZipFile(path) as archive:
            bad = archive.testzip()
            if bad is not None:
                return "invalid", f"成员 CRC 不符或成员头损坏：{bad}"
        return "valid", None
    except RuntimeError as exc:
        text = str(exc)
        if "password" in text.lower() or "encrypted" in text.lower():
            return "unsupported", f"加密成员无法 CRC 校验：{text}"
        return "invalid", text
    except Exception as exc:
        return "invalid", f"{type(exc).__name__}: {exc}"


def validate_pdf(path: str) -> tuple[str, str | None]:
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as handle:
            head = handle.read(1024)
            handle.seek(max(0, size - 2048))
            tail = handle.read()
    except OSError as exc:
        return "invalid", str(exc)
    if b"%PDF-" not in head:
        return "invalid", "缺少 %PDF 头"
    if b"%%EOF" not in tail:
        return "invalid", "缺少 %%EOF 尾"
    if b"startxref" not in tail:
        return "invalid", "缺少 startxref（交叉引用结构）"
    return "valid", None


def validate_legacy_office(
    path: str,
    sevenzip: str,
    *,
    sevenzip_validator=None,
) -> tuple[str, str | None]:
    """确认旧 Office 为 OLE 后交给可注入的 7-Zip 校验器。"""
    try:
        with open(path, "rb") as handle:
            magic = handle.read(len(_OLE_MAGIC))
    except OSError as exc:
        return "invalid", str(exc)
    if magic != _OLE_MAGIC:
        return "unsupported", "不是 OLE 复合文档；可能是 RTF 或扩展名不符"
    validator = sevenzip_validator or validate_sevenzip
    return validator(path, sevenzip)


def validate_sevenzip(path: str, sevenzip: str) -> tuple[str, str | None]:
    try:
        result = toolruntime.run_bounded_tool(
            [sevenzip, "t", "-p", "-y", "-sccUTF-8", path],
            tool="sevenzip",
            operation="format_validate",
            timeout_seconds=3600,
        )
    except toolruntime.ToolProcessTimeout:
        return "invalid", "7z t 超时"
    if toolruntime.is_native_crash_returncode(result.returncode):
        raise toolruntime.failure_from_process(
            result,
            tool="sevenzip",
            operation="format_validate",
            failure_kind="native_crash",
            recovered=True,
        )
    if result.returncode in (7, 8, 255):
        raise toolruntime.failure_from_process(
            result,
            tool="sevenzip",
            operation="format_validate",
            failure_kind=f"tool_exit_{result.returncode}",
            recovered=True,
            message=f"7-Zip 工具故障（退出码 {result.returncode}）",
        )
    if result.stdout_truncated or result.stderr_truncated:
        raise toolruntime.failure_from_process(
            result,
            tool="sevenzip",
            operation="format_validate",
            failure_kind="output_limit",
            recovered=True,
            message="7-Zip 校验输出超过受控上限",
        )
    if result.returncode == 0:
        return "valid", None
    text = (result.stderr + result.stdout).decode("utf-8", "replace")
    if "Wrong password" in text or "Enter password" in text:
        return "unsupported", "加密压缩包无法完整性测试"
    tail = " | ".join(
        line for line in text.splitlines() if line.strip())[-300:]
    return "invalid", f"7z t 退出码 {result.returncode}：{tail}"


def parse_et_text(text: str) -> list[tuple[str, str]]:
    """解析 exiftool -s 文本输出为（标签，值）列表。"""
    out = []
    for line in text.splitlines():
        tag, separator, value = line.partition(":")
        if separator:
            out.append((tag.strip(), value.strip()))
    return out


def classify_et_findings(
    findings: list[tuple[str, str]],
) -> list[str]:
    """判据 v1：Error 一律算；非 minor 警告命中损坏模式才算。"""
    bad = []
    for tag, value in findings:
        if tag == "Error":
            bad.append(f"Error: {value}")
        elif (
            tag == "Warning"
            and not value.startswith("[minor]")
            and _CORRUPT_RE.search(value)
        ):
            bad.append(f"Warning: {value}")
    return bad


def validate_media(
    path: str,
    kind: str,
    worker,
    ffprobe: str | None,
) -> tuple[str, str | None]:
    output = worker.execute([
        "-validate", "-a", "-s", "-Warning", "-Error",
        "-charset", "filename=utf8", path,
    ])
    bad = classify_et_findings(
        parse_et_text(output.decode("utf-8", "replace")))
    if kind in _FFPROBE_KINDS:
        if not ffprobe:
            raise core.PreflightError(f"{kind} 格式校验需要 ffprobe")
        try:
            document = meta.ffprobe_full(
                ffprobe, path, timeout=600, operation="format_validate")
        except toolruntime.ToolProcessTimeout:
            bad.append("ffprobe: 超时")
            return "invalid", "；".join(bad)
        except meta.MetadataSourceError as exc:
            bad.append(f"ffprobe: {exc}")
            return "invalid", "；".join(bad)
        streams = document.get("streams")
        if not isinstance(streams, list) or not all(
                isinstance(stream, dict) for stream in streams):
            raise toolruntime.ToolRuntimeFailure(
                toolruntime.ToolFaultEvidence(
                    tool="ffprobe",
                    operation="format_validate",
                    failure_kind="protocol_invalid",
                    message="ffprobe 返回的 streams 结构无效",
                ),
                recovered=True,
            )
        if not streams:
            bad.append("ffprobe: 未发现媒体流")
        elif kind == "audio" and os.path.getsize(path) <= 44:
            audio_streams = [
                stream
                for stream in streams
                if stream.get("codec_type") == "audio"
            ]
            format_duration = meta.first_float(
                (document.get("format", {}) or {}).get("duration"))
            stream_durations = [
                meta.first_float(stream.get("duration"))
                for stream in audio_streams
            ]
            if (
                not audio_streams
                or not (format_duration and format_duration > 0)
                and not any(
                    value and value > 0 for value in stream_durations)
            ):
                bad.append("ffprobe: 音频容器只有头部且没有可确认的音频样本")
    return ("invalid", "；".join(bad)) if bad else ("valid", None)


def pick_format_validator(ext: str, kind: str) -> str:
    if ext == "zip" or ext in _OOXML_EXTS:
        return "zip"
    if ext == "pdf":
        return "pdf"
    if ext == "doc":
        return "ole"
    if ext == "gif":
        return "gif"
    if ext == "jfif":
        return "media"
    if kind in _MEDIA_KINDS:
        return "media"
    if kind == "archive":
        return "7z"
    return "none"


@dataclass(frozen=True)
class FormatValidatorSpec:
    """一次文件格式校验使用的稳定判据与工具身份。"""

    validator: str
    tool_name: str
    tool_version: str


class FormatValidationSession:
    """共享文件级格式判据；外部工具仅在对应格式首次出现时启动。"""

    def __init__(self, tools: dict[str, object]) -> None:
        self._tools = dict(tools)
        self._worker = None
        self.last_tool_failure: toolruntime.ToolRuntimeFailure | None = None

    def _tool(self, name: str) -> dict[str, str]:
        value = self._tools.get(name)
        if not isinstance(value, dict):
            raise core.PreflightError(
                f"格式校验缺少 {name} 的路径／版本能力")
        path = value.get("path")
        version = value.get("version")
        if not isinstance(path, str) or not path \
                or not isinstance(version, str) or not version:
            raise core.PreflightError(
                f"格式校验的 {name} 路径／版本无效")
        return {"path": path, "version": version}

    def describe(self, extension: str, media_kind: str) \
            -> FormatValidatorSpec:
        validator = pick_format_validator(extension, media_kind)
        if validator == "zip":
            version = ".".join(map(str, sys.version_info[:3]))
            return FormatValidatorSpec(
                validator, "python-zipfile", version)
        if validator in ("pdf", "none"):
            return FormatValidatorSpec(
                validator, "daisy-format", FORMAT_VALIDATION_PROFILE)
        if validator in ("ole", "7z"):
            sevenzip = self._tool("sevenzip")
            return FormatValidatorSpec(
                validator, "7-Zip", sevenzip["version"])
        exiftool = self._tool("exiftool")
        effective_kind = (
            "image_gif" if validator == "gif" else media_kind)
        if effective_kind in _FFPROBE_KINDS:
            ffprobe = self._tool("ffprobe")
            return FormatValidatorSpec(
                validator,
                "exiftool+ffprobe",
                f"exiftool {exiftool['version']}; "
                f"ffprobe {ffprobe['version']}",
            )
        return FormatValidatorSpec(
            validator, "exiftool", exiftool["version"])

    def validate(
        self,
        path: str,
        media_kind: str,
        spec: FormatValidatorSpec,
    ) -> tuple[str, str | None]:
        """返回 schema 4 格式状态，不把 unsupported 提升为错误。"""
        self.last_tool_failure = None
        validator = spec.validator
        extended_path = core.to_extended_path(path)
        try:
            if validator == "none":
                return "unsupported", None
            if validator == "zip":
                return validate_zip(extended_path)
            if validator == "pdf":
                return validate_pdf(extended_path)
            if validator == "ole":
                result = validate_legacy_office(
                    extended_path, self._tool("sevenzip")["path"])
            elif validator == "7z":
                result = validate_sevenzip(
                    path, self._tool("sevenzip")["path"])
            else:
                if self._worker is None:
                    self._worker = meta.ExifToolWorker(
                        self._tool("exiftool")["path"])
                effective_kind = (
                    "image_gif" if validator == "gif" else media_kind)
                ffprobe_path = (
                    self._tool("ffprobe")["path"]
                    if effective_kind in _FFPROBE_KINDS else None
                )
                result = validate_media(
                    path, effective_kind, self._worker, ffprobe_path)
        except TimeoutError:
            return "timeout", "exiftool -validate 超时"
        except toolruntime.ToolRuntimeFailure as exc:
            self.last_tool_failure = exc
            return "error", str(exc)
        except Exception as exc:
            return "error", f"{type(exc).__name__}: {exc}"
        status, detail = result
        if status == "invalid" and detail and (
                "ffprobe: 超时" in detail or "7z t 超时" in detail):
            return "timeout", detail
        if status not in ("valid", "invalid", "unsupported"):
            return "error", (
                detail or f"校验器返回未知状态：{status!r}")
        return status, detail

    def close(self) -> None:
        if self._worker is not None:
            self._worker.close()
            self._worker = None

    def __enter__(self) -> FormatValidationSession:
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()


def validate_format_snapshot(
    snapshot_path: str,
    root_map: dict | None = None,
    sample_percent: float = 100.0,
    report_dir: str | None = None,
    exiftool: str | None = None,
    ffprobe: str | None = None,
    sevenzip: str | None = None,
    force: bool = False,
    on_progress=None,
    root_specs: list[str] | None = None,
) -> dict:
    """执行格式校验并写出既有报告集合。"""
    verification = open_verification_snapshot(
        snapshot_path,
        root_map=root_map,
        root_specs=root_specs,
        force=force,
        required_capabilities=("files",),
    )
    snapshot_path = verification.path
    con = verification.connection
    try:
        uuid_ = verification.snapshot_uuid
        roots = verification.current_roots
        label_by_rid = verification.labels_by_root_id
        labels = list(verification.root_labels)
        entries = con.execute(
            "SELECT entry_id, root_id, rel_path, extension, media_kind,"
            " size_bytes, modified_at_utc"
            " FROM entries WHERE is_placeholder=0"
            " ORDER BY root_id, rel_path"
        ).fetchall()
    finally:
        verification.close()

    if sample_percent < 100.0:
        ids = {
            eid
            for eid, _size in dbh.pick_sample(
                [(entry[0], entry[5]) for entry in entries],
                sample_percent,
                100,
                seed=uuid_ + ":validate",
            )
        }
        entries = [entry for entry in entries if entry[0] in ids]

    worker = None
    rows = []
    counts: dict = {}
    used_tools: dict[str, dict] = {}
    started = time.monotonic()

    def resolve_once(name: str, supplied: str | None) -> str:
        if name in used_tools:
            return used_tools[name]["path"]
        path = supplied or core.discover_tool(name, None)
        info = core.resolved_tool_info(
            name, path, explicit=bool(supplied))
        used_tools[name] = info
        core.emit_gui_event("tools_detected", tools={name: info})
        return info["path"]

    try:
        for index, entry in enumerate(entries, 1):
            eid, rid, rel, ext, kind, size, mtime = entry
            path = os.path.join(roots[rid], rel)
            validator = pick_format_validator(ext, kind)
            extended_path = core.to_extended_path(path)
            try:
                stat_result = os.stat(
                    extended_path, follow_symlinks=False)
                stat_match = 1 if (
                    stat_result.st_size == size
                    and core.ns_to_utc_iso(stat_result.st_mtime_ns) == mtime
                ) else 0
            except OSError:
                rows.append({
                    "path": label_by_rid[rid] + "\\" + rel,
                    "rel_path": rel,
                    "root_id": rid,
                    "media_kind": kind,
                    "validator": validator,
                    "status": "missing",
                    "detail": None,
                    "stat_match": 0,
                })
                counts["missing"] = counts.get("missing", 0) + 1
                if on_progress and index % 20 == 0:
                    on_progress(index, len(entries))
                continue
            if validator == "none":
                status, detail = "unsupported", None
            elif validator == "zip":
                status, detail = validate_zip(extended_path)
            elif validator == "pdf":
                status, detail = validate_pdf(extended_path)
            elif validator == "ole":
                status, detail = validate_legacy_office(
                    path, resolve_once("sevenzip", sevenzip))
            elif validator == "7z":
                status, detail = validate_sevenzip(
                    path, resolve_once("sevenzip", sevenzip))
            else:
                if worker is None:
                    worker = meta.ExifToolWorker(
                        resolve_once("exiftool", exiftool))
                try:
                    effective_kind = (
                        "image_gif" if validator == "gif" else kind)
                    ffprobe_path = (
                        resolve_once("ffprobe", ffprobe)
                        if effective_kind in _FFPROBE_KINDS
                        else None
                    )
                    status, detail = validate_media(
                        path, effective_kind, worker, ffprobe_path)
                except TimeoutError:
                    status, detail = "invalid", "exiftool -validate 超时"
            rows.append({
                "path": label_by_rid[rid] + "\\" + rel,
                "rel_path": rel,
                "root_id": rid,
                "media_kind": kind,
                "validator": validator,
                "status": status,
                "detail": detail,
                "stat_match": stat_match,
            })
            counts[status] = counts.get(status, 0) + 1
            if on_progress and index % 20 == 0:
                on_progress(index, len(entries))
    finally:
        if worker is not None:
            worker.close()
    if on_progress and entries:
        on_progress(len(entries), len(entries))

    ok = not counts.get("invalid") and not counts.get("missing")
    report = {
        "report_metadata": core.report_metadata("格式校验"),
        "snapshot": os.path.basename(snapshot_path),
        "snapshot_uuid": uuid_,
        "mode": (
            "full"
            if sample_percent >= 100.0
            else f"sample_{sample_percent}pct"
        ),
        "checked": len(rows),
        "counts": counts,
        "ok": ok,
        "elapsed_s": round(time.monotonic() - started, 1),
        "checked_at_utc": core.now_utc_iso(),
        "tools": used_tools,
        "rows": rows,
    }

    rdir = os.path.abspath(report_dir or "Output/Reports")
    os.makedirs(rdir, exist_ok=True)
    report_stem = core.snapshot_working_name(
        core.snapshot_name(labels, "Check_Format"))
    base = os.path.join(rdir, report_stem)
    files = []
    with open(base + ".json", "w", encoding="utf-8", newline="\n") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    files.append(base + ".json")
    with open(base + ".csv", "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow([
            "path", "rel_path", "media_kind", "validator", "status",
            "detail", "stat_match",
        ])
        for row in rows:
            writer.writerow([
                row["path"], row["rel_path"], row["media_kind"],
                row["validator"], row["status"], row["detail"],
                row["stat_match"],
            ])
    files.append(base + ".csv")
    info_path = base + "_Info.csv"
    identity = core.report_metadata("格式校验")
    with open(info_path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["key", "value"])
        writer.writerows(identity.items())
    files.append(info_path)
    status_summary = "，".join(
        f"{_FORMAT_STATUS_LABELS.get(key, key)}：{value:,}"
        for key, value in sorted(counts.items()))
    markdown = [
        "# DAISY 格式校验报告",
        "",
        *core.report_markdown_lines("格式校验"),
        "",
        f"- 快照：`{report['snapshot']}` (UUID: `{uuid_}`)",
        f"- 口径：{_verification_mode_text(report['mode'])}；"
        f"核对 {report['checked']:,} 条；用时 {report['elapsed_s']} 秒",
        f"- 结果：{status_summary}",
        "- 结论：在本次口径内未发现校验失败或缺失文件；"
        "不支持类型不作结论。" if ok else "- 结论：**发现校验失败或缺失文件。**",
        "",
    ]
    problems = [
        row for row in rows if row["status"] in ("invalid", "missing")]
    if problems:
        markdown.append("## 问题清单")
        markdown.append("")
        for row in problems[:50]:
            markdown.append(
                f"- [{_FORMAT_STATUS_LABELS.get(row['status'], row['status'])}] "
                f"`{core.markdown_cell(row['path'])}`"
                + (f"：{row['detail']}" if row["detail"] else ""))
        if len(problems) > 50:
            markdown.append(f"- …共 {len(problems)} 条，详见 CSV 数据表。")
        markdown.append("")
        markdown.append(
            "与哈希结果交叉解读：哈希未变只说明当前字节与基准一致，不能证明"
            "建库时文件可正常解析；哈希变化说明字节已变化，损坏时间和原因仍需"
            "结合历史证据判断。")
        markdown.append("")
    with open(base + ".md", "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(markdown))
    files.append(base + ".md")
    report["files"] = files
    return report
