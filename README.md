# Twitter / X Account Monitor

Monitors a list of X (Twitter) accounts for new tweets using [Nitter](https://github.com/zedeus/nitter) — no API key or paid subscription required.

## Requirements

- Python 3.10+

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
python twitter_monitor.py
```

Or, if the virtual environment is not activated:

```bash
venv/bin/python twitter_monitor.py
```

## How it works

- **First run:** fetches up to the 20 most recent tweets per account and saves them as a baseline. No output is shown for these since they are treated as already seen.
- **Subsequent runs:** only tweets posted since the last run are shown.
- Progress is printed as each account is checked, followed by a full summary at the end.
- Results and state are saved to `tweet_store.json` between runs.

## Automating with cron

To run the monitor every hour and append output to a log file:

```bash
crontab -e
```

Add the following line (adjust the path to match your project directory):

```
0 * * * * /path/to/proj1/venv/bin/python /path/to/proj1/twitter_monitor.py >> /path/to/proj1/monitor.log 2>&1
```

## Files

| File | Description |
|------|-------------|
| `twitter_monitor.py` | Main script |
| `accounts.json` | List of Twitter usernames to monitor |
| `tweet_store.json` | Auto-generated state file — tracks last seen tweet per account |
| `requirements.txt` | Python dependencies |
