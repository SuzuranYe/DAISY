# DAISY v1.5.1 技术规格

- 状态：**现行规范**。
- 对应版本：**v1.5.1**。
- 本文定义 DAISY 的现行统一语义：DBS 获取数据库所描述的文件档案信息，STG
  获取物理硬盘信息；两者共用应用外壳，但保持独立数据模型。DBS 与 STG 的完整
  技术约束均以本文为准。
- 安装、启动与常用工作流见项目根目录的 [README](../README.md)。
- 从 `Kit_AL v1.0.2` 到当前版本的阶段变化见
  [版本演化规格](Spec_DAISY_Version_Evolution.md)。

## 一、功能域、编号与权威边界

DAISY v1.5.1 的信息能力分为两个并列功能域：

- **DBS 数据库档案信息**：获取文件树、大小、时间、File ID、元数据、哈希和
  快照变化，保存为 SQLite 快照或 Diff，并支持核验与导出。
- **STG 物理硬盘信息**：获取物理盘、分区、卷、Windows 存储属性和 smartctl
  原始证据，保存为独立 ZIP，并支持归档核验。

统一 GUI、CLI、ENV 环境检测、管理员模式与测试入口只负责调度和交互。STG 不
写入 DBS 的 SQLite，DBS 也不把物理盘资料嵌入快照；当前版本不自动建立文件条目
与物理硬盘档案之间的关联。

现行编号、六字名称、GUI、CLI 与对应任务脚本必须一一对应。以下 8 项是
v1.5.1 的完整用户功能模块集合：

| 编号 | 六字名称 | CLI | Module 脚本 |
|---|---|---|---|
| ENV-01 | 运行环境检测 | `env-check` | `Script_DAISY_Module_ENV_01_Env_Check.py` |
| DBS-11 | 完整档案扫描 | `full-scan` | `Script_DAISY_Module_DBS_11_Full_Scan.py` |
| DBS-12 | 快速档案扫描 | `quick-scan` | `Script_DAISY_Module_DBS_12_Quick_Scan.py` |
| DBS-21 | 快照变更分析 | `diff` | `Script_DAISY_Module_DBS_21_Diff.py` |
| DBS-31 | 内容哈希核验 | `check-hash` | `Script_DAISY_Module_DBS_31_Check_Hash.py` |
| DBS-32 | 文件结构核验 | `check-format` | `Script_DAISY_Module_DBS_32_Check_Format.py` |
| DBS-41 | 结果报告导出 | `export-report` | `Script_DAISY_Module_DBS_41_Export_Report.py` |
| STG-11 | 硬盘信息登记 | `storage-collect` | `Script_DAISY_Module_STG_11_Collect.py` |

`DBS-91 DAISY功能自检` 只属于「高级」维护入口，通过 `unittest` 运行正式测试，
不占用功能模块或 Module 脚本。`storage-list` 是同一 STG-11 脚本的 `--list` 内部
准备模式，也不另占编号或脚本。归档核验不再提供独立用户命令；创建 ZIP 后仍由
STG-11 在底层自动执行同等完整核验。

v1.6.0 开发分支另有不占编号的统一编排脚本
`Script_DAISY_Module_DBS_10_Scan.py`，由新命令 `scan` 调用。它不是第 9 个业务
模块，而是 Full／Quick 共用的 schema 4 新建、恢复、控制和发布入口；旧
`full-scan`／`quick-scan` 在兼容期内仍指向原任务脚本。

文档解释意图和不变量，代码保存容易漂移的精确定义：

| 内容 | 最终权威 |
|---|---|
| schema 3 快照 SQLite DDL | `Script\Lib\Script_DAISY_Lib_DBS_01_Core.py` 中的 `SNAPSHOT_DDL` |
| schema 4 DDL、session、attempt、lease、恢复与发布 | `Script\Lib\Script_DAISY_Lib_DBS_08_State.py` 及 `Spec\Spec_DAISY_V1_6_0_Data_Contract.md` |
| schema 4 partial 创建、恢复预览、lease 心跳与运行编排 | `Script\Lib\Script_DAISY_Lib_DBS_09_Run.py` |
| Diff SQLite DDL | `Script\Lib\Script_DAISY_Lib_DBS_04_Diff.py` 中的 `DIFF_DDL` |
| 规范化元数据取值链 | `Script\Lib\Script_DAISY_Lib_DBS_02_Meta.py` |
| 哈希、schema 4 隔离 worker、timeout、复用和独立抽验 | `Script\Lib\Script_DAISY_Lib_DBS_03_Hash.py` |
| 数据库类型、schema、模块能力与业务投影 | `Script\Lib\Script_DAISY_Lib_DBS_05_Reader.py` |
| 核验快照准入、stat／哈希、格式判据和报告服务 | `Script\Lib\Script_DAISY_Lib_DBS_06_Verify.py` |
| 数据库解析模块注册表、CSV 与旧 Excel writer | `Script\Lib\Script_DAISY_Lib_DBS_07_Parse.py` |
| schema 3／4 快照 Issues 只读分析与分板块 Markdown | `Script\Lib\Script_DAISY_Lib_DBS_10_Issues.py` |
| CLI 分发、现行脚本名 | `Script\Script_DAISY_MAIN.py` 中的 `COMMANDS` |
| CLI 参数及默认值 | 上表对应任务脚本及统一编排脚本的参数解析器 |
| GUI 显示值到 CLI 的映射 | `Script\Script_DAISY_GUI.py` |
| STG 物理盘只读登记与 ZIP 协议 | 本文第十一节及 `Script\Lib\Script_DAISY_Lib_STG_*.py` |

## 二、系统不变量

1. **源档案只读**：DAISY 不创建、修改、重命名或删除源目录中的任何项目。
2. **快照封存后不可变**：后续核验、Diff 和导出只读输入数据库；新分析产生新文件。
3. **无有效内容哈希时不得推断内容相同**：大小和时间相同只能证明 stat 未变，不能替代内容证据。
4. **业务运行纯本地**：所有数据库与存储业务任务均没有网络、遥测、上传或在线查询；云占位文件不会被触发下载。只有用户明确确认后，Python 引导脚本或 GUI 缺失工具安装流程才会通过 WinGet 联网。
5. **路径可迁移**：身份以 root label 和相对路径表示，不依赖盘符；当次 `root_path` 仅作定位与审计。
6. **时间可审计**：自产时间使用 UTC ISO 8601 `Z`；本地时间只用于显示和文件名。
7. **文本统一**：正式文本输出使用 UTF-8（无 BOM）和 LF。
8. **失败如实保留**：单文件失败通常记录到 `errors`，不会伪装为成功；高错误率才触发告警或熔断。
9. **物理盘只读**：STG 不修改磁盘、分区、卷、文件系统、BitLocker 或 SMART
   设置，也不启动 SMART 自检；只读查询仍可能唤醒休眠硬盘。

### 2.1 内容读取边界

- Full 哈希和哈希校验会读取文件内容，但字节只进入 SHA-256 实现。
- 元数据阶段不提取文本正文、单元格、幻灯片正文或压缩包成员内容。
- 文档只读取属性区，例如 OOXML `docProps/*`、PDF Info／XMP。
- 压缩包登记只读取目录结构和成员描述，不读取成员数据。
- **显式运行 `check-format` 是例外**：结构校验器可以为验证 CRC 或结构而读取文件及压缩包成员数据，但仍不保存正文，也不写回源文件。

## 三、支持类型与处理

