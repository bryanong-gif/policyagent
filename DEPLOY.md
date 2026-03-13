# Deployment Guide
## Policy Trend Agent — Railway + Postgres
### Delivery: Email + Telegram

Estimated time: 20 minutes

---

## What you need before starting

- [ ] **Anthropic API key** — https://console.anthropic.com
- [ ] **Gmail account** (or any email with SMTP) — for digest + alerts
- [ ] **Telegram account** — for real-time urgent alerts
- [ ] **GitHub account** — https://github.com (free)
- [ ] **Railway account** — https://railway.app (sign in with GitHub, free trial)

---

## Step 1 — Set up Gmail app password

Railway will send emails on your behalf using Gmail's SMTP server.
You need an **app password** (not your real Gmail password).

1. Go to **myaccount.google.com** → Security → 2-Step Verification → turn it **On**
2. Go to **myaccount.google.com/apppasswords**
3. Click **Create** → name it "Policy Agent" → click **Create**
4. Copy the 16-character password (e.g. `abcd efgh ijkl mnop`) — save it somewhere

> Using Outlook instead? Use `smtp.office365.com`, port `587`, and your normal password.

---

## Step 2 — Set up Telegram bot

1. Open Telegram and message **@BotFather**
2. Send `/newbot` → follow prompts → copy the **bot token** (looks like `123456:ABC-xyz`)
3. Create a group or channel for alerts, add your bot as an **admin**
4. Find your **chat ID**:
   - Send any message to the group
   - Open this URL in your browser (replace TOKEN):
     `https://api.telegram.org/botTOKEN/getUpdates`
   - Look for `"chat":{"id":` — copy that number (will be negative for groups, e.g. `-1001234567890`)

---

## Step 3 — Push the code to GitHub

1. Go to **github.com/new** → create a **private** repo named `policy-agent`
2. Unzip `policy-agent-v3.zip` on your computer
3. Open a terminal in the `policy-agent` folder and run:

```bash
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/policy-agent.git
git push -u origin main
```

> No git? Download from https://git-scm.com/downloads

---

## Step 4 — Create Railway project

1. Go to **railway.app** → sign in with GitHub
2. Click **New Project** → **Deploy from GitHub repo** → select `policy-agent`
3. Railway detects it's Python and starts a build — it will fail on first deploy,
   that's fine, secrets aren't set yet

---

## Step 5 — Add Postgres database

1. Inside your Railway project click **+ New** → **Database** → **Add PostgreSQL**
2. Railway creates Postgres and sets `DATABASE_URL` automatically in your project
3. Click the **Postgres** service → **Connect** tab → copy the **psql connection string**
4. Run the schema migration (one time only):

```bash
# Mac — install psql if needed: brew install postgresql
# Windows — download from postgresql.org/download/windows

psql "YOUR_CONNECTION_STRING_HERE" -f migrations/001_initial_schema.sql
```

You should see output like:
```
CREATE TYPE
CREATE TABLE
CREATE INDEX
...
```

---

## Step 6 — Add environment variables

1. In Railway, click your **policy-agent service** (not the Postgres one)
2. Click the **Variables** tab → add each one below:

| Variable | Value |
|---|---|
| `ANTHROPIC_API_KEY` | Your Claude API key |
| `SMTP_HOST` | `smtp.gmail.com` |
| `SMTP_PORT` | `587` |
| `SMTP_USER` | `you@gmail.com` |
| `SMTP_PASS` | Your 16-char Gmail app password |
| `EMAIL_RECIPIENTS` | `you@gmail.com,colleague@example.com` |
| `TELEGRAM_BOT_TOKEN` | Your bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | Your group chat ID (negative number) |
| `DIGEST_SCHEDULE` | `weekly` |
| `DIGEST_DAY` | `monday` |
| `MIN_RELEVANCE_SCORE` | `6` |
| `LOOKBACK_HOURS` | `8` |

> `DATABASE_URL` is set automatically by Railway — don't add it manually.

---

## Step 7 — Set the cron schedule

1. Click your **policy-agent service** → **Settings**
2. Scroll to **Cron Schedule** → enter: `0 */6 * * *`
   _(runs every 6 hours)_
3. Click **Save**

---

## Step 8 — Test it

1. Click your service → **Deployments** → **Trigger Deployment**
2. Click the deployment to watch live logs
3. You should see:

```
─── Policy Trend Agent ───
Config: environment variables
Database: Postgres

1. Collecting
  Fetching RSS: Ofcom News...  → 3 items
  Fetching RSS: IMDA News...   → 1 item
  ...

2. Analysing
  [1/8] Online Safety Act guidance published...
    ✓ NOTABLE | score 8 | online_safety
  ...

3. Storing
  Inserted: 7 | Skipped (duplicate): 0

4. Delivering
  ✓ Urgent email sent: ...
  ✓ Digest email sent to 2 recipients (7 items)
  ✓ Telegram digest sent

─── Done ───
```

4. Check your inbox and Telegram — first items should arrive within a minute.

---

## You're live 🎉

**What happens now:**
- Every 6 hours: agent runs, collects items, analyses with Claude, stores to Postgres
- Urgent items: emailed + Telegammed immediately
- Every Monday 08:00 UTC: weekly digest with trend synthesis sent to email + Telegram

---

## Costs

| | Cost/month |
|---|---|
| Railway Hobby plan | ~$5 |
| Railway Postgres | ~$5 |
| Anthropic API | ~$2–5 |
| Gmail / Telegram | Free |
| **Total** | **~$12–15** |

Railway free trial gives $5 credit — enough for 1–2 weeks of testing before subscribing.

---

## Running locally (for testing before deploying)

```bash
# Install dependencies
pip install -r requirements.txt

# Copy and fill in config
cp config/config.example.yaml config/config.yaml
# Edit config.yaml — add your API key, Gmail credentials, Telegram token

# Test without sending anything
python scripts/run_agent.py --dry-run

# Live run
python scripts/run_agent.py

# Force send a digest right now
python scripts/run_agent.py --digest

# Test one jurisdiction only
python scripts/run_agent.py --jurisdiction uk --dry-run
```

---

## Troubleshooting

**Email not arriving**
→ Check spam folder first.
→ Test your Gmail app password:
```bash
python -c "
import smtplib
s = smtplib.SMTP('smtp.gmail.com', 587)
s.starttls()
s.login('you@gmail.com', 'your-app-password')
print('OK')
s.quit()
"
```

**Telegram not sending**
→ Make sure the bot is added to the group as **admin**.
→ Verify your chat ID is correct (groups are negative numbers).
→ Test manually:
```bash
curl -s "https://api.telegram.org/botTOKEN/sendMessage" \
  -d chat_id=YOUR_CHAT_ID \
  -d text="Test from policy agent"
```

**"No new items found" every run**
→ A few gov sites occasionally block scrapers. This is normal.
→ RSS sources (UK, EU) are more reliable than scrapers (SG, AU).
→ Check Railway logs for specific source errors.

**Postgres migration error**
→ Make sure you're running `psql` against the Railway Postgres, not a local DB.
→ Re-check the connection string in Railway → Postgres service → Connect tab.

**Want to add/remove sources?**
→ Edit `config/sources.yaml`, commit and push — Railway redeploys automatically.
