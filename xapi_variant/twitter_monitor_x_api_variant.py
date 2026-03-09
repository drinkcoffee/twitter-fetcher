#!/usr/bin/env python3
"""
X (Twitter) Account Monitor

Fetches recent tweets from a list of accounts, stores them locally,
and prints a summary of new tweets since the last run.

Uses the X API.
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import tweepy
from dotenv import load_dotenv

load_dotenv()

STORE_FILE = Path("tweet_store.json")
ACCOUNTS_FILE = Path("accounts.json")
MAX_RESULTS_FIRST_RUN = 20
MAX_RESULTS_SUBSEQUENT = 100  # max allowed by API per request


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

def load_credentials() -> str:
    token = os.getenv("TWITTER_BEARER_TOKEN")
    if not token:
        sys.exit(
            "Error: TWITTER_BEARER_TOKEN is not set.\n"
            "Copy .env.example to .env and add your Bearer Token."
        )
    return token


def load_accounts() -> list[str]:
    if not ACCOUNTS_FILE.exists():
        sys.exit(
            f"Error: {ACCOUNTS_FILE} not found.\n"
            'Create accounts.json with a JSON array of usernames, e.g.:\n'
            '  ["nasa", "openai", "github"]'
        )
    with open(ACCOUNTS_FILE) as f:
        accounts = json.load(f)
    if not isinstance(accounts, list) or not accounts:
        sys.exit("Error: accounts.json must be a non-empty JSON array of usernames.")
    return [a.lstrip("@") for a in accounts]


# ---------------------------------------------------------------------------
# Twitter API
# ---------------------------------------------------------------------------

def fetch_tweets(
    client: tweepy.Client,
    username: str,
    since_id: str | None,
    is_first_run: bool,
) -> tuple[str | None, list[dict], str | None]:
    """
    Returns (user_id, tweets, error_message).
    tweets is a list of dicts sorted oldest-first.
    """
    try:
        user_resp = client.get_user(username=username)
        if not user_resp.data:
            return None, [], f"user not found"

        user_id = str(user_resp.data.id)
        max_results = MAX_RESULTS_FIRST_RUN if is_first_run else MAX_RESULTS_SUBSEQUENT

        kwargs: dict = {
            "id": user_id,
            "max_results": max_results,
            "tweet_fields": ["created_at", "text", "id"],
            "exclude": ["retweets", "replies"],
        }
        if since_id:
            kwargs["since_id"] = since_id

        resp = client.get_users_tweets(**kwargs)

        if not resp.data:
            return user_id, [], None

        tweets = [
            {
                "id": str(t.id),
                "text": t.text,
                "created_at": t.created_at.isoformat() if t.created_at else "",
            }
            for t in resp.data
        ]
        # API returns newest-first; reverse to oldest-first for display
        tweets.sort(key=lambda t: int(t["id"]))
        return user_id, tweets, None

    except tweepy.errors.Forbidden:
        return None, [], "access forbidden (check your API access level)"
    except tweepy.errors.NotFound:
        return None, [], "user not found"
    except tweepy.errors.TweepyException as exc:
        return None, [], str(exc)


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
    text = tweet["text"]
    return f"  [{fmt_time(tweet['created_at'])}] {text}"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    bearer_token = load_credentials()
    accounts = load_accounts()
    store = load_store()
    store.setdefault("accounts", {})

    last_run: str | None = store.get("last_run")
    is_first_run = last_run is None

    client = tweepy.Client(bearer_token=bearer_token, wait_on_rate_limit=True)

    # ------------------------------------------------------------------
    # Header
    # ------------------------------------------------------------------
    sep = "=" * 70
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(sep)
    print("X (Twitter) Monitor")
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

    # ------------------------------------------------------------------
    # Fetch tweets for each account
    # ------------------------------------------------------------------
    with_new: list[tuple[str, list[dict]]] = []
    without_new: list[str] = []
    errors: list[str] = []

    for username in accounts:
        key = username.lower()
        account_data = store["accounts"].get(key, {})
        since_id = None if is_first_run else account_data.get("last_tweet_id")

        user_id, tweets, error = fetch_tweets(client, username, since_id, is_first_run)

        if error:
            errors.append(f"@{username}: {error}")
            continue

        # Update store
        if not store["accounts"].get(key):
            store["accounts"][key] = {}
        if user_id:
            store["accounts"][key]["user_id"] = user_id

        if tweets:
            max_id = max(tweets, key=lambda t: int(t["id"]))["id"]
            store["accounts"][key]["last_tweet_id"] = max_id
            store["accounts"][key]["last_updated"] = datetime.now(timezone.utc).isoformat()
            with_new.append((username, tweets))
        else:
            without_new.append(username)

    save_store(store)

    # ------------------------------------------------------------------
    # Output: accounts with new tweets
    # ------------------------------------------------------------------
    if with_new:
        label = "Recent Tweets (first run)" if is_first_run else "New Tweets Since Last Run"
        print(f"\n{label}\n")
        for username, tweets in with_new:
            count = len(tweets)
            print(f"@{username}  ({count} new tweet{'s' if count != 1 else ''})")
            for tweet in tweets:
                print(fmt_tweet(tweet))
            print()
    elif not errors:
        print("\nNo new tweets found from any monitored account.")

    # ------------------------------------------------------------------
    # Output: errors
    # ------------------------------------------------------------------
    if errors:
        print("\nErrors:")
        for msg in errors:
            print(f"  - {msg}")

    # ------------------------------------------------------------------
    # Output: accounts with no new tweets (bottom)
    # ------------------------------------------------------------------
    if without_new:
        print("-" * 70)
        print("No new tweets:")
        for username in without_new:
            print(f"  @{username}")

    print(sep)


if __name__ == "__main__":
    main()