| `media_kind` | 扩展名 | Full 元数据处理 |
|---|---|---|
| `photo_raw` | cr2 cr3 nef arw raf orf rw2 dng | ExifTool 照片 profile |
| `photo_jpeg` | jpg jpeg jfif | ExifTool |
| `image_gif` | gif | ExifTool；全量元数据另存成功的 ffprobe 原文 |
| `photo_working` | tif tiff psd psb png | ExifTool＋`working_metadata` |
| `video_mp4` | mp4 mov lrf | ExifTool＋ffprobe |
| `video_crm` | crm | ExifTool＋ffprobe，允许 CTMD 长尾字段进入 Raw Payload |
| `audio` | wav mp3 aac | 视频同管线；title／author／album／copyright 优先采用 ffprobe tags |
| `archive` | zip 7z rar tar gz bz2 xz | ZIP 使用 `zipfile` 目录；其他格式使用 7-Zip 列表；全量元数据另存 ExifTool 原文 |
| `document` | pdf doc docx xlsx pptx | 只登记属性，不读取正文；全量元数据另存 ExifTool 原文 |
| `other` | 其他全部 | 进入树和哈希；仅在全量元数据范围保存 ExifTool 原文 |

“支持”表示代码具有对应处理路径，不表示所有厂商、固件和损坏形态都经过真实样本验证。

元数据 profile v7：

- 照片：`-j -G1:3:4 -a -u -D -l -ee -charset filename=utf8`；
- 视频、音频、文档、压缩包和 `other`：同组参数但不含 `-ee`；
- ffprobe：`-print_format json -show_format -show_streams -show_chapters -show_programs -show_stream_groups -show_data`。
- v2 新增 ffprobe 容器级 `format.tags.location` 的 ISO 6709 规范化。
- v3 在 Raw 开启时把 ExifTool 覆盖扩展到本地所有文件，并对每个
  文件调用 ffprobe；该范围用于开发期全类型价值实测。
- v4 保留本地所有文件的 ExifTool Raw，但把 ffprobe 收敛为视频、
  音频和 GIF。音频／视频的 ffprobe 是规范化管线的必需后端，失败会记录
  元数据错误；GIF 只作 Raw 动画证据增补，失败不覆盖 ExifTool 主解析
  状态。其他照片、文档、压缩包和普通文件不调用 ffprobe。
- v5 补齐 `.jfif` 的 JPEG 分类、`.doc` 的文档分类和 GIF 的通用照片
  规范化字段，并把 ffprobe 原文收敛为视频、音频和 GIF。
- v6 把 GIF 从 `other` 提升为独立 `image_gif`，并把面向用户的选项明确
  为“基础元数据／全量元数据”。两种范围都解析有规范化落点的文件；
  仅全量元数据写入 `raw_payloads`。
- v7 修正曝光补偿、Canon 实拍色温与白平衡、照片小数秒、DNG 有效尺寸／
  位深／时区／GPS、视频 UTC、DJI 机型、文本标量和无效镜头值；AAC 纳入
  音频管线。ExifTool 的 `Warning`／`Error` 和规范化清洗原因写入
  `metadata_diagnostics`，其中 `Error` 同时进入 `errors` 并使条目失败。
- ffprobe 成功不等于读到了照片意义上的传统 EXIF。profile v5 不再因为
  静态照片可被表示为单帧视频流，就默认保存重复或带合成时长的结果。

## 四、元数据范围

完整扫描的元数据范围决定是否保存后端原始 JSON，不是元数据提取总开关：

- `--metadata-storage complete` 对应“全量元数据”，为默认值：后端 JSON canonicalize 后以
  zlib level 6 压缩，`payload_sha256` 是未压缩 canonical JSON 的 SHA-256。
- `--metadata-storage normalized` 对应“基础元数据”：仍执行生成规范化表
  所需的 ExifTool、ffprobe 或压缩包解析器，只是不写 `raw_payloads`。
  视频和音频仍调用 ffprobe；GIF 在基础范围只调用 ExifTool。
- 基础元数据范围下，`.jfif`、`.doc` 和 GIF 仍有规范化落点；真正未知且没有
  规范化表的 `other` 才标为 `not_applicable`。
- 基础元数据无法重新解释历史后端字段，也无法用原始载荷判断 `metadata_extraction_changed`。
- 元数据范围不是隐私开关；规范化列仍可能包含作者、设备、时间或位置等元数据。
- “全部字段”仅指**当前 profile 返回的 JSON 字段全部保留**，不代表外部工具未返回的字段也被采集。
- `payload_zlib` 和 `payload_sha256` 保留完整原始载荷；Diff 只有在 ExifTool
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
  Auto／Manual 只作通用辅助。实拍色温只采用 `ColorTempAsShot`，不以
  `ColorTemperature`／`ColorTempKelvin` 代替。
- DNG 尺寸优先 `SubIFD:DefaultCropSize`，再回退到 SubIFD 宽高；位深采用
  `SubIFD:BitsPerSample`。CR3 中预览图或轨道的位深不冒充 RAW 位深。
- `(0,0)` GPS、非正数光圈／焦距和全零镜头序列号转为 `NULL`，并写入
  `validation` 诊断；不据此修改 Raw Payload。
- 色彩优先采用 ICC profile；EXIF ColorSpace 只作辅助。
- Canon gamma／gamut 取 CanonLogVersion／ColorSpace2；其他厂商没有可靠来源时留空。
- 音频文本标签优先使用 ffprobe，避免 RIFF INFO 非标准编码造成误解。
- `video_metadata.stream_count` 保存 ffprobe 的总流数；`video_streams` 与
  `audio_streams` 只保存对应两类流的查询字段。timecode、CTMD、DJI telemetry
  等 data stream 明细仍完整保留在 ffprobe Raw Payload，不被误计为音视频流。
- 视频容器级 `location` 解析为 `video_gps_points`：经纬度为有范围约束的
  十进制度，海拔可空，原始字符串写入 `raw_value`。文件级静态位置没有
  点时间，故 `timestamp_seconds=NULL`；当前不提取逐帧或连续轨迹。
- 无法按 ISO 6709 解析或超出经纬度范围的 `location` 不写规范化点；默认
  开启 Raw Payload 时，ffprobe 原值仍完整保留，可供审计和后续重解释。
- 工具版本写入快照和 Raw Payload。跨工具版本的原始 JSON 差异可归为 `metadata_extraction_changed`，不等同于文件内容变化。

## 五、快照数据模型

### 5.1 快照数据库

| 分组 | 表／视图 | 用途 |
|---|---|---|
| 身份与运行 | `snapshot_info` | 版本、UUID、状态、coverage、工具版本、配置和统计 |
| 内嵌证据 | `snapshot_manifest`、`run_events` | 成功运行的清单和事件时间线 |
| 多 root | `roots` | root label、当次路径及枚举状态 |
| 树 | `dirs`、`entries` | 目录、文件、stat、媒体类型和处理状态 |
| 规范化元数据 | `photo_metadata`、`video_metadata`、`video_gps_points`、`video_streams`、`audio_streams`、`working_metadata`、`document_metadata`、`archive_metadata`、`archive_members` | 固定查询列；视频 GPS 点支持一文件多行 |
| 原始元数据 | `raw_payloads` | ExifTool／ffprobe 原始 JSON |
| 完整性 | `hashes` | SHA-256、读取字节、状态和复用溯源 |
| 元数据诊断 | `metadata_diagnostics` | 后端 warning／error 与字段清洗 validation；warning 不自动判失败 |
| 错误 | `errors` | 阶段、后端、错误码和文本 |
| 视图 | `v_file_manifest`、`v_dir_problems` | 常用清单与目录问题查询 |

Quick 与 Full 使用相同的 schema 3。Quick 不生成内容哈希、专用元数据或 Raw Payload，`video_gps_points` 因此为空，但保持统一的数据结构和明确的状态值。快照报告把视频 GPS 点导出为 `GPS_inventory_video.csv`。

### 5.2 Diff 数据库

| 表 | 用途 |
|---|---|
| `diff_info` | 两侧快照身份、版本、coverage、配对和统计 |
| `diff_subtrees` | 枚举失败影响范围 |
| `diff_dirs` | 目录维度变化 |
| `diff_hash_groups` | 内容哈希多重集和移动／复制分组 |
| `diff_entries` | 每个文件的状态、证据、原因及两侧值 |

## 六、身份、状态与路径

