# DAISY v1.3.3 技术规格

- 状态：**现行规范**。
- 对应版本：**v1.3.3**。
- 本文定义快照、哈希、元数据、格式校验和 Diff 的现行语义。
- 安装、启动与常用工作流见项目根目录的 [README](../README.md)。

## 一、权威边界

文档解释意图和不变量，代码保存容易漂移的精确定义：

| 内容 | 最终权威 |
|---|---|
| 快照 SQLite DDL | `Script\Lib\Script_DAISY_Lib_01_Core.py` 中的 `SNAPSHOT_DDL` |
| Diff SQLite DDL | `Script\Lib\Script_DAISY_Lib_04_Diff.py` 中的 `DIFF_DDL` |
| 规范化元数据取值链 | `Script\Lib\Script_DAISY_Lib_02_Meta.py` |
| 哈希、复用和独立抽验 | `Script\Lib\Script_DAISY_Lib_03_Hash.py` |
| CLI 参数及默认值 | 对应 `Script\Tool\Script_DAISY_Tool_*.py` 的参数解析器 |
| GUI 显示值到 CLI 的映射 | `Script\Script_DAISY_GUI.py` |

## 二、系统不变量

1. **源档案只读**：DAISY 不创建、修改、重命名或删除源目录中的任何项目。
2. **快照封存后不可变**：后续核验、Diff 和导出只读输入数据库；新分析产生新文件。
3. **无有效内容哈希时不得推断内容相同**：大小和时间相同只能证明 stat 未变，不能替代内容证据。
4. **业务运行纯本地**：七项业务任务没有网络、遥测、上传或在线查询；云占位文件不会被触发下载。根目录的依赖安装脚本不属于业务运行，执行时会通过 WinGet 联网。
5. **路径可迁移**：身份以 root label 和相对路径表示，不依赖盘符；当次 `root_path` 仅作定位与审计。
6. **时间可审计**：自产时间使用 UTC ISO 8601 `Z`；本地时间只用于显示和文件名。
7. **文本统一**：正式文本输出使用 UTF-8（无 BOM）和 LF。
8. **失败如实保留**：单文件失败通常记录到 `errors`，不会伪装为成功；高错误率才触发告警或熔断。

### 2.1 内容读取边界

- Full 哈希和哈希巡检会读取文件内容，但字节只进入 SHA-256 实现。
- 元数据阶段不提取文本正文、单元格、幻灯片正文或压缩包成员内容。
- 文档只读取属性区，例如 OOXML `docProps/*`、PDF Info／XMP。
- 压缩包登记只读取目录结构和成员描述，不读取成员数据。
- **显式运行 `check-format` 是例外**：结构校验器可以为验证 CRC 或结构而读取文件及压缩包成员数据，但仍不保存正文，也不写回源文件。

## 三、支持类型与处理

| `media_kind` | 扩展名 | Full 元数据处理 |
|---|---|---|
| `photo_raw` | cr2 cr3 nef arw raf orf rw2 dng | ExifTool 照片 profile |
| `photo_jpeg` | jpg jpeg | ExifTool |
| `photo_working` | tif tiff psd psb png | ExifTool＋`working_metadata` |
| `video_mp4` | mp4 mov lrf | ExifTool＋ffprobe |
| `video_crm` | crm | ExifTool＋ffprobe，允许 CTMD 长尾字段进入 Raw Payload |
| `audio` | wav mp3 | 视频同管线；title／author／album／copyright 优先采用 ffprobe tags |
| `archive` | zip 7z rar tar gz bz2 xz | ZIP 使用 `zipfile` 目录；其他格式使用 7-Zip 列表 |
| `document` | pdf docx xlsx pptx | 只登记属性，不读取正文 |
| `other` | 其他全部 | 进入树和哈希；不做专用元数据解析 |

“支持”表示代码具有对应处理路径，不表示所有厂商、固件和损坏形态都经过真实样本验证。

元数据 profile v2：

- 照片：`-j -G1:3:4 -a -u -D -l -ee -charset filename=utf8`；
- 视频、音频、文档：同组参数但不含 `-ee`；
- ffprobe：`-print_format json -show_format -show_streams -show_chapters -show_programs -show_stream_groups -show_data`。
- v2 新增 ffprobe 容器级 `format.tags.location` 的 ISO 6709 规范化；外部
  工具读取参数相对 v1 不变。

## 四、Raw Payload 与规范化元数据

Raw Payload 是**后端原始 JSON 的保留开关**，不是元数据提取总开关：

