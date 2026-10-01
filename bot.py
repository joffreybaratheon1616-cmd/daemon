import asyncio
import logging
import os
import secrets
import sys
from datetime import datetime, timezone
from typing import Optional

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ReturnDocument

# ==============================================================================
# CONFIGURATION & ENVIRONMENT VALIDATION
# ==============================================================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")
DB_NAME = os.getenv("DB_NAME", "serbrynden_db")
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "YourSupportHandle")

raw_owner_id = os.getenv("OWNER_ID")
if not BOT_TOKEN or not MONGO_URI or not raw_owner_id:
    print(
        "CRITICAL ERROR: Missing essential environment variables.\n"
        "Ensure BOT_TOKEN, MONGO_URI, and OWNER_ID are properly set in Render."
    )
    sys.exit(1)

try:
    OWNER_ID = int(raw_owner_id.strip())
except ValueError:
    print("CRITICAL ERROR: OWNER_ID must be a valid integer ID (e.g. 123456789).")
    sys.exit(1)

# ==============================================================================
# LOGGING SETUP
# ==============================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("SerBrynden_Robot")

# ==============================================================================
# DATABASE LAYER
# ==============================================================================
client = AsyncIOMotorClient(MONGO_URI)
db = client[DB_NAME]
users_col = db["users"]
keys_col = db["keys"]
accounts_col = db["accounts"]


async def init_db():
    """Ensure essential indexes for high performance and prevent duplicate keys."""
    await keys_col.create_index("hex_key", unique=True)
    await keys_col.create_index("status")
    await users_col.create_index("role")
    await accounts_col.create_index("status")
    logger.info("MongoDB indexes verified.")


async def get_user_role(user_id: int) -> str:
    """Fetch privilege role; owner always returns 'owner'."""
    if user_id == OWNER_ID:
        return "owner"
    user = await users_col.find_one({"_id": user_id})
    return user.get("role", "user") if user else "user"


async def ensure_user(user_id: int, username: Optional[str], first_name: str):
    """Upsert user info on each interaction."""
    role = "owner" if user_id == OWNER_ID else "user"
    await users_col.update_one(
        {"_id": user_id},
        {
            "$set": {
                "username": username,
                "first_name": first_name,
                "updated_at": datetime.now(timezone.utc),
            },
            "$setOnInsert": {
                "role": role,
                "is_banned": False,
                "registered_at": datetime.now(timezone.utc),
                "total_redeemed": 0,
            },
        },
        upsert=True,
    )


# ==============================================================================
# FSM STATES
# ==============================================================================
class AdminStates(StatesGroup):
    waiting_for_stock_items = State()


# ==============================================================================
# ROUTER
# ==============================================================================
router = Router()


