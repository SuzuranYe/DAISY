# DAISY v1.5.0 技术规格

- 状态：**现行规范**。
- 对应版本：**v1.5.0**。
- 本文定义 DAISY 的现行统一语义：DBS 获取数据库所描述的文件档案信息，STG
  获取物理硬盘信息；两者共用应用外壳，但保持独立数据模型；
  存储归档细节见[存储设备信息登记规格](Spec_DAISY_Storage.md)。
- 安装、启动与常用工作流见项目根目录的 [README](../README.md)。
- 从 `Kit_AL v1.0.2` 到当前版本的阶段变化见
  [版本演化规格](Spec_DAISY_Version_Evolution.md)。

## 一、功能域、编号与权威边界

DAISY v1.5.0 的信息能力分为两个并列功能域：

- **DBS 数据库档案信息**：获取文件树、大小、时间、File ID、元数据、哈希和
  快照变化，保存为 SQLite 快照或 Diff，并支持核验与导出。
- **STG 物理硬盘信息**：获取物理盘、分区、卷、Windows 存储属性和 smartctl
  原始证据，保存为独立 ZIP，并支持归档核验。

统一 GUI、CLI、ENV 环境检测、管理员模式与测试入口只负责调度和交互。STG 不
写入 DBS 的 SQLite，DBS 也不把物理盘资料嵌入快照；当前版本不自动建立文件条目
与物理硬盘档案之间的关联。

现行编号、名称、界面角色、CLI 与任务脚本必须按下表对齐。六字「界面名称」同时
用于功能模块按钮、面板菜单项和对应设置页标题：

| 编号 | 界面名称／步骤名 | GUI 角色 | CLI | 任务脚本 |
|---|---|---|---|---|
| ENV-01 | 运行环境检测 | 可见功能模块 | `env-check` | `Script_DAISY_Tool_ENV_01_Env_Check.py` |
| DBS-11 | 完整档案扫描 | 可见功能模块 | `full-scan` | `Script_DAISY_Tool_DBS_11_Full_Scan.py` |
| DBS-12 | 快速档案扫描 | 可见功能模块 | `quick-scan` | `Script_DAISY_Tool_DBS_12_Quick_Scan.py` |
| DBS-21 | 快照变更分析 | 可见功能模块 | `diff` | `Script_DAISY_Tool_DBS_21_Diff.py` |
| DBS-31 | 内容哈希核验 | 可见功能模块 | `check-hash` | `Script_DAISY_Tool_DBS_31_Check_Hash.py` |
| DBS-32 | 文件结构核验 | 可见功能模块 | `check-format` | `Script_DAISY_Tool_DBS_32_Check_Format.py` |
| DBS-41 | 结果报告导出 | 可见功能模块 | `export-report` | `Script_DAISY_Tool_DBS_41_Export_Report.py` |
| DBS-91 | DAISY功能自检 | 「高级」维护入口；不是功能模块 | 无独立 CLI | 无独立任务脚本；运行 `unittest discover` |
| STG-11 | 物理硬盘清单 | 登记页内部检测步骤；不显示为功能模块 | `storage-list` | `Script_DAISY_Tool_STG_11_List_Disks.py` |
| STG-12 | 硬盘信息登记 | 唯一可见硬盘功能模块 | `storage-collect` | `Script_DAISY_Tool_STG_12_Collect.py` |
| STG-21 | 硬盘归档核验 | 仅 CLI；不显示为功能模块 | `storage-verify` | `Script_DAISY_Tool_STG_21_Verify_Archive.py` |

文档解释意图和不变量，代码保存容易漂移的精确定义：

| 内容 | 最终权威 |
|---|---|
| 快照 SQLite DDL | `Script\Lib\Script_DAISY_Lib_01_Core.py` 中的 `SNAPSHOT_DDL` |
| Diff SQLite DDL | `Script\Lib\Script_DAISY_Lib_04_Diff.py` 中的 `DIFF_DDL` |
| 规范化元数据取值链 | `Script\Lib\Script_DAISY_Lib_02_Meta.py` |
| 哈希、复用和独立抽验 | `Script\Lib\Script_DAISY_Lib_03_Hash.py` |
| CLI 分发、现行脚本名 | `Script\Script_DAISY_MAIN.py` 中的 `COMMANDS` |
| CLI 参数及默认值 | 上表对应任务脚本的参数解析器 |
| GUI 显示值到 CLI 的映射 | `Script\Script_DAISY_GUI.py` |
| STG 物理盘只读登记与 ZIP 协议 | `Spec\Spec_DAISY_Storage.md` 及 `Script\Lib\Script_DAISY_Lib_STG_*.py` |

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
SQLite，并在同目录生成 `_Issues.md`。

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