- `rel_path` 保存相对 root 的原始路径；`path_key` 用于比较。
- `path_key` v1：NFC → `casefold()` → 分隔符统一为 `/`。
- 唯一性是 `(root_id, rel_path)`。`path_key` 碰撞不会丢条目，而是记录错误；Diff 对碰撞组给出 `unknown`。
- `root_label` 默认取根文件夹名，也可用 `label=路径` 显式指定。
- `volume_serial`／`file_index_hex` 来自 `os.stat()` 的设备和 inode 信息；不可可靠取得时为 NULL。
- File ID 是移动／重命名的辅助证据，不是内容证据。
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
SQLite。`_Issues.md` 是人读视图：单纯 `exiftool_reported_error=Unknown file type`
的记录不列为问题，也不会在没有其他问题时单独生成报告；原状态、诊断和错误行仍
完整保留在 SQLite，数据库状态与扫描结构不变。

元数据汇总状态优先级：

1. 解析前后 size／mtime 改变：`unstable`；
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
90 秒，超过后进入下一个 90 秒阶梯。策略写入 `config_json`／manifest，
超时错误同时记录实际秒数和文件体积；ffprobe 维持固定 60 秒。

哈希 `valid` 的充要条件是：摘要非空、`bytes_read == size_bytes`，并且读取前后 size／mtime 一致。

## 七、完整扫描／快速扫描与封存

### 7.1 默认能力

完整扫描（Full）默认：

- `hash=full`；
- 元数据范围为全量元数据（`complete`）；
- 采集 File ID；
- 独立抽验 1%，至少 100 个本次 computed 条目。

快速扫描（Quick）：

- 不读取内容；
- 不运行外部工具；
- 不计算内容哈希；
- 不提取元数据或 Raw Payload；
- 默认采集 File ID。

### 7.2 扫描稳定性

Full 不是文件系统原子快照，而是用多个时间点的观测尽量识别扫描期间的
源文件变化：

1. 枚举时登记每个已发现文件的 size、mtime、File ID 和观测时间；
2. 主 SHA-256 在读前、读后分别 stat，并核对枚举 size、实际读取字节数、
   读前后 size 和 mtime；不一致即 `unstable`；
3. 每个文件完成元数据解析后，再把当前 size／mtime 与枚举值比较；
4. 元数据阶段结束后，对枚举时已登记的本地所有文件再做一次
   size／mtime 复扫；变化或消失会同时把哈希与元数据状态标为
   `unstable`；
5. 主哈希完成后，从本次由 Python 实际计算且状态有效的条目中按比例抽样，
   由 PowerShell `Get-FileHash` 独立重算；默认 1%，至少 100 个，候选不足
   时全验。它不是主 SHA-256 的覆盖比例；
6. 哈希读取没有固定超时，30 秒无进度只记录 stall，恢复读取后继续；
   哈希错误率超过 20% 时告警，超过 50% 时中止并保留 partial。

当前边界必须明确：

- 末次复扫只检查枚举时已经登记的路径，不会重新枚举目录。因此扫描开始后
  新增的文件不会进入本次快照；已登记文件随后消失则能够检出；
- 基于 stat 的检查不能可靠发现“内容改变后又恢复原 size 和 mtime”的情况。
  Full 哈希能证明读取时的内容，但不能把整个源目录冻结在同一时刻；
- DAISY 当前不创建 VSS 或其他文件系统快照，不应把一次长时间扫描解释为
  原子时间点映像；
- DAISY 不提供按 mtime 静默跳过近期文件的“静置窗口”。建立权威基线前
  应先停止对源目录写入；扫描中发生的已登记文件变化会明确记为
  `unstable`，而不是用缺失哈希换取表面上的 Full。

### 7.3 运行态与封存

Full 运行态包含：

```text
<short-name>.<microseconds>_<runid8>.partial.sqlite
<short-name>.<microseconds>_<runid8>.partial.sqlite-wal
<short-name>.<microseconds>_<runid8>.partial.sqlite-shm
<short-name>.<microseconds>_<runid8>.partial.sqlite.lock
<short-name>.<microseconds>_<runid8>.events.jsonl
```

DBS-11 是当前唯一提供正式续传入口的任务。续传只接受 partial，并沿用数据库内
记录的 roots、哈希模式、元数据范围、File ID 策略、抽验比例及 `snapshot_stem`。
增量来源与 root 映射在 CLI 显式传值时仍可覆盖库内记录；外部工具路径会在续传时
重新解析。微秒和随机运行 ID 只属于运行态，不进入最终封存名。新任务对 partial 和
最终目标都采用 no-clobber 语义。

成功封存顺序：

1. 检查无 pending／processing 残留并写最终统计；
2. 写 `scan_status=complete` 和真实 `hash_coverage`；
3. 把 manifest 与运行事件写入 `snapshot_manifest`／`run_events`；
4. 执行 SQLite `integrity_check`；
5. checkpoint WAL，切回 DELETE journal 并关闭连接；
6. 对稳定 SQLite 字节计算完整 SHA-256；
7. 取摘要前 8 个十六进制字符并大写；
8. 有需要人工关注的问题时，以目标存在即失败的方式创建同目录 `_Issues.md`；
9. 以目标存在即失败的原子重命名发布 SQLite；
10. 删除已内嵌的运行态 JSONL。

失败或中断时保留 partial 和事件，供诊断或续传。

### 7.4 续传状态机与一致性边界

当前 DBS-11 续传流程如下：

1. 要求文件名以 `.partial.sqlite` 结尾，取得同名 ScanLock；本机 owner PID 仍存活
   时拒绝，owner 已失效时允许接管；
2. 只接受 `scan_status=running`，并同时核对生成器版本、schema、元数据 profile、
   `video_gps_points` 表和文件名布局版本；
3. 验证 partial 中保存的每个 root 仍然可访问，重新执行工具预检；
4. 从头重新枚举全部 root，并与既有登记对账。删除已消失条目，加入新增条目；只有
   size 或 mtime 变化时，才把既有条目的哈希和元数据状态重置为 `pending`；
5. 哈希阶段保留未变化条目的已完成结果，把遗留 `processing` 重置为 `pending`，
   然后只处理 `pending`；`error`、`unstable` 与 `skipped` 不自动重试；
6. 元数据阶段只处理 `pending`。当前实现不写 `meta_status=processing`；未提交事务由
   SQLite 回滚。未变化条目的 `error`、`timeout`、`unstable` 与 `skipped` 不自动
   重试；
7. 重新执行末次 size／mtime 复扫、独立哈希抽验和封存；事件 JSONL 以追加方式记录
   每次 `run_started`、中断、失败和重复进入的阶段，成功后整体写入 `run_events`。

续传完成与从头一次完成只承诺**业务语义可收敛**，不承诺数据库逐字段或逐字节相同。
即使源目录始终不变，也会存在以下预期差异：

- 续传保留原 `snapshot_uuid`、开始时间、配置和命名 stem；从头扫描创建新身份；
- 续传重新枚举会覆盖目录与条目的 `observed_at_utc`；
- 中断前已完成的哈希、元数据、工具版本和处理时间继续保留，待处理条目在恢复后完成；
- `run_events` 与 `snapshot_manifest` 明确包含中断及 `resumed=true` 的多段时间线；
- row ID、SQLite 页布局和最终数据库文件指纹不作为两种执行方式的等价判据。

只有在源目录、工具、环境和配置均未变化且没有瞬时错误时，忽略身份、时间、事件、
row ID 与物理布局后，文件清单、SHA-256、规范化元数据、统计和完成状态才应语义等价。
暂停期间发生的新增、删除或 size／mtime 变化会按恢复时重新枚举的结果进入快照；内容
改变但 size 与 mtime 均保持不变时，既有完成结果不会必然失效。未变化的瞬时
`error`／`timeout` 也不会因续传自动重试，因此它们可能与稍后从头扫描的结果不同。

