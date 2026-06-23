# Zepto Split Bot 🛒

A Telegram bot that parses Zepto invoice PDFs, lets you tag personal vs shared items, computes the split, and logs it to Splitwise automatically.

## How It Works

1. Download invoice from Zepto app → send to bot on Telegram
2. Bot shows numbered item list → you reply `mine: 1,2` and `kalash: 3`
3. Bot computes split → you confirm with `ok`
4. Bot creates the expense on Splitwise → Kalash gets notified

## Setup (One-Time, ~15 minutes)

### Step 1: Create Telegram Bot

1. Open Telegram, search for `@BotFather`
2. Send `/newbot`, follow the prompts (name it whatever, e.g. "Zepto Splitter")
3. Copy the bot token — looks like `7123456789:AAH...`

### Step 2: Get Splitwise API Key

1. Go to https://secure.splitwise.com/oauth_clients
2. Click "Register your application"
3. Fill in:
   - **Application name**: Zepto Split Bot
   - **Description**: Personal expense splitter
   - **Homepage URL**: https://example.com (doesn't matter)
   - **Callback URL**: https://example.com (doesn't matter for API key usage)
4. After registering, you'll see your **Consumer Key**, **Consumer Secret**, and an **API Key**
5. Copy the **API Key** — that's all you need

### Step 3: Find Kalash's Splitwise User ID (optional)

The bot will auto-detect Kalash from your friends list if their name contains "kalash".

If it doesn't work, find the ID manually:
1. Go to https://secure.splitwise.com
2. Open browser DevTools (F12) → Console
3. Run: `fetch('/api/v3.0/get_friends').then(r=>r.json()).then(d=>console.log(d.friends.map(f=>f.id+' '+f.first_name+' '+f.last_name)))`
4. Find Kalash's ID in the output

### Step 4: Deploy on Render

1. Push this code to a GitHub repo
2. Go to https://dashboard.render.com → New → Web Service
3. Connect your GitHub repo
4. Settings:
   - **Runtime**: Python
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `python bot.py`
   - **Instance Type**: Free
5. Add Environment Variables:
   - `TELEGRAM_BOT_TOKEN` = your bot token
   - `SPLITWISE_API_KEY` = your API key
   - `SPLITWISE_KALASH_USER_ID` = (optional)
   - `SPLITWISE_GROUP_ID` = (optional)
6. Deploy!

The bot will auto-detect Render and switch to webhook mode.

### Running Locally (for testing)

```bash
cp .env.example .env
# Fill in your values in .env

pip install -r requirements.txt
python bot.py
```

When run locally (no `RENDER_EXTERNAL_URL`), the bot uses polling mode — no webhook setup needed.

## Usage

Send any Zepto invoice PDF to the bot and follow the prompts:

```
You: [send PDF]

Bot: 🛒 Zepto Order — 23-06-2026
     Total: ₹197.01

     1. Amul Fresh Malai Paneer 1 pack (200 g) — ₹95.00
     2. Banana Robusta 3 pcs — ₹45.00
     3. Maggi Masala-ae-Magic Sabzi Masala 1 pack (12 pcs) — ₹57.00

     Tag your items:
     mine: 1,2
     kalash: 3
     (everything else splits 50/50)

You: mine: 1
     kalash: 3

Bot: 📊 Split Summary — 23-06-2026

     🔹 Your items: ₹95.00
        Amul Fresh Malai Paneer
     🔸 Kalash's items: ₹57.00
        Maggi Masala-ae-Magic
     🔀 Shared (50/50): ₹45.00 → ₹22.50 each

     💰 You paid: ₹197.01
     📌 Kalash owes you: ₹79.50

     Type ok to log to Splitwise or cancel to discard.

You: ok

Bot: ✅ Done! Logged to Splitwise.
     Kalash owes you ₹79.50
```

## Notes

- On Render free tier, the bot sleeps after 15 min of inactivity. First message after sleep takes ~30-60 seconds to respond.
- No database needed — the bot is stateless (conversation state is in-memory only).
- Splitwise automatically notifies Kalash when an expense is added.
