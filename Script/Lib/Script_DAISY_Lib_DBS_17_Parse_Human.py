"""DAISY 数据库解析的自包含 HTML 与流式 XLSX 人读 writer。"""
from __future__ import annotations

from dataclasses import dataclass
import html
import json
import os
import shutil
import tempfile
import zipfile

import Script_DAISY_Lib_DBS_01_Core as core
import Script_DAISY_Lib_DBS_05_Reader as dbreader
import Script_DAISY_Lib_DBS_07_Parse as dbparse


HTML_NAME = "Report.html"
XLSX_NAME = "Report_Excel.xlsx"
DEFAULT_PREVIEW_ROWS = 200
DEFAULT_HTML_CELL_CHARS = 2_000
DEFAULT_XLSX_MAX_ROWS = 1_048_576
DEFAULT_XLSX_MAX_CELL_CHARS = 32_767
_CSP_NONCE = "daisy-report-v1"


_HEADER_NAMES = {
    **getattr(dbparse, "_EXCEL_HEADER_NAMES", {}),
    "item": "项目",
    "section": "板块",
    "section_id": "板块 ID",
    "title": "标题",
    "label": "显示名",
    "value_type": "值类型",
    "logical_path": "逻辑路径",
    "parent_rel_path": "父目录相对路径",
    "record_type": "记录类型",
    "record_key": "记录键",
    "entry_path": "文件逻辑路径",
    "execution": "执行状态",
    "issue_files": "受影响文件数",
    "issue_records": "问题记录数",
    "unsupported_files": "不支持文件数",
    "low_confidence_records": "低置信度记录数",
    "information_json": "统计信息 JSON",
    "details_json": "问题明细 JSON",
    "payload": "原始载荷",
    "provider": "提供器",
    "provider_version": "提供器版本",
    "profile_version": "配置版本",
    "payload_sha256": "载荷 SHA-256",
    "data_json": "运行数据 JSON",
    "topic": "主题",
    "state": "能力状态",
    "path_key": "路径键",
    "old_path": "旧逻辑路径",
    "new_path": "新逻辑路径",
    "group_hash": "内容组 SHA-256",
    "metadata_changed": "元数据是否变化",
}


@dataclass(frozen=True)
class HumanArtifact:
    format_id: str
    relative_path: str
    row_count: int


@dataclass
class _SheetPart:
    path: str
    name: str
    module_id: str
    data_rows: int


def _display_text(
    value: object,
    *,
    max_chars: int | None = None,
) -> tuple[str, bool]:
    if isinstance(value, (dict, list, tuple)):
        text = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    elif value is None:
        text = ""
    else:
        text = str(value)
    cleaned = []
    for character in text:
        codepoint = ord(character)
        if (
            codepoint in (9, 10, 13)
            or 0x20 <= codepoint <= 0xD7FF
            or 0xE000 <= codepoint <= 0xFFFD
            or 0x10000 <= codepoint <= 0x10FFFF
        ):
            cleaned.append(character)
        else:
            cleaned.append(f"\\u{codepoint:04X}")
    text = "".join(cleaned)
    if max_chars is None or len(text) <= max_chars:
        return text, False
    marker = "…[显示已截断]"
    if max_chars <= len(marker):
        return text[:max_chars], True
    return text[:max_chars - len(marker)] + marker, True


def _header_label(field: str) -> str:
    return str(_HEADER_NAMES.get(field, "字段"))


def _excel_header(field: str) -> str:
    return f"{_header_label(field)}\n({field})"


def _sheet_name(
    title: str,
    part: int,
    used_names: set[str],
) -> str:
    base = str(title)
    for character in "[]:*?/\\":
        base = base.replace(character, "_")
    suffix = "" if part == 1 else f"_{part}"
    base = (base or "数据")[:31 - len(suffix)] + suffix
    candidate = base
    duplicate = 2
    while candidate.casefold() in used_names:
        extra = f"_{duplicate}"
        candidate = base[:31 - len(extra)] + extra
        duplicate += 1
    used_names.add(candidate.casefold())
    return candidate


