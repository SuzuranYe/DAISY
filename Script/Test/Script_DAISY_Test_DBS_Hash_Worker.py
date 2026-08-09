"""DAISY 哈希工作进程、无进展超时与处置测试。"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import sys
import tempfile
import threading
import unittest


_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SCRIPT_DIR = os.path.dirname(_TEST_DIR)
_REPO_ROOT = os.path.dirname(_SCRIPT_DIR)
_LIB_DIR = os.path.join(_SCRIPT_DIR, "Lib")
_FIXTURE_DIR = os.path.join(_TEST_DIR, "Fixtures")
sys.path[:0] = [_TEST_DIR, _SCRIPT_DIR, _LIB_DIR, _FIXTURE_DIR]

import DBS_Hash_Worker_Fixture as worker_fixture
import Script_DAISY_Lib_DBS_01_Core as core
import Script_DAISY_Lib_DBS_03_Hash as dbhash
import Script_DAISY_Lib_DBS_08_State as dbstate


_RUNTIME_ROOT = os.path.join(
    _REPO_ROOT, ".test_runtime", "v1_6_0", "hash_worker")
_NINE_GIB = 9 * 1024 ** 3


class _WorkerFixture(unittest.TestCase):
    def setUp(self) -> None:
        os.makedirs(_RUNTIME_ROOT, exist_ok=True)
        self._td = tempfile.TemporaryDirectory(
            prefix="case_", dir=_RUNTIME_ROOT)
        self.base = self._td.name
        self.path = os.path.join(self.base, "fixture.bin")
        with open(self.path, "wb") as handle:
            handle.write(b"abc")

    def tearDown(self) -> None:
        self._td.cleanup()

    def state_connection(self) -> sqlite3.Connection:
        con = sqlite3.connect(":memory:")
        dbstate.initialize_v4_connection(
            con,
            [("夹具", self.base)],
            {
                "phase": "full",
                "hash": "full",
                "metadata_storage": "complete",
                "format_validation": "off",
            },
            output_dir=self.base,
            partial_path=os.path.join(self.base, "scan.partial.sqlite"),
            publish_stem_path=os.path.join(self.base, "scan_final"),
            snapshot_uuid="1" * 32,
            session_id="2" * 32,
            lease_id="3" * 32,
            hostname="fixture-host",
            pid=4242,
            process_start_token="fixture-start",
        )
        stat_result = os.stat(self.path)
        con.execute(
            "INSERT INTO dirs"
            " (dir_id,root_id,rel_path,path_key,enum_status,observed_at_utc)"
            " VALUES (1,1,'','','ok',?)",
            (core.now_utc_iso(),),
        )
        con.execute(
            "INSERT INTO entries"
            " (entry_id,root_id,dir_id,rel_path,path_key,name,extension,"
            " media_kind,size_bytes,modified_at_utc,attributes,observed_at_utc,"
            " meta_status,hash_status) VALUES"
            " (1,1,1,'fixture.bin','fixture.bin','fixture.bin','bin','other',"
            " 3,?,0,?,'not_applicable','pending')",
            (
                core.ns_to_utc_iso(stat_result.st_mtime_ns),
                core.now_utc_iso(),
            ),
        )
        con.commit()
        return con


class TestHashTimeoutPolicy(unittest.TestCase):
    def test_timeout_scales_at_exact_nine_gib_boundaries(self) -> None:
        cases = (
            (0, 90),
            (_NINE_GIB - 1, 90),
            (_NINE_GIB, 90),
            (_NINE_GIB + 1, 180),
            (2 * _NINE_GIB, 180),
            (2 * _NINE_GIB + 1, 270),
            (5 * _NINE_GIB, 450),
        )
        for size_bytes, expected in cases:
            with self.subTest(size_bytes=size_bytes):
                self.assertEqual(
                    expected,
                    dbhash.hash_no_progress_timeout_for_size(size_bytes),
                )
        with self.assertRaises(ValueError):
            dbhash.hash_no_progress_timeout_for_size(-1)

    def test_atomic_timeout_decision_accepts_only_one_winner(self) -> None:
        decision = dbhash.AtomicTimeoutDecision()
        barrier = threading.Barrier(3)
        outcomes: list[bool] = []

        def choose(value: str) -> None:
            barrier.wait()
            outcomes.append(decision.choose(value, "user"))

        threads = [
            threading.Thread(target=choose, args=("skip_and_record",)),
            threading.Thread(target=choose, args=("stop_and_resume",)),
        ]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join()
        self.assertEqual([False, True], sorted(outcomes))
        self.assertIn(
            decision.resolve("continue_waiting").decision,
            ("skip_and_record", "stop_and_resume"),
        )

        default = dbhash.AtomicTimeoutDecision().resolve("continue_waiting")
        self.assertEqual(("continue_waiting", "default"), (
            default.decision, default.source))

    def test_worker_bound_choice_beats_default_and_stale_pid_is_rejected(
        self,
    ) -> None:
        control = dbhash.HashWorkerControl()
        control.bind_worker(1234)
        try:
            self.assertTrue(control.open_timeout_decision(1234))
            self.assertFalse(control.request_timeout_decision(
                4321, "skip_and_record"))
            self.assertTrue(control.request_timeout_decision(
                1234, "continue_waiting"))
            choice = control.resolve_timeout_decision(
                1234, "skip_and_record")
            self.assertEqual(("continue_waiting", "user"), (
                choice.decision, choice.source))
            self.assertTrue(control.open_timeout_decision(1234))
            default = control.resolve_timeout_decision(
                1234, "continue_waiting")
            self.assertEqual(("continue_waiting", "default"), (
                default.decision, default.source))
        finally:
            control.unbind_worker(1234)

    def test_timeout_choice_only_exists_during_current_worker_window(
        self,
    ) -> None:
        control = dbhash.HashWorkerControl()
        control.bind_worker(1234)
        try:
            self.assertFalse(control.request_timeout_decision(
                1234, "skip_and_record"))
            self.assertFalse(control.open_timeout_decision(4321))
            self.assertTrue(control.open_timeout_decision(1234))
            terminal = control.resolve_timeout_decision(
                1234, "skip_and_record")
            self.assertEqual(("skip_and_record", "advanced_policy"), (
                terminal.decision, terminal.source))
            self.assertFalse(control.request_timeout_decision(
                1234, "continue_waiting"))
            self.assertFalse(control.open_timeout_decision(1234))

            control.unbind_worker(1234)
            control.bind_worker(1234)
            self.assertTrue(control.open_timeout_decision(1234))
            self.assertTrue(control.request_timeout_decision(
                1234, "skip_and_record"))
            control.close_timeout_decision(1234)
            self.assertIsNone(control.take_timeout_decision(1234))
        finally:
            control.unbind_worker(1234)

    def test_terminal_timeout_choice_and_lifecycle_action_are_first_wins(
        self,
    ) -> None:
        control = dbhash.HashWorkerControl()
        control.bind_worker(1234)
        try:
            self.assertTrue(control.open_timeout_decision(1234))
            self.assertTrue(control.request_timeout_decision(
                1234, "skip_and_record"))
            self.assertFalse(control.request_pause())
            choice = control.take_timeout_decision(1234)
            self.assertEqual("skip_and_record", choice.decision)
            self.assertFalse(control.request_stop())
        finally:
            control.unbind_worker(1234)

        control = dbhash.HashWorkerControl()
        control.bind_worker(1234)
        try:
            self.assertTrue(control.open_timeout_decision(1234))
            self.assertTrue(control.request_pause())
            self.assertFalse(control.request_timeout_decision(
                1234, "skip_and_record"))
        finally:
            control.unbind_worker(1234)

    def test_first_lifecycle_action_wins_and_save_exit_is_distinct(self) \
            -> None:
        control = dbhash.HashWorkerControl()
        self.assertTrue(control.request_save_exit())
        self.assertFalse(control.request_pause())
        self.assertFalse(control.request_stop())
        self.assertEqual(("save_exit", "user"), control.current())


class _FakeExternalProcess:
    def __init__(
        self,
        *,
        stdout: bytes = b"",
        stderr: bytes = b"",
        returncode: int | None = 0,
        pid: int = 7701,
    ) -> None:
        self.pid = pid
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr
        self.terminate_calls = 0
        self.kill_calls = 0

    def poll(self):
        return self.returncode

    def terminate(self) -> None:
        self.terminate_calls += 1
        self.returncode = -15

    def kill(self) -> None:
        self.kill_calls += 1
        self.returncode = -9

    def communicate(self, timeout=None):
        del timeout
        return self._stdout, self._stderr


class TestIndependentHashProcess(_WorkerFixture):
    def test_valid_digest_uses_encoded_path_and_is_reaped(self) -> None:
        digest = hashlib.sha256(b"abc").hexdigest()
        process = _FakeExternalProcess(
            stdout=(digest.upper() + "\r\n").encode("ascii"))
        calls = []

        def factory(command, **kwargs):
            calls.append((command, kwargs))
            return process

        outcome = dbhash.run_independent_hash_process(
            self.path,
            os.path.join(self.base, "powershell.exe"),
            expected_size=3,
            stall_seconds=1.0,
            timeout_seconds=2.0,
            poll_seconds=0.001,
            _popen_factory=factory,
        )
        self.assertEqual(("completed", digest, 3, 3, True), (
            outcome.outcome,
            outcome.hash_hex,
            outcome.bytes_read,
            outcome.final_offset,
            outcome.worker_reaped,
        ))
        self.assertEqual(1, len(calls))
        self.assertIn("-EncodedCommand", calls[0][0])
        self.assertNotIn(
            os.path.abspath(self.path), "\0".join(calls[0][0]))
        self.assertEqual(0, process.terminate_calls)
        self.assertEqual(0, process.kill_calls)

    def test_ambiguous_output_is_tool_error_not_a_digest(self) -> None:
        process = _FakeExternalProcess(stdout=b"banner\nnot-a-hash\n")
        outcome = dbhash.run_independent_hash_process(
            self.path,
            os.path.join(self.base, "powershell.exe"),
            expected_size=3,
            stall_seconds=1.0,
            timeout_seconds=2.0,
            poll_seconds=0.001,
            _popen_factory=lambda _command, **_kwargs: process,
        )
        self.assertEqual("tool_error", outcome.outcome)
        self.assertIsNone(outcome.hash_hex)
        self.assertIn("唯一", outcome.error)

    def test_timeout_terminates_only_the_owned_process(self) -> None:
        process = _FakeExternalProcess(returncode=None)
        outcome = dbhash.run_independent_hash_process(
            self.path,
            os.path.join(self.base, "powershell.exe"),
            expected_size=3,
            stall_seconds=0.003,
            timeout_seconds=0.01,
            default_decision="skip_and_record",
            poll_seconds=0.001,
            _popen_factory=lambda _command, **_kwargs: process,
        )
        self.assertEqual("timeout", outcome.outcome)
        self.assertGreaterEqual(outcome.threshold_count, 1)
        self.assertEqual(1, process.terminate_calls)
        self.assertEqual(0, process.kill_calls)
        self.assertTrue(outcome.worker_reaped)

    def test_lifecycle_stop_uses_the_bound_worker_handle(self) -> None:
        process = _FakeExternalProcess(returncode=None)
        control = dbhash.HashWorkerControl()
        self.assertTrue(control.request_stop())
        outcome = dbhash.run_independent_hash_process(
            self.path,
            os.path.join(self.base, "powershell.exe"),
            expected_size=3,
            control=control,
            stall_seconds=1.0,
            timeout_seconds=2.0,
            poll_seconds=0.001,
            _popen_factory=lambda _command, **_kwargs: process,
        )
        self.assertEqual("stopped", outcome.outcome)
        self.assertEqual(1, process.terminate_calls)
        self.assertTrue(outcome.worker_reaped)


class TestReadPerformanceClassification(_WorkerFixture):
    def performance_connection(
        self,
        rates_mib: list[float],
        *,
        extensions: list[str] | None = None,
    ) -> sqlite3.Connection:
        con = sqlite3.connect(":memory:")
        dbstate.initialize_v4_connection(
            con,
            [("夹具", self.base)],
            {
                "phase": "full",
                "hash": "full",
                "metadata_storage": "complete",
                "format_validation": "off",
            },
            output_dir=self.base,
            partial_path=os.path.join(self.base, "perf.partial.sqlite"),
            publish_stem_path=os.path.join(self.base, "perf_final"),
            snapshot_uuid="6" * 32,
            session_id="7" * 32,
            lease_id="8" * 32,
            hostname="fixture-host",
            pid=4242,
            process_start_token="fixture-start",
        )
        con.execute(
            "UPDATE roots SET volume_serial='VOL-FIXTURE' WHERE root_id=1")
        now = core.now_utc_iso()
        con.execute(
            "INSERT INTO dirs"
            " (dir_id,root_id,rel_path,path_key,enum_status,observed_at_utc)"
            " VALUES (1,1,'','','ok',?)",
            (now,),
        )
        size = 8 * 1024 * 1024
        for index, rate_mib in enumerate(rates_mib, 1):
            extension = (
                extensions[index - 1] if extensions is not None else "bin")
            name = f"file_{index}.{extension}"
            con.execute(
                "INSERT INTO entries"
                " (entry_id,root_id,dir_id,rel_path,path_key,name,extension,"
                " media_kind,size_bytes,modified_at_utc,attributes,"
                " observed_at_utc,meta_status,hash_status)"
                " VALUES (?,1,1,?,?,?,?,?,?,?,0,?,'not_applicable','pending')",
                (
                    index,
                    name,
                    name,
                    name,
                    extension,
                    "other",
                    size,
                    now,
                    now,
                ),
            )
            attempt_id = dbstate.start_attempt(
                con,
                index,
                "hash",
                tool_name="fixture-hash",
                tool_version="1.fixture",
            )

            def writer(
                current: sqlite3.Connection,
                entry_id: int,
                _attempt_id: int,
            ) -> None:
                current.execute(
                    "INSERT INTO hashes"
                    " (entry_id,algorithm,hash_hex,origin,size_bytes,"
                    " bytes_read,status,tool,tool_version)"
                    " VALUES (?,'sha256',?,'computed',?,?,'valid',"
                    " 'fixture-hash','1.fixture')",
                    (entry_id, f"{entry_id:064x}", size, size),
                )

            active = size / (float(rate_mib) * 1024 ** 2)
            dbstate.finish_attempt(
                con,
                attempt_id,
                "succeeded",
                bytes_read=size,
                final_offset=size,
                end_reason="valid",
                performance={
                    "origin": "computed",
                    "size_bytes": size,
                    "bytes_read": size,
                    "elapsed_seconds": active,
                    "active_read_seconds": active,
                    "stall_count": 0,
                    "longest_stall_seconds": 0.0,
                    "first_stall_offset": None,
                    "last_stall_offset": None,
                    "final_offset": size,
                    "ended_reason": "valid",
                },
                _current_writer=writer,
            )
        return con

    def test_same_volume_type_and_size_group_marks_low_and_high(self) \
            -> None:
        con = self.performance_connection(
            [100.0] * 8 + [40.0, 20.0])
        try:
            result = dbhash.classify_read_performance_candidates(con)
            self.assertEqual((10, 1, 1, 1, False), (
                result["eligible"],
                result["throughput_groups"],
                result["low"],
                result["high"],
                result["physical_location_claimed"],
            ))
            rows = con.execute(
                "SELECT candidate_confidence,candidate_reason"
                " FROM read_performance ORDER BY performance_id"
            ).fetchall()
            self.assertEqual(["none"] * 8 + ["low", "high"], [
                row[0] for row in rows])
            self.assertIn("读取性能异常候选", rows[-1][1])
            self.assertIn("不能据此认定物理坏区", rows[-1][1])
        finally:
            con.close()

    def test_different_type_is_not_used_as_a_peer_group(self) -> None:
        con = self.performance_connection(
            [100.0] * 8 + [10.0],
            extensions=["bin"] * 8 + ["jpg"],
        )
        try:
            result = dbhash.classify_read_performance_candidates(con)
            self.assertEqual((1, 0, 0), (
                result["throughput_groups"],
                result["low"],
                result["high"],
            ))
            self.assertEqual(
                {"none"},
                {row[0] for row in con.execute(
                    "SELECT candidate_confidence FROM read_performance")},
            )
        finally:
            con.close()

    def test_dynamic_timeout_stall_is_high_even_without_peer_group(self) \
            -> None:
        con = self.performance_connection([100.0])
        try:
            con.execute(
                "UPDATE read_performance SET stall_count=1,"
                " longest_stall_seconds=90.0,"
                " candidate_confidence='low',"
                " candidate_reason='读取性能异常候选：旧值'")
            con.commit()
            result = dbhash.classify_read_performance_candidates(con)
            self.assertEqual((0, 1), (result["low"], result["high"]))
            confidence, reason = con.execute(
                "SELECT candidate_confidence,candidate_reason"
                " FROM read_performance"
            ).fetchone()
            self.assertEqual("high", confidence)
            self.assertIn("动态阈值", reason)
        finally:
            con.close()

    def test_reused_rows_are_reset_but_never_classified(self) -> None:
        con = self.performance_connection([10.0])
        try:
            con.execute(
                "UPDATE read_performance SET origin='reused',"
                " candidate_confidence='high',"
                " candidate_reason='读取性能异常候选：旧值'")
            con.execute(
                "UPDATE hashes SET origin='reused',"
                " source_snapshot_uuid=?,source_computed_at_utc=?",
                ("9" * 32, core.now_utc_iso()),
            )
            con.commit()
            result = dbhash.classify_read_performance_candidates(con)
            self.assertEqual((0, 1, 0), (
                result["eligible"],
                result["excluded_reused"],
                result["high"],
            ))
            self.assertEqual(("none", None), tuple(con.execute(
                "SELECT candidate_confidence,candidate_reason"
                " FROM read_performance"
            ).fetchone()))
        finally:
            con.close()


class TestHashWorkerSupervisor(_WorkerFixture):
    def test_stall_choice_targets_current_worker_and_skips_immediately(
        self,
    ) -> None:
        control = dbhash.HashWorkerControl()
        accepted = []

        def choose_on_stall(event, **payload) -> None:
            if event == "stall":
                accepted.append(control.request_timeout_decision(
                    payload["worker_pid"], "skip_and_record"))

        outcome = dbhash.run_hash_worker(
            self.path,
            expected_size=3,
            stall_seconds=0.02,
            timeout_seconds=1.0,
            control=control,
            on_event=choose_on_stall,
            poll_seconds=0.005,
            _worker_target=worker_fixture.blocking_worker,
        )
        self.assertEqual([True], accepted)
        self.assertEqual(("timeout", "skip_and_record", "user"), (
            outcome.outcome, outcome.decision, outcome.decision_source))
        self.assertEqual(0, outcome.threshold_count)
        self.assertTrue(outcome.worker_reaped)
        events = [item["event"] for item in outcome.events]
        self.assertIn("stall_decided", events)
        self.assertNotIn("threshold_reached", events)

    def test_real_worker_hashes_known_vector_and_is_reaped(self) -> None:
        outcome = dbhash.run_hash_worker(
            self.path,
            expected_size=3,
            stall_seconds=1.0,
            timeout_seconds=2.0,
            poll_seconds=0.01,
        )
        self.assertEqual("completed", outcome.outcome)
        self.assertEqual("valid", outcome.result["status"])
        self.assertEqual(
            hashlib.sha256(b"abc").hexdigest(),
            outcome.result["hash_hex"],
        )
        self.assertEqual(3, outcome.bytes_read)
        self.assertEqual(0, outcome.threshold_count)
        self.assertTrue(outcome.worker_reaped)
        self.assertEqual(0, outcome.worker_exitcode)

    def test_continuous_progress_can_outlive_timeout_window(self) -> None:
        outcome = dbhash.run_hash_worker(
            self.path,
            expected_size=5,
            stall_seconds=0.05,
            timeout_seconds=0.055,
            poll_seconds=0.005,
            _worker_target=worker_fixture.progressive_worker,
        )
        self.assertEqual("completed", outcome.outcome)
        self.assertGreater(outcome.elapsed_seconds, 0.055)
        self.assertEqual(0, outcome.threshold_count)
        self.assertEqual(5, outcome.bytes_read)
        self.assertTrue(outcome.worker_reaped)

    def test_continue_waiting_preserves_event_then_completes(self) -> None:
        choices = []

        def choose(_context, decision) -> None:
            choices.append(decision.choose("continue_waiting", "user"))

        outcome = dbhash.run_hash_worker(
            self.path,
            expected_size=1,
            stall_seconds=0.02,
            timeout_seconds=0.05,
            poll_seconds=0.005,
            on_threshold=choose,
            _worker_target=worker_fixture.delayed_worker,
        )
        self.assertEqual("completed", outcome.outcome)
        self.assertEqual([True], choices)
        self.assertEqual(1, outcome.threshold_count)
        self.assertEqual("continue_waiting", outcome.decision)
        self.assertEqual("user", outcome.decision_source)
        events = [item["event"] for item in outcome.events]
        self.assertIn("stall", events)
        self.assertIn("threshold_reached", events)
        self.assertIn("worker_completed", events)

    def test_default_continue_never_silently_skips_blocked_worker(self) -> None:
        control = dbhash.HashWorkerControl()

        def stop_after_three(context, _decision) -> None:
            if context["threshold_count"] == 3:
                control.request_pause()

        outcome = dbhash.run_hash_worker(
            self.path,
            expected_size=3,
            stall_seconds=0.02,
            timeout_seconds=0.05,
            default_decision="continue_waiting",
            control=control,
            on_threshold=stop_after_three,
            poll_seconds=0.005,
            _worker_target=worker_fixture.blocking_worker,
        )
        self.assertEqual("paused", outcome.outcome)
        self.assertEqual("stop_and_resume", outcome.decision)
        self.assertGreaterEqual(outcome.threshold_count, 3)
        timeout_decisions = [
            item for item in outcome.events
            if item["event"] == "threshold_decided"
        ]
        self.assertEqual({"continue_waiting"}, {
            item["decision"] for item in timeout_decisions
        })
        self.assertTrue(outcome.worker_reaped)

    def test_skip_and_stop_policies_reap_only_owned_worker(self) -> None:
        for policy, expected_outcome in (
                ("skip_and_record", "timeout"),
                ("stop_and_resume", "stopped")):
            with self.subTest(policy=policy):
                outcome = dbhash.run_hash_worker(
                    self.path,
                    expected_size=3,
                    stall_seconds=0.02,
                    timeout_seconds=0.05,
                    default_decision=policy,
                    poll_seconds=0.005,
                    _worker_target=worker_fixture.blocking_worker,
                )
                self.assertEqual(expected_outcome, outcome.outcome)
                self.assertEqual(policy, outcome.decision)
                self.assertEqual("advanced_policy", outcome.decision_source)
                self.assertEqual(1, outcome.threshold_count)
                self.assertTrue(outcome.worker_reaped)
                self.assertIsNotNone(outcome.worker_exitcode)

    def test_worker_crash_without_result_is_not_valid_hash(self) -> None:
        outcome = dbhash.run_hash_worker(
            self.path,
            expected_size=3,
            stall_seconds=0.2,
            timeout_seconds=1.0,
            poll_seconds=0.005,
            worker_start_timeout_seconds=1.0,
            _worker_target=worker_fixture.crashing_worker,
        )
        self.assertEqual("crashed", outcome.outcome)
        self.assertIsNone(outcome.result)
        self.assertTrue(outcome.worker_reaped)
        self.assertNotEqual(0, outcome.worker_exitcode)


class TestSchema4HashAttempt(_WorkerFixture):
    def test_save_exit_action_is_not_conflated_with_same_session_pause(
        self,
    ) -> None:
        con = self.state_connection()
        control = dbhash.HashWorkerControl()

        def save_after_start(event, **_payload) -> None:
            if event == "worker_started":
                control.request_save_exit()

        try:
            outcome = dbhash.process_hash_attempt_v4(
                con,
                1,
                self.path,
                control=control,
                on_event=save_after_start,
                _worker_target=worker_fixture.blocking_worker,
            )
            self.assertEqual("save_exit", outcome.outcome)
            runtime = dbstate.load_runtime(con)
            self.assertEqual(("paused", "suggest"), (
                runtime.run_state, runtime.resume_hint))
            self.assertEqual("saved", con.execute(
                "SELECT session_status FROM run_sessions"
            ).fetchone()[0])
            self.assertEqual("pending", con.execute(
                "SELECT hash_status FROM entries").fetchone()[0])
        finally:
            con.close()

    def test_success_commits_attempt_current_hash_and_performance(self) -> None:
        con = self.state_connection()
        try:
            outcome = dbhash.process_hash_attempt_v4(
                con,
                1,
                self.path,
                stall_seconds=1.0,
                timeout_seconds=2.0,
                poll_seconds=0.01,
            )
            self.assertEqual("completed", outcome.outcome)
            self.assertEqual(("succeeded", "none", "none"), tuple(
                con.execute(
                    "SELECT status,decision,decision_source"
                    " FROM entry_attempts").fetchone()))
            current = con.execute(
                "SELECT hash_hex,status,bytes_read FROM hashes"
            ).fetchone()
            self.assertEqual(
                (hashlib.sha256(b"abc").hexdigest(), "valid", 3),
                tuple(current),
            )
            self.assertEqual("done", con.execute(
                "SELECT hash_status FROM entries WHERE entry_id=1"
            ).fetchone()[0])
            performance = con.execute(
                "SELECT bytes_read,final_offset,ended_reason"
                " FROM read_performance").fetchone()
            self.assertEqual((3, 3, "valid"), tuple(performance))
            self.assertEqual(0, con.execute(
                "SELECT COUNT(*) FROM errors").fetchone()[0])
        finally:
            con.close()

    def test_timeout_retry_preserves_history_but_replaces_current_error(self) \
            -> None:
        con = self.state_connection()
        try:
            timed_out = dbhash.process_hash_attempt_v4(
                con,
                1,
                self.path,
                stall_seconds=0.02,
                timeout_seconds=0.05,
                default_decision="skip_and_record",
                poll_seconds=0.005,
                _worker_target=worker_fixture.blocking_worker,
            )
            self.assertEqual("timeout", timed_out.outcome)
            first = con.execute(
                "SELECT status,decision,decision_source FROM entry_attempts"
            ).fetchone()
            self.assertEqual(
                ("timeout", "skip_and_record", "advanced_policy"),
                tuple(first),
            )
            self.assertEqual("error", con.execute(
                "SELECT hash_status FROM entries WHERE entry_id=1"
            ).fetchone()[0])
            self.assertEqual(("failed", "no_progress_timeout"), tuple(
                con.execute(
                    "SELECT status,failure_reason FROM hashes").fetchone()))
            self.assertEqual("hash_timeout", con.execute(
                "SELECT error_code FROM errors").fetchone()[0])

            completed = dbhash.process_hash_attempt_v4(
                con,
                1,
                self.path,
                stall_seconds=1.0,
                timeout_seconds=2.0,
                poll_seconds=0.01,
            )
            self.assertEqual("completed", completed.outcome)
            self.assertEqual(
                [(1, "timeout"), (2, "succeeded")],
                con.execute(
                    "SELECT attempt_number,status FROM entry_attempts"
                    " ORDER BY attempt_number").fetchall(),
            )
            self.assertEqual(1, con.execute(
                "SELECT COUNT(*) FROM hashes").fetchone()[0])
            self.assertEqual("valid", con.execute(
                "SELECT status FROM hashes").fetchone()[0])
            self.assertEqual(0, con.execute(
                "SELECT COUNT(*) FROM errors").fetchone()[0])
        finally:
            con.close()

    def test_stop_keeps_manual_resume_and_restarts_file_from_zero(self) -> None:
        con = self.state_connection()
        try:
            stopped = dbhash.process_hash_attempt_v4(
                con,
                1,
                self.path,
                stall_seconds=0.02,
                timeout_seconds=0.05,
                default_decision="stop_and_resume",
                poll_seconds=0.005,
                _worker_target=worker_fixture.blocking_worker,
            )
            self.assertEqual("stopped", stopped.outcome)
            runtime = dbstate.load_runtime(con)
            self.assertEqual(("stopped", "manual_only"), (
                runtime.run_state, runtime.resume_hint))
            self.assertEqual("cancelled", con.execute(
                "SELECT status FROM entry_attempts").fetchone()[0])
            self.assertEqual("pending", con.execute(
                "SELECT hash_status FROM entries").fetchone()[0])
            self.assertEqual(0, con.execute(
                "SELECT COUNT(*) FROM hashes").fetchone()[0])
            dbstate.start_resume_session(
                con,
                config={},
                tools={},
                manual=True,
                session_id="4" * 32,
                lease_id="5" * 32,
                hostname="fixture-host",
                pid=4343,
                process_start_token="resume-start",
            )
            resumed = dbhash.process_hash_attempt_v4(
                con,
                1,
                self.path,
                stall_seconds=1.0,
                timeout_seconds=2.0,
                poll_seconds=0.01,
            )
            self.assertEqual("completed", resumed.outcome)
            attempts = con.execute(
                "SELECT attempt_number,status,final_offset"
                " FROM entry_attempts ORDER BY attempt_number").fetchall()
            self.assertEqual(
                [(1, "cancelled", 0), (2, "succeeded", 3)], attempts)
        finally:
            con.close()

    def test_save_pause_ends_session_and_resumes_with_new_attempt(self) -> None:
        con = self.state_connection()
        control = dbhash.HashWorkerControl()

        def pause_at_threshold(_context, _decision) -> None:
            control.request_pause()

        try:
            paused = dbhash.process_hash_attempt_v4(
                con,
                1,
                self.path,
                stall_seconds=0.02,
                timeout_seconds=0.05,
                control=control,
                save_on_pause=True,
                on_threshold=pause_at_threshold,
                poll_seconds=0.005,
                _worker_target=worker_fixture.blocking_worker,
            )
            self.assertEqual("paused", paused.outcome)
            runtime = dbstate.load_runtime(con)
            self.assertEqual(("paused", "suggest"), (
                runtime.run_state, runtime.resume_hint))
            self.assertEqual("saved", con.execute(
                "SELECT session_status FROM run_sessions"
                " WHERE session_number=1").fetchone()[0])
            dbstate.start_resume_session(
                con,
                config={},
                tools={},
                session_id="4" * 32,
                lease_id="5" * 32,
                hostname="fixture-host",
                pid=4343,
                process_start_token="resume-start",
            )
            resumed = dbhash.process_hash_attempt_v4(
                con,
                1,
                self.path,
                stall_seconds=1.0,
                timeout_seconds=2.0,
                poll_seconds=0.01,
            )
            self.assertEqual("completed", resumed.outcome)
            self.assertEqual(
                [(1, "cancelled"), (2, "succeeded")],
                con.execute(
                    "SELECT attempt_number,status FROM entry_attempts"
                    " ORDER BY attempt_number").fetchall(),
            )
        finally:
            con.close()

    def test_worker_crash_records_error_and_never_valid_hash(self) -> None:
        con = self.state_connection()
        try:
            outcome = dbhash.process_hash_attempt_v4(
                con,
                1,
                self.path,
                stall_seconds=0.2,
                timeout_seconds=1.0,
                poll_seconds=0.005,
                _worker_target=worker_fixture.crashing_worker,
            )
            self.assertEqual("crashed", outcome.outcome)
            self.assertEqual("error", con.execute(
                "SELECT hash_status FROM entries").fetchone()[0])
            self.assertEqual("failed", con.execute(
                "SELECT status FROM hashes").fetchone()[0])
            self.assertIsNone(con.execute(
                "SELECT hash_hex FROM hashes").fetchone()[0])
            self.assertEqual("hash_worker_crash", con.execute(
                "SELECT error_code FROM errors").fetchone()[0])
        finally:
            con.close()


class TestSchema4HashStage(_WorkerFixture):
    def test_full_stage_completes_checkpoint_and_current_item_is_opt_in(
        self,
    ) -> None:
        con = self.state_connection()
        events = []
        try:
            stats = dbhash.process_hash_stage_v4(
                con,
                "full",
                stall_seconds=1.0,
                timeout_seconds=2.0,
                show_current_file=False,
                on_event=lambda event, **_payload: events.append(event),
                poll_seconds=0.01,
            )
            self.assertEqual("completed", stats["state"])
            self.assertEqual((1, 1, 3), (
                stats["processed"], stats["done"], stats["bytes_read"]))
            self.assertNotIn("current_item", events)
            checkpoint = con.execute(
                "SELECT state,items_done,items_total,bytes_done,bytes_total"
                " FROM stage_checkpoints WHERE stage='hash'"
            ).fetchone()
            self.assertEqual(("completed", 1, 1, 3, 3), tuple(checkpoint))
        finally:
            con.close()

        con = self.state_connection()
        events = []
        try:
            dbhash.process_hash_stage_v4(
                con,
                "full",
                stall_seconds=1.0,
                timeout_seconds=2.0,
                show_current_file=True,
                on_event=lambda event, **_payload: events.append(event),
                poll_seconds=0.01,
            )
            self.assertIn("current_item", events)
        finally:
            con.close()

    def test_incremental_reuse_has_attempt_and_zero_read_performance(self) \
            -> None:
        con = self.state_connection()
        previous = dbhash.PreviousSnapshot(
            "fixture.sqlite",
            "a" * 32,
            {
                ("夹具", "fixture.bin"): {
                    "size": 3,
                    "mtime": con.execute(
                        "SELECT modified_at_utc FROM entries"
                    ).fetchone()[0],
                    "placeholder": 0,
                    "volume_serial": None,
                    "file_index_hex": None,
                    "hash_hex": hashlib.sha256(b"abc").hexdigest(),
                    "source": (
                        "a" * 32,
                        "2026-08-06T00:00:00.000000Z",
                    ),
                    "tool": "python-hashlib",
                    "tool_version": "fixture",
                },
            },
        )
        try:
            stats = dbhash.process_hash_stage_v4(
                con, "incremental", previous=previous)
            self.assertEqual((1, 1, 1), (
                stats["processed"], stats["done"], stats["reused"]))
            self.assertEqual(("reused", "valid", 0), tuple(con.execute(
                "SELECT origin,status,bytes_read FROM hashes").fetchone()))
            self.assertEqual(("succeeded", "reused"), tuple(con.execute(
                "SELECT status,end_reason FROM entry_attempts").fetchone()))
            self.assertEqual(("reused", 0), tuple(con.execute(
                "SELECT origin,bytes_read FROM read_performance").fetchone()))
        finally:
            con.close()

    def test_transient_retry_reprocesses_timeout_but_keeps_history(self) \
            -> None:
        con = self.state_connection()
        try:
            first = dbhash.process_hash_stage_v4(
                con,
                "full",
                stall_seconds=0.02,
                timeout_seconds=0.05,
                default_decision="skip_and_record",
                poll_seconds=0.005,
                _worker_target=worker_fixture.blocking_worker,
            )
            self.assertEqual(("completed", 1), (
                first["state"], first["timeout"]))
            retried = dbhash.process_hash_stage_v4(
                con,
                "full",
                retry_mode="transient",
                stall_seconds=1.0,
                timeout_seconds=2.0,
                poll_seconds=0.01,
            )
            self.assertEqual(("completed", 1, 0), (
                retried["state"], retried["done"], retried["timeout"]))
            self.assertEqual(
                [(1, "timeout"), (2, "succeeded")],
                con.execute(
                    "SELECT attempt_number,status FROM entry_attempts"
                    " ORDER BY attempt_number").fetchall(),
            )
        finally:
            con.close()

    def test_stage_pause_resumes_pending_file_without_losing_progress(self) \
            -> None:
        con = self.state_connection()
        control = dbhash.HashWorkerControl()

        def pause(_context, _decision) -> None:
            control.request_pause()

        try:
            paused = dbhash.process_hash_stage_v4(
                con,
                "full",
                stall_seconds=0.02,
                timeout_seconds=0.05,
                control=control,
                save_on_pause=True,
                on_threshold=pause,
                poll_seconds=0.005,
                _worker_target=worker_fixture.blocking_worker,
            )
            self.assertEqual("paused", paused["state"])
            self.assertEqual(0, paused["processed"])
            self.assertEqual("pending", con.execute(
                "SELECT hash_status FROM entries").fetchone()[0])
            dbstate.start_resume_session(
                con,
                config={},
                tools={},
                session_id="4" * 32,
                lease_id="5" * 32,
                hostname="fixture-host",
                pid=4343,
                process_start_token="resume-start",
            )
            resumed = dbhash.process_hash_stage_v4(
                con,
                "full",
                stall_seconds=1.0,
                timeout_seconds=2.0,
                poll_seconds=0.01,
            )
            self.assertEqual(("completed", 1, 1), (
                resumed["state"], resumed["processed"], resumed["done"]))
            self.assertEqual("completed", con.execute(
                "SELECT state FROM stage_checkpoints WHERE stage='hash'"
            ).fetchone()[0])
        finally:
            con.close()


if __name__ == "__main__":
    unittest.main()
