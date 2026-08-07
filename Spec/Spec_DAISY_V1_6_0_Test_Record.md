# DAISY v1.6.0 测试记录

状态：已完成

记录日期：2026-08-06～2026-08-07

关联计划：[v1.6.0 实施计划](Spec_DAISY_V1_6_0_Implementation_Plan.md)

## 一、测试边界

- 所有合成数据、临时目录和报告均位于工作区 `.test_runtime\v1_6_0`；该目录不进入 Git。
- 除用户明确授权只读使用 `TEMP\测试文件` 中两份最小 DNG 外，不读取或改写用户
  `TEMP\`，不访问金样中记录的外部源路径；授权 DNG 只复制到工作区测试目录后使用。
- 不运行真实档案扫描、真实物理硬盘检测、SMART 采集、WinGet 安装或管理员重启。
- 不枚举、停止、附加或复用其它进程。测试只等待和回收本次启动的精确子进程句柄。
- 外部解析工具只处理工作区生成的最小合成文件；不向工具传入工作区外的数据路径。

## 二、阶段 0：基线冻结

阶段起点为 `5ea7800 docs: freeze v1.6.0 implementation plan`。当前发布身份仍为 v1.5.1，
本阶段没有提高应用版本、schema 或 metadata profile。

冻结值：

| 项目 | 值 |
|---|---|
| 快照 schema | `3` |
| 最低读取器 | `1.4.1` |
| 数据契约 | `daisy-snapshot-v3` |
| path key 规则 | `1` |
| v1.4.1 Git 标签 | `v1.4.1` |
| 快照 DDL SHA-256 | `9d162b401617a9242393ba2dcf32445be6437799553abb4c5923c527dc0963a7` |
| Diff DDL SHA-256 | `de5828bdf33955a6256e61eea3c390fb8a5c140c436b059145aab8d432eefa3f` |

固定契约位于
[`DBS_v1_4_1_Schema3_Contract.json`](../Script/Test/Fixtures/DBS_v1_4_1_Schema3_Contract.json)。
它固定快照／Diff 表集合、DDL 哈希、v1.4.1 能力、未来能力缺失状态和五类确定性构造
金样，不包含私人路径。两个 DDL 哈希已直接与 Git 标签 `v1.4.1` 中的原始常量复核一致。

## 三、阶段 1：统一只读 Reader

新增 `Script_DAISY_Lib_DBS_05_Reader.py`，提供：

1. 快照／Diff／partial／未知 SQLite 识别；
2. schema、封存状态、path key、SQLite quick check 和外键检查；
3. 实际表列清单及稳定能力 ID；
4. `available`、`empty`、`unavailable`、`incompatible`、`invalid` 五态；
5. 库内 `database_integrity` 声明与本次 `sqlite_integrity` 检查结果分离；
6. `PRAGMA query_only=ON` 的只读连接；
7. 读取 `hash_coverage`、config 和 manifest 执行证据，区分“执行后 0 行”与“未执行”；
8. 将“语义能力可选”和“物理投影可查询”分开，兼容旧固定入口而不伪造执行状态；
9. 供 GUI 使用的不抛 SQLite 堆栈 `probe_database()` 结果。

接入范围：

- DBS-21 快照准入、Diff 问题报告和命名预读；
- DBS-31 内容哈希核验；
- DBS-32 文件结构核验；
- DBS-41 快照／Diff 导出；
- DBS-11 增量哈希来源；
- 快照 `_Issues.md` 读取。

生产代码中除统一 Reader 外已无其它 `mode=ro` SQLite 直连。扫描 partial 和 Diff 输出仍
按原设计使用读写连接，不属于封存数据库消费者。

## 四、阶段 1 测试结果

### 4.1 Reader 专项

`Script_DAISY_Test_DBS_Reader.py` 共 16 项，全部通过：

- v1.4.1 快照与 Diff 身份、表集合和 DDL 哈希；
- 中文及 `#` 路径的只读 URI；
- 输入数据库探测前后 SHA-256 不变；
- query-only 写入拒绝；
- partial 识别与封存消费者拒绝；
- schema 由契约决定，不依赖应用版本字符串相等；
- schema 4 在未实现适配前明确拒绝；
- 模块表缺失为 `unavailable`，其它模块仍可读；
- 表存在但必要列缺失为 `incompatible`；
- 真实空表为 `empty`／`0`，未记录能力为 `unavailable`／`NULL`；
- Quick／No-Hash 未执行的哈希、元数据和 raw payload 不伪装成空结果；
- Full raw payload 可解压并核对摘要，重复内容保持两个独立条目；
- error／unstable／枚举缺口进入 Issues，单纯 unsupported 只保留计数、不列明细；
- manifest 损坏产生告警，并可从合法 config 明确降级；
- Issues、DBS-21／31／32／41 执行前后，v1.4.1 输入库 SHA-256、大小和 mtime 均不变。

### 4.2 消费者定向回归

既有消费者定向回归 46 项全部通过，覆盖：

- 快照封存与 Issues；
- 增量哈希来源与多级来源追踪；
- DBS-31 CLI 与问题报告；
- Diff 准入及全部金样；
- 快照／Diff CSV、XLSX 和摘要导出。

另执行 GUI 自检文件清单与技术规格守卫 2 项，全部通过。

补齐执行证据判定后，重新运行受影响的 Quick、Diff、增量来源、Issues 和导出回归
48 项，全部通过；其中 Quick 快照继续可以参与 Diff，旧固定导出入口的业务投影未改变。

### 4.3 完整回归

第一次完整回归运行 293 项，292 项通过；唯一失败是新增 Reader 尚未加入测试中的显式
Lib 文件白名单。该失败没有涉及运行逻辑、数据库结果或 GUI。

补充白名单后重新运行：

```text
Ran 293 tests in 91.607s
OK
```

为所有封存数据库消费者补上按模块的能力门槛后，第三次重新运行完整回归：

```text
Ran 293 tests in 90.247s
OK
```

自审发现 Quick 未执行模块可能被误写为 `empty/0` 后，补齐五类确定性金样和执行证据
判定，再次运行最终阶段回归：

```text
Ran 297 tests in 87.172s
OK
```

最后将语义能力状态与物理投影可查询性拆分，并通过旧固定导出夹具验证后，最终运行：

```text
Ran 297 tests in 89.411s
OK
```

覆盖范围包括 DBS、STG、No-clobber、快照生成／封存、续传现行边界、哈希、元数据、
格式核验、Diff、报告导出、GUI 参数与既有 Tk 用例。失败 0，跳过 0。

## 五、阶段结论与未完成项

阶段 0～2 已完成。证据证明统一能力探测层可只读接纳 v1.4.1/schema 3，并且没有改变
schema 3 快照／Diff DDL 或既有输出投影。

以下内容仍未完成，不能因 Reader 和后文的状态层已落地而宣称 v1.6.0 完成：

- schema 4 状态层尚未接入 DBS-11 生产扫描入口；
- 哈希隔离 worker、运行时暂停安全边界与动态无进展 timeout；
- Full 可选格式校验、核验合并和跨新 schema Diff；
- 数据库解析、Issues 新板块和 GUI 四入口；
- v1.6.0 最终版本号、发布回归、合并、推送与标签。

## 六、阶段 2：行为保持型重构（完成）

第一步新增 `Script_DAISY_Lib_DBS_06_Verify.py`，仅抽取 DBS-31／32 原来重复的：

- 快照路径和文件名高 32 bit 指纹准入；
- 统一 Reader 能力门槛；
- 快照 UUID、哈希覆盖和 root 身份；
- 当前 root 映射、目录存在性检查和逻辑／物理路径；
- 只读连接在成功及异常路径上的关闭责任。

DBS-31／32 仍保留原命令、参数、哈希抽样、格式判据、输出字段、报告文件和退出码。
新增 5 项共用模型测试全部通过；原有哈希核验、格式核验和 v1.4.1 消费者定向回归
14 项全部通过。加入新测试后的完整回归结果为：

```text
Ran 302 tests in 91.136s
OK
```

失败 0，跳过 0。这是阶段 2 第一步的结果，后续 Parse 拆分记录如下。

第二步将 DBS-41 的数据库解析、CSV 和 XLSX 实现移至
`Script_DAISY_Lib_DBS_07_Parse.py`，并新增：

- 快照／Diff 稳定模块注册表、能力要求和 schema 3 fallback 声明；
- 保持原页面顺序与 SQL 投影的 `ParsePageSpec`；
- 保持 UTF-8 无 BOM、LF 和完整值的 `CsvQueryWriter`；
- 从技术 CSV 生成既有工作簿的 `LegacyExcelWriter`；
- 仅保留旧 CLI、退出码和 Python 函数别名的 DBS-41 Module。

Parse 专项 5 项和既有导出／v1.4.1 消费者回归 7 项全部通过。另从检查点 `11ed71b`
只读加载迁移前实现，对同一合成输入做逐字节差分：快照报告 18 个文件、Diff 报告 8 个
文件均完全一致，输入快照／Diff SHA-256 均不变。DBS-31／32 业务服务尚未成为薄入口，
因此阶段 2 继续标记为进行中。加入 Parse 专项后的完整回归结果为：

```text
Ran 307 tests in 94.573s
OK
```

失败 0，跳过 0。

第三步把 DBS-31／32 的业务实现移入 `Script_DAISY_Lib_DBS_06_Verify.py`：

- DBS-31 的 stat、哈希抽样／全量核验和 JSON／Issues 报告写出成为共用服务；
- DBS-32 的 ZIP、PDF、OLE、7-Zip、ExifTool、ffprobe 判据和全部报告写出成为共用服务；
- 旧 DBS-31／32 脚本分别由 223／430 行缩为 93／111 行，只保留 CLI、退出码和兼容符号；
- `validate_legacy_office` 仍保留旧 `validate_sevenzip` 运行时替换点。

共用 Verify、旧 CLI、哈希注入、格式判据和混合目录端到端定向回归 19 项全部通过。
另从检查点 `05225e0` 只读加载迁移前实现，在同一合成快照上做差分：DBS-31 的 JSON
与 `_Issues.md` 逐字节一致；固定报告随机名、时间戳和计时后，DBS-32 的 JSON、CSV、
Markdown、Info CSV 共 4 个文件逐字节一致；输入快照 SHA-256 均不变。完整回归结果为：

```text
Ran 308 tests in 95.871s
OK
```

失败 0，跳过 0。阶段 2 第 1～3 项完成；大型旧测试文件拆分尚未完成。

第四步按 AST 类边界迁移既有测试，不重写业务断言：

- `TestVerifyHashPatrol`、`TestValidators`、`TestValidateSnapshot` 移入 Verify 专项；
- `TestExportSnapshot`、`TestExportDiff` 移入 Parse 专项；
- 类名和测试方法名不变，临时目录改为工作区内的 `_RUNTIME_ROOT`；
- `Script_DAISY_Test_Unit.py` 从 6766 行降至 6230 行；Verify／Parse 专项分别包含
  19／11 项测试。

两个专项合计 30 项全部通过。完整发现式回归的用例总数仍为 308，证明没有漏测或重复
执行：

```text
Ran 308 tests in 91.228s
OK
```

失败 0，跳过 0。阶段 2 全部完成；阶段 3 的后续结果如下。

## 七、阶段 3：schema、session 与恢复状态机（完成）

新增 `Script_DAISY_Lib_DBS_08_State.py`，但尚未切换现行 DBS-11 扫描入口。该层提供：

- schema 4 对 schema 3 业务表的超集，以及 `run_sessions`、`snapshot_runtime`、
  `stage_checkpoints`、`run_state_events`、`entry_attempts`、`read_performance` 和
  `format_checks`；
- `run_state + state_revision` compare-and-swap、暂停／保存退出／停止的不同语义、
  新 resume session 和异常 attempt 回到文件边界；
