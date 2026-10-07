# 宫廷艺术展陈叙事签发

本项目提供宫廷艺术展陈叙事签发的服务端领域基础：统一管理展陈层级、时间轴节点、展品身份、年代证据、工艺术语、借展可见期、说明文本分支、专家异议和发布渠道，并在此基础上提供签发、撤展、勘误、调度与历史时点重建。项目只使用 Python 标准库和本地 SQLite 文件，不需要连接其他运行服务。

## 目录

- `src/exhibition_narrative_release/domain.py` 定义领域记录、时间工具与异常。
- `src/exhibition_narrative_release/store.py` 管理 SQLite 表和事务写入。
- `src/exhibition_narrative_release/service.py` 提供登记、签发、撤展、异议、勘误、调度与查询。
- `contracts/record.json` 说明基础登记契约，`contracts/release.json` 说明签发契约。
- `data/sample.json` 提供本地冒烟数据。
- `tests/` 覆盖登记、签发幂等、文本分支、撤展、异议、调度重启与时点重建。

## 业务规则

- **签发**：`issue_release` 固定所引用的展品与证据快照；内容相同的重试返回原回执，签发编号相同但摘要不同拒绝覆盖。
- **文本分支**：`effective_from` 在未来时为待生效，新的年代判断可替换尚未生效的文字；公众看过的版本（生效中或已被接替）只能通过 `add_errata` 连续勘误。
- **撤展**：`withdraw_exhibit` 只关闭未来展示位置（跨期位置截断到撤展时点），已经发生的展示（如媒体开放日）不可删除，同时唤起引用该展品的叙事复核任务。
- **异议**：提出解释的专家不能独自结束异议，须由其他专家处理。
- **调度**：`advance_time`/`run_due` 激活待生效说明、关闭到期借展并撤展；每件待办记录在 `transitions` 表中，模拟时间推进或进程重启后仍只执行一次。
- **时点重建**：`state_at(unit_id, at)` 返回该单元当时展示的器物、各渠道生效文本（含当时可见的勘误）、采用的解释与未结束的异议。

## 运行

运行测试：`PYTHONPATH=src python3 -m unittest discover -s tests`

检查源码：`python3 -m compileall -q src tests`

验证样例：`PYTHONPATH=src python3 -m exhibition_narrative_release.cli validate data/sample.json`
