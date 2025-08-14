import os, json, smtplib, argparse, ssl, sys, urllib.parse
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo
from pathlib import Path

# ------------ config ------------
TZ = ZoneInfo("America/Los_Angeles")

# Uber: university roles page; we’ll expand via “Load more”
UBER_URL = "https://www.uber.com/us/en/careers/list/?department=University"

# Microsoft: “Recent” ordering. We’ll page with ?pg=1..N
MSFT_BASE = (
    "https://jobs.careers.microsoft.com/global/en/search"
    "?q=software%20engineer&exp=Students%20and%20graduates"
    "&l=en_us&pg=1&pgSz=20&o=Recent&flt=true"
)

SEEN_UBER = Path("seen_uber.json")
SEEN_MSFT = Path("seen_msft.json")

# ------------ helpers ------------
def in_allowed_window(now_pt: datetime) -> bool:
    return dtime(5, 0) <= now_pt.time() <= dtime(23, 0)  # 5:00–23:00 PT

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

def matches_msft_test_se(title: str) -> bool:
    return "software engineering" in title.lower()

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

# ------------ scraping (lazy Playwright) ------------
def fetch_uber_with_playwright() -> list[dict]:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout  # lazy import
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0 Safari/537.36")
        )
        page = ctx.new_page()
        page.goto(UBER_URL, wait_until="domcontentloaded", timeout=60_000)

        # wait for listings
        for sel in ["a[href*='/careers/list/']",
                    "[data-baseweb='link'] a[href*='/careers/list/']"]:
            try:
                page.wait_for_selector(sel, timeout=10_000)
                break
            except PWTimeout:
                continue

        # try to expand a bit
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
            if href in seen_urls:
                continue
            seen_urls.add(href)
            jobs.append({"title": title, "url": href})

        browser.close()
        return jobs

def _msft_page_url(page_num: int) -> str:
    parsed = urllib.parse.urlparse(MSFT_BASE)
    qs = dict(urllib.parse.parse_qsl(parsed.query))
    qs["pg"] = str(page_num)
    new_query = urllib.parse.urlencode(qs, doseq=True)
    return urllib.parse.urlunparse(parsed._replace(query=new_query))

def fetch_msft_with_playwright(pages: int = 1) -> list[dict]:
    """Fetch Microsoft results for given number of pages (Recent order)."""
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout  # lazy import
    jobs, seen_urls = [], set()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0 Safari/537.36")
        )
        page = ctx.new_page()
        for pg in range(1, max(1, pages) + 1):
            url = _msft_page_url(pg)
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)

            # permissive selectors
            selectors = [
                "a[href*='/global/en/job/']",
                "[data-bi-name='job-title'] a[href*='/global/en/job/']",
                "a.ms-job-card",
            ]
            hydrated = False
            for sel in selectors:
                try:
                    page.wait_for_selector(sel, timeout=10_000)
                    hydrated = True
                    break
                except PWTimeout:
                    continue
            if not hydrated:
                continue

            # grab all visible job links on this page
            anchors = page.locator("a[href*='/global/en/job/']")
            count = anchors.count()
            for i in range(count):
                a = anchors.nth(i)
                href = (a.get_attribute("href") or "").strip()
                title = (a.inner_text() or "").strip()
                if not href or not title:
                    continue
                if href.startswith("/"):
                    href = "https://jobs.careers.microsoft.com" + href
                # normalize (drop query)
                try:
                    parsed = urllib.parse.urlparse(href)
                    href = urllib.parse.urlunparse(parsed._replace(query=""))
                except Exception:
                    pass
                if href in seen_urls:
                    continue
                seen_urls.add(href)
                jobs.append({"title": title, "url": href})

        browser.close()
    return jobs

# ------------ main ------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--test-email", action="store_true", help="Send a test email and exit.")
    parser.add_argument("--ignore-window", action="store_true", help="Run regardless of 5:00–23:00 PT window.")
    parser.add_argument("--send-all-now", action="store_true", help="Email ALL current matches once (ignores de-dupe).")
    parser.add_argument("--msft-test-se", action="store_true",
                        help="(MSFT only) match any title containing 'software engineering' (case-insensitive).")
    parser.add_argument("--msft-pages", type=int, default=1,
                        help="How many Microsoft pages to fetch (default: 1).")
    args = parser.parse_args()

    # Fast SMTP test path (no Playwright)
    if args.test_email:
        send_email("[Job Watch] TEST", "SMTP works. Watching Uber + Microsoft.")
        print("Test email sent.")
        return

    now_pt = datetime.now(TZ)
    if not args.ignore_window and not in_allowed_window(now_pt):
        print(f"Outside window (PT): {now_pt}. Skipping.")
        return

    # scrape
    uber_jobs = fetch_uber_with_playwright()
    print(f"[Uber] fetched {len(uber_jobs)} jobs.")
    msft_jobs = fetch_msft_with_playwright(pages=args.msft_pages)
    print(f"[MSFT] fetched {len(msft_jobs)} jobs across {args.msft_pages} page(s).")

    # filter
    uber_matches = [j for j in uber_jobs if matches_target(j["title"])]
    if args.msft_test_se:
        msft_matches = [j for j in msft_jobs if matches_msft_test_se(j["title"])]
    else:
        msft_matches = [j for j in msft_jobs if matches_target(j["title"])]

    # dedupe per site
    seen_uber = load_seen(SEEN_UBER)
    seen_msft = load_seen(SEEN_MSFT)

    if args.send_all_now:
        send_uber = uber_matches
        send_msft = msft_matches
    else:
        send_uber = [j for j in uber_matches if j["url"] not in seen_uber]
        send_msft = [j for j in msft_matches if j["url"] not in seen_msft]

    if not send_uber and not send_msft:
        print("No new matches this run.")
        return

    # email body
    lines = []
    if send_uber:
        lines.append("Uber Jobs:")
        lines.extend([f"- {j['title']}\n  {j['url']}" for j in send_uber])
        lines.append("")
    if send_msft:
        header = "Microsoft Jobs (test: 'software engineering')" if args.msft_test_se else "Microsoft Jobs:"
        lines.append(header)
        lines.extend([f"- {j['title']}\n  {j['url']}" for j in send_msft])
        lines.append("")
    body = "\n".join(lines).rstrip()

    subject = f"[Job Watch] {len(send_uber)+len(send_msft)} new match(es) (Uber: {len(send_uber)}, Microsoft: {len(send_msft)})"
    send_email(subject, body)
    print(f"Emailed {len(send_uber)} Uber + {len(send_msft)} Microsoft new match(es).")

    # persist seen
    for j in send_uber:
        seen_uber.add(j["url"])
    for j in send_msft:
        seen_msft.add(j["url"])
    save_seen(SEEN_UBER, seen_uber)
    save_seen(SEEN_MSFT, seen_msft)

if __name__ == "__main__":
    main()
