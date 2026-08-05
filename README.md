# DAISY

**Database for Archive Integrity by Suzuran Ye**

版本：**v1.5.0**

许可证：**MIT**

DAISY 是面向摄影素材库、个人档案与存储设备的本地清点、登记、核验和对比
工具。DBS 功能域获取文件树、文件信息、元数据、哈希及其变化并生成独立、
自描述、不可回写的 SQLite 快照或 Diff；STG 功能域获取物理硬盘、分区、卷和
smartctl 证据并生成独立 ZIP。两个功能域统一由同一 GUI／CLI 调度，但不混用
数据模型；扫描源目录与被登记硬盘均保持只读。

## 主要能力

- 完整扫描文件树、时间、规范化元数据、可选原始元数据全文、File ID 和 SHA-256；
- 快速扫描目录与文件信息，不读取文件内容；
- 校验当前文件的结构和可解析性；
- 独立复算 SHA-256，检查当前磁盘是否仍与快照一致；
- 对比两份快照，区分内容变化、移动、复制、元数据提取差异和证据不足；
- 把快照或 Diff 数据库导出为 CSV 和 Markdown 报告；
- 只读登记单块物理硬盘的 Windows 存储资料与 smartctl 原始证据，并生成可核验
  ZIP；
- 使用 Tkinter／ttk 图形界面，不需要安装额外 Python 包；多目录任务单独显示
  队列总进度、当前任务阶段和本阶段工作量，小窗视图在空闲和运行时均可进入；
  任务从顶部「面板」菜单选择，任务设置、运行进度和运行日志均可主动折叠，
  运行进度与运行日志默认折叠。

## 运行环境

DAISY 仅支持 Windows，当前版本在 Python 3.14 上完成验证。

| 依赖 | 最低版本 | 使用范围 |
|---|---:|---|
| Python | 3.14 | GUI 和全部任务 |
| ExifTool | 13 | 环境检测、完整扫描、文件结构核验 |
| ffprobe（随 FFmpeg 安装） | 8 | 环境检测、完整扫描、文件结构核验 |
| 7-Zip | 24 | 环境检测、完整扫描、文件结构核验 |
| PowerShell `Get-FileHash` | Windows 内置 | 环境检测、完整扫描、SHA-256 独立复算 |
| smartctl（smartmontools） | 7.5 | 环境检测、物理硬盘清单、硬盘信息登记 |

Quick 快速扫描除 Python 外不依赖 ExifTool、ffprobe、7-Zip 或 PowerShell。
ExifTool、FFmpeg、7-Zip 和 smartmontools 由用户通过 WinGet 独立安装，DAISY 不捆绑或
再分发这些程序；它们分别遵循各自的许可证。

DAISY 兼容 Windows PowerShell 5.1 与 PowerShell 7.x。自动发现顺序为：
手动路径、当前进程的 `PATH`、两个系列的 Windows 常规安装位置。便携版或
自定义目录可在 GUI 顶部「高级 > 工具路径」菜单中统一指定，也可通过 CLI 的
`--powershell-path` 指定。

### 自动安装依赖

