"""DBS 统一只读 Reader 的 v1.4.1/schema 3 兼容测试。"""
from __future__ import annotations

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
_MODULE_DIR = os.path.join(_SCRIPT_DIR, "Module")
_FIXTURE_DIR = os.path.join(_TEST_DIR, "Fixtures")
sys.path[:0] = [_TEST_DIR, _SCRIPT_DIR, _LIB_DIR, _MODULE_DIR]

import Script_DAISY_Lib_DBS_01_Core as core
import Script_DAISY_Lib_DBS_04_Diff as dbdiff
import Script_DAISY_Lib_DBS_05_Reader as reader
import Script_DAISY_Lib_DBS_08_State as state
import Script_DAISY_Module_DBS_31_Check_Hash as hashcheck
import Script_DAISY_Module_DBS_32_Check_Format as formatcheck
import Script_DAISY_Module_DBS_41_Export_Report as exportreport


_RUNTIME_ROOT = os.path.join(
    _REPO_ROOT, ".test_runtime", "v1_6_0", "reader")
_CONTRACT_PATH = os.path.join(
    _FIXTURE_DIR, "DBS_v1_4_1_Schema3_Contract.json")


def _contract() -> dict:
    with open(_CONTRACT_PATH, encoding="utf-8") as handle:
        return json.load(handle)


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _file_identity(path: str) -> tuple[str, int, int]:
    stat_result = os.stat(path)
    return _sha256(path), stat_result.st_size, stat_result.st_mtime_ns


