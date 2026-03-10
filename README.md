# Twitter / X Account Monitor

Monitors a list of X (Twitter) accounts for new tweets using [Nitter](https://github.com/zedeus/nitter) — no API key or paid subscription required.

## Requirements

- Python 3.10+
- Firefox web browser
- [Ollama](https://ollama.com) (default) or an Anthropic API key

## Setup

1. **Create and activate a virtual environment:**

   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```

2. **Install dependencies:**

   ```bash
   pip install -r requirements.txt
   ```

3. **Configure accounts to monitor:**

   Edit `accounts.json` with a JSON array of Twitter usernames:

   ```json
   ["nasa", "openai", "github"]
   ```

   The `@` prefix is optional — both `"nasa"` and `"@nasa"` are accepted.

## Running the script

```bash
venv/bin/python3 twitter_monitor.py
```

## LLM provider

The script uses an LLM to generate a short summary of new tweets. Two providers are supported, selected via the `LLM_PROVIDER` environment variable.

### Ollama (default)

Ollama runs models locally with no API key required. [Install Ollama](https://ollama.com/download) and pull a model before running:

```bash
ollama pull llama3.2
```

Then run the script — Ollama is used automatically if `LLM_PROVIDER` is not set:

```bash
venv/bin/python3 twitter_monitor.py
```

To use a different model, set `OLLAMA_MODEL`:

```bash
OLLAMA_MODEL=mistral venv/bin/python3 twitter_monitor.py
```

### Anthropic

To use Claude via the Anthropic API, set `LLM_PROVIDER=anthropic` and provide your API key:

```bash
export LLM_PROVIDER=anthropic
export ANTHROPIC_API_KEY=your_key_here
venv/bin/python3 twitter_monitor.py
```

### Summary of environment variables

| Variable | Default | Description |
|---|---|---|
| `LLM_PROVIDER` | `ollama` | LLM backend to use: `ollama` or `anthropic` |
| `OLLAMA_MODEL` | `llama3.2` | Ollama model name (only used when `LLM_PROVIDER=ollama`) |
| `ANTHROPIC_API_KEY` | — | Required when `LLM_PROVIDER=anthropic` |

## How it works

- **First run:** fetches up to the 20 most recent tweets per account and saves them as a baseline. No output is shown for these since they are treated as already seen.
- **Subsequent runs:** only tweets posted since the last run are shown.
- Progress is printed as each account is checked, followed by a full summary at the end.
- For any account with new tweets, the configured LLM generates a concise 2-3 sentence summary of what that account has been saying.
- Results and state are saved to `tweet_store.json` between runs.

## Automating with cron

To run the monitor every hour and append output to a log file:

```bash
crontab -e
```

Add the following line (adjust the path to match your project directory):

```
0 * * * * /path/to/twitter-fetcher/venv/bin/python3 /path/to/twitter-fetcher/twitter_monitor.py >> /path/to/twitter-fetcher/monitor.log 2>&1
```

## Files

| File | Description |
|------|-------------|
| `twitter_monitor.py` | Main script |
| `accounts.json` | List of Twitter usernames to monitor |
| `tweet_store.json` | Auto-generated state file — tracks last seen tweet per account |
| `requirements.txt` | Python dependencies |
| `xapi_variant/` | Alternative implementation using the official X/Twitter API |
