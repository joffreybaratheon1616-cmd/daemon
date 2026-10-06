"""Per-user predefined 2FA applied on hex login. No recovery email."""
import logging
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

from database.db import db
from keyboards.inline import main_menu_kb
from utils.helpers import safe_edit

logger = logging.getLogger(__name__)
HINT = "PREDEFINED 2FA"
WAIT_PASSWORD = 1


def _col():
    return db.get_db()["predefined_2fa"]


async def get_predefined(user_id: int) -> str | None:
    doc = await _col().find_one({"user_id": user_id})
    if not doc:
        return None
    return doc.get("password") or None


async def save_predefined(user_id: int, password: str):
    await _col().update_one(
        {"user_id": user_id},
        {"$set": {"password": password, "updated_at": datetime.now(timezone.utc)}},
        upsert=True,
    )


async def clear_predefined(user_id: int):
    await _col().delete_one({"user_id": user_id})


def menu_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add new 2FA", callback_data="p2fa_add", style="success")],
        [InlineKeyboardButton("🗑 Remove current 2FA", callback_data="p2fa_remove", style="danger")],
        [InlineKeyboardButton("🔙 Back", callback_data="back_main", style="primary")],
    ])


async def apply_predefined_2fa(client, user_id: int) -> str:
    """Set the saved password on this account. Returns a short status line."""
    password = await get_predefined(user_id)
    if not password:
        return ""
    try:
        await client.edit_2fa(new_password=password, hint=HINT, email=None)
        return "🔐 Predefined 2FA added. Hint: PREDEFINED 2FA. No recovery email."
    except Exception as e:
        logger.info("predefined 2FA not applied: %s", e)
        return f"⚠️ Predefined 2FA was not added: {e}"


async def open_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    saved = await get_predefined(query.from_user.id)
    state = "A password is saved. Add new replaces it." if saved else "No predefined 2FA. Hex login will not add 2FA."
    await safe_edit(
        query,
        f"🔐 Predefined 2FA\n\n{state}\n\nHint on the account will be PREDEFINED 2FA. No recovery email is set.",
        reply_markup=menu_kb(),
    )
    return ConversationHandler.END


async def ask_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await safe_edit(
        query,
        "Send the new 2FA password. If you already have one, this replaces it.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="p2fa_menu", style="primary")]]),
    )
    return WAIT_PASSWORD


async def receive_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    password = (update.message.text or "").strip()
    if len(password) < 1:
        await update.message.reply_text("Send a password.")
        return WAIT_PASSWORD
    await save_predefined(update.effective_user.id, password)
    await update.message.reply_text(
        "Saved. The old predefined 2FA was replaced. Hex login will set this password.",
        reply_markup=main_menu_kb(),
    )
    return ConversationHandler.END


async def remove_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await clear_predefined(query.from_user.id)
    await safe_edit(
        query,
        "Removed. Hex login will not add any 2FA.",
        reply_markup=menu_kb(),
    )
    return ConversationHandler.END


def register(application):
    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(ask_password, pattern=r"^p2fa_add$")],
        states={
            WAIT_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_password)],
        },
        fallbacks=[
            CallbackQueryHandler(open_menu, pattern=r"^p2fa_menu$"),
            CommandHandler("cancel", open_menu),
        ],
        per_message=False,
    )
    application.add_handler(conv)
    application.add_handler(CallbackQueryHandler(open_menu, pattern=r"^p2fa_menu$"))
    application.add_handler(CallbackQueryHandler(remove_password, pattern=r"^p2fa_remove$"))
