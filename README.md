# DAISY

**Database for Archive Integrity by Suzuran Ye**

版本：**v1.3.3**

许可证：**MIT**

DAISY 是面向摄影素材库与个人档案的本地清点、登记、核验和对比工具。每次扫描都会生成独立、自描述、不可回写的 SQLite 快照；扫描源目录保持只读，后续核验、对比和导出也不会修改既有快照。

## 主要能力

- 完整登记文件树、时间、元数据、Raw Payload、File ID 和 SHA-256；
- 快速清点目录与文件信息，不读取文件内容；
- 校验当前文件的结构和可解析性；
- 独立复算 SHA-256，检查当前磁盘是否仍与快照一致；
- 对比两份快照，区分内容变化、移动、复制、元数据提取差异和证据不足；
- 把快照或 Diff 数据库导出为 CSV 和 Markdown 报告；
- 使用 Tkinter／ttk 图形界面，不需要安装额外 Python 包。

## 运行环境

DAISY 仅支持 Windows，当前版本在 Python 3.14 上完成验证。

| 依赖 | 最低版本 | 使用范围 |
|---|---:|---|
| Python | 3.14 | GUI 和全部任务 |
| ExifTool | 13 | 环境检测、完整登记、格式校验 |
| ffprobe（随 FFmpeg 安装） | 8 | 环境检测、完整登记、格式校验 |
| 7-Zip | 24 | 环境检测、完整登记、格式校验 |
| PowerShell `Get-FileHash` | Windows 内置 | 环境检测、完整登记、SHA-256 独立复算 |

Quick 快速清点除 Python 外不依赖 ExifTool、ffprobe、7-Zip 或 PowerShell。
ExifTool、FFmpeg 和 7-Zip 由用户通过 WinGet 独立安装，DAISY 不捆绑或
再分发这些程序；它们分别遵循各自的许可证。

DAISY 兼容 Windows PowerShell 5.1 与 PowerShell 7.x。自动发现顺序为：
手动路径、当前进程的 `PATH`、两个系列的 Windows 常规安装位置。便携版或
自定义目录仍可在 GUI 高级选项中选择，也可通过 CLI 的
`--powershell-path` 指定。

### 自动安装依赖

