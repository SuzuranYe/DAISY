"""DBS Parse 模块注册表、writer 与 DBS-41 兼容入口测试。"""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock
import zipfile


_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SCRIPT_DIR = os.path.dirname(_TEST_DIR)
_REPO_ROOT = os.path.dirname(_SCRIPT_DIR)
_LIB_DIR = os.path.join(_SCRIPT_DIR, "Lib")
_MODULE_DIR = os.path.join(_SCRIPT_DIR, "Module")
sys.path[:0] = [_TEST_DIR, _SCRIPT_DIR, _LIB_DIR, _MODULE_DIR]

import Script_DAISY_Lib_DBS_01_Core as core
import Script_DAISY_Lib_DBS_07_Parse as dbparse
import Script_DAISY_Module_DBS_41_Export_Report as legacy_export


_RUNTIME_ROOT = os.path.join(
    _REPO_ROOT, ".test_runtime", "v1_6_0", "parse")


class TestParseRegistry(unittest.TestCase):
    def test_snapshot_registry_freezes_legacy_projection_order(self) -> None:
        modules = dbparse.parse_modules("snapshot")
        self.assertEqual(
            [module.module_id for module in modules],
            [
                "overview", "files", "directories", "photo_metadata",
                "video_metadata", "video_gps", "working_metadata",
                "document_metadata", "media_streams", "hashes",
                "archives", "diagnostics", "issues",
            ],
        )
        self.assertEqual(
            [page.filename for page in dbparse.legacy_pages("snapshot")],
            [
                "Tree.csv", "Tree_dirs.csv", "Exif_inventory_photo.csv",
                "Exif_inventory_video.csv", "GPS_inventory_video.csv",
                "Exif_inventory_working.csv",
                "Exif_inventory_document.csv",
                "Stream_inventory_video.csv",
                "Stream_inventory_audio.csv", "Hash_inventory.csv",
                "Archive_inventory.csv", "Archive_inventory_members.csv",
                "Metadata_diagnostics.csv", "Errors.csv",
            ],
        )
        self.assertEqual(
            dbparse.legacy_capabilities("snapshot"),
            (
                "overview", "issues", "files", "directories", "hashes",
                "photo_metadata", "video_metadata", "video_gps",
                "media_streams", "working_metadata", "document_metadata",
                "archives", "diagnostics",
            ),
        )
        self.assertTrue(all(module.schema3_fallback for module in modules))
        self.assertTrue(all(
            module.formats == frozenset(("csv", "xlsx"))
            for module in modules
        ))

    def test_diff_registry_freezes_legacy_projection_order(self) -> None:
        modules = dbparse.parse_modules("diff")
        self.assertEqual(
            [module.module_id for module in modules],
            [
                "overview", "file_changes", "directory_changes",
                "content_groups", "enumeration_gaps", "evidence_notes",
            ],
        )
        self.assertEqual(
            [page.filename for page in dbparse.legacy_pages("diff")],
            [
                "Diff_details.csv", "Diff_dirs.csv",
                "Diff_hash_groups.csv", "Diff_subtrees.csv",
            ],
        )
        with self.assertRaises(ValueError):
            dbparse.parse_modules("unknown")


class TestParseWriters(unittest.TestCase):
    def setUp(self) -> None:
        os.makedirs(_RUNTIME_ROOT, exist_ok=True)
        self._td = tempfile.TemporaryDirectory(
            prefix="case_", dir=_RUNTIME_ROOT)
        self.folder = self._td.name
        self.connection = sqlite3.connect(":memory:")
        self.connection.execute(
            "CREATE TABLE sample (id INTEGER PRIMARY KEY, value TEXT)")
        self.connection.executemany(
            "INSERT INTO sample (id,value) VALUES (?,?)",
            ((1, "中文"), (2, "line1\nline2")),
        )

    def tearDown(self) -> None:
        self.connection.close()
        self._td.cleanup()

    def test_csv_and_xlsx_writers_keep_legacy_bytes(self) -> None:
        writer = dbparse.CsvQueryWriter(self.connection, self.folder)
        self.assertEqual(writer.format_id, "csv")
        query_name = writer.write_page(dbparse.ParsePageSpec(
            "Query.csv", "SELECT id,value FROM sample ORDER BY id"))
        rows_name = writer.write_rows(
            "Rows.csv", ["key", "value"], [["语言", "中文"]])
        csv_paths = [
            os.path.join(self.folder, query_name),
            os.path.join(self.folder, rows_name),
        ]
        before = {path: Path(path).read_bytes() for path in csv_paths}
        for data in before.values():
            self.assertFalse(data.startswith(b"\xef\xbb\xbf"))
            self.assertNotIn(b"\r\n", data)
        self.assertIn("中文".encode("utf-8"), before[csv_paths[0]])

        excel_writer = dbparse.LegacyExcelWriter(self.folder)
        self.assertEqual(excel_writer.format_id, "xlsx")
        workbook_name = excel_writer.write([query_name, rows_name])
        self.assertEqual(workbook_name, "Report_Excel.xlsx")
        workbook_path = os.path.join(self.folder, workbook_name)
        self.assertTrue(zipfile.is_zipfile(workbook_path))
        with zipfile.ZipFile(workbook_path) as archive:
            self.assertIn("xl/workbook.xml", archive.namelist())
            self.assertIn("xl/worksheets/sheet1.xml", archive.namelist())
        after = {path: Path(path).read_bytes() for path in csv_paths}
        self.assertEqual(after, before)


class TestLegacyExportWrapper(unittest.TestCase):
    def test_old_python_entrypoints_are_exact_core_aliases(self) -> None:
        self.assertIs(legacy_export.export_snapshot, dbparse.export_snapshot)
        self.assertIs(legacy_export.export_diff, dbparse.export_diff)
        self.assertEqual(
            legacy_export._XLSX_MAX_CELL_CHARS,
            dbparse._XLSX_MAX_CELL_CHARS,
        )
        self.assertIs(legacy_export._excel_row, dbparse._excel_row)

    def test_old_cli_keeps_success_and_preflight_exit_codes(self) -> None:
        result = {"folder": "Report", "files": ["Tree.csv"]}
        stdout = io.StringIO()
        with mock.patch.object(
            legacy_export, "export_snapshot", return_value=result,
        ) as export_snapshot, mock.patch.object(
            sys, "argv", ["dbs41", "--snapshot", "input.sqlite"],
        ), redirect_stdout(stdout):
            self.assertEqual(legacy_export.main(), 0)
        export_snapshot.assert_called_once_with(
            "input.sqlite", "Output/Reports")
        self.assertIn("导出目录:Report", stdout.getvalue())

        stderr = io.StringIO()
        with mock.patch.object(
            legacy_export, "export_snapshot",
            side_effect=core.PreflightError("invalid"),
        ), mock.patch.object(
            sys, "argv", ["dbs41", "--snapshot", "bad.sqlite"],
        ), redirect_stderr(stderr):
            self.assertEqual(legacy_export.main(), 2)
        self.assertIn("导出失败：invalid", stderr.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
