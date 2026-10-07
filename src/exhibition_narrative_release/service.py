"""宫廷艺术展陈叙事签发的应用服务。

围绕策展团队的真实流程组织用例：

- 展陈层级 / 时间轴 / 术语 / 展品 / 证据 的登记；
- 解释（年代判断）的提出、采用、异议与解决——提出者不能独自结束异议；
- 叙事签发：固定所引用展品与证据的快照，同内容重试返回原回执，
  同编号异摘要拒绝覆盖；
- 年代订正：尚未生效的文字可被替换，公众已看过的版本只追加连续勘误；
- 提前撤展：只关闭未来展示位置并唤起叙事复核，已发生展示不删除；
- 到期调度：待生效说明与到期撤展在时间推进或重启后各执行一次；
- 历史时点重建：准确还原各单元当时的器物、采用/反对解释与各渠道文本。
"""
from __future__ import annotations

from typing import Any

from .clock import Clock, SystemClock
from .domain import (
    Corrigendum,
    Evidence,
    Exhibit,
    Interpretation,
    Loan,
    NarrativeError,
    Objection,
    ObjectionOpenError,
    Placement,
    Receipt,
    Record,
    ReferenceMismatch,
    Release,
    ReleaseConflict,
    ReviewTask,
    ScheduledEffect,
    Term,
    TextBranch,
    TimelineNode,
    Unit,
    canonical_json,
    digest_of,
    normalize_iso,
    parse_utc,
)
from .store import FAR_FUTURE, Store

CHANNELS = ("label", "guide", "press")
CHANNEL_NAME = {"label": "墙签", "guide": "导览接口", "press": "新闻资料"}