def _write_sheet_start(
    handle,
    fields: tuple[str, ...],
) -> None:
    handle.write(
        (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<worksheet xmlns="http://schemas.openxmlformats.org/'
            'spreadsheetml/2006/main">\n'
            '<sheetViews><sheetView workbookViewId="0">'
            '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" '
            'state="frozen"/></sheetView></sheetViews>\n'
            '<sheetFormatPr defaultRowHeight="18"/>\n'
        ).encode("utf-8")
    )
    columns = "".join(
        f'<col min="{index}" max="{index}" '
        f'width="{dbparse._excel_column_width(field)}" customWidth="1"/>'
        for index, field in enumerate(fields, 1)
    ) or '<col min="1" max="1" width="22" customWidth="1"/>'
    handle.write(f"<cols>{columns}</cols>\n<sheetData>\n".encode("utf-8"))
    dbparse._write_xlsx_row(
        handle,
        [_excel_header(field) for field in fields],
        1,
        header=True,
    )


def _write_sheet_end(
    handle,
    fields: tuple[str, ...],
    row_number: int,
) -> None:
    last_column = dbparse._xlsx_column_name(max(1, len(fields)))
    handle.write(
        (
            "</sheetData>\n"
            f'<autoFilter ref="A1:{last_column}{max(1, row_number)}"/>\n'
            "</worksheet>\n"
        ).encode("utf-8")
    )


class _PreviewSink:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.rows: list[dict[str, object]] = []

    def write(self, row: dict[str, object]) -> None:
        if len(self.rows) < self.limit:
            self.rows.append(dict(row))


class _XlsxModuleSink:
    def __init__(
        self,
        builder: "_XlsxBuilder",
        module_id: str,
        title: str,
        fields: tuple[str, ...],
        *,
        display_limit: int | None,
    ) -> None:
        self.builder = builder
        self.module_id = module_id
        self.title = title
        self.fields = fields
        self.display_limit = display_limit
        self.seen_rows = 0
        self.written_rows = 0
        self.part_number = 0
        self.handle = None
        self.current_part: _SheetPart | None = None
        self.current_data_rows = 0

    def __enter__(self) -> "_XlsxModuleSink":
        return self

    def _start_part(self) -> None:
        self._finish_part()
        self.part_number += 1
        name = _sheet_name(
            self.title, self.part_number, self.builder.used_names)
        path = os.path.join(
            self.builder.parts_dir,
            f"sheet_{len(self.builder.parts) + 1:05d}.xml",
        )
        self.handle = open(path, "xb")
        _write_sheet_start(self.handle, self.fields)
        self.current_part = _SheetPart(
            path=path,
            name=name,
            module_id=self.module_id,
            data_rows=0,
        )
        self.current_data_rows = 0

    def _finish_part(self) -> None:
        if self.handle is None or self.current_part is None:
            return
        _write_sheet_end(
            self.handle,
            self.fields,
            self.current_data_rows + 1,
        )
        self.handle.close()
        self.current_part.data_rows = self.current_data_rows
        self.builder.parts.append(self.current_part)
        self.handle = None
        self.current_part = None

    def write(self, row: dict[str, object]) -> None:
        self.seen_rows += 1
        if (
            self.display_limit is not None
            and self.written_rows >= self.display_limit
        ):
            return
        if (
            self.handle is None
            or self.current_data_rows >= self.builder.max_rows - 1
        ):
            self._start_part()
        values = []
        for field in self.fields:
            value, truncated = _display_text(
                row.get(field),
                max_chars=self.builder.max_cell_chars,
            )
            self.builder.truncated_cells += int(truncated)
            values.append(value)
        self.current_data_rows += 1
        self.written_rows += 1
        dbparse._write_xlsx_row(
            self.handle,
            values,
            self.current_data_rows + 1,
        )

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        try:
            self._finish_part()
        finally:
            if self.handle is not None:
                self.handle.close()
                self.handle = None
        self.builder.module_rows[self.module_id] = self.written_rows