如果双击 `Start_DAISY_GUI.pyw` 没有反应，通常是尚未安装 Python 或 `.pyw`
文件关联不可用。请先在项目根目录打开 PowerShell，再运行只负责安装
Python 3.14 的引导脚本：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Script\Script_DAISY_Install_Python.ps1
```

脚本会先说明用途，并且只有明确输入 `y` 后才通过 WinGet 安装或更新
`Python.Python.3.14`；它不会安装 ExifTool、FFmpeg、7-Zip 或 smartmontools。安装完成后
请重新打开 DAISY。

Python 已可用时，进入「ENV-01 运行环境检测」运行检测。页面会显示本机实际发现的
版本；无论工具是否已经发现，任务设置页的「软件安装」区都会常驻显示 ExifTool、
ffprobe、7-Zip 和 smartctl 四个彼此独立的「下载并安装」按钮，并采用等宽 2×2
布局。用户点击其中一项并再次确认后，
GUI 才会通过 WinGet 的固定白名单处理所选工具；已安装状态和可用更新由
WinGet 判断：

- `OliverBetz.ExifTool`：读取照片／视频元数据并参与文件结构核验；
- `Gyan.FFmpeg`：提供 ffprobe，用于读取音视频流、校验媒体容器，并在
  全量元数据模式下保留视频、音频和 GIF 的完整 JSON；
- `7zip.7zip`：读取并测试 7z、RAR、TAR 等归档格式。
- `smartmontools.smartmontools`：提供 smartctl，用于物理硬盘发现与完整只读
  SMART 读取。

GUI 不提供任意包名输入，也不会自动安装 PowerShell。所选工具安装结束后会刷新
当前进程的 PATH 并重新运行环境检测。若新程序仍未被发现，请关闭 DAISY
后重新打开。如果系统找不到 `winget`，请先从 Microsoft Store 安装或更新
“应用安装程序”（App Installer）。

### 手动安装

也可以逐项执行：

```powershell
winget install --exact --id Python.Python.3.14 --source winget
winget install --exact --id OliverBetz.ExifTool --source winget
winget install --exact --id Gyan.FFmpeg --source winget
winget install --exact --id 7zip.7zip --source winget
winget install --exact --id smartmontools.smartmontools --source winget
```

安装后重新打开 PowerShell，并运行：

```powershell
python .\Script\Script_DAISY_MAIN.py env-check
```

如果系统可以打开 PowerShell，但 DAISY 仍无法发现它，可先查询实际路径：

```powershell
Get-Command powershell.exe,pwsh.exe -ErrorAction SilentlyContinue |
    Select-Object Name,Source
