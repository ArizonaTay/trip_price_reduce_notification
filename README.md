# Trip.com Price Drop Monitor

Monitors hotel room prices on Trip.com and sends Telegram notifications when prices drop.

## Setup

1. Clone the repo and install dependencies:
   ```
   pip install -r requirements.txt
   playwright install chromium
   ```

2. Add your hotels to `prices.json` with the Trip.com URL, room name, and dates.
   - Set `last_price` to `null` for the first run (baseline will be set automatically).

3. Create a `.env` file:
   ```
   TELEGRAM_TOKEN=your_bot_token
   TELEGRAM_CHAT_ID=your_chat_id
   ```

4. Run manually:
   ```
   python scraper.py
   ```

## How it works

- Scrapes Trip.com hotel pages using Playwright (headless Chromium).
- Locates the room card by room name and extracts the **lowest available price** within that card (handles strikethrough prices, member discounts, etc.).
- Compares against the stored lowest price in `prices.json`.
- Sends Telegram alerts:
  - **Baseline Set** on first successful scrape
  - **Price Drop** when a lower price is found
- Only updates the stored price when a new lower price is found, so you always track the lowest seen price.
- Error messages are HTML-escaped to prevent Telegram parse failures.

## GitHub Actions

A workflow (`.github/workflows/monitor.yml`) runs every 6 hours automatically. Set these repository secrets:

- `TELEGRAM_TOKEN` — your bot token
- `TELEGRAM_CHAT_ID` — your chat ID

When a new lower price is detected, the workflow commits the updated `prices.json` back to the repo.

### Important notes

- The workflow correctly uses `git diff --quiet HEAD` to detect changes (not `git diff --quiet`) so that `prices.json` is properly committed after each run.