class _XlsxBuilder:
    def __init__(
        self,
        staging: str,
        *,
        max_rows: int,
        max_cell_chars: int,
    ) -> None:
        if max_rows < 2 or max_rows > DEFAULT_XLSX_MAX_ROWS:
            raise ValueError("XLSX 行上限必须位于 2～1,048,576")
        if max_cell_chars < 1 or max_cell_chars > DEFAULT_XLSX_MAX_CELL_CHARS:
            raise ValueError("XLSX 单元格上限必须位于 1～32,767")
        self.staging = staging
        self.max_rows = max_rows
        self.max_cell_chars = max_cell_chars
        self.parts_dir = tempfile.mkdtemp(
            prefix=".xlsx-parts-", dir=staging)
        self.parts: list[_SheetPart] = []
        self.used_names: set[str] = set()
        self.module_rows: dict[str, int] = {}
        self.truncated_cells = 0

    def open_module(
        self,
        module_id: str,
        title: str,
        fields: tuple[str, ...],
        *,
        display_limit: int | None,
    ) -> _XlsxModuleSink:
        return _XlsxModuleSink(
            self,
            module_id,
            title,
            fields,
            display_limit=display_limit,
        )

    def _overview_part(
        self,
        rows: list[tuple[str, object]],
    ) -> _SheetPart:
        path = os.path.join(self.parts_dir, "sheet_overview.xml")
        fields = ("item", "value")
        with open(path, "xb") as handle:
            _write_sheet_start(handle, fields)
            for row_number, (key, value) in enumerate(rows, 2):
                display, _truncated = _display_text(
                    value,
                    max_chars=self.max_cell_chars,
                )
                dbparse._write_xlsx_row(
                    handle,
                    [str(key), display],
                    row_number,
                )
            _write_sheet_end(handle, fields, len(rows) + 1)
        return _SheetPart(
            path=path,
            name=_sheet_name("报告概览", 1, self.used_names),
            module_id="__overview__",
            data_rows=len(rows),
        )

    def finalize(self, overview_rows: list[tuple[str, object]]) -> str:
        overview = self._overview_part(overview_rows)
        ordered_parts = [overview, *self.parts]
        workbook_path = os.path.join(self.staging, XLSX_NAME)
        with zipfile.ZipFile(
            workbook_path,
            "x",
            compression=zipfile.ZIP_DEFLATED,
        ) as archive:
            for index, part in enumerate(ordered_parts, 1):
                archive.write(
                    part.path,
                    f"xl/worksheets/sheet{index}.xml",
                )
            dbparse._write_xlsx_package_parts(
                archive,
                [part.name for part in ordered_parts],
            )
        self.cleanup()
        return workbook_path

    def cleanup(self) -> None:
        if os.path.isdir(self.parts_dir):
            if os.path.islink(self.parts_dir):
                raise RuntimeError("拒绝清理被替换为链接的 XLSX parts")
            shutil.rmtree(self.parts_dir)


def _descriptor_uuid(descriptor: dbreader.DatabaseDescriptor) -> object:
    key = "snapshot_uuid" if descriptor.database_type == "snapshot" \
        else "diff_uuid"
    return descriptor.identity.get(key)


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


