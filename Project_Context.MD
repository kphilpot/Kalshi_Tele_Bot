# Project: Kalshi_Tele_Bot
**Created:** 2026-03-28

## Project Scope
Telegram bot that monitors METAR temperature data for Austin (KAUS), Miami (KMIA), and Chicago (KMDW). Sends automated alerts when a daily temperature peak is detected, runs a Triple-Lock validation pipeline, performs T-Group Settlement Audits, and matches the confirmed high against Kalshi weather bracket markets.

## Current Status
Production-ready. Running locally on Dell 3640 workstation. Core poll cycle, Triple-Lock, Settlement Auditor, and backtest logging are all implemented and verified.

## Next Steps
- Collect 2-3 weeks of real backtest data to calibrate per-city price ceilings
- Automate trade execution once accuracy is validated at 90%+ across all three cities
