r"""Script_DAISY_Tool_31_Diff：对比两个封存快照，产出 Diff 数据库（权威结果）。

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
import sys

_TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
_LIB_DIR = os.path.join(os.path.dirname(_TOOL_DIR), "Lib")
sys.path.insert(0, _LIB_DIR)
import Script_DAISY_Lib_01_Core as core
import Script_DAISY_Lib_04_Diff as dbdiff


def main() -> int:
    core.force_utf8_io()
    ap = argparse.ArgumentParser(description="快照对比（只读双输入，独立输出）")
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
        import sqlite3
        _c = sqlite3.connect(f"file:{os.path.abspath(args.new)}?mode=ro",
                             uri=True)
        labels = [r[0] for r in _c.execute(
            "SELECT root_label FROM roots ORDER BY root_label")]
        _c.close()
    except sqlite3.Error:
        labels = []                    # 不可读时回退；准入校验会给出正式报错
    name = core.snapshot_name(labels or ["Unknown"], "Diff")
    out = os.path.abspath(os.path.join(args.output_dir, name + ".sqlite"))
    try:
        res = dbdiff.compare(os.path.abspath(args.old),
                             os.path.abspath(args.new), out,
                             map_root=map_root, force=args.force)
    except core.PreflightError as exc:
        print(f"对比失败：{exc}", file=sys.stderr)
        return 2

    if res["forced"]:                # 降级准入（文件名指纹缺失被越过）→ 后缀标记
        flagged = out[:-len(".sqlite")] + "_Abnormal.sqlite"
        try:
            os.rename(out, flagged)  # 目标存在即失败，绝不覆盖
        except FileExistsError:
            print(f"发布冲突：{flagged} 已存在，本次结果保留于 {out}",
                  file=sys.stderr)
            return 1
        out = flagged
    # Diff 数据库同样把 SHA-256 前 8 个十六进制字符大写后置。
    digest = core.sha256_file(out)
    hashed = out[:-len(".sqlite")] + f"_{digest[:8].upper()}.sqlite"
    try:
        os.rename(out, hashed)
    except FileExistsError:
        print(f"发布冲突：{hashed} 已存在，本次结果保留于 {out}",
              file=sys.stderr)
        return 1
    out = hashed
    counts = res["counts"]
    print(f"Diff 数据库：{out}")
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
