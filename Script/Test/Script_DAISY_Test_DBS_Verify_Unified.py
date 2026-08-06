"""v1.6.0 统一核验、受控格式 worker 与人读报告专项测试。"""
from __future__ import annotations

import hashlib
import io
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
import zipfile


_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SCRIPT_DIR = os.path.dirname(_TEST_DIR)
_REPO_ROOT = os.path.dirname(_SCRIPT_DIR)
_LIB_DIR = os.path.join(_SCRIPT_DIR, "Lib")
sys.path[:0] = [_TEST_DIR, _SCRIPT_DIR, _LIB_DIR]

import Script_DAISY_Lib_DBS_01_Core as core
import Script_DAISY_Lib_DBS_03_Hash as dbhash
import Script_DAISY_Lib_DBS_06_Verify as legacy
import Script_DAISY_Lib_DBS_11_Verify_Run as verifyrun


_RUNTIME_ROOT = os.path.join(
    _REPO_ROOT, ".test_runtime", "v1_6_0", "verify_unified")


def _identity(path: str) -> tuple[str, int, int]:
    stat_result = os.stat(path)
    return (
        core.sha256_file(path),
        int(stat_result.st_size),
        int(stat_result.st_mtime_ns),
    )


def _slow_format_worker(connection) -> None:
    """只供精确子进程 timeout 测试；不创建孙进程、不访问其它路径。"""
    connection.send({"kind": "ready"})
    connection.recv()
    time.sleep(30.0)