# ==============================================================================
# USER / REDEMPTION FLOW
# ==============================================================================
async def process_key_redemption(message: Message, hex_key: str):
    """Atomic key redemption to prevent double-spending."""
    clean_key = hex_key.strip().lower()

    # Atomically lock and claim key
    key_doc = await keys_col.find_one_and_update(
        {"hex_key": clean_key, "status": "unclaimed"},
        {
            "$set": {
                "status": "claimed",
                "claimed_by": message.from_user.id,
                "claimed_at": datetime.now(timezone.utc),
            }
        },
        return_document=ReturnDocument.BEFORE,
    )

    if not key_doc:
        existing = await keys_col.find_one({"hex_key": clean_key})
        if existing:
            if existing.get("status") == "claimed":
                await message.reply(
                    "❌ <b>Key Already Claimed</b>\n"
                    "This activation key has already been redeemed. If you believe this is a mistake, contact support."
                )
            else:
                await message.reply("❌ <b>Key Inactive</b>\nThis activation key has been revoked or expired.")
        else:
            await message.reply("❌ <b>Invalid Key</b>\nPlease make sure you copied the hex key correctly.")
        return

    # Assign an available account matching this key's category
    account_doc = await accounts_col.find_one_and_update(
        {"category": key_doc["category"], "status": "available"},
        {
            "$set": {
                "status": "assigned",
                "assigned_to": message.from_user.id,
                "key_used": clean_key,
                "assigned_at": datetime.now(timezone.utc),
            }
        },
        return_document=ReturnDocument.AFTER,
    )

    if not account_doc:
        # Rollback key status so the buyer doesn't lose it if stock runs out
        await keys_col.update_one(
            {"hex_key": clean_key},
            {"$set": {"status": "unclaimed", "claimed_by": None, "claimed_at": None}},
        )
        await message.reply(
            "⚠️ <b>Out of Stock</b>\n"
            "We are temporarily out of accounts for this category. Your key has <b>not</b> been consumed. "
            "Please notify support or try again in a few minutes."
        )
        return

    # Bind account reference to the key record and increment redeemed counter
    await keys_col.update_one({"hex_key": clean_key}, {"$set": {"account_id": account_doc["_id"]}})
    await users_col.update_one({"_id": message.from_user.id}, {"$inc": {"total_redeemed": 1}})

    login_info = account_doc.get("login", "N/A")
    password_info = account_doc.get("password", "N/A")
    extra_info = account_doc.get("extra", "None")

    response_text = (
        "🎉 <b>Redemption Successful!</b>\n\n"
        f"<b>Product:</b> <code>{account_doc.get('category')}</code>\n"
        f"<b>Activation Key:</b> <code>{clean_key}</code>\n\n"
        "<b>📦 Account Credentials:</b>\n"
        f"<b>Login:</b> <code>{login_info}</code>\n"
        f"<b>Password:</b> <code>{password_info}</code>\n"
        f"<b>Extra / 2FA:</b> <code>{extra_info}</code>\n\n"
        "⚠️ <i>Please change credentials and secure the account immediately. "
        "View past orders anytime using /myorders.</i>"
    )
    await message.reply(response_text)


@router.message(CommandStart())
async def handle_start(message: Message, command: CommandObject):
    await ensure_user(message.from_user.id, message.from_user.username, message.from_user.first_name)

    # Handle deep linking: https://t.me/SerBrynden_Robot?start=<HEX_KEY>
    if command.args:
        await process_key_redemption(message, command.args)
        return

    welcome_text = (
        f"👋 <b>Welcome, {message.from_user.first_name}!</b>\n\n"
        "I am the <b>Ser Brynden Fulfillment Bot</b>. I provide instant delivery for your purchases.\n\n"
        "<b>Available Commands:</b>\n"
        "• <code>/redeem &lt;hex_key&gt;</code> - Claim your account credentials\n"
        "• <code>/myorders</code> - View previously redeemed accounts\n"
        "• <code>/support</code> - Reach out for assistance\n"
    )
    await message.reply(welcome_text)


@router.message(Command("redeem"))
async def handle_redeem(message: Message, command: CommandObject):
    await ensure_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    if not command.args:
        await message.reply("❗ <b>Usage:</b> <code>/redeem &lt;hex_key&gt;</code>")
        return
    await process_key_redemption(message, command.args)


@router.message(Command("myorders"))
async def handle_myorders(message: Message):
    cursor = accounts_col.find({"assigned_to": message.from_user.id}).sort("assigned_at", -1).limit(10)
    orders = await cursor.to_list(length=10)

    if not orders:
        await message.reply("📦 You have no redeemed accounts registered on this profile.")
        return

    lines = ["<b>📋 Your Recent Accounts (Up to 10):</b>\n"]
    for idx, acc in enumerate(orders, 1):
        dt = acc.get("assigned_at")
        date_str = dt.strftime("%Y-%m-%d %H:%M") if dt else "Recent"
        lines.append(
            f"<b>{idx}. {acc.get('category')}</b> ({date_str})\n"
            f"   <b>Login:</b> <code>{acc.get('login')}</code>\n"
            f"   <b>Password:</b> <code>{acc.get('password')}</code>\n"
            f"   <b>Extra:</b> <code>{acc.get('extra', 'None')}</code>\n"
        )

    await message.reply("\n".join(lines))