如果双击 `Start_DAISY_GUI.pyw` 没有反应，通常是尚未安装 Python 或 `.pyw` 文件关联不可用。请先在项目根目录打开 PowerShell，然后运行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Install_DAISY_Dependencies.ps1
```

脚本会依次说明每个依赖的用途，并在安装或更新每一项之前单独询问：

- `Python.Python.3.14`：运行 GUI 和全部任务；
- `OliverBetz.ExifTool`：读取照片／视频元数据并参与格式校验；
- `Gyan.FFmpeg`：提供 ffprobe，用于读取音视频流和校验媒体容器；
- `7zip.7zip`：读取并测试 7z、RAR、TAR 等归档格式。

每一项只有明确输入 `y` 才会执行，其他输入只跳过当前项，不影响后续依赖的确认。逐项处理后，脚本会运行 DAISY 环境检测。若当前 PowerShell 尚未取得新的 PATH，请关闭终端和 DAISY，再重新打开。

如果系统找不到 `winget`，请先从 Microsoft Store 安装或更新“应用安装程序”（App Installer）。

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

随后在「10 环境检测」或「11 完整登记」的高级选项中选择对应的 `.exe`。CLI
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

首次建立正式基准时，在 GUI 中选择“11 完整登记”并保留默认值：

- 完整 SHA-256：开启；
- Raw Payload：保留；
- NTFS File ID：采集；
- 多目录：按添加顺序分别生成。

扫描可能持续数小时；GUI 提供进度、实时日志和停止控制。

## 七项任务

| 编号 | GUI／CLI | 用途 |
|---|---|---|
| 10 | 环境检测／`env-check` | 检查四项外部工具、版本、只读冒烟和 SHA-256 |
| 11 | 完整登记／`full-scan` | 生成完整 SQLite 快照，支持断点续传 |
| 12 | 快速清点／`quick-scan` | 只登记树、大小、时间和可选 File ID |
| 21 | 快照对比／`diff` | 对两份快照分类并判定证据等级 |
| 22 | 哈希校验／`check-hash` | 用独立实现复算 SHA-256 |
| 23 | 格式校验／`check-format` | 检查当前文件结构和可解析性 |
| 31 | 导出报告／`export-report` | 导出 CSV 和 Markdown |

哈希校验和格式校验必须指定当前档案根目录。单根快照可直接选择当前文件夹；
多根快照须为每个根使用 `label=当前路径`，其中 label 必须与快照记录一致。
`--root` 接受文件夹，不接受普通文件；因此盘符或根文件夹名称变化不会依赖
快照中的旧绝对路径。

## 重要边界

### 源文件只读

DAISY 不会在被扫描的档案目录中创建、修改、重命名或删除文件。Full 哈希和格式校验会读取内容，但不会写回源文件。

### Raw Payload

Raw Payload 控制是否在快照中保留外部工具返回的完整原始 JSON，不是“是否提取元数据”的总开关：

- 默认开启：同时保留规范化元数据和原始后端字段；
- 关闭：ExifTool／ffprobe 仍会运行，但不写入 `raw_payloads`；
- 它不是隐私开关，规范化列仍可能包含位置、作者、设备或序列号。

### 视频 GPS

Full 会把 ffprobe 容器级 `format.tags.location` 中合法的 ISO 6709
十进制度坐标写入 `video_gps_points`，同时在默认开启的 ffprobe Raw
Payload 中保留原值。经纬度会规范化为数值并校验范围；海拔可为空。

容器级 `location` 表示文件级静态位置，因此 `timestamp_seconds` 为
`NULL`。表结构允许同一视频保存多个点，也预留了点时间，但当前版本尚不
提取逐帧或连续 GPS 轨迹。Quick 不读取文件内容，所以该表保持为空。
`export-report` 会生成 `GPS_inventory_video.csv`。profile v1 的既有
快照不会被回写，导出时也不会凭空生成这一页。

由于规范化 profile 和 additive 表已变化，中断的 `.partial.sqlite`
必须同时匹配当前 DAISY 的版本、schema、profile 和 GPS 表后才允许续传。
改动前同为 v1.3.3、但仍使用 profile v1 的 partial 也会被明确拒绝，以免
同一快照混用两套解析语义。封存快照不受此限制。

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
├─ Install_DAISY_Dependencies.ps1
├─ LICENSE
├─ README.md
├─ Start_DAISY_GUI.pyw
├─ Spec\
│  └─ Spec_DAISY_Technical.md
└─ Script\
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

完整数据模型、不变量、哈希和 Diff 语义见[技术规格](Spec/Spec_DAISY_Technical.md)。

## 测试

测试只写入系统临时目录，不需要私人媒体样本。格式校验回归会在运行时
生成合法的微型 PNG，再截断其 IEND 块验证损坏检出；测试使用上述已安装的
外部工具，但不会下载依赖。在项目根目录运行全部自动化测试：

```powershell
python -B -m unittest discover -s .\Script\Test -p "Script_DAISY_Test_*.py" -v
```

也可以在 GUI 的“10 环境检测”页点击“运行项目自检”。它调用同一套
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
业务功能；缺少测试文件时，GUI 的“运行项目自检”按钮会禁用。

## 问题反馈

反馈问题前请先运行“10 环境检测”和项目自检，并说明 Windows、Python 和
外部工具版本。可以提供经过脱敏的错误文本和最小复现步骤，但不要附带真实
快照数据库、私人媒体或未经检查的 Raw Payload。

## 许可证

DAISY 以 [MIT License](LICENSE) 开源。该许可证适用于本仓库中的 DAISY
代码与文档；独立安装的 ExifTool、FFmpeg 和 7-Zip 不属于本仓库内容，仍
分别遵循各自的许可证。
