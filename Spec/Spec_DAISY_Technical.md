# DAISY v1.6.8 技术规格

- 状态：**v1.6.8 界面与核验优化开发规范**。
- 当前开发版本：**v1.6.8**；最新正式 Git 标签：**v1.6.7**。
- 版本沿革：**v1.4.1 → v1.6.4 → v1.6.6 → v1.6.7 → v1.6.8（开发中）**。
  v1.6.4 与 v1.6.6 虽曾被指定为长期稳定基线，现均因 GUI 哈希功能确认失效而由
  v1.6.7 取代；v1.6.8 继承该修复并继续优化界面与核验流程。
- 发布沿革：v1.6.1 仅为未打标签的阶段性修改，v1.6.2 首次收敛该阶段能力，v1.6.3
  继续完成 UI、默认窗口、文案和交互一致性整理；v1.6.4 修正核验工具的环境检测门控，
  并把上述成果收敛为长期稳定基线；v1.6.5 维护界面可见性、控件对齐和说明文案；
  v1.6.6 收敛界面细节、文档和内部冗余；v1.6.7 修复 GUI 哈希工作进程无法启动、
  进程退出异常及默认功能自检可能拖垮桌面的致命问题；v1.6.8 增加无数据库核验、
  数据库原路径直用、Python 环境管理，并统一主要按钮、目录池和运行结束后的面板行为。
- 本文定义 DAISY 的现行统一语义：档案数据功能登记、比较、核验并解析文件档案快照，硬盘信息
  只读登记物理硬盘信息；两者共用应用外壳，但保持独立数据模型。两个功能域的完整
  技术约束均以本文为准。
- 安装、启动与常用工作流见项目根目录的 [README](../README.md)。
- 从 `Kit_AL v1.0.2` 到当前版本的阶段变化见
  [版本演进](Spec_DAISY_Version_Evolution.md)。

## 一、功能域与权威边界

DAISY v1.6.8 的信息能力分为两个并列功能域：

- **档案数据**：登记文件树、大小、时间、NTFS-ID、元数据、哈希和
  快照变化，保存为 SQLite 快照或 Diff，并支持核验与导出。
- **硬盘信息**：只读获取物理盘、分区、卷、Windows 存储属性和 smartctl
  原始证据，保存为独立 ZIP，并支持归档核验。

统一 GUI、CLI、运行环境检测、管理员模式与测试入口只负责调度和交互。硬盘信息功能不
写入档案快照 SQLite，档案数据功能也不把物理盘资料嵌入快照；当前版本不自动建立文件条目
与物理硬盘档案之间的关联。

GUI 顶部使用 6 个六字完整入口，并按档案工作流、设备、环境的顺序排列；六个入口与页面
右下角任务操作使用同一请求尺寸和深色边框。环境页的 7 个检测／安装项使用相同按钮宽度，
并在可用宽度内自动换行。设置页、进度和报告
仍使用下表中的完整功能名。统一入口组合既有档案能力，冻结入口继续供旧脚本调用：

| GUI 入口 | 现行 CLI | 编排脚本 | 冻结兼容入口 |
|---|---|---|---|
| 运行环境检测 | `env-check` | `Script_DAISY_Module_Environment_Check.py` | 无 |
| 档案扫描建库 | `scan` | `Script_DAISY_Module_Scan.py` | 完整扫描 `full-scan`、快速扫描 `quick-scan` |
| 档案快照对比 | `diff` | `Script_DAISY_Module_Snapshot_Diff.py` | 同一入口 |
| 档案数据核验 | `verify` | `Script_DAISY_Module_Verify.py` | 哈希核验 `check-hash`、格式校验 `check-format` |
| 档案数据解析 | `parse-db` | `Script_DAISY_Module_Parse.py` | 数据解析 `export-report` |
| 硬盘信息登记 | `storage-collect` | `Script_DAISY_Module_Storage_Collect.py` | 同一入口 |

`DAISY 功能自检` 只属于「高级」维护入口，通过 `unittest` 运行默认安全测试；
会反复创建完整窗口的真实 Tk／桌面测试代码已从自动化套件删除，视觉验收按需手动执行。
自检不占用功能模块或 Module 脚本。`storage-list` 是同一硬盘信息登记脚本的 `--list` 内部
准备模式，也不另建脚本。归档核验不再提供独立用户命令；创建 ZIP 后仍由
硬盘信息登记在底层自动执行同等完整核验。

`scan` 与 `verify` 的统一编排脚本分别组合完整／快速扫描与
哈希／格式核验。`storage-list` 是硬盘信息登记页内准备模式。冻结命令保留原参数和业务投影，
但 GUI 不重复显示这些入口。

### 1.1 用户可见文字规范

GUI、CLI 帮助、报告、README 与现行规格使用同一套用户术语：

| 对象 | 统一写法 | 说明 |
|---|---|---|
| 六个主功能 | 运行环境检测、档案扫描建库、档案快照对比、档案数据核验、档案数据解析、硬盘信息登记 | 页面标题与顶部入口都使用完整六字名称；顶部顺序为档案扫描建库、档案快照对比、档案数据核验、档案数据解析、硬盘信息登记、运行环境检测 |
| 硬盘准备动作 | 检测硬盘 | 「硬盘信息登记」是功能，「检测硬盘」是选择设备前的动作 |
| 数据库准备动作 | 解析数据库 | 「档案数据解析」是功能，「解析数据库」是读取类型、版本和模块能力的动作 |
| 数据库结构版本 | 数据库结构版本 3/4 | 面向用户时使用完整中文；`schema 3`、`schema 4` 只用于技术合同、字段和命令输出 |
| 数据库来源版本 | 数据库生成程序版本 | 报告自身版本写作「报告生成程序版本」；不单独使用含义不明的「生成器」 |
| 已完成数据库 | 封存快照 | `snapshot`、`sealed` 只在代码、协议或技术字段中出现 |
| 未完成数据库 | 未完成快照 | 首次解释时可附扩展名 `.partial.sqlite`；不把 `partial` 单独当中文名词 |
| 根身份 | 根目录名 | `root_label`、`label` 只在代码、CLI 语法或数据库字段中出现 |
| NTFS 文件身份设置 | 文件标识 | 按钮为「不采集／NTFS-ID」；NTFS-ID 由 NTFS 卷序列号与文件索引组成，数据库字段和历史文档中的 `File ID` 指同一证据 |
| 扫描后独立重算 | 哈希复检 | 「抽样」只描述选择比例，不作为该功能的页面名称 |
| DAISY 自带的 ZIP/OOXML/PDF 校验 | 基本校验 | GUI 名称；内部键仍为 `verify_builtin`，不包含 ExifTool、ffprobe、7-Zip 或 rawpy/LibRaw |
| 档案数据解析范围 | 摘要、全部、自定义 | 内部值仍为 `human-summary`、`full-audit`、`custom` |
| 档案数据解析项目 | 数据模块 | 不使用含义重叠的「导出模块」或「内容模块」 |
| 数据模块状态 | 已选择、可选择、0 条记录、无可用记录、版本不兼容、结构异常 | 绿色表示已选择，黄色表示可选择，灰色表示当前不可用；0 条记录表示模块存在但为空，不显示笼统的「可导出」字样 |
| 可直接阅读的导出 | HTML 阅读报告、Excel 工作簿 | 不在界面中使用偏技术化的「人读报告」 |
| 问题伴随文件 | 问题报告 | `_Issues.md` 是固定文件后缀；普通说明不单独使用 `Issues` |
| 可复核异常 | 文件问题、问题记录 | 面向用户不把工具故障或未处理条目写成「文件错误」；`errors`、`error_code` 等数据库字段保留技术原名 |
| 元数据工具原文 | 工具原始输出 | `raw_payloads`、`raw payload` 只用于表名、字段或技术合同 |
| 导出运行说明 | 运行清单 | 首次技术说明可附英文 `manifest`；普通界面和报告不单独使用英文 |
| 未执行或未记录 | `NULL（原因）` | 不得写成 `0`、通过、不适用或文件问题 |
| 对当前文件不适用 | 不适用 | 只统计数量，不得写成失败、不可用或文件问题 |
| 能力检测状态 | 等待检测、版本号、不可用 | 检测成功时直接显示实际版本，不使用笼统的「可用」；「等待检测」不等于「不可用」，「不可用」也不等于「未安装」 |
| 机器分析格式 | CSV 数据表、JSONL 数据流 | 不使用「CSV 技术表」或「JSONL 原始流」 |
| 扫描退出动作 | 保存并退出 | 按钮、状态和确认标题使用该名称；说明文字可补充其会安全保存已完成进度 |
| 未完成扫描提示 | 续传提示 | 只提示存在未完成快照；用户确认后才填入续传设置，不自动开始任务 |
| 继续未完成扫描 | 续传 | 用户动作、界面和提示统一使用「续传」；自动修复异常中断状态或重建外部工具会话不称为续传 |
| 报告生成身份 | 报告生成工具、报告生成程序版本 | 与输入数据库的「数据库生成程序版本」分开 |
| 问题报告计数 | 受影响文件、问题记录 | 不把所有受影响文件直接称为损坏文件 |

用户可见说明采用简体中文短句，先说明作用，再说明必要边界；不重复按钮颜色、控件形状或
标题已经表达的信息。技术标识必须使用代码样式，并在首次出现时给出中文含义。动态状态只
描述已有证据，不把未检测写成不可用，也不把不可用写成未安装。悬停提示同时绑定字段标题、
文字和实际控件，并按当前字体像素宽度自动换行；不得依赖手工插入换行来凑版面。

冻结兼容 CLI 仍保留既有英文参数名、取值和历史帮助文字；本节规范面向现行 GUI、当前
命令入口的新增文字和阅读报告，不得借文字统一之名破坏旧脚本协议。

文档解释意图和不变量，代码保存容易漂移的精确定义：

| 内容 | 最终权威 |
|---|---|
| schema 3 快照 SQLite DDL | `Script\Lib\Script_DAISY_Lib_Snapshot_Core.py` 中的 `SNAPSHOT_DDL` |
| schema 4 DDL、会话、处理尝试、任务占用、续传与发布 | `Script\Lib\Script_DAISY_Lib_Scan_State.py` |
| schema 4 未完成快照创建、续传预览、任务占用心跳与运行编排 | `Script\Lib\Script_DAISY_Lib_Scan_Runtime.py` |
| Diff SQLite DDL | `Script\Lib\Script_DAISY_Lib_Snapshot_Diff.py` 中的 `DIFF_DDL` |
| 规范化元数据取值链 | `Script\Lib\Script_DAISY_Lib_Metadata.py` |
| 哈希、schema 4 隔离工作进程、超时、复用和哈希复检 | `Script\Lib\Script_DAISY_Lib_File_Hash.py` |
| 数据库类型、schema、模块能力与业务投影 | `Script\Lib\Script_DAISY_Lib_Database_Reader.py` |
| 核验快照准入、文件状态／哈希、格式判据和报告服务 | `Script\Lib\Script_DAISY_Lib_Snapshot_Verify.py` |
| 档案数据解析模块注册表、CSV 与旧 Excel 写入器 | `Script\Lib\Script_DAISY_Lib_Database_Parse.py` |
| schema 3/4 快照问题的只读分析与分板块 Markdown | `Script\Lib\Script_DAISY_Lib_Snapshot_Issues.py` |
| 档案数据核验阶段、控制、报告和退出码投影 | `Script\Lib\Script_DAISY_Lib_Verify_Runtime.py` |
| ExifTool/ffprobe/7-Zip 精确子进程监督 | `Script\Lib\Script_DAISY_Lib_Verify_Tools.py` |
| rawpy/LibRaw 隔离能力与每文件深度解码 | `Script\Lib\Script_DAISY_Lib_Raw_Verify.py` |
| RAW 续传 JSONL、最终伴随 JSON 与 Markdown 投影 | `Script\Lib\Script_DAISY_Lib_Raw_Evidence.py` |
| 档案数据解析稳定字段与流式业务投影 | `Script\Lib\Script_DAISY_Lib_Parse_Projection.py` |
| 档案数据解析技术写入器、运行清单、暂存与不覆盖发布 | `Script\Lib\Script_DAISY_Lib_Parse_Runtime.py` |
| 档案数据解析自包含 HTML 与流式 XLSX 阅读投影 | `Script\Lib\Script_DAISY_Lib_Parse_Human.py` |
| 外部工具统一故障证据、受控一次性进程与连续失败熔断 | `Script\Lib\Script_DAISY_Lib_Tool_Runtime.py` |
| Python 可选运行能力统一探测 | `Script\Lib\Script_DAISY_Lib_Environment_Capabilities.py` |
| CLI 分发、现行脚本名 | `Script\Script_DAISY_CLI.py` 中的 `COMMANDS` |
| CLI 参数及默认值 | 上表对应任务脚本及统一编排脚本的参数解析器 |
| GUI 显示值到 CLI 的映射 | `Script\Script_DAISY_GUI.py` |
| 物理盘只读登记与 ZIP 协议 | 本文第十一节及 `Script\Lib\Script_DAISY_Lib_Storage_*.py` |

## 二、系统不变量

