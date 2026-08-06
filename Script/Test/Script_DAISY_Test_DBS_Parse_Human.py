"""DAISY v1.6.0 自包含 HTML 与流式 XLSX writer 测试。"""
from __future__ import annotations

import hashlib
from html.parser import HTMLParser
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock
import xml.etree.ElementTree as ET
import zipfile
import zlib


_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SCRIPT_DIR = os.path.dirname(_TEST_DIR)
_REPO_ROOT = os.path.dirname(_SCRIPT_DIR)
_LIB_DIR = os.path.join(_SCRIPT_DIR, "Lib")
sys.path[:0] = [_TEST_DIR, _SCRIPT_DIR, _LIB_DIR]

import Script_DAISY_Lib_DBS_01_Core as core
import Script_DAISY_Lib_DBS_04_Diff as dbdiff
import Script_DAISY_Lib_DBS_07_Parse as dbparse
import Script_DAISY_Lib_DBS_15_Parse_Projection as projection
import Script_DAISY_Lib_DBS_16_Parse_Run as parserun
import Script_DAISY_Lib_DBS_17_Parse_Human as human
import Script_DAISY_Test_Tree as tree_fixture


_RUNTIME_ROOT = os.path.join(
    _REPO_ROOT, ".test_runtime", "v1_6_0", "parse_human")
_GENERATED_AT = "2026-08-07T03:04:05.1234567Z"
_MAIN_NS = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _identity(path: str) -> tuple[str, int, int]:
    stat_result = os.stat(path)
    return _sha256(path), stat_result.st_size, stat_result.st_mtime_ns


