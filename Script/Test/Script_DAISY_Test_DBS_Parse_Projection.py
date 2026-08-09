"""DAISY 档案数据解析稳定投影测试。

测试夹具和发布产物只位于工作区 ``.test_runtime``。测试不访问数据库中记录
的源路径，不枚举、附加或终止其它进程；所有投影均通过统一 Reader 只读连接。
"""
from __future__ import annotations

import hashlib
import json
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

import Script_DAISY_Lib_DBS_01_Core as core
import Script_DAISY_Lib_DBS_04_Diff as dbdiff
import Script_DAISY_Lib_DBS_05_Reader as dbreader
import Script_DAISY_Lib_DBS_07_Parse as dbparse
import Script_DAISY_Lib_DBS_08_State as dbstate
import Script_DAISY_Lib_DBS_09_Run as dbrun
import Script_DAISY_Lib_DBS_15_Parse_Projection as projection
import Script_DAISY_Test_Tree as tree_fixture


_RUNTIME_ROOT = os.path.join(
    _REPO_ROOT, ".test_runtime", "v1_6_0", "parse_projection")
_NOW = "2026-08-07T00:00:00.0000000Z"


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _file_identity(path: str) -> tuple[str, int, int]:
    stat_result = os.stat(path)
    return _sha256(path), stat_result.st_size, stat_result.st_mtime_ns


