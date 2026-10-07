"""宫廷艺术展陈叙事签发的应用服务入口。"""
import hashlib
import json
from dataclasses import asdict

from .domain import (
    Channel,
    ConflictError,
    CraftTerm,
    DisplaySlot,
    Erratum,
    Evidence,
    Exhibit,
    Interpretation,
    LoanWindow,
    NotFoundError,
    Objection,
    Record,
    Release,
    ReviewTask,
    StateError,
    TextVersion,
    TimelineNode,
    Unit,
    norm_ts,
    utc_now,
)
from .store import Store


def _digest(payload: dict) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class Service:
    """叙事签发库的统一入口：登记、签发、撤展、异议、勘误、调度与时点查询。"""

    def __init__(self, store: Store | None = None, now: object = None) -> None:
        self.store = store or Store()
        self._clock = norm_ts(now) if now else None

    def health(self) -> dict[str, str]:
        return {"service": "exhibition_narrative_release", "status": "ok"}

    def register(self, payload: dict[str, object]) -> dict[str, object]:
        required = ("record_id", "owner_id", "state")
        missing = [name for name in required if not str(payload.get(name, "")).strip()]
        if missing:
            raise ValueError("缺少必要字段：" + "、".join(missing))
        record = Record(
            record_id=str(payload["record_id"]), owner_id=str(payload["owner_id"]),
            state=str(payload["state"]), revision=int(payload.get("revision", 1)),
        )
        return self.store.add(record).__dict__.copy()

    def find(self, record_id: str) -> dict[str, object] | None:
        value = self.store.get(record_id)
        return value.__dict__.copy() if value else None

    # ---- 时钟 ----

    def _now(self) -> str:
        return self._clock or utc_now()

    def _require(self, kind, table: str, key: str, value: str, label: str):
        row = self.store.fetch_one(f"SELECT * FROM {table} WHERE {key}=?", (value,))
        if row is None:
            raise NotFoundError(f"{label}不存在：{value}")
        return kind(**dict(row))

    # ---- 登记：展陈层级、时间轴、展品、证据、术语、渠道、借展 ----

    def add_unit(self, unit_id: str, name: str, parent_id: str = "") -> Unit:
        if parent_id:
            self._require(Unit, "units", "unit_id", parent_id, "展陈单元")
        unit = Unit(unit_id=unit_id, name=name, parent_id=parent_id)
        with self.store.connection:
            self.store.insert("units", asdict(unit))
        return unit

    def add_timeline_node(
        self, node_id: str, unit_id: str, label: str,
        start_year: int = 0, end_year: int = 0, sort: int = 0,
    ) -> TimelineNode:
        self._require(Unit, "units", "unit_id", unit_id, "展陈单元")
        node = TimelineNode(
            node_id=node_id, unit_id=unit_id, label=label,
            start_year=start_year, end_year=end_year, sort=sort,
        )
        with self.store.connection:
            self.store.insert("timeline_nodes", asdict(node))
        return node

    def add_channel(self, channel_id: str, name: str) -> Channel:
        channel = Channel(channel_id=channel_id, name=name)
        with self.store.connection:
            self.store.insert("channels", asdict(channel))
        return channel

    def add_exhibit(self, exhibit_id: str, name: str, category: str, lender_id: str = "") -> Exhibit:
        exhibit = Exhibit(exhibit_id=exhibit_id, name=name, category=category, lender_id=lender_id)
        with self.store.connection:
            self.store.insert("exhibits", asdict(exhibit))
        return exhibit

    def add_evidence(
        self, evidence_id: str, exhibit_id: str, claim: str, method: str = "", source: str = ""
    ) -> Evidence:
        self._require(Exhibit, "exhibits", "exhibit_id", exhibit_id, "展品")
        evidence = Evidence(
            evidence_id=evidence_id, exhibit_id=exhibit_id, claim=claim,
            method=method, source=source, created_at=self._now(),
        )
        with self.store.connection:
            self.store.insert("evidence", asdict(evidence))
        return evidence

    def add_craft_term(self, term_id: str, term: str, definition: str = "") -> CraftTerm:
        craft_term = CraftTerm(term_id=term_id, term=term, definition=definition)
        with self.store.connection:
            self.store.insert("craft_terms", asdict(craft_term))
        return craft_term

    def add_loan_window(
        self, loan_id: str, exhibit_id: str, visible_from: object, visible_to: object
    ) -> LoanWindow:
        self._require(Exhibit, "exhibits", "exhibit_id", exhibit_id, "展品")
        loan = LoanWindow(
            loan_id=loan_id, exhibit_id=exhibit_id,
            visible_from=norm_ts(visible_from), visible_to=norm_ts(visible_to),
        )
        if not loan.visible_from < loan.visible_to:
            raise ValueError("借展可见期起点必须早于终点")
        with self.store.connection:
            self.store.insert("loan_windows", asdict(loan))
        return loan

    # ---- 解释与异议 ----

    def propose_interpretation(
        self, interpretation_id: str, exhibit_id: str, evidence_id: str,
        proposed_by: str, body: str = "",
    ) -> Interpretation:
        self._require(Exhibit, "exhibits", "exhibit_id", exhibit_id, "展品")
        evidence = self._require(Evidence, "evidence", "evidence_id", evidence_id, "年代证据")
        if evidence.exhibit_id != exhibit_id:
            raise ValueError("证据与展品不匹配")
        interpretation = Interpretation(
            interpretation_id=interpretation_id, exhibit_id=exhibit_id,
            evidence_id=evidence_id, proposed_by=proposed_by,
            body=body, created_at=self._now(),
        )
        with self.store.connection:
            self.store.insert("interpretations", asdict(interpretation))
        return interpretation

    def raise_objection(
        self, objection_id: str, interpretation_id: str, raised_by: str, detail: str = ""
    ) -> Objection:
        self._require(Interpretation, "interpretations", "interpretation_id", interpretation_id, "解释")
        objection = Objection(
            objection_id=objection_id, interpretation_id=interpretation_id,
            raised_by=raised_by, detail=detail, status="open", raised_at=self._now(),
        )
        with self.store.connection:
            self.store.insert("objections", asdict(objection))
        return objection

    def resolve_objection(self, objection_id: str, resolved_by: str, resolution: str = "") -> Objection:
        objection = self._require(Objection, "objections", "objection_id", objection_id, "异议")
        if objection.status != "open":
            raise StateError("异议已结束，不能重复处理")
        interpretation = self._require(
            Interpretation, "interpretations", "interpretation_id",
            objection.interpretation_id, "解释",
        )
        if resolved_by == interpretation.proposed_by:
            raise StateError("提出解释的专家不能独自结束异议")
        with self.store.connection:
            self.store.execute(
                "UPDATE objections SET status='resolved', resolved_by=?, resolved_at=?, resolution=? "
                "WHERE objection_id=?",
                (resolved_by, self._now(), resolution, objection_id),
            )
        return self._require(Objection, "objections", "objection_id", objection_id, "异议")

    # ---- 展示位置与撤展 ----

    def schedule_display(
        self, slot_id: str, unit_id: str, exhibit_id: str, start: object, finish: object
    ) -> DisplaySlot:
        self._require(Unit, "units", "unit_id", unit_id, "展陈单元")
        self._require(Exhibit, "exhibits", "exhibit_id", exhibit_id, "展品")
        slot = DisplaySlot(
            slot_id=slot_id, unit_id=unit_id, exhibit_id=exhibit_id,
            start=norm_ts(start), finish=norm_ts(finish),
        )
        if not slot.start < slot.finish:
            raise ValueError("展示开始必须早于结束")
        with self.store.connection:
            self.store.insert("display_slots", asdict(slot))
        return slot

    def withdraw_exhibit(
        self, exhibit_id: str, from_time: object, reason: str, unit_id: str = ""
    ) -> dict[str, object]:
        """临时撤展：只关闭未来展示位置，已经发生的展示保留，并唤起相关叙事复核。"""
        self._require(Exhibit, "exhibits", "exhibit_id", exhibit_id, "展品")
        moment = norm_ts(from_time)
        with self.store.connection:
            cancelled, trimmed = self._close_future_display(exhibit_id, moment, reason, unit_id)
            tasks = self._raise_review_tasks(exhibit_id, f"展品撤展：{reason}")
        return {
            "exhibit_id": exhibit_id,
            "from_time": moment,
            "cancelled_slots": cancelled,
            "trimmed_slots": trimmed,
            "review_tasks": tasks,
        }

    def _close_future_display(
        self, exhibit_id: str, moment: str, reason: str, unit_id: str = ""
    ) -> tuple[list[str], list[str]]:
        now = self._now()
        sql = "SELECT * FROM display_slots WHERE exhibit_id=? AND status='scheduled'"
        params: list[object] = [exhibit_id]
        if unit_id:
            sql += " AND unit_id=?"
            params.append(unit_id)
        cancelled: list[str] = []
        trimmed: list[str] = []
        for row in self.store.fetch(sql, tuple(params)):
            slot = DisplaySlot(**dict(row))
            if slot.start >= moment:
                self.store.execute(
                    "UPDATE display_slots SET status='cancelled', cancelled_at=?, cancel_reason=? "
                    "WHERE slot_id=?",
                    (now, reason, slot.slot_id),
                )
                cancelled.append(slot.slot_id)
            elif slot.start < moment < slot.finish:
                self.store.execute(
                    "UPDATE display_slots SET finish=? WHERE slot_id=?", (moment, slot.slot_id)
                )
                trimmed.append(slot.slot_id)
        return cancelled, trimmed

    def _raise_review_tasks(self, exhibit_id: str, reason: str) -> list[str]:
        rows = self.store.fetch(
            "SELECT DISTINCT tv.version_id FROM text_versions tv "
            "JOIN release_pins rp ON rp.release_id = tv.release_id "
            "WHERE rp.pin_type='exhibit' AND rp.ref_id=? AND tv.status IN ('pending','effective')",
            (exhibit_id,),
        )
        tasks: list[str] = []
        for row in rows:
            task_id = f"rvw-{row['version_id']}-{exhibit_id}"
            task = ReviewTask(
                task_id=task_id, reason=reason,
                ref_type="text_version", ref_id=row["version_id"],
                created_at=self._now(),
            )
            if self.store.insert_or_ignore("review_tasks", asdict(task)):
                tasks.append(task_id)
        return tasks

    # ---- 签发 ----

    def issue_release(self, payload: dict[str, object]) -> dict[str, object]:
        """签发说明文本。重试内容相同返回原回执；编号相同摘要不同拒绝覆盖。"""
        required = ("release_id", "unit_id", "channel_id", "body", "effective_from")
        missing = [name for name in required if not str(payload.get(name, "")).strip()]
        if missing:
            raise ValueError("缺少必要字段：" + "、".join(missing))
        release_id = str(payload["release_id"])
        unit_id = str(payload["unit_id"])
        channel_id = str(payload["channel_id"])
        body = str(payload["body"])
        exhibit_ids = sorted({str(item) for item in payload.get("exhibit_ids", [])})
        evidence_ids = sorted({str(item) for item in payload.get("evidence_ids", [])})
        effective_from = norm_ts(payload["effective_from"])
        digest = _digest({
            "unit_id": unit_id, "channel_id": channel_id, "body": body,
            "exhibit_ids": exhibit_ids, "evidence_ids": evidence_ids,
            "effective_from": effective_from,
        })
        existing = self.store.fetch_one("SELECT * FROM releases WHERE release_id=?", (release_id,))
        if existing is not None:
            release = Release(**dict(existing))
            if release.digest != digest:
                raise ConflictError("签发编号相同但摘要不同，拒绝覆盖")
            return json.loads(release.receipt)

        self._require(Unit, "units", "unit_id", unit_id, "展陈单元")
        self._require(Channel, "channels", "channel_id", channel_id, "发布渠道")
        exhibits = [
            self._require(Exhibit, "exhibits", "exhibit_id", item, "展品") for item in exhibit_ids
        ]
        evidence = [
            self._require(Evidence, "evidence", "evidence_id", item, "年代证据")
            for item in evidence_ids
        ]

        now = self._now()
        status = "effective" if effective_from <= now else "pending"
        version_id = f"tv-{release_id}"
        version = TextVersion(
            version_id=version_id, unit_id=unit_id, channel_id=channel_id, body=body,
            status=status, effective_from=effective_from, release_id=release_id, created_at=now,
        )
        receipt = {
            "release_id": release_id, "digest": digest, "version_id": version_id,
            "unit_id": unit_id, "channel_id": channel_id, "status": status, "issued_at": now,
        }
        with self.store.connection:
            # 新的年代判断可以替换尚未生效的文字
            self.store.execute(
                "UPDATE text_versions SET status='replaced' "
                "WHERE unit_id=? AND channel_id=? AND status='pending'",
                (unit_id, channel_id),
            )
            if status == "effective":
                self._supersede_current(unit_id, channel_id, effective_from)
            self.store.insert("text_versions", asdict(version))
            self.store.insert("releases", asdict(Release(
                release_id=release_id, digest=digest, unit_id=unit_id, channel_id=channel_id,
                version_id=version_id, status=status,
                receipt=json.dumps(receipt, ensure_ascii=False, sort_keys=True), issued_at=now,
            )))
            # 固定所引用的展品与证据快照
            for exhibit in exhibits:
                self.store.insert("release_pins", {
                    "release_id": release_id, "pin_type": "exhibit",
                    "ref_id": exhibit.exhibit_id,
                    "snapshot": json.dumps(asdict(exhibit), ensure_ascii=False, sort_keys=True),
                })
            for item in evidence:
                self.store.insert("release_pins", {
                    "release_id": release_id, "pin_type": "evidence",
                    "ref_id": item.evidence_id,
                    "snapshot": json.dumps(asdict(item), ensure_ascii=False, sort_keys=True),
                })
        return receipt

    def _supersede_current(self, unit_id: str, channel_id: str, until: str) -> None:
        row = self.store.fetch_one(
            "SELECT version_id FROM text_versions "
            "WHERE unit_id=? AND channel_id=? AND status='effective'",
            (unit_id, channel_id),
        )
        if row is not None:
            self.store.execute(
                "UPDATE text_versions SET status='superseded', effective_until=? WHERE version_id=?",
                (until, row["version_id"]),
            )

    def add_errata(self, version_id: str, note: str, at: object = None) -> Erratum:
        """公众看过的版本只能通过连续勘误说明变化。"""
        version = self._require(TextVersion, "text_versions", "version_id", version_id, "文本版本")
        if version.status not in ("effective", "superseded"):
            raise StateError("尚未生效的文字应通过新签发替换，不能勘误")
        moment = norm_ts(at) if at else self._now()
        with self.store.connection:
            row = self.store.fetch_one(
                "SELECT COUNT(*) AS n FROM errata WHERE version_id=?", (version_id,)
            )
            seq = int(row["n"]) + 1
            erratum = Erratum(
                errata_id=f"{version_id}-errata-{seq}", version_id=version_id,
                seq=seq, note=note, created_at=moment,
            )
            self.store.insert("errata", asdict(erratum))
        return erratum

    # ---- 调度：时间推进与到期处理，每件待办只执行一次 ----

    def advance_time(self, now: object) -> dict[str, object]:
        self._clock = norm_ts(now)
        return self.run_due()

    def run_due(self, now: object = None) -> dict[str, object]:
        moment = norm_ts(now) if now else self._now()
        activated: list[str] = []
        expired_loans: list[dict[str, object]] = []
        pending = self.store.fetch(
            "SELECT * FROM text_versions WHERE status='pending' AND effective_from<=? "
            "ORDER BY effective_from",
            (moment,),
        )
        for row in pending:
            version = TextVersion(**dict(row))
            with self.store.connection:
                if self._transition_done("text-activate", version.version_id):
                    continue
                self._supersede_current(version.unit_id, version.channel_id, version.effective_from)
                self.store.execute(
                    "UPDATE text_versions SET status='effective' WHERE version_id=?",
                    (version.version_id,),
                )
                self._record_transition("text-activate", version.version_id, moment)
                activated.append(version.version_id)
        loans = self.store.fetch(
            "SELECT * FROM loan_windows WHERE closed_at='' AND visible_to<=?", (moment,)
        )
        for row in loans:
            loan = LoanWindow(**dict(row))
            with self.store.connection:
                if self._transition_done("loan-expire", loan.loan_id):
                    continue
                self.store.execute(
                    "UPDATE loan_windows SET closed_at=? WHERE loan_id=?", (moment, loan.loan_id)
                )
                cancelled, trimmed = self._close_future_display(
                    loan.exhibit_id, loan.visible_to, "借展到期"
                )
                tasks = self._raise_review_tasks(loan.exhibit_id, "借展到期撤展")
                self._record_transition("loan-expire", loan.loan_id, moment)
                expired_loans.append({
                    "loan_id": loan.loan_id, "exhibit_id": loan.exhibit_id,
                    "cancelled_slots": cancelled, "trimmed_slots": trimmed,
                    "review_tasks": tasks,
                })
        return {"at": moment, "activated": activated, "expired_loans": expired_loans}

    def _transition_done(self, kind: str, ref_key: str) -> bool:
        return self.store.fetch_one(
            "SELECT 1 FROM transitions WHERE kind=? AND ref_key=?", (kind, ref_key)
        ) is not None

    def _record_transition(self, kind: str, ref_key: str, moment: str) -> None:
        self.store.insert("transitions", {"kind": kind, "ref_key": ref_key, "applied_at": moment})

    # ---- 查询 ----

    def get_text_version(self, version_id: str) -> TextVersion | None:
        row = self.store.fetch_one("SELECT * FROM text_versions WHERE version_id=?", (version_id,))
        return TextVersion(**dict(row)) if row else None

    def get_slot(self, slot_id: str) -> DisplaySlot | None:
        row = self.store.fetch_one("SELECT * FROM display_slots WHERE slot_id=?", (slot_id,))
        return DisplaySlot(**dict(row)) if row else None

    def get_loan(self, loan_id: str) -> LoanWindow | None:
        row = self.store.fetch_one("SELECT * FROM loan_windows WHERE loan_id=?", (loan_id,))
        return LoanWindow(**dict(row)) if row else None

    def get_objection(self, objection_id: str) -> Objection | None:
        row = self.store.fetch_one("SELECT * FROM objections WHERE objection_id=?", (objection_id,))
        return Objection(**dict(row)) if row else None

    def release_receipt(self, release_id: str) -> dict[str, object] | None:
        row = self.store.fetch_one("SELECT receipt FROM releases WHERE release_id=?", (release_id,))
        return json.loads(row["receipt"]) if row else None

    def release_pins(self, release_id: str) -> list[dict[str, object]]:
        rows = self.store.fetch(
            "SELECT pin_type, ref_id, snapshot FROM release_pins WHERE release_id=? "
            "ORDER BY pin_type, ref_id",
            (release_id,),
        )
        return [dict(row) for row in rows]

    def list_review_tasks(self) -> list[ReviewTask]:
        rows = self.store.fetch("SELECT * FROM review_tasks ORDER BY created_at, task_id")
        return [ReviewTask(**dict(row)) for row in rows]

    def timeline_of(self, unit_id: str) -> list[TimelineNode]:
        rows = self.store.fetch(
            "SELECT * FROM timeline_nodes WHERE unit_id=? ORDER BY sort, node_id", (unit_id,)
        )
        return [TimelineNode(**dict(row)) for row in rows]

    def state_at(self, unit_id: str, at: object) -> dict[str, object]:
        """重建某单元在历史时点的展示器物、采用与反对的解释以及各渠道文本。"""
        self._require(Unit, "units", "unit_id", unit_id, "展陈单元")
        moment = norm_ts(at)
        exhibits: list[dict[str, object]] = []
        seen: set[str] = set()
        slots = self.store.fetch(
            "SELECT * FROM display_slots WHERE unit_id=? AND status!='cancelled' "
            "AND start<=? AND finish>? ORDER BY start",
            (unit_id, moment, moment),
        )
        for row in slots:
            slot = DisplaySlot(**dict(row))
            if slot.exhibit_id in seen:
                continue
            seen.add(slot.exhibit_id)
            exhibit = self._require(Exhibit, "exhibits", "exhibit_id", slot.exhibit_id, "展品")
            exhibits.append({**asdict(exhibit), "slot_id": slot.slot_id})

        texts: dict[str, object] = {}
        release_ids: set[str] = set()
        channels = self.store.fetch("SELECT * FROM channels ORDER BY channel_id")
        for channel_row in channels:
            row = self.store.fetch_one(
                "SELECT * FROM text_versions WHERE unit_id=? AND channel_id=? "
                "AND status IN ('effective','superseded') AND effective_from<=? "
                "AND (effective_until='' OR effective_until>?) "
                "ORDER BY effective_from DESC LIMIT 1",
                (unit_id, channel_row["channel_id"], moment, moment),
            )
            if row is None:
                continue
            version = TextVersion(**dict(row))
            release_ids.add(version.release_id)
            errata = [
                asdict(Erratum(**dict(item)))
                for item in self.store.fetch(
                    "SELECT * FROM errata WHERE version_id=? AND created_at<=? ORDER BY seq",
                    (version.version_id, moment),
                )
            ]
            texts[version.channel_id] = {
                "version_id": version.version_id, "body": version.body,
                "status": version.status, "effective_from": version.effective_from,
                "errata": errata,
            }

        pinned_evidence: set[str] = set()
        for release_id in release_ids:
            for pin in self.store.fetch(
                "SELECT ref_id FROM release_pins WHERE release_id=? AND pin_type='evidence'",
                (release_id,),
            ):
                pinned_evidence.add(pin["ref_id"])
        adopted = [
            asdict(Interpretation(**dict(row)))
            for row in self.store.fetch("SELECT * FROM interpretations ORDER BY interpretation_id")
            if row["evidence_id"] in pinned_evidence
        ]
        contested: list[dict[str, object]] = []
        for row in self.store.fetch(
            "SELECT * FROM objections WHERE raised_at<=? AND (resolved_at='' OR resolved_at>?) "
            "ORDER BY objection_id",
            (moment, moment),
        ):
            objection = Objection(**dict(row))
            interpretation = self._require(
                Interpretation, "interpretations", "interpretation_id",
                objection.interpretation_id, "解释",
            )
            if interpretation.exhibit_id in seen:
                contested.append({**asdict(objection), "exhibit_id": interpretation.exhibit_id})
        return {
            "unit_id": unit_id, "at": moment,
            "exhibits": exhibits, "texts": texts,
            "adopted": adopted, "contested": contested,
        }
