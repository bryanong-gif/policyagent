# delivery/telegram_delivery.py
"""
Telegram delivery — real-time alerts and digest summaries.

Setup:
  1. Create a bot via @BotFather → get a token
  2. Get your chat_id: send a message to the bot, then:
     curl "https://api.telegram.org/bot<TOKEN>/getUpdates"
  3. Add to config.yaml:
     telegram:
       enabled: true
       bot_token: "123456:ABC-..."
       chat_id: "-1001234567890"   # group/channel ID or personal chat ID
"""

import requests
from datetime import datetime
import sqlite3

JURISDICTION_FLAG = {
    "sg": "🇸🇬", "au": "🇦🇺", "uk": "🇬🇧",
    "eu": "🇪🇺", "asean": "🌏", "global": "🌐",
}
URGENCY_ICON = {"urgent": "🔴", "notable": "🟡", "monitoring": "⚪"}


def _send(bot_token: str, chat_id: str, text: str, parse_mode: str = "HTML"):
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        return True
    except Exception as e:
        print(f"  ✗ Telegram send failed: {e}")
        return False


def send_urgent_alert(bot_token: str, chat_id: str, item: sqlite3.Row):
    """Send a single urgent item alert immediately."""
    flag = JURISDICTION_FLAG.get(item["jurisdiction"], "🌐")
    domain_label = (item["domain"] or "").replace("_", " ").title()

    text = (
        f"🔴 <b>Urgent Policy Alert</b>\n\n"
        f"{flag} <b>{item['jurisdiction'].upper()}</b> · {domain_label}\n\n"
        f"<b><a href='{item['url']}'>{item['title']}</a></b>\n\n"
        f"{item['summary'] or ''}\n\n"
    )
    if item["implications"]:
        text += f"<i>⚡ {item['implications']}</i>"

    print(f"  Sending Telegram alert: {item['title'][:60]}...")
    _send(bot_token, chat_id, text)


def send_digest(
    bot_token: str,
    chat_id: str,
    items: list[sqlite3.Row],
    synthesis: str,
    period_label: str = "Weekly",
):
    if not items:
        return

    now = datetime.now().strftime("%d %b %Y")
    urgent   = [i for i in items if i["urgency"] == "urgent"]
    notable  = [i for i in items if i["urgency"] == "notable"]

    # Message 1: synthesis
    if synthesis:
        intro = (
            f"📋 <b>Policy Trends {period_label} Digest</b> — {now}\n\n"
            f"{synthesis[:3000]}"
        )
        _send(bot_token, chat_id, intro)

    # Message 2: urgent items
    if urgent:
        lines = [f"🔴 <b>Urgent ({len(urgent)} items)</b>\n"]
        for item in urgent[:8]:
            flag = JURISDICTION_FLAG.get(item["jurisdiction"], "🌐")
            lines.append(
                f"{flag} <a href='{item['url']}'>{item['title'][:80]}</a>"
                f"\n<i>{item['summary'][:120] if item['summary'] else ''}</i>\n"
            )
        _send(bot_token, chat_id, "\n".join(lines))

    # Message 3: notable items
    if notable:
        lines = [f"🟡 <b>Notable ({len(notable)} items)</b>\n"]
        for item in notable[:10]:
            flag = JURISDICTION_FLAG.get(item["jurisdiction"], "🌐")
            lines.append(f"{flag} <a href='{item['url']}'>{item['title'][:80]}</a>")
        _send(bot_token, chat_id, "\n".join(lines))

    print(f"  ✓ Telegram digest sent: {len(items)} items across {len([m for m in [synthesis, urgent, notable] if m])} messages")