- 默认保留。后端 JSON canonicalize 后以 zlib level 6 压缩，`payload_sha256` 是未压缩 canonical JSON 的 SHA-256。
- `--no-raw-payload` 仍执行 ExifTool／ffprobe 并写规范化表，只是不写 `raw_payloads`。
- 关闭后无法重新解释历史后端字段，也无法用原始载荷判断 `metadata_extraction_changed`。
- Raw Payload 不是隐私开关；规范化列仍可能包含作者、设备、时间或位置等元数据。
- “全部字段”仅指**当前 profile 返回的 JSON 字段全部保留**，不代表外部工具未返回的字段也被采集。
- `payload_zlib` 和 `payload_sha256` 保留完整原始载荷；Diff 只有在 ExifTool
  摘要不同且工具版本相同时，才按需解压候选载荷，并在比较副本中排除
  `FileAccessDate`。该字段会因只读访问而变化，不构成提取语义变化；其他字段
  或工具版本变化仍判为 `metadata_extraction_changed`。

规范化取值要点：

- 拍摄时间不擅自推断时区；只有明确 offset 标签才填写偏移。
- `QuickTime:CreateDate` 不默认解释成 UTC。
- 色彩优先采用 ICC profile；EXIF ColorSpace 只作辅助。
- Canon gamma／gamut 取 CanonLogVersion／ColorSpace2；其他厂商没有可靠来源时留空。
- 音频文本标签优先使用 ffprobe，避免 RIFF INFO 非标准编码造成误解。
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
| 错误 | `errors` | 阶段、后端、错误码和文本 |
| 视图 | `v_file_manifest`、`v_dir_problems` | 常用清单与目录问题查询 |

Quick 与 Full 使用相同核心 schema。Quick 不生成内容哈希、专用元数据或 Raw Payload，`video_gps_points` 因此为空，但保持统一的数据结构和明确的状态值。快照报告把视频 GPS 点导出为 `GPS_inventory_video.csv`；profile v1 的既有快照没有该 additive 表，仍可导出其余页面。

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

元数据汇总状态优先级：

1. 解析前后 size／mtime 改变：`unstable`；
2. 任一适用后端超时：`timeout`；
3. 任一适用后端失败：`error`；
4. 所有适用后端成功：`done`；
5. 不适用：`not_applicable`；
6. 占位或明确跳过：`skipped`。

哈希 `valid` 的充要条件是：摘要非空、`bytes_read == size_bytes`，并且读取前后 size／mtime 一致。

## 七、Full／Quick 与封存

### 7.1 默认能力

Full 默认：

- `hash=full`；
- 保留 Raw Payload；
- 采集 File ID；
- 独立抽验 1%，至少 100 个本次 computed 条目。

Quick：

- 不读取内容；
- 不运行外部工具；
- 不计算内容哈希；
- 不提取元数据或 Raw Payload；
- 默认采集 File ID。

### 7.2 运行态与封存

Full 运行态包含：

```text
<name>.partial.sqlite
<name>.partial.sqlite-wal
<name>.partial.sqlite-shm
<name>.partial.sqlite.lock
<name>.events.jsonl
```

续传只接受 partial，并沿用数据库内原配置。新任务对 partial 和最终目标都采用 no-clobber 语义。

成功封存顺序：

1. 检查无 pending／processing 残留并写最终统计；
2. 写 `scan_status=complete` 和真实 `hash_coverage`；
3. 把 manifest 与运行事件写入 `snapshot_manifest`／`run_events`；
4. 执行 SQLite `integrity_check`；
5. checkpoint WAL，切回 DELETE journal 并关闭连接；
6. 对稳定 SQLite 字节计算完整 SHA-256；
7. 取摘要前 8 个十六进制字符并大写；
8. 以目标存在即失败的原子重命名发布；
9. 删除已内嵌的运行态 JSONL。

失败或中断时保留 partial 和事件，供诊断或续传。

### 7.3 文件名

```text
根标签_类型_[偏差标记_]日期_时间.微秒_runid8[_Abnormal]_XXXXXXXX.sqlite
```

- `XXXXXXXX` 是最终数据库完整 SHA-256 的最高 32 bit，不是完整摘要。
- Full 无标记基线＝full hash＋Raw Payload＋File ID。
- 偏差标记固定顺序：`No-Hash`、`Hash-Inc`、`No-Raw`、`No-FID`。
- Quick 已蕴含无哈希和无 Raw Payload，只在关闭 File ID 时增加 `No-FID`。
- 多 root 合并时用 `+` 连接安全化后的 label。
- 文件名使用本地时间；库内 UTC 时间和 UUID 才是权威身份。
- 任何哈希／元数据 error、unstable、枚举失败或异常来源复用都会增加 `_Abnormal`。

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

