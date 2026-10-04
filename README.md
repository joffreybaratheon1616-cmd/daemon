# Session / Mail Manager Bot

Telegram session manager with coloured buttons, Outlook/Gmail mail change (case variants + auto OTP), and auto mail logout on failure.

## Railway deploy

1. New project → **Deploy from GitHub** → this repo
2. Add **Variables**:

| Variable | Required | Description |
|----------|----------|-------------|
| `BOT_TOKEN` | yes | @BotFather token |
| `API_ID` | yes | my.telegram.org |
| `API_HASH` | yes | my.telegram.org |
| `MONGO_URI` | yes | MongoDB Atlas connection string |
| `DB_NAME` | no | default `sessionbot` |
| `OWNER_IDS` | recommended | comma-separated Telegram user IDs |

3. Start command (auto from `railway.toml` / `Procfile`): `python main.py`
4. Deploy. Use a **worker** service (no HTTP port required).

## Local run

```bash
cp .env.example .env   # fill values
pip install -r requirements.txt
python main.py
```

## Features

- Manage account (hex / string / .session)
- Device dashboard, terminate, clear, OTP, 2FA, export hex
- One-click **Change Mail** with email case-variants + IMAP auto OTP (Gmail / Outlook / Yahoo)
- Auto **logout saved mail** if no combo works
- Safe / Guard mode
- Coloured inline buttons (`primary` / `success` / `danger`)