class Service:
    def __init__(self, store: Store | None = None, clock: Clock | None = None) -> None:
        self.store = store or Store()
        self.clock = clock or SystemClock()

    def _now(self) -> str:
        return normalize_iso(self.clock.now())

    # ------------------------------------------------------------------
    # 兼容基线
    # ------------------------------------------------------------------
    def health(self) -> dict[str, str]:
        return {"service": "exhibition_narrative_release", "status": "ok"}

    def register(self, payload: dict[str, object]) -> dict[str, object]:
        required = ("record_id", "owner_id", "state")
        missing = [name for name in required if not str(payload.get(name, "")).strip()]
        if missing:
            raise ValueError("缺少必要字段：" + "、".join(missing))
        record = Record(
            record_id=str(payload["record_id"]),
            owner_id=str(payload["owner_id"]),
            state=str(payload["state"]),
            revision=int(payload.get("revision", 1)),
        )
        return self.store.add(record).__dict__.copy()

    def find(self, record_id: str) -> dict[str, object] | None:
        value = self.store.get_record(record_id)
        return value.__dict__.copy() if value else None

    # ------------------------------------------------------------------
    # 目录：层级 / 时间轴 / 术语
    # ------------------------------------------------------------------
    def define_unit(self, unit_id: str, title: str, parent_id: str = "") -> str:
        now = self._now()
        self.store.add_unit(Unit(unit_id=unit_id, title=title, parent_id=parent_id,
                                 created_at=now))
        return unit_id

    def add_timeline_node(self, node_id: str, label: str, at: str, unit_id: str = "") -> str:
        self.store.add_timeline_node(
            TimelineNode(node_id=node_id, label=label, at=normalize_iso(at), unit_id=unit_id)
        )
        return node_id

    def add_term(self, term_id: str, name: str, definition: str = "") -> str:
        self.store.add_term(Term(term_id=term_id, name=name, definition=definition))
        return term_id

    # ------------------------------------------------------------------
    # 展品与证据
    # ------------------------------------------------------------------
    def register_exhibit(self, exhibit_id: str, title: str,
                         term_ids: list[str] | tuple[str, ...] = ()) -> str:
        now = self._now()
        for tid in term_ids:
            if not self.store.get_term(tid):
                raise ReferenceMismatch(f"工艺术语不存在：{tid}")
        self.store.add_exhibit(
            Exhibit(exhibit_id=exhibit_id, title=title, term_ids=tuple(term_ids),
                    created_at=now)
        )
        return exhibit_id

    def add_evidence(self, evidence_id: str, exhibit_id: str, kind: str, summary: str,
                     observed_at: str = "", recorder_id: str = "",
                     recorded_at: str | None = None) -> str:
        if not self.store.get_exhibit(exhibit_id):
            raise ReferenceMismatch(f"展品不存在：{exhibit_id}")
        self.store.add_evidence(
            Evidence(
                evidence_id=evidence_id, exhibit_id=exhibit_id, kind=kind, summary=summary,
                observed_at=normalize_iso(observed_at) if observed_at else "",
                recorded_at=normalize_iso(recorded_at or self._now()),
                recorder_id=recorder_id,
            )
        )
        return evidence_id

    # ------------------------------------------------------------------
    # 借展可见期与展示位置
    # ------------------------------------------------------------------
    def register_loan(self, loan_id: str, exhibit_id: str, lender: str,
                      visible_from: str, visible_until: str) -> str:
        if not self.store.get_exhibit(exhibit_id):
            raise ReferenceMismatch(f"展品不存在：{exhibit_id}")
        vf, vu = normalize_iso(visible_from), normalize_iso(visible_until)
        if parse_utc(vu) <= parse_utc(vf):
            raise NarrativeError("借展可见期结束必须晚于开始")
        self.store.add_loan(
            Loan(loan_id=loan_id, exhibit_id=exhibit_id, lender=lender,
                 visible_from=vf, visible_until=vu, agreed_at=self._now())
        )
        # 到期撤展登记为计划效果，时间推进或重启后恰好执行一次。
        self.store.add_effect(
            ScheduledEffect(
                effect_id=f"eff:loan:{loan_id}", kind="loan_recall", due_at=vu,
                release_id="", payload={"loan_id": loan_id, "exhibit_id": exhibit_id},
            )
        )
        return loan_id

    def place(self, placement_id: str, unit_id: str, exhibit_id: str, start_at: str,
              end_at: str = FAR_FUTURE, slot: str = "") -> str:
        if not self.store.get_exhibit(exhibit_id):
            raise ReferenceMismatch(f"展品不存在：{exhibit_id}")
        self.store.add_placement(
            Placement(placement_id=placement_id, unit_id=unit_id, exhibit_id=exhibit_id,
                      start_at=normalize_iso(start_at), end_at=normalize_iso(end_at),
                      slot=slot)
        )
        return placement_id

    # ------------------------------------------------------------------
    # 解释与异议
    # ------------------------------------------------------------------
    def propose_interpretation(self, interpretation_id: str, exhibit_id: str, expert_id: str,
                               dating: str, rationale: str,
                               evidence_ids: list[str] | tuple[str, ...]) -> str:
        exhibit = self.store.get_exhibit(exhibit_id)
        if not exhibit:
            raise ReferenceMismatch(f"展品不存在：{exhibit_id}")
        evs = tuple(evidence_ids)
        for eid in evs:
            evidence = self.store.get_evidence(eid)
            if not evidence or evidence.exhibit_id != exhibit_id:
                raise ReferenceMismatch(f"证据不存在或不属于该展品：{eid}")
        now = self._now()
        self.store.add_interpretation(
            Interpretation(interpretation_id=interpretation_id, exhibit_id=exhibit_id,
                           expert_id=expert_id, dating=dating, rationale=rationale,
                           evidence_ids=evs, proposed_at=now, status="proposed")
        )
        return interpretation_id

    def adopt_interpretation(self, interpretation_id: str, by: str) -> str:
        interp = self._require_interp(interpretation_id)
        if self.store.open_objections(interp.interpretation_id):
            raise NarrativeError("存在未决异议，不能采用该解释，请先解决异议")
        self.store.append_interpretation_event(
            interpretation_id, "adopted", self._now(), actor_id=by, note="策展采用"
        )
        return interpretation_id

    def raise_objection(self, objection_id: str, interpretation_id: str, raiser_id: str,
                        reason: str) -> str:
        interp = self._require_interp(interpretation_id)
        now = self._now()
        self.store.add_objection(
            Objection(objection_id=objection_id, interpretation_id=interpretation_id,
                      raiser_id=raiser_id, reason=reason, raised_at=now, resolution="open")
        )
        # 提出异议即把该解释标记为“反对”状态。
        self.store.append_interpretation_event(
            interpretation_id, "opposed", now, actor_id=raiser_id, note=reason
        )
        # 唤起相关单元叙事复核。
        self._ensure_review(
            review_id=f"rev:obj:{objection_id}", unit_id=self._unit_for_exhibit(interp.exhibit_id),
            trigger="objection", ref_id=interpretation_id,
            note=f"对 {interp.exhibit_id} 年代判断存在专家异议：{reason}",
        )
        return objection_id

    def resolve_objection(self, objection_id: str, by: str, resolution: str,
                          note: str = "") -> dict[str, object]:
        if resolution not in ("resolved", "rejected"):
            raise NarrativeError("异议结论必须是 resolved 或 rejected")
        objection = self.store.get_objection(objection_id)
        if not objection:
            raise ReferenceMismatch(f"异议不存在：{objection_id}")
        if not objection.is_open:
            return {"objection_id": objection_id, "already_closed": True}
        interp = self._require_interp(objection.interpretation_id)
        # 提出解释的专家不能独自结束异议。
        if by == interp.expert_id:
            raise ObjectionOpenError(
                f"专家 {by} 是该解释的提出者，不能独自结束异议，"
                "须由策展团队或其他专家复核后关闭"
            )
        now = self._now()
        self.store.resolve_objection(objection_id, resolution, by, now, note)
        # 回到待定状态，是否采用由策展另行决定；异议成立则解释作废倾向。
        event = "retired" if resolution == "resolved" else "proposed"
        self.store.append_interpretation_event(
            interp.interpretation_id, event, now, actor_id=by,
            note=f"异议{ '成立' if resolution == 'resolved' else '驳回'}：{note}"
        )
        self._close_reviews_for("objection", interp.interpretation_id, now)
        return {"objection_id": objection_id, "resolution": resolution, "closed_by": by}

    def _require_interp(self, interpretation_id: str) -> Interpretation:
        interp = self.store.get_interpretation(interpretation_id)
        if not interp:
            raise ReferenceMismatch(f"解释不存在：{interpretation_id}")
        return interp

    def _unit_for_exhibit(self, exhibit_id: str) -> str:
        for unit in self.store.all_units():
            for p in self.store.placements_for_unit(unit.unit_id):
                if p.exhibit_id == exhibit_id:
                    return unit.unit_id
        return ""

    # ------------------------------------------------------------------
    # 叙事签发
    # ------------------------------------------------------------------
    def release(self, spec: dict[str, Any]) -> dict[str, Any]:
        now = self._now()
        release_id = str(spec["release_id"])
        unit_id = str(spec["unit_id"])
        curator_id = str(spec["curator_id"])
        summary = str(spec.get("summary", ""))
        texts = spec.get("texts", [])
        exhibit_ids = tuple(spec.get("exhibit_ids", ()))
        evidence_ids = tuple(spec.get("evidence_ids", ()))
        interp_ids = tuple(spec.get("interp_ids", ()))

        content = {
            "unit_id": unit_id, "curator_id": curator_id, "summary": summary,
            "texts": sorted(
                (
                    {
                        "channel": str(t["channel"]), "body": str(t["body"]),
                        "effective_from": normalize_iso(str(t["effective_from"])),
                    }
                    for t in texts
                ),
                key=lambda x: x["channel"],
            ),
            "exhibit_ids": sorted(exhibit_ids),
            "evidence_ids": sorted(evidence_ids),
            "interp_ids": sorted(interp_ids),
        }
        digest = digest_of(content)

        existing = self.store.get_release(release_id)
        if existing is not None:
            if existing.digest == digest:
                # 内容相同的重试：原封不动返回原回执。
                return self._receipt(existing, replayed=True)
            raise ReleaseConflict(release_id, existing.digest, digest)

        self._validate_refs(exhibit_ids, evidence_ids, interp_ids)
        pins = self._build_pins(exhibit_ids, evidence_ids, interp_ids)
        channels = tuple(sorted(t["channel"] for t in texts))

        self.store.insert_release(
            Release(release_id=release_id, unit_id=unit_id, curator_id=curator_id,
                    summary=summary, digest=digest, created_at=now, pins=pins,
                    channels=channels)
        )

        for text in texts:
            self._publish_text(
                branch_id=f"{release_id}:{text['channel']}", unit_id=unit_id,
                channel=str(text["channel"]), body=str(text["body"]),
                effective_from=normalize_iso(str(text["effective_from"])),
                exhibit_ids=exhibit_ids, evidence_ids=evidence_ids, interp_ids=interp_ids,
                release_id=release_id, now=now,
            )

        return self._receipt(self.store.get_release(release_id), replayed=False)

    def _publish_text(self, branch_id: str, unit_id: str, channel: str, body: str,
                      effective_from: str, exhibit_ids: tuple[str, ...],
                      evidence_ids: tuple[str, ...], interp_ids: tuple[str, ...],
                      release_id: str, now: str) -> None:
        if parse_utc(effective_from) <= parse_utc(now):
            # 立即生效：先接续当前生效文本，再发布新版本。
            current = self._effective_branch(unit_id, channel, now)
            branch = TextBranch(
                branch_id=branch_id, unit_id=unit_id, channel=channel, body=body,
                effective_from=effective_from, effective_until=FAR_FUTURE,
                exhibit_ids=exhibit_ids, evidence_ids=evidence_ids, interp_ids=interp_ids,
                published_at=now, status="effective",
            )
            self.store.add_branch(branch)
            if current:
                self.store.supersede_branch(current.branch_id, branch_id, now)
        else:
            # 尚未生效：登记待生效效果，到点各执行一次。
            self.store.add_branch(
                TextBranch(branch_id=branch_id, unit_id=unit_id, channel=channel, body=body,
                           effective_from=effective_from, effective_until=FAR_FUTURE,
                           exhibit_ids=exhibit_ids, evidence_ids=evidence_ids,
                           interp_ids=interp_ids, published_at="", status="pending")
            )
            self.store.add_effect(
                ScheduledEffect(
                    effect_id=f"eff:txt:{branch_id}", kind="text_effective",
                    due_at=effective_from, release_id=release_id,
                    payload={"branch_id": branch_id, "unit_id": unit_id, "channel": channel},
                )
            )

    def _effective_branch(self, unit_id: str, channel: str, at: str) -> TextBranch | None:
        for branch in self.store.branches_for_unit_channel(unit_id, channel):
            if (branch.status == "effective" and branch.published_at
                    and parse_utc(branch.published_at) <= parse_utc(at)
                    and parse_utc(branch.effective_from) <= parse_utc(at)):
                return branch
        return None

    # ------------------------------------------------------------------
    # 年代订正
    # ------------------------------------------------------------------
    def revise_dating(self, spec: dict[str, Any]) -> dict[str, Any]:
        now = self._now()
        release_id = str(spec["release_id"])
        unit_id = str(spec["unit_id"])
        curator_id = str(spec["curator_id"])
        exhibit_id = str(spec["exhibit_id"])
        new_ip = spec["interpretation"]
        corrections = spec.get("corrections", {})      # channel -> 勘误说明（已公开）
        replacements = spec.get("replacements", {})    # channel -> 替换正文（未生效）
        summary = str(spec.get("summary", "年代订正"))

        if not self.store.get_exhibit(exhibit_id):
            raise ReferenceMismatch(f"展品不存在：{exhibit_id}")

        new_iid = str(new_ip["interpretation_id"])
        new_evidence = tuple(new_ip.get("evidence_ids", ()))
        for eid in new_evidence:
            evidence = self.store.get_evidence(eid)
            if not evidence or evidence.exhibit_id != exhibit_id:
                raise ReferenceMismatch(f"证据不存在或不属于该展品：{eid}")

        content = {
            "kind": "dating_revision", "unit_id": unit_id, "curator_id": curator_id,
            "exhibit_id": exhibit_id,
            "interpretation": {
                "interpretation_id": new_iid, "expert_id": str(new_ip["expert_id"]),
                "dating": str(new_ip["dating"]), "rationale": str(new_ip.get("rationale", "")),
                "evidence_ids": sorted(new_evidence),
            },
            "corrections": dict(sorted(corrections.items())),
            "replacements": dict(sorted(replacements.items())),
            "summary": summary,
        }
        digest = digest_of(content)
        existing = self.store.get_release(release_id)
        if existing is not None:
            if existing.digest == digest:
                return self._receipt(existing, replayed=True)
            raise ReleaseConflict(release_id, existing.digest, digest)

        # 固定新判断与证据；旧的采用判断退场（记录保留供重建），新判断采用。
        if not self.store.get_interpretation(new_iid):
            self.propose_interpretation(
                new_iid, exhibit_id, str(new_ip["expert_id"]), str(new_ip["dating"]),
                str(new_ip.get("rationale", "")), list(new_evidence),
            )
        for old in self.store.interpretations_for_exhibit(exhibit_id):
            if old.interpretation_id != new_iid and old.status == "adopted":
                self.store.append_interpretation_event(
                    old.interpretation_id, "retired", now, actor_id=curator_id,
                    note=f"被年代订正 {release_id} 取代",
                )
        if not self.store.open_objections(new_iid):
            self.store.append_interpretation_event(
                new_iid, "adopted", now, actor_id=curator_id, note="年代订正后采用",
            )

        pins = self._build_pins((exhibit_id,), new_evidence, (new_iid,))
        self.store.insert_release(
            Release(release_id=release_id, unit_id=unit_id, curator_id=curator_id,
                    summary=summary, digest=digest, created_at=now, pins=pins,
                    channels=tuple(sorted(set(list(corrections) + list(replacements)))))
        )

        changed: dict[str, list[str]] = {"replaced": [], "corrected": []}

        # 尚未生效（未对公众公开）的文字：直接替换 / 作废。
        for branch in self.store.pending_branches_for_exhibit(exhibit_id):
            # 旧的待生效效果无论替换还是撤下都不再执行。
            self.store.cancel_effects([f"eff:txt:{branch.branch_id}"])
            new_body = replacements.get(branch.channel)
            if new_body is not None:
                repl_id = f"{release_id}:{branch.channel}:repl"
                self.store.supersede_branch(branch.branch_id, repl_id, now)
                self.store.add_branch(
                    TextBranch(branch_id=repl_id, unit_id=branch.unit_id,
                               channel=branch.channel, body=str(new_body),
                               effective_from=branch.effective_from,
                               effective_until=FAR_FUTURE, exhibit_ids=(exhibit_id,),
                               evidence_ids=new_evidence, interp_ids=(new_iid,),
                               published_at="", status="pending",
                               superseded_by="")
                )
                self.store.add_effect(
                    ScheduledEffect(
                        effect_id=f"eff:txt:{repl_id}", kind="text_effective",
                        due_at=branch.effective_from, release_id=release_id,
                        payload={"branch_id": repl_id, "unit_id": branch.unit_id,
                                 "channel": branch.channel},
                    )
                )
                changed["replaced"].append(branch.channel)
            else:
                # 没有替换文本：撤下尚未生效的旧文字，等待策展重写。
                self.store.supersede_branch(branch.branch_id, release_id, now)
                self.store.cancel_effects([f"eff:txt:{branch.branch_id}"])
                changed["replaced"].append(branch.channel + "(撤下待重写)")

        # 公众已看过的版本：正文不动，只追加连续勘误。
        for branch in self.store.public_branches_for_exhibit(exhibit_id):
            note = corrections.get(
                branch.channel,
                f"本刊载所涉器物年代经复核改判为“{new_ip['dating']}”，特此连续勘误。",
            )
            seq = self.store.next_corrigendum_seq(branch.branch_id)
            self.store.add_corrigendum(
                Corrigendum(corrigendum_id=f"{release_id}:{branch.channel}:corr:{seq}",
                            branch_id=branch.branch_id, seq=seq, note=str(note),
                            issued_at=now, release_id=release_id)
            )
            changed["corrected"].append(branch.channel)

        self._ensure_review(
            review_id=f"rev:dating:{release_id}", unit_id=unit_id,
            trigger="dating_revision", ref_id=exhibit_id,
            note=f"器物 {exhibit_id} 年代订正，需复核三单元叙事一致性",
        )
        receipt = self._receipt(self.store.get_release(release_id), replayed=False)
        receipt["changed"] = changed
        return receipt

    # ------------------------------------------------------------------
    # 提前撤展
    # ------------------------------------------------------------------
    def recall_loan(self, loan_id: str, recall_at: str | None = None,
                    reason: str = "借展方提前撤回") -> dict[str, Any]:
        loan = self.store.get_loan(loan_id)
        if not loan:
            raise ReferenceMismatch(f"借展不存在：{loan_id}")
        at = normalize_iso(recall_at or self._now())
        closed_slots = self._close_future_placements(loan.exhibit_id, at, reason,
                                                     loan_id=loan_id, due=at)
        # 提前撤回后，原定到期任务不再执行。
        self.store.cancel_effects([f"eff:loan:{loan_id}"])
        return {"loan_id": loan_id, "recall_at": at, "closed_slots": closed_slots}

    def _close_future_placements(self, exhibit_id: str, at: str, reason: str,
                                 loan_id: str, due: str) -> list[str]:
        closed: list[str] = []
        at_dt = parse_utc(at)
        for placement in self.store.open_placements_for_exhibit(exhibit_id, at):
            # 只关闭未来区间；尚未开始的位置取消为零时长，已发生区间原样保留。
            new_end = max(parse_utc(placement.start_at), at_dt)
            self.store.close_placement(
                placement.placement_id, new_end.isoformat(), at, reason
            )
            self._ensure_review(
                review_id=f"rev:loan:{loan_id}:{placement.placement_id}",
                unit_id=placement.unit_id, trigger="loan_recall", ref_id=loan_id,
                note=(f"器物 {exhibit_id}（展位 {placement.slot or placement.placement_id}）"
                      f"于 {due} 撤展，需复核该单元叙事：{reason}"),
            )
            closed.append(placement.placement_id)
        return closed

    # ------------------------------------------------------------------
    # 到期调度：时间推进 / 重启后各执行一次
    # ------------------------------------------------------------------
    def tick(self) -> list[dict[str, Any]]:
        now = self._now()
        applied: list[dict[str, Any]] = []
        for effect in self.store.due_effects(now):
            if effect.kind == "text_effective":
                branch = self.store.get_branch(effect.payload["branch_id"])
                if branch and branch.status == "pending":
                    current = self._effective_branch(branch.unit_id, branch.channel, now)
                    self.store.publish_branch(
                        branch.branch_id, now,
                        close_other_id=current.branch_id if current else "",
                        close_until=now,
                    )
                self.store.mark_effect_applied(effect.effect_id, now)
                applied.append({"effect_id": effect.effect_id, "kind": effect.kind,
                                "branch_id": effect.payload.get("branch_id", "")})
            elif effect.kind == "loan_recall":
                slots = self._close_future_placements(
                    effect.payload["exhibit_id"], effect.due_at, "借展到期撤展",
                    loan_id=effect.payload["loan_id"], due=effect.due_at,
                )
                self.store.mark_effect_applied(effect.effect_id, now)
                applied.append({"effect_id": effect.effect_id, "kind": effect.kind,
                                "closed_slots": slots})
        return applied

    # 语义别名：推进时间并执行所有到期效果。
    run_due = tick

    # ------------------------------------------------------------------
    # 历史时点重建
    # ------------------------------------------------------------------
    def as_of(self, when: str) -> dict[str, Any]:
        point = normalize_iso(when)
        point_dt = parse_utc(point)
        result: dict[str, Any] = {"as_of": point, "units": []}

        for unit in self.store.all_units():
            exhibits: list[dict[str, Any]] = []
            exhibit_ids: list[str] = []
            for placement in self.store.placements_for_unit(unit.unit_id):
                if (parse_utc(placement.start_at) <= point_dt
                        < parse_utc(placement.end_at)):
                    exhibit = self.store.get_exhibit(placement.exhibit_id)
                    if exhibit and exhibit.exhibit_id not in exhibit_ids:
                        exhibit_ids.append(exhibit.exhibit_id)
                        exhibits.append({
                            "exhibit_id": exhibit.exhibit_id,
                            "title": exhibit.title,
                            "slot": placement.slot,
                            "terms": list(exhibit.term_ids),
                        })

            adopted: list[dict[str, Any]] = []
            opposed: list[dict[str, Any]] = []
            retired: list[dict[str, Any]] = []
            for exhibit_id in exhibit_ids:
                for interp in self.store.interpretations_for_exhibit(exhibit_id):
                    state = self._interp_state_at(interp.interpretation_id, point_dt)
                    open_objs = [
                        {
                            "objection_id": o.objection_id, "raiser_id": o.raiser_id,
                            "reason": o.reason,
                        }
                        for o in self.store.open_objections(interp.interpretation_id)
                        if self._objection_open_at(o, point_dt)
                    ]
                    entry = {
                        "interpretation_id": interp.interpretation_id,
                        "exhibit_id": exhibit_id, "expert_id": interp.expert_id,
                        "dating": interp.dating, "evidence_ids": list(interp.evidence_ids),
                    }
                    if open_objs:
                        opposed.append({**entry, "objections": open_objs})
                    elif state == "adopted":
                        adopted.append(entry)
                    elif state == "retired":
                        retired.append(entry)

            channels: dict[str, Any] = {}
            for channel in CHANNELS:
                shown = None
                for branch in self.store.branches_for_unit_channel(unit.unit_id, channel):
                    if not branch.published_at:
                        continue
                    if (parse_utc(branch.published_at) <= point_dt
                            and parse_utc(branch.effective_from) <= point_dt
                            < parse_utc(branch.effective_until)):
                        shown = branch
                if shown:
                    channels[channel] = {
                        "channel_name": CHANNEL_NAME[channel],
                        "branch_id": shown.branch_id,
                        "body": shown.body,
                        "fixed_exhibits": list(shown.exhibit_ids),
                        "fixed_evidence": list(shown.evidence_ids),
                        "corrigenda": [
                            {
                                "seq": c.seq, "note": c.note, "issued_at": c.issued_at,
                                "release_id": c.release_id,
                            }
                            for c in self.store.corrigenda_for_branch(shown.branch_id)
                            if parse_utc(c.issued_at) <= point_dt
                        ],
                    }
                else:
                    channels[channel] = None

            result["units"].append({
                "unit_id": unit.unit_id, "title": unit.title,
                "exhibits": exhibits,
                "interpretations": {"adopted": adopted, "opposed": opposed,
                                    "retired": retired},
                "channels": channels,
            })
        return result

    def _interp_state_at(self, interpretation_id: str, point_dt) -> str:
        state = "proposed"
        for event in self.store.interpretation_events(interpretation_id):
            if parse_utc(event["at"]) <= point_dt:
                mapping = {"proposed": "proposed", "adopted": "adopted",
                           "readopted": "adopted", "opposed": "opposed",
                           "retired": "retired"}
                state = mapping.get(event["event"], state)
        return state

    @staticmethod
    def _objection_open_at(objection: Objection, point_dt) -> bool:
        if parse_utc(objection.raised_at) > point_dt:
            return False
        if objection.resolved_at and parse_utc(objection.resolved_at) <= point_dt:
            return False
        return True

    def release_snapshot(self, release_id: str) -> dict[str, Any] | None:
        """查看某次签发固定下来的展品 / 证据快照。"""
        release = self.store.get_release(release_id)
        if not release:
            return None
        return {
            "release_id": release.release_id, "unit_id": release.unit_id,
            "curator_id": release.curator_id, "summary": release.summary,
            "digest": release.digest, "created_at": release.created_at,
            "channels": list(release.channels), "pins": release.pins,
        }

    def open_reviews(self, unit_id: str = "") -> list[dict[str, Any]]:
        return [r.__dict__.copy() for r in self.store.reviews("open", unit_id)]

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    def _validate_refs(self, exhibit_ids, evidence_ids, interp_ids) -> None:
        for exhibit_id in exhibit_ids:
            if not self.store.get_exhibit(exhibit_id):
                raise ReferenceMismatch(f"展品不存在：{exhibit_id}")
        for evidence_id in evidence_ids:
            evidence = self.store.get_evidence(evidence_id)
            if not evidence:
                raise ReferenceMismatch(f"证据不存在：{evidence_id}")
            if exhibit_ids and evidence.exhibit_id not in exhibit_ids:
                raise ReferenceMismatch(
                    f"证据 {evidence_id} 所属器物不在本次固定引用范围内"
                )
        for interp_id in interp_ids:
            interp = self.store.get_interpretation(interp_id)
            if not interp:
                raise ReferenceMismatch(f"解释不存在：{interp_id}")
            if self.store.open_objections(interp_id):
                raise ReferenceMismatch(f"解释 {interp_id} 存在未决异议，不能作为定稿引用")

    def _build_pins(self, exhibit_ids, evidence_ids, interp_ids) -> dict[str, str]:
        pins: dict[str, str] = {}
        for exhibit_id in exhibit_ids:
            exhibit = self.store.get_exhibit(exhibit_id)
            pins[f"exhibit:{exhibit_id}"] = canonical_json(
                {"exhibit_id": exhibit.exhibit_id, "title": exhibit.title,
                 "term_ids": list(exhibit.term_ids)}
            )
        for evidence_id in evidence_ids:
            evidence = self.store.get_evidence(evidence_id)
            pins[f"evidence:{evidence_id}"] = canonical_json(evidence.__dict__)
        for interp_id in interp_ids:
            interp = self.store.get_interpretation(interp_id)
            pins[f"interpretation:{interp_id}"] = canonical_json(
                {**interp.__dict__, "evidence_ids": list(interp.evidence_ids)}
            )
        return pins

    @staticmethod
    def _receipt(release: Release, replayed: bool) -> dict[str, Any]:
        return Receipt(release_id=release.release_id, digest=release.digest,
                       created_at=release.created_at, replayed=replayed).__dict__.copy()

    def _ensure_review(self, review_id: str, unit_id: str, trigger: str, ref_id: str,
                       note: str) -> None:
        if any(r.review_id == review_id for r in self.store.reviews()):
            return
        self.store.add_review(
            ReviewTask(review_id=review_id, unit_id=unit_id, trigger=trigger, ref_id=ref_id,
                       note=note, created_at=self._now(), status="open")
        )

    def _close_reviews_for(self, trigger: str, ref_id: str, at: str) -> None:
        for review in self.store.reviews("open"):
            if review.trigger == trigger and review.ref_id == ref_id:
                self.store.close_review(review.review_id, at)
