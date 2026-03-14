#!/usr/bin/env python3
# scripts/run_agent.py
"""
Policy Trend Agent — main runner.
Two-tier collection: web search sweep (broad) + RSS/scrape (deep).
Delivery: Email + Telegram.
Config: env vars (Railway) or config/config.yaml (local).
"""

import sys, os, argparse
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rich.console import Console
from rich.table import Table

from collector.rss_collector import fetch_all_rss
from collector.scraper import fetch_all_scraped
from collector.web_search_collector import WebSearchCollector
from analyser.claude_analyser import PolicyAnalyser
from storage.database import PolicyDatabase
from delivery.email_delivery import (
    send_urgent_alert as email_urgent,
    send_digest as email_digest,
)
from delivery.telegram_delivery import (
    send_urgent_alert as tg_urgent,
    send_digest as tg_digest,
)

console = Console()


def load_config() -> dict:
    if os.environ.get("ANTHROPIC_API_KEY"):
        console.print("[dim]Config: environment variables[/dim]")
        return {
            "anthropic": {
                "api_key": os.environ["ANTHROPIC_API_KEY"],
                "model":   os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-20250514"),
            },
            "email": {
                "enabled":    bool(os.environ.get("SMTP_HOST")),
                "smtp_host":  os.environ.get("SMTP_HOST", ""),
                "smtp_port":  int(os.environ.get("SMTP_PORT", "587")),
                "smtp_user":  os.environ.get("SMTP_USER", ""),
                "smtp_pass":  os.environ.get("SMTP_PASS", ""),
                "recipients": [r.strip() for r in os.environ.get("EMAIL_RECIPIENTS", "").split(",") if r.strip()],
            },
            "telegram": {
                "enabled":   bool(os.environ.get("TELEGRAM_BOT_TOKEN")),
                "bot_token": os.environ.get("TELEGRAM_BOT_TOKEN", ""),
                "chat_id":   os.environ.get("TELEGRAM_CHAT_ID", ""),
            },
            "agent": {
                "lookback_hours":    int(os.environ.get("LOOKBACK_HOURS", "8")),
                "max_items_per_run": int(os.environ.get("MAX_ITEMS_PER_RUN", "100")),
                "digest_schedule":   os.environ.get("DIGEST_SCHEDULE", "weekly"),
                "digest_day":        os.environ.get("DIGEST_DAY", "monday"),
                "digest_time":       os.environ.get("DIGEST_TIME", "08:00"),
                "web_search_queries": int(os.environ.get("WEB_SEARCH_QUERIES", "10")),
            },
            "database": {
                "postgres": os.environ.get("DATABASE_URL", ""),
                "path":     os.environ.get("DB_PATH", "storage/policy_agent.db"),
            },
            "filters": {
                "min_relevance_score": int(os.environ.get("MIN_RELEVANCE_SCORE", "6")),
            },
        }

    import yaml
    config_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "config", "config.yaml",
    )
    if not os.path.exists(config_path):
        console.print("[red]No ANTHROPIC_API_KEY env var and no config/config.yaml found.[/red]")
        sys.exit(1)
    console.print("[dim]Config: config/config.yaml[/dim]")
    with open(config_path) as f:
        return yaml.safe_load(f)


def load_sources(jurisdiction_filter=None):
    import yaml
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "config", "sources.yaml",
    )
    with open(path) as f:
        sources = yaml.safe_load(f).get("sources", [])
    if jurisdiction_filter:
        sources = [s for s in sources if s.get("jurisdiction") == jurisdiction_filter]
    return sources


def get_db(config):
    db_cfg = config.get("database", {})
    pg_dsn = db_cfg.get("postgres")
    if pg_dsn:
        from storage.postgres_database import PolicyDatabasePG
        console.print("[dim]Database: Postgres[/dim]")
        return PolicyDatabasePG(pg_dsn)
    os.makedirs("storage", exist_ok=True)
    console.print("[dim]Database: SQLite[/dim]")
    return PolicyDatabase(db_cfg.get("path", "storage/policy_agent.db"))


def print_summary_table(items):
    table = Table(title="Analysed Items", show_lines=False)
    table.add_column("Urgency", width=10)
    table.add_column("Jur.",    width=6)
    table.add_column("Domain",  width=16)
    table.add_column("Score",   width=6)
    table.add_column("Source",  width=12)
    table.add_column("Title",   width=45)
    color_map = {"urgent": "red", "notable": "yellow", "monitoring": "dim"}
    for item in sorted(items, key=lambda x: (-x.relevance_score, x.urgency)):
        c = color_map.get(item.urgency, "white")
        source_tag = "🔍 web" if item.source_id == "web_search" else "📡 feed"
        table.add_row(
            f"[{c}]{item.urgency}[/{c}]",
            item.jurisdiction.upper(), item.domain,
            str(item.relevance_score), source_tag, item.title[:45],
        )
    console.print(table)