class TestParseProjection(unittest.TestCase):
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
        content: bytes = b"projection-fixture",
        enriched: bool = True,
    ) -> str:
        tree = os.path.join(self.base, "Tree_" + name)
        os.makedirs(tree)
        tree_fixture.write(tree, "中文目录/素材.bin", content)

        def enrich(con: sqlite3.Connection) -> None:
            if not enriched:
                return
            entry_id = int(con.execute(
                "SELECT entry_id FROM entries ORDER BY entry_id LIMIT 1"
            ).fetchone()[0])
            payload = json.dumps(
                {
                    "SourceFile": "中文目录/素材.bin",
                    "Nested": {"value": 1},
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            con.execute(
                "INSERT INTO photo_metadata"
                " (entry_id,camera_make,width,height,parser,parser_version,"
                " parsed_at_utc) VALUES (?,'Fixture',1920,1080,'fixture',"
                " '1.0',?)",
                (entry_id, _NOW),
            )
            con.execute(
                "INSERT INTO video_metadata"
                " (entry_id,container_format,duration_seconds,parser,"
                " parser_version,parsed_at_utc)"
                " VALUES (?,'fixture',1.25,'fixture','1.0',?)",
                (entry_id, _NOW),
            )
            con.execute(
                "INSERT INTO video_gps_points"
                " (entry_id,point_index,timestamp_seconds,gps_latitude,"
                " gps_longitude,source,raw_value)"
                " VALUES (?,0,0.5,25.0,121.0,'fixture','25,121')",
                (entry_id,),
            )
            con.execute(
                "INSERT INTO video_streams"
                " (entry_id,stream_index,codec_name,width,height)"
                " VALUES (?,0,'h264',1920,1080)",
                (entry_id,),
            )
            con.execute(
                "INSERT INTO audio_streams"
                " (entry_id,stream_index,codec_name,sample_rate,channels)"
                " VALUES (?,1,'aac',48000,2)",
                (entry_id,),
            )
            con.execute(
                "INSERT INTO working_metadata"
                " (entry_id,file_variant,width,height,parser,parser_version,"
                " parsed_at_utc) VALUES (?,'fixture',1920,1080,'fixture',"
                " '1.0',?)",
                (entry_id, _NOW),
            )
            con.execute(
                "INSERT INTO document_metadata"
                " (entry_id,doc_format,title,page_count,parser,parser_version,"
                " parsed_at_utc) VALUES (?,'fixture','中文标题',1,'fixture',"
                " '1.0',?)",
                (entry_id, _NOW),
            )
            con.execute(
                "INSERT INTO archive_metadata"
                " (entry_id,archive_format,member_count,parser,parser_version,"
                " parsed_at_utc) VALUES (?,'zip',1,'fixture','1.0',?)",
                (entry_id, _NOW),
            )
            con.execute(
                "INSERT INTO archive_members"
                " (entry_id,member_index,member_path,is_dir,size_bytes)"
                " VALUES (?,0,'成员.txt',0,3)",
                (entry_id,),
            )
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
                    _NOW,
                ),
            )
            con.execute(
                "INSERT INTO metadata_diagnostics"
                " (entry_id,provider,severity,diagnostic_code,field_name,"
                " message,raw_value,observed_at_utc)"
                " VALUES (?,'fixture','warning','fixture_warning','Field',"
                " '合成诊断','raw',?)",
                (entry_id, _NOW),
            )
            con.execute(
                "INSERT INTO errors"
                " (entry_id,stage,error_code,message,occurred_at_utc)"
                " VALUES (?,'metadata','fixture_error','合成问题',?)",
                (entry_id, _NOW),
            )

        return tree_fixture.build_snapshot(
            tree,
            self.snapshots,
            name,
            label="解析夹具",
            hash_mode="full",
            pre_finalize=enrich,
        )

    def _schema4_snapshot(self) -> str:
        tree = os.path.join(self.base, "Tree_Schema4")
        os.makedirs(tree)
        tree_fixture.write(tree, "运行历史.bin", b"schema4-history")
        partial = os.path.join(
            self.snapshots, "Schema4.partial.sqlite")
        publish_stem = os.path.join(self.snapshots, "Schema4")
        handle = dbrun.create_run(
            partial,
            [("运行夹具", tree)],
            {
                "phase": "full",
                "hash": "full",
                "metadata_storage": "complete",
                "format_validation": "all",
            },
            output_dir=self.snapshots,
            publish_stem_path=publish_stem,
            tool_versions={},
        )
        lease_path = handle.lease_path
        lease_id = handle.lease.lease_id
        con = handle.connection
        try:
            core.enumerate_and_reconcile(con)
            entry_id = int(con.execute(
                "SELECT entry_id FROM entries ORDER BY entry_id LIMIT 1"
            ).fetchone()[0])
            con.execute("UPDATE entries SET meta_status='done'")
            attempt_id = dbstate.start_attempt(
                con,
                entry_id,
                "hash",
                tool_name="hashlib",
                tool_version="fixture",
            )
            dbstate.finish_attempt(
                con,
                attempt_id,
                "succeeded",
                performance={
                    "origin": "computed",
                    "size_bytes": len(b"schema4-history"),
                    "bytes_read": len(b"schema4-history"),
                    "elapsed_seconds": 0.01,
                    "active_read_seconds": 0.01,
                    "stall_count": 0,
                    "longest_stall_seconds": 0.0,
                    "first_stall_offset": None,
                    "last_stall_offset": None,
                    "final_offset": len(b"schema4-history"),
                    "ended_reason": "completed",
                    "candidate_confidence": "none",
                    "candidate_reason": None,
                },
            )
            format_attempt = dbstate.start_attempt(
                con,
                entry_id,
                "format",
                coverage="full",
                validator="fixture",
                tool_name="daisy-format",
                tool_version="fixture",
            )
            dbstate.finish_attempt(
                con,
                format_attempt,
                "succeeded",
                end_reason="format_valid",
                stat_match=True,
            )
            con.execute(
                "UPDATE entries SET hash_status='skipped' WHERE entry_id=?",
                (entry_id,),
            )
            con.execute(
                "UPDATE snapshot_info SET hash_coverage='full' WHERE id=1")
            con.commit()
            dbstate.begin_sealing(con)
            dbstate.mark_sealed_unpublished(con)
        finally:
            dbrun.close_handle(handle, release_lease=False)
        result = dbstate.publish_sealed_snapshot(
            partial,
            os.path.join(self.snapshots, "Schema4.publish.sqlite"),
            lease_path=lease_path,
            lease_id=lease_id,
        )
        return result.final_path

    def test_schema3_all_modules_have_exact_stable_fields_and_are_read_only(
        self,
    ) -> None:
        snapshot = self._snapshot("AllModules")
        identity = _file_identity(snapshot)
        con, descriptor = dbreader.open_database(snapshot)
        try:
            statuses = {
                status.spec.module_id: status
                for status in dbparse.parse_module_statuses(descriptor)
            }
            self.assertEqual(set(statuses), {
                item.module_id
                for item in projection.projection_catalog("snapshot")
            })
            self.assertTrue(all(
                status.selectable for status in statuses.values()))
            for module_id, status in statuses.items():
                with self.subTest(module=module_id):
                    definition = projection.projection_definition(
                        "snapshot", module_id)
                    rows = list(projection.iter_module_rows(
                        con,
                        descriptor,
                        module_id,
                        batch_rows=1,
                    ))
                    self.assertTrue(rows, status.reason)
                    self.assertTrue(all(
                        tuple(row) == definition.fields for row in rows))
                    self.assertTrue(all(
                        "entry_id" not in row for row in rows))
            raw = list(projection.iter_module_rows(
                con, descriptor, "raw_payloads", batch_rows=1))
            self.assertEqual(raw[0]["payload"]["Nested"]["value"], 1)
            history = list(projection.iter_module_rows(
                con, descriptor, "run_history", batch_rows=1))
            self.assertTrue({
                "manifest", "run_event",
            }.issubset({str(row["record_type"]) for row in history}))
            self.assertTrue(all(
                "entry_id" not in json.loads(str(row["data_json"]))
                for row in history
            ))
        finally:
            con.close()
        self.assertEqual(identity, _file_identity(snapshot))

    def test_raw_payload_validation_rejects_each_corruption_class(self) \
            -> None:
        invalid_json = b"not-json"
        cases = (
            (
                "zlib",
                "UPDATE raw_payloads SET payload_zlib=?",
                (sqlite3.Binary(b"not-zlib"),),
                "无法解压",
            ),
            (
                "length",
                "UPDATE raw_payloads SET uncompressed_bytes="
                "uncompressed_bytes+1",
                (),
                "长度不符",
            ),
            (
                "sha256",
                "UPDATE raw_payloads SET payload_sha256=?",
                ("0" * 64,),
                "SHA-256 不符",
            ),
            (
                "json",
                "UPDATE raw_payloads SET payload_zlib=?,payload_sha256=?,"
                "uncompressed_bytes=?",
                (
                    sqlite3.Binary(zlib.compress(invalid_json)),
                    hashlib.sha256(invalid_json).hexdigest(),
                    len(invalid_json),
                ),
                "UTF-8 JSON",
            ),
        )
        for name, sql, parameters, message in cases:
            with self.subTest(corruption=name):
                snapshot = self._snapshot("Raw_" + name)
                writable = sqlite3.connect(snapshot)
                try:
                    writable.execute(sql, parameters)
                    writable.commit()
                finally:
                    writable.close()
                con, descriptor = dbreader.open_database(
                    snapshot,
                    verify_artifact_fingerprint=False,
                )
                try:
                    with self.assertRaises(core.PreflightError) as raised:
                        list(projection.iter_module_rows(
                            con, descriptor, "raw_payloads"))
                    self.assertIn(message, str(raised.exception))
                finally:
                    con.close()

    def test_projection_cancellation_and_batch_validation_are_explicit(
        self,
    ) -> None:
        snapshot = self._snapshot("Cancel")
        con, descriptor = dbreader.open_database(snapshot)
        try:
            with self.assertRaises(ValueError):
                list(projection.iter_module_rows(
                    con, descriptor, "files", batch_rows=0))
            with self.assertRaises(projection.ParseProjectionCancelled):
                list(projection.iter_module_rows(
                    con,
                    descriptor,
                    "files",
                    cancel_check=lambda: True,
                ))
            with self.assertRaises(core.PreflightError):
                list(projection.iter_module_rows(
                    con, descriptor, "not-a-module"))
        finally:
            con.close()

    def test_schema4_run_history_uses_logical_keys_not_entry_ids(self) \
            -> None:
        snapshot = self._schema4_snapshot()
        identity = _file_identity(snapshot)
        con, descriptor = dbreader.open_database(snapshot)
        try:
            self.assertEqual(descriptor.schema_version, 4)
            rows = list(projection.iter_module_rows(
                con, descriptor, "run_history", batch_rows=1))
        finally:
            con.close()
        self.assertEqual(identity, _file_identity(snapshot))
        record_types = {str(row["record_type"]) for row in rows}
        self.assertTrue({
            "session", "entry_attempt", "read_performance",
            "format_check", "state_event", "stage_checkpoint", "runtime",
        }.issubset(record_types))
        for row in rows:
            with self.subTest(record_type=row["record_type"]):
                payload = json.loads(str(row["data_json"]))
                self.assertNotIn("entry_id", payload)
                if row["record_type"] in (
                    "entry_attempt", "read_performance", "format_check",
                ):
                    self.assertEqual(
                        row["entry_path"], "运行夹具\\运行历史.bin")
                    self.assertIn("运行夹具\\运行历史.bin", str(
                        row["record_key"]))

    def test_diff_modules_use_exact_fields_and_preserve_all_inputs(self) \
            -> None:
        old_snapshot = self._snapshot(
            "DiffOld", content=b"old", enriched=False)
        new_snapshot = self._snapshot(
            "DiffNew", content=b"new", enriched=False)
        identities = {
            path: _file_identity(path)
            for path in (old_snapshot, new_snapshot)
        }
        diff_path = os.path.join(self.base, "Projection_Diff.sqlite")
        dbdiff.compare(old_snapshot, new_snapshot, diff_path)
        identities[diff_path] = _file_identity(diff_path)

        con, descriptor = dbreader.open_database(diff_path)
        try:
            statuses = {
                status.spec.module_id: status
                for status in dbparse.parse_module_statuses(descriptor)
            }
            self.assertEqual(set(statuses), {
                item.module_id for item in projection.projection_catalog(
                    "diff")
            })
            seen = set()
            for module_id, status in statuses.items():
                if not status.selectable:
                    continue
                with self.subTest(module=module_id):
                    definition = projection.projection_definition(
                        "diff", module_id)
                    rows = list(projection.iter_module_rows(
                        con, descriptor, module_id, batch_rows=1))
                    self.assertTrue(rows)
                    self.assertTrue(all(
                        tuple(row) == definition.fields for row in rows))
                    seen.add(module_id)
            self.assertTrue({
                "overview", "file_changes", "directory_changes",
                "evidence_notes",
            }.issubset(seen))
            overview = list(projection.iter_module_rows(
                con, descriptor, "overview", batch_rows=1))
            overview_by_key = {
                str(row["key"]): row for row in overview
            }
            self.assertEqual(
                overview_by_key["schema_version"]["label"],
                "数据库结构版本",
            )
            self.assertEqual(
                overview_by_key["mode"]["value"],
                "v1.4.1-compatible",
            )
            self.assertEqual(
                overview_by_key["content_changed"]["label"],
                "内容变化",
            )
            self.assertEqual(
                statuses["enumeration_gaps"].state, "empty")
            with self.assertRaises(core.PreflightError):
                list(projection.iter_module_rows(
                    con, descriptor, "enumeration_gaps"))
        finally:
            con.close()
        for path, identity in identities.items():
            self.assertEqual(identity, _file_identity(path))


if __name__ == "__main__":
    unittest.main(verbosity=2)
