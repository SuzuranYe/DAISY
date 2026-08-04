"""DAISY 哈希模块：三模式＋溯源＋独立实现抽验。

实现 full／incremental／none 三种模式、五项复用条件、计算溯源、
无固定超时的 stall 观测和 PowerShell Get-FileHash 独立抽验。
哈希 valid 的条件是摘要非空、读取字节等于文件大小，并且读取前后
size 和 mtime 一致。
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import random
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import Script_DAISY_Lib_01_Core as core

HASH_TOOL = "python-hashlib"
HASH_TOOL_VERSION = platform.python_version()


# === 单文件流式哈希 ===
def hash_one_file(path: str, expected_size: int | None = None,
                  chunk_bytes: int = core.HASH_CHUNK_BYTES,
                  on_chunk=None) -> dict:
    """流式 SHA-256＋前后 stat 一致性。返回 hashes 行所需全部字段。

    expected_size 为枚举登记的 size_bytes；不符（读前或读后）判 unstable。
    on_chunk(bytes_so_far) 每块回调（进度与 stall 心跳）。
    """
    r = {"hash_hex": None, "bytes_read": None, "chunk_bytes": chunk_bytes,
         "started_at_utc": core.now_utc_iso(), "finished_at_utc": None,
         "pre_size": None, "pre_mtime_utc": None,
         "post_size": None, "post_mtime_utc": None,
         "status": "failed", "failure_reason": None}
    ext = core.to_extended_path(path)
    try:
        st = os.stat(ext, follow_symlinks=False)
    except OSError as exc:
        r["failure_reason"] = f"pre_stat: {exc}"
        r["finished_at_utc"] = core.now_utc_iso()
        return r
    r["pre_size"] = st.st_size
    r["pre_mtime_utc"] = core.ns_to_utc_iso(st.st_mtime_ns)
    if expected_size is not None and st.st_size != expected_size:
        r["status"] = "unstable"
        r["failure_reason"] = (f"size_changed_since_enumeration: "
                               f"{expected_size} -> {st.st_size}")
        r["finished_at_utc"] = core.now_utc_iso()
        return r
    h = hashlib.sha256()
    n = 0
    try:
        with open(ext, "rb") as f:
            while True:
                b = f.read(chunk_bytes)
                if not b:
                    break
                h.update(b)
                n += len(b)
                if on_chunk:
                    on_chunk(n)
    except OSError as exc:
        r["bytes_read"] = n
        r["failure_reason"] = f"read: {exc}"
        r["finished_at_utc"] = core.now_utc_iso()
        return r
    r["hash_hex"] = h.hexdigest()
    r["bytes_read"] = n
    try:
        st2 = os.stat(ext, follow_symlinks=False)
        r["post_size"] = st2.st_size
        r["post_mtime_utc"] = core.ns_to_utc_iso(st2.st_mtime_ns)
    except OSError as exc:
        r["status"] = "unstable"
        r["failure_reason"] = f"post_stat: {exc}"
        r["finished_at_utc"] = core.now_utc_iso()
        return r
    if (r["pre_size"] == r["post_size"]
            and r["pre_mtime_utc"] == r["post_mtime_utc"]
            and (expected_size is None or n == expected_size)):
        r["status"] = "valid"
    else:
        r["status"] = "unstable"
        r["failure_reason"] = "changed_during_read"
    r["finished_at_utc"] = core.now_utc_iso()
    return r


class StallWatchdog:
    """哈希无固定超时：threshold 秒无进展报一次 stall，恢复后重新武装。"""

    def __init__(self, threshold_s: float, on_stall, poll_s: float = 5.0):
        self._threshold = threshold_s
        self._on_stall = on_stall
        self._poll = poll_s
        self._lock = threading.Lock()
        self._label = None
        self._last = time.monotonic()
        self._reported = True          # beat 之前不报
        self._stopped = threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()

    def beat(self, label: str) -> None:
        with self._lock:
            self._label = label
            self._last = time.monotonic()
            self._reported = False

    def _run(self) -> None:
        while not self._stopped.wait(self._poll):
            with self._lock:
                idle = time.monotonic() - self._last
                if self._reported or self._label is None or idle < self._threshold:
                    continue
                self._reported = True
                label, snap_idle = self._label, idle
            try:
                self._on_stall(label, snap_idle)
            except Exception:
                pass

    def stop(self) -> None:
        self._stopped.set()
        self._t.join(timeout=2.0)


# === 增量复用与计算溯源 ===
class PreviousSnapshot:
    def __init__(self, path: str, uuid_: str, index: dict,
                 has_file_issues: bool = False):
        self.path = path
        self.uuid = uuid_
        self.has_file_issues = has_file_issues
        self._index = index      # (当前 label, path_key) -> rec | "ambiguous"

    def lookup(self, label: str, path_key: str):
        rec = self._index.get((label, path_key))
        return None if rec == "ambiguous" else rec


def load_previous(prev_path: str,
                  map_root: dict | None = None) -> PreviousSnapshot:
    """验证当前 schema 3 来源并载入 status='valid' 的哈希索引。

    SQLite 损坏、扫描未完成、枚举缺口、哈希失败或 unstable 一律拒绝。
    单纯存在损坏／空白／无法解析的源文件不妨碍其他有效哈希复用；新扫描会
    重新读取元数据，并按当前结果生成自己的 Issues.md。"""
    if not os.path.isfile(prev_path):
        raise core.PreflightError(f"上一快照不存在：{prev_path}")
    recorded = core.filename_sha256_high32(prev_path)
    if recorded is None:
        raise core.PreflightError(f"上一快照文件名缺少 SHA-256 高32bit 指纹：{prev_path}")
    actual = core.sha256_file(prev_path)[:8].upper()
    if recorded != actual:
        raise core.PreflightError(
            f"上一快照文件名高32bit指纹不符：记录 {recorded}，实际 {actual}")
    con = sqlite3.connect(f"file:{prev_path}?mode=ro&immutable=1", uri=True)
    try:
        core.require_sealed_snapshot(con, "上一快照")
        (uuid_, schema_v, pk_rule, has_file_issues, has_unstable_entries,
         has_enumeration_gaps) = con.execute(
            "SELECT snapshot_uuid,schema_version,path_key_rule,"
            " has_file_issues,has_unstable_entries,"
            " has_enumeration_gaps FROM snapshot_info").fetchone()
        if pk_rule != core.PATH_KEY_RULE:
            raise core.PreflightError(
                f"上一快照 schema_version/path_key_rule 不符：{schema_v}/{pk_rule}"
                f"（可读 schema {sorted(core.READABLE_SCHEMA_VERSIONS)}；"
                f"当前 path_key_rule {core.PATH_KEY_RULE}）")
        actual_file_issues, = con.execute(
            "SELECT EXISTS(SELECT 1 FROM entries WHERE"
            " meta_status IN ('error','timeout'))").fetchone()
        actual_unstable, = con.execute(
            "SELECT EXISTS(SELECT 1 FROM entries WHERE"
            " meta_status='unstable' OR hash_status='unstable')").fetchone()
        actual_gaps, = con.execute(
            "SELECT EXISTS(SELECT 1 FROM dirs WHERE enum_status<>'ok')"
            " OR EXISTS(SELECT 1 FROM roots WHERE enum_status='failed')"
        ).fetchone()
        hash_failures, = con.execute(
            "SELECT COUNT(*) FROM entries WHERE hash_status='error'").fetchone()
        expected = (bool(actual_file_issues), bool(actual_unstable),
                    bool(actual_gaps))
        recorded_status = (bool(has_file_issues), bool(has_unstable_entries),
                           bool(has_enumeration_gaps))
        if recorded_status != expected:
            raise core.PreflightError(
                "上一快照状态字段与明细不一致："
                f"记录={recorded_status}，实际={expected}")
        blockers = []
        if has_enumeration_gaps:
            blockers.append("存在目录枚举缺口")
        if hash_failures:
            blockers.append(f"存在 {hash_failures} 个哈希失败条目")
        if has_unstable_entries:
            blockers.append("存在 unstable 条目")
        if blockers:
            raise core.PreflightError(
                "上一快照禁止作为增量来源：" + "；".join(blockers))
        mapping = map_root or {}
        index: dict = {}
        for (label, path_key, size, mtime, placeholder, vs, fih, hex_,
             origin, src_uuid, src_t, fin_t, tool, tool_v) in con.execute(
                "SELECT r.root_label, e.path_key, e.size_bytes,"
                " e.modified_at_utc, e.is_placeholder, e.volume_serial,"
                " e.file_index_hex, h.hash_hex, h.origin,"
                " h.source_snapshot_uuid, h.source_computed_at_utc,"
                " h.finished_at_utc, h.tool, h.tool_version"
                " FROM entries e JOIN roots r ON r.root_id = e.root_id"
                " JOIN hashes h ON h.entry_id = e.entry_id"
                " AND h.algorithm='sha256' AND h.status='valid'"):
            key = (mapping.get(label, label), path_key)
            if key in index:
                index[key] = "ambiguous"    # path_key 碰撞：不复用（条件 1 唯一性）
                continue
            # computed 行记录本快照事件，reused 行沿用最初计算事件
            src = (uuid_, fin_t) if origin == "computed" else (src_uuid, src_t)
            index[key] = {"size": size, "mtime": mtime,
                          "placeholder": placeholder,
                          "volume_serial": vs, "file_index_hex": fih,
                          "hash_hex": hex_, "source": src,
                          "tool": tool, "tool_version": tool_v}
        return PreviousSnapshot(
            prev_path, uuid_, index,
            has_file_issues=bool(has_file_issues))
    except sqlite3.Error as exc:
        raise core.PreflightError(
            f"上一快照 SQLite 结构不可读：{exc}") from exc
    finally:
        con.close()


def reuse_decision(entry: dict, prev: dict | None) -> tuple[bool, str]:
    """判断上一快照条目能否复用；存在性与唯一性由 lookup 负责。
    返回 (可否复用, reuse_basis 或拒绝原因)。"""
    if prev is None:
        return False, "no_previous_entry"
    if entry["size"] != prev["size"] or entry["mtime"] != prev["mtime"]:
        return False, "stat_changed"
    if entry["placeholder"] or prev["placeholder"]:
        return False, "placeholder"
    if (entry["volume_serial"] and entry["file_index_hex"]
            and prev["volume_serial"] and prev["file_index_hex"]):
        if (entry["volume_serial"] != prev["volume_serial"]
                or entry["file_index_hex"] != prev["file_index_hex"]):
            return False, "file_id_mismatch"      # 条件 5：不等强制重算
        return True, "size+mtime+fileid"
    return True, "size+mtime"


# === 哈希阶段（管线 [3/6]；逐文件断点续传） ===
def process_hash_stage(con: sqlite3.Connection, mode: str,
                       previous: PreviousSnapshot | None = None,
                       chunk_bytes: int = core.HASH_CHUNK_BYTES,
                       commit_every: int = 100,
                       max_files: int | None = None,
                       stall_seconds: float = 30.0,
                       on_progress=None, on_event=None,
                       error_warn_ratio: float = 0.2,
                       error_abort_ratio: float = 0.5) -> dict:
    """按 hash_status='pending' 逐文件哈希/复用入库。

    max_files 为内部测试钩子：处理 N 个文件后模拟中断（KeyboardInterrupt）。
    错误率 >warn 时告警继续，>abort 时中止并保留 partial。
    """
    if mode not in ("full", "incremental"):
        raise ValueError(f"mode={mode}")
    if mode == "incremental" and previous is None:
        raise core.PreflightError("增量模式需要 previous（--previous-snapshot）")
    cur = con.execute("UPDATE entries SET hash_status='skipped'"
                      " WHERE hash_status IN ('pending','processing')"
                      " AND is_placeholder=1")       # 云占位文件恒为 skipped
    n_placeholder = cur.rowcount
    con.execute("UPDATE entries SET hash_status='pending'"
                " WHERE hash_status='processing'")   # 遗留 processing 重置续传
    con.commit()
    roots = dict(con.execute("SELECT root_id, root_path FROM roots"))
    labels = dict(con.execute("SELECT root_id, root_label FROM roots"))
    todo = con.execute(
        "SELECT entry_id, root_id, rel_path, path_key, size_bytes,"
        " modified_at_utc, is_placeholder, volume_serial, file_index_hex"
        " FROM entries WHERE hash_status='pending'"
        " ORDER BY root_id, rel_path").fetchall()
    stats = {"total": len(todo), "done": 0, "reused": 0, "error": 0,
             "unstable": 0, "skipped": n_placeholder,
             "bytes_total": sum(r[4] for r in todo), "bytes_hashed": 0}
    warned = False
    wd = StallWatchdog(stall_seconds, lambda label, idle: (
        on_event and on_event("stall", file=label, idle_seconds=round(idle, 1))))
    processed = 0
    try:
        for (entry_id, root_id, rel, path_key, size, mtime, placeholder,
             vs, fih) in todo:
            if max_files is not None and processed >= max_files:
                con.commit()
                raise KeyboardInterrupt
            con.execute("UPDATE entries SET hash_status='processing'"
                        " WHERE entry_id=?", (entry_id,))
            con.execute("DELETE FROM hashes WHERE entry_id=?"
                        " AND algorithm='sha256'", (entry_id,))
            reused = False
            if mode == "incremental":
                prev = previous.lookup(labels[root_id], path_key)
                ok, basis = reuse_decision(
                    {"size": size, "mtime": mtime, "placeholder": placeholder,
                     "volume_serial": vs, "file_index_hex": fih}, prev)
                if ok:
                    src_uuid, src_t = prev["source"]
                    con.execute(
                        "INSERT INTO hashes (entry_id, algorithm, hash_hex,"
                        " origin, source_snapshot_uuid, source_computed_at_utc,"
                        " reuse_basis, size_bytes, status, tool, tool_version)"
                        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (entry_id, "sha256", prev["hash_hex"], "reused",
                         src_uuid, src_t, basis, size, "valid",
                         prev["tool"], prev["tool_version"]))
                    con.execute("UPDATE entries SET hash_status='done'"
                                " WHERE entry_id=?", (entry_id,))
                    stats["reused"] += 1
                    stats["done"] += 1
                    reused = True
            if not reused:
                path = os.path.join(roots[root_id], rel)
                wd.beat(rel)
                r = hash_one_file(path, expected_size=size,
                                  chunk_bytes=chunk_bytes,
                                  on_chunk=lambda _n: wd.beat(rel))
                con.execute(
                    "INSERT INTO hashes (entry_id, algorithm, hash_hex, origin,"
                    " size_bytes, bytes_read, chunk_bytes, started_at_utc,"
                    " finished_at_utc, pre_size, pre_mtime_utc, post_size,"
                    " post_mtime_utc, status, failure_reason, tool,"
                    " tool_version) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (entry_id, "sha256", r["hash_hex"], "computed", size,
                     r["bytes_read"], r["chunk_bytes"], r["started_at_utc"],
                     r["finished_at_utc"], r["pre_size"], r["pre_mtime_utc"],
                     r["post_size"], r["post_mtime_utc"], r["status"],
                     r["failure_reason"], HASH_TOOL, HASH_TOOL_VERSION))
                new_status = {"valid": "done", "failed": "error",
                              "unstable": "unstable"}[r["status"]]
                con.execute("UPDATE entries SET hash_status=?"
                            " WHERE entry_id=?", (new_status, entry_id))
                if r["status"] == "failed":
                    stats["error"] += 1
                    con.execute(
                        "INSERT INTO errors (entry_id, stage, error_code,"
                        " message, occurred_at_utc)"
                        " VALUES (?, 'hash', 'hash_failed', ?, ?)",
                        (entry_id, r["failure_reason"], core.now_utc_iso()))
                elif r["status"] == "unstable":
                    stats["unstable"] += 1
                else:
                    stats["done"] += 1
                stats["bytes_hashed"] += r["bytes_read"] or 0
            processed += 1
            if processed % commit_every == 0:
                con.commit()
            if on_progress:
                on_progress(processed, stats)
            if processed >= 20:
                ratio = stats["error"] / processed
                if ratio > error_abort_ratio:
                    con.commit()
                    raise core.PreflightError(
                        f"哈希错误率 {ratio:.0%} 超过 {error_abort_ratio:.0%}，"
                        f"中止并保留 partial（可 --resume 续传）")
                if ratio > error_warn_ratio and not warned:
                    warned = True
                    if on_event:
                        on_event("error_rate_warning", stage="hash",
                                 ratio=round(ratio, 3))
        con.commit()
    finally:
        wd.stop()
    return stats


# === 独立实现抽验（PowerShell Get-FileHash） ===
_PS_PROBE = (
    "if (-not (Get-Command Get-FileHash -ErrorAction SilentlyContinue)) {"
    " [Console]::Error.Write('Get-FileHash unavailable'); exit 3 };"
    "$PSVersionTable.PSVersion.ToString()"
)


def _powershell_candidates() -> list[str]:
    """按 PATH → Windows 常规位置返回去重后的 PowerShell 候选。"""
    candidates = []
    for command in ("powershell.exe", "powershell", "pwsh.exe", "pwsh"):
        found = shutil.which(command)
        if found:
            candidates.append(found)

    if os.name == "nt":
        windows_root = (os.environ.get("SystemRoot")
                        or os.environ.get("WINDIR"))
        if windows_root:
            candidates.append(os.path.join(
                windows_root, "System32", "WindowsPowerShell", "v1.0",
                "powershell.exe"))

        program_roots = [
            os.environ.get("ProgramW6432"),
            os.environ.get("ProgramFiles"),
            os.environ.get("ProgramFiles(x86)"),
        ]
        for root in program_roots:
            if root:
                candidates.append(
                    os.path.join(root, "PowerShell", "7", "pwsh.exe"))

        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            candidates.append(os.path.join(
                local_app_data, "Microsoft", "WindowsApps", "pwsh.exe"))

    unique = []
    seen = set()
    for path in candidates:
        absolute = os.path.abspath(path)
        key = os.path.normcase(absolute)
        if key not in seen:
            seen.add(key)
            unique.append(absolute)
    return unique


def _probe_powershell(path: str) -> tuple[str, str]:
    """验证 PowerShell 可启动、可报告版本并提供 Get-FileHash。"""
    try:
        proc = subprocess.run(
            [path, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command",
             _PS_PROBE],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise core.PreflightError(
            f"PowerShell 无法启动：{path}（{exc}）") from exc
    version = (proc.stdout or "").strip()
    if proc.returncode != 0:
        reason = (proc.stderr or "").strip()
        suffix = f"（{reason}）" if reason else ""
        raise core.PreflightError(
            f"PowerShell 缺少 Get-FileHash 或探测失败：{path}{suffix}")
    if not version:
        raise core.PreflightError(f"无法取得 PowerShell 版本：{path}")
    return os.path.abspath(path), version


def discover_powershell(explicit: str | None = None) -> tuple[str, str]:
    if explicit:
        if not os.path.isfile(explicit):
            raise core.PreflightError(
                f"PowerShell 显式路径不存在：{explicit}")
        return _probe_powershell(os.path.abspath(explicit))

    failures = []
    for path in _powershell_candidates():
        if not os.path.isfile(path):
            continue
        try:
            return _probe_powershell(path)
        except core.PreflightError as exc:
            failures.append(str(exc))

    if failures:
        raise core.PreflightError(
            "找到了 PowerShell 候选，但均无法用于独立哈希抽验：\n  "
            + "\n  ".join(failures))
    raise core.PreflightError(
        "未找到 PowerShell（已检查 PATH 与 Windows 常规安装位置；"
        "可用 --powershell-path 手动指定）")


_PS_BATCH = (
    "$ErrorActionPreference='Continue';"
    "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
    "$i=0;"
    "Get-Content -LiteralPath '{list}' -Encoding UTF8 | ForEach-Object {{"
    " try {{ $h=(Get-FileHash -LiteralPath $_ -Algorithm SHA256).Hash }}"
    " catch {{ $h='ERROR' }};"
    " \"$i $h\"; $i++ }}"
)
_HEX64 = re.compile(r"[0-9A-Fa-f]{64}\Z")
_PS_BATCH_SIZE = 200


def get_filehash_batch(paths: list[str], powershell: str | None = None,
                       on_progress=None) -> list[str | None]:
    """独立实现批量 SHA-256（Get-FileHash）。逐批一个 PS 进程，按行号回配；
    返回与 paths 等长的小写 hex 列表，读不到者为 None。"""
    if not paths:
        return []
    ps = powershell or discover_powershell()[0]
    result: list[str | None] = [None] * len(paths)
    for base in range(0, len(paths), _PS_BATCH_SIZE):
        batch = paths[base:base + _PS_BATCH_SIZE]
        fd, listfile = tempfile.mkstemp(suffix=".txt")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\r\n") as f:
                for p in batch:
                    f.write(os.path.abspath(p) + "\n")
            cmd = _PS_BATCH.format(list=listfile.replace("'", "''"))
            proc = subprocess.run(
                [ps, "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-Command", cmd], capture_output=True)
            for line in proc.stdout.decode("utf-8", "replace").splitlines():
                parts = line.strip().split()
                if len(parts) == 2 and parts[0].isdigit():
                    idx = int(parts[0])
                    if idx < len(batch) and _HEX64.match(parts[1]):
                        result[base + idx] = parts[1].lower()
        finally:
            os.unlink(listfile)
        if on_progress:
            on_progress(min(base + _PS_BATCH_SIZE, len(paths)), len(paths))
    return result


def pick_sample(rows: list[tuple], percent: float, min_count: int,
                seed: str = "") -> list[tuple]:
    """按大小四分层抽样：rows=[(entry_id, size_bytes), ...]。
    抽 max(min_count, ceil(percent%))，不足全取；同 seed 结果确定。"""
    n = len(rows)
    k = max(min_count, math.ceil(n * percent / 100.0))
    if k >= n:
        return list(rows)
    ordered = sorted(rows, key=lambda r: (r[1], r[0]))
    rng = random.Random(f"script-db-verify:{seed}")
    strata = 4
    bounds = [round(i * n / strata) for i in range(strata + 1)]
    quota = [k // strata] * strata
    for i in range(k % strata):
        quota[i] += 1
    picked: list[tuple] = []
    for i in range(strata):
        seg = ordered[bounds[i]:bounds[i + 1]]
        picked.extend(rng.sample(seg, min(len(seg), quota[i])))
    if len(picked) < k:                      # 层内不足时从剩余补齐
        taken = set(picked)
        rest = [r for r in ordered if r not in taken]
        picked.extend(rng.sample(rest, k - len(picked)))
    return picked


def independent_verify(con: sqlite3.Connection, percent: float = 1.0,
                       min_count: int = 100, powershell: str | None = None,
                       on_event=None, on_progress=None) -> dict:
    """对本次 computed valid 哈希抽样，用 Get-FileHash 独立复算比对。
    不一致→双方各重算一次→仍不一致标 unstable＋errors 留痕（醒目告警由调用方输出）。"""
    uuid_, = con.execute("SELECT snapshot_uuid FROM snapshot_info").fetchone()
    roots = dict(con.execute("SELECT root_id, root_path FROM roots"))
    rows = con.execute(
        "SELECT h.entry_id, e.size_bytes, e.root_id, e.rel_path, h.hash_hex"
        " FROM hashes h JOIN entries e ON e.entry_id = h.entry_id"
        " WHERE h.algorithm='sha256' AND h.status='valid'"
        " AND h.origin='computed' AND e.hash_status='done'"
        " ORDER BY e.root_id, e.rel_path").fetchall()
    stats = {"eligible": len(rows), "sampled": 0, "matched": 0,
             "mismatched": 0, "tool_error": 0}
    if not rows:
        return stats
    sample_ids = {eid for eid, _ in pick_sample(
        [(r[0], r[1]) for r in rows], percent, min_count, seed=uuid_)}
    chosen = [r for r in rows if r[0] in sample_ids]
    stats["sampled"] = len(chosen)
    ps = powershell or discover_powershell()[0]
    paths = [os.path.join(roots[rid], rel) for _, _, rid, rel, _ in chosen]
    got = get_filehash_batch(paths, powershell=ps, on_progress=on_progress)
    for (eid, _size, rid, rel, recorded), indep in zip(chosen, got):
        if indep is None:
            stats["tool_error"] += 1
            if on_event:
                on_event("verify_tool_error", rel_path=rel)
            continue
        if indep == recorded:
            stats["matched"] += 1
            continue
        # 复核一次：本工具重读重算＋独立实现重算
        path = os.path.join(roots[rid], rel)
        ours2 = hash_one_file(path)
        indep2 = get_filehash_batch([path], powershell=ps)[0]
        if (ours2["status"] == "valid" and ours2["hash_hex"] == recorded
                and indep2 == recorded):
            stats["matched"] += 1        # 首轮偶发异常，复核通过
            continue
        stats["mismatched"] += 1
        reason = (f"verify_mismatch: recorded={recorded}"
                  f" independent={indep} recheck_ours={ours2['hash_hex']}"
                  f" recheck_independent={indep2}")
        con.execute("UPDATE hashes SET status='unstable', failure_reason=?"
                    " WHERE entry_id=? AND algorithm='sha256'", (reason, eid))
        con.execute("UPDATE entries SET hash_status='unstable'"
                    " WHERE entry_id=?", (eid,))
        con.execute("INSERT INTO errors (entry_id, stage, error_code, message,"
                    " occurred_at_utc) VALUES (?, 'hash', 'verify_mismatch', ?, ?)",
                    (eid, reason, core.now_utc_iso()))
        if on_event:
            on_event("verify_mismatch", rel_path=rel, recorded=recorded,
                     independent=indep)
    con.commit()
    return stats