哈希读取默认以 4 MiB 分块同步执行。连续 30 秒没有完成数据块时，StallWatchdog
只写 `stall` 事件，不取消读取、不跳过文件，也没有单文件固定超时。底层读取抛出异常
时才把条目标为 `error` 并继续；若系统 I/O 一直不返回，当前任务可能长期停在该文件。
哈希错误率超过 20% 的告警和超过 50% 的整次中止不等于单文件超时机制。

上述限制保持 v1.4.1 扫描语义，不在 v1.5.1 修改。计划中的状态机、锁、失败重试、
工具溯源、变更失效、输出目录恢复、哈希超时和等价性测试见
[v1.6.0 可靠性、兼容与报告重构待办](Spec_DAISY_V1_6_0_Backlog.md)。

### 7.5 文件名

```text
根标签_类型_[偏差标记_]日期_时间_XXXXXXXX.sqlite
```

- `XXXXXXXX` 是最终数据库完整 SHA-256 的最高 32 bit，不是完整摘要。
- Full 无标记基线＝full hash＋全量元数据＋File ID。
- 偏差标记固定顺序：`No-Hash`、`Hash-Inc`、`Basic-Metadata`、`No-FID`。
- Quick 已蕴含无哈希和无元数据原文，只在关闭 File ID 时增加 `No-FID`。
- 多 root 合并时用 `+` 连接安全化后的 label。
- 文件名使用本地时间；库内 UTC 时间和 UUID 才是权威身份。
- 元数据 error／timeout、哈希 error、unstable 或枚举缺口写入对应状态和
  明细表，但不进入数据库文件名；存在需关注项时在数据库同目录生成
  `<数据库基名>_Issues.md`。仅格式未识别的 ExifTool error 保留库内证据，
  不进入该人读报告。
- warning／validation 会保留在库内；它们本身不让一次直接扫描生成问题报告。
- `filename_layout_version=2` 表示 v1.4.1 秒级短名称；manifest 的
  `snapshot_stem`／`snapshot_filename_pattern` 必须与当前封存名一致。

完整数据库 SHA-256 不持久化。32-bit 指纹适合快速发现常见损坏，但存在碰撞，不能替代外部完整摘要或签名。

## 八、哈希与增量复用

| 模式 | 行为 | 文件名标记 |
|---|---|---|
| `full` | 所有可读普通文件重新计算 SHA-256 | 无 |
| `incremental` | 满足条件时复用旧哈希，否则重算 | `Hash-Inc` |
| `none` | 不计算内容哈希 | `No-Hash` |

增量复用必须同时满足：

1. root 配对后 `path_key` 唯一且相同；
2. size 和 mtime 精确相同；
3. 两侧都不是占位文件；
4. 上一快照存在 `valid` SHA-256；
5. 两侧都有 File ID 时必须相同。

增量来源还必须是 schema 3 的 v1.4.1 完整封存件，并通过文件名指纹、SQLite
完整性、状态与明细一致性检查。扫描未完成、目录枚举缺口、哈希失败或
unstable 条目均硬性拒绝；`has_file_issues=1` 本身不阻止其他有效哈希复用。
复用记录保存最初计算事件，而不是只指向最后一个中间快照。

Full 的独立抽验和 `check-hash` 使用 PowerShell `Get-FileHash`，与主哈希实现分离。schema 4
扫描的抽验按文件启动本任务持有的精确 PowerShell 进程，路径以 UTF-8 令牌放入
UTF-16LE `-EncodedCommand`，不依赖尾随 `$args` 或字符串引号拼接。控制层沿用 30 秒
stall、90 秒／9 GiB 动态无进展阈值和三种处置；暂停、停止或 timeout 只终止并等待当前
句柄。读取前后 size／mtime 必须稳定。第一次摘要不一致时，主实现和独立实现各重算一次；
双方恢复为原摘要才算偶发抽验异常，否则写入 `verify_hash` attempt，并把当前哈希标为
unstable 留证。

正式兼容范围包括 Windows PowerShell 5.1（`powershell.exe`）和
PowerShell 7.x（`pwsh.exe`）。两个系列使用相同的 `Get-FileHash` 调用路径，
均须通过启动、版本读取和命令可用性验证后才会被采用。

PowerShell 按「手动路径 → `PATH` → Windows 常规安装位置」发现。自动发现会
逐个验证候选是否可启动、能否报告版本以及是否提供 `Get-FileHash`；单个坏候选
不会阻断后续候选。便携版或自定义安装位置通过 `--powershell-path` 指定。
`env-check` 还会在系统临时目录对固定样本实际执行一次 `Get-FileHash`，不读取
档案内容。

已知边界：攻击者可以刻意保持 size／mtime；因此增量快照不能永久替代定期 full hash。

## 九、快照准入与核验

以下情况硬拒绝，`--force` 也不能越过：

- `database_integrity` 不是 `ok` 或实际 SQLite／外键检查失败；
- `scan_status` 不是 `complete`；
- 文件名已有高 32 bit 指纹，但与当前字节复算不符；
- `schema_version` 不是 3，或 `path_key_rule` 不兼容。

唯一可降级项是 Diff／核验输入的**文件名缺少指纹**。`--force` 允许继续，
但结果会记录该降级并生成问题报告；已有但不匹配的指纹不能越过。增量来源
不允许缺少指纹。当前版本不读取旧 sidecar、散置 `.sha256` 或旧 `SHA8-` 命名。

v1.4.1 只读取当前 schema 3，不读取旧结构，不提供数据库迁移路径。Full 只
续传本版本、schema 3、profile 7 的 partial；v1.4.0 及更早数据库要获得当前
规范化结果，必须重新扫描原档案。

`22 check-hash` 和 `23 check-format` 都必须用 `--root` 指定当前档案根目录，
不回退到快照保存的旧绝对路径。单根快照可直接传一个文件夹路径；多根快照
必须为每个 root 使用 `label=当前路径`。普通文件不能作为 root。

`22 check-hash`：

- 总是先检查记录条目的存在性、size 和 mtime；
- 默认抽样 1%，至少 100 个有有效基准哈希的条目；
- `--full` 对所有有有效基准哈希的条目独立复算；
- 结论只覆盖本次实际检查口径。

`23 check-format`：

- 默认检查全部可校验文件，GUI 可选择按比例抽样；
- ZIP／OOXML 可读取成员并校验 CRC；
- PDF 使用头、尾和 `startxref` 结构检查；
- 媒体使用 ExifTool validate，视频／音频／GIF 叠加 ffprobe；
- 其他格式返回 unsupported，由哈希层而不是结构层提供变化保护。

报告只列出本次实际出现的状态计数；`unsupported=0` 等零值不作为占位项
显示。`unsupported` 状态本身仍用于区分“校验器无法判断”和
`valid`／`invalid`，不能因某一个测试库为 0 而删除。

格式校验不是逐帧解码。媒体“容器结构正常但码流内部损坏”可能漏检；外部工具版本变化也可能改变警告口径。

## 十、Diff 语义

### 10.1 root 与路径配对

- 优先按相同 root label 配对；
- `--map-root old=new` 可显式映射；
- 两侧各只有一个 root 且 label 不同时自动配对，并记录 `auto_paired`；
- 多 root 没有唯一解时不猜测，未配对 root 整体列为新增／删除。

若任一侧某子树枚举失败，该范围不能可靠判定 added／deleted，统一传播为 `unknown`。

### 10.2 文件状态

`diff_entries.status` 的 11 个值：

`unchanged`、`stat_changed_content_same`、`metadata_extraction_changed`、`content_changed`、`added`、`deleted`、`moved_or_renamed`、`copied`、`hash_missing`、`unstable`、`unknown`。

核心优先级：

1. 枚举失败或 path_key 碰撞：`unknown`；
2. 任一侧 unstable：`unstable`；
3. size 不同：`content_changed`，证据为 `stat_only`；
4. 双侧有效哈希不同：`content_changed`；
5. 双侧有效哈希相同：再细分 stat 变化、Raw Payload 稳定比较摘要变化或 unchanged；
6. 无双侧有效哈希：`hash_missing`，不以大小和时间推断相同。

