import logging
from datetime import datetime, timezone

from bson import ObjectId

from config import OWNER_IDS
from database.db import db

logger = logging.getLogger(__name__)


# ── Accounts ──────────────────────────────────────────────────────────────────
async def save_account(owner_id: int, hex_key: str, phone: str, name: str,
                       user_id: int, dc_id: int, session_string: str):
    """Save (or update) a managed account and return its MongoDB _id."""
    collection = db.get_db()["accounts"]
    now = datetime.now(timezone.utc)
    existing = await collection.find_one({"owner_id": owner_id, "user_id": user_id})

    data = {
        "owner_id": owner_id,
        "hex_key": hex_key,
        "phone": phone,
        "name": name,
        "user_id": user_id,
        "dc_id": dc_id,
        "session_string": session_string,
        "guard_active": False,
        "guard_allow_until": None,
        "updated_at": now,
    }

    if existing:
        await collection.update_one({"_id": existing["_id"]}, {"$set": data})
        return existing["_id"]

    data["last_otp"] = None
    data["last_otp_at"] = None
    data["created_at"] = now
    result = await collection.insert_one(data)
    return result.inserted_id


async def get_accounts_by_owner(owner_id: int):
    collection = db.get_db()["accounts"]
    cursor = collection.find({"owner_id": owner_id}).sort("created_at", -1)
    return await cursor.to_list(length=None)


async def get_account_by_user_id(owner_id: int, user_id: int):
    collection = db.get_db()["accounts"]
    return await collection.find_one({"owner_id": owner_id, "user_id": user_id})


async def get_account_by_id(account_id: str):
    collection = db.get_db()["accounts"]
    try:
        return await collection.find_one({"_id": ObjectId(account_id)})
    except Exception:
        return None


async def update_account(account_id: str, update_data: dict):
    collection = db.get_db()["accounts"]
    update_data["updated_at"] = datetime.now(timezone.utc)
    try:
        return await collection.update_one(
            {"_id": ObjectId(account_id)}, {"$set": update_data}
        )
    except Exception:
        return None


async def delete_account(account_id: str):
    collection = db.get_db()["accounts"]
    try:
        return await collection.delete_one({"_id": ObjectId(account_id)})
    except Exception:
        return None


async def set_last_otp(account_id: str, otp: str):
    """Persist the last OTP seen for an account so it can be re-displayed."""
    if not account_id or not otp:
        return
    await update_account(account_id, {
        "last_otp": otp,
        "last_otp_at": datetime.now(timezone.utc),
    })


# ── Sudo Users ───────────────────────────────────────────────────────────────
async def add_sudo_user(sudo_id: int, added_by: int):
    collection = db.get_db()["sudo_users"]
    existing = await collection.find_one({"user_id": sudo_id})
    if existing:
        return False
    await collection.insert_one({
        "user_id": sudo_id,
        "added_by": added_by,
        "added_at": datetime.now(timezone.utc),
    })
    logger.info("Sudo user %s added by %s", sudo_id, added_by)
    return True


async def remove_sudo_user(sudo_id: int):
    collection = db.get_db()["sudo_users"]
    result = await collection.delete_one({"user_id": sudo_id})
    return result.deleted_count > 0


async def get_all_sudo_users():
    collection = db.get_db()["sudo_users"]
    return await collection.find().to_list(length=None)


async def is_sudo_user(user_id: int):
    if user_id in OWNER_IDS:
        return True
    collection = db.get_db()["sudo_users"]
    result = await collection.find_one({"user_id": user_id})
    return result is not None


async def is_authorized(user_id: int) -> bool:
    """True if the user may use the bot.

    - If OWNER_IDS is not configured, the bot runs in OPEN mode (everyone).
    - Otherwise only owners and sudo users are allowed.
    """
    if not OWNER_IDS:
        return True
    allowed = await is_sudo_user(user_id)
    if not allowed:
        logger.info("Access denied for user %s (not owner/sudo)", user_id)
    return allowed


# ── Mails (multiple Gmail/Outlook accounts supported per user) ───────────────
async def save_mail(owner_id: int, email: str, app_password: str,
                    verified: bool | None = None, check_message: str | None = None):
    """Save (upsert) one mailbox. Multiple emails per owner are allowed."""
    collection = db.get_db()["mails"]
    email_norm = email.strip().lower()
    existing = await collection.find_one({"owner_id": owner_id, "email_lower": email_norm})

    set_data = {
        "owner_id": owner_id,
        "email": email.strip(),
        "email_lower": email_norm,
        "app_password": app_password,
        "updated_at": datetime.now(timezone.utc),
        "combo_uses": {},  # variant -> how many Telegram accounts used it (max 2)
    }
    if verified is not None:
        set_data["verified"] = verified
    if check_message is not None:
        set_data["check_message"] = check_message
        set_data["last_checked"] = datetime.now(timezone.utc)

    if existing:
        # Keep existing combo_uses when updating password
        set_data.pop("combo_uses", None)
        await collection.update_one({"_id": existing["_id"]}, {"$set": set_data})
    else:
        set_data.setdefault("created_at", datetime.now(timezone.utc))
        set_data.setdefault("verified", False)
        await collection.insert_one(set_data)


async def get_mail(owner_id: int):
    """Return the first saved mail (back-compat). Prefer get_all_mails()."""
    collection = db.get_db()["mails"]
    return await collection.find_one({"owner_id": owner_id})


async def get_all_mails(owner_id: int) -> list:
    """All mailboxes saved by this user (for multi-Gmail combo rotation)."""
    collection = db.get_db()["mails"]
    cursor = collection.find({"owner_id": owner_id}).sort("created_at", 1)
    return await cursor.to_list(length=50)


async def increment_combo_use(owner_id: int, email_lower: str, variant: str) -> int:
    """Mark a variant as used once. Returns new use count (capped conceptually at 2)."""
    collection = db.get_db()["mails"]
    key = f"combo_uses.{variant}"
    await collection.update_one(
        {"owner_id": owner_id, "email_lower": email_lower},
        {"$inc": {key: 1}},
    )
    doc = await collection.find_one({"owner_id": owner_id, "email_lower": email_lower})
    if not doc:
        return 1
    raw = (doc.get("combo_uses") or {}).get(variant, 0)
    if isinstance(raw, dict):
        return 0
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


async def remove_mail(owner_id: int, email: str | None = None):
    """Remove one mailbox, or all if email is None."""
    collection = db.get_db()["mails"]
    if email:
        return await collection.delete_one(
            {"owner_id": owner_id, "email_lower": email.strip().lower()}
        )
    return await collection.delete_many({"owner_id": owner_id})
