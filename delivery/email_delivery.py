# delivery/email_delivery.py
"""
HTML email delivery — urgent alerts + digest.
Works with Gmail (app password), Outlook, or any SMTP provider.
"""

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
import sqlite3

JURISDICTION_FLAG = {
    "sg": "🇸🇬", "au": "🇦🇺", "uk": "🇬🇧",
    "eu": "🇪🇺", "asean": "🌏", "global": "🌐",
}
URGENCY_COLOR   = {"urgent": "#dc2626", "notable": "#d97706", "monitoring": "#6b7280"}
DOMAIN_LABEL    = {"online_safety": "Online Safety", "ai_safety": "AI Safety",
                   "tech_governance": "Tech Governance", "other": "Other"}


def _smtp_send(smtp_config: dict, recipients: list[str], msg: MIMEMultipart):
    host = smtp_config["smtp_host"]
    port = int(smtp_config.get("smtp_port", 587))
    user = smtp_config["smtp_user"]
    pwd  = smtp_config["smtp_pass"]
    try:
        with smtplib.SMTP(host, port) as server:
            server.ehlo()
            server.starttls()
            server.login(user, pwd)
            server.sendmail(user, recipients, msg.as_string())
        return True
    except Exception as e:
        print(f"  ✗ Email send failed: {e}")
        return False


def _item_html(item: sqlite3.Row) -> str:
    flag   = JURISDICTION_FLAG.get(item["jurisdiction"], "🌐")
    color  = URGENCY_COLOR.get(item["urgency"], "#6b7280")
    domain = DOMAIN_LABEL.get(item["domain"], item["domain"])
    impl   = f'<p style="margin:6px 0 0;font-size:13px;color:#6b7280;font-style:italic;">⚡ {item["implications"]}</p>' if item["implications"] else ""
    return f"""
    <tr>
      <td style="padding:14px 0;border-bottom:1px solid #e5e7eb;">
        <div style="margin-bottom:5px;display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
          <span style="font-size:10px;font-weight:700;text-transform:uppercase;color:{color};
                background:{color}18;padding:2px 8px;border-radius:99px;letter-spacing:.5px;">
            {item['urgency']}
          </span>
          <span style="font-size:12px;color:#6b7280;">{flag} {item['jurisdiction'].upper()} &middot; {domain} &middot; {item['relevance_score']}/10</span>
        </div>
        <a href="{item['url']}" style="font-size:15px;font-weight:600;color:#1d4ed8;text-decoration:none;line-height:1.35;">
          {item['title']}
        </a>
        <p style="margin:6px 0 0;font-size:14px;color:#374151;line-height:1.6;">{item['summary'] or ''}</p>
        {impl}
      </td>
    </tr>"""


def _build_digest_html(items: list[sqlite3.Row], synthesis: str, period_label: str) -> str:
    now     = datetime.now().strftime("%d %b %Y")
    urgent  = [i for i in items if i["urgency"] == "urgent"]
    notable = [i for i in items if i["urgency"] == "notable"]
    monitor = [i for i in items if i["urgency"] == "monitoring"]

    def section(label, group):
        if not group:
            return ""
        rows = "".join(_item_html(i) for i in group)
        return f'<h2 style="font-size:16px;margin:28px 0 10px;color:#111827;border-left:3px solid #e5e7eb;padding-left:10px;">{label} <span style="font-weight:400;color:#9ca3af;">({len(group)})</span></h2><table width="100%" cellpadding="0" cellspacing="0">{rows}</table>'

    synthesis_block = ""
    if synthesis:
        body = synthesis.replace("## ", "<strong>").replace("\n## ", "</strong><br><strong>")
        synthesis_block = f'''
        <div style="background:#f8f7ff;border-left:3px solid #4f46e5;padding:18px 20px;margin:20px 0;border-radius:0 6px 6px 0;">
          <div style="font-size:11px;font-weight:700;letter-spacing:1.5px;text-transform:uppercase;color:#4f46e5;margin-bottom:10px;">Trend Synthesis</div>
          <div style="font-size:14px;line-height:1.75;color:#1f2937;white-space:pre-wrap;">{synthesis}</div>
        </div>'''

    jur_counts = {}
    for i in items:
        jur_counts[i["jurisdiction"].upper()] = jur_counts.get(i["jurisdiction"].upper(), 0) + 1
    stats = " &middot; ".join(f"{j}: {c}" for j, c in sorted(jur_counts.items()))

    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f3f4f6;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f6;padding:32px 16px;">
    <tr><td align="center">
      <table width="100%" cellpadding="0" cellspacing="0" style="max-width:660px;background:#ffffff;border-radius:8px;overflow:hidden;border:1px solid #e5e7eb;">

        <!-- Header -->
        <tr><td style="background:#0f172a;padding:24px 32px;">
          <div style="font-size:11px;letter-spacing:2px;text-transform:uppercase;color:#94a3b8;margin-bottom:6px;">Policy Intelligence</div>
          <div style="font-size:22px;font-weight:700;color:#f8fafc;">
            {period_label} Digest &mdash; {now}
          </div>
        </td></tr>

        <!-- Body -->
        <tr><td style="padding:24px 32px 32px;">
          {synthesis_block}
          {section("🔴 Urgent", urgent)}
          {section("🟡 Notable", notable)}
          {section("⚪ Monitoring", monitor)}
        </td></tr>

        <!-- Footer -->
        <tr><td style="padding:16px 32px;border-top:1px solid #e5e7eb;background:#f9fafb;">
          <p style="margin:0;font-size:12px;color:#9ca3af;">
            {len(items)} items &middot; {stats} &middot; Policy Trend Agent
          </p>
        </td></tr>

      </table>
    </td></tr>
  </table>
