# DAISY v1.6.4 测试记录

- 对应计划：[v1.6.4 发布计划](Spec_DAISY_V1_6_4_Release_Plan.md)
- 测试日期：2026-08-10
- 状态：发布门禁通过，待提交与推送
- 基线提交：`70afc7cb99292f86ddf2630bc248eec9e8d4500b`

## 一、版本与数据契约

| 项目 | 值 |
|---|---|
| 发布性质 | 继 v1.4.1 后第二个长期生产版本 |
| DBS 应用版本 | 1.6.4 |
| STG 应用版本 | 1.6.4 |
| 旧版 DBS schema | 3 |
| 统一扫描 schema | 4 |
| STG 归档 schema | 3 |
| 元数据 profile | 7 |
| 最低只读封存库兼容版本 | v1.4.1/schema 3 FULL |

应用版本发生变化，数据库、归档、元数据和恢复契约版本均未变化。

## 二、变更专项

| 检查项 | 结果 |
|---|---|
| 检测前显示单行琥珀色「需要先运行环境检测」提示卡 | 通过 |
| 检测前基本校验可用，4 个外部工具灰显且开始操作被拒绝 | 通过 |
| 检测后按工具清单与 RAW 隔离能力启用，不可用原因可见 | 通过 |
| 暂时灰显不覆盖已保存选择；重置后恢复待检测状态 | 通过 |
| 「产出」按钮保持原命令、位置、尺寸与两次绿色提示 | 通过 |
| 硬盘选择「全选／取消选择」与「浏览」请求尺寸一致 | 通过 |
| 「管理员模式」与面板按钮请求尺寸一致，和权限标题纵向居中 | 通过 |
| 「管理员模式」左边缘与倒数第二个「恢复默认」按钮对齐 | 通过，坐标差不超过 1 px |
| 窗口预设按 `1366×768 → 1600×900 → 1920×1080` 排列 | 通过 |
| 菜单显示「结果目录弹窗」，默认值、持久化和结束行为不变 | 通过 |

最终工作树执行 14 项变更专项，覆盖环境门控、RAW 能力、保存选择、默认 `1600×900`、
窗口预设顺序、菜单文案、管理员按钮 X/Y 对齐、硬盘按钮、结果目录弹窗持久化和「产出」
提示，结果为 `14/14` 通过，用时 2.812 秒，退出码 0。该组只创建测试 Tk 窗口和临时夹具，
没有启动业务扫描、依赖安装或真实外部工具。

初次加入双行环境提示卡时，`1600×900` 布局测试发现核验页内容高度 525 px、大于 427 px
视口；改为同层级的紧凑单行卡后通过。管理员按钮最初使用经典 `tk.Button`，Windows 下请求
尺寸为 144×44 px，而标题栏 `ttk.Button` 为 146×39 px；统一为同一套 `ttk` 度量后通过。
最后一轮专项首次执行时，有 1 项测试仍查找旧菜单名「结果目录提示」而发生 `StopIteration`，
其余 13 项通过；修正测试预期后原样重跑为 `14/14`。这些失败均在发布前得到修正，没有作为
通过结果保留。

## 三、完整自动化套件

```powershell
python -B -W error -m unittest discover -s .\Script\Test -p "Script_DAISY_Test_*.py" -q
```

结果：`674/674` 通过，用时 169.772 秒，命令总耗时 170.7 秒，退出码 0。输出中的
「无法创建运行事件证据，已拒绝开始扫描：fixture」来自故意注入失败证据的拒绝启动用例，
不是实际扫描故障。

完整套件在最终的窗口预设排序、菜单文案重命名和管理员按钮横向对齐之前执行；这 3 项只改变
GUI 常量、显示文字和布局，不改变业务逻辑。用户明确要求这些小改动不再触发完整套件，因此
最终工作树改由上一节的 14 项专项覆盖。此处不把此前的完整结果表述为对最后 3 项 UI 差异的
再次全量执行。

## 四、v1.4.1 FULL 兼容专项

专项覆盖 Reader 金样契约、跨版本 Diff、统一核验、问题报告和数据库解析全链路：

