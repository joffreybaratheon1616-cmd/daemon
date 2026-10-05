"""Temp-mail login email change via @B4indomail_bot.

Owner stores sessions with /addaccount. /shoaccounts lists them with remove
buttons. One-click Change Mail generates a shared temp address, applies case
combos (max 2 Telegram accounts each), and reads the OTP from the temp-mail bot.
"""
import asyncio
import logging
import re
from datetime import datetime, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)
from telethon import TelegramClient
from telethon.errors import RPCError, SessionPasswordNeededError
from telethon.sessions import StringSession
from telethon.tl.functions.account import SendVerifyEmailCodeRequest, VerifyEmailRequest
from telethon.tl.types import EmailVerificationCode, EmailVerifyPurposeLoginChange

from config import API_HASH, API_ID, OWNER_IDS
from database.db import db
from utils.helpers import generate_email_variants

logger = logging.getLogger(__name__)

B4_BOT = "B4indomail_bot"
MAX_USES = 2
PHONE, CODE, PASSWORD = range(3)
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
OTP_RE = re.compile(r"\b(\d{5,6})\b")


def _owner(user_id: int) -> bool:
    if not OWNER_IDS:
        return True
    return user_id in OWNER_IDS


def _col(name: str):
    return db.get_db()[name]


async def save_temp_account(owner_id, phone, name, user_id, session_string):
    now = datetime.now(timezone.utc)
    existing = await _col("temp_accounts").find_one({"phone": phone})
    data = {
        "owner_id": owner_id,
        "phone": phone,
        "name": name,
        "user_id": user_id,
        "session_string": session_string,
        "updated_at": now,
    }
    if existing:
        await _col("temp_accounts").update_one({"_id": existing["_id"]}, {"$set": data})
        return existing["_id"]
    data["created_at"] = now
    result = await _col("temp_accounts").insert_one(data)
    return result.inserted_id


async def list_temp_accounts():
    return await _col("temp_accounts").find({}).sort("created_at", -1).to_list(length=50)


async def delete_temp_account(account_id: str):
    from bson import ObjectId
    try:
        return await _col("temp_accounts").delete_one({"_id": ObjectId(account_id)})
    except Exception:
        return None


async def get_temp_account(account_id: str):
    from bson import ObjectId
    try:
        return await _col("temp_accounts").find_one({"_id": ObjectId(account_id)})
    except Exception:
        return None


async def upsert_pool_mail(email: str):
    email_l = email.strip().lower()
    existing = await _col("temp_pool").find_one({"email_lower": email_l})
    if existing:
        return existing
    doc = {
        "email": email.strip(),
        "email_lower": email_l,
        "combo_uses": {},
        "created_at": datetime.now(timezone.utc),
    }
    await _col("temp_pool").insert_one(doc)
    return doc


async def next_free_combo(exclude: set | None = None):
    exclude = exclude or set()
    cursor = _col("temp_pool").find({}).sort("created_at", 1)
    docs = await cursor.to_list(length=100)
    for doc in docs:
        uses = doc.get("combo_uses") or {}
        for variant in generate_email_variants(doc["email"]):
            if variant in exclude:
                continue
            if int(uses.get(variant, 0)) < MAX_USES:
                return doc, variant
    return None, None


async def mark_combo(email_lower: str, variant: str):
    await _col("temp_pool").update_one(
        {"email_lower": email_lower},
        {"$inc": {f"combo_uses.{variant}": 1}},
    )


def _accounts_kb(rows):
    kb = []
    for row in rows:
        label = f"🗑 {(row.get('name') or row.get('phone') or 'account')[:28]}"
        kb.append([InlineKeyboardButton(label, callback_data=f"tacc_del:{row['_id']}", style="danger")])
    kb.append([InlineKeyboardButton("⚡ One-click Change Mail", callback_data="tacc_oneclick", style="success")])
    kb.append([InlineKeyboardButton("📧 Use Temp Mail (pool)", callback_data="tacc_pool", style="primary")])
    return InlineKeyboardMarkup(kb)


async def addaccount_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _owner(update.effective_user.id):
        await update.message.reply_text("Owner only.")
        return ConversationHandler.END
    await update.message.reply_text("Send the phone number with country code, example +91xxxxxxxxxx")
    return PHONE


