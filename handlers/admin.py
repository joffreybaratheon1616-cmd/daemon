import logging

from telegram import Update
from telegram.ext import ContextTypes, CommandHandler

from config import OWNER_IDS
from database.models import (
    add_sudo_user,
    remove_sudo_user,
    get_all_sudo_users,
    is_sudo_user,
    is_authorized,
    save_mail,
    get_mail,
    get_all_mails,
    remove_mail,
)
from keyboards.inline import admin_back_kb
from utils.helpers import verify_mail, denied_text

logger = logging.getLogger(__name__)


def is_owner(user_id: int) -> bool:
    return user_id in OWNER_IDS


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show help filtered by the user's role (normal users never see owner commands)."""
    user_id = update.effective_user.id
    if not await is_authorized(user_id):
        await update.message.reply_text(denied_text(update.effective_user.id))
        return

    is_admin = is_owner(user_id)
    is_sudo = await is_sudo_user(user_id)

    text = "📚 <b>Help — Available Commands</b>\n\n"
    text += (
        "<b>👤 Your Features:</b>\n"
        "├─ /start — Start the bot and show main menu\n"
        "├─ /help — Show this help message\n"
        "├─ /cancel — Stop the current mail change or account login. Does not stop Safe Guard\n"
        "├─ <b>Manage Account</b> — connect session, devices, clear, OTP, mail, 2FA, export hex, safe guard\n"
        "├─ <b>Safe / Guard</b> — auto-terminate new logins\n"
        "├─ <b>My Accounts</b> — list saved accounts and Full Operations\n"
        "├─ <b>One-click Change Mail</b> — on the account dashboard and Full Operations\n"
        "├─ /addmail email app_password — Save and verify login mail (Gmail / Outlook / Yahoo)\n"
        "├─ /checkmail — Check saved mail\n"
        "├─ /mymail — View saved mail\n"
        "└─ /rmmail — Remove saved mail\n\n"
        "One-click uses the owner's temp-mail pool. You cannot add or remove those accounts.\n\n"
        "<b>Tip:</b> Send a raw 512-char hex key in DM or group (if you are authorized) and the bot will auto-verify it.\n\n"
    )

    if is_sudo and not is_admin:
        text += (
            "<b>🔧 Sudo Commands:</b>\n"
            "├─ /addsudo userid — Add a sudo user\n"
            "├─ /rmsudo userid — Remove a sudo user\n"
            "└─ /sudolist — List all sudo users\n\n"
        )

    if is_admin:
        text += (
            "<b>👑 Owner Commands:</b>\n"
            "├─ /access user_id — Control another user's stored accounts\n"
            "├─ /addaccount — Save an account that talks to @B4indomail_bot\n"
            "├─ /shoaccounts — List those accounts, remove or test one\n"
            "├─ /pool — Temp-mail pool and combo use\n"
            "├─ /setdomain instmart.shop — Preferred /gen domain\n"
            "├─ /setlimit 5 — Daily one-click limit (0 = none)\n"
            "├─ /maillog — Last mail-change results\n"
            "├─ /running — Who is changing mail right now\n"
            "├─ /addsudo userid — Add a sudo user\n"
            "├─ /rmsudo userid — Remove a sudo user\n"
            "└─ /sudolist — List all sudo users\n\n"
        )

    await update.message.reply_text(text, parse_mode="HTML", reply_markup=admin_back_kb())


async def add_sudo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_owner(user_id):
        await update.message.reply_text("❌ Only owners can use this command.")
        return

    target_id = None
    if update.message.reply_to_message:
        target_id = update.message.reply_to_message.from_user.id
    elif context.args:
        try:
            target_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("❌ Invalid user ID. Use: `/addsudo 123456789`", parse_mode="Markdown")
            return

    if not target_id:
        await update.message.reply_text("❌ Specify a user ID or reply to a user. Use: `/addsudo 123456789`", parse_mode="Markdown")
        return
    if target_id == user_id:
        await update.message.reply_text("❌ You're already an owner.")
        return

    success = await add_sudo_user(target_id, user_id)
    if success:
        await update.message.reply_text(f"✅ User `{target_id}` added as sudo user.", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"ℹ️ User `{target_id}` is already a sudo user.", parse_mode="Markdown")


async def remove_sudo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_owner(user_id):
        await update.message.reply_text("❌ Only owners can use this command.")
        return

    if not context.args:
        await update.message.reply_text("❌ Usage: `/rmsudo 123456789`", parse_mode="Markdown")
        return
    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID.")
        return

    success = await remove_sudo_user(target_id)
    if success:
        await update.message.reply_text(f"✅ User `{target_id}` removed from sudo.", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"ℹ️ User `{target_id}` was not a sudo user.", parse_mode="Markdown")


async def sudo_list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not (is_owner(user_id) or await is_sudo_user(user_id)):
        await update.message.reply_text("❌ You don't have permission.")
        return

    sudo_users = await get_all_sudo_users()
    text = "**👑 Owners:**\n"
    for oid in OWNER_IDS:
        text += f"├─ `{oid}` (Owner)\n"

    text += "\n**🔧 Sudo Users:**\n"
    if sudo_users:
        for su in sudo_users:
            text += f"├─ `{su['user_id']}` (added by `{su.get('added_by', '?')}`)\n"
    else:
        text += "├─ _(none)_\n"

    await update.message.reply_text(text, parse_mode="Markdown")