证据等级：

- `independent_computation`：两侧哈希来自不同计算事件；
- `propagated_single_computation`：两侧最终追溯到同一计算事件；
- `heuristic_file_id`：仅用 File ID 辅助移动判断；
- `stat_only`：大小不同已经足以证明内容不同；
- `insufficient`：证据不足。

移动、复制和硬链接使用全快照 SHA-256 多重集进行分组；无哈希时才可能退回 File ID 启发式。

## 十一、STG 物理硬盘信息登记

### 11.1 定位、版本与权威实现

STG 用于 Windows 单机上的只读物理硬盘信息登记与证据归档。GUI 只有一个硬盘
功能模块：`STG-11 硬盘信息登记`。同一页的「检测物理硬盘」按钮调用该模块脚本的
内部列盘模式并刷新硬盘池，不另占功能编号。统一 CLI 的 `storage-list` 和
`storage-collect` 均由同一个 STG-11 Module 脚本分派；`storage-list` 只是登记前的
准备模式。归档类型标识为 `PROFILE`，源码统一使用 `DAISY_Lib_STG` 和
`DAISY_Module_STG` 命名空间。

STG 的 `archive_schema_version=3` 只表示 ZIP 协议，与快照／Diff 的 SQLite
`schema_version=3` 没有数据模型关系。STG 不导入 `sqlite3`，不创建、读取或修改
数据库。默认产物目录为 `Output/Storage`。当前只读取 STG 归档 schema 3，不兼容
早期协议；Manifest 中的应用版本为 `1.5.1`。

代码权威边界：

| 范围 | 文件 |
|---|---|
| 数据模型、命名、编码与摘要 | `Script/Lib/Script_DAISY_Lib_STG_01_Core.py` |
| Windows 存储清单 | `Script/Lib/Script_DAISY_Lib_STG_02_Windows.py` |
| smartctl 命令与解析 | `Script/Lib/Script_DAISY_Lib_STG_03_Smartctl.py` |
| 扫描关联、身份确认与报告 | `Script/Lib/Script_DAISY_Lib_STG_04_Service.py` |
| ZIP 生成、发布与核验 | `Script/Lib/Script_DAISY_Lib_STG_05_Archive.py` |
| `STG-11` 列盘与登记入口 | `Script/Module/Script_DAISY_Module_STG_11_Collect.py` |
| 统一 GUI／CLI 接入 | `Script/Script_DAISY_GUI.py`、`Script/Script_DAISY_MAIN.py` |

smartctl 由 `ENV-01` 发现、验证与缓存。缺失时，只能在用户逐项确认后通过固定
`smartmontools.smartmontools` WinGet 包安装；PowerShell 不由 GUI 安装。

### 11.2 STG 系统不变量

1. **物理盘只读**：不执行修改磁盘、分区、卷、文件系统、BitLocker 或 SMART
   设置的命令，也不启动 SMART 自检。
2. **物理盘优先**：采集目标以 Windows `DiskNumber` 和 smartctl 扫描项共同
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
11. **权限显式**：STG-11 的列盘与登记模式需要管理员权限才能完整运行。GUI 在
    模块说明、悬停说明、任务设置页和启动确认中显示要求，并提供顶部管理员模式
    开关，通过 Windows UAC 重启；开关悬停说明明确当前仅「硬盘信息登记」及其检测
    步骤需要此模式。
12. **发布后自检**：最终 ZIP 发布后必须自动执行完整核验；不提供可被误认为独立
    业务功能的手动核验模块。

### 11.3 管理员权限与只读命令边界

STG-11 的列盘和登记模式应在管理员模式下运行，以取得完整的 Windows 存储与
smartctl 资料。GUI 顶部管理员模式开关会先确认，再通过 Windows UAC 重启当前
应用；任务运行期间不可切换权限。未提权运行不放宽只读边界，只会如实记录权限
缺口、失败或 `incomplete` 诊断结果。

smartctl 扫描固定为：

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

Windows 清单和 smartctl 扫描独立执行，任一失败时仍保留另一侧实际结果。关联
规则依次识别：

1. `PhysicalDriveN`；
2. `/dev/pdN`；
3. Windows smartmontools 的 `/dev/sdX` 编号规则。

Windows 盘存在而 smartctl 未发现时，STG-11 仍列出 Windows 目标并说明关联缺口，
但禁止为该项建立完整归档。smartctl 项无法关联 Windows `DiskNumber` 时也列出，
但不能当作完整目标。同一物理盘出现多个 smartctl 项时保留提示，并使用扫描顺序
中的第一项。

GUI 硬盘池列出当次扫描到的全部有效 DiskNumber。脱机、Windows 资料缺失或
smartctl 未关联的设备保留在池中并显示原因，但复选框禁用。用户可逐项勾选，也可
点击「选择所有联机硬盘」选择所有联机且资料完整的设备。每块已选硬盘拆成独立
`队列 i/n` 子进程和独立 ZIP；即使只选一块也显示 `队列 1/1`。每次点击「检测物理
硬盘」都会先清除上一轮清单与选择。选择框使用 20 px 自绘指示器；检测结果显示
「若接入硬盘发生变化，请重新进行检测。」接入状态改变后不得沿用旧 DiskNumber。

登记开始后，STG-11 按 `DiskNumber` 重新取得详细 Windows 清单，并核对容量、
`UniqueId` 和序列号，再以固定只读模板采集单盘证据。

### 11.5 Windows 数据模型

#### 11.5.1 `disk`

保存 `Get-Disk` 的编号、路径、位置、FriendlyName、型号、序列号、固件、
UniqueId、运行和健康状态、总线、分区样式、离线／只读／系统／启动盘状态、
逻辑和物理扇区、总容量、已分配容量及最大空闲范围。

#### 11.5.2 `partitions`

每个分区保存编号、盘符、全部 AccessPath、偏移、长度、结束偏移、类型、GPT／
MBR 类型、GUID、只读／离线／活动／启动／系统／隐藏／影子副本状态以及运行
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
GPT／MBR 元数据，不能直接称为可分配未分配空间。正式 JSON 同时保留
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

GUI 在「简化文本」下拉项选择「生成 ZIP 外部 TXT」，或 CLI 使用
`--summary-txt` 时，在 ZIP 同目录生成 `<完整 ZIP 基名>_Report.txt`。该文件不属于
归档，记录人类可读的硬盘身份、SMART 总体结论、关键 SMART 属性、分区、空间、
可靠性和警告；不记录温度、关联 ZIP 文件名或 SHA-256，默认不生成。缺失值显示为
「未提供」，布尔值显示为「是／否」；HDD 的 Windows 磨损值明确注明不一定适用。
关键风险计数 RAW 非零时显示「注意」，但只有 smartctl 的 `when_failed` 非空时才
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
- 无 JSON、目标身份变化或完整关联缺失时拒绝建立完整归档。

### 11.10 测试边界

默认 `unittest` 使用合成设备、内存数据和系统临时目录，不读取真实硬盘。测试
入口为 `Script_DAISY_Test_Storage_Unit.py` 和
`Script_DAISY_Test_Storage_Read_Only.py`，覆盖：

- 盘号映射；
- Windows 卷空间与布局间隙；
- 热插拔身份保护；
- smartctl 固定只读模板；
- GUI／CLI 共用目标关联；
- ZIP 平铺内容、UTF-8／LF、CRC、文件名指纹、篡改和 no-clobber；
- PowerShell 禁止命令和 `shell=True` 审计。

实盘验证是显式的额外步骤，必须先重新列盘并按物理盘编号选择。

### 11.11 STG 已知限制

