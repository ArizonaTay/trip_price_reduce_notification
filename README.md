# Trip.com Price Drop Monitor

Monitors hotel room prices on Trip.com and sends Telegram notifications when prices drop. Runs on **GitHub Actions** (free) every 6 hours — no server needed.

## Quick Start (GitHub Actions)

### 1. Fork this repo

Click the **Fork** button on GitHub.

### 2. Configure your hotels

Edit `prices.json` in your fork with the Trip.com URL, room name, and dates you want to track:

```json
[
  {
    "name": "Hotel Name",
    "room_name": "Deluxe Room",
    "url": "https://sg.trip.com/hotels/detail/?hotelId=...&checkIn=2026-10-24&checkOut=2026-10-26",
    "last_price": null,
    "currency": "SGD"
  }
]
```

Set `last_price` to `null` for the first run — a baseline will be set automatically.

### 3. Get a Telegram Bot Token

1. Open Telegram and search for [@BotFather](https://t.me/BotFather).
2. Send `/newbot` and follow the prompts to create a new bot.
3. Copy the **token** (looks like `123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11`).

### 4. Get your Chat ID

1. Start a chat with your new bot and send any message.
2. Open a browser and visit:
   ```
   https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates
   ```
3. Look for `"chat":{"id":123456789}` — that number is your **Chat ID**.

> Alternatively, message [@userinfobot](https://t.me/userinfobot) on Telegram to get your Chat ID instantly.

### 5. Add repository secrets

In your fork on GitHub, go to **Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Value |
|--------|-------|
| `TELEGRAM_TOKEN` | Your bot token from BotFather |
| `TELEGRAM_CHAT_ID` | Your chat ID from step 4 |

### 6. Run the workflow

- Go to the **Actions** tab in your fork.
- Select **Monitor Trip.com Hotel Prices** and click **Run workflow** → **Run workflow**.
- The workflow will run immediately and then every 6 hours automatically.

### 7. Get notified

- First run sets a **baseline** price.
- Subsequent runs send a **Price Drop** alert if a lower price is found.
- The workflow commits updated prices back to the repo.

## How it works

- Uses Playwright (headless Chromium) to scrape Trip.com hotel pages.
- Locates the room card by room name and extracts the lowest available price.
- Compares against the stored lowest price in `prices.json`.
- Sends Telegram alerts via the Bot API.
- Only updates the stored price when a new lower price is found.