续传只接受 partial，并沿用数据库内原配置及 `snapshot_stem`。微秒和随机
运行 ID 只属于运行态，不进入最终封存名。新任务对 partial 和最终目标都采用
no-clobber 语义。

成功封存顺序：

1. 检查无 pending／processing 残留并写最终统计；
2. 写 `scan_status=complete` 和真实 `hash_coverage`；
3. 把 manifest 与运行事件写入 `snapshot_manifest`／`run_events`；
4. 执行 SQLite `integrity_check`；
5. checkpoint WAL，切回 DELETE journal 并关闭连接；
6. 对稳定 SQLite 字节计算完整 SHA-256；
7. 取摘要前 8 个十六进制字符并大写；
8. 有问题时，以目标存在即失败的方式创建同目录 `_Issues.md`；
9. 以目标存在即失败的原子重命名发布 SQLite；
10. 删除已内嵌的运行态 JSONL。

失败或中断时保留 partial 和事件，供诊断或续传。

### 7.4 文件名

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
  明细表，但不进入数据库文件名；此时在数据库同目录生成
  `<数据库基名>_Issues.md`。
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

Full 的独立抽验和 `check-hash` 使用 PowerShell `Get-FileHash`，与主哈希实现分离。抽验不一致时双方重算；仍不一致则标记异常并留证。

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

## 十一、STG 存储设备信息登记

- `STG-12 硬盘信息登记` 是 GUI 中唯一可见的硬盘功能模块。登记页的「检测物理
  硬盘」按钮在同一页面调用内部 `STG-11`，清单完成后刷新目标下拉框；用户不需要
  在两个功能模块之间切换。`STG-21` 只保留 CLI 入口。
- 内部 `STG-11` 与可见 `STG-12` 的完整执行需要管理员权限。GUI 在模块说明、
  悬停说明、任务设置页和启动确认中显示该要求；管理员模式悬停说明明确当前仅
  「硬盘信息登记」及其检测步骤需要此模式。未提权时应开启顶部管理员模式开关，
  确认后由 Windows UAC 重新启动 DAISY。任务运行期间不能切换权限。未提权继续
  运行只会如实保留权限缺口、不完整诊断或失败，不会降低只读安全边界。
- `STG-11`／`storage-list` 联合 Windows 存储 cmdlet 和
  `smartctl --scan-open --json=c` 列出物理盘；盘符只用于人类识别，不作为
  权威身份。GUI 每次重新列盘会清除旧选择，`STG-12` 只允许从当次清单中选择
  同时具有 Windows 记录和 smartctl 关联的目标。
- `STG-12`／`storage-collect` 按 `DiskNumber` 重新取得详细 Windows 清单并
  核对容量、UniqueId 和序列号，再以固定只读模板
  `smartctl -x --json=ov -d <扫描类型> <扫描设备>` 采集单盘证据。
- `STG-21`／`storage-verify` 是仅 CLI 的安全核验工具，只读取既有 ZIP，核验文件名 SHA-256 高 32 bit、
  固定成员集合、Manifest、时间对、成员字节数和 ZIP CRC；不访问真实硬盘，也
  不需要管理员权限。
- 默认输出为 `Output\Storage`。每块物理盘生成独立 PROFILE ZIP，可选在 ZIP
  外生成简化 TXT；目标存在即失败，不覆盖既有文件。
- STG ZIP 使用独立 `archive_schema_version=3`。它不导入 `sqlite3`，也不创建、
  查询或修改快照／Diff 数据库；和 SQLite `schema_version=3` 仅数字相同。
- smartctl 由 `ENV-01` 发现、验证与缓存，缺失时可在用户逐项确认后通过固定
  `smartmontools.smartmontools` WinGet 包安装。PowerShell 仍不由 GUI 安装。
- 归档可能包含序列号、卷标、卷 GUID、挂载路径、PNP Device ID、计算机名和
  BitLocker 状态；不得未经检查公开分享。完整协议见
  [存储设备信息登记规格](Spec_DAISY_Storage.md)。

## 十二、版本、界面适配与已知限制

- 当前应用版本为 `1.5.0`。DBS 新数据库产物继续使用 `schema_version=3`、
  元数据 profile 7 和 `min_reader_version=1.4.1`，当前实现只读取 schema 3。
- v1.5.0 新增 STG 功能域，并统一任务入口脚本的 ENV／DBS／STG 前缀。数据库
  实现除 `SCANNER_VERSION` 更新为 `1.5.0` 外保持不变：DDL、字段、约束、
  schema 版本和数据库业务语义均未改变。