1. **源档案只读**：DAISY 不创建、修改、重命名或删除源目录中的任何项目。
2. **快照封存后不可变**：后续核验、Diff 和导出只读输入数据库；新分析产生新文件。
3. **无有效 SHA-256 时不得推断内容相同**：大小和时间相同只能证明文件属性未变，不能替代内容证据。
4. **业务运行纯本地**：所有数据库与存储业务任务均没有网络、遥测、上传或在线查询；云占位文件不会被触发下载。只有用户明确确认后，Python 引导脚本、GUI 中 Python／外部工具的固定 WinGet 安装或 rawpy pip 安装流程才可能联网。
5. **路径可迁移**：身份以根目录名 (`root_label`) 和相对路径表示，不依赖盘符；当次 `root_path` 仅作定位与审计。
6. **时间可审计**：自产时间使用 UTC ISO 8601 `Z`；本地时间只用于显示和文件名。
7. **文本统一**：正式文本输出使用 UTF-8（无 BOM）和 LF。
8. **失败如实保留**：单文件失败通常记录到 `errors`，不会伪装为成功；同一工具连续出现
   同类故障时重建工具会话或停止相应阶段。
9. **物理盘只读**：硬盘信息登记不修改磁盘、分区、卷、文件系统、BitLocker 或 SMART
   设置，也不启动 SMART 自检；只读查询仍可能唤醒休眠硬盘。

### 2.1 内容读取边界

- 完整扫描的哈希和哈希核验会读取文件内容，但字节只进入 SHA-256 实现。
- 元数据阶段不提取文本正文、单元格、幻灯片正文或压缩包成员内容。
- 文档只读取属性区，例如 OOXML `docProps/*`、PDF Info/XMP。
- 压缩包登记只读取目录结构和成员描述，不读取成员数据。
- **档案数据核验中的格式与 RAW 项目是例外**：它们可以为 CRC、结构或解码验证读取
  文件及压缩包成员数据；兼容命令 `check-format` 适用同一边界。校验过程不保存正文，
  也不写回源文件。

## 三、支持类型与处理

| `media_kind` | 扩展名 | 完整扫描的元数据处理 |
|---|---|---|
| `photo_raw` | cr2 cr3 nef arw raf orf rw2 dng | ExifTool 照片 profile |
| `photo_jpeg` | jpg jpeg jfif | ExifTool |
| `image_gif` | gif | ExifTool；全量元数据另存成功的 ffprobe 原文 |
| `photo_working` | tif tiff psd psb png | ExifTool＋`working_metadata` |
| `video_mp4` | mp4 mov lrf | ExifTool＋ffprobe |
| `video_crm` | crm | ExifTool＋ffprobe，允许 CTMD 长尾字段进入工具原始输出 |
| `audio` | wav mp3 aac | 视频同管线；title/author/album/copyright 优先采用 ffprobe tags |
| `archive` | zip 7z rar tar gz bz2 xz | ZIP 使用 `zipfile` 目录；其他格式使用 7-Zip 列表；全量元数据另存 ExifTool 原文 |
| `document` | pdf doc docx xlsx pptx | 只登记属性，不读取正文；全量元数据另存 ExifTool 原文 |
| `other` | 其他全部 | 进入树和哈希；仅在全量元数据范围保存 ExifTool 原文 |

「支持」表示代码具有对应处理路径，不表示所有厂商、固件和损坏形态都经过真实样本验证。
表中 ExifTool/ffprobe 路径以相应工具未关闭为前提；关闭某项后不启动该工具，未被其他
适用解析器处理的文件记为「跳过」，不记为文件问题。

元数据 profile v7：

- 照片：`-j -G1:3:4 -a -u -D -l -ee -charset filename=utf8`；
- 视频、音频、文档、压缩包和 `other`：同组参数但不含 `-ee`；
- ffprobe：`-print_format json -show_format -show_streams -show_chapters -show_programs -show_stream_groups -show_data`。
- v2 新增 ffprobe 容器级 `format.tags.location` 的 ISO 6709 规范化。
- v3 在开启工具原始输出时把 ExifTool 覆盖扩展到本地所有文件，并对每个
  文件调用 ffprobe；该范围用于开发期全类型价值实测。
- v4 保留本地所有文件的 ExifTool 原始输出，但把 ffprobe 收敛为视频、
  音频和 GIF。音频／视频的 ffprobe 是规范化管线的必需后端，失败会记录
  元数据错误；GIF 只作工具原始输出中的动画证据增补，失败不覆盖 ExifTool 主解析
  状态。其他照片、文档、压缩包和普通文件不调用 ffprobe。
- v5 补齐 `.jfif` 的 JPEG 分类、`.doc` 的文档分类和 GIF 的通用照片
  规范化字段，并把 ffprobe 原文收敛为视频、音频和 GIF。
- v6 把 GIF 从 `other` 提升为独立 `image_gif`，并把面向用户的选项明确
  为「基础元数据／全量元数据」。两种范围都解析有规范化落点的文件；
  仅全量元数据写入 `raw_payloads`。
- v7 修正曝光补偿、Canon 实拍色温与白平衡、照片小数秒、DNG 有效尺寸／
  位深／时区／GPS、视频 UTC、DJI 机型、文本标量和无效镜头值；AAC 纳入
  音频管线。ExifTool 的 `Warning`/`Error` 和规范化清洗原因写入
  `metadata_diagnostics`，其中 `Error` 同时进入 `errors` 并使条目失败。
- ffprobe 成功不等于读到了照片意义上的传统 EXIF。profile v5 不再因为
  静态照片可被表示为单帧视频流，就默认保存重复或带合成时长的结果。

## 四、元数据范围

完整扫描可分别把 ExifTool 与 ffprobe 设为「全量」「基础」或「关闭」。范围既决定是否
运行该工具，也决定是否保存该工具的原始 JSON：

- `--metadata-exiftool-mode complete` 与 `--metadata-ffprobe-mode complete` 对应「全量」；
  运行相应工具，写入规范化字段，并保存该工具的原始 JSON。后端 JSON canonicalize 后以
  zlib level 6 压缩，`payload_sha256` 是未压缩 canonical JSON 的 SHA-256。
- 两个逐工具参数的 `normalized` 对应「基础」；仍运行生成规范化字段所需的相应工具，
  但不把该工具的结果写入 `raw_payloads`。
- 两个逐工具参数的 `off` 对应「关闭」；不启动该工具。没有其他适用解析器的文件记为
  `skipped`（跳过），不生成逐文件问题。
- `--metadata-storage complete` 或 `--metadata-storage normalized` 是旧共享范围的兼容参数，
  不能与逐工具范围同时使用；现行 GUI 只生成逐工具参数。ZIP/7-Zip 结构登记仍按文件类型独立执行。
- 快速扫描固定关闭 ExifTool 与 ffprobe，不接受逐工具元数据范围。
- 基础元数据范围下，`.jfif`、`.doc` 和 GIF 仍有规范化落点；真正未知且没有
  规范化表的 `other` 才标为 `not_applicable`。
- 基础元数据无法重新解释历史后端字段，也无法用工具原始输出判断
  `metadata_extraction_changed`。
- 元数据范围不是隐私开关；规范化列仍可能包含作者、设备、时间或位置等元数据。
- 「全部字段」仅指**当前 profile 返回的 JSON 字段全部保留**，不代表外部工具未返回的字段也被采集。
- `payload_zlib` 和 `payload_sha256` 保留完整的工具原始输出；Diff 只有在 ExifTool
  摘要不同且工具版本相同时，才按需解压候选载荷，并在比较副本中排除
  `SourceFile`、`Directory` 和 `FileAccessDate`。这些提取环境字段会因
  根目录迁移或只读访问而变化，不构成提取语义变化；其他字段或工具版本变化
  仍判为 `metadata_extraction_changed`。

规范化取值要点：

- 照片和视频优先采用 `SubSecDateTimeOriginal＋OffsetTimeOriginal`；显式
  offset 可换算出 UTC 时保留原小数秒。没有可换算 offset 的视频才回退到
  ffprobe `format.tags.creation_time`。`QuickTime:CreateDate` 不默认解释成 UTC。
- 曝光补偿只接受 ExifTool 的 `num` 或原生数值，不把 `-2/3` 的显示字符串
  解析成 `-2 EV`。
- Canon 白平衡优先 MakerNote `Canon:WhiteBalance`；ExifIFD 的
  Auto/Manual 只作通用辅助。实拍色温只采用 `ColorTempAsShot`，不以
  `ColorTemperature`/`ColorTempKelvin` 代替。
- DNG 尺寸优先 `SubIFD:DefaultCropSize`，再回退到 SubIFD 宽高；位深采用
  `SubIFD:BitsPerSample`。CR3 中预览图或轨道的位深不得视为 RAW 位深。
- `(0,0)` GPS、非正数光圈／焦距和全零镜头序列号转为 `NULL`，并写入
  `validation`（字段规范化提示）；不据此修改工具原始输出。
- 色彩优先采用 ICC profile；EXIF ColorSpace 只作辅助。
- Canon gamma/gamut 取 CanonLogVersion/ColorSpace2；其他厂商没有可靠来源时留空。
- 音频文本标签优先使用 ffprobe，避免 RIFF INFO 非标准编码造成误解。
- `video_metadata.stream_count` 保存 ffprobe 的总流数；`video_streams` 与
  `audio_streams` 只保存对应两类流的查询字段。timecode、CTMD、DJI telemetry
  等数据流明细仍完整保留在 ffprobe 工具原始输出中，不被误计为音视频流。
- 视频容器级 `location` 解析为 `video_gps_points`：经纬度为有范围约束的
  十进制度，海拔可空，原始字符串写入 `raw_value`。文件级静态位置没有
  点时间，故 `timestamp_seconds=NULL`；当前不提取逐帧或连续轨迹。
- 无法按 ISO 6709 解析或超出经纬度范围的 `location` 不写规范化点；默认
  开启全量元数据时，ffprobe 原值仍完整保留，可供审计和后续重解释。
- 工具版本写入快照和 `raw_payloads`。跨工具版本的原始 JSON 差异可归为 `metadata_extraction_changed`，不等同于文件内容变化。

## 五、快照数据模型

### 5.1 快照数据库

| 分组 | 表／视图 | 用途 |
|---|---|---|
| 身份与运行 | `snapshot_info` | 版本、UUID、状态、coverage、工具版本、配置和统计 |
| 内嵌证据 | `snapshot_manifest`、`run_events` | 成功运行的清单和事件时间线 |
| 多根目录 | `roots` | 根目录名、当次路径及枚举状态 |
| 树 | `dirs`、`entries` | 目录、文件、stat、媒体类型和处理状态 |
| 规范化元数据 | `photo_metadata`、`video_metadata`、`video_gps_points`、`video_streams`、`audio_streams`、`working_metadata`、`document_metadata`、`archive_metadata`、`archive_members` | 固定查询列；视频 GPS 点支持一文件多行 |
| 工具原始输出 | `raw_payloads` | ExifTool/ffprobe 原始 JSON |
| 完整性 | `hashes` | SHA-256、读取字节、状态和复用溯源 |
| 元数据诊断 | `metadata_diagnostics` | 工具 warning/error 与字段规范化提示；warning 不自动判为失败 |
| 错误 | `errors` | 阶段、后端、错误码和文本 |
| 视图 | `v_file_manifest`、`v_dir_problems` | 常用清单与目录问题查询 |

`Quick` 与 `Full` 使用相同的 schema 3。快速扫描不生成哈希、专用元数据或工具原始输出，`video_gps_points` 因此为空，但保持统一的数据结构和明确的状态值。快照报告把视频 GPS 点导出为 `GPS_inventory_video.csv`。

### 5.2 Diff 数据库

| 表 | 用途 |
|---|---|
| `diff_info` | 两侧快照身份、版本、coverage、配对和统计 |
| `diff_subtrees` | 枚举失败影响范围 |
| `diff_dirs` | 目录维度变化 |
| `diff_hash_groups` | 哈希多重集和移动／复制分组 |
| `diff_entries` | 每个文件的状态、证据、原因及两侧值 |

## 六、身份、状态与路径

- `rel_path` 保存相对 root 的原始路径；`path_key` 用于比较。
- `path_key` v1：NFC → `casefold()` → 分隔符统一为 `/`。
- 唯一性是 `(root_id, rel_path)`。`path_key` 碰撞不会丢条目，而是记录错误；Diff 对碰撞组给出 `unknown`。
- `root_label` 默认取根文件夹名；CLI 可用 `根目录名=路径` 显式指定。
- `volume_serial` 与 `file_index_hex` 来自 `os.stat()` 的设备和 inode 信息；不可可靠取得时为 NULL。
- NTFS-ID 是移动／重命名的辅助证据，不是内容证据。
- 文件系统时间以纳秒整数读取，数据库采用固定精度文本；增量复用要求存储值精确相等。

快照级状态彼此独立：

| 字段 | 含义 |
|---|---|
| `database_integrity` | SQLite 与外键是否通过完整性检查 |
| `scan_status` | 扫描是否完整结束并已封存 |
| `has_file_issues` | 是否存在损坏、空白或无法解析的源文件 |
| `has_unstable_entries` | 是否有文件在扫描或读取期间发生变化 |
| `has_enumeration_gaps` | 是否存在未能枚举的根目录或子目录 |

`has_file_issues=1` 不表示数据库损坏；只要数据库完整且扫描完成，仍正常发布
SQLite。`_Issues.md` 是供人工阅读的视图：单纯 `exiftool_reported_error=Unknown file type`
的记录不列为问题，也不会在没有其他问题时单独生成报告；原状态、诊断和错误行仍
完整保留在 SQLite，数据库状态与扫描结构不变。

元数据汇总状态优先级：