def _overview_rows(
    descriptor: dbreader.DatabaseDescriptor,
    generated_at_utc: str,
    module_records: list[dict[str, object]],
    *,
    truncated_cells: int,
) -> list[tuple[str, object]]:
    return [
        ("报告用途", "供人工阅读、筛选和追溯 DAISY 数据库结果"),
        ("工具", core.report_metadata("数据库解析")["tool_name"]),
        ("工具版本", core.SCANNER_VERSION),
        ("报告时间（UTC）", generated_at_utc),
        ("输入数据库", os.path.basename(descriptor.path)),
        ("数据库类型", descriptor.database_type),
        ("schema", descriptor.schema_version),
        ("数据库 UUID", _descriptor_uuid(descriptor)),
        ("生成器版本", descriptor.source_version),
        ("封存状态", descriptor.lifecycle),
        ("兼容模式", _compatibility_mode(descriptor)),
        ("SQLite 完整性", descriptor.sqlite_integrity),
        ("所选模块", "、".join(
            str(record["title"]) for record in module_records)),
        ("模块数量", len(module_records)),
        ("XLSX 单元格截断数", truncated_cells),
        (
            "完整值说明",
            "XLSX／HTML 是人读投影；被截断或未嵌入的完整值请使用所选 CSV／JSONL，"
            "未选择技术格式时以 SQLite 数据库为准。",
        ),
        (
            "公式安全",
            "所有数据库文本均以字符串单元格写入，不包含公式元素。",
        ),
    ]


def _html_header_cell(field: str) -> str:
    return (
        "<th><span>"
        + html.escape(_header_label(field))
        + "</span><code>"
        + html.escape(field)
        + "</code></th>"
    )


def _html_cell(value: object, max_chars: int) -> tuple[str, bool]:
    text, truncated = _display_text(value, max_chars=max_chars)
    return html.escape(text), truncated


def _report_summary(
    descriptor: dbreader.DatabaseDescriptor,
    previews: dict[str, _PreviewSink],
) -> tuple[str, str]:
    if descriptor.database_type == "diff":
        overview = previews.get("overview")
        counts = {
            str(row.get("key")): int(row.get("value") or 0)
            for row in (overview.rows if overview is not None else ())
            if row.get("section") == "file_status"
        }
        if not counts:
            return (
                "已导出 Diff 身份，当前预览未含变化计数",
                "完整变化请查看所选文件变化模块或技术导出。",
            )
        changed = sum(
            count for status, count in counts.items()
            if status != "unchanged"
        )
        details = "、".join(
            f"{status}={count}" for status, count in sorted(counts.items()))
        return f"Diff 非 unchanged 记录 {changed} 项", details
    issue_preview = previews.get("issues")
    if issue_preview is None:
        return (
            "未选择问题摘要",
            "本报告不能据此断言数据库没有问题；可重新解析并勾选“问题摘要”。",
        )
    issue_records = sum(
        int(row.get("issue_records") or 0)
        for row in issue_preview.rows
        if row.get("issue_records") is not None
    )
    null_sections = sum(
        1 for row in issue_preview.rows
        if str(row.get("execution")) not in ("executed", "applicable")
        or row.get("issue_records") is None
    )
    if issue_records:
        return (
            f"问题板块累计记录 {issue_records}",
            "同一文件可能出现在多个证据板块；请以各板块受影响文件数和明细为准。",
        )
    if null_sections:
        return (
            "已执行板块未记录问题，但存在 NULL 板块",
            "NULL 表示未执行、旧库未记录或不适用，不等于检查通过。",
        )
    return "已执行问题板块记录为 0", "没有把未执行能力伪装为 0。"


