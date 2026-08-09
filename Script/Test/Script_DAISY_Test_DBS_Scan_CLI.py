"""DAISY v1.6.0 统一扫描生产 CLI 的工作区隔离测试。"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SCRIPT_DIR = os.path.dirname(_TEST_DIR)
_REPO_ROOT = os.path.dirname(_SCRIPT_DIR)
_LIB_DIR = os.path.join(_SCRIPT_DIR, "Lib")
_MODULE_DIR = os.path.join(_SCRIPT_DIR, "Module")
sys.path[:0] = [_TEST_DIR, _SCRIPT_DIR, _LIB_DIR, _MODULE_DIR]

import Script_DAISY_Lib_DBS_08_State as dbstate
import Script_DAISY_Lib_DBS_09_Run as dbrun
import Script_DAISY_Module_DBS_10_Scan as scan_cli


_MAIN = os.path.join(_SCRIPT_DIR, "Script_DAISY_MAIN.py")
_RUNTIME_ROOT = os.path.join(
    _REPO_ROOT, ".test_runtime", "v1_6_1", "scan_cli")


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


class TestScanCliConfig(unittest.TestCase):
    def parse(self, *args: str):
        return scan_cli.build_parser().parse_args(list(args))

    def test_full_defaults_keep_format_validation_off(self) -> None:
        args = self.parse("--root", "Archive")
        config = scan_cli._new_config(args, "full")
        self.assertEqual((
            "full", "full", "complete", "off", 10.0,
            "continue_waiting",
        ), (
            config["phase"], config["hash"], config["metadata_storage"],
            config["format_validation"], config["format_sample_percent"],
            config["hash_timeout_policy"]["default_decision"],
        ))
        self.assertEqual(90, config["hash_timeout_policy"]["minimum_seconds"])
        self.assertEqual(
            9 * 1024 ** 3,
            config["hash_timeout_policy"]["step_bytes"],
        )
        self.assertIs(config["metadata_exiftool"], True)
        self.assertIs(config["metadata_ffprobe"], True)
        self.assertEqual(config["metadata_exiftool_mode"], "complete")
        self.assertEqual(config["metadata_ffprobe_mode"], "complete")

    def test_full_metadata_tool_flags_are_independent_and_limit_preflight(
        self,
    ) -> None:
        args = self.parse(
            "--root", "Archive", "--hash", "none",
            "--no-metadata-exiftool",
        )
        config = scan_cli._new_config(args, "full")
        self.assertIs(config["metadata_exiftool"], False)
        self.assertIs(config["metadata_ffprobe"], True)
        self.assertEqual(config["metadata_exiftool_mode"], "off")
        self.assertEqual(config["metadata_ffprobe_mode"], "complete")
        with mock.patch.object(
            scan_cli.core,
            "run_preflight",
            return_value={
                "ffprobe": {"path": "fixture", "version": "fixture"},
                "sevenzip": {"path": "fixture", "version": "fixture"},
            },
        ) as preflight:
            tools = scan_cli._full_preflight(args, os.path.abspath("Output"))
        self.assertEqual(set(tools), {"ffprobe", "sevenzip"})
        self.assertEqual(
            set(preflight.call_args.args[0]), {"ffprobe", "sevenzip"})

        both_off = self.parse(
            "--root", "Archive", "--hash", "none",
            "--no-metadata-exiftool", "--no-metadata-ffprobe",
        )
        with mock.patch.object(
            scan_cli.core,
            "run_preflight",
            return_value={
                "sevenzip": {"path": "fixture", "version": "fixture"},
            },
        ) as preflight:
            scan_cli._full_preflight(both_off, os.path.abspath("Output"))
        self.assertEqual(set(preflight.call_args.args[0]), {"sevenzip"})

    def test_full_metadata_tool_ranges_are_independent_without_ddl_change(
        self,
    ) -> None:
        args = self.parse(
            "--root", "Archive",
            "--metadata-exiftool-mode", "normalized",
            "--metadata-ffprobe-mode", "complete",
        )
        config = scan_cli._new_config(args, "full")
        self.assertEqual(config["metadata_exiftool_mode"], "normalized")
        self.assertEqual(config["metadata_ffprobe_mode"], "complete")
        self.assertIs(config["metadata_exiftool"], True)
        self.assertIs(config["metadata_ffprobe"], True)
        self.assertEqual(config["metadata_storage"], "complete")
        self.assertEqual(
            dbrun.metadata_tool_modes(config),
            {"exiftool": "normalized", "ffprobe": "complete"},
        )
        self.assertEqual(
            dbrun.metadata_tool_modes({
                "metadata_storage": "normalized",
                "metadata_exiftool": False,
                "metadata_ffprobe": True,
            }),
            {"exiftool": "off", "ffprobe": "normalized"},
        )

    def test_format_validation_still_preflights_required_decoders(self) \
            -> None:
        args = self.parse(
            "--root", "Archive", "--hash", "none",
            "--no-metadata-exiftool", "--no-metadata-ffprobe",
            "--format-validation", "all",
        )
        discovered = {
            name: {"path": "fixture", "version": "fixture"}
            for name in ("exiftool", "ffprobe", "sevenzip")
        }
        with mock.patch.object(
                scan_cli.core, "run_preflight", return_value=discovered) \
                as preflight:
            scan_cli._full_preflight(args, os.path.abspath("Output"))
        self.assertEqual(
            set(preflight.call_args.args[0]),
            {"exiftool", "ffprobe", "sevenzip"},
        )

    def test_full_format_sample_accepts_zero_and_filename_only_uses_mode(
        self,
    ) -> None:
        args = self.parse(
            "--root", "Archive",
            "--format-validation", "sample",
            "--format-sample-percent", "0",
        )
        config = scan_cli._new_config(args, "full")
        self.assertEqual(0.0, config["format_sample_percent"])
        with mock.patch.object(
                scan_cli.core.time, "strftime",
                return_value="2026-08-09_12-34-56"):
            stem = scan_cli._scan_snapshot_stem(["Archive"], "full")
        self.assertEqual("Archive_Full_2026-08-09_12-34-56", stem)
        self.assertNotIn("Fmt", stem)
        self.assertNotIn("Metadata", stem)

    def test_quick_rejects_content_and_format_features(self) -> None:
        for extra, message in (
            (("--hash", "full"), "哈希"),
            (("--metadata-storage", "complete"), "元数据"),
            (("--no-metadata-exiftool",), "元数据工具"),
            (("--no-metadata-ffprobe",), "元数据工具"),
            (("--metadata-exiftool-mode", "complete"), "元数据工具"),
            (("--metadata-ffprobe-mode", "normalized"), "元数据工具"),
            (("--format-validation", "all"), "格式校验"),
        ):
            with self.subTest(extra=extra):
                args = self.parse("--mode", "quick", "--root", "Archive", *extra)
                with self.assertRaisesRegex(Exception, message):
                    scan_cli._new_config(args, "quick")

    def test_metadata_finish_reports_not_applicable_separately_from_errors(
        self,
    ) -> None:
        reporter = scan_cli.ScanReporter(
            "", quiet=True, event_log_active=False)
        with mock.patch.object(scan_cli.core, "emit_gui_event") as emit:
            reporter.event("stage_started", stage="metadata")
            reporter.event(
                "stage_finished", stage="metadata", processed=12,
                error=1, not_applicable=8, skipped=2,
            )
        finish = next(
            call for call in emit.call_args_list
            if call.args and call.args[0] == "progress_finish"
        )
        self.assertIn("异常记录 1", finish.kwargs["summary"])
        self.assertIn("不适用 8", finish.kwargs["summary"])
        self.assertIn("跳过 2", finish.kwargs["summary"])

    def test_active_owner_is_rejected_before_source_or_tool_preflight(
        self,
    ) -> None:
        args = self.parse("--resume", "Active.partial.sqlite")
        preview = dbrun.ResumePreview(
            partial_path=os.path.abspath("Active.partial.sqlite"),
            lease_path=os.path.abspath("Active.partial.sqlite.lease"),
            run_state="running",
            resume_hint="none",
            current_stage="hash",
            active_session_id="a" * 32,
            active_session_ended=False,
            lease_classification="active_local",
            roots=(("档案", r"Z:\不应访问"),),
            config={"phase": "quick", "hash": "none"},
            tools={},
        )
        with (
            mock.patch.object(scan_cli.core, "validate_root") as validate,
            mock.patch.object(scan_cli, "_quick_preflight") as preflight,
        ):
            with self.assertRaisesRegex(Exception, "有效任务"):
                scan_cli._resume_preflight(args, preview)
        validate.assert_not_called()
        preflight.assert_not_called()

    def test_older_full_partial_defaults_both_metadata_tools_to_enabled(
        self,
    ) -> None:
        frozen_tools = {
            name: {
                "path": os.path.abspath(f"fixture-{name}.exe"),
                "version": "fixture",
            }
            for name in ("exiftool", "ffprobe", "sevenzip")
        }
        preview = dbrun.ResumePreview(
            partial_path=os.path.abspath("Legacy.partial.sqlite"),
            lease_path=os.path.abspath("Legacy.partial.sqlite.lease"),
            run_state="paused",
            resume_hint="resume",
            current_stage="metadata",
            active_session_id=None,
            active_session_ended=True,
            lease_classification="stale_local",
            roots=(("档案", os.path.abspath("Archive")),),
            config={
                "phase": "full",
                "hash": "none",
                "format_validation": "off",
            },
            tools=frozen_tools,
        )
        args = self.parse("--resume", preview.partial_path, "--quiet")
        with (
            mock.patch.object(scan_cli.core, "validate_root"),
            mock.patch.object(
                scan_cli.core,
                "run_preflight",
                return_value=frozen_tools,
            ) as preflight,
        ):
            returned = scan_cli._resume_preflight(args, preview)
        self.assertIs(returned, preview.config)
        self.assertEqual(
            set(preflight.call_args.args[0]),
            {"exiftool", "ffprobe", "sevenzip"},
        )

        override = self.parse(
            "--resume", preview.partial_path,
            "--no-metadata-ffprobe", "--quiet")
        with self.assertRaisesRegex(Exception, "冻结参数"):
            scan_cli._resume_preflight(override, preview)


class TestScanCliProduction(unittest.TestCase):
    def setUp(self) -> None:
        os.makedirs(_RUNTIME_ROOT, exist_ok=True)
        self._td = tempfile.TemporaryDirectory(
            prefix="case_", dir=_RUNTIME_ROOT)
        self.base = self._td.name
        self.root_path = os.path.join(self.base, "Archive")
        self.output_dir = os.path.join(self.base, "Snapshots")
        self.temp_dir = os.path.join(self.base, "temp")
        os.makedirs(self.root_path)
        os.makedirs(self.output_dir)
        os.makedirs(self.temp_dir)

    def tearDown(self) -> None:
        self._td.cleanup()

    def run_scan(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.update({
            "TEMP": self.temp_dir,
            "TMP": self.temp_dir,
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUTF8": "1",
        })
        return subprocess.run(
            [sys.executable, "-B", _MAIN, "scan", *args],
            cwd=_REPO_ROOT,
            env=env,
            text=True,
            encoding="utf-8",
            errors="strict",
            capture_output=True,
            timeout=60,
            check=False,
        )

    def published(self) -> str:
        results = [
            str(path) for path in Path(self.output_dir).glob("*.sqlite")
            if not path.name.endswith(".partial.sqlite")
        ]
        self.assertEqual(1, len(results), results)
        return results[0]

    def assert_clean_runtime_files(self) -> None:
        residue = [
            path.name for path in Path(self.output_dir).iterdir()
            if path.name.endswith((
                ".partial.sqlite", ".lease", ".events.jsonl",
                ".publishing.sqlite",
                ".raw_verification.jsonl",
            ))
        ]
        self.assertEqual([], residue)

    def test_quick_subprocess_publishes_schema4_without_changing_source(
        self,
    ) -> None:
        source = os.path.join(self.root_path, "中文资料.txt")
        with open(source, "w", encoding="utf-8", newline="\n") as stream:
            stream.write("只读资料\n")
        source_digest = _sha256(source)
        completed = self.run_scan(
            "--mode", "quick",
            "--root", f"档案={self.root_path}",
            "--output-dir", self.output_dir,
            "--quiet",
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn("快照：", completed.stdout)
        final = self.published()
        self.assertEqual(source_digest, _sha256(source))
        self.assert_clean_runtime_files()
        self.assertEqual([], list(Path(self.output_dir).glob(
            "*_Raw_Verification.json")))
        con = sqlite3.connect(
            Path(final).resolve(strict=True).as_uri() + "?mode=ro",
            uri=True,
        )
        try:
            self.assertEqual(4, con.execute(
                "SELECT schema_version FROM snapshot_info WHERE id=1"
            ).fetchone()[0])
            self.assertEqual("published", dbstate.load_runtime(con).run_state)
            config = json.loads(con.execute(
                "SELECT config_json FROM snapshot_info WHERE id=1"
            ).fetchone()[0])
            self.assertEqual(("quick", "none", "off"), (
                config["phase"], config["hash"],
                config["format_validation"],
            ))
            self.assertEqual("skipped", con.execute(
                "SELECT state FROM stage_checkpoints WHERE stage='verify_hash'"
            ).fetchone()[0])
        finally:
            con.close()

    def test_event_log_creation_failure_refuses_scan_and_keeps_recovery(
        self,
    ) -> None:
        partial = os.path.join(self.output_dir, "Event.partial.sqlite")
        handle = dbrun.create_run(
            partial,
            [("档案", self.root_path)],
            {
                "phase": "quick",
                "hash": "none",
                "metadata_storage": "normalized",
                "format_validation": "off",
            },
            output_dir=self.output_dir,
            publish_stem_path=os.path.join(self.output_dir, "Event"),
            tool_versions={},
        )
        args = scan_cli.build_parser().parse_args([
            "--mode", "quick", "--quiet",
        ])
        try:
            with mock.patch.object(
                    scan_cli, "_append_event", side_effect=OSError("fixture")):
                result = scan_cli._run_handle(
                    args, handle, {"phase": "quick", "hash": "none"})
            self.assertEqual(1, result)
            self.assertEqual(
                "failed_recoverable",
                dbstate.load_runtime(handle.connection).run_state,
            )
            self.assertTrue(os.path.isfile(partial))
            self.assertTrue(os.path.isfile(handle.lease_path))
        finally:
            dbrun.close_handle(handle, release_lease=True)

    def saved_quick_partial(self, *, stopped: bool = False) -> str:
        partial = os.path.join(self.output_dir, "Resume.partial.sqlite")
        handle = dbrun.create_run(
            partial,
            [("档案", self.root_path)],
            {
                "phase": "quick",
                "quick": True,
                "hash": "none",
                "metadata_storage": "normalized",
                "format_validation": "off",
                "format_sample_percent": 10.0,
                "no_file_id": False,
                "hash_timeout_policy": {
                    "default_decision": "continue_waiting",
                },
            },
            output_dir=self.output_dir,
            publish_stem_path=os.path.join(self.output_dir, "Resume"),
            tool_versions={},
        )
        if stopped:
            dbstate.stop_run(handle.connection, reason="fixture_stop")
        else:
            dbstate.request_pause(handle.connection, for_exit=True)
            dbstate.mark_paused(handle.connection, for_exit=True)
        dbrun.close_handle(handle, release_lease=True)
        return partial

    def test_saved_quick_scan_resumes_in_new_session_and_publishes(
        self,
    ) -> None:
        with open(
            os.path.join(self.root_path, "resume.txt"),
            "w", encoding="utf-8", newline="\n",
        ) as stream:
            stream.write("resume\n")
        partial = self.saved_quick_partial()
        completed = self.run_scan("--resume", partial, "--quiet")
        self.assertEqual(0, completed.returncode, completed.stderr)
        final = self.published()
        con = sqlite3.connect(
            Path(final).resolve(strict=True).as_uri() + "?mode=ro",
            uri=True,
        )
        try:
            self.assertEqual(2, con.execute(
                "SELECT COUNT(*) FROM run_sessions").fetchone()[0])
            self.assertEqual(
                [(1, "initial", "saved"), (2, "resume", "completed")],
                con.execute(
                    "SELECT session_number,session_kind,session_status"
                    " FROM run_sessions ORDER BY session_number"
                ).fetchall(),
            )
        finally:
            con.close()

    def test_stopped_partial_requires_explicit_manual_resume(self) -> None:
        partial = self.saved_quick_partial(stopped=True)
        before = _sha256(partial)
        rejected = self.run_scan("--resume", partial, "--quiet")
        self.assertEqual(2, rejected.returncode)
        self.assertIn("明确手动续传", rejected.stderr)
        self.assertEqual(before, _sha256(partial))
        completed = self.run_scan(
            "--resume", partial, "--manual-resume", "--quiet")
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.published()

    def test_resume_parameter_override_is_rejected_without_writing_db(
        self,
    ) -> None:
        partial = self.saved_quick_partial()
        before = _sha256(partial)
        completed = self.run_scan(
            "--resume", partial, "--hash", "full", "--quiet")
        self.assertEqual(2, completed.returncode)
        self.assertIn("冻结参数", completed.stderr)
        self.assertEqual(before, _sha256(partial))
        self.assertFalse(os.path.exists(partial + ".lease"))

    def test_sealed_publish_retry_does_not_rescan_missing_source(self) -> None:
        source = os.path.join(self.root_path, "sealed.txt")
        with open(source, "w", encoding="utf-8", newline="\n") as stream:
            stream.write("sealed evidence\n")
        partial = os.path.join(self.output_dir, "Sealed.partial.sqlite")
        handle = dbrun.create_run(
            partial,
            [("档案", self.root_path)],
            {
                "phase": "quick",
                "quick": True,
                "hash": "none",
                "metadata_storage": "normalized",
                "format_validation": "off",
            },
            output_dir=self.output_dir,
            publish_stem_path=os.path.join(self.output_dir, "Sealed"),
            tool_versions={},
        )
        with mock.patch.object(
                dbstate,
                "_publish_no_clobber",
                side_effect=OSError("fixture conflict")):
            with self.assertRaisesRegex(OSError, "fixture conflict"):
                dbrun.run_scan_to_publication(
                    handle, dbrun.RunCommandRouter())
        dbstate.release_lease_file(
            handle.lease_path, handle.lease.lease_id)
        os.remove(source)

        completed = self.run_scan("--resume", partial, "--quiet")
        self.assertEqual(0, completed.returncode, completed.stderr)
        final = self.published()
        self.assert_clean_runtime_files()
        con = sqlite3.connect(
            Path(final).resolve(strict=True).as_uri() + "?mode=ro",
            uri=True,
        )
        try:
            self.assertEqual(
                [(1, "initial", "failed"),
                 (2, "resume", "completed")],
                con.execute(
                    "SELECT session_number,session_kind,session_status"
                    " FROM run_sessions ORDER BY session_number"
                ).fetchall(),
            )
            self.assertEqual(
                [("sealed.txt",)],
                con.execute(
                    "SELECT rel_path FROM entries ORDER BY entry_id"
                ).fetchall(),
            )
            self.assertIn(
                "publication_retry_started",
                [row[0] for row in con.execute(
                    "SELECT event FROM run_state_events ORDER BY event_id")],
            )
            manifest = json.loads(con.execute(
                "SELECT manifest_json FROM snapshot_manifest WHERE id=1"
            ).fetchone()[0])
            self.assertEqual(2, manifest["counts"]["sessions"])
            self.assertEqual({
                "retry_sessions": 1,
                "failed_retries": 0,
                "source_rescanned": False,
            }, manifest["publication_recovery"])
        finally:
            con.close()


if __name__ == "__main__":
    unittest.main()