1. 解析前后 size/mtime 改变：`unstable`；
2. 任一适用后端超时：`timeout`；
3. 任一适用后端失败：`error`；
4. 所有适用后端成功：`done`；
5. 不适用：`not_applicable`；
6. 占位或明确跳过：`skipped`。

ExifTool `Warning` 单独保存但不等同于解析失败；ExifTool `Error` 会写入
`metadata_diagnostics` 与 `errors`，并使 `meta_status=error`。只有
`validation` 的条目仍可为 `done`，其规范化无效值已经转为 `NULL`。

ExifTool 单文件超时按枚举阶段已登记的 `size_bytes` 计算：
`max(90, ceil(size_bytes / (9 × 1024³)) × 90)` 秒。恰好 `9 GiB` 为
90 秒，超过后进入下一个 90 秒阶梯。策略写入 `config_json` 和 manifest，
超时错误同时记录实际秒数和文件体积；ffprobe 维持固定 60 秒。

哈希 `valid` 的充要条件是：摘要非空、`bytes_read == size_bytes`，并且读取前后 size/mtime 一致。

## 七、完整扫描／快速扫描与封存

### 7.1 默认能力

完整扫描（内部值 `full`）默认：

- `hash=full`；
- 元数据范围为全量元数据 (`complete`)；
- 采集 NTFS-ID；
- 哈希复检默认抽样 1%，至少 100 个本次实际计算且有效的条目。

快速扫描（内部值 `quick`）：

- 不读取内容；
- 不运行外部工具；
- 不计算哈希；
- 不提取元数据或工具原始输出；
- 默认采集 NTFS-ID。

### 7.2 扫描稳定性

完整扫描不是文件系统原子快照，而是用多个时间点的观测尽量识别扫描期间的
源文件变化：

1. 枚举时登记每个已发现文件的 size、mtime、NTFS-ID 和观测时间；
2. 主 SHA-256 在读前、读后分别 stat，并核对枚举 size、实际读取字节数、
   读前后 size 和 mtime；不一致即 `unstable`；
3. 每个文件完成元数据解析后，再把当前 size/mtime 与枚举值比较；
4. 元数据阶段结束后，对枚举时已登记的本地所有文件再做一次
   size/mtime 复扫；变化或消失会同时把哈希与元数据状态标为
   `unstable`；
5. 主哈希完成后，从本次由 Python 实际计算且状态有效的条目中按比例抽样，
   由 PowerShell `Get-FileHash` 独立重算；默认 1%，至少 100 个，候选不足
   时全验。它不是主 SHA-256 的覆盖比例；
6. 连续 30 秒无进展先记录停顿证据；处置阈值从 90 秒起，每增加 9 GiB 文件大小再增加
   90 秒。达到阈值后可继续等待、跳过并记录或停止并保留未完成快照；默认继续等待，
   因而阈值本身不是强制终止时间。

当前边界必须明确：

- 末次复扫只检查枚举时已经登记的路径，不会重新枚举目录。因此扫描开始后
  新增的文件不会进入本次快照；已登记文件随后消失则能够检出；
- 基于文件属性的检查不能可靠发现「内容改变后又恢复原 size 和 mtime」的情况。
  完整扫描的哈希能证明读取时的内容，但不能把整个源目录冻结在同一时刻；
- DAISY 当前不创建 VSS 或其他文件系统快照，不应把一次长时间扫描解释为
  原子时间点映像；
- DAISY 不提供按 mtime 静默跳过近期文件的「静置窗口」。建立权威基线前
  应先停止对源目录写入；扫描中发生的已登记文件变化会明确记为
  `unstable`，而不是用缺失哈希换取表面上的完整扫描。

### 7.3 运行态与封存

schema 4 扫描运行态包含：

```text
<short-name>.<microseconds>_<runid8>.partial.sqlite
<short-name>.<microseconds>_<runid8>.partial.sqlite-wal
<short-name>.<microseconds>_<runid8>.partial.sqlite-shm
<short-name>.<microseconds>_<runid8>.partial.sqlite.lease
<short-name>.<microseconds>_<runid8>.events.jsonl
```

统一 `scan` 是 schema 4 的正式续传入口。续传只接受 `.partial.sqlite` 未完成快照，并
沿用其中冻结的扫描模式、根目录、哈希、逐工具元数据范围、NTFS-ID、哈希复检、格式和
超时策略；调用方不能在续传时覆盖这些参数。续传预检重新确认冻结的工具路径、版本及
rawpy/LibRaw 能力，发生变化时拒绝续传。微秒和随机运行 ID 只属于运行态，不进入最终
封存名。新任务对未完成快照和最终目标都采用不覆盖语义 (no-clobber)。

成功封存顺序：

1. 检查无 pending/processing 残留并写最终统计；
2. 写 `scan_status=complete` 和真实 `hash_coverage`；
3. 把 manifest 与运行事件写入 `snapshot_manifest` 和 `run_events`；
4. 执行 SQLite `integrity_check`；
5. checkpoint WAL，切回 DELETE journal 并关闭连接；
6. 对稳定 SQLite 字节计算完整 SHA-256；
7. 取摘要前 8 个十六进制字符并大写；
8. 有需要人工关注的问题时，以目标存在即失败的方式创建同目录 `_Issues.md`；
9. 以目标存在即失败的原子重命名发布 SQLite；
10. 删除已内嵌的运行态 JSONL。

失败或中断时保留未完成快照和事件，供诊断或续传。

统一 schema 4 的用户动作边界如下：暂停只在当前任务进程内生效，任务到达可解释的安全
边界后才进入 `paused`，同一进程可继续；暂停本身不产生跨重启承诺。「保存并退出」只能在
安全暂停后把已完成记录提交到 `.partial.sqlite`，结束当前 session、释放 lease，并写入
`resume_hint=suggest`。下次启动只显示续传提示；用户仍须明确准备续传、核对冻结配置并
点击「开始任务」，程序不得自动读取或自动继续。普通「停止」也保留未完成快照，但写入
`resume_hint=manual_only`，下次不主动推荐。已提交的文件级结果继续保留；正在处理的文件
不序列化 Python 哈希对象或外部工具进程状态，继续或跨重启续传时可能从该文件起点重试。
进程异常退出时，只有确认旧 lease 无效后才能把遗留 attempt 标为 `abandoned` 并创建新的
resume session。精确 DDL、事务和状态转换以 `Script_DAISY_Lib_Scan_State.py` 及其
契约测试为准。

### 7.4 冻结 schema 3 续传边界

旧 `full-scan` 的 schema 3 续传流程冻结如下；v1.6.0 GUI 的 `scan` 使用第 12.1 节所述
schema 4 状态机，不应把两条路径混写：

1. 要求文件名以 `.partial.sqlite` 结尾，取得同名 ScanLock；本机 owner PID 仍存活
   时拒绝，owner 已失效时允许接管；
2. 只接受 `scan_status=running`，并同时核对生成程序版本、schema、元数据 profile、
   `video_gps_points` 表和文件名布局版本；
3. 验证 partial 中保存的每个 root 仍然可访问，重新执行工具预检；
4. 从头重新枚举全部 root，并与既有登记对账。删除已消失条目，加入新增条目；只有
   size 或 mtime 变化时，才把既有条目的哈希和元数据状态重置为 `pending`；
5. 哈希阶段保留未变化条目的已完成结果，把遗留 `processing` 重置为 `pending`，
   然后只处理 `pending`；`error`、`unstable` 与 `skipped` 不自动重试；
6. 元数据阶段只处理 `pending`。当前实现不写 `meta_status=processing`；未提交事务由
   SQLite 回滚。未变化条目的 `error`、`timeout`、`unstable` 与 `skipped` 不自动
   重试；
7. 重新执行末次 size/mtime 复扫、独立哈希复检和封存；事件 JSONL 以追加方式记录
   每次 `run_started`、中断、失败和重复进入的阶段，成功后整体写入 `run_events`。

续传完成与从头一次完成只承诺**业务语义可收敛**，不承诺数据库逐字段或逐字节相同。
即使源目录始终不变，也会存在以下预期差异：

- 续传保留原 `snapshot_uuid`、开始时间、配置和命名 stem；从头扫描创建新身份；
- 续传重新枚举会覆盖目录与条目的 `observed_at_utc`；
- 中断前已完成的哈希、元数据、工具版本和处理时间继续保留，待处理条目在续传后完成；
- `run_events` 与 `snapshot_manifest` 明确包含中断及 `resumed=true` 的多段时间线；
- row ID、SQLite 页布局和最终数据库文件指纹不作为两种执行方式的等价判据。

只有在源目录、工具、环境和配置均未变化且没有瞬时错误时，忽略身份、时间、事件、
row ID 与物理布局后，文件清单、SHA-256、规范化元数据、统计和完成状态才应语义等价。
暂停期间发生的新增、删除或 size/mtime 变化会按续传时重新枚举的结果进入快照；内容
改变但 size 与 mtime 均保持不变时，既有完成结果不会必然失效。未变化的瞬时
`error`/`timeout` 也不会因续传自动重试，因此它们可能与稍后从头扫描的结果不同。

哈希读取默认以 4 MiB 分块同步执行。连续 30 秒没有完成数据块时，StallWatchdog
只写 `stall` 事件，不取消读取、不跳过文件，也没有单文件固定超时。底层读取抛出异常
时才把条目标为 `error` 并继续；若系统 I/O 一直不返回，当前任务可能长期停在该文件。
哈希错误率超过 20% 的告警和超过 50% 的整次中止不等于单文件超时机制。

上述限制只用于冻结的 schema 3 兼容入口。统一 schema 4 扫描已经实现独立状态机、
lease、失败重试、工具溯源、输出目录冻结、动态哈希 timeout 和业务投影等价测试；精确
定义仍以状态层代码及其契约测试为准。

### 7.5 文件名

```text
根目录名_类型_日期_时间_XXXXXXXX.sqlite
```

- `XXXXXXXX` 是最终数据库完整 SHA-256 的高 32 bit，不是完整摘要。
- schema 4 统一扫描的类型只使用 `Full` 或 `Quick`；哈希、元数据和 NTFS-ID 等实际能力以
  `config_json` 与 manifest 为准，不再编码进新文件名。
- schema 3 冻结入口继续接受并生成既有偏差标记，以保持 v1.4.1 兼容；读取器不能据此
  推断 schema 4 能力。
- 多根目录合并时用 `+` 连接安全化后的根目录名（`label`）。
- 文件名使用本地时间；库内 UTC 时间和 UUID 才是权威身份。
- 元数据 error/timeout、哈希 error、unstable 或枚举缺口写入对应状态和
  明细表，但不进入数据库文件名；存在需关注项时在数据库同目录生成
  `<数据库基名>_Issues.md`。仅格式未识别的 ExifTool error 保留库内证据，
  不进入该 Markdown 问题报告。
- warning/validation 会保留在库内；它们本身不让一次直接扫描生成问题报告。
- `filename_layout_version=2` 表示 v1.4.1 秒级短名称；manifest 的
  `snapshot_stem` 与 `snapshot_filename_pattern` 必须与当前封存名一致。
- `filename_layout_version=3` 表示 schema 4 的自描述命名合同：v1.6.0 已有带功能 token 的
  合格文件继续接受，v1.6.2 及后续新建文件只写 `Full`/`Quick`；两者均以库内 pattern 和 manifest
  校验，不能只靠文件名猜测能力。

完整数据库 SHA-256 不持久化。32-bit 指纹适合快速发现常见损坏，但存在碰撞，不能替代外部完整摘要或签名。

## 八、哈希与增量复用

下表描述冻结的 schema 3 兼容入口；schema 4 统一扫描的新文件名不编码这些功能差异。

| 模式 | 行为 | 文件名标记 |
|---|---|---|
| `full` | 所有可读普通文件重新计算 SHA-256 | 无 |
| `incremental` | 满足条件时复用旧哈希，否则重算 | `Hash-Inc` |
| `none` | 不计算哈希 | `No-Hash` |

增量复用必须同时满足：

1. root 配对后 `path_key` 唯一且相同；
2. size 和 mtime 精确相同；
3. 两侧都不是占位文件；
4. 上一快照存在 `valid` SHA-256；
5. 两侧都有 NTFS-ID 时必须相同。

增量来源还必须是 schema 3 的 v1.4.1 完整封存件，并通过文件名指纹、SQLite
完整性、状态与明细一致性检查。扫描未完成、目录枚举缺口、哈希失败或
unstable 条目均硬性拒绝；`has_file_issues=1` 本身不阻止其他有效哈希复用。
复用记录保存最初计算事件，而不是只指向最后一个中间快照。

完整扫描的独立哈希复检和 `check-hash` 使用 PowerShell `Get-FileHash`，与主哈希实现分离。schema 4
扫描的复检按文件启动本任务持有的精确 PowerShell 进程，路径以 UTF-8 令牌放入
UTF-16LE `-EncodedCommand`，不依赖尾随 `$args` 或字符串引号拼接。控制层沿用 30 秒
stall、每 9 GiB 增加 90 秒的动态无进展阈值和三种处置；暂停、停止或 timeout 只终止并等待当前
句柄。读取前后 size/mtime 必须稳定。第一次摘要不一致时，主实现和独立实现各重算一次；
双方恢复为原摘要才算偶发复检异常，否则写入 `verify_hash` attempt，并把当前哈希标为
unstable 留证。

