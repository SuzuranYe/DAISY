"""Script_DAISY_Module_Environment_Check：运行环境检测。

只检查 DAISY 所需工具、版本、冒烟样本、只读断言与 SHA-256 自检；不读取
源档案，也不执行性能测试。
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import sys
import tempfile
import time

_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
_LIB_DIR = os.path.join(os.path.dirname(_MODULE_DIR), "Lib")
sys.path.insert(0, _LIB_DIR)
import Script_DAISY_Lib_Snapshot_Core as core
import Script_DAISY_Lib_File_Hash as dbh
import Script_DAISY_Lib_Environment_Capabilities as envcap
import Script_DAISY_Lib_Storage_Core as storage_core
import Script_DAISY_Lib_Storage_Windows as storage_windows
import Script_DAISY_Lib_Storage_Smartctl as smartctl


_TOOL_DISPLAY_NAMES = {
    "python": "Python",
    "exiftool": "ExifTool",
    "ffprobe": "ffprobe",
    "sevenzip": "7-Zip",
    "powershell": "PowerShell",
    "smartctl": "smartctl",
}
_GUI_INSTALLABLE_TOOLS = frozenset(
    ("exiftool", "ffprobe", "sevenzip", "smartctl"))
_CAPABILITY_STATE_LABELS = {
    "available": "可用",
    "unavailable": "不可用",
    "incompatible": "不兼容",
    "crashed": "探测进程异常退出",
    "timeout": "探测超时",
}


def inspect_local_tools(
    explicit: dict[str, str | None],
) -> tuple[dict[str, dict], list[dict[str, object]]]:
    """独立检查每项工具，使 GUI 能一次列全本机版本与缺失项。"""
    tools: dict[str, dict] = {
        "python": core.resolved_tool_info(
            "python", sys.executable, explicit=False,
            version=platform.python_version(),
        ),
    }
    issues: list[dict[str, object]] = []
    for name in ("exiftool", "ffprobe", "sevenzip"):
        try:
            path = core.discover_tool(name, explicit.get(name))
            tools[name] = core.resolved_tool_info(
                name, path, explicit=bool(explicit.get(name)))
        except core.PreflightError as exc:
            issues.append({
                "name": name,
                "display": _TOOL_DISPLAY_NAMES[name],
                "installable": name in _GUI_INSTALLABLE_TOOLS,
                "reason": str(exc),
            })
    try:
        ps_path, ps_version = dbh.discover_powershell(
            explicit.get("powershell"))
        tools["powershell"] = core.resolved_tool_info(
            "powershell", ps_path,
            explicit=bool(explicit.get("powershell")),
            version=ps_version,
        )
    except core.PreflightError as exc:
        issues.append({
            "name": "powershell",
            "display": _TOOL_DISPLAY_NAMES["powershell"],
            "installable": False,
            "reason": str(exc),
        })
    try:
        smartctl_path = smartctl.find_smartctl(explicit.get("smartctl"))
        smartctl_version = smartctl.require_supported_version(
            smartctl.version(smartctl_path))
        tools["smartctl"] = core.resolved_tool_info(
            "smartctl", str(smartctl_path),
            explicit=bool(explicit.get("smartctl")),
            version=smartctl_version,
        )
    except storage_core.DaisySmartError as exc:
        issues.append({
            "name": "smartctl",
            "display": _TOOL_DISPLAY_NAMES["smartctl"],
            "installable": True,
            "reason": str(exc),
        })
    return tools, issues


def inspect_runtime_capabilities() -> dict[str, dict[str, object]]:
    """探测可选运行能力；不可用能力不让基础环境检测整体失败。"""
    return {
        capability_id: capability.as_dict()
        for capability_id, capability in (
            envcap.probe_runtime_capabilities()).items()
    }


def compact_console_version(name: str, version: object) -> str:
    """控制台清单中的 PowerShell 只显示易读的主次版本。"""
    text = str(version or "版本未知").strip()
    if name != "powershell":
        return text
    match = re.search(r"\d+(?:\.\d+)?", text)
    return match.group(0) if match else text


def write_environment_report(
    output_dir: str, report: dict[str, object],
) -> str:
    """按明确请求写出 UTF-8 JSON 环境报告。"""
    os.makedirs(output_dir, exist_ok=True)
    out = os.path.join(
        output_dir,
        f"Env_Check_{time.strftime('%Y-%m-%d_%H-%M-%S')}.json",
    )
    with open(out, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    return out


def main() -> int:
    core.force_utf8_io()
    ap = argparse.ArgumentParser(
        description="运行环境检测：检测 Python、外部工具和 RAW 解码能力")
    ap.add_argument(
        "--output-dir", default="Output/Reports",
        help="环境报告输出目录；默认 Output/Reports")
    ap.add_argument(
        "--export-report", action="store_true",
        help="导出 JSON 环境检测报告；默认不导出")
    ap.add_argument("--exiftool-path", help="ExifTool 可执行文件路径")
    ap.add_argument("--ffprobe-path", help="ffprobe 可执行文件路径")
    ap.add_argument("--sevenzip-path", help="7-Zip 可执行文件路径")
    ap.add_argument("--powershell-path", help="PowerShell 可执行文件路径")
    ap.add_argument("--smartctl-path", help="smartctl 可执行文件路径")
    args = ap.parse_args()

    print("== DAISY 运行环境检测 ==")
    prog = core.Progress(1, 1, "运行环境检测")
    explicit = {
        "exiftool": args.exiftool_path,
        "ffprobe": args.ffprobe_path,
        "sevenzip": args.sevenzip_path,
        "powershell": args.powershell_path,
        "smartctl": args.smartctl_path,
    }
    inventory, issues = inspect_local_tools(explicit)
    runtime_capabilities = inspect_runtime_capabilities()
    core.emit_gui_event(
        "environment_inventory",
        tools=inventory,
        missing=issues,
        capabilities=runtime_capabilities,
    )
    core.emit_gui_event(
        "runtime_capabilities",
        capabilities=runtime_capabilities,
    )
    if inventory:
        core.emit_gui_event("tools_detected", tools=inventory)
    print("本机工具版本：")
    for name in (
            "python", "exiftool", "ffprobe", "sevenzip", "powershell",
            "smartctl"):
        info = inventory.get(name)
        if info:
            print(
                f"  {_TOOL_DISPLAY_NAMES[name]:<10} "
                f"{compact_console_version(name, info['version']):<12} "
                f"{info['path']}")
    print("可选运行能力：")
    for capability in runtime_capabilities.values():
        state = str(capability.get("state") or "unknown")
        version = str(capability.get("version") or "")
        reason = str(capability.get("reason") or "")
        summary = version if state == "available" else reason
        print(
            f"  {capability.get('title', capability.get('id', ''))}："
            f"{_CAPABILITY_STATE_LABELS.get(state, '未知')}"
            + (f" · {summary}" if summary else "")
        )
    checks: dict[str, object] = {
        "sha256_nist": "not_run",
        "python_runtime": "passed",
        "tool_smoke_readonly": "not_run",
        "powershell_get_filehash": "not_run",
        "smartctl_readonly_scan": "not_run",
        "windows_storage_inventory": "not_run",
        "rawpy_libraw": runtime_capabilities.get(
            envcap.RAW_CAPABILITY_ID, {}).get("state", "unavailable"),
    }

    def finish_report(
        detected_tools: dict[str, dict],
        *,
        check_issues: list[dict[str, object]],
    ) -> str | None:
        report = {
            **core.report_metadata("运行环境检测"),
            "generated_at_utc": core.now_utc_iso(),
            "scanner_version": core.SCANNER_VERSION,
            "tools": detected_tools,
            "runtime_capabilities": runtime_capabilities,
            "missing": issues,
            "check_issues": check_issues,
            "checks": checks,
        }
        if not args.export_report:
            return None
        return write_environment_report(args.output_dir, report)

    def complete(
        detail: str,
        detected_tools: dict[str, dict],
        *,
        check_issues: list[dict[str, object]],
        returncode: int,
    ) -> int:
        try:
            out = finish_report(
                detected_tools, check_issues=check_issues)
        except OSError as exc:
            prog.finish("环境检测完成；报告导出失败")
            print(f"报告导出失败：{exc}", file=sys.stderr)
            return 2
        prog.finish(detail)
        if out:
            print(f"\n报告：{out}")
        else:
            print("\n检测报告：未导出")
        return returncode

    if issues:
        print("未就绪的工具：", file=sys.stderr)
        for issue in issues:
            print(
                f"  {issue['display']}：{issue['reason']}",
                file=sys.stderr,
            )
        return complete(
            "环境检测完成；已列出缺失工具",
            inventory,
            check_issues=[],
            returncode=1,
        )
    try:
        tools = core.run_preflight(
            {"exiftool": args.exiftool_path, "ffprobe": args.ffprobe_path,
             "sevenzip": args.sevenzip_path}, output_dir=None)
        tools["python"] = inventory["python"]
        ps_path, ps_version = dbh.discover_powershell(args.powershell_path)
        tools["powershell"] = core.resolved_tool_info(
            "powershell", ps_path, explicit=bool(args.powershell_path),
            version=ps_version)
        smart_scan = smartctl.scan(args.smartctl_path)
        if not smart_scan.devices:
            raise core.PreflightError(
                "smartctl 设备枚举未发现硬盘；请检查权限、驱动或转接盒。")
        tools["smartctl"] = core.resolved_tool_info(
            "smartctl", smart_scan.executable,
            explicit=bool(args.smartctl_path),
            version=smart_scan.version,
        )
        storage_inventory = storage_windows.read_inventory(
            detailed=False,
            powershell=args.powershell_path,
            timeout=60,
        )
        if not storage_inventory.records:
            raise core.PreflightError("Windows 存储查询未发现硬盘。")
        with tempfile.TemporaryDirectory() as td:
            sample = os.path.join(td, "powershell_hash_smoke.bin")
            with open(sample, "wb") as f:
                f.write(b"abc")
            expected = core.sha256_file(sample)
            actual = dbh.get_filehash_batch(
                [sample], powershell=ps_path)[0]
            if actual != expected:
                raise core.PreflightError(
                    "PowerShell Get-FileHash 冒烟测试失败")
        core.emit_gui_event(
            "tools_detected", tools=tools)
    except (core.PreflightError, storage_core.DaisySmartError) as exc:
        reason = str(exc)
        print(f"部分功能检查未通过：\n{reason}", file=sys.stderr)
        return complete(
            "环境检测完成；部分功能检查未通过",
            inventory,
            check_issues=[{
                "name": "readonly_smoke_checks",
                "display": "只读功能检查",
                "reason": reason,
            }],
            returncode=1,
        )
    print("  Python、SHA-256 NIST 向量、五项工具功能与存储只读查询：通过")
    checks.update({
        "sha256_nist": "passed",
        "tool_smoke_readonly": "passed",
        "powershell_get_filehash": "passed",
        "smartctl_readonly_scan": "passed",
        "windows_storage_inventory": "passed",
    })
    unavailable_capabilities = tuple(
        str(capability.get("title") or capability.get("id") or key)
        for key, capability in runtime_capabilities.items()
        if str(capability.get("state") or "unavailable") != "available"
    )
    if unavailable_capabilities:
        detail = (
            "环境检测完成；不可用可选能力："
            + "、".join(unavailable_capabilities)
        )
        returncode = 1
    else:
        detail = (
            "Python 与五项工具版本、Windows 存储只读查询及 "
            "SHA-256 自检通过"
        )
        returncode = 0
    return complete(
        detail,
        tools,
        check_issues=[],
        returncode=returncode,
    )


if __name__ == "__main__":
    sys.exit(main())
