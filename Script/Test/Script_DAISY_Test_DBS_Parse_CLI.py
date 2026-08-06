"""DAISY v1.6.0 数据库解析 CLI 与旧导出隔离测试。"""
from __future__ import annotations

import csv
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import zlib


_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SCRIPT_DIR = os.path.dirname(_TEST_DIR)
_REPO_ROOT = os.path.dirname(_SCRIPT_DIR)
_LIB_DIR = os.path.join(_SCRIPT_DIR, "Lib")
sys.path[:0] = [_TEST_DIR, _SCRIPT_DIR, _LIB_DIR]

import Script_DAISY_Lib_DBS_04_Diff as dbdiff
import Script_DAISY_Test_Tree as tree_fixture


_MAIN = os.path.join(_SCRIPT_DIR, "Script_DAISY_MAIN.py")
_GUI_PREFIX = "@@DAISY_GUI@@"
_RUNTIME_ROOT = os.path.join(
    _REPO_ROOT, ".test_runtime", "v1_6_0", "parse_cli")


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _identity(path: str) -> tuple[str, int, int]:
    stat_result = os.stat(path)
    return _sha256(path), stat_result.st_size, stat_result.st_mtime_ns


class TestParseCli(unittest.TestCase):
    def setUp(self) -> None:
        os.makedirs(_RUNTIME_ROOT, exist_ok=True)
        self._td = tempfile.TemporaryDirectory(
            prefix="case_", dir=_RUNTIME_ROOT)
        self.base = self._td.name
        self.snapshots = os.path.join(self.base, "Snapshots")
        self.reports = os.path.join(self.base, "Reports")
        self.process_temp = os.path.join(self.base, "ProcessTemp")
        os.makedirs(self.snapshots)
        os.makedirs(self.reports)
        os.makedirs(self.process_temp)

    def tearDown(self) -> None:
        self._td.cleanup()

    def run_main(
        self,
        command: str,
        *args: str,
        gui_progress: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.update({
            "TEMP": self.process_temp,
            "TMP": self.process_temp,
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUTF8": "1",
        })
        if gui_progress:
            env["DAISY_GUI_PROGRESS"] = "1"
        else:
            env.pop("DAISY_GUI_PROGRESS", None)
        return subprocess.run(
            [sys.executable, "-B", _MAIN, command, *args],
            cwd=_REPO_ROOT,
            env=env,
            text=True,
            encoding="utf-8",
            errors="strict",
            capture_output=True,
            timeout=60,
            check=False,
        )

    def snapshot(
        self,
        name: str,
        *,
        content: bytes = b"fixture",
        raw_payload: bool = False,
    ) -> str:
        tree = os.path.join(self.base, "Tree_" + name)
        os.makedirs(tree)
        tree_fixture.write(
            tree,
            "中文目录/=SUM(1,1).txt",
            content,
        )

        def add_raw(con: sqlite3.Connection) -> None:
            if not raw_payload:
                return
            entry_id = int(con.execute(
                "SELECT entry_id FROM entries ORDER BY entry_id LIMIT 1"
            ).fetchone()[0])
            payload = json.dumps(
                {
                    "SourceFile": "中文目录/=SUM(1,1).txt",
                    "设备": {"型号": "测试机", "序号": 7},
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            con.execute(
                "INSERT INTO raw_payloads"
                " (entry_id,provider,payload_zlib,payload_sha256,"
                " uncompressed_bytes,provider_version,profile_version,"
                " parsed_at_utc) VALUES (?,'exiftool',?,?,?,?,1,?)",
                (
                    entry_id,
                    zlib.compress(payload),
                    hashlib.sha256(payload).hexdigest(),
                    len(payload),
                    "fixture",
                    "2026-08-07T00:00:00.0000000Z",
                ),
            )

        return tree_fixture.build_snapshot(
            tree,
            self.snapshots,
            name,
            label="解析测试",
            hash_mode="full",
            pre_finalize=add_raw,
        )

    def report_directories(self) -> list[Path]:
        return sorted(
            path for path in Path(self.reports).iterdir()
            if path.is_dir()
        )

    def test_command_help_keeps_new_and_legacy_arguments_separate(
        self,
    ) -> None:
        modern = self.run_main("parse-db", "--help")
        self.assertEqual(0, modern.returncode, modern.stderr)
        self.assertIn("--database", modern.stdout)
        self.assertIn("--preset", modern.stdout)
        self.assertIn("--include", modern.stdout)
        self.assertIn("--format", modern.stdout)
        self.assertNotIn("--snapshot", modern.stdout)

        legacy = self.run_main("export-report", "--help")
        self.assertEqual(0, legacy.returncode, legacy.stderr)
        self.assertIn("--snapshot", legacy.stdout)
        self.assertIn("--diff", legacy.stdout)
        self.assertIn("迁移提示", legacy.stdout)
        self.assertNotIn("  --database DATABASE", legacy.stdout)
        self.assertNotIn("  --include ", legacy.stdout)

    def test_snapshot_custom_four_formats_are_complete_and_read_only(
        self,
    ) -> None:
        snapshot = self.snapshot("四格式", raw_payload=True)
        before = _identity(snapshot)
        completed = self.run_main(
            "parse-db",
            "--database", snapshot,
            "--preset", "custom",
            "--include", "files,raw_payloads",
            "--format", "html",
            "--format", "xlsx",
            "--format", "csv",
            "--format", "jsonl",
            "--output-dir", self.reports,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn("已识别：封存快照 · schema 3", completed.stdout)
        self.assertIn("v1.4.1-compatible", completed.stdout)
        self.assertIn("隐私提示：", completed.stdout)
        self.assertIn("建议打开:", completed.stdout)
        self.assertEqual(before, _identity(snapshot))

        report_dirs = self.report_directories()
        self.assertEqual(1, len(report_dirs), report_dirs)
        report = report_dirs[0]
        names = {path.name for path in report.iterdir()}
        self.assertTrue({
            "Report.html",
            "Report_Excel.xlsx",
            "Report_manifest.json",
            "files.csv",
            "files.jsonl",
            "raw_payloads.jsonl",
        }.issubset(names), names)
        self.assertNotIn("raw_payloads.csv", names)

        csv_path = report / "files.csv"
        csv_bytes = csv_path.read_bytes()
        self.assertFalse(csv_bytes.startswith(b"\xef\xbb\xbf"))
        self.assertNotIn(b"\r", csv_bytes)
        with csv_path.open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual("=SUM(1,1).txt", rows[0]["name"])

        raw_lines = [
            json.loads(line)
            for line in (report / "raw_payloads.jsonl").read_text(
                encoding="utf-8").splitlines()
        ]
        self.assertEqual("测试机", raw_lines[0]["record"]["payload"][
            "设备"]["型号"])
        manifest = json.loads((report / "Report_manifest.json").read_text(
            encoding="utf-8"))
        self.assertEqual(before[0], manifest["input"]["sha256"])
        self.assertEqual(
            ["files", "raw_payloads"], manifest["plan"]["modules"])
        self.assertEqual(
            ["html", "xlsx", "csv", "jsonl"],
            manifest["plan"]["formats"],
        )

    def test_diff_database_is_auto_recognized_and_exported_read_only(
        self,
    ) -> None:
        old_snapshot = self.snapshot("Diff旧", content=b"old")
        new_snapshot = self.snapshot("Diff新", content=b"new-content")
        snapshot_identities = {
            path: _identity(path)
            for path in (old_snapshot, new_snapshot)
        }
        diff_path = os.path.join(self.base, "Comparison.sqlite")
        dbdiff.compare(old_snapshot, new_snapshot, diff_path)
        diff_identity = _identity(diff_path)

        completed = self.run_main(
            "parse-db",
            "--database", diff_path,
            "--format", "html",
            "--output-dir", self.reports,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn("已识别：Diff 数据库", completed.stdout)
        self.assertIn("本次模块：overview、evidence_notes", completed.stdout)
        self.assertEqual(diff_identity, _identity(diff_path))
        for path, identity in snapshot_identities.items():
            self.assertEqual(identity, _identity(path))

        report_dirs = self.report_directories()
        self.assertEqual(1, len(report_dirs), report_dirs)
        report = report_dirs[0]
        manifest = json.loads((report / "Report_manifest.json").read_text(
            encoding="utf-8"))
        self.assertEqual("diff", manifest["input"]["database_type"])
        html = (report / "Report.html").read_text(encoding="utf-8")
        self.assertIn("Diff", html)

    def test_invalid_inputs_and_combinations_publish_nothing(self) -> None:
        snapshot = self.snapshot("失败路径")
        unknown = os.path.join(self.base, "Unknown.sqlite")
        con = sqlite3.connect(unknown)
        try:
            con.execute("CREATE TABLE unrelated(value TEXT)")
            con.commit()
        finally:
            con.close()
        cases = (
            (
                "空自定义",
                ("parse-db", "--database", snapshot, "--preset", "custom",
                 "--format", "csv", "--output-dir", self.reports),
            ),
            (
                "未知模块",
                ("parse-db", "--database", snapshot, "--preset", "custom",
                 "--include", "not-a-module", "--format", "csv",
                 "--output-dir", self.reports),
            ),
            (
                "未知格式",
                ("parse-db", "--database", snapshot, "--format", "pdf",
                 "--output-dir", self.reports),
            ),
            (
                "未知数据库",
                ("parse-db", "--database", unknown, "--output-dir",
                 self.reports),
            ),
            (
                "混用旧参数",
                ("parse-db", "--database", snapshot, "--snapshot", snapshot,
                 "--output-dir", self.reports),
            ),
        )
        for label, command in cases:
            with self.subTest(label=label):
                completed = self.run_main(command[0], *command[1:])
                self.assertEqual(2, completed.returncode)
                self.assertEqual([], self.report_directories())

    def test_gui_progress_events_are_machine_readable_and_complete(
        self,
    ) -> None:
        snapshot = self.snapshot("进度事件")
        completed = self.run_main(
            "parse-db",
            "--database", snapshot,
            "--preset", "custom",
            "--include", "files",
            "--format", "csv",
            "--output-dir", self.reports,
            gui_progress=True,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        events = [
            json.loads(line[len(_GUI_PREFIX):])
            for line in completed.stdout.splitlines()
            if line.startswith(_GUI_PREFIX)
        ]
        self.assertEqual("progress_start", events[0]["event"])
        self.assertTrue(any(
            event["event"] == "progress_update"
            and event.get("done") == event.get("total") == 1
            for event in events
        ))
        self.assertEqual("progress_finish", events[-1]["event"])

    def test_legacy_export_report_still_uses_frozen_outputs(self) -> None:
        snapshot = self.snapshot("旧导出")
        before = _identity(snapshot)
        completed = self.run_main(
            "export-report",
            "--snapshot", snapshot,
            "--output-dir", self.reports,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn("导出目录:", completed.stdout)
        self.assertNotIn("已识别：", completed.stdout)
        self.assertEqual(before, _identity(snapshot))
        report_dirs = self.report_directories()
        self.assertEqual(1, len(report_dirs), report_dirs)
        names = {path.name for path in report_dirs[0].iterdir()}
        self.assertIn("Tree.csv", names)
        self.assertIn("Report_Excel.xlsx", names)
        self.assertNotIn("Report.html", names)
        self.assertNotIn("Report_manifest.json", names)


if __name__ == "__main__":
    unittest.main(verbosity=2)