正式兼容范围包括 Windows PowerShell 5.1 (`powershell.exe`) 和
PowerShell 7.x (`pwsh.exe`)。两个系列使用相同的 `Get-FileHash` 调用路径，
均须通过启动、版本读取和命令可用性验证后才会被采用。

PowerShell 按「手动路径 → `PATH` → Windows 常规安装位置」发现。自动发现会
逐个验证候选是否可启动、能否报告版本以及是否提供 `Get-FileHash`；单个坏候选
不会阻断后续候选。便携版或自定义安装位置通过 `--powershell-path` 指定。
`env-check` 还会在系统临时目录对固定样本实际执行一次 `Get-FileHash`，不读取
档案内容。

已知边界：攻击者可以刻意保持 size/mtime；因此增量快照不能永久替代定期 full hash。

## 九、快照准入与核验

以下情况硬拒绝，`--force` 也不能越过：

- `database_integrity` 不是 `ok`，或实际 SQLite 检查或外键检查失败；
- `scan_status` 不是 `complete`；
- 文件名已有高 32 bit 指纹，但与当前字节复算不符；
- `schema_version` 不在统一 Reader 支持范围内（当前为 3/4），或 `path_key_rule`
  不兼容。

唯一可降级项是 Diff 与核验输入的**文件名缺少指纹**。`--force` 允许继续，
但结果会记录该降级并生成问题报告；已有但不匹配的指纹不能越过。增量来源
不允许缺少指纹。当前版本不读取旧 sidecar、散置 `.sha256` 或旧 `SHA8-` 命名。

v1.6.8 的统一 Reader 只读支持 v1.4.1/schema 3 与当前 schema 4 封存快照，不原地
迁移旧库。冻结 `full-scan` 只续传同一生成程序版本、schema 3、profile 7 的未完成快照；
统一 `scan` 的 schema 4 续传按独立续传契约判断。v1.4.0 及更早数据库要获得当前
规范化结果，必须重新扫描原档案。

统一 `verify` 先在 `--snapshot`（已有数据库）与 `--direct`（无数据库）之间二选一：

- `--snapshot` 且盘符和路径未变时，可省略 `--root`，运行时使用快照记录的各根绝对路径；
- 已有数据库但路径变化时，用一个或多个 `--root 根目录名=当前路径` 覆盖对应根；
- `--direct` 必须至少提供一个 `--root`，可用路径或 `根目录名=路径`；它只登记本次遍历与
  当前格式／容器／RAW 检查证据，没有基准摘要，因此 `--hash` 固定为 `off`，也不接受
  只针对数据库指纹降级的 `--force`；
- 普通文件不能作为根目录，多根目录的路径与根目录名均不得重复。

冻结的 `哈希核验 check-hash` 和 `格式校验 check-format` 仍必须用 `--root` 指定当前档案
根目录，不改变其既有命令行协议。

`哈希核验 check-hash`：

- 总是先检查记录条目的存在性、size 和 mtime；
- 默认抽样 1%，至少 100 个有有效基准哈希的条目；
- `--full` 对所有有有效基准哈希的条目独立复算；
- 结论只覆盖本次实际检查口径。

`格式校验 check-format`：

- 默认检查全部可校验文件；冻结 CLI 仍可指定抽样比例，现行 GUI 固定检查全部适用文件；
- ZIP/OOXML 可读取成员并校验 CRC；
- PDF 使用头、尾和 `startxref` 结构检查；
- 媒体使用 ExifTool validate，视频、音频和 GIF 叠加 ffprobe；
- 其他格式返回 `unsupported`；只有另行启用哈希复检时，内容变化证据才由哈希层提供。

ExifTool 与 ffprobe 都适用于同一文件时，监督器先执行 ExifTool，再执行 ffprobe，并把两者
诊断合并为一条格式校验结果。任一前置步骤发生工具级故障、暂停、停止或超时后，后续步骤
不保证执行；连续同签名工具故障达到阈值时停止格式校验阶段，并记录未处理范围。这里的
「分别启用」只描述选项和单工具执行能力，不表示同一文件上的工具彼此故障隔离。

报告只列出本次实际出现的状态计数；`unsupported=0` 等零值不作为占位项
显示。`unsupported` 状态本身仍用于区分「校验器无法判断」和
`valid`/`invalid`，不能因某一个测试库为 0 而删除。

格式校验不是逐帧解码。媒体「容器结构正常但码流内部损坏」可能漏检；外部工具版本变化也可能改变警告口径。

## 十、Diff 语义

### 10.1 根目录与路径配对

- 优先按相同根目录名（`label`）配对；
- `--map-root old=new` 可显式指定「旧根目录名=新根目录名」；
- 两侧各只有一个根目录且名称不同时自动配对，并记录 `auto_paired`；
- 多根目录没有唯一解时不猜测，未配对根目录整体列为新增／删除。

若任一侧某子树枚举失败，该范围不能可靠判定 `added` 或 `deleted`，统一传播为 `unknown`。

### 10.2 文件状态

`diff_entries.status` 的 11 个值：

`unchanged`、`stat_changed_content_same`、`metadata_extraction_changed`、`content_changed`、`added`、`deleted`、`moved_or_renamed`、`copied`、`hash_missing`、`unstable`、`unknown`。

核心优先级：

1. 枚举失败或 path_key 碰撞：`unknown`；
2. 任一侧 unstable：`unstable`；
3. size 不同：`content_changed`，证据为 `stat_only`；
4. 双侧有效哈希不同：`content_changed`；
5. 双侧有效哈希相同：再细分文件属性变化、工具原始输出稳定比较摘要变化或 `unchanged`；
6. 无双侧有效哈希：`hash_missing`，不以大小和时间推断相同。

证据等级：

- `independent_computation`：两侧哈希来自不同计算事件；
- `propagated_single_computation`：两侧最终追溯到同一计算事件；
- `heuristic_file_id`：仅用 NTFS-ID 辅助移动判断；
- `stat_only`：大小不同已经足以证明内容不同；
- `insufficient`：证据不足。

移动、复制和硬链接使用全快照 SHA-256 多重集进行分组；无哈希时才可能退回 NTFS-ID 启发式。

### 10.3 跨版本只读投影

v1.6.0 的快照对比支持 schema 3/4 的旧旧、旧新、新旧和新新四种方向。输入先由
统一 Reader 转成 `daisy-diff-input-v1`，Diff 业务层不直接查询快照物理表。交换方向时，
`added`/`deleted`、`old`/`new` 路径、schema 和未配对 root 必须一起反转；枚举失败范围在任一方向
都保持 `unknown`。

文件／目录是必要能力；哈希、工具原始输出、格式校验和运行证据是可选能力。哈希缺失只降低
内容证据，工具原始输出一侧缺失时元数据结论为 `NULL`。格式校验与会话／处理尝试差异只
写能力说明，不套用现有文件变化状态。Diff 输出仍使用冻结的 schema 3 `DIFF_DDL`，来源
schema、投影标识和能力结论写入既有身份列与 `counts_json`，不改变 v1.4.1 的表列契约。

## 十一、物理硬盘信息登记

### 11.1 定位、版本与权威实现

硬盘信息登记用于 Windows 单机上的只读物理硬盘信息登记与证据归档。GUI 只有一个硬盘
功能模块：`硬盘信息登记`。同一页的「检测硬盘」按钮调用该模块脚本的
内部列盘模式并刷新硬盘池，不另列为功能入口。统一 CLI 的 `storage-list` 和
`storage-collect` 均由同一个硬盘信息登记 Module 脚本分派；`storage-list` 只是登记前的
准备模式。归档类型标识为 `PROFILE`，相关源码统一使用 `Storage` 职责名称。

硬盘归档的 `archive_schema_version=3` 只表示 ZIP 协议，与快照和 Diff 的 SQLite
`schema_version=3` 没有数据模型关系。硬盘信息模块不导入 `sqlite3`，不创建、读取或修改
数据库。默认产物目录为 `Output/Storage`。当前只读取硬盘归档 schema 3，不兼容
早期协议；Manifest 中的应用版本取当前 `APP_VERSION`，本版为 `1.6.8`。

代码权威边界：

| 范围 | 文件 |
|---|---|
| 数据模型、命名、编码与摘要 | `Script/Lib/Script_DAISY_Lib_Storage_Core.py` |
| Windows 存储清单 | `Script/Lib/Script_DAISY_Lib_Storage_Windows.py` |
| smartctl 命令与解析 | `Script/Lib/Script_DAISY_Lib_Storage_Smartctl.py` |
| 扫描关联、身份确认与报告 | `Script/Lib/Script_DAISY_Lib_Storage_Service.py` |
| ZIP 生成、发布与核验 | `Script/Lib/Script_DAISY_Lib_Storage_Archive.py` |
| `硬盘信息登记` 列盘与登记入口 | `Script/Module/Script_DAISY_Module_Storage_Collect.py` |
| 统一 GUI/CLI 接入 | `Script/Script_DAISY_GUI.py`、`Script/Script_DAISY_CLI.py` |

smartctl 由 `运行环境检测` 发现、验证与缓存。缺失时，只能在用户逐项确认后通过固定
`smartmontools.smartmontools` WinGet 包安装；PowerShell 不由 GUI 安装。

### 11.2 系统不变量

1. **物理盘只读**：不执行修改磁盘、分区、卷、文件系统、BitLocker 或 SMART
   设置的命令，也不启动 SMART 自检。
2. **物理盘优先**：采集目标以 Windows `DiskNumber` 和 smartctl 设备枚举项共同
   标识，不把盘符当作物理盘身份。
3. **采集前重新确认身份**：详细 Windows 查询后，必须与选择时的容量、
   `UniqueId` 和序列号比较；发现热插拔或编号复用即中止。
4. **原始证据保留**：smartctl 原始 JSON 及其中的 `smartctl.output` 不被摘要
   字段替代。
5. **缺口透明**：不支持、权限不足、超时和空字段都保留，不能伪装为 0、健康
   或通过。
6. **归档不可覆盖**：最终 ZIP 目标存在即失败，旧文件不得改变。
7. **文本统一**：正式 JSON 和可选 TXT 为 UTF-8 无 BOM、LF；ZIP 成员全部平铺。
8. **时间可审计**：清单保存 UTC 与本地偏移时间；文件名只用于人类排序。同一
   事件的 UTC 与本地字段必须由同一个带时区时间生成，并代表同一时刻。
9. **本地运行**：采集、归档和验证不联网、不上传、不遥测。
10. **完整性显式**：访问或命令层错误必须标为 `incomplete`，不得仅凭 ZIP
    成功生成就宣称登记完整。
11. **权限显式**：硬盘信息登记的硬盘检测与登记建议使用管理员权限，以取得更完整的信息。
    GUI 只在「硬盘信息登记」页提供管理员模式按钮，并在悬停说明和启动确认中说明
    非管理员模式可能不完整或失败；提权通过 Windows UAC 重启。
12. **发布后自检**：最终 ZIP 发布后必须自动执行完整核验；不提供可被误认为独立
    业务功能的手动核验模块。

### 11.3 管理员权限与只读命令边界

硬盘信息登记的硬盘检测和登记应在管理员模式下运行，以取得完整的 Windows 存储与
smartctl 信息。GUI「硬盘信息登记」页的管理员模式按钮会先确认，再通过 Windows UAC 重启当前
应用；任务运行期间不可切换权限。未提权运行不放宽只读边界，只会如实记录权限
缺口、失败或 `incomplete` 诊断结果。

smartctl 设备枚举固定为：

```text
smartctl --scan-open --json=c
```

完整读取固定为：

```text
smartctl -x --json=ov -d <scan.type> <scan.name>
```

`-d` 的类型和设备名称只能来自当次扫描对象。实现显式禁止 `-t`、`--test`、
`-s`、`--smart`、`--set`、`-X`、`--abort` 等主动或修改选项，并始终使用参数
数组和 `shell=False` 默认语义。

PowerShell 脚本只允许查询命令。只读测试禁止 `Clear-Disk`、`Initialize-Disk`、
`Format-Volume`、`Set-Disk`、`Set-Partition`、`Set-Volume`、`New-Partition`、
`Remove-Partition`、`Resize-Partition`、`Repair-Volume`、`Optimize-Volume` 和
BitLocker 修改命令。

### 11.4 目标发现、关联与硬盘池

Windows 清单和 smartctl 设备枚举独立执行，任一失败时仍保留另一侧实际结果。关联
规则依次识别：

1. `PhysicalDriveN`；
2. `/dev/pdN`；
3. Windows smartmontools 的 `/dev/sdX` 编号规则。

Windows 盘存在而 smartctl 未发现时，硬盘信息登记仍列出 Windows 目标并说明关联缺口，
但禁止为该项建立完整硬盘档案。smartctl 项无法关联 Windows `DiskNumber` 时也列出，
但不能当作完整目标。同一物理盘出现多个 smartctl 项时保留提示，并使用扫描顺序
中的第一项。

GUI 硬盘池列出本次检测到的全部有效 DiskNumber。脱机、Windows 硬盘信息缺失或
无法读取 SMART 信息的设备保留在池中并显示原因，但复选框禁用。用户只可逐项勾选；界面
不提供「全选／取消选择」。独立的「检测硬盘」按钮位于池子上方和边框外，硬盘内容列在
下方固定深色描边池内。每块已选硬盘拆成独立
`队列 i/n` 子进程和独立 ZIP；即使只选一块也显示 `队列 1/1`。每次点击「检测硬盘」
都会先清除上一轮清单与选择。选择框使用 20 px 自绘指示器；接入状态改变后必须重新检测，
不得沿用旧 `DiskNumber`。