- RAID 控制器、厂商驱动、USB 桥和虚拟磁盘可能隐藏或改写 SMART；
- Windows `Healthy` 与 smartctl 结论来自不同层，不能互相替代；
- Storage Reliability Counter 不保证所有设备都实现；
- 卷空间是采集瞬间值，可能在 ZIP 写入前发生变化；
- 单个物理盘包含多个卷标时，文件名只承担人类提示，不是权威身份；
- 32 bit 文件名指纹存在碰撞，不能替代完整摘要、数字签名或外部校验清单。

## 十二、版本、界面适配与已知限制

### 12.1 版本与兼容性

- 当前应用版本为 `1.5.1`。DBS 新数据库产物继续使用 `schema_version=3`、
  元数据 profile 7 和 `min_reader_version=1.4.1`，当前实现只读取 schema 3。
- v1.5.0 新增 STG 功能域，并统一 Module 与 Lib 脚本的 ENV／DBS／STG 前缀；
  v1.5.1 只优化 UI、交互、人读报告和对应测试。v1.5.1 对 DBS Core 的功能改动
  仅限应用版本与 `Issues.md` 呈现边界；数据库 DDL、字段、约束、schema、扫描、
  Diff、数据库生成和业务语义均未改变。DBS 库文件名与导入路径的统一属于 v1.5.0。
- `.partial.sqlite` 续传必须同时匹配 `SCANNER_VERSION`、`schema_version`、
  元数据 profile 和 GPS 表；因此 v1.5.0 及更早版本的未完成 partial 不能由
  v1.5.1 续传。
  已完成的 schema 3 封存快照仍可按现有准入规则只读使用。
- 项目长期兼容门槛：v1.6.0 及后续版本中，所有接受封存 DBS 快照或 Diff 的功能至少
  必须只读支持 v1.4.1／schema 3。旧库不得原地迁移；缺少新字段时使用明确的能力降级，
  不伪造为 0、空值或成功。该门槛不表示 v1.4.1 程序能够读取未来新 schema，也不把
  v1.4.1 未完成 partial 纳入无条件续传承诺。详细矩阵见
  [v1.6.0 待办](Spec_DAISY_V1_6_0_Backlog.md)。
- v1.6.0 开发分支已增加统一只读 Reader。它按身份表、schema、封存状态和实际表列识别
  快照／Diff／partial，并将模块状态区分为 `available`、`empty`、`unavailable`、
  `incompatible` 和 `invalid`。DBS-21／31／32／41、增量来源和 Issues 读取均通过该层；
  schema 3 的 DDL、数据契约和发布版本身份在阶段 1 未改变。对于 schema 3，Reader 还
  读取 `hash_coverage`、config 和 manifest 的执行证据：模块执行后 0 行才是 `empty/0`，
  Quick／No-Hash 明确未执行的模块是 `unavailable/NULL`，不得伪装为无问题或无记录。
  能力的语义状态与物理投影是否可查询分别记录；旧固定导出可读取结构完整的 schema 3
  空表，但新模块选择界面不得据此把未执行模块列为可选。
- v1.6.0 阶段 3 已实现独立 schema 4 状态层：新表保持 schema 3 业务表超集，新增 session、
  attempt、格式当前结果、低频性能摘要、CAS 状态转换、精确 lease、截断事件恢复和发布
  副本。统一 Reader 只把 `run_state=published` 的完整 schema 4 当作普通封存输入，并用
  流式业务投影比较一次完成与多 session 恢复；运行身份、attempt 和观察时间不进入业务
  投影。schema 4 生产链已经通过新 `scan` CLI 从证据采集运行到发布；旧
  `full-scan`／`quick-scan` 和现行 GUI 仍使用冻结 schema 3。不得把新 CLI 通过误写成
  GUI 或旧兼容命令也已切换。
- v1.6.0 阶段 4 的第一检查点已在哈希库中实现 spawn 工作进程、启动握手、30 秒 stall、
  90 秒／9 GiB 动态无进展 timeout、三种原子处置、精确句柄回收、逐文件 checkpoint、
  attempt 与低频性能摘要。schema 4 哈希当前结果与历史 attempt 在同一 SQLite 事务提交；
  暂停或停止中的当前文件不保存 `hashlib` 内部状态，恢复时从文件起点重做。该内核已接入
  `DBS_09_Run.py` 的 schema 4 生产链，并由 `scan` CLI 调用；现行 GUI 与旧兼容命令
  尚未接入，因此 schema 3 扫描语义及 Core 版本常量仍未改变。
- `DBS_09_Run.py` 进一步封装 schema 4 partial 的 no-clobber 预留、只读恢复预览、
  `<partial>.lease` 明确接管和数据库／lease 双端心跳。partial、publish stem 与 event log
  必须互不相同；同会话暂停后进程消失时，旧 session 先转为 abandoned，再创建 resume
  session。恢复与心跳只以 `mode=rw` 打开现有数据库，损坏 lease 仅能由明确恢复接管，
  且接管后重新核对 session／状态／配置。该层只操作调用方给出的精确路径与 PID，不枚举
  或终止其它进程。
- `DBS_09_Run.py` 的阶段 4 控制子层使用 `daisy-control-v1` 单行 UTF-8 JSONL，把 GUI
  的暂停、继续、保存退出、停止和 timeout 决定路由到当前运行段。消息有 4096 bytes
  上限和严格递增序号；生命周期动作为 first-wins，timeout 决定绑定当前 worker PID。
  同会话暂停后的继续会创建新控制对象并从当前文件起点重试；稍后保存退出通过受审计的
  `paused_saved_for_exit` 动作结束 session。该控制子层已通过合成 worker 测试，但尚未
  接入 `scan --control-stdin` 生产入口；GUI 尚未把自己的按钮接到该通道。
- 同一运行层已为枚举、元数据和复扫提供显式受控包装：枚举暂停后重跑临时树对账，元数据
  在单文件提交后停下并从数据库状态恢复全局进度，复扫保存已观察变化后可重跑。数值进度
  以 500 ms、当前文件以 100 ms 限频；当前文件开关关闭时不调用生产回调。Core／Meta 的
  schema 3 旧函数只增加默认关闭的末尾参数，未传回调的旧扫描路径不改变。
- `DBS_09_Run.py` 的 schema 4 内部生产链依次执行枚举、哈希、元数据、可选格式、复扫、
  独立哈希抽验、读取性能分析、封存和发布。扫描专用 `verify_format` 明确记为 skipped，
  不把未执行写成完成。只有前置 checkpoint 全部为 completed／skipped 且不存在 running
  attempt 或 pending／processing 当前结果时才进入 sealing。manifest、计数和事件先内嵌，
  SQLite 与外键检查通过后 partial 才进入 `sealed_unpublished`；发布副本最终把 publish
  checkpoint 和 session 写为 completed／published。任一目标冲突都保留 sealed partial
  和精确 lease，不重扫源档案。
- 读取性能分析只消费当前成功、`origin=computed` 的主哈希 attempt；`origin=reused`、
  独立抽验和历史 attempt 不参与比较。吞吐比较组必须同卷、同扩展名（无扩展名时同
  `media_kind`），并按 `round(log2(size_bytes))` 归入最大约 2 倍跨度的相近大小带；组内
  至少 8 个且文件至少 1 MiB。算法用吞吐中位数和 MAD；MAD 为 0 时分别以中位数的 50%
  和 25% 作为低／高置信度界线。30 秒 stall 至少为低置信度，达到该文件动态 timeout
  阈值为高置信度。低置信度只留 `read_performance`，高置信度进入同名 Issues；措辞只称
  可疑逻辑路径／时段，明确不能推断物理坏区。
- `DBS_10_Issues.py` 通过统一 Reader 只读分析 schema 3／4 快照，固定输出枚举、哈希、
  Exif／元数据、格式、读取性能候选和运行证据六个板块。已执行且无问题为 `0`，未执行、
  旧库未记录或能力不可解释为 `NULL`；unsupported／unknown／unrecognized format 只显示
  去重总数，不显示路径也不单独触发报告。普通 warning、`[minor]` warning、validation
  和低置信度性能样本折叠；明确损坏类 warning 或单文件至少 100 条折叠 warning 才进入
  待复核候选。`CopyN` 在展示和家族计数中归一化为 `Copy#`，原始 SQLite 不改写。
