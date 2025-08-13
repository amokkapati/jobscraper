import os
import json
import requests
from datetime import datetime
import smtplib
from email.mime.text import MIMEText
import pytz

UBER_API = "https://www.uber.com/api/loadMoreJobs?localeCode=en&country=us&city=&department=University&page=1"
SEEN_FILE = "seen.json"

def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r") as f:
            return set(json.load(f))
    return set()

def save_seen(seen):
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen), f)

def fetch_jobs():
    headers = {
        "User-Agent": "Mozilla/5.0"
    }
    r = requests.get(UBER_API, headers=headers)
    r.raise_for_status()
    data = r.json()
    jobs = []
    for job in data.get("jobs", []):
        title = job.get("title", "").strip()
        url = "https://www.uber.com" + job.get("absolute_url", "")
        jobs.append((title, url))
    return jobs

def matches_target(title: str) -> bool:
    t = title.lower()
    has_2026 = "2026" in t

    # SWE-ish keywords
    has_swe = (
        ("software engineer" in t) or
        ("software engineering" in t) or
        (" swe " in f" {t} ")
    )

    # Graduate path (doesn't have to be SWE)
    has_graduate = "graduate" in t

    return has_2026 and (has_swe or has_graduate)

def send_email(subject, body):
    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", "465"))
    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASS")
    to_email = os.getenv("TO_EMAIL")

    if not smtp_user or not smtp_pass or not to_email:
        raise RuntimeError("SMTP_USER, SMTP_PASS, TO_EMAIL must be set")

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = to_email

    with smtplib.SMTP_SSL(smtp_host, smtp_port) as server:
        server.login(smtp_user, smtp_pass)
        server.sendmail(smtp_user, [to_email], msg.as_string())

def within_time_window():
    tz = pytz.timezone("America/Los_Angeles")
    now = datetime.now(tz)
    return 5 <= now.hour < 23

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--test-email", action="store_true", help="Send test email and exit")
    parser.add_argument("--ignore-window", action="store_true", help="Ignore time window")
    parser.add_argument("--send-all-now", action="store_true", help="Send all matches regardless of seen list")
    args = parser.parse_args()

    if args.test_email:
        send_email("[Uber Watch] TEST", "This is a test alert from your Uber watcher. SMTP is wired.")
        print("Sent test email.")
        if not args.send_all_now:
            return

    if not args.ignore_window and not within_time_window():
        print("Outside time window. Skipping.")
        return

    jobs = fetch_jobs()
    seen = load_seen()

    matches = []
    for title, url in jobs:
        if matches_target(title):
            if args.send_all_now or url not in seen:
                matches.append((title, url))
            seen.add(url)

    save_seen(seen)

    if matches:
        body = "\n".join(f"{title}\n{url}" for title, url in matches)
        send_email("[Uber Watch] New Matches", body)
        print(f"Sent email for {len(matches)} matches.")
    else:
        print("No matches this run.")

if __name__ == "__main__":
    main()
