"""
Auto-detect and verify raw hex auth keys sent by authorized users
in private chats or groups.
"""
import logging
import re

from telegram import Update
from telegram.ext import ContextTypes, MessageHandler, filters

from config import API_ID, API_HASH
from database.models import save_account, is_authorized
from keyboards.inline import manage_dashboard_kb, main_menu_kb
from utils.helpers import (
    check_spam_status,
    get_devices,
    format_account_info,
    looks_like_hex_session,
    denied_text,
)
from utils.session_utils import verify_and_get_client

logger = logging.getLogger(__name__)

# Only treat pure hex (or hex with 0x) of correct length; ignore short messages
HEX_RE = re.compile(r"^(?:0x)?[0-9a-fA-F]{512}$")


async def auto_hex_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """If an authorized user sends a 512-char hex, auto-verify and show dashboard."""
    if not update.message or not update.message.text:
        return

    text = update.message.text.strip()
    if not looks_like_hex_session(text) and not HEX_RE.match(text):
        return

    user = update.effective_user
    if not user:
        return

    if not await is_authorized(user.id):
        # Silently ignore unauthorized users (don't spam groups)
        return

    # Don't steal messages that belong to an active Manage Account conversation
    if context.user_data.get("current_client") or context.user_data.get("waiting_hex"):
        return

    status = await update.message.reply_text(
        "🔑 **Hex detected** — verifying session…",
        parse_mode="Markdown",
    )

    client, info = await verify_and_get_client(text, API_ID, API_HASH)
    if client is None:
        await status.edit_text(
            f"❌ **Verification Failed**\n\n{info}",
            parse_mode="Markdown",
        )
        return

    try:
        devices = await get_devices(client)
        spam_status = await check_spam_status(client)

        name = f"{info.get('first_name', '')} {info.get('last_name', '')}".strip()
        account_id = await save_account(
            owner_id=user.id,
            hex_key=text.strip(),
            phone=info.get("phone", "Unknown"),
            name=name or "Unknown",
            user_id=info.get("id", 0),
            dc_id=info.get("dc_id", 0),
            session_string=info.get("session_string", ""),
        )

        # Store client so Manage Account style actions work
        context.user_data["current_client"] = client
        context.user_data["current_account_id"] = str(account_id)
        context.user_data["current_user_id"] = info["id"]
        context.user_data["current_phone"] = info.get("phone", "Unknown")
        context.user_data["current_name"] = name or "Unknown"

        dash_text = format_account_info(info)
        dash_text += (
            f"├─ **Devices**  : {len(devices)} connected\n"
            f"├─ **Spam**     : {spam_status}\n"
            f"└─ **Status**   : ✅ **Verified & Connected** (auto)"
        )

        await status.edit_text(
            dash_text,
            parse_mode="Markdown",
            reply_markup=manage_dashboard_kb(),
        )
    except Exception as e:
        logger.exception("auto_hex failed")
        if client and client.is_connected():
            try:
                await client.disconnect()
            except Exception:
                pass
        await status.edit_text(f"❌ **Error:** `{e}`", parse_mode="Markdown")


def register(application):
    # Low priority so ConversationHandlers (Manage Account) get first chance
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.Regex(r"(?i)^(?:0x)?[0-9a-f]{512}$"),
            auto_hex_handler,
        ),
        group=10,
    )