- schema 4 发布层可接收只读 Issues builder：先在 `mode=ro` 发布副本上分析并复核摘要
  未变化，再以 UTF-8 无 BOM、LF、no-clobber 创建 sidecar，最后发布 SQLite。报告或
  SQLite 任一目标冲突均不覆盖；SQLite 发布失败时只删除本次新建的 sidecar，保留 sealed
  partial 供恢复。该能力已接入 schema 4 生产链和新 `scan` CLI；旧兼容命令与 GUI
  尚未切换，因而仍不能据此宣称 v1.6.0 用户流程已完成切换。

- `Script_DAISY_Module_DBS_10_Scan.py` 是 schema 4 的首个生产编排入口。新建时冻结
  Full／Quick、格式校验、90 秒／9 GiB 无进展策略和工具身份；恢复前先做只读预览，
  有效 owner 会在源目录或工具预检前被拒绝，stopped 必须显式 `--manual-resume`。
  `--control-stdin` 只读取 `daisy-control-v1` JSONL，不关闭调用方 stdin；本任务的
  lease 心跳在封存前停止并确认线程已退出，避免后台写入改变最终字节；事件日志创建或
  写入失败会阻断扫描／封存。Quick 不调用外部工具，Full 默认关闭格式校验；发布成功、
  保存退出、手动停止分别返回 0、75、130。旧命令仍按 v1.5.1/schema 3 执行，直到兼容
  包装和 GUI 在后续检查点共同切换。
- `sealed_unpublished` 不得通过普通 `resume_run` 降级回扫描阶段。`scan --resume` 会改用
  只发布恢复：在读取源目录或运行工具预检前识别 sealed 状态，创建单独 resume session，
  停止精确心跳后直接从 sealed partial 建立发布副本。失败 session 保留并可再次重试；
  成功库的 manifest 会同步 session 数和发布重试次数，并明确
  `source_rescanned=false`。因此目标冲突后的恢复不依赖源文件仍在线，也不会改写扫描业务
  证据。

### 12.2 信息架构与字段命名

- 当前顶栏固定为 `文件｜面板｜高级｜设置｜视图｜帮助`，菜单栏使用浅米黄色底色。
  「面板」包含「环境」「数据」和「硬盘」三个子菜单；色带下方三行标题分别为
  「环境 ENV」「数据 DBS」「硬盘 STG」。「高级」包含「工具路径」「哈希比例」、
  动态「显示／隐藏命令预览」命令和「DAISY功能自检」。DBS-11 独立哈希抽验与
  DBS-31 内容哈希抽样只改变参数编辑位置，不改变字段、默认值或 CLI 参数。
- 所有功能模块按钮与设置页标题共用同一套六字名称。任务设置中不使用页内
  「展开后勾选」控件：故障恢复为「不启用／启用」下拉框，根标签映射直接显示为
  文本输入；既有 `--force`、`--map-root` 默认值和执行语义不变。只读下拉框未展开
  时拦截鼠标滚轮，滚轮只滚动页面，不改变选项。

常规设置页的字段标题如下；括号表示条件满足时才出现：

| 页面 | 字段标题 |
|---|---|
| ENV-01 | 环境报告目录 |
| DBS-11 | 启动方式、档案根目录、生成方式、（续传快照）、快照目录、元数据范围、NTFS标识、哈希模式、（上一封存快照）、（根标签映射） |
| DBS-12 | 档案根目录、生成方式、快照目录、NTFS标识 |
| DBS-21 | 基准快照、对比快照、差异目录、根标签映射、指纹降级 |
| DBS-31 | 封存快照、档案根目录、校验范围、指纹降级、报告位置 |
| DBS-32 | 封存快照、档案根目录、校验范围、（抽样比例）、报告目录、指纹降级 |
| DBS-41 | 输入类型、输入数据库、报告目录 |
| STG-11 | 物理硬盘池、存储档案目录、简化文本 |

顶部高级设置使用「Exif工具」「视频工具」「压缩工具」「系统工具」「硬盘工具」以及
「哈希抽验」「哈希抽样」等短标题；实际可执行文件名、用途、默认值和风险继续由菜单
状态、选项文本与悬停说明完整表达。所有字段和分区标题最多 6 个字符，标签共用固定
右边界；「添加目录」和「浏览」等操作统一位于字段右侧。界面不显示必填星号，
`required` 属性及运行前校验仍然生效。

### 12.3 面板与运行状态

- 功能模块、任务设置、运行进度和运行日志可独立折叠，运行进度与运行日志默认
  折叠。任务设置、进度、日志和命令区采用固定 grid 顺序，不提供拖动调整；点击
  开始任务后自动收起设置、展开进度和日志，日志获得 1080P 剩余纵向空间。命令预览
  默认关闭。队列总进度、当前任务阶段和本阶段工作量三条进度语义独立；顶部显示
  当前完整路径。日志可打开为单例独立窗口，与主窗口实时追加和清空同步。
- 小窗视图在空闲和运行时始终可进入；「视图」菜单提供动态「进入小窗模式／返回
  完整界面」，小窗保留当前目标、三条进度和运行控制，并在返回时恢复面板顺序与
  固定布局。
  开始完整档案扫描前的确认框按分别／合并模式列出全部完整根路径，提示任务可能
  持续几小时到几天，再由用户确认是否执行。

面板状态转换必须遵循下表：

| 事件 | 任务设置 | 运行进度 | 运行日志 |
|---|---|---|---|
| 空闲进入页面 | 展开 | 收起 | 收起 |
| 点击开始任务 | 收起 | 展开 | 展开并占用剩余高度 |
| 开始检测物理硬盘 | 收起 | 展开 | 展开 |
| 硬盘检测成功 | 展开并刷新硬盘池 | 收起 | 收起 |
| 硬盘检测失败 | 收起 | 展开并保留诊断 | 展开并保留诊断 |

v1.5.1 的进度数据与 UI 刷新是两层机制：子进程的 `Progress.update()` 最多每秒发送
一次 `progress_update`，阶段开始／完成立即发送；GUI 每 80 ms 清空一次事件队列。
不确定进度条以 12 ms 步进播放动画，但动画不表示扫描数据按该频率更新。各阶段实际
回调粒度如下：

| 阶段 | 当前回调粒度 | v1.5.1 显示边界 |
|---|---|---|
| 枚举 | 每 500 个文件 | 慢目录或不足 500 项时可能长时间没有数值更新 |
| 哈希 | 每完成 1 个文件 | 单个大文件的 4 MiB 块只驱动 stall 心跳，不推进进度条 |
| 元数据 | 每 10 个文件 | 单文件解析很慢时中间没有更新 |
| 复扫 | 无中间回调 | 只有阶段开始和完成 |
| 独立抽验 | 底层每批 200 项可回调，但 DBS-11 未接入进度条 | 阶段内保持不确定显示 |
| 预检／封存 | 无中间回调 | 只有阶段开始和完成 |

因此“最多 1 Hz”不是“保证每秒刷新”。v1.6.0 计划增加当前文件开关、按块限频字节
进度、全阶段统一遥测与事件合并，见
[v1.6.0 待办](Spec_DAISY_V1_6_0_Backlog.md)。

### 12.4 窗口、DPI 与滚动

- 普通窗口以 `1920×1080` 为目标尺寸。Windows 进程启用 Per-Monitor V2
  DPI 感知；窗口进入不同分辨率、工作区或 DPI 的显示器后，会重新约束尺寸、位置、
  最小值和功能模块宽度。工作区不足时优先完整留在当前显示器内。1080p 默认布局
  使用精简说明与紧凑表单间距；内容未溢出时 Canvas 将滚动位置固定为顶部，不产生
  顶部空白，并隐藏滚动条；只有真实内容高度超过视口时才允许纵向滚动。