登记开始后，硬盘信息登记按 `DiskNumber` 重新取得详细 Windows 清单，并核对容量、
`UniqueId` 和序列号，再以固定只读模板采集单盘证据。

硬盘登记页的「管理员模式」使用与「检测硬盘」相同的字符宽度、内边距和实际尺寸；按钮
位于「管理员权限已启用／未启用」标题前方，两者在同一 Y 轴居中，且按钮左边缘与下方
「检测硬盘」对齐。

### 11.5 Windows 数据模型

#### 11.5.1 `disk`

保存 `Get-Disk` 的编号、路径、位置、FriendlyName、型号、序列号、固件、
UniqueId、运行和健康状态、总线、分区样式、离线／只读／系统／启动盘状态、
逻辑和物理扇区、总容量、已分配容量及最大空闲范围。

#### 11.5.2 `partitions`

每个分区保存编号、盘符、全部 AccessPath、偏移、长度、结束偏移、类型、GPT/MBR 类型、GUID、
只读／离线／活动／启动／系统／隐藏／影子副本状态以及运行
状态。无盘符和无文件系统分区不得丢弃。

#### 11.5.3 `volume`

卷保存资源管理器卷标、盘符、卷 GUID 路径、文件系统、驱动器类型、健康和运行
状态、容量、剩余、计算所得已用、使用率、分配单元、去重和 DAX 字段。

详细模式补充 `Win32_LogicalDisk`、`Win32_Volume` 与可选 BitLocker 状态。
BitLocker 只登记算法、加密百分比、保护／锁定状态和保护器类型；不保存恢复密钥
或 KeyProtector ID。

#### 11.5.4 物理与可靠性补充

`Get-PhysicalDisk` 保存介质类型、转速、固件、池状态和物理位置。匹配方法必须
登记为 `device_id` 或 `serial_number`。`Get-StorageReliabilityCounter` 保存驱动
实际提供的温度、磨损、通电小时、错误和最大延迟；缺失不能解释为 0。

Win32 数据保留 PNP Device ID、传统几何与能力描述，用于兼容性和诊断，不作为
容量或身份的第一权威来源。

#### 11.5.5 布局间隙

实现按磁盘大小、分区 offset 和 size 推导地址空间间隙。前导和尾部间隙可能是
GPT/MBR 元数据，不能直接称为可分配未分配空间。正式 JSON 同时保留
`AllocatedSize` 与 `LargestFreeExtent`，由调用方自行解释。

### 11.6 空间语义

```text
used_bytes = size - size_remaining
used_percent = used_bytes / size * 100
```

仅在 `size >= size_remaining >= 0` 时计算。无文件系统、锁定卷、未挂载卷或驱动
未提供容量时为 `null`，不是 0。

### 11.7 产物、状态与 ZIP 发布

每块物理盘生成一个独立 PROFILE ZIP。归档内部固定包含：

| 路径 | 语义 |
|---|---|
| `<前缀>_Manifest.json` | 版本、身份、命令、来源、缺口及成员声明 |
| `<前缀>_Smartctl.json` | smartctl 原始 JSON，包含结构化字段和 `output` |
| `<前缀>_Storage.json` | 完整 Windows 物理盘、分区、卷和可靠性数据 |

`<前缀>` 为 `<卷标或回退>_PROFILE_YYYY-MM-DD_HH-MM-SS`。3 个文件全部位于 ZIP
根目录；成员名不含最终 ZIP 指纹，避免哈希自引用。内部不保存逐文件 SHA-256。
Manifest 与 Storage JSON 的应用字段记录 DAISY 名称、版本和作者。

GUI 的「简化报告」按钮默认开启，或 CLI 使用
`--summary-txt` 时，在 ZIP 同目录生成 `<完整 ZIP 基名>_Report.txt`。该文件不属于
归档，记录便于阅读的硬盘身份、SMART 总体结论、关键 SMART 属性、分区、空间、
可靠性和警告；不记录温度、关联 ZIP 文件名或 SHA-256。缺失值显示为
「未提供」，布尔值显示为「是／否」；HDD 的 Windows 磨损值明确注明不一定适用。
关键风险计数的 `raw.value` 非零时显示「注意」，但只有 smartctl 的 `when_failed` 非空时才
标为「异常」。TXT 标题区记录生成工具名、DAISY 版本和作者。

Manifest 的 `collection.status` 取值如下：

- `complete`：smartctl 读取完整，且没有采集提示；
- `complete_with_warnings`：读取完整，但存在可选 Windows 查询缺口、健康位或
  历史记录提示；
- `incomplete`：smartctl 退出状态的 `0x01`、`0x02` 或 `0x04` 位存在，说明命令
  解析、设备打开／识别或 SMART 命令失败。该 ZIP 仅为诊断归档。

ZIP 发布顺序：

1. 在目标目录以微秒和随机 ID 创建 `.partial.zip`；
2. 写入两个原始／结构化 payload 和 Manifest；
3. 重新打开并执行 CRC 与成员集合复核；
4. 对稳定 ZIP 字节计算完整 SHA-256；
5. 取摘要前 8 个十六进制字符并转大写；
6. 构造最终名称；
7. 以目标存在即失败的方式发布；冲突时保留 partial；
8. 对最终 ZIP 自动执行完整核验：文件名指纹、安全路径、成员集合、Manifest、
   时间对、成员字节数和 CRC 必须全部通过；失败时保留已发布 ZIP 并明确报错；
9. 若选择简化报告，再以 no-clobber 方式发布外部 TXT；极端竞态导致 TXT 发布
   失败时，已经发布的 ZIP 保留，并明确报告 TXT partial 的位置。

最终名称：

```text
<卷标或回退>_PROFILE_YYYY-MM-DD_HH-MM-SS_XXXXXXXX.zip
```

多卷标按分区顺序去重后用 `+` 连接。无卷标则回退盘符，再回退
`PhysicalDriveN`。文件名不使用序列号作为默认人类标识。

完整 ZIP SHA-256 不写回 ZIP 内部，避免自引用。文件名只保留高 32 bit；完整摘要
由生成结果和核验命令输出。目标存在即失败，不覆盖既有文件。

归档可能包含序列号、卷标、卷 GUID、挂载路径、PNP Device ID、计算机名和
BitLocker 状态；不得未经检查公开分享。

### 11.8 创建后自动核验准入

创建流程必须同时确认：

- 文件名存在 8 位十六进制后缀，且与 ZIP 实际 SHA-256 高 32 bit 相同；
- ZIP 无重复、目录、不安全或穿越路径；
- 按 ZIP 文件名前缀推导的 3 个 schema 3 文件精确匹配，不多不少；
- ZIP CRC 全部通过；
- Manifest schema 为当前版本；
- Manifest 的类型、平铺布局和成员名前缀与 ZIP 文件名一致；
- Manifest 中同一事件的 UTC 与本地时间可以解析、带时区且代表同一时刻；
- Manifest 的 payload 名称、角色及字节数与 ZIP 成员一致。

任一失败均令创建流程返回失败，不提供 `--force` 绕过。底层核验函数保留供创建
流程和测试调用，但不暴露为独立 GUI 功能、Module 脚本或统一 CLI 子命令。

### 11.9 错误与退出码

- CLI `0`：完整或带提示的完整采集；
- CLI `1`：诊断 ZIP 已生成，但采集状态为 `incomplete`；
- CLI `2`：环境、参数、采集、发布或创建后自动核验失败；
- smartctl 的位掩码不直接作为 DAISY 进程退出码；它写入 Manifest，并在选择
  外部简化报告时写入报告；
- smartctl 返回健康或历史错误位但 JSON 可解析时，采集仍可归档；
- 无 JSON、目标身份变化或完整关联缺失时拒绝建立完整硬盘档案。

### 11.10 测试边界

默认 `unittest` 使用合成设备、内存数据和系统临时目录，不读取真实硬盘。测试
入口为 `Script_DAISY_Test_Storage_Unit.py` 和
`Script_DAISY_Test_Storage_Read_Only.py`，覆盖：

- 盘号映射；
- Windows 卷空间与布局间隙；
- 热插拔身份保护；
- smartctl 固定只读模板；
- GUI/CLI 共用目标关联；
- ZIP 平铺内容、UTF-8/LF、CRC、文件名指纹、篡改和 no-clobber；
- PowerShell 禁止命令和 `shell=True` 审计。

实盘验证是显式的额外步骤，必须先重新列盘并按物理盘编号选择。

### 11.11 已知限制

- RAID 控制器、厂商驱动、USB 桥和虚拟磁盘可能隐藏或改写 SMART；
- Windows `Healthy` 与 smartctl 结论来自不同层，不能互相替代；
- Storage Reliability Counter 不保证所有设备都实现；
- 卷空间是采集瞬间值，可能在 ZIP 写入前发生变化；
- 单个物理盘包含多个卷标时，文件名只承担人类提示，不是权威身份；
- 32 bit 文件名指纹存在碰撞，不能替代完整摘要、数字签名或外部校验清单。

## 十二、版本、界面适配与已知限制

### 12.1 版本与兼容性

- 当前应用版本为 `1.6.8`。统一 `scan` 新产物使用 `schema_version=4`、
  `daisy-snapshot-v4` 和独立 `daisy-resume-v1`；冻结的 `full-scan`、`quick-scan` 入口继续
  生成 schema 3。元数据 profile 仍为 7；封存数据库的长期只读兼容基线为 v1.4.1/schema 3。
- v1.6.1 是未打标签的阶段性修改，不作为发布版本；v1.6.2 在完整自动化回归、
  v1.4.1 FULL 兼容专项与发布审计通过后成为稳定标签。自动化验收不替代真实工具、特殊
  文件、超大档案和不同 Windows 设备的持续验证。
- v1.5.0 新增硬盘信息功能域，并统一 Module 与 Lib 脚本的旧域缩写前缀；
  v1.5.1 只优化 UI、交互、阅读报告和对应测试。v1.5.1 对快照核心层的功能改动
  仅限应用版本与 `_Issues.md` 问题报告的呈现边界；数据库 DDL、字段、约束、schema、扫描、
  Diff、数据库生成和业务语义均未改变。档案库文件名与导入路径的统一属于 v1.5.0。
- v1.6.2 是 v1.6.0 数据契约上的 UI 与交互正式版本：统一扫描新增 ExifTool/ffprobe 两个
  schema 4 冻结配置键，缺键默认 `true`；schema 3/4 DDL、Diff DDL、Reader 投影、
  resume contract 和各 schema 的读取版本声明均不变。冻结 `full-scan`、`quick-scan` 参数集合保持不变。
- 冻结 schema 3 `.partial.sqlite` 仍要求生成程序版本、数据库结构、元数据配置和 GPS 表
  完全匹配；v1.5.1 及更早的未完成快照不能由 v1.6.0 接管。schema 4 续传按独立
  续传契约、冻结配置、会话 (`session`) 和占用锁 (`lease`) 判定，不用补丁版本号替代续传契约。
  已完成的 schema 3 封存快照继续只读使用。
- 项目长期兼容门槛：v1.6.0 及后续版本中，所有接受封存档案快照或 Diff 的功能至少
  必须只读支持 v1.4.1/schema 3。旧库不得原地迁移；缺少新字段时使用明确的能力降级，
  不得显示为 0、空值或成功。该门槛不表示 v1.4.1 程序能够读取未来新 schema，也不把
  v1.4.1 未完成快照纳入无条件续传承诺。状态与恢复边界见本文第 7.3 节及本节；
  精确 DDL 和状态转换以状态层代码及其契约测试为准。
- v1.6.0 使用统一只读 Reader。它按身份表、数据库结构版本、封存状态和实际表列识别
  快照、Diff 和未完成快照，并将模块状态区分为 `available`、`empty`、`unavailable`、
  `incompatible` 和 `invalid`。对比／哈希核验／格式校验／解析、增量来源和问题报告读取均通过该层；
  schema 3 的 DDL、数据契约和发布版本身份在阶段 1 未改变。对于 schema 3，Reader 还
  读取 `hash_coverage`、配置和运行清单 (`manifest`) 的执行证据：模块执行后 0 行才是
  `empty/0`，
  Quick/No-Hash 明确未执行的模块是 `unavailable/NULL`，不得伪装为无问题或无记录。
  能力的语义状态与物理投影是否可查询分别记录；旧固定导出可读取结构完整的 schema 3
  空表，但新模块选择界面不得据此把未执行模块列为可选。
