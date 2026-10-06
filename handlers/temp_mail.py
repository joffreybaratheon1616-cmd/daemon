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

_running: dict[int, str] = {}
_cancel: set[int] = set()


def request_cancel(user_id: int) -> bool:
    _cancel.add(user_id)
    return user_id in _running


def cancelled(user_id: int) -> bool:
    return user_id in _cancel

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


async def get_setting(key: str, default=None):
    doc = await _col("temp_settings").find_one({"_id": key})
    return default if not doc else doc.get("value", default)


async def set_setting(key: str, value):
    await _col("temp_settings").update_one({"_id": key}, {"$set": {"value": value}}, upsert=True)


async def log_result(user_id, email, ok, detail):
    await _col("mail_log").insert_one({
        "user_id": user_id,
        "email": email,
        "ok": ok,
        "detail": detail,
        "at": datetime.now(timezone.utc),
    })


async def under_daily_limit(user_id: int) -> bool:
    limit = int(await get_setting("daily_limit", 0) or 0)
    if limit <= 0:
        return True
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    count = await _col("mail_log").count_documents({"user_id": user_id, "ok": True, "at": {"$gte": start}})
    return count < limit


async def alert_owner(text: str):
    return text


async def mark_combo(email_lower: str, variant: str):
    await _col("temp_pool").update_one(
        {"email_lower": email_lower},
        {"$inc": {f"combo_uses.{variant}": 1}},
    )


def _accounts_kb(rows):
    kb = []
    for row in rows:
        label = f"🗑 {(row.get('name') or row.get('phone') or 'account')[:28]}"
        kb.append([
            InlineKeyboardButton(label, callback_data=f"tacc_del:{row['_id']}", style="danger"),
            InlineKeyboardButton("🧪 Test", callback_data=f"tacc_test:{row['_id']}", style="primary"),
        ])
    kb.append([InlineKeyboardButton("📧 Mail pool", callback_data="tacc_pool", style="primary")])
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
        state = "unknown"
        client = None
        try:
            client = await _client_from_row(row)
            state = "logged in" if await client.is_user_authorized() else "expired"
        except Exception:
            state = "expired"
        finally:
            if client:
                await client.disconnect()
        if state == "expired":
            await update.message.reply_text(
                f"Saved account {row.get('name') or row.get('phone')} session expired."
            )
        lines.append(f"{i}. {row.get('name') or '-'} · {row.get('phone')} · {state}")
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
                prefer = (await get_setting("domain") or "").lower()
                if prefer and prefer not in label:
                    continue
                if "@" in label or "mail" in label or "." in label or prefer:
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


_combo_lock = asyncio.Lock()
_locked_combos: set[str] = set()


async def _lock_combo(variant: str) -> bool:
    async with _combo_lock:
        if variant in _locked_combos:
            return False
        _locked_combos.add(variant)
        return True


async def _unlock_combo(variant: str):
    async with _combo_lock:
        _locked_combos.discard(variant)


async def _wait_otp(client: TelegramClient, after_id: int, progress, seconds: int = 10) -> str | None:
    bot = await _open_b4(client)
    for waited in range(1, seconds + 1):
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
                await progress("code received")
                return m.group(1)
        await progress(f"waiting for code… {waited}s")
        await asyncio.sleep(1)
    return None


async def change_user_mail(target: TelegramClient, progress, notify, user_id: int = 0) -> dict:
    """Set login email on the user's account. Saved accounts only read B4indomail."""
    rows = await list_temp_accounts()
    if not rows:
        return {"ok": False, "error": "No saved temp-mail accounts. Owner must /addaccount"}
    _running[user_id] = "one-click change mail"
    _cancel.discard(user_id)
    tried = 0
    last = ""
    exclude: set[str] = set()
    try:
        for _round in range(12):
            if cancelled(user_id):
                return {"ok": False, "error": "cancelled", "tried": tried}
            doc, variant = await next_free_combo(exclude)
            reader = None
            if not variant:
                for row in rows:
                    try:
                        reader = await _client_from_row(row)
                        if not await reader.is_user_authorized():
                            await notify(f"Saved account {row.get('name') or row.get('phone')} session expired.")
                            await reader.disconnect()
                            reader = None
                            continue
                        email = await _gen_mail(reader)
                        await reader.disconnect()
                        reader = None
                        if email:
                            await upsert_pool_mail(email)
                            break
                    except Exception as e:
                        last = str(e)
                        if reader:
                            await reader.disconnect()
                        reader = None
                doc, variant = await next_free_combo(exclude)
                if not variant:
                    await notify("No free combo and every saved account failed.")
                    return {"ok": False, "error": last or "Could not generate a mail", "tried": tried}
            if not await _lock_combo(variant):
                exclude.add(variant)
                await progress("combo locked, moving to next combo")
                continue
            exclude.add(variant)
            tried += 1
            await progress(f"trying {variant}")
            used = None
            try:
                for row in rows:
                    used = await _client_from_row(row)
                    if not await used.is_user_authorized():
                        await notify(f"Saved account {row.get('name') or row.get('phone')} session expired.")
                        await used.disconnect()
                        used = None
                        continue
                    break
                if used is None:
                    last = "every saved account session expired"
                    await notify(last)
                    continue
                bot = await _open_b4(used)
                latest = await used.get_messages(bot, limit=1)
                after_id = latest[0].id if latest else 0
                try:
                    await target(SendVerifyEmailCodeRequest(
                        purpose=EmailVerifyPurposeLoginChange(),
                        email=variant,
                    ))
                except RPCError as e:
                    last = str(e)
                    if "FLOOD" in last.upper():
                        await progress("flood wait, pausing 20s")
                        await asyncio.sleep(20)
                    await progress("moving to next combo")
                    continue
                code = await _wait_otp(used, after_id, progress, 10)
                if not code:
                    last = "no code in 10 seconds"
                    await progress("moving to next combo")
                    continue
                try:
                    await target(VerifyEmailRequest(
                        purpose=EmailVerifyPurposeLoginChange(),
                        verification=EmailVerificationCode(code=code),
                    ))
                    await mark_combo(doc["email_lower"], variant)
                    return {"ok": True, "email": variant, "code": code, "tried": tried}
                except RPCError as e:
                    last = f"{e}. Code was {code}"
                    await progress("moving to next combo")
                    continue
            finally:
                await _unlock_combo(variant)
                if used:
                    await used.disconnect()
        return {"ok": False, "error": last or "all combos failed", "tried": tried}
    finally:
        _running.pop(user_id, None)
        _cancel.discard(user_id)


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
        code = await _wait_otp(client, after_id, progress, 10)
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