</body></html>"""


def _build_alert_html(item: sqlite3.Row) -> str:
    flag   = JURISDICTION_FLAG.get(item["jurisdiction"], "🌐")
    color  = URGENCY_COLOR["urgent"]
    domain = DOMAIN_LABEL.get(item["domain"], item["domain"])
    impl   = f'<p style="margin:12px 0 0;font-size:13px;color:#6b7280;font-style:italic;border-left:2px solid #e5e7eb;padding-left:10px;">⚡ {item["implications"]}</p>' if item["implications"] else ""
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#f3f4f6;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f6;padding:32px 16px;">
    <tr><td align="center">
      <table width="100%" cellpadding="0" cellspacing="0" style="max-width:580px;background:#fff;border-radius:8px;border:1px solid #e5e7eb;overflow:hidden;">
        <tr><td style="background:#7f1d1d;padding:16px 24px;">
          <span style="font-size:11px;letter-spacing:2px;text-transform:uppercase;color:#fca5a5;font-weight:700;">🔴 Urgent Alert</span>
        </td></tr>
        <tr><td style="padding:24px;">
          <div style="font-size:12px;color:#6b7280;margin-bottom:8px;">{flag} {item['jurisdiction'].upper()} &middot; {domain} &middot; Score {item['relevance_score']}/10</div>
          <a href="{item['url']}" style="font-size:17px;font-weight:700;color:#1d4ed8;text-decoration:none;line-height:1.35;">{item['title']}</a>
          <p style="margin:12px 0 0;font-size:14px;color:#374151;line-height:1.6;">{item['summary'] or ''}</p>
          {impl}
          <div style="margin-top:20px;">
            <a href="{item['url']}" style="display:inline-block;background:#1d4ed8;color:#fff;font-size:13px;font-weight:600;padding:10px 20px;border-radius:5px;text-decoration:none;">
              Read full item →
            </a>
          </div>
        </td></tr>
        <tr><td style="padding:12px 24px;border-top:1px solid #e5e7eb;background:#f9fafb;">
          <p style="margin:0;font-size:11px;color:#9ca3af;">Policy Trend Agent</p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body></html>"""


def send_urgent_alert(smtp_config: dict, recipients: list[str], item: sqlite3.Row):
    """Send a single urgent item as an immediate email alert."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🔴 Urgent Policy Alert — {item['jurisdiction'].upper()}: {item['title'][:60]}"
    msg["From"]    = f"Policy Agent <{smtp_config['smtp_user']}>"
    msg["To"]      = ", ".join(recipients)
    msg.attach(MIMEText(_build_alert_html(item), "html"))

    ok = _smtp_send(smtp_config, recipients, msg)
    if ok:
        print(f"  ✓ Urgent email sent: {item['title'][:60]}")


def send_digest(
    smtp_config: dict,
    recipients: list[str],
    items: list[sqlite3.Row],
    synthesis: str,
    period_label: str = "Weekly",
):
    """Send the full digest email."""
    if not items:
        print("  No items for digest — skipping email.")
        return

    now = datetime.now().strftime("%d %b %Y")
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"📋 Policy Trends {period_label} Digest — {now}"
    msg["From"]    = f"Policy Agent <{smtp_config['smtp_user']}>"
    msg["To"]      = ", ".join(recipients)
    msg.attach(MIMEText(_build_digest_html(items, synthesis, period_label), "html"))

    ok = _smtp_send(smtp_config, recipients, msg)
    if ok:
        print(f"  ✓ Digest email sent to {len(recipients)} recipients ({len(items)} items)")
