import os
import re
import hmac
import sqlite3
import logging
import html
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
from contextlib import contextmanager

import httpx
from fastapi import FastAPI, Request, HTTPException

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("tracker")

BOT_TOKEN = os.environ["BOT_TOKEN"]
WEBHOOK_SECRET = os.environ["WEBHOOK_SECRET"]
AUTHORIZED_USER_IDS = {x.strip() for x in os.getenv("AUTHORIZED_USER_IDS", "").split(",") if x.strip()}
DEFAULT_GROUP_ID = os.getenv("DEFAULT_GROUP_ID", "").strip()
DATABASE_PATH = os.getenv("DATABASE_PATH", "/data/tracker.db")
TIMEZONE = ZoneInfo(os.getenv("TIMEZONE", "Africa/Lagos"))
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

app = FastAPI(title="Telegram Submission Tracker")

URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
FIRST_RE = re.compile(r"\bfirst\b", re.IGNORECASE)
SECOND_RE = re.compile(r"\bsecond\b", re.IGNORECASE)
X_URL_RE = re.compile(r"https?://(?:www\.)?(?:x\.com|twitter\.com)/\S+", re.IGNORECASE)


@contextmanager
def db():
    os.makedirs(os.path.dirname(DATABASE_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT NOT NULL,
            chat_title TEXT,
            user_id TEXT NOT NULL,
            username TEXT,
            display_name TEXT,
            submission_date TEXT NOT NULL,
            kind TEXT NOT NULL CHECK(kind IN ('first','second','unspecified')),
            message_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(chat_id, message_id)
        );
        CREATE TABLE IF NOT EXISTS expected_members (
            chat_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            username TEXT,
            display_name TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY(chat_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS groups_seen (
            chat_id TEXT PRIMARY KEY,
            chat_title TEXT,
            first_seen TEXT NOT NULL
        );
        """)


@app.on_event("startup")
def startup():
    init_db()


async def telegram(method: str, payload: dict):
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(f"{TELEGRAM_API}/{method}", json=payload)
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram API error: {data}")
        return data.get("result")


async def send_message(chat_id, text):
    await telegram("sendMessage", {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    })


def local_date(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, tz=TIMEZONE).date().isoformat()


def classify(text: str, entities: list | None = None) -> str | None:
    raw_text = text or ""
    urls = X_URL_RE.findall(raw_text)
    for entity in entities or []:
        if entity.get("type") == "text_link" and X_URL_RE.match(entity.get("url", "")):
            urls.append(entity["url"])
        elif entity.get("type") == "url":
            offset, length = entity.get("offset", 0), entity.get("length", 0)
            if X_URL_RE.match(raw_text[offset:offset + length]):
                urls.append(raw_text[offset:offset + length])
    if not urls:
        return None
    first = bool(FIRST_RE.search(raw_text))
    second = bool(SECOND_RE.search(raw_text))
    if first and not second:
        return "first"
    if second and not first:
        return "second"
    return "unspecified"


def display_name(user: dict) -> str:
    name = " ".join(x for x in [user.get("first_name"), user.get("last_name")] if x)
    return name or user.get("username") or str(user.get("id", "Unknown"))


def save_submission(message: dict, kind: str):
    chat = message["chat"]
    user = message.get("from") or {}
    if not user or user.get("is_bot"):
        return
    created = datetime.fromtimestamp(message["date"], tz=TIMEZONE)
    with db() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO submissions
            (chat_id, chat_title, user_id, username, display_name, submission_date,
             kind, message_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (str(chat["id"]), chat.get("title", str(chat["id"])), str(user["id"]),
             user.get("username"), display_name(user), created.date().isoformat(),
             kind, str(message["message_id"]), created.isoformat())
        )
        conn.execute(
            """INSERT INTO groups_seen(chat_id, chat_title, first_seen)
            VALUES(?,?,?) ON CONFLICT(chat_id) DO UPDATE SET chat_title=excluded.chat_title""",
            (str(chat["id"]), chat.get("title", str(chat["id"])), created.isoformat())
        )


def parse_report_date(argument: str) -> date:
    today = datetime.now(TIMEZONE).date()
    arg = (argument or "").strip().lower()
    if not arg or arg == "today":
        return today
    if arg == "yesterday":
        return today - timedelta(days=1)
    return date.fromisoformat(arg)


def build_report(chat_id: str, day: date) -> str:
    with db() as conn:
        rows = conn.execute(
            """SELECT user_id, username, display_name, kind, MIN(created_at) AS first_seen
            FROM submissions WHERE chat_id=? AND submission_date=?
            GROUP BY user_id, kind""",
            (chat_id, day.isoformat())
        ).fetchall()
        roster = conn.execute(
            """SELECT user_id, username, display_name FROM expected_members
            WHERE chat_id=? AND active=1""", (chat_id,)
        ).fetchall()
        chat_row = conn.execute(
            "SELECT chat_title FROM groups_seen WHERE chat_id=?", (chat_id,)
        ).fetchone()

    people = {}
    for row in rows:
        uid = row["user_id"]
        p = people.setdefault(uid, {
            "username": row["username"], "name": row["display_name"],
            "first": False, "second": False, "unspecified": False
        })
        p[row["kind"]] = True

    # A maintained/imported roster is required to know who did not submit at all.
    for member in roster:
        uid = member["user_id"]
        people.setdefault(uid, {
            "username": member["username"], "name": member["display_name"],
            "first": False, "second": False, "unspecified": False
        })

    if not roster:
        roster_note = (
            "\n\n<i>Note: No expected-member roster is configured for this group. "
            "The report can show people who submitted, but cannot reliably identify "
            "members who submitted nothing. Add/import the group's member roster to "
            "get a complete missing-members list.</i>"
        )
    else:
        roster_note = ""

    lines = []
    for uid, p in sorted(people.items(), key=lambda item: (item[1]["username"] or item[1]["name"]).lower()):
        label = ("@" + html.escape(p["username"])) if p["username"] else html.escape(p["name"] or "Unknown")
        first = "✅" if p["first"] else "—"
        second = "✅" if p["second"] else "—"
        extra = " ⚠️ Unspecified" if p["unspecified"] else ""
        lines.append(f"• {label}: First {first} | Second {second}{extra}")

    first_count = sum(p["first"] for p in people.values())
    second_count = sum(p["second"] for p in people.values())
    unspecified_count = sum(p["unspecified"] for p in people.values())
    missing_first = sum(not p["first"] for p in people.values()) if roster else None
    missing_second = sum(not p["second"] for p in people.values()) if roster else None
    title = chat_row["chat_title"] if chat_row else chat_id

    summary = [
        f"📊 <b>Submission report</b>",
        f"<b>Group:</b> {html.escape(title or chat_id)}",
        f"<b>Date:</b> {day.strftime('%d %b %Y')} (Africa/Lagos)",
        f"<b>First:</b> {first_count}  |  <b>Second:</b> {second_count}",
        f"<b>Unspecified:</b> {unspecified_count}",
    ]
    if roster:
        summary.append(f"<b>Missing First:</b> {missing_first}  |  <b>Missing Second:</b> {missing_second}")
    summary.append("\n<b>Member breakdown</b>")
    summary.extend(lines or ["No qualifying link submissions recorded."])
    return "\n".join(summary) + roster_note



def is_authorized(user_id) -> bool:
    return str(user_id) in AUTHORIZED_USER_IDS


def known_groups() -> list[dict]:
    with db() as conn:
        return [dict(row) for row in conn.execute(
            "SELECT chat_id, chat_title FROM groups_seen ORDER BY chat_title"
        ).fetchall()]


async def private_help(chat_id):
    await send_message(chat_id,
        "<b>Submission Tracker</b>\n"
        "/report [today|yesterday|YYYY-MM-DD] — report for the only/default group\n"
        "/report GROUP_ID [today|yesterday|YYYY-MM-DD] — choose a group\n"
        "/groups — list groups the bot has seen\n"
        "/addmember GROUP_ID USER_ID NAME — add a person to the expected roster\n"
        "/help — show this help\n\n"
        "Reports are sent only in this private chat. Group messages are collected quietly."
    )


async def handle_private_command(message: dict):
    chat_id = str(message.get("chat", {}).get("id", ""))
    user_id = str((message.get("from") or {}).get("id", ""))
    text = message.get("text") or ""
    parts = text.strip().split(maxsplit=3)
    command = parts[0].split("@")[0].lower() if parts else ""

    if command == "/start":
        if not AUTHORIZED_USER_IDS:
            await send_message(chat_id, "The owner has not configured AUTHORIZED_USER_IDS. Reports remain locked.")
        elif is_authorized(user_id):
            await send_message(chat_id, "Private access enabled. Reports will be delivered only here. Send /help for commands.")
        else:
            await send_message(chat_id, "This bot is private. You are not authorized to view reports.")
        return

    if not is_authorized(user_id):
        await send_message(chat_id, "You are not authorized to use this bot.")
        return
    if command == "/help":
        await private_help(chat_id)
        return
    if command == "/groups":
        groups = known_groups()
        if not groups:
            await send_message(chat_id, "No groups detected yet. Add the bot to your group and let a message arrive.")
        else:
            lines = [f"• {html.escape(g['chat_title'] or 'Group')} — <code>{g['chat_id']}</code>" for g in groups]
            await send_message(chat_id, "<b>Groups seen</b>\n" + "\n".join(lines))
        return
    if command == "/addmember":
        if len(parts) < 4:
            await send_message(chat_id, "Format: /addmember GROUP_ID USER_ID NAME. Expected members must be added manually.")
            return
        group_id, member_id, name = parts[1], parts[2], parts[3]
        with db() as conn:
            exists = conn.execute("SELECT 1 FROM groups_seen WHERE chat_id=?", (group_id,)).fetchone()
            if not exists:
                await send_message(chat_id, "I don't recognize that group ID yet. Use /groups after the bot receives a group message.")
                return
            conn.execute(
                """INSERT INTO expected_members(chat_id,user_id,display_name,active)
                   VALUES(?,?,?,1) ON CONFLICT(chat_id,user_id) DO UPDATE SET
                   display_name=excluded.display_name, active=1""",
                (group_id, member_id, name)
            )
        await send_message(chat_id, f"Added <b>{html.escape(name)}</b> to the expected-member roster.")
        return
    if command == "/report":
        args = parts[1:]
        groups = known_groups()
        group_id, date_arg = None, ""
        if args and args[0].lstrip("-").isdigit():
            group_id = args[0]
            date_arg = args[1] if len(args) > 1 else ""
        else:
            date_arg = args[0] if args else ""
            if DEFAULT_GROUP_ID:
                group_id = DEFAULT_GROUP_ID
            elif len(groups) == 1:
                group_id = groups[0]["chat_id"]
            elif len(groups) > 1:
                await send_message(chat_id, "More than one group is connected. Use /groups, then /report GROUP_ID [date].")
                return
        if not group_id:
            await send_message(chat_id, "No group selected yet. Add the bot to your group first, then use /groups.")
            return
        try:
            day = parse_report_date(date_arg)
        except ValueError:
            await send_message(chat_id, "Use /report [today|yesterday|YYYY-MM-DD] or /report GROUP_ID [date].")
            return
        report = build_report(group_id, day)
        for offset in range(0, len(report), 3900):
            await send_message(chat_id, report[offset:offset + 3900])
        return
    await send_message(chat_id, "Unknown command. Send /help for available commands.")


async def handle_message(message: dict):
    chat = message.get("chat", {})
    if chat.get("type") == "private":
        await handle_private_command(message)
        return
    if chat.get("type") not in ("group", "supergroup"):
        return

    chat_id = str(chat.get("id", ""))
    if not chat_id:
        return
    created = datetime.fromtimestamp(message.get("date", int(datetime.now(TIMEZONE).timestamp())), tz=TIMEZONE)
    with db() as conn:
        conn.execute(
            """INSERT INTO groups_seen(chat_id,chat_title,first_seen) VALUES(?,?,?)
               ON CONFLICT(chat_id) DO UPDATE SET chat_title=excluded.chat_title""",
            (chat_id, chat.get("title", chat_id), created.isoformat())
        )
    text = message.get("text") or message.get("caption") or ""
    if text.strip().startswith("/"):
        return
    kind = classify(text, message.get("entities") or message.get("caption_entities"))
    if kind:
        save_submission(message, kind)


@app.get("/")
async def health():
    return {"status": "ok", "service": "telegram-submission-tracker"}


@app.post("/telegram/{secret}")
async def webhook(secret: str, request: Request):
    if not hmac.compare_digest(secret, WEBHOOK_SECRET):
        raise HTTPException(status_code=403, detail="Forbidden")
    header_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not hmac.compare_digest(header_secret, WEBHOOK_SECRET):
        raise HTTPException(status_code=403, detail="Invalid Telegram secret")
    update = await request.json()
    message = update.get("message") or update.get("edited_message")
    if message:
        try:
            await handle_message(message)
        except Exception:
            log.exception("Failed to process Telegram update")
    return {"ok": True}