- host、PID、进程启动 token 与 lease ID 联合所有权，活 owner、死 owner、PID 复用、
  异机未过期／过期、损坏锁和非 owner refresh／release；
- 仅容忍最后一个未换行截断 JSON 的事件日志读取；
- `sealed_unpublished` 原件、SQLite backup 发布副本、副本内 `published`、真实副本摘要
  命名和 no-clobber 发布；失败不覆盖目标并保留原 partial；
- Reader 对真正 schema 4 结构、粗粒度状态、data／resume／projection contract、发布文件名
  模式和字节指纹的只读验证；
- 不含 session／attempt／lease／观察时间的流式业务投影与类型稳定摘要。

自审发现，若数据库内保存含自身 SHA-256 后缀的最终文件名，写入该文件名会再次改变
数据库 SHA-256，形成不可解的自引用。因此契约改为保存
`published_path_pattern=发布 stem_<SHA256-high32-uppercase>.sqlite`；实际文件名只在发布
副本关闭后由真实字节摘要产生。该原则与 schema 3 的 filename pattern 一致。

冻结哈希：

| 项目 | SHA-256 |
|---|---|
| schema 3 `SNAPSHOT_DDL` | `9d162b401617a9242393ba2dcf32445be6437799553abb4c5923c527dc0963a7` |
| schema 4 `SNAPSHOT_DDL_V4` | `c8e3bbbd899818bc9653fcc5a27594b3a650d44643e838c23d4db4f9c66e1d34` |

状态层专项 20 项、Reader 专项 21 项全部通过。状态层覆盖非法／旧 revision／错误 session
原子拒绝、三种退出语义、attempt 事务回滚、性能候选措辞、格式历史、异常恢复、JSONL、
lease 分类及文件操作、发布成功／失败，以及一次完成与保存退出后恢复的业务投影等价。
Reader 新增 5 项 schema 4 用例，仍保留全部 v1.4.1 只读不变测试。

阶段 3 完整发现式回归结果：

```text
Ran 333 tests in 94.343s
OK
```

失败 0，跳过 0。既有 Verify 19 项、Parse 11 项及真实 Tk 构造、窗口／字号矩阵、滚动和
下拉选择测试均继续通过。现行 `SCANNER_VERSION=1.5.1`、`SCHEMA_VERSION=3` 与 schema 3
扫描 DDL 没有在本阶段改变；生产切换必须等阶段 4 worker 与安全暂停边界完成。

## 八、阶段 4：哈希 worker 与文件边界（第一检查点）

新增 `Script_DAISY_Test_DBS_Hash_Worker.py` 和工作区内可控 worker 夹具。当前检查点覆盖：

- 0、9 GiB 整数边界和多档大文件的动态无进展 timeout 公式；
- worker 启动握手、持续进展超过 timeout 总时长、30 秒 stall 与 timeout 的独立语义；
- 用户／高级默认策略的原子决策，以及默认继续等待不会静默跳过；
- 永久阻塞、跳过、停止、暂停、无结果和崩溃均只通过本次创建的精确进程句柄回收；
- 成功、timeout、崩溃和重试的当前哈希、errors、attempt、性能摘要同事务更新；
- 保存退出和停止的不同恢复提示，新 session 从当前文件起点重新尝试并保留历史；
- Full 和增量复用的逐文件阶段、三种处理集合、checkpoint、默认隐藏当前文件及事件限频；
- schema 3 `hash_one_file`、StallWatchdog、既有哈希阶段、增量复用和独立抽验不变。

最终代码的 worker 专项独立重复两轮，另在完整发现式回归中执行一轮；三轮均通过：

| 批次 | 结果 | 用时 |
|---|---:|---:|
| worker 专项 A | 17／17 | 2.615s |
| worker 专项 B | 17／17 | 2.545s |
| 完整发现式回归（含同一 17 项） | 350／350 | 96.991s |

状态事务专项在加入当前结果回调后复测 20／20，通过，用时 0.488s；既有 schema 3 哈希
定向回归 15／15，通过。上述批次失败 0，跳过 0。测试没有枚举其它进程，没有按进程名
终止程序，没有读取用户 `TEMP\`，只回收测试持有的 worker 句柄；临时数据均位于
`.test_runtime\v1_6_0`。

本检查点不宣称阶段 4 已整体完成。现行 DBS-11 仍生成冻结的 schema 3；生产编排、GUI
timeout 三动作、恢复入口、意外终止后的端到端续传和其 Tk 状态矩阵仍待后续检查点。

### 8.1 schema 4 运行文件与 lease（第二检查点）

新增 `Script_DAISY_Lib_DBS_09_Run.py`；首轮包含 12 项运行生命周期测试，提交前安全审查
再增加 2 项，最终为 14 项，覆盖：

- schema 4 partial 独占预留、同身份 lease、root 卷信息和失败后的精确自产物清理；
- 已存在 partial／lease 的 no-clobber，以及只读预览前后数据库和 lease 字节不变；
- schema 3 候选只读拒绝，不创建 schema 4 lease、不改变旧库 SHA-256；
- 活 owner 拒绝接管、保存退出后建议恢复、stopped 仅手动恢复；
- 同会话暂停后 owner 消失时，旧 session 记为 abandoned，再创建新 resume session；
- 损坏 lease 的可见状态与明确接管，以及接管后 active session／状态／配置二次核对；
- lease 文件与数据库 session 使用同一时间和 lease ID 心跳，后台心跳可立即停止；
- partial 在预览后消失时心跳拒绝且不创建同名空 SQLite。

自审构造失败清理时发现，原输出身份校验只验证三个路径同目录，没有拒绝 event log 与
partial 是同一路径。首个针对此条件的测试因而意外成功并留下一个仍打开的测试连接；测试
报告了 Windows 文件占用，没有被重试掩盖。实现随后增加“三个路径必须互异”约束，并只
删除报错明确指出、位于 `.test_runtime\v1_6_0\run_lifecycle` 的本轮夹具目录。修复后首轮
运行生命周期专项 12／12，通过；状态层与生命周期联合 32／32，通过，并把
`ResourceWarning` 提升为错误确认无连接泄漏。

第一次完整回归为 361／362；唯一失败是 Lib 文件集合守卫尚未登记
`Script_DAISY_Lib_DBS_09_Run.py`。更新显式白名单及技术规格后，定向守卫 1／1 通过，
第二次完整发现式回归结果为：

```text
Ran 362 tests in 93.310s
OK
```

提交前审查进一步发现，恢复／心跳若使用普通 `sqlite3.connect(path)`，路径在竞态中消失
可能生成同名空库；损坏 lease 也会被预览提前拒绝，无法进入规范要求的明确恢复。改用
`mode=rw`、增加损坏 lease 分类及接管后二次核对后，最终生命周期专项 14／14 通过，最终
代码又独立重复两轮 14／14（0.284s、0.287s），最终完整发现式回归为：

```text
Ran 364 tests in 92.415s
OK
```

随后仅收窄损坏 lease 的异常捕获范围，避免把调用方非法时间参数误记为锁损坏；同一最终
代码的生命周期专项再通过 14／14（0.281s），并以完整回归确认最终字节：

```text
Ran 364 tests in 92.091s
OK
```

最终批次失败 0，跳过 0。上述完整回归均使用工作区内临时目录；没有读取用户 `TEMP\`，
没有运行真实扫描或外部工具采集，也没有枚举、附加或停止其它进程。

### 8.2 控制协议与同会话暂停循环（第三检查点）

本检查点在 `DBS_09_Run.py` 增加有界 UTF-8 JSONL 控制输入、严格递增序号、命令回执和
运行路由；在哈希 worker 控制对象中增加当前 PID 绑定的 timeout 决策。覆盖：

- 五类控制消息的往返编码，以及无效 UTF-8、多行、未换行尾帧、超长、未知协议和非法
  字段拒绝；
- 控制队列上限、重复／倒序 sequence、回调投递和不关闭调用方输入流；
- 运行中暂停／保存退出／停止 first-wins，paused 状态只接受一次后续动作；
- timeout 用户决定与高级默认决定原子竞争，决定只在当前 worker 的 stall／threshold
  窗口内有效，旧 PID、过早／过晚消息和第二次决定均不能生效；
- stall 后可在阈值前选择跳过，精确 worker 被回收且不会伪造 threshold 事件；
- 暂停后继续在同 session 从当前文件起点重试，历史 attempt 为 cancelled／succeeded；
- 暂停后保存以 CAS 记录 `paused_saved_for_exit`，结束 session 并保留 pending 文件；
- 暂停后停止进入 `stopped + manual_only`，与保存退出的建议恢复语义不同。

同一最终代码的关键专项重复结果：

| 批次 | 结果 | 用时 |
|---|---:|---:|
| 状态机专项 | 21／21 | 0.491s |
| 哈希 worker 专项 | 23／23 | 2.694s |
| 运行编排专项 | 27／27 | 0.473s |
| 状态／worker／运行联合 A | 71／71 | 3.571s |
| 状态／worker／运行联合 B | 71／71 | 3.570s |
| 完整发现式回归 A | 384／384 | 93.752s |
| 完整发现式回归 B | 384／384 | 93.937s |

全部批次失败 0，跳过 0，并启用 `ResourceWarning` 即错误。完整回归显式把 `TEMP`、`TMP`
和 `TMPDIR` 分别指向工作区 `.test_runtime\v1_6_0\full_stage4_control_final\temp` 与
`.test_runtime\v1_6_0\full_stage4_control_repeat\temp`；没有读取用户 `TEMP\`，没有运行
真实扫描或硬盘检测，也没有枚举、附加、复用或停止其它进程。现行 DBS-11 仍为
v1.5.1／schema 3 生产入口，本检查点只完成 v1.6.0 运行层能力，不能宣称阶段 4 或生产
切换已经完成。

### 8.3 枚举、元数据与复扫安全边界（第四检查点）

新运行层增加三个受控阶段包装；Core／Meta 的旧函数仅在末尾增加默认关闭的可选回调。
专项覆盖：

- 枚举收到暂停后不合并临时半成品，root 保持 pending；同 session 继续后完整重跑并得到
  两个文件，checkpoint 最终为 completed；
- 元数据在当前文件完成后暂停，继续时只处理仍为 pending 的文件；最终总数从数据库全局
  状态重建为 2／2，而不是错误显示本轮剩余 1／1；
- 元数据当前文件事件默认不生产，打开后按 100 ms 限频；数值进度与 checkpoint 按
  500 ms 限频并在结束时强制刷新；
- 元数据与复扫在领取新工作前可保存退出或停止，未启动伪造的外部工具路径；
- 保存退出仍为 `paused + suggest + saved session`，停止仍为
  `stopped + manual_only`；
- 旧枚举、复扫、归档元数据、Raw／Normalized、视频 GPS 专项在未传控制回调时保持通过。

同一代码的受控运行专项为 33／33；受控运行加旧扫描路径定向回归连续两轮均为 42／42
（0.874s、0.851s）。完整发现式回归结果：

```text
Ran 390 tests in 93.453s
OK
```

失败 0，跳过 0，并启用 `ResourceWarning` 即错误。完整回归的 `TEMP`、`TMP`、`TMPDIR`
均指向工作区 `.test_runtime\v1_6_0\full_stage4_boundaries\temp`；未读取用户 `TEMP\`，
未运行真实扫描、硬盘检测或外部工具采集，未枚举、附加、复用或停止其它进程。生产 CLI
与 GUI 仍未切换，阶段 4 继续实施。

### 8.4 扫描证据流水线（第五检查点）

运行层已把 schema 4 的枚举、哈希、元数据和复扫组合为内部证据采集流水线。它只读取
当前 active session 冻结的配置和工具记录，不接受恢复时临时替换参数；工具记录同时保存
路径、版本和探测来源，`snapshot_info` 仍只保存稳定版本字符串。当前检查点覆盖：

- Full 按枚举、哈希、元数据、复扫顺序执行，每阶段写入 schema 4 checkpoint；当前文件
  事件默认不生产，用户显式打开后才允许产生；
- Quick 只枚举和复扫，哈希与元数据标为 skipped，未提供工具也能完成，并以 mock 证明
  哈希 worker 和元数据外部工具均未启动；
- 哈希阶段保存退出后释放精确 lease，再由新 session 恢复；当前文件从头重试，已提交
  项目不重做，attempt 历史保留 cancelled／succeeded；
- 输出目录是扫描根的子目录时排除整个自产物子树；输出目录等于扫描根时只排除本次
  partial、WAL／SHM、lease 和 event log，普通档案文件仍被登记；
- Quick 若冻结配置启用哈希或格式校验，会在枚举前拒绝；Full 的格式校验阶段尚未接入时，
  对 sample／all 配置显式拒绝，不能遗漏阶段后伪装完成；
- schema 3 的 DDL、版本常量和现行扫描入口未改；流水线尚不执行格式校验、独立抽验、
  Issues、封存或发布，也尚未注册生产 CLI。

流水线与状态层定向测试为 28／28，通过；其中流水线端到端 7 项覆盖 Full、Quick、配置
拒绝、两种输出目录关系及保存退出后恢复。最终代码的完整发现式回归连续两轮结果为：

| 批次 | 结果 | 用时 |
|---|---:|---:|
| 完整发现式回归 A | 397／397 | 93.696s |
| 完整发现式回归 B | 397／397 | 94.224s |

全部批次失败 0，跳过 0，并启用 `ResourceWarning` 即错误。两轮完整回归分别把 `TEMP`、
`TMP`、`TMPDIR` 指向工作区 `.test_runtime\v1_6_0\full_pipeline_a\temp` 和
`.test_runtime\v1_6_0\full_pipeline_b\temp`；测试只使用合成小文件和受控工具替身，未读取
用户 `TEMP\`，未扫描真实硬盘，未枚举、附加、复用或停止其它进程。阶段 4 仍需完成
生产入口、独立抽验、封存／发布和 CLI／GUI 控制接线后才可关闭。

## 九、阶段 5：共享格式判据与 Full 可选格式校验（第一检查点）

新增 `FormatValidationSession` 作为扫描与未来统一核验共用的文件级判据层；现行 DBS-32
入口和报告循环尚未改写，因此旧 CLI 输出、退出码和 v1.4.1 只读语义保持原状。共享层与
schema 4 运行层当前提供：

- 内置 ZIP／OOXML、PDF、OLE、7-Zip、ExifTool 和 ffprobe 判据按文件类型惰性选择，工具
  只在对应格式首次出现时启动；未知类型不启动外部工具；
- `off` 保持 `format_checks` 空表并把 checkpoint 标为 skipped；`sample` 和 `all` 分别
  写入 sample／full 覆盖，默认关闭不改变 Full 基线；
- 抽样使用快照 UUID 的确定性种子，0% 明确选择 0 条，100% 全取，小样本沿用既有至少
  100 条策略；布尔、负数、超过 100 和非有限值在写入任何结果前拒绝；
- 每个已选文件建立 `entry_attempts(stage='format')` 与当前 `format_checks`，校验前后均
  比较 size／mtime；valid、invalid、timeout、error、unstable 和 unsupported 分开记录；
- unknown／unsupported 只进入统计和数据库能力证据，不写入 `errors`，以后 Issues 渲染
  只能显示总数，不能列路径或冒充问题；
- 格式阶段在文件边界支持暂停／继续、保存退出和新 session 恢复；当前文件事件默认关闭，
  显式打开后按 100 ms 限频；
- Full 证据流水线已在元数据后、复扫前接入可选格式阶段；Quick 启用格式仍在枚举前拒绝；
  Full 在格式阶段保存退出后，可关闭精确 lease、重开、重跑枚举并完成剩余格式项与复扫。

定向结果：共享格式／运行／旧核验联合 33／33，通过；状态、Reader 与运行联合 87／87，
通过。最终代码的完整发现式回归连续两轮结果为：

| 批次 | 结果 | 用时 |
|---|---:|---:|
| 完整发现式回归 A | 405／405 | 94.876s |
| 完整发现式回归 B | 405／405 | 95.747s |

全部批次失败 0，跳过 0，并启用 `ResourceWarning` 即错误。两轮完整回归分别把 `TEMP`、
`TMP`、`TMPDIR` 指向工作区 `.test_runtime\v1_6_0\full_format_a\temp` 和
`.test_runtime\v1_6_0\full_format_b\temp`；没有读取用户 `TEMP\`，没有扫描真实硬盘，
没有枚举、附加或停止其它进程。

本检查点尚不关闭阶段 5：统一“核验”入口、独立哈希抽验的 schema 4 控制包装、生产
封存入口和 GUI 尚未完成。暂停期间源文件被删除／改名还需要独立的一致性设计；不能为
绕过外键而删除 attempt 历史，也不能把未完成边界写成已经支持。Issues 报告层的后续
检查点如下。

### 9.1 Issues 分板块报告与发布联动（第二检查点）

新增 `Script_DAISY_Lib_DBS_10_Issues.py`，并保持现行 schema 3 Core 报告器与扫描入口
不变。新报告层当前提供：

- 固定六板块标题与顶部状态表，严格区分已执行后的 `0` 和未执行／未记录的 `NULL`；
- 问题文件数、底层记录数、诊断总文件数、需呈现诊断文件数、展示数和完整证据表分别
  统计，不再把文件数近似成诊断数；
- unknown／unsupported／unrecognized format 仅显示去重总数，不显示路径，也不单独
  生成 Issues；已知损坏、timeout、unstable 和工具错误不受该过滤影响；
- 普通 warning、`[minor]` warning 与 validation 折叠，复用既有格式核验严重 warning
  判据；同一文件至少 100 条折叠 warning 时只生成一个有界待复核候选；
- `Copy1` 到大序号统一显示为 `Copy#`，需要处理、待复核候选、信息性诊断按顺序呈现，
  同一行合并当前元数据／哈希／格式状态、错误依据和建议操作；