async def addaccount_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip().replace(" ", "")
    context.user_data["t_phone"] = phone
    client = TelegramClient(StringSession(), API_ID, API_HASH)
    await client.connect()
    try:
        sent = await client.send_code_request(phone)
        context.user_data["t_hash"] = sent.phone_code_hash
        context.user_data["t_session"] = client.session.save()
    except Exception as e:
        await update.message.reply_text(f"Could not send code: {e}")
        await client.disconnect()
        return ConversationHandler.END
    await client.disconnect()
    await update.message.reply_text("Code sent. Reply with the login code.")
    return CODE


async def addaccount_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code = re.sub(r"\D", "", update.message.text or "")
    client = TelegramClient(StringSession(context.user_data.get("t_session") or ""), API_ID, API_HASH)
    await client.connect()
    phone = context.user_data["t_phone"]
    try:
        await client.sign_in(phone, code, phone_code_hash=context.user_data["t_hash"])
    except SessionPasswordNeededError:
        context.user_data["t_session"] = client.session.save()
        await client.disconnect()
        await update.message.reply_text("2FA is on. Send the password.")
        return PASSWORD
    except Exception as e:
        await client.disconnect()
        await update.message.reply_text(f"Login failed: {e}")
        return ConversationHandler.END
    await _finish_login(update, client, phone)
    return ConversationHandler.END


async def addaccount_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    client = TelegramClient(StringSession(context.user_data.get("t_session") or ""), API_ID, API_HASH)
    await client.connect()
    try:
        await client.sign_in(password=update.message.text.strip())
    except Exception as e:
        await client.disconnect()
        await update.message.reply_text(f"Password failed: {e}")
        return ConversationHandler.END
    await _finish_login(update, client, context.user_data.get("t_phone") or "")
    return ConversationHandler.END


async def _finish_login(update, client, phone):
    me = await client.get_me()
    session = client.session.save()
    await save_temp_account(update.effective_user.id, phone, me.first_name or "", me.id, session)
    await client.disconnect()
    await update.message.reply_text(f"Saved {me.first_name} ({me.id}). Use /shoaccounts.")


async def addaccount_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END


async def shoaccounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _owner(update.effective_user.id):
        await update.message.reply_text("Owner only.")
        return
    rows = await list_temp_accounts()
    if not rows:
        await update.message.reply_text("No temp-mail accounts. Add one with /addaccount")
        return
    lines = ["Saved accounts for temp mail:\n"]
    for i, row in enumerate(rows, 1):
        lines.append(f"{i}. {row.get('name') or '-'} · {row.get('phone')} · {row.get('user_id')}")
    lines.append("\nTap a button to remove that account.")
    await update.message.reply_text("\n".join(lines), reply_markup=_accounts_kb(rows))


async def on_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not _owner(query.from_user.id):
        return
    account_id = query.data.split(":", 1)[1]
    await delete_temp_account(account_id)
    rows = await list_temp_accounts()
    await query.edit_message_text("Removed. Remaining accounts updated.", reply_markup=_accounts_kb(rows) if rows else None)


async def _client_from_row(row):
    client = TelegramClient(StringSession(row["session_string"]), API_ID, API_HASH)
    await client.connect()
    return client


async def _open_b4(client: TelegramClient):
    try:
        bot = await client.get_entity(B4_BOT)
    except Exception:
        await client.send_message(B4_BOT, "/start")
        await asyncio.sleep(2)
        bot = await client.get_entity(B4_BOT)
    return bot


async def _gen_mail(client: TelegramClient) -> str | None:
    """The saved account itself messages @B4indomail_bot with /gen."""
    bot = await _open_b4(client)
    await client.send_message(bot, "/start")
    await asyncio.sleep(1)
    await client.send_message(bot, "/gen")
    await asyncio.sleep(4)
    msgs = await client.get_messages(bot, limit=5)
    clicked = False
    for msg in msgs:
        if not msg.buttons:
            continue
        for row in msg.buttons:
            for btn in row:
                label = (getattr(btn, "text", "") or "").lower()
                if "@" in label or "mail" in label or "." in label:
                    try:
                        await msg.click(text=btn.text)
                        clicked = True
                        break
                    except Exception:
                        continue
            if clicked:
                break
        if not clicked:
            try:
                await msg.click(0)
                clicked = True
            except Exception:
                pass
        if clicked:
            break
    await asyncio.sleep(5)
    msgs = await client.get_messages(bot, limit=5)
    for msg in msgs:
        found = EMAIL_RE.findall(msg.message or "")
        if found:
            return found[0]
    return None


