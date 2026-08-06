"""DAISY v1.6.0 schema 4 partial 与 lease 生命周期测试。"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest


_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SCRIPT_DIR = os.path.dirname(_TEST_DIR)
_REPO_ROOT = os.path.dirname(_SCRIPT_DIR)
_LIB_DIR = os.path.join(_SCRIPT_DIR, "Lib")
sys.path[:0] = [_TEST_DIR, _SCRIPT_DIR, _LIB_DIR]

import Script_DAISY_Lib_DBS_01_Core as core
import Script_DAISY_Lib_DBS_08_State as dbstate
import Script_DAISY_Lib_DBS_09_Run as dbrun


_RUNTIME_ROOT = os.path.join(
    _REPO_ROOT, ".test_runtime", "v1_6_0", "run_lifecycle")


class _RunFixture(unittest.TestCase):
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

    def tearDown(self) -> None:
        self._td.cleanup()

    def create(self, **overrides) -> dbrun.RunHandle:
        arguments = {
            "partial_path": self.partial,
            "roots": [("档案", self.root_path)],
            "config": {
                "phase": "full",
                "hash": "full",
                "metadata_storage": "complete",
                "format_validation": "off",
            },
            "output_dir": self.output_dir,
            "publish_stem_path": self.publish_stem,
            "tool_versions": {
                "exiftool": "fixture",
                "ffprobe": "fixture",
                "sevenzip": "fixture",
            },
        }
        arguments.update(overrides)
        return dbrun.create_run(**arguments)


class TestRunCreation(_RunFixture):
    def test_lease_path_is_deterministic_and_requires_partial_suffix(
        self,
    ) -> None:
        expected = os.path.abspath(self.partial) + ".lease"
        self.assertEqual(expected, dbrun.lease_path_for_partial(self.partial))
        with self.assertRaises(core.PreflightError):
            dbrun.lease_path_for_partial(
                os.path.join(self.output_dir, "plain.sqlite"))

    def test_create_has_schema4_identity_volume_and_matching_lease(self) \
            -> None:
        handle = self.create(
            snapshot_uuid="1" * 32,
            session_id="2" * 32,
            lease_id="3" * 32,
        )
        try:
            runtime = dbstate.load_runtime(handle.connection)
            lease = dbstate.read_lease_file(handle.lease_path)
            self.assertEqual(4, handle.connection.execute(
                "SELECT schema_version FROM snapshot_info").fetchone()[0])
            self.assertEqual(("2" * 32, "3" * 32), (
                runtime.active_session_id, lease.lease_id))
            self.assertEqual(runtime.active_session_id, lease.session_id)
            root = handle.connection.execute(
                "SELECT root_label,root_path,volume_serial FROM roots"
            ).fetchone()
            self.assertEqual(("档案", os.path.abspath(self.root_path)),
                             tuple(root[:2]))
            self.assertIsNotNone(root[2])
        finally:
            dbrun.close_handle(handle, release_lease=True)

    def test_existing_partial_is_not_opened_or_modified(self) -> None:
        handle = self.create()
        handle.connection.execute("PRAGMA wal_checkpoint(FULL)")
        before_database = core.sha256_file(handle.partial_path)
        with open(handle.lease_path, "rb") as stream:
            before_lease = stream.read()
        try:
            with self.assertRaisesRegex(
                    core.PreflightError, "不会覆盖"):
                self.create()
            self.assertEqual(
                before_database, core.sha256_file(handle.partial_path))
            with open(handle.lease_path, "rb") as stream:
                self.assertEqual(before_lease, stream.read())
        finally:
            dbrun.close_handle(handle, release_lease=True)

    def test_failed_initialization_removes_only_its_reserved_artifacts(
        self,
    ) -> None:
        with self.assertRaises(core.PreflightError):
            self.create(event_log_path=self.partial)
        for path in (
                self.partial,
                self.partial + "-wal",
                self.partial + "-shm",
                dbrun.lease_path_for_partial(self.partial)):
            self.assertFalse(os.path.exists(path), path)


class TestRunResume(_RunFixture):
    def test_schema3_candidate_is_rejected_without_mutation(self) -> None:
        legacy = os.path.join(self.output_dir, "Legacy.partial.sqlite")
        con = core.create_partial_snapshot(
            legacy,
            [("档案", self.root_path)],
            config={"profile_version": 1},
        )
        con.close()
        core.release_scan_lock(legacy)
        before = core.sha256_file(legacy)
        with self.assertRaisesRegex(core.PreflightError, "schema 4"):
            dbrun.inspect_resume(legacy)
        self.assertEqual(before, core.sha256_file(legacy))
        self.assertFalse(os.path.exists(dbrun.lease_path_for_partial(legacy)))

    def test_preview_is_read_only_and_reports_active_owner(self) -> None:
        handle = self.create()
        handle.connection.execute("PRAGMA wal_checkpoint(FULL)")
        before_database = core.sha256_file(handle.partial_path)
        with open(handle.lease_path, "rb") as stream:
            before_lease = stream.read()
        try:
            preview = dbrun.inspect_resume(handle.partial_path)
            self.assertEqual(("running", "none", "enumerate"), (
                preview.run_state,
                preview.resume_hint,
                preview.current_stage,
            ))
            self.assertEqual("active_local", preview.lease_classification)
            self.assertEqual((("档案", os.path.abspath(self.root_path)),),
                             preview.roots)
            self.assertEqual(
                before_database, core.sha256_file(handle.partial_path))
            with open(handle.lease_path, "rb") as stream:
                self.assertEqual(before_lease, stream.read())
        finally:
            dbrun.close_handle(handle, release_lease=True)

    def test_active_owner_cannot_be_taken_over(self) -> None:
        handle = self.create()
        try:
            runtime_before = dbstate.load_runtime(handle.connection)
            with self.assertRaisesRegex(
                    core.PreflightError, "lease 仍有效"):
                dbrun.resume_run(handle.partial_path)
            runtime_after = dbstate.load_runtime(handle.connection)
            self.assertEqual(runtime_before, runtime_after)
        finally:
            dbrun.close_handle(handle, release_lease=True)

    def test_saved_pause_releases_and_resumes_in_new_session(self) -> None:
        handle = self.create(session_id="1" * 32, lease_id="2" * 32)
        dbstate.request_pause(handle.connection, for_exit=True)
        dbstate.mark_paused(handle.connection, for_exit=True)
        dbrun.close_handle(handle, release_lease=True)
        preview = dbrun.inspect_resume(self.partial)
        self.assertEqual(("paused", "suggest", "missing", True), (
            preview.run_state,
            preview.resume_hint,
            preview.lease_classification,
            preview.active_session_ended,
        ))

        resumed = dbrun.resume_run(
            self.partial,
            session_id="3" * 32,
            lease_id="4" * 32,
        )
        try:
            runtime = dbstate.load_runtime(resumed.connection)
            self.assertEqual(("running", "3" * 32), (
                runtime.run_state, runtime.active_session_id))
            sessions = resumed.connection.execute(
                "SELECT session_number,session_kind,session_status"
                " FROM run_sessions ORDER BY session_number"
            ).fetchall()
            self.assertEqual(
                [(1, "initial", "saved"), (2, "resume", "active")],
                sessions,
            )
        finally:
            dbrun.close_handle(resumed, release_lease=True)

    def test_same_session_pause_crash_is_recovered_before_resume(self) -> None:
        handle = self.create(session_id="1" * 32, lease_id="2" * 32)
        dbstate.request_pause(handle.connection, for_exit=False)
        dbstate.mark_paused(handle.connection, for_exit=False)
        handle.connection.close()

        resumed = dbrun.resume_run(
            self.partial,
            session_id="3" * 32,
            lease_id="4" * 32,
            pid_alive=lambda _pid: False,
        )
        try:
            self.assertEqual("running", dbstate.load_runtime(
                resumed.connection).run_state)
            sessions = resumed.connection.execute(
                "SELECT session_number,session_status,end_reason"
                " FROM run_sessions ORDER BY session_number"
            ).fetchall()
            self.assertEqual("abandoned", sessions[0][1])
            self.assertEqual("owner_terminated", sessions[0][2])
            self.assertEqual((2, "active"), tuple(sessions[1][:2]))
            events = [row[0] for row in resumed.connection.execute(
                "SELECT event FROM run_state_events ORDER BY event_id")]
            self.assertIn("interrupted_recovered", events)
            self.assertEqual("resume_started", events[-1])
        finally:
            dbrun.close_handle(resumed, release_lease=True)

    def test_invalid_lease_is_visible_and_explicitly_recoverable(self) -> None:
        handle = self.create()
        handle.connection.close()
        with open(handle.lease_path, "wb") as stream:
            stream.write(b"{broken")
        preview = dbrun.inspect_resume(self.partial)
        self.assertEqual("invalid", preview.lease_classification)

        resumed = dbrun.resume_run(self.partial)
        try:
            self.assertEqual(
                "running", dbstate.load_runtime(resumed.connection).run_state)
            self.assertEqual(
                resumed.lease.lease_id,
                dbstate.read_lease_file(resumed.lease_path).lease_id,
            )
        finally:
            dbrun.close_handle(resumed, release_lease=True)

    def test_stopped_partial_requires_manual_resume(self) -> None:
        handle = self.create()
        dbstate.stop_run(handle.connection, reason="user_stop")
        dbrun.close_handle(handle, release_lease=True)
        with self.assertRaisesRegex(
                core.PreflightError, "明确手动恢复"):
            dbrun.resume_run(self.partial)
        resumed = dbrun.resume_run(self.partial, manual=True)
        try:
            self.assertEqual(
                "running", dbstate.load_runtime(resumed.connection).run_state)
        finally:
            dbrun.close_handle(resumed, release_lease=True)


class TestRunHeartbeat(_RunFixture):
    def test_missing_partial_is_not_recreated_by_heartbeat(self) -> None:
        handle = self.create()
        handle.connection.close()
        os.remove(handle.partial_path)
        with self.assertRaisesRegex(core.PreflightError, "无法读写打开"):
            dbrun.heartbeat_once(
                handle.partial_path,
                handle.lease_path,
                handle.lease.lease_id,
            )
        self.assertFalse(os.path.exists(handle.partial_path))
        dbstate.release_lease_file(
            handle.lease_path, handle.lease.lease_id)

    def test_heartbeat_updates_exact_file_and_database_session(self) -> None:
        handle = self.create()
        now = "2026-08-06T08:00:00.000000Z"
        try:
            refreshed = dbrun.heartbeat_once(
                handle.partial_path,
                handle.lease_path,
                handle.lease.lease_id,
                now_utc=now,
            )
            self.assertEqual(now, refreshed.heartbeat_at_utc)
            row = handle.connection.execute(
                "SELECT lease_heartbeat_at_utc,lease_expires_at_utc"
                " FROM run_sessions WHERE session_id=?",
                (handle.lease.session_id,),
            ).fetchone()
            self.assertEqual(now, row[0])
            self.assertEqual(refreshed.expires_at_utc, row[1])
            other = os.path.join(self.output_dir, "Other.partial.sqlite.lease")
            self.assertFalse(os.path.exists(other))
        finally:
            dbrun.close_handle(handle, release_lease=True)

    def test_heartbeat_object_beats_immediately_and_stops(self) -> None:
        handle = self.create()
        heartbeat = dbrun.LeaseHeartbeat(handle, interval_seconds=60)
        try:
            heartbeat.start()
            heartbeat.stop()
            self.assertIsNone(heartbeat.error)
            self.assertFalse(heartbeat._thread.is_alive())
        finally:
            dbrun.close_handle(handle, release_lease=True)


if __name__ == "__main__":
    unittest.main()