- 仅高置信度读取性能候选进入报告，低置信度仅留库；报告显示大小、读取量、总耗时、
  活跃读取、平均吞吐、stall、最终偏移和 session，并明确不能据此认定物理坏区；
- schema 4 发布可在只读发布副本上生成最终文件名对应的 sidecar；分析前后摘要必须一致，
  sidecar 为 UTF-8 无 BOM／LF，报告冲突或 SQLite 发布失败均遵守 no-clobber 并保留 sealed
  partial。

定向测试覆盖 schema 3／4、Quick／Full、格式开／关、仅 unsupported、真实 JPEG 错误、
普通／minor／validation／严重／高密度 warning、`CopyN`、明细上限、性能候选四组合、
旧失败 attempt 后成功、sidecar 编码和两个发布失败路径。结果如下：

| 批次 | 结果 | 用时 |
|---|---:|---:|
| Issues 专项 | 10／10 | 约 0.6s |
| Issues／发布／既有格式核验联合 | 35／35 | 3.723s |
| 完整发现式回归 A | 416／416 | 98.527s |
| 完整发现式回归 B | 416／416 | 100.252s |

全部批次失败 0，跳过 0，并启用 `ResourceWarning` 即错误。两轮完整回归分别把 `TEMP`、
`TMP`、`TMPDIR` 指向工作区 `.test_runtime\v1_6_0\full_issues_a\temp` 和
`.test_runtime\v1_6_0\full_issues_b\temp`；未读取用户 `TEMP\`，未扫描真实档案或硬盘，
未枚举、附加、复用或停止其它进程。v1.4.1/schema 3 输入在新报告分析前后 SHA-256、
大小和 mtime 不变。

本检查点只完成报告分析、渲染和发布事务能力。读取性能离群候选的实际分组计算、生产
扫描的封存／发布编排、统一 CLI／GUI 仍未接入，不能把合成的高置信度候选渲染测试写成
性能异常诊断已经完整落地。

### 9.2 独立抽验、性能分析与内部生产发布链（第三检查点）

schema 4 扫描内部链已补齐复扫后的独立哈希抽验、性能候选计算、封存和发布；现行
DBS-11 CLI／GUI 仍保持 schema 3，尚未切换。当前检查点实现并验证：

- 独立抽验按文件创建一个本任务持有的 PowerShell `Get-FileHash` 进程，使用 UTF-8 路径
  令牌和 UTF-16LE `-EncodedCommand`；30 秒 stall、90 秒／9 GiB 动态无进展阈值、继续
  等待／跳过记录／停止续传、暂停和保存均绑定当前 PID，只终止并等待该精确句柄；
- 抽验前后核对 size／mtime。首轮摘要不一致时，主哈希 worker 与独立 PowerShell 各重算
  一次；双方回到原摘要才记为偶发恢复，否则 `verify_hash` attempt、当前 hash、entry 和
  errors 一致标为 unstable。工具错误、timeout、源变化和 mismatch 分开记录；
- 暂停后的 verify attempt 记 cancelled，同 session 继续后从文件起点重试；Quick／No-Hash
  不要求 PowerShell，并把 verify_hash checkpoint 明确写为 skipped；
- 性能分析只消费当前成功的 computed 主哈希 attempt，排除 reused、独立抽验和历史尝试；
  比较组固定为同卷、同扩展名（无扩展名时同 media kind）和
  `round(log2(size_bytes))` 大小带，至少 8 个／组且文件至少 1 MiB。吞吐中位数、MAD、
  中位数比例和 stall 阈值共同区分 low／high；低置信度只留库，高置信度进入同一 Issues；
- `run_scan_to_publication` 串联证据阶段、抽验、性能分析、封存和发布。前置 checkpoint、
  running attempt、pending／processing 当前结果均有硬拒绝；扫描专用 verify_format 明确
  skipped。manifest、计数和事件内嵌后执行 SQLite／外键检查，再进入
  `sealed_unpublished`；
- 发布副本内完成 publish checkpoint、运行状态和 session，摘要后缀基于关闭后的最终
  字节。Issues 在发布副本上只读生成并复核摘要，无问题不创建；冲突不覆盖，发布失败保留
  sealed partial 与精确 lease，封存前失败进入 `failed_recoverable`；
- 成功后只清理本次 partial、精确 lease 和冻结 event log。测试覆盖中文文件名、UTF-8／LF
  事件内嵌、Full、Quick、真实 mismatch、性能 high＋low、发布冲突、阶段残留拒绝和损坏
  事件日志故障注入。

真实 Windows PowerShell 5.1 烟雾测试只读工作区 `README.md`，PowerShell 摘要与 Python
SHA-256 相同，进程退出码为 0 且精确句柄已回收；没有读取任何真实档案或用户临时目录。
定向与第一轮完整回归结果：

| 批次 | 结果 | 用时 |
|---|---:|---:|
| 哈希／运行／状态／发布／Issues 联合 | 121／121 | 9.448s |
| 完整发现式回归 A | 436／436 | 99.817s |
| 完整发现式回归 B | 436／436 | 100.494s |

全部批次失败 0，跳过 0，并启用 `ResourceWarning` 即错误。两轮完整回归把 `TEMP`、
`TMP`、`TMPDIR` 分别固定到工作区 `.test_runtime\v1_6_0\full_publication_a\temp` 和
`.test_runtime\v1_6_0\full_publication_b\temp`；未读取用户 `TEMP\`，未扫描真实档案或
硬盘，未枚举、附加、复用或停止其它进程。schema 3 DDL、
`SCANNER_VERSION=1.5.1`、`SCHEMA_VERSION=3` 和现行 DBS-11 入口未改。

本检查点完成的是可供新入口调用的 schema 4 内部生产链。现行 DBS-11 CLI、统一核验
CLI、GUI timeout／恢复／发布界面和 sealed partial 的重启后发布恢复入口仍待后续接线；
因此阶段 4／5 和 v1.6.0 整体均未完成，也不能更新 README 为已发布功能。

### 9.3 统一扫描生产 CLI（第四检查点）

新增不占 DBS 编号的 `scan` 编排命令和
`Script_DAISY_Module_DBS_10_Scan.py`。旧 `full-scan`／`quick-scan` 暂不改写，避免
兼容包装和 GUI 尚未完成时改变既有 schema 3 自动化。新入口已实现：

- 新建 Full／Quick schema 4 partial；Full 默认格式校验关闭，Quick 硬拒绝哈希、元数据、
  格式校验及外部工具参数；`Fmt-Sample`／`Fmt-All` 只用于新 filename layout 3；
- 冻结 30 秒 stall、`max(90, ceil(size / 9 GiB) * 90)` 无进展规则和默认处置；恢复不能
  覆盖冻结配置，哈希重试范围和当前文件显示只作为当前 session 的显式运行选项；
- 恢复先只读识别状态、lease、roots、config 和 tools。有效本机／异机 owner 在访问源根或
  工具冒烟前拒绝；stopped 只有 `--manual-resume` 能接管；冻结工具路径或版本改变即拒绝；
- `--control-stdin` 使用现有 `daisy-control-v1` 严格 UTF-8 JSONL inbox，控制回执和拒绝均
  进入 GUI 事件；stdin 由调用方持有，扫描入口不关闭；
- 只刷新本任务精确 partial／lease。心跳错误请求保存退出，封存前通过 `before_seal`
  最多等待 10 秒并确认线程退出；未退出即拒绝封存。事件日志创建或写入失败也拒绝开始／
  封存，避免静默丢失运行证据；
- 阶段事件转换为 9 段 GUI 进度，当前文件和 threshold 仍为独立结构化事件；成功、保存
  退出和停止的退出码分别为 0、75、130；失败只保留本任务 partial 和可审计恢复边界；
- 事件日志逐次短打开写入，封存完成后不再重建已删除日志。发布成功后 partial、lease、
  event log 和 publishing staging 均无残留。
- `sealed_unpublished` 使用只发布恢复，不进入普通 resume／扫描链。恢复 session、失败和
  重试写入 `run_state_events`，manifest 同步 session／重试计数并明确未复扫源目录。测试
  在第一次发布冲突后删除源夹具，第二个进程仍从 sealed partial 完成发布并保留原条目。

定向验证全部把 `TEMP`／`TMP` 指向工作区 `.test_runtime\v1_6_0` 下的独立目录：

| 批次 | 结果 | 用时 |
|---|---:|---:|
| 统一扫描 CLI 专项 | 10／10 | 1.525s |
| CLI＋State＋Run＋Publication 联合回归 | 90／90 | 7.351s |
| CLI 模块清单断言 | 1／1 | 0.001s |
| 完整发现式回归 A | 446／446 | 101.143s |
| 完整发现式回归 B | 446／446 | 101.572s |

专项以工作区内中文小文件真实启动一个受控 Quick 子进程，验证源 SHA-256 不变、schema 4
最终库 published、第二个 resume session 完成以及无 partial／lease／event／staging 残留；
另验证 stopped 未获明确授权时数据库 SHA-256 不变。两轮完整回归分别固定到工作区
`.test_runtime\v1_6_0\scan_cli_checkpoint_a\temp` 和
`.test_runtime\v1_6_0\scan_cli_checkpoint_b\temp`，均失败 0、跳过 0，并把 `ResourceWarning`
视为错误。测试没有运行 Full 外部工具采集、真实硬盘检测或工作区外扫描，也没有枚举、
停止、附加或复用其它进程。GUI 接线和突然终止控制子进程端到端故障注入仍是阶段 4 的
剩余阻断项。

### 9.4 现有扫描 GUI 生产接线（第五检查点）

提交 `569aebc feat: connect resumable scan gui controls` 已把现有 Full／Quick 页面接到统一
`scan --mode full|quick --control-stdin` 入口，并验证以下边界：

- Full／Quick 使用 schema 4 新建／恢复链；旧 `full-scan`／`quick-scan` 命令保持不变；
- 暂停／继续、保存退出、停止、timeout 三决定和运行中关闭只操作本窗口持有的精确子进程
  与 stdin；
- 保存退出只持久化 task key 和 partial 恢复指针，下次启动先显示恢复卡片，用户确认后
  才填入页面；普通表单值不恢复，停止任务不主动建议恢复；
- 开始任务后收起设置并展开进度／日志；Full 的格式校验默认关闭，动态 timeout 默认处置、
  抽样比例和当前文件开关位于高级设置；
- Windows lease 活性探测修正为检查精确 PID 的 `STILL_ACTIVE`，测试只终止自己启动的
  Quick 子进程，没有枚举其它进程。

定向结果为 State 22／22、Run 51／51、统一扫描 CLI 10／10、GUI scan 19／19；随后完整
发现式回归为 465／465。测试数据和 `TEMP`／`TMP` 均位于工作区
`.test_runtime\v1_6_0`；未读取用户 `TEMP\`，未扫描真实硬盘或工作区外档案。

这一检查点只连接现有 Full／Quick 页面，不等于扫描／对比／核验／解析四入口 GUI 已经
完成。代码发布身份仍是 `SCANNER_VERSION=1.5.1`，因此当前 GUI 标题显示 v1.5.1 是预期
事实；最终版本号尚未更新。

## 十、阶段 5：统一核验核心（第一检查点）

提交 `68ef716 feat: add unified verification core` 新增只读统一核验业务模型：

- schema 3／4 封存快照均通过统一 Reader 识别，输入数据库核验前后身份一致；
- 全量 stat 始终执行；哈希和格式分别支持 off／sample／all，抽样比例和种子互不串扰；
- 哈希必须前后 stat 稳定、独立 PowerShell 完整读取、worker 已回收且摘要与有效基准相同
  才能记为一致；无基准明确记为不可核验；
- 内置 ZIP／PDF 在单独 `spawn` worker 中执行，支持 pause／timeout／stop 和精确回收；
- unknown／unsupported 只计数不列路径；Markdown 人读报告与 JSON 技术证据来自同一模型，
  使用 staging、摘要校验和 no-clobber 发布；
- 未采用自定义 Windows native Job。

新统一核验专项 10／10、旧核验＋新统一核验联合 31／31 通过，并把 `ResourceWarning` 视为
错误。该提交之后尚未执行完整发现式回归。外部 ExifTool／FFprobe／7-Zip 直接监督、统一
verify CLI、旧入口投影对照和 GUI 合并尚未完成，不能关闭阶段 5。

## 十一、2026-08-06 重启前交接与新增 RAW 需求

用户新增“RAW 深度校验”：Full 扫描和统一核验的格式校验下均可选择，默认关闭；rawpy／
LibRaw 必须在隔离子进程实际解码，崩溃、timeout、访问异常和内存错误不能拖垮 GUI／父
任务；明确 unsupported 只计总数，解码失败／截断等进入固定 Issues 板块；统一运行能力
层负责可用性和禁用原因；结果只写报告／伴随证据，不改变数据库结构。该需求及 RAW-01～14
测试已写入实施计划，本节只记录需求，尚无实现或通过结果。

重启交接时：`Codex` 的已提交代码 HEAD 为 `68ef716`，`main` 为 `824af84`。未跟踪 WIP
`Script/Lib/Script_DAISY_Lib_DBS_12_Verify_Tools.py` 为 632 行，SHA-256
`ABF89ED666E85E69E5C3A5FF397F5FB7A8DCFF010B20790C73131F6F7E754CEA`；它尚未接入、
尚未测试，已知仍有事件去重和文件消失分类待修，不能视为检查点。用户 `TEMP\` 仍未跟踪
且未被读取或改动。详细续接顺序、native 异常的不确定性和下一步见实施计划第 17 章。

## 十二、阶段 5：外部格式工具直接监督（第二检查点）

2026-08-07 按重启交接摘要恢复未提交 WIP，摘要、行数、分支和 HEAD 全部吻合。新增
`Script_DAISY_Lib_DBS_12_Verify_Tools.py`，并由统一核验默认格式路由接入：ZIP／PDF
继续使用不创建孙进程的内置 `spawn` worker；OLE／7-Zip、ExifTool 和 FFprobe 改用每次
调用明确创建并持有的直接 `Popen` 句柄。实现与验证边界包括：

- 不枚举、不附加、不按名称终止进程，不使用 ctypes 或自定义 Windows Job；
- stdout／stderr 由两个线程持续排空，每路只保留 8 MiB 证据并标记截断，防止异常工具
  通过无界输出耗尽内存；进程回收后先等待排空，仍阻塞才关闭管道解阻；
- pause、默认继续等待、用户继续后跳过、高级默认停止、回调异常和控制器绑定冲突都只
  终止并等待本次精确子进程；
- ExifTool 单工具事件不再重复；ExifTool＋FFprobe 的事件和 threshold 次数各保留一次；
- FFprobe JSON 类型错误、音频文件在探测中消失和输出截断不会形成假 valid；
- 工具预检后消失返回 tool error，不中止整份报告；`0xC0000005` 等 Windows native 异常
  退出码以及未回收状态归为工具错误，不误写为文件损坏；
- 7-Zip valid／加密 unsupported／CRC invalid 继续保持不同分类；非 OLE `.doc` 不启动
  7-Zip，只累计 unsupported；
- 统一核验的 schema 3 输入在默认外部路由测试前后 SHA-256、大小和 mtime 不变。

最终批次均设置 `TEMP`／`TMP`／`TMPDIR` 到工作区 `.test_runtime\v1_6_0`，并把
`ResourceWarning` 视为错误：

| 批次 | 结果 | 边界 |
|---|---:|---|
| 外部工具监督专项 | 16／16 | Python 精确子进程＋注入分类；不调用真实外部格式工具 |
| 统一核验专项 | 11／11 | 内置 worker、默认外部路由、只读 schema 3 和报告发布 |
| 安全旧核验回归 | 18／18 | 排除会自动发现工作区外 ExifTool／7-Zip 的 3 项测试 |

三个批次失败 0、跳过 0。没有读取用户 `TEMP\`，没有扫描真实档案／硬盘，也没有枚举、
附加、复用或停止其它进程。由于工作区没有可执行的 ExifTool／FFprobe／7-Zip 替身，本
检查点没有运行真实工具端到端测试，不能把注入分类测试描述成真实工具兼容证明；这部分
仍需在不违反工作区边界的测试设施就绪后补证。统一 verify CLI、旧入口完整投影对照、
最终 GUI 合并和 RAW 深度校验仍未完成。

## 十三、阶段 5：统一核验 CLI（第三检查点）

新增 `Script_DAISY_Module_DBS_30_Verify.py` 和主入口 `verify`，复用统一核验业务模型，不改写
旧 `check-hash`／`check-format` 入口。CLI 提供：

- 全量 stat，以及相互独立的哈希／格式 off、sample、all；默认样本分别为 1% 和 10%；
- 动态无进展 timeout、无人操作默认处置、当前文件开关及控制 stdin；
- 进程内暂停／继续／停止；明确拒绝 `save_exit` 并返回
  `verification_not_resumable`，不暗示核验支持跨重启续传；
- 同源 Markdown 人读报告与 JSON 技术证据，结论到退出码的稳定映射；
- schema 3／4 封存快照只读输入，v1.4.1 无逐文件哈希时报告 `incomplete`，不产生假成功。

代码审查同时发现 `KeyboardInterrupt` 曾把未登记的 `keyboard_interrupt` 作为停止来源，可能
由状态校验异常掩盖原始中断；已改为状态模型允许的显式 `user` 来源。最终验证如下：

| 批次 | 结果 | 关键证据 |
|---|---:|---|
| 统一核验 CLI 专项 | 7／7 | 参数、控制、入口、退出码、报告、schema 3 只读身份 |
| 统一核验核心复测 | 11／11 | 内置／外部路由、暂停、timeout、报告发布 |
| GUI 入口／模块清单契约 | 1／1 | 新模块已登记，既有 8 项 GUI 菜单尚未提前重组 |

全部批次失败 0、跳过 0，并把 `ResourceWarning` 视为错误。端到端子进程把
`TEMP`／`TMP`／`TMPDIR` 固定到工作区 `.test_runtime\v1_6_0`；输入 schema 3 数据库前后
SHA-256、大小和 mtime 相同。没有调用真实 ExifTool／FFprobe／7-Zip，没有扫描真实档案或
硬盘，没有读取用户 `TEMP\`，也没有枚举、附加或停止其它进程。最终 GUI 合并和 RAW 深度
校验仍待完成，阶段 5 不能关闭。

## 十四、阶段 5：旧入口投影对照（第四检查点）

审查发现统一核验与 v1.5.1 兼容入口使用相同 `pick_sample` 算法，却分别采用新的
`:verify:hash`／`:verify:format` seed。这不会使抽样失去确定性，但会让同一快照、同一比例
在新旧入口选中不同文件，不利于报告复核。统一入口现分别沿用旧 `:patrol`／`:validate`
seed；哈希和格式的 seed 仍彼此独立。

新增 `Script_DAISY_Test_DBS_Verify_Compatibility.py`，验证：

- 240 个 schema 3／v1.4.1 合成文件按 10% 抽样且最少 100 个时，新旧哈希入口选择完全相同
  的 100 个相对路径；
- 同规模格式夹具的新旧入口同样选择完全相同的 100 个相对路径；
- 缺失、stat 变化、哈希不一致和工具错误经规范化后的相对路径／状态投影一致，变化或缺失
  文件均不会启动哈希执行；
- valid、invalid、unsupported、missing 格式计数及问题路径／状态投影一致；
- 每个用例前后输入 SQLite 的 SHA-256、大小和 mtime 相同。

最终联合批次如下，全部把 `ResourceWarning` 视为错误：

| 批次 | 结果 | 边界 |
|---|---:|---|
| 新旧核验兼容专项 | 4／4 | 240 文件样本集合＋哈希／格式问题投影 |
| 统一核验 CLI | 7／7 | 参数、控制、报告和只读端到端 |
| 统一核验核心 | 11／11 | worker、暂停、timeout、外部路由和报告 |
| 安全旧核验 | 18／18 | 排除会自动发现工作区外真实工具的 3 项 |
| GUI 入口／模块清单契约 | 1／1 | 新专项已进入项目自检清单，既有菜单未提前重组 |

测试只使用工作区 `.test_runtime\v1_6_0` 和注入工具结果，没有调用真实
ExifTool／FFprobe／7-Zip，没有读取用户 `TEMP\` 或影响其它进程。3 项真实工具用例的排除
意味着当前证据不能声称真实工具端到端兼容；它只证明明确列出的业务投影与控制边界。

## 十五、阶段 5：RAW 隔离能力与 worker（第五检查点）

新增统一运行能力模型和 rawpy／LibRaw 隔离探测：父进程只接收结构化
available／unavailable／incompatible／crashed／timeout、版本和原因，不在 Tk 主进程或普通
线程导入 rawpy。探测 timeout／崩溃只回收本次创建的精确 `spawn` 子进程。

新增 RAW 每文件 worker：

- 只在子进程导入 rawpy，执行 `imread(...).postprocess()`，验证宽、高、通道、像素数和缓冲
  字节数均非空后丢弃数组；父进程不接收像素；
- unsupported、invalid decode、MemoryError、worker error、native-like crash 和 timeout 分开；
- timeout 沿用 ExifTool 的默认 90s／每 9 GiB 增加 90s 阶梯；无人操作默认继续等待；
- 暂停、跳过并记录和停止续传只终止／等待本次精确 worker；事件证据上限为 512 条；
- RAW 候选使用显式扩展名集合，不把普通未识别类型当问题。

专项修正后连续 3 轮共 36／36，失败 0、跳过 0，并把 `ResourceWarning` 视为错误。首轮 native-like
退出测试发现 Windows 双向管道的 `poll()` 会抛出 `BrokenPipeError`；生产监管已把握手期和
结果期管道断裂统一归为 `worker_crashed`。连续复测覆盖：

- 能力 available／unavailable／incompatible／crashed／timeout 和未知能力拒绝；
- 父进程 `sys.modules` 不新增 rawpy，两个生产模块均无顶层 rawpy import；
- 生产 child 注入合成 rawpy 后确实调用 `postprocess`，只返回尺寸摘要；
- unsupported／decode error／MemoryError／native-like 退出分类；
- 默认继续后晚到成功、默认跳过、默认停止和暂停均精确回收 worker；
- 0、9 GiB、9 GiB+1 的阈值分别为 90s、90s、180s。

当前没有许可与 SHA-256 冻结的工作区真实 RAW 夹具，也没有调用工作区外 rawpy 或私人照片，
所以 RAW-05 的真实 LibRaw 解码证据仍未满足，属于发布阻断。扫描恢复 JSONL、最终伴随 JSON、
Issues 合并、统一核验／Full／ENV-01／GUI 接线也尚未实现；不得把本检查点描述为 RAW 功能
全部完成。

## 十六、阶段 5：RAW 恢复证据与伴随报告（第六检查点）

新增工作 JSONL 和最终伴随报告层，不连接 SQLite：

- `.partial.sqlite` 确定性对应 `.raw_verification.jsonl`，头部绑定 snapshot UUID、格式
  sample／all、比例和 rawpy／LibRaw 版本摘要；
- 已有 journal 必须先验证完整头部与 binding SHA-256，绑定不符时不修复、不截断；绑定一致
  后才允许移除最后一个没有 LF 的半行；
- valid／unsupported 结果只保存 entry ID 与 stat 身份，路径和 detail 均为 NULL；只有
  invalid／timeout／error 保留逻辑路径并进入最终问题明细；
- 暂停／停止不写终态，恢复时当前文件从头执行；已写终态只有 size／mtime 同时匹配才复用；
- 最终 JSON 同源生成固定“RAW 深度校验问题”Markdown，严格区分 executed 0 和 incomplete／
  未执行 NULL；sample 报告明确不代表全部 RAW；
- staging 回读验证、no-clobber、UTF-8 无 BOM／LF，失败清理本次精确 staging。

首轮 8 项中 4 项通过、4 项因 CRLF 失败。根因是 Windows 的 `os.open` 文件描述符默认文本
模式，即使 `os.write` 输入 bytes 仍会转换 LF。所有 journal／staging 描述符现显式加入
`O_BINARY`，没有放宽断言。修正后连续 3 轮共 24／24，失败 0、跳过 0；覆盖绑定不符字节
不变、半行修复、5 类终态、路径隐私、暂停／停止拒绝、incomplete 结论、no-clobber 和
staging 清理。测试只写工作区 `.test_runtime\v1_6_0`，未读取用户 `TEMP\` 或其它文件。

当前伴随报告还没有与 SQLite 的最终发布动作联动，也没有接入统一核验、Full、ENV-01 或
GUI；真实 RAW 解码阻断仍未解除。

## 十七、阶段 5：统一核验 RAW 从属阶段（第七检查点）

统一 `verify` 增加 `--raw-deep-validation` 和 `--raw-timeout-seconds`，并在报告中增加固定
RAW 板块。实现边界：

- RAW 必须依附格式 sample／all；关闭格式时参数预检拒绝，关闭 RAW 时不探测、不启动 worker；
- rawpy 能力探测发生在 `_load_entries` 之前；非 available、非 isolated 或探测 worker 未确认
  回收均拒绝，因此显式请求不可用能力不会先读取快照或源文件；
- RAW 范围重新使用格式阶段同一确定性 seed／比例，240 个全 RAW 文件的 10% 样本在两阶段
  均为同一 100 个路径；
- 每文件解码前后 stat，只有 `RawDecodeOutcome.succeeded` 才记 valid；unsupported 只计数，
  invalid／timeout／error 保留问题路径；
- 主 JSON／Markdown 增加“RAW 深度校验问题”，技术能力版本和覆盖边界同源；不写 SQLite。

RAW 新专项首轮 4／5；失败来自测试用 `assertNotIn("valid.dng", ...)` 会命中
`invalid.dng`，不是路径隐私实现失败。断言改为精确 `rel_path` JSON 字段后连续 3 轮共
15／15。最终联合批次：

| 批次 | 结果 | 关键证据 |
|---|---:|---|
| 统一核验 RAW | 3×5／5 | 预检顺序、关闭零调用、100 文件同样本、问题／隐私、schema 3 只读 |
| 统一核验核心 | 11／11 | RAW 默认关闭下既有行为与报告发布 |
| 统一核验 CLI | 7／7 | 旧默认参数、控制、退出码和端到端 |
| 新旧核验兼容 | 4／4 | 哈希／格式抽样及问题投影不漂移 |

全部把 `ResourceWarning` 视为错误，未调用真实 rawpy、ExifTool、FFprobe 或 7-Zip，未读取
用户 `TEMP\`，也未枚举或影响其它进程。真实 RAW 解码、Full 恢复伴随证据、ENV-01 和 GUI
仍未完成。

## 十八、阶段 5：Full 扫描 RAW 从属阶段与联合发布（第八检查点）

Full 扫描现已接入 RAW 深检，但仍保持默认关闭。实现和测试确认：

- CLI 层级约束拒绝 Quick、格式关闭时的 RAW、孤立 RAW timeout 和非正有限 timeout；
- rawpy／LibRaw 隔离能力失败发生在根目录解析之前，未先访问源目录；正常恢复比较冻结版本，
  `sealed_unpublished` 发布重试不重新探测工具或访问源目录；
- RAW 选中集合直接来自本次 `format_checks`，没有第二套抽样；仍使用 `format` 检查点，不新增
  SQLite 阶段、表或列；
- 每个终态写入绑定 JSONL；valid／unsupported 不留路径，invalid／timeout／error 才留路径；
- `save_exit` 与进程内 `pause` 已在 worker 结果中分开；保存退出不写当前文件终态，新 session
  重新执行该文件并复用此前完整终态；
- 最终 RAW JSON、RAW Issues 板块和 SQLite 由同一发布流程协调，RAW JSON staging 回读摘要
  正确，数据库失败会回滚本次精确伴随目标且不留下 `.publishing`；
- RAW JSON 内记录最终数据库 SHA-256；最终 SQLite 的完整 `sqlite_master` DDL 与执行前相同。

首轮联合发布专项 5／6；唯一失败是测试使用 `LIKE '%raw%'`，错误地把既有 schema 的
`raw_payloads` 元数据原始载荷表当成新增 RAW 深检表。实现的完整 DDL 前后相等断言当时已通过，
因此删除了概念错误的名称断言，没有修改生产 schema 来迎合测试。修正后结果：

| 批次 | 结果 | 覆盖 |
|---|---:|---|
| Full 扫描 RAW 新专项 | 6／6 | 配置／预检顺序、隐私、保存退出续接、联合发布、DDL、失败回滚 |
| 扫描／状态／RAW／Issues 联合 | 120／120 | 既有状态机与默认关闭回归 |
| RAW worker＋证据＋扫描接线连续 3 轮 | 3×27／27 | worker 回收、JSONL、发布稳定性 |
| GUI scan＋no-clobber＋既有发布 | 37／37 | GUI 命令兼容和原发布路径 |
| `scan --help` | 通过 | 新参数可发现且默认语义明确 |

所有 Python 测试均使用 `-B -W error`；测试只写工作区 `.test_runtime\v1_6_0`，没有读取用户
`TEMP\`、没有枚举或终止其它进程、没有扫描真实硬盘／档案。当前仍没有许可与 SHA-256 冻结
的真实 RAW 夹具，也没有执行真实 rawpy／LibRaw；RAW-05 继续是发布阻断。ENV-01 和 GUI 的
能力禁用原因／开关接线亦未完成。

## 十九、阶段 5：ENV-01 与 Full GUI 能力接线（第九检查点）

本检查点没有修改扫描器、Diff、SQLite DDL 或数据库写入逻辑，只把已完成的统一 RAW 能力
层接入环境检测和现有 Full GUI：

- `ENV-01` 复用统一注册表，一次探测结果进入 `environment_inventory`、独立
  `runtime_capabilities` 事件和成功环境报告；
- RAW 能力不可用不会让基础必需工具检测失败，界面与终端均显示 state 和直接原因；
- Full 的 RAW 开关默认关闭，位于格式校验下；格式关闭、能力未检测或隔离证据不完整时禁用；
- 能力从可用变为不可用时，已选 RAW 被撤销，命令预览同步移除参数；
- 环境页新增可选能力卡片；Full 页仍保持 RAW 依附 sample／all 的层级；
- RAW 启用而哈希关闭时，timeout 默认处置仍传入扫描后端；默认 Full 行为不增加 RAW 探测。

验证全部使用 `python -B -W error`，结果如下。重复批次彼此重叠，不能相加冒充唯一测试数：

| 批次 | 结果 | 关键证据 |
|---|---:|---|
| 4 个改动文件 AST | 4／4 | ENV、GUI 和两份测试文件语法有效 |
| 真实 Tk 控件类 | 6／6 | 隐藏窗口、菜单状态、预览、环境卡片和既有控件状态机 |
| ENV／GUI 定向 | 27／27 | 统一注册表、可选能力报告、参数层级和 GUI 事件 |
| 扫描／状态／RAW／Issues／GUI／发布联合 | 161／161 | 默认关闭、恢复、no-clobber、DDL 与既有发布回归 |
| RAW＋ENV＋真实 Tk 稳定性 | 3×34／34 | 连续三轮均失败 0、跳过 0 |

真实 Tk 测试只创建并销毁本测试持有的隐藏根窗口；进程类测试只等待或回收本测试精确创建的
子进程。测试未读取用户 `TEMP\`，未扫描真实硬盘／档案，未枚举、附加、复用或停止其它
进程。仍未调用真实 rawpy／LibRaw，也没有许可与摘要冻结的真实 RAW 夹具，所以 RAW-05
继续阻断发布。统一“核验”页尚未完成四入口 GUI 重组；本节不能表述为全部 RAW 产品界面已
完成，当前应用版本仍应保持 v1.5.1。

## 二十、阶段 6：schema 3／4 跨版本 Diff（第十检查点）

本检查点只修改 Reader、Diff 消费者、CLI 展示和测试夹具，不修改扫描器、数据库生成流程或
任何 SQLite DDL。Diff 的物理表读取收拢到 Reader 的版本化 `daisy-diff-input-v1` 投影：

- schema 3 和 schema 4 均投影为相同的 root、目录、文件、有效哈希、File ID 和 Raw Payload
  摘要模型，Diff 业务层不再直接执行快照 SQL；
- 文件／目录结构能力仍是准入硬条件；哈希或 Raw Payload 证据缺失时只降低对应比较能力，
  不阻断结构对比，也不把“未知”伪装成“相同”或“变化”；
- `hash_missing` 只表达哈希证据不足，`metadata_changed=NULL` 表达元数据证据不可比较；格式
  核验、session、attempt 和性能能力只进入能力说明，不擅自扩充冻结的 11 种文件状态；
- Diff 输出继续使用 schema 3，`DIFF_DDL` 的冻结文本和摘要不变；全部输入数据库在测试前后
  的 SHA-256、大小和 mtime 不变。

专项夹具以工作区内合成数据生成合法 schema 3 快照和由状态机发布的 schema 4 快照，覆盖
3→3、3→4、4→3、4→4、方向互换、移动／复制／硬链接、显式多 root 映射、未配对 root、
枚举失败传播、可选证据表缺失、CLI 发布和 no-clobber。结果如下；批次彼此重叠，不能相加为
唯一用例数：

| 批次 | 结果 | 关键证据 |
|---|---:|---|
| 跨版本 Diff 专项 | 8／8 | 四向矩阵、证据降级、方向语义、DDL 和输入只读 |
| 跨版本 Diff 连续稳定性 | 3×8／8 | 三轮均失败 0、跳过 0 |
| Diff／Reader／CLI／no-clobber 定向 | 72／72 | 投影边界、旧契约、发布与异常路径 |
| 只读消费者广覆盖首轮 | 244／246 | 暴露 GUI 参数期望和受控模块／规范登记两处遗漏 |
| 同批次修复后完整复跑 | 246／246 | 完全相同选择集，用时 242.705 秒 |

上述自动化批次使用 `python -B -W error` 和工作区 `.test_runtime\v1_6_0` 合成数据；没有
读取用户 `TEMP\`，没有扫描真实硬盘／档案，也没有枚举、附加、复用或停止其它进程。为了
避免旧用例自动发现工作区外的真实工具，广覆盖批次使用显式安全测试选择集。

用户随后明确授权 `TEMP\测试文件` 作为真实只读夹具。该目录有 37 个文件、2 个目录，合计
7,681,279,487 字节；全部数据库、Diff 和派生副本仍只写入 `.test_runtime\v1_6_0`。真实验证
使用 Git tag `v1.4.1` 的精确提交 `0fddc09feafa757135138f45150715fbeefa60c2`，没有把当前
分支的旧入口冒充发布版代码：

| 真实批次 | 结果 | 关键证据 |
|---|---:|---|
| v1.4.1 schema 3 Quick | 2／2 | 每次 37 文件、2 目录、7.68 GB、错误 0、unstable 0 |
| 当前 schema 4 Quick | 2／2 | 每次 37 文件，阶段跳过原因与原子发布完整 |
| 真实静态四方向 Diff | 4／4 | 3→3、3→4、4→3、4→4 均为 37 hash missing、2 目录 unchanged |
| 四份真实输入只读身份 | 4／4 | Diff 前后 SHA-256、大小、mtime 全部不变 |
| 真实副本变化正反向 | 2／2 | 内容变化、新增、删除、移动各 1，hash missing 2，路径严格反转 |
| 原始来源身份 | 7／7 | 被复制来源的 SHA-256、大小、mtime 全部不变 |

变化批次先由 v1.4.1 对真实小文件副本建 schema 3 快照，再只在本测试拥有的派生树中移动
DNG、移出 PDF、替换 JPEG、加入中文名 XLSX，最后由当前代码建 schema 4 快照。正向移动
证据为 `heuristic_file_id`；反向 added／deleted 和 old／new 路径机器断言通过。原夹具内没有
创建、改名、覆盖或删除文件。

这还不是对所有历史私有数据库变体或 Full 工具链的证明。隔离能力探测实际返回
`rawpy` 未安装且 worker 已干净回收；PATH 中也没有 7-Zip。因此本轮没有执行真实 RAW 成功
解码或完整 Full／格式链，也没有用假工具绕过预检；RAW-05 与真实工具链继续阻断发布。

## 二十一、阶段 7：数据库解析识别与选择计划（第十一检查点）

本检查点没有改变旧 DBS-41 查询、CSV 编码／字段、XLSX 内容或 CLI 参数。新实现先建立
只读产品模型：快照 15 个模块、Diff 6 个模块；卡片状态由 Reader 的能力和行数折叠，内容
预设与 HTML／XLSX／CSV／JSONL 格式分别验证。raw payload 模块带隐私警告；schema 3 缺少
schema 4 运行表只影响可选能力，不会拒绝其它模块。

schema 4 快速识别仍检查发布文件名模式，但可延迟整个数据库的内容指纹和 SQLite 完整性；
返回值明确标记 `integrity_checked=false` 和“正式读取前必须完整复核”。默认 Reader 调用仍
执行完整复核，因而没有放宽其它消费者准入。正式解析识别使用完整指纹和 SQLite 检查。

| 批次 | 结果 | 关键证据 |
|---|---:|---|
| 旧 Parse／DBS-41 | 11／11 | 旧注册顺序、CSV／XLSX 字节、CLI 退出码和摘要不变 |
| Reader | 22／22 | 默认完整准入、快速延迟指纹、schema 3／4 与输入只读 |
| 新解析计划 | 5／5 | 15／6 目录、0／NULL、三预设、四格式、隐私和 Diff |
| 定向合计 | 38／38 | `python -B -W error`，失败 0、跳过 0 |
| 解析计划连续稳定性 | 3×5／5 | 三轮均失败 0、跳过 0 |
| 安全消费者联合 | 71／71 | Parse、Reader、Issues、Diff／核验兼容和 no-clobber |
| GUI／规范契约 | 3／3 | 自检清单、CLI 参数映射和技术规范 |
| 真实 Quick 数据库识别 | 2／2 | v1.4.1 schema 3 与当前 schema 4 模式、状态、默认计划正确 |
| 真实输入身份 | 2／2 | 识别前后 SHA-256、大小、mtime 不变 |

合成测试只写 `.test_runtime\v1_6_0`。真实批次读取的是前一检查点经用户授权的夹具所生成
数据库，不访问其内部记录的源路径；没有改动 `TEMP\测试文件`，也没有枚举、附加、复用或
停止其它进程。一次临时真实断言脚本因误写不存在的 `assertion()` 在断言前退出；改用原生
`assert` 后同批次 2／2 通过，该失误不是产品代码失败。

截至第十一检查点，流式 CSV／JSONL、raw payload 校验、manifest、staging、no-clobber、HTML、新版 XLSX、
`parse-db` CLI 和 GUI 均未完成，本节证据不能被表述为数据库解析功能已交付。

## 二十二、阶段 7：稳定投影与技术导出（第十二检查点）

本检查点新增稳定投影与内部技术导出执行层，不修改扫描器、Diff 写入、schema 3／4 DDL
或数据库生成流程，也不切换旧 `export-report`：

- 快照 15 个模块、Diff 6 个模块均以显式稳定字段输出，大表使用 `fetchmany()`，没有
  `SELECT *`／`t.*`／`fetchall()`；
- RAW payload 按行核对 zlib、长度、SHA-256 和 UTF-8 JSON，四类损坏均有独立失败证据；
- schema 3 运行历史读取旧 manifest／event；schema 4 同时读取 session、attempt、性能、
  格式、状态、checkpoint 和 runtime，记录键不使用 `entry_id`；
- CSV／JSONL 共用一次模块遍历；CSV 为 UTF-8 无 BOM、LF、稳定表头和完整值，JSONL 保留
  中文与嵌套类型；
- manifest 记录输入、计划、模块、字段、行数和所有产物摘要；一致只读事务、输入双摘要、
  取消、异常清理和目录 no-clobber 发布均有测试。

结果如下；批次之间未重复计入本表的 82 项检查点联合总数：

| 批次 | 结果 | 关键证据 |
|---|---:|---|
| 旧 Parse／DBS-41 | 11／11 | 原 CSV／XLSX 字节、模块顺序、旧 CLI 退出码不变 |
| 新解析规划 | 5／5 | 15／6 模块、预设、格式映射和不可用状态 |
| 稳定投影 | 5／5 | 全模块字段、schema 3／4 history、Diff、取消、RAW 四类损坏 |
| 技术导出执行层 | 5／5 | CSV／JSONL／manifest、单次遍历、冲突、取消、异常、输入变化 |
| Reader | 22／22 | schema 4 history 聚合、默认准入与 v1.4.1 只读兼容 |
| Issues | 10／10 | 既有固定板块与 0／NULL 语义不回退 |
| 跨版本 Diff | 8／8 | 四方向业务投影与冻结 Diff DDL |
| 核验兼容 | 4／4 | 旧抽样和问题投影不漂移 |
| 既有 no-clobber | 11／11 | 旧发布与冲突语义不回退 |
| 受控规范登记 | 1／1 | 新 Lib、测试清单与技术规范一致 |
| 检查点联合总数 | 82／82 | `python -B -W error`，失败 0、跳过 0 |

真实验证复用了前一检查点在工作区生成的数据库，不访问数据库记录的源路径：2 个 schema 3
Quick、2 个 schema 4 Quick、4 个 schema 方向 Diff 和 2 个双向变化 Diff，合计 10／10
逐模块遍历通过；同一批数据库再完成 10／10 完整审计技术导出。每份报告的 CSV 编码／LF、
JSONL 每行 JSON、manifest 契约及产物 SHA-256 均回读验证，10 个输入的 SHA-256、大小和
mtime 前后相同。所有报告只写入 `.test_runtime\v1_6_0\real_parse_technical_*`。

测试过程发现并诚实处理了两项测试证据问题：首个临时真实遍历命令未显式关闭 SHA 输入
文件，产生 `ResourceWarning`，未纳入正式结果；改为独立脚本和 `with open` 后 10／10
干净通过。输入变化用例最初增加 1 ns，被 Windows 文件系统时间粒度舍入；改为增加 1 秒
后真实触发拒绝发布。产品代码没有为迎合错误断言而放宽检测。

截至第十二检查点，HTML、新版 XLSX、`parse-db` CLI、GUI 模块卡片和 1080p／字号矩阵仍未实施；本节不把
技术 writer 检查点称为完整“数据库解析”交付。

## 二十三、阶段 7：人读 HTML 与流式 XLSX（第十三检查点）

本检查点让 HTML／XLSX／CSV／JSONL 共用同一次稳定模块遍历：

- HTML 使用 nonce CSP，无 `unsafe-inline`、远程资源或 `file://`，数据库内容全部转义；
- HTML 固定最多 200 行模块预览，显示真实总数、截断、兼容降级、0／NULL 和完整值边界；
- Diff 首页使用变化状态结论，快照首页使用 Issues 证据，不混用两类语义；
- XLSX 首张为报告概览，后续流式 sheet parts 支持冻结、筛选、语义列宽、行上限拆表和名称
  去重；所有数据库值为 `inlineStr`，不写公式元素；
