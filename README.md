# DAISY

**Database for Archive Integrity by Suzuran Ye**

当前版本：**v1.6.0**｜作者：**Suzuran Ye**｜平台：**Windows**

DAISY 是一套纯本地、源数据只读的档案登记与核验工具。它可以把目录制作成可审计的
SQLite 快照，比较快照、复核当前文件、解析数据库并生成适合人工查看的报告；也可以只读
登记物理硬盘的 Windows 存储信息与 smartctl 原始证据。

v1.6.0 把数据功能收敛为“扫描、对比、核验、数据库解析”4 个入口，加入可暂停和跨重启
恢复的 schema 4 扫描、统一能力探测、可选格式／RAW 深检、外部工具恢复与熔断、分板块
Issues，以及 HTML／XLSX／CSV／JSONL 数据库解析。所有封存数据库消费者继续只读支持
v1.4.1／schema 3，不迁移、不回写旧库。

## 功能入口

GUI 只显示 6 个用户入口。内部 DBS 编号和旧 CLI 仍保留，避免破坏既有脚本。

| 功能入口 | 用途 | 主要产物 |
|---|---|---|
| 运行环境检测 | 检查 Python、外部工具和可选 RAW 能力 | 环境报告 JSON |
| 档案扫描 | 选择完整／快速模式，建立可暂停恢复的快照 | schema 4 SQLite；必要时 Issues／RAW 伴随报告 |
| 快照对比 | 比较 schema 3／4 快照，区分增删、变化、移动和证据不足 | Diff SQLite；必要时 Issues Markdown |
| 数据核验 | 组合 stat、哈希、格式和可选 RAW 深检 | Markdown／JSON；RAW 伴随报告 |
| 数据库解析 | 识别数据库模块，选择内容与输出格式 | 自包含 HTML、中文 XLSX、CSV、JSONL、manifest |
| 硬盘信息登记 | 只读采集物理盘、分区、卷、Windows 属性和 SMART 证据 | 每盘独立 PROFILE ZIP；可选 TXT |

`DBS-91 DAISY功能自检` 位于顶部「高级」菜单，是项目测试入口，不是业务模块。硬盘页的
「检测物理硬盘」是选择设备前的准备步骤，不另占功能编号。

## 扫描、暂停与恢复

“档案扫描”先选择模式，再显示适用功能：

- 完整扫描默认计算完整 SHA-256，提取规范化元数据并保留 raw payload；格式校验默认关闭，
  可选抽样或全部；RAW 深度校验从属于格式校验并默认关闭。
- 快速扫描只登记目录树和文件 stat，不读取内容哈希或媒体元数据；参与 Diff 时缺少哈希的
  结论会明确降级为 `hash_missing`，不会把 stat 相同冒充成内容相同。
- RAW 深检使用独立 rawpy／LibRaw 子进程实际执行 `postprocess()`。rawpy 不可用时选项会
  禁用并显示原因；不支持的 RAW 只统计，解码失败、截断或 timeout 才进入问题报告。

运行中的扫描可以：

- 暂停后在同一窗口继续；
- 保存进度并退出，下次启动只显示恢复卡片，由用户确认后继续；
- 停止任务并保留 partial，但下次不主动建议恢复；用户仍可手动选择。

跨重启不保存 Python 哈希对象或外部工具进程状态。已提交的文件级结果保留；中断时正在
处理的单个文件从起点重试。哈希无进展 timeout 默认为 90 秒，每增加 9 GiB 再增加 90 秒；
到达阈值时提供“继续等待”“跳过并记录”“停止并保留续传”，无人选择时默认继续等待，
自动处置可在顶部「高级」菜单修改。

外部工具被视为不可信子进程。ExifTool 会话失效后会回收、重建并有限重试当前文件；
ffprobe、7-Zip、rawpy、哈希 worker、PowerShell 哈希和 smartctl 采用相同的工具故障分类、
精确句柄回收和连续失败熔断。工具故障不会被扩散为海量“文件损坏”；schema 4 扫描会
转为 `failed_recoverable` 并保留未处理范围，修复工具后可继续。

## 对比、核验与数据库解析

“快照对比”支持 schema 3→3、3→4、4→3 和 4→4。输入方向决定 added／deleted 语义；
旧库缺少新证据时显示能力不足，不伪造为相同或 0。

“数据核验”可独立组合：

- 当前文件 stat；
- 内容哈希核验；
- 文件格式校验；
- 格式校验下的 RAW 深度校验。

哈希和格式使用各自的确定性抽样；不支持或无法识别的格式只计总数，不列为文件问题。
核验可暂停、继续或停止，但核验报告本身没有扫描 partial，因此不提供跨次保存续传。

“数据库解析”会先只读识别一个快照或 Diff，再展示实际可用模块。快照最多提供 15 个模块，
Diff 最多提供 6 个模块；可使用“人读摘要”“完整审计”“自定义”预设，并任意组合：

