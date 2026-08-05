r"""Script_DAISY_Module_DBS_41_Export_Report：DBS-41 结果报告导出。

快照导出（分组清单＋簿记，多 CSV、UTF-8 无 BOM、LF；规范化字段不剔除）：
  Tree.csv / Tree_dirs.csv                 —— 树
  Exif_inventory_photo/video/working/document.csv —— EXIF 组
  GPS_inventory_video.csv                       —— 视频规范化 GPS 点
  Stream_inventory_video/audio.csv         —— ffmpeg 组
  Hash_inventory.csv                       —— 哈希组
  Archive_inventory.csv / _members.csv     —— 压缩包组
  Summary.csv / Errors.csv / Metadata_diagnostics.csv —— 簿记与诊断
  （raw_payloads 为 zlib BLOB，不入 CSV——用 SQL 直接查询快照库）

Diff 数据库导出：
  Diff_summary.md（status×evidence 交叉表、失败子树醒目段、内容/结构双维度
  结论、propagated 单列声明、覆盖声明）＋ Diff_details.csv
  ＋ Diff_dirs.csv / Diff_hash_groups.csv / Diff_subtrees.csv

用法：
  python .\Script\Script_DAISY_MAIN.py export-report --snapshot .\Output\Snapshots\Scan_x.sqlite
  python .\Script\Script_DAISY_MAIN.py export-report --diff .\Output\Diffs\Diff_x.sqlite
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sqlite3
import sys

_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
_LIB_DIR = os.path.join(os.path.dirname(_MODULE_DIR), "Lib")
sys.path.insert(0, _LIB_DIR)
import Script_DAISY_Lib_DBS_01_Core as core

_LIST_CAP = 50      # 摘要内明细列表上限（超出注明总数，绝不静默截断）


def _write_csv(path: str, header: list, rows: list) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)


def _write_report_info(folder: str) -> str:
    """写入不破坏业务 CSV 表头的独立报告身份页。"""
    name = "Report_info.csv"
    identity = core.report_metadata("DBS-41 结果报告导出")
    _write_csv(
        os.path.join(folder, name), ["key", "value"],
        list(identity.items()),
    )
    return name


def _dump_query(con: sqlite3.Connection, folder: str, name: str,
                sql: str) -> str:
    cur = con.execute(sql)
    header = [c[0] for c in cur.description]
    _write_csv(os.path.join(folder, name), header, cur.fetchall())
    return name


# === 快照导出 ===
_ENTRY_PAGES = [
    ("Exif_inventory_photo.csv", "photo_metadata"),
    ("Exif_inventory_video.csv", "video_metadata"),
    ("GPS_inventory_video.csv", "video_gps_points"),
    ("Exif_inventory_working.csv", "working_metadata"),
    ("Exif_inventory_document.csv", "document_metadata"),
    ("Stream_inventory_video.csv", "video_streams"),
    ("Stream_inventory_audio.csv", "audio_streams"),
    ("Hash_inventory.csv", "hashes"),
    ("Archive_inventory.csv", "archive_metadata"),
    ("Archive_inventory_members.csv", "archive_members"),
    ("Metadata_diagnostics.csv", "metadata_diagnostics"),
]


def _flatten(prefix: str, obj, out: list) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            _flatten(f"{prefix}.{k}" if prefix else str(k), v, out)
    else:
        out.append((prefix, obj))


def export_snapshot(snapshot_path: str, output_dir: str) -> dict:
    snapshot_path = os.path.abspath(snapshot_path)
    if not os.path.isfile(snapshot_path):
        raise core.PreflightError(f"快照不存在：{snapshot_path}")
    stem = os.path.basename(snapshot_path)
    stem = stem[:-len(".sqlite")] if stem.endswith(".sqlite") else stem
    folder = os.path.join(os.path.abspath(output_dir), stem + "_Report")
    os.makedirs(folder, exist_ok=True)
    con = sqlite3.connect(f"file:{snapshot_path}?mode=ro", uri=True)
    files = [_write_report_info(folder)]
    try:
        core.require_sealed_snapshot(con)
        # 人读路径一律拼接为「label\rel_path」完整逻辑路径
        files.append(_dump_query(
            con, folder, "Tree.csv",
            "SELECT r.root_label || '\\' || e.rel_path AS path,"
            " r.root_label, e.* FROM entries e"
            " JOIN roots r ON r.root_id = e.root_id"
            " ORDER BY r.root_label, e.rel_path"))
        files.append(_dump_query(
            con, folder, "Tree_dirs.csv",
            "SELECT CASE WHEN d.rel_path = '' THEN r.root_label"
            " ELSE r.root_label || '\\' || d.rel_path END AS path,"
            " r.root_label, d.* FROM dirs d"
            " JOIN roots r ON r.root_id = d.root_id"
            " ORDER BY r.root_label, d.rel_path"))
        for name, tbl in _ENTRY_PAGES:
            order = "r.root_label, e.rel_path"
            if tbl == "video_gps_points":
                order += ", t.timestamp_seconds, t.point_index"
            files.append(_dump_query(
                con, folder, name,
                f"SELECT r.root_label || '\\' || e.rel_path AS path,"
                f" r.root_label, e.rel_path, t.* FROM {tbl} t"
                " JOIN entries e ON e.entry_id = t.entry_id"
                " JOIN roots r ON r.root_id = e.root_id"
                f" ORDER BY {order}"))
        files.append(_dump_query(
            con, folder, "Errors.csv",
            "SELECT er.error_pk, er.stage, er.error_code, er.message,"
            " er.occurred_at_utc,"
            " CASE WHEN e.rel_path IS NULL THEN NULL"
            " ELSE r.root_label || '\\' || e.rel_path END AS path,"
            " r.root_label, e.rel_path, d.rel_path AS dir_rel_path"
            " FROM errors er"
            " LEFT JOIN entries e ON e.entry_id = er.entry_id"
            " LEFT JOIN roots r ON r.root_id = e.root_id"
            " LEFT JOIN dirs d ON d.dir_id = er.dir_id"
            " ORDER BY er.error_pk"))
        info_cur = con.execute("SELECT * FROM snapshot_info")
        info = dict(zip([c[0] for c in info_cur.description],
                        info_cur.fetchone()))
        kv = []
        for k, v in info.items():
            if k == "counts_json" and v:
                flat: list = []
                _flatten("counts", json.loads(v), flat)
                kv.extend(flat)
            else:
                kv.append((k, v))
        _write_csv(os.path.join(folder, "Summary.csv"), ["key", "value"], kv)
        files.append("Summary.csv")
    finally:
        con.close()
    return {"folder": folder, "files": files}


# === Diff 导出 ===
def _load_all(con, table):
    cur = con.execute(f"SELECT * FROM {table}")
    header = [c[0] for c in cur.description]
    return header, [dict(zip(header, r)) for r in cur.fetchall()]


def _md_escape(s) -> str:
    return str(s).replace("|", "\\|")


def export_diff(diff_path: str, output_dir: str) -> dict:
    diff_path = os.path.abspath(diff_path)
    if not os.path.isfile(diff_path):
        raise core.PreflightError(f"Diff 数据库不存在：{diff_path}")
    stem = os.path.basename(diff_path)
    for suf in (".diff.sqlite", ".sqlite"):
        if stem.endswith(suf):
            stem = stem[:-len(suf)]
            break
    folder = os.path.join(os.path.abspath(output_dir), stem + "_Report")
    os.makedirs(folder, exist_ok=True)
    con = sqlite3.connect(f"file:{diff_path}?mode=ro", uri=True)
    files = [_write_report_info(folder)]
    try:
        core.require_sqlite_integrity(con, "Diff 数据库")
        schema_version, = con.execute(
            "SELECT schema_version FROM diff_info").fetchone()
        core.require_readable_schema_version(
            schema_version, "Diff 数据库")
        _full = ("CASE WHEN {r} IS NULL THEN NULL WHEN {r} = ''"
                 " THEN {l} ELSE {l} || '\\' || {r} END")
        files.append(_dump_query(
            con, folder, "Diff_details.csv",
            "SELECT "
            + _full.format(l="old_root_label", r="old_rel_path")
            + " AS old_path, "
            + _full.format(l="new_root_label", r="new_rel_path")
            + " AS new_path, * FROM diff_entries ORDER BY status, path_key"))
        files.append(_dump_query(
            con, folder, "Diff_dirs.csv",
            "SELECT "
            + _full.format(l="old_root_label", r="old_rel_path")
            + " AS old_path, "
            + _full.format(l="new_root_label", r="new_rel_path")
            + " AS new_path, * FROM diff_dirs ORDER BY path_key"))
        files.append(_dump_query(con, folder, "Diff_hash_groups.csv",
                                 "SELECT * FROM diff_hash_groups"
                                 " ORDER BY group_id"))
        files.append(_dump_query(con, folder, "Diff_subtrees.csv",
                                 "SELECT * FROM diff_subtrees"
                                 " ORDER BY side, root_label, rel_path"))
        _, info_rows = _load_all(con, "diff_info")
        info = info_rows[0]
        _, entries = _load_all(con, "diff_entries")
        _, dirs = _load_all(con, "diff_dirs")
        _, groups = _load_all(con, "diff_hash_groups")
        _, subtrees = _load_all(con, "diff_subtrees")
    finally:
        con.close()

    se: dict = {}
    for r in entries:
        se.setdefault(r["status"], {}).setdefault(r["evidence"], 0)
        se[r["status"]][r["evidence"]] += 1
    n = lambda st: sum(se.get(st, {}).values())
    content_breaking = sum(n(s) for s in
                           ("content_changed", "added", "deleted", "copied"))
    # 维度隔离：hash_missing/unstable 的路径双侧都已配对存在，
    # 只削弱内容维度证据，不得传染到结构维度的存在性结论
    content_caveats = sum(n(s) for s in ("unknown", "hash_missing", "unstable"))
    structure_caveats = n("unknown")
    dir_changed = [d for d in dirs if d["status"] != "unchanged"]
    structure_breaking = (sum(n(s) for s in ("added", "deleted",
                                             "moved_or_renamed", "copied"))
                          + sum(1 for d in dirs
                                if d["status"] in ("added", "deleted")))
    same_evidence = {}
    for st in ("unchanged", "stat_changed_content_same",
               "metadata_extraction_changed"):
        for ev, c in se.get(st, {}).items():
            same_evidence[ev] = same_evidence.get(ev, 0) + c
    heur = sum(c for st, evs in se.items() for ev, c in evs.items()
               if ev == "heuristic_file_id")
    changed_groups = [g for g in groups if g["old_count"] != g["new_count"]]
    mapping = json.loads(info["root_mapping_json"])
    counts_json = json.loads(info["counts_json"]) if info["counts_json"] else {}

    def conclusion(breaking, kind, caveats, caveat_kinds):
        if breaking:
            return f"**不一致**（{breaking} 条{kind}差异）"
        if caveats:
            return (f"在可断言范围内一致；另有 {caveats} 条无法断言"
                    f"（{caveat_kinds}，见明细）")
        return "**一致**"

    lines = [
        "# Diff 摘要",
        "",
        *core.report_markdown_lines("DBS-41 结果报告导出"),
        "",
        f"- 旧快照：`{info['old_snapshot_file']}`（uuid `{info['old_snapshot_uuid']}`，"
        f"hash_coverage=**{info['old_hash_coverage']}**）",
        f"- 新快照：`{info['new_snapshot_file']}`（uuid `{info['new_snapshot_uuid']}`，"
        f"hash_coverage=**{info['new_hash_coverage']}**）",
        f"- 生成（UTC）：{info['created_at_utc']}；工具版本：{info['tool_version']}"
        + ("；**forced=1（不完整输入：文件名高32bit指纹缺失被越过）**"
           if info["forced"] else ""),
        f"- root 配对：{mapping['pairs']}"
        + ("（**单根自动配对**：label 不同，按挂载内容直接对比）"
           if mapping.get("auto_paired") else "")
        + (f"；未配对旧侧 {mapping['unpaired_old']}、新侧 {mapping['unpaired_new']}"
           f"（未配对 root 整体计入增删）"
           if mapping["unpaired_old"] or mapping["unpaired_new"] else ""),
        "",
        "## 双维度结论",
        "",
        f"- 内容维度（哈希多重集）：{conclusion(content_breaking, '内容', content_caveats, 'unknown/hash_missing/unstable')}",
        f"- 结构维度（路径树）：{conclusion(structure_breaking, '结构', structure_caveats, 'unknown——枚举失败或碰撞')}"
        + ("（hash_missing/unstable 条目双侧均已配对存在，"
           "不影响存在性结论）" if content_caveats > structure_caveats else ""),
        "",
        "## 状态 × evidence",
        "",
        "| status | evidence | 数量 |",
        "|---|---|---:|",
    ]
    for st in sorted(se):
        for ev in sorted(se[st]):
            lines.append(f"| {st} | {ev} | {se[st][ev]:,} |")
    lines.append("")
    if same_evidence:
        lines.append("「内容相同」证据分布："
                     + "；".join(f"{ev}={c:,}"
                                 for ev, c in sorted(same_evidence.items()))
                     + "。")
        if same_evidence.get("propagated_single_computation"):
            lines.append("注意：propagated_single_computation 表示两侧追溯到"
                         "**同一次计算事件**（哈希被抄录传递），"
                         "**不构成独立验证**，不得表述为「已验证一致」。")
        lines.append("")
    if subtrees:
        lines.append("## ⚠ 枚举失败子树（其下差异一律 unknown，绝不判增删）")
        lines.append("")
        for s in subtrees:
            rel = s["rel_path"] if s["rel_path"] else "<root 级失败>"
            lines.append(f"- [{s['side']}] `{_md_escape(s['root_label'])}"
                         f"\\{_md_escape(rel)}`（{s['enum_status']}，"
                         f"另一侧受影响约 {s['affected_estimate']} 条）")
        lines.append("")
    lines.append("## 目录维度")
    lines.append("")
    dir_counts: dict = {}
    for d in dirs:
        dir_counts[d["status"]] = dir_counts.get(d["status"], 0) + 1
    lines.append("，".join(f"{k}={v:,}" for k, v in sorted(dir_counts.items()))
                 or "（无目录记录）")
    for d in dir_changed[:_LIST_CAP]:
        lbl = (d["old_root_label"] if d["old_rel_path"] is not None
               else d["new_root_label"]) or ""
        rel = (d["old_rel_path"] if d["old_rel_path"] is not None
               else d["new_rel_path"])
        lines.append(f"- {d['status']}: "
                     f"`{_md_escape(lbl)}\\{_md_escape(rel)}`"
                     + (f"（{d['reason']}）" if d["reason"] else ""))
    if len(dir_changed) > _LIST_CAP:
        lines.append(f"- …共 {len(dir_changed)} 条目录级变更，"
                     "详见 Diff_dirs.csv")
    lines.append("")
    lines.append("## 重复内容组变化")
    lines.append("")
    if changed_groups:
        for g in changed_groups[:_LIST_CAP]:
            lines.append(f"- `{g['hash_hex'][:16]}…`：{g['old_count']} → "
                         f"{g['new_count']}"
                         + (f"（新侧硬链接组 {g['new_hardlink_sets']}）"
                            if g["new_hardlink_sets"] else ""))
        if len(changed_groups) > _LIST_CAP:
            lines.append(f"- …共 {len(changed_groups)} 组，"
                         "详见 Diff_hash_groups.csv")
    else:
        lines.append("（无副本数量变化）")
    lines.append("")
    lines.append("## 启发式结论（单列，不与哈希确证混计）")
    lines.append("")
    lines.append(f"file_id 启发移动判定：{heur:,} 条"
                 "（依据 NTFS 文件标识＋size＋mtime，非内容证据）。")
    lines.append("")
    lines.append("## 覆盖声明")
    lines.append("")
    lines.append(f"- 双侧 hash_coverage：旧={info['old_hash_coverage']}，"
                 f"新={info['new_hash_coverage']}（hash_coverage=none 侧"
                 "缺少哈希时只得出 hash_missing，不构成校验失败）")
    pr = counts_json.get("payload_rows", {})
    lines.append(f"- raw payload 行数：旧={pr.get('old')}，新={pr.get('new')}"
                 "（任一侧缺失的条目 metadata_changed 未评估）")
    lines.append("")
    with open(os.path.join(folder, "Diff_summary.md"), "w",
              encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))
    files.append("Diff_summary.md")
    return {"folder": folder, "files": files}


def main() -> int:
    core.force_utf8_io()
    ap = argparse.ArgumentParser(
        description="DBS-41 结果报告导出（只读输入）")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--snapshot", help="封存快照 .sqlite")
    g.add_argument("--diff", help="Diff 数据库 .sqlite")
    ap.add_argument("--output-dir", default="Output/Reports")
    args = ap.parse_args()
    try:
        if args.snapshot:
            res = export_snapshot(args.snapshot, args.output_dir)
        else:
            res = export_diff(args.diff, args.output_dir)
    except core.PreflightError as exc:
        print(f"导出失败：{exc}", file=sys.stderr)
        return 2
    print(f"导出目录:{res['folder']}")
    for f in res["files"]:
        print(f"  {f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