- RAW 在 HTML／XLSX 只显示前 200 行，205 行夹具的 manifest 仍记录总数 205；
- 取消中途写入的 XLSX parts、总 staging 和最终报告全部不存在。

| 批次 | 结果 | 关键证据 |
|---|---:|---|
| HTML／XLSX 专项 | 6／6 | CSP、转义、控制字符、有限预览、Diff 结论、XLSX 结构与取消 |
| 技术执行层回归 | 5／5 | 四格式接入后 CSV／JSONL、manifest 与 no-clobber 不回退 |
| 旧 Parse／DBS-41 | 11／11 | 旧 CSV／XLSX 和旧 CLI 完全保持 |
| 真实人读导出 | 10／10 | schema 3／4、四方向和变化 Diff 的 HTML／XLSX 结构通过 |
| 真实输入身份 | 10／10 | SHA-256、大小、mtime 前后相同 |

HTML 专项使用标准 `HTMLParser` 确认数据库中的 `<script>`／`<img onerror>` 仅为文本，报告
只有一个带固定 nonce 的静态脚本，所有 `href` 只指向报告内部锚点。XLSX 专项对 ZIP 做
CRC 检查，并用 XML 解析器逐个读取 package XML；工作表含冻结 pane 与 autoFilter，且没有
`<f>`。公式前缀文件名保持普通字符串；强制 48 字符显示上限时 XLSX 显示截断，JSONL 的
180 字符嵌套原值仍完整。

