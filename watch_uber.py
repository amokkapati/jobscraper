import os
import re
import json
import smtplib
import argparse
from datetime import datetime
from email.mime.text import MIMEText
import requests

SEEN_FILE = "seen_jobs.json"

def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r") as f:
            return set(json.load(f))
    return set()

def save_seen(seen):
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen), f)

def fetch_jobs():
    url = "https://www.uber.com/api/loadMoreJobs"
    params = {
        "localeCode": "en",
        "country": "us",
        "city": "",
        "department": "University",
        "page": "1"
    }
    r = requests.get(url, params=params)
    r.raise_for_status()
    return r.json().get("jobs", [])

def matches_target(title: str) -> bool:
    t = title.lower()
    return ("software engineer" in t) or (" swe " in f" {t} ")

def filter_targets(jobs):
    return [
        {"id": job["id"], "title": job["title"], "url": job["absolute_url"]}
        for job in jobs if matches_target(job["title"])
    ]

def send_email(subject, body):
    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", "465") or "465")
    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASS")
    to_email = os.getenv("TO_EMAIL")

    if not all([smtp_user, smtp_pass, to_email]):
        raise ValueError("SMTP_USER, SMTP_PASS, and TO_EMAIL must be set as env vars/secrets.")

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = to_email

    with smtplib.SMTP_SSL(smtp_host, smtp_port) as server:
        server.login(smtp_user, smtp_pass)
        server.send_message(msg)

def within_time_window():
    # Time window: 5:00 AM to 11:00 PM PT
    now = datetime.utcnow()
    pt_hour = (now.hour - 7) % 24
    return 5 <= pt_hour < 23

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--test-email", action="store_true", help="Send test email only.")
    parser.add_argument("--ignore-window", action="store_true", help="Ignore time-of-day window.")
    parser.add_argument("--send-all-now", action="store_true", help="Send all SWE jobs now, ignoring de-dupe.")
    args = parser.parse_args()

    if args.test_email:
        send_email("[Uber Watch] TEST", "This is a test alert from your Uber watcher. SMTP is wired.")
        print("Sent test email.")
        if not (args.ignore_window or args.send_all_now):
            return

    if not args.ignore_window and not within_time_window():
        print("Outside time window. Skipping scrape.")
        return

    jobs = fetch_jobs()
    matches = filter_targets(jobs)
    seen = load_seen()

    if args.send_all_now:
        if matches:
            subject = f"[Uber Watch] {len(matches)} SWE job(s) (one-time dump)"
            body = "All current SWE roles:\n\n" + "\n\n".join(
                f"- {m['title']}\n  {m['url']}" for m in matches
            )
            send_email(subject, body)
            print(f"Emailed {len(matches)} SWE job(s) in one-time dump.")
        else:
            print("No SWE jobs found to dump.")
        return

    new_matches = [m for m in matches if m["id"] not in seen]

    if new_matches:
        for m in new_matches:
            seen.add(m["id"])
        save_seen(seen)
        subject = f"[Uber Watch] {len(new_matches)} new SWE job(s)"
        body = "New SWE roles:\n\n" + "\n\n".join(
            f"- {m['title']}\n  {m['url']}" for m in new_matches
        )
        send_email(subject, body)
        print(f"Emailed {len(new_matches)} new SWE job(s).")
    else:
        print("No matches this run.")

if __name__ == "__main__":
    main()
