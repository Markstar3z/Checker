# Telegram Submission Tracker

A Railway-hosted Telegram bot that quietly records qualifying X post links in a group and sends reports to an authorized user's private Telegram chat.

## Features
- Event-driven collection of group messages; report calculation happens only when requested.
- Recognizes X/Twitter links marked `first` or `second`, before or after the link.
- X link without a marker, or with both markers, is `Unspecified`.
- Ignores ordinary text and non-X links.
- Sends reports only to the authorized user's private DM, never the group.
- Commands: `/start`, `/help`, `/groups`, `/report`, `/report yesterday`, `/report YYYY-MM-DD`, `/report GROUP_ID [date]`, `/addmember GROUP_ID USER_ID NAME`.
- SQLite storage on a Railway persistent volume mounted at `/data`.

## Telegram limitations
- Collection begins only after the bot is added and its webhook is active; no historical backfill.
- In BotFather, use `/setprivacy` and disable Group Privacy, or grant suitable admin access, so ordinary group messages reach the bot.
- Telegram's Bot API does not provide a complete ordinary group member roster. To identify members who submitted nothing, add expected members manually with `/addmember GROUP_ID USER_ID NAME`. Without a roster, the report cannot reliably identify non-submitters.
- You must open the bot's private chat and send `/start` once before it can DM you.
- Set `AUTHORIZED_USER_IDS` to your numeric Telegram user ID. Multiple IDs may be comma-separated.
- This release uses the same First/Second rule for every group; data is group-scoped for future per-group rules.

## Deploy to Railway
1. Create a bot with `@BotFather`.
2. Push this folder to a **private** GitHub repository.
3. Create a Railway project from that repository.
4. Add a Railway Volume mounted at `/data`.
5. Set `BOT_TOKEN`, `WEBHOOK_SECRET` (32+ characters using letters, numbers, `_`, `-`), `AUTHORIZED_USER_IDS`, optional `DEFAULT_GROUP_ID`, `DATABASE_PATH=/data/tracker.db`, `TIMEZONE=Africa/Lagos`, and `PUBLIC_URL` (Railway HTTPS domain).
6. Deploy, then run `python set_webhook.py` once using the same variables.
7. Disable Group Privacy in BotFather and add the bot to your group.
8. Open the bot's private chat and send `/start`; use `/groups` to confirm the group is detected.
9. Test with `First https://x.com/example/status/123` and `https://x.com/example/status/456 second`, then request `/report` privately.

## Local tests
```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
# Set BOT_TOKEN, WEBHOOK_SECRET and AUTHORIZED_USER_IDS
pytest -q
uvicorn app.main:app --reload
```
Unit tests do not verify Telegram delivery, bot permissions, webhook reachability, or Railway networking. Verify these in a private test group before production use.

## Security
Never commit `.env`, bot tokens, or webhook secrets. Keep GitHub private and store secrets in Railway variables. Rotate the bot token with BotFather if exposed.
