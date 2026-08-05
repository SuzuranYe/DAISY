# DAISY

**Database for Archive Integrity by Suzuran Ye**

当前版本：**v1.5.0**｜作者：**Suzuran Ye**｜平台：**Windows**

DAISY 是一套纯本地、源数据只读的档案登记与核验工具。它可以把文件目录制作成
可审计的 SQLite 快照，比较两次快照、复核内容和文件结构、导出报告，也可以只读
登记物理硬盘的 Windows 存储信息与 smartctl 原始证据。

## DAISY 能做什么

GUI 中有且只有以下 8 个功能模块；每项都对应一个同编号 Module 脚本和一个统一
CLI 子命令：

| 编号 | 功能模块 | 能解决的问题 | 主要产物 |
|---|---|---|---|
| ENV-01 | 运行环境检测 | 检查 5 项依赖、版本、只读冒烟与 SHA-256 能力 | 环境报告 JSON |
| DBS-11 | 完整档案扫描 | 完整登记目录树、文件信息、元数据和 SHA-256 | 封存快照 SQLite |
| DBS-12 | 快速档案扫描 | 不做内容哈希和媒体元数据，快速登记目录与文件 stat | 快速快照 SQLite |
| DBS-21 | 快照变更分析 | 比较两份封存快照，区分增删、变化、移动和证据不足 | Diff SQLite；必要时 Issues Markdown |
| DBS-31 | 内容哈希核验 | 对照快照重新读取当前文件并复算 SHA-256 | JSON；必要时 Issues Markdown |
| DBS-32 | 文件结构核验 | 独立检查媒体、压缩包和文档结构是否可解析 | JSON、CSV、Markdown、Info CSV |
| DBS-41 | 结果报告导出 | 把快照或 Diff 转为便于查看和分析的表格 | 完整 CSV、中文 XLSX；Diff 另含 Markdown |
| STG-11 | 硬盘信息登记 | 只读采集物理盘、分区、卷、Windows 属性和 SMART 证据 | 每盘独立 PROFILE ZIP；可选 TXT |

`DBS-91 DAISY功能自检` 位于顶部「高级」菜单，只用于运行项目自身测试，不是第
9 个业务模块。STG-11 页内的硬盘检测也是同一模块的准备模式，不另占编号。

## 常见使用场景

### 第一次建立档案基准

1. 运行「ENV-01 运行环境检测」。
2. 打开「DBS-11 完整档案扫描」。
3. 加入一个或多个档案根目录；默认分别生成数据库，也可选择合并。
4. 保持默认完整 SHA-256，确认后开始任务。
5. 将最终 `.sqlite` 作为只读基准保存；`.partial.sqlite` 只用于同版本续传。

完整扫描会读取每个可读文件的全部内容，可能持续几小时到几天。界面会始终显示
当前完整根路径、任务阶段、本阶段进度和总队列；即使只有一项也显示 `队列 1/1`。

### 日后检查档案有没有变化

- 已有两份快照：使用「DBS-21 快照变更分析」。
- 只有一份基准快照，想直接检查当前目录：使用「DBS-31 内容哈希核验」。
- 只关心容器／文件是否还能解析：使用「DBS-32 文件结构核验」。
- 需要 Excel 或人工阅读：使用「DBS-41 结果报告导出」后打开
  `Report_Excel.xlsx`；脚本和审计仍可读取同目录完整 CSV。

### 快速记录一次目录状态

使用「DBS-12 快速档案扫描」。它记录目录、大小、时间和 File ID，但不计算内容
SHA-256，也不采集媒体元数据。快速快照可以参与 Diff；由于缺少哈希，内容结论会
如实降级为 `hash_missing`，不会把 stat 相同冒充成内容相同。

### 登记一块或多块物理硬盘

1. 在顶部开启「管理员模式」，按提示通过 Windows UAC 重新启动。
2. 打开「STG-11 硬盘信息登记」，点击「检测物理硬盘」。
3. 在硬盘池中逐项勾选，或点击「选择所有联机硬盘」。
4. 脱机盘、Windows 资料缺失或 smartctl 未关联的设备仍会显示原因，但不可勾选。
5. 开始后，每块盘作为独立 `队列 i/n` 任务重新确认身份并生成独立 ZIP。

检测结果会提示「若接入硬盘发生变化，请重新进行检测。」；不得在硬盘接入状态改变后
沿用旧 DiskNumber 和旧勾选。

STG 只使用 Windows 查询接口以及固定的 smartctl 只读命令；不会启动 SMART 自检，
不会修改 SMART 设置、磁盘、分区、卷、文件系统或 BitLocker。最终 ZIP 发布后会
自动完成文件名指纹、安全路径、成员集合、Manifest、时间、字节数与 CRC 核验。