- 档案数据解析提供快照 15 个数据模块、Diff 6 个数据模块，状态只来自统一 Reader；
  「摘要／全部／自定义」三种导出范围的内部值分别为
  `human-summary`、`full-audit`、`custom`，并与 HTML/XLSX/CSV/JSONL 格式相互独立。只有
  `available` 模块可由导出范围或全选选中；`empty`、`unavailable` 和 `incompatible` 保留
  `0` 与 `NULL` 的差异和原因。工具原始输出模块固定显示隐私提示。Reader 的
  快照模块界面名称固定为「快照概览、问题与诊断、文件清单、目录清单、文件哈希、照片元数据、
  视频元数据、视频定位点、音视频轨道、工作图像信息、文档元数据、压缩包与成员、工具原始输出、
  元数据诊断、扫描运行记录」；这组名称仅用于 GUI，不改变模块 ID、稳定投影、导出标题或文件名。
  模块按钮使用固定宽高，不随窗口边缘伸缩，只按可用宽度换行；模块区四边留白一致。
  「全选／取消选择」复用文件浏览按钮的尺寸和样式。
  schema 4 发布指纹复核新增默认开启的可选参数，数据库解析快速识别可延迟完整文件摘要并
  显示未复核警告，正式导出仍恢复摘要和 SQLite 完整性检查。稳定投影不导出 `entry_id`
  作为跨库身份，大表以 `fetchmany()` 分批读取；`raw_payloads` 中的工具原始输出逐行核对 zlib、长度、SHA-256
  和 UTF-8 JSON。HTML/XLSX/CSV/JSONL 共用一次模块遍历，生成记录输入和产物摘要的
  运行清单，并在唯一暂存目录完成后以不覆盖方式发布。HTML 为带随机数 CSP、无外部资源、
  限定预览的自包含报告；XLSX 使用流式工作表分片，首张为概览，支持冻结、筛选、拆表和字符串公式
  防护。统一 `parse-db --database` 已提供三种导出范围、可重复或逗号分隔的数据模块选择和四格式
  选择，输出数据模块进度并安全发布导出结果；`export-report --snapshot` 与 `--diff` 仍走冻结参数和
  写入器，CSV/XLSX 顺序和值没有切换。GUI 已接入后台只读数据库识别、Reader 数据模块
  状态、导出范围、四格式选择和标准运行面板切换；历史入口仍保留为隐藏兼容任务定义 (`TaskSpec`)。
- v1.6.0 外部原生工具故障边界不修改 schema 3。统一入口仅在非 GUI 任务工作进程中
  设置 Windows `SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX |
  SEM_NOOPENFILEERRORBOX`；Tk 主进程、注册表和系统 WER 配置不修改。冻结完整扫描的 ffprobe
  调用增加 `CREATE_NO_WINDOW`，Windows 高位退出状态记录为十六进制工具崩溃。统一工具
  运行层覆盖启动、监控线程、等待、输出上限、超时、原生进程退出和精确回收；ExifTool
  坏会话会作废、重建并有限重试。连续同签名工具故障默认 3 次后熔断，schema 4 阶段转为
  `failed_recoverable`，剩余条目保持可重试，问题报告只输出聚合事件。工具崩溃只能说明本次
  解析未完成，不能直接证明文件损坏。受控测试没有故意触发真实访问冲突 (access violation)，因此
  不能承诺所有第三方二进制绝不会显示自身实现的 UI。
- v1.6.0 阶段 3 已实现独立 schema 4 状态层：新表保持 schema 3 业务表超集，新增会话
  (`session`)、处理尝试 (`attempt`)、格式当前结果、低频性能摘要、CAS 状态转换、精确占用锁、
  截断事件恢复和发布
  副本。统一 Reader 只把 `run_state=published` 的完整 schema 4 当作普通封存输入，并用
  流式业务投影比较一次完成与多次会话续传；运行身份、处理尝试和观察时间不进入业务
  投影。schema 4 生产链已经通过新 `scan` CLI 从证据采集运行到发布；GUI 的可见
  「扫描」页也已切换到该统一入口。旧 `full-scan`、`quick-scan` 命令与隐藏兼容 TaskSpec
  继续使用冻结 schema 3，不得把新页切换误写成旧命令的数据库契约也已改变。
- v1.6.0 阶段 4 的第一检查点已在哈希库中实现 `spawn` 工作进程、启动握手、30 秒无进展告警、
  每 9 GiB 增加 90 秒的动态无进展超时、三种原子处置、精确句柄回收、逐文件检查点、
  处理尝试与低频性能摘要。schema 4 哈希当前结果与历史处理尝试在同一 SQLite 事务提交；
  暂停或停止中的当前文件不保存 `hashlib` 内部状态，续传时从文件起点重做。该内核已接入
  `Script_DAISY_Lib_Scan_Runtime.py` 的 schema 4 生产链，并由 `scan` CLI 与 GUI 可见扫描页调用；旧兼容
  命令未接入，因此 schema 3 兼容扫描业务语义未改变。
- `Script_DAISY_Lib_Scan_Runtime.py` 进一步封装 schema 4 未完成快照的不覆盖预留、只读续传预览、
  `<partial>.lease` 明确接管和数据库／占用锁双端心跳。未完成快照、发布基名和事件日志
  必须互不相同；同一会话暂停后进程消失时，旧会话先转为 `abandoned`，再创建续传会话。
  续传与心跳只以 `mode=rw` 打开现有数据库，损坏的占用锁仅能由明确续传接管，
  且接管后重新核对会话、状态和配置。该层只操作调用方给出的精确路径与 PID，不枚举
  或终止其他进程。
- `Script_DAISY_Lib_Scan_Runtime.py` 的阶段 4 控制子层使用 `daisy-control-v1` 单行 UTF-8 JSONL，把 GUI
  的暂停、继续、保存并退出、停止和超时决定路由到当前运行段。消息上限为 4096 字节，
  并使用严格递增序号；首个生命周期动作生效，超时决定绑定当前工作进程 PID。
  同会话暂停后的继续会创建新控制对象并从当前文件起点重试；稍后保存并退出通过受审计的
  `paused_saved_for_exit` 动作结束会话。该控制子层已接入 `scan --control-stdin` 生产
  入口和 GUI；扫描页提供暂停／继续、保存并退出和停止，统一核验页只提供暂停／继续与
  停止，不得表述为支持跨重启续传。
- 同一运行层已为枚举、元数据和复扫提供显式受控包装：枚举暂停后重跑临时树对账，元数据
  在单文件提交后停下并从数据库状态重建全局进度，复扫保存已观察变化后可重跑。数值进度
  以 500 ms、当前文件以 100 ms 限频；当前文件开关关闭时不调用生产回调。Core/Meta 的
  schema 3 旧函数只增加默认关闭的末尾参数，未传回调的旧扫描路径不改变。
- `Script_DAISY_Lib_Scan_Runtime.py` 的 schema 4 内部生产链依次执行枚举、哈希、元数据、可选格式、复扫、
  独立哈希复检、读取性能分析、封存和发布。扫描专用 `verify_format` 明确记为 skipped，
  不把未执行写成完成。只有前置 checkpoint 全部为 completed/skipped 且不存在 running
  处理尝试或 `pending`/`processing` 当前结果时才进入封存。运行清单、计数和事件先内嵌，
  SQLite 与外键检查通过后，未完成快照才进入 `sealed_unpublished`；发布副本最终把发布
  检查点和会话写为 `completed`/`published`。任一目标冲突都保留已封存但未发布的快照
  和精确占用锁，不重扫源档案。
- 读取性能分析只消费当前成功、`origin=computed` 的主哈希 attempt；`origin=reused`、
  独立抽验和历史处理尝试不参与比较。吞吐比较组必须同卷、同扩展名（无扩展名时同
  `media_kind`），并按 `round(log2(size_bytes))` 归入最大约 2 倍跨度的相近大小带；组内
  至少 8 个且文件至少 1 MiB。算法用吞吐中位数和 MAD；MAD 为 0 时分别以中位数的 50%
  和 25% 作为低／高置信度界线。30 秒无进展至少为低置信度，达到该文件动态超时
  阈值为高置信度。低置信度只留 `read_performance`，高置信度进入同名 `_Issues.md` 问题报告；措辞只称
  可疑逻辑路径／时段，明确不能推断物理坏区。
- `Script_DAISY_Lib_Snapshot_Issues.py` 通过统一 Reader 只读分析 schema 3/4 快照，固定输出「目录枚举问题」、
  「哈希问题」、「元数据问题」、「格式校验问题」、「RAW 深度校验问题」、
  「读取性能异常候选」和「运行与证据问题」七个板块。已执行且无问题为 `0`，未执行、
  旧库未记录或能力不可解释为 `NULL`；不支持、无法判定或格式未识别只显示去重总数，
  不显示路径，也不单独触发报告。普通警告、次要警告和字段规范化提示
  与低置信度性能样本不逐项展开；明确损坏类警告或单文件至少 100 条未展开警告才进入
  待复核候选。`CopyN` 在展示和家族计数中归一化为 `Copy#`，原始 SQLite 不改写。
- schema 4 发布层可接收只读问题报告构建器：先在 `mode=ro` 发布副本上分析并复核摘要
  未变化，再以 UTF-8 无 BOM、LF 和不覆盖方式创建伴随文件，最后发布 SQLite。报告或
  SQLite 任一目标冲突均不覆盖；SQLite 发布失败时只删除本次新建的伴随文件，保留已封存
  但未发布的快照供发布重试。该能力已接入 schema 4 生产链、新 `scan` CLI 和 GUI 的可见扫描页；
  旧兼容命令保持冻结路径。

- `Script_DAISY_Module_Scan.py` 是 schema 4 的首个生产编排入口。新建时冻结
  Full/Quick、格式校验、每 9 GiB 增加 90 秒的无进展策略和工具身份；续传前先做只读预览，
  存在有效占用者时，会在源目录或工具预检前拒绝接管；`stopped` 状态必须显式指定
  `--manual-resume`。
  `--control-stdin` 只读取 `daisy-control-v1` JSONL，不关闭调用方 stdin；本任务的
  占用锁心跳在封存前停止并确认线程已退出，避免后台写入改变最终字节；事件日志创建或
  写入失败会阻断扫描／封存。Quick 不调用外部工具；统一 `scan` CLI 的 Full 模式默认关闭
  格式校验。发布成功、保存并退出、手动停止分别返回 0、75、130。GUI 的可见扫描页已经切换到该入口；
  旧命令仍按冻结的 v1.5.1 业务语义生成 schema 3，作为兼容路径保留。
- `sealed_unpublished` 不得通过普通 `resume_run` 降级回扫描阶段。`scan --resume` 会改用
  只发布重试：在读取源目录或运行工具预检前识别已封存状态，创建独立发布重试会话，
  停止精确心跳后直接从已封存但未发布的快照建立发布副本。失败会话保留并可再次重试；
  成功库的运行清单会同步会话数和发布重试次数，并明确
  `source_rescanned=false`。因此目标冲突后的发布重试不依赖源文件仍在线，也不会改写扫描业务
  证据。

### 12.2 信息架构与字段命名

- 当前顶栏固定为 `文件｜功能｜视图｜设置｜高级｜帮助`，菜单栏使用浅米黄色底色，
  字体族和字号与正文同步。「功能」按「档案」「设备」「环境」分组，顺序与色带下方
  「档案扫描建库、档案快照对比、档案数据核验、档案数据解析、硬盘信息登记、运行环境检测」
  六个完整入口一致。
  「设置」包含窗口大小、界面字体、工具路径、完成提示音和结果目录弹窗；恢复入口统一为
  顶部功能栏的「重置软件」；
  「高级」只包含扫描选项、核验选项、对比选项、命令预览和功能自检。扫描选项只设置超时处置和
  当前文件显示；哈希复检比例固定为默认值，不在 GUI 中提供调节入口。
- GUI 的数据域只显示「档案扫描建库」「档案快照对比」「档案数据核验」和
  「档案数据解析」4 个用户入口；旧完整／快速扫描、
  哈希／格式校验和旧版报告导出任务定义继续用于配置迁移、续传指针与脚本兼容，不重复显示。
- 顶部功能入口与设置页、进度、日志和报告统一使用六字完整功能名；6 个入口始终保持
  单排，并采用与表单模式按钮相同的平直外形。入口使用与暂停按钮一致的淡青色系、深色
  描边和固定宽高，并与页面右下角常驻任务按钮统一尺寸；整组靠左，不随窗口横向拉伸，
  普通态和选中态的变化不得改变按钮尺寸。
  GUI 中，值域严格为
  `{False, True}` 的表单项固定使用二态按钮：黄色表示关闭，按下后绿色表示启用；顶部
  设置不提供控件样式切换。扫描页的完整／快速使用两个
  固定模式按钮；未选择时不展开其他设置。完整扫描的元数据只显示 ExifTool/ffprobe 两个独立
  按钮，各自在「全量 → 基础 → 关闭」之间循环。逐工具
  全量范围只控制既有 `raw_payloads.provider`，不改变 DDL。
  数据核验默认不选择输入类型，未选择「已有数据库／无数据库」时不显示下方任何内容；已有
  数据库后只先显示「路径未变／路径变化」，第二层选完才显示对应输入和核验设置。路径状态
  决定直接采用快照原路径，还是显示根目录映射池。无数据库时显示独立目录池，并禁用没有
  基准可比较的哈希复核。核验项目使用哈希复核、基本校验、ExifTool、ffprobe、7-Zip、
  rawpy/LibRaw 六个完整名称按钮。当前会话尚未收到完整环境清单时，核验区显示「需要先运行环境检测」，
  并使用与硬盘登记管理员状态相同层级的琥珀色边框提示卡、粗体标题和独立说明；四个外部
  项目灰显，开始操作被参数预检拒绝；基本校验作为内置能力保持可见。环境检测后，
  ExifTool、ffprobe 和 7-Zip 只按已验证工具清单启用，rawpy/LibRaw 只按隔离运行能力结果
  启用；不可用项保持灰色并在悬停说明中给出原因。界面灰显不覆盖用户保存的请求状态，
  能力恢复后仍按该状态选择；新配置默认请求全部项目。启用项目全量检查适用文件。
  续传重试范围使用
  「仅未处理／瞬时失败／全部未成功」三个互斥按钮；超时处置使用高级菜单中的单选项；
  档案数据解析的导出范围及
  HTML/XLSX/CSV/JSONL 输出格式均使用固定按钮，不显示复选框。
  RAW 深度校验受统一能力探测约束。对比页默认按单根目录自动对应；只有选择「多根目录」才
  展开根目录名配对。每组「基准根目录名／对比根目录名」按上下两行编辑；「添加配对」位于
  配对内容池上方和边框外。对比的指纹降级位于
  「高级→对比选项」；既有 `--force`、`--map-root` 默认值和执行语义不变。任务表单的
  离散选项全部使用按钮，不创建 `ttk.Combobox`；标准菜单栏不属于任务表单下拉框。
