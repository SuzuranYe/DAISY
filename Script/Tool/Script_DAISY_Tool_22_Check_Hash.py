r"""Script_DAISY_Tool_22_Check_Hash：哈希巡检——既有快照 vs 当前磁盘的按需核对。

① 全量 stat 核对：快照全部条目的存在性与 size/mtime（枚举级，分钟级）；
② 哈希核对：stat 未变者中抽样（默认 1%，至少 100，按大小分层）或 --full 全量，
   用独立实现（PowerShell Get-FileHash）重算比对。
只读快照与磁盘，产出 JSON 报告，不生成新快照。盘符可以变化：
用 --root "label=当前路径" 指定本次挂载点，缺省用快照记录的 root_path。

用法：
  python .\Script\Script_DAISY_MAIN.py check-hash --snapshot .\Output\Snapshots\Scan_x.sqlite ^
      --root "Archive2024=E:\Archive2024" [--sample-percent 1] [--full]
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys

_TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
_LIB_DIR = os.path.join(os.path.dirname(_TOOL_DIR), "Lib")
sys.path.insert(0, _LIB_DIR)
import Script_DAISY_Lib_01_Core as core
import Script_DAISY_Lib_03_Hash as dbh


def patrol(snapshot_path: str, root_map: dict | None = None,
           sample_percent: float = 1.0, full: bool = False,
           powershell: str | None = None, force: bool = False,
           on_progress=None) -> dict:
    """执行巡检并返回报告 dict。root_map={label: 当前路径}。"""
    snapshot_path = os.path.abspath(snapshot_path)
    if not os.path.isfile(snapshot_path):
        raise core.PreflightError(f"快照不存在：{snapshot_path}")
    recorded = core.filename_sha256_high32(snapshot_path)
    if recorded is not None:
        if recorded != core.sha256_file(snapshot_path)[:8].upper():
            raise core.PreflightError("快照文件名高32bit指纹不符")
    elif not force:
        raise core.PreflightError(
            f"快照文件名缺少高32bit指纹（--force 可越过）：{snapshot_path}")
    con = sqlite3.connect(f"file:{snapshot_path}?mode=ro", uri=True)
    try:
        uuid_, coverage, status = con.execute(
            "SELECT snapshot_uuid, hash_coverage, scan_status"
            " FROM snapshot_info").fetchone()
        if status != "complete":
            raise core.PreflightError(f"快照未封存（scan_status={status}）")
        roots = {}
        labels = []
        label_by_rid = {}
        for root_id, label, rpath in con.execute(
                "SELECT root_id, root_label, root_path FROM roots"):
            cur = (root_map or {}).get(label, rpath)
            if not os.path.isdir(cur):
                raise core.PreflightError(
                    f"root「{label}」当前路径不存在：{cur}"
                    f"（用 --root \"{label}=当前路径\" 指定）")
            roots[root_id] = cur
            labels.append(label)
            label_by_rid[root_id] = label

        # ① 全量 stat 核对（存在性＋size/mtime）
        stat_missing, stat_changed = [], []
        flagged: set[int] = set()
        entries = con.execute(
            "SELECT entry_id, root_id, rel_path, size_bytes, modified_at_utc"
            " FROM entries WHERE is_placeholder = 0"
            " ORDER BY root_id, rel_path").fetchall()
        for eid, rid, rel, size, mtime in entries:
            p = os.path.join(roots[rid], rel)
            try:
                st = os.stat(core.to_extended_path(p), follow_symlinks=False)
            except OSError:
                stat_missing.append({"path": label_by_rid[rid] + "\\" + rel,
                                     "rel_path": rel, "root_id": rid})
                flagged.add(eid)
                continue
            if st.st_size != size or core.ns_to_utc_iso(st.st_mtime_ns) != mtime:
                stat_changed.append(
                    {"path": label_by_rid[rid] + "\\" + rel,
                     "rel_path": rel, "root_id": rid,
                     "size_recorded": size, "size_now": st.st_size,
                     "mtime_recorded": mtime,
                     "mtime_now": core.ns_to_utc_iso(st.st_mtime_ns)})
                flagged.add(eid)

        # ② 哈希核对（独立实现）：valid 哈希且 stat 未变者
        hrows = [r for r in con.execute(
            "SELECT h.entry_id, e.size_bytes, e.root_id, e.rel_path, h.hash_hex"
            " FROM hashes h JOIN entries e ON e.entry_id = h.entry_id"
            " WHERE h.algorithm='sha256' AND h.status='valid'"
            " AND e.is_placeholder = 0 ORDER BY e.root_id, e.rel_path")
            if r[0] not in flagged]
        if full:
            chosen = hrows
        else:
            ids = {eid for eid, _ in dbh.pick_sample(
                [(r[0], r[1]) for r in hrows], sample_percent, 100,
                seed=uuid_ + ":patrol")}
            chosen = [r for r in hrows if r[0] in ids]
        hash_mismatched, hash_tool_error = [], []
        used_tools: dict[str, dict] = {}
        if chosen:
            ps, ps_version = dbh.discover_powershell(powershell)
            ps_info = core.resolved_tool_info(
                "powershell", ps, explicit=bool(powershell),
                version=ps_version)
            used_tools["powershell"] = ps_info
            core.emit_gui_event(
                "tools_detected", tools={"powershell": ps_info})
            paths = [os.path.join(roots[rid], rel)
                     for _, _, rid, rel, _ in chosen]
            got = dbh.get_filehash_batch(paths, powershell=ps,
                                         on_progress=on_progress)
            for (eid, _s, rid, rel, recorded), indep in zip(chosen, got):
                if indep is None:
                    hash_tool_error.append(
                        {"path": label_by_rid[rid] + "\\" + rel,
                         "rel_path": rel, "root_id": rid})
                elif indep != recorded:
                    hash_mismatched.append(
                        {"path": label_by_rid[rid] + "\\" + rel,
                         "rel_path": rel, "root_id": rid,
                         "recorded": recorded, "independent": indep})
        return {"snapshot": os.path.basename(snapshot_path),
                "root_labels": labels,
                "snapshot_uuid": uuid_,
                "hash_coverage": coverage,
                "mode": "full" if full else f"sample_{sample_percent}pct",
                "entries_total": len(entries),
                "stat_checked": len(entries),
                "stat_missing": stat_missing,
                "stat_changed": stat_changed,
                "hash_eligible": len(hrows),
                "hash_checked": len(chosen),
                "hash_mismatched": hash_mismatched,
                "hash_tool_error": hash_tool_error,
                "tools": used_tools,
                "checked_at_utc": core.now_utc_iso(),
                "ok": not (stat_missing or stat_changed
                           or hash_mismatched or hash_tool_error)}
    finally:
        con.close()


def main() -> int:
    core.force_utf8_io()
    ap = argparse.ArgumentParser(description="哈希巡检：快照 vs 当前磁盘（只读）")
    ap.add_argument("--snapshot", required=True, help="封存快照 .sqlite 路径")
    ap.add_argument("--root", action="append", default=[],
                    help="label=当前路径，可重复；缺省用快照记录的 root_path")
    ap.add_argument("--sample-percent", type=float, default=1.0)
    ap.add_argument("--full", action="store_true", help="全量哈希核对")
    ap.add_argument("--powershell-path")
    ap.add_argument("--force", action="store_true",
                    help="文件名高32bit指纹缺失时仍继续（不符仍拒绝）")
    ap.add_argument("--report", help="报告 JSON 输出路径（默认 Output/Reports）")
    args = ap.parse_args()

    root_map = {}
    for spec in args.root:
        label, _, path = spec.partition("=")
        if not _ or not label or not path:
            print(f"--root 语法应为 label=路径：{spec}", file=sys.stderr)
            return 2
        root_map[label] = path
    try:
        prog = core.Progress(1, 1, "哈希巡检")
        rep = patrol(args.snapshot, root_map,
                     sample_percent=args.sample_percent, full=args.full,
                     powershell=args.powershell_path, force=args.force,
                     on_progress=lambda i, n: prog.update(i, total=n))
    except core.PreflightError as exc:
        print(f"巡检失败：{exc}", file=sys.stderr)
        return 2

    prog.finish(
        f"stat {rep['stat_checked']:,} 条 / 哈希 {rep['hash_checked']:,} 条")
    if args.report:
        report_path = os.path.abspath(args.report)
        os.makedirs(os.path.dirname(report_path) or ".", exist_ok=True)
    else:                               # 可删报告统一进 Output\Reports\（与快照分离）
        os.makedirs("Output/Reports", exist_ok=True)
        report_path = os.path.abspath(os.path.join(
            "Output/Reports",
            core.snapshot_name(rep["root_labels"], "Check_Hash")
            + ("" if rep["ok"] else "_Abnormal") + ".json"))
    with open(report_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(rep, f, ensure_ascii=False, indent=2)

    print(f"快照：{rep['snapshot']}（coverage={rep['hash_coverage']}）")
    print(f"stat 核对：{rep['stat_checked']:,} 条 | 缺失 {len(rep['stat_missing'])}"
          f" | 变化 {len(rep['stat_changed'])}")
    print(f"哈希核对（{rep['mode']}）：{rep['hash_checked']:,}/"
          f"{rep['hash_eligible']:,} 条 | 不一致 {len(rep['hash_mismatched'])}"
          f" | 工具错误 {len(rep['hash_tool_error'])}")
    print(f"报告：{report_path}")
    if rep["ok"]:
        print("结论：当前磁盘与基准快照一致（在本次核对口径内）")
        return 0
    print("结论：发现差异——建议尽快做完整性复核（--hash full 全量重扫＋Diff）",
          file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