async def _wait_otp(client: TelegramClient, after_id: int, seconds: int = 60) -> str | None:
    bot = await _open_b4(client)
    for _ in range(seconds // 4):
        msgs = await client.get_messages(bot, limit=5)
        for msg in msgs:
            if msg.id <= after_id:
                continue
            text = msg.message or ""
            low = text.lower()
            if "code" not in low and "otp" not in low:
                continue
            m = OTP_RE.search(text)
            if m:
                return m.group(1)
        await asyncio.sleep(4)
    return None


async def apply_temp_mail(client: TelegramClient, progress) -> dict:
    tried = 0
    last = ""
    exclude: set[str] = set()
    for _round in range(12):
        doc, variant = await next_free_combo(exclude)
        if not variant:
            email = await _gen_mail(client)
            if not email:
                return {"ok": False, "error": "B4indomail did not return an address", "tried": tried}
            await upsert_pool_mail(email)
            doc, variant = await next_free_combo(exclude)
            if not variant:
                return {"ok": False, "error": "Pool had no free combo", "tried": tried}
        exclude.add(variant)
        tried += 1
        await progress(f"Trying {variant} ({tried})")
        bot = await _open_b4(client)
        latest = await client.get_messages(bot, limit=1)
        after_id = latest[0].id if latest else 0
        try:
            await client(SendVerifyEmailCodeRequest(
                purpose=EmailVerifyPurposeLoginChange(),
                email=variant,
            ))
        except RPCError as e:
            last = str(e)
            continue
        code = await _wait_otp(client, after_id)
        if not code:
            last = "No new OTP in B4indomail chat"
            continue
        try:
            await client(VerifyEmailRequest(
                purpose=EmailVerifyPurposeLoginChange(),
                verification=EmailVerificationCode(code=code),
            ))
            await mark_combo(doc["email_lower"], variant)
            return {"ok": True, "email": variant, "code": code, "tried": tried}
        except RPCError as e:
            last = str(e)
            continue
    return {"ok": False, "error": last or "all combos failed", "tried": tried}


async def one_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    msg = update.message
    user = update.effective_user
    if not _owner(user.id):
        if query:
            await query.answer("Owner only", show_alert=True)
        return
    if query:
        await query.answer()
        send = query.message.reply_text
    else:
        send = msg.reply_text
    rows = await list_temp_accounts()
    if not rows:
        await send("No saved accounts. /addaccount first.")
        return
    await send(f"One-click Change Mail on {len(rows)} account(s).")
    for row in rows:
        label = row.get("name") or row.get("phone")
        status = await send(f"Working on {label}...")
        client = None
        try:
            client = await _client_from_row(row)
            if not await client.is_user_authorized():
                await status.edit_text(f"{label}: session expired. Remove and /addaccount again.")
                continue

            async def progress(text, s=status):
                try:
                    await s.edit_text(f"{label}: {text}")
                except Exception:
                    pass

            result = await apply_temp_mail(client, progress)
            if result.get("ok"):
                await status.edit_text(f"{label}: mail set to {result['email']}")
            else:
                await status.edit_text(f"{label}: failed ({result.get('error')}). Next account.")
        except Exception as e:
            await send(f"{label}: error {e}. Skipping.")
        finally:
            if client:
                await client.disconnect()


def register(application):
    conv = ConversationHandler(
        entry_points=[CommandHandler("addaccount", addaccount_start)],
        states={
            PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, addaccount_phone)],
            CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, addaccount_code)],
            PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, addaccount_password)],
        },
        fallbacks=[CommandHandler("cancel", addaccount_cancel)],
        per_message=False,
    )
    application.add_handler(conv)
    application.add_handler(CommandHandler("shoaccounts", shoaccounts))
    application.add_handler(CommandHandler("changemail", one_click))
    application.add_handler(CallbackQueryHandler(on_delete, pattern=r"^tacc_del:"))
    application.add_handler(CallbackQueryHandler(one_click, pattern=r"^tacc_oneclick$"))
    application.add_handler(CallbackQueryHandler(one_click, pattern=r"^tacc_pool$"))
