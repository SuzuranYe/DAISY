# DAISY

**Database for Archive Integrity by Suzuran Ye**

版本：**v1.4.2**

许可证：**MIT**

DAISY 是面向摄影素材库与个人档案的本地清点、登记、核验和对比工具。每次扫描都会生成独立、自描述、不可回写的 SQLite 快照；扫描源目录保持只读，后续核验、对比和导出也不会修改既有快照。

## 主要能力

- 完整扫描文件树、时间、规范化元数据、可选原始元数据全文、File ID 和 SHA-256；
- 快速扫描目录与文件信息，不读取文件内容；
- 校验当前文件的结构和可解析性；
- 独立复算 SHA-256，检查当前磁盘是否仍与快照一致；
- 对比两份快照，区分内容变化、移动、复制、元数据提取差异和证据不足；
- 把快照或 Diff 数据库导出为 CSV 和 Markdown 报告；
- 使用 Tkinter／ttk 图形界面，不需要安装额外 Python 包；多目录任务单独显示
  队列总进度、当前任务阶段和本阶段工作量，运行时可切换到只保留进度与停止
  控制的小窗视图；左侧导航、任务设置、运行进度和运行日志均可主动折叠。

## 运行环境

DAISY 仅支持 Windows，当前版本在 Python 3.14 上完成验证。

| 依赖 | 最低版本 | 使用范围 |
|---|---:|---|
| Python | 3.14 | GUI 和全部任务 |
| ExifTool | 13 | 环境监测、完整扫描、文件结构核验 |
| ffprobe（随 FFmpeg 安装） | 8 | 环境监测、完整扫描、文件结构核验 |
| 7-Zip | 24 | 环境监测、完整扫描、文件结构核验 |
| PowerShell `Get-FileHash` | Windows 内置 | 环境监测、完整扫描、SHA-256 独立复算 |

Quick 快速扫描除 Python 外不依赖 ExifTool、ffprobe、7-Zip 或 PowerShell。
ExifTool、FFmpeg 和 7-Zip 由用户通过 WinGet 独立安装，DAISY 不捆绑或
再分发这些程序；它们分别遵循各自的许可证。

DAISY 兼容 Windows PowerShell 5.1 与 PowerShell 7.x。自动发现顺序为：
手动路径、当前进程的 `PATH`、两个系列的 Windows 常规安装位置。便携版或
自定义目录仍可在 GUI 高级选项中选择，也可通过 CLI 的
`--powershell-path` 指定。

### 自动安装依赖

如果双击 `Start_DAISY_GUI.pyw` 没有反应，通常是尚未安装 Python 或 `.pyw`
文件关联不可用。请先在项目根目录打开 PowerShell，再运行只负责安装
Python 3.14 的引导脚本：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Script\Script_DAISY_Install_Python.ps1
```

脚本会先说明用途，并且只有明确输入 `y` 后才通过 WinGet 安装或更新
`Python.Python.3.14`；它不会安装 ExifTool、FFmpeg 或 7-Zip。安装完成后
请重新打开 DAISY。

Python 已可用时，进入「ENV-00 环境监测」运行监测。页面会显示本机实际发现的
版本；若缺少 ExifTool、ffprobe 或 7-Zip，会出现“下载并安装缺失工具”
按钮。用户再次确认后，GUI 才会通过 WinGet 的固定白名单包逐项安装：

- `OliverBetz.ExifTool`：读取照片／视频元数据并参与文件结构核验；
- `Gyan.FFmpeg`：提供 ffprobe，用于读取音视频流、校验媒体容器，并在
  全量元数据模式下保留视频、音频和 GIF 的完整 JSON；
- `7zip.7zip`：读取并测试 7z、RAR、TAR 等归档格式。

GUI 不提供任意包名输入，也不会自动安装 PowerShell。安装队列结束后会刷新
当前进程的 PATH 并重新运行环境监测。若新程序仍未被发现，请关闭 DAISY
后重新打开。如果系统找不到 `winget`，请先从 Microsoft Store 安装或更新
“应用安装程序”（App Installer）。

### 手动安装

也可以逐项执行：

```powershell
winget install --exact --id Python.Python.3.14 --source winget
winget install --exact --id OliverBetz.ExifTool --source winget
winget install --exact --id Gyan.FFmpeg --source winget
winget install --exact --id 7zip.7zip --source winget
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

随后在「ENV-00 环境监测」或「ENV-11 完整扫描」的高级选项中选择对应的 `.exe`。CLI
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

首次建立正式基准时，在 GUI 中选择“ENV-11 完整扫描”并保留默认值：

- 完整 SHA-256：开启；
- 元数据范围：全量元数据；
- NTFS File ID：采集；
- 多目录：按添加顺序分别生成。

“独立哈希抽验比例”位于高级设置。它是在主 SHA-256 完成后，使用
PowerShell `Get-FileHash` 对本次实际计算的条目独立复算；默认 1%，至少
100 个，候选不足时全验。它不是主哈希的覆盖比例。

扫描可能持续数小时；GUI 提供进度、实时日志和停止控制。

## 七项业务任务与项目自检

| 编号 | GUI／CLI | 用途 |
|---|---|---|
| ENV-00 | 环境监测／`env-check` | 检查四项外部工具、版本、只读冒烟和 SHA-256 |
| ENV-01 | 项目自检／GUI 维护入口 | 运行随附 unittest；不读取私人档案或生成正式产物 |
| ENV-11 | 完整扫描／`full-scan` | 生成完整 SQLite 快照，支持断点续传 |
| ENV-12 | 快速扫描／`quick-scan` | 只登记树、大小、时间和可选 File ID |
| DB-21 | 快照变更分析／`diff` | 对两份快照分类并判定证据等级 |
| DB-31 | 内容一致性核验／`check-hash` | 用独立实现复算 SHA-256 |
| DB-32 | 文件结构核验／`check-format` | 检查当前文件结构和可解析性 |
| DB-41 | 导出报告／`export-report` | 导出 CSV 和 Markdown |

