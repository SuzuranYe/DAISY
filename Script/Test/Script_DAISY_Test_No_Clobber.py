"""DAISY 快照不可覆盖回归测试。

每项同时核对：退出码／文件数量／旧件 SHA-256／旧 snapshot_uuid／残留。

运行：python -B .\\Script\\Test\\Script_DAISY_Test_No_Clobber.py
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SCRIPT = os.path.dirname(_TEST_DIR)
_REPO_ROOT = os.path.dirname(_SCRIPT)
_LIB = os.path.join(_SCRIPT, "Lib")
_TOOL = os.path.join(_SCRIPT, "Tool")
sys.path[:0] = [_TEST_DIR, _SCRIPT, _LIB, _TOOL]

import Script_DAISY_Lib_01_Core as core
import Script_DAISY_Lib_04_Diff as dbdiff

QUICK = os.path.join(_TOOL, "Script_DAISY_Tool_12_Quick_Scan.py")


def snapshot_uuid(db: str) -> str:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return con.execute("SELECT snapshot_uuid FROM snapshot_info").fetchone()[0]
    finally:
        con.close()


class _Tree(unittest.TestCase):
    """夹具：小档案树＋输出目录。"""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.arch = os.path.join(self._td.name, "Arch验收")
        os.makedirs(self.arch)
        for name, data in [("a.bin", b"alpha"), ("b.txt", b"beta-data")]:
            with open(os.path.join(self.arch, name), "wb") as f:
                f.write(data)
        self.out = os.path.join(self._td.name, "Out")
        os.makedirs(self.out)

    def tearDown(self):
        self._td.cleanup()

    def run_quick(self):
        return subprocess.run(
            [sys.executable, "-B", QUICK, "--root", f"V={self.arch}",
             "--output-dir", self.out, "--quiet"],
            capture_output=True, timeout=120)

    def sealed(self):
        return sorted(f for f in os.listdir(self.out)
                      if f.endswith(".sqlite")
                      and not f.endswith(".partial.sqlite"))

    def residues(self):
        return sorted(f for f in os.listdir(self.out)
                      if f.endswith((".sqlite-wal", ".sqlite-shm", ".lock")))

    def make_partial(self, base: str):
        partial = os.path.join(self.out, base + ".partial.sqlite")
        con = core.create_partial_snapshot(
            partial, [("V", self.arch)], config={"phase": "no-clobber-test"})
        core.enumerate_and_reconcile(con)
        con.execute("UPDATE entries SET meta_status='skipped',"
                    " hash_status='skipped'")
        con.commit()
        return partial, con


class TestSameSecondQuick(_Tree):
    def test_two_back_to_back_runs_both_survive(self):
        r1 = self.run_quick()
        self.assertEqual(r1.returncode, 0, r1.stderr.decode("utf-8", "replace"))
        first, = self.sealed()
        p1 = os.path.join(self.out, first)
        sha1, uuid1 = core.sha256_file(p1), snapshot_uuid(p1)
        r2 = self.run_quick()                     # 大概率同一秒
        self.assertEqual(r2.returncode, 0, r2.stderr.decode("utf-8", "replace"))
        both = self.sealed()
        self.assertEqual(len(both), 2, both)      # 两份都在、名称唯一
        self.assertEqual(core.sha256_file(p1), sha1)     # 第一份逐字节未动
        self.assertEqual(snapshot_uuid(p1), uuid1)
        self.assertEqual(self.residues(), [])


class TestFinalExists(_Tree):
    def test_publish_conflict_keeps_old_bytes(self):
        forced_digest = "0" * 64
        final = os.path.join(self.out, "X_00000000.sqlite")
        with open(final, "wb") as f:
            f.write(b"OLD-EVIDENCE")
        sha_old = core.sha256_file(final)
        partial, con = self.make_partial("X")
        with patch.object(core, "sha256_file", return_value=forced_digest):
            with self.assertRaises(core.PreflightError):
                core.finalize_snapshot(con, partial, hash_coverage="none")
        self.assertEqual(core.sha256_file(final), sha_old)   # 旧件不动
        self.assertTrue(os.path.exists(partial))             # 本次结果保留
        self.assertFalse(any(
            name.endswith((".sha256", ".sidecar.zip"))
            for name in os.listdir(self.out)))


class TestEmbeddedEvidence(_Tree):
    def test_success_is_one_self_describing_sqlite(self):
        result = self.run_quick()
        self.assertEqual(
            result.returncode, 0, result.stderr.decode("utf-8", "replace"))
        name, = self.sealed()
        self.assertEqual(os.listdir(self.out), [name])
        final = os.path.join(self.out, name)
        self.assertEqual(
            core.filename_sha256_high32(final), core.sha256_file(final)[:8].upper())
        con = sqlite3.connect(f"file:{final}?mode=ro", uri=True)
        manifest = json.loads(con.execute(
            "SELECT manifest_json FROM snapshot_manifest").fetchone()[0])
        self.assertFalse(manifest["integrity"]["full_digest_retained"])
        self.assertEqual(manifest["integrity"]["retained_bits"], 32)
        events = con.execute(
            "SELECT event FROM run_events ORDER BY event_seq").fetchall()
        con.close()
        self.assertGreater(len(events), 1)
        self.assertEqual(events[-1][0], "snapshot_sealed")


class TestPartialOwnership(_Tree):
    def test_second_creator_fails(self):
        partial, con = self.make_partial("Z")
        with self.assertRaises(core.PreflightError):         # O_EXCL 独占
            core.create_partial_snapshot(
                partial, [("V", self.arch)],
                config={"phase": "no-clobber-test"})
        con.close()

    def test_lock_owner_alive_vs_dead(self):
        partial = os.path.join(self.out, "L.partial.sqlite")
        with open(partial, "wb"):
            pass
        lp = partial + ".lock"
        # 活 owner（本进程 pid）：接管也必须拒绝
        with open(lp, "w", encoding="utf-8", newline="\n") as f:
            json.dump({"run_uuid": "r", "host": "h", "pid": os.getpid(),
                       "acquired_at_utc": "t"}, f)
        with self.assertRaises(core.PreflightError):
            core.acquire_scan_lock(partial, takeover=True)
        # 死 owner：默认拒绝（提示接管），takeover=True 可接管
        dead = subprocess.run([sys.executable, "-B", "-c", "import os;"
                               "print(os.getpid())"], capture_output=True,
                              text=True, timeout=60)
        dead_pid = int(dead.stdout.strip())
        with open(lp, "w", encoding="utf-8", newline="\n") as f:
            json.dump({"run_uuid": "r", "host": "h", "pid": dead_pid,
                       "acquired_at_utc": "t"}, f)
        with self.assertRaises(core.PreflightError):
            core.acquire_scan_lock(partial, takeover=False)
        core.acquire_scan_lock(partial, takeover=True)       # 接管成功
        core.release_scan_lock(partial)


class TestSuffixCollisions(_Tree):
    def test_normal_vs_normal_no_clobber(self):
        partial1, con1 = self.make_partial("N")
        forced_digest = "a" * 64
        with patch.object(core, "sha256_file", return_value=forced_digest):
            final1 = core.finalize_snapshot(
                con1, partial1, hash_coverage="none")
        sha1 = core.sha256_file(final1)
        # 同 base 再来一轮（人为同名，绕过唯一命名直击 no-clobber 层）
        partial2, con2 = self.make_partial("N")
        with patch.object(core, "sha256_file", return_value=forced_digest):
            with self.assertRaises(core.PreflightError):
                core.finalize_snapshot(con2, partial2, hash_coverage="none")
        self.assertEqual(core.sha256_file(final1), sha1)

    def test_abnormal_vs_abnormal_no_clobber(self):
        partial1, con1 = self.make_partial("M")
        forced_digest = "b" * 64
        with patch.object(core, "sha256_file", return_value=forced_digest):
            final1 = core.finalize_snapshot(
                con1, partial1, hash_coverage="none", force_abnormal=True)
        self.assertTrue(final1.endswith(
            "_Abnormal_BBBBBBBB.sqlite"))
        sha1 = core.sha256_file(final1)
        partial2, con2 = self.make_partial("M")
        with patch.object(core, "sha256_file", return_value=forced_digest):
            with self.assertRaises(core.PreflightError):
                core.finalize_snapshot(
                    con2, partial2, hash_coverage="none",
                    force_abnormal=True)
        self.assertEqual(core.sha256_file(final1), sha1)


class TestMissingFilenameFingerprint(_Tree):
    def test_missing_filename_fingerprint_requires_force(self):
        result = self.run_quick()
        self.assertEqual(result.returncode, 0)
        name, = self.sealed()
        sealed = os.path.join(self.out, name)
        token = core.filename_sha256_high32(sealed)
        self.assertIsNotNone(token)
        legacy = os.path.join(
            self.out, name[:-len(f"_{token}.sqlite")] + ".sqlite")
        os.rename(sealed, legacy)
        out = os.path.join(self._td.name, "forced.diff.sqlite")
        with self.assertRaises(core.PreflightError):
            dbdiff.compare(legacy, legacy, out)
        dbdiff.compare(legacy, legacy, out, force=True)
        con = sqlite3.connect(out)
        self.assertEqual(
            con.execute("SELECT forced FROM diff_info").fetchone()[0], 1)
        con.close()


class TestHashMismatch(_Tree):
    def test_mismatch_is_hard_failure_even_with_force(self):
        result = self.run_quick()
        self.assertEqual(result.returncode, 0)
        name, = self.sealed()
        final = os.path.join(self.out, name)
        with open(final, "ab") as f:
            f.write(b"tamper")
        with self.assertRaises(core.PreflightError):
            dbdiff.compare(
                final, final, os.path.join(self._td.name, "bad.diff.sqlite"),
                force=True)


class TestEventLogFailureRetention(_Tree):
    def test_publish_conflict_keeps_runtime_event_log(self):
        forced_digest = "e" * 64
        final = os.path.join(self.out, "E_EEEEEEEE.sqlite")
        with open(final, "wb") as f:
            f.write(b"OLD")
        partial, con = self.make_partial("E")
        event_log = os.path.join(self.out, "E.events.jsonl")
        payload = '{"ts":"t","event":"started"}\n'
        with open(event_log, "w", encoding="utf-8", newline="\n") as f:
            f.write(payload)
        with patch.object(core, "sha256_file", return_value=forced_digest):
            with self.assertRaises(core.PreflightError):
                core.finalize_snapshot(
                    con, partial, "none", event_log_path=event_log)
        with open(event_log, encoding="utf-8") as f:
            self.assertEqual(f.read(), payload)
        self.assertTrue(os.path.exists(partial))


class TestRerunAfterConflict(_Tree):
    def test_new_run_allowed_conflict_untouched(self):
        forced_digest = "f" * 64
        final = os.path.join(self.out, "F_FFFFFFFF.sqlite")
        with open(final, "wb") as f:
            f.write(b"CONFLICT-EVIDENCE")
        partial, con = self.make_partial("F")
        with patch.object(core, "sha256_file", return_value=forced_digest):
            with self.assertRaises(core.PreflightError):
                core.finalize_snapshot(con, partial, hash_coverage="none")
        sha_conflict = core.sha256_file(final)
        sha_partial = core.sha256_file(partial)
        r = self.run_quick()                      # 冲突后新运行：全新名称
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace"))
        self.assertEqual(len(self.sealed()), 2)   # F.sqlite＋新快照
        self.assertEqual(core.sha256_file(final), sha_conflict)
        self.assertEqual(core.sha256_file(partial), sha_partial)  # 均未被复用


if __name__ == "__main__":
    unittest.main(verbosity=2)