class _Fixture(unittest.TestCase):
    def setUp(self) -> None:
        os.makedirs(_RUNTIME_ROOT, exist_ok=True)
        self._temporary = tempfile.TemporaryDirectory(
            prefix="case_", dir=_RUNTIME_ROOT)
        self.base = self._temporary.name
        self.current = os.path.join(self.base, "Current")
        self.reports = os.path.join(self.base, "Reports")
        os.makedirs(self.current)
        os.makedirs(self.reports)

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def snapshot(
        self,
        files: dict[str, tuple[bytes, str]],
        *,
        hash_coverage: str = "full",
        name: str = "fixture.sqlite",
    ) -> str:
        fixed_ns = 1_700_000_000_123_456_700
        for relative, (payload, _kind) in files.items():
            path = os.path.join(self.current, relative)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as handle:
                handle.write(payload)
            os.utime(path, ns=(fixed_ns, fixed_ns))

        path = os.path.join(self.base, name)
        config = {
            "phase": "full",
            "quick": False,
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
                " (1,'unified-verify-fixture',3,1,'complete','ok',?,"
                " '2026-01-01T00:00:00.0000000Z',0,'fixture','Windows',"
                " '1.4.1',?)",
                (hash_coverage, json.dumps(config, ensure_ascii=False)),
            )
            connection.execute(
                "INSERT INTO roots"
                " (root_id,root_path,root_label,enum_status)"
                " VALUES (1,'X:/Recorded','夹具','ok')")
            connection.execute(
                "INSERT INTO dirs"
                " (dir_id,root_id,rel_path,path_key,enum_status,observed_at_utc)"
                " VALUES (1,1,'','','ok','2026-01-01T00:00:00.0000000Z')")
            for entry_id, (relative, (payload, kind)) in enumerate(
                    sorted(files.items()), 1):
                physical = os.path.join(self.current, relative)
                modified = core.ns_to_utc_iso(os.stat(physical).st_mtime_ns)
                extension = os.path.splitext(relative)[1].lstrip(".").casefold()
                connection.execute(
                    "INSERT INTO entries"
                    " (entry_id,root_id,dir_id,rel_path,path_key,name,extension,"
                    " media_kind,size_bytes,modified_at_utc,attributes,"
                    " observed_at_utc,meta_status,hash_status) VALUES"
                    " (?,1,1,?,?,?,?,?,?,?,0,"
                    " '2026-01-01T00:00:00.0000000Z','not_applicable',?)",
                    (
                        entry_id, relative, relative.casefold(),
                        os.path.basename(relative), extension, kind,
                        len(payload), modified,
                        "skipped" if hash_coverage == "none" else "done",
                    ),
                )
                if hash_coverage != "none":
                    connection.execute(
                        "INSERT INTO hashes"
                        " (entry_id,algorithm,hash_hex,origin,size_bytes,"
                        " bytes_read,status,tool,tool_version) VALUES"
                        " (?,'sha256',?,'computed',?,?,'valid','hashlib','1')",
                        (
                            entry_id, hashlib.sha256(payload).hexdigest(),
                            len(payload), len(payload),
                        ),
                    )
            manifest = {
                "data_contract": "daisy-snapshot-v3",
                "min_reader_version": "1.4.1",
                "config": config,
                "effective_profile": {
                    "scan_kind": "full",
                    "hash_coverage_actual": hash_coverage,
                    "metadata_storage": "complete",
                    "raw_payload_retained": True,
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

    def verify(
        self,
        snapshot: str,
        *,
        options: verifyrun.VerificationOptions,
        **kwargs,
    ) -> dict[str, object]:
        return verifyrun.run_unified_verification(
            snapshot,
            [f"夹具={self.current}"],
            options=options,
            force=True,
            **kwargs,
        )


class TestUnifiedVerificationModel(_Fixture):
    def test_options_reject_nonfinite_and_unknown_modes(self) -> None:
        with self.assertRaises(ValueError):
            verifyrun.VerificationOptions(hash_mode="maybe")
        with self.assertRaises(ValueError):
            verifyrun.VerificationOptions(format_sample_percent=float("nan"))
        with self.assertRaises(ValueError):
            verifyrun.VerificationOptions(hash_timeout_seconds=0)

    def test_schema3_no_hash_is_incomplete_not_false_success(self) -> None:
        snapshot = self.snapshot(
            {"中文.txt": (b"plain", "other")}, hash_coverage="none")
        baseline = _identity(snapshot)
        report = self.verify(
            snapshot,
            options=verifyrun.VerificationOptions(
                hash_mode="all", format_mode="off"),
        )
        self.assertEqual(_identity(snapshot), baseline)
        self.assertEqual(report["snapshot"]["database"]["schema_version"], 3)
        self.assertEqual(report["snapshot"]["database"]["source_version"],
                         "1.4.1")
        self.assertEqual(report["sections"]["stat"]["state"], "executed")
        self.assertEqual(report["sections"]["hash"]["unverifiable"], 1)
        self.assertEqual(report["conclusion"], "incomplete")
        self.assertFalse(report["ok"])

    def test_strict_hash_matches_only_complete_reaped_digest(self) -> None:
        snapshot = self.snapshot({"file.bin": (b"data", "other")})

        def resolver(_explicit):
            return {"path": "X:/Synthetic/powershell.exe", "version": "7.4"}

        def runner(path, _powershell, **_kwargs):
            size = os.path.getsize(path)
            return dbhash.IndependentHashOutcome(
                outcome="completed",
                hash_hex=core.sha256_file(path),
                error=None,
                decision="none",
                decision_source="none",
                size_bytes=size,
                bytes_read=size,
                final_offset=size,
                elapsed_seconds=0.01,
                active_read_seconds=0.01,
                stall_count=0,
                longest_stall_seconds=0.0,
                first_stall_offset=None,
                last_stall_offset=None,
                threshold_count=0,
                worker_pid=12345,
                worker_exitcode=0,
                worker_reaped=True,
                events=(),
            )

        report = self.verify(
            snapshot,
            options=verifyrun.VerificationOptions(
                hash_mode="all", format_mode="off"),
            _powershell_resolver=resolver,
            _hash_runner=runner,
        )
        hashed = report["sections"]["hash"]
        self.assertEqual(hashed["checked"], 1)
        self.assertEqual(hashed["matched"], 1)
        self.assertEqual(hashed["problems"], [])
        self.assertEqual(report["conclusion"], "passed")

    def test_hash_post_stat_change_is_unstable_not_mismatch(self) -> None:
        snapshot = self.snapshot({"file.bin": (b"data", "other")})
        physical = os.path.join(self.current, "file.bin")

        def resolver(_explicit):
            return {"path": "X:/Synthetic/powershell.exe", "version": "7.4"}

        def runner(path, _powershell, **_kwargs):
            digest = core.sha256_file(path)
            with open(path, "wb") as handle:
                handle.write(b"DATA")
            size = os.path.getsize(path)
            return dbhash.IndependentHashOutcome(
                "completed", digest, None, "none", "none",
                size, size, size, 0.01, 0.01, 0, 0.0,
                None, None, 0, 12345, 0, True, (),
            )

        report = self.verify(
            snapshot,
            options=verifyrun.VerificationOptions(
                hash_mode="all", format_mode="off"),
            _powershell_resolver=resolver,
            _hash_runner=runner,
        )
        problems = report["sections"]["hash"]["problems"]
        self.assertEqual([row["status"] for row in problems], ["unstable"])
        self.assertNotIn("mismatched", report["sections"]["hash"]["counts"])
        with open(physical, "rb") as handle:
            self.assertEqual(handle.read(), b"DATA")

    def test_stat_hash_format_modes_have_independent_selection(self) -> None:
        snapshot = self.snapshot({
            "a.txt": (b"a", "other"),
            "b.txt": (b"b", "other"),
        })
        report = self.verify(
            snapshot,
            options=verifyrun.VerificationOptions(
                hash_mode="off", format_mode="all"),
        )
        self.assertEqual(report["sections"]["stat"]["checked"], 2)
        self.assertEqual(report["sections"]["hash"]["state"], "NULL")
        self.assertEqual(report["sections"]["format"]["selected"], 2)
        self.assertEqual(report["sections"]["format"]["unsupported"], 2)
        self.assertEqual(report["sections"]["format"]["problems"], [])
        self.assertEqual(report["conclusion"], "passed")

    def test_unknown_format_only_counts_and_never_emits_path(self) -> None:
        snapshot = self.snapshot({"秘密.unknown": (b"plain", "other")})
        report = self.verify(
            snapshot,
            options=verifyrun.VerificationOptions(
                hash_mode="off", format_mode="all"),
        )
        encoded = json.dumps(report["sections"]["format"], ensure_ascii=False)
        markdown = verifyrun.render_verification_markdown(report)
        self.assertEqual(report["sections"]["format"]["unsupported"], 1)
        self.assertNotIn("秘密.unknown", encoded)
        self.assertNotIn("秘密.unknown", markdown)
        self.assertIn("只记录总数", markdown)


class TestFormatWorkerAndControl(_Fixture):
    @staticmethod
    def _zip_payload(valid: bool) -> bytes:
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("member.txt", b"payload" * 100)
        payload = stream.getvalue()
        return payload if valid else payload[:-20]

    def test_builtin_format_worker_is_reaped_and_invalid_is_kept(self) -> None:
        snapshot = self.snapshot({
            "good.zip": (self._zip_payload(True), "archive"),
            "bad.zip": (self._zip_payload(False), "archive"),
        })
        report = self.verify(
            snapshot,
            options=verifyrun.VerificationOptions(
                hash_mode="off", format_mode="all",
                format_timeout_seconds=10.0,
            ),
        )
        formatted = report["sections"]["format"]
        self.assertEqual(formatted["valid"], 1)
        self.assertEqual(formatted["counts"].get("invalid"), 1)
        self.assertEqual(formatted["problems"][0]["path"], "夹具\\bad.zip")
        self.assertEqual(report["conclusion"], "issues_found")

    def test_format_timeout_reaps_only_owned_worker(self) -> None:
        path = os.path.join(self.current, "slow.pdf")
        with open(path, "wb") as handle:
            handle.write(b"%PDF-1.4\n%%EOF\n")
        outcome = verifyrun.run_format_worker(
            path,
            "document",
            legacy.FormatValidatorSpec(
                "pdf", "daisy-format", legacy.FORMAT_VALIDATION_PROFILE),
            {},
            expected_size=os.path.getsize(path),
            timeout_seconds=0.1,
            default_decision="skip_and_record",
            poll_seconds=0.01,
            _worker_target=_slow_format_worker,
        )
        self.assertEqual(outcome.outcome, "timeout")
        self.assertEqual(outcome.status, "timeout")
        self.assertTrue(outcome.worker_reaped)
        self.assertIsNotNone(outcome.worker_exitcode)
        self.assertEqual(outcome.threshold_count, 1)

    def test_pause_then_continue_at_stat_boundary(self) -> None:
        snapshot = self.snapshot({"file.bin": (b"data", "other")})
        control = verifyrun.UnifiedVerificationControl()
        accepted, _reason = control.request_pause()
        self.assertTrue(accepted)
        result: list[dict[str, object]] = []
        errors: list[BaseException] = []

        def target() -> None:
            try:
                result.append(self.verify(
                    snapshot,
                    options=verifyrun.VerificationOptions(
                        hash_mode="off", format_mode="off"),
                    control=control,
                ))
            except BaseException as exc:
                errors.append(exc)

        thread = threading.Thread(target=target, daemon=True)
        thread.start()
        deadline = time.monotonic() + 5.0
        while control.state != "paused" and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(control.state, "paused")
        self.assertEqual(control.request_continue(), (True, "accepted"))
        thread.join(timeout=5.0)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(result[0]["run_state"], "complete")


class TestVerificationReportPublication(_Fixture):
    def test_markdown_json_are_utf8_lf_and_no_clobber(self) -> None:
        snapshot = self.snapshot({"中文.txt": (b"plain", "other")})
        report = self.verify(
            snapshot,
            options=verifyrun.VerificationOptions(
                hash_mode="off", format_mode="off"),
        )
        with mock.patch.object(
            core, "snapshot_working_name", return_value="Fixed_Verify_Report",
        ):
            publication = verifyrun.publish_verification_report(
                report, self.reports)
            with open(publication.json_path, "rb") as handle:
                original_json = handle.read()
            with open(publication.markdown_path, "rb") as handle:
                original_markdown = handle.read()
            with self.assertRaises(core.PreflightError):
                verifyrun.publish_verification_report(report, self.reports)
        self.assertFalse(original_json.startswith(b"\xef\xbb\xbf"))
        self.assertFalse(original_markdown.startswith(b"\xef\xbb\xbf"))
        self.assertNotIn(b"\r", original_json)
        self.assertNotIn(b"\r", original_markdown)
        with open(publication.json_path, "rb") as handle:
            self.assertEqual(handle.read(), original_json)
        with open(publication.markdown_path, "rb") as handle:
            self.assertEqual(handle.read(), original_markdown)
        markdown = original_markdown.decode("utf-8")
        self.assertIn("## 哈希问题", markdown)
        self.assertIn("## 格式校验问题", markdown)
        self.assertIn("NULL（本次未执行）", markdown)
        self.assertEqual(
            [name for name in os.listdir(self.reports) if ".partial." in name],
            [],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