```

随后在顶部「高级 > 工具路径」菜单中选择对应的 `.exe`。该菜单统一管理 ExifTool、
ffprobe、7-Zip、PowerShell 和 smartctl，并可恢复全部自动发现。CLI
也可以运行 `env-check --powershell-path "完整路径"`；验证成功后，GUI 会在
当前窗口中缓存该路径。

## 快速开始

普通用户直接双击根目录的 `Start_DAISY_GUI.pyw`。

CLI 供自动化和故障排查使用：

```powershell
python .\Script\Script_DAISY_MAIN.py
python .\Script\Script_DAISY_MAIN.py gui
python .\Script\Script_DAISY_MAIN.py <子命令> --help
```

首次建立正式基准时，在 GUI 中选择「DBS-11 完整档案扫描」并保留默认值：

- 完整 SHA-256：开启；
- 元数据范围：全量元数据；
- NTFS File ID：采集；
- 多目录：按添加顺序分别生成。

「独立哈希抽验比例」位于顶部「高级 > 哈希比例」菜单。它是在主 SHA-256 完成后，使用
PowerShell `Get-FileHash` 对本次实际计算的条目独立复算；默认 1%，至少
100 个，候选不足时全验。它不是主哈希的覆盖比例。

完整档案扫描可能持续几小时到几天；GUI 提供进度、实时日志和停止控制。开始前的
确认框会按分别／合并模式列出全部扫描根目录的完整路径。

## 环境、数据与硬盘功能

| 编号 | 界面角色／CLI | 用途 |
|---|---|---|
| ENV-01 | 运行环境检测／`env-check` | 检查五项外部工具、存储查询、只读冒烟和 SHA-256 |
| DBS-11 | 完整档案扫描／`full-scan` | 生成完整 SQLite 快照，支持断点续传 |
| DBS-12 | 快速档案扫描／`quick-scan` | 只登记树、大小、时间和可选 File ID |
| DBS-21 | 快照变更分析／`diff` | 对两份快照分类并判定证据等级 |
| DBS-31 | 内容哈希核验／`check-hash` | 用独立实现复算 SHA-256 |
| DBS-32 | 文件结构核验／`check-format` | 检查当前文件结构和可解析性 |
| DBS-41 | 结果报告导出／`export-report` | 导出 CSV 和 Markdown |
| DBS-91 | 「高级 > DAISY功能自检」 | 运行随附 unittest；不读取私人档案或生成正式产物 |
| STG-11 | 登记页内部检测／`storage-list` | 列出物理盘、卷标、型号及 smartctl 关联 |
| STG-12 | 硬盘信息登记／`storage-collect` | 只读采集单块物理盘并生成指纹 ZIP |
| STG-21 | 仅 CLI：`storage-verify` | 核验 ZIP 指纹、成员结构、Manifest 与 CRC |

「结果报告导出」会按输入类型说明实际产物。封存快照导出 `Tree.csv`、目录、
规范化元数据、视频 GPS、媒体流、哈希、压缩包、错误和 `Summary.csv` 等清单；
Diff 数据库导出 `Diff_summary.md`、`Diff_details.csv`、`Diff_dirs.csv`、
`Diff_hash_groups.csv` 和 `Diff_subtrees.csv`。

顶部「面板」菜单包含「环境」「数据」「硬盘」三个子菜单，色带下方另有
可折叠的功能模块按钮区；两套入口同步当前选中项，不使用左侧工作台。三行标题
分别为「环境 ENV」「数据 DBS」「硬盘 STG」。所有按钮和对应设置页标题共用同一
套六字名称，子菜单按任务性质加入分隔线、悬停和当前项高亮。

数据区包含 `DBS-11`、`DBS-12`、`DBS-21`、`DBS-31`、`DBS-32` 和 `DBS-41`；
维护编号 `DBS-91 DAISY功能自检` 位于顶部「高级」菜单，不是业务功能模块。
其中 `11/12` 表示快照采集，`21` 表示分析，`31/32` 表示核验，`41` 表示输出，
`91` 表示维护测试。

硬盘区只有一个可见功能模块：`STG-12 硬盘信息登记`。登记页中的「检测物理硬盘」
按钮会调用内部 `STG-11` 只读列盘步骤并刷新当次选择；`STG-21` 保留为仅供 CLI
使用的归档安全核验工具，不显示为 GUI 功能模块。STG 产物是 `Output\Storage`
下的独立 ZIP，不会写入 DBS 的 SQLite 快照或 Diff 数据库。

内部 `STG-11` 与可见 `STG-12` 需要管理员权限才能完整运行。管理员模式的悬停说明
会明确：GUI 中目前仅「硬盘信息登记」及其内部检测步骤需要此模式。未提权时，模块说明、
悬停说明、任务设置页、状态栏和启动确认都会提示：开启顶部管理员模式开关，并按
提示重新启动 DAISY；确认后 DAISY 通过 Windows UAC 请求提权。在硬盘信息登记页
先点击「检测物理硬盘」，再从当次清单选择目标并点击「开始任务」。
热插拔后必须重新列盘，不能长期把某个盘符或编号当作固定硬盘身份。完整 SMART
读取可能唤醒休眠硬盘，但不会启动 SMART 自检或修改磁盘、分区、卷、文件系统及
BitLocker 设置。权限不足时程序会保留实际
错误，并把已生成 ZIP 标为 `incomplete` 诊断归档，不会伪称登记完整。归档核验
`STG-21` 不访问真实硬盘，不需要管理员权限。对应 CLI 示例：

```powershell
python .\Script\Script_DAISY_MAIN.py storage-list
python .\Script\Script_DAISY_MAIN.py storage-collect --disk-number 3
python .\Script\Script_DAISY_MAIN.py storage-verify .\Output\Storage\档案.zip
```

完整存储协议见
[存储设备信息登记规格](Spec/Spec_DAISY_Storage.md)。

内容一致性核验和文件结构核验必须指定当前档案根目录。单根快照可直接选择当前文件夹；
多根快照须为每个根使用 `label=当前路径`，其中 label 必须与快照记录一致。
`--root` 接受文件夹，不接受普通文件；因此盘符或根文件夹名称变化不会依赖
快照中的旧绝对路径。

## 重要边界

### 源文件只读

DAISY 不会在被扫描的档案目录中创建、修改、重命名或删除文件。完整扫描哈希和文件结构核验会读取内容，但不会写回源文件。

Full 没有“静置窗口”或按 mtime 静默跳过近期文件的选项。建立权威基线前
应先停止对源目录写入；对已登记文件，扫描会通过哈希读前／读后 stat、
元数据读后 stat 和末次复扫识别变化，并标为 `unstable`。这不是 VSS
原子快照：枚举完成后新增的路径不会进入本次快照，长时间扫描期间也不能保证
所有文件对应同一个瞬时时刻。

### 元数据范围

完整扫描再按元数据范围分为“基础元数据”和“全量元数据”。这个选项决定
“保留多少解析结果”，不是“是否读取 ExifTool 元数据”：

- 全量元数据（默认）：写入照片、GIF、视频、音频、文档、压缩包等规范化字段；同时
  对本地所有文件尝试保存 ExifTool 原始 JSON，并为视频、音频和
  GIF 保存 ffprobe 原始 JSON；
- 基础元数据：仍解析文件并写入规范化字段，但不写 `raw_payloads`。
  视频和音频仍运行 ffprobe 以生成容器与流字段；GIF 在基础范围只运行
  ExifTool。`.jfif` 按 JPEG、`.doc` 按文档、GIF 按 `image_gif` 处理，
  GIF 的通用图像字段写入
  `photo_metadata`；真正没有规范化落点的未知类型才标为“不适用”；
- 基础元数据会显著缩小快照，但以后无法从历史快照重新解释外部工具原始字段，
  也无法判定 `metadata_extraction_changed`；
- 两种模式都不是隐私开关，规范化字段仍可能包含位置、作者、设备或序列号。

### v1.4.1 数据结构边界

v1.4.1 写出的快照和 Diff 使用 `schema_version=3`，最低阅读器版本为
`v1.4.1`。程序只读取当前结构，不读取 schema 1／2，不续传旧版 partial，
也不提供迁移命令。v1.4.0 及更早数据库如需获得 v1.4.1 的规范化结果，必须
对原档案重新扫描。

### v1.4.1 短名称与 ExifTool 超时

最终快照和 Diff 文件名精确到秒，不再保留微秒与随机运行 ID；运行态
`.partial.sqlite` 仍使用内部微秒和随机 ID 防止冲突。最终格式为：

```text
根标签_类型_[偏差标记_]日期_时间_XXXXXXXX.sqlite
```

快照状态拆分为 `database_integrity`、`scan_status`、`has_file_issues`、
`has_unstable_entries` 和 `has_enumeration_gaps`。损坏、空白或无法解析的源文件
只令 `has_file_issues=1`，数据库仍可完整封存，并在同目录额外生成同基名的
`_Issues.md`；状态不进入数据库文件名。warning／validation 单独保留，默认
不生成问题报告。

### v1.4.2 UI 优化版本

v1.4.2 只调整 GUI：队列总进度固定显示在任务阶段与本阶段进度上方，三条
进度不再随单项／多项任务切换而改变布局；“小窗运行”会收起配置、日志和
命令预览，只保留进度信息、停止与返回控制。设置、进度和日志均可独立折叠，
设置页收起后使用与其他面板一致的小号标题和紧凑留白；日志固定排列在进度下方，
不再依赖可被缩窗挤没的分隔窗格。页内品牌栏与
窗口左上角 logo 均移除，标准菜单下方直接显示三色色带。
标准菜单的悬停与任务选中项统一使用深绿色底和白字；色带下方
提供可折叠的同步功能模块区；其标题、折叠按钮与下方设置／进度／日志面板使用
同一左右基线。`ENV`、`DBS`、`STG` 固定各占一行；所有功能块
统一扩大为更宽、更高的相同尺寸并使用六字名称，标题、分区与未选中功能块文字
统一使用深绿色；选中后统一切换为深绿色底和白字，不再按分区切换颜色。功能块
不随窗口宽度重排，普通窗口也不能缩得比
完整功能模块更窄；完整编号与名称保留在悬停说明中。各任务中的
哈希抽样比例在 v1.4.2 当时统一归入页内高级设置；v1.5.0 已移入顶部
「高级 > 哈希比例」菜单。
标准菜单还提供项目目录、
结果目录、带关闭确认的退出、面板显示、关于信息与 GitHub 主页入口；命令预览默认
关闭。v1.4.2 当时由「视图」菜单打开，v1.5.0 已移入「高级」。右侧滚动条使用低
对比度米黄色滑块，不再跟随任务主题变色。按钮悬停
会显示用途说明，
目录／日志／缓存等辅助操作与开始／停止任务控制分为上下两组，辅助操作会
随可用宽度自动换行；右下角主操作统一显示“开始任务”，并使用深绿色底和白字。
停止按钮统一使用无描边的橙色警告配色。运行中退出需要先后通过普通退出确认和
停止进程确认；空闲退出只确认一次。任务正常完成或完成但需要检查时，若结果目录
存在，GUI 会询问是否立即打开；停止、硬失败、自检和依赖安装不弹出该询问。
GUI 不再注册
自定义快捷键；“就绪”、任务说明与普通表单强调统一使用深绿色，橙色仅表示
警告。页面右上角的
只读／产物提示徽标也已移除。
数据格式、元数据 profile、CLI 参数和业务任务语义均未改变；新产物继续使用
`schema_version=3` 与 `min_reader_version=1.4.1`。

### v1.5.0 数据与硬盘功能域融合

v1.5.0 将原独立硬盘工具吸收为 DAISY 的 STG 功能域，与原 DBS 数据库档案信息
功能域并列，共用统一 GUI、CLI、环境检测、管理员模式和测试入口。新增物理硬盘清单、
单盘信息登记及存储档案核验；`ENV-01` 同时检测 smartctl 和 Windows 存储查询，
可在用户逐项确认后通过固定 WinGet 包安装 smartmontools。STG 只调用 Windows
只读查询与固定的 `smartctl --scan-open --json=c`、
`smartctl -x --json=ov -d <type> <device>`，不执行自检或设置修改。

STG 归档使用独立 ZIP `archive_schema_version=3`，与 SQLite
`schema_version=3` 只是数字相同，不共享数据模型。数据库代码只把生成器版本改为
`1.5.0`；DDL、字段、约束、schema 版本、元数据 profile、扫描／Diff／核验／导出
逻辑均保持 v1.4.2 行为。由于 partial 继续要求精确匹配生成器版本，v1.4.2 的
未完成 partial 不能由 v1.5.0 续传；既有完整 schema 3 快照仍可只读使用。

同版 GUI 顶栏整理为 `文件｜面板｜高级｜视图｜帮助`，菜单栏使用浅米黄色底色。
「面板」包含环境、数据、硬盘三个子菜单；「高级」包含「工具路径」「哈希比例」、
动态「显示／隐藏命令预览」和「DAISY功能自检」。故障恢复改为「不启用／启用」
下拉项，根标签映射直接显示为文本输入，不再使用页内展开控件；所有设置下拉框在
未展开时忽略滚轮改值。STG-12 的外部简化 TXT 改为「生成／不生成」下拉选择，
默认不生成。

硬盘区只有 `STG-12 硬盘信息登记` 一个 GUI 功能模块；`STG-11` 由页内检测按钮
调用，`STG-21` 仅保留 CLI。运行进度和运行日志默认折叠；「视图」菜单对功能模块、
任务设置、运行进度和运行日志显示下一步的「展开」或「折叠」动作。各设置页标题与
功能模块按钮严格共用六字名称。小窗视图在空闲和运行时均可进入，并保留当前扫描
根文件夹的完整路径；运行日志标题旁提供「清空日志」。普通窗口在足够大的显示器上
默认以 1280×720 客户区打开；目标显示器工作区较小时自动缩小。进程启用
Per-Monitor V2 DPI 感知，窗口跨不同分辨率、工作区或 DPI 的显示器后重新约束
尺寸、位置和最小值。管理员模式以开关形式常驻功能模块标题栏，显示当前开启／关闭
状态；悬停说明指出目前仅「硬盘信息登记」及其检测步骤需要此模式。空闲且尚未
提权时可确认并通过 Windows UAC 重新启动，任务运行中不可切换。
四个工具安装入口位于 ENV-01 任务设置页的「软件安装」区，不再与目录、日志、
缓存或开始／停止按钮混排。「关于 DAISY」列出应用／DBS 生成器、DBS schema、
元数据 profile、DBS／STG 文件名布局、STG 归档 schema、完整快照最低读取器
`v1.4.1`，并说明 partial 仅允许同生成器版本续传。

增量扫描只复用满足当前 schema 的完整封存库。文件名指纹不符、SQLite 损坏、
扫描未完成、目录枚举缺口、哈希失败或 unstable 条目会拒绝作为增量来源；
单纯的 `has_file_issues=1` 不阻止其他有效哈希复用，新扫描仍会重新读取元数据。

ExifTool 的单文件超时按登记体积计算：不超过 `9 GiB` 为 90 秒，此后每个
`9 GiB` 阶梯增加 90 秒，即 `max(90, ceil(size_bytes / 9 GiB) × 90)`。
策略及实际超时会进入快照配置和错误证据；ffprobe 超时仍为 60 秒。

### 视频 GPS

Full 会把 ffprobe 容器级 `format.tags.location` 中合法的 ISO 6709
十进制度坐标写入 `video_gps_points`，同时在默认开启的 ffprobe Raw
Payload 中保留原值。经纬度会规范化为数值并校验范围；海拔可为空。

容器级 `location` 表示文件级静态位置，因此 `timestamp_seconds` 为
`NULL`。表结构允许同一视频保存多个点，也预留了点时间，但当前版本尚不
提取逐帧或连续 GPS 轨迹。Quick 不读取文件内容，所以该表保持为空。
`export-report` 会生成 `GPS_inventory_video.csv`。

由于规范化 profile 和 additive 表已变化，中断的 `.partial.sqlite`
必须同时匹配当前 DAISY 的版本、schema、profile 和 GPS 表后才允许续传。
旧版本或不是当前 profile v7 的 partial 会被明确拒绝，以免同一快照混用
两套解析语义；旧封存快照也不读取。

### 数据库文件名指纹

成功快照和 Diff 数据库以 `_XXXXXXXX.sqlite` 结尾。`XXXXXXXX` 是最终 SQLite 完整 SHA-256 的前 8 个十六进制字符：

- 用于快速发现常见损坏和文件名／内容错配；
- 只有 32 bit，不能替代完整 SHA-256、数字签名或外部校验清单。

### 公开分享与隐私

快照和报告可能包含档案根路径、文件名、作者、GPS、设备信息、序列号及
Raw Payload。不要把真实快照、Diff 数据库或未经检查的报告上传到公开仓库、
Issue 或其他公共位置；需要反馈问题时，应先移除或替换私人路径和元数据。
STG ZIP 还可能包含卷标、卷 GUID、挂载路径、PNP Device ID、计算机名和
BitLocker 状态，同样不得未经检查公开分享。

## 输出

运行时按需创建以下目录；它们不会提交到 Git：

| 目录 | 内容 |
|---|---|
| `Output\Snapshots\` | Full／Quick SQLite 快照 |
| `Output\Diffs\` | Diff SQLite 数据库 |
| `Output\Reports\` | 环境、格式、哈希和导出报告 |
| `Output\Storage\` | 单硬盘只读信息 ZIP 与可选简化 TXT |

## 项目结构

当前版本控制中的发布结构如下（不含 `.git` 与运行时 `Output`）：

```text
DAISY\
├─ .gitattributes
├─ .gitignore
├─ LICENSE
├─ README.md
├─ Start_DAISY_GUI.pyw
├─ Spec\
│  ├─ Spec_DAISY_Technical.md
│  ├─ Spec_DAISY_Version_Evolution.md
│  └─ Spec_DAISY_Storage.md
└─ Script\
   ├─ Script_DAISY_Install_Python.ps1
   ├─ Script_DAISY_MAIN.py
   ├─ Script_DAISY_GUI.py
   ├─ Lib\
   │  ├─ Script_DAISY_Lib_01_Core.py
   │  ├─ Script_DAISY_Lib_02_Meta.py
   │  ├─ Script_DAISY_Lib_03_Hash.py
   │  ├─ Script_DAISY_Lib_04_Diff.py
   │  ├─ Script_DAISY_Lib_STG_01_Core.py
   │  ├─ Script_DAISY_Lib_STG_02_Windows.py
   │  ├─ Script_DAISY_Lib_STG_03_Smartctl.py
   │  ├─ Script_DAISY_Lib_STG_04_Service.py
   │  └─ Script_DAISY_Lib_STG_05_Archive.py
   ├─ Tool\
   │  ├─ Script_DAISY_Tool_ENV_01_Env_Check.py
   │  ├─ Script_DAISY_Tool_DBS_11_Full_Scan.py
   │  ├─ Script_DAISY_Tool_DBS_12_Quick_Scan.py
   │  ├─ Script_DAISY_Tool_DBS_21_Diff.py
   │  ├─ Script_DAISY_Tool_DBS_31_Check_Hash.py
   │  ├─ Script_DAISY_Tool_DBS_32_Check_Format.py
   │  ├─ Script_DAISY_Tool_DBS_41_Export_Report.py
   │  ├─ Script_DAISY_Tool_STG_11_List_Disks.py
   │  ├─ Script_DAISY_Tool_STG_12_Collect.py
   │  └─ Script_DAISY_Tool_STG_21_Verify_Archive.py
   └─ Test\
      ├─ Script_DAISY_Test_Tree.py
      ├─ Script_DAISY_Test_Unit.py
      ├─ Script_DAISY_Test_No_Clobber.py
      ├─ Script_DAISY_Test_Storage_Unit.py
      └─ Script_DAISY_Test_Storage_Read_Only.py
