"""DAISY v1.6.0 schema 4 partial 与 lease 生命周期测试。"""
from __future__ import annotations

import io
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import unittest


_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SCRIPT_DIR = os.path.dirname(_TEST_DIR)
_REPO_ROOT = os.path.dirname(_SCRIPT_DIR)
_LIB_DIR = os.path.join(_SCRIPT_DIR, "Lib")
_FIXTURE_DIR = os.path.join(_TEST_DIR, "Fixtures")
sys.path[:0] = [_TEST_DIR, _SCRIPT_DIR, _LIB_DIR, _FIXTURE_DIR]

import DBS_Hash_Worker_Fixture as worker_fixture
import Script_DAISY_Lib_DBS_01_Core as core
import Script_DAISY_Lib_DBS_08_State as dbstate
import Script_DAISY_Lib_DBS_09_Run as dbrun


_RUNTIME_ROOT = os.path.join(
    _REPO_ROOT, ".test_runtime", "v1_6_0", "run_lifecycle")


def _wait_for_eof(inbox: dbrun.ControlInbox) -> None:
    deadline = time.monotonic() + 2.0
    while not inbox.eof and time.monotonic() < deadline:
        time.sleep(0.005)
    if not inbox.eof:
        raise AssertionError("控制输入线程未在期限内读到 EOF")


class TestControlProtocol(unittest.TestCase):
    def test_all_actions_round_trip_as_one_utf8_json_line(self) -> None:
        commands = (
            dbrun.ControlCommand(1, "pause"),
            dbrun.ControlCommand(2, "continue", request_id="继续-2"),
            dbrun.ControlCommand(3, "save_exit"),
            dbrun.ControlCommand(4, "stop"),
            dbrun.ControlCommand(
                5,
                "timeout_decision",
                worker_pid=4242,
                decision="skip_and_record",
            ),
        )
        for command in commands:
            with self.subTest(action=command.action):
                encoded = dbrun.encode_control_command(command)
                self.assertTrue(encoded.endswith(b"\n"))
                self.assertEqual(1, encoded.count(b"\n"))
                self.assertEqual(command, dbrun.decode_control_line(encoded))

    def test_decoder_rejects_malformed_or_ambiguous_messages(self) -> None:
        bad_messages = (
            b"",
            b"not-json\n",
            b"[]\n",
            b'{"protocol":"other","sequence":1,"action":"pause"}\n',
            b'{"protocol":"daisy-control-v1","sequence":true,'
            b'"action":"pause"}\n',
            b'{"protocol":"daisy-control-v1","sequence":1,'
            b'"action":"unknown"}\n',
            b'{"protocol":"daisy-control-v1","sequence":1,'
            b'"action":"timeout_decision"}\n',
            b'{"protocol":"daisy-control-v1","sequence":1,'
            b'"action":"pause","worker_pid":1}\n',
            b'{"protocol":"daisy-control-v1","sequence":1,'
            b'"action":"pause"}\n\n',
            b"\xff\n",
            b"x" * (dbrun.CONTROL_MAX_LINE_BYTES + 1),
        )
        for message in bad_messages:
            with self.subTest(message=message[:40]):
                with self.assertRaises(ValueError):
                    dbrun.decode_control_line(message)

    def test_inbox_orders_messages_and_rejects_stale_sequence(self) -> None:
        first = dbrun.encode_control_command(
            dbrun.ControlCommand(1, "pause"))
        third = dbrun.encode_control_command(
            dbrun.ControlCommand(3, "stop"))
        second = dbrun.encode_control_command(
            dbrun.ControlCommand(2, "continue"))
        rejected = []
        stream = io.BytesIO(first + first + b"broken\n" + third + second)
        inbox = dbrun.ControlInbox(
            stream, on_rejected=rejected.append)
        inbox.start()
        _wait_for_eof(inbox)
        self.assertEqual(
            [(1, "pause"), (3, "stop")],
            [(item.sequence, item.action) for item in inbox.poll()],
        )
        self.assertEqual(
            ["stale_sequence", "invalid_message", "stale_sequence"],
            [item.code for item in rejected],
        )
        self.assertFalse(stream.closed)