@router.message(Command("support"))
async def handle_support(message: Message):
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Contact Support", url=f"https://t.me/{SUPPORT_USERNAME}")]]
    )
    await message.reply(
        "Need help with an order, replacement, or inquiry? Tap the button below:",
        reply_markup=kb,
    )


# ==============================================================================
# ADMIN COMMANDS
# ==============================================================================
@router.message(Command("stock"))
async def handle_stock(message: Message):
    role = await get_user_role(message.from_user.id)
    if role not in ["admin", "owner"]:
        return

    acc_counts = await accounts_col.aggregate(
        [{"$match": {"status": "available"}}, {"$group": {"_id": "$category", "count": {"$sum": 1}}}]
    ).to_list(length=100)

    key_counts = await keys_col.aggregate(
        [{"$match": {"status": "unclaimed"}}, {"$group": {"_id": "$category", "count": {"$sum": 1}}}]
    ).to_list(length=100)

    acc_dict = {item["_id"]: item["count"] for item in acc_counts}
    key_dict = {item["_id"]: item["count"] for item in key_counts}
    categories = set(acc_dict.keys()) | set(key_dict.keys())

    if not categories:
        await message.reply("📊 <b>Stock Status:</b> Inventory and key pools are currently empty.")
        return

    res = ["<b>📊 Current Inventory & Stock Levels:</b>\n"]
    for cat in sorted(categories):
        accs = acc_dict.get(cat, 0)
        keys = key_dict.get(cat, 0)
        res.append(f"• <b>{cat}</b>: <code>{accs}</code> accounts | <code>{keys}</code> unclaimed keys")

    await message.reply("\n".join(res))


@router.message(Command("genkey"))
async def handle_genkey(message: Message, command: CommandObject):
    """Usage: /genkey <category> <count>"""
    role = await get_user_role(message.from_user.id)
    if role not in ["admin", "owner"]:
        return

    args = command.args.split() if command.args else []
    if len(args) < 2:
        await message.reply(
            "❗ <b>Usage:</b> <code>/genkey &lt;category&gt; &lt;count&gt;</code>\n"
            "Example: <code>/genkey netflix 5</code>"
        )
        return

    category = args[0]
    try:
        count = int(args[1])
        if count <= 0 or count > 50:
            await message.reply("Please specify a count between 1 and 50.")
            return
    except ValueError:
        await message.reply("Count must be an integer.")
        return

    generated_keys = []
    docs = []
    for _ in range(count):
        hex_key = secrets.token_hex(16)
        generated_keys.append(hex_key)
        docs.append(
            {
                "hex_key": hex_key,
                "category": category,
                "status": "unclaimed",
                "claimed_by": None,
                "claimed_at": None,
                "created_at": datetime.now(timezone.utc),
            }
        )

    await keys_col.insert_many(docs)

    output = [f"✅ Generated <b>{count}</b> keys for <code>{category}</code>:\n"]
    for k in generated_keys:
        output.append(f"<code>{k}</code>")

    await message.reply("\n".join(output))


@router.message(Command("addstock"))
async def handle_addstock(message: Message, command: CommandObject, state: FSMContext):
    """Usage: /addstock <category>"""
    role = await get_user_role(message.from_user.id)
    if role not in ["admin", "owner"]:
        return

    if not command.args:
        await message.reply("❗ <b>Usage:</b> <code>/addstock &lt;category&gt;</code>")
        return

    category = command.args.strip()
    await state.update_data(category=category)
    await state.set_state(AdminStates.waiting_for_stock_items)
    await message.reply(
        f"Send the credentials for category <code>{category}</code>.\n\n"
        "Format per line:\n"
        "<code>login:password</code> OR <code>login:password:extra_info</code>"
    )


