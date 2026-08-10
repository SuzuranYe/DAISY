"""DAISY 档案数据解析技术写入与安全发布测试。"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock
import zlib


_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SCRIPT_DIR = os.path.dirname(_TEST_DIR)
_REPO_ROOT = os.path.dirname(_SCRIPT_DIR)
_LIB_DIR = os.path.join(_SCRIPT_DIR, "Lib")
sys.path[:0] = [_TEST_DIR, _SCRIPT_DIR, _LIB_DIR]

import Script_DAISY_Lib_Snapshot_Core as core
import Script_DAISY_Lib_Database_Parse as dbparse
import Script_DAISY_Lib_Parse_Projection as projection
import Script_DAISY_Lib_Parse_Runtime as parserun
import Script_DAISY_Test_Tree as tree_fixture


_RUNTIME_ROOT = os.path.join(
    _REPO_ROOT, ".test_runtime", "v1_6_0", "parse_run")
_GENERATED_AT = "2026-08-07T01:02:03.1234567Z"


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _file_identity(path: str) -> tuple[str, int, int]:
    stat_result = os.stat(path)
    return _sha256(path), stat_result.st_size, stat_result.st_mtime_ns


def _directory_identity(path: str) -> dict[str, tuple[str, int]]:
    return {
        name: (
            _sha256(os.path.join(path, name)),
            os.path.getsize(os.path.join(path, name)),
        )
        for name in sorted(os.listdir(path))
        if os.path.isfile(os.path.join(path, name))
    }


class TestParseRun(unittest.TestCase):
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

    def _snapshot(self, name: str = "技术导出") -> str:
        tree = os.path.join(self.base, "Tree_" + name)
        os.makedirs(tree)
        tree_fixture.write(tree, "中文/=SUM(1,1).txt", b"formula-prefix")
        tree_fixture.write(tree, "普通.bin", b"normal")

        def add_raw(con: sqlite3.Connection) -> None:
            entry_id = int(con.execute(
                "SELECT entry_id FROM entries"
                " WHERE rel_path='中文\\=SUM(1,1).txt'"
            ).fetchone()[0])
            payload = json.dumps(
                {
                    "SourceFile": "中文/=SUM(1,1).txt",
                    "nested": {"中文": [1, True, None]},
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
                    "13.fixture",
                    "2026-08-07T00:00:00.0000000Z",
                ),
            )

        return tree_fixture.build_snapshot(
            tree,
            self.snapshots,
            name,
            label="技术夹具",
            hash_mode="full",
            pre_finalize=add_raw,
        )

    @staticmethod
    def _plan(
        snapshot: str,
        *,
        include: tuple[str, ...] = ("files", "raw_payloads"),
        formats: tuple[str, ...] = ("csv", "jsonl"),
    ) -> dbparse.ParseExportPlan:
        inspection = dbparse.inspect_parse_database(snapshot)
        return dbparse.plan_parse_export(
            inspection,
            preset="custom",
            include=include,
            formats=formats,
        )

    def _staging_names(self) -> list[str]:
        return [
            name for name in os.listdir(self.reports)
            if name.startswith(".daisy-parse-staging-")
        ]

    def test_csv_jsonl_manifest_are_complete_and_input_is_read_only(
        self,
    ) -> None:
        snapshot = self._snapshot()
        input_identity = _file_identity(snapshot)
        plan = self._plan(snapshot)
        original_iterator = projection.iter_module_rows
        progress = []
        with mock.patch.object(
            projection,
            "iter_module_rows",
            wraps=original_iterator,
        ) as iterator:
            result = parserun.export_technical_report(
                snapshot,
                self.reports,
                plan,
                generated_at_utc=_GENERATED_AT,
                batch_rows=1,
                progress_every_rows=1,
                progress_callback=progress.append,
            )
        self.assertEqual(iterator.call_count, 2)
        self.assertEqual(
            [call.args[2] for call in iterator.call_args_list],
            ["files", "raw_payloads"],
        )
        self.assertEqual(input_identity, _file_identity(snapshot))
        self.assertTrue(os.path.isdir(result.report_directory))
        self.assertTrue(result.report_directory.endswith(
            "_Report_20260807T010203.123456Z"))
        self.assertEqual(self._staging_names(), [])
        self.assertEqual(
            {artifact.relative_path for artifact in result.artifacts},
            {"files.csv", "files.jsonl", "raw_payloads.jsonl"},
        )

        csv_path = os.path.join(result.report_directory, "files.csv")
        with open(csv_path, "rb") as handle:
            csv_bytes = handle.read()
        self.assertFalse(csv_bytes.startswith(b"\xef\xbb\xbf"))
        self.assertNotIn(b"\r", csv_bytes)
        with open(csv_path, encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(
            tuple(rows[0]),
            projection.projection_definition("snapshot", "files").fields,
        )
        formula_row = next(
            row for row in rows if row["name"] == "=SUM(1,1).txt")
        self.assertEqual(formula_row["name"], "=SUM(1,1).txt")
        self.assertEqual(formula_row["rel_path"], "中文\\=SUM(1,1).txt")

        raw_jsonl = os.path.join(
            result.report_directory, "raw_payloads.jsonl")
        with open(raw_jsonl, encoding="utf-8") as handle:
            raw_lines = [json.loads(line) for line in handle]
        self.assertEqual(len(raw_lines), 1)
        raw_line = raw_lines[0]
        self.assertEqual(raw_line["contract"], parserun.JSONL_CONTRACT)
        self.assertEqual(raw_line["module_id"], "raw_payloads")
        self.assertEqual(
            raw_line["record"]["payload"]["nested"]["中文"],
            [1, True, None],
        )
        with open(result.manifest_path, encoding="utf-8") as handle:
            manifest = json.load(handle)
        self.assertEqual(
            raw_line["database"]["uuid"],
            manifest["input"]["uuid"],
        )

        self.assertEqual(manifest["contract"], parserun.REPORT_CONTRACT)
        self.assertEqual(manifest["input"]["sha256"], input_identity[0])
        self.assertEqual(manifest["input"]["schema_version"], 3)
        self.assertEqual(
            manifest["input"]["compatibility_mode"],
            "v1.4.1-compatible",
        )
        self.assertEqual(
            [module["module_id"] for module in manifest["modules"]],
            ["files", "raw_payloads"],
        )
        self.assertIn("CSV 保留完整原值", manifest["warnings"]["csv"])
        for artifact in manifest["artifacts"]:
            artifact_path = os.path.join(
                result.report_directory, artifact["path"])
            self.assertEqual(artifact["sha256"], _sha256(artifact_path))
            self.assertEqual(
                artifact["size_bytes"], os.path.getsize(artifact_path))
        self.assertTrue(any(item.phase == "publish" for item in progress))

    def test_existing_report_conflict_never_overwrites_and_cleans_staging(
        self,
    ) -> None:
        snapshot = self._snapshot("冲突")
        plan = self._plan(
            snapshot,
            include=("files",),
            formats=("csv",),
        )
        first = parserun.export_technical_report(
            snapshot,
            self.reports,
            plan,
            generated_at_utc=_GENERATED_AT,
        )
        first_identity = _directory_identity(first.report_directory)
        with self.assertRaises(core.PreflightError) as raised:
            parserun.export_technical_report(
                snapshot,
                self.reports,
                plan,
                generated_at_utc=_GENERATED_AT,
            )
        self.assertIn("不会覆盖", str(raised.exception))
        self.assertEqual(
            first_identity, _directory_identity(first.report_directory))
        self.assertEqual(self._staging_names(), [])
        report_dirs = [
            name for name in os.listdir(self.reports)
            if os.path.isdir(os.path.join(self.reports, name))
        ]
        self.assertEqual(len(report_dirs), 1)

    def test_cancellation_after_staging_publishes_nothing(self) -> None:
        snapshot = self._snapshot("取消")
        plan = self._plan(
            snapshot,
            include=("files",),
            formats=("csv",),
        )
        state = {"cancel": False}

        def progress(item: parserun.ParseProgress) -> None:
            if item.phase == "module":
                state["cancel"] = True

        with self.assertRaises(parserun.ParseExportCancelled):
            parserun.export_technical_report(
                snapshot,
                self.reports,
                plan,
                cancel_check=lambda: state["cancel"],
                progress_callback=progress,
                generated_at_utc=_GENERATED_AT,
            )
        self.assertEqual(os.listdir(self.reports), [])

    def test_projection_failure_publishes_nothing_and_cleans_staging(
        self,
    ) -> None:
        snapshot = self._snapshot("投影失败")
        plan = self._plan(
            snapshot,
            include=("files",),
            formats=("csv",),
        )

        def broken_rows(*_args, **_kwargs):
            yield {
                field: None
                for field in projection.projection_definition(
                    "snapshot", "files").fields
            }
            raise core.PreflightError("合成投影失败")

        with mock.patch.object(
            projection,
            "iter_module_rows",
            side_effect=broken_rows,
        ):
            with self.assertRaises(core.PreflightError) as raised:
                parserun.export_technical_report(
                    snapshot,
                    self.reports,
                    plan,
                    generated_at_utc=_GENERATED_AT,
                )
        self.assertIn("合成投影失败", str(raised.exception))
        self.assertEqual(os.listdir(self.reports), [])

    def test_input_mtime_change_during_export_refuses_publication(self) \
            -> None:
        snapshot = self._snapshot("输入变化")
        plan = self._plan(
            snapshot,
            include=("files",),
            formats=("jsonl",),
        )
        changed = {"done": False}

        def progress(item: parserun.ParseProgress) -> None:
            if (
                item.phase == "module"
                and "已完成" in item.message
                and not changed["done"]
            ):
                stat_result = os.stat(snapshot)
                os.utime(
                    snapshot,
                    ns=(
                        stat_result.st_atime_ns,
                        stat_result.st_mtime_ns + 1_000_000_000,
                    ),
                )
                changed["done"] = True

        with self.assertRaises(core.PreflightError) as raised:
            parserun.export_technical_report(
                snapshot,
                self.reports,
                plan,
                progress_callback=progress,
                generated_at_utc=_GENERATED_AT,
            )
        self.assertTrue(changed["done"])
        self.assertIn("解析前后发生变化", str(raised.exception))
        self.assertEqual(os.listdir(self.reports), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