### 12.5 偏好、关闭与管理员重启

- 顶部「设置」菜单持久化默认窗口大小、字体族、字号、空闲关闭确认和最后功能页面。
  正常关闭或管理员重启后只恢复页面，不保存表单路径、硬盘选择或其它任务参数。
  运行或启动中关闭始终确认，不受空闲偏好影响。字体菜单只显示本机已安装候选字体，
  标准字号为默认值；表单、菜单、提示与独立日志窗口同步应用所选字体和字号。
- GUI 中仅「硬盘信息登记」及其检测步骤需要管理员权限，顶部管理员模式开关在
  空闲时先保存当前页面，再通过 Windows UAC 重新启动应用；开关不会在原进程内
  动态改变权限。

偏好文件固定为 `Output/GUI_Settings.json`，编码为 UTF-8 无 BOM、LF，并以临时文件
加原子替换写入：

| 键 | 类型／默认值 | 语义 |
|---|---|---|
| `version` | `1` | 偏好文件格式版本 |
| `window_size` | `[1920, 1080]` | 普通窗口目标客户区；仍受当前工作区约束 |
| `font_family` | `Microsoft YaHei UI` | 首选界面字体；不可用时回退到已安装候选字体 |
| `font_size_delta` | `0` | 标准 `0`、较大 `1`、特大 `2` |
| `confirm_close_when_idle` | `true` | 空闲关闭是否执行第一层确认 |
| `last_task_key` | `env_check` | 正常关闭或管理员重启后恢复的功能页面 |

未知键不会转成任务参数；非法字段逐项回退。`storage_list` 是 STG-11 内部步骤，不能
作为恢复页面。偏好文件不得包含 `saved_values`、档案路径、快照路径、硬盘编号、目录
队列、日志或进度。运行或启动中关闭始终执行确认；确认退出时先保存页面偏好，再停止
本窗口自己的任务。管理员重启也先保存页面，只有提权进程成功启动后才关闭旧窗口。

正常关闭、管理员重启与清理缓存的恢复边界如下：

| 操作 | 下次页面 | 表单内容 |
|---|---|---|
| 正常关闭后重开 | 最后功能页面 | 不保存，使用页面默认值 |
| 管理员模式重启 | 当前功能页面 | 不保存，使用页面默认值 |
| 清理缓存 | ENV-01 | 清空当前窗口全部表单、日志、进度和工具缓存 |

### 12.6 报告与其余界面行为

- 「结果报告导出」按输入类型说明产物：封存快照导出文件树、目录、规范化元数据、
  视频 GPS、媒体流、哈希、压缩包、错误和 Summary 等 CSV；Diff 数据库导出
  `Diff_summary.md`、`Diff_details.csv`、`Diff_dirs.csv`、
  `Diff_hash_groups.csv` 与 `Diff_subtrees.csv`。两种输入均额外生成
  `Report_Excel.xlsx`：原生 OOXML 使用 Unicode 内联字符串、中文工作表、中英字段、
  冻结表头、筛选和语义列宽；超过 Excel 单表行上限时分表而不静默截断。超过
  Excel 单元格上限的显示值只在 XLSX 中截短，完整值仍保留在 CSV。CSV 仍为 UTF-8
  无 BOM／LF，并保留完整技术字段。
- 「帮助」首项为「联系作者」，显示作者及 GitHub noreply 邮箱；「关于 DAISY」
  显示应用／DBS 生成器版本、DBS SQLite schema、元数据 profile、
  DBS／STG 文件名布局、STG 归档 schema 和 `v1.4.1` 最低读取器版本，并明确完整
  schema 3 快照与同生成器版本 partial 的不同兼容边界。
- GUI 默认优先使用 `Microsoft YaHei UI`，并可在本机已安装的中文／系统候选字体间
  切换，不依赖第三方字体。
  运行进度和运行日志标题区的 4 个操作按钮使用相同字体、宽度、内边距、列间距和
  右侧基线；功能模块标题使用与运行进度、运行日志一致的黑色标题字，三色装饰线
  恢复为原有 4 px 细线。系统标题栏使用 16／32／48 px 小雏菊多尺寸图标；界面不再
  显示 DAISY 花体字标。
- 单项任务也完整进入队列模型，始终显示 `队列 1/1`；多根目录和多块物理硬盘按
  实际子进程逐项显示 `队列 i/n`。每项在普通界面和小窗中均显示完整当前目标。
- 可同时打开多个 GUI 窗口。每个窗口的表单、队列、日志、进度、事件队列和子进程
  句柄属于各自实例；相同或不同模块可并发运行。窗口仍共享操作系统资源、外部工具和
  用户指定的输出路径，因此并发会竞争磁盘 I/O；确定性报告目标应使用不同输出目录，
  快照类产物继续依靠唯一 partial 与 no-clobber 保护正式文件。
- STG-11 检测开始时自动展示进度与日志。检测成功后弹窗，展开任务设置并收起进度、
  日志，便于选择硬盘；随后点击开始任务会再次进入标准运行布局。检测失败时保留进度
  与日志，避免隐藏诊断信息。
- JSON 和 Markdown／TXT 报告直接写入 DAISY 工具名、版本与作者；纯业务 CSV
  保持原有表头，并用同组的 `Report_info.csv` 或 `_Info.csv` 保存报告身份。
  DBS-41 另生成中文人读 XLSX，避免 Excel 双击 UTF-8 无 BOM CSV 时按本地 ANSI
  代码页误判中文。

### 12.7 GUI 安装与缓存边界

- 无 Python 时，`Script\Script_DAISY_Install_Python.ps1` 只在用户确认后
  通过固定包 ID 安装 Python 3.14，不安装其他工具。
- Python 已可运行时，「ENV-01 运行环境检测」会同时报告已发现工具的本机版本和
  全部缺失项。无论检测结果如何，ENV-01 任务设置页的「软件安装」区均以等宽
  单行四列布局常驻显示 ExifTool、ffprobe、7-Zip、smartctl 四个独立安装按钮，
  并只在用户再次确认后通过固定 WinGet 白名单处理所选工具；已安装状态和可用
  更新由 WinGet 判断，PowerShell 不由 GUI 安装。
- ExifTool、ffprobe、7-Zip、PowerShell、smartctl 的手动可执行文件路径只在
  顶部「高级 > 工具路径」菜单统一指定，并优先于本窗口检测缓存和运行时
  自动发现。
- 安装队列完成后 GUI 刷新当前进程 PATH 并重新检测。所有业务任务本身没有
  下载或安装逻辑。
- “清理缓存”先要求确认，再清除项目内白名单缓存目录
  `__pycache__`、`.pytest_cache`、`.mypy_cache`、`.ruff_cache`，
  独立 `.pyc`／`.pyo` 文件与当前窗口工具路径缓存，同时清空所有表单参数、
  目录队列、硬盘清单、日志和三条进度，返回 ENV-01 的首次启动状态。
- 清理不会跟随目录链接，也不会进入 `.git`、虚拟环境、`node_modules`
  或 `Output`。快照、Diff、报告和 partial 均不属于缓存，不会删除。

### 12.8 性能与覆盖限制

- Diff 当前把两侧条目载入内存，内存占用随条目数增长。
- Full 哈希针对机械盘采用顺序读取，不在同一介质并行争抢。
- 正式环境检测和 GUI 不执行介质性能跑分。
- 非 Canon RAW 和更多厂商格式仍需要补充真实样本；代码路径存在不等于所有变体
  都已经过实样验证。

可重复执行的回归测试位于 [`Script\Test`](../Script/Test/)；GUI 顶部「高级 >
DAISY功能自检」可启动同一套测试，覆盖 SQLite schema、数据库约束、快照、Diff、
GUI 参数映射和 STG 只读／归档边界；它不属于业务任务。v1.5.1 的矩阵、发布门槛和
实测结果见 [v1.5.1 测试计划](Spec_DAISY_V1_5_1_Test_Plan.md)。