def _should_send_digest(schedule, agent_cfg):
    now = datetime.now()
    if schedule == "daily":
        h, m = map(int, agent_cfg.get("digest_time", "08:00").split(":"))
        return now.hour == h and now.minute < 30
    if schedule == "weekly":
        day_map = {"monday":0,"tuesday":1,"wednesday":2,"thursday":3,
                   "friday":4,"saturday":5,"sunday":6}
        return now.weekday() == day_map.get(agent_cfg.get("digest_day","monday").lower(), 0)
    return False


def run(args):
    console.rule("[bold]Policy Trend Agent")
    console.print(f"[dim]{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}[/dim]\n")

    config    = load_config()
    sources   = load_sources(args.jurisdiction)
    agent_cfg = config.get("agent", {})
    email_cfg = config.get("email", {})
    tg_cfg    = config.get("telegram", {})

    lookback_hours     = agent_cfg.get("lookback_hours", 8)
    max_items          = agent_cfg.get("max_items_per_run", 100)
    min_relevance      = config.get("filters", {}).get("min_relevance_score", 6)
    web_search_queries = agent_cfg.get("web_search_queries", 10)
    api_key            = config["anthropic"]["api_key"]
    model              = config["anthropic"].get("model", "claude-sonnet-4-20250514")

    # ── 1. COLLECT — TIER 1: Web search sweep ────────────────
    console.rule("[cyan]1a. Web Search Sweep (broad)")
    analyser = PolicyAnalyser(api_key=api_key, model=model)
    web_searcher = WebSearchCollector(api_key=api_key, model=model, usage=analyser.usage)
    web_items = web_searcher.collect(max_queries=web_search_queries)

    # ── 1. COLLECT — TIER 2: RSS + scrape sources ─────────────
    console.rule("[cyan]1b. RSS + Scrape Sources (deep)")
    rss_items     = fetch_all_rss(sources, lookback_hours)
    scraped_items = fetch_all_scraped(sources, lookback_hours)

    all_raw = web_items + rss_items + scraped_items
    console.print(
        f"\n[green]Total raw items: {len(all_raw)}[/green] "
        f"(Web search: {len(web_items)}, RSS: {len(rss_items)}, Scraped: {len(scraped_items)})"
    )

    if not all_raw:
        console.print("[yellow]No new items found. Exiting.[/yellow]")
        return

    # ── 2. ANALYSE ───────────────────────────────────────────
    console.rule("[cyan]2. Analysing")
    analysed = analyser.analyse_batch(all_raw, min_relevance=min_relevance, max_items=max_items)
    if not analysed:
        console.print("[yellow]No relevant items after analysis.[/yellow]")
        return
    print_summary_table(analysed)

    if args.dry_run:
        console.print("\n[yellow]Dry run — not storing or sending.[/yellow]")
        return

    # ── 3. STORE ─────────────────────────────────────────────
    console.rule("[cyan]3. Storing")
    db = get_db(config)
    inserted, skipped = db.insert_batch(analysed)
    console.print(f"[green]Inserted: {inserted}[/green] | Skipped (duplicate): {skipped}")

    # ── 4. DELIVER ───────────────────────────────────────────
    console.rule("[cyan]4. Delivering")

    urgent_items = db.get_unnotified(urgency_filter="urgent")
    if urgent_items:
        console.print(f"[red]Sending {len(urgent_items)} urgent alerts...[/red]")
        for item in urgent_items:
            if email_cfg.get("enabled") and email_cfg.get("recipients"):
                email_urgent(email_cfg, email_cfg["recipients"], item)
            if tg_cfg.get("enabled") and tg_cfg.get("bot_token"):
                tg_urgent(tg_cfg["bot_token"], tg_cfg["chat_id"], item)
        db.mark_notified([i["id"] for i in urgent_items])
    else:
        console.print("No urgent items.")

    digest_schedule    = agent_cfg.get("digest_schedule", "weekly")
    should_send_digest = args.digest or _should_send_digest(digest_schedule, agent_cfg)

    if should_send_digest:
        console.print("\n[bold]Generating trend synthesis and digest...[/bold]")
        digest_items = db.get_unnotified()
        if digest_items:
            synthesis = analyser.synthesise_trends(analysed)

            if email_cfg.get("enabled") and email_cfg.get("recipients"):
                email_digest(email_cfg, email_cfg["recipients"], digest_items,
                             synthesis, period_label=digest_schedule.capitalize())

            if tg_cfg.get("enabled") and tg_cfg.get("bot_token"):
                tg_digest(tg_cfg["bot_token"], tg_cfg["chat_id"], digest_items,
                          synthesis, period_label=digest_schedule.capitalize())

            db.save_digest("", datetime.now().isoformat(), len(digest_items), synthesis)
            db.mark_notified([i["id"] for i in digest_items])
            console.print(f"[green]Digest sent: {len(digest_items)} items[/green]")
        else:
            console.print("[dim]No unnotified items for digest.[/dim]")
    else:
        console.print(f"[dim]Digest scheduled for {agent_cfg.get('digest_day','Monday')} — skipping today.[/dim]")

    db.close()
    analyser.print_cost_report()
    console.rule("[green]Done")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Policy Trend Agent")
    parser.add_argument("--digest",       action="store_true")
    parser.add_argument("--jurisdiction", type=str)
    parser.add_argument("--dry-run",      action="store_true")
    run(parser.parse_args())
