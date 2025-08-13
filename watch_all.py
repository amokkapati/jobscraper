import os
import argparse
import requests
import pytz
import smtplib
from datetime import datetime
from email.mime.text import MIMEText
from playwright.sync_api import sync_playwright

# --------------------------
# Config
# --------------------------
UBER_URL = "https://www.uber.com/api/loadMoreJobs?localeCode=en&country=us&city=&department=University&page=1"
MSFT_URL = "https://jobs.careers.microsoft.com/global/en/search?q=software%20engineer&exp=Students%20and%20graduates&l=en_us&pg=1&pgSz=20&o=Recent"

KEYWORDS = ["2026", "software engineer", "graduate"]


# --------------------------
# Email sender
# --------------------------
def send_email(subject, body):
    smtp_host = os.getenv("SMTP_HOST")
    smtp_port = int(os.getenv("SMTP_PORT", "465"))
    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASS")
    to_email = os.getenv("TO_EMAIL")

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = to_email

    with smtplib.SMTP_SSL(smtp_host, smtp_port) as server:
        server.login(smtp_user, smtp_pass)
        server.sendmail(smtp_user, [to_email], msg.as_string())


# --------------------------
# Filters
# --------------------------
def matches_target(title: str) -> bool:
    title_lower = title.lower()
    return any(k in title_lower for k in KEYWORDS)

def matches_msft_test_se(title: str) -> bool:
    return "software engineering" in title.lower()


# --------------------------
# Fetch Uber Jobs
# --------------------------
def fetch_uber_jobs():
    r = requests.get(UBER_URL, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    data = r.json()
    jobs = []
    for j in data.get("jobs", []):
        jobs.append({
            "title": j["title"],
            "url": f"https://www.uber.com/global/en/careers/list/{j['jobId']}/",
        })
    return jobs


# --------------------------
# Fetch Microsoft Jobs
# --------------------------
def fetch_msft_jobs():
    jobs = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(MSFT_URL, timeout=60000)
        page.wait_for_selector("li.jobs-list-item", timeout=60000)
        items = page.query_selector_all("li.jobs-list-item")
        for item in items:
            title_el = item.query_selector("h3")
            link_el = item.query_selector("a")
            if title_el and link_el:
                title = title_el.inner_text().strip()
                url = link_el.get_attribute("href")
                if url and not url.startswith("http"):
                    url = "https://jobs.careers.microsoft.com" + url
                jobs.append({"title": title, "url": url})
        browser.close()
    return jobs


# --------------------------
# Main
# --------------------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--test-email", action="store_true", help="Send test email only")
    parser.add_argument("--ignore-window", action="store_true", help="Ignore 5am-11pm PT window")
    parser.add_argument("--send-all-now", action="store_true", help="Send all matches now, not just new ones")
    parser.add_argument("--msft-test-se", action="store_true", help="(MSFT only) match any title containing 'software engineering'")
    args = parser.parse_args()

    if args.test_email:
        send_email("[Job Watch] TEST", "This is a test alert from your job watcher. SMTP is wired.")
        print("Test email sent.")
        return

    # Time window check (5:00 AM – 11:00 PM PT)
    if not args.ignore_window:
        pt = pytz.timezone("US/Pacific")
        now = datetime.now(pt)
        if not (5 <= now.hour < 23):
            print(f"Outside active hours (PT): {now.strftime('%H:%M')}")
            return

    uber_jobs = fetch_uber_jobs()
    msft_jobs = fetch_msft_jobs()

    # Filtering
    uber_matches = [j for j in uber_jobs if matches_target(j["title"])]
    if args.msft_test_se:
        msft_matches = [j for j in msft_jobs if matches_msft_test_se(j["title"])]
    else:
        msft_matches = [j for j in msft_jobs if matches_target(j["title"])]

    # Skip if nothing new
    if not uber_matches and not msft_matches:
        print("No matches this run.")
        return

    # Build email
    lines = []
    if uber_matches:
        lines.append("Uber Jobs:")
        lines.extend([f"- {j['title']}\n  {j['url']}" for j in uber_matches])
        lines.append("")
    if msft_matches:
        header = "Microsoft Jobs (test: 'software engineering')" if args.msft_test_se else "Microsoft Jobs:"
        lines.append(header)
        lines.extend([f"- {j['title']}\n  {j['url']}" for j in msft_matches])
        lines.append("")

    body = "\n".join(lines)
    send_email("[Job Watch] New Matches Found", body)
    print("Alert email sent.")


if __name__ == "__main__":
    main()