class TestRunCommandRouter(unittest.TestCase):
    def test_running_lifecycle_action_is_first_wins(self) -> None:
        receipts = []
        router = dbrun.RunCommandRouter(on_receipt=receipts.append)
        barrier = threading.Barrier(3)
        results = []

        def route(command: dbrun.ControlCommand) -> None:
            barrier.wait()
            results.append(router.route(command))

        threads = (
            threading.Thread(
                target=route,
                args=(dbrun.ControlCommand(1, "pause"),),
            ),
            threading.Thread(
                target=route,
                args=(dbrun.ControlCommand(2, "stop"),),
            ),
        )
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join()
        self.assertEqual([False, True], sorted(
            receipt.accepted for receipt in results))
        self.assertIn(
            router.hash_control.current()[0], ("pause", "stop"))
        self.assertEqual(2, len(receipts))

    def test_timeout_decision_is_bound_to_current_worker(self) -> None:
        router = dbrun.RunCommandRouter()
        control = router.hash_control
        control.bind_worker(4242)
        try:
            self.assertTrue(control.open_timeout_decision(4242))
            stale = router.route(dbrun.ControlCommand(
                1,
                "timeout_decision",
                worker_pid=4343,
                decision="skip_and_record",
            ))
            accepted = router.route(dbrun.ControlCommand(
                2,
                "timeout_decision",
                worker_pid=4242,
                decision="skip_and_record",
            ))
            self.assertFalse(stale.accepted)
            self.assertTrue(accepted.accepted)
            choice = control.resolve_timeout_decision(
                4242, "continue_waiting")
            self.assertEqual(("skip_and_record", "user"), (
                choice.decision, choice.source))
        finally:
            control.unbind_worker(4242)

    def test_paused_wait_accepts_one_action_then_replaces_worker_control(
        self,
    ) -> None:
        router = dbrun.RunCommandRouter()
        old_control = router.hash_control
        router.enter_paused()
        rejected = router.route(dbrun.ControlCommand(1, "pause"))
        accepted = router.route(dbrun.ControlCommand(2, "continue"))
        duplicate = router.route(dbrun.ControlCommand(3, "stop"))
        self.assertFalse(rejected.accepted)
        self.assertTrue(accepted.accepted)
        self.assertFalse(duplicate.accepted)
        self.assertEqual("continue", router.wait_paused_action(0.01))
        new_control = router.begin_running()
        self.assertEqual("running", router.state)
        self.assertIsNot(old_control, new_control)
        self.assertIsNone(new_control.current())

    def test_ended_router_wakes_pause_wait_and_rejects_commands(self) -> None:
        router = dbrun.RunCommandRouter()
        router.enter_paused()
        result = []
        waiting = threading.Thread(
            target=lambda: result.append(router.wait_paused_action(2.0)))
        waiting.start()
        router.end()
        waiting.join(timeout=1.0)
        self.assertFalse(waiting.is_alive())
        self.assertEqual([None], result)
        receipt = router.route(dbrun.ControlCommand(1, "continue"))
        self.assertEqual((False, "run_ended"), (
            receipt.accepted, receipt.reason))


class TestControlInbox(unittest.TestCase):
    def test_inbox_discards_oversized_tail_and_bounds_queue(self) -> None:
        valid = dbrun.encode_control_command(
            dbrun.ControlCommand(2, "save_exit"))
        rejected = []
        stream = io.BytesIO(
            b"x" * (dbrun.CONTROL_MAX_LINE_BYTES + 50) + b"\n" + valid)
        inbox = dbrun.ControlInbox(
            stream, max_queue=1, on_rejected=rejected.append)
        inbox.start()
        _wait_for_eof(inbox)
        self.assertEqual((dbrun.ControlCommand(2, "save_exit"),),
                         inbox.poll())
        self.assertEqual(["line_too_long"], [item.code for item in rejected])

        rejected = []
        stream = io.BytesIO(
            dbrun.encode_control_command(dbrun.ControlCommand(1, "pause"))
            + dbrun.encode_control_command(dbrun.ControlCommand(2, "stop"))
        )
        inbox = dbrun.ControlInbox(
            stream, max_queue=1, on_rejected=rejected.append)
        inbox.start()
        _wait_for_eof(inbox)
        self.assertEqual((dbrun.ControlCommand(1, "pause"),), inbox.poll())
        self.assertEqual(["queue_full"], [item.code for item in rejected])

    def test_callback_delivery_does_not_fill_poll_queue(self) -> None:
        delivered = []
        rejected = []
        payload = json.dumps({
            "protocol": dbrun.CONTROL_PROTOCOL,
            "sequence": 1,
            "action": "continue",
        }).encode("utf-8") + b"\n"
        stream = io.BytesIO(payload)
        inbox = dbrun.ControlInbox(
            stream,
            on_command=delivered.append,
            on_rejected=rejected.append,
        )
        inbox.start()
        _wait_for_eof(inbox)
        inbox.stop()
        self.assertEqual([dbrun.ControlCommand(1, "continue")], delivered)
        self.assertEqual((), inbox.poll())
        self.assertEqual([], rejected)
        self.assertFalse(stream.closed)

    def test_inbox_rejects_unterminated_final_json_object(self) -> None:
        rejected = []
        encoded = dbrun.encode_control_command(
            dbrun.ControlCommand(1, "pause")).rstrip(b"\n")
        inbox = dbrun.ControlInbox(
            io.BytesIO(encoded), on_rejected=rejected.append)
        inbox.start()
        _wait_for_eof(inbox)
        self.assertEqual((), inbox.poll())
        self.assertEqual(
            ["unterminated_line"], [item.code for item in rejected])


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

    def add_hash_entry(self, handle: dbrun.RunHandle) -> str:
        path = os.path.join(self.root_path, "fixture.bin")
        with open(path, "wb") as stream:
            stream.write(b"abc")
        observed = core.now_utc_iso()
        modified = core.ns_to_utc_iso(os.stat(path).st_mtime_ns)
        handle.connection.execute(
            "INSERT INTO dirs"
            " (dir_id,root_id,rel_path,path_key,enum_status,observed_at_utc)"
            " VALUES (1,1,'','','ok',?)",
            (observed,),
        )
        handle.connection.execute(
            "INSERT INTO entries"
            " (entry_id,root_id,dir_id,rel_path,path_key,name,extension,"
            " media_kind,size_bytes,modified_at_utc,attributes,observed_at_utc,"
            " meta_status,hash_status) VALUES"
            " (1,1,1,'fixture.bin','fixture.bin','fixture.bin','bin','other',"
            " 3,?,0,?,'not_applicable','pending')",
            (modified, observed),
        )
        handle.connection.commit()
        return path