class _HtmlAudit(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.scripts = 0
        self.external = []
        self.nonces = []

    def handle_starttag(self, tag, attrs) -> None:
        attributes = dict(attrs)
        if tag == "script":
            self.scripts += 1
        if tag in ("script", "style"):
            self.nonces.append(attributes.get("nonce"))
        for key in ("src", "href"):
            value = attributes.get(key)
            if value and not value.startswith("#"):
                self.external.append((tag, key, value))


class TestParseHuman(unittest.TestCase):
    def setUp(self) -> None:
        os.makedirs(_RUNTIME_ROOT, exist_ok=True)
        self._td = tempfile.TemporaryDirectory(
            prefix="case_", dir=_RUNTIME_ROOT)
        self.base = self._td.name
        self.snapshots = os.path.join(self.base, "Snapshots")
        self.reports = os.path.join(self.base, "Reports")
        os.makedirs(self.snapshots)
        os.makedirs(self.reports)

    def tearDown(self) -> None:
        self._td.cleanup()

    def _snapshot(
        self,
        name: str = "人读",
        *,
        file_count: int = 6,
        payload_prefix: str = "payload",
    ) -> str:
        tree = os.path.join(self.base, "Tree_" + name)
        os.makedirs(tree)
        for index in range(file_count):
            filename = (
                "=SUM(1,1).txt" if index == 0 else f"中文素材_{index:03}.bin"
            )
            tree_fixture.write(
                tree,
                os.path.join("中文目录", filename),
                f"{payload_prefix}-{index}".encode("utf-8"),
            )

        def enrich(con: sqlite3.Connection) -> None:
            entries = list(con.execute(
                "SELECT entry_id,rel_path FROM entries ORDER BY entry_id"))
            first_id = int(entries[0][0])
            con.execute(
                "INSERT INTO document_metadata"
                " (entry_id,doc_format,title,author,page_count,parser,"
                " parser_version,parsed_at_utc)"
                " VALUES (?,'fixture',?, '=1+1',1,'fixture','1.0',?)",
                (
                    first_id,
                    "<script>alert('数据库')</script>\x01=1+1" + "长" * 120,
                    "2026-08-07T00:00:00.0000000Z",
                ),
            )
            con.execute(
                "INSERT INTO errors"
                " (entry_id,stage,error_code,message,occurred_at_utc)"
                " VALUES (?,'metadata','fixture_error',?,?)",
                (
                    first_id,
                    "<img src=x onerror=alert(1)>",
                    "2026-08-07T00:00:00.0000000Z",
                ),
            )
            payload = json.dumps(
                {
                    "formula": "=cmd|' /C calc'!A0",
                    "html": "<svg onload=alert(1)>",
                    "nested": {"中文": [1, True, None]},
                    "long": "值" * 180,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            compressed = zlib.compress(payload)
            payload_sha = hashlib.sha256(payload).hexdigest()
            con.executemany(
                "INSERT INTO raw_payloads"
                " (entry_id,provider,payload_zlib,payload_sha256,"
                " uncompressed_bytes,provider_version,profile_version,"
                " parsed_at_utc) VALUES (?,'exiftool',?,?,?,?,1,?)",
                [
                    (
                        int(entry_id),
                        compressed,
                        payload_sha,
                        len(payload),
                        "13.fixture",
                        "2026-08-07T00:00:00.0000000Z",
                    )
                    for entry_id, _rel_path in entries
                ],
            )

        return tree_fixture.build_snapshot(
            tree,
            self.snapshots,
            name,
            label="人读夹具",
            hash_mode="full",
            pre_finalize=enrich,
        )

    @staticmethod
    def _plan(
        snapshot: str,
        include: tuple[str, ...],
        formats: tuple[str, ...],
    ) -> dbparse.ParseExportPlan:
        inspection = dbparse.inspect_parse_database(snapshot)
        return dbparse.plan_parse_export(
            inspection,
            preset="custom",
            include=include,
            formats=formats,
        )

    def test_html_is_self_contained_escaped_bounded_and_read_only(self) \
            -> None:
        snapshot = self._snapshot("HTML")
        input_identity = _identity(snapshot)
        plan = self._plan(
            snapshot,
            ("overview", "issues", "files", "document_metadata", "raw_payloads"),
            ("html",),
        )
        result = parserun.export_parse_report(
            snapshot,
            self.reports,
            plan,
            generated_at_utc=_GENERATED_AT,
            html_preview_rows=2,
            html_cell_chars=80,
            batch_rows=1,
        )
        self.assertEqual(input_identity, _identity(snapshot))
        self.assertEqual(
            {item.relative_path for item in result.artifacts},
            {human.HTML_NAME},
        )
        html_path = os.path.join(result.report_directory, human.HTML_NAME)
        with open(html_path, encoding="utf-8") as handle:
            text = handle.read()
        audit = _HtmlAudit()
        audit.feed(text)
        self.assertEqual(audit.scripts, 1)
        self.assertEqual(audit.external, [])
        self.assertTrue(audit.nonces)
        self.assertTrue(all(
            nonce == "daisy-report-v1" for nonce in audit.nonces))
        self.assertNotIn("unsafe-inline", text)
        self.assertNotIn("file://", text.casefold())
        self.assertNotIn("http://", text.casefold())
        self.assertNotIn("https://", text.casefold())
        self.assertNotIn("<script>alert('数据库')</script>", text)
        self.assertIn("&lt;script&gt;alert", text)
        self.assertIn("\\u0001", text)
        self.assertIn("@media print", text)
        self.assertIn("table-filter", text)
        self.assertIn("预览 2／6 行", text)
        self.assertIn("HTML 只嵌入有限预览", text)
        with open(result.manifest_path, encoding="utf-8") as handle:
            manifest = json.load(handle)
        files = next(
            item for item in manifest["modules"]
            if item["module_id"] == "files")
        self.assertEqual(files["rows"], 6)
        self.assertEqual(files["display_rows"]["html"], 2)
        artifact = manifest["artifacts"][0]
        self.assertEqual(artifact["format"], "html")
        self.assertEqual(artifact["sha256"], _sha256(html_path))

    def test_xlsx_has_overview_split_sheets_filters_and_no_formulas(self) \
            -> None:
        snapshot = self._snapshot("XLSX")
        plan = self._plan(
            snapshot,
            ("files", "document_metadata", "raw_payloads"),
            ("xlsx",),
        )
        result = parserun.export_parse_report(
            snapshot,
            self.reports,
            plan,
            generated_at_utc=_GENERATED_AT,
            xlsx_max_rows=4,
            xlsx_max_cell_chars=48,
            batch_rows=1,
        )
        workbook = os.path.join(result.report_directory, human.XLSX_NAME)
        with zipfile.ZipFile(workbook) as archive:
            self.assertIsNone(archive.testzip())
            names = set(archive.namelist())
            self.assertIn("xl/workbook.xml", names)
            self.assertIn("xl/styles.xml", names)
            workbook_root = ET.fromstring(archive.read("xl/workbook.xml"))
            sheet_names = [
                node.attrib["name"]
                for node in workbook_root.findall(".//x:sheet", _MAIN_NS)
            ]
            self.assertEqual(sheet_names[0], "报告概览")
            self.assertIn("文件清单", sheet_names)
            self.assertIn("文件清单_2", sheet_names)
            self.assertEqual(
                len({name.casefold() for name in sheet_names}),
                len(sheet_names),
            )
            self.assertTrue(all(len(name) <= 31 for name in sheet_names))
            sheet_xml = [
                archive.read(name)
                for name in sorted(names)
                if name.startswith("xl/worksheets/sheet")
            ]
            for payload in sheet_xml:
                ET.fromstring(payload)
                self.assertIn(b"state=\"frozen\"", payload)
                self.assertIn(b"<autoFilter", payload)
                self.assertNotIn(b"<f>", payload)
            joined = b"\n".join(sheet_xml).decode("utf-8")
            self.assertIn("文件名\n( name)".replace(" ", ""), joined.replace(" ", ""))
            self.assertIn("=SUM(1,1).txt", joined)
            self.assertIn("…[显示已截断]", joined)
            self.assertNotIn("externalLink", "\n".join(names))
        with open(result.manifest_path, encoding="utf-8") as handle:
            manifest = json.load(handle)
        files = next(
            item for item in manifest["modules"]
            if item["module_id"] == "files")
        self.assertEqual(files["display_rows"]["xlsx"], 6)
        raw = next(
            item for item in manifest["modules"]
            if item["module_id"] == "raw_payloads")
        self.assertEqual(raw["display_rows"]["xlsx"], 6)

    def test_all_formats_share_one_projection_pass_per_module(self) -> None:
        snapshot = self._snapshot("全部格式")
        plan = self._plan(
            snapshot,
            ("files", "raw_payloads"),
            ("html", "xlsx", "csv", "jsonl"),
        )
        original = projection.iter_module_rows
        with mock.patch.object(
            projection, "iter_module_rows", wraps=original,
        ) as iterator:
            result = parserun.export_parse_report(
                snapshot,
                self.reports,
                plan,
                generated_at_utc=_GENERATED_AT,
                batch_rows=1,
                xlsx_max_cell_chars=48,
            )
        self.assertEqual(iterator.call_count, 2)
        self.assertEqual(
            [call.args[2] for call in iterator.call_args_list],
            ["files", "raw_payloads"],
        )
        self.assertEqual(
            {artifact.relative_path for artifact in result.artifacts},
            {
                human.HTML_NAME,
                human.XLSX_NAME,
                "files.csv",
                "files.jsonl",
                "raw_payloads.jsonl",
            },
        )
        with open(
            os.path.join(
                result.report_directory, "raw_payloads.jsonl"),
            encoding="utf-8",
        ) as handle:
            raw_record = json.loads(next(handle))["record"]
        self.assertEqual(len(raw_record["payload"]["long"]), 180)

    def test_raw_xlsx_is_limited_to_200_rows_but_total_stays_honest(self) \
            -> None:
        snapshot = self._snapshot("RAW上限", file_count=205)
        plan = self._plan(
            snapshot,
            ("raw_payloads",),
            ("xlsx",),
        )
        result = parserun.export_parse_report(
            snapshot,
            self.reports,
            plan,
            generated_at_utc=_GENERATED_AT,
            batch_rows=7,
        )
        with open(result.manifest_path, encoding="utf-8") as handle:
            manifest = json.load(handle)
        module = manifest["modules"][0]
        self.assertEqual(module["rows"], 205)
        self.assertEqual(module["display_rows"]["xlsx"], 200)
        workbook = os.path.join(result.report_directory, human.XLSX_NAME)
        with zipfile.ZipFile(workbook) as archive:
            raw_sheet = ET.fromstring(
                archive.read("xl/worksheets/sheet2.xml"))
        self.assertEqual(len(raw_sheet.findall(".//x:row", _MAIN_NS)), 201)

    def test_cancelled_xlsx_removes_parts_staging_and_final_report(self) \
            -> None:
        snapshot = self._snapshot("取消XLSX")
        plan = self._plan(snapshot, ("files",), ("xlsx",))
        state = {"cancel": False}

        def progress(item: parserun.ParseProgress) -> None:
            if item.phase == "module" and item.rows_done >= 2:
                state["cancel"] = True

        with self.assertRaises(parserun.ParseExportCancelled):
            parserun.export_parse_report(
                snapshot,
                self.reports,
                plan,
                generated_at_utc=_GENERATED_AT,
                batch_rows=1,
                progress_every_rows=1,
                progress_callback=progress,
                cancel_check=lambda: state["cancel"],
            )
        self.assertTrue(state["cancel"])
        self.assertEqual(os.listdir(self.reports), [])

    def test_diff_html_leads_with_change_conclusion_not_snapshot_issues(
        self,
    ) -> None:
        old_snapshot = self._snapshot(
            "人读Diff旧", file_count=2, payload_prefix="old")
        new_snapshot = self._snapshot(
            "人读Diff新", file_count=2, payload_prefix="new")
        diff_path = os.path.join(self.base, "Human_Diff.sqlite")
        dbdiff.compare(old_snapshot, new_snapshot, diff_path)
        identities = {
            path: _identity(path)
            for path in (old_snapshot, new_snapshot, diff_path)
        }
        inspection = dbparse.inspect_parse_database(diff_path)
        plan = dbparse.plan_parse_export(
            inspection,
            preset="human-summary",
            formats=("html",),
        )
        result = parserun.export_parse_report(
            diff_path,
            self.reports,
            plan,
            generated_at_utc=_GENERATED_AT,
        )
        with open(
            os.path.join(result.report_directory, human.HTML_NAME),
            encoding="utf-8",
        ) as handle:
            text = handle.read()
        self.assertIn("Diff 非 unchanged 记录", text)
        self.assertIn("content_changed=2", text)
        self.assertNotIn("未选择问题摘要</h2>", text)
        for path, identity in identities.items():
            self.assertEqual(identity, _identity(path))


if __name__ == "__main__":
    unittest.main(verbosity=2)
