import os, sys, json, ssl, smtplib, argparse
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

CAREERS_URL = "https://www.uber.com/us/en/careers/list/?department=University"
TZ = ZoneInfo("America/Los_Angeles")
SEEN_PATH = Path("seen.json")

def in_allowed_window(now_pt: datetime) -> bool:
    start = dtime(5, 0)   # 5:00 AM PT
    end   = dtime(23, 0)  # 11:00 PM PT
    return start <= now_pt.time() <= end

def load_seen():
    if SEEN_PATH.exists():
        try:
            return set(json.loads(SEEN_PATH.read_text()))
        except Exception:
            return set()
    return set()

def save_seen(seen_set):
    try:
        SEEN_PATH.write_text(json.dumps(sorted(seen_set)))
    except Exception as e:
        print(f"Warn: failed to save seen.json: {e}", file=sys.stderr)

def fetch_roles():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ))
        page = ctx.new_page()
        page.goto(CAREERS_URL, wait_until="domcontentloaded", timeout=60_000)

        # Wait for listings to render (be flexible on selectors)
        candidates = [
            "a[href*='/careers/list/']",
            "[data-baseweb='link'] a[href*='/careers/list/']",
        ]
        for sel in candidates:
            try:
                page.wait_for_selector(sel, timeout=15_000)
                break
            except PWTimeout:
                continue
        page.wait_for_timeout(1200)

        # Try to load more results (scroll and click "Load more" if present)
        for _ in range(20):
            before = page.locator("a[href*='/careers/list/']").count()
            page.keyboard.press("End")
            page.wait_for_timeout(800)
            load_more = page.locator("button:has-text('Load more')")
            if load_more.count() > 0:
                try:
                    load_more.first.click(timeout=3000)
                    page.wait_for_timeout(1500)
                except Exception:
                    pass
            after = page.locator("a[href*='/careers/list/']").count()
            if after <= before:
                break

        anchors = page.locator("a[href*='/careers/list/']").all()
        jobs, seen_urls = [], set()
        for a in anchors:
            href = a.get_attribute("href") or ""
            title = (a.inner_text() or "").strip()
            if not title or "/careers/list/" not in href:
                continue
            if href.startswith("/"):
                href = "https://www.uber.com" + href
            if href in seen_urls:
                continue
            seen_urls.add(href)
            jobs.append({"title": title, "url": href})

        browser.close()
        return jobs

def matches_target(title: str) -> bool:
    t = title.lower()
    # Match "software engineer" or "swe" token; avoid false positives like "newsweet"
    return ("software engineer" in t) or (" swe " in f" {t} ")

def filter_targets(jobs):
    return [j for j in jobs if matches_target(j["title"])]

def send_email(subject: str, body: str):
    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", "465"))
    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASS")
    to_email  = os.getenv("TO_EMAIL")

    if not (smtp_user and smtp_pass and to_email):
        raise RuntimeError("Missing SMTP_USER / SMTP_PASS / TO_EMAIL env vars.")

    msg = MIMEMultipart()
    msg["From"] = smtp_user
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    with smtplib.SMTP_SSL(smtp_host, smtp_port, context=ssl.create_default_context()) as server:
        server.login(smtp_user, smtp_pass)
        server.sendmail(smtp_user, [to_email], msg.as_string())

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--test-email", action="store_true", help="Send a test alert without scraping.")
    parser.add_argument("--ignore-window", action="store_true", help="Run regardless of PT time window.")
    args = parser.parse_args()

    now_pt = datetime.now(TZ)
    if not args.ignore_window and not in_allowed_window(now_pt):
        print(f"Outside window (PT): {now_pt}. Skipping.")
        return

    if args.test_email:
        send_email("[Uber Watch] TEST", "This is a test alert from your Uber watcher. SMTP is wired.")
        print("Sent test email.")
        return

    jobs = fetch_roles()
    print(f"Fetched {len(jobs)} jobs.")

    matches = filter_targets(jobs)
    if not matches:
        print("No matches this run.")
        return

    # De-dup against seen.json — alert only on new URLs
    seen = load_seen()
    new_matches = [m for m in matches if m["url"] not in seen]
    if not new_matches:
        print("Matches exist but all were already seen. No email sent.")
        return

    subject = f"[Uber Watch] {len(new_matches)} new SWE job(s)"
    body = "Found the following new SWE roles:\n\n" + "\n\n".join(
        f"- {m['title']}\n  {m['url']}" for m in new_matches
    )
    send_email(subject, body)
    print(f"Emailed {len(new_matches)} new match(es).")

    for m in new_matches:
        seen.add(m["url"])
    save_seen(seen)

if __name__ == "__main__":
    main()
