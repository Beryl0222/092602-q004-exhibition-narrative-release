"""宫廷艺术展陈叙事签发的 SQLite 存储。"""
import sqlite3
from pathlib import Path

from .domain import Record

_SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    record_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    state TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK(revision > 0),
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS units (
    unit_id TEXT PRIMARY KEY,
    parent_id TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS timeline_nodes (
    node_id TEXT PRIMARY KEY,
    unit_id TEXT NOT NULL,
    label TEXT NOT NULL,
    start_year INTEGER NOT NULL DEFAULT 0,
    end_year INTEGER NOT NULL DEFAULT 0,
    sort INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS exhibits (
    exhibit_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    lender_id TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS evidence (
    evidence_id TEXT PRIMARY KEY,
    exhibit_id TEXT NOT NULL,
    claim TEXT NOT NULL,
    method TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS craft_terms (
    term_id TEXT PRIMARY KEY,
    term TEXT NOT NULL,
    definition TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS channels (
    channel_id TEXT PRIMARY KEY,
    name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS loan_windows (
    loan_id TEXT PRIMARY KEY,
    exhibit_id TEXT NOT NULL,
    visible_from TEXT NOT NULL,
    visible_to TEXT NOT NULL,
    closed_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS interpretations (
    interpretation_id TEXT PRIMARY KEY,
    exhibit_id TEXT NOT NULL,
    evidence_id TEXT NOT NULL,
    proposed_by TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS objections (
    objection_id TEXT PRIMARY KEY,
    interpretation_id TEXT NOT NULL,
    raised_by TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    raised_at TEXT NOT NULL,
    resolved_by TEXT NOT NULL DEFAULT '',
    resolved_at TEXT NOT NULL DEFAULT '',
    resolution TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS text_versions (
    version_id TEXT PRIMARY KEY,
    unit_id TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    body TEXT NOT NULL,
    status TEXT NOT NULL,
    effective_from TEXT NOT NULL,
    effective_until TEXT NOT NULL DEFAULT '',
    release_id TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS errata (
    errata_id TEXT PRIMARY KEY,
    version_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    note TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(version_id, seq)
);
CREATE TABLE IF NOT EXISTS releases (
    release_id TEXT PRIMARY KEY,
    digest TEXT NOT NULL,
    unit_id TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    version_id TEXT NOT NULL,
    status TEXT NOT NULL,
    receipt TEXT NOT NULL,
    issued_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS release_pins (
    release_id TEXT NOT NULL,
    pin_type TEXT NOT NULL,
    ref_id TEXT NOT NULL,
    snapshot TEXT NOT NULL,
    PRIMARY KEY (release_id, pin_type, ref_id)
);
CREATE TABLE IF NOT EXISTS display_slots (
    slot_id TEXT PRIMARY KEY,
    unit_id TEXT NOT NULL,
    exhibit_id TEXT NOT NULL,
    start TEXT NOT NULL,
    finish TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'scheduled',
    cancelled_at TEXT NOT NULL DEFAULT '',
    cancel_reason TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS review_tasks (
    task_id TEXT PRIMARY KEY,
    reason TEXT NOT NULL,
    ref_type TEXT NOT NULL,
    ref_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS transitions (
    kind TEXT NOT NULL,
    ref_key TEXT NOT NULL,
    applied_at TEXT NOT NULL,
    PRIMARY KEY (kind, ref_key)
);
"""


class Store:
    """管理 SQLite 表和事务写入。多步写入由调用方用 `with store.connection:` 包裹。"""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.connection = sqlite3.connect(str(path))
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.executescript(_SCHEMA)
        self.connection.commit()

    def add(self, record: Record) -> Record:
        value = record.stamped()
        with self.connection:
            self.connection.execute(
                "INSERT INTO records(record_id, owner_id, state, revision, created_at) VALUES(?,?,?,?,?)",
                (value.record_id, value.owner_id, value.state, value.revision, value.created_at),
            )
        return value

    def get(self, record_id: str) -> Record | None:
        row = self.fetch_one(
            "SELECT record_id, owner_id, state, revision, created_at FROM records WHERE record_id=?",
            (record_id,),
        )
        return Record(**dict(row)) if row else None

    def insert(self, table: str, row: dict) -> None:
        columns = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        self.connection.execute(
            f"INSERT INTO {table} ({columns}) VALUES ({marks})", tuple(row.values())
        )

    def insert_or_ignore(self, table: str, row: dict) -> bool:
        columns = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        cursor = self.connection.execute(
            f"INSERT OR IGNORE INTO {table} ({columns}) VALUES ({marks})", tuple(row.values())
        )
        return cursor.rowcount > 0

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        return self.connection.execute(sql, params)

    def fetch(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        return self.connection.execute(sql, params).fetchall()

    def fetch_one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        return self.connection.execute(sql, params).fetchone()
