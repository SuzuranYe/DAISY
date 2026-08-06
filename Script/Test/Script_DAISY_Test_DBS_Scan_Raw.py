"""v1.6.0 Full 扫描 RAW 从属阶段、续传与联合发布专项测试。"""
from __future__ import annotations

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
_MODULE_DIR = os.path.join(_SCRIPT_DIR, "Module")
sys.path[:0] = [_TEST_DIR, _SCRIPT_DIR, _LIB_DIR, _MODULE_DIR]

import Script_DAISY_Lib_DBS_01_Core as core
import Script_DAISY_Lib_DBS_08_State as dbstate
import Script_DAISY_Lib_DBS_09_Run as dbrun
import Script_DAISY_Lib_DBS_13_Raw as dbraw
import Script_DAISY_Lib_DBS_14_Raw_Evidence as rawevidence
import Script_DAISY_Lib_ENV_01_Capabilities as envcap
import Script_DAISY_Module_DBS_10_Scan as scan_cli


_RUNTIME_ROOT = os.path.join(
    _REPO_ROOT, ".test_runtime", "v1_6_0", "scan_raw")


def _capability() -> dict[str, object]:
    return envcap.RuntimeCapability(
        envcap.RAW_CAPABILITY_ID,
        envcap.RAW_CAPABILITY_TITLE,
        "available",
        version="0.synthetic",
        provider="rawpy/LibRaw",
        isolated=True,
        details={
            "libraw_version": "0.synthetic",
            "worker_pid": 43210,
            "worker_exitcode": 0,
            "worker_reaped": True,
        },
    ).as_dict()


def _outcome(
    status: str | None,
    *,
    outcome: str = "completed",
    control_action: str | None = None,
    size_bytes: int = 16,
) -> dbraw.RawDecodeOutcome:
    valid = status == "valid"
    return dbraw.RawDecodeOutcome(
        outcome=outcome,
        status=status,
        code=(
            "raw_unsupported" if status == "unsupported"
            else "decode_error" if status == "invalid"
            else None
        ),
        detail="synthetic invalid" if status == "invalid" else None,
        decision="stop_and_resume" if control_action else "none",
        decision_source="gui" if control_action else "none",
        control_action=control_action,
        size_bytes=size_bytes,
        elapsed_seconds=0.01,
        threshold_seconds=90.0,
        threshold_count=0,
        worker_pid=24680,
        worker_exitcode=0,
        worker_reaped=True,
        rawpy_version="0.synthetic",
        libraw_version="0.synthetic",
        width=4 if valid else None,
        height=4 if valid else None,
        channels=3 if valid else None,
        pixel_count=48 if valid else None,
        decoded_bytes=48 if valid else None,
        events=(),
        events_truncated=False,
    )


class TestScanRawConfig(unittest.TestCase):
    def parse(self, *values: str):
        return scan_cli.build_parser().parse_args(list(values))

    def test_default_off_hierarchy_and_timeout_policy(self) -> None:
        default = scan_cli._new_config(
            self.parse("--root", "Archive"), "full")
        self.assertIs(False, default["raw_deep_validation"])
        self.assertIsNone(
            default["raw_timeout_policy"]["override_seconds"])
        enabled = scan_cli._new_config(self.parse(
            "--root", "Archive",
            "--format-validation", "sample",
            "--raw-deep-validation",
            "--raw-timeout-seconds", "12.5",
            "--timeout-action", "skip_and_record",
        ), "full")
        self.assertIs(True, enabled["raw_deep_validation"])
        self.assertEqual(12.5, enabled["raw_timeout_policy"][
            "override_seconds"])
        self.assertEqual("skip_and_record", enabled[
            "raw_timeout_policy"]["default_decision"])
        with self.assertRaisesRegex(core.PreflightError, "必须依附"):
            scan_cli._new_config(self.parse(
                "--root", "Archive", "--raw-deep-validation"), "full")
        with self.assertRaisesRegex(core.PreflightError, "Quick"):
            scan_cli._new_config(self.parse(
                "--mode", "quick", "--root", "Archive",
                "--format-validation", "all",
                "--raw-deep-validation",
            ), "quick")

    def test_unavailable_capability_precedes_root_access(self) -> None:
        args = self.parse(
            "--root", r"Z:\不应访问",
            "--format-validation", "all",
            "--raw-deep-validation",
        )
        with (
            mock.patch.object(
                scan_cli,
                "_requested_raw_capability",
                side_effect=core.PreflightError("fixture unavailable"),
            ) as capability,
            mock.patch.object(scan_cli, "_parse_roots") as parse_roots,
        ):
            with self.assertRaisesRegex(
                    core.PreflightError, "fixture unavailable"):
                scan_cli._create_new_run(args)
        capability.assert_called_once_with()
        parse_roots.assert_not_called()