```powershell
python -B -W error -m unittest -q `
  Script.Test.Script_DAISY_Test_DBS_Reader `
  Script.Test.Script_DAISY_Test_DBS_Diff_Compatibility `
  Script.Test.Script_DAISY_Test_DBS_Verify `
  Script.Test.Script_DAISY_Test_DBS_Verify_CLI `
  Script.Test.Script_DAISY_Test_DBS_Verify_Compatibility `
  Script.Test.Script_DAISY_Test_DBS_Verify_Unified `
  Script.Test.Script_DAISY_Test_DBS_Issues `
  Script.Test.Script_DAISY_Test_DBS_Parse `
  Script.Test.Script_DAISY_Test_DBS_Parse_Planning `
  Script.Test.Script_DAISY_Test_DBS_Parse_Projection `
  Script.Test.Script_DAISY_Test_DBS_Parse_Run `
  Script.Test.Script_DAISY_Test_DBS_Parse_Human `
  Script.Test.Script_DAISY_Test_DBS_Parse_CLI
```

结果：`128/128` 通过，用时 16.064 秒，命令总耗时 16.6 秒，退出码 0。最终 3 项 UI 小改动
没有触及 Reader、Diff、核验、问题报告或解析实现，按用户要求未重复执行该专项。

## 五、真实 v1.4.1 数据库只读审计

授权输入目录：
`C:\Cytus\Project\Dev_Proj\Sandbox\数据库141-测试分析`

只读审计结果：

- 识别 46 个数据库，总计 25,086,885,888 字节、2,005,382 条文件记录；来源版本全部为
  v1.4.1，schema 全部为 3，兼容模式全部为 `v1.4.1-compatible`。
- 每个库均识别 19 张表和 15 个可解析模块，共生成 138 个合法导出计划。
- 对最小数据库和首个不小于 100 MB 的代表库执行 SQLite 完整性检查与文件名 SHA-256
  高 32 位复核，均通过。
- 对代表库实际导出 14 个产物，覆盖 CSV、HTML、JSONL 和 XLSX；输出位于系统临时目录，
  退出临时上下文后已自动回收。
- 审计前后 46 个输入的文件大小和 mtime 变化数均为 0；授权目录内没有产生测试输出。
- 审计耗时 22.869 秒。

第一次审计命令错误地给「简化报告」组合了不受支持的 JSONL 格式，规划阶段按契约拒绝；
这属于审计脚本组合错误，不是数据库不兼容，且没有写入输入。改用各预设支持的格式组合后
完整重跑并得到上述结果。

## 六、静态与仓库门禁

| 门禁 | 结果 |
|---|---|
| Python 进程内逐文件编译 | 68/68 通过 |
| PowerShell AST 解析 | 1/1 通过 |
| `git diff --check` | 通过 |
| UTF-8 无 BOM 且仅 LF | 89/89 通过 |
| Markdown 相对链接与无双方括号链接 | 17/17 通过 |
| v1.6.3 至当前 `Script/Lib`／`Script/Module` 差异 | 仅 DBS、STG 两处应用版本号 |
| DBS／STG DDL、扫描、Diff 与冻结兼容入口 | 无修改 |

静态门禁的第一次 Python 检查把大文件内容放进 `python -c` 参数，超过 Windows 命令行长度；
该次输出无效，随后改为单进程内逐文件读取并得到 68/68。第一次 Markdown 链接脚本中的负向
回顾正则被调用层转义并产生非终止异常；随后启用 `ErrorActionPreference=Stop`、改用无该
语法的表达式并得到 17/17。文档固化后的复检还发现 Windows PowerShell 5.1 不提供
`Path.GetRelativePath`；改用受工作区根约束的路径截取后再次得到 17/17。记录这些过程是为了
避免把测试工具自身的假阳性当作发布证据。

文档固化后已重新执行全部静态门禁，并清理工作区内精确识别的 `.test_runtime`、
`Script/__pycache__` 与 `Script/Lib/__pycache__`。随后用户明确授权删除工作区内的
`Output`，经绝对路径与工作区边界校验后已直接递归删除；该删除不经过回收站。没有删除
源档案、授权的 v1.4.1 测试数据库或任何工作区外文件。

## 七、发布引用

发布提交为本记录所在提交，精确对象以 `v1.6.4^{}` 为准。发布完成后必须从远端重新获取并
确认 `origin/Codex == origin/main == v1.6.4^{}`；最终哈希在发布回执中报告。该写法避免在
提交内容内写入无法自引用的提交哈希。