- 普通按钮使用统一字符宽度、内边距和 12 px 水平间距；输出目录的「浏览」使用较小专用
  尺寸，输入资源的「添加／选择」使用淡青色明显按钮。所有按钮文字统一使用与正文相同的 10 号标准字号和
  常规字重；按钮层级只由尺寸、颜色和位置表达，不单独放大或加粗文字。运行环境检测页按
  「检测状态 → 软件安装 → 环境报告目录」排列，
  检测状态和软件安装共用 7 个对齐位置及完整工具名，包含 Python、ExifTool、ffprobe、
  7-Zip、PowerShell、smartctl 和 rawpy/LibRaw；6 个安装操作可点击。可安装项第一行
  显示工具名，第二行统一显示「安装或更新」；PowerShell 对应列第一行显示工具名，第二行
  显示「系统提供」并禁用。等待检测为灰色，成功为绿色且第二行显示实际版本，不可用为
  红色；点击任一状态按钮均重新执行完整环境检测。两排按钮与「完整扫描」等模式按钮同宽，
  高度约为其 1.5 倍以容纳两行文字，并靠左按最多四列整项换行，不横向拉伸填满整行。
- 扫描模式和生成方式需要改变条件字段时，GUI 先在未映射的新 `Frame` 中完成控件、字体和
  几何构建，再原子替换 `Canvas` 的当前表单并销毁旧 `Frame`；建库方式等不影响 `active_when`
  的按钮只保存值和更新预览，不重建表单。档案数据解析的摘要／全部／自定义预设原位更新
  同一个 `ParseModulePool`，不得销毁表单容器、数据库输入或输出格式按钮。
- 点击「解析数据库」时不得收起或展开设置、进度、日志，不得重建 `form_inner`，也不得把
  表单滚动位置重置到顶部；识别中只禁用冲突操作并更新状态、进度数据与说明文字。成功或失败
  均原位更新同一个 `ParseModulePool`。模块按钮使用固定尺寸、按可用宽度换行并从左侧开始
  排列，不随容器横向拉伸。
- 顶部「设置」菜单提供默认关闭且持久化的「完成提示音」。普通任务正常结束或
  「完成但需要检查」时异步播放一次；失败、暂停、保存并退出、停止、依赖安装和硬盘检测
  准备步骤不播放。提示音失败只写日志，不改变退出码或报告。
- 「结果目录弹窗」默认关闭。普通业务任务产出有效结果后，只将底部卡其色的
  「产出」按钮以绿色闪烁两次，再自动恢复，不抢焦点；启用该设置后才额外询问是否打开目录。
  该按钮常态使用卡其，顶部六个未选中功能入口使用天青，二者均无描边。
  底部操作从右向左固定为「开始／停止 → 暂停／继续 → 产出」。三个常驻按钮与
  日志标题区三个工具按钮等宽，与扫描模式按钮等高。同一个「开始」按钮在任务运行时原位
  变为橙色「停止」；扫描进入暂停请求态后，才在暂停按钮左侧显示「保存并退出」，暂停按钮
  原位改为「继续」，但二者在后端确认安全暂停前均保持禁用。未使用独立的可见停止按钮。
  「重置软件」位于顶部功能栏标题区，运行期间禁用；「设置」菜单不再提供重复恢复入口。
- 任务队列成功、需检查、失败、停止或保存并退出后，保持结束瞬间的设置、进度、日志和
  小窗状态；不得自动收起进度或日志，也不得自动展开设置或退出小窗模式。依赖安装后的
  自动环境复检与硬盘检测完成时遵守同一面板保持规则。
- 扫描续传提示卡位于设置标题行下方，不得占用标题右侧的设置操作列；目录、核验根目录映射、
  快照配对和硬盘列表使用带深色边框的内容池。添加／检测按钮全部位于池子边框外，内容在
  下方池内逐行显示，空池也保留占位说明。扫描与核验的添加目录、快照对比的添加配对，以及
  数据解析的数据库选择使用淡青色明显按钮；快照输入和输出目录使用中性的「浏览」。硬盘池
  不显示「全选／取消选择」。

常规设置页的字段标题如下；括号表示条件满足时才出现：

| 页面 | 字段标题 |
|---|---|
| 运行环境检测 | 环境报告目录 |
| 档案扫描建库 | 扫描模式、生成方式、档案根目录、建库方式、（续传快照）、快照保存目录、元数据（ExifTool/ffprobe 独立三态）、文件标识（不采集／NTFS-ID）、哈希（不采集／SHA-256）、（重试范围） |
| 档案快照对比 | 基准快照、对比快照、目录数量、对比结果目录、（根目录名配对）；高级菜单提供指纹降级 |
| 档案数据核验 | 核验方式、（封存快照）、（路径状态）、（档案根目录）、核验项目、核验报告目录 |
| 档案数据解析 | 输入数据库、导出范围、数据模块、输出格式、数据导出目录 |
| 硬盘信息登记 | 硬盘选择、硬盘档案目录、简化报告 |

顶部「设置→工具路径」直接使用 ExifTool、ffprobe、7-Zip、PowerShell 和 smartctl 的
完整名称，并只追加「手动指定」「已检测」或「未检测」短状态，不在菜单项内显示完整路径；
扫描、核验和对比选项分别置于「高级」的独立子菜单。六个功能页说明各用一句短句直接描述用途，不写
内部实现边界；字段帮助和悬停说明按当前字体的实际像素宽度换行，避免孤立标点、强制断句
和固定字符数换行。中文字段和分区标题最多 6 个汉字；纯 ASCII 工具品牌保留官方完整
名称，不为凑长度使用简称，其他纯 ASCII 标题最多 12 个字符。标签共用固定
右边界；输入资源按钮使用「添加」或「选择」，输出目录使用「浏览」，并置于内容左侧。
界面不显示必填星号，
`required` 属性及运行前校验仍然生效。

### 12.3 面板与运行状态

v1.6.8 的权威配色源为《明日方舟_孤星_配色取样_修复版_v2.pptx》。核心色和辅助色固定
如下；交互需要的深浅色阶可以从核心色延展，但不得用近似取样值替代这些锚点：

| 色彩角色 | 色值 | GUI 用途 |
|---|---|---|
| 档案黑 | `#131210` | 高对比强调与深色基准 |
| 信号橙 | `#F06733` | 停止动作 |
| 浅黄字 | `#DFD9A9` | 深色提示及面板标题文字 |
| 先驱青绿 | `#88C1B0` | 淡青入口、选中态和成功语义的基准色 |
| 工程黄 | `#ECAA3C` | 警告、关闭态和续传提示 |
| 档案深红 | `#9A2D28` | 错误、危险与停止悬停态 |
| 深背景 | `#11110F` | 深色提示面 |
| 深色正文 | `#171614` | 浅色表面的正文 |
| 浅色正文／表面 | `#FFF9EF` | 内容表面、输入框与深色按钮文字 |
| 描边 | `#6A6257` | 普通字段、面板和内容池边框 |

界面还可使用用户提供的补充色板。补充色不改变六个核心色的语义，只负责大面积中性色、
信息分层和进度区分：

| 补充色 | 色值 | 当前用途 |
|---|---|---|
| 暖白 | `#F0E7E2` | 窗口底、中性按钮与进度／日志内容面 |
| 科研蓝 | `#5E8CC0` | 队列总进度 |
| 鼠尾草 | `#647B75` | 阶段进度与次要文字 |
| 灰褐 | `#A89F98` | 进度／日志面板边界 |
| 板岩 | `#2C3240` | 进度／日志标题栏 |
| 旧粉 | `#D3998B` | 错误状态浅底 |
| 墨青 | `#1F272A` | 日志正文 |
| 天青 | `#80AFC3` | 本阶段进度与日志选区 |
| 卡其 | `#C5B778` | 元数据基础模式 |
| 石灰 | `#A9A397` | 中性按钮悬停态 |
| 暗橙 | `#DE5123` | 停止按钮悬停态 |

大面积底色不得直接铺满浅黄字或先驱青绿。功能模块容器不显示外框，所有按钮均采用无描边
平面样式；字段、内容池、设置卡、进度和日志面板仍保留结构边界。按钮状态只通过填充色和
文字体现。功能模块整体与设置标题行使用灰褐底色；顶部模块常态为天青、当前项为板岩，
底部开始／暂停／产出分别使用科研蓝／天青／卡其，常态状态徽标使用板岩。

宽度为 12 字符的浏览、选择、展开／收起及日志工具按钮统一增加纵向内边距，不单独保留
一组矮按钮。元数据工具的全量／基础／关闭使用天青／卡其／旧粉，但三态统一为墨青文字和
常规字重。保存目录与其他单行输入框统一使用 `1 px` 字段边界。

- 功能栏、设置区、进度区和日志区可独立显示或隐藏，运行进度与运行日志默认
  收起。任务设置、进度、日志和命令区采用固定 grid 顺序，不提供拖动调整；点击
  开始任务后自动收起设置、展开进度和日志，日志获得 1080p 剩余纵向空间。命令预览
  默认关闭。队列总进度、当前任务阶段和本阶段工作量三条进度语义独立；顶部显示
  当前完整路径。运行进度与运行日志使用相同的板岩标题栏、浅黄标题文字和暖白内容面；
  三条进度分别使用科研蓝、鼠尾草和天青；日志可打开为
  单例独立窗口，与主窗口实时追加和清空同步。
- 小窗视图在空闲和运行时始终可进入；「视图」菜单以固定的「功能栏／设置区／进度区／
  日志区」复选项表示面板可见性，可见即勾选，并提供动态「小窗模式／完整界面」，
  小窗保留当前目标、三条进度和运行控制，并在返回时恢复面板顺序与
  固定布局。
  开始完整扫描前的确认框按分别／合并模式列出全部完整根路径，提示任务可能
  持续几小时到几天，再由用户确认是否执行。

面板状态转换必须遵循下表：

| 事件 | 任务设置 | 运行进度 | 运行日志 |
|---|---|---|---|
| 空闲进入页面 | 展开 | 收起 | 收起 |
| 点击开始任务 | 收起 | 展开 | 展开并占用剩余高度 |
| 任务运行结束 | 保持结束瞬间状态 | 保持结束瞬间状态 | 保持结束瞬间状态 |
| 开始检测硬盘 | 收起 | 展开 | 展开 |
| 硬盘检测成功 | 保持并刷新硬盘选择 | 保持 | 保持 |
| 硬盘检测失败 | 保持，硬盘清单不更新 | 保持并保留诊断 | 保持并保留诊断 |
| 点击解析数据库 | 收起 | 展开 | 展开 |
| 解析数据库成功 | 展开并刷新数据模块列表 | 收起 | 收起 |
| 解析数据库失败 | 展开并清空旧数据模块 | 展开并保留诊断 | 展开并保留诊断 |

进度详情必须把 `source_error`、`tool_error`、其余 `errors`、`not_applicable` 和
`skipped` 分别呈现为「源文件问题」「工具故障」「异常记录」「不适用」和「跳过」。不支持或
不适用的文件类型不能为了让进度显得醒目而累加到 `errors` 或显示为「异常记录」；底层结构化状态、问题报告触发规则和
GUI 计数必须使用同一语义边界。

v1.6.0 的生产事件与 UI 绘制使用独立限频：冻结兼容命令的 `Progress.update()` 最多
每秒发送一次；schema 4 扫描的数值进度最多每 500 ms 发送一次，可选当前文件事件最多
每 100 ms 发送一次；阶段开始／完成立即发送。GUI 每 80 ms 合并并清空事件队列。未知
总量时，进度条以 12 ms 步进播放动画，但动画不表示底层扫描以该频率取得新数据。

| 阶段 | schema 4 进度来源 | 显示边界 |
|---|---|---|
| 枚举 | 受控枚举计数，最多 500 ms 一次 | 当前目录调用仍可能暂时阻塞 |
| 哈希 | 工作进程的数据块进度＋文件完成，最多 500 ms 一次 | 当前文件可独立以 100 ms 限频显示 |
| 元数据 | 文件边界累计，最多 500 ms 一次 | 单次外部工具调用期间保持上一数值 |
| 格式与 RAW | 文件终态与受控工作进程事件 | 超时对话框不显示未经证据支持的进度 |
| 复扫／哈希复检 | 条目累计，最多 500 ms 一次 | 当前文件开关关闭时不发送路径事件 |
| 封存／发布 | 状态边界 | 无可解释中间总量时使用未知进度 |

因此「最多每 500 ms」是上限，不是保证每半秒刷新；工具正在处理一个文件而尚无新证据时，
保持上一数值，不显示未经证据支持的变化。

### 12.4 窗口、DPI 与滚动

