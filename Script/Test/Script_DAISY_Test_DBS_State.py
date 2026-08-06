"""DAISY v1.6.0 schema 4、状态机、lease 与恢复契约测试。"""
from __future__ import annotations

import hashlib
import json
import os
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
import Script_DAISY_Lib_DBS_05_Reader as reader
import Script_DAISY_Lib_DBS_08_State as state


_RUNTIME_ROOT = os.path.join(
    _REPO_ROOT, ".test_runtime", "v1_6_0", "state")
_NOW = "2026-08-06T00:00:00.000000Z"
_LATER = "2026-08-06T00:01:00.000000Z"
_SNAPSHOT_ID = "1" * 32
_SESSION_ID = "2" * 32
_LEASE_ID = "3" * 32
_SECOND_SESSION_ID = "4" * 32
_SECOND_LEASE_ID = "5" * 32


def _sha256_bytes(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


class _StateFixture(unittest.TestCase):
    def setUp(self) -> None:
        os.makedirs(_RUNTIME_ROOT, exist_ok=True)
        self._td = tempfile.TemporaryDirectory(
            prefix="case_", dir=_RUNTIME_ROOT)
        self.base = self._td.name
        self.root_path = os.path.join(self.base, "archive")
        self.partial_path = os.path.join(
            self.base, "Archive_Full.partial.sqlite")
        self.publish_stem = os.path.join(
            self.base, "Archive_Full_20260806_080000")
        self.event_path = os.path.join(
            self.base, "Archive_Full.events.jsonl")

    def tearDown(self) -> None:
        self._td.cleanup()

    def initialize(
        self,
        con: sqlite3.Connection,
        *,
        roots: list[tuple[str, str]] | None = None,
    ) -> state.RuntimeSnapshot:
        return state.initialize_v4_connection(
            con,
            roots if roots is not None else [("档案", self.root_path)],
            {
                "phase": "full",
                "hash": "full",
                "metadata_storage": "complete",
                "format_validation": "off",
            },
            output_dir=self.base,
            partial_path=self.partial_path,
            publish_stem_path=self.publish_stem,
            event_log_path=self.event_path,
            tool_versions={"exiftool": "fixture"},
            snapshot_uuid=_SNAPSHOT_ID,
            session_id=_SESSION_ID,
            lease_id=_LEASE_ID,
            hostname="fixture-host",
            pid=4242,
            process_start_token="fixture-start",
            now_utc=_NOW,
        )

    def add_entry(
        self,
        con: sqlite3.Connection,
        entry_id: int = 1,
        *,
        name: str | None = None,
    ) -> None:
        directory = con.execute(
            "SELECT 1 FROM dirs WHERE dir_id=1").fetchone()
        if directory is None:
            con.execute(
                "INSERT INTO dirs"
                " (dir_id,root_id,rel_path,path_key,enum_status,observed_at_utc)"
                " VALUES (1,1,'','','ok',?)",
                (_NOW,),
            )
        filename = name or f"file_{entry_id}.bin"
        con.execute(
            "INSERT INTO entries"
            " (entry_id,root_id,dir_id,rel_path,path_key,name,extension,"
            " media_kind,size_bytes,modified_at_utc,attributes,observed_at_utc)"
            " VALUES (?,1,1,?,?,?,?,?,4,?,0,?)",
            (
                entry_id,
                filename,
                filename,
                filename,
                "bin",
                "other",
                _NOW,
                _NOW,
            ),
        )
        con.commit()

    def create_sealed_partial(self) -> None:
        con = sqlite3.connect(self.partial_path)
        try:
            self.initialize(con)
            state.begin_sealing(con, now_utc=_LATER)
            state.mark_sealed_unpublished(con, now_utc=_LATER)
        finally:
            con.close()


class TestSchema4Contract(_StateFixture):
    def test_schema_hashes_and_required_tables_are_frozen(self) -> None:
        self.assertEqual(
            "9d162b401617a9242393ba2dcf32445be6437799553abb4c5923c527dc0963a7",
            _sha256_bytes(core.SNAPSHOT_DDL),
        )
        self.assertEqual(
            "c8e3bbbd899818bc9653fcc5a27594b3a650d44643e838c23d4db4f9c66e1d34",
            _sha256_bytes(state.SNAPSHOT_DDL_V4),
        )
        con = sqlite3.connect(":memory:")
        try:
            self.initialize(con)
            tables = {
                row[0] for row in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")
            }
            self.assertTrue({
                "run_sessions",
                "snapshot_runtime",
                "stage_checkpoints",
                "run_state_events",
                "entry_attempts",
                "read_performance",
                "format_checks",
            }.issubset(tables))
            runtime_columns = {
                row[1] for row in con.execute(
                    "PRAGMA table_info(snapshot_runtime)")
            }
            self.assertIn("published_path_pattern", runtime_columns)
            self.assertNotIn("published_path", runtime_columns)
            self.assertEqual([], con.execute(
                "PRAGMA foreign_key_check").fetchall())
        finally:
            con.close()

    def test_initialization_freezes_identity_and_effective_config(self) -> None:
        con = sqlite3.connect(":memory:")
        try:
            runtime = self.initialize(con)
            self.assertEqual("running", runtime.run_state)
            self.assertEqual("enumerate", runtime.current_stage)
            self.assertEqual(1, runtime.state_revision)
            self.assertEqual(os.path.abspath(self.base), runtime.output_dir)
            row = con.execute(
                "SELECT schema_version,scanner_version,config_json"
                " FROM snapshot_info WHERE id=1").fetchone()
            self.assertEqual((4, "1.6.0"), tuple(row[:2]))
            config = json.loads(row[2])
            self.assertEqual("daisy-snapshot-v4", config["data_contract"])
            self.assertEqual("daisy-resume-v1", config["resume_contract"])
            session = con.execute(
                "SELECT session_kind,session_status,lease_id,pid"
                " FROM run_sessions").fetchone()
            self.assertEqual(
                ("initial", "active", _LEASE_ID, 4242), tuple(session))
        finally:
            con.close()

    def test_initialization_rejects_empty_duplicate_or_invalid_identity(self) -> None:
        con = sqlite3.connect(":memory:")
        with self.assertRaisesRegex(core.PreflightError, "至少需要一个 root"):
            self.initialize(con, roots=[])
        con.close()

        con = sqlite3.connect(":memory:")
        with self.assertRaisesRegex(core.PreflightError, "root label 重复"):
            self.initialize(con, roots=[("同名", "X:/A"), ("同名", "X:/B")])
        con.close()

        con = sqlite3.connect(":memory:")
        with self.assertRaisesRegex(core.PreflightError, "snapshot_uuid"):
            state.initialize_v4_connection(
                con,
                [("档案", self.root_path)],
                {},
                output_dir=self.base,
                partial_path=self.partial_path,
                publish_stem_path=self.publish_stem,
                snapshot_uuid="NOT-A-UUID",
            )
        con.close()

    def test_schema3_is_rejected_without_mutation(self) -> None:
        path = os.path.join(self.base, "schema3.sqlite")
        con = sqlite3.connect(path)
        con.executescript(core.SNAPSHOT_DDL)
        con.commit()
        con.close()
        before = _file_sha256(path)
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            with self.assertRaisesRegex(core.PreflightError, "schema 4"):
                state.require_v4_connection(con)
        finally:
            con.close()
        self.assertEqual(before, _file_sha256(path))


class TestRunStateMachine(_StateFixture):
    def test_pause_continue_stop_and_manual_resume_are_distinct(self) -> None:
        con = sqlite3.connect(":memory:")
        try:
            self.initialize(con)
            requested = state.request_pause(con, now_utc=_LATER)
            self.assertEqual("pause_requested", requested.run_state)
            paused = state.mark_paused(con, now_utc=_LATER)
            self.assertEqual(("paused", "none"), (
                paused.run_state, paused.resume_hint))
            continued = state.continue_running(con, now_utc=_LATER)
            self.assertEqual("running", continued.run_state)
            stopped = state.stop_run(con, now_utc=_LATER)
            self.assertEqual(("stopped", "manual_only"), (
                stopped.run_state, stopped.resume_hint))
            with self.assertRaisesRegex(core.PreflightError, "手动恢复"):
                state.start_resume_session(
                    con, config={}, tools={}, manual=False)
            resumed = state.start_resume_session(
                con,
                config={"phase": "full"},
                tools={},
                manual=True,
                session_id=_SECOND_SESSION_ID,
                lease_id=_SECOND_LEASE_ID,
                hostname="fixture-host",
                pid=4343,
                process_start_token="resume-start",
                now_utc=_LATER,
            )
            self.assertEqual(("running", "none"), (
                resumed.run_state, resumed.resume_hint))
            sessions = con.execute(
                "SELECT session_number,session_status,parent_session_id"
                " FROM run_sessions ORDER BY session_number").fetchall()
            self.assertEqual((1, "stopped", None), tuple(sessions[0]))
            self.assertEqual(
                (2, "active", _SESSION_ID), tuple(sessions[1]))
        finally:
            con.close()

    def test_save_exit_ends_session_and_suggests_resume(self) -> None:
        con = sqlite3.connect(":memory:")
        try:
            self.initialize(con)
            state.request_pause(con, now_utc=_LATER, for_exit=True)
            saved = state.mark_paused(con, now_utc=_LATER, for_exit=True)
            self.assertEqual(("paused", "suggest"), (
                saved.run_state, saved.resume_hint))
            session = con.execute(
                "SELECT session_status,ended_at_utc,end_reason"
                " FROM run_sessions WHERE session_id=?",
                (_SESSION_ID,),
            ).fetchone()
            self.assertEqual(("saved", _LATER, "saved_exit"), tuple(session))
            with self.assertRaisesRegex(core.PreflightError, "已结束"):
                state.continue_running(con)
            resumed = state.start_resume_session(
                con,
                config={},
                tools={},
                session_id=_SECOND_SESSION_ID,
                lease_id=_SECOND_LEASE_ID,
                hostname="fixture-host",
                pid=4343,
                process_start_token="resume-start",
                now_utc=_LATER,
            )
            self.assertEqual("running", resumed.run_state)
        finally:
            con.close()

    def test_illegal_stale_or_wrong_session_transition_is_atomic(self) -> None:
        con = sqlite3.connect(":memory:")
        try:
            runtime = self.initialize(con)
            before_events = con.execute(
                "SELECT COUNT(*) FROM run_state_events").fetchone()[0]
            with self.assertRaisesRegex(core.PreflightError, "非法"):
                state.transition_run_state(
                    con, "published", event="invalid")
            with self.assertRaisesRegex(core.PreflightError, "revision"):
                state.transition_run_state(
                    con,
                    "pause_requested",
                    event="stale",
                    expected_revision=runtime.state_revision - 1,
                )
            with self.assertRaisesRegex(core.PreflightError, "session"):
                state.transition_run_state(
                    con,
                    "pause_requested",
                    event="wrong_owner",
                    session_id=_SECOND_SESSION_ID,
                )
            after = state.load_runtime(con)
            self.assertEqual(runtime, after)
            self.assertEqual(before_events, con.execute(
                "SELECT COUNT(*) FROM run_state_events").fetchone()[0])
        finally:
            con.close()

    def test_resume_identity_checks_every_frozen_output_path(self) -> None:
        con = sqlite3.connect(":memory:")
        try:
            runtime = self.initialize(con)
            actual = state.validate_resume_identity(
                con,
                self.base,
                self.partial_path,
                self.publish_stem,
                self.event_path,
            )
            self.assertEqual(runtime, actual)
            with self.assertRaisesRegex(core.PreflightError, "event_log_path"):
                state.validate_resume_identity(
                    con,
                    self.base,
                    self.partial_path,
                    self.publish_stem,
                    os.path.join(self.base, "other.events.jsonl"),
                )
        finally:
            con.close()


class TestAttemptsAndRecovery(_StateFixture):
    @staticmethod
    def performance(*, confidence: str = "none", reason: str | None = None) \
            -> dict[str, object]:
        return {
            "origin": "computed",
            "size_bytes": 4,
            "bytes_read": 4,
            "elapsed_seconds": 1.5,
            "active_read_seconds": 1.0,
            "stall_count": 1,
            "longest_stall_seconds": 0.5,
            "first_stall_offset": 2,
            "last_stall_offset": 2,
            "final_offset": 4,
            "ended_reason": "completed",
            "candidate_confidence": confidence,
            "candidate_reason": reason,
        }

    def test_attempt_updates_current_result_and_performance_atomically(self) \
            -> None:
        con = sqlite3.connect(":memory:")
        try:
            self.initialize(con)
            self.add_entry(con)
            attempt = state.start_attempt(
                con, 1, "hash", tool_name="hashlib", now_utc=_NOW)
            with self.assertRaisesRegex(core.PreflightError, "尚未结束"):
                state.start_attempt(con, 1, "hash")
            state.update_attempt_progress(
                con,
                attempt,
                bytes_read=2,
                final_offset=2,
                stall_count=1,
                max_stall_seconds=0.5,
                now_utc=_LATER,
            )
            state.finish_attempt(
                con,
                attempt,
                "succeeded",
                bytes_read=4,
                final_offset=4,
                performance=self.performance(),
                now_utc=_LATER,
            )
            self.assertEqual("done", con.execute(
                "SELECT hash_status FROM entries WHERE entry_id=1"
            ).fetchone()[0])
            self.assertEqual(("succeeded", 4, 4), tuple(con.execute(
                "SELECT status,bytes_read,final_offset FROM entry_attempts"
                " WHERE attempt_id=?", (attempt,)).fetchone()))
            self.assertEqual(("computed", 1.5), tuple(con.execute(
                "SELECT origin,elapsed_seconds FROM read_performance"
                " WHERE attempt_id=?", (attempt,)).fetchone()))
            with self.assertRaisesRegex(core.PreflightError, "已经结束"):
                state.finish_attempt(con, attempt, "succeeded")
        finally:
            con.close()

    def test_bad_performance_candidate_rolls_back_attempt_completion(self) \
            -> None:
        con = sqlite3.connect(":memory:")
        try:
            self.initialize(con)
            self.add_entry(con)
            attempt = state.start_attempt(con, 1, "metadata")
            with self.assertRaisesRegex(ValueError, "读取性能异常候选"):
                state.finish_attempt(
                    con,
                    attempt,
                    "succeeded",
                    performance=self.performance(
                        confidence="low", reason="疑似较慢"),
                )
            self.assertEqual("running", con.execute(
                "SELECT status FROM entry_attempts WHERE attempt_id=?",
                (attempt,),
            ).fetchone()[0])
            self.assertEqual("processing", con.execute(
                "SELECT meta_status FROM entries WHERE entry_id=1"
            ).fetchone()[0])
            state.finish_attempt(con, attempt, "succeeded")
        finally:
            con.close()

    def test_format_current_row_retains_history_and_revision(self) -> None:
        con = sqlite3.connect(":memory:")
        try:
            self.initialize(con)
            self.add_entry(con)
            first = state.start_attempt(
                con, 1, "format", coverage="sample", validator="zip")
            state.finish_attempt(
                con, first, "unsupported", detail="unknown format")
            second = state.start_attempt(
                con, 1, "format", coverage="full", validator="zip")
            state.finish_attempt(
                con,
                second,
                "invalid",
                stat_match=True,
                detail="CRC mismatch",
            )
            current = con.execute(
                "SELECT attempt_id,status,coverage,detail,result_revision"
                " FROM format_checks WHERE entry_id=1").fetchone()
            self.assertEqual(
                (second, "invalid", "full", "CRC mismatch", 2),
                tuple(current),
            )
            history = con.execute(
                "SELECT attempt_number,status FROM entry_attempts"
                " WHERE entry_id=1 AND stage='format'"
                " ORDER BY attempt_number").fetchall()
            self.assertEqual([(1, "unsupported"), (2, "invalid")], history)
        finally:
            con.close()

    def test_interrupted_work_returns_to_file_boundaries(self) -> None:
        con = sqlite3.connect(":memory:")
        try:
            self.initialize(con)
            for entry_id in (1, 2, 3):
                self.add_entry(con, entry_id)
            state.start_attempt(con, 1, "hash")
            state.start_attempt(con, 2, "metadata")
            state.start_attempt(
                con, 3, "format", validator="zip")
            result = state.recover_interrupted(
                con, reason="fixture_crash", now_utc=_LATER)
            self.assertEqual(3, result.attempts_abandoned)
            self.assertEqual((1, 1, 1), (
                result.hash_entries_reset,
                result.metadata_entries_reset,
                result.format_entries_reset,
            ))
            self.assertEqual("failed_recoverable", result.runtime.run_state)
            self.assertEqual("suggest", result.runtime.resume_hint)
            self.assertEqual({"abandoned"}, {
                row[0] for row in con.execute(
                    "SELECT status FROM entry_attempts")
            })
            self.assertEqual("pending", con.execute(
                "SELECT hash_status FROM entries WHERE entry_id=1"
            ).fetchone()[0])
            self.assertEqual("pending", con.execute(
                "SELECT meta_status FROM entries WHERE entry_id=2"
            ).fetchone()[0])
            self.assertEqual("pending", con.execute(
                "SELECT status FROM format_checks WHERE entry_id=3"
            ).fetchone()[0])
            self.assertEqual("abandoned", con.execute(
                "SELECT session_status FROM run_sessions"
                " WHERE session_id=?", (_SESSION_ID,)).fetchone()[0])
        finally:
            con.close()

    def test_sealed_unpublished_crash_can_reenter_recovery(self) -> None:
        con = sqlite3.connect(":memory:")
        try:
            self.initialize(con)
            state.begin_sealing(con)
            state.mark_sealed_unpublished(con)
            recovered = state.recover_interrupted(
                con, reason="publish_owner_lost")
            self.assertEqual(
                "failed_recoverable", recovered.runtime.run_state)
            resumed = state.start_resume_session(
                con,
                config={},
                tools={},
                session_id=_SECOND_SESSION_ID,
                lease_id=_SECOND_LEASE_ID,
                hostname="fixture-host",
                pid=4343,
                process_start_token="resume-start",
            )
            self.assertEqual("running", resumed.run_state)
            self.assertEqual("publish", resumed.current_stage)
        finally:
            con.close()


class TestEventJournalAndLease(_StateFixture):
    def test_event_journal_accepts_only_unterminated_truncated_tail(self) \
            -> None:
        path = os.path.join(self.base, "events.jsonl")
        with open(path, "wb") as handle:
            handle.write(
                b'{"event":"one"}\n{"event":"two"}\n{"event":')
        result = state.read_event_journal(path)
        self.assertEqual(("one", "two"), tuple(
            item["event"] for item in result.records))
        self.assertTrue(result.truncated_tail)

        with open(path, "wb") as handle:
            handle.write(b'{"event":"one"}\n{"event":}\n')
        with self.assertRaisesRegex(core.PreflightError, "第 2 行损坏"):
            state.read_event_journal(path)

        with open(path, "wb") as handle:
            handle.write(b'{"event":}\n{"event":"two"}\n')
        with self.assertRaisesRegex(core.PreflightError, "第 1 行损坏"):
            state.read_event_journal(path)

    def test_lease_classification_covers_owner_identity_and_expiry(self) \
            -> None:
        record = state.new_lease_record(
            _SESSION_ID,
            lease_id=_LEASE_ID,
            host="fixture-host",
            pid=42,
            process_start_token="start-a",
            now_utc=_NOW,
        )
        self.assertEqual("active_local", state.classify_lease(
            record,
            now_utc=_LATER,
            local_host="fixture-host",
            pid_alive=lambda _pid: True,
            process_token=lambda _pid: "start-a",
        ))
        self.assertEqual("stale_dead", state.classify_lease(
            record,
            now_utc=_NOW,
            local_host="fixture-host",
            pid_alive=lambda _pid: False,
        ))
        self.assertEqual("stale_pid_reused", state.classify_lease(
            record,
            now_utc=_NOW,
            local_host="fixture-host",
            pid_alive=lambda _pid: True,
            process_token=lambda _pid: "start-b",
        ))
        self.assertEqual("active_foreign", state.classify_lease(
            record,
            now_utc="2026-08-06T00:00:30.000000Z",
            local_host="other-host",
        ))
        self.assertEqual("expired_foreign", state.classify_lease(
            record,
            now_utc="2026-08-06T00:00:31.000000Z",
            local_host="other-host",
        ))

    def test_lease_file_requires_owner_and_explicit_takeover(self) -> None:
        path = os.path.join(self.base, "scan.lock")
        record = state.acquire_lease_file(
            path,
            _SESSION_ID,
            lease_id=_LEASE_ID,
            host="fixture-host",
            pid=42,
            process_start_token="start-a",
            now_utc=_NOW,
        )
        self.assertEqual(record, state.read_lease_file(path))
        with self.assertRaisesRegex(core.PreflightError, "仍有效"):
            state.acquire_lease_file(
                path,
                _SECOND_SESSION_ID,
                lease_id=_SECOND_LEASE_ID,
                host="fixture-host",
                pid=43,
                process_start_token="start-b",
                now_utc=_LATER,
                pid_alive=lambda _pid: True,
                token_probe=lambda _pid: "start-a",
            )
        with self.assertRaisesRegex(core.PreflightError, "非 owner"):
            state.refresh_lease_file(path, _SECOND_LEASE_ID)
        refreshed = state.refresh_lease_file(
            path, _LEASE_ID, now_utc=_LATER)
        self.assertEqual(_LATER, refreshed.heartbeat_at_utc)
        with self.assertRaisesRegex(core.PreflightError, "非 owner"):
            state.release_lease_file(path, _SECOND_LEASE_ID)
        state.release_lease_file(path, _LEASE_ID)
        self.assertFalse(os.path.exists(path))

        with open(path, "wb") as handle:
            handle.write(b"broken")
        with self.assertRaisesRegex(core.PreflightError, "明确恢复"):
            state.acquire_lease_file(
                path,
                _SESSION_ID,
                lease_id=_LEASE_ID,
                host="fixture-host",
                pid=42,
                process_start_token="start-a",
            )
        taken = state.acquire_lease_file(
            path,
            _SECOND_SESSION_ID,
            takeover=True,
            lease_id=_SECOND_LEASE_ID,
            host="fixture-host",
            pid=43,
            process_start_token="start-b",
        )
        self.assertEqual(_SECOND_LEASE_ID, taken.lease_id)


class TestSchema4Publication(_StateFixture):
    def test_publish_uses_copy_hash_no_clobber_and_cleans_exact_inputs(self) \
            -> None:
        self.create_sealed_partial()
        lock_path = self.partial_path + ".lock"
        state.acquire_lease_file(
            lock_path,
            _SESSION_ID,
            lease_id=_LEASE_ID,
            host="fixture-host",
            pid=42,
            process_start_token="start-a",
            now_utc=_NOW,
        )
        staging = os.path.join(self.base, "publish.partial.sqlite")
        result = state.publish_sealed_snapshot(
            self.partial_path,
            staging,
            lease_path=lock_path,
            lease_id=_LEASE_ID,
            now_utc=_LATER,
        )
        self.assertTrue(os.path.isfile(result.final_path))
        self.assertEqual(result.sha256, _file_sha256(result.final_path))
        self.assertTrue(result.final_path.endswith(
            f"_{result.sha256[:8].upper()}.sqlite"))
        self.assertTrue(result.partial_removed)
        self.assertTrue(result.lease_released)
        self.assertEqual((), result.warnings)
        self.assertFalse(os.path.exists(self.partial_path))
        self.assertFalse(os.path.exists(staging))
        self.assertFalse(os.path.exists(lock_path))
        con = sqlite3.connect(
            f"file:{result.final_path}?mode=ro", uri=True)
        try:
            runtime = state.load_runtime(con)
            self.assertEqual("published", runtime.run_state)
            self.assertEqual(
                self.publish_stem
                + "_<SHA256-high32-uppercase>.sqlite",
                runtime.published_path_pattern,
            )
        finally:
            con.close()

    def test_publish_failure_preserves_original_and_removes_own_staging(self) \
            -> None:
        self.create_sealed_partial()
        staging = os.path.join(self.base, "publish.partial.sqlite")
        with mock.patch.object(
                state,
                "_publish_no_clobber",
                side_effect=core.PreflightError("fixture conflict")):
            with self.assertRaisesRegex(core.PreflightError, "fixture conflict"):
                state.publish_sealed_snapshot(
                    self.partial_path, staging, now_utc=_LATER)
        self.assertTrue(os.path.isfile(self.partial_path))
        self.assertFalse(os.path.exists(staging))
        con = sqlite3.connect(
            f"file:{self.partial_path}?mode=ro", uri=True)
        try:
            self.assertEqual(
                "sealed_unpublished", state.load_runtime(con).run_state)
        finally:
            con.close()

    def test_no_clobber_helper_never_overwrites_existing_target(self) -> None:
        working = os.path.join(self.base, "working.sqlite")
        final = os.path.join(self.base, "final.sqlite")
        with open(working, "wb") as handle:
            handle.write(b"new")
        with open(final, "wb") as handle:
            handle.write(b"old")
        with self.assertRaisesRegex(core.PreflightError, "不会覆盖"):
            state._publish_no_clobber(working, final)
        with open(working, "rb") as handle:
            self.assertEqual(b"new", handle.read())
        with open(final, "rb") as handle:
            self.assertEqual(b"old", handle.read())


class TestSchema4BusinessProjection(_StateFixture):
    def _published_connection(
        self,
        *,
        resume: bool,
    ) -> sqlite3.Connection:
        con = sqlite3.connect(":memory:")
        self.initialize(con)
        self.add_entry(con, name="stable.bin")
        con.execute(
            "UPDATE entries SET meta_status='not_applicable',"
            " hash_status='skipped' WHERE entry_id=1")
        con.commit()
        if resume:
            state.request_pause(con, now_utc=_LATER, for_exit=True)
            state.mark_paused(con, now_utc=_LATER, for_exit=True)
            state.start_resume_session(
                con,
                config={
                    "phase": "full",
                    "hash": "full",
                    "metadata_storage": "complete",
                    "format_validation": "off",
                },
                tools={"exiftool": "fixture"},
                session_id=_SECOND_SESSION_ID,
                lease_id=_SECOND_LEASE_ID,
                hostname="fixture-host",
                pid=4343,
                process_start_token="resume-start",
                now_utc=_LATER,
            )
        state.begin_sealing(con, now_utc=_LATER)
        state.mark_sealed_unpublished(con, now_utc=_LATER)
        state.mark_published(con, now_utc=_LATER)
        return con

    def test_direct_and_resumed_runs_have_equal_business_projection(self) \
            -> None:
        direct = self._published_connection(resume=False)
        resumed = self._published_connection(resume=True)
        try:
            self.assertNotEqual(
                direct.execute(
                    "SELECT state_revision FROM snapshot_runtime"
                ).fetchone()[0],
                resumed.execute(
                    "SELECT state_revision FROM snapshot_runtime"
                ).fetchone()[0],
            )
            self.assertNotEqual(
                direct.execute("SELECT COUNT(*) FROM run_sessions").fetchone()[0],
                resumed.execute("SELECT COUNT(*) FROM run_sessions").fetchone()[0],
            )
            direct_descriptor = reader.inspect_connection(direct)
            resumed_descriptor = reader.inspect_connection(resumed)
            direct_digest = reader.snapshot_business_projection_digest(
                direct, direct_descriptor)
            resumed_digest = reader.snapshot_business_projection_digest(
                resumed, resumed_descriptor)
            self.assertEqual(direct_digest, resumed_digest)
            sections = {
                row.section for row in reader.iter_snapshot_business_projection(
                    resumed, resumed_descriptor)
            }
            self.assertNotIn("run_sessions", sections)
            self.assertNotIn("entry_attempts", sections)
            self.assertIn("entries", sections)
        finally:
            direct.close()
            resumed.close()


if __name__ == "__main__":
    unittest.main()
