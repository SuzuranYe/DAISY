r"""Script_DAISY_Tool_DBS_12_Quick_Scan：快速档案扫描——只登记文件信息的轻量快照。

与 DBS-11 完整档案扫描采用同一快照格式（同 DDL、内嵌事件与清单），但**完全
不接触外部工具**（无 ExifTool/ffprobe/7-Zip 依赖，未安装也能跑）、不哈希、
不提取元数据：只登记文件树与文件信息（名称/扩展名/类型、大小、创建与修改
时间 UTC 100ns、属性、NTFS 文件标识、云占位检测、逐目录枚举状态）。

管线四阶段：轻预检（输出目录＋磁盘空间）→ 枚举 → 复扫校验 → 封存。
分钟级完成，无续传（中断后直接重跑）。产物 hash_coverage='none'、媒体条目
meta_status='skipped'，可直接被 Diff、核验与报表工具消费
（Diff 对无哈希侧如实给 hash_missing——快扫只能发现树/大小/时间层面的变化，
既不验证内容也不检查媒体可读性）。

用法：
  python .\Script\Script_DAISY_MAIN.py quick-scan --root "label=D:\档案" --output-dir .\Snapshots
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys

_TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
_LIB_DIR = os.path.join(os.path.dirname(_TOOL_DIR), "Lib")
sys.path.insert(0, _LIB_DIR)
import Script_DAISY_Lib_01_Core as core

STAGES_TOTAL = 4
MIN_FREE_BYTES = 200 * 1024 * 1024


def main() -> int:
    core.force_utf8_io()
    ap = argparse.ArgumentParser(description="快速档案扫描：仅文件信息的轻量快照")
    ap.add_argument("--root", action="append", default=[], required=True,
                    help="档案根文件夹，可重复；语法 label=路径 或 路径")
    ap.add_argument("--output-dir", default="Output/Snapshots")
    ap.add_argument("--no-file-id", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    # [1/4] 轻预检：不需要任何外部工具
    prog = core.Progress(1, STAGES_TOTAL, "预检", args.quiet)
    try:
        roots = []
        for spec in args.root:
            label, path = core.parse_root_spec(spec)
            core.validate_root(path)
            roots.append((label, path))
        os.makedirs(args.output_dir, exist_ok=True)
        free = shutil.disk_usage(args.output_dir).free
        if free < MIN_FREE_BYTES:
            raise core.PreflightError(
                f"输出目录剩余空间不足：{free/1e6:.0f} MB")
    except core.PreflightError as exc:
        print(f"\n预检失败：{exc}", file=sys.stderr)
        return 2
    prog.finish("输出目录与磁盘空间通过（快扫无工具依赖）")

    config = {"phase": "quick", "quick": True, "hash": "none",
              "no_file_id": args.no_file_id,
              "path_key_rule": core.PATH_KEY_RULE,
              "filename_layout_version": core.FILENAME_LAYOUT_VERSION}
    try:
        # 根文件夹名_Quick_日期时间：与 11 的 _Full_ 在文件名上可辨
        profile_tokens = core.snapshot_profile_tokens(
            "quick", hash_mode="none", raw_payload=False,
            file_id=not args.no_file_id)
        name = core.snapshot_name(
            [lb for lb, _ in roots], "Quick", profile_tokens)
        config["snapshot_stem"] = name
        publish_stem_path = os.path.abspath(
            os.path.join(args.output_dir, name))
        working_name = core.snapshot_working_name(name)
        partial = os.path.abspath(os.path.join(
            args.output_dir, working_name + ".partial.sqlite"))
        con = core.create_partial_snapshot(partial, roots, config,
                                           tool_versions={})
    except core.PreflightError as exc:
        print(f"启动失败：{exc}", file=sys.stderr)
        return 2

    base = partial[:-len(".partial.sqlite")]
    events = core.EventLog(base + ".events.jsonl")
    events.emit("run_started", resumed=False, quick=True,
                partial=os.path.basename(partial),
                scanner_version=core.SCANNER_VERSION, config=config)
    try:
        # [2/4] 枚举（可重跑对账）
        prog = core.Progress(2, STAGES_TOTAL, "枚举", args.quiet)
        events.emit("stage_started", stage="enumerate")
        exclude = {partial, partial + "-wal", partial + "-shm",
                   base + ".events.jsonl"}
        stats = core.enumerate_and_reconcile(
            con, collect_file_id=not args.no_file_id, exclude_paths=exclude,
            exclude_dirs={os.path.abspath(args.output_dir)},
            on_progress=lambda s: prog.update(s["files"], bytes_done=s["bytes"],
                                              errors=s["dir_errors"]))
        prog.finish(f"{stats['files']:,} 文件 / {stats['dirs']:,} 目录 / "
                    f"{stats['bytes']/1e9:.2f} GB / 目录错误 {stats['dir_errors']}")
        events.emit("stage_finished", stage="enumerate", **stats)

        # 状态快进：快扫不做哈希与元数据（语义与 --hash none/跳过一致）
        con.execute("UPDATE entries SET meta_status='not_applicable'"
                    " WHERE meta_status='pending' AND media_kind='other'")
        con.execute("UPDATE entries SET meta_status='skipped'"
                    " WHERE meta_status='pending'")
        con.execute("UPDATE entries SET hash_status='skipped'"
                    " WHERE hash_status='pending'")
        con.commit()
        events.emit("stage_skipped", stage="hash", reason="quick_scan")
        events.emit("stage_skipped", stage="metadata", reason="quick_scan")

        # [3/4] 复扫校验
        prog = core.Progress(3, STAGES_TOTAL, "复扫校验", args.quiet)
        events.emit("stage_started", stage="rescan")
        changed = core.rescan_check(con)
        prog.finish(f"unstable {changed}")
        events.emit("stage_finished", stage="rescan", unstable=changed)

        # [4/4] 封存
        prog = core.Progress(4, STAGES_TOTAL, "封存", args.quiet)
        events.emit("stage_started", stage="finalize")
        manifest = {
            "scanner_version": core.SCANNER_VERSION,
            "tools": {},
            "config": config,
        }
        final = core.finalize_snapshot(
            con, partial, hash_coverage="none",
            publish_stem_path=publish_stem_path,
            manifest=manifest, event_log_path=base + ".events.jsonl")
        prog.finish(os.path.basename(final))
        events.close()
        try:
            os.remove(base + ".events.jsonl")
        except OSError as exc:
            print(f"!! 已封存，但无法删除已内置的临时事件日志：{exc}",
                  file=sys.stderr)
        print(f"\n快照：{final}"
              "\nSHA-256 高 32 bit：已大写后置于文件名"
              "\nmanifest 与事件日志：已内置于 SQLite")
        issue_report = core.artifact_issue_report_path(final)
        if os.path.isfile(issue_report):
            print("!! SQLite 数据库已完整封存；另发现扫描证据问题"
                  f"（枚举失败/unstable），问题报告：{issue_report}",
                  file=sys.stderr)
        return 0

    except KeyboardInterrupt:
        con.commit()
        con.close()
        events.emit("run_interrupted", partial=os.path.basename(partial))
        events.close()
        core.release_scan_lock(partial)    # 本进程退出即让出所有权
        print("\n已中断。快扫为分钟级、无续传：直接重跑生成新快照即可"
              f"（残留 partial 可删：{partial}）", file=sys.stderr)
        return 130
    except core.PreflightError as exc:
        events.emit("run_failed", error=str(exc))
        events.close()
        core.release_scan_lock(partial)
        print(f"\n失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
