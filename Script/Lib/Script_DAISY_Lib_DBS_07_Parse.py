r"""Script_DAISY_Lib_DBS_07_Parse：DBS 数据库解析与旧报告 writer。

快照导出（分组清单＋簿记，多 CSV、UTF-8 无 BOM、LF；规范化字段不剔除）：
  Tree.csv / Tree_dirs.csv                 —— 树
  Exif_inventory_photo/video/working/document.csv —— EXIF 组
  GPS_inventory_video.csv                       —— 视频规范化 GPS 点
  Stream_inventory_video/audio.csv         —— ffmpeg 组
  Hash_inventory.csv                       —— 哈希组
  Archive_inventory.csv / _members.csv     —— 压缩包组
  Summary.csv / Errors.csv / Metadata_diagnostics.csv —— 簿记与诊断
  （raw_payloads 为 zlib BLOB，不入 CSV——用 SQL 直接查询快照库）

Diff 数据库导出：
  Diff_summary.md（status×evidence 交叉表、失败子树醒目段、内容/结构双维度
  结论、propagated 单列声明、覆盖声明）＋ Diff_details.csv
  ＋ Diff_dirs.csv / Diff_hash_groups.csv / Diff_subtrees.csv

两种输入都保留完整技术 CSV，并额外生成面向人工阅读的 Report_Excel.xlsx：
中文工作表与字段、冻结表头、筛选、语义列宽；超过 Excel 行上限时自动分表。

用法：
  python .\Script\Script_DAISY_MAIN.py export-report --snapshot .\Output\Snapshots\Scan_x.sqlite
  python .\Script\Script_DAISY_MAIN.py export-report --diff .\Output\Diffs\Diff_x.sqlite
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
import json
import os
import sqlite3
import sys
import tempfile
from typing import Iterable
import zipfile
from xml.sax.saxutils import escape

_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
_LIB_DIR = os.path.join(os.path.dirname(_MODULE_DIR), "Lib")
sys.path.insert(0, _LIB_DIR)
import Script_DAISY_Lib_DBS_01_Core as core
import Script_DAISY_Lib_DBS_05_Reader as dbreader

_LIST_CAP = 50      # 摘要内明细列表上限（超出注明总数，绝不静默截断）
_EXCEL_WORKBOOK_NAME = "Report_Excel.xlsx"
_XLSX_MAX_ROWS = 1_048_576
_XLSX_MAX_CELL_CHARS = 32_767
_EXCEL_SHEET_NAMES = {
    "Report_guide.csv": "阅读说明",
    "Report_info.csv": "报告身份",
    "Tree.csv": "文件清单",
    "Tree_dirs.csv": "目录清单",
    "Exif_inventory_photo.csv": "照片元数据",
    "Exif_inventory_video.csv": "视频元数据",
    "GPS_inventory_video.csv": "视频定位",
    "Exif_inventory_working.csv": "工作文件元数据",
    "Exif_inventory_document.csv": "文档元数据",
    "Stream_inventory_video.csv": "视频流",
    "Stream_inventory_audio.csv": "音频流",
    "Hash_inventory.csv": "哈希清单",
    "Archive_inventory.csv": "压缩包清单",
    "Archive_inventory_members.csv": "压缩包成员",
    "Metadata_diagnostics.csv": "元数据诊断",
    "Errors.csv": "错误明细",
    "Summary.csv": "快照概览",
    "Diff_details.csv": "文件变化",
    "Diff_dirs.csv": "目录变化",
    "Diff_hash_groups.csv": "重复内容变化",
    "Diff_subtrees.csv": "枚举缺口",
}
_EXCEL_HEADER_NAMES = {
    "key": "项目",
    "value": "内容",
    "path": "完整路径",
    "old_path": "旧完整路径",
    "new_path": "新完整路径",
    "root_label": "根标签",
    "old_root_label": "旧根标签",
    "new_root_label": "新根标签",
    "rel_path": "相对路径",
    "old_rel_path": "旧相对路径",
    "new_rel_path": "新相对路径",
    "name": "文件名",
    "extension": "扩展名",
    "media_kind": "文件类型",
    "size_bytes": "大小（字节）",
    "old_size": "旧大小（字节）",
    "new_size": "新大小（字节）",
    "created_at_utc": "创建时间（UTC）",
    "modified_at_utc": "修改时间（UTC）",
    "old_mtime_utc": "旧修改时间（UTC）",
    "new_mtime_utc": "新修改时间（UTC）",
    "observed_at_utc": "观察时间（UTC）",
    "parsed_at_utc": "解析时间（UTC）",
    "started_at_utc": "开始时间（UTC）",
    "finished_at_utc": "完成时间（UTC）",
    "enum_status": "枚举状态",
    "old_enum_status": "旧枚举状态",
    "new_enum_status": "新枚举状态",
    "meta_status": "元数据状态",
    "hash_status": "哈希状态",
    "status": "状态",
    "evidence": "证据等级",
    "reason": "原因",
    "message": "信息",
    "error_message": "错误信息",
    "error_code": "错误码",
    "diagnostic_code": "诊断码",
    "severity": "等级",
    "stage": "阶段",
    "field_name": "字段",
    "raw_value": "原始值",
    "hash_hex": "SHA-256",
    "old_hash_hex": "旧 SHA-256",
    "new_hash_hex": "新 SHA-256",
    "origin": "哈希来源",
    "old_hash_origin": "旧哈希来源",
    "new_hash_origin": "新哈希来源",
    "algorithm": "算法",
    "failure_reason": "失败原因",
    "camera_make": "相机品牌",
    "camera_model": "相机型号",
    "camera_serial": "相机序列号",
    "lens_model": "镜头型号",
    "lens_serial": "镜头序列号",
    "capture_time_raw": "拍摄时间（原始）",
    "capture_time_utc": "拍摄时间（UTC）",
    "capture_time_source": "拍摄时间来源",
    "width": "宽度",
    "height": "高度",
    "duration_seconds": "时长（秒）",
    "bit_rate": "码率",
    "codec_name": "编码格式",
    "stream_index": "流序号",
    "stream_count": "流数量",
    "gps_latitude": "纬度",
    "gps_longitude": "经度",
    "gps_altitude": "海拔",
    "point_index": "定位点序号",
    "timestamp_seconds": "时间点（秒）",
    "archive_format": "压缩格式",
    "member_count": "成员数",
    "member_index": "成员序号",
    "member_path": "成员路径",
    "uncompressed_bytes": "解压后字节数",
    "compressed_bytes": "压缩后字节数",
    "old_count": "旧数量",
    "new_count": "新数量",
    "classification": "分类",
    "side": "侧别",
    "affected_estimate": "预计影响数",
    "tool": "工具",
    "tool_version": "工具版本",
    "parser": "解析器",
    "parser_version": "解析器版本",
}
_EXCEL_VALUE_NAMES = {
    "photo_raw": "RAW 照片",
    "photo_jpeg": "JPEG 照片",
    "image_gif": "GIF 图像",
    "photo_working": "图像工作文件",
    "video_mp4": "普通视频",
    "video_crm": "Cinema RAW Light 视频",
    "audio": "音频",
    "archive": "压缩包",
    "document": "文档",
    "other": "其他文件",
    "done": "完成",
    "error": "错误",
    "timeout": "超时",
    "unstable": "不稳定",
    "skipped": "已跳过",
    "not_applicable": "不适用",
    "valid": "有效",
    "failed": "失败",
    "unchanged": "未变化",
    "added": "新增",
    "deleted": "删除",
    "content_changed": "内容变化",
    "stat_changed_content_same": "属性变化、内容相同",
    "metadata_extraction_changed": "元数据提取变化",
    "moved_or_renamed": "移动或重命名",
    "copied": "复制",
    "hash_missing": "缺少哈希",
    "unknown": "无法判定",
    "independent_computation": "独立计算",
    "propagated_single_computation": "同次计算沿用",
    "heuristic_file_id": "File ID 启发式",
    "stat_only": "仅文件属性",
    "insufficient": "证据不足",
    "computed": "本次计算",
    "reused": "复用",
}


def _write_csv(path: str, header: list, rows: list) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)


def _write_report_guide(
    folder: str, source_kind: str, source_path: str,
) -> str:
    """建立人读入口；完整技术字段仍保留在各 CSV。"""
    name = "Report_guide.csv"
    kind_label = "封存快照" if source_kind == "snapshot" else "Diff 数据库"
    _write_csv(
        os.path.join(folder, name),
        ["key", "value"],
        [
            ("报告用途", "供人工浏览、筛选和追溯 DAISY 结果"),
            ("输入类型", kind_label),
            ("输入数据库", os.path.basename(source_path)),
            ("建议打开", f"{_EXCEL_WORKBOOK_NAME}（中文兼容）"),
            (
                "完整数据",
                "同目录 CSV 保留数据库字段名与完整值，适合脚本和审计",
            ),
            (
                "CSV 编码",
                "UTF-8 无 BOM；Excel 双击可能误判编码，请使用 XLSX",
            ),
            (
                "工作簿说明",
                "中文工作表与字段用于阅读；括号内保留原数据库字段名；"
                "超长单元格按 Excel 上限显示，完整值仍在 CSV",
            ),
        ],
    )
    return name


def _xlsx_column_name(index: int) -> str:
    if index < 1:
        raise ValueError("Excel 列编号必须从 1 开始")
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _xlsx_text(value: object) -> str:
    """清除 XML 1.0 禁止字符并转义；其余 Unicode（含中文）原样保留。"""
    text = "" if value is None else str(value)
    cleaned: list[str] = []
    for character in text:
        codepoint = ord(character)
        cleaned.append(
            character
            if (
                codepoint in (9, 10, 13)
                or 0x20 <= codepoint <= 0xD7FF
                or 0xE000 <= codepoint <= 0xFFFD
                or 0x10000 <= codepoint <= 0x10FFFF
            )
            else "\uFFFD"
        )
    return escape("".join(cleaned))


def _xlsx_attribute(value: object) -> str:
    return escape(
        "" if value is None else str(value),
        {'"': "&quot;", "'": "&apos;"},
    )


def _excel_header(field_name: str) -> str:
    label = _EXCEL_HEADER_NAMES.get(field_name)
    return f"{label}\n({field_name})" if label else field_name


def _excel_row(header: list[str], row: list[str]) -> list[str]:
    translated = []
    for index, value in enumerate(row):
        field_name = header[index] if index < len(header) else ""
        if field_name in {
            "media_kind", "meta_status", "hash_status", "status",
            "evidence", "origin", "old_hash_origin", "new_hash_origin",
        }:
            display_value = _EXCEL_VALUE_NAMES.get(value, value)
        else:
            display_value = value
        display_value = str(display_value)
        if len(display_value) > _XLSX_MAX_CELL_CHARS:
            display_value = display_value[:_XLSX_MAX_CELL_CHARS - 1] + "…"
        translated.append(display_value)
    return translated


def _excel_column_width(field_name: str) -> int:
    lowered = field_name.casefold()
    if "hash_hex" in lowered:
        return 68
    if any(part in lowered for part in (
            "path", "message", "reason", "raw_value", "config_json")):
        return 52
    if lowered.endswith("_utc") or lowered.endswith("_raw"):
        return 25
    if lowered.endswith("_id") or lowered.endswith("_pk"):
        return 14
    return 22


def _write_xlsx_row(
    stream, values: list[str], row_number: int, *, header: bool = False,
) -> None:
    row_options = ' ht="34" customHeight="1"' if header else ""
    stream.write(
        f'<row r="{row_number}"{row_options}>'.encode("utf-8"))
    style = ' s="1"' if header else ""
    for column_number, raw_value in enumerate(values, start=1):
        value = "" if raw_value is None else str(raw_value)
        preserve = (
            ' xml:space="preserve"'
            if value[:1].isspace() or value[-1:].isspace() else ""
        )
        reference = f"{_xlsx_column_name(column_number)}{row_number}"
        stream.write(
            (
                f'<c r="{reference}" t="inlineStr"{style}><is>'
                f'<t{preserve}>{_xlsx_text(value)}</t></is></c>'
            ).encode("utf-8")
        )
    stream.write(b"</row>\n")


def _xlsx_sheet_name(
    csv_name: str, part: int, used_names: set[str],
) -> str:
    base = _EXCEL_SHEET_NAMES.get(
        csv_name, os.path.splitext(os.path.basename(csv_name))[0])
    for character in "[]:*?/\\":
        base = base.replace(character, "_")
    suffix = "" if part == 1 else f"_{part}"
    base = (base or "Sheet")[:31 - len(suffix)] + suffix
    candidate = base
    duplicate = 2
    while candidate.casefold() in used_names:
        extra = f"_{duplicate}"
        candidate = base[:31 - len(extra)] + extra
        duplicate += 1
    used_names.add(candidate.casefold())
    return candidate


def _write_xlsx_csv_sheets(
    archive: zipfile.ZipFile,
    csv_path: str,
    csv_name: str,
    sheets: list[str],
    used_names: set[str],
) -> None:
    """把一个 CSV 流式写成一个或多个工作表，不静默截断 Excel 行上限。"""
    with open(csv_path, encoding="utf-8", newline="") as source:
        reader = csv.reader(source)
        header = next(reader, [])
        display_header = [_excel_header(field) for field in header]
        pending_row: list[str] | None = None
        exhausted = False
        part = 1
        while not exhausted:
            sheet_number = len(sheets) + 1
            sheet_name = _xlsx_sheet_name(
                csv_name, part, used_names)
            info = zipfile.ZipInfo(
                f"xl/worksheets/sheet{sheet_number}.xml")
            info.compress_type = zipfile.ZIP_DEFLATED
            with archive.open(info, "w") as stream:
                stream.write(
                    (
                        '<?xml version="1.0" encoding="UTF-8" '
                        'standalone="yes"?>\n'
                        '<worksheet xmlns="http://schemas.openxmlformats.org/'
                        'spreadsheetml/2006/main">\n'
                        '<sheetViews><sheetView workbookViewId="0">'
                        '<pane ySplit="1" topLeftCell="A2" '
                        'activePane="bottomLeft" state="frozen"/>'
                        '</sheetView></sheetViews>\n'
                        '<sheetFormatPr defaultRowHeight="18"/>\n'
                    ).encode("utf-8")
                )
                column_count = max(1, len(header))
                column_definitions = "".join(
                    f'<col min="{index}" max="{index}" '
                    f'width="{_excel_column_width(field)}" customWidth="1"/>'
                    for index, field in enumerate(header, start=1)
                )
                if not column_definitions:
                    column_definitions = (
                        '<col min="1" max="1" width="22" customWidth="1"/>')
                stream.write(
                    (
                        f'<cols>{column_definitions}</cols>\n<sheetData>\n'
                    ).encode("utf-8")
                )
                row_number = 1
                _write_xlsx_row(
                    stream, display_header, row_number, header=True)
                while row_number < _XLSX_MAX_ROWS:
                    if pending_row is not None:
                        row = pending_row
                        pending_row = None
                    else:
                        try:
                            row = next(reader)
                        except StopIteration:
                            exhausted = True
                            break
                    row_number += 1
                    _write_xlsx_row(
                        stream, _excel_row(header, row), row_number)
                if not exhausted:
                    try:
                        pending_row = next(reader)
                    except StopIteration:
                        exhausted = True
                last_column = _xlsx_column_name(column_count)
                stream.write(
                    (
                        '</sheetData>\n'
                        f'<autoFilter ref="A1:{last_column}{row_number}"/>\n'
                        '</worksheet>\n'
                    ).encode("utf-8")
                )
            sheets.append(sheet_name)
            part += 1


def _write_xlsx_package_parts(
    archive: zipfile.ZipFile, sheets: list[str],
) -> None:
    sheet_overrides = "".join(
        '<Override '
        f'PartName="/xl/worksheets/sheet{index}.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.'
        'spreadsheetml.worksheet+xml"/>'
        for index in range(1, len(sheets) + 1)
    )
    archive.writestr(
        "[Content_Types].xml",
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/'
        'content-types">'
        '<Default Extension="rels" ContentType="application/vnd.'
        'openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/'
        'vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.'
        'openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        f'{sheet_overrides}</Types>\n',
    )
    archive.writestr(
        "_rels/.rels",
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/'
        '2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/'
        'officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/>'
        '</Relationships>\n',
    )
    workbook_sheets = "".join(
        f'<sheet name="{_xlsx_attribute(name)}" sheetId="{index}" '
        f'r:id="rId{index}"/>'
        for index, name in enumerate(sheets, start=1)
    )
    archive.writestr(
        "xl/workbook.xml",
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/'
        '2006/main" xmlns:r="http://schemas.openxmlformats.org/'
        'officeDocument/2006/relationships">'
        f'<sheets>{workbook_sheets}</sheets></workbook>\n',
    )
    worksheet_relationships = "".join(
        f'<Relationship Id="rId{index}" Type="http://schemas.'
        'openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        f'Target="worksheets/sheet{index}.xml"/>'
        for index in range(1, len(sheets) + 1)
    )
    styles_id = len(sheets) + 1
    archive.writestr(
        "xl/_rels/workbook.xml.rels",
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/'
        '2006/relationships">'
        f'{worksheet_relationships}'
        f'<Relationship Id="rId{styles_id}" Type="http://schemas.'
        'openxmlformats.org/officeDocument/2006/relationships/styles" '
        'Target="styles.xml"/>'
        '</Relationships>\n',
    )
    archive.writestr(
        "xl/styles.xml",
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/'
        'spreadsheetml/2006/main">'
        '<fonts count="2">'
        '<font><sz val="10"/><name val="Microsoft YaHei UI"/>'
        '<family val="2"/></font>'
        '<font><b/><sz val="10"/><name val="Microsoft YaHei UI"/>'
        '<family val="2"/><color rgb="FFFFFFFF"/></font>'
        '</fonts>'
        '<fills count="3"><fill><patternFill patternType="none"/></fill>'
        '<fill><patternFill patternType="gray125"/></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="FF347A68"/>'
        '<bgColor indexed="64"/></patternFill></fill></fills>'
        '<borders count="1"><border><left/><right/><top/><bottom/>'
        '<diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" '
        'borderId="0"/></cellStyleXfs>'
        '<cellXfs count="2">'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" '
        'applyFont="1" applyAlignment="1"><alignment vertical="top"/>'
        '</xf>'
        '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" '
        'applyFont="1" applyFill="1" applyAlignment="1">'
        '<alignment vertical="center" wrapText="1"/></xf></cellXfs>'
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" '
        'builtinId="0"/></cellStyles>'
        '</styleSheet>\n',
    )


def _write_excel_workbook(folder: str, files: list[str]) -> str:
    """生成原生 Unicode XLSX，供 Excel 直接打开而不猜测 CSV 编码。"""
    csv_names = [name for name in files if name.casefold().endswith(".csv")]
    workbook_path = os.path.join(folder, _EXCEL_WORKBOOK_NAME)
    descriptor, temporary_path = tempfile.mkstemp(
        prefix=".Report_Excel_", suffix=".tmp", dir=folder)
    os.close(descriptor)
    try:
        with zipfile.ZipFile(
                temporary_path, "w", zipfile.ZIP_DEFLATED) as archive:
            sheets: list[str] = []
            used_names: set[str] = set()
            for csv_name in csv_names:
                _write_xlsx_csv_sheets(
                    archive,
                    os.path.join(folder, csv_name),
                    csv_name,
                    sheets,
                    used_names,
                )
            _write_xlsx_package_parts(archive, sheets)
        os.replace(temporary_path, workbook_path)
    except BaseException:
        try:
            os.remove(temporary_path)
        except OSError:
            pass
        raise
    return _EXCEL_WORKBOOK_NAME


def _write_report_info(folder: str) -> str:
    """写入不破坏业务 CSV 表头的独立报告身份页。"""
    name = "Report_info.csv"
    identity = core.report_metadata("DBS-41 结果报告导出")
    _write_csv(
        os.path.join(folder, name), ["key", "value"],
        list(identity.items()),
    )
    return name


def _dump_query(con: sqlite3.Connection, folder: str, name: str,
                sql: str) -> str:
    cur = con.execute(sql)
    header = [c[0] for c in cur.description]
    _write_csv(os.path.join(folder, name), header, cur.fetchall())
    return name


@dataclass(frozen=True)
class ParsePageSpec:
    """旧报告中的一个确定性表格投影。"""

    filename: str
    query: str


@dataclass(frozen=True)
class ParseModuleSpec:
    """数据库解析模块的稳定注册信息；writer 不直接决定模块能力。"""

    module_id: str
    title: str
    database_type: str
    required_capabilities: tuple[str, ...]
    pages: tuple[ParsePageSpec, ...] = ()
    schema3_fallback: bool = True
    formats: frozenset[str] = frozenset(("html", "xlsx", "csv", "jsonl"))
    privacy_level: str = "content_metadata"
    description: str = ""
    optional_capabilities: tuple[str, ...] = ()
    presets: frozenset[str] = frozenset(("full-audit",))
    preview_limit: int = 200
    projection_version: str = "daisy-parse-module-v1"
    legacy_export: bool = True


@dataclass(frozen=True)
class ParseModuleStatus:
    """一个解析模块在已识别数据库中的可选状态。"""

    spec: ParseModuleSpec
    state: str
    row_count: int | None
    reason: str | None
    queryable: bool
    capabilities: tuple[dict[str, object], ...]
    optional_capabilities: tuple[dict[str, object], ...]

    @property
    def selectable(self) -> bool:
        return self.state == "available" and self.queryable

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.spec.module_id,
            "title": self.spec.title,
            "description": self.spec.description,
            "state": self.state,
            "row_count": self.row_count,
            "reason": self.reason,
            "selectable": self.selectable,
            "queryable": self.queryable,
            "formats": sorted(self.spec.formats),
            "privacy_level": self.spec.privacy_level,
            "presets": sorted(self.spec.presets),
            "preview_limit": self.spec.preview_limit,
            "projection_version": self.spec.projection_version,
            "schema3_fallback": self.spec.schema3_fallback,
            "capabilities": [dict(item) for item in self.capabilities],
            "optional_capabilities": [
                dict(item) for item in self.optional_capabilities
            ],
        }


@dataclass(frozen=True)
class ParseDatabaseInspection:
    """两阶段数据库识别中的一次只读结果。"""

    descriptor: dbreader.DatabaseDescriptor
    file_size_bytes: int
    integrity_checked: bool
    compatibility_mode: str
    modules: tuple[ParseModuleStatus, ...]

    @property
    def module_state_counts(self) -> dict[str, int]:
        counts = {
            state: 0 for state in (
                "available", "empty", "unavailable", "incompatible",
                "invalid",
            )
        }
        for module in self.modules:
            counts[module.state] = counts.get(module.state, 0) + 1
        return counts

    def as_dict(self) -> dict[str, object]:
        return {
            "database": self.descriptor.as_dict(),
            "file_size_bytes": self.file_size_bytes,
            "integrity_checked": self.integrity_checked,
            "compatibility_mode": self.compatibility_mode,
            "module_state_counts": self.module_state_counts,
            "modules": [module.as_dict() for module in self.modules],
        }


@dataclass(frozen=True)
class ParseExportPlan:
    """内容选择与格式选择的正交、可序列化计划。"""

    preset: str
    module_ids: tuple[str, ...]
    format_ids: tuple[str, ...]
    format_modules: dict[str, tuple[str, ...]]
    privacy_notices: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "preset": self.preset,
            "modules": list(self.module_ids),
            "formats": list(self.format_ids),
            "format_modules": {
                format_id: list(module_ids)
                for format_id, module_ids in self.format_modules.items()
            },
            "privacy_notices": list(self.privacy_notices),
        }


PARSE_PRESETS = frozenset(("human-summary", "full-audit", "custom"))
PARSE_FORMATS = ("html", "xlsx", "csv", "jsonl")


class CsvQueryWriter:
    """保持 v1.5.1 CSV 编码、表头和值语义的 writer。"""

    format_id = "csv"

    def __init__(self, connection: sqlite3.Connection, folder: str) -> None:
        self.connection = connection
        self.folder = folder

    def write_page(self, page: ParsePageSpec) -> str:
        return _dump_query(
            self.connection, self.folder, page.filename, page.query)

    def write_rows(
        self, filename: str, header: list, rows: list,
    ) -> str:
        _write_csv(os.path.join(self.folder, filename), header, rows)
        return filename


class LegacyExcelWriter:
    """从旧技术 CSV 生成既有 Report_Excel.xlsx 的 writer。"""

    format_id = "xlsx"

    def __init__(self, folder: str) -> None:
        self.folder = folder

    def write(self, files: list[str]) -> str:
        return _write_excel_workbook(self.folder, files)


def _entry_page(filename: str, table: str) -> ParsePageSpec:
    order = "r.root_label, e.rel_path"
    if table == "video_gps_points":
        order += ", t.timestamp_seconds, t.point_index"
    return ParsePageSpec(
        filename,
        f"SELECT r.root_label || '\\' || e.rel_path AS path,"
        f" r.root_label, e.rel_path, t.* FROM {table} t"
        " JOIN entries e ON e.entry_id = t.entry_id"
        " JOIN roots r ON r.root_id = e.root_id"
        f" ORDER BY {order}",
    )


_SNAPSHOT_MODULES = (
    ParseModuleSpec(
        "overview", "数据概览", "snapshot", ("overview",),
        formats=frozenset(("html", "xlsx", "csv")),
        description="身份、根目录、数量、容量、状态与覆盖率",
        presets=frozenset(("human-summary", "full-audit")),
    ),
    ParseModuleSpec(
        "files", "文件清单", "snapshot", ("files",),
        (ParsePageSpec(
            "Tree.csv",
            "SELECT r.root_label || '\\' || e.rel_path AS path,"
            " r.root_label, e.* FROM entries e"
            " JOIN roots r ON r.root_id = e.root_id"
            " ORDER BY r.root_label, e.rel_path",
        ),),
        description="路径、类型、大小、时间与处理状态",
    ),
    ParseModuleSpec(
        "directories", "目录清单", "snapshot", ("directories",),
        (ParsePageSpec(
            "Tree_dirs.csv",
            "SELECT CASE WHEN d.rel_path = '' THEN r.root_label"
            " ELSE r.root_label || '\\' || d.rel_path END AS path,"
            " r.root_label, d.* FROM dirs d"
            " JOIN roots r ON r.root_id = d.root_id"
            " ORDER BY r.root_label, d.rel_path",
        ),),
        description="目录树、root 和枚举状态",
    ),
    ParseModuleSpec(
        "photo_metadata", "照片信息", "snapshot", ("photo_metadata",),
        (_entry_page("Exif_inventory_photo.csv", "photo_metadata"),),
        description="照片的规范化拍摄与设备字段",
    ),
    ParseModuleSpec(
        "video_metadata", "视频信息", "snapshot", ("video_metadata",),
        (_entry_page("Exif_inventory_video.csv", "video_metadata"),),
        description="视频文件级格式化元数据",
    ),
    ParseModuleSpec(
        "video_gps", "视频定位", "snapshot", ("video_gps",),
        (_entry_page("GPS_inventory_video.csv", "video_gps_points"),),
        description="视频中的规范化定位点",
    ),
    ParseModuleSpec(
        "working_metadata", "工作文件", "snapshot", ("working_metadata",),
        (_entry_page("Exif_inventory_working.csv", "working_metadata"),),
        description="PSD、TIFF、PNG 等工作文件字段",
    ),
    ParseModuleSpec(
        "document_metadata", "文档信息", "snapshot",
        ("document_metadata",),
        (_entry_page("Exif_inventory_document.csv", "document_metadata"),),
        description="PDF／Office 文档格式化字段",
    ),
    ParseModuleSpec(
        "media_streams", "媒体轨道", "snapshot", ("media_streams",),
        (
            _entry_page("Stream_inventory_video.csv", "video_streams"),
            _entry_page("Stream_inventory_audio.csv", "audio_streams"),
        ),
        description="视频与音频流的编码和时长信息",
    ),
    ParseModuleSpec(
        "hashes", "逐文件哈希", "snapshot", ("hashes",),
        (_entry_page("Hash_inventory.csv", "hashes"),),
        description="逐文件 SHA-256、来源与读取状态",
    ),
    ParseModuleSpec(
        "archives", "压缩归档", "snapshot", ("archives",),
        (
            _entry_page("Archive_inventory.csv", "archive_metadata"),
            _entry_page("Archive_inventory_members.csv", "archive_members"),
        ),
        description="压缩包及其成员摘要",
    ),
    ParseModuleSpec(
        "diagnostics", "诊断证据", "snapshot", ("diagnostics",),
        (_entry_page(
            "Metadata_diagnostics.csv", "metadata_diagnostics"),),
        description="错误与元数据诊断的原始证据",
    ),
    ParseModuleSpec(
        "issues", "问题摘要", "snapshot", ("issues",),
        (ParsePageSpec(
            "Errors.csv",
            "SELECT er.error_pk, er.stage, er.error_code, er.message,"
            " er.occurred_at_utc,"
            " CASE WHEN e.rel_path IS NULL THEN NULL"
            " ELSE r.root_label || '\\' || e.rel_path END AS path,"
            " r.root_label, e.rel_path, d.rel_path AS dir_rel_path"
            " FROM errors er"
            " LEFT JOIN entries e ON e.entry_id = er.entry_id"
            " LEFT JOIN roots r ON r.root_id = e.root_id"
            " LEFT JOIN dirs d ON d.dir_id = er.dir_id"
            " ORDER BY er.error_pk",
        ),),
        description="枚举、哈希、元数据、格式与性能问题",
        optional_capabilities=(
            "format_checks", "read_performance", "entry_attempts",
        ),
        presets=frozenset(("human-summary", "full-audit")),
    ),
    ParseModuleSpec(
        "raw_payloads", "原始数据", "snapshot", ("raw_payloads",),
        schema3_fallback=True,
        formats=frozenset(("html", "xlsx", "jsonl")),
        privacy_level="sensitive_raw",
        description="ExifTool／ffprobe canonical JSON 原始载荷",
        legacy_export=False,
    ),
    ParseModuleSpec(
        "run_history", "运行历史", "snapshot", ("run_history",),
        schema3_fallback=True,
        description="manifest、会话、事件、尝试和工具来源",
        optional_capabilities=(
            "run_sessions", "entry_attempts", "read_performance",
            "format_checks",
        ),
        legacy_export=False,
    ),
)


_DIFF_PATH_SQL = (
    "CASE WHEN {relative} IS NULL THEN NULL WHEN {relative} = ''"
    " THEN {label} ELSE {label} || '\\' || {relative} END"
)
_DIFF_MODULES = (
    ParseModuleSpec(
        "overview", "对比概览", "diff", ("overview",),
        formats=frozenset(("html", "xlsx", "csv")),
        description="双侧身份、覆盖率、root 配对与结论",
        presets=frozenset(("human-summary", "full-audit")),
    ),
    ParseModuleSpec(
        "file_changes", "文件变化", "diff", ("file_changes",),
        (ParsePageSpec(
            "Diff_details.csv",
            "SELECT "
            + _DIFF_PATH_SQL.format(
                label="old_root_label", relative="old_rel_path")
            + " AS old_path, "
            + _DIFF_PATH_SQL.format(
                label="new_root_label", relative="new_rel_path")
            + " AS new_path, * FROM diff_entries ORDER BY status, path_key",
        ),),
        description="增删、变化、移动、复制与证据等级",
    ),
    ParseModuleSpec(
        "directory_changes", "目录变化", "diff", ("directory_changes",),
        (ParsePageSpec(
            "Diff_dirs.csv",
            "SELECT "
            + _DIFF_PATH_SQL.format(
                label="old_root_label", relative="old_rel_path")
            + " AS old_path, "
            + _DIFF_PATH_SQL.format(
                label="new_root_label", relative="new_rel_path")
            + " AS new_path, * FROM diff_dirs ORDER BY path_key",
        ),),
        description="目录增删、状态变化与 unknown",
    ),
    ParseModuleSpec(
        "content_groups", "内容分组", "diff", ("content_groups",),
        (ParsePageSpec(
            "Diff_hash_groups.csv",
            "SELECT * FROM diff_hash_groups ORDER BY group_id",
        ),),
        description="哈希多重集、副本和硬链接变化",
    ),
    ParseModuleSpec(
        "enumeration_gaps", "枚举缺口", "diff", ("enumeration_gaps",),
        (ParsePageSpec(
            "Diff_subtrees.csv",
            "SELECT * FROM diff_subtrees"
            " ORDER BY side, root_label, rel_path",
        ),),
        description="失败子树、影响范围与 unknown 传播",
        presets=frozenset(("human-summary", "full-audit")),
    ),
    ParseModuleSpec(
        "evidence_notes", "证据说明", "diff", ("evidence_notes",),
        formats=frozenset(("html", "xlsx", "csv")),
        description="能力缺失、跨版本降级与证据边界",
        presets=frozenset(("human-summary", "full-audit")),
    ),
)


_PARSE_MODULE_ORDER = {
    "snapshot": (
        "overview", "issues", "files", "directories", "hashes",
        "photo_metadata", "video_metadata", "video_gps",
        "media_streams", "working_metadata", "document_metadata",
        "archives", "raw_payloads", "diagnostics", "run_history",
    ),
    "diff": (
        "overview", "file_changes", "directory_changes",
        "content_groups", "enumeration_gaps", "evidence_notes",
    ),
}

_LEGACY_MODULE_ORDER = {
    "snapshot": (
        "overview", "files", "directories", "photo_metadata",
        "video_metadata", "video_gps", "working_metadata",
        "document_metadata", "media_streams", "hashes", "archives",
        "diagnostics", "issues",
    ),
    "diff": _PARSE_MODULE_ORDER["diff"],
}


def _registered_modules(database_type: str) -> tuple[ParseModuleSpec, ...]:
    if database_type == "snapshot":
        return _SNAPSHOT_MODULES
    if database_type == "diff":
        return _DIFF_MODULES
    raise ValueError(f"未知数据库类型：{database_type}")


def parse_modules(database_type: str) -> tuple[ParseModuleSpec, ...]:
    """返回面向 v1.6.0 产品界面的完整稳定模块目录。"""
    registered = {
        module.module_id: module
        for module in _registered_modules(database_type)
    }
    order = _PARSE_MODULE_ORDER[database_type]
    if set(registered) != set(order):
        raise RuntimeError(f"{database_type} 解析模块目录与稳定顺序不一致")
    return tuple(registered[module_id] for module_id in order)


def parse_module_statuses(
    descriptor: dbreader.DatabaseDescriptor,
) -> tuple[ParseModuleStatus, ...]:
    """把 Reader 能力折叠成卡片状态；不把 empty／NULL 伪装可选。"""
    priority = {
        "available": 0,
        "empty": 1,
        "unavailable": 2,
        "incompatible": 3,
        "invalid": 4,
    }
    result = []
    for spec in parse_modules(descriptor.database_type):
        required = []
        for capability_id in spec.required_capabilities:
            try:
                capability = descriptor.capability(capability_id)
            except KeyError as exc:
                raise RuntimeError(
                    f"解析模块 {spec.module_id} 引用了未登记能力"
                    f" {capability_id}"
                ) from exc
            required.append(capability)
        optional = []
        for capability_id in spec.optional_capabilities:
            try:
                optional.append(descriptor.capability(capability_id))
            except KeyError as exc:
                raise RuntimeError(
                    f"解析模块 {spec.module_id} 引用了未登记可选能力"
                    f" {capability_id}"
                ) from exc
        state = max(required, key=lambda item: priority[item.state]).state
        row_counts = [
            capability.row_count for capability in required
            if capability.row_count is not None
        ]
        row_count = (
            sum(int(value) for value in row_counts)
            if state in ("available", "empty")
            and len(row_counts) == len(required)
            else None
        )
        reasons = [
            f"{capability.capability_id}={capability.state}："
            f"{capability.reason or '未记录原因'}"
            for capability in required
            if capability.state != "available"
        ]
        result.append(ParseModuleStatus(
            spec=spec,
            state=state,
            row_count=row_count,
            reason="；".join(reasons) if reasons else None,
            queryable=all(capability.queryable for capability in required),
            capabilities=tuple(
                capability.as_dict() for capability in required),
            optional_capabilities=tuple(
                capability.as_dict() for capability in optional),
        ))
    return tuple(result)


def _compatibility_mode(
    descriptor: dbreader.DatabaseDescriptor,
) -> str:
    if descriptor.database_type == "snapshot":
        return (
            "v1.4.1-compatible"
            if descriptor.schema_version == 3 else "v1.6.0-native"
        )
    old_schema = int(descriptor.identity["old_schema_version"])
    new_schema = int(descriptor.identity["new_schema_version"])
    return (
        "v1.4.1-compatible"
        if (old_schema, new_schema) == (3, 3) else "cross-version"
    )


def inspect_parse_database(
    path: str,
    *,
    verify_integrity: bool = False,
) -> ParseDatabaseInspection:
    """只读识别解析输入；快速阶段不读取整个 schema 4 文件核摘要。"""
    con, descriptor = dbreader.open_database(
        path,
        require_sealed=True,
        verify_integrity=verify_integrity,
        verify_artifact_fingerprint=verify_integrity,
    )
    try:
        file_size = os.stat(descriptor.path).st_size
    finally:
        con.close()
    return ParseDatabaseInspection(
        descriptor=descriptor,
        file_size_bytes=int(file_size),
        integrity_checked=bool(verify_integrity),
        compatibility_mode=_compatibility_mode(descriptor),
        modules=parse_module_statuses(descriptor),
    )


def _normalize_tokens(values: Iterable[str]) -> tuple[str, ...]:
    if isinstance(values, str):
        values = (values,)
    result = []
    for value in values:
        for item in str(value).split(","):
            normalized = item.strip().casefold()
            if normalized and normalized not in result:
                result.append(normalized)
    return tuple(result)


def plan_parse_export(
    inspection: ParseDatabaseInspection,
    *,
    preset: str = "human-summary",
    include: Iterable[str] = (),
    formats: Iterable[str] = ("html",),
) -> ParseExportPlan:
    """验证模块／格式组合；预设不会暗中开启或移除输出格式。"""
    normalized_preset = str(preset).strip().casefold()
    if normalized_preset not in PARSE_PRESETS:
        raise core.PreflightError(f"未知解析内容预设：{preset}")
    requested_modules = _normalize_tokens(include)
    requested_formats = _normalize_tokens(formats)
    unknown_formats = [
        format_id for format_id in requested_formats
        if format_id not in PARSE_FORMATS
    ]
    if unknown_formats:
        raise core.PreflightError(
            "未知解析输出格式：" + "、".join(unknown_formats))
    if not requested_formats:
        raise core.PreflightError("至少选择一种解析输出格式")

    by_id = {module.spec.module_id: module for module in inspection.modules}
    unknown_modules = [
        module_id for module_id in requested_modules
        if module_id not in by_id
    ]
    if unknown_modules:
        raise core.PreflightError(
            "当前数据库类型没有解析模块：" + "、".join(unknown_modules))
    for module_id in requested_modules:
        module = by_id[module_id]
        if not module.selectable:
            detail = f"：{module.reason}" if module.reason else ""
            raise core.PreflightError(
                f"解析模块 {module_id} 为 {module.state}，不可选择{detail}")

    selected = []
    if normalized_preset != "custom":
        selected.extend(
            module.spec.module_id for module in inspection.modules
            if normalized_preset in module.spec.presets and module.selectable
        )
    for module_id in requested_modules:
        if module_id not in selected:
            selected.append(module_id)
    if not selected:
        raise core.PreflightError(
            "当前预设没有可选模块；请选择有记录的模块或更换数据库")

    format_modules: dict[str, tuple[str, ...]] = {}
    for format_id in requested_formats:
        compatible = tuple(
            module_id for module_id in selected
            if format_id in by_id[module_id].spec.formats
        )
        if not compatible:
            raise core.PreflightError(
                f"所选模块均不支持输出格式 {format_id}")
        format_modules[format_id] = compatible
    unsupported = [
        module_id for module_id in selected
        if not any(
            module_id in format_modules[format_id]
            for format_id in requested_formats
        )
    ]
    if unsupported:
        raise core.PreflightError(
            "所选格式无法承载模块：" + "、".join(unsupported))

    privacy_notices = []
    for module_id in selected:
        module = by_id[module_id]
        if module.spec.privacy_level == "sensitive_raw":
            privacy_notices.append(
                "原始数据可能包含位置、设备、软件和作者信息；"
                "HTML／XLSX 只允许受限预览，完整值优先写入 JSONL。"
            )
    return ParseExportPlan(
        preset=normalized_preset,
        module_ids=tuple(selected),
        format_ids=requested_formats,
        format_modules=format_modules,
        privacy_notices=tuple(privacy_notices),
    )


def legacy_modules(database_type: str) -> tuple[ParseModuleSpec, ...]:
    registered = {
        module.module_id: module
        for module in _registered_modules(database_type)
        if module.legacy_export
    }
    order = _LEGACY_MODULE_ORDER[database_type]
    if set(registered) != set(order):
        raise RuntimeError(
            f"{database_type} 旧报告模块与冻结顺序不一致")
    return tuple(registered[module_id] for module_id in order)


def legacy_pages(database_type: str) -> tuple[ParsePageSpec, ...]:
    return tuple(
        page
        for module in legacy_modules(database_type)
        for page in module.pages
    )


def legacy_capabilities(database_type: str) -> tuple[str, ...]:
    # 保持 v1.5.1 在多项结构同时异常时的首个报错顺序。
    order = {
        "snapshot": (
            "overview", "issues", "files", "directories", "hashes",
            "photo_metadata", "video_metadata", "video_gps",
            "media_streams", "working_metadata", "document_metadata",
            "archives", "diagnostics",
        ),
        "diff": (
            "overview", "file_changes", "directory_changes",
            "content_groups", "enumeration_gaps", "evidence_notes",
        ),
    }
    if database_type not in order:
        raise ValueError(f"未知数据库类型：{database_type}")
    registered = {
        capability_id
        for module in legacy_modules(database_type)
        for capability_id in module.required_capabilities
    }
    result = order[database_type]
    if set(result) != registered:
        raise RuntimeError(
            f"{database_type} 旧报告能力顺序与模块注册表不一致")
    return result


# === 快照导出 ===


def _flatten(prefix: str, obj, out: list) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            _flatten(f"{prefix}.{k}" if prefix else str(k), v, out)
    else:
        out.append((prefix, obj))


def export_snapshot(snapshot_path: str, output_dir: str) -> dict:
    snapshot_path = os.path.abspath(snapshot_path)
    if not os.path.isfile(snapshot_path):
        raise core.PreflightError(f"快照不存在：{snapshot_path}")
    stem = os.path.basename(snapshot_path)
    stem = stem[:-len(".sqlite")] if stem.endswith(".sqlite") else stem
    folder = os.path.join(os.path.abspath(output_dir), stem + "_Report")
    os.makedirs(folder, exist_ok=True)
    con, descriptor = dbreader.open_database(
        snapshot_path, expected_type="snapshot")
    files = [
        _write_report_guide(folder, "snapshot", snapshot_path),
        _write_report_info(folder),
    ]
    try:
        dbreader.require_queryable_capabilities(
            descriptor, *legacy_capabilities("snapshot"))
        csv_writer = CsvQueryWriter(con, folder)
        for page in legacy_pages("snapshot"):
            files.append(csv_writer.write_page(page))
        info_cur = con.execute("SELECT * FROM snapshot_info")
        info = dict(zip([c[0] for c in info_cur.description],
                        info_cur.fetchone()))
        kv = []
        for k, v in info.items():
            if k == "counts_json" and v:
                flat: list = []
                _flatten("counts", json.loads(v), flat)
                kv.extend(flat)
            else:
                kv.append((k, v))
        files.append(csv_writer.write_rows(
            "Summary.csv", ["key", "value"], kv))
    finally:
        con.close()
    files.append(LegacyExcelWriter(folder).write(files))
    return {"folder": folder, "files": files}


# === Diff 导出 ===
def _load_all(con, table):
    cur = con.execute(f"SELECT * FROM {table}")
    header = [c[0] for c in cur.description]
    return header, [dict(zip(header, r)) for r in cur.fetchall()]


def _md_escape(s) -> str:
    return str(s).replace("|", "\\|")


def export_diff(diff_path: str, output_dir: str) -> dict:
    diff_path = os.path.abspath(diff_path)
    if not os.path.isfile(diff_path):
        raise core.PreflightError(f"Diff 数据库不存在：{diff_path}")
    stem = os.path.basename(diff_path)
    for suf in (".diff.sqlite", ".sqlite"):
        if stem.endswith(suf):
            stem = stem[:-len(suf)]
            break
    folder = os.path.join(os.path.abspath(output_dir), stem + "_Report")
    os.makedirs(folder, exist_ok=True)
    con, descriptor = dbreader.open_database(
        diff_path, expected_type="diff")
    files = [
        _write_report_guide(folder, "diff", diff_path),
        _write_report_info(folder),
    ]
    try:
        dbreader.require_queryable_capabilities(
            descriptor, *legacy_capabilities("diff"))
        csv_writer = CsvQueryWriter(con, folder)
        for page in legacy_pages("diff"):
            files.append(csv_writer.write_page(page))
        _, info_rows = _load_all(con, "diff_info")
        info = info_rows[0]
        _, entries = _load_all(con, "diff_entries")
        _, dirs = _load_all(con, "diff_dirs")
        _, groups = _load_all(con, "diff_hash_groups")
        _, subtrees = _load_all(con, "diff_subtrees")
    finally:
        con.close()

    se: dict = {}
    for r in entries:
        se.setdefault(r["status"], {}).setdefault(r["evidence"], 0)
        se[r["status"]][r["evidence"]] += 1
    n = lambda st: sum(se.get(st, {}).values())
    content_breaking = sum(n(s) for s in
                           ("content_changed", "added", "deleted", "copied"))
    # 维度隔离：hash_missing/unstable 的路径双侧都已配对存在，
    # 只削弱内容维度证据，不得传染到结构维度的存在性结论
    content_caveats = sum(n(s) for s in ("unknown", "hash_missing", "unstable"))
    structure_caveats = n("unknown")
    dir_changed = [d for d in dirs if d["status"] != "unchanged"]
    structure_breaking = (sum(n(s) for s in ("added", "deleted",
                                             "moved_or_renamed", "copied"))
                          + sum(1 for d in dirs
                                if d["status"] in ("added", "deleted")))
    same_evidence = {}
    for st in ("unchanged", "stat_changed_content_same",
               "metadata_extraction_changed"):
        for ev, c in se.get(st, {}).items():
            same_evidence[ev] = same_evidence.get(ev, 0) + c
    heur = sum(c for st, evs in se.items() for ev, c in evs.items()
               if ev == "heuristic_file_id")
    changed_groups = [g for g in groups if g["old_count"] != g["new_count"]]
    mapping = json.loads(info["root_mapping_json"])
    counts_json = json.loads(info["counts_json"]) if info["counts_json"] else {}

    def conclusion(breaking, kind, caveats, caveat_kinds):
        if breaking:
            return f"**不一致**（{breaking} 条{kind}差异）"
        if caveats:
            return (f"在可断言范围内一致；另有 {caveats} 条无法断言"
                    f"（{caveat_kinds}，见明细）")
        return "**一致**"

    content_conclusion = conclusion(
        content_breaking, "内容", content_caveats,
        "unknown/hash_missing/unstable")
    structure_conclusion = conclusion(
        structure_breaking, "结构", structure_caveats,
        "unknown——枚举失败或碰撞")

    lines = [
        "# Diff 摘要",
        "",
        *core.report_markdown_lines("DBS-41 结果报告导出"),
        "",
        f"- 旧快照：`{info['old_snapshot_file']}`（uuid `{info['old_snapshot_uuid']}`，"
        f"hash_coverage=**{info['old_hash_coverage']}**）",
        f"- 新快照：`{info['new_snapshot_file']}`（uuid `{info['new_snapshot_uuid']}`，"
        f"hash_coverage=**{info['new_hash_coverage']}**）",
        f"- 生成（UTC）：{info['created_at_utc']}；工具版本：{info['tool_version']}"
        + ("；**forced=1（不完整输入：文件名高32bit指纹缺失被越过）**"
           if info["forced"] else ""),
        f"- root 配对：{mapping['pairs']}"
        + ("（**单根自动配对**：label 不同，按挂载内容直接对比）"
           if mapping.get("auto_paired") else "")
        + (f"；未配对旧侧 {mapping['unpaired_old']}、新侧 {mapping['unpaired_new']}"
           f"（未配对 root 整体计入增删）"
           if mapping["unpaired_old"] or mapping["unpaired_new"] else ""),
        "",
        "## 双维度结论",
        "",
        f"- 内容维度（哈希多重集）：{content_conclusion}",
        f"- 结构维度（路径树）：{structure_conclusion}"
        + ("（hash_missing/unstable 条目双侧均已配对存在，"
           "不影响存在性结论）" if content_caveats > structure_caveats else ""),
        "",
        "## 状态 × evidence",
        "",
        "| status | evidence | 数量 |",
        "|---|---|---:|",
    ]
    for st in sorted(se):
        for ev in sorted(se[st]):
            lines.append(f"| {st} | {ev} | {se[st][ev]:,} |")
    lines.append("")
    if same_evidence:
        lines.append("「内容相同」证据分布："
                     + "；".join(f"{ev}={c:,}"
                                 for ev, c in sorted(same_evidence.items()))
                     + "。")
        if same_evidence.get("propagated_single_computation"):
            lines.append("注意：propagated_single_computation 表示两侧追溯到"
                         "**同一次计算事件**（哈希被抄录传递），"
                         "**不构成独立验证**，不得表述为「已验证一致」。")
        lines.append("")
    if subtrees:
        lines.append("## ⚠ 枚举失败子树（其下差异一律 unknown，绝不判增删）")
        lines.append("")
        for s in subtrees:
            rel = s["rel_path"] if s["rel_path"] else "<root 级失败>"
            lines.append(f"- [{s['side']}] `{_md_escape(s['root_label'])}"
                         f"\\{_md_escape(rel)}`（{s['enum_status']}，"
                         f"另一侧受影响约 {s['affected_estimate']} 条）")
        lines.append("")
    lines.append("## 目录维度")
    lines.append("")
    dir_counts: dict = {}
    for d in dirs:
        dir_counts[d["status"]] = dir_counts.get(d["status"], 0) + 1
    lines.append("，".join(f"{k}={v:,}" for k, v in sorted(dir_counts.items()))
                 or "（无目录记录）")
    for d in dir_changed[:_LIST_CAP]:
        lbl = (d["old_root_label"] if d["old_rel_path"] is not None
               else d["new_root_label"]) or ""
        rel = (d["old_rel_path"] if d["old_rel_path"] is not None
               else d["new_rel_path"])
        lines.append(f"- {d['status']}: "
                     f"`{_md_escape(lbl)}\\{_md_escape(rel)}`"
                     + (f"（{d['reason']}）" if d["reason"] else ""))
    if len(dir_changed) > _LIST_CAP:
        lines.append(f"- …共 {len(dir_changed)} 条目录级变更，"
                     "详见 Diff_dirs.csv")
    lines.append("")
    lines.append("## 重复内容组变化")
    lines.append("")
    if changed_groups:
        for g in changed_groups[:_LIST_CAP]:
            lines.append(f"- `{g['hash_hex'][:16]}…`：{g['old_count']} → "
                         f"{g['new_count']}"
                         + (f"（新侧硬链接组 {g['new_hardlink_sets']}）"
                            if g["new_hardlink_sets"] else ""))
        if len(changed_groups) > _LIST_CAP:
            lines.append(f"- …共 {len(changed_groups)} 组，"
                         "详见 Diff_hash_groups.csv")
    else:
        lines.append("（无副本数量变化）")
    lines.append("")
    lines.append("## 启发式结论（单列，不与哈希确证混计）")
    lines.append("")
    lines.append(f"file_id 启发移动判定：{heur:,} 条"
                 "（依据 NTFS 文件标识＋size＋mtime，非内容证据）。")
    lines.append("")
    lines.append("## 覆盖声明")
    lines.append("")
    lines.append(f"- 双侧 hash_coverage：旧={info['old_hash_coverage']}，"
                 f"新={info['new_hash_coverage']}（hash_coverage=none 侧"
                 "缺少哈希时只得出 hash_missing，不构成校验失败）")
    pr = counts_json.get("payload_rows", {})
    lines.append(f"- raw payload 行数：旧={pr.get('old')}，新={pr.get('new')}"
                 "（任一侧缺失的条目 metadata_changed 未评估）")
    lines.append("")
    with open(os.path.join(folder, "Diff_summary.md"), "w",
              encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))
    files.append("Diff_summary.md")
    files.append(LegacyExcelWriter(folder).write(files))
    return {"folder": folder, "files": files}
