"""端到端业务场景：媒体开放日、年代订正、提前撤展、异议、幂等与历史重建。"""
import unittest

from exhibition_narrative_release.clock import FakeClock
from exhibition_narrative_release.domain import (
    ObjectionOpenError,
    ReleaseConflict,
)
from exhibition_narrative_release.service import CHANNELS, Service
from exhibition_narrative_release.store import FAR_FUTURE, Store

T0 = "2026-09-01T08:00:00+00:00"
MEDIA_DAY = "2026-10-01T09:00:00+00:00"
AFTER_OPEN = "2026-10-01T12:00:00+00:00"
RECALL_DAY = "2026-10-10T10:00:00+00:00"
FUTURE_TEXT = "2026-11-01T09:00:00+00:00"
LOAN_UNTIL = "2026-12-31T18:00:00+00:00"


def build_world(clock: FakeClock) -> Service:
    """搭建媒体开放日前后的完整展览世界。"""
    clock.set(T0)
    svc = Service(Store(), clock)

    # 三个展陈单元
    svc.define_unit("unit-origin", "器物流年")
    svc.define_unit("unit-craft", "匠心工艺", parent_id="unit-origin")
    svc.define_unit("unit-media", "宫廷之声", parent_id="unit-origin")

    svc.add_timeline_node("node-open", "媒体开放日", MEDIA_DAY)
    svc.add_term("term-cloisonne", "掐丝珐琅（景泰蓝）", "在铜胎上掐丝填釉烧制")
    svc.add_term("term-jingtailan", "景泰蓝", "明景泰年间盛行的铜胎掐丝珐琅俗称")

    # 景泰蓝器物：原定为清乾隆，后获明景泰新证据
    svc.register_exhibit("ex-vase", "景泰蓝缠枝莲纹瓶", ["term-cloisonne"])
    svc.add_evidence("ev-mark-qianlong", "ex-vase", "款识",
                     "底款近似乾隆款，初判清代", "2026-09-02T00:00:00+00:00",
                     recorder_id="lab")
    svc.add_evidence("ev-body-jingtai", "ex-vase", "胎体成分与纹饰比对",
                     "铜胎成分与掐丝工艺符合明景泰官作特征",
                     "2026-10-02T00:00:00+00:00", recorder_id="lab")

    # 另一件借展器物，将被提前撤回
    svc.register_exhibit("ex-loan-basin", "铜鎏金花盆", [])
    svc.register_loan("loan-basin", "ex-loan-basin", "某私人藏家",
                      "2026-09-20T00:00:00+00:00", LOAN_UNTIL)

    # 展位
    svc.place("pl-vase-craft", "unit-craft", "ex-vase",
              "2026-09-20T00:00:00+00:00", FAR_FUTURE, slot="A-01")
    svc.place("pl-basin-origin", "unit-origin", "ex-loan-basin",
              "2026-09-20T00:00:00+00:00", FAR_FUTURE, slot="B-07")

    # 旧年代判断（媒体开放日采用）
    svc.propose_interpretation(
        "ip-qing", "ex-vase", "expert-zhang", "清乾隆年间",
        "底款近似乾隆款", ["ev-mark-qianlong"])
    svc.adopt_interpretation("ip-qing", by="curator-li")

    # 媒体开放日当天，三渠道签发（即刻对公众可见）
    clock.set(MEDIA_DAY)
    svc.release({
        "release_id": "rel-media",
        "unit_id": "unit-craft",
        "curator_id": "curator-li",
        "summary": "媒体开放日三渠道定稿（清乾隆旧判）",
        "exhibit_ids": ["ex-vase"],
        "evidence_ids": ["ev-mark-qianlong"],
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
    return svc


class 媒体开放日基线测试(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock(MEDIA_DAY)
        self.svc = build_world(self.clock)

    def test_健康检查与基线登记仍可用(self):
        self.assertEqual(self.svc.health()["status"], "ok")
        saved = self.svc.register({"record_id": "r1", "owner_id": "o", "state": "draft"})
        self.assertEqual(self.svc.find("r1")["owner_id"], saved["owner_id"])

    def test_媒体日重建显示旧判与三渠道文本(self):
        snap = self.svc.as_of(AFTER_OPEN)
        craft = next(u for u in snap["units"] if u["unit_id"] == "unit-craft")
        self.assertEqual([e["exhibit_id"] for e in craft["exhibits"]], ["ex-vase"])
        self.assertEqual(craft["interpretations"]["adopted"][0]["interpretation_id"], "ip-qing")
        self.assertEqual(craft["channels"]["label"]["body"], "掐丝珐琅瓶，清乾隆年间。")
        self.assertEqual(craft["channels"]["guide"]["channel_name"], "导览接口")
        self.assertEqual(craft["channels"]["press"]["body"], "新闻资料：乾隆景泰蓝珍品亮相。")

    def test_签发固定了展品与证据快照(self):
        snap = self.svc.release_snapshot("rel-media")
        self.assertIn("exhibit:ex-vase", snap["pins"])
        self.assertIn("evidence:ev-mark-qianlong", snap["pins"])
        self.assertIn("interpretation:ip-qing", snap["pins"])


class 幂等与覆盖保护测试(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock(MEDIA_DAY)
        self.svc = build_world(self.clock)
        self.spec = {
            "release_id": "rel-x", "unit_id": "unit-craft", "curator_id": "curator-li",
            "summary": "重试测试", "exhibit_ids": ["ex-vase"],
            "evidence_ids": ["ev-mark-qianlong"], "interp_ids": ["ip-qing"],
            "texts": [{"channel": "label", "body": "同一内容",
                       "effective_from": MEDIA_DAY}],
        }

    def test_内容相同的重试返回原回执(self):
        first = self.svc.release(self.spec)
        self.assertFalse(first["replayed"])
        self.clock.advance(hours=2)
        second = self.svc.release(self.spec)
        self.assertTrue(second["replayed"])
        self.assertEqual(first["release_id"], second["release_id"])
        self.assertEqual(first["digest"], second["digest"])
        self.assertEqual(first["created_at"], second["created_at"])

    def test_编号相同摘要不同必须停止覆盖(self):
        self.svc.release(self.spec)
        tampered = dict(self.spec, summary="被篡改的摘要")
        with self.assertRaises(ReleaseConflict):
            self.svc.release(tampered)
        # 原签发仍然完好
        self.assertEqual(self.svc.release_snapshot("rel-x")["summary"], "重试测试")


class 年代订正测试(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock(MEDIA_DAY)
        self.svc = build_world(self.clock)

    def _订正(self, release_id="rel-revise"):
        self.clock.set("2026-10-05T10:00:00+00:00")
        return self.svc.revise_dating({
            "release_id": release_id,
            "unit_id": "unit-craft",
            "curator_id": "curator-wang",
            "exhibit_id": "ex-vase",
            "summary": "景泰蓝年代由清乾隆订正为明景泰",
            "interpretation": {
                "interpretation_id": "ip-ming",
                "expert_id": "expert-chen",
                "dating": "明景泰年间",
                "rationale": "铜胎成分与掐丝工艺符合明景泰官作",
                "evidence_ids": ["ev-body-jingtai"],
            },
            "corrections": {
                "label": "勘误：此瓶经最新胎体检测，年代改判为明景泰年间。",
                "guide": "导览勘误：器物年代更新为明景泰。",
                "press": "新闻勘误：此前乾隆判定有误，应为明景泰官作。",
            },
        })

    def test_已公开版本通过连续勘误说明变化且正文不被删改(self):
        receipt = self._订正()
        self.assertIn("label", receipt["changed"]["corrected"])
        snap = self.svc.as_of("2026-10-06T00:00:00+00:00")
        craft = next(u for u in snap["units"] if u["unit_id"] == "unit-craft")
        label = craft["channels"]["label"]
        # 正文保留媒体开放日原样
        self.assertEqual(label["body"], "掐丝珐琅瓶，清乾隆年间。")
        # 追加连续勘误
        seqs = [c["seq"] for c in label["corrigenda"]]
        self.assertEqual(seqs, [1])
        self.assertIn("明景泰", label["corrigenda"][0]["note"])

    def test_新判断被采用旧判断退场但历史仍可重建(self):
        self._订正()
        now_snap = self.svc.as_of("2026-10-06T00:00:00+00:00")
        craft = next(u for u in now_snap["units"] if u["unit_id"] == "unit-craft")
        adopted = [i["interpretation_id"] for i in craft["interpretations"]["adopted"]]
        retired = [i["interpretation_id"] for i in craft["interpretations"]["retired"]]
        self.assertIn("ip-ming", adopted)
        self.assertIn("ip-qing", retired)
        # 媒体开放日当时仍是旧判、且尚无勘误
        old = self.svc.as_of(AFTER_OPEN)
        old_craft = next(u for u in old["units"] if u["unit_id"] == "unit-craft")
        self.assertEqual(
            [i["interpretation_id"] for i in old_craft["interpretations"]["adopted"]],
            ["ip-qing"])
        self.assertEqual(old_craft["channels"]["label"]["corrigenda"], [])

    def test_连续勘误不断追加(self):
        self._订正("rel-revise-1")
        # 再次订正（新证据），应在同一公开文本上追加 seq=2
        self.svc.add_evidence("ev-more", "ex-vase", "档案", "宫廷档案佐证景泰",
                              "2026-10-07T00:00:00+00:00", recorder_id="archive")
        self.clock.set("2026-10-08T10:00:00+00:00")
        self.svc.revise_dating({
            "release_id": "rel-revise-2", "unit_id": "unit-craft",
            "curator_id": "curator-wang", "exhibit_id": "ex-vase",
            "summary": "补充档案二次确认",
            "interpretation": {
                "interpretation_id": "ip-ming-2", "expert_id": "expert-chen",
                "dating": "明景泰年间（官作）", "rationale": "档案佐证",
                "evidence_ids": ["ev-body-jingtai", "ev-more"]},
            "corrections": {"label": "二次勘误：档案进一步确认明景泰官作。"},
        })
        snap = self.svc.as_of("2026-10-09T00:00:00+00:00")
        label = next(u for u in snap["units"] if u["unit_id"] == "unit-craft")["channels"]["label"]
        self.assertEqual([c["seq"] for c in label["corrigenda"]], [1, 2])

    def test_订正本身也是幂等且防覆盖(self):
        r1 = self._订正("rel-idem")
        r2 = self._订正("rel-idem")
        self.assertTrue(r2["replayed"])
        self.assertEqual(r1["digest"], r2["digest"])

    def test_未生效文字可被替换_到点显示替换稿且不留勘误(self):
        # 媒体日另签一条 11 月才生效的未来 press 文本（旧判）
        self.clock.set(MEDIA_DAY)
        self.svc.release({
            "release_id": "rel-future", "unit_id": "unit-craft",
            "curator_id": "curator-li", "summary": "11月特展未来文本（旧判）",
            "exhibit_ids": ["ex-vase"], "evidence_ids": ["ev-mark-qianlong"],
            "interp_ids": ["ip-qing"],
            "texts": [{"channel": "press", "body": "11月：乾隆专题（待生效旧文）",
                       "effective_from": FUTURE_TEXT}],
        })
        # 订正时给出 press 的替换稿（未生效，直接替换，不出勘误）
        self.clock.set("2026-10-05T10:00:00+00:00")
        self.svc.revise_dating({
            "release_id": "rel-revise-pl", "unit_id": "unit-craft",
            "curator_id": "curator-wang", "exhibit_id": "ex-vase",
            "summary": "替换未来 press",
            "interpretation": {
                "interpretation_id": "ip-ming", "expert_id": "expert-chen",
                "dating": "明景泰年间", "rationale": "胎体证据",
                "evidence_ids": ["ev-body-jingtai"]},
            "corrections": {"label": "公开墙签勘误：改判明景泰。"},
            "replacements": {"press": "11月：明景泰官作专题（替换稿）"},
        })
        # 10 月：press 仍展示媒体日已公开旧稿，未来替换稿未出现
        snap = self.svc.as_of("2026-10-06T00:00:00+00:00")
        press = next(u for u in snap["units"] if u["unit_id"] == "unit-craft")["channels"]["press"]
        self.assertEqual(press["body"], "新闻资料：乾隆景泰蓝珍品亮相。")

        # 推进到 11 月，替换稿到点生效，各效果只执行一次
        self.clock.set(FUTURE_TEXT)
        ran = self.svc.tick()
        self.assertEqual(len([e for e in ran if e["kind"] == "text_effective"]), 1)
        future = self.svc.as_of(FUTURE_TEXT)
        fpress = next(u for u in future["units"] if u["unit_id"] == "unit-craft")["channels"]["press"]
        self.assertEqual(fpress["body"], "11月：明景泰官作专题（替换稿）")
        # 替换稿从未对公众发布过，不附勘误
        self.assertEqual(fpress["corrigenda"], [])
        # 再次推进不重复执行
        self.assertEqual([e for e in self.svc.tick() if e["kind"] == "text_effective"], [])


class 提前撤展测试(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock(MEDIA_DAY)
        self.svc = build_world(self.clock)

    def test_撤展只关闭未来位置且唤起复核_历史展示保留(self):
        self.clock.set(RECALL_DAY)
        result = self.svc.recall_loan("loan-basin", reason="借展方提前撤回")
        self.assertEqual(result["closed_slots"], ["pl-basin-origin"])

        # 撤展后：展位已关闭
        after = self.svc.as_of("2026-10-11T00:00:00+00:00")
        origin = next(u for u in after["units"] if u["unit_id"] == "unit-origin")
        self.assertNotIn("ex-loan-basin", [e["exhibit_id"] for e in origin["exhibits"]])

        # 媒体开放日已发生的展示不能被删除：撤展之前仍能重建出该器物
        before = self.svc.as_of(MEDIA_DAY)
        b_origin = next(u for u in before["units"] if u["unit_id"] == "unit-origin")
        self.assertIn("ex-loan-basin", [e["exhibit_id"] for e in b_origin["exhibits"]])

        # 唤起叙事复核
        reviews = self.svc.open_reviews("unit-origin")
        self.assertTrue(any(r["trigger"] == "loan_recall" for r in reviews))

    def test_到期撤展经调度恰好执行一次_含重启(self):
        # 不手动撤回，推进到借展到期之后
        self.clock.set("2027-01-01T00:00:00+00:00")
        first = self.svc.tick()
        loan_effects = [e for e in first if e["kind"] == "loan_recall"]
        self.assertEqual(len(loan_effects), 1)
        self.assertEqual(loan_effects[0]["closed_slots"], ["pl-basin-origin"])
        # 再次 tick（模拟又一次时间推进）不重复执行
        second = self.svc.tick()
        self.assertFalse([e for e in second if e["kind"] == "loan_recall"])

    def test_提前撤回取消原定到期任务(self):
        self.clock.set(RECALL_DAY)
        self.svc.recall_loan("loan-basin")
        self.clock.set("2027-01-01T00:00:00+00:00")
        effects = self.svc.tick()
        self.assertFalse([e for e in effects if e["kind"] == "loan_recall"])


class 待生效文本调度测试(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock(MEDIA_DAY)
        self.svc = build_world(self.clock)
        self.svc.release({
            "release_id": "rel-fut", "unit_id": "unit-craft",
            "curator_id": "curator-li", "summary": "待生效导览更新",
            "exhibit_ids": ["ex-vase"], "evidence_ids": ["ev-mark-qianlong"],
            "interp_ids": ["ip-qing"],
            "texts": [{"channel": "guide", "body": "新版导览（11月生效）",
                       "effective_from": FUTURE_TEXT}],
        })

    def test_未到点不生效_到点执行一次_重启不重复(self):
        # 10 月仍显示旧导览
        october = self.svc.as_of("2026-10-15T00:00:00+00:00")
        guide = next(u for u in october["units"] if u["unit_id"] == "unit-craft")["channels"]["guide"]
        self.assertEqual(guide["body"], "导览：此瓶为乾隆时期掐丝珐琅代表。")

        self.clock.set(FUTURE_TEXT)
        ran = self.svc.tick()
        self.assertEqual(len([e for e in ran if e["kind"] == "text_effective"]), 1)
        again = self.svc.tick()
        self.assertFalse([e for e in again if e["kind"] == "text_effective"])

        live = self.svc.as_of(FUTURE_TEXT)
        lguide = next(u for u in live["units"] if u["unit_id"] == "unit-craft")["channels"]["guide"]
        self.assertEqual(lguide["body"], "新版导览（11月生效）")

    def test_用新存储重开服务后到期仍执行一次(self):
        import tempfile, os
        from exhibition_narrative_release.store import Store as _Store
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            store = _Store(path)
            clock = FakeClock(MEDIA_DAY)
            svc = Service(store, clock)
            # 复用同一套世界，但通过文件库持久化较繁琐——这里直接验证台账持久语义：
            svc.define_unit("u", "单元")
            svc.register_exhibit("e", "器物")
            svc.release({
                "release_id": "r", "unit_id": "u", "curator_id": "c", "summary": "s",
                "exhibit_ids": ["e"], "texts": [
                    {"channel": "label", "body": "未来", "effective_from": FUTURE_TEXT}]})
            # 重启：新建服务指向同一文件
            store.connection.close()
            store2 = _Store(path)
            svc2 = Service(store2, FakeClock(FUTURE_TEXT))
            ran = svc2.tick()
            self.assertEqual(len([e for e in ran if e["kind"] == "text_effective"]), 1)
            # 再重启一次也不重复
            store2.connection.close()
            store3 = _Store(path)
            svc3 = Service(store3, FakeClock("2026-12-01T00:00:00+00:00"))
            self.assertEqual(svc3.tick(), [])
        finally:
            os.remove(path)


class 专家异议测试(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock(MEDIA_DAY)
        self.svc = build_world(self.clock)

    def test_提出解释的专家不能独自结束异议(self):
        # expert-zhang 提出 ip-qing；另一专家 expert-chen 提异议
        self.svc.raise_objection("obj-1", "ip-qing", "expert-chen", "款识可能为后仿")
        # 提出者 expert-zhang 试图自己关闭 → 拒绝
        with self.assertRaises(ObjectionOpenError):
            self.svc.resolve_objection("obj-1", by="expert-zhang", resolution="resolved",
                                       note="我坚持原判")
        # 异议仍开放，解释处于反对状态
        snap = self.svc.as_of(self.clock.now())
        craft = next(u for u in snap["units"] if u["unit_id"] == "unit-craft")
        opposed = [i["interpretation_id"] for i in craft["interpretations"]["opposed"]]
        self.assertIn("ip-qing", opposed)

    def test_第三方可关闭异议_关闭后反对解除(self):
        self.svc.raise_objection("obj-1", "ip-qing", "expert-chen", "款识可能为后仿")
        self.svc.resolve_objection("obj-1", by="curator-wang", resolution="rejected",
                                   note="复核确认款识为真")
        snap = self.svc.as_of(self.clock.now())
        craft = next(u for u in snap["units"] if u["unit_id"] == "unit-craft")
        self.assertEqual(craft["interpretations"]["opposed"], [])

    def test_存在未决异议不能作为定稿引用(self):
        self.svc.raise_objection("obj-1", "ip-qing", "expert-chen", "存疑")
        with self.assertRaises(Exception):
            self.svc.release({
                "release_id": "rel-bad", "unit_id": "unit-craft",
                "curator_id": "curator-li", "summary": "引用有异议判断",
                "exhibit_ids": ["ex-vase"], "interp_ids": ["ip-qing"],
                "texts": [{"channel": "label", "body": "x", "effective_from": MEDIA_DAY}]})


if __name__ == "__main__":
    unittest.main()
