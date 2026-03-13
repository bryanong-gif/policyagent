# storage/database.py
"""
SQLite storage layer.
Handles deduplication, CRUD, and querying for the policy agent.
"""

import sqlite3
import json
from datetime import datetime
from typing import Optional
from analyser.claude_analyser import AnalysedItem
import hashlib


CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    url_hash        TEXT UNIQUE NOT NULL,
    source_id       TEXT NOT NULL,
    title           TEXT NOT NULL,
    url             TEXT NOT NULL,
    published       TEXT,
    jurisdiction    TEXT NOT NULL,
    domain          TEXT,
    content_type    TEXT,
    urgency         TEXT DEFAULT 'monitoring',
    sentiment       TEXT DEFAULT 'neutral',
    relevance_score INTEGER DEFAULT 5,
    summary         TEXT,
    key_points      TEXT,   -- JSON array
    tags            TEXT,   -- JSON array
    implications    TEXT,
    raw_domains     TEXT,   -- JSON array
    created_at      TEXT DEFAULT (datetime('now')),
    notified        INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_jurisdiction ON items (jurisdiction);
CREATE INDEX IF NOT EXISTS idx_domain ON items (domain);
CREATE INDEX IF NOT EXISTS idx_urgency ON items (urgency);
CREATE INDEX IF NOT EXISTS idx_created_at ON items (created_at);

CREATE TABLE IF NOT EXISTS digests (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    period_start TEXT,
    period_end   TEXT,
    item_count   INTEGER,
    synthesis    TEXT,
    created_at   TEXT DEFAULT (datetime('now'))
);
"""


def url_hash(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:32]


class PolicyDatabase:
    def __init__(self, db_path: str = "storage/policy_agent.db"):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(CREATE_TABLES)
        self.conn.commit()

    def item_exists(self, url: str) -> bool:
        cur = self.conn.execute(
            "SELECT 1 FROM items WHERE url_hash = ?", (url_hash(url),)
        )
        return cur.fetchone() is not None

    def insert_item(self, item: AnalysedItem) -> Optional[int]:
        """Insert item, return row id or None if duplicate."""
        h = url_hash(item.url)
        try:
            cur = self.conn.execute(
                """INSERT INTO items
                   (url_hash, source_id, title, url, published, jurisdiction,
                    domain, content_type, urgency, sentiment, relevance_score,
                    summary, key_points, tags, implications, raw_domains)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    h, item.source_id, item.title, item.url, item.published,
                    item.jurisdiction, item.domain, item.content_type,
                    item.urgency, item.sentiment, item.relevance_score,
                    item.summary,
                    json.dumps(item.key_points),
                    json.dumps(item.tags),
                    item.implications,
                    json.dumps(item.raw_domains),
                ),
            )
            self.conn.commit()
            return cur.lastrowid
        except sqlite3.IntegrityError:
            return None  # duplicate

    def insert_batch(self, items: list[AnalysedItem]) -> tuple[int, int]:
        """Insert many items. Returns (inserted, skipped)."""
        inserted = skipped = 0
        for item in items:
            row_id = self.insert_item(item)
            if row_id:
                inserted += 1
            else:
                skipped += 1
        return inserted, skipped

    def get_unnotified(self, urgency_filter: Optional[str] = None) -> list[sqlite3.Row]:
        """Fetch items that haven't been sent in a notification yet."""
        query = "SELECT * FROM items WHERE notified = 0"
        params = []
        if urgency_filter:
            query += " AND urgency = ?"
            params.append(urgency_filter)
        query += " ORDER BY relevance_score DESC, created_at DESC"
        return self.conn.execute(query, params).fetchall()

    def mark_notified(self, item_ids: list[int]):
        if item_ids:
            placeholders = ",".join("?" * len(item_ids))
            self.conn.execute(
                f"UPDATE items SET notified = 1 WHERE id IN ({placeholders})",
                item_ids,
            )
            self.conn.commit()

    def save_digest(self, period_start: str, period_end: str,
                    item_count: int, synthesis: str):
        self.conn.execute(
            """INSERT INTO digests (period_start, period_end, item_count, synthesis)
               VALUES (?,?,?,?)""",
            (period_start, period_end, item_count, synthesis),
        )
        self.conn.commit()

    def query_items(
        self,
        jurisdiction: Optional[str] = None,
        domain: Optional[str] = None,
        urgency: Optional[str] = None,
        days: int = 7,
        limit: int = 50,
    ) -> list[sqlite3.Row]:
        query = "SELECT * FROM items WHERE created_at >= datetime('now', ?)"
        params = [f"-{days} days"]
        if jurisdiction:
            query += " AND jurisdiction = ?"
            params.append(jurisdiction)
        if domain:
            query += " AND domain = ?"
            params.append(domain)
        if urgency:
            query += " AND urgency = ?"
            params.append(urgency)
        query += " ORDER BY relevance_score DESC, created_at DESC LIMIT ?"
        params.append(limit)
        return self.conn.execute(query, params).fetchall()

    def close(self):
        self.conn.close()