- `Report.html`：无网络依赖的自包含人读首页；
- `Report_Excel.xlsx`：中文工作表、冻结表头、筛选、列宽和大表拆分；
- CSV：稳定字段的机器分析格式，UTF-8 无 BOM、LF；
- JSONL：保留嵌套类型和完整技术值；
- manifest：记录输入身份、模块、字段、行数及所有产物摘要。

报告先写入唯一 staging，完整复核输入和产物后 no-clobber 发布。Excel 用户应优先打开
XLSX；CSV 保持机器可读，不再承担“直接双击即可获得最佳中文排版”的人读职责。

## Issues 报告

快照 `_Issues.md` 使用固定板块：枚举问题、哈希问题、Exif／元数据问题、格式校验问题、
RAW 深度校验问题、读取性能异常候选和运行／证据问题。规则如下：

- 已执行且无问题显示 `0`；未执行、旧库未记录或不可解释显示 `NULL`；
- unsupported／unknown／unrecognized format 只统计总数，不显示路径，也不单独触发报告；
- 普通 warning、`[minor]` warning 和清洗诊断折叠；明确损坏或高密度异常才进入候选；
- 工具熔断只显示一条聚合事件、影响数量和有限范围，不复制成逐文件错误；
- 性能异常只是逻辑慢路径候选，不能据此断言物理坏区或设备故障。

完整原始证据仍保留在只读 SQLite 或伴随机器报告中，人读 Markdown 不承担完整数据库转储。

## 硬盘信息登记

1. 在顶部开启「管理员模式」，按提示通过 Windows UAC 重新启动。
2. 打开「硬盘信息登记」，点击「检测物理硬盘」。
3. 检测期间显示进度与日志；成功后弹窗并自动返回展开的硬盘选择区。
4. 选择一个或多个联机设备并开始；设置再次收起，进度与日志展开。

STG 只使用 Windows 查询接口和固定 smartctl 只读命令，不启动 SMART 自检，不修改 SMART
设置、磁盘、分区、卷、文件系统或 BitLocker。每个 ZIP 固定包含三类 JSON：

- `*_Manifest.json`：归档身份、采集溯源、命令、状态、告警和成员声明；
- `*_Smartctl.json`：`smartctl -x` 原始 JSON 证据；
- `*_Storage.json`：Windows 物理盘、分区、卷、挂载点、BitLocker 和可靠性数据。

最终 ZIP 自动验证文件名指纹、安全路径、成员集合、Manifest、时间、字节数和 CRC。

## 快速开始

### 获取 v1.6.0

```powershell
git clone https://github.com/SuzuranYe/DAISY.git
Set-Location .\DAISY
git checkout v1.6.0
```

也可以从 GitHub 的 `v1.6.0` 标签下载固定源码归档。

### 图形界面

双击：

```text
Start_DAISY_GUI.pyw
```

或在 PowerShell 中运行：

```powershell
python .\Script\Script_DAISY_MAIN.py gui
```

默认窗口目标为 `1920×1080`，会在较小工作区内收缩。常规 1080p 页面无需滚动；只有内容
真实溢出时才显示并响应滚动条。任务开始后自动收起设置、展开进度和日志，并让日志填满
剩余高度。「视图」可进入／返回小窗模式，也可把日志打开为实时同步的独立窗口。

顶部「设置」可持久化调整默认窗口大小、字体、字号、二态选项的按钮／下拉样式、空闲关闭
确认和“任务完成提示音”。提示音默认关闭；正常完成或“完成但需要检查”时异步播放一次，
失败、暂停、保存退出、停止、依赖安装和硬盘检测准备阶段不播放。任务运行期间关闭窗口
始终要求确认。

DAISY 只恢复最后使用的功能页面，不保存表单路径、硬盘选择或其它任务参数。偏好写入
`Output/GUI_Settings.json`；损坏或非法字段按项回退到安全默认值，`Output` 不进入 Git。

### 命令行

```powershell
python .\Script\Script_DAISY_MAIN.py --help
python .\Script\Script_DAISY_MAIN.py scan --mode full --root "Archive=E:\Archive"
python .\Script\Script_DAISY_MAIN.py scan --resume .\Output\Snapshots\任务.partial.sqlite
python .\Script\Script_DAISY_MAIN.py diff --old .\old.sqlite --new .\new.sqlite
python .\Script\Script_DAISY_MAIN.py verify --snapshot .\baseline.sqlite --root "Archive=E:\Archive"
python .\Script\Script_DAISY_MAIN.py parse-db --database .\baseline.sqlite --format html --format xlsx
python .\Script\Script_DAISY_MAIN.py storage-list
python .\Script\Script_DAISY_MAIN.py storage-collect --disk-number 3
```

`full-scan`、`quick-scan`、`check-hash`、`check-format` 和 `export-report` 继续作为 v1.5.1
兼容入口保留；新 GUI 使用 `scan`、`verify` 和 `parse-db`。

## 运行环境