- 普通窗口以 `1920×1080` 为默认目标尺寸；「设置→窗口大小」把 `1920×1080` 放在首项，
  其后为 `1366×768`、`1600×900`。Windows 进程启用 Per-Monitor V2
  DPI 感知；窗口进入不同分辨率、工作区或 DPI 的显示器后，会重新约束尺寸、位置、
  最小值和功能模块宽度。工作区不足时优先完整留在当前显示器内。标准字号下的默认布局
  使用精简说明与紧凑表单间距；内容未溢出时 Canvas 将滚动位置固定为顶部，不产生
  顶部空白，并隐藏滚动条；只有真实内容高度超过视口时才允许纵向滚动。
- 表单只为包含多个字段且不与首字段重名的分组显示独立分区标题；单字段分组直接使用字段
  标题，避免重复文案占用 1080p 的纵向空间。
- v1.6.2 的历史发布验收曾覆盖多字体、3 档字号、1.0/1.25/1.5 Tk scaling，以及
  1840×1020、1440×900、1280×720、1280×960、1280×1024、1100×850；设置菜单另覆盖
  1366×768、1600×900、1920×1080。v1.6.8 增加核验条件项和环境按钮后，不再承诺所有页面
  初始无滚动；较小工作区、展开的条件字段或特大字号只有在内容超过视口与 2 px 几何容差后
  才显示滚动条，并可到达最后一项。会反复创建窗口的固定按钮交叉矩阵代码已经删除，视觉
  验收由 AI 按需手动打开软件执行。
  顶部 6 个入口必须保持单排，因此 1024 px 不声明为正常支持宽度；普通界面最小宽度为
  1180 px，窄窗口只压缩按钮内部横向留白，不缩写、裁切或换行六字入口。

### 12.5 用户配置、关闭与管理员重启

- 顶部「设置」菜单持久化窗口大小、字体族、字号、完成提示音、结果目录弹窗、
  最后功能页面、白名单非路径选项和手动工具路径。
  正常关闭或管理员重启后恢复页面与非路径选项，不保存普通档案根目录、输入数据库、输出
  路径或硬盘选择；「保存并退出」产生的受控未完成快照路径属于续传提示例外。
  空闲关闭不确认；运行或启动中关闭始终确认。字体菜单只显示本机已安装候选字体，
  标准字号为默认值；表单、菜单、提示与独立日志窗口同步应用所选字体和字号。
- GUI 中仅「硬盘信息登记」提供管理员模式入口。管理员权限通常能取得更完整的 Windows
  存储和 SMART 信息；非管理员模式仍可在确认风险后尝试，但部分查询可能不完整或失败。
  该页的管理员模式按钮在空闲时先保存当前页面，再通过 Windows UAC 重新启动应用，且不会
  在原进程内动态改变权限。

用户配置文件固定为 `Output/GUI_Settings.json`，编码为 UTF-8 无 BOM、LF，并以临时文件
加原子替换写入：

| 键 | 类型／默认值 | 语义 |
|---|---|---|
| `version` | `3` | 用户配置文件格式版本 |
| `window_size` | `[1920, 1080]` | 普通窗口目标客户区；仍受当前工作区约束 |
| `font_family` | `Microsoft YaHei UI` | 首选界面字体；不可用时回退到已安装候选字体 |
| `font_size_delta` | `0` | 标准 `0`、较大 `1`、特大 `2` |
| `completion_sound_enabled` | `false` | 普通任务正常完成或完成但需要检查时，异步播放一次系统提示音 |
| `result_directory_prompt_enabled` | `false` | 任务产出结果后是否询问打开目录；关闭时结果按钮仍闪烁两次 |
| `last_task_key` | `env_check` | 正常关闭或管理员重启后恢复的功能页面 |
| `manual_tool_paths` | `{}` | 仅保存固定工具名对应的绝对路径，运行时重新预检 |
| `task_options` | `{}` | 仅保存白名单非路径选项；不接受目录、数据库或输出路径 |
| `recovery_scans` | `[]` | 最多 20 条受控未完成快照续传提示，不自动开始读取 |

旧配置中的 `binary_control_style` 与其他未知键一样被忽略，不会转成任务参数或再次写入；
非法字段逐项回退。`storage_list` 是硬盘信息登记的内部步骤，不能
作为恢复页面。除 `recovery_scans` 中受控的未完成快照路径外，配置文件不得包含档案路径、
封存数据库路径、输出路径、硬盘编号、目录队列、日志或进度。运行或启动中关闭始终执行
确认；确认退出时先保存页面配置，再停止
本窗口自己的任务。管理员重启也先保存页面，只有提权进程成功启动后才关闭旧窗口。

正常关闭、管理员重启与重置软件的恢复边界如下：

| 操作 | 下次页面 | 表单内容 |
|---|---|---|
| 正常关闭后重开 | 最后功能页面 | 保留白名单内的非路径选项；路径、硬盘选择、日志和进度不保存 |
| 管理员模式重启 | 当前功能页面 | 保留白名单内的非路径选项；路径、硬盘选择和当前日志不保存 |
| 重置软件 | 运行环境检测 | 恢复窗口、字体和提示默认值；清空任务选项、手动工具路径、续传提示、硬盘清单、日志、进度和可重建缓存 |

### 12.6 导出与其他界面行为

- 「档案数据解析」先识别快照或 Diff，再按统一 Reader 能力选择数据模块。快照最多 15 个数据模块，
  Diff 最多 6 个数据模块；输入框下方使用独立的「解析数据库」操作行，其位置和尺寸与硬盘页
  「检测硬盘」一致。GUI 将内容选择命名为「导出范围」和「数据模块」，范围用按钮选择，
  数据模块按钮固定靠左排列；解析操作只原位替换模块内容与摘要，不改变页面展开状态或滚动位置。
  默认 `full-audit`；HTML/XLSX/CSV/JSONL 四个格式按钮默认全部开启，手动修改格式后
  切换导出范围不得覆盖格式选择。`human-summary`/`full-audit`/`custom` 与输出格式保持正交。
  HTML 是无网络资源的自包含阅读首页；XLSX 使用 Unicode 内联字符串、中文工作表、
  冻结表头、筛选、语义列宽和超限拆表；CSV 为 UTF-8 无 BOM/LF 的稳定机器字段；JSONL
  保留嵌套类型和完整值。运行清单 (`manifest`) 记录输入身份、所选数据模块与格式、行数和
  产物摘要。旧
  `export-report` 继续按冻结顺序生成旧 CSV/XLSX，供已有自动化兼容使用。
- 「帮助」依次提供「关于」「联系作者」「GitHub 主页」；「关于」
  显示应用版本、统一扫描与旧版兼容快照各自的数据库结构版本、元数据配置、
  快照／硬盘归档文件名布局、硬盘归档结构版本和 `v1.4.1` 封存快照兼容基线。续传说明区分
  按独立续传契约判断的 schema 4 统一扫描，以及要求同一生成程序版本的冻结 schema 3 旧入口。
- GUI 默认优先使用 `Microsoft YaHei UI`，并可在本机已安装的中文／系统候选字体间
  切换，不依赖第三方字体。标准正文基准为 10 号，「较大」和「特大」分别在此基础上增加
  1 级和 2 级；已有 `font_size_delta` 配置继续按相同含义读取。
  功能模块、设置、运行进度和运行日志标题区的工具按钮统一使用与「添加／浏览」相同的
  小按钮尺寸；同一操作行使用相同字体、内边距、列间距和右侧基线。「收起模块」与
  「收起设置」的右边界对齐。展开／收起只改变内容区，标题栏水平边距和按钮 x 轴位置保持
  不变；点击后焦点交还主窗口，不显示文字周围的虚线焦点框。功能模块标题使用与运行进度、
  运行日志一致的黑色标题字，三色装饰线
  恢复为原有 4 px 细线。系统标题栏使用 16/32/48 px 小雏菊多尺寸图标；界面不再
  显示 DAISY 花体字标。
- 单项任务也完整进入队列模型，始终显示 `队列 1/1`；多根目录和多块硬盘按
  实际子进程逐项显示 `队列 i/n`。每项在普通界面和小窗中均显示完整当前目标。
- 可同时打开多个 GUI 窗口。每个窗口的表单、队列、日志、进度、事件队列和子进程
  句柄属于各自实例；相同或不同任务可并发运行。窗口仍共享操作系统资源、外部工具和
  用户指定的输出路径，因此并发会竞争磁盘 I/O；并发任务应使用不同输出目录，
  快照类产物继续依靠唯一未完成文件与不覆盖发布保护正式文件。
- 硬盘信息登记检测开始时自动展示进度与日志。检测成功后弹窗并刷新硬盘池，但保持设置、
  进度和日志当时的可见状态；用户需要选择硬盘时手动展开设置。检测失败同样保持面板并保留
  诊断信息。
- JSON 和 Markdown/TXT 报告直接写入 DAISY 工具名、版本与作者；纯业务 CSV
  保持原有表头，并用同组的 `Report_info.csv` 或 `_Info.csv` 保存报告身份。
  档案数据解析与冻结的报告导出兼容入口均生成便于阅读的中文 XLSX，避免 Excel 双击
  UTF-8 无 BOM CSV 时按本地 ANSI 代码页误判中文。

### 12.7 GUI 安装与软件重置边界

- 无 Python 时，`Script\Script_DAISY_Install_Python.ps1` 只在用户确认后
  通过固定包 ID 安装或更新 Python 3.14，不安装其他工具；Python 已可运行时，GUI 环境页
  也提供同一固定 Python 3.14 包的安装或更新入口。
- 该引导脚本保持 ASCII 英文提示，以兼容 Windows PowerShell 5.1 对 UTF-8 无 BOM 脚本的
  旧式解码行为；这不改变项目正式文本输出使用 UTF-8 无 BOM、LF 的要求。
- Python 已可运行时，「运行环境检测」会同时报告 Python、已发现工具的本机版本和
  全部不可用项。页面用 7 个等宽状态按钮显示 Python、ExifTool、ffprobe、7-Zip、
  PowerShell、smartctl 和 rawpy/LibRaw，再用对齐位置显示 6 个安装操作及 PowerShell
  「系统提供」占位；上下两区都按最多四列整项换行。所有按钮都使用完整工具名，等待时第二行显示
  「等待检测」，成功时直接显示实际版本，不使用笼统的「可用」，失败时显示「不可用」；
  两行文字同时设置整体居中和逐行居中，不依赖系统 ttk 主题的默认排版。Python 和外部工具
  只在用户再次确认后通过固定 WinGet 白名单处理；rawpy 只在确认后通过当前 GUI 所属
  Python 的固定 pip 白名单安装或更新，使用 `only-if-needed` 依赖策略。PowerShell 不由
  GUI 安装。
- ExifTool、ffprobe、7-Zip、PowerShell、smartctl 的手动可执行文件路径只在
  顶部「设置→工具路径」菜单统一指定，并优先于本窗口检测缓存和运行时查找。
- 安装队列完成后 GUI 刷新当前进程 PATH，并通过统一能力探测层重新检测外部工具和
  rawpy；探测与实际 RAW 解码仍在隔离子进程中进行，Tk 主进程不导入 rawpy。所有
  业务任务本身没有下载或安装逻辑。点击安装项先在后台运行不安装软件的固定白名单版本查询：外部工具
  使用 `winget show` 的默认最新版本，rawpy 使用当前 Python 的 `pip index versions`；只有
  成功解析最新版本后才显示检测到的当前版本和软件源最新版本，并询问是否安装。查询失败、无法识别或用户取消均
  不生成安装命令。自动复检完成后，日志与状态栏显示「当前版本（安装前）→ 更新后版本」及
  版本是否变化。安装失败、复检失败、仍不可用和版本未变化必须分别说明，不得仅凭安装命令
  退出码宣称升级成功。
- 顶部功能栏的「重置软件」先要求确认，再恢复默认窗口、字体、提示音和结果目录弹窗，清除
  已保存任务选项、手动工具路径和续传提示，并清除项目内白名单缓存目录
  `__pycache__`、`.pytest_cache`、`.mypy_cache`、`.ruff_cache`，
  独立 `.pyc`/`.pyo` 文件与当前窗口工具路径缓存，同时清空所有表单参数、
  目录队列、硬盘清单、日志和三条进度，并返回运行环境检测页。「设置」菜单不再保留第二个
  恢复入口。
- 重置操作不会跟随目录链接，也不会进入 `.git`、虚拟环境、`node_modules`
  或 `Output`。快照、Diff、导出结果和未完成快照均不属于缓存，不会删除。

### 12.8 性能与覆盖限制

- Diff 当前把两侧条目载入内存，内存占用随条目数增长。
- 完整扫描的哈希针对机械盘采用顺序读取，不在同一介质并行争抢。
- 正式环境检测和 GUI 不执行介质性能跑分。
- 非 Canon RAW 和更多厂商格式仍需要补充真实样本；代码路径存在不等于所有变体
  都已经过实样验证。

可重复执行的回归测试位于 [`Script\Test`](../Script/Test/)；GUI 顶部「高级→
功能自检」可启动默认安全测试，覆盖 SQLite schema、数据库约束、快照、Diff、
无窗口 GUI 参数映射和硬盘信息只读／归档边界；它不属于业务任务。真实 Tk／桌面测试代码
已从自动化套件删除，日常自检不会创建完整桌面窗口；视觉验收按需手动执行。命令见
[README](../README.md#测试)，历史版本变化见[版本演进](Spec_DAISY_Version_Evolution.md)。
