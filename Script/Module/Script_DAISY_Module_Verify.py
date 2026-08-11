r"""档案数据核验：文件状态、哈希与格式校验的只读编排入口。

旧 ``check-hash``/``check-format`` 命令继续保持 v1.5.1 参数和输出；本入口只读
可只读消费 schema 3/4 封存快照，也可直接枚举用户指定目录，并发布一份
Markdown 阅读报告和一份 JSON 技术证据。
档案数据核验支持进程内暂停／继续／停止，但不提供跨重启续传，因而明确拒绝
``save_exit`` 控制动作。
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import threading
import time
from typing import Callable


_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
_LIB_DIR = os.path.join(os.path.dirname(_MODULE_DIR), "Lib")
sys.path.insert(0, _LIB_DIR)

import Script_DAISY_Lib_Snapshot_Core as core
import Script_DAISY_Lib_Scan_Runtime as dbrun
import Script_DAISY_Lib_Verify_Runtime as verifyrun


_STAGES = {
    "stat": (1, "文件状态"),
    "hash": (2, "哈希复检"),
    "format": (3, "格式校验"),
    "raw": (4, "RAW 深度校验"),
}
_STAGES_TOTAL = len(_STAGES)
_TIMEOUT_ACTIONS = (
    "continue_waiting", "skip_and_record", "stop_and_resume",
)


class VerificationReporter:
    """把档案数据核验事件映射为 GUI 协议和紧凑控制台进度。"""

    def __init__(self, *, quiet: bool, default_decision: str) -> None:
        self.quiet = quiet
        self.default_decision = default_decision
        self._lock = threading.RLock()
        self._started: set[str] = set()
        self._stage_started_at: dict[str, float] = {}
        self._last_console_progress: dict[str, float] = {}

    def _start_stage(self, stage: str) -> None:
        with self._lock:
            if stage not in _STAGES or stage in self._started:
                return
            self._started.add(stage)
            self._stage_started_at[stage] = time.monotonic()
            index, name = _STAGES[stage]
            if not self.quiet:
                print(f"[{index}/{_STAGES_TOTAL}] {name} …", flush=True)
            core.emit_gui_event(
                "progress_start",
                stage_idx=index,
                stage_total=_STAGES_TOTAL,
                name=name,
            )

    def progress(
        self,
        stage: str,
        processed: int,
        total: int,
        summary: dict[str, object],
    ) -> None:
        with self._lock:
            if stage not in _STAGES:
                return
            self._start_stage(stage)
            payload = dict(summary)
            payload.update({
                "stage": stage,
                "done": int(processed),
                "total": int(total),
            })
            problems = int(payload.get("problems") or 0)
            payload["errors"] = problems
            core.emit_gui_event("progress_update", **payload)
            now = time.monotonic()
            if self.quiet or now - self._last_console_progress.get(
                    stage, 0.0) < 1.0:
                return
            self._last_console_progress[stage] = now
            suffix = f"{int(processed):,}/{int(total):,}"
            if problems:
                suffix += f" · 受影响文件 {problems:,}"
            if payload.get("unverifiable"):
                suffix += f" · 不可核验 {int(payload['unverifiable']):,}"
            print(
                f"[{_STAGES[stage][0]}/{_STAGES_TOTAL}] "
                f"{_STAGES[stage][1]} | {suffix}",
                flush=True,
            )

    def event(self, event: str, **payload: object) -> None:
        with self._lock:
            clean = dict(payload)
            stage = str(clean.get("stage") or "")
            if event in ("stage_started", "stage_restarted"):
                self._start_stage(stage)
            elif event == "stage_skipped" and stage in _STAGES:
                index, name = _STAGES[stage]
                reason = str(clean.get("reason") or "当前配置")
                core.emit_gui_event(
                    "progress_skip",
                    stage_idx=index,
                    stage_total=_STAGES_TOTAL,
                    name=name,
                    reason=reason,
                )
                if not self.quiet:
                    print(
                        f"[{index}/{_STAGES_TOTAL}] {name} 跳过：{reason}",
                        flush=True,
                    )
            elif event == "stage_finished" and stage in _STAGES:
                index, name = _STAGES[stage]
                elapsed = time.monotonic() - self._stage_started_at.get(
                    stage, time.monotonic())
                processed = int(clean.get("processed") or 0)
                problems = int(clean.get("problems") or 0)
                summary = f"处理 {processed:,}"
                if problems:
                    summary += f" · 受影响文件 {problems:,}"
                core.emit_gui_event(
                    "progress_finish",
                    stage_idx=index,
                    stage_total=_STAGES_TOTAL,
                    name=name,
                    summary=summary,
                    elapsed=elapsed,
                )
                if not self.quiet:
                    print(
                        f"[{index}/{_STAGES_TOTAL}] {name} 完成：{summary}",
                        flush=True,
                    )
            elif event == "stage_failed" and stage in _STAGES:
                index, name = _STAGES[stage]
                processed = int(clean.get("processed") or 0)
                total = int(clean.get("total") or 0)
                not_processed = int(clean.get("not_processed") or 0)
                tool = str(clean.get("tool") or "外部工具")
                summary = (
                    f"{tool} 连续故障，核验已停止 · "
                    f"已处理 {processed:,}/{total:,} · "
                    f"未处理 {not_processed:,}"
                )
                core.emit_gui_event(
                    "progress_fail",
                    stage_idx=index,
                    stage_total=_STAGES_TOTAL,
                    name=name,
                    summary=summary,
                    done=processed,
                    total=total,
                )
                if not self.quiet:
                    print(
                        f"[{index}/{_STAGES_TOTAL}] {name} 失败：{summary}",
                        file=sys.stderr,
                        flush=True,
                    )
            if event == "threshold_reached" and not self.quiet:
                decision_text = {
                    "continue_waiting": "继续等待",
                    "skip_and_record": "跳过并记录",
                    "stop_and_resume": "停止并保留结果",
                }[self.default_decision]
                print(
                    "警告：单文件无可观察进展达到阈值："
                    f"{clean.get('file')}（{clean.get('threshold_seconds')} 秒）；"
                    f"无人操作时默认：{decision_text}",
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


class VerificationCommandRouter:
    """把通用控制消息收窄到核验实际支持的进程内动作。"""

    def __init__(
        self,
        control: verifyrun.UnifiedVerificationControl,
        *,
        on_receipt: Callable[[dbrun.ControlReceipt], None] | None = None,
    ) -> None:
        self.control = control
        self._on_receipt = on_receipt

    def route(self, command: dbrun.ControlCommand) -> dbrun.ControlReceipt:
        if command.action == "pause":
            accepted, reason = self.control.request_pause()
        elif command.action == "continue":
            accepted, reason = self.control.request_continue()
        elif command.action == "stop":
            accepted, reason = self.control.request_stop()
        elif command.action == "timeout_decision":
            assert command.worker_pid is not None
            assert command.decision is not None
            accepted, reason = self.control.request_timeout_decision(
                command.worker_pid,
                command.decision,
            )
        elif command.action == "save_exit":
            accepted, reason = False, "verification_not_resumable"
        else:
            accepted, reason = False, "unsupported_action"
        receipt = dbrun.ControlReceipt(
            command.sequence,
            command.action,
            accepted,
            reason,
        )
        if self._on_receipt is not None:
            try:
                self._on_receipt(receipt)
            except Exception:
                pass
        return receipt


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "档案数据核验：核对全部文件状态，并可选复检哈希、格式、容器结构与 RAW 解码；"
            "封存快照保持只读，也支持无数据库直接核验"
        ),
    )
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument(
        "--snapshot", help="作为核验基准的封存快照")
    input_group.add_argument(
        "--direct", action="store_true",
        help="无数据库直接核验；只检查当前文件，不宣称哈希一致",
    )
    parser.add_argument(
        "--root", action="append",
        help=(
            "当前根目录；快照原路径未变时可省略，路径变化或直接核验时可重复指定"
        ),
    )
    parser.add_argument(
        "--hash", choices=("off", "sample", "all"),
        help="哈希复检范围；数据库模式默认抽样，直接模式固定关闭",
    )
    parser.add_argument(
        "--hash-sample-percent", type=float, help="哈希复检抽样比例")
    parser.add_argument(
        "--format", choices=("off", "sample", "all"), default="off",
        help="格式校验范围；默认关闭",
    )
    parser.add_argument(
        "--format-sample-percent", type=float, help="格式校验抽样比例")
    parser.add_argument(
        "--format-tool", action="append",
        choices=verifyrun.FORMAT_TOOL_IDS,
        help=(
            "格式校验工具，可重复指定；启用格式校验且省略本参数时使用全部工具"
        ),
    )
    parser.add_argument(
        "--raw-deep-validation", action="store_true",
        help="使用隔离的 rawpy/LibRaw 子进程实际解码 RAW；默认关闭",
    )
    parser.add_argument(
        "--timeout-action", choices=_TIMEOUT_ACTIONS,
        help="单文件达到动态阈值后的默认处置；停止会保留已完成结果并生成报告，但不能跨重启续传",
    )
    parser.add_argument(
        "--hash-timeout-seconds", type=float, help="覆盖哈希无进展阈值")
    parser.add_argument(
        "--format-timeout-seconds", type=float,
        help="覆盖格式校验无进展阈值")
    parser.add_argument(
        "--raw-timeout-seconds", type=float, help="覆盖 RAW 解码无进展阈值")
    parser.add_argument(
        "--show-current-file", action="store_true",
        help="在结构化进度中发送当前相对路径；默认关闭",
    )
    parser.add_argument(
        "--control-stdin", action="store_true",
        help="从标准输入接收 daisy-control-v1 UTF-8 JSONL；不支持保存并退出",
    )
    parser.add_argument(
        "--report-dir", help="报告输出目录；默认 Output/Reports")
    parser.add_argument("--powershell-path", help="PowerShell 可执行文件路径")
    parser.add_argument("--exiftool-path", help="ExifTool 可执行文件路径")
    parser.add_argument("--ffprobe-path", help="ffprobe 可执行文件路径")
    parser.add_argument("--sevenzip-path", help="7-Zip 可执行文件路径")
    parser.add_argument(
        "--force", action="store_true",
        help="允许缺少文件名指纹的旧库继续；指纹不一致仍拒绝",
    )
    parser.add_argument(
        "--quiet", action="store_true", help="不显示常规控制台进度")
    return parser


def verification_options(args: argparse.Namespace) \
        -> verifyrun.VerificationOptions:
    hash_mode = args.hash or ("off" if args.direct else "sample")
    selected_format_tools = tuple(
        args.format_tool or verifyrun.FORMAT_TOOL_IDS)
    if hash_mode != "sample" and args.hash_sample_percent is not None:
        raise core.PreflightError(
            "--hash-sample-percent 仅用于 --hash sample")
    if args.format != "sample" and args.format_sample_percent is not None:
        raise core.PreflightError(
            "--format-sample-percent 仅用于 --format sample")
    if hash_mode == "off" and (
            args.powershell_path or args.hash_timeout_seconds is not None):
        raise core.PreflightError(
            "哈希复检关闭时不能指定 PowerShell 路径或哈希超时阈值")
    if args.format == "off" and args.format_tool:
        raise core.PreflightError(
            "--format-tool 仅用于已启用的 --format")
    if args.format == "off" and any((
        args.exiftool_path,
        args.ffprobe_path,
        args.sevenzip_path,
        args.format_timeout_seconds is not None,
    )):
        raise core.PreflightError(
            "格式校验关闭时不能指定格式工具路径或格式校验超时阈值")
    selected_set = set(selected_format_tools) if args.format != "off" else set()
    for tool_id, path in (
        ("exiftool", args.exiftool_path),
        ("ffprobe", args.ffprobe_path),
        ("sevenzip", args.sevenzip_path),
    ):
        if path and tool_id not in selected_set:
            raise core.PreflightError(
                f"未选择 {tool_id} 时不能指定对应工具路径")
    if hash_mode == "off" and args.format == "off" \
            and not args.raw_deep_validation \
            and args.timeout_action is not None:
        raise core.PreflightError(
            "仅核对文件状态时不能指定超时处置")
    try:
        return verifyrun.VerificationOptions(
            hash_mode=hash_mode,
            hash_sample_percent=(
                1.0 if args.hash_sample_percent is None
                else args.hash_sample_percent
            ),
            format_mode=args.format,
            format_sample_percent=(
                10.0 if args.format_sample_percent is None
                else args.format_sample_percent
            ),
            format_tools=selected_format_tools,
            timeout_decision=args.timeout_action or "continue_waiting",
            hash_timeout_seconds=args.hash_timeout_seconds,
            format_timeout_seconds=args.format_timeout_seconds,
            raw_deep_validation=args.raw_deep_validation,
            raw_timeout_seconds=args.raw_timeout_seconds,
            show_current_file=args.show_current_file,
        )
    except (TypeError, ValueError) as exc:
        raise core.PreflightError(str(exc)) from exc


def _tool_overrides(args: argparse.Namespace) -> dict[str, str | None]:
    return {
        "powershell": args.powershell_path,
        "exiftool": args.exiftool_path,
        "ffprobe": args.ffprobe_path,
        "sevenzip": args.sevenzip_path,
    }


def _section_state_label(section: dict[str, object]) -> str:
    """把技术状态转换为不会误导用户的简短显示文字。"""
    return {
        "executed": "已执行",
        "unavailable": "NULL（缺少可用基准）",
        "stopped": "未完成",
        "failed": "失败",
    }.get(str(section.get("state") or ""), "NULL（未执行）")


def _print_report_summary(
    report: dict[str, object],
    publication: verifyrun.VerificationPublication,
) -> None:
    sections = report["sections"]
    stat = sections["stat"]
    hashed = sections["hash"]
    formatted = sections["format"]
    raw = sections["raw"]
    def problem_files(section: dict[str, object]) -> str:
        if section.get("state") not in ("executed", "stopped", "failed"):
            return "NULL"
        explicit = section.get("problem_files")
        value = (
            int(explicit)
            if isinstance(explicit, int) and not isinstance(explicit, bool)
            else len(section.get("problems") or [])
        )
        return f"{value:,}"

    def count(section: dict[str, object], key: str) -> str:
        if section.get("state") not in ("executed", "stopped", "failed"):
            return "NULL"
        return f"{int(section.get(key) or 0):,}"

    print(
        f"文件状态：已处理 {count(stat, 'checked')}｜"
        f"受影响文件 {problem_files(stat)}")
    print(
        f"哈希：{_section_state_label(hashed)}｜"
        f"已处理 {count(hashed, 'processed')}｜"
        f"不可核验 {count(hashed, 'unverifiable')}｜"
        f"受影响文件 {problem_files(hashed)}")
    print(
        f"格式校验：{_section_state_label(formatted)}｜"
        f"已处理 {count(formatted, 'processed')}｜"
        f"不支持 {count(formatted, 'unsupported')}｜"
        f"受影响文件 {problem_files(formatted)}")
    print(
        f"RAW 深度校验：{_section_state_label(raw)}｜"
        f"已处理 {count(raw, 'processed')}｜"
        f"不支持 {count(raw, 'unsupported')}｜"
        f"受影响文件 {problem_files(raw)}")
    print(f"Markdown 报告：{publication.markdown_path}")
    print(f"技术证据：{publication.json_path}")


def main(argv: list[str] | None = None) -> int:
    core.force_utf8_io()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.direct and not args.root:
            raise core.PreflightError(
                "无数据库直接核验必须至少指定一个 --root")
        if args.direct and args.hash not in (None, "off"):
            raise core.PreflightError(
                "无数据库直接核验没有可比较的哈希基准；只能使用 --hash off")
        if args.direct and args.force:
            raise core.PreflightError("无数据库直接核验不能使用 --force")
        options = verification_options(args)
    except core.PreflightError as exc:
        print(f"启动失败：{exc}", file=sys.stderr)
        return 2

    reporter = VerificationReporter(
        quiet=args.quiet,
        default_decision=options.timeout_decision,
    )
    control = verifyrun.UnifiedVerificationControl()
    router = VerificationCommandRouter(
        control, on_receipt=reporter.control_receipt)
    inbox = None
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
            print(f"控制输入无法启动：{exc}", file=sys.stderr)
            return 2

    try:
        report = verifyrun.run_unified_verification(
            None if args.direct else args.snapshot,
            list(args.root or ()),
            options=options,
            force=args.force,
            tools=_tool_overrides(args),
            control=control,
            on_progress=reporter.progress,
            on_event=reporter.event,
        )
    except KeyboardInterrupt:
        # ``request_stop`` 只接受统一状态模型定义的来源；键盘中断在
        # 用户语义上仍是显式停止。不要传入未登记来源而掩盖原始中断。
        control.request_stop("user")
        print("\n核验已中断；输入快照未修改。", file=sys.stderr)
        return 130
    except core.PreflightError as exc:
        print(f"核验启动／输入检查失败：{exc}", file=sys.stderr)
        return 2
    except (OSError, sqlite3.Error) as exc:
        print(f"核验失败：{exc}", file=sys.stderr)
        return 1
    finally:
        if inbox is not None:
            inbox.stop()

    try:
        publication = verifyrun.publish_verification_report(
            report, args.report_dir)
    except (core.PreflightError, OSError) as exc:
        print(f"核验完成，但报告发布失败：{exc}", file=sys.stderr)
        return 1
    reporter.event(
        "report_published",
        json_path=publication.json_path,
        markdown_path=publication.markdown_path,
        conclusion=report["conclusion"],
    )
    _print_report_summary(report, publication)
    conclusion = str(report.get("conclusion") or "")
    if conclusion == "passed":
        print("结论：在本次核验范围内未发现问题。")
        return 0
    if conclusion == "stopped":
        print(
            "结论：核验已停止；报告只代表停止前完成的范围。",
            file=sys.stderr,
        )
        return 130
    if conclusion == "failed":
        print(
            "结论：外部工具连续故障，核验已停止；报告只代表已处理范围。",
            file=sys.stderr,
        )
        return 1
    if conclusion == "incomplete":
        print(
            "结论：证据不完整，不能宣称内容一致；详见报告。",
            file=sys.stderr,
        )
        return 1
    print("结论：发现需要处理或复核的问题。", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
