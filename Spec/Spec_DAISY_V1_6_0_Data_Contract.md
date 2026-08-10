# DAISY v1.6.0 数据与恢复契约

阅读说明：本文冻结 v1.6.0 数据契约，代码标识和契约术语不得按后续 GUI 名称改写；
面向用户的现行术语以[技术规格](Spec_DAISY_Technical.md#11-用户可见文字规范)为准。

## 一、范围与兼容边界

本文冻结 v1.6.0 新建快照的数据库、续传和发布语义。实现与测试不得用应用版本、文件名
或 GUI 状态代替数据库契约。

- v1.4.1/schema 3 的 `SNAPSHOT_DDL` 和 `DIFF_DDL` 字节及语义永久冻结；
- v1.6.0 只读接纳合格的 schema 3 封存快照和 Diff；
- v1.6.0 新扫描使用 schema 4，不在 schema 3 库上 `ALTER TABLE`；
- v1.4.1 partial 只做只读诊断，不接管、不写入、不升级；
- 不承诺 v1.4.1 程序读取 schema 4；
- schema 4 先保持 schema 3 的业务表，再增加独立运行证据表。

## 二、冻结标识

| 标识 | 值 |
|---|---|
| 快照 schema | `4` |
| 旧库最低兼容 schema | `3` |
| data contract | `daisy-snapshot-v4` |
| resume contract | `daisy-resume-v1` |
| projection contract | `daisy-snapshot-projection-v1` |
| filename layout | `3` |
| schema 4 最低读取器 | `1.6.0` |
| lease 心跳间隔 | `5s` |
| lease 有效期 | `30s` |
| schema 3 `SNAPSHOT_DDL` SHA-256 | `9d162b401617a9242393ba2dcf32445be6437799553abb4c5923c527dc0963a7` |
| schema 4 `SNAPSHOT_DDL_V4` SHA-256 | `c8e3bbbd899818bc9653fcc5a27594b3a650d44643e838c23d4db4f9c66e1d34` |

`snapshot_info.scan_status` 在 schema 4 中仍只承担粗粒度兼容状态：

| 运行状态 | `scan_status` | `database_integrity` |
|---|---|---|
| `running`／`pause_requested`／`sealing` | `running` | `pending` |
| `paused`／`stopped`／`failed_recoverable` | `interrupted` | `pending` |
| `failed_terminal` | `interrupted` | `failed` 或 `pending` |
| `sealed_unpublished`／`published` | `complete` | `ok` |

完整状态只读 `snapshot_runtime.run_state`，不得向 schema 3 的旧枚举偷偷加入新值。

## 三、schema 4 新表

### 3.1 `run_sessions`

一行表示一次初始运行或一次恢复运行。恢复不会覆盖旧 session。

| 列 | 约束与语义 |
|---|---|
| `session_id` | 32 位小写十六进制 UUID，主键 |
| `session_number` | 从 1 开始，库内唯一递增 |
| `parent_session_id` | 恢复来源；初始 session 为 `NULL` |
| `session_kind` | `initial`／`resume` |
| `session_status` | `active`／`paused`／`saved`／`stopped`／`completed`／`failed`／`abandoned` |
| `started_at_utc`、`updated_at_utc`、`ended_at_utc` | session 时间线 |
| `hostname`、`pid`、`process_start_token` | 精确 owner 身份，PID 不能单独作为身份 |
| `lease_id` | 本次所有权租约 UUID |
| `lease_acquired_at_utc`、`lease_heartbeat_at_utc`、`lease_expires_at_utc` | 租约证据 |
| `scanner_version`、`resume_contract` | 创建和恢复契约 |
| `config_json`、`tools_json` | 本 session 实际配置与工具 |
| `end_reason` | 保存退出、停止、失败、发布或异常终止原因 |

### 3.2 `snapshot_runtime`

单例行 `id=1`，是 partial 的当前运行事实。

| 列 | 约束与语义 |
|---|---|
| `snapshot_uuid` | 对应 `snapshot_info.snapshot_uuid` |
| `schema_version` | 固定为 `4` |
| `data_contract`、`min_reader_version` | `daisy-snapshot-v4`／`1.6.0` |
| `resume_contract`、`projection_contract` | 冻结标识 |
| `filename_layout_version` | 固定为 `3` |
| `run_state` | 第四节冻结枚举 |
| `state_revision` | 从 1 开始；每次转换加 1，用于 compare-and-swap |
| `resume_hint` | `none`／`suggest`／`manual_only` |
| `active_session_id` | 当前或最后一个 session |
| `current_stage` | 当前阶段；没有时为 `NULL` |
| `created_at_utc`、`updated_at_utc`、`last_checkpoint_at_utc` | 当前状态时间 |
| `output_dir` | 规范化绝对输出目录 |
| `partial_path` | 规范化绝对 partial 路径 |
| `publish_stem_path` | 不含摘要后缀的发布绝对路径 |
| `event_log_path` | 临时 JSONL 绝对路径 |
| `published_path_pattern` | 仅发布副本记录绝对路径模式 `发布 stem_<SHA256-high32-uppercase>.sqlite`，否则 `NULL` |
| `last_error_code`、`last_error_message` | 最近状态级错误 |

恢复必须核对 `partial_path`、输出目录、发布 stem、resume contract 和 filename layout；任一
不一致都只诊断，不猜测修补。

### 3.3 `stage_checkpoints`

固定阶段为 `enumerate`、`hash`、`metadata`、`format`、`rescan`、`verify_hash`、
`verify_format`、`seal`、`publish`。每阶段只有一个当前行。

状态为 `pending`、`running`、`pause_requested`、`paused`、`completed`、`skipped`、
`failed_recoverable` 或 `failed_terminal`。记录 session、项目／字节进度、错误数、当前
`entry_id`、起止／检查点时间及限量 JSON 摘要。不得逐块写库。

### 3.4 `run_state_events`

这是状态转换的数据库内权威日志。每行记录 session 内序号、时间、事件、前后状态、
`state_revision` 和 JSON 载荷。外部 `.events.jsonl` 仅用于运行中可观察性，不得凌驾于
已提交的数据库状态。

### 3.5 `entry_attempts`

历史尝试与当前结果分离，唯一键为 `(entry_id, stage, attempt_number)`。

- `stage`：`hash`、`metadata`、`format`、`verify_hash`、`verify_format`；
- `status`：`running`、`succeeded`、`invalid`、`error`、`timeout`、`unstable`、
  `unsupported`、`skipped_policy`、`cancelled`、`abandoned`；
- 记录 session、工具、开始／最后进展／结束时间、源 size／mtime、已读字节、最终偏移、
  stall 次数和最长 stall；
- `decision`：`none`、`continue_waiting`、`skip_and_record`、`stop_and_resume`；
- `decision_source`：`none`、`user`、`default`、`advanced_policy`、`shutdown`；
- `end_reason`、错误码／错误消息和限量结果 JSON 保存可审计结论。

更新当前哈希、元数据或格式结果前可以删除上一次派生行，但不得删除历史 attempt。

### 3.6 `read_performance`

一行对应一次 attempt 的低频摘要，`attempt_id` 唯一。至少记录 entry、session、阶段、
`origin`、文件大小、已读字节、总耗时、活跃读取耗时、stall 统计、首次／末次 stall
偏移、最终偏移和结束原因。

候选置信度只允许 `none`、`low`、`high`。候选原因必须使用「读取性能异常候选」措辞；
不得把逻辑路径推断为物理坏区。

### 3.7 `format_checks`

一行表示一个 entry 的当前格式结果，历史在 `entry_attempts`。

- `status`：`pending`、`processing`、`valid`、`invalid`、`unsupported`、`timeout`、
  `error`、`unstable`、`skipped_policy`；
- `coverage`：`sample`／`full`；
- 记录当前 attempt、validator、工具、`stat_match`、详情、时间和结果 revision；
- 未开启格式校验时保持空表，Reader 报 `unavailable`，不能伪装为执行后 `0` 问题。

### 3.8 外部工具故障证据

外部工具运行证据复用 `entry_attempts`、`stage_checkpoints`、`run_state_events` 和
`run_sessions`，不得为修复工具故障而修改 schema 3，也不得把工具内部会话状态写进业务表。

- 所有工具统一区分 `source_error`、`tool_error`、`timeout`、`unsupported`、`unstable` 和
  `not_processed`；其中 `not_processed` 是阶段覆盖语义，不新增到 schema 3 的条目状态枚举；
- 每个工具故障事件至少记录工具、操作、session／worker PID、退出码或 errno、有限 stderr、
  重试／重启次数、影响条目范围和剩余未处理数；无法取得的字段明确为 `NULL`；
- ExifTool 长驻会话另有独立 `tool_session_id`。EOF、进程退出、Broken Pipe、`OSError 22`
  或协议失效必须使旧会话立即不可复用；新会话通过健康检查后才可接收当前文件的一次重试；
- 一次性 ffprobe、7-Zip、rawpy／LibRaw、哈希 worker 和 smartctl 不伪造长驻会话恢复；每次只
  回收本次创建的精确进程，按后端策略有限重试，并进入同一故障分类与熔断器；
- 同一工具级签名默认连续 3 次失败，或工具重启／健康检查失败时，当前阶段转为
  `failed_recoverable`。当前及剩余未完成条目保持可重试，不逐项制造错误，快照不得封存；
- 恢复只重跑未完成或明确允许重试的工具故障条目；已完成哈希不因元数据工具故障重新计算；
- `_Issues.md` 的「运行／证据问题」只投影聚合事件、受影响数量和有限范围，不把同一工具
  故障复制为每个源文件的问题。完整机器证据留在上述运行表或不修改 SQLite 的伴随输出。

## 四、运行状态机

冻结状态：

- `running`；
- `pause_requested`；
- `paused`；
- `stopped`；
- `sealing`；
- `sealed_unpublished`；
- `published`；
- `failed_recoverable`；
- `failed_terminal`。

允许的转换如下；表外转换必须拒绝并保持数据库字节语义不变。

| 当前 | 允许目标 |
|---|---|
| `running` | `pause_requested`、`stopped`、`sealing`、`failed_recoverable`、`failed_terminal` |
| `pause_requested` | `running`、`paused`、`stopped`、`failed_recoverable`、`failed_terminal` |
| `paused` | `running`、`stopped`、`failed_recoverable`、`failed_terminal` |
| `stopped` | `running`（仅手动恢复）、`failed_terminal` |
| `sealing` | `sealed_unpublished`、`failed_recoverable`、`failed_terminal` |
| `sealed_unpublished` | `published`、`failed_recoverable` |
| `failed_recoverable` | `running`、`stopped`、`failed_terminal` |
| `published` | 无 |
| `failed_terminal` | 无 |

每次转换在一个 SQLite 事务中同时完成：

1. 以 `run_state + state_revision` 做 compare-and-swap；
2. 更新 `snapshot_runtime`；
3. 映射粗粒度 `snapshot_info` 状态；
4. 更新 session；
5. 写 `run_state_events`；
6. 提交后才向 GUI 确认成功。

## 五、用户动作语义

### 5.1 暂停／继续

`running → pause_requested → paused`。只有工作领取停止、当前事务到达安全边界且当前
attempt 可解释后才能进入 `paused`。同 session 继续为 `paused → running`，lease 保留。

### 5.2 保存进度并退出

到达 `paused` 后把 session 标为 `saved`，`resume_hint=suggest`，提交并关闭数据库，再按
lease ID 释放锁。下次只显示恢复卡片，不自动读取。

### 5.3 停止任务

转换到 `stopped`，session 标为 `stopped`，`resume_hint=manual_only`。partial 保留；启动
时不主动推荐，但用户可明确选择手动恢复。

### 5.4 突然终止恢复

确认旧 lease 无效后，在一个恢复事务中：

同会话 `paused` 但 active session 没有结束，也属于突然终止；它不同于已完成「保存进度
并退出」的 `paused + saved session`，必须先执行以下恢复事务，不能直接创建并行 session。

1. 把 `entry_attempts.status=running` 改为 `abandoned`；
2. 把对应 `entries.hash_status/meta_status=processing` 还原为 `pending`；
3. 把 `format_checks.status=processing` 还原为 `pending`；
4. 清空阶段的当前 entry，并把未完成阶段标为可恢复失败；
5. 旧 session 标为 `abandoned`；
6. 当前状态改为 `failed_recoverable`，`resume_hint=suggest`；
7. 用户确认后创建新的 `resume` session，再进入 `running`。

当前文件从头处理；不序列化 hashlib 或外部工具进程状态。

### 5.5 任务控制协议

GUI 与 v1.6.0 任务子进程之间使用 `daisy-control-v1` 单行 UTF-8 JSONL。每条消息不超过
4096 bytes，包含严格递增的正整数 `sequence` 和动作；过长、损坏、重复、倒序或未知协议
消息必须拒绝，不能猜测执行。动作固定为 `pause`、`continue`、`save_exit`、`stop` 和
`timeout_decision`。`timeout_decision` 还必须携带当前受控 worker 的正整数 PID，以及
`continue_waiting`、`skip_and_record`、`stop_and_resume` 之一。

生命周期动作以先到者为准。运行中只接受一次暂停／保存退出／停止；进入安全暂停点后只
接受一次继续／保存退出／停止。每次继续都创建新的进程控制对象，上一文件 worker 的 PID
和 timeout 决定不能泄漏到下一次尝试。timeout 决定与当前 worker PID 绑定；旧 PID、已
结束 worker 或第二个决定均拒绝；读取恢复进展后关闭旧决定窗口并丢弃未执行选择。终止型
timeout 决定和生命周期动作也以先提交者为准，不能同时返回两个成功回执。用户决定与高级
默认值通过同一原子入口竞争，默认值不能覆盖已经提交的用户选择。

用户可以先暂停，再决定保存退出。此时数据库仍保持 `paused`，但必须以 CAS 增加
`state_revision`，写入 `paused_saved_for_exit` 事件，把 session 从 `paused` 改为 `saved`
并设置 `resume_hint=suggest`。这是一个受审计的 session 收尾动作，不是允许任意
`paused → paused` 状态转换；重复执行必须整体拒绝。控制输入读取器不关闭调用方提供的
stdin，也不枚举或控制其它进程。GUI 作为管道写端 owner，在收到 `run_saved`、
`run_stopped`、`run_result` 或失败终态后必须关闭自己持有的写端，使已经停止接收控制的
子进程读取线程解除阻塞；只能关闭当前 `Popen` 返回的精确句柄。

非哈希阶段也只能在可解释边界响应生命周期动作。枚举在目录／文件领取边界停止，保留
临时对账证据但不把不完整树合并为当前业务结果；同会话继续后重新枚举并完整对账。元数据
阶段先完成当前文件、提交其当前结果，再停止领取下一文件；复扫先提交已经观察到的
unstable，再在继续后确定性重跑。旧 schema 3 函数只有在调用方显式传入控制回调时才启用
这些停点，默认调用的扫描语义和返回值不变。

数值进度写库与 GUI 通知至多每 500 ms 一次，阶段结束强制刷新；当前文件事件至多每
100 ms 一次。关闭「显示当前文件」时，生产者不调用当前文件回调，而不只是由 GUI 隐藏
文本。

## 六、lease 与进程身份

锁文件和数据库 session 使用相同 `lease_id`。锁文件写入 host、PID、进程启动 token、
session、获取／心跳／过期时间。

schema 4 的锁文件固定为 `<partial 绝对路径>.lease`。冻结的 partial、publish stem 和
event log 必须是输出目录内三个互不相同的绝对路径；仅位于同一目录不足以证明身份安全。
恢复与心跳以 SQLite URI `mode=rw` 打开既有 partial；文件在预览后消失时必须失败，禁止
普通 `sqlite3.connect(path)` 意外创建一个同名空库。损坏 lease 在预览中标为 `invalid`，
但只有用户明确进入恢复流程时才允许原子接管。

- 同 host、PID 存活且启动 token 相同：始终视为活 owner，即使心跳暂时过期也拒绝接管；
- 同 host、PID 存活但 token 不同：PID 已复用，旧 lease 可在明确恢复时接管；
- 同 host、PID 不存在：旧 lease 可在明确恢复时接管；
- 异机 owner：过期前拒绝，过期后才允许明确接管；
- 损坏锁：普通启动拒绝，只允许用户明确恢复；
- refresh 和 release 都必须匹配 lease ID，禁止一个窗口删除另一个窗口的锁。

锁刷新使用同目录临时文件加原子替换。实现和测试只操作当前任务的精确锁路径，不按进程
名枚举或终止任何其它进程。

Windows 上「PID 存活」不能只以 `OpenProcess` 成功判断，因为父端仍持有进程对象句柄时，
已经退出的进程仍可能被打开。实现必须查询 `GetExitCodeProcess`，只有
`STILL_ACTIVE` 才算存活；查询失败时保守视为存活并拒绝接管。进程启动 token 仍用于防止
PID 复用，二者缺一不可。

## 七、事件尾部恢复

数据库事件是权威证据。读取临时 JSONL 时：

- 完整合法行全部接纳；
- 仅当最后一行没有换行且 JSON 截断时，忽略这一尾行并记录 `truncated_tail`；
- 中间坏行或已经换行的坏尾行视为损坏，禁止静默跳过；
- 重放不得覆盖数据库中已存在的 session 序号。

## 八、封存与发布失败恢复

不得先在唯一 partial 中写 `published` 再尝试不可控移动。冻结流程为：

1. partial：`running → sealing`；
2. 完成复扫、外键和 SQLite 完整性验证；
3. partial：`sealing → sealed_unpublished`，提交并关闭；
4. 以 SQLite backup／受控复制创建同目录发布副本；
5. 仅在副本中写 `published`、最终路径模式和完成 session；
6. 关闭副本后计算摘要并以 no-clobber 原子发布；
7. 发布成功后删除 partial 和其精确 lease；
8. 任一步失败都保留原 partial 为 `sealed_unpublished` 或 `failed_recoverable`，不覆盖旧产物。

因此最终封存数据库自述为 `published`；发布失败的 partial 仍可重新发布，不必重扫档案。

最终路径不能原样写进数据库：文件名后缀来自数据库自身 SHA-256，写入最终文件名又会改变
该 SHA-256，形成不可解的自引用。数据库只记录冻结 stem 和摘要占位模式；实际文件名在
副本关闭并计算摘要后产生，Reader 再核对模式及文件名字节指纹。这与 schema 3 的
`snapshot_filename_pattern` 原则一致。

## 九、Reader 与投影

- schema 3 继续使用既有 fallback，未来表为 `unavailable/NULL`；
- schema 4 必须具备本文件全部新表和必要列，不能只改 `schema_version=4` 冒充；
- schema 4 封存输入只有 `snapshot_runtime.run_state=published` 才属于普通 sealed；
- `sealed_unpublished` 只能进入恢复／发布入口；
- Diff、核验和解析通过版本化投影读取，不在业务代码散落 schema 分支。

一次完成与多次恢复的业务投影必须相同。允许差异白名单仅包括：

- snapshot／session／attempt／lease 身份；
- 开始、结束、观察和性能时间；
- run events、attempt 次数、stall 与恢复原因；
- 工作文件名及最终时间戳名称；
- 不影响当前业务结果的工具运行时证据。

roots、dirs、entries 当前属性、当前有效哈希、规范化元数据、格式当前结果、错误分类和能力
结论不得因暂停或跨重启恢复而变化。

`Script_DAISY_Lib_DBS_05_Reader.iter_snapshot_business_projection()` 以 cursor 流式输出该
投影；`snapshot_business_projection_digest()` 使用带类型标记的逐行编码比较，不把整表
载入内存。BLOB 以实际字节长度和 SHA-256 进入投影，不能只相信库内声明摘要。

### 9.1 跨版本 Diff 投影

Diff 输入统一投影标识为 `daisy-diff-input-v1`。DBS-21 不再查询快照物理表；只有
`Script_DAISY_Lib_DBS_05_Reader.snapshot_diff_projection()` 可以把 schema 3／4 转成
以下稳定视图：快照身份、root、目录、文件、有效 SHA-256 及来源事件、File ID、原始元数据
摘要和能力状态。ExifTool 原始载荷的易变路径／访问时间过滤也属于 Reader 投影，不在 Diff
业务层复制 SQL 或解压规则。

文件和目录是结构对比的必要能力；哈希、原始元数据、格式校验、运行会话、尝试与性能是
可选证据。可选表缺失或列不兼容时，结构对比仍可进行，但对应能力必须是
`unavailable`／`incompatible`，不能读取残留行或推断一致。双侧能力折叠规则为：

- 双侧 `available`：`comparable`；
- 双侧 `empty`：`empty`，表示双方均有执行／结构证据但没有记录，不等于逐文件结果一致；
- 文件／目录的 `available` 与 `empty` 仍可做集合对比；
- 其它混合状态或任一侧 unavailable／incompatible：`unavailable`，并保留双侧原因。

哈希不足只产生 `hash_missing`／`insufficient`，不得污染 added／deleted 等结构结论。原始
元数据一侧缺失时 `metadata_changed=NULL`；`counts_json.metadata_evidence` 把 paired 但不可比
与 added／deleted 的 not applicable 分开。schema 4 的 `format_checks`、session、attempt 和
性能证据当前只进入能力说明，不映射为既有 11 种文件状态；否则会把工具／运行差异误报为
文件变化。未来若要增加对应 Diff 状态，必须另行升级输出契约。

`DIFF_DDL` 及 Diff 自身 `schema_version=3` 继续按 v1.4.1 冻结。跨版本来源只写入既有
`old_schema_version`／`new_schema_version`；投影标识、双侧能力和元数据证据摘要写入既有
`counts_json`，不新增表、列或状态枚举。整个对比前后，两份输入快照的 SHA-256、大小和
mtime 必须保持不变。
