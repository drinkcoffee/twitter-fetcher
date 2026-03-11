#!/usr/bin/env python3
"""
X (Twitter) Account Monitor — Nitter edition

Fetches recent tweets from a list of accounts via Nitter (no API key needed),
stores them locally, and prints a summary of new tweets since the last run.
"""

import json
import os
import random
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import anthropic
import ollama
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.remote.webdriver import WebDriver

STORE_FILE = Path("tweet_store.json")
ACCOUNTS_FILE = Path("accounts.json")
MAX_RESULTS_FIRST_RUN = 20

# Try instances in order; first one that responds wins
NITTER_INSTANCES = [
    "https://nitter.poast.org",
    "https://nitter.privacydev.net",
    "https://nitter.cz",
    "https://nitter.net",
]



# ---------------------------------------------------------------------------
# Data persistence
# ---------------------------------------------------------------------------

def load_store() -> dict:
    if STORE_FILE.exists():
        with open(STORE_FILE) as f:
            result = json.load(f)
    else:
        result = {"accounts": {}, "last_run": None}
    return result


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
    result = [a.lstrip("@") for a in accounts]
    print(f"Checking accounts -> {result}")
    return result


# ---------------------------------------------------------------------------
# Nitter scraping
# ---------------------------------------------------------------------------

def pick_instance(driver: WebDriver) -> str | None:
    """Return the first Nitter instance that responds with real content."""
    print(f"Trying {len(NITTER_INSTANCES)} Nitter instances")
    for base in random.sample(NITTER_INSTANCES, len(NITTER_INSTANCES)):
        try:
            print(f" Trying {base}")
            driver.get(base)
            html = driver.page_source
            if len(html) > 1000:
                # print(f"[DEBUG] pick_instance() -> {base} ({len(html)} bytes)")
                return base
            print(f" {base} returned too little content ({len(html)} bytes), skipping")
        except Exception as exc:
            print(f" {base} failed: {exc}")
    print(f"No Nitter instances reachable)")
    return None


def parse_tweet_id(tweet_link: str) -> str | None:
    """Extract numeric tweet ID from a path like /username/status/12345."""
    #print(f"[DEBUG] parse_tweet_id('{tweet_link}')")
    parts = tweet_link.rstrip("/").split("/")
    if "status" in parts:
        idx = parts.index("status")
        if idx + 1 < len(parts):
            candidate = parts[idx + 1].split("#")[0]
            if candidate.isdigit():
                #print(f"[DEBUG] parse_tweet_id() -> '{candidate}'")
                return candidate
    #print(f"[DEBUG] parse_tweet_id() -> None")
    return None


def parse_nitter_date(title: str) -> str:
    """
    Nitter title attribute format: 'Mar 9, 2026 · 3:45 PM UTC'
    Returns ISO format string or the raw title if parsing fails.
    """
    #print(f"[DEBUG] parse_nitter_date('{title}')")
    try:
        clean = title.replace(" · ", " ").replace(" UTC", "")
        dt = datetime.strptime(clean, "%b %d, %Y %I:%M %p")
        result = dt.replace(tzinfo=timezone.utc).isoformat()
        #print(f"[DEBUG] parse_nitter_date() -> '{result}'")
        return result
    except ValueError:
        print(f"[DEBUG] parse_nitter_date() -> '{title}' (parse failed, returning raw)")
        return title


