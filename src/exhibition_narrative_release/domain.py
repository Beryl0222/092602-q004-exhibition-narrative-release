"""宫廷艺术展陈叙事签发的领域记录、时间工具与异常。"""
from dataclasses import dataclass, replace
from datetime import datetime, timezone


def utc_now() -> str:
    """返回当前 UTC 时间的 ISO 字符串。"""
    return datetime.now(timezone.utc).isoformat()


def norm_ts(value: object) -> str:
    """把字符串或 datetime 统一为 UTC ISO 字符串，保证入库后可比较。"""
    moment = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat()


class DomainError(Exception):
    """领域规则错误基类。"""


class ConflictError(DomainError):
    """编号相同但摘要不一致等冲突。"""


class NotFoundError(DomainError):
    """引用的实体不存在。"""


class StateError(DomainError):
    """当前状态不允许该操作。"""


@dataclass(frozen=True)
class Record:
    record_id: str
    owner_id: str
    state: str
    revision: int = 1
    created_at: str = ""

    def stamped(self) -> "Record":
        value = self.created_at or datetime.now(timezone.utc).isoformat()
        return replace(self, created_at=value)


@dataclass(frozen=True)
class Unit:
    """展陈层级中的一个单元。"""

    unit_id: str
    name: str
    parent_id: str = ""


@dataclass(frozen=True)
class TimelineNode:
    """时间轴节点，标注单元内的一段历史脉络。"""

    node_id: str
    unit_id: str
    label: str
    start_year: int = 0
    end_year: int = 0
    sort: int = 0


@dataclass(frozen=True)
class Exhibit:
    """展品身份。"""

    exhibit_id: str
    name: str
    category: str
    lender_id: str = ""


@dataclass(frozen=True)
class Evidence:
    """年代证据，一旦登记不可改写，新的年代判断登记为新证据。"""

    evidence_id: str
    exhibit_id: str
    claim: str
    method: str = ""
    source: str = ""
    created_at: str = ""


@dataclass(frozen=True)
class CraftTerm:
    """工艺术语。"""

    term_id: str
    term: str
    definition: str = ""


@dataclass(frozen=True)
class Channel:
    """发布渠道，如墙签、导览接口、新闻资料。"""

    channel_id: str
    name: str


@dataclass(frozen=True)
class LoanWindow:
    """借展可见期，到期后触发撤展且只触发一次。"""

    loan_id: str
    exhibit_id: str
    visible_from: str
    visible_to: str
    closed_at: str = ""


@dataclass(frozen=True)
class Interpretation:
    """一条解释：某专家依据证据对展品作出的说明。"""

    interpretation_id: str
    exhibit_id: str
    evidence_id: str
    proposed_by: str
    body: str = ""
    created_at: str = ""


@dataclass(frozen=True)
class Objection:
    """专家异议，提出解释的专家不能独自结束它。"""

    objection_id: str
    interpretation_id: str
    raised_by: str
    detail: str = ""
    status: str = "open"
    raised_at: str = ""
    resolved_by: str = ""
    resolved_at: str = ""
    resolution: str = ""


@dataclass(frozen=True)
class TextVersion:
    """说明文本分支上的一个版本。

    状态：pending 待生效 / effective 生效中 / superseded 已被接替 / replaced 未生效即被替换。
    """

    version_id: str
    unit_id: str
    channel_id: str
    body: str
    status: str
    effective_from: str
    release_id: str
    created_at: str
    effective_until: str = ""


@dataclass(frozen=True)
class Erratum:
    """连续勘误中的一条，只挂在公众已经看过的版本上。"""

    errata_id: str
    version_id: str
    seq: int
    note: str
    created_at: str


@dataclass(frozen=True)
class Release:
    """一次签发及其回执，编号与摘要共同保证幂等。"""

    release_id: str
    digest: str
    unit_id: str
    channel_id: str
    version_id: str
    status: str
    receipt: str
    issued_at: str


@dataclass(frozen=True)
class DisplaySlot:
    """一个展示位置：某单元在某时段展示某展品。已经发生的展示不可删除。"""

    slot_id: str
    unit_id: str
    exhibit_id: str
    start: str
    finish: str
    status: str = "scheduled"
    cancelled_at: str = ""
    cancel_reason: str = ""


@dataclass(frozen=True)
class ReviewTask:
    """叙事复核任务，由撤展或借展到期唤起。"""

    task_id: str
    reason: str
    ref_type: str
    ref_id: str
    status: str = "open"
    created_at: str = ""