class TestControlledHashStage(_RunFixture):
    def _run_paused_action(self, action: str):
        handle = self.create()
        self.add_hash_entry(handle)
        router = dbrun.RunCommandRouter()
        events = []
        worker_starts = 0
        sequence = 0

        def on_event(event, **_payload) -> None:
            nonlocal worker_starts, sequence
            events.append(event)
            if event == "worker_started":
                worker_starts += 1
                if worker_starts == 1:
                    sequence += 1
                    receipt = router.route(dbrun.ControlCommand(
                        sequence, "pause"))
                    self.assertTrue(receipt.accepted)
            elif event == "run_paused":
                sequence += 1
                receipt = router.route(dbrun.ControlCommand(
                    sequence, action))
                self.assertTrue(receipt.accepted)

        try:
            stats = dbrun.run_hash_stage_controlled(
                handle.connection,
                "full",
                router,
                stall_seconds=1.0,
                timeout_seconds=2.0,
                on_event=on_event,
                poll_seconds=0.005,
                paused_wait_seconds=0.01,
            )
            runtime = dbstate.load_runtime(handle.connection)
            attempts = handle.connection.execute(
                "SELECT attempt_number,status FROM entry_attempts"
                " ORDER BY attempt_number"
            ).fetchall()
            session = handle.connection.execute(
                "SELECT session_status,ended_at_utc,end_reason"
                " FROM run_sessions WHERE session_id=?",
                (runtime.active_session_id,),
            ).fetchone()
            checkpoint = handle.connection.execute(
                "SELECT state,checkpoint_json FROM stage_checkpoints"
                " WHERE stage='hash'"
            ).fetchone()
            return stats, runtime, attempts, tuple(session), tuple(checkpoint), \
                events, router
        finally:
            dbrun.close_handle(handle, release_lease=True)

    def test_pause_then_continue_retries_file_in_same_session(self) -> None:
        (stats, runtime, attempts, session, checkpoint,
         events, router) = self._run_paused_action("continue")
        self.assertEqual(("completed", 1, 1), (
            stats["state"], stats["processed"], stats["done"]))
        self.assertEqual(("running", "none"), (
            runtime.run_state, runtime.resume_hint))
        self.assertEqual(
            [(1, "cancelled"), (2, "succeeded")], attempts)
        self.assertEqual("active", session[0])
        self.assertIsNone(session[1])
        self.assertEqual("completed", checkpoint[0])
        self.assertEqual(
            1, events.count("run_paused"))
        self.assertEqual(1, events.count("run_resumed"))
        self.assertEqual("running", router.state)

    def test_pause_then_save_ends_session_and_keeps_pending_file(self) \
            -> None:
        (stats, runtime, attempts, session, checkpoint,
         events, router) = self._run_paused_action("save_exit")
        self.assertEqual("save_exit", stats["state"])
        self.assertEqual(("paused", "suggest"), (
            runtime.run_state, runtime.resume_hint))
        self.assertEqual([(1, "cancelled")], attempts)
        self.assertEqual(("saved", "save_exit"), (
            session[0], session[2]))
        self.assertIsNotNone(session[1])
        self.assertEqual("paused", checkpoint[0])
        self.assertEqual(1, events.count("run_saved"))
        self.assertEqual("ended", router.state)

    def test_pause_then_stop_requires_manual_resume(self) -> None:
        (stats, runtime, attempts, session, checkpoint,
         events, router) = self._run_paused_action("stop")
        self.assertEqual("stopped", stats["state"])
        self.assertEqual(("stopped", "manual_only"), (
            runtime.run_state, runtime.resume_hint))
        self.assertEqual([(1, "cancelled")], attempts)
        self.assertEqual(("stopped", "user_stop"), (
            session[0], session[2]))
        self.assertIsNotNone(session[1])
        self.assertEqual("failed_recoverable", checkpoint[0])
        self.assertEqual(1, events.count("run_stopped"))
        self.assertEqual("ended", router.state)


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
