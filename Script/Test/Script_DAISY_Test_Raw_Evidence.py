"""v1.6.0 RAW 工作 JSONL、伴随 JSON 与问题报告投影专项测试。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest


_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SCRIPT_DIR = os.path.dirname(_TEST_DIR)
_REPO_ROOT = os.path.dirname(_SCRIPT_DIR)
_LIB_DIR = os.path.join(_SCRIPT_DIR, "Lib")
sys.path[:0] = [_TEST_DIR, _SCRIPT_DIR, _LIB_DIR]

import Script_DAISY_Lib_Snapshot_Core as core
import Script_DAISY_Lib_Raw_Verify as dbraw
import Script_DAISY_Lib_Raw_Evidence as rawevidence


_RUNTIME_ROOT = os.path.join(
    _REPO_ROOT, ".test_runtime", "v1_6_0", "raw_evidence")


def _outcome(
    status: str,
    *,
    code: str | None = None,
    detail: str | None = None,
) -> dbraw.RawDecodeOutcome:
    valid = status == "valid"
    outcome = (
        "timeout" if status == "timeout"
        else "crashed" if status == "error"
        else "completed"
    )
    return dbraw.RawDecodeOutcome(
        outcome=outcome,
        status=status,
        code=code,
        detail=detail,
        decision=("skip_and_record" if status == "timeout" else "none"),
        decision_source=("advanced_policy" if status == "timeout" else "none"),
        control_action=None,
        size_bytes=128,
        elapsed_seconds=0.25,
        threshold_seconds=90.0,
        threshold_count=1 if status == "timeout" else 0,
        worker_pid=12345,
        worker_exitcode=39 if status == "error" else 0,
        worker_reaped=True,
        rawpy_version="0.test",
        libraw_version="0.synthetic",
        width=12 if valid else None,
        height=8 if valid else None,
        channels=3 if valid else None,
        pixel_count=288 if valid else None,
        decoded_bytes=288 if valid else None,
        events=(),
        events_truncated=False,
    )


class _EvidenceFixture(unittest.TestCase):
    def setUp(self) -> None:
        os.makedirs(_RUNTIME_ROOT, exist_ok=True)
        self._temporary = tempfile.TemporaryDirectory(
            prefix="case_", dir=_RUNTIME_ROOT)
        self.base = self._temporary.name
        self.partial = os.path.join(self.base, "Fixture.partial.sqlite")
        self.journal_path = rawevidence.raw_working_evidence_path(
            self.partial)
        self.binding = rawevidence.RawEvidenceBinding(
            snapshot_uuid="raw-evidence-fixture",
            format_mode="sample",
            format_sample_percent=10.0,
            rawpy_version="0.test",
            libraw_version="0.synthetic",
        )

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def journal(self) -> rawevidence.RawEvidenceJournal:
        return rawevidence.RawEvidenceJournal(
            self.journal_path, self.binding)

    @staticmethod
    def append(
        journal: rawevidence.RawEvidenceJournal,
        entry_id: int,
        status: str,
        path: str,
    ) -> dict[str, object]:
        return journal.append_result(
            entry_id=entry_id,
            logical_path=path,
            size_bytes=128,
            modified_at_utc="2026-01-01T00:00:00.0000000Z",
            outcome=_outcome(
                status,
                code=(
                    "raw_unsupported" if status == "unsupported"
                    else "decode_error" if status == "invalid"
                    else "raw_no_progress_timeout" if status == "timeout"
                    else "worker_crashed" if status == "error"
                    else None
                ),
                detail=(
                    f"synthetic {status}" if status in (
                        "invalid", "timeout", "error") else None),
            ),
        )


class TestRawEvidenceJournal(_EvidenceFixture):
    def test_paths_header_binding_and_utf8_lf(self) -> None:
        self.assertEqual(
            self.journal_path,
            os.path.join(self.base, "Fixture.raw_verification.jsonl"),
        )
        final_snapshot = os.path.join(self.base, "Fixture_ABCD1234.sqlite")
        self.assertEqual(
            rawevidence.raw_report_path(final_snapshot),
            os.path.join(
                self.base, "Fixture_ABCD1234_Raw_Verification.json"),
        )
        journal = self.journal()
        self.assertEqual(journal.records, ())
        with open(self.journal_path, "rb") as handle:
            payload = handle.read()
        self.assertFalse(payload.startswith(b"\xef\xbb\xbf"))
        self.assertNotIn(b"\r", payload)
        self.assertTrue(payload.endswith(b"\n"))
        reopened = rawevidence.RawEvidenceJournal(
            self.journal_path, self.binding, create=False)
        self.assertEqual(reopened.records, ())

        before = payload
        mismatch = rawevidence.RawEvidenceBinding(
            snapshot_uuid="other-snapshot",
            format_mode="sample",
            format_sample_percent=10.0,
            rawpy_version="0.test",
        )
        with self.assertRaises(core.PreflightError):
            rawevidence.RawEvidenceJournal(
                self.journal_path, mismatch, create=False)
        with open(self.journal_path, "rb") as handle:
            self.assertEqual(handle.read(), before)

    def test_terminal_records_resume_and_privacy(self) -> None:
        journal = self.journal()
        statuses = ("valid", "unsupported", "invalid", "timeout", "error")
        for entry_id, status in enumerate(statuses, 1):
            self.append(
                journal, entry_id, status,
                f"夹具\\private_{status}.dng")
        self.assertEqual(
            [record["sequence"] for record in journal.records],
            [1, 2, 3, 4, 5],
        )
        self.assertNotIn("path", {
            key for key, value in journal.records[1].items()
            if value is not None
        })
        self.assertIsNone(journal.records[1]["path"])
        self.assertIsNone(journal.records[1]["detail"])
        self.assertTrue(journal.matches_terminal(
            1,
            size_bytes=128,
            modified_at_utc="2026-01-01T00:00:00.0000000Z",
        ))
        self.assertFalse(journal.matches_terminal(
            1,
            size_bytes=129,
            modified_at_utc="2026-01-01T00:00:00.0000000Z",
        ))
        reopened = rawevidence.RawEvidenceJournal(
            self.journal_path, self.binding, create=False)
        self.assertEqual(reopened.records, journal.records)
        with open(self.journal_path, encoding="utf-8") as handle:
            text = handle.read()
        self.assertNotIn("private_unsupported.dng", text)
        self.assertNotIn("private_valid.dng", text)
        self.assertIn("private_invalid.dng", text)

    def test_pause_and_stop_are_not_terminal_evidence(self) -> None:
        journal = self.journal()
        for action, outcome_name in (
            ("pause", "paused"),
            ("save_exit", "save_exit"),
            ("stop", "stopped"),
        ):
            outcome = _outcome("error")
            paused = dbraw.RawDecodeOutcome(
                **{
                    **outcome.__dict__,
                    "outcome": outcome_name,
                    "status": None,
                    "control_action": action,
                }
            )
            with self.subTest(action=action):
                with self.assertRaises(ValueError):
                    journal.append_result(
                        entry_id=1,
                        logical_path="夹具\\paused.dng",
                        size_bytes=128,
                        modified_at_utc="2026-01-01T00:00:00.0000000Z",
                        outcome=paused,
                    )
        self.assertEqual(journal.records, ())

    def test_truncated_tail_repairs_only_after_binding_validation(self) -> None:
        journal = self.journal()
        self.append(journal, 1, "valid", "夹具\\valid.dng")
        with open(self.journal_path, "ab") as handle:
            handle.write(b'{"record":"result"')
        with open(self.journal_path, "rb") as handle:
            broken = handle.read()

        mismatch = rawevidence.RawEvidenceBinding(
            snapshot_uuid="wrong",
            format_mode="sample",
            format_sample_percent=10.0,
            rawpy_version="0.test",
        )
        with self.assertRaises(core.PreflightError):
            rawevidence.RawEvidenceJournal(
                self.journal_path, mismatch, create=False)
        with open(self.journal_path, "rb") as handle:
            self.assertEqual(handle.read(), broken)

        repaired = rawevidence.RawEvidenceJournal(
            self.journal_path, self.binding, create=False)
        self.assertTrue(repaired.truncated_tail_repaired)
        self.assertEqual(len(repaired.records), 1)
        with open(self.journal_path, "rb") as handle:
            payload = handle.read()
        self.assertTrue(payload.endswith(b"\n"))
        self.assertNotIn(b'{"record":"result"', payload)


class TestRawEvidenceReport(_EvidenceFixture):
    def completed_report(self) -> dict[str, object]:
        journal = self.journal()
        statuses = ("valid", "unsupported", "invalid", "timeout", "error")
        for entry_id, status in enumerate(statuses, 1):
            self.append(
                journal, entry_id, status,
                f"夹具\\private_{status}.dng")
        return rawevidence.build_raw_report(
            journal,
            range(1, 6),
            raw_candidate_total=9,
            snapshot_filename="Fixture.sqlite",
            database_identity={"sha256": "A" * 64},
        )

    def test_report_counts_issues_and_unsupported_privacy(self) -> None:
        report = self.completed_report()
        rawevidence.validate_raw_report(report)
        self.assertEqual(report["state"], "executed")
        self.assertEqual(report["conclusion"], "issues_found")
        self.assertEqual(report["counts"], {
            "valid": 1,
            "unsupported": 1,
            "invalid": 1,
            "timeout": 1,
            "error": 1,
        })
        self.assertEqual(
            {row["status"] for row in report["problems"]},
            {"invalid", "timeout", "error"},
        )
        serialized = json.dumps(report, ensure_ascii=False)
        self.assertNotIn("private_unsupported.dng", serialized)
        self.assertNotIn("private_valid.dng", serialized)
        markdown = rawevidence.render_raw_issue_section(report)
        self.assertIn("## RAW 深度校验问题", markdown)
        self.assertIn("执行状态：已执行", markdown)
        self.assertIn("校验范围：抽样", markdown)
        self.assertIn("仅统计，不列路径", markdown)
        self.assertIn("| 解码失败 |", markdown)
        self.assertNotIn("- unsupported：", markdown)
        self.assertIn("private_invalid.dng", markdown)
        self.assertNotIn("private_unsupported.dng", markdown)
        self.assertEqual(
            rawevidence.render_raw_issue_section(None),
            "## RAW 深度校验问题\n\n"
            "NULL（本次未执行或旧库未记录）\n",
        )

    def test_incomplete_report_cannot_claim_zero_problems(self) -> None:
        journal = self.journal()
        self.append(journal, 1, "valid", "夹具\\valid.dng")
        report = rawevidence.build_raw_report(
            journal,
            (1, 2),
            raw_candidate_total=2,
        )
        self.assertEqual(report["state"], "incomplete")
        self.assertEqual(report["conclusion"], "incomplete")
        markdown = rawevidence.render_raw_issue_section(report)
        self.assertIn("NULL（执行未完成", markdown)
        self.assertNotIn("0（已执行", markdown)

    def test_publish_is_utf8_lf_verified_and_no_clobber(self) -> None:
        report = self.completed_report()
        snapshot = os.path.join(self.base, "Fixture_ABCD1234.sqlite")
        published = rawevidence.publish_raw_report(report, snapshot)
        with open(published, "rb") as handle:
            original = handle.read()
        self.assertFalse(original.startswith(b"\xef\xbb\xbf"))
        self.assertNotIn(b"\r", original)
        with open(published, encoding="utf-8") as handle:
            rawevidence.validate_raw_report(json.load(handle))
        with self.assertRaises(core.PreflightError):
            rawevidence.publish_raw_report(report, snapshot)
        with open(published, "rb") as handle:
            self.assertEqual(handle.read(), original)
        self.assertEqual([
            name for name in os.listdir(self.base)
            if name.endswith(".partial")
        ], [])

    def test_report_rejects_records_outside_frozen_selection(self) -> None:
        journal = self.journal()
        self.append(journal, 7, "valid", "夹具\\valid.dng")
        with self.assertRaises(core.PreflightError):
            rawevidence.build_raw_report(
                journal,
                (1,),
                raw_candidate_total=1,
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
