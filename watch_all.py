import os, json, smtplib, argparse, ssl, sys, urllib.parse
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo
from pathlib import Path

# ------------ config ------------
TZ = ZoneInfo("America/Los_Angeles")
UBER_URL = "https://www.uber.com/us/en/careers/list/?department=University"
SEEN_UBER = Path("seen_uber.json")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")

# ------------ helpers ------------
def in_allowed_window(now_pt: datetime) -> bool:
    return dtime(5, 0) <= now_pt.time() <= dtime(23, 0)  # 5:00–23:00 PT

def normalize_url(url: str) -> str:
    """Drop query + fragment so the same job doesn’t appear twice."""
    try:
        parsed = urllib.parse.urlparse(url)
        return urllib.parse.urlunparse(parsed._replace(query="", fragment=""))
    except Exception:
        return url.strip()

def load_seen(path: Path):
    if path.exists():
        try:
            return set(json.loads(path.read_text()))
        except Exception:
            return set()
    return set()

def save_seen(path: Path, seen_set):
    try:
        path.write_text(json.dumps(sorted(seen_set)))
    except Exception as e:
        print(f"Warn: failed to save {path.name}: {e}", file=sys.stderr)

def matches_target(title: str) -> bool:
    t = title.lower()
    has_2026 = "2026" in t
    has_sweish = ("software engineer" in t) or ("software engineering" in t) or (" swe " in f" {t} ")
    has_graduate = "graduate" in t
    return has_2026 and (has_sweish or has_graduate)

def send_email(subject: str, body: str):
    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT") or "465")
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

# ------------ scraping (Uber) ------------
def fetch_uber_with_playwright() -> list[dict]:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(user_agent=UA)
        page = ctx.new_page()
        page.goto(UBER_URL, wait_until="domcontentloaded", timeout=60_000)

        # wait for listings
        try:
            page.wait_for_selector("a[href*='/careers/list/']", timeout=10_000)
        except PWTimeout:
            return []

        # scroll / load more
        for _ in range(16):
            before = page.locator("a[href*='/careers/list/']").count()
            page.keyboard.press("End")
            page.wait_for_timeout(500)
            load_more = page.locator("button:has-text('Load more')")
            if load_more.count() > 0:
                try:
                    load_more.first.click(timeout=2000)
                    page.wait_for_timeout(900)
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
            href = normalize_url(href)
            if href in seen_urls:
                continue
            seen_urls.add(href)
            jobs.append({"title": title, "url": href})

        browser.close()
        return jobs

# ------------ main ------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--test-email", action="store_true")
    parser.add_argument("--ignore-window", action="store_true")
    parser.add_argument("--send-all-now", action="store_true")
    args = parser.parse_args()

    if args.test_email:
        send_email("[Job Watch] TEST", "SMTP works. Watching Uber only.")
        print("Test email sent.")
        return

    now_pt = datetime.now(TZ)
    if not args.ignore_window and not in_allowed_window(now_pt):
        print(f"Outside window (PT): {now_pt}. Skipping.")
        return

    # scrape + filter
    uber_jobs = fetch_uber_with_playwright()
    print(f"[Uber] fetched {len(uber_jobs)} jobs.")
    uber_matches = [j for j in uber_jobs if matches_target(j["title"])]

    # dedupe with seen.json
    seen_uber = load_seen(SEEN_UBER)
    if args.send_all_now:
        send_uber = uber_matches
    else:
        send_uber = [j for j in uber_matches if j["url"] not in seen_uber]

    if not send_uber:
        print("No new matches this run.")
        return

    # email body
    lines = ["Uber Jobs:"]
    lines.extend([f"- {j['title']}\n  {j['url']}" for j in send_uber])
    body = "\n".join(lines).rstrip()

    subject = f"[Job Watch] {len(send_uber)} new Uber match(es)"
    send_email(subject, body)
    print(f"Emailed {len(send_uber)} Uber new match(es).")

    # persist seen
    for j in send_uber:
        seen_uber.add(j["url"])
    save_seen(SEEN_UBER, seen_uber)

if __name__ == "__main__":
    main()
