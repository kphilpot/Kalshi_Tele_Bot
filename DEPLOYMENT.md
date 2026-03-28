# Kalshi Weather Bot — Deployment Guide

DigitalOcean Ubuntu 22.04 LTS droplet, Python 3.11+, systemd persistent service.

---

## Part 1 — Before You Start: Get Your Telegram Chat ID

You need this before running the bot.

### Step 1 — Create your bot with BotFather

1. Open Telegram and search for `@BotFather`
2. Send: `/newbot`
3. Choose a name (e.g. `Kalshi Weather Monitor`)
4. Choose a username ending in `bot` (e.g. `kalshi_weather_bot`)
5. BotFather will send you a token like:
   ```
   5812345678:AAHxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   ```
6. Copy this — it goes in `TELEGRAM_BOT_TOKEN=` in your `.env`

### Step 2 — Find your personal chat ID

1. Send any message to your new bot (e.g. "hello")
2. Open a browser and paste this URL (replace YOUR_TOKEN):
   ```
   https://api.telegram.org/botYOUR_TOKEN/getUpdates
   ```
3. You'll see JSON output. Find the `"chat"` object:
   ```json
   "chat": {
     "id": 123456789,
     "first_name": "Your Name",
     "type": "private"
   }
   ```
4. The number after `"id":` is your `TELEGRAM_CHAT_ID`

If the JSON is empty (`{"ok":true,"result":[]}`), send another message to the bot and refresh the URL.

---

## Part 2 — DigitalOcean Server Setup

### Step 1 — Create a droplet

- Image: Ubuntu 22.04 LTS
- Size: Basic, 1 vCPU / 1 GB RAM is sufficient
- Add your SSH key during creation

### Step 2 — Connect and update

```bash
ssh root@YOUR_SERVER_IP
apt update && apt upgrade -y
```

### Step 3 — Install Python 3.11+

```bash
apt install -y python3.11 python3.11-venv python3.11-dev python3-pip git
```

Verify:
```bash
python3.11 --version
```

### Step 4 — Create a dedicated user

```bash
useradd -r -s /bin/false -m -d /opt/kalshi-weather-bot botuser
```

---

## Part 3 — Install the Bot

### Step 1 — Clone your GitHub repo

```bash
git clone https://github.com/YOUR_USERNAME/kalshi-weather-bot.git /opt/kalshi-weather-bot
chown -R botuser:botuser /opt/kalshi-weather-bot
```

Or if you're not using git yet, copy your files manually:
```bash
mkdir -p /opt/kalshi-weather-bot
# Then scp your files up:
# scp *.py requirements.txt .env root@YOUR_SERVER_IP:/opt/kalshi-weather-bot/
chown -R botuser:botuser /opt/kalshi-weather-bot
```

### Step 2 — Create Python virtual environment

```bash
cd /opt/kalshi-weather-bot
python3.11 -m venv venv
venv/bin/pip install --upgrade pip
venv/bin/pip install -r requirements.txt
```

### Step 3 — Create the .env file

```bash
cp .env.example .env
nano .env
```

Fill in all four values:
```
TELEGRAM_BOT_TOKEN=5812345678:AAHxxxx...
TELEGRAM_CHAT_ID=123456789
KALSHI_EMAIL=your@email.com
KALSHI_PASSWORD=yourpassword
```

Save and exit nano: `Ctrl+X`, then `Y`, then `Enter`

Lock down permissions so only botuser can read it:
```bash
chown botuser:botuser .env
chmod 600 .env
```

### Step 4 — Create the state directory

```bash
mkdir -p /opt/kalshi-weather-bot/state
chown botuser:botuser /opt/kalshi-weather-bot/state
```

---

## Part 4 — Set Up systemd Service

### Step 1 — Install the service file

```bash
cp /opt/kalshi-weather-bot/kalshi-weather-bot.service /etc/systemd/system/
```

### Step 2 — Reload systemd and enable

```bash
systemctl daemon-reload
systemctl enable kalshi-weather-bot
```

### Step 3 — Start the bot

```bash
systemctl start kalshi-weather-bot
```

### Step 4 — Verify it's running

```bash
systemctl status kalshi-weather-bot
```

You should see `Active: active (running)`.

### Step 5 — Watch live logs

