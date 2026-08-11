r"""Script_DAISY_CLI：DAISY 统一入口。

全部日常命令行操作从本文件进入；功能模块位于 Module 子目录并由本入口分发，
项目根的 Python 运行入口只保留 Windows GUI 启动器。

用法：
  python .\Script\Script_DAISY_CLI.py                  # 打印运行指引
  python .\Script\Script_DAISY_CLI.py --version        # 打印当前版本
  python .\Script\Script_DAISY_CLI.py <子命令> [参数]   # 运行对应工具
  python .\Script\Script_DAISY_CLI.py <子命令> --help   # 全部参数
"""
import argparse
import importlib
import os
import sys
import time


_ARGPARSE_TRANSLATIONS = {
    "usage: ": "用法：",
    "options": "选项",
    "positional arguments": "位置参数",
    "show this help message and exit": "显示帮助并退出",
    "the following arguments are required: %s": "缺少必需参数：%s",
    "one of the arguments %s is required": "必须提供以下参数之一：%s",
    "unrecognized arguments: %s": "无法识别的参数：%s",
    "expected one argument": "需要一个参数值",
    "expected at least one argument": "至少需要一个参数值",
}


def _translate_argparse(text: str) -> str:
    """统一主入口所分发命令的 argparse 固定界面文字。"""
    return _ARGPARSE_TRANSLATIONS.get(text, text)


# argparse 没有逐解析器的翻译入口；DAISY 主进程只在本地覆盖固定帮助词。
argparse._ = _translate_argparse

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_BASE = os.path.dirname(_SCRIPT_DIR)
_LIB_DIR = os.path.join(_SCRIPT_DIR, "Lib")
_MODULE_DIR = os.path.join(_SCRIPT_DIR, "Module")
sys.path[:0] = [
    path for path in (_SCRIPT_DIR, _LIB_DIR, _MODULE_DIR)
    if path not in sys.path
]

# 子命令 →（模块名，一句话说明）。分发为薄转发：参数原样传给模块的 main()。
COMMANDS = {
    "gui": ("Script_DAISY_GUI",
            "图形界面：填写参数、查看进度与实时日志"),
    "env-check": ("Script_DAISY_Module_Environment_Check",
                  "运行环境检测：检测 Python、外部工具和 RAW 解码能力"),
    "scan": ("Script_DAISY_Module_Scan",
             "档案扫描建库：建立完整或快速封存快照；任务支持暂停和续传"),
    "full-scan": ("Script_DAISY_Module_Full_Scan",
                  "兼容完整扫描入口：文件树、元数据与哈希快照"),
    "quick-scan": ("Script_DAISY_Module_Quick_Scan",
                   "兼容快速扫描入口：只登记文件树与文件属性"),
    "diff": ("Script_DAISY_Module_Snapshot_Diff",
             "档案快照对比：比较两份封存快照，记录增删、变化、移动与复制"),
    "verify": ("Script_DAISY_Module_Verify",
               "档案数据核验：按封存快照复核，或无数据库直接检查文件"),
    "check-hash": ("Script_DAISY_Module_Check_Hash",
                   "兼容哈希核验入口：独立复算 SHA-256"),
    "check-format": ("Script_DAISY_Module_Check_Format",
                     "兼容格式校验入口：检查文件结构与可解析性"),
    "export-report": ("Script_DAISY_Module_Parse",
                      "旧版报告导出兼容入口：从快照或 Diff 导出冻结格式报告"),
    "parse-db": ("Script_DAISY_Module_Parse",
                  "档案数据解析：只读解析数据库并按所选模块与格式导出"),
    "storage-list": ("Script_DAISY_Module_Storage_Collect",
                      "检测硬盘：列出 Windows 硬盘并匹配 SMART 读取目标"),
    "storage-collect": ("Script_DAISY_Module_Storage_Collect",
                        "硬盘信息登记：只读采集并生成 ZIP"),
}
COMMAND_ARGUMENT_PREFIXES = {
    "parse-db": ("--parse-db-mode",),
    "storage-list": ("--list",),
}


def _configure_task_worker_runtime(command: str) -> None:
    """为非 GUI 任务设置局部原生工具故障边界；失败时保留可见警告。"""
    if command == "gui":
        return
    try:
        import Script_DAISY_Lib_Snapshot_Core as core
    except ImportError:
        # 由后续模块导入保留统一的「包不完整」用户提示。
        return

    outcome = core.configure_windows_worker_error_mode()
    if outcome["status"] in ("error", "degraded"):
        print(
            "警告：Windows 原生程序故障弹窗抑制未完全启用："
            f"{outcome['detail'] or outcome['status']}",
            file=sys.stderr,
            flush=True,
        )


