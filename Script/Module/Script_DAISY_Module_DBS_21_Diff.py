r"""Script_DAISY_Module_DBS_21_Diff：DBS-21 档案快照对比。

语义说明：Spec/Spec_DAISY_Technical.md。摘要和 CSV 导出由 `export-report`
子命令从 Diff 数据库生成；本脚本输出控制台摘要。

用法：
  python .\Script\Script_DAISY_MAIN.py diff --old OLD.sqlite --new NEW.sqlite
  可选参数：--output-dir、--map-root、--force；详见 --help。
"""
from __future__ import annotations

import argparse
import os
import sys

_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
_LIB_DIR = os.path.join(os.path.dirname(_MODULE_DIR), "Lib")
sys.path.insert(0, _LIB_DIR)
import Script_DAISY_Lib_DBS_01_Core as core
import Script_DAISY_Lib_DBS_04_Diff as dbdiff
import Script_DAISY_Lib_DBS_05_Reader as dbreader


_STATUS_LABELS = {
    "unchanged": "未变化",
    "stat_changed_content_same": "属性变化、内容相同",
    "metadata_extraction_changed": "元数据提取变化",
    "content_changed": "内容变化",
    "added": "新增",
    "deleted": "删除",
    "moved_or_renamed": "移动或重命名",
    "copied": "复制",
    "hash_missing": "缺少哈希",
    "unstable": "扫描期间发生变化",
    "unknown": "无法判定",
    "pending": "等待处理",
    "ok": "正常",
    "failed": "失败",
    "access_denied": "访问被拒绝",
    "io_error": "读取错误",
    "skipped_reparse": "已跳过重解析点",
    "skipped_excluded": "已按规则排除",
    "timeout": "超时",
    "not_enumerated": "未枚举",
}
_EVIDENCE_LABELS = {
    "independent_computation": "独立计算",
    "propagated_single_computation": "同次计算沿用",
    "heuristic_file_id": "NTFS-ID 启发式",
    "stat_only": "仅文件属性",
    "insufficient": "证据不足",
}
_CAPABILITY_LABELS = {
    "available": "有记录",
    "empty": "0 条记录",
    "unavailable": "未记录",
    "incompatible": "版本不兼容",
    "invalid": "结构异常",
}
_COVERAGE_LABELS = {"full": "完整", "partial": "部分", "none": "无"}


def _display_code(value: object, labels: dict[str, str]) -> str:
    text = str(value or "")
    return labels.get(text, text or "未知")


def _capability_reason(value: object) -> str:
    """将 Reader 的双侧技术状态转换为面向用户的说明。"""
    return (
        str(value or "")
        .replace("old=available", "基准侧：有记录")
        .replace("old=empty", "基准侧：0 条记录")
        .replace("old=unavailable", "基准侧：未记录")
        .replace("new=available", "对比侧：有记录")
        .replace("new=empty", "对比侧：0 条记录")
        .replace("new=unavailable", "对比侧：未记录")
    )


def _status_count(counts: dict, status: str) -> int:
    return sum((counts.get("status_evidence") or {}).get(status, {}).values())


