# Kalshi Weather Bot — Startup Guide

**Last Updated**: 2026-04-03

---

## Quick Start (Copy-Paste)

### Step 1: Prerequisites Check

Before starting, verify you have:
- **Python 3.9 or later** installed
- **`.env` file** in the project root with these 4 variables:
  ```
  TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
  TELEGRAM_CHAT_ID=your_telegram_chat_id_here
  KALSHI_API_KEY_ID=your_kalshi_api_key_id_here
  KALSHI_PRIVATE_KEY_PATH=/path/to/your/kalshi/private/key
  ```
- **Kalshi private key file** at the path specified in `KALSHI_PRIVATE_KEY_PATH`

If any of these are missing, the bot will fail to start.

---

### Step 2: Install Dependencies (First Time Only)

Open PowerShell **in the project directory** and run:

```powershell
py -m pip install -r requirements.txt
```

Expected output: `Successfully installed ...` with a list of packages.

If you see "No module named pip", try:
```powershell
py -m ensurepip --upgrade
```

Then retry the pip install command.

---

### Step 3: Start the Bot

In PowerShell (in the project directory), run:

```powershell
py bot.py
```

**Do NOT use `python bot.py`** — on Windows, use `py` (the Python launcher).

---

### Step 4: What to Expect

When the bot starts successfully, you should see:

```
2026-04-03 12:45:30 [INFO] __main__: Kalshi Weather Bot initialising...
2026-04-03 12:45:31 [INFO] apscheduler.scheduler: Scheduler started
2026-04-03 12:45:31 [INFO] __main__: Telegram polling started
2026-04-03 12:45:31 [INFO] telegram.ext._application: Application initialized
```

**The bot is now running.** It will:
- Poll NWS and Kalshi data every 10 minutes (during noon–10 PM poll window)
- Send Telegram alerts when high temperatures are detected
- Log all activity to the console

---

### Step 5: Test the Bot

In Telegram, send these commands to the bot:

| Command | What It Does |
|---------|-------------|
| `/start` | Show bot status and monitoring overview |
| `/ping` | Quick alive check (time + METAR status) |
| `/status` | Current state for all 3 cities |
| `/dispatch` | Manual trigger: cross-reference all markets now |
| `/reset KAUS` | Reset state for Austin (or use `all` for all cities) |

---

### Step 6: Stop the Bot

In PowerShell, press **`Ctrl+C`** to shut down gracefully.

```
2026-04-03 12:50:15 [INFO] __main__: Bot shutting down...
```

---

## Troubleshooting

### "Python was not found; run without arguments to install from Microsoft Store"

**Problem**: You typed `python bot.py` instead of `py bot.py`.

**Solution**: Use `py bot.py` (the Windows Python launcher). This is the correct command on Windows.

---

### "ModuleNotFoundError: No module named 'telegram'"

**Problem**: Dependencies not installed, or installed in a different environment.

**Solution**: Run this command in the project directory:
```powershell
py -m pip install -r requirements.txt
```

Then verify by running:
```powershell
py -c "import telegram; print(telegram.__version__)"
```

You should see a version number (e.g., `20.1`).

---

### "RuntimeError: TELEGRAM_BOT_TOKEN is not set in .env"

**Problem**: The `.env` file is missing or incomplete.

**Solution**:
1. Create a file named `.env` in the project root (same folder as `bot.py`)
2. Add these 4 lines:
   ```
   TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
   TELEGRAM_CHAT_ID=your_telegram_chat_id_here
   KALSHI_API_KEY_ID=your_kalshi_api_key_id_here
   KALSHI_PRIVATE_KEY_PATH=/path/to/your/kalshi/private/key
   ```
3. Replace the placeholder values with your actual credentials
4. Save the file
5. Restart the bot with `py bot.py`

---

### "Failed to read private key file"

**Problem**: The Kalshi private key path in `.env` is wrong or the file doesn't exist.

**Solution**:
1. Verify the path in `KALSHI_PRIVATE_KEY_PATH` is correct
2. Check that the file exists at that path
3. Make sure the path uses forward slashes `/` (even on Windows) or raw string notation if using backslashes

Example valid paths:
```
KALSHI_PRIVATE_KEY_PATH=C:/Users/user/kalshi_key.pem
KALSHI_PRIVATE_KEY_PATH=./keys/kalshi_key.pem
```

---

### "Connection refused" or "Connection timeout"

**Problem**: The bot can't reach NWS or Kalshi APIs.

**Solution**:
1. Check your internet connection
2. Verify the APIs are reachable:
   - NWS: `https://api.weather.gov/`
   - Kalshi: `https://api.elections.kalshi.com/`
3. Check if your firewall or VPN is blocking these connections

---

### "Telegram polling started" but no alerts arrive

**Problem**: The bot is running but not sending alerts.

**Possible causes**:
- Alerts only fire during the poll window (noon–10 PM local city time)
- The detected high hasn't passed all validation gates (Triple-Lock)
- Check the Telegram chat ID in `.env` — verify it's correct by sending `/status` command

**To debug**:
1. Send `/status` to check current state
2. Send `/dispatch` to manually trigger a cross-reference
3. Check the console output for any error messages

---

## Architecture Overview

The bot runs a daily cycle:

| Time (Local) | Action |
|-------------|--------|
| 8:00 AM EST | Morning brief + log Kalshi markets (markets not sent to Telegram) |
| 2:00 PM EST | Afternoon check-in |
| Noon–10 PM | Poll METAR every 10 minutes, detect high temps |
| After 5 PM | Fetch NWS Climate Report for confirmation |
| 8:00 PM | Timeout force-confirmation if no agreement |
| 10:00 PM EST | End-of-day summary + backtest snapshot |
| 12:00 AM | Midnight reset (next day) |

---

## Next Steps

- **First Run**: Send `/start` to confirm the bot is alive
- **Monitor Alerts**: Watch for temperature detection alerts during the noon–10 PM poll window
- **Debug Issues**: Use `/status` and `/dispatch` commands to investigate state
- **Read Logs**: Check the console output for any errors or unusual messages

For more details on architecture and troubleshooting, see `HANDOFF.md`.
