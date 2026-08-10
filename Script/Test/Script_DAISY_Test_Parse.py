"""档案数据解析模块注册表、写入器与数据解析兼容入口测试。"""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import csv
import io
import os
from pathlib import Path
import shutil
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

import Script_DAISY_Lib_Snapshot_Core as core
import Script_DAISY_Lib_Metadata as meta
import Script_DAISY_Lib_Snapshot_Diff as dbdiff
import Script_DAISY_Lib_Database_Parse as dbparse
import Script_DAISY_Module_Parse as legacy_export
import Script_DAISY_Test_Tree as test_tree


Export = legacy_export
tt = test_tree


_RUNTIME_ROOT = os.path.join(
    _REPO_ROOT, ".test_runtime", "v1_6_0", "parse")


class TestParseRegistry(unittest.TestCase):
    def test_snapshot_registry_freezes_legacy_projection_order(self) -> None:
        modules = dbparse.parse_modules("snapshot")
        self.assertEqual(
            [module.module_id for module in modules],
            [
                "overview", "issues", "files", "directories", "hashes",
                "photo_metadata", "video_metadata", "video_gps",
                "media_streams", "working_metadata", "document_metadata",
                "archives", "raw_payloads", "diagnostics", "run_history",
            ],
        )
        self.assertEqual(
            [module.module_id for module in dbparse.legacy_modules(
                "snapshot")],
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
            {"csv", "xlsx"}.issubset(module.formats)
            for module in dbparse.legacy_modules("snapshot")
        ))
        raw_module = next(
            module for module in modules
            if module.module_id == "raw_payloads")
        self.assertEqual(raw_module.privacy_level, "sensitive_raw")
        self.assertFalse(raw_module.legacy_export)

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
        self.assertIn("导出目录：Report", stdout.getvalue())

        stderr = io.StringIO()
        with mock.patch.object(
            legacy_export, "export_snapshot",
            side_effect=core.PreflightError("invalid"),
        ), mock.patch.object(
            sys, "argv", ["dbs41", "--snapshot", "bad.sqlite"],
        ), redirect_stderr(stderr):
            self.assertEqual(legacy_export.main(), 2)
        self.assertIn("导出失败：invalid", stderr.getvalue())


class _ParseFixture(unittest.TestCase):
    def setUp(self) -> None:
        os.makedirs(_RUNTIME_ROOT, exist_ok=True)
        self._td = tempfile.TemporaryDirectory(
            prefix="export_", dir=_RUNTIME_ROOT)
        self.base = self._td.name
        self.old_tree = os.path.join(self.base, "TreeOld")
        self.new_tree = os.path.join(self.base, "TreeNew")
        self.snaps = os.path.join(self.base, "Snapshots")
        os.makedirs(self.old_tree)
        os.makedirs(self.snaps)

    def tearDown(self) -> None:
        self._td.cleanup()

    def clone(self) -> None:
        shutil.copytree(self.old_tree, self.new_tree)

    def snap(self, tree: str, name: str, **kwargs) -> str:
        kwargs.setdefault("label", "T")
        return test_tree.build_snapshot(
            tree, self.snaps, name, **kwargs)


