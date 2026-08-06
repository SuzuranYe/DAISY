# DAISY

**Database for Archive Integrity by Suzuran Ye**

当前版本：**v1.5.1**｜作者：**Suzuran Ye**｜平台：**Windows**

DAISY 是一套纯本地、源数据只读的档案登记与核验工具。它可以把文件目录制作成
可审计的 SQLite 快照，比较两次快照、复核内容和文件结构、导出报告，也可以只读
登记物理硬盘的 Windows 存储信息与 smartctl 原始证据。

v1.5.1 重点改善 1080P 下的完整显示、固定面板、小窗往返、独立日志、字段命名和
操作对齐，并为 Excel 提供原生中文 XLSX。数据库扫描、Diff、数据库生成、DDL、
schema 与 data contract 继续保持 v1.4.1 契约。

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

### 继续未完成的完整扫描

DBS-11 可以选择同版本留下的 `.partial.sqlite` 继续。恢复时会验证 ScanLock、生成器、
schema、元数据 profile、必要表和原 root，然后重新枚举并对账；未变化且已经完成的
哈希／元数据不会重做，待处理哈希和遗留 `processing` 会继续。未变化的
`error`／`timeout` 不会自动重试。

续传后的库保留原 UUID、配置和中断事件，因此不应与重新从头扫描的库比较文件哈希或
逐字段相等；在源目录、工具和环境未变化时，文件清单、内容哈希、规范化元数据、统计
和完成状态才应语义等价。哈希连续 30 秒没有数据块进展时目前只记录 stall，不会自动
超时或跳过。完整边界见[技术规格](Spec/Spec_DAISY_Technical.md)第 7.4 节；计划改进见
[v1.6.0 可靠性、兼容与报告重构待办](Spec/Spec_DAISY_V1_6_0_Backlog.md)。

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
3. 检测期间界面显示进度与日志；成功后会弹窗并自动返回展开的硬盘选择区。
4. 在硬盘池中逐项勾选，或点击「全选」；「取消选择」可清空本次勾选。
5. 脱机盘、Windows 资料缺失或 smartctl 未关联的设备仍会显示原因，但不可勾选。
6. 开始后，设置再次收起，进度与日志展开；每块盘作为独立 `队列 i/n` 任务重新
   确认身份并生成独立 ZIP。

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

### 获取 v1.5.1

克隆仓库并切换到固定发布标签：

```powershell
git clone https://github.com/SuzuranYe/DAISY.git
Set-Location .\DAISY
git checkout v1.5.1
```

也可以从 GitHub 的 `v1.5.1` 标签下载源码归档。发布标签固定版本内容；需要继续
跟踪开发提交时再切换到对应分支。

### 图形界面

已安装 Python 时，双击：

```text
Start_DAISY_GUI.pyw
```

也可以在 PowerShell 中运行：

```powershell
python .\Script\Script_DAISY_MAIN.py gui
```

窗口以 `1920×1080` 为默认目标尺寸，并在较小工作区内自动收缩；1080p 默认布局的
常规设置无需向下滚动。任务设置、运行进度、运行日志和命令区采用固定顺序，不提供
拖动调整；各区仍可独立折叠。点击「开始任务」后会自动收起设置、展开进度与日志，
并让日志占用剩余高度。「视图」菜单可直接进入／返回小窗模式，也可把日志打开为实时
同步的独立窗口。

任务设置使用统一信息结构：字段和分区标题最多 6 个字符，标签共用同一右边界，
「添加目录」与「浏览」等操作统一位于字段右侧。界面不再显示必填星号；必填规则仍会
在开始任务前校验，详细含义可将鼠标悬停在字段文字、字段区域或控件上查看。

顶部「设置」菜单可持久化默认窗口大小、字体、字号和空闲关闭是否确认。正常关闭或以
管理员模式重启后，DAISY 会回到最后使用的功能页面，但不会保存表单路径、硬盘选择或
其它任务参数。默认字号保持正常可读；字体只列出本机实际安装的候选项。任务运行或
启动过程中，无论空闲关闭偏好如何，关闭窗口都一定要求确认。表单只有在内容真实超出
可视区时才显示并响应滚动条。

这些界面偏好写入 `Output/GUI_Settings.json`。该文件只包含窗口、字体、字号、空闲
关闭确认和最后页面，不包含任务表单值；内容损坏或字段非法时会按字段回退到安全
默认值。`Output` 不进入 git，也不会被「清理缓存」功能删除。

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

该脚本只处理 Python 3.14。Python 可用后，ENV-01 设置页会把 ExifTool、ffprobe、
7-Zip 和 smartctl 四个独立安装按钮等宽排在同一行；每次安装都需要确认，并通过固定
WinGet 白名单处理。PowerShell 不由 DAISY 安装。手动可执行文件路径统一在
「高级 > 工具路径」设置；哈希比例与命令预览也统一位于「高级」。

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
- 项目长期兼容规则：未来所有接受封存 DBS 数据库的功能，至少必须只读支持
  v1.4.1／schema 3；旧库不得原地迁移，缺少未来字段时必须显示“不可用”而不是伪造
  结果。该保证指新程序读取旧封存库，不反向承诺旧程序读取未来新 schema；未完成
  partial 另按恢复契约处理。
- 未完成 partial 只能由相同生成器版本续传，所以 v1.5.0 及更早版本的 partial
  不能由 v1.5.1 续传；已封存的合格 schema 3 快照仍可按准入规则只读使用。
- v1.5.1 没有改变数据库 DDL、字段、约束、schema 版本或 DBS 扫描／Diff／生成
  语义；代码层只更新应用版本和报告身份，并优化 UI 与人读输出。STG 的
  `archive_schema_version=3` 与 SQLite schema 只是数字相同，数据模型彼此独立。
- v1.6.0 的续传状态机、失败重试、工具溯源、哈希超时、旧库兼容、核验审计和报告重构
  仍是计划项，不应把规划文档解释为 v1.5.1 已具备这些能力。

DBS 与 STG 的完整技术语义见[技术规格](Spec/Spec_DAISY_Technical.md)，版本历史见
[版本演化](Spec/Spec_DAISY_Version_Evolution.md)。v1.6.0 的已确认需求、实施顺序和完整
验证矩阵见[v1.6.0 实施计划](Spec/Spec_DAISY_V1_6_0_Implementation_Plan.md)，数据库解析
的交互与导出契约见[数据库解析设计](Spec/Spec_DAISY_V1_6_0_Database_Parsing_Design.md)。

## 项目结构

```text
DAISY/
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

发布验收范围、组合矩阵和实际执行结果见
[v1.5.1 测试计划](Spec/Spec_DAISY_V1_5_1_Test_Plan.md)。

v1.5.1 最终 GUI 测试为 93／93；完整自动化回归连续两轮均为 281／281。字体、字号、
窗口尺寸、宽高比和 Tk 缩放矩阵均使用真实 Tk 控件几何断言，不以截图代替。

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
