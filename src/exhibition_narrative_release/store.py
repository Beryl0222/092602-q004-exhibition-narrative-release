"""宫廷艺术展陈叙事签发的 SQLite 存储。

设计原则：
- 事实（展品、证据、签发、勘误、已发生展示）只追加，不更新、不删除。
- 撤展通过缩短展示区间表达，媒体开放日已发生的区间原样保留。
- 未来效果（待生效说明、到期撤展）走唯一台账，claim+apply 同事务，
  保证模拟时间推进或进程重启后每个效果恰好应用一次。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from .domain import (
    Corrigendum,
    Evidence,
    Exhibit,
    Interpretation,
    Loan,
    Objection,
    Placement,
    Receipt,
    Record,
    Release,
    ReviewTask,
    ScheduledEffect,
    Term,
    TextBranch,
    TimelineNode,
    Unit,
)

FAR_FUTURE = "9999-12-31T00:00:00+00:00"


def _jloads(value: str | None, default: Any) -> Any:
    return json.loads(value) if value else default


def _tuple(value: str | None) -> tuple[str, ...]:
    return tuple(_jloads(value, []))


class Store:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self.connection = sqlite3.connect(str(path))
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self._create_schema()
        self.connection.commit()

    # ------------------------------------------------------------------
    # 建表
    # ------------------------------------------------------------------
    def _create_schema(self) -> None:
        ddl = [
            """CREATE TABLE IF NOT EXISTS records (
                record_id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                state TEXT NOT NULL,
                revision INTEGER NOT NULL CHECK(revision > 0),
                created_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS units (
                unit_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                parent_id TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS timeline_nodes (
                node_id TEXT PRIMARY KEY,
                label TEXT NOT NULL,
                at TEXT NOT NULL,
                unit_id TEXT NOT NULL DEFAULT ''
            )""",
            """CREATE TABLE IF NOT EXISTS terms (
                term_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                definition TEXT NOT NULL DEFAULT ''
            )""",
            """CREATE TABLE IF NOT EXISTS exhibits (
                exhibit_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                term_ids TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS evidence (
                evidence_id TEXT PRIMARY KEY,
                exhibit_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                summary TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                recorder_id TEXT NOT NULL DEFAULT ''
            )""",
            """CREATE TABLE IF NOT EXISTS interpretations (
                interpretation_id TEXT PRIMARY KEY,
                exhibit_id TEXT NOT NULL,
                expert_id TEXT NOT NULL,
                dating TEXT NOT NULL,
                rationale TEXT NOT NULL,
                evidence_ids TEXT NOT NULL DEFAULT '[]',
                proposed_at TEXT NOT NULL,
                status TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS interpretation_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                interpretation_id TEXT NOT NULL,
                event TEXT NOT NULL,
                at TEXT NOT NULL,
                actor_id TEXT NOT NULL DEFAULT '',
                note TEXT NOT NULL DEFAULT ''
            )""",
            """CREATE TABLE IF NOT EXISTS objections (
                objection_id TEXT PRIMARY KEY,
                interpretation_id TEXT NOT NULL,
                raiser_id TEXT NOT NULL,
                reason TEXT NOT NULL,
                raised_at TEXT NOT NULL,
                resolution TEXT NOT NULL,
                resolved_by TEXT NOT NULL DEFAULT '',
                resolved_at TEXT NOT NULL DEFAULT '',
                note TEXT NOT NULL DEFAULT ''
            )""",
            """CREATE TABLE IF NOT EXISTS loans (
                loan_id TEXT PRIMARY KEY,
                exhibit_id TEXT NOT NULL,
                lender TEXT NOT NULL,
                visible_from TEXT NOT NULL,
                visible_until TEXT NOT NULL,
                agreed_at TEXT NOT NULL DEFAULT ''
            )""",
            """CREATE TABLE IF NOT EXISTS placements (
                placement_id TEXT PRIMARY KEY,
                unit_id TEXT NOT NULL,
                exhibit_id TEXT NOT NULL,
                start_at TEXT NOT NULL,
                end_at TEXT NOT NULL,
                slot TEXT NOT NULL DEFAULT '',
                closed INTEGER NOT NULL DEFAULT 0,
                closed_at TEXT NOT NULL DEFAULT '',
                reason TEXT NOT NULL DEFAULT ''
            )""",
            """CREATE TABLE IF NOT EXISTS branches (
                branch_id TEXT PRIMARY KEY,
                unit_id TEXT NOT NULL,
                channel TEXT NOT NULL,
                body TEXT NOT NULL,
                effective_from TEXT NOT NULL,
                effective_until TEXT NOT NULL,
                exhibit_ids TEXT NOT NULL DEFAULT '[]',
                evidence_ids TEXT NOT NULL DEFAULT '[]',
                interp_ids TEXT NOT NULL DEFAULT '[]',
                published_at TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL,
                superseded_by TEXT NOT NULL DEFAULT ''
            )""",
            """CREATE TABLE IF NOT EXISTS corrigenda (
                corrigendum_id TEXT PRIMARY KEY,
                branch_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                note TEXT NOT NULL,
                issued_at TEXT NOT NULL,
                release_id TEXT NOT NULL DEFAULT '',
                UNIQUE(branch_id, seq)
            )""",
            """CREATE TABLE IF NOT EXISTS releases (
                release_id TEXT PRIMARY KEY,
                unit_id TEXT NOT NULL,
                curator_id TEXT NOT NULL,
                summary TEXT NOT NULL,
                digest TEXT NOT NULL,
                created_at TEXT NOT NULL,
                channels TEXT NOT NULL DEFAULT '[]',
                pins TEXT NOT NULL DEFAULT '{}'
            )""",
            """CREATE TABLE IF NOT EXISTS scheduled_effects (
                effect_id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                due_at TEXT NOT NULL,
                release_id TEXT NOT NULL,
                payload TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL,
                applied_at TEXT NOT NULL DEFAULT ''
            )""",
            """CREATE TABLE IF NOT EXISTS review_tasks (
                review_id TEXT PRIMARY KEY,
                unit_id TEXT NOT NULL,
                trigger TEXT NOT NULL,
                ref_id TEXT NOT NULL,
                note TEXT NOT NULL,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL,
                closed_at TEXT NOT NULL DEFAULT ''
            )""",
        ]
        for stmt in ddl:
            self.connection.execute(stmt)

    # ------------------------------------------------------------------
    # 基础
    # ------------------------------------------------------------------
    def add(self, record: Record) -> Record:
        value = record.stamped()
        with self.connection:
            self.connection.execute(
                "INSERT INTO records(record_id, owner_id, state, revision, created_at) "
                "VALUES(?,?,?,?,?)",
                (value.record_id, value.owner_id, value.state, value.revision, value.created_at),
            )
        return value

    def get_record(self, record_id: str) -> Record | None:
        row = self.connection.execute(
            "SELECT record_id, owner_id, state, revision, created_at FROM records WHERE record_id=?",
            (record_id,),
        ).fetchone()
        return Record(**dict(row)) if row else None

    # ------------------------------------------------------------------
    # 目录：单元 / 时间轴 / 术语
    # ------------------------------------------------------------------
    def add_unit(self, unit: Unit) -> Unit:
        with self.connection:
            self.connection.execute(
                "INSERT INTO units(unit_id,title,parent_id,created_at) VALUES(?,?,?,?)",
                (unit.unit_id, unit.title, unit.parent_id, unit.created_at),
            )
        return unit

    def all_units(self) -> list[Unit]:
        rows = self.connection.execute(
            "SELECT unit_id,title,parent_id,created_at FROM units ORDER BY unit_id"
        ).fetchall()
        return [Unit(**dict(r)) for r in rows]

    def add_timeline_node(self, node: TimelineNode) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO timeline_nodes(node_id,label,at,unit_id) VALUES(?,?,?,?)",
                (node.node_id, node.label, node.at, node.unit_id),
            )

    def timeline_nodes(self, unit_id: str = "") -> list[TimelineNode]:
        if unit_id:
            rows = self.connection.execute(
                "SELECT node_id,label,at,unit_id FROM timeline_nodes "
                "WHERE unit_id=? OR unit_id='' ORDER BY at",
                (unit_id,),
            ).fetchall()
        else:
            rows = self.connection.execute(
                "SELECT node_id,label,at,unit_id FROM timeline_nodes ORDER BY at"
            ).fetchall()
        return [TimelineNode(**dict(r)) for r in rows]

    def add_term(self, term: Term) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO terms(term_id,name,definition) VALUES(?,?,?)",
                (term.term_id, term.name, term.definition),
            )

    def get_term(self, term_id: str) -> Term | None:
        row = self.connection.execute(
            "SELECT term_id,name,definition FROM terms WHERE term_id=?", (term_id,)
        ).fetchone()
        return Term(**dict(row)) if row else None

    # ------------------------------------------------------------------
    # 展品与证据
    # ------------------------------------------------------------------
    def add_exhibit(self, exhibit: Exhibit) -> Exhibit:
        with self.connection:
            self.connection.execute(
                "INSERT INTO exhibits(exhibit_id,title,term_ids,created_at) VALUES(?,?,?,?)",
                (
                    exhibit.exhibit_id,
                    exhibit.title,
                    json.dumps(list(exhibit.term_ids), ensure_ascii=False),
                    exhibit.created_at,
                ),
            )
        return exhibit

    def get_exhibit(self, exhibit_id: str) -> Exhibit | None:
        row = self.connection.execute(
            "SELECT exhibit_id,title,term_ids,created_at FROM exhibits WHERE exhibit_id=?",
            (exhibit_id,),
        ).fetchone()
        if not row:
            return None
        data = dict(row)
        return Exhibit(
            exhibit_id=data["exhibit_id"],
            title=data["title"],
            term_ids=tuple(_jloads(data["term_ids"], [])),
            created_at=data["created_at"],
        )

    def add_evidence(self, evidence: Evidence) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO evidence(evidence_id,exhibit_id,kind,summary,observed_at,"
                "recorded_at,recorder_id) VALUES(?,?,?,?,?,?,?)",
                (
                    evidence.evidence_id,
                    evidence.exhibit_id,
                    evidence.kind,
                    evidence.summary,
                    evidence.observed_at,
                    evidence.recorded_at,
                    evidence.recorder_id,
                ),
            )

    def get_evidence(self, evidence_id: str) -> Evidence | None:
        row = self.connection.execute(
            "SELECT evidence_id,exhibit_id,kind,summary,observed_at,recorded_at,recorder_id "
            "FROM evidence WHERE evidence_id=?",
            (evidence_id,),
        ).fetchone()
        return Evidence(**dict(row)) if row else None

    # ------------------------------------------------------------------
    # 解释、解释状态事件与异议
    # ------------------------------------------------------------------
    def add_interpretation(self, interp: Interpretation) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO interpretations(interpretation_id,exhibit_id,expert_id,dating,"
                "rationale,evidence_ids,proposed_at,status) VALUES(?,?,?,?,?,?,?,?)",
                (
                    interp.interpretation_id,
                    interp.exhibit_id,
                    interp.expert_id,
                    interp.dating,
                    interp.rationale,
                    json.dumps(list(interp.evidence_ids), ensure_ascii=False),
                    interp.proposed_at,
                    interp.status,
                ),
            )
            self.connection.execute(
                "INSERT INTO interpretation_events(interpretation_id,event,at,actor_id,note) "
                "VALUES(?,?,?,?,?)",
                (interp.interpretation_id, interp.status, interp.proposed_at,
                 interp.expert_id, "提出解释"),
            )

    def get_interpretation(self, interpretation_id: str) -> Interpretation | None:
        row = self.connection.execute(
            "SELECT interpretation_id,exhibit_id,expert_id,dating,rationale,evidence_ids,"
            "proposed_at,status FROM interpretations WHERE interpretation_id=?",
            (interpretation_id,),
        ).fetchone()
        if not row:
            return None
        data = dict(row)
        return Interpretation(
            interpretation_id=data["interpretation_id"],
            exhibit_id=data["exhibit_id"],
            expert_id=data["expert_id"],
            dating=data["dating"],
            rationale=data["rationale"],
            evidence_ids=_tuple(data["evidence_ids"]),
            proposed_at=data["proposed_at"],
            status=data["status"],
        )

    def interpretations_for_exhibit(self, exhibit_id: str) -> list[Interpretation]:
        rows = self.connection.execute(
            "SELECT interpretation_id,exhibit_id,expert_id,dating,rationale,evidence_ids,"
            "proposed_at,status FROM interpretations WHERE exhibit_id=? ORDER BY proposed_at",
            (exhibit_id,),
        ).fetchall()
        result = []
        for row in rows:
            data = dict(row)
            result.append(
                Interpretation(
                    interpretation_id=data["interpretation_id"],
                    exhibit_id=data["exhibit_id"],
                    expert_id=data["expert_id"],
                    dating=data["dating"],
                    rationale=data["rationale"],
                    evidence_ids=_tuple(data["evidence_ids"]),
                    proposed_at=data["proposed_at"],
                    status=data["status"],
                )
            )
        return result

    def append_interpretation_event(self, interpretation_id: str, event: str,
                                    at: str, actor_id: str = "", note: str = "") -> None:
        status_map = {
            "proposed": "proposed", "adopted": "adopted", "readopted": "adopted",
            "opposed": "opposed", "retired": "retired",
        }
        with self.connection:
            self.connection.execute(
                "INSERT INTO interpretation_events(interpretation_id,event,at,actor_id,note) "
                "VALUES(?,?,?,?,?)",
                (interpretation_id, event, at, actor_id, note),
            )
            if event in status_map:
                self.connection.execute(
                    "UPDATE interpretations SET status=? WHERE interpretation_id=?",
                    (status_map[event], interpretation_id),
                )

    def interpretation_events(self, interpretation_id: str) -> list[sqlite3.Row]:
        return self.connection.execute(
            "SELECT interpretation_id,event,at,actor_id,note FROM interpretation_events "
            "WHERE interpretation_id=? ORDER BY at,id",
            (interpretation_id,),
        ).fetchall()

    def add_objection(self, objection: Objection) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO objections(objection_id,interpretation_id,raiser_id,reason,"
                "raised_at,resolution,resolved_by,resolved_at,note) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    objection.objection_id,
                    objection.interpretation_id,
                    objection.raiser_id,
                    objection.reason,
                    objection.raised_at,
                    objection.resolution,
                    objection.resolved_by,
                    objection.resolved_at,
                    objection.note,
                ),
            )

    def get_objection(self, objection_id: str) -> Objection | None:
        row = self.connection.execute(
            "SELECT objection_id,interpretation_id,raiser_id,reason,raised_at,resolution,"
            "resolved_by,resolved_at,note FROM objections WHERE objection_id=?",
            (objection_id,),
        ).fetchone()
        return Objection(**dict(row)) if row else None

    def open_objections(self, interpretation_id: str | None = None) -> list[Objection]:
        if interpretation_id is None:
            rows = self.connection.execute(
                "SELECT objection_id,interpretation_id,raiser_id,reason,raised_at,resolution,"
                "resolved_by,resolved_at,note FROM objections WHERE resolution='open'"
            ).fetchall()
        else:
            rows = self.connection.execute(
                "SELECT objection_id,interpretation_id,raiser_id,reason,raised_at,resolution,"
                "resolved_by,resolved_at,note FROM objections WHERE resolution='open' "
                "AND interpretation_id=?",
                (interpretation_id,),
            ).fetchall()
        return [Objection(**dict(r)) for r in rows]

    def resolve_objection(self, objection_id: str, resolution: str, by: str,
                          at: str, note: str) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE objections SET resolution=?,resolved_by=?,resolved_at=?,note=? "
                "WHERE objection_id=?",
                (resolution, by, at, note, objection_id),
            )

    # ------------------------------------------------------------------
    # 借展与展示位置
    # ------------------------------------------------------------------
    def add_loan(self, loan: Loan) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO loans(loan_id,exhibit_id,lender,visible_from,visible_until,"
                "agreed_at) VALUES(?,?,?,?,?,?)",
                (loan.loan_id, loan.exhibit_id, loan.lender, loan.visible_from,
                 loan.visible_until, loan.agreed_at),
            )

    def get_loan(self, loan_id: str) -> Loan | None:
        row = self.connection.execute(
            "SELECT loan_id,exhibit_id,lender,visible_from,visible_until,agreed_at "
            "FROM loans WHERE loan_id=?",
            (loan_id,),
        ).fetchone()
        return Loan(**dict(row)) if row else None

    def add_placement(self, placement: Placement) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO placements(placement_id,unit_id,exhibit_id,start_at,end_at,slot,"
                "closed,closed_at,reason) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    placement.placement_id, placement.unit_id, placement.exhibit_id,
                    placement.start_at, placement.end_at, placement.slot,
                    1 if placement.closed else 0, placement.closed_at, placement.reason,
                ),
            )

    def open_placements_for_exhibit(self, exhibit_id: str, at: str) -> list[Placement]:
        """尚未结束、且 end_at 晚于 at 的位置（撤展只影响这些“未来”位置）。"""
        rows = self.connection.execute(
            "SELECT placement_id,unit_id,exhibit_id,start_at,end_at,slot,closed,closed_at,"
            "reason FROM placements WHERE exhibit_id=? AND closed=0 AND end_at>?",
            (exhibit_id, at),
        ).fetchall()
        return [self._placement(r) for r in rows]

    def placements_for_unit(self, unit_id: str) -> list[Placement]:
        rows = self.connection.execute(
            "SELECT placement_id,unit_id,exhibit_id,start_at,end_at,slot,closed,closed_at,"
            "reason FROM placements WHERE unit_id=? ORDER BY start_at,placement_id",
            (unit_id,),
        ).fetchall()
        return [self._placement(r) for r in rows]

    def close_placement(self, placement_id: str, end_at: str, closed_at: str,
                        reason: str) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE placements SET end_at=?,closed=1,closed_at=?,reason=? "
                "WHERE placement_id=? AND closed=0",
                (end_at, closed_at, reason, placement_id),
            )

    @staticmethod
    def _placement(row: sqlite3.Row) -> Placement:
        data = dict(row)
        return Placement(
            placement_id=data["placement_id"],
            unit_id=data["unit_id"],
            exhibit_id=data["exhibit_id"],
            start_at=data["start_at"],
            end_at=data["end_at"],
            slot=data["slot"],
            closed=bool(data["closed"]),
            closed_at=data["closed_at"],
            reason=data["reason"],
        )

    # ------------------------------------------------------------------
    # 文本分支与勘误
    # ------------------------------------------------------------------
    def add_branch(self, branch: TextBranch) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO branches(branch_id,unit_id,channel,body,effective_from,"
                "effective_until,exhibit_ids,evidence_ids,interp_ids,published_at,status,"
                "superseded_by) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    branch.branch_id, branch.unit_id, branch.channel, branch.body,
                    branch.effective_from, branch.effective_until,
                    json.dumps(list(branch.exhibit_ids), ensure_ascii=False),
                    json.dumps(list(branch.evidence_ids), ensure_ascii=False),
                    json.dumps(list(branch.interp_ids), ensure_ascii=False),
                    branch.published_at, branch.status, branch.superseded_by,
                ),
            )

    def get_branch(self, branch_id: str) -> TextBranch | None:
        row = self.connection.execute(
            "SELECT branch_id,unit_id,channel,body,effective_from,effective_until,exhibit_ids,"
            "evidence_ids,interp_ids,published_at,status,superseded_by FROM branches "
            "WHERE branch_id=?",
            (branch_id,),
        ).fetchone()
        return self._branch(row) if row else None

    def pending_branches_for_exhibit(self, exhibit_id: str) -> list[TextBranch]:
        rows = self.connection.execute(
            "SELECT branch_id,unit_id,channel,body,effective_from,effective_until,exhibit_ids,"
            "evidence_ids,interp_ids,published_at,status,superseded_by FROM branches "
            "WHERE status='pending'"
        ).fetchall()
        return [b for b in (self._branch(r) for r in rows) if exhibit_id in b.exhibit_ids]

    def branches_for_unit_channel(self, unit_id: str, channel: str) -> list[TextBranch]:
        rows = self.connection.execute(
            "SELECT branch_id,unit_id,channel,body,effective_from,effective_until,exhibit_ids,"
            "evidence_ids,interp_ids,published_at,status,superseded_by FROM branches "
            "WHERE unit_id=? AND channel=? ORDER BY effective_from,branch_id",
            (unit_id, channel),
        ).fetchall()
        return [self._branch(r) for r in rows]

    def public_branches_for_exhibit(self, exhibit_id: str) -> list[TextBranch]:
        rows = self.connection.execute(
            "SELECT branch_id,unit_id,channel,body,effective_from,effective_until,exhibit_ids,"
            "evidence_ids,interp_ids,published_at,status,superseded_by FROM branches "
            "WHERE published_at<>''"
        ).fetchall()
        return [b for b in (self._branch(r) for r in rows) if exhibit_id in b.exhibit_ids]

    def supersede_branch(self, branch_id: str, superseded_by: str, at: str) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE branches SET status='superseded',superseded_by=?,"
                "effective_until=? WHERE branch_id=?",
                (superseded_by, at, branch_id),
            )

    def publish_branch(self, branch_id: str, published_at: str, close_other_id: str = "",
                       close_until: str = "") -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE branches SET status='effective',published_at=? WHERE branch_id=?",
                (published_at, branch_id),
            )
            if close_other_id:
                self.connection.execute(
                    "UPDATE branches SET status='superseded',effective_until=? "
                    "WHERE branch_id=? AND status='effective'",
                    (close_until, close_other_id),
                )

    def add_corrigendum(self, corr: Corrigendum) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO corrigenda(corrigendum_id,branch_id,seq,note,issued_at,release_id) "
                "VALUES(?,?,?,?,?,?)",
                (corr.corrigendum_id, corr.branch_id, corr.seq, corr.note,
                 corr.issued_at, corr.release_id),
            )

    def next_corrigendum_seq(self, branch_id: str) -> int:
        row = self.connection.execute(
            "SELECT COALESCE(MAX(seq),0) AS m FROM corrigenda WHERE branch_id=?",
            (branch_id,),
        ).fetchone()
        return int(row["m"]) + 1

    def corrigenda_for_branch(self, branch_id: str) -> list[Corrigendum]:
        rows = self.connection.execute(
            "SELECT corrigendum_id,branch_id,seq,note,issued_at,release_id FROM corrigenda "
            "WHERE branch_id=? ORDER BY seq",
            (branch_id,),
        ).fetchall()
        return [Corrigendum(**dict(r)) for r in rows]

    @staticmethod
    def _branch(row: sqlite3.Row) -> TextBranch:
        data = dict(row)
        return TextBranch(
            branch_id=data["branch_id"],
            unit_id=data["unit_id"],
            channel=data["channel"],
            body=data["body"],
            effective_from=data["effective_from"],
            effective_until=data["effective_until"],
            exhibit_ids=_tuple(data["exhibit_ids"]),
            evidence_ids=_tuple(data["evidence_ids"]),
            interp_ids=_tuple(data["interp_ids"]),
            published_at=data["published_at"],
            status=data["status"],
            superseded_by=data["superseded_by"],
        )

    # ------------------------------------------------------------------
    # 签发与回执
    # ------------------------------------------------------------------
    def get_release(self, release_id: str) -> Release | None:
        row = self.connection.execute(
            "SELECT release_id,unit_id,curator_id,summary,digest,created_at,channels,pins "
            "FROM releases WHERE release_id=?",
            (release_id,),
        ).fetchone()
        if not row:
            return None
        data = dict(row)
        return Release(
            release_id=data["release_id"],
            unit_id=data["unit_id"],
            curator_id=data["curator_id"],
            summary=data["summary"],
            digest=data["digest"],
            created_at=data["created_at"],
            channels=tuple(_jloads(data["channels"], [])),
            pins=_jloads(data["pins"], {}),
        )

    def insert_release(self, release: Release) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO releases(release_id,unit_id,curator_id,summary,digest,created_at,"
                "channels,pins) VALUES(?,?,?,?,?,?,?,?)",
                (
                    release.release_id, release.unit_id, release.curator_id, release.summary,
                    release.digest, release.created_at,
                    json.dumps(list(release.channels), ensure_ascii=False),
                    json.dumps(release.pins, ensure_ascii=False),
                ),
            )

    # ------------------------------------------------------------------
    # 计划效果台账（恰好一次）与复核任务
    # ------------------------------------------------------------------
    def add_effect(self, effect: ScheduledEffect) -> bool:
        """登记未来效果；effect_id 已存在则忽略（重试安全）。返回是否新增。"""
        try:
            with self.connection:
                self.connection.execute(
                    "INSERT INTO scheduled_effects(effect_id,kind,due_at,release_id,payload,"
                    "status,applied_at) VALUES(?,?,?,?,?,?,?)",
                    (
                        effect.effect_id, effect.kind, effect.due_at, effect.release_id,
                        json.dumps(effect.payload, ensure_ascii=False),
                        effect.status, effect.applied_at,
                    ),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def get_effect(self, effect_id: str) -> ScheduledEffect | None:
        row = self.connection.execute(
            "SELECT effect_id,kind,due_at,release_id,payload,status,applied_at "
            "FROM scheduled_effects WHERE effect_id=?",
            (effect_id,),
        ).fetchone()
        if not row:
            return None
        data = dict(row)
        return ScheduledEffect(
            effect_id=data["effect_id"],
            kind=data["kind"],
            due_at=data["due_at"],
            release_id=data["release_id"],
            payload=_jloads(data["payload"], {}),
            status=data["status"],
            applied_at=data["applied_at"],
        )

    def due_effects(self, now: str) -> list[ScheduledEffect]:
        rows = self.connection.execute(
            "SELECT effect_id,kind,due_at,release_id,payload,status,applied_at "
            "FROM scheduled_effects WHERE status='pending' AND due_at<=? ORDER BY due_at,effect_id",
            (now,),
        ).fetchall()
        result = []
        for row in rows:
            data = dict(row)
            result.append(
                ScheduledEffect(
                    effect_id=data["effect_id"],
                    kind=data["kind"],
                    due_at=data["due_at"],
                    release_id=data["release_id"],
                    payload=_jloads(data["payload"], {}),
                    status=data["status"],
                    applied_at=data["applied_at"],
                )
            )
        return result

    def mark_effect_applied(self, effect_id: str, applied_at: str) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE scheduled_effects SET status='applied',applied_at=? "
                "WHERE effect_id=? AND status='pending'",
                (applied_at, effect_id),
            )

    def cancel_effects(self, effect_ids: Iterable[str]) -> None:
        ids = list(effect_ids)
        if not ids:
            return
        with self.connection:
            self.connection.executemany(
                "UPDATE scheduled_effects SET status='cancelled' WHERE effect_id=? "
                "AND status='pending'",
                [(eid,) for eid in ids],
            )

    def add_review(self, review: ReviewTask) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO review_tasks(review_id,unit_id,trigger,ref_id,note,created_at,"
                "status,closed_at) VALUES(?,?,?,?,?,?,?,?)",
                (
                    review.review_id, review.unit_id, review.trigger, review.ref_id,
                    review.note, review.created_at, review.status, review.closed_at,
                ),
            )

    def reviews(self, status: str | None = None, unit_id: str = "") -> list[ReviewTask]:
        sql = ("SELECT review_id,unit_id,trigger,ref_id,note,created_at,status,closed_at "
               "FROM review_tasks")
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status=?")
            params.append(status)
        if unit_id:
            clauses.append("unit_id=?")
            params.append(unit_id)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at,review_id"
        rows = self.connection.execute(sql, params).fetchall()
        return [ReviewTask(**dict(r)) for r in rows]

    def close_review(self, review_id: str, at: str) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE review_tasks SET status='closed',closed_at=? WHERE review_id=?",
                (at, review_id),
            )
