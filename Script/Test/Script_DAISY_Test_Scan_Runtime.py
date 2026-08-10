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
from unittest import mock


_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SCRIPT_DIR = os.path.dirname(_TEST_DIR)
_REPO_ROOT = os.path.dirname(_SCRIPT_DIR)
_LIB_DIR = os.path.join(_SCRIPT_DIR, "Lib")
_FIXTURE_DIR = os.path.join(_TEST_DIR, "Fixtures")
sys.path[:0] = [_TEST_DIR, _SCRIPT_DIR, _LIB_DIR, _FIXTURE_DIR]

import Hash_Worker_Fixture as worker_fixture
import Script_DAISY_Lib_Snapshot_Core as core
import Script_DAISY_Lib_Metadata as dbmeta
import Script_DAISY_Lib_File_Hash as dbhash
import Script_DAISY_Lib_Snapshot_Verify as dbverify
import Script_DAISY_Lib_Scan_State as dbstate
import Script_DAISY_Lib_Scan_Runtime as dbrun


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


class TestControlledIndependentHashStage(_RunFixture):
    @staticmethod
    def independent_outcome(
        digest: str | None,
        *,
        outcome: str = "completed",
        decision: str = "none",
        decision_source: str = "none",
        error: str | None = None,
    ) -> dbhash.IndependentHashOutcome:
        completed = 3 if digest is not None else 0
        return dbhash.IndependentHashOutcome(
            outcome=outcome,
            hash_hex=digest,
            error=error,
            decision=decision,
            decision_source=decision_source,
            size_bytes=3,
            bytes_read=completed,
            final_offset=completed,
            elapsed_seconds=0.01,
            active_read_seconds=0.01 if completed else 0.0,
            stall_count=0,
            longest_stall_seconds=0.01,
            first_stall_offset=None,
            last_stall_offset=None,
            threshold_count=0,
            worker_pid=7701,
            worker_exitcode=0,
            worker_reaped=True,
            events=(),
        )

    @staticmethod
    def primary_outcome(digest: str) -> dbhash.HashWorkerOutcome:
        return dbhash.HashWorkerOutcome(
            outcome="completed",
            result={
                "hash_hex": digest,
                "bytes_read": 3,
                "status": "valid",
            },
            decision="none",
            decision_source="none",
            size_bytes=3,
            bytes_read=3,
            final_offset=3,
            elapsed_seconds=0.01,
            active_read_seconds=0.01,
            stall_count=0,
            longest_stall_seconds=0.01,
            first_stall_offset=None,
            last_stall_offset=None,
            threshold_count=0,
            worker_pid=7702,
            worker_exitcode=0,
            worker_reaped=True,
            events=(),
        )

    def prepared(self) -> tuple[dbrun.RunHandle, str]:
        handle = self.create()
        self.add_hash_entry(handle)
        result = dbrun.run_hash_stage_controlled(
            handle.connection,
            "full",
            dbrun.RunCommandRouter(),
            stall_seconds=1.0,
            timeout_seconds=2.0,
            poll_seconds=0.005,
        )
        self.assertEqual("completed", result["state"])
        digest = handle.connection.execute(
            "SELECT hash_hex FROM hashes"
        ).fetchone()[0]
        return handle, digest

    def test_matching_sample_records_independent_attempt_and_performance(
        self,
    ) -> None:
        handle, digest = self.prepared()
        try:
            result = dbrun.run_independent_hash_stage_controlled(
                handle.connection,
                dbrun.RunCommandRouter(),
                percent=100,
                min_count=1,
                powershell_path="fixture-powershell",
                powershell_version="5.1.fixture",
                _independent_runner=lambda *_args, **_kwargs:
                self.independent_outcome(digest),
            )
            self.assertEqual(("completed", 1, 1, 1, 0), (
                result["state"],
                result["eligible"],
                result["sampled"],
                result["matched"],
                result["tool_error"],
            ))
            self.assertEqual(
                ("succeeded", "powershell-get-filehash", "5.1.fixture"),
                tuple(handle.connection.execute(
                    "SELECT status,tool_name,tool_version"
                    " FROM entry_attempts WHERE stage='verify_hash'"
                ).fetchone()),
            )
            self.assertEqual(("independent", 3), tuple(
                handle.connection.execute(
                    "SELECT origin,bytes_read FROM read_performance"
                    " WHERE stage='verify_hash'"
                ).fetchone()))
            self.assertEqual("done", handle.connection.execute(
                "SELECT hash_status FROM entries"
            ).fetchone()[0])
        finally:
            dbrun.close_handle(handle, release_lease=True)

    def test_persistent_disagreement_marks_current_hash_unstable(self) \
            -> None:
        handle, digest = self.prepared()
        wrong = "f" * 64 if digest != "f" * 64 else "e" * 64
        try:
            result = dbrun.run_independent_hash_stage_controlled(
                handle.connection,
                dbrun.RunCommandRouter(),
                percent=100,
                min_count=1,
                powershell_path="fixture-powershell",
                powershell_version="5.1.fixture",
                _independent_runner=lambda *_args, **_kwargs:
                self.independent_outcome(wrong),
                _primary_runner=lambda *_args, **_kwargs:
                self.primary_outcome(digest),
            )
            self.assertEqual((1, 0), (
                result["mismatched"], result["matched"]))
            self.assertEqual(("unstable", "unstable"), tuple(
                handle.connection.execute(
                    "SELECT e.hash_status,h.status FROM entries e"
                    " JOIN hashes h ON h.entry_id=e.entry_id"
                ).fetchone()))
            self.assertEqual(("unstable", "verify_mismatch"), tuple(
                handle.connection.execute(
                    "SELECT status,error_code FROM entry_attempts"
                    " WHERE stage='verify_hash'"
                ).fetchone()))
            self.assertEqual(("hash", "verify_mismatch"), tuple(
                handle.connection.execute(
                    "SELECT stage,error_code FROM errors"
                ).fetchone()))
        finally:
            dbrun.close_handle(handle, release_lease=True)

    def test_first_disagreement_that_both_rechecks_resolve_is_matched(
        self,
    ) -> None:
        handle, digest = self.prepared()
        wrong = "f" * 64 if digest != "f" * 64 else "e" * 64
        values = iter((wrong, digest))
        try:
            result = dbrun.run_independent_hash_stage_controlled(
                handle.connection,
                dbrun.RunCommandRouter(),
                percent=100,
                min_count=1,
                powershell_path="fixture-powershell",
                powershell_version="5.1.fixture",
                _independent_runner=lambda *_args, **_kwargs:
                self.independent_outcome(next(values)),
                _primary_runner=lambda *_args, **_kwargs:
                self.primary_outcome(digest),
            )
            self.assertEqual((1, 0), (
                result["matched"], result["mismatched"]))
            payload = json.loads(handle.connection.execute(
                "SELECT result_json FROM entry_attempts"
                " WHERE stage='verify_hash'"
            ).fetchone()[0])
            self.assertTrue(payload["initial_mismatch_resolved"])
            self.assertEqual("done", handle.connection.execute(
                "SELECT hash_status FROM entries"
            ).fetchone()[0])
        finally:
            dbrun.close_handle(handle, release_lease=True)

    def test_pause_continue_retries_cancelled_independent_attempt(self) \
            -> None:
        handle, digest = self.prepared()
        router = dbrun.RunCommandRouter()
        calls = 0

        def runner(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                self.assertTrue(router.route(
                    dbrun.ControlCommand(1, "pause")
                ).accepted)
                return self.independent_outcome(
                    None,
                    outcome="paused",
                    decision="stop_and_resume",
                    decision_source="user",
                )
            return self.independent_outcome(digest)

        def on_event(event, **_payload) -> None:
            if event == "run_paused":
                self.assertTrue(router.route(
                    dbrun.ControlCommand(2, "continue")
                ).accepted)

        try:
            result = dbrun.run_independent_hash_stage_controlled(
                handle.connection,
                router,
                percent=100,
                min_count=1,
                powershell_path="fixture-powershell",
                powershell_version="5.1.fixture",
                on_event=on_event,
                paused_wait_seconds=0.01,
                _independent_runner=runner,
            )
            self.assertEqual(("completed", 1, 2), (
                result["state"], result["matched"], calls))
            self.assertEqual(
                [(1, "cancelled"), (2, "succeeded")],
                handle.connection.execute(
                    "SELECT attempt_number,status FROM entry_attempts"
                    " WHERE stage='verify_hash' ORDER BY attempt_number"
                ).fetchall(),
            )
        finally:
            dbrun.close_handle(handle, release_lease=True)

    def test_hash_none_skips_without_powershell_capability(self) -> None:
        handle = self.create(config={
            "phase": "quick",
            "hash": "none",
            "metadata_storage": "normalized",
            "format_validation": "off",
        })
        try:
            result = dbrun.run_independent_hash_stage_controlled(
                handle.connection,
                dbrun.RunCommandRouter(),
                powershell_path="",
                powershell_version="",
            )
            self.assertEqual(("completed", 0), (
                result["state"], result["sampled"]))
            self.assertEqual("skipped", handle.connection.execute(
                "SELECT state FROM stage_checkpoints"
                " WHERE stage='verify_hash'"
            ).fetchone()[0])
        finally:
            dbrun.close_handle(handle, release_lease=True)


class TestControlledStageBoundaries(_RunFixture):
    def test_enumeration_pauses_at_boundary_then_restarts_cleanly(
        self,
    ) -> None:
        for name in ("a.bin", "b.bin"):
            with open(os.path.join(self.root_path, name), "wb") as stream:
                stream.write(name.encode("ascii"))
        handle = self.create()
        router = dbrun.RunCommandRouter()
        events = []

        def on_event(event, **_payload) -> None:
            events.append(event)
            if event == "stage_started" \
                    and events.count("stage_started") == 1:
                receipt = router.route(
                    dbrun.ControlCommand(1, "pause"))
                self.assertTrue(receipt.accepted)
            elif event == "run_paused":
                self.assertEqual("pending", handle.connection.execute(
                    "SELECT enum_status FROM roots").fetchone()[0])
                self.assertEqual(0, handle.connection.execute(
                    "SELECT COUNT(*) FROM entries").fetchone()[0])
                receipt = router.route(
                    dbrun.ControlCommand(2, "continue"))
                self.assertTrue(receipt.accepted)

        try:
            stats = dbrun.run_enumeration_stage_controlled(
                handle.connection,
                router,
                collect_file_id=False,
                on_event=on_event,
                on_progress=lambda _stats: None,
                paused_wait_seconds=0.01,
            )
            self.assertEqual("completed", stats["state"])
            self.assertEqual("running", dbstate.load_runtime(
                handle.connection).run_state)
            self.assertEqual("completed", handle.connection.execute(
                "SELECT state FROM stage_checkpoints"
                " WHERE stage='enumerate'").fetchone()[0])
            self.assertEqual(2, stats["files"])
            self.assertEqual(2, handle.connection.execute(
                "SELECT COUNT(*) FROM entries").fetchone()[0])
            self.assertEqual(1, events.count("run_paused"))
            self.assertEqual(1, events.count("run_resumed"))
            self.assertEqual(2, events.count("stage_started"))
            self.assertEqual(1, events.count("stage_restarted"))
            self.assertEqual(1, events.count("stage_finished"))
        finally:
            dbrun.close_handle(handle, release_lease=True)

    def test_stage_boundary_save_exit_is_distinct_from_stop(self) -> None:
        handle = self.create()
        router = dbrun.RunCommandRouter()
        tools = {
            "exiftool": {"path": "must-not-start", "version": "fixture"},
            "ffprobe": {"path": "must-not-start", "version": "fixture"},
            "sevenzip": {"path": "must-not-start", "version": "fixture"},
        }
        try:
            self.assertTrue(router.route(
                dbrun.ControlCommand(1, "save_exit")).accepted)
            stats = dbrun.run_metadata_stage_controlled(
                handle.connection, tools, router)
            runtime = dbstate.load_runtime(handle.connection)
            self.assertEqual("save_exit", stats["state"])
            self.assertEqual(("paused", "suggest"), (
                runtime.run_state, runtime.resume_hint))
            self.assertEqual("saved", handle.connection.execute(
                "SELECT session_status FROM run_sessions").fetchone()[0])
        finally:
            dbrun.close_handle(handle, release_lease=True)

    def test_stage_boundary_stop_requires_manual_resume(self) -> None:
        handle = self.create()
        router = dbrun.RunCommandRouter()
        try:
            self.assertTrue(router.route(
                dbrun.ControlCommand(1, "stop")).accepted)
            stats = dbrun.run_rescan_stage_controlled(
                handle.connection, router)
            runtime = dbstate.load_runtime(handle.connection)
            self.assertEqual("stopped", stats["state"])
            self.assertEqual(("stopped", "manual_only"), (
                runtime.run_state, runtime.resume_hint))
            self.assertEqual("stopped", handle.connection.execute(
                "SELECT session_status FROM run_sessions").fetchone()[0])
        finally:
            dbrun.close_handle(handle, release_lease=True)

    def test_metadata_and_rescan_hooks_stop_before_new_work(self) -> None:
        handle = self.create()
        tools = {
            "exiftool": {"path": "must-not-start", "version": "fixture"},
            "ffprobe": {"path": "must-not-start", "version": "fixture"},
            "sevenzip": {"path": "must-not-start", "version": "fixture"},
        }
        try:
            with self.assertRaises(core.StageControlBoundary):
                dbmeta.process_metadata_stage(
                    handle.connection,
                    tools,
                    should_stop=lambda: True,
                )
            with self.assertRaises(core.StageControlBoundary):
                core.rescan_check(
                    handle.connection, should_stop=lambda: True)
        finally:
            dbrun.close_handle(handle, release_lease=True)

    def test_metadata_pause_resume_uses_global_counts_and_current_opt_in(
        self,
    ) -> None:
        for name in ("a.bin", "b.bin"):
            with open(os.path.join(self.root_path, name), "wb") as stream:
                stream.write(name.encode("ascii"))
        handle = self.create()
        core.enumerate_and_reconcile(
            handle.connection, collect_file_id=False)
        router = dbrun.RunCommandRouter()
        events = []
        extracted = []

        class FakeExifToolWorker:
            def __init__(self, _path) -> None:
                pass

            def extract(
                _worker,
                file_path,
                photo_profile=False,
                timeout=None,
            ):
                extracted.append(os.path.basename(file_path))
                if len(extracted) == 1:
                    receipt = router.route(
                        dbrun.ControlCommand(1, "pause"))
                    self.assertTrue(receipt.accepted)
                return {"SourceFile": file_path}

            @staticmethod
            def close() -> None:
                pass

        def on_event(event, **payload) -> None:
            events.append((event, payload))
            if event == "run_paused":
                receipt = router.route(
                    dbrun.ControlCommand(2, "continue"))
                self.assertTrue(receipt.accepted)

        tools = {
            "exiftool": {"path": "fixture", "version": "fixture"},
            "ffprobe": {"path": "fixture", "version": "fixture"},
            "sevenzip": {"path": "fixture", "version": "fixture"},
        }
        try:
            with mock.patch.object(
                    dbmeta, "ExifToolWorker", FakeExifToolWorker):
                stats = dbrun.run_metadata_stage_controlled(
                    handle.connection,
                    tools,
                    router,
                    show_current_file=True,
                    on_event=on_event,
                    paused_wait_seconds=0.01,
                )
            self.assertEqual(("completed", 2, 2, 2), (
                stats["state"], stats["total"], stats["processed"],
                stats["done"],
            ))
            self.assertEqual(["a.bin", "b.bin"], extracted)
            self.assertEqual(2, handle.connection.execute(
                "SELECT COUNT(*) FROM raw_payloads").fetchone()[0])
            checkpoint = handle.connection.execute(
                "SELECT state,items_done,items_total"
                " FROM stage_checkpoints WHERE stage='metadata'"
            ).fetchone()
            self.assertEqual(("completed", 2, 2), tuple(checkpoint))
            names = [event for event, _payload in events]
            self.assertEqual(1, names.count("run_paused"))
            self.assertEqual(1, names.count("run_resumed"))
            self.assertGreaterEqual(names.count("current_item"), 1)
        finally:
            dbrun.close_handle(handle, release_lease=True)

    def test_metadata_current_item_producer_is_disabled_by_default(
        self,
    ) -> None:
        with open(os.path.join(self.root_path, "only.bin"), "wb") as stream:
            stream.write(b"fixture")
        handle = self.create()
        core.enumerate_and_reconcile(
            handle.connection, collect_file_id=False)
        events = []

        class FakeExifToolWorker:
            def __init__(self, _path) -> None:
                pass

            @staticmethod
            def extract(file_path, photo_profile=False, timeout=None):
                return {"SourceFile": file_path}

            @staticmethod
            def close() -> None:
                pass

        tools = {
            "exiftool": {"path": "fixture", "version": "fixture"},
            "ffprobe": {"path": "fixture", "version": "fixture"},
            "sevenzip": {"path": "fixture", "version": "fixture"},
        }
        try:
            with mock.patch.object(
                    dbmeta, "ExifToolWorker", FakeExifToolWorker):
                stats = dbrun.run_metadata_stage_controlled(
                    handle.connection,
                    tools,
                    dbrun.RunCommandRouter(),
                    on_event=lambda event, **_payload: events.append(event),
                )
            self.assertEqual("completed", stats["state"])
            self.assertNotIn("current_item", events)
        finally:
            dbrun.close_handle(handle, release_lease=True)


class TestControlledFormatStage(_RunFixture):
    class FakeFormatSession:
        def __init__(self, _tools) -> None:
            pass

        @staticmethod
        def describe(_extension, _media_kind):
            return dbverify.FormatValidatorSpec(
                "pdf", "fixture-validator", "1.fixture")

        @staticmethod
        def validate(_path, _media_kind, _spec):
            return "valid", None

        @staticmethod
        def close() -> None:
            pass

    def prepare_entries(self, handle: dbrun.RunHandle, count: int = 2) \
            -> None:
        for index in range(count):
            with open(
                os.path.join(self.root_path, f"file_{index}.pdf"),
                "wb",
            ) as stream:
                stream.write(b"%PDF-1.4\nstartxref\n0\n%%EOF\n")
        result = dbrun.run_enumeration_stage_controlled(
            handle.connection, dbrun.RunCommandRouter())
        self.assertEqual("completed", result["state"])

    def test_pause_continue_restarts_at_next_file_boundary(self) -> None:
        handle = self.create()
        self.prepare_entries(handle)
        router = dbrun.RunCommandRouter()
        events = []
        sequence = 0
        completed = 0

        def on_event(event, **_payload) -> None:
            nonlocal sequence, completed
            events.append(event)
            if event == "format_item_finished":
                completed += 1
                if completed == 1:
                    sequence += 1
                    self.assertTrue(router.route(
                        dbrun.ControlCommand(sequence, "pause")
                    ).accepted)
            elif event == "run_paused":
                sequence += 1
                self.assertTrue(router.route(
                    dbrun.ControlCommand(sequence, "continue")
                ).accepted)

        try:
            result = dbrun.run_format_stage_controlled(
                handle.connection,
                "all",
                {},
                router,
                show_current_file=True,
                on_event=on_event,
                paused_wait_seconds=0.01,
                _session_factory=self.FakeFormatSession,
            )
            self.assertEqual(("completed", 2, 2), (
                result["state"], result["processed"], result["valid"]))
            self.assertEqual(2, handle.connection.execute(
                "SELECT COUNT(*) FROM entry_attempts"
                " WHERE stage='format' AND status='succeeded'"
            ).fetchone()[0])
            self.assertEqual(("completed", 2, 2), tuple(
                handle.connection.execute(
                    "SELECT state,items_done,items_total"
                    " FROM stage_checkpoints WHERE stage='format'"
                ).fetchone()))
            self.assertIn("run_paused", events)
            self.assertIn("run_resumed", events)
            self.assertIn("current_item", events)
        finally:
            dbrun.close_handle(handle, release_lease=True)

    def test_small_sample_uses_minimum_and_invalid_percent_is_atomic(
        self,
    ) -> None:
        handle = self.create()
        self.prepare_entries(handle, count=3)
        try:
            for value in (-1, 101, float("nan"), True, "bad"):
                with self.subTest(value=value), self.assertRaises(
                        core.PreflightError):
                    dbrun.run_format_stage_controlled(
                        handle.connection,
                        "sample",
                        {},
                        dbrun.RunCommandRouter(),
                        sample_percent=value,
                        _session_factory=self.FakeFormatSession,
                    )
            self.assertEqual(0, handle.connection.execute(
                "SELECT COUNT(*) FROM format_checks").fetchone()[0])
            result = dbrun.run_format_stage_controlled(
                handle.connection,
                "sample",
                {},
                dbrun.RunCommandRouter(),
                sample_percent=10,
                _session_factory=self.FakeFormatSession,
            )
            self.assertEqual((3, 3, 3), (
                result["eligible"], result["selected"], result["valid"]))
            self.assertEqual(
                [("sample", 3)],
                handle.connection.execute(
                    "SELECT coverage,COUNT(*) FROM format_checks"
                    " GROUP BY coverage"
                ).fetchall(),
            )
        finally:
            dbrun.close_handle(handle, release_lease=True)

    def test_save_exit_then_new_session_finishes_pending_format(self) \
            -> None:
        handle = self.create()
        self.prepare_entries(handle)
        router = dbrun.RunCommandRouter()
        sequence = 0
        completed = 0

        def on_event(event, **_payload) -> None:
            nonlocal sequence, completed
            if event == "format_item_finished":
                completed += 1
                if completed == 1:
                    sequence += 1
                    self.assertTrue(router.route(
                        dbrun.ControlCommand(sequence, "pause")
                    ).accepted)
            elif event == "run_paused":
                sequence += 1
                self.assertTrue(router.route(
                    dbrun.ControlCommand(sequence, "save_exit")
                ).accepted)

        try:
            first = dbrun.run_format_stage_controlled(
                handle.connection,
                "all",
                {},
                router,
                on_event=on_event,
                paused_wait_seconds=0.01,
                _session_factory=self.FakeFormatSession,
            )
            self.assertEqual("save_exit", first["state"])
            self.assertEqual(
                [("pending", 1), ("valid", 1)],
                handle.connection.execute(
                    "SELECT status,COUNT(*) FROM format_checks"
                    " GROUP BY status ORDER BY status"
                ).fetchall(),
            )
        finally:
            dbrun.close_handle(handle, release_lease=True)

        resumed = dbrun.resume_run(self.partial)
        try:
            second = dbrun.run_format_stage_controlled(
                resumed.connection,
                "all",
                {},
                dbrun.RunCommandRouter(),
                _session_factory=self.FakeFormatSession,
            )
            self.assertEqual(("completed", 2, 2), (
                second["state"], second["processed"], second["valid"]))
            self.assertEqual(2, resumed.connection.execute(
                "SELECT COUNT(*) FROM run_sessions").fetchone()[0])
            self.assertEqual(
                [(1, "succeeded"), (1, "succeeded")],
                resumed.connection.execute(
                    "SELECT attempt_number,status FROM entry_attempts"
                    " WHERE stage='format' ORDER BY entry_id"
                ).fetchall(),
            )
        finally:
            dbrun.close_handle(resumed, release_lease=True)


class TestScanEvidencePipeline(_RunFixture):
    @staticmethod
    def tools() -> dict[str, dict[str, object]]:
        return {
            "exiftool": {"path": "fixture", "version": "13.fixture"},
            "ffprobe": {"path": "fixture", "version": "7.fixture"},
            "sevenzip": {"path": "fixture", "version": "24.fixture"},
        }

    class FakeExifToolWorker:
        def __init__(self, _path) -> None:
            pass

        @staticmethod
        def extract(file_path, photo_profile=False, timeout=None):
            return {"SourceFile": file_path}

        @staticmethod
        def close() -> None:
            pass

    def test_full_pipeline_uses_frozen_session_inputs_and_schema4_stages(
        self,
    ) -> None:
        with open(os.path.join(self.root_path, "file.bin"), "wb") as stream:
            stream.write(b"abc")
        handle = self.create(tool_versions=self.tools())
        events = []
        try:
            with mock.patch.object(
                    dbmeta, "ExifToolWorker", self.FakeExifToolWorker):
                result = dbrun.run_scan_evidence_stages(
                    handle,
                    dbrun.RunCommandRouter(),
                    show_current_file=False,
                    on_event=lambda event, **_payload: events.append(event),
                    hash_stall_seconds=1.0,
                    hash_timeout_seconds=2.0,
                    hash_poll_seconds=0.005,
                )
            self.assertEqual(("completed", "rescan"), (
                result["state"], result["stage"]))
            self.assertEqual(
                {"enumerate", "hash", "metadata", "format", "rescan"},
                set(result["stages"]),
            )
            entry = handle.connection.execute(
                "SELECT hash_status,meta_status FROM entries"
            ).fetchone()
            self.assertEqual(("done", "done"), tuple(entry))
            self.assertEqual(1, handle.connection.execute(
                "SELECT COUNT(*) FROM hashes WHERE status='valid'"
            ).fetchone()[0])
            self.assertEqual(1, handle.connection.execute(
                "SELECT COUNT(*) FROM raw_payloads"
            ).fetchone()[0])
            checkpoints = dict(handle.connection.execute(
                "SELECT stage,state FROM stage_checkpoints"
                " WHERE stage IN"
                " ('enumerate','hash','metadata','format','rescan')"
            ))
            self.assertEqual(
                {
                    "enumerate": "completed",
                    "hash": "completed",
                    "metadata": "completed",
                    "format": "skipped",
                    "rescan": "completed",
                },
                checkpoints,
            )
            versions = handle.connection.execute(
                "SELECT exiftool_version,ffprobe_version,sevenzip_version"
                " FROM snapshot_info"
            ).fetchone()
            self.assertEqual(
                ("13.fixture", "7.fixture", "24.fixture"),
                tuple(versions),
            )
            self.assertNotIn("current_item", events)
        finally:
            dbrun.close_handle(handle, release_lease=True)

    def test_quick_pipeline_never_starts_hash_or_metadata_tools(self) -> None:
        with open(os.path.join(self.root_path, "file.bin"), "wb") as stream:
            stream.write(b"abc")
        handle = self.create(
            config={
                "phase": "quick",
                "quick": True,
                "hash": "none",
                "no_file_id": True,
            },
            tool_versions={},
        )
        try:
            with mock.patch.object(
                    dbhash, "run_hash_worker",
                    side_effect=AssertionError("Quick 不应启动哈希 worker")), \
                    mock.patch.object(
                        dbmeta,
                        "ExifToolWorker",
                        side_effect=AssertionError(
                            "Quick 不应启动元数据工具"),
                    ):
                result = dbrun.run_scan_evidence_stages(
                    handle, dbrun.RunCommandRouter())
            self.assertEqual("completed", result["state"])
            self.assertEqual(("skipped", "not_applicable"), tuple(
                handle.connection.execute(
                    "SELECT hash_status,meta_status FROM entries"
                ).fetchone()))
            checkpoints = dict(handle.connection.execute(
                "SELECT stage,state FROM stage_checkpoints"
                " WHERE stage IN ('hash','metadata','format','rescan')"
            ))
            self.assertEqual(
                {"hash": "skipped", "metadata": "skipped",
                 "format": "skipped", "rescan": "completed"},
                checkpoints,
            )
        finally:
            dbrun.close_handle(handle, release_lease=True)

    def test_quick_pipeline_rejects_format_validation(self) -> None:
        handle = self.create(
            config={
                "phase": "quick",
                "quick": True,
                "hash": "none",
                "format_validation": "all",
            },
            tool_versions={},
        )
        try:
            with self.assertRaisesRegex(
                core.PreflightError, "快速扫描.*不能启用格式校验"):
                dbrun.run_scan_evidence_stages(
                    handle, dbrun.RunCommandRouter())
            self.assertEqual(0, handle.connection.execute(
                "SELECT COUNT(*) FROM entries").fetchone()[0])
        finally:
            dbrun.close_handle(handle, release_lease=True)

    def test_enabled_format_is_persisted_and_unsupported_is_not_error(
        self,
    ) -> None:
        files = {
            "valid.pdf": b"%PDF-1.4\nstartxref\n0\n%%EOF\n",
            "broken.pdf": b"%PDF-1.4\nmissing trailer",
            "unknown.bin": b"unknown",
        }
        for name, payload in files.items():
            with open(os.path.join(self.root_path, name), "wb") as stream:
                stream.write(payload)
        handle = self.create(
            config={
                "phase": "full",
                "hash": "full",
                "metadata_storage": "complete",
                "format_validation": "all",
            },
            tool_versions=self.tools(),
        )
        try:
            with mock.patch.object(
                    dbmeta, "ExifToolWorker", self.FakeExifToolWorker):
                result = dbrun.run_scan_evidence_stages(
                    handle,
                    dbrun.RunCommandRouter(),
                    hash_stall_seconds=1.0,
                    hash_timeout_seconds=2.0,
                    hash_poll_seconds=0.005,
                )
            self.assertEqual("completed", result["state"])
            self.assertEqual(
                {"valid": 1, "invalid": 1, "unsupported": 1},
                {
                    status: count
                    for status, count in handle.connection.execute(
                        "SELECT status,COUNT(*) FROM format_checks"
                        " GROUP BY status"
                    )
                },
            )
            self.assertEqual(
                [
                    ("broken.pdf", "invalid", "pdf"),
                    ("unknown.bin", "unsupported", "none"),
                    ("valid.pdf", "valid", "pdf"),
                ],
                handle.connection.execute(
                    "SELECT e.rel_path,f.status,f.validator"
                    " FROM format_checks f"
                    " JOIN entries e ON e.entry_id=f.entry_id"
                    " ORDER BY e.rel_path"
                ).fetchall(),
            )
            self.assertEqual(3, handle.connection.execute(
                "SELECT COUNT(*) FROM entry_attempts"
                " WHERE stage='format'").fetchone()[0])
            self.assertEqual(0, handle.connection.execute(
                "SELECT COUNT(*) FROM errors"
                " WHERE stage='format'").fetchone()[0])
            self.assertEqual(("completed", 1), tuple(
                handle.connection.execute(
                    "SELECT state,error_count FROM stage_checkpoints"
                    " WHERE stage='format'"
                ).fetchone()))
        finally:
            dbrun.close_handle(handle, release_lease=True)

    def test_format_sample_zero_executes_with_empty_coverage(self) -> None:
        with open(os.path.join(self.root_path, "file.pdf"), "wb") as stream:
            stream.write(b"%PDF-1.4\nstartxref\n0\n%%EOF\n")
        handle = self.create(
            config={
                "phase": "full",
                "hash": "full",
                "metadata_storage": "complete",
                "format_validation": "sample",
                "format_sample_percent": 0,
            },
            tool_versions=self.tools(),
        )
        try:
            with mock.patch.object(
                    dbmeta, "ExifToolWorker", self.FakeExifToolWorker):
                result = dbrun.run_scan_evidence_stages(
                    handle,
                    dbrun.RunCommandRouter(),
                    hash_stall_seconds=1.0,
                    hash_timeout_seconds=2.0,
                    hash_poll_seconds=0.005,
                )
            self.assertEqual("completed", result["state"])
            self.assertEqual(0, handle.connection.execute(
                "SELECT COUNT(*) FROM format_checks").fetchone()[0])
            checkpoint = handle.connection.execute(
                "SELECT state,items_done,items_total,checkpoint_json"
                " FROM stage_checkpoints WHERE stage='format'"
            ).fetchone()
            self.assertEqual(("completed", 0, 0), tuple(checkpoint[:3]))
            self.assertEqual(1, json.loads(checkpoint[3])["eligible"])
        finally:
            dbrun.close_handle(handle, release_lease=True)

    def test_format_sample_hundred_keeps_sample_coverage(self) -> None:
        with open(os.path.join(self.root_path, "file.pdf"), "wb") as stream:
            stream.write(b"%PDF-1.4\nstartxref\n0\n%%EOF\n")
        handle = self.create(
            config={
                "phase": "full",
                "hash": "full",
                "metadata_storage": "complete",
                "format_validation": "sample",
                "format_sample_percent": 100,
            },
            tool_versions=self.tools(),
        )
        try:
            with mock.patch.object(
                    dbmeta, "ExifToolWorker", self.FakeExifToolWorker):
                result = dbrun.run_scan_evidence_stages(
                    handle,
                    dbrun.RunCommandRouter(),
                    hash_stall_seconds=1.0,
                    hash_timeout_seconds=2.0,
                    hash_poll_seconds=0.005,
                )
            self.assertEqual("completed", result["state"])
            self.assertEqual(("sample", "valid"), tuple(
                handle.connection.execute(
                    "SELECT coverage,status FROM format_checks"
                ).fetchone()))
        finally:
            dbrun.close_handle(handle, release_lease=True)

    def test_output_subdirectory_inside_root_is_excluded(self) -> None:
        self.output_dir = os.path.join(self.root_path, "Snapshots")
        os.makedirs(self.output_dir)
        self.partial = os.path.join(
            self.output_dir, "Run.partial.sqlite")
        self.publish_stem = os.path.join(self.output_dir, "Run")
        with open(os.path.join(self.root_path, "kept.bin"), "wb") as stream:
            stream.write(b"kept")
        with open(os.path.join(self.output_dir, "old.sqlite"), "wb") as stream:
            stream.write(b"excluded")
        handle = self.create(tool_versions=self.tools())
        try:
            with mock.patch.object(
                    dbmeta, "ExifToolWorker", self.FakeExifToolWorker):
                result = dbrun.run_scan_evidence_stages(
                    handle,
                    dbrun.RunCommandRouter(),
                    hash_stall_seconds=1.0,
                    hash_timeout_seconds=2.0,
                    hash_poll_seconds=0.005,
                )
            self.assertEqual("completed", result["state"])
            self.assertEqual(
                [("kept.bin",)],
                handle.connection.execute(
                    "SELECT rel_path FROM entries ORDER BY rel_path"
                ).fetchall(),
            )
        finally:
            dbrun.close_handle(handle, release_lease=True)

    def test_output_directory_equal_to_root_excludes_owned_files(self) \
            -> None:
        self.output_dir = self.root_path
        self.partial = os.path.join(
            self.output_dir, "Run.partial.sqlite")
        self.publish_stem = os.path.join(self.output_dir, "Run")
        with open(os.path.join(self.root_path, "kept.bin"), "wb") as stream:
            stream.write(b"kept")
        handle = self.create(tool_versions=self.tools())
        runtime = dbstate.load_runtime(handle.connection)
        with open(runtime.event_log_path, "w", encoding="utf-8",
                  newline="\n") as stream:
            stream.write("owned\n")
        try:
            with mock.patch.object(
                    dbmeta, "ExifToolWorker", self.FakeExifToolWorker):
                result = dbrun.run_scan_evidence_stages(
                    handle,
                    dbrun.RunCommandRouter(),
                    hash_stall_seconds=1.0,
                    hash_timeout_seconds=2.0,
                    hash_poll_seconds=0.005,
                )
            self.assertEqual("completed", result["state"])
            self.assertEqual(
                [("kept.bin",)],
                handle.connection.execute(
                    "SELECT rel_path FROM entries ORDER BY rel_path"
                ).fetchall(),
            )
        finally:
            dbrun.close_handle(handle, release_lease=True)

    def test_save_exit_then_resume_retries_hash_and_finishes_pipeline(
        self,
    ) -> None:
        with open(os.path.join(self.root_path, "file.bin"), "wb") as stream:
            stream.write(b"abc")
        handle = self.create(tool_versions=self.tools())
        first_router = dbrun.RunCommandRouter()

        def save_on_worker_start(event, **_payload) -> None:
            if event == "worker_started":
                receipt = first_router.route(
                    dbrun.ControlCommand(1, "save_exit"))
                self.assertTrue(receipt.accepted)

        try:
            first = dbrun.run_scan_evidence_stages(
                handle,
                first_router,
                on_event=save_on_worker_start,
                hash_stall_seconds=1.0,
                hash_timeout_seconds=2.0,
                hash_poll_seconds=0.005,
            )
            self.assertEqual(("save_exit", "hash"), (
                first["state"], first["stage"]))
            self.assertEqual("pending", handle.connection.execute(
                "SELECT hash_status FROM entries").fetchone()[0])
        finally:
            dbrun.close_handle(handle, release_lease=True)

        resumed = dbrun.resume_run(self.partial)
        try:
            with mock.patch.object(
                    dbmeta, "ExifToolWorker", self.FakeExifToolWorker):
                second = dbrun.run_scan_evidence_stages(
                    resumed,
                    dbrun.RunCommandRouter(),
                    hash_stall_seconds=1.0,
                    hash_timeout_seconds=2.0,
                    hash_poll_seconds=0.005,
                )
            self.assertEqual("completed", second["state"])
            self.assertEqual(
                [(1, "cancelled"), (2, "succeeded")],
                resumed.connection.execute(
                    "SELECT attempt_number,status FROM entry_attempts"
                    " WHERE stage='hash' ORDER BY attempt_number"
                ).fetchall(),
            )
            self.assertEqual(("done", "done"), tuple(
                resumed.connection.execute(
                    "SELECT hash_status,meta_status FROM entries"
                ).fetchone()))
            self.assertEqual(2, resumed.connection.execute(
                "SELECT COUNT(*) FROM run_sessions").fetchone()[0])
        finally:
            dbrun.close_handle(resumed, release_lease=True)

    def test_pipeline_resumes_from_format_stage_through_rescan(self) -> None:
        for index in range(2):
            with open(
                os.path.join(self.root_path, f"file_{index}.pdf"),
                "wb",
            ) as stream:
                stream.write(b"%PDF-1.4\nstartxref\n0\n%%EOF\n")
        handle = self.create(
            config={
                "phase": "full",
                "hash": "full",
                "metadata_storage": "complete",
                "format_validation": "all",
            },
            tool_versions=self.tools(),
        )
        router = dbrun.RunCommandRouter()
        saved = False

        def save_after_first_format(event, **_payload) -> None:
            nonlocal saved
            if event == "format_item_finished" and not saved:
                saved = True
                self.assertTrue(router.route(
                    dbrun.ControlCommand(1, "save_exit")
                ).accepted)

        try:
            with mock.patch.object(
                    dbmeta, "ExifToolWorker", self.FakeExifToolWorker):
                first = dbrun.run_scan_evidence_stages(
                    handle,
                    router,
                    on_event=save_after_first_format,
                    hash_stall_seconds=1.0,
                    hash_timeout_seconds=2.0,
                    hash_poll_seconds=0.005,
                )
            self.assertEqual(("save_exit", "format"), (
                first["state"], first["stage"]))
            self.assertEqual(
                [("pending", 1), ("valid", 1)],
                handle.connection.execute(
                    "SELECT status,COUNT(*) FROM format_checks"
                    " GROUP BY status ORDER BY status"
                ).fetchall(),
            )
        finally:
            dbrun.close_handle(handle, release_lease=True)

        resumed = dbrun.resume_run(self.partial)
        try:
            with mock.patch.object(
                    dbmeta, "ExifToolWorker", self.FakeExifToolWorker):
                second = dbrun.run_scan_evidence_stages(
                    resumed,
                    dbrun.RunCommandRouter(),
                    hash_stall_seconds=1.0,
                    hash_timeout_seconds=2.0,
                    hash_poll_seconds=0.005,
                )
            self.assertEqual(("completed", "rescan"), (
                second["state"], second["stage"]))
            self.assertEqual(
                [("valid", 2)],
                resumed.connection.execute(
                    "SELECT status,COUNT(*) FROM format_checks"
                    " GROUP BY status"
                ).fetchall(),
            )
            self.assertEqual(2, resumed.connection.execute(
                "SELECT COUNT(*) FROM run_sessions").fetchone()[0])
            self.assertEqual(2, resumed.connection.execute(
                "SELECT COUNT(*) FROM entry_attempts"
                " WHERE stage='format' AND status='succeeded'"
            ).fetchone()[0])
        finally:
            dbrun.close_handle(resumed, release_lease=True)


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
                core.PreflightError, "明确手动续传"):
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
            self.assertTrue(heartbeat.alive)
            self.assertTrue(heartbeat.stop())
            self.assertIsNone(heartbeat.error)
            self.assertFalse(heartbeat.alive)
            with self.assertRaisesRegex(ValueError, "必须大于 0"):
                heartbeat.stop(0)
        finally:
            dbrun.close_handle(handle, release_lease=True)


if __name__ == "__main__":
    unittest.main()