```bash
journalctl -u kalshi-weather-bot -f
```

Press `Ctrl+C` to stop tailing. To see logs from a specific time:
```bash
journalctl -u kalshi-weather-bot --since "2026-03-27 08:00:00"
```

---

## Part 5 — Test the Bot

### Test 1 — Basic connectivity

Go to Telegram and send your bot:
```
/start
```

You should get a response showing monitoring windows.

### Test 2 — Manual dispatch

```
/dispatch
```

This runs a full cross-reference for all three cities immediately, regardless of the poll window. You'll get back a combined status message with the ALL-CAPS manual trigger warning at the top.

### Test 3 — Status check

```
/status
```

Shows the compact current state for all three cities.

### Test 4 — State reset (if needed)

```
/reset KAUS
/reset all
```

Clears state for one or all cities — useful during testing.

---

## Part 6 — Managing the Service

### Stop the bot
```bash
systemctl stop kalshi-weather-bot
```

### Restart the bot (e.g., after updating code)
```bash
systemctl restart kalshi-weather-bot
```

### Disable auto-start on reboot
```bash
systemctl disable kalshi-weather-bot
```

### Update the bot code
```bash
cd /opt/kalshi-weather-bot
git pull                        # if using git
venv/bin/pip install -r requirements.txt   # if requirements changed
systemctl restart kalshi-weather-bot
```

---

## Part 7 — Important Notes

### Timezone behaviour

The bot uses `pytz` for all timezone math. APScheduler is anchored to `America/New_York`. DST transitions are handled automatically — you don't need to do anything when clocks change.

### CLI times are LST

The NWS CLI product always reports times in Local Standard Time (LST), never daylight time. During summer (DST active), a time of "2:00 PM LST" in Austin means "3:00 PM CDT" on the clock. The bot annotates CLI times with "(LST)" in morning messages so you always know what you're looking at.

### DSM confirmation timing

The ASOS Daily Summary Message (DSM) is typically issued between late morning and early afternoon local time. If the daily high occurs early in the day, the DSM may not be available until hours later. The bot holds the alert and retries every 10 minutes. If the DSM hasn't confirmed by 5 PM local time, a DSM timeout alert fires.

### Kalshi market discovery

The bot tries a list of known series ticker candidates first (Tier 1), then falls back to a broad category search (Tier 2). If Kalshi changes their ticker naming conventions, Tier 2 will catch it automatically as long as the market title still contains the city name and "high."

### If the server reboots mid-day

State is persisted to JSON files in `/opt/kalshi-weather-bot/state/`. When the bot restarts, it loads the state file for today and resumes exactly where it left off. No data is lost. Use `/dispatch` to immediately re-check all cities after a restart.

### Log rotation

systemd journal handles log rotation automatically. To limit the total size of journal logs:
```bash
journalctl --vacuum-size=100M
```

Or configure in `/etc/systemd/journald.conf`:
```
SystemMaxUse=100M
```
Then: `systemctl restart systemd-journald`

---

## Obsidian Knowledge Base (Optional but Recommended)

Create a local Obsidian vault (separate from your code repo) with this structure for strategy documentation:

```
Kalshi Bot Brain/
├── Logic/
│   ├── Trigger Logic.md        — confirmation strategy step-by-step
│   ├── Bracket Rules.md        — upper-end rule explained
│   ├── DSM Timing.md           — when DSM publishes, timeout behaviour
│   └── Poll Windows.md         — city-by-city timing reference
├── Errors & Edge Cases/
│   ├── Known Issues.md
│   ├── DSM Never Updates.md
│   └── Kalshi API Quirks.md
└── Deployment/
    └── Server Setup.md
```

The code repo (`DEPLOYMENT.md`) is the technical reference. The Obsidian vault is where you keep the trading strategy reasoning, known edge cases, and running notes.

---

## Quick Reference

| Task | Command |
|------|---------|
| View logs | `journalctl -u kalshi-weather-bot -f` |
| Restart bot | `systemctl restart kalshi-weather-bot` |
| Check status | `systemctl status kalshi-weather-bot` |
| Manual trigger | Send `/dispatch` in Telegram |
| Check city state | Send `/status` in Telegram |
| Reset state | Send `/reset all` in Telegram |
| Update code | `git pull && systemctl restart kalshi-weather-bot` |