每个 ZIP 内固定包含三份职责不同的 JSON：`*_Manifest.json` 是归档索引与采集溯源，
记录版本、设备身份、命令、状态、告警和成员声明；`*_Smartctl.json` 原样保留
`smartctl -x` 的 JSON 证据；`*_Storage.json` 保存 Windows 查询得到的物理盘、分区、
卷、挂载点、BitLocker 和可靠性数据。前者说明「这包是什么、如何采得」，后两者分别
保存 smartctl 与 Windows 两条证据链。

## 快速开始

### 图形界面

已安装 Python 时，双击：

```text
Start_DAISY_GUI.pyw
```

也可以在 PowerShell 中运行：

```powershell
python .\Script\Script_DAISY_MAIN.py gui
```

窗口以 `1920×1080` 为目标尺寸，并在较小工作区内自动收缩；1080p 默认布局会压缩
说明与表单间距，常规设置无需向下滚动。任务设置、运行进度和运行日志之间可上下拖动
分隔条调整纵向占比，也都可折叠；点击「开始任务」后进度和日志会自动展开。「视图」
菜单可直接进入／返回小窗模式。标题栏使用小雏菊图标；全界面统一请求
`Microsoft YaHei UI`，不依赖第三方字体。

DAISY 可以同时打开多个窗口。每个窗口的表单、队列、日志、进度和子进程句柄彼此
独立，也可同时运行相同或不同模块；但多个窗口仍共享物理磁盘、外部工具和用户指定的
输出目录。并发扫描会竞争磁盘 I/O；对同一确定性报告目标并发导出时，应为各窗口选择
不同报告目录。快照类任务使用唯一 partial 和 no-clobber 发布，已有正式产物不会被
静默覆盖。

### 命令行

查看全部子命令：

```powershell
python .\Script\Script_DAISY_MAIN.py --help
```

查看某项参数：

```powershell
python .\Script\Script_DAISY_MAIN.py full-scan --help
python .\Script\Script_DAISY_MAIN.py storage-collect --help
```

常用示例：

```powershell
python .\Script\Script_DAISY_MAIN.py full-scan --root "Archive=E:\Archive"
python .\Script\Script_DAISY_MAIN.py quick-scan --root "Archive=E:\Archive"
python .\Script\Script_DAISY_MAIN.py storage-list
python .\Script\Script_DAISY_MAIN.py storage-collect --disk-number 3
```

CLI 的 `storage-collect` 一次登记一块盘；GUI 的多选硬盘池通过多个单盘 CLI
子进程形成队列，因此 GUI 与 CLI 不存在两套采集逻辑。

## 运行环境

DAISY 仅支持 Windows，当前版本使用 Python 3.14 验证。

| 依赖 | 最低版本 | 使用范围 |
|---|---:|---|
| Python | 3.14 | GUI、CLI 与全部模块 |
| ExifTool | 13 | ENV、完整扫描、结构核验 |
| ffprobe | 8 | ENV、完整扫描、结构核验 |
| 7-Zip | 24 | ENV、完整扫描、结构核验 |
| Windows PowerShell 5.1 或 PowerShell 7.x | 系统可用版本 | SHA-256 与 Windows 查询 |
| smartctl | 7.5 | ENV 与 STG-11 |

快速扫描除 Python 外不依赖 ExifTool、ffprobe、7-Zip 或 PowerShell。

