#!/usr/bin/env python3
# scripts/export_csv.py
"""Export items from the DB to CSV for analysis in Excel/Sheets."""

import sys, os, csv
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml
from storage.database import PolicyDatabase
from datetime import datetime


def main():
    config_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "config", "config.yaml"
    )
    with open(config_path) as f:
        config = yaml.safe_load(f)

    db = PolicyDatabase(config.get("database", {}).get("path", "storage/policy_agent.db"))
    items = db.query_items(days=90, limit=1000)

    filename = f"policy_export_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "id", "title", "jurisdiction", "domain", "content_type",
            "urgency", "sentiment", "relevance_score", "published",
            "summary", "implications", "url", "source_id", "created_at"
        ])
        for item in items:
            writer.writerow([
                item["id"], item["title"], item["jurisdiction"],
                item["domain"], item["content_type"], item["urgency"],
                item["sentiment"], item["relevance_score"], item["published"],
                item["summary"], item["implications"], item["url"],
                item["source_id"], item["created_at"],
            ])

    print(f"Exported {len(items)} items to {filename}")
    db.close()


if __name__ == "__main__":
    main()
