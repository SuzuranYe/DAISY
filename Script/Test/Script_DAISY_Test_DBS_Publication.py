"""DAISY v1.6.0 独立抽验、封存与生产发布编排测试。"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock


_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SCRIPT_DIR = os.path.dirname(_TEST_DIR)
_REPO_ROOT = os.path.dirname(_SCRIPT_DIR)
_LIB_DIR = os.path.join(_SCRIPT_DIR, "Lib")
sys.path[:0] = [_TEST_DIR, _SCRIPT_DIR, _LIB_DIR]

import Script_DAISY_Lib_DBS_01_Core as core
import Script_DAISY_Lib_DBS_02_Meta as dbmeta
import Script_DAISY_Lib_DBS_03_Hash as dbhash
import Script_DAISY_Lib_DBS_05_Reader as dbreader
import Script_DAISY_Lib_DBS_08_State as dbstate
import Script_DAISY_Lib_DBS_09_Run as dbrun


_RUNTIME_ROOT = os.path.join(
    _REPO_ROOT, ".test_runtime", "v1_6_0", "publication")


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


class _FakeExifToolWorker:
    def __init__(self, _path) -> None:
        pass

    @staticmethod
    def extract(file_path, photo_profile=False, timeout=None):
        del photo_profile, timeout
        return {"SourceFile": file_path}

    @staticmethod
    def close() -> None:
        pass


class TestScanPublication(unittest.TestCase):
    def setUp(self) -> None:
        os.makedirs(_RUNTIME_ROOT, exist_ok=True)
        self._td = tempfile.TemporaryDirectory(
            prefix="case_", dir=_RUNTIME_ROOT)
        self.base = self._td.name
        self.root_path = os.path.join(self.base, "Archive")
        self.output_dir = os.path.join(self.base, "Snapshots")
        os.makedirs(self.root_path)
        os.makedirs(self.output_dir)
        self.partial = os.path.join(
            self.output_dir, "Run.partial.sqlite")
        self.publish_stem = os.path.join(self.output_dir, "Run")
        self._handles: list[dbrun.RunHandle] = []

    def tearDown(self) -> None:
        for handle in self._handles:
            try:
                handle.connection.close()
            except sqlite3.Error:
                pass
            if os.path.exists(handle.lease_path):
                try:
                    dbstate.release_lease_file(
                        handle.lease_path, handle.lease.lease_id)
                except (OSError, core.PreflightError):
                    pass
        self._td.cleanup()

    @staticmethod
    def tools(*, powershell: bool = True) -> dict[str, object]:
        result: dict[str, object] = {
            "exiftool": {"path": "fixture", "version": "13.fixture"},
            "ffprobe": {"path": "fixture", "version": "7.fixture"},
            "sevenzip": {"path": "fixture", "version": "24.fixture"},
        }
        if powershell:
            result["powershell"] = {
                "path": "fixture-powershell",
                "version": "5.1.fixture",
            }
        return result

    def create(
        self,
        *,
        config: dict[str, object] | None = None,
        tools: dict[str, object] | None = None,
    ) -> dbrun.RunHandle:
        handle = dbrun.create_run(
            self.partial,
            [("档案", self.root_path)],
            config or {
                "phase": "full",
                "hash": "full",
                "verify_sample_percent": 100.0,
                "metadata_storage": "normalized",
                "format_validation": "off",
            },
            output_dir=self.output_dir,
            publish_stem_path=self.publish_stem,
            tool_versions=tools if tools is not None else self.tools(),
        )
        self._handles.append(handle)
        return handle

    @staticmethod
    def independent_outcome(path: str, digest: str | None = None) \
            -> dbhash.IndependentHashOutcome:
        actual = digest or _sha256(path)
        size = os.path.getsize(path)
        return dbhash.IndependentHashOutcome(
            outcome="completed",
            hash_hex=actual,
            error=None,
            decision="none",
            decision_source="none",
            size_bytes=size,
            bytes_read=size,
            final_offset=size,
            elapsed_seconds=0.01,
            active_read_seconds=0.01,
            stall_count=0,
            longest_stall_seconds=0.01,
            first_stall_offset=None,
            last_stall_offset=None,
            threshold_count=0,
            worker_pid=7801,
            worker_exitcode=0,
            worker_reaped=True,
            events=(),
        )

    @staticmethod
    def primary_outcome(path: str) -> dbhash.HashWorkerOutcome:
        digest = _sha256(path)
        size = os.path.getsize(path)
        return dbhash.HashWorkerOutcome(
            outcome="completed",
            result={
                "hash_hex": digest,
                "bytes_read": size,
                "status": "valid",
            },
            decision="none",
            decision_source="none",
            size_bytes=size,
            bytes_read=size,
            final_offset=size,
            elapsed_seconds=0.01,
            active_read_seconds=0.01,
            stall_count=0,
            longest_stall_seconds=0.01,
            first_stall_offset=None,
            last_stall_offset=None,
            threshold_count=0,
            worker_pid=7802,
            worker_exitcode=0,
            worker_reaped=True,
            events=(),
        )

    def run_evidence(self, handle: dbrun.RunHandle) -> None:
        with mock.patch.object(
                dbmeta, "ExifToolWorker", _FakeExifToolWorker):
            result = dbrun.run_scan_evidence_stages(
                handle,
                dbrun.RunCommandRouter(),
                hash_stall_seconds=1.0,
                hash_timeout_seconds=2.0,
                hash_poll_seconds=0.005,
            )
        self.assertEqual(("completed", "rescan"), (
            result["state"], result["stage"]))

    def test_clean_full_pipeline_publishes_self_describing_snapshot(
        self,
    ) -> None:
        source = os.path.join(self.root_path, "中文资料.bin")
        with open(source, "wb") as stream:
            stream.write("内容".encode("utf-8"))
        source_before = _sha256(source)
        handle = self.create()
        runtime = dbstate.load_runtime(handle.connection)
        with open(
            runtime.event_log_path, "x", encoding="utf-8", newline="\n"
        ) as stream:
            stream.write(json.dumps({
                "ts": core.now_utc_iso(),
                "event": "fixture_event",
                "detail": "中文",
            }, ensure_ascii=False) + "\n")
        self.run_evidence(handle)

        result = dbrun.run_scan_completion_stages(
            handle,
            dbrun.RunCommandRouter(),
            hash_stall_seconds=1.0,
            hash_timeout_seconds=2.0,
            hash_poll_seconds=0.005,
            _independent_runner=lambda path, *_args, **_kwargs:
            self.independent_outcome(path),
        )
        publication = result["publication"]
        self.assertEqual(("published", "publish"), (
            result["state"], result["stage"]))
        self.assertTrue(os.path.isfile(publication.final_path))
        self.assertEqual(publication.sha256, _sha256(publication.final_path))
        self.assertTrue(publication.final_path.endswith(
            f"_{publication.sha256[:8].upper()}.sqlite"))
        self.assertFalse(os.path.exists(self.partial))
        self.assertFalse(os.path.exists(handle.lease_path))
        self.assertFalse(os.path.exists(
            self.publish_stem + ".publishing.sqlite"))
        self.assertFalse(os.path.exists(runtime.event_log_path))
        self.assertIsNone(publication.issue_report_path)
        self.assertEqual(source_before, _sha256(source))

        probe = dbreader.probe_database(publication.final_path)
        self.assertTrue(probe.valid, probe.error)
        con = sqlite3.connect(
            Path(publication.final_path).resolve(strict=True).as_uri()
            + "?mode=ro",
            uri=True,
        )
        try:
            self.assertEqual("published", dbstate.load_runtime(con).run_state)
            self.assertEqual("full", con.execute(
                "SELECT hash_coverage FROM snapshot_info WHERE id=1"
            ).fetchone()[0])
            checkpoints = dict(con.execute(
                "SELECT stage,state FROM stage_checkpoints"
            ))
            self.assertEqual({
                "enumerate": "completed",
                "hash": "completed",
                "metadata": "completed",
                "format": "skipped",
                "rescan": "completed",
                "verify_hash": "completed",
                "verify_format": "skipped",
                "seal": "completed",
                "publish": "completed",
            }, checkpoints)
            manifest = json.loads(con.execute(
                "SELECT manifest_json FROM snapshot_manifest WHERE id=1"
            ).fetchone()[0])
            self.assertEqual("daisy-snapshot-v4", manifest["data_contract"])
            self.assertEqual(
                "run_state_events",
                manifest["runtime_contract"][
                    "authoritative_event_storage"],
            )
            self.assertEqual(1, manifest["counts"]["entries"])
            self.assertEqual(
                [("fixture_event",), ("snapshot_sealed",)],
                con.execute(
                    "SELECT event FROM run_events ORDER BY event_seq"
                ).fetchall(),
            )
        finally:
            con.close()

    def test_persistent_hash_disagreement_publishes_one_issues_sidecar(
        self,
    ) -> None:
        source = os.path.join(self.root_path, "mismatch.bin")
        with open(source, "wb") as stream:
            stream.write(b"abc")
        handle = self.create()
        self.run_evidence(handle)
        recorded = handle.connection.execute(
            "SELECT hash_hex FROM hashes"
        ).fetchone()[0]
        wrong = "f" * 64 if recorded != "f" * 64 else "e" * 64
        result = dbrun.run_scan_completion_stages(
            handle,
            dbrun.RunCommandRouter(),
            hash_stall_seconds=1.0,
            hash_timeout_seconds=2.0,
            hash_poll_seconds=0.005,
            _independent_runner=lambda path, *_args, **_kwargs:
            self.independent_outcome(path, wrong),
            _primary_runner=lambda path, **_kwargs:
            self.primary_outcome(path),
        )
        publication = result["publication"]
        self.assertIsNotNone(publication.issue_report_path)
        self.assertTrue(os.path.isfile(publication.issue_report_path))
        with open(
                publication.issue_report_path, encoding="utf-8") as stream:
            markdown = stream.read()
        self.assertIn("## 哈希问题", markdown)
        self.assertIn("verify_mismatch", markdown)
        self.assertEqual(1, markdown.count("# DAISY 问题报告"))
        con = sqlite3.connect(
            Path(publication.final_path).resolve(strict=True).as_uri()
            + "?mode=ro",
            uri=True,
        )
        try:
            self.assertEqual(("unstable", "unstable"), tuple(con.execute(
                "SELECT e.hash_status,h.status FROM entries e"
                " JOIN hashes h ON h.entry_id=e.entry_id"
            ).fetchone()))
        finally:
            con.close()

    def test_publish_conflict_preserves_sealed_partial_and_exact_lease(
        self,
    ) -> None:
        with open(os.path.join(self.root_path, "file.bin"), "wb") as stream:
            stream.write(b"abc")
        handle = self.create()
        self.run_evidence(handle)
        with mock.patch.object(
                dbstate,
                "_publish_no_clobber",
                side_effect=core.PreflightError("fixture conflict")):
            with self.assertRaisesRegex(core.PreflightError, "fixture conflict"):
                dbrun.run_scan_completion_stages(
                    handle,
                    dbrun.RunCommandRouter(),
                    hash_stall_seconds=1.0,
                    hash_timeout_seconds=2.0,
                    hash_poll_seconds=0.005,
                    _independent_runner=lambda path, *_args, **_kwargs:
                    self.independent_outcome(path),
                )
        self.assertTrue(os.path.isfile(self.partial))
        self.assertTrue(os.path.isfile(handle.lease_path))
        self.assertFalse(os.path.exists(
            self.publish_stem + ".publishing.sqlite"))
        self.assertFalse(any(
            name.endswith("_Issues.md")
            for name in os.listdir(self.output_dir)))
        con = sqlite3.connect(
            Path(self.partial).resolve(strict=True).as_uri() + "?mode=ro",
            uri=True,
        )
        try:
            self.assertEqual(
                "sealed_unpublished", dbstate.load_runtime(con).run_state)
        finally:
            con.close()

    def test_performance_analysis_feeds_the_same_issues_report(self) -> None:
        block = b"x" * (1024 * 1024)
        for index in range(10):
            with open(
                os.path.join(self.root_path, f"file_{index:02d}.bin"),
                "wb",
            ) as stream:
                stream.write(block)
        handle = self.create()
        self.run_evidence(handle)
        performance_ids = [
            int(row[0]) for row in handle.connection.execute(
                "SELECT performance_id FROM read_performance"
                " WHERE stage='hash' ORDER BY performance_id"
            )
        ]
        active_seconds = [0.01] * 8 + [0.025, 0.05]
        for performance_id, active in zip(
                performance_ids, active_seconds):
            handle.connection.execute(
                "UPDATE read_performance SET elapsed_seconds=?,"
                " active_read_seconds=?,stall_count=0,"
                " longest_stall_seconds=0 WHERE performance_id=?",
                (active, active, performance_id),
            )
        handle.connection.commit()
        result = dbrun.run_scan_completion_stages(
            handle,
            dbrun.RunCommandRouter(),
            hash_stall_seconds=1.0,
            hash_timeout_seconds=2.0,
            hash_poll_seconds=0.005,
            _independent_runner=lambda path, *_args, **_kwargs:
            self.independent_outcome(path),
        )
        self.assertEqual((1, 1, False), (
            result["performance"]["low"],
            result["performance"]["high"],
            result["performance"]["physical_location_claimed"],
        ))
        publication = result["publication"]
        self.assertIsNotNone(publication.issue_report_path)
        with open(
                publication.issue_report_path, encoding="utf-8") as stream:
            markdown = stream.read()
        self.assertIn("## 读取性能异常候选", markdown)
        self.assertIn("不能据此认定物理坏区", markdown)
        con = sqlite3.connect(
            Path(publication.final_path).resolve(strict=True).as_uri()
            + "?mode=ro",
            uri=True,
        )
        try:
            self.assertEqual(
                [("high", 1), ("low", 1), ("none", 8)],
                con.execute(
                    "SELECT candidate_confidence,COUNT(*)"
                    " FROM read_performance WHERE stage='hash'"
                    " GROUP BY candidate_confidence"
                    " ORDER BY candidate_confidence"
                ).fetchall(),
            )
        finally:
            con.close()

    def test_incomplete_stages_are_rejected_before_state_transition(
        self,
    ) -> None:
        handle = self.create()
        with self.assertRaisesRegex(core.PreflightError, "阶段尚未形成终态"):
            dbrun.seal_and_publish_scan(handle)
        runtime = dbstate.load_runtime(handle.connection)
        self.assertEqual("running", runtime.run_state)
        self.assertEqual("pending", handle.connection.execute(
            "SELECT state FROM stage_checkpoints WHERE stage='seal'"
        ).fetchone()[0])
        self.assertFalse(os.path.exists(
            self.publish_stem + ".publishing.sqlite"))

    def test_seal_failure_keeps_partial_and_enters_recoverable_state(
        self,
    ) -> None:
        with open(os.path.join(self.root_path, "file.bin"), "wb") as stream:
            stream.write(b"abc")
        handle = self.create()
        self.run_evidence(handle)
        event_path = dbstate.load_runtime(handle.connection).event_log_path
        with open(event_path, "x", encoding="utf-8", newline="\n") as stream:
            stream.write("not-json\n")
        with self.assertRaisesRegex(core.PreflightError, "不是有效 JSON"):
            dbrun.run_scan_completion_stages(
                handle,
                dbrun.RunCommandRouter(),
                hash_stall_seconds=1.0,
                hash_timeout_seconds=2.0,
                hash_poll_seconds=0.005,
                _independent_runner=lambda path, *_args, **_kwargs:
                self.independent_outcome(path),
            )
        runtime = dbstate.load_runtime(handle.connection)
        last_error_code = handle.connection.execute(
            "SELECT last_error_code FROM snapshot_runtime WHERE id=1"
        ).fetchone()[0]
        self.assertEqual(("failed_recoverable", "seal_failed"), (
            runtime.run_state, last_error_code))
        self.assertEqual("failed_recoverable", handle.connection.execute(
            "SELECT state FROM stage_checkpoints WHERE stage='seal'"
        ).fetchone()[0])
        self.assertTrue(os.path.isfile(self.partial))
        self.assertTrue(os.path.isfile(handle.lease_path))
        self.assertFalse(os.path.exists(
            self.publish_stem + ".publishing.sqlite"))

    def test_quick_pipeline_skips_hash_verification_without_powershell(
        self,
    ) -> None:
        with open(os.path.join(self.root_path, "quick.bin"), "wb") as stream:
            stream.write(b"quick")
        handle = self.create(
            config={
                "phase": "quick",
                "hash": "none",
                "metadata_storage": "normalized",
                "format_validation": "off",
            },
            tools={},
        )
        result = dbrun.run_scan_to_publication(
            handle,
            dbrun.RunCommandRouter(),
        )
        publication = result["publication"]
        self.assertTrue(os.path.isfile(publication.final_path))
        self.assertEqual(0, result["verification"]["sampled"])
        con = sqlite3.connect(
            Path(publication.final_path).resolve(strict=True).as_uri()
            + "?mode=ro",
            uri=True,
        )
        try:
            self.assertEqual("none", con.execute(
                "SELECT hash_coverage FROM snapshot_info WHERE id=1"
            ).fetchone()[0])
            self.assertEqual("skipped", con.execute(
                "SELECT state FROM stage_checkpoints"
                " WHERE stage='verify_hash'"
            ).fetchone()[0])
        finally:
            con.close()


if __name__ == "__main__":
    unittest.main()
