#!/usr/bin/env python3
"""
X (Twitter) Account Monitor — Nitter edition

Fetches recent tweets from a list of accounts via Nitter (no API key needed),
stores them locally, and prints a summary of new tweets since the last run.
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import anthropic
import requests
from bs4 import BeautifulSoup

STORE_FILE = Path("tweet_store.json")
ACCOUNTS_FILE = Path("accounts.json")
MAX_RESULTS_FIRST_RUN = 20

# Try instances in order; first one that responds wins
NITTER_INSTANCES = [
    "https://nitter.net",
    "https://nitter.poast.org",
    "https://nitter.privacydev.net",
    "https://nitter.cz",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; twitter-monitor/2.0)"
}


# ---------------------------------------------------------------------------
# Data persistence
# ---------------------------------------------------------------------------

def load_store() -> dict:
    if STORE_FILE.exists():
        with open(STORE_FILE) as f:
            return json.load(f)
    return {"accounts": {}, "last_run": None}


def save_store(store: dict) -> None:
    store["last_run"] = datetime.now(timezone.utc).isoformat()
    with open(STORE_FILE, "w") as f:
        json.dump(store, f, indent=2)


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def load_accounts() -> list[str]:
    if not ACCOUNTS_FILE.exists():
        raise SystemExit(
            f"Error: {ACCOUNTS_FILE} not found.\n"
            'Create accounts.json with a JSON array of usernames, e.g.:\n'
            '  ["nasa", "openai", "github"]'
        )
    with open(ACCOUNTS_FILE) as f:
        accounts = json.load(f)
    if not isinstance(accounts, list) or not accounts:
        raise SystemExit("Error: accounts.json must be a non-empty JSON array of usernames.")
    return [a.lstrip("@") for a in accounts]


# ---------------------------------------------------------------------------
# Nitter scraping
# ---------------------------------------------------------------------------

def pick_instance() -> str | None:
    """Return the first Nitter instance that responds."""
    for base in NITTER_INSTANCES:
        try:
            r = requests.get(base, headers=HEADERS, timeout=8)
            if r.status_code == 200:
                return base
        except requests.RequestException:
            continue
    return None


def parse_tweet_id(tweet_link: str) -> str | None:
    """Extract numeric tweet ID from a path like /username/status/12345."""
    parts = tweet_link.rstrip("/").split("/")
    if "status" in parts:
        idx = parts.index("status")
        if idx + 1 < len(parts):
            candidate = parts[idx + 1].split("#")[0]
            if candidate.isdigit():
                return candidate
    return None


def parse_nitter_date(title: str) -> str:
    """
    Nitter title attribute format: 'Mar 9, 2026 · 3:45 PM UTC'
    Returns ISO format string or the raw title if parsing fails.
    """
    try:
        clean = title.replace(" · ", " ").replace(" UTC", "")
        dt = datetime.strptime(clean, "%b %d, %Y %I:%M %p")
        return dt.replace(tzinfo=timezone.utc).isoformat()
    except ValueError:
        return title


def fetch_tweets(
    base_url: str,
    username: str,
    since_id: str | None,
    is_first_run: bool,
) -> tuple[list[dict], str | None]:
    """
    Scrape Nitter for tweets from `username`.
    Returns (tweets, error_message). tweets is sorted oldest-first.
    """
    url = f"{base_url}/{username}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code == 404:
            return [], "user not found"
        if resp.status_code != 200:
            return [], f"HTTP {resp.status_code}"
    except requests.RequestException as exc:
        return [], str(exc)

    soup = BeautifulSoup(resp.text, "html.parser")
    items = soup.select(".timeline-item")

    tweets = []
    for item in items:
        # Skip pinned tweets on subsequent runs to avoid re-reporting them
        if item.select_one(".pinned") and not is_first_run:
            continue

        link_tag = item.select_one(".tweet-link")
        content_tag = item.select_one(".tweet-content")
        date_tag = item.select_one(".tweet-date a")

        if not (link_tag and content_tag):
            continue

        tweet_id = parse_tweet_id(link_tag.get("href", ""))
        if not tweet_id:
            continue

        # Filter to only show tweets newer than what we've seen
        if since_id and int(tweet_id) <= int(since_id):
            continue

        date_title = date_tag.get("title", "") if date_tag else ""
        tweets.append({
            "id": tweet_id,
            "text": content_tag.get_text(separator=" ", strip=True),
            "created_at": parse_nitter_date(date_title) if date_title else "",
        })

    # Oldest-first, and cap on first run
    tweets.sort(key=lambda t: int(t["id"]))
    if is_first_run:
        tweets = tweets[-MAX_RESULTS_FIRST_RUN:]

    return tweets, None


# ---------------------------------------------------------------------------
# AI summarization
# ---------------------------------------------------------------------------

def summarize_tweets(username: str, tweets: list[dict]) -> str:
    """Return a concise AI-generated summary of what an account has been saying."""
    tweet_text = "\n".join(
        f"- [{fmt_time(t['created_at'])}] {t['text']}" for t in tweets
    )
    client = anthropic.Anthropic()
    with client.messages.stream(
        model="claude-opus-4-6",
        max_tokens=256,
        system="You summarize tweets concisely. Be direct and factual. 2-3 sentences max.",
        messages=[{
            "role": "user",
            "content": (
                f"Summarize what @{username} has been saying based on these recent tweets:\n\n"
                f"{tweet_text}"
            ),
        }],
    ) as stream:
        return stream.get_final_message().content[0].text


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def fmt_time(iso: str) -> str:
    if not iso:
        return "unknown time"
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except ValueError:
        return iso


def fmt_tweet(tweet: dict) -> str:
    return f"  [{fmt_time(tweet['created_at'])}] {tweet['text']}"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    accounts = load_accounts()
    store = load_store()
    store.setdefault("accounts", {})

    last_run: str | None = store.get("last_run")
    is_first_run = last_run is None

    print("Connecting to a Nitter instance...")
    base_url = pick_instance()
    if not base_url:
        raise SystemExit("Error: No Nitter instances are reachable. Try again later.")
    print(f"Using: {base_url}\n")

    sep = "=" * 70
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(sep)
    print("X (Twitter) Monitor  [via Nitter]")
    print(f"Run at: {now_str}")
    if last_run:
        try:
            last_dt = datetime.fromisoformat(last_run)
            print(f"Last run: {last_dt.strftime('%Y-%m-%d %H:%M UTC')}")
        except ValueError:
            print(f"Last run: {last_run}")
    else:
        print("First run — fetching recent tweets as baseline")
    print(sep)

    with_new: list[tuple[str, list[dict]]] = []
    without_new: list[str] = []
    errors: list[str] = []

    total = len(accounts)
    for i, username in enumerate(accounts, 1):
        print(f"  [{i}/{total}] Checking @{username}...", end=" ", flush=True)

        key = username.lower()
        account_data = store["accounts"].get(key, {})
        since_id = None if is_first_run else account_data.get("last_tweet_id")

        tweets, error = fetch_tweets(base_url, username, since_id, is_first_run)

        if error:
            print(f"error: {error}")
            errors.append(f"@{username}: {error}")
            continue

        store["accounts"].setdefault(key, {})

        if tweets:
            count = len(tweets)
            print(f"{count} new tweet{'s' if count != 1 else ''}")
            max_id = max(tweets, key=lambda t: int(t["id"]))["id"]
            store["accounts"][key]["last_tweet_id"] = max_id
            store["accounts"][key]["last_updated"] = datetime.now(timezone.utc).isoformat()
            with_new.append((username, tweets))
        else:
            print("no new tweets")
            without_new.append(username)

        time.sleep(1)  # be polite to Nitter instances

    save_store(store)
    print()

    if with_new:
        label = "Recent Tweets (first run)" if is_first_run else "New Tweets Since Last Run"
        print(f"\n{label}\n")
        for username, tweets in with_new:
            count = len(tweets)
            print(f"@{username}  ({count} new tweet{'s' if count != 1 else ''})")
            for tweet in tweets:
                print(fmt_tweet(tweet))
            print(f"\n  Summary: {summarize_tweets(username, tweets)}")
            print()
    elif not errors:
        print("\nNo new tweets found from any monitored account.")

    if errors:
        print("\nErrors:")
        for msg in errors:
            print(f"  - {msg}")

    if without_new:
        print("-" * 70)
        print("No new tweets:")
        for username in without_new:
            print(f"  @{username}")

    print(sep)


if __name__ == "__main__":
    main()
