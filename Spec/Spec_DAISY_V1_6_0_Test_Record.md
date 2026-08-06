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

阶段 0～1 已完成。证据证明统一能力探测层可只读接纳 v1.4.1/schema 3，并且没有改变
schema 3 快照／Diff DDL 或既有输出投影。

以下内容仍未完成，不能因 Reader 已落地而宣称 v1.6.0 完成：

- 新 session／attempt／性能／格式校验证据 schema；
- 暂停、保存退出和跨重启恢复状态机；
- 哈希隔离 worker 与动态无进展 timeout；
- Full 可选格式校验、核验合并和跨新 schema Diff；
- 数据库解析、Issues 新板块和 GUI 四入口；
- v1.6.0 最终版本号、发布回归、合并、推送与标签。

## 六、阶段 2：行为保持型重构（进行中）

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