class _ReaderFixture(unittest.TestCase):
    def setUp(self) -> None:
        os.makedirs(_RUNTIME_ROOT, exist_ok=True)
        self._td = tempfile.TemporaryDirectory(
            prefix="case_", dir=_RUNTIME_ROOT)
        self.base = self._td.name

    def tearDown(self) -> None:
        self._td.cleanup()

    def snapshot(
        self,
        name: str = "v141_中文#sample.sqlite",
        *,
        status: str = "complete",
        integrity: str = "ok",
        schema_version: int = 3,
        source_version: str = "1.4.1",
        scan_kind: str = "full",
        hash_coverage: str = "full",
        metadata_storage: str = "complete",
        include_raw: bool = False,
        duplicate_content: bool = False,
        root_path: str = "X:/Fixture",
        modified_at_utc: str = "2026-01-01T00:00:00.0000000Z",
    ) -> str:
        path = os.path.join(self.base, name)
        con = sqlite3.connect(path)
        try:
            con.executescript(core.SNAPSHOT_DDL)
            config_object = {
                "phase": scan_kind,
                "quick": scan_kind == "quick",
                "hash": hash_coverage,
                "metadata_storage": metadata_storage,
                "no_file_id": False,
                "data_contract": "daisy-snapshot-v3",
                "min_reader_version": "1.4.1",
            }
            config = json.dumps(config_object, ensure_ascii=False)
            con.execute(
                "INSERT INTO snapshot_info"
                " (id,snapshot_uuid,schema_version,path_key_rule,scan_status,"
                " database_integrity,hash_coverage,started_at_utc,"
                " local_utc_offset_min,hostname,os_version,scanner_version,"
                " config_json) VALUES (1,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "snapshot-v141-" + hashlib.sha256(
                        name.encode("utf-8")).hexdigest()[:12],
                    schema_version, core.PATH_KEY_RULE,
                    status, integrity, hash_coverage,
                    "2026-01-01T00:00:00.0000000Z",
                    0, "fixture", "Windows", source_version, config,
                ),
            )
            con.execute(
                "INSERT INTO roots"
                " (root_id,root_path,root_label,enum_status)"
                " VALUES (1,?,'夹具','ok')", (root_path,))
            con.execute(
                "INSERT INTO dirs"
                " (dir_id,root_id,rel_path,path_key,enum_status,observed_at_utc)"
                " VALUES (1,1,'','','ok','2026-01-01T00:00:00.0000000Z')")
            con.execute(
                "INSERT INTO entries"
                " (entry_id,root_id,dir_id,rel_path,path_key,name,extension,"
                " media_kind,size_bytes,modified_at_utc,attributes,"
                " observed_at_utc,meta_status,hash_status)"
                " VALUES (1,1,1,'照片.bin','照片.bin','照片.bin','bin','other',"
                " 4,?,0,'2026-01-01T00:00:00.0000000Z',?,?)",
                (
                    modified_at_utc,
                    "not_applicable",
                    "skipped" if hash_coverage == "none" else "done",
                ),
            )
            if duplicate_content:
                con.execute(
                    "INSERT INTO entries"
                    " (entry_id,root_id,dir_id,rel_path,path_key,name,extension,"
                    " media_kind,size_bytes,modified_at_utc,attributes,"
                    " observed_at_utc,meta_status,hash_status)"
                    " VALUES (2,1,1,'副本.bin','副本.bin','副本.bin','bin',"
                    " 'other',4,?,0,'2026-01-01T00:00:00.0000000Z',?,?)",
                    (
                        modified_at_utc,
                        "not_applicable",
                        "skipped" if hash_coverage == "none" else "done",
                    ),
                )
            if hash_coverage != "none":
                for entry_id in range(1, 3 if duplicate_content else 2):
                    con.execute(
                        "INSERT INTO hashes"
                        " (entry_id,algorithm,hash_hex,origin,size_bytes,"
                        " bytes_read,status,tool,tool_version)"
                        " VALUES (?,'sha256',?,'computed',4,4,'valid',"
                        " 'hashlib','1')",
                        (entry_id, "ab" * 32),
                    )
            raw_retained = (
                scan_kind == "full" and metadata_storage == "complete")
            if include_raw:
                if not raw_retained:
                    raise ValueError("只有 Full／complete 金样可包含 raw payload")
                payload = b'{"SourceFile":"fixture.bin","Value":1}'
                con.execute(
                    "INSERT INTO raw_payloads"
                    " (entry_id,provider,payload_zlib,payload_sha256,"
                    " uncompressed_bytes,provider_version,profile_version,"
                    " parsed_at_utc) VALUES (1,'exiftool',?,?,?,?,1,?)",
                    (
                        zlib.compress(payload), hashlib.sha256(payload).hexdigest(),
                        len(payload), "13.30",
                        "2026-01-01T00:00:00.0000000Z",
                    ),
                )
            con.execute(
                "INSERT INTO run_events"
                " (event_seq,occurred_at_utc,event,payload_json)"
                " VALUES (1,'2026-01-01T00:00:00.0000000Z','snapshot_sealed','{}')")
            counts = core.collect_snapshot_counts(con)
            con.execute(
                "UPDATE snapshot_info SET has_file_issues=?,"
                " has_unstable_entries=?,has_enumeration_gaps=?,counts_json=?"
                " WHERE id=1",
                (
                    int(counts["has_file_issues"]),
                    int(counts["has_unstable_entries"]),
                    int(counts["has_enumeration_gaps"]),
                    json.dumps(counts, ensure_ascii=False),
                ),
            )
            manifest = json.dumps({
                "data_contract": "daisy-snapshot-v3",
                "min_reader_version": "1.4.1",
                "config": config_object,
                "effective_profile": {
                    "scan_kind": scan_kind,
                    "hash_mode_requested": hash_coverage,
                    "hash_coverage_actual": hash_coverage,
                    "metadata_storage": metadata_storage,
                    "raw_payload_retained": raw_retained,
                    "raw_payload_rows": 1 if include_raw else 0,
                },
            }, ensure_ascii=False)
            con.execute(
                "INSERT INTO snapshot_manifest"
                " (id,manifest_version,manifest_json,embedded_at_utc)"
                " VALUES (1,1,?,?)",
                (manifest, "2026-01-01T00:00:01.0000000Z"),
            )
            con.commit()
        finally:
            con.close()
        return path

    def snapshot_v4(
        self,
        *,
        format_validation: str = "off",
        publish: bool = True,
    ) -> str:
        partial = os.path.join(self.base, "v160_Full.partial.sqlite")
        publish_stem = os.path.join(
            self.base, "v160_Full_20260806_080000")
        event_path = os.path.join(self.base, "v160_Full.events.jsonl")
        con = sqlite3.connect(partial)
        try:
            state.initialize_v4_connection(
                con,
                [("夹具", os.path.join(self.base, "archive"))],
                {
                    "phase": "full",
                    "hash": "full",
                    "metadata_storage": "complete",
                    "format_validation": format_validation,
                },
                output_dir=self.base,
                partial_path=partial,
                publish_stem_path=publish_stem,
                event_log_path=event_path,
                snapshot_uuid="a" * 32,
                session_id="b" * 32,
                lease_id="c" * 32,
                hostname="fixture-host",
                pid=4242,
                process_start_token="fixture-start",
                now_utc="2026-08-06T00:00:00.000000Z",
            )
            state.begin_sealing(
                con, now_utc="2026-08-06T00:01:00.000000Z")
            state.mark_sealed_unpublished(
                con, now_utc="2026-08-06T00:01:00.000000Z")
        finally:
            con.close()
        if not publish:
            return partial
        staging = os.path.join(self.base, "v160.publish.partial.sqlite")
        result = state.publish_sealed_snapshot(
            partial,
            staging,
            now_utc="2026-08-06T00:01:00.000000Z",
        )
        return result.final_path

    def diff(self, name: str = "v141_diff.sqlite") -> str:
        path = os.path.join(self.base, name)
        con = sqlite3.connect(path)
        try:
            con.executescript(dbdiff.DIFF_DDL)
            con.execute(
                "INSERT INTO diff_info"
                " (id,diff_uuid,schema_version,old_schema_version,"
                " new_schema_version,old_snapshot_uuid,new_snapshot_uuid,"
                " old_snapshot_file,new_snapshot_file,old_hash_coverage,"
                " new_hash_coverage,root_mapping_json,forced,tool_version,"
                " created_at_utc,counts_json)"
                " VALUES (1,'diff-v141',3,3,3,'old','new','old.sqlite',"
                " 'new.sqlite','full','full','{}',0,'1.4.1',"
                " '2026-01-01T00:00:00.0000000Z','{}')")
            con.execute(
                "INSERT INTO diff_entries"
                " (diff_entry_id,path_key,old_root_label,new_root_label,"
                " old_rel_path,new_rel_path,status,evidence)"
                " VALUES (1,'a','夹具','夹具','a','a','unchanged',"
                " 'independent_computation')")
            con.commit()
        finally:
            con.close()
        return path

    def add_issue_evidence(self, path: str) -> None:
        con = sqlite3.connect(path)
        try:
            con.execute(
                "UPDATE entries SET meta_status='error' WHERE entry_id=1")
            con.execute(
                "UPDATE entries SET hash_status='unstable' WHERE entry_id=2")
            con.execute(
                "UPDATE hashes SET status='unstable',failure_reason='changed'"
                " WHERE entry_id=2")
            con.execute(
                "INSERT INTO dirs"
                " (dir_id,root_id,rel_path,path_key,enum_status,error_message,"
                " observed_at_utc) VALUES"
                " (2,1,'受限','受限','access_denied','拒绝访问',"
                " '2026-01-01T00:00:00.0000000Z')")
            con.execute(
                "INSERT INTO entries"
                " (entry_id,root_id,dir_id,rel_path,path_key,name,extension,"
                " media_kind,size_bytes,modified_at_utc,attributes,"
                " observed_at_utc,meta_status,hash_status) VALUES"
                " (3,1,1,'未知.bin','未知.bin','未知.bin','bin','other',0,"
                " '2026-01-01T00:00:00.0000000Z',0,"
                " '2026-01-01T00:00:00.0000000Z','error','done')")
            con.execute(
                "INSERT INTO hashes"
                " (entry_id,algorithm,hash_hex,origin,size_bytes,bytes_read,"
                " status,tool,tool_version) VALUES"
                " (3,'sha256',?,'computed',0,0,'valid','hashlib','1')",
                (hashlib.sha256(b"").hexdigest(),),
            )
            con.executemany(
                "INSERT INTO errors"
                " (entry_id,stage,error_code,message,occurred_at_utc)"
                " VALUES (?,?,?,?,?)",
                (
                    (
                        1, "metadata", "metadata_read_failed", "解析失败",
                        "2026-01-01T00:00:00.0000000Z",
                    ),
                    (
                        3, "metadata", "exiftool_reported_error",
                        "Unknown file type",
                        "2026-01-01T00:00:00.0000000Z",
                    ),
                ),
            )
            counts = core.collect_snapshot_counts(con)
            con.execute(
                "UPDATE snapshot_info SET has_file_issues=?,"
                " has_unstable_entries=?,has_enumeration_gaps=?,counts_json=?"
                " WHERE id=1",
                (
                    int(counts["has_file_issues"]),
                    int(counts["has_unstable_entries"]),
                    int(counts["has_enumeration_gaps"]),
                    json.dumps(counts, ensure_ascii=False),
                ),
            )
            con.commit()
        finally:
            con.close()