@router.message(AdminStates.waiting_for_stock_items)
async def process_stock_lines(message: Message, state: FSMContext):
    data = await state.get_data()
    category = data.get("category")
    lines = message.text.strip().split("\n")

    docs = []
    for line in lines:
        parts = line.strip().split(":", 2)
        if len(parts) >= 2:
            docs.append(
                {
                    "category": category,
                    "login": parts[0].strip(),
                    "password": parts[1].strip(),
                    "extra": parts[2].strip() if len(parts) > 2 else "None",
                    "status": "available",
                    "created_at": datetime.now(timezone.utc),
                }
            )

    if docs:
        await accounts_col.insert_many(docs)
        await message.reply(f"✅ Successfully stocked <b>{len(docs)}</b> accounts into <code>{category}</code>.")
    else:
        await message.reply("❌ Invalid format. Please use <code>login:password</code> or <code>login:password:extra</code>.")

    await state.clear()


@router.message(Command("check"))
async def handle_check(message: Message, command: CommandObject):
    """Usage: /check <hex_key>"""
    role = await get_user_role(message.from_user.id)
    if role not in ["admin", "owner"]:
        return

    if not command.args:
        await message.reply("❗ <b>Usage:</b> <code>/check &lt;hex_key&gt;</code>")
        return

    key = command.args.strip().lower()
    doc = await keys_col.find_one({"hex_key": key})
    if not doc:
        await message.reply("Key not found in database.")
        return

    status = doc.get("status")
    claimed_by = doc.get("claimed_by", "None")
    category = doc.get("category")
    dt = doc.get("claimed_at")
    dt_str = dt.strftime("%Y-%m-%d %H:%M:%S UTC") if dt else "N/A"

    await message.reply(
        f"<b>Key:</b> <code>{key}</code>\n"
        f"<b>Category:</b> {category}\n"
        f"<b>Status:</b> {status}\n"
        f"<b>Claimed By:</b> <code>{claimed_by}</code>\n"
        f"<b>Claimed At:</b> {dt_str}"
    )


# ==============================================================================
# OWNER COMMANDS
# ==============================================================================
@router.message(Command("promote"))
async def handle_promote(message: Message, command: CommandObject):
    """Usage: /promote <user_id>"""
    role = await get_user_role(message.from_user.id)
    if role != "owner":
        return

    if not command.args or not command.args.strip().isdigit():
        await message.reply("❗ <b>Usage:</b> <code>/promote &lt;telegram_user_id&gt;</code>")
        return

    target_id = int(command.args.strip())
    await users_col.update_one({"_id": target_id}, {"$set": {"role": "admin"}}, upsert=True)
    await message.reply(f"✅ User <code>{target_id}</code> is now an <b>Admin</b>.")


@router.message(Command("demote"))
async def handle_demote(message: Message, command: CommandObject):
    """Usage: /demote <user_id>"""
    role = await get_user_role(message.from_user.id)
    if role != "owner":
        return

    if not command.args or not command.args.strip().isdigit():
        await message.reply("❗ <b>Usage:</b> <code>/demote &lt;telegram_user_id&gt;</code>")
        return

    target_id = int(command.args.strip())
    await users_col.update_one({"_id": target_id}, {"$set": {"role": "user"}})
    await message.reply(f"✅ User <code>{target_id}</code> demoted to regular <b>User</b>.")


@router.message(Command("broadcast"))
async def handle_broadcast(message: Message, command: CommandObject):
    """Usage: /broadcast <message_text>"""
    role = await get_user_role(message.from_user.id)
    if role != "owner":
        return

    if not command.args:
        await message.reply("❗ <b>Usage:</b> <code>/broadcast &lt;message&gt;</code>")
        return

    broadcast_text = command.args
    cursor = users_col.find({}, {"_id": 1})
    all_users = await cursor.to_list(length=10000)

    sent = 0
    failed = 0
    bot = message.bot

    status_msg = await message.reply("🚀 Broadcast started...")
    for u in all_users:
        uid = u["_id"]
        try:
            await bot.send_message(uid, broadcast_text)
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1

    await status_msg.edit_text(f"📢 Broadcast finished.\n<b>Delivered:</b> {sent}\n<b>Failed/Blocked:</b> {failed}")


# ==============================================================================
# MAIN ENTRYPOINT
# ==============================================================================
async def main():
    await init_db()
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)

    logger.info("Bot started successfully. Listening for updates...")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        client.close()


if __name__ == "__main__":
    asyncio.run(main())