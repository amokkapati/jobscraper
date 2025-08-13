import os, json, smtplib, argparse, ssl, sys
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

CAREERS_URL = "https://www.uber.com/us/en/careers/list/?department=University"
TZ = ZoneInfo("America/Los_Angeles")
SEEN_PATH = Path("seen_jobs.json")  # remembers URLs to avoid repeat alerts

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
        print(f"Warn: failed to save seen file: {e}", file=sys.stderr)

def fetch_roles():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0 Safari/537.36")
        )
        page = ctx.new_page()
        page.goto(CAREERS_URL, wait_until="domcontentloaded", timeout=60_000)

        # Wait for listings to render (be flexible)
        selectors = [
            "a[href*='/careers/list/']",
            "[data-baseweb='link'] a[href*='/careers/list/']",
        ]
        hydrated = False
        for sel in selectors:
            try:
                page.wait_for_selector(sel, timeout=15_000)
                hydrated = True
                break
            except PWTimeout:
                continue
        if not hydrated:
            page.wait_for_timeout(1500)

        # Scroll / click “Load more”
        for _ in range(24):
            before = page.locator("a[href*='/careers/list/']").count()
            page.keyboard.press("End")
            page.wait_for_timeout(700)
            load_more = page.locator("button:has-text('Load more')")
            if load_more.count() > 0:
                try:
                    load_more.first.click(timeout=3000)
                    page.wait_for_timeout(1200)
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
    has_2026 = "2026" in t
    has_sweish = (
        ("software engineer" in t) or
        ("software engineering" in t) or
        (" swe " in f" {t} ")
    )
    has_graduate = "graduate" in t
    return has_2026 and (has_sweish or has_graduate)

def filter_targets(jobs):
    return [j for j in jobs if matches_target(j["title"])]

def send_email(subject: str, body: str):
    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT") or "465")  # default if blank
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
    parser.add_argument("--test-email", action="store_true", help="Send a test alert (SMTP only).")
    parser.add_argument("--ignore-window", action="store_true", help="Run regardless of 5:00–23:00 PT window.")
    parser.add_argument("--send-all-now", action="store_true", help="Email ALL current matches once (ignores de-dupe).")
    args = parser.parse_args()

    if args.test_email:
        send_email("[Uber Watch] TEST", "SMTP works. Monitoring for 2026 + (SWE or Graduate).")
        print("Sent test email.")

    now_pt = datetime.now(TZ)
    if not args.ignore_window and not in_allowed_window(now_pt):
        print(f"Outside window (PT): {now_pt}. Skipping.")
        return

    jobs = fetch_roles()
    print(f"Fetched {len(jobs)} jobs.")
    matches = filter_targets(jobs)

    if args.send_all_now:
        if matches:
            subject = f"[Uber Watch] {len(matches)} match(es) (one-time dump)"
            body = "All current 2026 SWE/Graduate roles:\n\n" + "\n\n".join(
                f"- {m['title']}\n  {m['url']}" for m in matches
            )
            send_email(subject, body)
            print(f"Emailed {len(matches)} (one-time dump).")
        else:
            print("No 2026 SWE/Graduate roles to dump.")
        return

    if not matches:
        print("No matches this run.")
        return

    seen = load_seen()
    new_matches = [m for m in matches if m["url"] not in seen]
    if not new_matches:
        print("Matches exist but all were already seen. No email sent.")
        return

    subject = f"[Uber Watch] {len(new_matches)} new 2026 SWE/Graduate role(s)"
    body = "New roles:\n\n" + "\n\n".join(f"- {m['title']}\n  {m['url']}" for m in new_matches)
    send_email(subject, body)
    print(f"Emailed {len(new_matches)} new match(es).")

    for m in new_matches:
        seen.add(m["url"])
    save_seen(seen)

if __name__ == "__main__":
    main()
