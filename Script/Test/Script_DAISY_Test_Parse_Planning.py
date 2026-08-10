"""DAISY 档案数据解析识别、模块状态与格式计划测试。

全部夹具和产物只位于工作区 ``.test_runtime``；测试不访问快照记录的源路径，
不枚举、附加或停止其它进程。
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import sys
import tempfile
import unittest
import zlib


_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SCRIPT_DIR = os.path.dirname(_TEST_DIR)
_REPO_ROOT = os.path.dirname(_SCRIPT_DIR)
_LIB_DIR = os.path.join(_SCRIPT_DIR, "Lib")
sys.path[:0] = [_TEST_DIR, _SCRIPT_DIR, _LIB_DIR]

import Script_DAISY_Lib_Snapshot_Core as core
import Script_DAISY_Lib_Snapshot_Diff as dbdiff
import Script_DAISY_Lib_Database_Parse as dbparse
import Script_DAISY_Test_Tree as tree_fixture


_RUNTIME_ROOT = os.path.join(
    _REPO_ROOT, ".test_runtime", "v1_6_0", "parse_planning")


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _file_identity(path: str) -> tuple[str, int, int]:
    stat_result = os.stat(path)
    return _sha256(path), stat_result.st_size, stat_result.st_mtime_ns


class TestParsePlanning(unittest.TestCase):
    def setUp(self) -> None:
        os.makedirs(_RUNTIME_ROOT, exist_ok=True)
        self._td = tempfile.TemporaryDirectory(
            prefix="case_", dir=_RUNTIME_ROOT)
        self.base = self._td.name
        self.snapshots = os.path.join(self.base, "Snapshots")
        os.makedirs(self.snapshots)

    def tearDown(self) -> None:
        self._td.cleanup()

    def _snapshot(
        self,
        name: str,
        *,
        hash_mode: str = "full",
        raw_payload: bool = False,
        content: bytes = b"fixture-content",
    ) -> str:
        tree = os.path.join(self.base, "Tree_" + name)
        os.makedirs(tree)
        tree_fixture.write(tree, "中文目录/照片.bin", content)

        def add_raw_payload(con: sqlite3.Connection) -> None:
            if not raw_payload:
                return
            entry_id = int(con.execute(
                "SELECT entry_id FROM entries ORDER BY entry_id LIMIT 1"
            ).fetchone()[0])
            payload = b'{"SourceFile":"fixture.bin","Value":1}'
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
                    "13.30",
                    "2026-08-07T00:00:00.0000000Z",
                ),
            )

        return tree_fixture.build_snapshot(
            tree,
            self.snapshots,
            name,
            label="解析夹具",
            hash_mode=hash_mode,
            pre_finalize=add_raw_payload,
        )

    @staticmethod
    def _module_map(
        inspection: dbparse.ParseDatabaseInspection,
    ) -> dict[str, dbparse.ParseModuleStatus]:
        return {
            module.spec.module_id: module for module in inspection.modules
        }

    def test_schema3_inspection_and_orthogonal_plan_are_read_only(self) \
            -> None:
        snapshot = self._snapshot("FullRaw", raw_payload=True)
        identity = _file_identity(snapshot)

        inspection = dbparse.inspect_parse_database(snapshot)
        self.assertEqual(inspection.descriptor.database_type, "snapshot")
        self.assertEqual(inspection.descriptor.schema_version, 3)
        self.assertEqual(
            inspection.compatibility_mode, "v1.4.1-compatible")
        self.assertFalse(inspection.integrity_checked)
        self.assertIsNone(inspection.descriptor.sqlite_integrity)
        self.assertEqual(
            [module.spec.module_id for module in inspection.modules],
            [
                "overview", "issues", "files", "directories", "hashes",
                "photo_metadata", "video_metadata", "video_gps",
                "media_streams", "working_metadata", "document_metadata",
                "archives", "raw_payloads", "diagnostics", "run_history",
            ],
        )
        modules = self._module_map(inspection)
        self.assertEqual(modules["overview"].state, "available")
        self.assertEqual(modules["issues"].state, "empty")
        self.assertFalse(modules["issues"].selectable)
        self.assertIn("0 条记录", modules["issues"].reason)
        self.assertIn("已执行，未生成记录", modules["issues"].reason)
        self.assertNotIn("未记录原因", modules["issues"].reason)
        self.assertEqual(modules["raw_payloads"].state, "available")
        self.assertEqual(modules["run_history"].state, "available")

        human = dbparse.plan_parse_export(inspection)
        self.assertEqual(human.module_ids, ("overview",))
        self.assertEqual(human.format_ids, ("html",))
        custom = dbparse.plan_parse_export(
            inspection,
            preset="custom",
            include="files,raw_payloads",
            formats="html,jsonl",
        )
        self.assertEqual(custom.module_ids, ("files", "raw_payloads"))
        self.assertEqual(
            custom.format_modules,
            {
                "html": ("files", "raw_payloads"),
                "jsonl": ("files", "raw_payloads"),
            },
        )
        self.assertEqual(len(custom.privacy_notices), 1)

        verified = dbparse.inspect_parse_database(
            snapshot, verify_integrity=True)
        self.assertTrue(verified.integrity_checked)
        self.assertEqual(verified.descriptor.sqlite_integrity, "ok")
        self.assertEqual(_file_identity(snapshot), identity)

    def test_unrun_and_empty_modules_cannot_be_explicitly_selected(self) \
            -> None:
        no_hash = self._snapshot("NoHash", hash_mode="none")
        inspection = dbparse.inspect_parse_database(no_hash)
        modules = self._module_map(inspection)
        self.assertEqual(modules["hashes"].state, "unavailable")
        self.assertIsNone(modules["hashes"].row_count)
        self.assertIn("hash_coverage=none", modules["hashes"].reason)
        self.assertEqual(modules["raw_payloads"].state, "empty")
        self.assertEqual(modules["raw_payloads"].row_count, 0)
        with self.assertRaises(core.PreflightError) as unavailable:
            dbparse.plan_parse_export(
                inspection,
                preset="custom",
                include=("hashes",),
                formats=("csv",),
            )
        self.assertIn("本次快照未执行哈希阶段", str(unavailable.exception))
        with self.assertRaises(core.PreflightError) as empty:
            dbparse.plan_parse_export(
                inspection,
                preset="custom",
                include=("raw_payloads",),
                formats=("jsonl",),
            )
        self.assertIn("0 条记录", str(empty.exception))

    def test_full_audit_selects_only_available_modules(self) -> None:
        snapshot = self._snapshot("FullAudit", raw_payload=True)
        inspection = dbparse.inspect_parse_database(snapshot)
        plan = dbparse.plan_parse_export(
            inspection,
            preset="full-audit",
            formats=("csv", "jsonl"),
        )
        modules = self._module_map(inspection)
        self.assertTrue(plan.module_ids)
        self.assertTrue(all(
            modules[module_id].selectable for module_id in plan.module_ids))
        self.assertNotIn("issues", plan.module_ids)
        self.assertNotIn("photo_metadata", plan.module_ids)
        self.assertIn("raw_payloads", plan.module_ids)
        self.assertEqual(plan.format_ids, ("csv", "jsonl"))

    def test_diff_inspection_and_human_summary_use_diff_catalog(self) -> None:
        old_snapshot = self._snapshot("DiffOld", content=b"old")
        new_snapshot = self._snapshot("DiffNew", content=b"new-content")
        identities = {
            path: _file_identity(path)
            for path in (old_snapshot, new_snapshot)
        }
        diff_path = os.path.join(self.base, "Comparison.sqlite")
        dbdiff.compare(old_snapshot, new_snapshot, diff_path)

        inspection = dbparse.inspect_parse_database(diff_path)
        self.assertEqual(inspection.descriptor.database_type, "diff")
        self.assertEqual(
            inspection.compatibility_mode, "v1.4.1-compatible")
        self.assertEqual(
            [module.spec.module_id for module in inspection.modules],
            [
                "overview", "file_changes", "directory_changes",
                "content_groups", "enumeration_gaps", "evidence_notes",
            ],
        )
        modules = self._module_map(inspection)
        self.assertEqual(modules["file_changes"].state, "available")
        self.assertEqual(modules["enumeration_gaps"].state, "empty")
        human = dbparse.plan_parse_export(inspection)
        self.assertEqual(human.module_ids, ("overview", "evidence_notes"))
        for path, identity in identities.items():
            self.assertEqual(_file_identity(path), identity)

    def test_invalid_presets_modules_and_formats_are_explicit(self) -> None:
        snapshot = self._snapshot("InvalidOptions")
        inspection = dbparse.inspect_parse_database(snapshot)
        cases = (
            {"preset": "unknown"},
            {"preset": "custom", "include": ("not-a-module",)},
            {"formats": ("pdf",)},
            {"preset": "custom", "include": (), "formats": ("csv",)},
        )
        for options in cases:
            with self.subTest(options=options):
                with self.assertRaises(core.PreflightError):
                    dbparse.plan_parse_export(inspection, **options)


if __name__ == "__main__":
    unittest.main(verbosity=2)
