#!/usr/bin/env python3
"""
X (Twitter) Account Monitor — Nitter edition

Fetches recent tweets from a list of accounts via Nitter (no API key needed)
and prints a summary of tweets within the lookback window.
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

ACCOUNTS_FILE = Path("accounts.json")
LOOKBACK_HOURS = 24  # Only report tweets from this many hours ago

# Try instances in order; first one that responds wins
NITTER_INSTANCES = [
    "https://nitter.poast.org",
    "https://nitter.privacydev.net",
    "https://nitter.cz",
    "https://nitter.net",
]


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
                return base
            print(f" {base} returned too little content ({len(html)} bytes), skipping")
        except Exception as exc:
            print(f" {base} failed: {exc}")
    print("No Nitter instances reachable")
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
        print(f"[DEBUG] parse_nitter_date() -> '{title}' (parse failed, returning raw)")
        return title


def fetch_tweets(
    driver: WebDriver,
    base_url: str,
    username: str,
    cutoff_dt: datetime,
) -> tuple[list[dict], str | None]:
    """
    Scrape Nitter for tweets from `username` posted after `cutoff_dt`.
    Returns (tweets, error_message). tweets is sorted oldest-first.
    """
    url = f"{base_url}/{username}"
    try:
        driver.get(url)
        html = driver.page_source
        if len(html) < 1000:
            return [], f"unexpected empty response ({len(html)} bytes)"
    except Exception as exc:
        return [], str(exc)

    soup = BeautifulSoup(html, "html.parser")
    if soup.select_one(".error-panel"):
        return [], "user not found"
    items = soup.select(".timeline-item")

    tweets = []
    for item in items:
        if item.select_one(".pinned"):
            continue

        link_tag = item.select_one(".tweet-link")
        content_tag = item.select_one(".tweet-content")
        date_tag = item.select_one(".tweet-date a")

        if not (link_tag and content_tag):
            continue

        tweet_id = parse_tweet_id(link_tag.get("href", ""))
        if not tweet_id:
            continue

        date_title = date_tag.get("title", "") if date_tag else ""
        created_at = parse_nitter_date(date_title) if date_title else ""

        if created_at:
            try:
                if datetime.fromisoformat(created_at) < cutoff_dt:
                    continue
            except ValueError:
                pass

        tweets.append({
            "id": tweet_id,
            "text": content_tag.get_text(separator=" ", strip=True),
            "created_at": created_at,
        })

    tweets.sort(key=lambda t: int(t["id"]))
    return tweets, None


# ---------------------------------------------------------------------------
# AI summarization
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = "You summarize tweets concisely. Be direct and factual. 2-3 sentences max."


def summarize_tweets(username: str, tweets: list[dict]) -> str:
    """Return a concise AI-generated summary of what an account has been saying."""
    tweet_text = "\n".join(
        f"- [{fmt_time(t['created_at'])}] {t['text']}" for t in tweets
    )
    user_prompt = (
        f"Summarize what @{username} has been saying based on these recent tweets:\n\n"
        f"{tweet_text}"
    )
    provider = os.environ.get("LLM_PROVIDER", "ollama").lower()
    return _summarize_anthropic(user_prompt) if provider == "anthropic" else _summarize_ollama(user_prompt)


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
    return _summarize_anthropic(user_prompt) if provider == "anthropic" else _summarize_ollama(user_prompt)


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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    provider = os.environ.get("LLM_PROVIDER", "ollama").lower()
    if provider == "anthropic" and not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("Error: ANTHROPIC_API_KEY environment variable is not set.")
    print(f"Using LLM provider: {provider}" + (f" (model: {os.environ.get('OLLAMA_MODEL', 'llama3.2')})" if provider == "ollama" else ""))

    accounts = load_accounts()
    cutoff_dt = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)

    options = Options()
    options.add_argument('-headless')
    driver = webdriver.Firefox(options=options)
    try:
        _run(accounts, cutoff_dt, driver)
    finally:
        driver.quit()


def _run(accounts: list[str], cutoff_dt: datetime, driver: WebDriver) -> None:
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
    print(f"Showing tweets from the last {LOOKBACK_HOURS} hour{'s' if LOOKBACK_HOURS != 1 else ''} (since {cutoff_dt.strftime('%Y-%m-%d %H:%M UTC')})")
    print(sep)

    with_tweets: list[tuple[str, list[dict]]] = []
    without_tweets: list[str] = []
    errors: list[str] = []

    total = len(accounts)
    for i, username in enumerate(accounts, 1):
        print(f"[{i}/{total}] Checking @{username}...", end=" ", flush=True)

        tweets, error = fetch_tweets(driver, base_url, username, cutoff_dt)

        if error:
            print(f"error: {error}")
            errors.append(f"@{username}: {error}")
            continue

        if tweets:
            print(f"{len(tweets)} tweet{'s' if len(tweets) != 1 else ''}")
            with_tweets.append((username, tweets))
        else:
            print("no tweets")
            without_tweets.append(username)

        time.sleep(1)  # be polite to Nitter instances

    print()

    if with_tweets:
        print(f"\nTweets (last {LOOKBACK_HOURS}h)\n")
        account_summaries: list[tuple[str, str]] = []
        for username, tweets in with_tweets:
            print(f"@{username}  ({len(tweets)} tweet{'s' if len(tweets) != 1 else ''})")
            for tweet in tweets:
                print(f"  {fmt_time(tweet['created_at'])}")
            summary = summarize_tweets(username, tweets)
            account_summaries.append((username, summary))
            print(f"\n  Summary: {summary}")
            print()

        if len(account_summaries) > 1:
            print(sep)
            print(f"Overall Summary ({len(account_summaries)} accounts)")
            print(sep)
            print(summarize_all(account_summaries))
            print()
    elif not errors:
        print(f"\nNo tweets found from any monitored account in the last {LOOKBACK_HOURS}h.")

    if errors:
        print("\nErrors:")
        for msg in errors:
            print(f"  - {msg}")

    if without_tweets:
        print("-" * 70)
        print(f"No tweets in last {LOOKBACK_HOURS}h:")
        for username in without_tweets:
            print(f"  @{username}")

    print(sep)


if __name__ == "__main__":
    main()
