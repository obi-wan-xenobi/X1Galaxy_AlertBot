# 🛰 X1Galaxy Alert Bot (Telegram)
### Monitor your X1 validators with real-time Telegram alerts

This repository contains the code for the **X1Galaxy Alert Bot** — a Telegram bot that lets users **subscribe to X1 validator identity pubkeys** and receive alerts when important validator metrics change.

The bot runs as a lightweight Python service, polling the latest indexed validator dataset every few minutes and notifying subscribed users via Telegram.

---

## ✨ Features

- ✅ **Subscribe** to validator identity pubkeys and receive private alerts
- 📋 **List** your active subscriptions
- 🔕 **Unsubscribe** from alerts at any time
- 🟢🔴 Alerts on **validator status changes** (Active / Delinquent)
- ⚖️ Alerts on **commission changes**
- 💰 Alerts on **epoch reward increases** (based on lifetime rewards deltas)
- 🧠 Friendly validator names where available (otherwise uses shortened pubkey)

---

## 🔗 Bot Commands

The bot registers these commands in the Telegram UI menu:

- `/start` – Introduction + help
- `/subscribe <IDENTITY>` – Subscribe to validator alerts
- `/list` – View current subscriptions
- `/unsubscribe <IDENTITY>` – (recommended to add; see “Improvements”)

Example:

```bash
/subscribe HN4DDjs...
