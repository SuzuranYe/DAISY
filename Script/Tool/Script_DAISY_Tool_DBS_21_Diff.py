r"""Script_DAISY_Tool_DBS_21_Diff：快照变更分析。

语义说明：Spec/Spec_DAISY_Technical.md。摘要／CSV 导出由 `export-report`
子命令从 Diff 数据库生成；本脚本输出控制台摘要。

用法：
  python .\Script\Script_DAISY_MAIN.py diff --old .\\Output\Snapshots\\Scan_A.sqlite ^
      --new .\\Output\Snapshots\\Scan_B.sqlite [--output-dir .\\Diffs]
      [--map-root 旧label=新label] [--force]
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys

_TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
_LIB_DIR = os.path.join(os.path.dirname(_TOOL_DIR), "Lib")
sys.path.insert(0, _LIB_DIR)
import Script_DAISY_Lib_01_Core as core
import Script_DAISY_Lib_04_Diff as dbdiff


def _status_count(counts: dict, status: str) -> int:
    return sum((counts.get("status_evidence") or {}).get(status, {}).values())


def _has_diff_issues(result: dict) -> bool:
    """只把降级准入、unstable、unknown 或枚举失败视为问题。"""
    return bool(
        result.get("forced") or result.get("subtrees")
        or _status_count(result["counts"], "unstable")
        or _status_count(result["counts"], "unknown"))


def _append_table(lines: list[str], headers: tuple[str, ...], rows: list[tuple]):
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join("---" for _ in headers) + " |")
    for row in rows:
        lines.append("| " + " | ".join(core.markdown_cell(v) for v in row) + " |")


def _render_diff_issue_report(db_path: str, artifact_filename: str,
                              result: dict, row_limit: int = 500) -> str:
    counts = result["counts"]
    con = sqlite3.connect(f"file:{os.path.abspath(db_path)}?mode=ro", uri=True)
    try:
        problem_total, = con.execute(
            "SELECT COUNT(*) FROM diff_entries"
            " WHERE status IN ('unstable','unknown')").fetchone()
        problem_rows = con.execute(
            "SELECT status,evidence,COALESCE(new_root_label,old_root_label,''),"
            " COALESCE(new_rel_path,old_rel_path,''),reason"
            " FROM diff_entries WHERE status IN ('unstable','unknown')"
            " ORDER BY status,path_key LIMIT ?", (row_limit,)).fetchall()
    finally:
        con.close()
    summary_rows = [
        ("降级准入", 1 if result.get("forced") else 0),
        ("unstable 条目", _status_count(counts, "unstable")),
        ("unknown 条目", _status_count(counts, "unknown")),
        ("旧侧枚举失败子树", counts.get("subtrees", {}).get("old", 0)),
        ("新侧枚举失败子树", counts.get("subtrees", {}).get("new", 0)),
    ]
    lines = [
        "# DAISY Diff 问题报告",
        "",
        f"- 数据库：`{artifact_filename}`",
        f"- 报告生成时间：`{core.now_utc_iso()}`",
        f"- 哈希覆盖：旧=`{result['coverage'][0]}`，新=`{result['coverage'][1]}`",
        "- 结论：本次 Diff 存在降级证据或无法可靠判定的条目。",
        "- 命名：问题状态不写入数据库文件名。",
        "",
        "## 汇总",
        "",
    ]
    _append_table(lines, ("项目", "数量"), summary_rows)
    if result.get("subtrees"):
        lines.extend(["", "## 枚举失败子树", ""])
        _append_table(lines, ("侧", "根标签", "相对路径", "状态", "受影响估计"), [
            (row["side"], row["root_label"], row["rel_path"] or "<root>",
             row["enum_status"], row["affected_estimate"])
            for row in result["subtrees"]
        ])
    if problem_rows:
        lines.extend(["", "## 问题条目", ""])
        _append_table(lines, ("状态", "证据", "根标签", "相对路径", "原因"),
                      problem_rows)
        if problem_total > len(problem_rows):
            lines.append(f"\n仅列出前 {len(problem_rows)}／{problem_total} 条。")
    lines.extend([
        "",
        "## 说明",
        "",
        "完整结果仍以 Diff SQLite 内的 `diff_entries` 与 `diff_subtrees` 为准。",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    core.force_utf8_io()
    ap = argparse.ArgumentParser(
        description="DBS-21 快照变更分析（只读双输入，独立输出）")
    ap.add_argument("--old", required=True, help="旧（基准）封存快照 .sqlite")
    ap.add_argument("--new", required=True, help="新封存快照 .sqlite")
    ap.add_argument("--output-dir", default="Output/Diffs")
    ap.add_argument("--map-root", action="append", default=[],
                    help="root 配对改写：旧label=新label，可重复")
    ap.add_argument("--force", action="store_true",
                    help="允许文件名高32bit指纹缺失（不符仍拒绝）")
    args = ap.parse_args()

    map_root = {}
    for spec in args.map_root:
        old_lb, sep, new_lb = spec.partition("=")
        if not sep or not old_lb or not new_lb:
            print(f"--map-root 语法应为 旧label=新label：{spec}", file=sys.stderr)
            return 2
        map_root[old_lb] = new_lb

    os.makedirs(args.output_dir, exist_ok=True)
    # 先验证证据链；预期哈希缺失且未 --force 时不打开快照读取 label
    if (core.filename_sha256_high32(os.path.abspath(args.new)) is None
            and not args.force):
        print("对比失败：新侧快照文件名缺少高32bit指纹"
              "（确认输入后可用 --force 降级准入）", file=sys.stderr)
        return 2
    # Diff 库名带根文件夹名：取新侧快照的 root label（配对目标侧）
    try:
        _c = sqlite3.connect(f"file:{os.path.abspath(args.new)}?mode=ro",
                             uri=True)
        labels = [r[0] for r in _c.execute(
            "SELECT root_label FROM roots ORDER BY root_label")]
        _c.close()
    except sqlite3.Error:
        labels = []                    # 不可读时回退；准入校验会给出正式报错
    name = core.snapshot_name(labels or ["Unknown"], "Diff")
    publish_stem = os.path.abspath(os.path.join(args.output_dir, name))
    working_name = core.snapshot_working_name(name)
    out = os.path.abspath(os.path.join(
        args.output_dir, working_name + ".partial.sqlite"))
    try:
        res = dbdiff.compare(os.path.abspath(args.old),
                             os.path.abspath(args.new), out,
                             map_root=map_root, force=args.force)
    except core.PreflightError as exc:
        print(f"对比失败：{exc}", file=sys.stderr)
        return 2

    # Diff 数据库同样把 SHA-256 前 8 个十六进制字符大写后置。
    digest = core.sha256_file(out)
    hashed = publish_stem + f"_{digest[:8].upper()}.sqlite"
    issue_markdown = (_render_diff_issue_report(
        out, os.path.basename(hashed), res) if _has_diff_issues(res) else None)
    try:
        issue_report = core.publish_sqlite_artifact(
            out, hashed, issue_markdown)
    except core.PreflightError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    out = hashed
    counts = res["counts"]
    print(f"Diff 数据库：{out}")
    if issue_report:
        print(f"问题报告：{issue_report}")
    print(f"hash_coverage：旧={res['coverage'][0]} 新={res['coverage'][1]}"
          + ("｜forced=1（不完整输入）" if res["forced"] else ""))
    print("状态 × evidence：")
    for status in ("unchanged", "stat_changed_content_same",
                   "metadata_extraction_changed", "content_changed", "added",
                   "deleted", "moved_or_renamed", "copied", "hash_missing",
                   "unstable", "unknown"):
        ev = counts["status_evidence"].get(status)
        if not ev:
            continue
        detail = "，".join(f"{k}={v}" for k, v in sorted(ev.items()))
        print(f"  {status}: {sum(ev.values()):,}（{detail}）")
    dirs = counts["dirs_status"]
    print("目录维度：" + "，".join(f"{k}={v}" for k, v in sorted(dirs.items())))
    if res["subtrees"]:
        print("!! 枚举失败子树（其下差异均记 unknown，绝不判增删）：",
              file=sys.stderr)
        for s in res["subtrees"]:
            rel = s["rel_path"] or "<root>"
            print(f"   [{s['side']}] {s['root_label']}\\{rel}"
                  f"（{s['enum_status']}，另一侧受影响约"
                  f" {s['affected_estimate']} 条）", file=sys.stderr)
    rm = res["root_mapping"]
    if rm.get("auto_paired"):
        old_lb, new_lb = rm["auto_paired"]
        print(f"单根自动配对：{old_lb} ↔ {new_lb}"
              "（label 不同，按挂载内容直接对比）")
    if rm["unpaired_old"] or rm["unpaired_new"]:
        print(f"!! 未配对 root：旧侧 {rm['unpaired_old']}"
              f" 新侧 {rm['unpaired_new']}（整体计入增删）", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
