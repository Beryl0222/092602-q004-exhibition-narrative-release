import os
import tempfile
import unittest

from exhibition_narrative_release.domain import ConflictError, NotFoundError, StateError
from exhibition_narrative_release.service import Service
from exhibition_narrative_release.store import Store

T0 = "2026-10-01T09:00:00+00:00"
T1 = "2026-10-05T09:00:00+00:00"
T2 = "2026-10-10T09:00:00+00:00"
T3 = "2026-10-20T09:00:00+00:00"
MEDIA_OPEN = "2026-09-20T09:00:00+00:00"
MEDIA_CLOSE = "2026-09-20T17:00:00+00:00"


def make_service(now=T0, path=":memory:"):
    service = Service(Store(path), now=now)
    service.add_unit("unit-hall", "景泰蓝展厅")
    service.add_channel("wall", "墙签")
    service.add_channel("guide", "导览接口")
    service.add_channel("press", "新闻资料")
    service.add_exhibit("ex-cloisonne", "掐丝珐琅缠枝莲纹瓶", "景泰蓝", lender_id="palace-museum")
    service.add_exhibit("ex-jade", "青玉山子", "玉器")
    service.add_evidence(
        "ev-kangxi", "ex-cloisonne", "康熙朝宫廷造办处制",
        method="款识与珐琅料分析", source="故宫器物部",
    )
    return service


def release_payload(release_id, body, effective_from, channel="wall",
                    exhibits=("ex-cloisonne",), evidence=("ev-kangxi",)):
    return {
        "release_id": release_id, "unit_id": "unit-hall", "channel_id": channel,
        "body": body, "exhibit_ids": list(exhibits), "evidence_ids": list(evidence),
        "effective_from": effective_from,
    }


class 签发测试(unittest.TestCase):
    def test_签发固定引用且重试返回原回执(self):
        service = make_service()
        payload = release_payload("rel-1", "清康熙掐丝珐琅缠枝莲纹瓶", T2)
        receipt = service.issue_release(payload)
        self.assertEqual(receipt["status"], "pending")
        self.assertEqual(receipt["version_id"], "tv-rel-1")
        self.assertEqual(service.issue_release(dict(payload)), receipt)
        self.assertEqual(service.release_receipt("rel-1"), receipt)
        pins = service.release_pins("rel-1")
        self.assertEqual(
            {(pin["pin_type"], pin["ref_id"]) for pin in pins},
            {("exhibit", "ex-cloisonne"), ("evidence", "ev-kangxi")},
        )
        self.assertIn("康熙", next(p["snapshot"] for p in pins if p["pin_type"] == "evidence"))

    def test_编号相同摘要不同必须停止覆盖(self):
        service = make_service()
        payload = release_payload("rel-1", "清康熙掐丝珐琅缠枝莲纹瓶", T2)
        receipt = service.issue_release(payload)
        with self.assertRaises(ConflictError):
            service.issue_release({**payload, "body": "清乾隆掐丝珐琅缠枝莲纹瓶"})
        self.assertEqual(service.release_receipt("rel-1"), receipt)

    def test_签发引用的展品与证据必须存在(self):
        service = make_service()
        with self.assertRaises(NotFoundError):
            service.issue_release(release_payload("rel-x", "文字", T2, exhibits=("ghost",)))
        with self.assertRaises(NotFoundError):
            service.issue_release(release_payload("rel-y", "文字", T2, evidence=("ghost",)))


