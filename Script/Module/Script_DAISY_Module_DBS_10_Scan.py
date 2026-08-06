r"""DAISY v1.6.0 统一扫描入口：Full／Quick、暂停恢复与 schema 4 发布。

旧 ``full-scan``／``quick-scan`` 命令在兼容期内继续存在；新 GUI 将
DBS-11 完整档案扫描与 DBS-12 快速档案扫描映射到本入口，以共享同一套
session、lease、worker 与发布编排。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sqlite3
import sys
import threading
import time
from typing import Callable


_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
_LIB_DIR = os.path.join(os.path.dirname(_MODULE_DIR), "Lib")
sys.path.insert(0, _LIB_DIR)

import Script_DAISY_Lib_DBS_01_Core as core
import Script_DAISY_Lib_DBS_02_Meta as dbmeta
import Script_DAISY_Lib_DBS_03_Hash as dbhash
import Script_DAISY_Lib_DBS_08_State as dbstate
import Script_DAISY_Lib_DBS_09_Run as dbrun
import Script_DAISY_Lib_DBS_10_Issues as dbissues


STAGES_TOTAL = 9
QUICK_MIN_FREE_BYTES = 200 * 1024 * 1024
_STAGES = {
    "enumerate": (2, "枚举"),
    "hash": (3, "内容哈希"),
    "metadata": (4, "元数据"),
    "format": (5, "格式校验"),
    "rescan": (6, "最终复扫"),
    "verify_hash": (7, "独立抽验"),
    "seal": (8, "封存检查"),
    "publish": (9, "原子发布"),
}
_TIMEOUT_ACTIONS = (
    "continue_waiting", "skip_and_record", "stop_and_resume",
)
_RETRY_MODES = ("pending", "transient", "all_unsuccessful")


def _finite_percent(value: object, label: str, *, allow_zero: bool) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise core.PreflightError(f"{label}不是有效数字：{value!r}") from exc
    minimum = 0.0 if allow_zero else 0.0
    if not math.isfinite(number) or number < minimum or number > 100.0 \
            or (not allow_zero and number == 0.0):
        boundary = "0～100" if allow_zero else "大于 0 且不超过 100"
        raise core.PreflightError(f"{label}必须{boundary}：{value!r}")
    return number


def _append_event(path: str, event: str, payload: dict[str, object]) -> None:
    record = {"ts": core.now_utc_iso(), "event": event, **payload}
    with open(path, "a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")


class ScanReporter:
    """把内部事件同时送往事件证据、GUI 通道和紧凑控制台输出。"""

    def __init__(
        self,
        event_log_path: str,
        *,
        quiet: bool,
        event_log_active: bool = True,
    ) -> None:
        self.event_log_path = event_log_path
        self.quiet = quiet
        self._event_log_active = event_log_active
        self._event_log_error: BaseException | None = None
        self._lock = threading.RLock()
        self._started: set[str] = set()
        self._stage_started_at: dict[str, float] = {}
        self._last_console_progress: dict[str, float] = {}

    @property
    def event_log_error(self) -> BaseException | None:
        return self._event_log_error

    def _start_stage(self, stage: str) -> None:
        with self._lock:
            if stage in self._started or stage not in _STAGES:
                return
            self._started.add(stage)
            self._stage_started_at[stage] = time.monotonic()
            index, name = _STAGES[stage]
            if not self.quiet:
                print(f"[{index}/{STAGES_TOTAL}] {name} …", flush=True)
            core.emit_gui_event(
                "progress_start",
                stage_idx=index,
                stage_total=STAGES_TOTAL,
                name=name,
            )

    @staticmethod
    def _progress_payload(payload: dict[str, object]) -> dict[str, object]:
        result = dict(payload)
        result["done"] = result.get(
            "processed", result.get("files", result.get("done", 0)))
        if "bytes_done" not in result:
            result["bytes_done"] = result.get(
                "bytes_read", result.get("bytes"))
        result.pop("stage", None)
        failures = 0
        for key in (
            "dir_errors", "error", "timeout", "unstable", "invalid",
            "mismatched", "tool_error",
        ):
            try:
                failures += int(result.get(key) or 0)
            except (TypeError, ValueError):
                pass
        result["errors"] = failures
        return result

    def progress(self, stage: str, payload: dict[str, object]) -> None:
        with self._lock:
            if stage not in _STAGES:
                return
            self._start_stage(stage)
            normalized = self._progress_payload(payload)
            core.emit_gui_event("progress_update", stage=stage, **normalized)
            now = time.monotonic()
            if self.quiet or now - self._last_console_progress.get(
                    stage, 0.0) < 1.0:
                return
            self._last_console_progress[stage] = now
            done = int(normalized.get("done") or 0)
            total = normalized.get("total")
            suffix = f"{done:,}/{int(total):,}" if total else f"{done:,}"
            bytes_done = normalized.get("bytes_done")
            if bytes_done is not None:
                suffix += f" · {int(bytes_done) / 1e9:.2f} GB"
            errors = int(normalized.get("errors") or 0)
            if errors:
                suffix += f" · 问题 {errors:,}"
            print(f"[{_STAGES[stage][0]}/{STAGES_TOTAL}] "
                  f"{_STAGES[stage][1]} | {suffix}", flush=True)

    def event(self, event: str, **payload: object) -> None:
        with self._lock:
            clean = dict(payload)
            if self._event_log_active and self._event_log_error is None:
                try:
                    _append_event(self.event_log_path, event, clean)
                except (OSError, TypeError, ValueError) as exc:
                    self._event_log_error = exc
            stage = str(clean.get("stage") or "")
            if event in ("stage_started", "stage_restarted"):
                self._start_stage(stage)
            elif event == "stage_skipped" and stage in _STAGES:
                index, name = _STAGES[stage]
                core.emit_gui_event(
                    "progress_skip",
                    stage_idx=index,
                    stage_total=STAGES_TOTAL,
                    name=name,
                    reason=str(clean.get("reason") or "当前配置"),
                )
                if not self.quiet:
                    print(f"[{index}/{STAGES_TOTAL}] {name} 跳过："
                          f"{clean.get('reason') or '当前配置'}", flush=True)
            elif event == "stage_finished" and stage in _STAGES:
                index, name = _STAGES[stage]
                elapsed = time.monotonic() - self._stage_started_at.get(
                    stage, time.monotonic())
                summary_parts = []
                for key, label in (
                    ("processed", "处理"), ("files", "文件"),
                    ("matched", "一致"), ("mismatched", "不一致"),
                    ("error", "错误"), ("timeout", "超时"),
                ):
                    if clean.get(key) is not None:
                        summary_parts.append(f"{label} {clean[key]}")
                summary = " · ".join(summary_parts) or "完成"
                core.emit_gui_event(
                    "progress_finish",
                    stage_idx=index,
                    stage_total=STAGES_TOTAL,
                    name=name,
                    summary=summary,
                    elapsed=elapsed,
                )
                if not self.quiet:
                    print(f"[{index}/{STAGES_TOTAL}] {name} 完成：{summary}",
                          flush=True)
                if stage == "seal":
                    # 封存后事件文件已不再是权威来源；发布函数会删除它。
                    self._event_log_active = False
            if event == "threshold_reached" and not self.quiet:
                print(
                    "!! 单文件连续无进展达到阈值："
                    f"{clean.get('file')}（{clean.get('threshold_seconds')}s）。"
                    "默认继续等待；可由 GUI 选择跳过或停止并保留续传。",
                    file=sys.stderr,
                    flush=True,
                )
            core.emit_gui_event(event, **clean)

    def control_rejected(self, rejection: dbrun.ControlRejection) -> None:
        self.event(
            "control_rejected",
            code=rejection.code,
            detail=rejection.detail,
        )

    def control_receipt(self, receipt: dbrun.ControlReceipt) -> None:
        self.event(
            "control_receipt",
            sequence=receipt.sequence,
            action=receipt.action,
            accepted=receipt.accepted,
            reason=receipt.reason,
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "统一扫描：选择 Full 或 Quick，创建可暂停、可跨重启恢复的 "
            "schema 4 快照"
        ),
    )
    parser.add_argument("--mode", choices=("full", "quick"))
    parser.add_argument(
        "--root", action="append", default=[],
        help="新建扫描的档案根，可重复；语法 label=路径 或 路径",
    )
    parser.add_argument("--output-dir")
    parser.add_argument("--resume", help="恢复 schema 4 .partial.sqlite")
    parser.add_argument(
        "--manual-resume", action="store_true",
        help="明确恢复已停止的任务；普通暂停恢复不需要",
    )
    parser.add_argument(
        "--hash", choices=("none", "incremental", "full"),
    )
    parser.add_argument("--previous-snapshot")
    parser.add_argument("--map-root", action="append", default=[])
    parser.add_argument("--verify-sample-percent", type=float)
    parser.add_argument(
        "--metadata-storage", choices=("complete", "normalized"),
    )
    parser.add_argument(
        "--format-validation", choices=("off", "sample", "all"),
    )
    parser.add_argument("--format-sample-percent", type=float)
    parser.add_argument("--no-file-id", action="store_true", default=None)
    parser.add_argument(
        "--timeout-action", choices=_TIMEOUT_ACTIONS,
        help="无进展达到动态阈值后的默认处置；默认继续等待",
    )
    parser.add_argument(
        "--retry-mode", choices=("pending", "transient", "all-unsuccessful"),
        default="pending",
        help="本 session 的哈希处理范围",
    )
    parser.add_argument(
        "--show-current-file", action="store_true",
        help="发送正在处理的相对文件路径；默认关闭",
    )
    parser.add_argument(
        "--control-stdin", action="store_true",
        help="从 stdin 接收 daisy-control-v1 UTF-8 JSONL 控制消息",
    )
    parser.add_argument("--exiftool-path")
    parser.add_argument("--ffprobe-path")
    parser.add_argument("--sevenzip-path")
    parser.add_argument("--powershell-path")
    parser.add_argument("--quiet", action="store_true")
    return parser


def _parse_roots(specs: list[str]) -> list[tuple[str, str]]:
    roots = []
    for spec in specs:
        label, path = core.parse_root_spec(spec)
        core.validate_root(path)
        roots.append((label, path))
    if not roots:
        raise core.PreflightError("新建扫描至少需要一个 --root")
    return roots


def _quick_preflight(output_dir: str) -> dict[str, object]:
    os.makedirs(output_dir, exist_ok=True)
    free = shutil.disk_usage(output_dir).free
    if free < QUICK_MIN_FREE_BYTES:
        raise core.PreflightError(
            f"输出目录剩余空间不足：{free / 1e6:.0f} MB")
    core.emit_gui_event("tools_detected", tools={})
    return {}


def _full_preflight(args: argparse.Namespace, output_dir: str) \
        -> dict[str, object]:
    tools = core.run_preflight(
        {
            "exiftool": args.exiftool_path,
            "ffprobe": args.ffprobe_path,
            "sevenzip": args.sevenzip_path,
        },
        output_dir=output_dir,
    )
    if args.hash != "none":
        powershell, version = dbhash.discover_powershell(
            args.powershell_path)
        tools["powershell"] = core.resolved_tool_info(
            "powershell",
            powershell,
            explicit=bool(args.powershell_path),
            version=version,
        )
        core.emit_gui_event(
            "tools_detected", tools={"powershell": tools["powershell"]})
    return tools


def _format_tokens(mode: str) -> list[str]:
    if mode == "sample":
        return ["Fmt-Sample"]
    if mode == "all":
        return ["Fmt-All"]
    return []


def _new_config(
    args: argparse.Namespace,
    mode: str,
) -> dict[str, object]:
    hash_mode = args.hash or ("full" if mode == "full" else "none")
    metadata_storage = args.metadata_storage or (
        "complete" if mode == "full" else "normalized")
    format_mode = args.format_validation or "off"
    if mode == "quick":
        if hash_mode != "none":
            raise core.PreflightError("Quick 不能启用内容哈希")
        if args.metadata_storage not in (None, "normalized"):
            raise core.PreflightError("Quick 不能启用元数据提取")
        if format_mode != "off":
            raise core.PreflightError("Quick 不能启用格式校验")
        if args.previous_snapshot or args.map_root:
            raise core.PreflightError("Quick 不接受增量快照或根标签映射")
        if args.verify_sample_percent is not None:
            raise core.PreflightError("Quick 不接受独立哈希抽验比例")
        if args.format_sample_percent is not None:
            raise core.PreflightError("Quick 不接受格式校验抽样比例")
        if args.timeout_action is not None:
            raise core.PreflightError("Quick 不接受哈希 timeout 处置")
        if any((
            args.exiftool_path, args.ffprobe_path,
            args.sevenzip_path, args.powershell_path,
        )):
            raise core.PreflightError("Quick 不调用外部解析或哈希工具")
    if hash_mode == "incremental" and not args.previous_snapshot:
        raise core.PreflightError(
            "--hash incremental 需要 --previous-snapshot")
    if hash_mode != "incremental" and (
            args.previous_snapshot or args.map_root):
        raise core.PreflightError(
            "--previous-snapshot／--map-root 仅用于 incremental")
    if hash_mode == "none" and args.powershell_path:
        raise core.PreflightError(
            "哈希关闭时不接受 --powershell-path")
    if hash_mode == "none" and args.timeout_action is not None:
        raise core.PreflightError(
            "哈希关闭时不接受 --timeout-action")
    if format_mode != "sample" and args.format_sample_percent is not None:
        raise core.PreflightError(
            "--format-sample-percent 仅用于 --format-validation sample")
    verify_percent = _finite_percent(
        1.0 if args.verify_sample_percent is None
        else args.verify_sample_percent,
        "独立哈希抽验比例",
        allow_zero=True,
    )
    format_percent = _finite_percent(
        10.0 if args.format_sample_percent is None
        else args.format_sample_percent,
        "格式校验抽样比例",
        allow_zero=True,
    )
    return {
        "phase": mode,
        "quick": mode == "quick",
        "hash": hash_mode,
        "previous_snapshot": (
            os.path.abspath(args.previous_snapshot)
            if args.previous_snapshot else None
        ),
        "map_root": list(args.map_root),
        "verify_sample_percent": verify_percent,
        "metadata_storage": metadata_storage,
        "format_validation": format_mode,
        "format_sample_percent": format_percent,
        "no_file_id": bool(args.no_file_id),
        "profile_version": dbmeta.PROFILE_VERSION,
        "path_key_rule": core.PATH_KEY_RULE,
        "filename_layout_version": dbstate.FILENAME_LAYOUT_VERSION,
        "exiftool_timeout_policy": dbmeta.exiftool_timeout_policy(),
        "hash_timeout_policy": {
            "kind": "no_progress",
            "minimum_seconds": 90,
            "step_bytes": 9 * 1024 ** 3,
            "step_seconds": 90,
            "stall_warning_seconds": dbhash.HASH_STALL_SECONDS,
            "default_decision": args.timeout_action or "continue_waiting",
        },
    }


def _create_new_run(
    args: argparse.Namespace,
) -> tuple[dbrun.RunHandle, dict[str, object]]:
    mode = args.mode or "full"
    roots = _parse_roots(args.root)
    output_dir = os.path.abspath(args.output_dir or "Output/Snapshots")
    config = _new_config(args, mode)
    args.hash = str(config["hash"])
    preflight = core.Progress(1, STAGES_TOTAL, "预检", args.quiet)
    tools = (
        _quick_preflight(output_dir)
        if mode == "quick" else _full_preflight(args, output_dir)
    )
    preflight.finish(
        "输出目录与空间通过（Quick 无工具依赖）"
        if mode == "quick" else "工具、标准向量、只读断言与输出空间通过"
    )
    tokens = core.snapshot_profile_tokens(
        mode,
        hash_mode=str(config["hash"]),
        raw_payload=config["metadata_storage"] == "complete",
        file_id=not bool(config["no_file_id"]),
    )
    tokens.extend(_format_tokens(str(config["format_validation"])))
    stem = core.snapshot_name(
        [label for label, _path in roots],
        "Full" if mode == "full" else "Quick",
        tokens,
    )
    config["snapshot_stem"] = stem
    publish_stem = os.path.join(output_dir, stem)
    working_name = core.snapshot_working_name(stem)
    partial = os.path.join(
        output_dir, working_name + ".partial.sqlite")
    event_log = partial[:-len(".partial.sqlite")] + ".events.jsonl"
    handle = dbrun.create_run(
        partial,
        roots,
        config,
        output_dir=output_dir,
        publish_stem_path=publish_stem,
        event_log_path=event_log,
        tool_versions=tools,
        scanner_version=dbstate.MIN_READER_VERSION,
    )
    return handle, config


def _frozen_tool(
    tools: dict[str, object], name: str,
) -> tuple[str, str]:
    value = tools.get(name)
    if not isinstance(value, dict):
        raise core.PreflightError(f"partial 缺少冻结的 {name} 工具记录")
    path = str(value.get("path") or "")
    version = str(value.get("version") or "")
    if not path or not version:
        raise core.PreflightError(f"partial 的 {name} 工具记录不完整")
    return path, version


def _same_tool(
    name: str,
    frozen: tuple[str, str],
    current: dict[str, object],
) -> None:
    current_path = str(current.get("path") or "")
    current_version = str(current.get("version") or "")
    if os.path.normcase(os.path.abspath(frozen[0])) != os.path.normcase(
            os.path.abspath(current_path)) or frozen[1] != current_version:
        raise core.PreflightError(
            f"恢复前 {name} 路径或版本发生变化："
            f"冻结={frozen[0]} / {frozen[1]}；"
            f"当前={current_path} / {current_version}"
        )


def _resume_preflight(
    args: argparse.Namespace,
    preview: dbrun.ResumePreview,
) -> dict[str, object]:
    config = preview.config
    mode = str(config.get("phase") or "")
    if mode not in ("full", "quick"):
        raise core.PreflightError(f"partial 的扫描模式无效：{mode!r}")
    if args.mode is not None and args.mode != mode:
        raise core.PreflightError(
            f"--mode {args.mode} 与 partial 冻结模式 {mode} 不一致")
    forbidden = {
        "--root": bool(args.root),
        "--output-dir": args.output_dir is not None,
        "--hash": args.hash is not None,
        "--previous-snapshot": args.previous_snapshot is not None,
        "--map-root": bool(args.map_root),
        "--verify-sample-percent": args.verify_sample_percent is not None,
        "--metadata-storage": args.metadata_storage is not None,
        "--format-validation": args.format_validation is not None,
        "--format-sample-percent": args.format_sample_percent is not None,
        "--no-file-id": args.no_file_id is not None,
        "--timeout-action": args.timeout_action is not None,
        "--exiftool-path": args.exiftool_path is not None,
        "--ffprobe-path": args.ffprobe_path is not None,
        "--sevenzip-path": args.sevenzip_path is not None,
        "--powershell-path": args.powershell_path is not None,
    }
    supplied = [flag for flag, present in forbidden.items() if present]
    if supplied:
        raise core.PreflightError(
            "恢复必须沿用 partial 的冻结参数，请移除：" + "、".join(supplied))
    if preview.run_state in ("published", "failed_terminal"):
        raise core.PreflightError(
            f"状态 {preview.run_state} 不能恢复")
    if preview.run_state == "stopped" and not args.manual_resume:
        raise core.PreflightError("stopped partial 需要用户明确手动恢复")
    if preview.lease_classification in ("active_local", "active_foreign"):
        raise core.PreflightError(
            "partial 仍由有效 owner 使用，拒绝运行恢复预检："
            f"{preview.lease_classification}")
    if preview.run_state == "sealed_unpublished":
        preflight = core.Progress(
            1, STAGES_TOTAL, "发布恢复预检", args.quiet)
        preflight.finish("sealed 身份与 lease 状态可接管；不访问源目录或工具")
        return config
    for _label, root in preview.roots:
        core.validate_root(root)
    preflight = core.Progress(1, STAGES_TOTAL, "恢复预检", args.quiet)
    if mode == "quick":
        tools: dict[str, object] = {}
        _quick_preflight(os.path.dirname(preview.partial_path))
    else:
        explicit = {
            name: _frozen_tool(preview.tools, name)[0]
            for name in ("exiftool", "ffprobe", "sevenzip")
        }
        tools = core.run_preflight(
            explicit,
            output_dir=os.path.dirname(preview.partial_path),
        )
        for name in ("exiftool", "ffprobe", "sevenzip"):
            _same_tool(name, _frozen_tool(preview.tools, name), tools[name])
        if str(config.get("hash") or "none") != "none":
            frozen_ps = _frozen_tool(preview.tools, "powershell")
            ps_path, ps_version = dbhash.discover_powershell(frozen_ps[0])
            current_ps = core.resolved_tool_info(
                "powershell", ps_path, explicit=True, version=ps_version)
            _same_tool("powershell", frozen_ps, current_ps)
            tools["powershell"] = current_ps
    preflight.finish("冻结身份、源目录、工具与输出目录均可恢复")
    return config


def _open_resume_run(
    args: argparse.Namespace,
) -> tuple[dbrun.RunHandle, dict[str, object]]:
    preview = dbrun.inspect_resume(os.path.abspath(args.resume))
    config = _resume_preflight(args, preview)
    handle = (
        dbrun.resume_publication_run(
            preview.partial_path,
            scanner_version=dbstate.MIN_READER_VERSION,
        )
        if preview.run_state == "sealed_unpublished" else
        dbrun.resume_run(
            preview.partial_path,
            manual=args.manual_resume,
            scanner_version=dbstate.MIN_READER_VERSION,
        )
    )
    return handle, config


def _recover_open_handle(handle: dbrun.RunHandle, reason: str) -> None:
    try:
        handle.connection.rollback()
        runtime = dbstate.load_runtime(handle.connection)
        if runtime.run_state in (
            "running", "pause_requested", "paused", "sealing",
            "sealed_unpublished",
        ):
            dbstate.recover_interrupted(
                handle.connection,
                reason=reason[:2000] or "scan_process_failed",
            )
    except (sqlite3.Error, core.PreflightError):
        pass


def _close_open_handle(handle: dbrun.RunHandle) -> None:
    try:
        dbrun.close_handle(handle, release_lease=True)
    except (sqlite3.Error, OSError, core.PreflightError):
        try:
            handle.connection.close()
        except sqlite3.Error:
            pass


def _record_publish_retry_failure(
    handle: dbrun.RunHandle,
    error: BaseException,
) -> None:
    try:
        dbrun.record_publication_retry_failure(handle, str(error))
    except (sqlite3.Error, core.PreflightError):
        pass


def _publish_sealed_only(
    handle: dbrun.RunHandle,
    reporter: ScanReporter,
    before_publish: Callable[[], None],
) -> dict[str, object]:
    runtime = dbstate.load_runtime(handle.connection)
    reporter.event(
        "stage_started", stage="publish", retry=True, source_rescan=False)
    before_publish()
    handle.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    handle.connection.execute("PRAGMA journal_mode=DELETE")
    handle.connection.close()
    publication = dbstate.publish_sealed_snapshot(
        handle.partial_path,
        runtime.publish_stem_path + ".publishing.sqlite",
        lease_path=handle.lease_path,
        lease_id=handle.lease.lease_id,
        issue_report_builder=(
            dbissues.build_snapshot_issue_report_from_connection),
    )
    warnings = list(publication.warnings)
    if os.path.exists(runtime.event_log_path):
        try:
            os.remove(runtime.event_log_path)
        except OSError as exc:
            warnings.append(
                f"最终快照已发布，但临时事件日志未删除：{exc}")
    if warnings != list(publication.warnings):
        publication = dbstate.PublicationResult(
            final_path=publication.final_path,
            sha256=publication.sha256,
            partial_removed=publication.partial_removed,
            lease_released=publication.lease_released,
            warnings=tuple(warnings),
            issue_report_path=publication.issue_report_path,
        )
    reporter.event(
        "stage_finished",
        stage="publish",
        retry=True,
        source_rescan=False,
        final_path=publication.final_path,
        issue_report_path=publication.issue_report_path,
    )
    return {
        "state": "published",
        "stage": "publish",
        "publication": publication,
        "publication_retry": True,
    }


def _run_handle(
    args: argparse.Namespace,
    handle: dbrun.RunHandle,
    config: dict[str, object],
) -> int:
    runtime = dbstate.load_runtime(handle.connection)
    publish_only = runtime.run_state == "sealed_unpublished"
    reporter = ScanReporter(
        runtime.event_log_path,
        quiet=args.quiet,
        event_log_active=not publish_only,
    )
    reporter.event(
        "run_started" if not handle.resumed else "run_resumed",
        partial=os.path.basename(handle.partial_path),
        scanner_version=dbstate.MIN_READER_VERSION,
        schema_version=dbstate.SCHEMA_VERSION,
        config=config,
    )
    if reporter.event_log_error is not None:
        _recover_open_handle(handle, str(reporter.event_log_error))
        print(
            "\n无法创建运行事件证据，已拒绝开始扫描："
            f"{reporter.event_log_error}",
            file=sys.stderr,
        )
        return 1
    router = dbrun.RunCommandRouter(on_receipt=reporter.control_receipt)
    inbox = None
    heartbeat_errors: list[BaseException] = []

    def heartbeat_failed(exc: BaseException) -> None:
        heartbeat_errors.append(exc)
        reporter.event("lease_heartbeat_failed", error=str(exc))
        router.route(dbrun.ControlCommand(
            sequence=2_147_483_647,
            action="save_exit",
            request_id="lease-heartbeat-failed",
        ))

    heartbeat = dbrun.LeaseHeartbeat(
        handle, on_error=heartbeat_failed)
    try:
        heartbeat.start()
    except (OSError, sqlite3.Error, core.PreflightError) as exc:
        _recover_open_handle(handle, str(exc))
        reporter.event("lease_heartbeat_failed", error=str(exc))
        print(f"\nlease 心跳无法启动：{exc}", file=sys.stderr)
        return 1
    heartbeat_stopped = False
    if args.control_stdin:
        stream = getattr(sys.stdin, "buffer", sys.stdin)
        inbox = dbrun.ControlInbox(
            stream,
            on_command=router.route,
            on_rejected=reporter.control_rejected,
        )
        try:
            inbox.start()
        except RuntimeError as exc:
            heartbeat.stop(timeout_seconds=10.0)
            _recover_open_handle(handle, str(exc))
            reporter.event("control_input_failed", error=str(exc))
            print(f"\n控制输入无法启动：{exc}", file=sys.stderr)
            return 1

    def before_seal() -> None:
        nonlocal heartbeat_stopped
        heartbeat_stopped = heartbeat.stop(timeout_seconds=10.0)
        if not heartbeat_stopped:
            raise core.PreflightError(
                "lease 心跳线程未在封存前停止，拒绝封存")
        if heartbeat.error is not None:
            raise core.PreflightError(
                f"lease 心跳失败，拒绝封存：{heartbeat.error}")
        if reporter.event_log_error is not None:
            raise core.PreflightError(
                "运行事件证据写入失败，拒绝封存："
                f"{reporter.event_log_error}")

    retry_mode = args.retry_mode.replace("-", "_")
    timeout_policy = config.get("hash_timeout_policy")
    default_decision = "continue_waiting"
    if isinstance(timeout_policy, dict):
        default_decision = str(
            timeout_policy.get("default_decision") or default_decision)
    try:
        result = (
            _publish_sealed_only(handle, reporter, before_seal)
            if publish_only else
            dbrun.run_scan_to_publication(
                handle,
                router,
                show_current_file=args.show_current_file,
                hash_default_decision=default_decision,
                hash_retry_mode=retry_mode,
                on_progress=reporter.progress,
                on_event=reporter.event,
                before_seal=before_seal,
            )
        )
    except KeyboardInterrupt:
        _recover_open_handle(handle, "keyboard_interrupt")
        reporter.event(
            "run_interrupted",
            partial=os.path.basename(handle.partial_path),
        )
        print(
            f"\n已安全中断并保留恢复证据：{handle.partial_path}",
            file=sys.stderr,
        )
        return 130
    except (core.PreflightError, OSError, sqlite3.Error) as exc:
        if publish_only:
            _record_publish_retry_failure(handle, exc)
        else:
            _recover_open_handle(handle, str(exc))
        reporter.event("run_failed", error=str(exc))
        print(f"\n扫描失败并保留可诊断 partial：{exc}", file=sys.stderr)
        return 1
    finally:
        if not heartbeat_stopped:
            heartbeat_stopped = heartbeat.stop(timeout_seconds=10.0)
            if not heartbeat_stopped:
                heartbeat_errors.append(RuntimeError(
                    "lease 心跳线程未在退出前停止"))
        if inbox is not None:
            inbox.stop()

    state = str(result.get("state") or "")
    if heartbeat_errors:
        _recover_open_handle(handle, str(heartbeat_errors[-1]))
        print(
            f"\nlease 心跳失败，已保留恢复证据：{heartbeat_errors[-1]}",
            file=sys.stderr,
        )
        return 1
    if state == "published":
        publication = result["publication"]
        reporter.event(
            "run_result",
            state=state,
            final_path=publication.final_path,
            issue_report_path=publication.issue_report_path,
        )
        print(
            f"\n快照：{publication.final_path}"
            "\nmanifest、session、attempt 与运行证据：已内置于 SQLite",
        )
        if publication.issue_report_path:
            print(
                "!! 数据库已完整封存；另有源文件或扫描证据问题："
                f"{publication.issue_report_path}",
                file=sys.stderr,
            )
        for warning in publication.warnings:
            print(f"!! {warning}", file=sys.stderr)
        return 0
    reporter.event(
        "run_result",
        state=state,
        stage=str(result.get("stage") or ""),
        partial=handle.partial_path,
    )
    if state == "save_exit":
        print(
            "\n进度已安全保存；下次打开 DAISY 后可选择恢复："
            f"\n{handle.partial_path}",
        )
        return 75
    if state == "stopped":
        print(
            "\n任务已停止并保留审计证据；不会主动建议恢复，"
            f"可手动选择：\n{handle.partial_path}",
            file=sys.stderr,
        )
        return 130
    print(
        f"\n扫描返回未识别状态 {state!r}；partial 已保留："
        f"{handle.partial_path}",
        file=sys.stderr,
    )
    return 1


def main(argv: list[str] | None = None) -> int:
    core.force_utf8_io()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.resume and args.root:
        print("--resume 与 --root 不能同时使用", file=sys.stderr)
        return 2
    if not args.resume and args.manual_resume:
        print("--manual-resume 只能与 --resume 一起使用", file=sys.stderr)
        return 2
    handle = None
    try:
        handle, config = (
            _open_resume_run(args) if args.resume else _create_new_run(args)
        )
        return _run_handle(args, handle, config)
    except core.PreflightError as exc:
        print(f"启动失败：{exc}", file=sys.stderr)
        return 2
    except (OSError, sqlite3.Error) as exc:
        if handle is not None:
            _recover_open_handle(handle, str(exc))
        print(f"扫描入口失败并已保留恢复证据：{exc}", file=sys.stderr)
        return 1
    finally:
        if handle is not None:
            _close_open_handle(handle)


if __name__ == "__main__":
    sys.exit(main())