def _write_html(
    path: str,
    *,
    descriptor: dbreader.DatabaseDescriptor,
    generated_at_utc: str,
    module_records: list[dict[str, object]],
    previews: dict[str, _PreviewSink],
    fields_by_module: dict[str, tuple[str, ...]],
    max_cell_chars: int,
) -> int:
    conclusion, conclusion_detail = _report_summary(descriptor, previews)
    navigation = []
    sections = []
    displayed_rows = 0
    for record in module_records:
        module_id = str(record["module_id"])
        if module_id not in previews:
            continue
        title = str(record["title"])
        fields = fields_by_module[module_id]
        preview = previews[module_id].rows
        total_rows = int(record["rows"])
        displayed_rows += len(preview)
        navigation.append(
            f'<a href="#{html.escape(module_id)}">{html.escape(title)}</a>')
        table_id = f"table-{module_id}"
        header = "".join(_html_header_cell(field) for field in fields)
        body_rows = []
        truncated_cells = 0
        for row in preview:
            cells = []
            for field in fields:
                cell, truncated = _html_cell(
                    row.get(field), max_cell_chars)
                truncated_cells += int(truncated)
                cells.append(f"<td>{cell}</td>")
            body_rows.append("<tr>" + "".join(cells) + "</tr>")
        compatibility = record.get("compatibility_notes") or []
        compatibility_text = (
            "；".join(
                f"{item.get('title') or item.get('id')}="
                f"{item.get('state')}（{item.get('reason') or '未记录原因'}）"
                for item in compatibility
            )
            if compatibility else "无额外能力降级"
        )
        open_attribute = " open" if module_id in ("overview", "issues") \
            else ""
        sections.append(
            f'<section id="{html.escape(module_id)}">'
            f'<details{open_attribute}><summary><span>{html.escape(title)}</span>'
            f'<strong>{total_rows} 行</strong></summary>'
            f'<p class="module-note">预览 {len(preview)}／{total_rows} 行；'
            f'人读截断单元格 {truncated_cells}。'
            f'{html.escape(compatibility_text)}</p>'
            f'<label class="filter-label">筛选当前预览 '
            f'<input class="table-filter" data-target="{table_id}" '
            f'type="search" placeholder="输入文本"></label>'
            f'<div class="table-wrap"><table id="{table_id}">'
            f'<thead><tr>{header}</tr></thead><tbody>'
            + "".join(body_rows)
            + "</tbody></table></div></details></section>"
        )
    tool = core.report_metadata("数据库解析")
    warnings = "；".join(descriptor.warnings) or "无 Reader 警告"
    document = f'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'nonce-{_CSP_NONCE}'; script-src 'nonce-{_CSP_NONCE}'; img-src data:; base-uri 'none'; form-action 'none'; object-src 'none'">
