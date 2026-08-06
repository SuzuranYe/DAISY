r"""Script_DAISY_MAIN：DAISY 统一入口。

全部日常命令行操作从本文件进入；功能模块位于 Module 子目录并由本入口分发，
项目根的 Python 运行入口只保留 Windows GUI 启动器。

用法：
  python .\Script\Script_DAISY_MAIN.py                  # 打印运行指引
  python .\Script\Script_DAISY_MAIN.py <子命令> [参数]   # 运行对应工具
  python .\Script\Script_DAISY_MAIN.py <子命令> --help   # 全部参数
"""
import importlib
import os
import sys
import time

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

# 子命令 → (模块名, 一句话说明)。分发为薄转发：参数原样传给模块的 main()。
COMMANDS = {
    "gui": ("Script_DAISY_GUI",
            "图形界面：填写参数、查看进度与实时日志"),
    "env-check": ("Script_DAISY_Module_ENV_01_Env_Check",
                  "ENV-01 运行环境检测：工具发现、版本、冒烟与只读断言"),
    "scan": ("Script_DAISY_Module_DBS_10_Scan",
             "统一扫描：Full／Quick、暂停恢复与 schema 4 快照"),
    "full-scan": ("Script_DAISY_Module_DBS_11_Full_Scan",
                  "DBS-11 完整档案扫描：文件树、元数据与哈希快照"),
    "quick-scan": ("Script_DAISY_Module_DBS_12_Quick_Scan",
                   "DBS-12 快速档案扫描：只登记文件树与文件信息"),
    "diff": ("Script_DAISY_Module_DBS_21_Diff",
             "DBS-21 快照变更分析：分类变化与证据等级"),
    "verify": ("Script_DAISY_Module_DBS_30_Verify",
               "统一核验：文件状态、内容哈希与格式校验"),
    "check-hash": ("Script_DAISY_Module_DBS_31_Check_Hash",
                   "DBS-31 内容哈希核验：独立复算 SHA-256"),
    "check-format": ("Script_DAISY_Module_DBS_32_Check_Format",
                     "DBS-32 文件结构核验：检查结构与可解析性"),
    "export-report": ("Script_DAISY_Module_DBS_41_Export_Report",
                      "DBS-41 结果报告导出：从快照或 Diff 导出报告"),
    "parse-db": ("Script_DAISY_Module_DBS_41_Export_Report",
                 "DBS-41 数据库解析：识别模块并导出人读或技术报告"),
    "storage-list": ("Script_DAISY_Module_STG_11_Collect",
                     "STG-11 页内检测：列盘并关联 smartctl"),
    "storage-collect": ("Script_DAISY_Module_STG_11_Collect",
                        "STG-11 硬盘信息登记：只读采集并生成 ZIP"),
}
COMMAND_ARGUMENT_PREFIXES = {
    "parse-db": ("--parse-db-mode",),
    "storage-list": ("--list",),
}


def guide() -> str:
    try:
        import Script_DAISY_Lib_DBS_01_Core as core
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
        f"Author: {project_author}",
        "",
        "用法：在本包目录下用 PowerShell 执行：",
        f"  cd \"{_BASE}\"",
        "  python .\\Script\\Script_DAISY_MAIN.py <子命令> [参数]"
        "   （子命令后加 --help 看全部参数）",
        "  python .\\Script\\Script_DAISY_MAIN.py gui"
        "   （打开图形界面；也可双击 Start_DAISY_GUI.pyw）",
        "",
        "子命令：",
    ]
    for name, (_, desc) in COMMANDS.items():
        lines.append(f"  {name:<14} {desc}")
    lines += [
        "",
        "典型流程：",
        "  图形界面        python .\\Script\\Script_DAISY_MAIN.py gui",
        "  新版统一扫描    python .\\Script\\Script_DAISY_MAIN.py scan --mode full --root \"E:\\档案2024\"",
        "  恢复扫描        python .\\Script\\Script_DAISY_MAIN.py scan --resume .\\Output\\Snapshots\\任务.partial.sqlite",
        "  首次完整扫描    python .\\Script\\Script_DAISY_MAIN.py full-scan --root \"E:\\档案2024\""
        "   （默认完整 SHA-256）",
        "  接盘快速扫描    python .\\Script\\Script_DAISY_MAIN.py quick-scan --root \"E:\\档案2024\"",
        "  盘接上的安心检查 python .\\Script\\Script_DAISY_MAIN.py check-hash --snapshot .\\Output\\Snapshots\\基准.sqlite --root \"档案2024=E:\\档案2024\"",
        "  统一只读核验    python .\\Script\\Script_DAISY_MAIN.py verify --snapshot .\\Output\\Snapshots\\基准.sqlite --root \"档案2024=E:\\档案2024\"",
        "  数据库解析      python .\\Script\\Script_DAISY_MAIN.py parse-db --database .\\Output\\Snapshots\\基准.sqlite --format html",
        "  完整性复核      full-scan --hash full --metadata-storage normalized 后与首扫快照 diff",
        "  物理硬盘清单    python .\\Script\\Script_DAISY_MAIN.py storage-list",
        "  单盘信息登记    python .\\Script\\Script_DAISY_MAIN.py storage-collect --disk-number 3",
        "",
        "产物去向：Output\\Snapshots\\（单文件自描述快照，文件名带高32bit指纹）",
        "          Output\\Diffs\\（单文件对比库）｜Output\\Reports\\（报告报表，可删可重生）",
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
              f"  请核对 {_SCRIPT_DIR} 下 GUI、Module 与 Lib 内容"
              f"是否齐全（清单见 README.md）",
              file=sys.stderr)
        return 2
    sys.argv = [
        module_name + ".py",
        *COMMAND_ARGUMENT_PREFIXES.get(cmd, ()),
        *sys.argv[2:],
    ]
    t0 = time.monotonic()
    rc = mod.main()
    elapsed = time.monotonic() - t0
    if elapsed < 60:
        print(f"总计用时 {elapsed:.1f}s", flush=True)
    else:
        h, rem = divmod(int(elapsed), 3600)
        print(f"总计用时 {h:02d}:{rem // 60:02d}:{rem % 60:02d}", flush=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())
