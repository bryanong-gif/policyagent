# storage/postgres_database.py
"""
Postgres adapter — drop-in replacement for database.py.
Install: pip install psycopg2-binary
Set DATABASE_URL in config.yaml:
  database:
    postgres: "postgresql://user:pass@host:5432/policy_agent"
"""

import json
import hashlib
import psycopg2
import psycopg2.extras
from datetime import datetime
from typing import Optional
from analyser.claude_analyser import AnalysedItem


def url_hash(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:32]


class PolicyDatabasePG:
    """Postgres-backed policy store. Mirrors PolicyDatabase (SQLite) API exactly."""

    def __init__(self, dsn: str):
        self.conn = psycopg2.connect(dsn)
        self.conn.autocommit = False
        psycopg2.extras.register_default_jsonb(self.conn)

    def item_exists(self, url: str) -> bool:
        with self.conn.cursor() as cur:
            cur.execute("SELECT 1 FROM items WHERE url_hash = %s", (url_hash(url),))
            return cur.fetchone() is not None

    def insert_item(self, item: AnalysedItem) -> Optional[int]:
        h = url_hash(item.url)
        try:
            with self.conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO items
                       (url_hash, source_id, title, url, published, jurisdiction,
                        domain, content_type, urgency, sentiment, relevance_score,
                        summary, key_points, tags, implications, raw_domains)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       RETURNING id""",
                    (
                        h, item.source_id, item.title, item.url,
                        item.published, item.jurisdiction,
                        item.domain, item.content_type,
                        item.urgency, item.sentiment, item.relevance_score,
                        item.summary,
                        json.dumps(item.key_points),
                        json.dumps(item.tags),
                        item.implications,
                        json.dumps(item.raw_domains),
                    ),
                )
                row = cur.fetchone()
                self.conn.commit()
                return row[0] if row else None
        except psycopg2.errors.UniqueViolation:
            self.conn.rollback()
            return None

    def insert_batch(self, items: list[AnalysedItem]) -> tuple[int, int]:
        inserted = skipped = 0
        for item in items:
            row_id = self.insert_item(item)
            if row_id:
                inserted += 1
            else:
                skipped += 1
        return inserted, skipped

    def get_unnotified(self, urgency_filter: Optional[str] = None):
        query = "SELECT * FROM items WHERE notified = FALSE"
        params = []
        if urgency_filter:
            query += " AND urgency = %s"
            params.append(urgency_filter)
        query += " ORDER BY relevance_score DESC, created_at DESC"
        with self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query, params)
            return cur.fetchall()

    def mark_notified(self, item_ids: list[int]):
        if item_ids:
            with self.conn.cursor() as cur:
                cur.execute(
                    "UPDATE items SET notified = TRUE WHERE id = ANY(%s)", (item_ids,)
                )
            self.conn.commit()

    def save_digest(self, period_start, period_end, item_count, synthesis):
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO digests (period_start, period_end, item_count, synthesis)
                   VALUES (%s,%s,%s,%s)""",
                (period_start, period_end, item_count, synthesis),
            )
        self.conn.commit()

    def query_items(self, jurisdiction=None, domain=None, urgency=None,
                    days=7, limit=50, search: Optional[str] = None):
        query = "SELECT * FROM items WHERE created_at >= NOW() - INTERVAL %s"
        params = [f"{days} days"]
        if jurisdiction:
            query += " AND jurisdiction = %s"
            params.append(jurisdiction)
        if domain:
            query += " AND domain = %s"
            params.append(domain)
        if urgency:
            query += " AND urgency = %s"
            params.append(urgency)
        if search:
            query += " AND search_vector @@ plainto_tsquery('english', %s)"
            params.append(search)
        query += " ORDER BY relevance_score DESC, created_at DESC LIMIT %s"
        params.append(limit)
        with self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query, params)
            return cur.fetchall()

    def close(self):
        self.conn.close()