- `.partial.sqlite` 续传必须同时匹配 `SCANNER_VERSION`、`schema_version`、
  元数据 profile 和 GPS 表；因此 v1.4.2 的未完成 partial 不能由 v1.5.0 续传。
  已完成的 schema 3 封存快照仍可按现有准入规则只读使用。
- 当前顶栏固定为 `文件｜面板｜高级｜视图｜帮助`，菜单栏使用浅米黄色底色。
  「面板」包含「环境」「数据」和「硬盘」三个子菜单；色带下方三行标题分别为
  「环境 ENV」「数据 DBS」「硬盘 STG」。「高级」包含「工具路径」「哈希比例」、
  动态「显示／隐藏命令预览」命令和「DAISY功能自检」。DBS-11 独立哈希抽验与
  DBS-31 内容哈希抽样只改变参数编辑位置，不改变字段、默认值或 CLI 参数。
- 所有功能模块按钮与设置页标题共用同一套六字名称。任务设置中不使用页内
  「展开后勾选」控件：故障恢复为「不启用／启用」下拉框，根标签映射直接显示为
  文本输入；既有 `--force`、`--map-root` 默认值和执行语义不变。只读下拉框未展开
  时拦截鼠标滚轮，滚轮只滚动页面，不改变选项。
- 功能模块、任务设置、运行进度和运行日志可独立折叠，运行进度与运行日志默认
  折叠；命令预览默认关闭。队列总进度、当前任务阶段和本阶段工作量三条进度常驻且
  语义独立。进度顶部显示当前扫描根文件夹的完整路径，单项、队列和小窗视图一致。
  「清空日志」位于运行日志标题旁。
- 小窗视图在空闲和运行时始终可进入；它保留当前完整目标、三条进度和运行控制。
  开始完整档案扫描前的确认框按分别／合并模式列出全部完整根路径，提示任务可能
  持续几小时到几天，再由用户确认是否执行。
- 普通窗口在工作区足够时以 `1280×720` 打开。Windows 进程启用 Per-Monitor V2
  DPI 感知；窗口进入不同分辨率、工作区或 DPI 的显示器后，会重新约束尺寸、位置、
  最小值和功能模块宽度。工作区不足时优先完整留在当前显示器内，不强行保持
  1280×720。
- GUI 中仅「硬盘信息登记」及其内部 STG-11 检测步骤需要管理员权限，顶部管理员
  模式开关在空闲时通过 Windows UAC 重新启动应用；仅 CLI 的 STG-21 不需要提权。
  开关不会在原进程内动态改变权限。
- 「结果报告导出」按输入类型说明产物：封存快照导出文件树、目录、规范化元数据、
  视频 GPS、媒体流、哈希、压缩包、错误和 Summary 等 CSV；Diff 数据库导出
  `Diff_summary.md`、`Diff_details.csv`、`Diff_dirs.csv`、
  `Diff_hash_groups.csv` 与 `Diff_subtrees.csv`。
- 「关于 DAISY」显示应用／DBS 生成器版本、DBS SQLite schema、元数据 profile、
  DBS／STG 文件名布局、STG 归档 schema 和 `v1.4.1` 最低读取器版本，并明确完整
  schema 3 快照与同生成器版本 partial 的不同兼容边界。

### 12.1 GUI 安装与缓存边界

- 无 Python 时，`Script\Script_DAISY_Install_Python.ps1` 只在用户确认后
  通过固定包 ID 安装 Python 3.14，不安装其他工具。
- Python 已可运行时，「ENV-01 运行环境检测」会同时报告已发现工具的本机版本和
  全部缺失项。无论检测结果如何，ENV-01 任务设置页的「软件安装」区均以等宽
  2×2 布局常驻显示 ExifTool、ffprobe、7-Zip、smartctl 四个独立安装按钮，
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

### 12.2 性能与覆盖限制

- Diff 当前把两侧条目载入内存，内存占用随条目数增长。
- Full 哈希针对机械盘采用顺序读取，不在同一介质并行争抢。
- 正式环境检测和 GUI 不执行介质性能跑分。
- 非 Canon RAW 和更多厂商格式仍需要补充真实样本；代码路径存在不等于所有变体
  都已经过实样验证。

可重复执行的回归测试位于 [`Script\Test`](../Script/Test/)；GUI 顶部「高级 >
DAISY功能自检」可启动同一套测试，覆盖 SQLite schema、数据库约束、快照、Diff、
GUI 参数映射和 STG 只读／归档边界；它不属于业务任务。