真实批次读取前一检查点的 10 个数据库，只在 `.test_runtime\v1_6_0\real_parse_human_*`
生成报告，不访问数据库记录的源路径。`parse-db` CLI 与 GUI 尚未完成，本节不作为阶段 7
完成证据。

## 二十四、阶段 7：数据库解析统一 CLI（第十四检查点）

本检查点只新增 DBS-41 编排路径、主入口登记和端到端测试，不修改扫描器、Diff 写入、
schema 3／4 DDL 或任何数据库生成逻辑：

- `parse-db --database` 与旧 `export-report --snapshot／--diff` 使用同一模块中的两套参数
  解析器；新旧参数无法交叉，旧 writer、文件顺序、输出值和退出码保持冻结；
- 新入口支持 `human-summary／full-audit／custom`，模块和格式可重复或逗号分隔，默认
  `human-summary＋html`；
- 快速阶段显示类型、schema、兼容模式和模块状态，正式阶段仍完整核验输入；RAW 原始载荷
  选择会先显示隐私提示；
- 模块进度同时进入终端和既有 GUI 机器事件，普通终端不输出事件；CLI 不自动打开任何
  外部程序或数据库记录路径；
- 成功报告沿用唯一 staging、manifest、输入双身份和 no-clobber 发布；无效数据库、模块、
  格式、空自定义或参数混用均在零正式产物状态退出。

