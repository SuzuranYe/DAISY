r"""Script_DAISY_Module_DBS_41_Export_Report：DBS-41 档案数据解析入口。

``parse-db --database`` 使用 v1.6.0 的只读识别、选择计划和四格式流式写入器；
``export-report --snapshot/--diff`` 继续调用 v1.5.1 冻结写入器。两个入口共用本编排
模块，但旧 CLI、退出码、文件顺序和供调用方使用的函数名不切换。
"""
from __future__ import annotations

import argparse
import os
import sys
import time

_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
_LIB_DIR = os.path.join(os.path.dirname(_MODULE_DIR), "Lib")
sys.path.insert(0, _LIB_DIR)
import Script_DAISY_Lib_DBS_01_Core as core
import Script_DAISY_Lib_DBS_07_Parse as dbparse
import Script_DAISY_Lib_DBS_16_Parse_Run as parserun


export_snapshot = dbparse.export_snapshot
export_diff = dbparse.export_diff

# v1.5.1 测试和第三方调用可能引用这两个既有内部符号；继续保留原有行为。
_XLSX_MAX_CELL_CHARS = dbparse._XLSX_MAX_CELL_CHARS
_excel_row = dbparse._excel_row


class _ParseCliProgress:
    """把流式解析进度同时投影到终端和既有 GUI 事件协议。"""

    def __init__(self, module_total: int) -> None:
        self.module_total = max(1, int(module_total))
        self._last_console = 0.0
        self._last_module: str | None = None
        core.emit_gui_event(
            "progress_start",
            stage_idx=1,
            stage_total=1,
            name="档案数据解析",
        )

    def __call__(self, item: parserun.ParseProgress) -> None:
        completed = 0
        if item.phase == "module":
            completed = max(0, item.module_index - 1)
            if "已完成" in item.message:
                completed = item.module_index
        elif item.phase == "publish":
            completed = self.module_total
        core.emit_gui_event(
            "progress_update",
            done=completed,
            total=self.module_total,
            rows_done=item.rows_done,
            current=item.message,
            errors=0,
        )

        now = time.monotonic()
        important = (
            item.phase != "module"
            or item.module_id != self._last_module
            or "已完成" in item.message
            or now - self._last_console >= 1.0
        )
        if not important:
            return
        self._last_console = now
        self._last_module = item.module_id
        if item.phase == "module":
            print(
                f"[{item.module_index}/{self.module_total}] "
                f"{item.message}",
                flush=True,
            )
        else:
            print(item.message, flush=True)

    def finish(self) -> None:
        core.emit_gui_event(
            "progress_update",
            done=self.module_total,
            total=self.module_total,
            errors=0,
        )
        core.emit_gui_event(
            "progress_finish",
            stage_idx=1,
            stage_total=1,
            name="档案数据解析",
            summary="导出完成并发布",
        )


def build_parser(*, database_mode: bool = False) -> argparse.ArgumentParser:
    if not database_mode:
        parser = argparse.ArgumentParser(
            description="旧版报告导出兼容入口（只读输入）",
            epilog=(
                "迁移提示：如需自动识别数据库、选择数据模块或导出 HTML/JSONL，"
                "请使用 parse-db --database；本入口继续生成冻结报告。"
            ),
        )
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--snapshot", help="封存快照 (.sqlite)")
        group.add_argument("--diff", help="Diff 数据库 (.sqlite)")
        parser.add_argument(
            "--output-dir", default="Output/Reports",
            help="报告输出目录；默认 Output/Reports")
        return parser

    parser = argparse.ArgumentParser(
        description="档案数据解析：只读识别数据库，并按所选数据模块和格式导出")
    parser.add_argument(
        "--database",
        required=True,
        help="由 DAISY 自动识别的封存快照或 Diff 数据库 (.sqlite)",
    )
    parser.add_argument(
        "--preset",
        choices=tuple(sorted(dbparse.PARSE_PRESETS)),
        default=None,
        help="导出范围；命令行省略时使用 human-summary（摘要内容）",
    )
    parser.add_argument(
        "--include",
        action="append",
        default=None,
        metavar="MODULE[,MODULE...]",
        help=("数据模块 ID；使用摘要内容或全部内容范围时追加，使用自定义"
              "范围时作为完整选择；可重复或用逗号分隔"),
    )
    parser.add_argument(
        "--format",
        dest="formats",
        action="append",
        default=None,
        metavar="FORMAT[,FORMAT...]",
        help="输出格式：html/xlsx/csv/jsonl；可重复或以逗号分隔，命令行默认 html",
    )
    parser.add_argument(
        "--output-dir", default="Output/Reports",
        help="导出目录；默认 Output/Reports")
    return parser


