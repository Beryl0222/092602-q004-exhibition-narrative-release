"""端到端演示：媒体开放日、景泰蓝年代订正、借展提前撤回。

用 FakeClock 模拟时间推进，便于在命令行直接观察不同历史时点的叙事重建。
"""
from __future__ import annotations

from .clock import FakeClock
from .service import Service
from .store import FAR_FUTURE, Store

T0 = "2026-09-01T08:00:00+00:00"
MEDIA_DAY = "2026-10-01T09:00:00+00:00"
REVISION = "2026-10-05T10:00:00+00:00"
RECALL = "2026-10-10T10:00:00+00:00"
FUTURE_TEXT = "2026-11-01T09:00:00+00:00"


def build() -> tuple[Service, FakeClock]:
    clock = FakeClock(T0)
    svc = Service(Store(), clock)

    svc.define_unit("unit-origin", "器物流年")
    svc.define_unit("unit-craft", "匠心工艺", parent_id="unit-origin")
    svc.define_unit("unit-media", "宫廷之声", parent_id="unit-origin")
    svc.add_timeline_node("node-open", "媒体开放日", MEDIA_DAY)
    svc.add_term("term-cloisonne", "掐丝珐琅（景泰蓝）", "铜胎掐丝填釉烧制")

    svc.register_exhibit("ex-vase", "景泰蓝缠枝莲纹瓶", ["term-cloisonne"])
    svc.add_evidence("ev-mark-qianlong", "ex-vase", "款识", "底款近似乾隆款，初判清代",
                     "2026-09-02T00:00:00+00:00", recorder_id="lab")
    svc.add_evidence("ev-body-jingtai", "ex-vase", "胎体成分与纹饰比对",
                     "铜胎与掐丝符合明景泰官作特征", "2026-10-02T00:00:00+00:00",
                     recorder_id="lab")

    svc.register_exhibit("ex-loan-basin", "铜鎏金花盆", [])
    svc.register_loan("loan-basin", "ex-loan-basin", "某私人藏家",
                      "2026-09-20T00:00:00+00:00", "2026-12-31T18:00:00+00:00")

    svc.place("pl-vase-craft", "unit-craft", "ex-vase", "2026-09-20T00:00:00+00:00",
              FAR_FUTURE, slot="A-01")
    svc.place("pl-basin-origin", "unit-origin", "ex-loan-basin",
              "2026-09-20T00:00:00+00:00", FAR_FUTURE, slot="B-07")

    svc.propose_interpretation("ip-qing", "ex-vase", "expert-zhang", "清乾隆年间",
                               "底款近似乾隆款", ["ev-mark-qianlong"])
    svc.adopt_interpretation("ip-qing", by="curator-li")

    clock.set(MEDIA_DAY)
    svc.release({
        "release_id": "rel-media", "unit_id": "unit-craft", "curator_id": "curator-li",
        "summary": "媒体开放日三渠道定稿（清乾隆旧判）",
        "exhibit_ids": ["ex-vase"], "evidence_ids": ["ev-mark-qianlong"],
        "interp_ids": ["ip-qing"],
        "texts": [
            {"channel": "label", "body": "掐丝珐琅瓶，清乾隆年间。",
             "effective_from": MEDIA_DAY},
            {"channel": "guide", "body": "导览：此瓶为乾隆时期掐丝珐琅代表。",
             "effective_from": MEDIA_DAY},
            {"channel": "press", "body": "新闻资料：乾隆景泰蓝珍品亮相。",
             "effective_from": MEDIA_DAY},
        ],
    })
    # 一条 11 月才生效的未来新闻稿，随后将被订正替换。
    svc.release({
        "release_id": "rel-future", "unit_id": "unit-craft", "curator_id": "curator-li",
        "summary": "11月特展新闻稿（旧判，待生效）",
        "exhibit_ids": ["ex-vase"], "evidence_ids": ["ev-mark-qianlong"],
        "interp_ids": ["ip-qing"],
        "texts": [{"channel": "press", "body": "11月：乾隆专题（待生效旧文）",
                   "effective_from": FUTURE_TEXT}],
    })
    return svc, clock


def run() -> list[dict[str, object]]:
    svc, clock = build()
    timeline: list[dict[str, object]] = [
        {"stage": "媒体开放日当天", "at": MEDIA_DAY,
         "snapshot": svc.as_of(MEDIA_DAY)},
    ]

    # 媒体开放日后收到年代订正。
    clock.set(REVISION)
    receipt = svc.revise_dating({
        "release_id": "rel-revise", "unit_id": "unit-craft",
        "curator_id": "curator-wang", "exhibit_id": "ex-vase",
        "summary": "景泰蓝年代由清乾隆订正为明景泰",
        "interpretation": {
            "interpretation_id": "ip-ming", "expert_id": "expert-chen",
            "dating": "明景泰年间", "rationale": "铜胎成分与掐丝符合明景泰官作",
            "evidence_ids": ["ev-body-jingtai"]},
        "corrections": {
            "label": "勘误：此瓶经最新胎体检测，年代改判为明景泰年间。",
            "guide": "导览勘误：器物年代更新为明景泰。",
            "press": "新闻勘误：此前乾隆判定有误，应为明景泰官作。"},
        "replacements": {"press": "11月：明景泰官作专题（替换稿）"},
    })
    timeline.append({"stage": "年代订正后（已公开版本追加勘误，未来稿被替换）",
                     "at": REVISION, "receipt": receipt,
                     "snapshot": svc.as_of(REVISION)})

    # 借展方通知另一件展品提前撤回。
    clock.set(RECALL)
    recall = svc.recall_loan("loan-basin", reason="借展方提前撤回")
    timeline.append({"stage": "借展提前撤回（只关未来展位并唤起复核）",
                     "at": RECALL, "recall": recall,
                     "open_reviews": svc.open_reviews(),
                     "snapshot": svc.as_of(RECALL)})

    # 推进到 11 月，待生效替换稿到点，只执行一次。
    clock.set(FUTURE_TEXT)
    applied = svc.tick()
    timeline.append({"stage": "推进到11月，待生效稿各执行一次",
                     "at": FUTURE_TEXT, "applied": applied,
                     "snapshot": svc.as_of(FUTURE_TEXT)})
    return timeline
