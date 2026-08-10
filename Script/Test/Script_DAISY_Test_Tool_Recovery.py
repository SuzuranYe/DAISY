"""外部工具会话恢复、故障分类与元数据熔断专项测试。"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock


_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SCRIPT_DIR = os.path.dirname(_TEST_DIR)
_REPO_ROOT = os.path.dirname(_SCRIPT_DIR)
_LIB_DIR = os.path.join(_SCRIPT_DIR, "Lib")
sys.path[:0] = [_TEST_DIR, _SCRIPT_DIR, _LIB_DIR]

import Script_DAISY_Lib_Snapshot_Core as core
import Script_DAISY_Lib_Metadata as dbmeta
import Script_DAISY_Lib_File_Hash as dbhash
import Script_DAISY_Lib_Snapshot_Verify as dbverify
import Script_DAISY_Lib_Scan_State as dbstate
import Script_DAISY_Lib_Scan_Runtime as dbrun
import Script_DAISY_Lib_Tool_Runtime as toolruntime
import Script_DAISY_Lib_Storage_Core as stgcore
import Script_DAISY_Lib_Storage_Smartctl as smartctl


_RUNTIME_ROOT = os.path.join(
    _REPO_ROOT, ".test_runtime", "v1_6_1", "tool_recovery")


class _ScriptedStdin(io.BytesIO):
    def __init__(
        self,
        *,
        fail_write_at: set[int] | None = None,
        fail_flush_at: set[int] | None = None,
    ) -> None:
        super().__init__()
        self.write_calls = 0
        self.flush_calls = 0
        self.fail_write_at = set(fail_write_at or ())
        self.fail_flush_at = set(fail_flush_at or ())

    def write(self, payload: bytes) -> int:
        self.write_calls += 1
        if self.write_calls in self.fail_write_at:
            raise OSError(22, "Invalid argument")
        return super().write(payload)

    def flush(self) -> None:
        self.flush_calls += 1
        if self.flush_calls in self.fail_flush_at:
            raise OSError(22, "Invalid argument")
        super().flush()


class _FakeExifProcess:
    _next_pid = 41000

    def __init__(
        self,
        stdout: bytes,
        *,
        fail_write_at: set[int] | None = None,
        fail_flush_at: set[int] | None = None,
        stderr: bytes = b"",
    ) -> None:
        type(self)._next_pid += 1
        self.pid = type(self)._next_pid
        self.stdin = _ScriptedStdin(
            fail_write_at=fail_write_at,
            fail_flush_at=fail_flush_at,
        )
        self.stdout = io.BytesIO(stdout)
        self.stderr = io.BytesIO(stderr)
        self.returncode = None

    def poll(self):
        return self.returncode

    def kill(self) -> None:
        self.returncode = -9

    def terminate(self) -> None:
        self.returncode = -15

    def wait(self, timeout=None):
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


def _exif_stream(*documents: bytes) -> bytes:
    blocks = [b"13.99\n{ready}\n"]
    for document in documents:
        blocks.append(document + b"\n{ready}\n")
    return b"".join(blocks)


class TestExifToolSessionRecovery(unittest.TestCase):
    def _worker(self, processes: list[_FakeExifProcess]) -> dbmeta.ExifToolWorker:
        pending = list(processes)

        def factory(_command, **_kwargs):
            if not pending:
                raise AssertionError("ExifTool 测试进程脚本已耗尽")
            return pending.pop(0)

        return dbmeta.ExifToolWorker(
            "fixture-exiftool",
            health_timeout=1.0,
            _popen_factory=factory,
        )

    def test_write_oserror_22_restarts_and_retries_current_file_once(self) \
            -> None:
        first = _FakeExifProcess(
            _exif_stream(), fail_write_at={2}, stderr=b"first session failed")
        second = _FakeExifProcess(
            _exif_stream(b'[{"SourceFile":"x.jpg"}]'))
        worker = self._worker([first, second])
        try:
            document = worker.extract("x.jpg", photo_profile=True, timeout=1)
            telemetry = worker.telemetry()
        finally:
            worker.close()
        self.assertEqual("x.jpg", document["SourceFile"])
        self.assertEqual((2, 1), (
            telemetry["session_count"], telemetry["restart_count"]))
        self.assertTrue(first.stdin.closed)
        self.assertTrue(first.stdout.closed)
        self.assertTrue(first.stderr.closed)

    def test_flush_oserror_22_uses_same_recovery_path(self) -> None:
        first = _FakeExifProcess(_exif_stream(), fail_flush_at={2})
        second = _FakeExifProcess(
            _exif_stream(b'[{"SourceFile":"flush.jpg"}]'))
        worker = self._worker([first, second])
        try:
            document = worker.extract(
                "flush.jpg", photo_profile=True, timeout=1)
        finally:
            worker.close()
        self.assertEqual("flush.jpg", document["SourceFile"])
        self.assertEqual(1, worker.restart_count)

    def test_eof_restarts_instead_of_reusing_dead_session(self) -> None:
        first = _FakeExifProcess(_exif_stream())
        second = _FakeExifProcess(
            _exif_stream(b'[{"SourceFile":"eof.jpg"}]'))
        worker = self._worker([first, second])
        try:
            document = worker.extract("eof.jpg", photo_profile=True, timeout=1)
        finally:
            worker.close()
        self.assertEqual("eof.jpg", document["SourceFile"])
        self.assertEqual(1, worker.restart_count)

    def test_second_pipe_failure_raises_recovered_tool_failure(self) -> None:
        first = _FakeExifProcess(_exif_stream(), fail_write_at={2})
        second = _FakeExifProcess(_exif_stream(), fail_write_at={2})
        third = _FakeExifProcess(_exif_stream())
        worker = self._worker([first, second, third])
        try:
            with self.assertRaises(toolruntime.ToolRuntimeFailure) as raised:
                worker.extract("repeat.jpg", photo_profile=True, timeout=1)
            self.assertTrue(raised.exception.recovered)
            self.assertEqual("pipe_write_failed", raised.exception.latest.failure_kind)
            self.assertEqual(3, worker.session_count)
            self.assertIsNotNone(worker.telemetry()["active_session"])
        finally:
            worker.close()


def _tool_failure(
    *,
    tool: str = "exiftool",
    operation: str = "metadata_extract",
    failure_kind: str = "pipe_write_failed",
    recovered: bool = True,
) -> toolruntime.ToolRuntimeFailure:
    return toolruntime.ToolRuntimeFailure(
        toolruntime.ToolFaultEvidence(
            tool=tool,
            operation=operation,
            failure_kind=failure_kind,
            message="fixture invalid pipe",
            pid=42001,
            errno=22,
            tool_session_id="a" * 32,
            restart_count=2,
        ),
        recovered=recovered,
    )


class _AlwaysFailExifWorker:
    calls = 0

    def __init__(self, _path) -> None:
        type(self).calls = 0

    def extract(self, _path, photo_profile=False, timeout=None):
        type(self).calls += 1
        raise _tool_failure()

    @staticmethod
    def close() -> None:
        pass

    @staticmethod
    def telemetry() -> dict[str, object]:
        return {
            "session_count": 7,
            "restart_count": 6,
            "active_session": None,
            "recent_sessions": [],
        }


class _SuccessfulExifWorker:
    calls: list[str] = []

    def __init__(self, _path) -> None:
        type(self).calls = []

    def extract(self, path, photo_profile=False, timeout=None):
        del photo_profile, timeout
        type(self).calls.append(path)
        return {"SourceFile": path}

    @staticmethod
    def close() -> None:
        pass

    @staticmethod
    def telemetry() -> dict[str, object]:
        return {
            "session_count": 1,
            "restart_count": 0,
            "active_session": None,
            "recent_sessions": [],
        }


class _MetadataFixture(unittest.TestCase):
    def setUp(self) -> None:
        os.makedirs(_RUNTIME_ROOT, exist_ok=True)
        self._temporary = tempfile.TemporaryDirectory(
            prefix="case_", dir=_RUNTIME_ROOT)
        self.base = self._temporary.name
        self.archive = os.path.join(self.base, "Archive")
        self.output = os.path.join(self.base, "Snapshots")
        os.makedirs(self.archive)
        os.makedirs(self.output)
        for index in range(10):
            with open(os.path.join(self.archive, f"{index:02d}.jpg"), "wb") \
                    as stream:
                stream.write(b"not-a-real-jpeg")
        self.tools = {
            "exiftool": {"path": "fixture", "version": "fixture"},
            "ffprobe": {"path": "fixture", "version": "fixture"},
            "sevenzip": {"path": "fixture", "version": "fixture"},
        }

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def create_handle(
        self,
        name: str,
        *,
        hash_mode: str = "full",
        format_mode: str = "off",
    ) -> dbrun.RunHandle:
        return dbrun.create_run(
            os.path.join(self.output, f"{name}.partial.sqlite"),
            [("档案", self.archive)],
            {
                "phase": "full",
                "hash": hash_mode,
                "metadata_storage": "complete",
                "format_validation": format_mode,
                "format_sample_percent": 100.0,
            },
            output_dir=self.output,
            publish_stem_path=os.path.join(self.output, name),
            tool_versions={
                "exiftool": "fixture",
                "ffprobe": "fixture",
                "sevenzip": "fixture",
            },
        )


class TestMetadataToolCircuit(_MetadataFixture):
    def test_three_failures_stop_before_error_fanout(self) -> None:
        partial = os.path.join(self.output, "Legacy.partial.sqlite")
        con = core.create_partial_snapshot(
            partial,
            [("档案", self.archive)],
            config={"phase": "full"},
        )
        core.enumerate_and_reconcile(con, collect_file_id=False)
        try:
            with mock.patch.object(
                    dbmeta, "ExifToolWorker", _AlwaysFailExifWorker):
                with self.assertRaises(dbmeta.MetadataToolCircuitOpen) as raised:
                    dbmeta.process_metadata_stage(
                        con, self.tools, tool_circuit_threshold=3)
            self.assertEqual(3, _AlwaysFailExifWorker.calls)
            self.assertEqual(10, raised.exception.summary["not_processed"])
            self.assertEqual(
                [("pending", 10)],
                con.execute(
                    "SELECT meta_status,COUNT(*) FROM entries"
                    " GROUP BY meta_status").fetchall(),
            )
            self.assertEqual(
                3,
                con.execute(
                    "SELECT COUNT(*) FROM errors"
                    " WHERE error_code='metadata_exiftool_tool_error'"
                ).fetchone()[0],
            )
            self.assertEqual(
                ("interrupted", "pending"),
                con.execute(
                    "SELECT scan_status,database_integrity"
                    " FROM snapshot_info WHERE id=1").fetchone(),
            )
        finally:
            con.close()

    def test_schema4_records_recoverable_stage_and_aggregate_event(self) \
            -> None:
        partial = os.path.join(self.output, "Run.partial.sqlite")
        handle = dbrun.create_run(
            partial,
            [("档案", self.archive)],
            {
                "phase": "full",
                "hash": "full",
                "metadata_storage": "complete",
                "format_validation": "off",
            },
            output_dir=self.output,
            publish_stem_path=os.path.join(self.output, "Run"),
            tool_versions={
                "exiftool": "fixture",
                "ffprobe": "fixture",
                "sevenzip": "fixture",
            },
        )
        try:
            core.enumerate_and_reconcile(
                handle.connection, collect_file_id=False)
            events = []
            with mock.patch.object(
                    dbmeta, "ExifToolWorker", _AlwaysFailExifWorker):
                result = dbrun.run_metadata_stage_controlled(
                    handle.connection,
                    self.tools,
                    dbrun.RunCommandRouter(),
                    on_event=lambda event, **payload: events.append(
                        (event, payload)),
                    tool_circuit_threshold=3,
                )
            self.assertEqual("failed_recoverable", result["state"])
            runtime = dbstate.load_runtime(handle.connection)
            self.assertEqual("failed_recoverable", runtime.run_state)
            checkpoint = handle.connection.execute(
                "SELECT state,items_done,items_total,checkpoint_json"
                " FROM stage_checkpoints WHERE stage='metadata'"
            ).fetchone()
            self.assertEqual(("failed_recoverable", 0, 10), tuple(checkpoint[:3]))
            detail = json.loads(checkpoint[3])
            self.assertEqual(10, detail["not_processed"])
            run_event = handle.connection.execute(
                "SELECT payload_json FROM run_state_events"
                " WHERE event='run_failed' ORDER BY event_id DESC LIMIT 1"
            ).fetchone()
            self.assertEqual(
                "metadata_tool_circuit_open",
                json.loads(run_event[0])["reason"],
            )
            self.assertIn("tool_circuit_open", [name for name, _ in events])
            self.assertIn("stage_failed", [name for name, _ in events])
        finally:
            dbrun.close_handle(handle, release_lease=True)


class TestMetadataToolSelection(_MetadataFixture):
    def legacy_connection(self, name: str):
        partial = os.path.join(self.output, f"{name}.partial.sqlite")
        connection = core.create_partial_snapshot(
            partial,
            [("档案", self.archive)],
            config={"phase": "full"},
        )
        core.enumerate_and_reconcile(connection, collect_file_id=False)
        return connection

    def test_both_metadata_tools_disabled_skip_without_file_errors(
        self,
    ) -> None:
        connection = self.legacy_connection("BothDisabled")
        try:
            with (
                mock.patch.object(
                    dbmeta, "ExifToolWorker",
                    side_effect=AssertionError("不应启动 ExifTool"),
                ),
                mock.patch.object(
                    dbmeta, "ffprobe_full",
                    side_effect=AssertionError("不应启动 ffprobe"),
                ),
            ):
                stats = dbmeta.process_metadata_stage(
                    connection,
                    {"sevenzip": self.tools["sevenzip"]},
                    metadata_exiftool=False,
                    metadata_ffprobe=False,
                )
            self.assertEqual((0, 10, 0), (
                stats["done"], stats["skipped"], stats["error"]))
            self.assertEqual(
                [("skipped", 10)],
                connection.execute(
                    "SELECT meta_status,COUNT(*) FROM entries"
                    " GROUP BY meta_status"
                ).fetchall(),
            )
            self.assertEqual(
                0, connection.execute(
                    "SELECT COUNT(*) FROM errors").fetchone()[0])
        finally:
            connection.close()

    def test_manifest_records_selection_and_defaults_missing_keys_on(self) \
            -> None:
        handle = self.create_handle("ManifestSelection", hash_mode="none")
        try:
            legacy = dbrun._scan_manifest_payload(
                handle.connection, {}, {}, {}, None)
            self.assertEqual(
                legacy["metadata"]["selected_tools"],
                {"exiftool": True, "ffprobe": True},
            )
            selected = dbrun._scan_manifest_payload(
                handle.connection,
                {"metadata_exiftool": False, "metadata_ffprobe": True},
                {}, {}, None,
            )
            self.assertEqual(
                selected["metadata"]["selected_tools"],
                {"exiftool": False, "ffprobe": True},
            )
        finally:
            dbrun.close_handle(handle, release_lease=True)

    def test_exiftool_only_does_not_call_ffprobe(self) -> None:
        connection = self.legacy_connection("ExifOnly")
        try:
            with (
                mock.patch.object(
                    dbmeta, "ExifToolWorker", _SuccessfulExifWorker),
                mock.patch.object(
                    dbmeta, "ffprobe_full",
                    side_effect=AssertionError("不应调用 ffprobe"),
                ),
            ):
                stats = dbmeta.process_metadata_stage(
                    connection,
                    {
                        "exiftool": self.tools["exiftool"],
                        "sevenzip": self.tools["sevenzip"],
                    },
                    metadata_exiftool=True,
                    metadata_ffprobe=False,
                )
            self.assertEqual((10, 0, 0), (
                stats["done"], stats["skipped"], stats["error"]))
            self.assertEqual(len(_SuccessfulExifWorker.calls), 10)
            self.assertEqual(
                10, connection.execute(
                    "SELECT COUNT(*) FROM photo_metadata").fetchone()[0])
        finally:
            connection.close()

    def test_ffprobe_only_skips_photos_and_processes_video(self) -> None:
        video = os.path.join(self.archive, "clip.mp4")
        with open(video, "wb") as stream:
            stream.write(b"synthetic-video")
        connection = self.legacy_connection("FfprobeOnly")
        ffprobe_calls: list[str] = []

        def fake_ffprobe(_tool_path: str, path: str) -> dict[str, object]:
            ffprobe_calls.append(path)
            return {
                "format": {
                    "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
                    "duration": "1.0",
                    "size": "15",
                },
                "streams": [{
                    "index": 0,
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 16,
                    "height": 16,
                }],
            }

        try:
            with (
                mock.patch.object(
                    dbmeta, "ExifToolWorker",
                    side_effect=AssertionError("不应启动 ExifTool"),
                ),
                mock.patch.object(
                    dbmeta, "ffprobe_full", side_effect=fake_ffprobe),
            ):
                stats = dbmeta.process_metadata_stage(
                    connection,
                    {
                        "ffprobe": self.tools["ffprobe"],
                        "sevenzip": self.tools["sevenzip"],
                    },
                    metadata_exiftool=False,
                    metadata_ffprobe=True,
                )
            self.assertEqual((1, 10, 0), (
                stats["done"], stats["skipped"], stats["error"]))
            self.assertEqual(ffprobe_calls, [video])
            provider = connection.execute(
                "SELECT parser FROM video_metadata").fetchone()[0]
            self.assertEqual(provider, "ffprobe")
            self.assertEqual(
                0, connection.execute(
                    "SELECT COUNT(*) FROM errors").fetchone()[0])
        finally:
            connection.close()


class TestOneShotToolClassification(unittest.TestCase):
    @staticmethod
    def result(returncode: int, *, stdout: bytes = b"{}") \
            -> toolruntime.ToolProcessResult:
        return toolruntime.ToolProcessResult(
            command=("fixture",),
            returncode=returncode,
            stdout=stdout,
            stderr=b"fixture stderr",
            elapsed_seconds=0.01,
            pid=43001,
            reaped=True,
            stdout_truncated=False,
            stderr_truncated=False,
        )

    def test_ffprobe_native_crash_is_tool_failure(self) -> None:
        with mock.patch.object(
                toolruntime, "run_bounded_tool",
                return_value=self.result(-1073741819)):
            with self.assertRaises(toolruntime.ToolRuntimeFailure) as raised:
                dbmeta.ffprobe_full("fixture", "clip.mp4")
        self.assertEqual("native_crash", raised.exception.latest.failure_kind)
        self.assertTrue(raised.exception.recovered)

    def test_ffprobe_normal_nonzero_is_source_error(self) -> None:
        with mock.patch.object(
                toolruntime, "run_bounded_tool",
                return_value=self.result(1)):
            with self.assertRaises(dbmeta.MetadataSourceError) as raised:
                dbmeta.ffprobe_full("fixture", "clip.mp4")
        self.assertEqual("ffprobe", raised.exception.tool)

    def test_sevenzip_memory_exit_is_tool_failure(self) -> None:
        with mock.patch.object(
                toolruntime, "run_bounded_tool",
                return_value=self.result(8, stdout=b"")):
            with self.assertRaises(toolruntime.ToolRuntimeFailure) as raised:
                dbmeta.sevenzip_summary("archive.7z", "fixture", "7z")
        self.assertEqual("tool_exit", raised.exception.latest.failure_kind)


class _WaitFailureProcess:
    def __init__(self) -> None:
        self.pid = 44001
        self.stdout = io.BytesIO(b"result")
        self.stderr = io.BytesIO(b"diagnostic")
        self.returncode = None
        self.wait_calls = 0

    def poll(self):
        return self.returncode

    def terminate(self) -> None:
        self.returncode = -15

    def kill(self) -> None:
        self.returncode = -9

    def wait(self, timeout=None):
        self.wait_calls += 1
        if self.wait_calls == 1:
            raise OSError(22, "Invalid argument")
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


class TestBoundedToolSupervision(unittest.TestCase):
    def test_wait_handle_failure_is_typed_and_owned_process_is_reaped(self) \
            -> None:
        process = _WaitFailureProcess()
        with self.assertRaises(toolruntime.ToolRuntimeFailure) as raised:
            toolruntime.run_bounded_tool(
                ["fixture"],
                tool="fixture",
                operation="probe",
                timeout_seconds=1.0,
                _popen_factory=lambda *_args, **_kwargs: process,
            )
        self.assertEqual(
            "supervision_failed", raised.exception.latest.failure_kind)
        self.assertIsNotNone(process.returncode)
        self.assertTrue(process.stdout.closed)
        self.assertTrue(process.stderr.closed)

    def test_monitor_thread_start_failure_reaps_exact_process(self) -> None:
        process = _WaitFailureProcess()
        process.wait_calls = 1
        with (
            mock.patch.object(
                toolruntime.threading.Thread,
                "start",
                side_effect=RuntimeError("fixture thread failure"),
            ),
            self.assertRaises(toolruntime.ToolRuntimeFailure) as raised,
        ):
            toolruntime.run_bounded_tool(
                ["fixture"],
                tool="fixture",
                operation="probe",
                timeout_seconds=1.0,
                _popen_factory=lambda *_args, **_kwargs: process,
            )
        self.assertEqual(
            "monitor_start_failed", raised.exception.latest.failure_kind)
        self.assertIsNotNone(process.returncode)


def _hash_crash(expected_size: int) -> dbhash.HashWorkerOutcome:
    return dbhash.HashWorkerOutcome(
        outcome="crashed",
        result=None,
        decision="none",
        decision_source="none",
        size_bytes=int(expected_size),
        bytes_read=0,
        final_offset=0,
        elapsed_seconds=0.01,
        active_read_seconds=0.0,
        stall_count=0,
        longest_stall_seconds=0.0,
        first_stall_offset=None,
        last_stall_offset=None,
        threshold_count=0,
        worker_pid=45001,
        worker_exitcode=-1073741819,
        worker_reaped=True,
        events=(),
        failure_kind="native_crash",
    )


def _independent_hash_crash(
    expected_size: int,
) -> dbhash.IndependentHashOutcome:
    return dbhash.IndependentHashOutcome(
        outcome="crashed",
        hash_hex=None,
        error="PowerShell native crash",
        decision="none",
        decision_source="none",
        size_bytes=int(expected_size),
        bytes_read=0,
        final_offset=0,
        elapsed_seconds=0.01,
        active_read_seconds=0.0,
        stall_count=0,
        longest_stall_seconds=0.0,
        first_stall_offset=None,
        last_stall_offset=None,
        threshold_count=0,
        worker_pid=45002,
        worker_exitcode=-1073741819,
        worker_reaped=True,
        events=(),
        failure_kind="native_crash",
    )


class _FailFormatSession:
    calls = 0

    def __init__(self, _tools) -> None:
        type(self).calls = 0
        self.last_tool_failure = None

    @staticmethod
    def describe(_extension: str, _media_kind: str) \
            -> dbverify.FormatValidatorSpec:
        return dbverify.FormatValidatorSpec(
            "jpeg", "exiftool", "fixture")

    def validate(self, _path: str, _media_kind: str, _spec):
        type(self).calls += 1
        self.last_tool_failure = _tool_failure(
            operation="format_validate")
        return "error", "fixture ExifTool fault"

    @staticmethod
    def close() -> None:
        pass


class TestSchema4ToolCircuits(_MetadataFixture):
    def test_hash_worker_crash_circuits_without_file_error_fanout(self) \
            -> None:
        handle = self.create_handle("HashCircuit")
        try:
            core.enumerate_and_reconcile(
                handle.connection, collect_file_id=False)
            calls = 0

            def runner(_path: str, **kwargs) -> dbhash.HashWorkerOutcome:
                nonlocal calls
                calls += 1
                return _hash_crash(int(kwargs["expected_size"]))

            with mock.patch.object(
                    dbhash, "run_hash_worker", side_effect=runner):
                result = dbhash.process_hash_stage_v4(
                    handle.connection, "full")
            self.assertEqual(3, calls)
            self.assertEqual("failed_recoverable", result["state"])
            self.assertEqual(10, result["not_processed"])
            self.assertEqual(
                [("pending", 10)],
                handle.connection.execute(
                    "SELECT hash_status,COUNT(*) FROM entries"
                    " GROUP BY hash_status").fetchall(),
            )
            self.assertEqual(3, handle.connection.execute(
                "SELECT COUNT(*) FROM entry_attempts WHERE stage='hash'"
            ).fetchone()[0])
            self.assertEqual(
                "failed_recoverable",
                dbstate.load_runtime(handle.connection).run_state,
            )
        finally:
            dbrun.close_handle(handle, release_lease=True)

    def test_format_tool_crash_circuits_and_resets_current_projection(self) \
            -> None:
        handle = self.create_handle("FormatCircuit", format_mode="all")
        try:
            core.enumerate_and_reconcile(
                handle.connection, collect_file_id=False)
            router = dbrun.RunCommandRouter()
            result = dbrun.run_format_stage_controlled(
                handle.connection,
                "all",
                self.tools,
                router,
                sample_percent=100.0,
                _session_factory=_FailFormatSession,
            )
            self.assertEqual(3, _FailFormatSession.calls)
            self.assertEqual("failed_recoverable", result["state"])
            self.assertEqual(10, result["not_processed"])
            self.assertEqual(
                [("pending", 10)],
                handle.connection.execute(
                    "SELECT status,COUNT(*) FROM format_checks"
                    " GROUP BY status").fetchall(),
            )
            self.assertEqual("ended", router.state)
            self.assertEqual(
                "failed_recoverable",
                dbstate.load_runtime(handle.connection).run_state,
            )
        finally:
            dbrun.close_handle(handle, release_lease=True)

    def test_independent_hash_tool_faults_remain_retriable(self) -> None:
        handle = self.create_handle("VerifyHashCircuit")
        try:
            con = handle.connection
            core.enumerate_and_reconcile(con, collect_file_id=False)
            rows = con.execute(
                "SELECT entry_id,size_bytes FROM entries"
                " WHERE is_placeholder=0 ORDER BY entry_id"
            ).fetchall()
            with con:
                con.execute("UPDATE entries SET hash_status='done'")
                con.executemany(
                    "INSERT INTO hashes"
                    " (entry_id,algorithm,hash_hex,origin,size_bytes,bytes_read,"
                    " status,tool,tool_version)"
                    " VALUES (?,'sha256',?,'computed',?,?,'valid',?,?)",
                    [
                        (
                            int(entry_id),
                            f"{int(entry_id):064x}",
                            int(size_bytes),
                            int(size_bytes),
                            dbhash.HASH_TOOL,
                            dbhash.HASH_TOOL_VERSION,
                        )
                        for entry_id, size_bytes in rows
                    ],
                )
            calls = 0

            def runner(_path: str, _shell: str, **kwargs) \
                    -> dbhash.IndependentHashOutcome:
                nonlocal calls
                calls += 1
                return _independent_hash_crash(
                    int(kwargs["expected_size"]))

            router = dbrun.RunCommandRouter()
            result = dbrun.run_independent_hash_stage_controlled(
                con,
                router,
                percent=100.0,
                min_count=10,
                powershell_path="fixture-powershell",
                powershell_version="fixture",
                _independent_runner=runner,
            )
            self.assertEqual(3, calls)
            self.assertEqual("failed_recoverable", result["state"])
            self.assertEqual(10, result["not_processed"])
            latest = dbrun._latest_verify_hash_attempts(con)
            self.assertEqual(3, len(latest))
            self.assertTrue(all(
                not dbrun._verify_hash_attempt_is_terminal(value)
                for value in latest.values()
            ))
            self.assertEqual("ended", router.state)
        finally:
            dbrun.close_handle(handle, release_lease=True)


class TestSmartctlToolBoundary(unittest.TestCase):
    def test_native_crash_is_one_task_level_failure(self) -> None:
        result = toolruntime.ToolProcessResult(
            command=("smartctl.exe", "--version"),
            returncode=-1073741819,
            stdout=b"",
            stderr=b"fixture crash",
            elapsed_seconds=0.01,
            pid=46001,
            reaped=True,
            stdout_truncated=False,
            stderr_truncated=False,
        )
        with mock.patch.object(
                smartctl.toolruntime, "run_bounded_tool", return_value=result):
            with self.assertRaisesRegex(
                    stgcore.DaisySmartError, "原生崩溃"):
                smartctl._run(["smartctl.exe", "--version"], timeout=20)


if __name__ == "__main__":
    unittest.main()