| 批次 | 结果 | 关键证据 |
|---|---:|---|
| 新 CLI 端到端 | 6／6 | 新旧帮助隔离、四格式、Diff、失败零发布、GUI 事件、旧 writer |
| 数据库解析联合 | 38／38 | 旧 Parse 11、规划 5、投影 5、执行 5、人读 6、新 CLI 6 |
| 真实 CLI 矩阵 | 10／10 | schema 3 快照 2、schema 4 快照 2、Diff 6 |
| 跨版本 Diff 复核 | 8／8 | 四方向、方向互换、能力降级、冻结 DDL 和 CLI 发布 |
| 真实变化断言 | 1／1 | 正反向内容变化／移动／增删／hash missing 精确匹配 |
| 真实输入身份 | 10／10 | CLI 前后 SHA-256、大小、mtime 完全相同 |

首轮 CLI 专项为 4 通过、1 失败、1 清理错误：失败断言错误地禁止旧帮助页迁移提示出现
`--database` 文本；清理错误来自测试用 `sqlite3.Connection` 上下文只提交而未关闭，Windows
正确拒绝删除仍占用的未知库夹具。测试改为只禁止真正的 `--database DATABASE` 选项行并在
`finally` 显式关闭连接后，同一 6 项完整复跑为 6／6，`-W error` 下无句柄警告。产品代码
没有为了通过测试而放宽准入或强制删除占用文件。

