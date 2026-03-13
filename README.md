# Policy Trend Agent
## Online Safety · AI Safety · Technology Governance
### Jurisdictions: Singapore · Australia · UK · EU · ASEAN
### Delivery: Email + Telegram

---

## Architecture

```
scheduler (Railway cron / GitHub Actions)
    │
    ▼
collector/          ← RSS feeds + web scrapers
    │
    ▼
analyser/           ← Claude API: classify, summarise, trend-detect
    │
    ▼
storage/            ← Postgres (prod) or SQLite (local)
    │
    ▼
delivery/           ← Email digest + Telegram alerts
```

---

## Quick Start (local)

```bash
pip install -r requirements.txt
cp config/config.example.yaml config/config.yaml
# Fill in: Anthropic API key, Gmail credentials, Telegram bot token

python scripts/run_agent.py --dry-run   # test without sending
python scripts/run_agent.py             # live run
python scripts/run_agent.py --digest    # force send digest now
```

## Deploy to Railway

See **DEPLOY.md** for the full step-by-step guide.

---

## Project Structure

```
policy-agent/
├── README.md
├── DEPLOY.md                       # deployment guide
├── requirements.txt
├── Procfile                        # Railway process definition
├── railway.toml                    # Railway cron config
├── runtime.txt                     # Python version
├── config/
│   ├── config.example.yaml         # copy → config.yaml, fill in secrets
│   └── sources.yaml                # all RSS feeds + scrape targets
├── collector/
│   ├── rss_collector.py            # RSS/Atom feed ingestion
│   └── scraper.py                  # HTML scraper for non-RSS sites
├── analyser/
│   └── claude_analyser.py          # Claude API: classify + synthesise
├── storage/
│   ├── database.py                 # SQLite (local)
│   └── postgres_database.py        # Postgres (production)
├── delivery/
│   ├── email_delivery.py           # HTML email: alerts + digest
│   └── telegram_delivery.py        # Telegram bot: alerts + digest
├── dashboard/
│   └── app.py                      # Flask web dashboard
├── migrations/
│   └── 001_initial_schema.sql      # Postgres schema (run once)
└── scripts/
    ├── run_agent.py                 # main entry point
    └── export_csv.py               # export items to CSV
```

---

## Tagging Schema

Every item gets tagged automatically by Claude:

| Field | Values |
|---|---|
| `jurisdiction` | sg · au · uk · eu · asean · global |
| `domain` | online_safety · ai_safety · tech_governance · other |
| `content_type` | legislation · consultation · enforcement · guidance · academic · news · speech |
| `urgency` | monitoring · notable · urgent |
| `sentiment` | regulatory_tightening · regulatory_loosening · neutral |

---

## Environment Variables (Railway)

| Variable | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Claude API key |
| `SMTP_HOST` | e.g. `smtp.gmail.com` |
| `SMTP_USER` | your email address |
| `SMTP_PASS` | Gmail app password |
| `EMAIL_RECIPIENTS` | comma-separated list |
| `TELEGRAM_BOT_TOKEN` | from @BotFather |
| `TELEGRAM_CHAT_ID` | group/channel ID |
| `DATABASE_URL` | auto-set by Railway Postgres |
