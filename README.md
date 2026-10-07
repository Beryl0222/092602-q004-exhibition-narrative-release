# 宫廷艺术展陈叙事签发库

为恭王府展览内容团队提供**统一的叙事签发库**，把展陈层级、时间轴节点、展品身份、
年代证据、工艺术语、借展可见期、说明文本分支、专家异议与发布渠道收敛到同一条
只追加（append-only）的事实链上，保证墙签、导览接口、新闻资料三个展陈单元在任何
历史时点都彼此一致、可重建、可追溯。

## 核心不变量

- **签发即固定引用**：每次签发把所引用展品、年代证据与解释做快照（`pins`），
  事后展品改判或证据更新都不改变既有回执。
- **重试幂等**：同一 `release_id` 且规范内容摘要相同的重试，返回**原回执**
  （`replayed=true`）；编号相同但摘要不同则抛出 `ReleaseConflict`，**停止覆盖**。
- **已发生展示不可删除**：媒体开放日已经发生的器物展示、文本与判断永久保留。
- **临时撤展只关未来**：提前撤回仅缩短未来展示区间（关闭未来墙签位），并唤起相关
  单元的叙事复核；过去区间原样保留，历史重建不受影响。
- **年代订正两条路径**：
  - 尚未生效（未对公众公开）的文字 → 直接替换/撤下，不留勘误；
  - 公众已看过的版本 → 正文不动，只**连续追加勘误**（seq 1、2、…）说明变化。
- **异议分权**：提出解释的专家**不能独自结束**针对该解释的异议
  （`ObjectionOpenError`），须由策展团队或其他专家复核关闭；存在未决异议的解释
  不能作为定稿引用。
- **到期效果恰好一次**：待生效说明与到期撤展登记在唯一台账上，`tick()` 以
  claim+apply 同事务推进；模拟时间推进或进程重启后，每个效果只执行一次。
- **历史时点重建**：`as_of(when)` 准确还原各单元当时展示的器物、采用与反对的解释、
  各渠道文本以及当时已发布的勘误链。

## 目录

- `src/exhibition_narrative_release/domain.py` — 不可变领域记录、时间规范化、摘要与错误类型
- `src/exhibition_narrative_release/clock.py` — `SystemClock` 与可模拟推进的 `FakeClock`
- `src/exhibition_narrative_release/store.py` — SQLite 表结构、只追加写入、到期效果唯一台账
- `src/exhibition_narrative_release/service.py` — 签发、订正、撤展、异议、调度与 `as_of` 查询
- `src/exhibition_narrative_release/demo.py` — 媒体开放日→年代订正→提前撤展的端到端演示
- `contracts/` — 基础登记与叙事签发输入契约
- `tests/` — 基线与完整业务场景测试

发布渠道：`label`（墙签）、`guide`（导览接口）、`press`（新闻资料）。

## 运行

```bash
# 全部测试
PYTHONPATH=src python3 -m unittest discover -s tests

# 语法检查
python3 -m compileall -q src tests

# 端到端故事演示（分阶段输出历史重建）
PYTHONPATH=src python3 -m exhibition_narrative_release.cli story

# 兼容基线：登记样例记录
PYTHONPATH=src python3 -m exhibition_narrative_release.cli validate data/sample.json
```

仅依赖 Python 标准库与本地 SQLite，无需外部服务。

## 典型用法

```python
from exhibition_narrative_release.clock import FakeClock
from exhibition_narrative_release.service import Service
from exhibition_narrative_release.store import Store

svc = Service(Store(), FakeClock("2026-10-01T09:00:00+00:00"))
svc.define_unit("unit-craft", "匠心工艺")
svc.register_exhibit("ex-vase", "景泰蓝缠枝莲纹瓶", ["term-cloisonne"])
svc.add_evidence("ev-1", "ex-vase", "款识", "底款近似乾隆款", recorder_id="lab")
svc.propose_interpretation("ip-qing", "ex-vase", "expert-zhang",
                           "清乾隆年间", "底款", ["ev-1"])
svc.adopt_interpretation("ip-qing", by="curator-li")

receipt = svc.release({
    "release_id": "rel-media", "unit_id": "unit-craft", "curator_id": "curator-li",
    "summary": "媒体开放日定稿", "exhibit_ids": ["ex-vase"],
    "evidence_ids": ["ev-1"], "interp_ids": ["ip-qing"],
    "texts": [{"channel": "label", "body": "掐丝珐琅瓶，清乾隆年间。",
               "effective_from": "2026-10-01T09:00:00+00:00"}],
})

# 任意历史时点重建
svc.as_of("2026-10-01T12:00:00+00:00")
```

## 数据模型一览

| 主题 | 表 / 记录 | 只追加方式 |
| --- | --- | --- |
| 展陈层级 | `units` | parent 形成树 |
| 时间轴 | `timeline_nodes` | 节点固定 |
| 工艺术语 | `terms` | 受控词表 |
| 展品身份 | `exhibits` | 身份不变，新判断走解释 |
| 年代证据 | `evidence` | 证据只追加，不回改 |
| 解释/异议 | `interpretations`、`interpretation_events`、`objections` | 状态以事件流表达，可重建任一时点 |
| 借展/展位 | `loans`、`placements` | 撤展=缩短区间，不删行 |
| 文本分支 | `branches` | 待生效可替换；已公开仅追加 `corrigenda` |
| 签发回执 | `releases`（含 `pins` 快照、`digest`） | 幂等 + 防覆盖 |
| 到期效果 | `scheduled_effects` | pending/applied/cancelled 唯一台账，恰好一次 |
| 叙事复核 | `review_tasks` | 撤展/异议/订正触发 |