class 文本分支测试(unittest.TestCase):
    def test_新年代判断替换尚未生效的文字(self):
        service = make_service()
        service.issue_release(release_payload("rel-1", "康熙朝说明", T2))
        service.add_evidence("ev-qianlong", "ex-cloisonne", "乾隆朝造办处改制")
        service.issue_release(release_payload("rel-2", "乾隆朝说明", T3, evidence=("ev-qianlong",)))
        self.assertEqual(service.get_text_version("tv-rel-1").status, "replaced")
        self.assertEqual(service.get_text_version("tv-rel-2").status, "pending")

    def test_公众看过的版本只能连续勘误(self):
        service = make_service()
        service.issue_release(release_payload("rel-1", "康熙朝说明", T1))
        service.advance_time(T1)
        service.issue_release(release_payload("rel-2", "待生效说明", T3))
        with self.assertRaises(StateError):
            service.add_errata("tv-rel-2", "尚未生效不能勘误")
        first = service.add_errata("tv-rel-1", "年代判断已订正，详见后续说明")
        second = service.add_errata("tv-rel-1", "补充釉料分析结论")
        self.assertEqual((first.seq, second.seq), (1, 2))
        service.issue_release(release_payload("rel-3", "乾隆朝说明", T1))
        self.assertEqual(service.get_text_version("tv-rel-1").status, "superseded")
        third = service.add_errata("tv-rel-1", "已被新版接替")
        self.assertEqual(third.seq, 3)


class 撤展测试(unittest.TestCase):
    def test_临时撤展只关闭未来展示位置(self):
        service = make_service()
        service.schedule_display("slot-media", "unit-hall", "ex-jade", MEDIA_OPEN, MEDIA_CLOSE)
        service.schedule_display("slot-now", "unit-hall", "ex-jade",
                                 "2026-09-30T09:00:00+00:00", "2026-10-15T17:00:00+00:00")
        service.schedule_display("slot-future", "unit-hall", "ex-jade", T3,
                                 "2026-10-25T17:00:00+00:00")
        service.issue_release(release_payload("rel-1", "青玉山子说明", T1, exhibits=("ex-jade",),
                                              evidence=()))
        service.advance_time(T1)
        result = service.withdraw_exhibit("ex-jade", from_time=T1, reason="借展方提前撤回")
        self.assertEqual(result["cancelled_slots"], ["slot-future"])
        self.assertEqual(result["trimmed_slots"], ["slot-now"])
        self.assertEqual(service.get_slot("slot-now").finish, T1)
        # 媒体开放日已经发生的展示不能被删除
        self.assertEqual(service.get_slot("slot-media").status, "scheduled")
        state = service.state_at("unit-hall", "2026-09-20T12:00:00+00:00")
        self.assertIn("ex-jade", {item["exhibit_id"] for item in state["exhibits"]})
        # 唤起相关叙事复核
        self.assertEqual(result["review_tasks"], ["rvw-tv-rel-1-ex-jade"])
        self.assertEqual(service.list_review_tasks()[0].status, "open")


class 异议测试(unittest.TestCase):
    def test_提出解释的专家不能独自结束异议(self):
        service = make_service()
        service.propose_interpretation("it-1", "ex-cloisonne", "ev-kangxi",
                                       proposed_by="expert-a", body="康熙朝")
        service.raise_objection("ob-1", "it-1", raised_by="expert-b", detail="款识存疑")
        with self.assertRaises(StateError):
            service.resolve_objection("ob-1", resolved_by="expert-a")
        done = service.resolve_objection("ob-1", resolved_by="expert-c",
                                         resolution="复核后维持原判断")
        self.assertEqual(done.status, "resolved")
        with self.assertRaises(StateError):
            service.resolve_objection("ob-1", resolved_by="expert-c")