左侧以减淡的绿、黄、红分别标出“环境”“数据库”“硬盘”三区。`ENV-` 表示
环境与扫描任务，`DB-` 表示数据库任务；硬盘区为后续存储设备模块预留
`STG-` 前缀，但 v1.4.2 不创建空任务页，也不提前加入任何硬盘业务功能。

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

v1.4.2 只调整 GUI：多项任务的队列总进度固定显示在任务阶段与本阶段进度
上方；“小窗运行”会收起配置、侧栏、日志和命令预览，只保留进度信息、停止
与返回控制。设置、进度、日志和左侧导航均可独立折叠，日志固定排列在进度
下方，不再依赖可被缩窗挤没的分隔窗格。顶部使用一笔向左右展开的铃兰耳朵
线稿与 DAISY 组合标志，并以 Palatino Linotype Italic 列出全称和版本号；
标准菜单提供项目目录、结果目录、退出、面板显示、关于信息与 GitHub 主页
入口，命令预览默认关闭并可由“视图”菜单打开。按钮悬停会显示用途说明，
底部操作会随可用宽度自动换行。GUI 不再注册自定义快捷键，页面右上角的
只读／产物提示徽标也已移除。
数据格式、元数据 profile、CLI 参数和业务任务语义均未改变；新产物继续使用
`schema_version=3` 与 `min_reader_version=1.4.1`。

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

## 输出

运行时按需创建以下目录；它们不会提交到 Git：

| 目录 | 内容 |
|---|---|
| `Output\Snapshots\` | Full／Quick SQLite 快照 |
| `Output\Diffs\` | Diff SQLite 数据库 |
| `Output\Reports\` | 环境、格式、哈希和导出报告 |

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
│  └─ Spec_DAISY_Version_Evolution.md
└─ Script\
   ├─ Script_DAISY_Install_Python.ps1
   ├─ Script_DAISY_MAIN.py
   ├─ Script_DAISY_GUI.py
   ├─ Lib\
   │  ├─ Script_DAISY_Lib_01_Core.py
   │  ├─ Script_DAISY_Lib_02_Meta.py
   │  ├─ Script_DAISY_Lib_03_Hash.py
   │  └─ Script_DAISY_Lib_04_Diff.py
   ├─ Tool\
   │  ├─ Script_DAISY_Tool_10_Env_Check.py
   │  ├─ Script_DAISY_Tool_11_Full_Scan.py
   │  ├─ Script_DAISY_Tool_12_Quick_Scan.py
   │  ├─ Script_DAISY_Tool_21_Diff.py
   │  ├─ Script_DAISY_Tool_22_Check_Hash.py
   │  ├─ Script_DAISY_Tool_23_Check_Format.py
   │  └─ Script_DAISY_Tool_31_Export_Report.py
   └─ Test\
      ├─ Script_DAISY_Test_Tree.py
      ├─ Script_DAISY_Test_Unit.py
      └─ Script_DAISY_Test_No_Clobber.py
```

完整数据模型、不变量、哈希和 Diff 语义见
[技术规格](Spec/Spec_DAISY_Technical.md)；从 `Kit_AL v1.0.2` 到当前版本的
阶段变化见[版本演化规格](Spec/Spec_DAISY_Version_Evolution.md)。

## 测试

测试只写入系统临时目录，不需要私人媒体样本。格式校验回归会在运行时
生成合法的微型 PNG，再截断其 IEND 块验证损坏检出；测试使用上述已安装的
外部工具，但不会下载依赖。在项目根目录运行全部自动化测试：

```powershell
python -B -m unittest discover -s .\Script\Test -p "Script_DAISY_Test_*.py" -v
```

也可以进入 GUI 的“ENV-01 项目自检”页点击“运行项目自检”。它调用同一套
`unittest`，结果实时写入 GUI 日志，不作为第八项业务任务，也不生成正式产物。

也可以分别运行两个测试套件：

```powershell
python -B .\Script\Test\Script_DAISY_Test_Unit.py
python -B .\Script\Test\Script_DAISY_Test_No_Clobber.py
```

`Script_DAISY_Test_Tree.py` 是 Diff 合成场景生成器，可用以下命令查看场景：

```powershell
python -B .\Script\Test\Script_DAISY_Test_Tree.py --list
```

七项业务任务不导入 `Script\Test\`。测试层可以独立移除而不影响 DAISY
业务功能；缺少测试文件时，“ENV-01 项目自检”页的运行按钮会禁用。

GUI 左下角的“清理缓存”只删除项目目录内可安全重建的 `__pycache__`、
`.pytest_cache`、`.mypy_cache`、`.ruff_cache` 和独立 `.pyc`／`.pyo`
文件，并清空当前窗口缓存的工具路径。每个实际删除的目录或文件都会写入
运行日志。它不会跟随链接，不会进入 `.git`、虚拟环境、`node_modules`
或 `Output`，也不会删除快照、Diff、报告、运行日志或未完成数据库。

## 问题反馈

反馈问题前请先运行“ENV-00 环境监测”和“ENV-01 项目自检”，并说明 Windows、Python 和
外部工具版本。可以提供经过脱敏的错误文本和最小复现步骤，但不要附带真实
快照数据库、私人媒体或未经检查的 Raw Payload。

## 许可证

DAISY 以 [MIT License](LICENSE) 开源。该许可证适用于本仓库中的 DAISY
代码与文档；独立安装的 ExifTool、FFmpeg 和 7-Zip 不属于本仓库内容，仍
分别遵循各自的许可证。