真实 CLI 只读取前面由用户授权素材派生、已位于工作区的 10 个数据库，不重新扫描原素材，
也不访问库内源路径；每个 subprocess 均由测试精确创建、等待和回收。输出只写入
`.test_runtime\v1_6_0\real_parse_cli_*`。本检查点仍缺数据库解析 GUI，阶段 7 不能标记完成。

外围回归随后完成 Unit 225／225、no-clobber 11／11、Reader 22／22。Unit 首次给定的
180 秒驱动上限低于历史完整耗时，在真实 Tk 相对缩放矩阵处被精确终止；此前可见项均为
通过，但该中断批次没有计入结果。将上限改为 420 秒后，同一文件从头完整运行 225 项，
257.056 秒通过，失败 0、跳过 0；这同时复核了 v1.5.1 的 1080p、字体／比例、滚动、下拉
重选、独立日志和进度布局契约。

## 二十五、现场外部工具内存异常调查登记

用户报告类似内存访问错误弹窗以前也发生过，最近一次已手动关闭。当前证据没有
保留弹窗截图、准确进程名、可执行文件路径、触发文件或退出码，因此本记录不把它归因于
FFprobe、FFmpeg、当前素材或此前的 `python.exe` 异常。用户询问前五分钟内，Codex 只执行
工作区源码的 PowerShell 只读检索，没有启动 `ffmpeg.exe` 或 `ffprobe.exe`；这只能排除该
次检索直接触发弹窗，不能说明用户此前仍在运行的任务调用链。

静态审查得到两个可复核缺口：旧 Full 元数据的 `ffprobe_full()` 仍使用未指定 Windows
creation flags 的 `subprocess.run`；新版统一核验监督器虽已设置 `CREATE_NO_WINDOW`、精确
回收并把高位 Windows 状态码格式化为十六进制崩溃码，但尚未抑制 Windows 原生错误 UI。
微软文档同时说明错误模式是进程级状态并由子进程继承，所以不能在多线程 Tk 主进程中
临时切换。实施计划已新增 15.12、AUD-33、VER-13～14、GUI-28 和 SEC-08；登记当时状态为
“已登记、未修复、未验证”，不得把登记动作本身写成已解决。

随后完成第一层代码硬化：统一入口只为非 GUI 任务设置 worker 进程错误模式；工具版本
预检、旧 `ffprobe_full()` 和新版直接监督器也在创建后代前做幂等设置。旧 FFprobe 增加
`CREATE_NO_WINDOW`，高位 Windows 退出状态以无符号十六进制记录；正常非零、timeout 与
native 崩溃仍保持不同语义。Tk 主进程不设置，数据库 DDL、schema、成功 JSON 映射和既有
`ffprobe_error` 状态没有改变。

| 批次 | 结果 | 关键证据 |
|---|---:|---|
| 外部工具故障边界专项 | 21／21 | 注入模式、真实 Windows 继承、隐藏窗口、异常码、精确回收 |
| 旧核验＋统一核验＋CLI | 39／39 | 21＋11＋7；成功、timeout、unsupported 和工具错误不漂移 |
| 运行层＋扫描 CLI＋RAW 扫描 | 67／67 | 51＋10＋6；阶段、发布、暂停与零 schema 改动 |
| 完整 Unit | 225／225 | 225.533 秒；Tk 入口、DDL、Diff、元数据和既有 GUI 契约 |
| no-clobber＋Reader＋跨版本 Diff | 41／41 | 11＋22＋8；v1.4.1/schema 3 冻结契约和只读身份 |

