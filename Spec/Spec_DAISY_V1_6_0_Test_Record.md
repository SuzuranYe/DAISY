# DAISY v1.6.0 测试记录

状态：实施中

记录日期：2026-08-06

关联计划：[v1.6.0 实施计划](Spec_DAISY_V1_6_0_Implementation_Plan.md)

## 一、测试边界

- 所有合成数据、临时目录和报告均位于工作区 `.test_runtime\v1_6_0`；该目录不进入 Git。
- 不读取或改写用户 `TEMP\`，不访问金样中记录的外部源路径。
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
