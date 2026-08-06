r"""DBS-30 统一核验：stat、内容哈希与格式校验的只读编排入口。

旧 ``check-hash``／``check-format`` 命令继续保持 v1.5.1 参数和输出；本入口只读
消费 schema 3／4 封存快照，并发布一份 Markdown 人读报告和一份 JSON 技术证据。
统一核验支持进程内暂停／继续／停止，但不提供跨重启续传，因而明确拒绝
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

import Script_DAISY_Lib_DBS_01_Core as core
import Script_DAISY_Lib_DBS_09_Run as dbrun
import Script_DAISY_Lib_DBS_11_Verify_Run as verifyrun


_STAGES = {
    "stat": (1, "文件状态"),
    "hash": (2, "内容哈希"),
    "format": (3, "格式校验"),
    "raw": (4, "RAW 深检"),
}
_STAGES_TOTAL = len(_STAGES)
_TIMEOUT_ACTIONS = (
    "continue_waiting", "skip_and_record", "stop_and_resume",
)


class VerificationReporter:
    """把统一核验事件映射为 GUI 协议和紧凑控制台进度。"""

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
                suffix += f" · 问题 {problems:,}"
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
                    summary += f" · 问题 {problems:,}"
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
            if event == "threshold_reached" and not self.quiet:
                decision_text = {
                    "continue_waiting": "继续等待",
                    "skip_and_record": "跳过并记录",
                    "stop_and_resume": "停止（核验不可跨重启续传）",
                }[self.default_decision]
                print(
                    "!! 单文件无可观察进展达到阈值："
                    f"{clean.get('file')}（{clean.get('threshold_seconds')}s）；"
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
            "DBS-30 统一核验：全量文件状态＋可选内容哈希／格式校验；"
            "输入快照只读"
        ),
    )
    parser.add_argument("--snapshot", required=True)
    parser.add_argument(
        "--root", action="append", required=True,
        help="当前根目录；单根可直接给路径，多根逐项使用 label=路径",
    )
    parser.add_argument(
        "--hash", choices=("off", "sample", "all"), default="sample",
        help="内容哈希范围；默认抽样",
    )
    parser.add_argument("--hash-sample-percent", type=float)
    parser.add_argument(
        "--format", choices=("off", "sample", "all"), default="off",
        help="格式校验范围；默认关闭",
    )
    parser.add_argument("--format-sample-percent", type=float)
    parser.add_argument(
        "--raw-deep-validation", action="store_true",
        help="在格式范围内用隔离 rawpy／LibRaw 实际解码 RAW；默认关闭",
    )
    parser.add_argument(
        "--timeout-action", choices=_TIMEOUT_ACTIONS,
        help="单文件达到动态阈值后的默认处置；默认继续等待",
    )
    parser.add_argument("--hash-timeout-seconds", type=float)
    parser.add_argument("--format-timeout-seconds", type=float)
    parser.add_argument("--raw-timeout-seconds", type=float)
    parser.add_argument(
        "--show-current-file", action="store_true",
        help="发送正在核验的相对文件路径；默认关闭",
    )
    parser.add_argument(
        "--control-stdin", action="store_true",
        help="从 stdin 接收 daisy-control-v1 UTF-8 JSONL；不支持保存退出",
    )
    parser.add_argument("--report-dir")
    parser.add_argument("--powershell-path")
    parser.add_argument("--exiftool-path")
    parser.add_argument("--ffprobe-path")
    parser.add_argument("--sevenzip-path")
    parser.add_argument(
        "--force", action="store_true",
        help="文件名指纹缺失时仍继续（不符仍拒绝）",
    )
    parser.add_argument("--quiet", action="store_true")
    return parser


def verification_options(args: argparse.Namespace) \
        -> verifyrun.VerificationOptions:
    if args.hash != "sample" and args.hash_sample_percent is not None:
        raise core.PreflightError(
            "--hash-sample-percent 仅用于 --hash sample")
    if args.format != "sample" and args.format_sample_percent is not None:
        raise core.PreflightError(
            "--format-sample-percent 仅用于 --format sample")
    if args.hash == "off" and (
            args.powershell_path or args.hash_timeout_seconds is not None):
        raise core.PreflightError(
            "哈希关闭时不接受 PowerShell 路径或哈希 timeout")
    if args.format == "off" and any((
        args.exiftool_path,
        args.ffprobe_path,
        args.sevenzip_path,
        args.format_timeout_seconds is not None,
        args.raw_deep_validation,
        args.raw_timeout_seconds is not None,
    )):
        raise core.PreflightError(
            "格式校验关闭时不接受格式工具、格式 timeout 或 RAW 深检")
    if args.hash == "off" and args.format == "off" \
            and args.timeout_action is not None:
        raise core.PreflightError(
            "仅执行文件状态时不接受 timeout 默认处置")
    try:
        return verifyrun.VerificationOptions(
            hash_mode=args.hash,
            hash_sample_percent=(
                1.0 if args.hash_sample_percent is None
                else args.hash_sample_percent
            ),
            format_mode=args.format,
            format_sample_percent=(
                10.0 if args.format_sample_percent is None
                else args.format_sample_percent
            ),
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


def _print_report_summary(
    report: dict[str, object],
    publication: verifyrun.VerificationPublication,
) -> None:
    sections = report["sections"]
    stat = sections["stat"]
    hashed = sections["hash"]
    formatted = sections["format"]
    raw = sections["raw"]
    print(
        f"文件状态：核对 {int(stat.get('checked') or 0):,} | "
        f"问题 {len(stat.get('problems') or []):,}")
    print(
        f"内容哈希：{hashed.get('state')} | "
        f"核对 {int(hashed.get('checked') or 0):,} | "
        f"不可核验 {int(hashed.get('unverifiable') or 0):,} | "
        f"问题 {len(hashed.get('problems') or []):,}")
    print(
        f"格式校验：{formatted.get('state')} | "
        f"核对 {int(formatted.get('checked') or 0):,} | "
        f"不支持 {int(formatted.get('unsupported') or 0):,} | "
        f"问题 {len(formatted.get('problems') or []):,}")
    print(
        f"RAW 深检：{raw.get('state')} | "
        f"核对 {int(raw.get('checked') or 0):,} | "
        f"不支持 {int(raw.get('unsupported') or 0):,} | "
        f"问题 {len(raw.get('problems') or []):,}")
    print(f"人读报告：{publication.markdown_path}")
    print(f"技术证据：{publication.json_path}")


def main(argv: list[str] | None = None) -> int:
    core.force_utf8_io()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
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
            args.snapshot,
            list(args.root),
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
        print("结论：在本次覆盖口径内未发现问题。")
        return 0
    if conclusion == "stopped":
        print(
            "结论：核验已停止；报告只代表停止前完成的范围。",
            file=sys.stderr,
        )
        return 130
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