专项使用注入结果和本测试精确创建、等待、回收的 Python 子进程，没有调用真实 FFprobe，
没有读取用户 `TEMP\`，也没有枚举或终止其它进程。真实 Windows API 用例确认任务进程设置
的全部必需 error-mode bits 被隐藏子进程继承；整个回归期间未观察到系统内存错误框。
由于没有故意触发真实 access violation，也没有当时故障工具的路径、版本和触发文件，
当前结论是“代码硬化与安全替身回归通过，原现场根因仍不确定”，不能宣称已经复现或证明
所有第三方二进制都绝不会显示自有弹窗。

## 二十六、阶段 7～8：四入口 GUI 与数据库解析选择链（第十五检查点）

本检查点把数据功能的可见 GUI 收敛为“扫描、对比、核验、数据库解析”4 个入口；环境与
硬盘域保持独立。旧 TaskSpec 和 CLI 仍存在以兼容脚本、偏好和恢复指针，但不再作为重复
页面显示。工作树中的数据库相关业务修改只有扫描阶段完成摘要增加“不适用／跳过”两类
人读字段；没有修改 DDL、数据库写入、Diff、Reader、数据库生成或旧导出 writer。

数据库解析界面会在后台只读识别输入，成功后根据 Reader 结果生成模块卡；只有 available
模块可由预设或自定义选择，empty／unavailable／incompatible／invalid 保留状态和原因。
默认预览映射到 `human-summary＋html＋xlsx`，正式运行仍调用统一 `parse-db` CLI。成功识别
会展开设置并收起进度／日志；正式开始后按通用规则收起设置、展开进度和日志。

界面控件清单审查确认，所有 `choice_flag` 且值域严格为 `{False, True}` 的表单项都使用
黄色关闭／绿色启用按钮；多值模式继续使用下拉菜单。核验可暂停／继续／停止，但没有
扫描 partial 续传语义，所以保存退出保持禁用。元数据等阶段的 `not_applicable` 在 GUI
进度详情和阶段完成摘要中显示为“不适用”，不会并入“错误”。

| 批次 | 结果 | 关键证据 |
|---|---:|---|
| GUI scan/control 全文件 | 25／25 | 受控暂停／续传、核验控制、恢复卡、真实 Tk、数据库识别 |
| 解析识别后 GUI 矩阵 | 72／72 | 3 相对缩放 × 2 字体 × 3 字号 × 4 尺寸；模块文字不裁切且可滚到底 |
| 完整 Unit | 228／228 | 268.867 秒；真实 Tk 尺寸／字号／缩放、旧 DDL／Diff／GUI 契约 |
| 新语义定向 | 2／2 | 核验不启用保存退出；元数据不适用与错误分离 |
| 静态门槛 | 通过 | `py_compile` 与 `git diff --check` |

数据库解析真实 Tk 用例以工作区 `.test_runtime\v1_6_0` 内合成 schema 3 快照执行。首次
运行准确发现识别后表单高 772 px、可视区 573 px；模块卡改为单行状态及响应式 1～4 列后，
同一断言通过。用例还确认兼容模式为 `v1.4.1-compatible`、模块池不可误编辑、HTML／XLSX
参数进入预览、识别后面板状态正确；切换 custom 后，空选择被拒绝，全选只选 available，
命令预览出现 `--include`。该合成库不是历史 tag 原件，因此本项不能替代后续
v1.4.1 真实数据库矩阵。

本轮没有读取用户 `TEMP\`，没有运行真实物理硬盘检测，也没有枚举、附加或终止其它进程。
当前仍未执行完整发现式回归和最终 GUI 组合矩阵，版本常量仍为 v1.5.1；因此阶段 8～9 和
v1.6.0 发布门槛继续保持未完成。

## 二十七、阶段 8：历史实库、真实 RAW 与最终开发回归（第十六检查点）

### 27.1 范围与只读边界

本检查点只在工作区内生成派生产物。用户明确指定 `TEMP\测试文件` 作为真实验证来源；测试
只读取其中两份最小 DNG，并把精确副本写入被 git 忽略的
`.test_runtime\v1_6_0\final_raw_real_20260807_219eb24`。源目录没有写入、重命名或删除，
源文件与副本的大小和 SHA-256 在测试前后相同。其余真实数据库、v1.4.1 导出源码、报告和
合成目录均位于 `.test_runtime\v1_6_0`。没有运行真实物理硬盘检测，没有枚举、附加或终止
其它进程，也没有修改系统 Python、注册表或系统错误报告设置。

### 27.2 v1.4.1 Full 与数据库解析

使用从 v1.4.1 tag 导出的历史源码，对 3 个工作区合成文件执行真实 Full 扫描，得到历史
schema 3／Full 封存快照。当前统一核验结果如下：

| 项目 | 结果 |
|---|---:|
| 文件 stat | 3／3 |
| 内容哈希 | 3／3 |
| 格式校验已处理 | 0 |
| 不支持格式 | 3；只统计，不列文件问题 |
| 格式问题 | 0 |
| RAW 深检 | NULL；未执行 |

输入数据库在核验前后的大小、mtime 和 SHA-256 相同。当前 Reader 对该库给出
`v1.4.1-compatible`，available 模块为概览、文件、目录、哈希、原始载荷和运行历史，共
6 个；其余 9 个为 empty，不伪装成能力缺失。`full-audit` 预设成功生成 HTML、XLSX、CSV
和 JSONL。Issues 投影把枚举、哈希、元数据记为已执行且问题 0，格式、RAW、性能和运行
证据保持 NULL；“已执行但没有记录”采用明确文案，不再显示“未记录原因”。

另对 10 个既有工作区派生数据库执行 `human-summary` HTML／XLSX 解析：2 个
v1.4.1/schema 3 Quick、2 个 schema 4 Quick、4 个固定跨版本 Diff 和 2 个真实变化 Diff
全部成功，10／10 均包含 HTML、XLSX 和 manifest。全部输入数据库的大小、mtime 与
SHA-256 在解析前后不变。

### 27.3 隔离 rawpy 真实解码

经用户批准，仅把 rawpy 0.27.0 和 NumPy 写入工作区被忽略的测试运行目录。隔离能力探测
返回 rawpy 0.27.0、LibRaw 0.22.1、`available=true`、`isolated=true`、worker exit 0 且
`worker_reaped=true`。该安装只用于本轮验收，不是 DAISY 已捆绑依赖。

两份授权 DNG 大小分别为 555,298 与 772,948 字节。schema 4 Full 扫描开启完整哈希、全部
格式校验和 RAW 深检后，2／2 文件完成哈希、格式与独立复核；RAW 伴随报告为 candidate 2、
selected 2、processed 2、valid 2，error／timeout／invalid／unsupported 均为 0，实际解码
15,283,200 像素，Issues 数为 0。随后统一核验再次得到 stat 2、hash 2、format 2、RAW 2，
问题 0；输入 SQLite 和源／副本摘要均未变化。成功依据是隔离 worker 调用完整
`postprocess()` 并返回非空像素，不是只读 metadata 或缩略图。

### 27.4 GUI 补充与组合矩阵

顶部「设置 > 开关选项样式」现提供“按钮模式（默认）”和“下拉菜单模式”。偏好键
`binary_control_style` 只接受 `buttons／dropdowns`；旧偏好文件缺少该键时安全回到按钮
模式。切换前收集当前页面值，重建后值、命令预览和滚动位置不漂移；下拉模式重选当前项
文字不消失。多值字段始终保持下拉菜单，RAW 在两种样式下都受统一能力探测门控，运行或
数据库识别期间入口锁定。正常重开恢复样式和页面，但不恢复任务表单路径或选择。

数据库解析另补充两条真实 Tk 边界：后台识别期间收起设置、展开进度／日志并锁定运行；
损坏 SQLite 失败后清空旧模块卡，重新展开设置，同时保留失败进度和日志。两个 GUI 实例
的控制 stdin 也由独立测试确认互不串线。

| 批次 | 结果 | 用时／覆盖 |
|---|---:|---|
| 开关样式定向 | 8／8 | 偏好、菜单、1080P、值保留、同项重选、RAW 门控 |
| 跨样式组合 | 72／72 | 2 样式 × 2 相对缩放 × 3 字号 × 3 尺寸 × 2 页面 |
| GUI scan/control 全文件 | 32／32 | 24.301 秒；真实 Tk、识别失败／进行中、双实例隔离 |
| 完整 Unit 首轮 | 227／228 | 266.076 秒；RAW 提示旧断言失败，不计作通过 |
| RAW 提示修复定向 | 1／1 | 7.558 秒；原说明和不可用原因同时存在 |
| 完整 Unit 重跑 | 228／228 | 270.247 秒；从头重跑 |
| 发现式全套回归 | 594／594 | 360.061 秒；`-W error` |

所有正式通过批次均使用 `python -B -W error`；语法编译通过。最终静态 diff、发布身份、
README 全面改写、提交、合并、推送和 tag 仍属于阶段 9，当前不能宣称 v1.6.0 已发布。

## 二十八、统一外部工具恢复与完成提示音（第十七检查点）

现场 ExifTool 连锁故障被拆成“源文件诊断”和“工具运行故障”两条证据链。长驻
ExifTool 会话现在会在 EOF、退出、write／flush `OSError 22` 或协议失效后立即作废，
精确回收并重建；当前文件只有限重试，旧坏管道不会继续接受后续文件。一次性进程统一由
`DBS_18_Tool_Runtime.py` 监督，覆盖启动、输出监控、wait、timeout、native 退出、有限
stdout／stderr 和精确回收。ffprobe、7-Zip、PowerShell 哈希、smartctl、格式／哈希 worker
和 RAW worker 保留各自进程模型，但使用相同工具故障分类与默认 3 次连续失败熔断语义。

schema 4 熔断时把阶段和运行态写为 `failed_recoverable`，当前及剩余条目保持可重试，
不得封存，也不得生成逐文件源错误。Issues 的“运行／证据问题”只输出一个聚合工具事件。
专项以 204,913 个未处理条目验证报告仍为 1 条 `tool_failure_aggregated`，并复核输入 SQLite
的 SHA-256、大小和 mtime 未变化。

顶部「设置」新增默认关闭的“任务完成提示音”。退出码 `0／1` 的普通任务正常结束后异步
播放一次；失败、可恢复失败、暂停、保存退出、停止、依赖安装和物理盘检测准备步骤不播放。
偏好 round-trip 使用 UTF-8 无 BOM、LF；真实 Tk 菜单测试验证菜单项、变量和保存调用同步。
音频测试全部替换 `winsound` 或 Tk `bell()`，没有播放真实声音。

| 批次 | 结果 | 关键证据 |
|---|---:|---|
| 统一工具恢复专项 | 15／15 | ExifTool 会话恢复、受控进程监管、哈希／格式／PowerShell／smartctl 熔断 |
| 外部格式工具专项 | 21／21 | ffprobe／7-Zip 分类、timeout、输出排空、Windows native 边界 |
| RAW 扫描专项 | 7／7 | native worker 故障聚合、恢复文件边界、零 schema 改动 |
| 统一核验专项 | 13／13 | 哈希／格式工具熔断各只形成一条聚合问题 |
| Issues 专项 | 11／11 | 分板块、NULL／0、204,913 项聚合为 1 条、输入只读 |
| 菜单／偏好／提示音语义 | 6／6 | 顶部菜单、默认值、持久化、正常／异常完成边界、替身音频 |
| 上述联合批次 | 73／73 × 3 | 三轮从头重复，失败 0、跳过 0 |
| 真实 Tk 菜单切换 | 1／1 × 3 | 默认关闭、两次切换、变量同步、每次持久化 |

本检查点未读取事故中点名的 JPG，未启动真实 ffprobe／smartctl，未扫描物理盘，未枚举、
附加或终止其它进程。完整 Unit 在加入工具运行库白名单前曾为 229／230；唯一失败是测试
白名单缺少新文件，补齐后该断言 1／1 通过。由于随后又加入真实 Tk 菜单测试，最终完整
Unit 和发现式全套仍须在阶段 9 从头运行，不能把局部修复冒充最终总回归。

## 二十九、阶段 9 最终发布回归

版本常量切换为 `1.6.0`，README、技术规格、数据库解析设计、数据契约、实施计划和版本
演进同步更新后，所有最终批次均从头执行：

| 批次 | 结果 | 用时／关键证据 |
|---|---:|---|
| 完整 Unit | 231／231 | 243.001 秒；真实 Tk 入口、1080p、全部字体／字号／比例／窗口／控件路径 |
| 发现式全套 | 616／616 | 292.911 秒；失败 0、跳过 0、`-W error` 无资源警告 |
| GUI 扫描／控制重复 | 32／32 × 3 | 三轮约 61.7 秒；暂停恢复、数据库解析、面板状态、双实例隔离 |
| 工具恢复联合重复 | 73／73 × 3 | ExifTool／ffprobe／7-Zip／RAW／哈希／smartctl、Issues 和提示音 |
| 提示音真实 Tk 菜单 | 1／1 × 3 | 顶部设置、默认关闭、切换、变量同步和偏好保存 |
| 版本身份定向 | 3／3 | DBS、STG、归档 manifest 均为 `1.6.0` |

发现式 616 已包含一轮 Unit 和所有独立测试文件；表中批次存在有意重叠，不相加为唯一总数。
两个用户 GUI 入口均在 Unit 中真实构造、自动关闭并确认 Tk root 已销毁；Tcl／Tk 环境变量
缺失或无效的两个隔离子进程也成功加载 Tcl/Tk 8.6。最终 GUI 组合覆盖按钮／下拉两种二态
样式、3 档字号、候选字体、多个窗口尺寸／宽高比／相对缩放、全部下拉箭头及同项重选，
没有发现遮挡、不可点击、无内容滚动、顶部空白或文字消失。

最终回归没有调用真实 ffprobe、smartctl 或物理盘检测，没有枚举、附加或终止其它进程。
真实 RAW 证据沿用第 27 节已完成的授权工作区副本；本批次不重新读取用户源目录。`TEMP\`
保持未跟踪，未暂存、未改名、未删除。
