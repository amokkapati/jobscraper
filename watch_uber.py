import os, sys, smtplib, ssl, time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright

CAREERS_URL = "https://www.uber.com/us/en/careers/list/?department=University"
TZ = ZoneInfo("America/Los_Angeles")

def in_allowed_window(now_pt: datetime) -> bool:
    start = dtime(5, 0)   # 5:00 AM PT
    end   = dtime(23, 0)  # 11:00 PM PT
    return start <= now_pt.time() <= end

def fetch_roles():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ))
        page = ctx.new_page()
        page.goto(CAREERS_URL, wait_until="load", timeout=60_000)
        # Wait for job search UI to hydrate. Fallback to a short sleep if needed.
        page.wait_for_timeout(5000)
        # Grab all job links on the list page (Uber uses /careers/list/<id>/ pages)
        anchors = page.locator("a[href*='/careers/list/']").all()
        jobs = []
        seen = set()
        for a in anchors:
            href = a.get_attribute("href") or ""
            title = (a.inner_text() or "").strip()
            if not title or "/careers/list/" not in href:
                continue
            # Normalize href to absolute
            if href.startswith("/"):
                href = "https://www.uber.com" + href
            # Dedup by href
            if href in seen:
                continue
            seen.add(href)
            jobs.append({"title": title, "url": href})
        browser.close()
        return jobs

def filter_targets(jobs):
    hits = []
    for j in jobs:
        t = j["title"].lower()
        if "software engineer" in t and "2026" in t:
            hits.append(j)
    return hits

def send_email(matches):
    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", "465"))
    smtp_user = os.getenv("SMTP_USER")  # e.g. your Gmail address
    smtp_pass = os.getenv("SMTP_PASS")  # app password if Gmail
    to_email  = os.getenv("TO_EMAIL")   # where to send alerts

    if not (smtp_user and smtp_pass and to_email):
        print("Missing SMTP_USER / SMTP_PASS / TO_EMAIL env vars.", file=sys.stderr)
        return

    subject = f"[Uber Watch] {len(matches)} match(es) for 2026 + Software Engineer"
    body_lines = []
    for m in matches:
        body_lines.append(f"- {m['title']}\n  {m['url']}")
    body = "Found the following roles:\n\n" + "\n\n".join(body_lines)

    msg = MIMEMultipart()
    msg["From"] = smtp_user
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(smtp_host, smtp_port, context=context) as server:
        server.login(smtp_user, smtp_pass)
        server.sendmail(smtp_user, [to_email], msg.as_string())

def main():
    now_pt = datetime.now(TZ)
    if not in_allowed_window(now_pt):
        print(f"Outside window (PT): {now_pt}. Skipping.")
        return

    jobs = fetch_roles()
    matches = filter_targets(jobs)
    if matches:
        send_email(matches)
    else:
        print("No matches this run.")

if __name__ == "__main__":
    main()