def guide() -> str:
    try:
        import Script_DAISY_Lib_Snapshot_Core as core
        ver = "v" + core.SCANNER_VERSION
        project_name = core.PROJECT_NAME
        project_full_name = core.PROJECT_FULL_NAME
        project_author = core.PROJECT_AUTHOR
    except ImportError:
        ver = "（版本未知：Script\\ 缺失或不完整）"
        project_name = "DAISY"
        project_full_name = "Database for Archive Integrity by Suzuran Ye"
        project_author = "Suzuran Ye"
    lines = [
        f"{project_name} {ver}",
        project_full_name,
        f"作者：{project_author}",
        "",
        "用法：在本包目录下用 PowerShell 执行：",
        f"  cd \"{_BASE}\"",
        "  python .\\Script\\Script_DAISY_CLI.py --version",
        "  python .\\Script\\Script_DAISY_CLI.py <子命令> [参数]"
        "   （子命令后加 --help 看全部参数）",
        "  python .\\Script\\Script_DAISY_CLI.py gui"
        "   （打开图形界面；也可双击 Start_DAISY_GUI.pyw）",
        "",
        "子命令：",
    ]
    command_width = max(len(name) for name in COMMANDS)
    for name, (_, desc) in COMMANDS.items():
        lines.append(f"  {name:<{command_width}} {desc}")
    lines += [
        "",
        "典型流程：",
        "  图形界面        python .\\Script\\Script_DAISY_CLI.py gui",
        "  扫描建库        python .\\Script\\Script_DAISY_CLI.py scan --mode full --root \"E:\\档案2024\"",
        "  续传扫描        python .\\Script\\Script_DAISY_CLI.py scan --resume .\\Output\\Snapshots\\任务.partial.sqlite",
        "  兼容完整扫描    python .\\Script\\Script_DAISY_CLI.py full-scan --root \"E:\\档案2024\""
        "   （默认完整 SHA-256）",
        "  兼容快速扫描    python .\\Script\\Script_DAISY_CLI.py quick-scan --root \"E:\\档案2024\"",
        "  哈希核验        python .\\Script\\Script_DAISY_CLI.py check-hash --snapshot .\\Output\\Snapshots\\基准.sqlite --root \"档案2024=E:\\档案2024\"",
        "  档案数据核验    python .\\Script\\Script_DAISY_CLI.py verify --snapshot .\\Output\\Snapshots\\基准.sqlite --root \"档案2024=E:\\档案2024\"",
        "  档案数据解析    python .\\Script\\Script_DAISY_CLI.py parse-db --database .\\Output\\Snapshots\\基准.sqlite --format html",
        "  检测硬盘        python .\\Script\\Script_DAISY_CLI.py storage-list",
        "  硬盘信息登记    python .\\Script\\Script_DAISY_CLI.py storage-collect --disk-number 3",
        "",
        "产物去向：Output\\Snapshots\\（单文件自描述快照，文件名带 8 位 SHA-256 指纹）",
        "          Output\\Diffs\\（单文件对比库）｜Output\\Reports\\（报告与解析结果）",
        "          Output\\Storage\\（单硬盘只读信息档案 ZIP）",
        "文档：README.md（入口与结构）",
        "      Spec\\Spec_DAISY_Technical.md（现行技术规格）",
    ]
    return "\n".join(lines)


def _own_console() -> bool:
    """双击启动检测：本进程独占控制台窗口（终端里运行时计数 ≥ 2）。"""
    if sys.platform != "win32":
        return False
    import ctypes
    arr = (ctypes.c_uint * 4)()
    return ctypes.windll.kernel32.GetConsoleProcessList(arr, 4) == 1


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help", "help"):
        print(guide())
        if _own_console():             # 双击启动：撑住窗口让指引可读
            try:
                input("\n（双击启动只能查看本指引；实际使用请在终端运行。"
                      "按 Enter 关闭）")
            except EOFError:
                pass
        return 0
    if sys.argv[1] in ("-V", "--version", "version"):
        print(guide().splitlines()[0])
        return 0
    cmd = sys.argv[1]
    if cmd not in COMMANDS:
        print(f"未知子命令：{cmd}\n")
        print(guide())
        return 2
    module_name, _ = COMMANDS[cmd]
    try:
        mod = importlib.import_module(module_name)
    except ImportError as exc:               # 包不完整：给人话而非堆栈
        print(f"包不完整，无法加载子命令 {cmd} 所需模块：{exc}\n"
              f"  请确认 {_SCRIPT_DIR} 下的 GUI、Module 与 Lib 文件完整"
              f"（清单见 README.md）。",
              file=sys.stderr)
        return 2
    _configure_task_worker_runtime(cmd)
    sys.argv = [
        module_name + ".py",
        *COMMAND_ARGUMENT_PREFIXES.get(cmd, ()),
        *sys.argv[2:],
    ]
    t0 = time.monotonic()
    rc = mod.main()
    elapsed = time.monotonic() - t0
    if elapsed < 60:
        print(f"总计用时 {elapsed:.1f} 秒", flush=True)
    else:
        h, rem = divmod(int(elapsed), 3600)
        print(f"总计用时 {h:02d}:{rem // 60:02d}:{rem % 60:02d}", flush=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())