class _RawRunFixture(unittest.TestCase):
    def setUp(self) -> None:
        os.makedirs(_RUNTIME_ROOT, exist_ok=True)
        self._temporary = tempfile.TemporaryDirectory(
            prefix="case_", dir=_RUNTIME_ROOT)
        self.base = self._temporary.name
        self.root = os.path.join(self.base, "Archive")
        self.output = os.path.join(self.base, "Snapshots")
        os.makedirs(self.root)
        os.makedirs(self.output)
        self.handles: list[dbrun.RunHandle] = []

    def tearDown(self) -> None:
        for handle in reversed(self.handles):
            try:
                dbrun.close_handle(handle, release_lease=True)
            except (OSError, sqlite3.Error, core.PreflightError):
                pass
        self._temporary.cleanup()

    def create_handle(self, names: tuple[str, ...]) -> dbrun.RunHandle:
        for name in names:
            with open(
                os.path.join(self.root, name),
                "wb",
            ) as stream:
                stream.write((name + "\n").encode("utf-8"))
        config = {
            "phase": "full",
            "quick": False,
            "hash": "none",
            "metadata_storage": "normalized",
            "format_validation": "all",
            "format_sample_percent": 100.0,
            "raw_deep_validation": True,
            "raw_timeout_policy": {
                **dbraw.raw_timeout_policy(),
                "override_seconds": None,
                "default_decision": "continue_waiting",
            },
            "verify_sample_percent": 0.0,
            "no_file_id": True,
        }
        partial = os.path.join(self.output, "Fixture.partial.sqlite")
        handle = dbrun.create_run(
            partial,
            [("档案", self.root)],
            config,
            output_dir=self.output,
            publish_stem_path=os.path.join(self.output, "Fixture"),
            tool_versions={envcap.RAW_CAPABILITY_ID: _capability()},
        )
        self.handles.append(handle)
        core.enumerate_and_reconcile(
            handle.connection,
            collect_file_id=False,
            exclude_dirs={self.output},
        )
        dbrun._prepare_format_selection(
            handle.connection, "all", 100.0)
        return handle

    @staticmethod
    def schema(con: sqlite3.Connection) -> list[tuple[object, ...]]:
        return con.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master"
            " WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
        ).fetchall()