DAISY 仅支持 Windows，v1.6.0 使用 Python 3.14 验证。

| 依赖 | 使用范围 |
|---|---|
| Python 3.14 | GUI、CLI 与全部模块 |
| ExifTool 13+ | 完整扫描、格式核验、环境检测 |
| ffprobe 8+ | 媒体元数据与格式核验 |
| 7-Zip 24+ | 压缩包／旧文档结构核验 |
| Windows PowerShell 5.1 或 PowerShell 7.x | 独立哈希与 Windows 查询 |
| smartctl 7.5+ | 环境检测与硬盘登记 |
| rawpy／LibRaw（可选） | RAW 深度校验；缺失时功能禁用，不影响默认扫描 |

没有 Python 时，可由用户主动运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\Script\Script_DAISY_Install_Python.ps1
```

ENV 页为 ExifTool、ffprobe、7-Zip 和 smartctl 提供 4 个等宽安装按钮；每次安装都需要确认，
并通过固定 WinGet 白名单处理。PowerShell 和 rawpy 不由该页面自动安装。手动工具路径统一
位于「高级 > 工具路径」。

## 输出目录

| 目录 | 内容 | 是否可重建 |
|---|---|---|
| `Output/Snapshots` | schema 3／4 封存快照和运行中的 partial | 封存快照应长期保留 |
| `Output/Diffs` | 两份快照的变更数据库 | 可由原快照重建 |
| `Output/Reports` | 环境、核验、Issues 和数据库解析报告 | 可由数据库或当前文件重建 |
| `Output/Storage` | 每块物理盘的 PROFILE ZIP 与可选 TXT | 需重新读取对应硬盘 |

报告和 STG ZIP 可能包含路径、卷标、序列号、卷 GUID、计算机名、PNP Device ID 或
BitLocker 状态；公开分享前必须人工检查。

## 安全与兼容边界

- 源档案只读；扫描、核验、Diff 和解析不会写回源文件。
- 封存数据库只读；分析产生新文件，不原地迁移或补列。
- 业务运行纯本地；只有用户确认的 WinGet 安装流程可能访问软件源。
- 最终 SQLite、Diff、报告目录和 STG ZIP 均使用 no-clobber 发布。
- v1.6.0 统一扫描生成 schema 4；旧 `full-scan／quick-scan` 兼容入口继续生成冻结的
  schema 3。新程序所有封存数据库消费者至少只读支持 v1.4.1／schema 3。
- 旧库缺少 schema 4 能力时显示 `NULL／不可用`，不伪造为 0、空或成功；旧库前后摘要、
  大小和 mtime 必须不变。
- 兼容保证是“v1.6.0 读取 v1.4.1 封存库”，不保证 v1.4.1 程序读取 schema 4，也不允许
  v1.6.0 直接接管 v1.4.1 未完成 partial。
- 多个 GUI 窗口彼此拥有独立表单、日志、控制流和子进程句柄，但仍共享磁盘 I/O、外部工具
  和用户指定输出目录；并发任务应选择不同的确定性报告目标。

详细契约见[技术规格](Spec/Spec_DAISY_Technical.md)、
[v1.6.0 数据契约](Spec/Spec_DAISY_V1_6_0_Data_Contract.md)和
[数据库解析设计](Spec/Spec_DAISY_V1_6_0_Database_Parsing_Design.md)。

## 项目结构

```text
DAISY/
├─ Start_DAISY_GUI.pyw
├─ Script/
│  ├─ Script_DAISY_MAIN.py
│  ├─ Script_DAISY_GUI.py
│  ├─ Module/                 # 统一入口与冻结兼容入口
│  ├─ Lib/                    # Reader、状态机、扫描、核验、解析、STG
│  └─ Test/
├─ Spec/
└─ Output/                    # 首次生成产物时创建；不进入 Git
```

GUI 负责参数、状态和进程控制；业务逻辑仍由同一 CLI／Module 层执行。

## 测试

发布测试计划和逐阶段证据见
[v1.6.0 实施计划](Spec/Spec_DAISY_V1_6_0_Implementation_Plan.md)与
[v1.6.0 测试记录](Spec/Spec_DAISY_V1_6_0_Test_Record.md)。

运行完整回归：

```powershell
python -B -W error -m unittest discover -s .\Script\Test -p "Script_DAISY_Test_*.py" -v
```

默认测试只使用工作区合成夹具和本次精确创建的子进程，不读取真实档案、不运行真实物理
硬盘检测，也不枚举或终止其它进程。真实 RAW 验收只使用经明确授权后复制到工作区测试
目录的最小夹具。

## 联系与许可证

- GitHub：<https://github.com/SuzuranYe/DAISY>
- 作者邮箱：`151104858+SuzuranYe@users.noreply.github.com`
- 许可证：[MIT License](LICENSE)

问题反馈请提供 DAISY 版本、运行模块、退出码、日志末尾和最小复现条件；分享报告前请删除
不希望公开的路径、序列号或设备身份信息。