来源快照异常时默认拒绝复用。`--allow-abnormal-source` 可显式越过，但新产物强制 `_Abnormal`。复用记录保存最初计算事件，而不是只指向最后一个中间快照。

Full 的独立抽验和 `check-hash` 使用 PowerShell `Get-FileHash`，与主哈希实现分离。抽验不一致时双方重算；仍不一致则标记异常并留证。

PowerShell 按「手动路径 → `PATH` → Windows 常规安装位置」发现。自动发现会
逐个验证候选是否可启动、能否报告版本以及是否提供 `Get-FileHash`；单个坏候选
不会阻断后续候选。便携版或自定义安装位置通过 `--powershell-path` 指定。
`env-check` 还会在系统临时目录对固定样本实际执行一次 `Get-FileHash`，不读取
档案内容。

已知边界：攻击者可以刻意保持 size／mtime；因此增量快照不能永久替代定期 full hash。

## 九、快照准入与核验

以下情况硬拒绝，`--force` 也不能越过：

- 数据库不是封存完成状态；
- SQLite integrity check 失败；
- 文件名已有高 32 bit 指纹，但与当前字节复算不符；
- 两侧 `schema_version` 或 `path_key_rule` 不兼容。

唯一可降级项是**文件名缺少指纹**。`--force` 允许继续，但结果必须标记为异常。当前版本不读取旧 sidecar、散置 `.sha256` 或旧 `SHA8-` 命名；旧快照需对原档案重新登记。

`check-hash`：

- 总是先检查记录条目的存在性、size 和 mtime；
- 默认抽样 1%，至少 100 个有有效基准哈希的条目；
- `--full` 对所有有有效基准哈希的条目独立复算；
- 结论只覆盖本次实际检查口径。

`check-format`：

- 默认检查全部可校验文件，GUI 可选择按比例抽样；
- ZIP／OOXML 可读取成员并校验 CRC；
- PDF 使用头、尾和 `startxref` 结构检查；
- 媒体使用 ExifTool validate，视频／音频叠加 ffprobe；
- 其他格式返回 unsupported，由哈希层而不是结构层提供变化保护。

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

## 十一、版本、性能与已知限制

- `SCANNER_VERSION=1.3.3`；`schema_version=1`。
- v1.3.0 新增 `snapshot_manifest` 和 `run_events`，属于 additive DDL 扩展，因此 schema 版本仍为 1。
- v1.3.0 对旧封装不兼容，是已明确记录的版本号例外，不能由次版本号推断兼容。
- v1.3.1 整理 GitHub 发布结构、依赖安装说明和代码内历史命名，并加入 GUI 项目自检入口；不改变数据库 schema 或七项业务任务的运行语义。
- v1.3.2 排除 `FileAccessDate` 对 Diff 元数据判断的干扰，加入运行时生成的截断媒体回归，移除未接入正式路径的 `block_hashes` 表和 `hash_coverage=partial` 值，并把依赖安装改为逐项说明、逐项确认；正式读写语义不变，`schema_version` 仍为 1。
- v1.3.3 修复已安装 PowerShell 不在进程 `PATH` 时的误判，增加 Windows 常规位置回退、坏候选跳过、完整登记手动路径覆盖，并让正式环境检测实际验证 `Get-FileHash`；同时新增 additive `video_gps_points`、ISO 6709 文件级视频位置规范化和 `GPS_inventory_video.csv`，元数据 profile 升至 2，`schema_version` 仍为 1。
- `.partial.sqlite` 续传必须同时匹配 `SCANNER_VERSION`、`schema_version`、
  元数据 profile 和 GPS 表；改动前同版本但仍为 profile v1 的 partial
  会被明确拒绝。既有封存快照不迁移、不回写。
- Diff 当前把两侧条目载入内存，内存占用随条目数增长。
- Full 哈希针对机械盘采用顺序读取，不在同一介质并行争抢。
- 正式环境检测和 GUI 不执行介质性能跑分。
- 非 Canon RAW 和更多厂商格式仍需要补充真实样本；“代码路径存在”不等于“所有变体已验证”。

可重复执行的回归测试位于 [`Script\Test`](../Script/Test/)；GUI 的“10 环境检测”
页可启动同一套测试，但它不属于第八项业务任务。