class TestScanRawStage(_RawRunFixture):
    def test_terminal_evidence_privacy_and_zero_schema_change(self) -> None:
        handle = self.create_handle((
            "a_valid.dng", "b_unsupported.dng", "c_invalid.dng"))
        integration = scan_cli.RawScanIntegration(
            handle, {
                "raw_deep_validation": True,
                "format_validation": "all",
                "format_sample_percent": 100.0,
                "raw_timeout_policy": {
                    "override_seconds": None,
                    "default_decision": "continue_waiting",
                },
            }, create_journal=True)
        before_schema = self.schema(handle.connection)

        def runner(path: str, **kwargs) -> dbraw.RawDecodeOutcome:
            name = os.path.basename(path)
            status = (
                "unsupported" if "unsupported" in name
                else "invalid" if "invalid" in name else "valid")
            return _outcome(
                status,
                size_bytes=int(kwargs["expected_size"]),
            )

        result = integration.run(
            handle.connection,
            dbrun.RunCommandRouter(),
            show_current_file=True,
            on_progress=None,
            on_event=None,
            raw_runner=runner,
        )
        self.assertEqual("completed", result["state"])
        self.assertEqual((3, 1, 1, 1), (
            result["processed"], result["valid"],
            result["unsupported"], result["invalid"],
        ))
        self.assertEqual(before_schema, self.schema(handle.connection))
        with open(integration.journal.path, encoding="utf-8") as stream:
            journal_text = stream.read()
        self.assertNotIn("a_valid.dng", journal_text)
        self.assertNotIn("b_unsupported.dng", journal_text)
        self.assertIn("c_invalid.dng", journal_text)

        artifacts = integration.additional_artifact_builder(
            handle.connection,
            os.path.join(self.output, "Fixture_ABCDEF12.sqlite"),
            "a" * 64,
        )
        self.assertEqual(1, len(artifacts))
        report = json.loads(next(iter(artifacts.values())).decode("utf-8"))
        self.assertEqual("executed", report["state"])
        self.assertEqual(1, len(report["problems"]))
        self.assertNotIn("b_unsupported.dng", json.dumps(
            report, ensure_ascii=False))
        section = rawevidence.raw_issue_section_payload(report)
        self.assertEqual(("executed", 1, 1), (
            section["execution"], section["issue_files"],
            section["issue_records"],
        ))

    def test_joint_publication_builds_raw_json_and_single_issues_report(
        self,
    ) -> None:
        handle = self.create_handle(("broken.dng",))
        config = {
            "raw_deep_validation": True,
            "format_validation": "all",
            "format_sample_percent": 100.0,
            "raw_timeout_policy": {
                "override_seconds": None,
                "default_decision": "continue_waiting",
            },
        }
        integration = scan_cli.RawScanIntegration(
            handle, config, create_journal=True)
        completed = integration.run(
            handle.connection,
            dbrun.RunCommandRouter(),
            show_current_file=False,
            on_progress=None,
            on_event=None,
            raw_runner=lambda _path, **kwargs: _outcome(
                "invalid", size_bytes=int(kwargs["expected_size"])),
        )
        self.assertEqual("completed", completed["state"])
        handle.connection.execute(
            "UPDATE entries SET hash_status='skipped',meta_status='skipped'")
        handle.connection.execute(
            "UPDATE format_checks SET status='valid',stat_match=1")
        handle.connection.commit()
        schema_before = self.schema(handle.connection)
        dbstate.begin_sealing(handle.connection)
        dbstate.mark_sealed_unpublished(handle.connection)
        handle.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        handle.connection.execute("PRAGMA journal_mode=DELETE")
        handle.connection.close()

        result = dbstate.publish_sealed_snapshot(
            handle.partial_path,
            os.path.join(self.output, "Fixture.publishing.sqlite"),
            lease_path=handle.lease_path,
            lease_id=handle.lease.lease_id,
            issue_report_builder=integration.issue_report_builder,
            additional_artifact_builder=(
                integration.additional_artifact_builder),
        )
        self.handles.remove(handle)
        self.assertTrue(os.path.isfile(result.final_path))
        self.assertEqual(1, len(result.artifact_paths))
        self.assertTrue(os.path.isfile(result.artifact_paths[0]))
        self.assertTrue(os.path.isfile(result.issue_report_path))
        with open(result.issue_report_path, encoding="utf-8") as stream:
            issues = stream.read()
        self.assertIn("## RAW 深度校验问题", issues)
        self.assertIn("broken.dng", issues)
        with open(result.artifact_paths[0], encoding="utf-8") as stream:
            raw_report = json.load(stream)
        self.assertEqual(result.sha256, raw_report[
            "snapshot"]["database_identity"]["sha256"])
        final = sqlite3.connect(result.final_path)
        try:
            self.assertEqual(schema_before, self.schema(final))
        finally:
            final.close()
        integration.cleanup_after_publication(result)
        self.assertFalse(os.path.exists(integration.journal.path))

    def test_save_exit_retries_current_file_after_new_session(self) -> None:
        handle = self.create_handle(("resume.dng",))
        config = {
            "raw_deep_validation": True,
            "format_validation": "all",
            "format_sample_percent": 100.0,
            "raw_timeout_policy": {
                "override_seconds": None,
                "default_decision": "continue_waiting",
            },
        }
        integration = scan_cli.RawScanIntegration(
            handle, config, create_journal=True)
        router = dbrun.RunCommandRouter()

        def save_runner(path: str, **kwargs) -> dbraw.RawDecodeOutcome:
            router.route(dbrun.ControlCommand(
                sequence=1,
                action="save_exit",
                request_id="fixture-save",
            ))
            return _outcome(
                None,
                outcome="save_exit",
                control_action="save_exit",
                size_bytes=int(kwargs["expected_size"]),
            )

        saved = integration.run(
            handle.connection,
            router,
            show_current_file=False,
            on_progress=None,
            on_event=None,
            raw_runner=save_runner,
        )
        self.assertEqual("save_exit", saved["state"])
        self.assertEqual((), integration.journal.records)
        partial = handle.partial_path
        dbrun.close_handle(handle, release_lease=True)
        self.handles.remove(handle)

        resumed = dbrun.resume_run(partial)
        self.handles.append(resumed)
        resumed_integration = scan_cli.RawScanIntegration(
            resumed, config, create_journal=True)
        completed = resumed_integration.run(
            resumed.connection,
            dbrun.RunCommandRouter(),
            show_current_file=False,
            on_progress=None,
            on_event=None,
            raw_runner=lambda _path, **kwargs: _outcome(
                "valid", size_bytes=int(kwargs["expected_size"])),
        )
        self.assertEqual("completed", completed["state"])
        self.assertEqual(1, len(resumed_integration.journal.records))
        self.assertEqual("valid", resumed_integration.journal.records[0][
            "status"])


class TestPublicationArtifactBundle(_RawRunFixture):
    def test_database_failure_rolls_back_exact_companions_and_staging(
        self,
    ) -> None:
        working = os.path.join(self.base, "working.sqlite")
        final = os.path.join(self.base, "final.sqlite")
        raw_path = os.path.join(self.base, "final_Raw_Verification.json")
        with open(working, "wb") as stream:
            stream.write(b"database")
        original_publish = dbstate._publish_no_clobber

        def fail_database(source: str, target: str) -> None:
            if os.path.normcase(source) == os.path.normcase(working):
                raise core.PreflightError("fixture database failure")
            original_publish(source, target)

        with mock.patch.object(
                dbstate, "_publish_no_clobber", side_effect=fail_database):
            with self.assertRaisesRegex(
                    core.PreflightError, "fixture database failure"):
                dbstate._publish_with_artifacts_no_clobber(
                    working,
                    final,
                    "# Issues\n",
                    {raw_path: b'{"contract":"fixture"}\n'},
                )
        self.assertTrue(os.path.isfile(working))
        self.assertFalse(os.path.exists(final))
        self.assertFalse(os.path.exists(raw_path))
        self.assertFalse(os.path.exists(
            core.artifact_issue_report_path(final)))
        self.assertEqual([], [
            name for name in os.listdir(self.base)
            if name.endswith(".publishing")
        ])


if __name__ == "__main__":
    unittest.main(verbosity=2)
