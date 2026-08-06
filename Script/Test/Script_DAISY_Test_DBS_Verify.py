"""DBS-31／32 共用核验输入模型的行为保持测试。"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock


_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SCRIPT_DIR = os.path.dirname(_TEST_DIR)
_REPO_ROOT = os.path.dirname(_SCRIPT_DIR)
_LIB_DIR = os.path.join(_SCRIPT_DIR, "Lib")
_MODULE_DIR = os.path.join(_SCRIPT_DIR, "Module")
sys.path[:0] = [_TEST_DIR, _SCRIPT_DIR, _LIB_DIR, _MODULE_DIR]

import Script_DAISY_Lib_DBS_01_Core as core
import Script_DAISY_Lib_DBS_06_Verify as dbverify
import Script_DAISY_Module_DBS_31_Check_Hash as hashcheck
import Script_DAISY_Module_DBS_32_Check_Format as formatcheck


_RUNTIME_ROOT = os.path.join(
    _REPO_ROOT, ".test_runtime", "v1_6_0", "verify")


def _identity(path: str) -> tuple[str, int, int]:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    stat_result = os.stat(path)
    return digest.hexdigest(), stat_result.st_size, stat_result.st_mtime_ns


class TestVerificationInputModel(unittest.TestCase):
    def setUp(self) -> None:
        os.makedirs(_RUNTIME_ROOT, exist_ok=True)
        self._td = tempfile.TemporaryDirectory(
            prefix="case_", dir=_RUNTIME_ROOT)
        self.base = self._td.name
        self.current_root = os.path.join(self.base, "current")
        os.makedirs(self.current_root)
        self.current_file = os.path.join(self.current_root, "file.bin")
        with open(self.current_file, "wb") as handle:
            handle.write(b"data")
        fixed_ns = 1_700_000_000_123_456_700
        os.utime(self.current_file, ns=(fixed_ns, fixed_ns))

    def tearDown(self) -> None:
        self._td.cleanup()

    def snapshot(
        self,
        name: str = "fixture.sqlite",
        *,
        scan_kind: str = "full",
        hash_coverage: str = "full",
    ) -> str:
        path = os.path.join(self.base, name)
        stat_result = os.stat(self.current_file)
        modified_at = core.ns_to_utc_iso(stat_result.st_mtime_ns)
        config_object = {
            "phase": scan_kind,
            "quick": scan_kind == "quick",
            "hash": hash_coverage,
            "metadata_storage": "complete",
            "data_contract": "daisy-snapshot-v3",
            "min_reader_version": "1.4.1",
        }
        connection = sqlite3.connect(path)
        try:
            connection.executescript(core.SNAPSHOT_DDL)
            connection.execute(
                "INSERT INTO snapshot_info"
                " (id,snapshot_uuid,schema_version,path_key_rule,scan_status,"
                " database_integrity,hash_coverage,started_at_utc,"
                " local_utc_offset_min,hostname,os_version,scanner_version,"
                " config_json) VALUES"
                " (1,'verify-fixture',3,1,'complete','ok',?,"
                " '2026-01-01T00:00:00.0000000Z',0,'fixture','Windows',"
                " '1.4.1',?)",
                (hash_coverage, json.dumps(config_object, ensure_ascii=False)),
            )
            connection.execute(
                "INSERT INTO roots"
                " (root_id,root_path,root_label,enum_status)"
                " VALUES (1,'X:/Recorded','夹具','ok')")
            connection.execute(
                "INSERT INTO dirs"
                " (dir_id,root_id,rel_path,path_key,enum_status,observed_at_utc)"
                " VALUES (1,1,'','','ok','2026-01-01T00:00:00.0000000Z')")
            connection.execute(
                "INSERT INTO entries"
                " (entry_id,root_id,dir_id,rel_path,path_key,name,extension,"
                " media_kind,size_bytes,modified_at_utc,attributes,"
                " observed_at_utc,meta_status,hash_status) VALUES"
                " (1,1,1,'file.bin','file.bin','file.bin','bin','other',4,?,0,"
                " '2026-01-01T00:00:00.0000000Z','not_applicable',?)",
                (modified_at,
                 "skipped" if hash_coverage == "none" else "done"),
            )
            if hash_coverage != "none":
                connection.execute(
                    "INSERT INTO hashes"
                    " (entry_id,algorithm,hash_hex,origin,size_bytes,bytes_read,"
                    " status,tool,tool_version) VALUES"
                    " (1,'sha256',?,'computed',4,4,'valid','hashlib','1')",
                    (hashlib.sha256(b"data").hexdigest(),),
                )
            manifest = {
                "data_contract": "daisy-snapshot-v3",
                "min_reader_version": "1.4.1",
                "config": config_object,
                "effective_profile": {
                    "scan_kind": scan_kind,
                    "hash_coverage_actual": hash_coverage,
                    "metadata_storage": "complete",
                    "raw_payload_retained": scan_kind == "full",
                },
            }
            connection.execute(
                "INSERT INTO snapshot_manifest"
                " (id,manifest_version,manifest_json,embedded_at_utc)"
                " VALUES (1,1,?,'2026-01-01T00:00:01.0000000Z')",
                (json.dumps(manifest, ensure_ascii=False),),
            )
            connection.execute(
                "INSERT INTO run_events"
                " (event_seq,occurred_at_utc,event,payload_json) VALUES"
                " (1,'2026-01-01T00:00:01.0000000Z','snapshot_sealed','{}')")
            counts = core.collect_snapshot_counts(connection)
            connection.execute(
                "UPDATE snapshot_info SET counts_json=? WHERE id=1",
                (json.dumps(counts, ensure_ascii=False),),
            )
            connection.commit()
        finally:
            connection.close()
        return path

    def test_common_admission_maps_roots_and_closes_connection(self) -> None:
        path = self.snapshot()
        baseline = _identity(path)
        verification = dbverify.open_verification_snapshot(
            path,
            root_map={"夹具": self.current_root},
            force=True,
            required_capabilities=("files", "hashes"),
        )
        connection = verification.connection
        with verification:
            self.assertEqual(verification.snapshot_uuid, "verify-fixture")
            self.assertEqual(verification.hash_coverage, "full")
            self.assertEqual(verification.root_labels, ("夹具",))
            self.assertEqual(
                verification.physical_path(1, "file.bin"), self.current_file)
            self.assertEqual(
                verification.logical_path(1, "file.bin"), "夹具\\file.bin")
            self.assertEqual(
                connection.execute("PRAGMA query_only").fetchone()[0], 1)
        with self.assertRaises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")
        self.assertEqual(_identity(path), baseline)

    def test_hash_and_format_use_the_same_admission_entrypoint(self) -> None:
        path = self.snapshot()
        baseline = _identity(path)
        digest = hashlib.sha256(b"data").hexdigest()
        with mock.patch.object(
            dbverify, "open_verification_snapshot",
            wraps=dbverify.open_verification_snapshot,
        ) as shared_open, mock.patch.object(
            hashcheck.dbh, "discover_powershell",
            return_value=("fixture-powershell.exe", "7.5.0"),
        ), mock.patch.object(
            hashcheck.dbh, "get_filehash_batch", return_value=[digest],
        ):
            hash_report = hashcheck.patrol(
                path,
                root_map={"夹具": self.current_root},
                full=True,
                force=True,
            )
            format_report = formatcheck.validate_snapshot(
                path,
                root_map={"夹具": self.current_root},
                report_dir=os.path.join(self.base, "format_report"),
                force=True,
            )
        self.assertEqual(shared_open.call_count, 2)
        self.assertEqual(
            shared_open.call_args_list[0].kwargs["required_capabilities"],
            ("files", "hashes"),
        )
        self.assertEqual(
            shared_open.call_args_list[1].kwargs["required_capabilities"],
            ("files",),
        )
        self.assertTrue(hash_report["ok"])
        self.assertEqual(hash_report["hash_checked"], 1)
        self.assertTrue(format_report["ok"])
        self.assertEqual(format_report["counts"], {"unsupported": 1})
        self.assertEqual(_identity(path), baseline)

    def test_quick_no_hash_rejects_hash_but_allows_format_input(self) -> None:
        path = self.snapshot(
            "quick.sqlite", scan_kind="quick", hash_coverage="none")
        with self.assertRaises(core.PreflightError) as caught:
            dbverify.open_verification_snapshot(
                path,
                root_map={"夹具": self.current_root},
                force=True,
                required_capabilities=("files", "hashes"),
            )
        self.assertIn("hashes 为 unavailable", str(caught.exception))
        with dbverify.open_verification_snapshot(
            path,
            root_map={"夹具": self.current_root},
            force=True,
            required_capabilities=("files",),
        ) as verification:
            self.assertEqual(verification.hash_coverage, "none")

    def test_mapping_failure_closes_reader_connection(self) -> None:
        path = self.snapshot()
        captured = []
        original_open = dbverify.dbreader.open_database

        def capture_open(*args, **kwargs):
            result = original_open(*args, **kwargs)
            captured.append(result[0])
            return result

        missing_root = os.path.join(self.base, "missing")
        with mock.patch.object(
            dbverify.dbreader, "open_database", side_effect=capture_open,
        ), self.assertRaises(core.PreflightError):
            dbverify.open_verification_snapshot(
                path,
                root_map={"夹具": missing_root},
                force=True,
            )
        self.assertEqual(len(captured), 1)
        with self.assertRaises(sqlite3.ProgrammingError):
            captured[0].execute("SELECT 1")

    def test_fingerprint_mismatch_is_hard_failure_even_with_force(self) -> None:
        source = self.snapshot()
        mismatched = os.path.join(self.base, "Fixture_00000000.sqlite")
        shutil.copy2(source, mismatched)
        with self.assertRaises(core.PreflightError) as caught:
            dbverify.open_verification_snapshot(
                mismatched,
                root_map={"夹具": self.current_root},
                force=True,
            )
        self.assertIn("高32bit指纹不符", str(caught.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