async def add_mail_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Save + verify a login mail. Usage: /addmail email app_password"""
    user_id = update.effective_user.id
    if not await is_authorized(user_id):
        await update.message.reply_text(denied_text(update.effective_user.id))
        return

    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "❌ Usage: `/addmail email@outlook.com your_app_password`\n\n"
            "Supports **Gmail, Outlook/Hotmail/Live, Yahoo**.\n"
            "Example: `/addmail aarabharyan@outlook.com xxxx-xxxx-xxxx-xxxx`\n\n"
            "_Generate an app password from your account security settings "
            "(IMAP must be enabled)._",
            parse_mode="Markdown",
        )
        return

    email = context.args[0]
    app_password = " ".join(context.args[1:])

    if "@" not in email or "." not in email:
        await update.message.reply_text("❌ That doesn't look like a valid email address.")
        return

    status = await update.message.reply_text("🧪 Verifying mail connection...")

    result = await verify_mail(email, app_password)
    if result.get("ok"):
        await save_mail(user_id, email, app_password, verified=True, check_message="OK")
        text = (
            "✅ **Mail saved & verified!**\n\n"
            f"├─ Email: `{email}`\n"
            f"├─ IMAP: {result.get('host')}\n"
            f"├─ Unread: {result.get('unread')}\n"
            f"└─ Telegram emails: {result.get('telegram_emails')}\n\n"
            "_Add more Gmails with `/addmail` again. "
            "Change Mail will try all capitalisation combos "
            "(each usable on 2 accounts) and auto-shift on errors._"
        )
    else:
        await save_mail(user_id, email, app_password, verified=False,
                        check_message=str(result.get("error")))
        text = (
            "⚠️ **Mail saved but verification FAILED.**\n\n"
            f"Email: `{email}`\n\n"
            f"Reason: {result.get('error')}\n\n"
            "_Double-check the app password and that IMAP is enabled._"
        )

    await status.edit_text(text, parse_mode="Markdown")


async def check_mail_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mail checker: /checkmail"""
    user_id = update.effective_user.id
    if not await is_authorized(user_id):
        await update.message.reply_text(denied_text(update.effective_user.id))
        return

    mail = await get_mail(user_id)
    if not mail:
        await update.message.reply_text(
            "❌ No mail saved. Use `/addmail email app_password` first.",
            parse_mode="Markdown",
        )
        return

    email = mail["email"]
    app_password = mail["app_password"]

    status = await update.message.reply_text("🧪 Checking mail...")

    result = await verify_mail(email, app_password)
    if result.get("ok"):
        await save_mail(user_id, email, app_password, verified=True, check_message="OK")
        text = (
            "✅ **Mail Verified**\n\n"
            f"├─ Email: `{email}`\n"
            f"├─ IMAP: {result.get('host')}\n"
            f"├─ Unread: {result.get('unread')}\n"
            f"└─ Telegram emails: {result.get('telegram_emails')}\n"
        )
        if result.get("latest"):
            text += f"\nLatest: `{result['latest']}`"
    else:
        await save_mail(user_id, email, app_password, verified=False,
                        check_message=str(result.get("error")))
        text = (
            "❌ **Mail check failed**\n\n"
            f"Email: `{email}`\n\n"
            f"Reason: {result.get('error')}\n\n"
            "_Fix the credentials with `/addmail`._"
        )

    await status.edit_text(text, parse_mode="Markdown")


async def my_mail_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await is_authorized(user_id):
        await update.message.reply_text(denied_text(update.effective_user.id))
        return

    mail = await get_mail(user_id)
    all_mails = await get_all_mails(user_id)
    if all_mails:
        lines = [f"📧 **Saved Mailboxes ({len(all_mails)})**\n"]
        for i, m in enumerate(all_mails, 1):
            verified = "✅" if m.get("verified") else "⚠️"
            uses = m.get("combo_uses") or {}
            used = sum(1 for c in uses.values() if int(c) > 0)
            lines.append(
                f"{i}. {verified} `{m['email']}` — "
                f"combos used: {used}"
            )
        lines.append(
            "\n_Add more with `/addmail email app_password`. "
            "Each capitalisation combo can be used on up to 2 accounts._"
        )
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    else:
        await update.message.reply_text(
            "❌ No mail saved. Use `/addmail email app_password`", parse_mode="Markdown"
        )


async def remove_mail_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await is_authorized(user_id):
        await update.message.reply_text(denied_text(update.effective_user.id))
        return

    await remove_mail(user_id)
    await update.message.reply_text("✅ Saved mail removed.")


def register(application):
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CommandHandler("addsudo", add_sudo_cmd))
    application.add_handler(CommandHandler("rmsudo", remove_sudo_cmd))
    application.add_handler(CommandHandler("sudolist", sudo_list_cmd))
    application.add_handler(CommandHandler("addmail", add_mail_cmd))
    application.add_handler(CommandHandler("checkmail", check_mail_cmd))
    application.add_handler(CommandHandler("mymail", my_mail_cmd))
    application.add_handler(CommandHandler("rmmail", remove_mail_cmd))