def _has_diff_issues(result: dict) -> bool:
    """只把指纹缺失准入、unstable、unknown 或枚举失败视为问题。"""
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
    con, descriptor = dbreader.open_database(
        db_path, expected_type="diff", require_sealed=False,
        verify_integrity=False)
    try:
        # 工作库文件名仍为 partial；结构已由 compare() 完整创建。
        if descriptor.lifecycle == "sealed":
            dbreader.require_capabilities(descriptor, "file_changes")
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
        ("指纹降级", "是" if result.get("forced") else "否"),
        ("扫描期间发生变化的条目", _status_count(counts, "unstable")),
        ("无法判定条目", _status_count(counts, "unknown")),
        ("基准侧枚举失败子树", counts.get("subtrees", {}).get("old", 0)),
        ("对比侧枚举失败子树", counts.get("subtrees", {}).get("new", 0)),
    ]
    lines = [
        "# DAISY 档案快照对比问题报告",
        "",
        *core.report_markdown_lines("档案快照对比"),
        "",
        f"- Diff 数据库：`{artifact_filename}`",
        f"- 报告生成时间 (UTC)：`{core.now_utc_iso()}`",
        "- 哈希覆盖："
        f"基准侧为 {_display_code(result['coverage'][0], _COVERAGE_LABELS)}；"
        f"对比侧为 {_display_code(result['coverage'][1], _COVERAGE_LABELS)}",
        "- 结论：本次对比存在证据受限或无法可靠判定的条目。",
        "",
        "## 汇总",
        "",
    ]
    _append_table(lines, ("项目", "结果"), summary_rows)
    if result.get("subtrees"):
        lines.extend(["", "## 枚举失败子树", ""])
        _append_table(lines, ("侧", "根目录名", "相对路径", "状态", "预计受影响条目"), [
            ({"old": "基准", "new": "对比"}.get(row["side"], row["side"]),
             row["root_label"], row["rel_path"] or "（根目录）",
             _display_code(row["enum_status"], _STATUS_LABELS),
             row["affected_estimate"])
            for row in result["subtrees"]
        ])
    if problem_rows:
        lines.extend(["", "## 问题条目", ""])
        _append_table(
            lines, ("状态", "证据", "根目录名", "相对路径", "原因"),
            [
                (
                    _display_code(row[0], _STATUS_LABELS),
                    _display_code(row[1], _EVIDENCE_LABELS),
                    *row[2:],
                )
                for row in problem_rows
            ],
        )
        if problem_total > len(problem_rows):
            lines.append(f"\n仅列出前 {len(problem_rows)}/{problem_total} 条。")
    lines.extend([
        "",
        "## 说明",
        "",
        "完整结果仍以 Diff 数据库的 `diff_entries` 与 `diff_subtrees` 表为准。",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    core.force_utf8_io()
    ap = argparse.ArgumentParser(
        description="档案快照对比：只读比较两份封存快照并生成 Diff 数据库")
    ap.add_argument("--old", required=True, help="基准封存快照 (.sqlite)")
    ap.add_argument("--new", required=True, help="对比封存快照 (.sqlite)")
    ap.add_argument(
        "--output-dir", default="Output/Diffs",
        help="Diff 数据库输出目录；默认 Output/Diffs")
    ap.add_argument("--map-root", action="append", default=[],
                    help="根目录名对应；格式为「基准根目录名=对比根目录名」，可重复")
    ap.add_argument("--force", action="store_true",
                    help="允许缺少文件名指纹；指纹不一致仍拒绝")
    args = ap.parse_args()

    map_root = {}
    for spec in args.map_root:
        old_lb, sep, new_lb = spec.partition("=")
        if not sep or not old_lb or not new_lb:
            print(
                f"--map-root 应为「基准根目录名=对比根目录名」：{spec}",
                file=sys.stderr,
            )
            return 2
        map_root[old_lb] = new_lb

    os.makedirs(args.output_dir, exist_ok=True)
    # 先验证证据链；预期哈希缺失且未 --force 时不打开快照读取 label
    if (core.filename_sha256_high32(os.path.abspath(args.new)) is None
            and not args.force):
        print("对比失败：对比快照文件名缺少指纹"
              "（确认输入后可用 --force 明确允许继续）", file=sys.stderr)
        return 2
    # Diff 库名带根文件夹名：取新侧快照的 root label（配对目标侧）
    try:
        _c, descriptor = dbreader.open_database(
            args.new, expected_type="snapshot")
        try:
            labels = list(dbreader.snapshot_root_labels(_c, descriptor))
        finally:
            _c.close()
    except core.PreflightError:
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
    print(
        "哈希覆盖："
        f"基准侧为 {_display_code(res['coverage'][0], _COVERAGE_LABELS)}；"
        f"对比侧为 {_display_code(res['coverage'][1], _COVERAGE_LABELS)}"
        + ("｜已允许文件名指纹缺失" if res["forced"] else ""))
    schemas = counts.get("snapshot_schemas") or {}
    projection = counts.get("projection") or {}
    print(
        f"数据库结构版本：基准侧为 {schemas.get('old')}；对比侧为 {schemas.get('new')}"
        f"｜规范化投影版本：基准侧为 {projection.get('old')}；"
        f"对比侧为 {projection.get('new')}"
    )
    print("证据可用性：")
    for capability_id, title in (
        ("hashes", "哈希"),
        ("raw_payloads", "工具原始输出"),
        ("format_checks", "格式校验"),
    ):
        capability = res["capabilities"].get(capability_id) or {}
        state = {
            "comparable": "可比",
            "empty": "双方无记录",
            "unavailable": "不可比",
        }.get(str(capability.get("state")), "未知")
        detail = (
            f"；{_capability_reason(capability['reason'])}"
            if capability.get("reason") else "")
        print(
            f"  {title}：{state}"
            f"（基准侧：{_display_code(capability.get('old'), _CAPABILITY_LABELS)}；"
            f"对比侧：{_display_code(capability.get('new'), _CAPABILITY_LABELS)}）"
            f"{detail}"
        )
    print("状态 × 证据：")
    for status in ("unchanged", "stat_changed_content_same",
                   "metadata_extraction_changed", "content_changed", "added",
                   "deleted", "moved_or_renamed", "copied", "hash_missing",
                   "unstable", "unknown"):
        ev = counts["status_evidence"].get(status)
        if not ev:
            continue
        detail = "，".join(
            f"{_display_code(k, _EVIDENCE_LABELS)}：{v}"
            for k, v in sorted(ev.items()))
        print(
            f"  {_display_code(status, _STATUS_LABELS)}：{sum(ev.values()):,}"
            f"（{detail}）")
    dirs = counts["dirs_status"]
    print("目录维度：" + "，".join(
        f"{_display_code(k, _STATUS_LABELS)}：{v}"
        for k, v in sorted(dirs.items())))
    if res["subtrees"]:
        print("警告：枚举失败子树（其下差异均为无法判定，不判定增删）：",
              file=sys.stderr)
        for s in res["subtrees"]:
            rel = s["rel_path"] or "（根目录）"
            side = {"old": "基准", "new": "对比"}.get(
                s["side"], s["side"])
            status = _display_code(s["enum_status"], _STATUS_LABELS)
            print(f"   [{side}] {s['root_label']}\\{rel}"
                  f"（{status}，另一侧受影响约"
                  f" {s['affected_estimate']} 条）", file=sys.stderr)
    rm = res["root_mapping"]
    if rm.get("auto_paired"):
        old_lb, new_lb = rm["auto_paired"]
        print(f"单根自动配对：{old_lb} ↔ {new_lb}"
              "（根目录名不同，按挂载内容直接对比）")
    if rm["unpaired_old"] or rm["unpaired_new"]:
        old_names = "、".join(str(item) for item in rm["unpaired_old"]) or "无"
        new_names = "、".join(str(item) for item in rm["unpaired_new"]) or "无"
        print(f"警告：未配对根目录：基准侧 {old_names}；"
              f"对比侧 {new_names}（整体计入增删）", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
