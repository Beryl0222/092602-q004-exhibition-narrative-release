"""宫廷艺术展陈叙事签发的领域记录与基础工具。

时间一律使用带时区的 ISO-8601 字符串，内部规范化为 UTC 比较。
所有业务记录均不可变（frozen dataclass）；发生订正或撤展时不修改、不删除
已有记录，而是追加勘误、关闭未来展位并生成叙事复核任务。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any


# ---------------------------------------------------------------------------
# 时间与摘要工具
# ---------------------------------------------------------------------------

def now_iso() -> str:
    """当前 UTC 时间的 ISO-8601 字符串。"""
    return datetime.now(timezone.utc).isoformat()


def normalize_iso(value: str) -> str:
    """把任意带时区（或视为 UTC 的朴素值）的 ISO 时间转为 UTC 规范串。"""
    text = (value or "").strip()
    if not text:
        raise ValueError("时间不能为空")
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def parse_utc(value: str) -> datetime:
    """解析为可比较的 UTC datetime。"""
    return datetime.fromisoformat(normalize_iso(value))


def canonical_json(payload: Any) -> str:
    """排序键、无多余空白的规范 JSON，作为摘要输入。"""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest_of(payload: Any) -> str:
    """对签发内容做 SHA-256 摘要（十六进制），用于幂等与覆盖检测。"""
    body = canonical_json(payload)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 错误
# ---------------------------------------------------------------------------

class NarrativeError(Exception):
    """叙事签发领域错误基类。"""


class ReleaseConflict(NarrativeError):
    """签发编号已存在但内容摘要不同：拒绝覆盖。"""

    def __init__(self, release_id: str, existing_digest: str, incoming_digest: str) -> None:
        super().__init__(
            f"签发编号 {release_id} 已存在但摘要不同，已停止覆盖："
            f"既有 {existing_digest[:12]} != 新 {incoming_digest[:12]}"
        )
        self.release_id = release_id
        self.existing_digest = existing_digest
        self.incoming_digest = incoming_digest


class ObjectionOpenError(NarrativeError):
    """提出解释的专家不能独自结束异议。"""


class ReferenceMismatch(NarrativeError):
    """签发引用了不存在或状态不符的展品 / 证据 / 解释。"""


# ---------------------------------------------------------------------------
# 兼容基线：最简登记记录
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Record:
    record_id: str
    owner_id: str
    state: str
    revision: int = 1
    created_at: str = ""

    def stamped(self) -> "Record":
        value = self.created_at or now_iso()
        return replace(self, created_at=value)


# ---------------------------------------------------------------------------
# 展陈层级：单元（三个互相联动的展陈单元）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Unit:
    unit_id: str
    title: str
    parent_id: str = ""          # 层级通过 parent 形成树
    created_at: str = ""


# ---------------------------------------------------------------------------
# 时间轴节点
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TimelineNode:
    node_id: str
    label: str                  # 例如“媒体开放日”
    at: str                     # 规范化后的 ISO 时间
    unit_id: str = ""           # 可挂在某单元，也可为全局节点


# ---------------------------------------------------------------------------
# 工艺术语（受控词表）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Term:
    term_id: str
    name: str                    # 例如“掐丝珐琅 / 景泰蓝”
    definition: str = ""


# ---------------------------------------------------------------------------
# 展品身份
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Exhibit:
    exhibit_id: str
    title: str
    term_ids: tuple[str, ...] = ()
    created_at: str = ""


# ---------------------------------------------------------------------------
# 年代证据。证据只追加；同一证据可被后续签发重新解读，但记录不变。
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Evidence:
    evidence_id: str
    exhibit_id: str
    kind: str                    # 款识 / 胎体成分 / 纹饰比对 / 档案 …
    summary: str
    observed_at: str             # 证据指向的年代观察时间点（可为空串表示未知）
    recorded_at: str             # 证据入库时间（事实时间）
    recorder_id: str = ""


# ---------------------------------------------------------------------------
# 解释（年代判断）与专家异议
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Interpretation:
    """对某件展品的一种解释（如新的年代判断）。

    status 取值：
      - adopted   已被某单元采用
      - opposed   存在未决异议、暂不采用
      - retired   被新判断取代（旧记录保留，供历史重建）
    """

    interpretation_id: str
    exhibit_id: str
    expert_id: str               # 提出解释的专家
    dating: str                  # 判断的年代，如“明景泰年间”
    rationale: str
    evidence_ids: tuple[str, ...]
    proposed_at: str
    status: str = "proposed"     # proposed / adopted / opposed / retired


@dataclass(frozen=True)
class Objection:
    objection_id: str
    interpretation_id: str
    raiser_id: str               # 提出异议者
    reason: str
    raised_at: str
    resolution: str = "open"     # open / resolved / rejected
    resolved_by: str = ""
    resolved_at: str = ""
    note: str = ""

    @property
    def is_open(self) -> bool:
        return self.resolution == "open"


# ---------------------------------------------------------------------------
# 借展可见期与展示位置
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Loan:
    loan_id: str
    exhibit_id: str
    lender: str
    visible_from: str
    visible_until: str           # 提前撤回时只缩短：见 LoanVisibility 关闭记录
    agreed_at: str = ""


@dataclass(frozen=True)
class Placement:
    """展品在某单元的展示位置（墙签位）。

    撤展不是删除，而是把 end_at 从远期缩短为撤回时刻（close），
    媒体开放日已发生的区间因此原样保留。
    """

    placement_id: str
    unit_id: str
    exhibit_id: str
    start_at: str
    end_at: str                  # 生效中的结束时间；关闭后等于撤回时刻
    slot: str = ""               # 墙签位置编号
    closed: bool = False
    closed_at: str = ""          # 发起关闭（撤展）的业务时间
    reason: str = ""


# ---------------------------------------------------------------------------
# 说明文本分支（墙签 / 导览接口 / 新闻资料）与勘误链
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TextBranch:
    """某单元某渠道的一条说明文本版本。

    状态：
      - pending   已签发、尚未生效（effective_from 在未来）
      - effective 生效中
      - superseded 被未生效替换，或被后续生效版本接续
      - retracted 已被勘误公开更正（记录仍保留）
    """

    branch_id: str
    unit_id: str
    channel: str                 # label / guide / press
    body: str
    effective_from: str
    effective_until: str         # 远期串表示开放
    exhibit_ids: tuple[str, ...] = ()   # 固定引用的展品
    evidence_ids: tuple[str, ...] = ()  # 固定引用的证据
    interp_ids: tuple[str, ...] = ()    # 固定引用的解释
    published_at: str = ""       # 首次对公众生效的时间（未公开为空）
    status: str = "pending"
    superseded_by: str = ""


@dataclass(frozen=True)
class Corrigendum:
    """对“公众已看过”的文本追加的连续勘误。不改正文，只追加说明。"""

    corrigendum_id: str
    branch_id: str
    seq: int                     # 同一文本上的连续序号
    note: str
    issued_at: str
    release_id: str = ""


# ---------------------------------------------------------------------------
# 签发与回执
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Release:
    """一次叙事签发：固定所引用展品与证据的快照。"""

    release_id: str
    unit_id: str
    curator_id: str
    summary: str
    digest: str
    created_at: str
    # 引用快照：键为引用类型:id，值为当时固定的规范 JSON
    pins: dict[str, str] = field(default_factory=dict)
    channels: tuple[str, ...] = ()


@dataclass(frozen=True)
class Receipt:
    release_id: str
    digest: str
    created_at: str
    replayed: bool = False       # True 表示这是内容相同的重试，返回的是原回执


# ---------------------------------------------------------------------------
# 计划效果台账与叙事复核（驱动“各执行一次”）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ScheduledEffect:
    """待生效说明、到期撤展等未来效果。

    status: pending / applied / cancelled
    同一 effect_id 唯一；重启后靠 status 与唯一约束保证恰好应用一次。
    """

    effect_id: str
    kind: str                    # text_effective / loan_recall
    due_at: str
    release_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    status: str = "pending"
    applied_at: str = ""


@dataclass(frozen=True)
class ReviewTask:
    """撤展或异议触发的叙事复核任务。"""

    review_id: str
    unit_id: str
    trigger: str                 # loan_recall / objection / dating_revision
    ref_id: str
    note: str
    created_at: str
    status: str = "open"         # open / closed
    closed_at: str = ""