```

完整数据库模型、不变量、哈希和 Diff 语义见
[技术规格](Spec/Spec_DAISY_Technical.md)，存储归档见
[存储设备信息登记规格](Spec/Spec_DAISY_Storage.md)；从 `Kit_AL v1.0.2` 到当前版本的
阶段变化见[版本演化规格](Spec/Spec_DAISY_Version_Evolution.md)。

## 测试

测试只写入系统临时目录，不需要私人媒体样本。格式校验回归会在运行时
生成合法的微型 PNG，再截断其 IEND 块验证损坏检出；测试使用上述已安装的
外部工具，但不会下载依赖。在项目根目录运行全部自动化测试：

```powershell
python -B -m unittest discover -s .\Script\Test -p "Script_DAISY_Test_*.py" -v
```

也可以从 GUI 顶部「高级 > DAISY功能自检」运行 `DBS-91`。它调用同一套
`unittest`，结果实时写入 GUI 日志，不作为业务任务，也不生成正式产物。

也可以分别运行数据库与存储测试文件：

```powershell
python -B .\Script\Test\Script_DAISY_Test_Unit.py
python -B .\Script\Test\Script_DAISY_Test_No_Clobber.py
python -B .\Script\Test\Script_DAISY_Test_Storage_Unit.py
python -B .\Script\Test\Script_DAISY_Test_Storage_Read_Only.py
```

`Script_DAISY_Test_Tree.py` 是 Diff 合成场景生成器，可用以下命令查看场景：

```powershell
python -B .\Script\Test\Script_DAISY_Test_Tree.py --list
```

所有业务任务均不导入 `Script\Test\`。存储测试使用合成设备与系统临时目录，
默认不会读取真实硬盘。测试层可以独立移除而不影响 DAISY
业务功能；缺少测试文件时，「DAISY功能自检」入口会显示不可用提示。

GUI 的“清理缓存”会先确认，然后删除项目目录内可安全重建的 `__pycache__`、
`.pytest_cache`、`.mypy_cache`、`.ruff_cache` 和独立 `.pyc`／`.pyo`
文件，并把所有页面的参数、目录队列、硬盘选择、工具路径、日志与三条进度恢复
为首次启动状态。它不会跟随链接，不会进入 `.git`、虚拟环境、`node_modules`
或 `Output`，也不会删除快照、Diff、报告或未完成数据库。

## 问题反馈

反馈问题前请先运行「ENV-01 运行环境检测」和「DBS-91 DAISY功能自检」，并说明 Windows、Python 和
外部工具版本。可以提供经过脱敏的错误文本和最小复现步骤，但不要附带真实
快照数据库、私人媒体或未经检查的 Raw Payload。

## 许可证

DAISY 以 [MIT License](LICENSE) 开源。该许可证适用于本仓库中的 DAISY
代码与文档；独立安装的 ExifTool、FFmpeg、7-Zip 和 smartmontools 不属于本仓库内容，仍
分别遵循各自的许可证。