<title>DAISY 数据库解析报告</title>
<style nonce="{_CSP_NONCE}">
:root{{--bg:#f5f7f6;--card:#fff;--ink:#17211e;--muted:#5e6d67;--line:#d9e1de;--accent:#347a68;--warn:#9a5a18}}
*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.55 "Microsoft YaHei UI","Noto Sans SC",sans-serif}}
header{{padding:28px clamp(18px,4vw,54px);background:#173e34;color:#fff}}header h1{{margin:0 0 6px;font-size:clamp(24px,4vw,38px)}}header p{{margin:0;color:#dbe9e4}}
.layout{{display:grid;grid-template-columns:220px minmax(0,1fr);gap:22px;max-width:1500px;margin:0 auto;padding:22px}}nav{{position:sticky;top:18px;align-self:start;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px}}nav a{{display:block;color:var(--accent);padding:6px 8px;text-decoration:none;border-radius:6px}}nav a:hover{{background:#eaf3f0}}main{{min-width:0}}
.hero,.identity,section{{background:var(--card);border:1px solid var(--line);border-radius:12px;margin-bottom:16px;padding:18px}}.hero h2{{margin:0;color:var(--accent)}}.hero p{{margin:6px 0 0;color:var(--muted)}}
.facts{{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:10px}}.fact{{padding:10px;border-radius:8px;background:#f0f5f3}}.fact span{{display:block;color:var(--muted);font-size:12px}}.fact strong{{display:block;overflow-wrap:anywhere}}
details>summary{{display:flex;justify-content:space-between;gap:12px;cursor:pointer;font-size:18px;color:var(--accent)}}.module-note{{color:var(--muted)}}.filter-label{{display:flex;align-items:center;gap:10px;margin:10px 0}}input{{width:min(360px,100%);padding:8px 10px;border:1px solid var(--line);border-radius:7px;font:inherit}}
.table-wrap{{overflow:auto;max-height:620px;border:1px solid var(--line);border-radius:8px}}table{{border-collapse:collapse;min-width:100%;background:#fff}}th,td{{padding:8px 10px;border-bottom:1px solid var(--line);border-right:1px solid #edf1ef;text-align:left;vertical-align:top;white-space:pre-wrap;overflow-wrap:anywhere}}th{{position:sticky;top:0;background:#e5efec;z-index:1}}th span,th code{{display:block}}th code{{font-size:11px;color:var(--muted)}}td{{max-width:520px}}
.notice{{color:var(--warn)}}footer{{padding:20px;color:var(--muted);text-align:center}}
@media(max-width:780px){{.layout{{display:block;padding:12px}}nav{{position:static;margin-bottom:14px}}nav a{{display:inline-block}}header{{padding:22px 16px}}.hero,.identity,section{{padding:14px}}details>summary{{font-size:16px}}}}
@media print{{body{{background:#fff}}nav,.filter-label{{display:none}}.layout{{display:block;max-width:none;padding:0}}header{{background:#fff;color:#000;padding:0 0 16px}}header p{{color:#444}}section,.hero,.identity{{break-inside:avoid;border-color:#aaa}}.table-wrap{{max-height:none;overflow:visible}}th{{position:static}}}}
</style>
</head>
<body>
<header><h1>DAISY 数据库解析报告</h1><p>{html.escape(os.path.basename(descriptor.path))}</p></header>
<div class="layout">
<nav><strong>报告目录</strong><a href="#summary">结论</a><a href="#identity">身份</a>{''.join(navigation)}</nav>
<main>
<section class="hero" id="summary"><h2>{html.escape(conclusion)}</h2><p>{html.escape(conclusion_detail)}</p></section>
<section class="identity" id="identity"><h2>数据库身份</h2><div class="facts">
<div class="fact"><span>类型／schema</span><strong>{html.escape(descriptor.database_type)}／{descriptor.schema_version}</strong></div>
<div class="fact"><span>UUID</span><strong>{html.escape(str(_descriptor_uuid(descriptor)))}</strong></div>
<div class="fact"><span>生成器</span><strong>{html.escape(str(descriptor.source_version))}</strong></div>
<div class="fact"><span>兼容模式</span><strong>{html.escape(_compatibility_mode(descriptor))}</strong></div>
<div class="fact"><span>SQLite 完整性</span><strong>{html.escape(str(descriptor.sqlite_integrity))}</strong></div>
<div class="fact"><span>报告时间（UTC）</span><strong>{html.escape(generated_at_utc)}</strong></div>
<div class="fact"><span>报告工具</span><strong>{html.escape(tool['tool_name'])} {html.escape(tool['tool_version'])}</strong></div>
</div><p class="notice">Reader 提示：{html.escape(warnings)}</p><p>路径仅作为可复制文本显示，不生成 file 链接；本报告不会访问数据库中记录的源文件。HTML 只嵌入有限预览，完整事实以 SQLite 和所选技术导出为准。</p></section>
{''.join(sections)}
</main></div>
<footer>DAISY · {html.escape(tool['tool_author'])}</footer>
<script nonce="{_CSP_NONCE}">
document.querySelectorAll('.table-filter').forEach(function(input){{
  input.addEventListener('input',function(){{
    var table=document.getElementById(input.dataset.target);
    var query=input.value.toLocaleLowerCase();
    table.querySelectorAll('tbody tr').forEach(function(row){{
      row.hidden=!row.textContent.toLocaleLowerCase().includes(query);
    }});
  }});
}});
</script>
</body></html>
'''
    with open(path, "x", encoding="utf-8", newline="\n") as handle:
        handle.write(document)
    return displayed_rows


class HumanReportContext:
    """在模块单次流式遍历中收集 HTML 预览并写 XLSX sheet parts。"""

    def __init__(
        self,
        staging: str,
        descriptor: dbreader.DatabaseDescriptor,
        plan: dbparse.ParseExportPlan,
        generated_at_utc: str,
        *,
        preview_rows: int = DEFAULT_PREVIEW_ROWS,
        html_cell_chars: int = DEFAULT_HTML_CELL_CHARS,
        xlsx_max_rows: int = DEFAULT_XLSX_MAX_ROWS,
        xlsx_max_cell_chars: int = DEFAULT_XLSX_MAX_CELL_CHARS,
    ) -> None:
        if preview_rows <= 0:
            raise ValueError("HTML 预览行数必须大于 0")
        if html_cell_chars <= 0:
            raise ValueError("HTML 单元格显示上限必须大于 0")
        self.staging = staging
        self.descriptor = descriptor
        self.plan = plan
        self.generated_at_utc = generated_at_utc
        self.preview_rows = preview_rows
        self.html_cell_chars = html_cell_chars
        self.previews: dict[str, _PreviewSink] = {}
        self.fields_by_module: dict[str, tuple[str, ...]] = {}
        self.xlsx = (
            _XlsxBuilder(
                staging,
                max_rows=xlsx_max_rows,
                max_cell_chars=xlsx_max_cell_chars,
            )
            if "xlsx" in plan.format_ids else None
        )

    def open_module_sinks(
        self,
        module_id: str,
        title: str,
        fields: tuple[str, ...],
        formats: tuple[str, ...],
        stack,
        *,
        module_preview_limit: int,
    ) -> list[object]:
        sinks: list[object] = []
        self.fields_by_module[module_id] = fields
        if "html" in formats:
            preview = _PreviewSink(min(
                self.preview_rows, module_preview_limit))
            self.previews[module_id] = preview
            sinks.append(preview)
        if "xlsx" in formats:
            if self.xlsx is None:
                raise RuntimeError("XLSX context 未初始化")
            display_limit = (
                module_preview_limit
                if module_id == "raw_payloads" else None
            )
            sinks.append(stack.enter_context(self.xlsx.open_module(
                module_id,
                title,
                fields,
                display_limit=display_limit,
            )))
        return sinks

    def finalize(
        self,
        module_records: list[dict[str, object]],
    ) -> tuple[HumanArtifact, ...]:
        artifacts = []
        for record in module_records:
            module_id = str(record["module_id"])
            display_rows = {}
            if module_id in self.previews:
                display_rows["html"] = len(self.previews[module_id].rows)
            if self.xlsx is not None and module_id in self.xlsx.module_rows:
                display_rows["xlsx"] = self.xlsx.module_rows[module_id]
            record["display_rows"] = display_rows
        overview_rows = _overview_rows(
            self.descriptor,
            self.generated_at_utc,
            module_records,
            truncated_cells=(
                self.xlsx.truncated_cells if self.xlsx is not None else 0),
        )
        if self.xlsx is not None:
            path = self.xlsx.finalize(overview_rows)
            artifacts.append(HumanArtifact(
                "xlsx",
                os.path.basename(path),
                sum(self.xlsx.module_rows.values()),
            ))
        if "html" in self.plan.format_ids:
            path = os.path.join(self.staging, HTML_NAME)
            rows = _write_html(
                path,
                descriptor=self.descriptor,
                generated_at_utc=self.generated_at_utc,
                module_records=module_records,
                previews=self.previews,
                fields_by_module=self.fields_by_module,
                max_cell_chars=self.html_cell_chars,
            )
            artifacts.append(HumanArtifact(
                "html", os.path.basename(path), rows))
        return tuple(artifacts)

    def cleanup(self) -> None:
        if self.xlsx is not None:
            self.xlsx.cleanup()