class TestExportSnapshot(_ParseFixture):
    def _read_csv(self, path):
        with open(path, "rb") as f:
            raw = f.read()
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), "CSV 不得带 BOM")
        self.assertNotIn(b"\r\n", raw, "CSV 须为 LF 行尾")
        with open(path, encoding="utf-8", newline="") as f:
            return list(csv.reader(f))

    def test_excel_display_caps_cells_without_changing_csv_contract(self):
        value = "中" * (Export._XLSX_MAX_CELL_CHARS + 10)
        display, = Export._excel_row(["message"], [value])
        self.assertEqual(len(display), Export._XLSX_MAX_CELL_CHARS)
        self.assertTrue(display.endswith("…"))
        self.assertEqual(len(value), Export._XLSX_MAX_CELL_CHARS + 10)

    def test_snapshot_export_pages(self):
        import zipfile as _zf
        import xml.etree.ElementTree as _et
        tt.write(self.old_tree, "照片.bin", b"photo-like")
        tt.write(self.old_tree, "a,b'c.txt", b"comma,quote")
        with _zf.ZipFile(os.path.join(self.old_tree, "pack.zip"), "w",
                         _zf.ZIP_DEFLATED) as z:
            z.writestr("成员.txt", b"member-data")
        snap = self.snap(self.old_tree, "exp")
        # 压缩包页需要 archive_members——用元数据阶段真实填充
        out_dir = os.path.join(self.base, "Exports")
        res = Export.export_snapshot(snap, out_dir)
        folder = res["folder"]
        for page in ("Tree.csv", "Tree_dirs.csv", "Hash_inventory.csv",
                     "GPS_inventory_video.csv", "Summary.csv", "Errors.csv",
                     "Report_guide.csv", "Report_info.csv",
                     "Report_Excel.xlsx"):
            self.assertTrue(os.path.isfile(os.path.join(folder, page)), page)
        report_info = self._read_csv(os.path.join(folder, "Report_info.csv"))
        self.assertIn(["tool_author", core.PROJECT_AUTHOR], report_info)
        self.assertIn(["tool_version", core.SCANNER_VERSION], report_info)
        tree = self._read_csv(os.path.join(folder, "Tree.csv"))
        self.assertEqual(len(tree) - 1, 3)              # 表头＋3 文件
        header = tree[0]
        self.assertIn("root_label", header)
        self.assertIn("rel_path", header)
        self.assertIn("media_kind", header)
        cells = {c for row in tree[1:] for c in row}
        self.assertIn("照片.bin", cells)                 # Unicode 原样
        self.assertIn("a,b'c.txt", cells)               # 逗号转义往返
        hashes = self._read_csv(os.path.join(folder, "Hash_inventory.csv"))
        self.assertEqual(len(hashes) - 1, 3)
        self.assertIn("hash_hex", hashes[0])
        self.assertIn("origin", hashes[0])
        summary = self._read_csv(os.path.join(folder, "Summary.csv"))
        self.assertGreater(len(summary), 5)             # 键值对簿记
        with _zf.ZipFile(os.path.join(folder, "Report_Excel.xlsx")) as book:
            self.assertIn("xl/workbook.xml", book.namelist())
            for member in book.namelist():
                if member.endswith((".xml", ".rels")):
                    _et.fromstring(book.read(member))
            workbook_xml = book.read("xl/workbook.xml").decode("utf-8")
            self.assertIn("阅读说明", workbook_xml)
            self.assertIn("文件清单", workbook_xml)
            tree_sheet = book.read(
                "xl/worksheets/sheet3.xml").decode("utf-8")
            self.assertIn("文件逻辑路径", tree_sheet)
            self.assertIn("照片.bin", tree_sheet)

    def test_snapshot_export_video_gps_points(self):
        tt.write(self.old_tree, "clip.mp4", b"video-like")

        def add_gps(con):
            eid, = con.execute(
                "SELECT entry_id FROM entries WHERE rel_path='clip.mp4'"
            ).fetchone()
            con.execute(
                "INSERT INTO video_gps_points"
                " (entry_id,point_index,timestamp_seconds,gps_latitude,"
                " gps_longitude,gps_altitude,source,raw_value)"
                " VALUES (?,0,NULL,27.25,111.75,8.5,"
                " 'ffprobe:format.tags.location','+27.25+111.75+8.5/')",
                (eid,))

        snap = self.snap(self.old_tree, "gps", pre_finalize=add_gps)
        res = Export.export_snapshot(
            snap, os.path.join(self.base, "Exports"))
        rows = self._read_csv(
            os.path.join(res["folder"], "GPS_inventory_video.csv"))
        self.assertEqual(len(rows) - 1, 1)
        row = dict(zip(rows[0], rows[1]))
        self.assertEqual(row["path"], "T\\clip.mp4")
        self.assertEqual(row["point_index"], "0")
        self.assertEqual(row["timestamp_seconds"], "")
        self.assertEqual(row["gps_latitude"], "27.25")
        self.assertEqual(row["raw_value"], "+27.25+111.75+8.5/")

    def test_snapshot_export_archive_pages(self):
        import zipfile as _zf
        import zlib as _z
        data = b"member-data" * 3
        with _zf.ZipFile(os.path.join(self.old_tree, "pack.zip"), "w",
                         _zf.ZIP_DEFLATED) as z:
            z.writestr("成员.txt", data)

        def run_meta(con, tree):
            tools = {k: {"path": core.discover_tool(k, None), "version": "t"}
                     for k in ("exiftool", "ffprobe", "sevenzip")}
            meta.process_metadata_stage(
                con, tools, retain_original_metadata=False)

        snap = tt.build_snapshot(self.old_tree, self.snaps, "arc", label="T",
                                 post_enum=run_meta)
        res = Export.export_snapshot(snap, os.path.join(self.base, "Exports"))
        members = self._read_csv(
            os.path.join(res["folder"], "Archive_inventory_members.csv"))
        self.assertEqual(len(members) - 1, 1)
        row = dict(zip(members[0], members[1]))
        self.assertEqual(row["member_path"], "成员.txt")
        self.assertEqual(row["crc32_hex"], f"{_z.crc32(data):08x}")
        arc = self._read_csv(
            os.path.join(res["folder"], "Archive_inventory.csv"))
        self.assertEqual(len(arc) - 1, 1)


