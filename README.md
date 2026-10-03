# Smart Coins

FastAPI backend + a browser-based web UI, backed by MongoDB Atlas. Everything runs as one hosted service — there's no desktop app to install anywhere; staff just open a browser.

## What's in this repo
```
server.py          # FastAPI backend — all business logic and the REST API
web/index.html      # Self-contained web UI, served by server.py at "/"
requirements.txt     # Python dependencies
render.yaml          # One-click free-tier deploy config for Render
.env.example          # Environment variables you need to set
```

## Run it

```bash
<<<<<<< HEAD

=======
python -m pip install -r requirements.txt
export MONGODB_URI='mongodb+srv://rahar:rG9pyXZtQBEEIgWu@cluster0.fw1osbv.mongodb.net/smart_coins?authSource=admin'
export MONGODB_DB='smart_coins'
python -m uvicorn server:app --host 0.0.0.0 --port 8000
>>>>>>> c64d41e (Added fixes)
```

Then open `http://localhost:8000/` in a browser — that's the whole app, API and UI together on one URL. `MONGODB_URI` has no built-in fallback; the server refuses to start without it.

Default login: `admin` / `admin`. Change it before putting this in front of real staff.

## Hosting it for free (Render)
This repo includes `render.yaml`, so Render reads it automatically.

1. Push this repo to GitHub.
2. On [dashboard.render.com](https://dashboard.render.com), sign up free (no card), click **New > Blueprint**, and point it at the repo. Render proposes a free Web Service named `smart-coins-api`.
3. On the service's **Environment** tab, set `MONGODB_URI` to your Atlas connection string — it's the one secret `render.yaml` deliberately leaves blank.
4. In MongoDB Atlas, go to **Network Access > Add IP Address > Allow Access from Anywhere** (`0.0.0.0/0`) — Render's free tier has no fixed outbound IP, so Atlas can't be locked to one address.
5. Deploy. Render gives you a URL like `https://smart-coins-api.onrender.com` — open it in a browser, that's the app.
6. **Free-tier trade-off:** the service sleeps after ~15 minutes idle; the first request after a quiet spell takes 30-60 seconds to wake up, then it's normal speed until idle again.

## Rules implemented
- S-Mart ₹10=1 coin; Kids World ₹10=2; Insignia 360 ₹10=3.
- Minimum earning and redemption bill ₹500.
- Coins available next calendar day at 00:00.
- Coins expire one year from earning date and expiry is retained in history.
- 10 coins = ₹1; redemption value cannot exceed bill/5.
- Redemption coins must be strictly less than total credited coins and cannot exceed eligible coins.
- Partial redemption supported through requested redemption value.
- Cash-paid portion creates fresh coins using the redemption outlet ratio.
- One active membership per mobile number.

## Inactivity reminders ("haven't shopped in 3 months")
The **Reminders** section of the web UI (backed by `GET /reminders/inactive?days=90`) lists every active member whose last earn/redeem transaction — or join date, if they've never transacted — is older than the chosen number of days (90 ≈ 3 months by default). Each row gets a ready-made, pre-filled `wa.me` WhatsApp link.

**Why this doesn't auto-send, and why that's the honest answer:** a reminder to someone who hasn't messaged your business number falls under WhatsApp's "utility template, outside the customer-service window" category, which has always been a paid, per-message category on Meta's official Business API — it was never free, including before the October 1, 2026 pricing update (that update only added charges to messages that *used to be* free; this category was already billed). So:
- **Free and compliant (what's built here):** a `wa.me/<number>?text=<message>` link per inactive member. Clicking it opens WhatsApp with the message pre-filled; a staff member reviews and hits send. Zero cost, no Meta Business account or template approval needed, fully within WhatsApp's terms — the one trade-off is that it's a manual click per member, not a background job.
- **Paid, fully automated:** the official WhatsApp Business Cloud API, with an approved "utility" message template, billed per message at Meta's regional rate. Worth it once message volume makes the manual click impractical.
- **Not implemented here on purpose:** unofficial bulk-automation libraries that drive your own WhatsApp Web session (e.g. `whatsapp-web.js`, `pywhatkit`) are technically free but violate WhatsApp's Terms of Service for business/bulk messaging and risk the number being permanently banned — not something worth risking on a number your stores depend on.

Configure with two optional environment variables: `WHATSAPP_COUNTRY_CODE` (default `91`) and `REMINDER_MESSAGE_TEMPLATE` (default greets the member by name and mentions their eligible coin balance and card number; supports `{name}`, `{card_no}`, `{eligible}`, `{credited}`).

## Who can log in (user management)
There's no longer just the one shared `admin` login. Admins see a **Users** tab in the web UI to create logins for other staff, each with a role:
- **Admin** — everything, including managing other users and running backups.
- **Staff** — everyday use (members, bill credit, redemption, reminders) but can't manage users or run backups.

The system always keeps at least one active admin — you can't deactivate the last one, and you can't deactivate your own account (ask another admin to do that, if needed). The original `admin`/`admin` bootstrap account still gets created automatically on first run if no users exist yet; change its password or create a named admin account for yourself and deactivate it once you have.

## Backups
`POST /backup` (admin only) runs `mongodump` on the server and writes a timestamped folder under `mongo_backups/`. Requires `mongodump` to be installed wherever the server runs; Render's free tier does not have it preinstalled, so treat this as a local/VPS-only feature unless you add it to the build step.