def fetch_tweets(
    driver: WebDriver,
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
        driver.get(url)
        html = driver.page_source
        if len(html) < 1000:
            return [], f"unexpected empty response ({len(html)} bytes)"
    except Exception as exc:
        print(f"[DEBUG] fetch_tweets() -> ([], '{exc}')")
        return [], str(exc)

    soup = BeautifulSoup(html, "html.parser")
    # Check for 404-style "user not found" page
    if soup.select_one(".error-panel"):
        return [], "user not found"
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

SYSTEM_PROMPT = "You summarize tweets concisely. Be direct and factual. 2-3 sentences max."


def summarize_tweets(username: str, tweets: list[dict]) -> str:
    """Return a concise AI-generated summary of what an account has been saying."""
    #print(f"[DEBUG] summarize_tweets(username='{username}', tweets={len(tweets)} items)")
    tweet_text = "\n".join(
        f"- [{fmt_time(t['created_at'])}] {t['text']}" for t in tweets
    )
    user_prompt = (
        f"Summarize what @{username} has been saying based on these recent tweets:\n\n"
        f"{tweet_text}"
    )

    provider = os.environ.get("LLM_PROVIDER", "ollama").lower()
    if provider == "anthropic":
        result = _summarize_anthropic(user_prompt)
    else:
        result = _summarize_ollama(user_prompt)

    #print(f"[DEBUG] summarize_tweets() -> '{result[:100]}{'...' if len(result) > 100 else ''}'")
    return result


def _summarize_anthropic(user_prompt: str) -> str:
    client = anthropic.Anthropic()
    with client.messages.stream(
        model="claude-opus-4-6",
        max_tokens=256,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    ) as stream:
        return stream.get_final_message().content[0].text


def _summarize_ollama(user_prompt: str) -> str:
    model = os.environ.get("OLLAMA_MODEL", "llama3.2")
    response = ollama.chat(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    )
    return response.message.content


def summarize_all(account_summaries: list[tuple[str, str]]) -> str:
    """Return an overall summary across all account summaries."""
    combined = "\n".join(f"@{username}: {summary}" for username, summary in account_summaries)
    user_prompt = (
        f"Below are summaries of what several accounts have been posting on X (Twitter).\n\n"
        f"{combined}\n\n"
        f"Write a brief overall summary of the key themes and topics being discussed."
    )
    provider = os.environ.get("LLM_PROVIDER", "ollama").lower()
    if provider == "anthropic":
        result = _summarize_anthropic(user_prompt)
    else:
        result = _summarize_ollama(user_prompt)
    return result


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def fmt_time(iso: str) -> str:
    if not iso:
        print(f"[DEBUG] fmt_time() -> 'unknown time'")
        return "unknown time"
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        result = dt.strftime("%Y-%m-%d %H:%M UTC")
        return result
    except ValueError:
        print(f"[DEBUG] fmt_time() -> '{iso}' (parse failed, returning raw)")
        return iso


def fmt_tweet(tweet: dict) -> str:
    # print(f"[DEBUG] fmt_tweet(id={tweet.get('id')}, text='{tweet.get('text', '')[:50]}...')")
    result = f"  [{fmt_time(tweet['created_at'])}] {tweet['text']}"
    # print(f"[DEBUG] fmt_tweet() -> '{result[:80]}{'...' if len(result) > 80 else ''}'")
    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    provider = os.environ.get("LLM_PROVIDER", "ollama").lower()
    if provider == "anthropic" and not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("Error: ANTHROPIC_API_KEY environment variable is not set.")
    print(f"Using LLM provider: {provider}" + (f" (model: {os.environ.get('OLLAMA_MODEL', 'llama3.2')})" if provider == "ollama" else ""))

    accounts = load_accounts()
    store = load_store()
    store.setdefault("accounts", {})

    last_run: str | None = store.get("last_run")
    is_first_run = last_run is None

    if last_run is not None:
        last_run_dt = datetime.fromisoformat(last_run)
        now = datetime.now(timezone.utc)
        if (now - last_run_dt).total_seconds() < 3600:
            print("Last run was less than an hour ago — treating as if last run was one day ago.")
            last_run = (now - timedelta(days=1)).isoformat()
            # Clear per-account since_id so tweets from the past day are fetched
            for key in store.get("accounts", {}):
                store["accounts"][key].pop("last_tweet_id", None)

    options = Options()
    options.add_argument('-headless')
    driver = webdriver.Firefox(options=options)
    try:
        _run(accounts, store, last_run, is_first_run, driver)
    finally:
        driver.quit()


def _run(accounts, store, last_run, is_first_run, driver: WebDriver) -> None:
    print("Connecting to a Nitter instance...")
    base_url = pick_instance(driver)
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
        print(f"[{i}/{total}] Checking @{username}...", end=" ", flush=True)

        key = username.lower()
        account_data = store["accounts"].get(key, {})
        since_id = None if is_first_run else account_data.get("last_tweet_id")

        tweets, error = fetch_tweets(driver, base_url, username, since_id, is_first_run)

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
        account_summaries: list[tuple[str, str]] = []
        for username, tweets in with_new:
            count = len(tweets)
            print(f"@{username}  ({count} new tweet{'s' if count != 1 else ''})")
            for tweet in tweets:
                print(f"  {fmt_time(tweet['created_at'])}")
            summary = summarize_tweets(username, tweets)
            account_summaries.append((username, summary))
            print(f"\n  Summary: {summary}")
            print()

        if len(account_summaries) > 1:
            print(sep)
            print(f"Overall Summary({len(account_summaries)} accounts)")
            print(sep)
            print(summarize_all(account_summaries))
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