没有 Python 时，可由用户主动运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\Script\Script_DAISY_Install_Python.ps1
```

该脚本只处理 Python 3.14。Python 可用后，ENV-01 设置页会常驻显示 ExifTool、
ffprobe、7-Zip 和 smartctl 的独立安装按钮；每次安装都需要确认，并通过固定 WinGet
白名单处理。PowerShell 不由 DAISY 安装。手动可执行文件路径统一在「高级 > 工具
路径」设置；哈希比例与命令预览也统一位于「高级」。

## 输入、输出与报告

| 目录 | 内容 | 是否可重建 |
|---|---|---|
| `Output/Snapshots` | Full／Quick 封存快照和运行中的 partial | 封存快照应长期保留 |
| `Output/Diffs` | 两份快照的变更数据库 | 可由原快照重新生成 |
| `Output/Reports` | 环境、核验和导出报告 | 可由数据库或当前文件重新生成 |
| `Output/Storage` | 每块物理盘的 STG PROFILE ZIP 与可选 TXT | 需重新读取对应硬盘 |

所有独立 JSON、Markdown 和 TXT 报告都写入 DAISY 工具名、版本与作者。业务 CSV
必须保持机器可读表头，因此报告身份写入同组的 `Report_info.csv` 或 `_Info.csv`。
STG ZIP 的 Manifest、Storage JSON 和外部 TXT 同样包含生成器身份。

DBS-41 对快照会导出文件树、目录、规范化元数据、视频 GPS、媒体流、哈希、压缩
包、错误、诊断和 Summary CSV；对 Diff 会导出 `Diff_summary.md`、
`Diff_details.csv`、`Diff_dirs.csv`、`Diff_hash_groups.csv` 和
`Diff_subtrees.csv`。CSV 始终为 UTF-8（无 BOM）、LF，并保留数据库字段名；另附
`Report_Excel.xlsx` 作为人读入口，使用中文工作表和中英字段、冻结表头、筛选及按
内容调整的列宽，Excel 直接打开时不再依赖本地代码页猜测中文编码。

DBS-11 的 `_Issues.md` 只呈现需要关注的问题。ExifTool 单纯返回「格式未识别」时，
对应状态、诊断和错误记录仍完整保留在 SQLite，但不会单独触发或列入 Issues 报告；
读取失败、超时、unstable、哈希错误和枚举缺口仍照常报告。

## 安全与兼容边界

- 源档案只读：扫描、核验、Diff 和导出不会修改源文件或源目录。
- 封存数据库只读：后续分析读取既有 SQLite，并把结果写成新文件。
- 本地运行：扫描、核验、Diff、导出和 STG 采集不上传、不遥测；ENV 中经用户
  确认后执行的 WinGet 安装或更新动作可能访问软件源。
- 不覆盖：最终 SQLite、Diff 和 STG ZIP 按 no-clobber 规则发布。可重建报告会
  使用确定性目录或文件名；需要保留旧报告时，应选择新的报告目录。
- 隐私：报告和 STG ZIP 可能包含完整路径、卷标、序列号、卷 GUID、计算机名、
  PNP Device ID 或 BitLocker 状态；公开分享前必须人工检查。
- 当前 DBS `schema_version=3`、元数据 profile 7、最低完整快照读取器
  `v1.4.1`；当前实现只读取 schema 3。
- 未完成 partial 只能由相同生成器版本续传，所以 v1.4.2 partial 不能由
  v1.5.0 续传；已封存的合格 schema 3 快照仍可按准入规则只读使用。
- v1.5.0 没有改变数据库 DDL、字段、约束、schema 版本或 DBS 业务语义；代码层
  只更新应用版本、报告身份，并统一 DBS 库文件名及导入路径。STG 的
  `archive_schema_version=3` 与 SQLite schema 只是数字相同，数据模型彼此独立。

DBS 与 STG 的完整技术语义见[技术规格](Spec/Spec_DAISY_Technical.md)，版本历史见
[版本演化](Spec/Spec_DAISY_Version_Evolution.md)。

## 项目结构

```text
DAISY-F/
├─ Start_DAISY_GUI.pyw
├─ Script/
│  ├─ Script_DAISY_MAIN.py
│  ├─ Script_DAISY_GUI.py
│  ├─ Module/
│  │  ├─ Script_DAISY_Module_ENV_01_Env_Check.py
│  │  ├─ Script_DAISY_Module_DBS_11_Full_Scan.py
│  │  ├─ Script_DAISY_Module_DBS_12_Quick_Scan.py
│  │  ├─ Script_DAISY_Module_DBS_21_Diff.py
│  │  ├─ Script_DAISY_Module_DBS_31_Check_Hash.py
│  │  ├─ Script_DAISY_Module_DBS_32_Check_Format.py
│  │  ├─ Script_DAISY_Module_DBS_41_Export_Report.py
│  │  └─ Script_DAISY_Module_STG_11_Collect.py
│  ├─ Lib/
│  └─ Test/
├─ Spec/
└─ Output/                 # 首次生成产物时创建
```

`Script_DAISY_MAIN.py` 是统一 CLI 分发入口；GUI 只收集参数、显示状态并启动相同
子命令。8 个 `Script/Module` 文件与 8 个 GUI 功能模块必须始终一一对应。

## 测试

运行完整回归：

```powershell
python -B -m unittest discover -s .\Script\Test -p "Script_DAISY_Test_*.py" -v
```

默认测试使用合成数据和系统临时目录，不读取真实档案或真实物理硬盘。STG 的只读
审计会检查 smartctl 参数模板、PowerShell 禁止命令、`shell=False`、ZIP 自动
核验和 no-clobber 行为。

## 联系与许可证

- GitHub：<https://github.com/SuzuranYe/DAISY>
- 作者邮箱：`151104858+SuzuranYe@users.noreply.github.com`
- 许可证：[MIT License](LICENSE)

问题反馈请同时提供 DAISY 版本、运行模块、退出码、日志末尾和最小复现条件；分享
报告前请先删除不希望公开的路径、序列号或设备身份信息。
