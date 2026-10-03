# Smart Coins Centralized MongoDB Edition 3.0.0

Central FastAPI backend with MongoDB Atlas as the shared database. All outlet clients use the same API and therefore share membership, coin balances, expiry and redemption history.

## Run backend

```bash
python -m pip install -r requirements.txt
export MONGODB_URI='mongodb+srv://rahar:rahar@cluster0.fw1osbv.mongodb.net/smart_coins'
export MONGODB_DB='smart_coins'
python -m uvicorn server:app --host 0.0.0.0 --port 8000
```

Windows PowerShell:
```powershell
$env:MONGODB_URI='mongodb+srv://rahar:rahar@cluster0.fw1osbv.mongodb.net/smart_coins'
$env:MONGODB_DB='smart_coins'
python -m uvicorn server:app --host 0.0.0.0 --port 8000
```

`MONGODB_URI` has no built-in fallback — the server refuses to start until it is set.

Default login: `admin` / `admin`. Change it before production.

## Run desktop client

```bash
python client.py
```
Enter the hosted API URL, for example `https://smartcoins.yourdomain.com`.

## Atlas setup
- Create the `smart_coins` database.
- Add the backend server IP under Network Access.
- Create a database user and rotate the password before production.
- Use TLS and a reverse proxy such as Nginx/Caddy.
- MongoDB transactions require Atlas or a replica-set deployment.

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
The **Reminders** tab in `client.py` (backed by `GET /reminders/inactive?days=90`) lists every active member whose last earn/redeem transaction — or join date, if they've never transacted — is older than the chosen number of days (90 ≈ 3 months by default). Each row gets a ready-made, pre-filled `wa.me` WhatsApp link.

**Why this doesn't auto-send, and why that's the honest answer:** a reminder to someone who hasn't messaged your business number falls under WhatsApp's "utility template, outside the customer-service window" category, which has always been a paid, per-message category on Meta's official Business API — it was never free, including before the October 1, 2026 pricing update (that update only added charges to messages that *used to be* free; this category was already billed). So:
- **Free and compliant (what's built here):** a `wa.me/<number>?text=<message>` link per inactive member. Clicking it opens WhatsApp with the message pre-filled; a staff member reviews and hits send. Zero cost, no Meta Business account or template approval needed, fully within WhatsApp's terms — the one trade-off is that it's a manual click per member, not a background job.
- **Paid, fully automated:** the official WhatsApp Business Cloud API, with an approved "utility" message template, billed per message at Meta's regional rate. Worth it once message volume makes the manual click impractical.
- **Not implemented here on purpose:** unofficial bulk-automation libraries that drive your own WhatsApp Web session (e.g. `whatsapp-web.js`, `pywhatkit`) are technically free but violate WhatsApp's Terms of Service for business/bulk messaging and risk the number being permanently banned — not something worth risking on a number your stores depend on.

Configure with two optional environment variables: `WHATSAPP_COUNTRY_CODE` (default `91`) and `REMINDER_MESSAGE_TEMPLATE` (default greets the member by name and mentions their eligible coin balance and card number; supports `{name}`, `{card_no}`, `{eligible}`, `{credited}`).

## Hosting the backend for free (Render)
This repo includes `render.yaml` so Render can pick up the right build/start commands automatically.

1. **Push this project to a GitHub repo** (Render deploys from git). If you don't have one yet: create a new empty repo on github.com, then from this folder:
   ```bash
   git init && git add . && git commit -m "Smart Coins backend"
   git remote add origin <your-new-repo-url>
   git push -u origin main
   ```
2. Go to [dashboard.render.com](https://dashboard.render.com), sign up free (no card required), click **New > Blueprint**, and point it at your repo. Render reads `render.yaml` and proposes a free Web Service named `smart-coins-api` automatically.
3. Before the first deploy, set the one secret it can't read from git: open the service's **Environment** tab and set `MONGODB_URI` to your Atlas connection string.
4. **MongoDB Atlas Network Access — don't skip this:** Render's free tier doesn't give you a fixed outbound IP, so Atlas can't be locked to one address. In Atlas, go to **Network Access > Add IP Address > Allow Access from Anywhere** (`0.0.0.0/0`). This is the standard trade-off for free-tier hosting; if that's a concern, a paid Render plan or a VPS with a static IP is the fix later.
5. Deploy. Render gives you a URL like `https://smart-coins-api.onrender.com` — that's what goes into `client.py`'s "Server URL" field at login.
6. **The free-tier trade-off:** the service sleeps after ~15 minutes with no traffic, so the first request after a quiet spell takes 30-60 seconds while it wakes up. Every request after that is normal speed until it goes idle again. Fine for a few outlets checking in throughout the day; not suitable if you need instant response with no warm-up, ever.

## Web UI (`web/index.html`)
A modern browser-based alternative to the Tkinter desktop client — same features (dashboard, members, bill credit, redemption, reminders), served directly by `server.py` at `/`, so it's always same-origin with the API (no CORS setup needed). Open `https://your-deployed-url/` (or `http://localhost:8000/` when running locally) in any browser.

It's a single self-contained HTML file with no build step and no dependencies beyond two Google Fonts — edit it directly if you want to adjust copy, colors, or layout. It stores the server URL and session token in the browser's `localStorage` so staff don't have to re-enter them each time; "Sign out" clears that.

This exists alongside `client.py` (the desktop app) rather than replacing it — use whichever fits an outlet's hardware better.