class TestDatabaseReader(_ReaderFixture):
    def test_v141_contract_hashes_and_tables_are_frozen(self) -> None:
        contract = _contract()
        self.assertEqual(contract["source_application_version"], "1.4.1")
        self.assertEqual(contract["source_git_tag"], "v1.4.1")
        self.assertEqual(len(contract["deterministic_fixture_profiles"]), 5)
        self.assertEqual(contract["schema_version"], 3)
        self.assertEqual(
            hashlib.sha256(core.SNAPSHOT_DDL.encode("utf-8")).hexdigest(),
            contract["snapshot_ddl_sha256"],
        )
        self.assertEqual(
            hashlib.sha256(dbdiff.DIFF_DDL.encode("utf-8")).hexdigest(),
            contract["diff_ddl_sha256"],
        )
        snapshot = reader.inspect_database(self.snapshot())
        diff = reader.inspect_database(self.diff())
        self.assertEqual(
            sorted(snapshot.tables), contract["snapshot_tables"])
        self.assertEqual(sorted(diff.tables), contract["diff_tables"])

    def test_v141_snapshot_identity_and_capabilities(self) -> None:
        path = self.snapshot()
        before = _sha256(path)
        descriptor = reader.inspect_database(path, expected_type="snapshot")
        after = _sha256(path)

        self.assertEqual(before, after)
        self.assertEqual(descriptor.database_type, "snapshot")
        self.assertEqual(descriptor.schema_version, 3)
        self.assertEqual(descriptor.source_version, "1.4.1")
        self.assertEqual(descriptor.lifecycle, "sealed")
        self.assertEqual(descriptor.database_integrity, "ok")
        self.assertEqual(descriptor.sqlite_integrity, "ok")
        self.assertEqual(descriptor.data_contract, "daisy-snapshot-v3")
        self.assertEqual(descriptor.min_reader_version, "1.4.1")
        self.assertEqual(descriptor.capability("files").state, "available")
        self.assertEqual(descriptor.capability("files").row_count, 1)
        self.assertEqual(descriptor.capability("raw_payloads").state, "empty")
        self.assertEqual(
            descriptor.capability("format_checks").state, "unavailable")
        self.assertIsNone(
            descriptor.capability("format_checks").row_count)
        self.assertEqual(
            descriptor.capability("read_performance").state, "unavailable")
        accepted, = reader.require_capabilities(descriptor, "raw_payloads")
        self.assertEqual(accepted.state, "empty")
        with self.assertRaises(core.PreflightError):
            reader.require_capabilities(descriptor, "format_checks")
        contract = _contract()
        for capability_id in contract["schema3_snapshot_capabilities"]:
            self.assertIn(
                descriptor.capability(capability_id).state,
                ("available", "empty"),
                capability_id,
            )
        for capability_id in contract["future_snapshot_capabilities"]:
            self.assertEqual(
                descriptor.capability(capability_id).state,
                "unavailable",
                capability_id,
            )

    def test_v141_quick_and_no_hash_do_not_fake_unrun_modules(self) -> None:
        quick = reader.inspect_database(self.snapshot(
            "v141_quick.sqlite",
            scan_kind="quick",
            hash_coverage="none",
        ))
        self.assertEqual(quick.identity["scan_kind"], "quick")
        self.assertEqual(quick.capability("files").state, "available")
        for capability_id in (
            "hashes", "photo_metadata", "video_metadata", "video_gps",
            "media_streams", "working_metadata", "document_metadata",
            "archives", "raw_payloads",
        ):
            capability = quick.capability(capability_id)
            self.assertEqual(capability.state, "unavailable", capability_id)
            self.assertIsNone(capability.row_count, capability_id)
        with self.assertRaises(core.PreflightError):
            reader.require_capabilities(quick, "hashes")
        queryable = reader.require_queryable_capabilities(
            quick, "hashes", "raw_payloads")
        self.assertEqual(
            [capability.state for capability in queryable],
            ["unavailable", "unavailable"],
        )

        no_hash = reader.inspect_database(self.snapshot(
            "v141_full_no_hash.sqlite",
            scan_kind="full",
            hash_coverage="none",
            metadata_storage="normalized",
        ))
        self.assertEqual(no_hash.capability("hashes").state, "unavailable")
        self.assertEqual(
            no_hash.capability("raw_payloads").state, "unavailable")
        self.assertEqual(
            no_hash.capability("photo_metadata").state, "empty")

    def test_v141_full_raw_payload_and_duplicate_content_are_readable(
        self,
    ) -> None:
        path = self.snapshot(
            "v141_full_evidence.sqlite",
            include_raw=True,
            duplicate_content=True,
        )
        before = _file_identity(path)
        con, descriptor = reader.open_database(path)
        try:
            self.assertEqual(descriptor.capability("hashes").row_count, 2)
            self.assertEqual(
                descriptor.capability("raw_payloads").state, "available")
            duplicate_count, = con.execute(
                "SELECT COUNT(*) FROM hashes WHERE hash_hex=?",
                ("ab" * 32,),
            ).fetchone()
            payload_zlib, payload_sha256 = con.execute(
                "SELECT payload_zlib,payload_sha256 FROM raw_payloads"
            ).fetchone()
            payload = zlib.decompress(payload_zlib)
            self.assertEqual(duplicate_count, 2)
            self.assertEqual(
                hashlib.sha256(payload).hexdigest(), payload_sha256)
        finally:
            con.close()
        self.assertEqual(_file_identity(path), before)

    def test_v141_issue_golden_keeps_unsupported_as_count_only(self) -> None:
        path = self.snapshot(
            "v141_issues.sqlite", duplicate_content=True)
        self.add_issue_evidence(path)
        before = _file_identity(path)
        descriptor = reader.inspect_database(path)
        report = core.render_snapshot_issue_report(
            path, artifact_filename="v141_issues.sqlite")
        self.assertGreater(descriptor.capability("issues").row_count, 0)
        self.assertIsNotNone(report)
        self.assertIn("## 目录枚举问题", report)
        self.assertIn("## 问题条目状态", report)
        self.assertIn("## 错误明细", report)
        self.assertIn("未列为问题的格式未识别文件 | 1", report)
        self.assertIn("照片.bin", report)
        self.assertIn("副本.bin", report)
        self.assertNotIn("未知.bin", report)
        self.assertEqual(_file_identity(path), before)

    def test_schema_drives_compatibility_not_application_string(self) -> None:
        descriptor = reader.inspect_database(
            self.snapshot(source_version="99.123-test"))
        self.assertEqual(descriptor.schema_version, 3)
        self.assertEqual(descriptor.source_version, "99.123-test")

    def test_open_connection_enforces_query_only(self) -> None:
        path = self.snapshot()
        con, descriptor = reader.open_database(path)
        try:
            self.assertTrue(descriptor.sealed)
            self.assertEqual(con.execute("PRAGMA query_only").fetchone()[0], 1)
            with self.assertRaises(sqlite3.OperationalError):
                con.execute(
                    "UPDATE snapshot_info SET scanner_version='changed'")
        finally:
            con.close()

    def test_partial_is_identified_but_rejected_by_sealed_consumers(self) -> None:
        path = self.snapshot(
            "resume.partial.sqlite", status="interrupted", integrity="pending")
        probe = reader.probe_database(path)
        self.assertFalse(probe.valid)
        self.assertIn("尚未完整封存", probe.error)

        descriptor = reader.inspect_database(path, require_sealed=False)
        self.assertEqual(descriptor.kind, "partial")
        self.assertEqual(descriptor.lifecycle, "partial")
        self.assertEqual(descriptor.database_integrity, "pending")
        self.assertEqual(descriptor.sqlite_integrity, "ok")
        self.assertEqual(descriptor.capability("files").state, "invalid")

    def test_unknown_and_wrong_type_are_clear_invalid_results(self) -> None:
        unknown = os.path.join(self.base, "unknown.sqlite")
        con = sqlite3.connect(unknown)
        con.execute("CREATE TABLE anything (value TEXT)")
        con.close()
        probe = reader.probe_database(unknown)
        self.assertFalse(probe.valid)
        self.assertIn("无法识别 DAISY 数据库", probe.error)

        diff_path = self.diff()
        mismatch = reader.probe_database(
            diff_path, expected_type="snapshot")
        self.assertFalse(mismatch.valid)
        self.assertIn("数据库类型不符", mismatch.error)

    def test_unsupported_schema_is_not_guessed_from_source_version(self) -> None:
        path = self.snapshot(schema_version=4, source_version="1.6.0")
        probe = reader.probe_database(path)
        self.assertFalse(probe.valid)
        self.assertIn("schema_version=4", probe.error)

    def test_schema4_published_identity_and_capabilities(self) -> None:
        path = self.snapshot_v4(format_validation="off")
        descriptor = reader.inspect_database(path)
        self.assertEqual(4, descriptor.schema_version)
        self.assertEqual("sealed", descriptor.lifecycle)
        self.assertEqual("published", descriptor.status)
        self.assertEqual("daisy-snapshot-v4", descriptor.data_contract)
        self.assertEqual("published", descriptor.identity["run_state"])
        self.assertEqual(
            "daisy-resume-v1", descriptor.identity["resume_contract"])
        self.assertEqual(
            "available", descriptor.capability("run_sessions").state)
        format_capability = descriptor.capability("format_checks")
        self.assertEqual("unavailable", format_capability.state)
        self.assertIsNone(format_capability.row_count)
        self.assertIn("未执行格式校验", format_capability.reason)

    def test_schema4_enabled_but_empty_format_checks_is_honest_empty(self) \
            -> None:
        path = self.snapshot_v4(format_validation="all")
        descriptor = reader.inspect_database(path)
        capability = descriptor.capability("format_checks")
        self.assertEqual("empty", capability.state)
        self.assertEqual(0, capability.row_count)
        self.assertTrue(capability.queryable)

    def test_schema4_sealed_unpublished_is_recovery_only(self) -> None:
        path = self.snapshot_v4(publish=False)
        probe = reader.probe_database(path)
        self.assertFalse(probe.valid)
        self.assertIn("run_state=sealed_unpublished", probe.error)
        descriptor = reader.inspect_database(path, require_sealed=False)
        self.assertEqual("sealed_unpublished", descriptor.lifecycle)
        self.assertEqual("sealed_unpublished", descriptor.kind)
        self.assertEqual("invalid", descriptor.capability("files").state)

    def test_schema4_requires_the_frozen_schema3_business_superset(self) \
            -> None:
        path = self.snapshot_v4(publish=False)
        con = sqlite3.connect(path)
        con.execute("DROP TABLE photo_metadata")
        con.commit()
        con.close()
        probe = reader.probe_database(path, require_sealed=False)
        self.assertFalse(probe.valid)
        self.assertIn("缺少 schema 3 业务表", probe.error)
        self.assertIn("photo_metadata", probe.error)

    def test_schema4_published_filename_pattern_and_fingerprint_are_checked(
        self,
    ) -> None:
        path = self.snapshot_v4()
        wrong_path = os.path.join(self.base, "renamed.sqlite")
        os.rename(path, wrong_path)
        probe = reader.probe_database(wrong_path)
        self.assertFalse(probe.valid)
        self.assertIn("不符合冻结路径模式", probe.error)

    def test_existing_table_with_missing_columns_is_incompatible(self) -> None:
        path = self.snapshot()
        con = sqlite3.connect(path)
        con.execute("CREATE TABLE format_checks (entry_id INTEGER)")
        con.commit()
        con.close()
        descriptor = reader.inspect_database(path)
        capability = descriptor.capability("format_checks")
        self.assertEqual(capability.state, "incompatible")
        self.assertIn("format_checks.status", capability.reason)
        self.assertIsNone(capability.row_count)

    def test_missing_optional_module_does_not_reject_other_capabilities(
        self,
    ) -> None:
        path = self.snapshot()
        con = sqlite3.connect(path)
        con.execute("DROP TABLE photo_metadata")
        con.commit()
        con.close()
        descriptor = reader.inspect_database(path)
        self.assertEqual(descriptor.capability("files").state, "available")
        photo = descriptor.capability("photo_metadata")
        self.assertEqual(photo.state, "unavailable")
        self.assertIsNone(photo.row_count)
        self.assertIn("photo_metadata", photo.reason)
        with self.assertRaises(core.PreflightError) as caught:
            reader.require_capabilities(descriptor, "photo_metadata")
        self.assertIn("unavailable", str(caught.exception))
        with self.assertRaises(core.PreflightError) as query_caught:
            reader.require_queryable_capabilities(
                descriptor, "photo_metadata")
        self.assertIn("缺少表", str(query_caught.exception))

    def test_bad_manifest_is_reported_without_hiding_config_fallback(
        self,
    ) -> None:
        path = self.snapshot()
        con = sqlite3.connect(path)
        con.execute(
            "UPDATE snapshot_manifest SET manifest_json='not-json'")
        con.commit()
        con.close()
        descriptor = reader.inspect_database(path)
        self.assertEqual(descriptor.data_contract, "daisy-snapshot-v3")
        self.assertEqual(descriptor.min_reader_version, "1.4.1")
        self.assertEqual(
            descriptor.capability("run_history").state, "available")
        self.assertTrue(any(
            "manifest_json 不是有效 JSON" in warning
            for warning in descriptor.warnings
        ))

    def test_v141_consumers_do_not_change_input_database_identity(self) -> None:
        current_root = os.path.join(self.base, "current_root")
        os.makedirs(current_root)
        current_file = os.path.join(current_root, "照片.bin")
        with open(current_file, "wb") as handle:
            handle.write(b"data")
        fixed_ns = 1_700_000_000_123_456_700
        os.utime(current_file, ns=(fixed_ns, fixed_ns))
        modified_at = core.ns_to_utc_iso(os.stat(current_file).st_mtime_ns)
        snapshot = self.snapshot(
            "v141_consumers.sqlite",
            root_path=current_root,
            modified_at_utc=modified_at,
        )
        baseline = _file_identity(snapshot)

        self.assertIsNone(core.render_snapshot_issue_report(snapshot))
        self.assertEqual(_file_identity(snapshot), baseline)

        with mock.patch.object(
            hashcheck.dbh, "discover_powershell",
            return_value=("fixture-powershell.exe", "7.5.0"),
        ), mock.patch.object(
            hashcheck.dbh, "get_filehash_batch",
            return_value=["ab" * 32],
        ):
            hash_report = hashcheck.patrol(
                snapshot,
                root_map={"夹具": current_root},
                full=True,
                force=True,
            )
        self.assertTrue(hash_report["ok"])
        self.assertEqual(_file_identity(snapshot), baseline)

        format_report = formatcheck.validate_snapshot(
            snapshot,
            root_map={"夹具": current_root},
            sample_percent=100.0,
            report_dir=os.path.join(self.base, "format_report"),
            force=True,
        )
        self.assertEqual(format_report["counts"], {"unsupported": 1})
        self.assertTrue(format_report["ok"])
        self.assertEqual(_file_identity(snapshot), baseline)

        exportreport.export_snapshot(
            snapshot, os.path.join(self.base, "snapshot_export"))
        self.assertEqual(_file_identity(snapshot), baseline)

        newer = self.snapshot(
            "v141_consumers_new.sqlite",
            root_path=current_root,
            modified_at_utc=modified_at,
        )
        newer_baseline = _file_identity(newer)
        diff_path = os.path.join(self.base, "v141_consumers.diff.sqlite")
        dbdiff.compare(snapshot, newer, diff_path, force=True)
        self.assertEqual(_file_identity(snapshot), baseline)
        self.assertEqual(_file_identity(newer), newer_baseline)
        exportreport.export_diff(
            diff_path, os.path.join(self.base, "diff_export"))
        self.assertEqual(_file_identity(snapshot), baseline)
        self.assertEqual(_file_identity(newer), newer_baseline)

    def test_v141_diff_identity_and_capabilities(self) -> None:
        path = self.diff()
        before = _sha256(path)
        descriptor = reader.inspect_database(path, expected_type="diff")
        self.assertEqual(before, _sha256(path))
        self.assertEqual(descriptor.database_type, "diff")
        self.assertEqual(descriptor.source_version, "1.4.1")
        self.assertEqual(descriptor.schema_version, 3)
        self.assertEqual(
            descriptor.capability("file_changes").state, "available")
        self.assertEqual(
            descriptor.capability("directory_changes").state, "empty")
        self.assertEqual(
            descriptor.capability("evidence_notes").state, "available")

    def test_probe_serialization_keeps_null_distinct_from_zero(self) -> None:
        probe = reader.probe_database(self.snapshot())
        payload = probe.as_dict()
        self.assertEqual(payload["state"], "valid")
        capabilities = payload["database"]["capabilities"]
        self.assertEqual(capabilities["raw_payloads"]["row_count"], 0)
        self.assertIsNone(capabilities["format_checks"]["row_count"])
        self.assertEqual(capabilities["raw_payloads"]["state"], "empty")
        self.assertEqual(
            capabilities["format_checks"]["state"], "unavailable")


if __name__ == "__main__":
    unittest.main(verbosity=2)
