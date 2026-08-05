r"""Script_DAISY_Module_DBS_11_Full_Scan：DBS-11 完整档案扫描——扫描档案生成不可变快照库（树＋元数据＋哈希）。

六阶段管线：预检→枚举（可重跑对账）→哈希（full/incremental/none，逐文件续传）
→元数据（双后端，逐文件续传）→复扫＋独立实现抽验→封存。

用法：
  python .\Script\Script_DAISY_MAIN.py full-scan --root "label=D:\\档案" --hash full
  python .\Script\Script_DAISY_MAIN.py full-scan --root "label=D:\\档案" --hash incremental ^
      --previous-snapshot .\\Output\Snapshots\\Scan_xxx.sqlite
  python .\Script\Script_DAISY_MAIN.py full-scan --resume .\\Output\Snapshots\\Scan_xxx.partial.sqlite
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys

_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
_LIB_DIR = os.path.join(os.path.dirname(_MODULE_DIR), "Lib")
sys.path.insert(0, _LIB_DIR)
import Script_DAISY_Lib_DBS_01_Core as core
import Script_DAISY_Lib_DBS_03_Hash as dbh
import Script_DAISY_Lib_DBS_02_Meta as meta

STAGES_TOTAL = 6


def open_resume(partial: str) -> tuple[sqlite3.Connection, list]:
    if not os.path.isfile(partial) or not partial.endswith(".partial.sqlite"):
        raise core.PreflightError(f"--resume 需要指向 .partial.sqlite：{partial}")
    # 续传须先取得所有权：owner 存活则拒绝，已终止才可接管
    core.acquire_scan_lock(partial, takeover=True)
    con = None
    try:
        con = sqlite3.connect(partial)
        con.execute("PRAGMA foreign_keys=ON")
        status, scanner_version, schema_version, config_text = con.execute(
            "SELECT scan_status,scanner_version,schema_version,config_json"
            " FROM snapshot_info").fetchone()
        if status != "running":
            raise core.PreflightError(
                f"该 partial 状态为 {status}，不可续传")
        try:
            profile_version = json.loads(config_text).get("profile_version")
        except (AttributeError, TypeError, ValueError) as exc:
            raise core.PreflightError(
                "partial 的 config_json 无法解析，禁止续传") from exc
        has_gps_table = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table'"
            " AND name='video_gps_points'").fetchone() is not None
        if (scanner_version != core.SCANNER_VERSION
                or schema_version != core.SCHEMA_VERSION
                or profile_version != meta.PROFILE_VERSION
                or not has_gps_table):
            raise core.PreflightError(
                "partial 与当前解析语义不一致，禁止跨版本或 profile 续传："
                f"partial=v{scanner_version}/schema {schema_version}/"
                f"profile {profile_version}/GPS表 {int(has_gps_table)}，"
                f"当前=v{core.SCANNER_VERSION}/schema {core.SCHEMA_VERSION}/"
                f"profile {meta.PROFILE_VERSION}/GPS表 1。"
                "请用创建它的 DAISY 版本续传，或重新开始扫描")
        roots = con.execute(
            "SELECT root_label, root_path FROM roots ORDER BY root_id").fetchall()
        for _, p in roots:
            core.validate_root(p)
        return con, roots
    except Exception:
        if con is not None:
            con.close()
        core.release_scan_lock(partial)
        raise


def main() -> int:
    core.force_utf8_io()
    ap = argparse.ArgumentParser(
        description="DBS-11 完整档案扫描：档案 → 不可变快照库")
    ap.add_argument("--root", action="append", default=[],
                    help="档案根文件夹，可重复；语法 label=路径 或 路径")
    ap.add_argument("--output-dir", default="Output/Snapshots")
    ap.add_argument(
        "--hash", choices=["none", "incremental", "full"], default="full",
        help="哈希模式；默认 full（完整读取并登记 SHA-256）")
    ap.add_argument("--previous-snapshot",
                    help="增量模式的上一封存快照 .sqlite（--hash incremental 必填）")
    ap.add_argument("--map-root", action="append", default=[],
                    help="增量模式 root 配对改写：旧label=新label，可重复")
    ap.add_argument("--verify-sample-percent", type=float, default=1.0,
                    help="独立实现抽验比例（默认 1%%，至少 100 个）")
    ap.add_argument(
        "--metadata-storage", choices=["complete", "normalized"],
        default="complete",
        help="元数据范围：complete=全量元数据（基础字段＋原始工具输出，"
             "默认）；normalized=基础元数据（仅规范化常用字段）")
    ap.add_argument("--no-file-id", action="store_true")
    ap.add_argument("--resume")
    ap.add_argument("--exiftool-path")
    ap.add_argument("--ffprobe-path")
    ap.add_argument("--sevenzip-path")
    ap.add_argument("--powershell-path")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    if not args.resume and not args.root:
        print("需要 --root（或 --resume）", file=sys.stderr)
        return 2

    try:
        if args.resume:
            partial = os.path.abspath(args.resume)
            con, roots = open_resume(partial)
            resumed = True
            # 续传沿用 partial 内记录的原配置（partial 的参数即其身份）
            config = json.loads(con.execute(
                "SELECT config_json FROM snapshot_info").fetchone()[0])
            if config.get("filename_layout_version") != core.FILENAME_LAYOUT_VERSION:
                raise core.PreflightError(
                    "partial 缺少当前 filename_layout_version，禁止续传")
            publish_stem_path = core.resolve_snapshot_publish_stem(
                partial, config.get("snapshot_stem"))
            args.hash = config.get("hash", args.hash)
            stored_mode = config.get("metadata_storage")
            if stored_mode not in ("complete", "normalized"):
                raise core.PreflightError(
                    f"partial metadata_storage 无效：{stored_mode!r}")
            args.metadata_storage = stored_mode
            args.no_file_id = config.get("no_file_id", args.no_file_id)
            args.verify_sample_percent = config.get(
                "verify_sample_percent", args.verify_sample_percent)
            if not args.previous_snapshot:
                args.previous_snapshot = config.get("previous_snapshot")
            if not args.map_root:
                args.map_root = config.get("map_root", [])
            if not args.quiet:
                print(f"续传沿用原配置：hash={args.hash}", flush=True)
    except core.PreflightError as exc:
        print(f"启动失败：{exc}", file=sys.stderr)
        return 2

    if args.hash == "incremental" and not args.previous_snapshot:
        print("--hash incremental 需要 --previous-snapshot", file=sys.stderr)
        return 2
    map_root = {}
    for spec in args.map_root:
        old, sep, new = spec.partition("=")
        if not sep or not old or not new:
            print(f"--map-root 语法应为 旧label=新label：{spec}", file=sys.stderr)
            return 2
        map_root[old] = new

    # [1/6] 预检
    prog = core.Progress(1, STAGES_TOTAL, "预检", args.quiet)
    ps_path = None
    try:
        tools = core.run_preflight(
            {"exiftool": args.exiftool_path, "ffprobe": args.ffprobe_path,
             "sevenzip": args.sevenzip_path},
            output_dir=args.output_dir)
        if args.hash != "none":
            # 独立抽验依赖 Get-FileHash，因此在预检阶段提前失败
            ps_path, ps_ver = dbh.discover_powershell(args.powershell_path)
            tools["powershell"] = core.resolved_tool_info(
                "powershell", ps_path, explicit=bool(args.powershell_path),
                version=ps_ver)
            core.emit_gui_event(
                "tools_detected", tools={"powershell": tools["powershell"]})
    except core.PreflightError as exc:
        print(f"\n预检失败：\n{exc}", file=sys.stderr)
        return 2
    tool_versions = {k: v["version"] for k, v in tools.items()}
    prog.finish("三工具＋NIST 向量＋只读断言通过"
                + ("＋PowerShell" if ps_path else ""))

    try:
        if args.resume:
            pass                       # 已在上方打开并载入配置
        else:
            config = {"phase": "full", "hash": args.hash,
                      "previous_snapshot": (os.path.abspath(args.previous_snapshot)
                                            if args.previous_snapshot else None),
                      "map_root": args.map_root,
                      "verify_sample_percent": args.verify_sample_percent,
                      "metadata_storage": args.metadata_storage,
                      "no_file_id": args.no_file_id,
                      "profile_version": meta.PROFILE_VERSION,
                      "path_key_rule": core.PATH_KEY_RULE,
                      "filename_layout_version": core.FILENAME_LAYOUT_VERSION,
                      "exiftool_timeout_policy": meta.exiftool_timeout_policy()}
            roots = []
            for spec in args.root:
                label, path = core.parse_root_spec(spec)
                core.validate_root(path)
                roots.append((label, path))
            os.makedirs(args.output_dir, exist_ok=True)
            profile_tokens = core.snapshot_profile_tokens(
                "full", hash_mode=args.hash,
                raw_payload=args.metadata_storage == "complete",
                file_id=not args.no_file_id)
            name = core.snapshot_name(
                [lb for lb, _ in roots], "Full", profile_tokens)
            config["snapshot_stem"] = name
            publish_stem_path = os.path.abspath(
                os.path.join(args.output_dir, name))
            working_name = core.snapshot_working_name(name)
            partial = os.path.abspath(
                os.path.join(args.output_dir,
                             working_name + ".partial.sqlite"))
            con = core.create_partial_snapshot(partial, roots, config, tool_versions)
            resumed = False
    except core.PreflightError as exc:
        print(f"启动失败：{exc}", file=sys.stderr)
        return 2

    base = partial[:-len(".partial.sqlite")]
    events = core.EventLog(base + ".events.jsonl")
    events.emit("run_started", resumed=resumed, partial=os.path.basename(partial),
                scanner_version=core.SCANNER_VERSION, tools=tool_versions,
                config=config)

    test_max = os.environ.get("DAISY_TEST_MAX_FILES")   # 内部测试钩子：模拟中断
    try:
        # [2/6] 枚举（可重跑对账）
        prog = core.Progress(2, STAGES_TOTAL, "枚举", args.quiet)
        events.emit("stage_started", stage="enumerate")
        exclude = {partial, partial + "-wal", partial + "-shm",
                   base + ".events.jsonl"}
        stats = core.enumerate_and_reconcile(
            con, collect_file_id=not args.no_file_id, exclude_paths=exclude,
            exclude_dirs={os.path.abspath(args.output_dir)},
            on_progress=lambda s: prog.update(s["files"], bytes_done=s["bytes"],
                                              errors=s["dir_errors"]),
            max_files=int(test_max) if test_max else None)
        prog.finish(f"{stats['files']:,} 文件 / {stats['dirs']:,} 目录 / "
                    f"{stats['bytes']/1e9:.2f} GB / 目录错误 {stats['dir_errors']}")
        events.emit("stage_finished", stage="enumerate", **stats)

        # [3/6] 哈希（full/incremental/none；逐文件断点续传）
        if args.hash == "none":
            con.execute("UPDATE entries SET hash_status='skipped'"
                        " WHERE hash_status='pending'")
            con.commit()
            events.emit("stage_skipped", stage="hash", reason="hash_mode_none")
            core.emit_gui_event(
                "progress_skip", stage_idx=3, stage_total=STAGES_TOTAL,
                name="哈希", reason="--hash none")
            if not args.quiet:
                print(f"[3/{STAGES_TOTAL}] 哈希 跳过（--hash none）", flush=True)
        else:
            prog = core.Progress(3, STAGES_TOTAL, "哈希", args.quiet)
            events.emit("stage_started", stage="hash", mode=args.hash)
            prev = None
            if args.hash == "incremental":
                prev = dbh.load_previous(
                    os.path.abspath(args.previous_snapshot), map_root)
                con.execute("UPDATE snapshot_info SET previous_snapshot_uuid=?",
                            (prev.uuid,))
                con.commit()
                events.emit("previous_snapshot_loaded", uuid=prev.uuid,
                            path=os.path.basename(args.previous_snapshot),
                            has_file_issues=prev.has_file_issues)
            test_max_hash = os.environ.get("DAISY_TEST_MAX_HASH_FILES")
            hstats = dbh.process_hash_stage(
                con, args.hash, previous=prev,
                max_files=int(test_max_hash) if test_max_hash else None,
                on_progress=lambda i, st: prog.update(
                    i, total=st["total"], bytes_done=st["bytes_hashed"],
                    bytes_total=st["bytes_total"], errors=st["error"]),
                on_event=lambda ev, **kw: events.emit(ev, **kw))
            prog.finish(
                f"{hstats['done']}/{hstats['total']} 成功"
                f"（复用 {hstats['reused']}）/ {hstats['bytes_hashed']/1e9:.2f} GB"
                f" / 错误 {hstats['error']} / unstable {hstats['unstable']}")
            events.emit("stage_finished", stage="hash", **hstats)

        # [4/6] 元数据（双后端＋压缩包摘要；逐文件断点续传）
        prog = core.Progress(4, STAGES_TOTAL, "元数据", args.quiet)
        events.emit("stage_started", stage="metadata")
        mstats = meta.process_metadata_stage(
            con, tools,
            retain_original_metadata=args.metadata_storage == "complete",
            timeout_policy=config.get("exiftool_timeout_policy"),
            on_progress=lambda i, st: prog.update(
                i, total=st["total"], errors=st["error"] + st["timeout"]))
        ff_summary = ""
        if args.metadata_storage == "complete":
            ff_summary = (
                f" / ffprobe Raw {mstats['ffprobe_payloads']}"
                f" / 可选探测不可读 {mstats['ffprobe_optional_unreadable']}"
                f" / 可选探测超时 {mstats['ffprobe_optional_timeouts']}")
        prog.finish(f"{mstats['done']}/{mstats['total']} 成功 / 错误 {mstats['error']}"
                    f" / 超时 {mstats['timeout']} / unstable {mstats['unstable']}"
                    f"{ff_summary}")
        events.emit("stage_finished", stage="metadata", **mstats)

        # [5/6] 复扫校验＋独立实现抽验
        prog = core.Progress(5, STAGES_TOTAL, "复扫＋抽验", args.quiet)
        events.emit("stage_started", stage="rescan")
        changed = core.rescan_check(con)
        events.emit("stage_finished", stage="rescan", unstable=changed)
        vsummary = ""
        if args.hash != "none":
            events.emit("stage_started", stage="verify_sample")
            vstats = dbh.independent_verify(
                con, percent=args.verify_sample_percent, min_count=100,
                powershell=ps_path,
                on_event=lambda ev, **kw: events.emit(ev, **kw))
            events.emit("stage_finished", stage="verify_sample", **vstats)
            vsummary = (f" / 抽验 {vstats['matched']}/{vstats['sampled']} 一致")
            if vstats["mismatched"] or vstats["tool_error"]:
                vsummary += (f"，不一致 {vstats['mismatched']}"
                             f"，工具错误 {vstats['tool_error']}")
            if vstats["mismatched"]:
                print(f"\n!! 独立实现抽验发现 {vstats['mismatched']} 处哈希不一致"
                      "——相关条目已标 unstable，详见 errors 表与事件日志",
                      file=sys.stderr)
        prog.finish(f"unstable {changed}{vsummary}")

        # [6/6] 封存
        prog = core.Progress(6, STAGES_TOTAL, "封存", args.quiet)
        events.emit("stage_started", stage="finalize")
        manifest = {
            "scanner_version": core.SCANNER_VERSION,
            "tools": tools,
            "config": config,
        }
        final = core.finalize_snapshot(
            con, partial, hash_coverage=args.hash,
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
            print("!! SQLite 数据库已完整封存；另发现源文件或扫描证据问题"
                  f"（错误/unstable/枚举失败），问题报告：{issue_report}",
                  file=sys.stderr)
        return 0

    except KeyboardInterrupt:
        con.commit()
        con.close()
        events.emit("run_interrupted", partial=os.path.basename(partial))
        events.close()
        core.release_scan_lock(partial)    # 本进程退出即让出所有权
        print(f"\n已中断。续传：python {os.path.basename(__file__)}"
              f" --resume \"{partial}\"", file=sys.stderr)
        return 130
    except core.PreflightError as exc:
        events.emit("run_failed", error=str(exc))
        events.close()
        core.release_scan_lock(partial)
        print(f"\n失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