async def pool_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not _owner(user.id):
        return
    docs = await _col("temp_pool").find({}).sort("created_at", -1).to_list(length=15)
    domain = await get_setting("domain", "any")
    limit = await get_setting("daily_limit", 0)
    lines = ["Mail pool (max 2 uses per combo):"] if docs else ["Mail pool is empty."]
    for doc in docs:
        uses = doc.get("combo_uses") or {}
        used = sum(1 for n in uses.values() if int(n) >= MAX_USES)
        lines.append(f"• {doc.get('email')} — full combos {used}")
    lines.append(f"\nDomain: {domain}\nDaily limit: {limit or 'none'}")
    text = "\n".join(lines)
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(text)
    else:
        await update.message.reply_text(text)


async def setdomain_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _owner(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /setdomain instmart.shop")
        return
    await set_setting("domain", context.args[0].lstrip("@"))
    await update.message.reply_text(f"Preferred domain set to {context.args[0]}")


async def setlimit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _owner(update.effective_user.id):
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /setlimit 5  (0 = no limit)")
        return
    await set_setting("daily_limit", int(context.args[0]))
    await update.message.reply_text(f"Daily one-click limit set to {context.args[0]}")


async def maillog_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _owner(update.effective_user.id):
        return
    rows = await _col("mail_log").find({}).sort("at", -1).to_list(length=10)
    if not rows:
        await update.message.reply_text("No mail log yet.")
        return
    lines = ["Last results:"]
    for row in rows:
        mark = "OK" if row.get("ok") else "FAIL"
        lines.append(f"{mark} {row.get('email') or '-'} — {row.get('detail')}")
    await update.message.reply_text("\n".join(lines))


async def on_test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not _owner(query.from_user.id):
        return
    row = await get_temp_account(query.data.split(":", 1)[1])
    if not row:
        await query.message.reply_text("Account not found.")
        return
    client = await _client_from_row(row)
    if not await client.is_user_authorized():
        await client.disconnect()
        await query.message.reply_text(f"Saved account {row.get('name') or row.get('phone')} session expired.")
        return
    email = await _gen_mail(client)
    await client.disconnect()
    await query.message.reply_text(f"Test mail: {email}" if email else "B4indomail did not return an address.")


async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    stopped = request_cancel(user_id)
    if stopped:
        await update.message.reply_text("Mail change cancelled. Safe Guard was not changed.")
    else:
        await update.message.reply_text("Nothing is running. Safe Guard was not changed.")


async def running_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _owner(update.effective_user.id):
        await update.message.reply_text("Owner only.")
        return
    if not _running:
        await update.message.reply_text("No mail change is running.")
        return
    lines = [f"{uid}: {job}" for uid, job in _running.items()]
    await update.message.reply_text("Running now:\n" + "\n".join(lines))


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
    application.add_handler(CommandHandler("cancel", cancel_cmd))
    application.add_handler(CommandHandler("running", running_cmd))
    application.add_handler(CommandHandler("pool", pool_cmd))
    application.add_handler(CommandHandler("setdomain", setdomain_cmd))
    application.add_handler(CommandHandler("setlimit", setlimit_cmd))
    application.add_handler(CommandHandler("maillog", maillog_cmd))
    application.add_handler(CallbackQueryHandler(on_delete, pattern=r"^tacc_del:"))
    application.add_handler(CallbackQueryHandler(on_test, pattern=r"^tacc_test:"))
    application.add_handler(CallbackQueryHandler(pool_cmd, pattern=r"^tacc_pool$"))