class 调度与重启测试(unittest.TestCase):
    def test_待生效说明与到期撤展各执行一次(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "narrative.db")
            service = make_service(path=path)
            service.issue_release(release_payload("rel-1", "康熙朝说明", T2))
            service.add_loan_window("loan-1", "ex-jade", visible_from=T0, visible_to=T3)
            service.schedule_display("slot-future", "unit-hall", "ex-jade", T3,
                                     "2026-10-30T00:00:00+00:00")
            first = service.advance_time(T2)
            self.assertEqual(first["activated"], ["tv-rel-1"])
            self.assertEqual(service.advance_time(T2)["activated"], [])
            # 模拟进程重启：同一库文件、新的服务实例
            restarted = Service(Store(path), now="2026-10-21T09:00:00+00:00")
            due = restarted.run_due()
            self.assertEqual(due["activated"], [])
            self.assertEqual([item["loan_id"] for item in due["expired_loans"]], ["loan-1"])
            self.assertEqual(restarted.get_slot("slot-future").status, "cancelled")
            self.assertEqual(restarted.get_loan("loan-1").closed_at, due["at"])
            again = restarted.run_due()
            self.assertEqual(again["activated"], [])
            self.assertEqual(again["expired_loans"], [])


class 时点重建测试(unittest.TestCase):
    def test_重建历史时点的器物解释与渠道文本(self):
        service = make_service()
        service.schedule_display("slot-media", "unit-hall", "ex-cloisonne",
                                 MEDIA_OPEN, "2026-10-30T17:00:00+00:00")
        service.issue_release(release_payload("rel-wall", "清康熙掐丝珐琅缠枝莲纹瓶", T1))
        service.issue_release(release_payload("rel-press", "新闻稿：康熙朝景泰蓝亮相", T1,
                                              channel="press"))
        service.propose_interpretation("it-1", "ex-cloisonne", "ev-kangxi",
                                       proposed_by="expert-a", body="康熙朝")
        service.raise_objection("ob-1", "it-1", raised_by="expert-b", detail="款识存疑")
        service.advance_time(T1)
        # 年代订正：登记新证据并签发未来生效的新文字
        service.add_evidence("ev-qianlong", "ex-cloisonne", "乾隆朝造办处改制", method="XRF 釉料分析")
        service.propose_interpretation("it-2", "ex-cloisonne", "ev-qianlong",
                                       proposed_by="expert-c", body="乾隆朝")
        service.issue_release(release_payload("rel-wall-2", "清乾隆掐丝珐琅缠枝莲纹瓶", T3,
                                              evidence=("ev-qianlong",)))
        service.add_errata("tv-rel-wall", "年代判断已订正，详见新版说明")
        # 媒体开放日后的历史时点：旧文字仍生效、带勘误、异议未结束
        moment = "2026-10-08T09:00:00+00:00"
        state = service.state_at("unit-hall", moment)
        self.assertEqual({item["exhibit_id"] for item in state["exhibits"]}, {"ex-cloisonne"})
        self.assertEqual(state["texts"]["wall"]["body"], "清康熙掐丝珐琅缠枝莲纹瓶")
        self.assertEqual(len(state["texts"]["wall"]["errata"]), 1)
        self.assertEqual(state["texts"]["press"]["body"], "新闻稿：康熙朝景泰蓝亮相")
        self.assertEqual({item["interpretation_id"] for item in state["adopted"]}, {"it-1"})
        self.assertEqual({item["objection_id"] for item in state["contested"]}, {"ob-1"})
        # 异议由第三方结束后，时间推进到新文字生效
        service.advance_time(T2)
        service.resolve_objection("ob-1", resolved_by="expert-c")
        service.advance_time(T3)
        later = service.state_at("unit-hall", "2026-10-25T09:00:00+00:00")
        self.assertEqual(later["texts"]["wall"]["body"], "清乾隆掐丝珐琅缠枝莲纹瓶")
        self.assertEqual(later["texts"]["wall"]["errata"], [])
        self.assertEqual(later["contested"], [])
        self.assertEqual(
            {item["interpretation_id"] for item in later["adopted"]}, {"it-1", "it-2"}
        )
        # 历史时点不受后续变化影响
        again = service.state_at("unit-hall", moment)
        self.assertEqual(again["texts"]["wall"]["body"], "清康熙掐丝珐琅缠枝莲纹瓶")
        self.assertEqual({item["objection_id"] for item in again["contested"]}, {"ob-1"})


if __name__ == "__main__":
    unittest.main()