class TestExportDiff(_ParseFixture):
    def test_diff_export_details_and_summary(self):
        tt.write(self.old_tree, "keep.bin", b"keep-data")
        tt.write(self.old_tree, "change.bin", b"AAAA")
        tt.write(self.old_tree, "gone.bin", b"gone-data")
        self.clone()
        tt.write(self.new_tree, "change.bin", b"BBBB")
        os.remove(os.path.join(self.new_tree, "gone.bin"))
        tt.write(self.new_tree, "fresh.bin", b"fresh-data")
        s1 = self.snap(self.old_tree, "old")
        s2 = self.snap(self.new_tree, "new")
        diff_path = os.path.join(self.base, "d.diff.sqlite")
        dbdiff.compare(s1, s2, diff_path)
        res = Export.export_diff(diff_path, os.path.join(self.base, "Exports"))
        folder = res["folder"]
        with open(os.path.join(folder, "Diff_details.csv"), encoding="utf-8",
                  newline="") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 4)
        by_status = {r["status"] for r in rows}
        self.assertEqual(by_status,
                         {"unchanged", "content_changed", "deleted", "added"})
        with open(os.path.join(folder, "Diff_summary.md"),
                  encoding="utf-8") as handle:
            md = handle.read()
        self.assertIn("内容变化", md)
        self.assertIn("independent_computation", md)
        self.assertIn("hash_coverage", md)
        self.assertIn("内容维度", md)
        self.assertIn("结构维度", md)
        self.assertIn("不一致", md)         # 本场景内容与结构均有差异
        self.assertIn(core.PROJECT_AUTHOR, md)
        self.assertTrue(os.path.isfile(
            os.path.join(folder, "Report_info.csv")))
        self.assertTrue(os.path.isfile(
            os.path.join(folder, "Report_Excel.xlsx")))
        for extra in ("Diff_dirs.csv", "Diff_hash_groups.csv"):
            self.assertTrue(os.path.isfile(os.path.join(folder, extra)))

    def test_diff_summary_flags_failed_subtrees_and_propagated(self):
        tt.write(self.old_tree, "a.bin", b"prop-data")
        sa = self.snap(self.old_tree, "A")
        sb = self.snap(self.old_tree, "B", hash_mode="incremental",
                       previous_path=sa)
        diff_path = os.path.join(self.base, "p.diff.sqlite")
        dbdiff.compare(sa, sb, diff_path)
        res = Export.export_diff(diff_path, os.path.join(self.base, "Exports"))
        with open(os.path.join(res["folder"], "Diff_summary.md"),
                  encoding="utf-8") as handle:
            md = handle.read()
        self.assertIn("propagated_single_computation", md)
        self.assertIn("不构成独立验证", md)   # propagated 不得表述为已验证一致


if __name__ == "__main__":
    unittest.main(verbosity=2)