def _run_legacy(args: argparse.Namespace) -> int:
    try:
        if args.snapshot:
            result = export_snapshot(args.snapshot, args.output_dir)
        else:
            result = export_diff(args.diff, args.output_dir)
    except core.PreflightError as exc:
        print(f"导出失败：{exc}", file=sys.stderr)
        return 2
    print(f"导出目录：{result['folder']}")
    for filename in result["files"]:
        print(f"  {filename}")
    return 0


def _run_parse(args: argparse.Namespace) -> int:
    inspection = dbparse.inspect_parse_database(args.database)
    plan = dbparse.plan_parse_export(
        inspection,
        preset=args.preset or "human-summary",
        include=args.include or (),
        formats=args.formats or ("html",),
    )
    descriptor = inspection.descriptor
    type_label = (
        "封存快照" if descriptor.database_type == "snapshot"
        else "Diff 数据库"
    )
    counts = inspection.module_state_counts
    print(
        f"数据库已解析：{type_label} · 数据库结构版本 {descriptor.schema_version} · "
        f"{dbparse.compatibility_mode_label(inspection.compatibility_mode)}"
    )
    print(
        "数据模块状态："
        f"可导出 {counts.get('available', 0)} · "
        f"0 条记录 {counts.get('empty', 0)} · "
        f"无可用记录 {counts.get('unavailable', 0)} · "
        f"版本不兼容 {counts.get('incompatible', 0)} · "
        f"结构异常 {counts.get('invalid', 0)}"
    )
    module_titles = {
        module.spec.module_id: module.spec.title for module in inspection.modules
    }
    format_titles = {
        "html": "HTML 阅读报告",
        "xlsx": "Excel 工作簿",
        "csv": "CSV 数据表",
        "jsonl": "JSONL 数据流",
    }
    print("数据模块：" + "、".join(
        module_titles.get(module_id, module_id) for module_id in plan.module_ids))
    print("输出格式：" + "、".join(
        format_titles.get(format_id, format_id) for format_id in plan.format_ids))
    for notice in plan.privacy_notices:
        print(f"隐私提示：{notice}")

    progress = _ParseCliProgress(len(plan.module_ids))
    result = parserun.export_parse_report(
        args.database,
        args.output_dir,
        plan,
        progress_callback=progress,
    )
    progress.finish()
    print(f"导出目录：{result.report_directory}")
    print(f"运行清单：{result.manifest_path}")
    preferred = None
    for format_id, filename in (
        ("html", "Report.html"),
        ("xlsx", "Report_Excel.xlsx"),
    ):
        if format_id in plan.format_ids:
            preferred = os.path.join(result.report_directory, filename)
            break
    if preferred is not None:
        print(f"建议打开：{preferred}")
    print("已生成：")
    for artifact in result.artifacts:
        print(f"  {artifact.relative_path}")
    return 0


def main() -> int:
    core.force_utf8_io()
    database_mode = "--parse-db-mode" in sys.argv[1:]
    if database_mode:
        sys.argv.remove("--parse-db-mode")
    args = build_parser(database_mode=database_mode).parse_args()
    if not database_mode:
        return _run_legacy(args)
    try:
        return _run_parse(args)
    except parserun.ParseExportCancelled:
        print("档案数据解析已取消；未完成结果不会发布。", file=sys.stderr)
        return 130
    except KeyboardInterrupt:
        print("档案数据解析已由用户中断；未完成结果不会发布。", file=sys.stderr)
        return 130
    except (core.PreflightError, OSError, ValueError) as exc:
        print(f"档案数据解析失败：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
