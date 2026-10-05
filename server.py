import os, hashlib, secrets, json, subprocess, re
from pathlib import Path
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Optional
from urllib.parse import quote
from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError, PyMongoError
from reportlab.pdfgen import canvas

BASE = Path(__file__).resolve().parent
RECEIPTS = BASE / 'receipts'; RECEIPTS.mkdir(exist_ok=True)
MONGODB_URI = os.getenv('MONGODB_URI')
MONGODB_DB = os.getenv('MONGODB_DB', 'smart_coins')
if not MONGODB_URI:
    raise RuntimeError('MONGODB_URI environment variable is not set. Set it before starting the server (see .env.example).')
client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=10000)
db = client[MONGODB_DB]
app = FastAPI(title='Smart Coins Central API', version='4.0.0')

@app.exception_handler(PyMongoError)
def db_error_handler(request: Request, exc: PyMongoError):
    return JSONResponse(status_code=503, content={'detail': 'Database temporarily unavailable. Please try again in a moment.'})

@app.exception_handler(Exception)
def generic_error_handler(request: Request, exc: Exception):
    return JSONResponse(status_code=500, content={'detail': 'Something went wrong on the server. Please try again, and let an admin know if it keeps happening.'})

STORES = {
    'SMART': {'name': 'S-Mart', 'percentage': 1.0, 'coins_per_10': 1},
    'KIDS': {'name': 'Kids World', 'percentage': 1.0, 'coins_per_10': 2},
    'INSIGNIA': {'name': 'Insignia 360', 'percentage': 2.0, 'coins_per_10': 3},
}
COINS_PER_RUPEE = 10
WHATSAPP_COUNTRY_CODE = os.getenv('WHATSAPP_COUNTRY_CODE', '91')
REMINDER_MESSAGE_TEMPLATE = os.getenv('REMINDER_MESSAGE_TEMPLATE',
    "Hi {name}! We miss you at Smart Coins. You have {eligible} eligible coins waiting on card {card_no} — come shop with us soon before they expire. 🙂")

WHATSAPP_REMINDER_TEMPLATES = {
    'MEMBERSHIP_CREATED': {
        'title': 'Membership Created', 'trigger': 'Immediately after a membership is created',
        'message': "Welcome, {{NAME}}! 🎉 Your SKG ONE Lifetime Membership is now active.\n\nYour membership unlocks benefits across S-MART, KIDS WORLD and INSIGNIA 360° ROOFTOP RESTAURANT.\n\nAs a welcome gift, 990 Smart Coins have been added to your account.\n\nMembership No.: {{MEMBER_NO}}\n\nShop at S-MART, discover something special at KIDS WORLD, or enjoy a memorable experience at INSIGNIA 360° ROOFTOP RESTAURANT.\n\nYour SKG ONE benefits are ready whenever you are. ✨"
    },
    'SMART_COINS_EARNED': {
        'title': 'Smart Coins Earned', 'trigger': 'Immediately after a successful bill credit',
        'message': "Good news, {{NAME}}! 🪙 You’ve earned {{COINS}} Smart Coins on your ₹{{AMOUNT}} purchase at {{BUSINESS}}.\n\nAvailable from tomorrow.\n\nCurrent Smart Coin Balance: {{BALANCE}}\n\nUse your SKG ONE benefits across S-MART, KIDS WORLD and INSIGNIA 360° ROOFTOP RESTAURANT.\n\nKeep earning, keep enjoying! ✨"
    },
    'SMART_COINS_REDEEMED': {
        'title': 'Smart Coins Redeemed', 'trigger': 'Immediately after a successful redemption',
        'message': "{{NAME}}, your Smart Coins have been redeemed successfully. 🎁\n\nYou’ve redeemed {{COINS}} Smart Coins worth ₹{{COINS}} at {{BUSINESS}}.\n\nRemaining Smart Coin Balance: {{BALANCE}}\n\nContinue enjoying the SKG ONE experience across S-MART, KIDS WORLD and INSIGNIA 360° ROOFTOP RESTAURANT.\n\nEnjoy your SKG ONE benefits! ✨"
    },
    'SMART_COINS_EXPIRING_30': {
        'title': 'Smart Coins Expiring — 30 Days', 'trigger': '30 days before eligible Smart Coins expire',
        'message': "{{NAME}}, your Smart Coins are expiring soon. ⏳\n\nYou have {{COINS}} Smart Coins worth ₹{{COINS}} expiring on {{DATE}}.\n\nUse them at KIDS WORLD or INSIGNIA 360° ROOFTOP RESTAURANT before they expire.\n\nAnd while you’re there, remember that your SKG ONE membership also brings benefits at S-MART.\n\nDon’t let your benefits go unused! ✨"
    },
    'SMART_COINS_EXPIRING_7': {
        'title': 'Smart Coins Expiring — 7 Days', 'trigger': '7 days before eligible Smart Coins expire',
        'message': "Important reminder, {{NAME}}! ⏰ Your {{COINS}} Smart Coins worth ₹{{COINS}} will expire on {{DATE}}.\n\nUse them before they expire at KIDS WORLD or INSIGNIA 360° ROOFTOP RESTAURANT.\n\nKeep enjoying the complete SKG ONE experience across S-MART, KIDS WORLD and INSIGNIA 360° ROOFTOP RESTAURANT.\n\nMake your Smart Coins count! 🪙"
    },
    'BIRTHDAY_7': {
        'title': 'Birthday — 7 Days Before', 'trigger': '7 days before the member’s birthday',
        'message': "Your special day is just 7 days away, {{NAME}}! 🎂\n\nStart planning something special with SKG ONE.\n\nCelebrate with your loved ones at INSIGNIA 360° ROOFTOP RESTAURANT, or find a special gift at KIDS WORLD.\n\nAnd remember, your SKG ONE benefits are also available at S-MART.\n\nHere’s to a celebration worth remembering! ✨"
    },
    'BIRTHDAY_2': {
        'title': 'Birthday — 2 Days Before', 'trigger': '2 days before the member’s birthday',
        'message': "Only 2 days to go, {{NAME}}! 🎉 Your birthday is almost here.\n\nMake it memorable with a special celebration at INSIGNIA 360° ROOFTOP RESTAURANT or discover something special at KIDS WORLD.\n\nYour SKG ONE membership keeps bringing you more across S-MART, KIDS WORLD and INSIGNIA 360° ROOFTOP RESTAURANT.\n\nYour celebration starts here! 🥳"
    },
    'BIRTHDAY_TODAY': {
        'title': 'Birthday — Today', 'trigger': 'On the member’s birthday',
        'message': "Happy Birthday, {{NAME}}! 🎂🎈\n\nWishing you happiness, success and a wonderful year ahead.\n\nCelebrate your special day at INSIGNIA 360° ROOFTOP RESTAURANT, choose a special gift from KIDS WORLD, or enjoy your everyday shopping at S-MART.\n\nHave a wonderful celebration with SKG ONE! ✨\n\nDon’t forget to enjoy your SKG ONE benefits!"
    },
    'ANNIVERSARY_7': {
        'title': 'Marriage Anniversary — 7 Days Before', 'trigger': '7 days before the member’s marriage anniversary',
        'message': "Your special anniversary is just 7 days away, {{NAME}}! ❤️\n\nMake the occasion memorable with SKG ONE.\n\nPlan a special evening at INSIGNIA 360° ROOFTOP RESTAURANT or choose something special for your loved one at KIDS WORLD.\n\nAnd don’t forget, your SKG ONE benefits are also available at S-MART.\n\nHere’s to beautiful moments together! ✨"
    },
    'ANNIVERSARY_2': {
        'title': 'Marriage Anniversary — 2 Days Before', 'trigger': '2 days before the member’s marriage anniversary',
        'message': "Only 2 days to go, {{NAME}}! ❤️ Your anniversary is almost here.\n\nCelebrate your special occasion at INSIGNIA 360° ROOFTOP RESTAURANT or surprise your loved one with something special from KIDS WORLD.\n\nEnjoy the complete SKG ONE experience across S-MART, KIDS WORLD and INSIGNIA 360° ROOFTOP RESTAURANT.\n\nMake the moment special! ✨"
    },
    'ANNIVERSARY_TODAY': {
        'title': 'Marriage Anniversary — Today', 'trigger': 'On the member’s marriage anniversary',
        'message': "Happy Anniversary, {{NAME}}! ❤️🥂\n\nWishing you both many more years of happiness, togetherness and beautiful memories.\n\nMake your special day memorable at INSIGNIA 360° ROOFTOP RESTAURANT, find something special at KIDS WORLD, and continue enjoying the benefits of SKG ONE at S-MART.\n\nWishing you a wonderful anniversary! ✨\n\nDon’t forget to enjoy your SKG ONE benefits!"
    },
    'NO_TRANSACTION_40': {
        'title': 'No Transaction — 40 Days', 'trigger': 'Automatically eligible when there has been no transaction for 40 days',
        'message': "{{NAME}}, we haven’t seen you for a while! 👋\n\nYour SKG ONE Lifetime Membership is still active and your benefits are waiting for you.\n\nIt’s a good time to visit S-MART for your everyday needs, explore something special at KIDS WORLD, or enjoy an evening at INSIGNIA 360° ROOFTOP RESTAURANT.\n\nCome back and enjoy the complete SKG ONE experience. ✨\n\nDon’t forget to enjoy your SKG ONE benefits!"
    },
}

DEFAULT_USERS = {
    'SMART': {'username': 'SMART', 'password': 'QWERTY', 'role': 'STORE', 'store': 'SMART'},
    'KIDS': {'username': 'KIDS', 'password': 'ASDFGH', 'role': 'STORE', 'store': 'KIDS'},
    'INSIGNIA': {'username': 'INSIGNIA', 'password': 'ZXCVBN', 'role': 'STORE', 'store': 'INSIGNIA'},
    'ADMIN': {'username': 'GOVIND', 'password': 'Govind@123', 'role': 'ADMIN', 'store': None},
}

def whatsapp_link(mobile: str, text: str) -> str:
    digits = re.sub(r'\D', '', mobile or '')
    if digits.startswith('0'): digits = digits.lstrip('0')
    if not digits.startswith(WHATSAPP_COUNTRY_CODE) and len(digits) <= 10: digits = WHATSAPP_COUNTRY_CODE + digits
    return f'https://wa.me/{digits}?text={quote(text)}'

class LoginReq(BaseModel): username: str; password: str
class MemberReq(BaseModel):
    name: str; mobile: str; email: str = ''; address: str = ''; dob: str = ''; anniversary: str = ''; card_no: str
class MemberUpdateReq(BaseModel):
    card_no: str; name: str; mobile: str; email: str = ''; address: str = ''; dob: str = ''; anniversary: str = ''
class EarnReq(BaseModel): card_no: str; bill_no: str; bill_date: Optional[str] = None; amount: float = Field(gt=0); store: str
class RedeemReq(BaseModel): card_no: str; bill_no: str; bill_date: Optional[str] = None; amount: float = Field(gt=0); store: str; requested_value: Optional[float] = None
class UserReq(BaseModel): username: str; password: str; role: str = 'STORE'; store: Optional[str] = None
class CorrectionReq(BaseModel): transaction_type: str; transaction_id: str; reason: str


IST = ZoneInfo('Asia/Kolkata')
def now(): return datetime.now(IST).replace(tzinfo=None)
def today(): return datetime.now(IST).date()
def iso(d): return d.isoformat()
def add_year(d):
    try: return d.replace(year=d.year + 1)
    except ValueError: return d.replace(year=d.year + 1, day=28)
def hashpw(p, s): return hashlib.pbkdf2_hmac('sha256', p.encode(), s.encode(), 180000).hex()
def parse_date(s):
    try: return datetime.strptime(s, '%Y-%m-%d').date()
    except (ValueError, TypeError): raise HTTPException(400, f"Invalid bill date '{s}' — expected format YYYY-MM-DD.")
def valid_mobile(mobile):
    if not re.fullmatch(r'\d{10}', mobile or ''): raise HTTPException(400, 'Mobile number must contain exactly 10 digits.')
def audit(event, ref, details, uid=None):
    db.audit.insert_one({'event': event, 'ref': ref, 'details': details, 'user_id': uid, 'created_at': now()})
def auth(a):
    if not a or not a.startswith('Bearer '): raise HTTPException(401, 'Login required')
    s = db.sessions.find_one({'token': a[7:]})
    if not s: raise HTTPException(401, 'Invalid session')
    u = db.users.find_one({'_id': s['user_id'], 'active': True})
    if not u: raise HTTPException(401, 'Invalid session')
    return u
def require_admin(u):
    if u.get('role') != 'ADMIN': raise HTTPException(403, 'Admin only')
def require_store_access(u, store):
    if store not in STORES: raise HTTPException(400, 'Invalid store')
    if u.get('role') != 'ADMIN' and u.get('store') != store:
        raise HTTPException(403, f"This login can only operate {STORES.get(u.get('store'), {}).get('name', 'its assigned outlet')}.")
def transaction_date(requested, u):
    # Normal outlet users cannot choose a date. Admin may intentionally back-date.
    if u.get('role') == 'ADMIN': return parse_date(requested) if requested else today()
    return today()
def expire_old():
    d = iso(today())
    for lot in db.coin_lots.find({'status': 'ACTIVE', 'expiry_on': {'$lt': d}, 'remaining_coins': {'$gt': 0}}):
        left = lot['remaining_coins']
        db.coin_lots.update_one({'_id': lot['_id']}, {'$set': {'status': 'EXPIRED', 'remaining_coins': 0}})
        db.expiry_log.insert_one({'lot_id': lot['_id'], 'expired_coins': left, 'expiry_date': lot['expiry_on'], 'created_at': now()})
        audit('COIN_EXPIRY', str(lot['_id']), f'expired={left};expiry={lot["expiry_on"]}')
def member(card):
    m = db.members.find_one({'card_no': card, 'active': True})
    if not m: raise HTTPException(404, 'Active member not found')
    return m
def balances(mid):
    credited = sum(x.get('coins', 0) for x in db.coin_lots.find({'member_id': mid, 'status': {'$nin': ['REVERSED', 'EXPIRED']}}))
    eligible = sum(x.get('remaining_coins', 0) for x in db.coin_lots.find({'member_id': mid, 'status': 'ACTIVE', 'available_on': {'$lte': iso(today())}, 'expiry_on': {'$gte': iso(today())}, 'remaining_coins': {'$gt': 0}}))
    redeemed = sum(x.get('coins', 0) for x in db.redemptions.find({'member_id': mid, 'status': 'COMPLETED'}))
    return int(credited), int(eligible), int(redeemed)
def lots(mid):
    return list(db.coin_lots.find({'member_id': mid, 'status': 'ACTIVE', 'available_on': {'$lte': iso(today())}, 'expiry_on': {'$gte': iso(today())}, 'remaining_coins': {'$gt': 0}}).sort([('expiry_on', ASCENDING), ('created_at', ASCENDING)]))

def ensure_indexes():
    db.users.create_index('username', unique=True)
    db.members.create_index('card_no', unique=True)
    db.members.create_index('mobile', unique=True, partialFilterExpression={'active': True})
    db.earnings.create_index('bill_no', unique=True)
    db.redemptions.create_index('bill_no', unique=True)
    db.coin_lots.create_index([('status', ASCENDING), ('expiry_on', ASCENDING), ('remaining_coins', DESCENDING)])
    db.members.create_index([('active', ASCENDING), ('dob', ASCENDING)])
    db.members.create_index([('active', ASCENDING), ('anniversary', ASCENDING)])
    db.sessions.create_index('token', unique=True)
    # Seed the four required accounts if missing. Passwords are stored only as hashes.
    for cfg in DEFAULT_USERS.values():
        existing = db.users.find_one({'username': cfg['username']})
        salt = existing.get('salt') if existing else None
        if not salt: salt = secrets.token_hex(16)
        db.users.update_one({'username': cfg['username']}, {'$set': {
            'password_hash': hashpw(cfg['password'], salt), 'salt': salt, 'role': cfg['role'],
            'store': cfg['store'], 'active': True, 'updated_at': now()
        }, '$setOnInsert': {'created_at': now()}}, upsert=True)
    # Retire the old generic bootstrap login so it cannot bypass outlet scoping.
    db.users.update_one({'username': 'admin', 'role': 'ADMIN'}, {'$set': {'active': False, 'updated_at': now()}})
ensure_indexes()

WEB_INDEX = BASE / 'web' / 'index.html'
@app.get('/', include_in_schema=False)
def web_ui():
    return FileResponse(WEB_INDEX, media_type='text/html') if WEB_INDEX.exists() else {'status': 'Smart Coins API running'}
@app.get('/health')
def health():
    client.admin.command('ping'); return {'status': 'ok', 'database': MONGODB_DB}

@app.post('/login')
def login(x: LoginReq):
    u = db.users.find_one({'username': x.username, 'active': True})
    if not u or hashpw(x.password, u['salt']) != u['password_hash']: raise HTTPException(401, 'Invalid username/password')
    t = secrets.token_urlsafe(32); db.sessions.insert_one({'token': t, 'user_id': u['_id'], 'created_at': now()})
    return {'token': t, 'username': u['username'], 'role': u['role'], 'store': u.get('store')}

@app.get('/members')
def members(q: str = '', authorization: str | None = Header(None)):
    u = auth(authorization); expire_old(); rg = {'$regex': q, '$options': 'i'}; out = []
    for m in db.members.find({'active': True, '$or': [{'card_no': rg}, {'name': rg}, {'mobile': rg}]}).sort('name', ASCENDING):
        c, e, r = balances(m['_id'])
        out.append({'card_no': m['card_no'], 'name': m['name'], 'mobile': m['mobile'], 'email': m.get('email', ''), 'address': m.get('address', ''), 'dob': m.get('dob', ''), 'anniversary': m.get('anniversary', ''), 'created_at': m.get('created_at').isoformat() if isinstance(m.get('created_at'), datetime) else m.get('created_at'), 'credited': c, 'eligible': e, 'redeemed': r, 'can_edit': u.get('role') == 'ADMIN', 'can_history': u.get('role') == 'ADMIN'})
    return out

@app.post('/members')
def create_member(x: MemberReq, authorization: str | None = Header(None)):
    u = auth(authorization)
    mobile = x.mobile.strip(); valid_mobile(mobile)
    card = x.card_no.strip()
    if not card: raise HTTPException(400, 'Physical card number is required.')
    if db.members.find_one({'card_no': card}): raise HTTPException(409, 'This card number is already assigned.')
    if db.members.find_one({'mobile': mobile, 'active': True}): raise HTTPException(409, 'This mobile number already has an active membership.')
    created_at = now()
    try:
        result = db.members.insert_one({'card_no': card, 'name': x.name.strip(), 'mobile': mobile, 'email': x.email.strip(), 'address': x.address.strip(), 'dob': x.dob, 'anniversary': x.anniversary, 'active': True, 'created_at': created_at, 'created_by': u['_id']})
    except DuplicateKeyError: raise HTTPException(409, 'Card number or mobile number is already assigned.')

    # Welcome bonus: 990 Smart Coins. It follows the standard coin rule:
    # earned today, usable from the next calendar day, and expires one year
    # from the membership creation date. Stored in both earnings and coin_lots
    # so it participates in balance, expiry, redemption allocation and audit.
    bonus_bill_no = f'WELCOME-{card}'
    bonus_date = created_at.date()
    bonus_lot = {
        'member_id': result.inserted_id, 'source_type': 'WELCOME_BONUS',
        'bill_no': bonus_bill_no, 'bill_date': iso(bonus_date), 'amount': 0,
        'coins': 990, 'remaining_coins': 990,
        'available_on': iso(bonus_date + timedelta(days=1)),
        'expiry_on': iso(add_year(bonus_date)), 'store': 'SYSTEM',
        'status': 'ACTIVE', 'created_by': u['_id'], 'created_at': created_at
    }
    db.earnings.insert_one({**bonus_lot})
    db.coin_lots.insert_one(bonus_lot)
    audit('MEMBER_CREATE', card, json.dumps({'card_no': card, 'name': x.name.strip(), 'mobile': mobile, 'welcome_bonus': 990}), u['_id'])
    audit('WELCOME_BONUS', card, '990 Smart Coins;available_on=' + bonus_lot['available_on'] + ';expiry=' + bonus_lot['expiry_on'], u['_id'])
    action = reminder_action('MEMBERSHIP_CREATED', {'name': x.name.strip(), 'card_no': card, 'mobile': mobile})
    audit('WHATSAPP_READY', card, 'template=MEMBERSHIP_CREATED')
    return {'card_no': card, 'welcome_bonus': 990, 'welcome_bonus_available_on': bonus_lot['available_on'], 'welcome_bonus_expiry_on': bonus_lot['expiry_on'], 'whatsapp': action}

@app.put('/members/{card_no}')
def update_member(card_no: str, x: MemberUpdateReq, authorization: str | None = Header(None)):
    u = auth(authorization); require_admin(u); m = member(card_no)
    mobile = x.mobile.strip(); valid_mobile(mobile)
    conflict = db.members.find_one({'mobile': mobile, 'active': True, 'card_no': {'$ne': card_no}})
    if conflict: raise HTTPException(409, 'This mobile number already has another active membership.')
    new_card = x.card_no.strip()
    if not new_card: raise HTTPException(400, 'Card number is required.')
    card_conflict = db.members.find_one({'card_no': new_card, '_id': {'$ne': m['_id']}})
    if card_conflict: raise HTTPException(409, 'This card number is already assigned.')
    before = {k: m.get(k, '') for k in ('card_no', 'name', 'mobile', 'email', 'address', 'dob', 'anniversary')}
    after = {'card_no': new_card, 'name': x.name.strip(), 'mobile': mobile, 'email': x.email.strip(), 'address': x.address.strip(), 'dob': x.dob, 'anniversary': x.anniversary}
    db.members.update_one({'_id': m['_id']}, {'$set': after, '$currentDate': {'updated_at': True}})
    audit('MEMBER_UPDATE', card_no, json.dumps({'before': before, 'after': after}), u['_id'])
    return {'ok': True}

@app.get('/members/{card_no}/history')
def member_history(card_no: str, authorization: str | None = Header(None)):
    u = auth(authorization); require_admin(u); m = member(card_no); mid = m['_id']
    users = {x['_id']: x['username'] for x in db.users.find({}, {'username': 1})}
    earnings = []
    for x in db.earnings.find({'member_id': mid}).sort('created_at', ASCENDING):
        earnings.append({'type': 'WELCOME_BONUS' if x.get('source_type') == 'WELCOME_BONUS' else 'BILL_CREDIT', 'id': x.get('bill_no'), 'status': x.get('status', 'COMPLETED'), 'store': 'Welcome Bonus' if x.get('source_type') == 'WELCOME_BONUS' else STORES.get(x.get('store'), {}).get('name', x.get('store')), 'bill_no': x.get('bill_no'), 'bill_date': x.get('bill_date'), 'amount': x.get('amount'), 'coins': x.get('coins'), 'available_on': x.get('available_on'), 'expiry_on': x.get('expiry_on'), 'created_at': x.get('created_at').isoformat() if isinstance(x.get('created_at'), datetime) else x.get('created_at'), 'user': users.get(x.get('created_by'), 'unknown')})
    redemptions = []
    for x in db.redemptions.find({'member_id': mid}).sort('created_at', ASCENDING):
        redemptions.append({'type': 'REDEMPTION', 'id': x.get('redemption_id'), 'status': x.get('status'), 'store': STORES.get(x.get('store'), {}).get('name', x.get('store')), 'bill_no': x.get('bill_no'), 'bill_date': x.get('bill_date'), 'amount': x.get('amount'), 'coins': x.get('coins'), 'value': x.get('value'), 'created_at': x.get('created_at').isoformat() if isinstance(x.get('created_at'), datetime) else x.get('created_at'), 'user': users.get(x.get('created_by'), 'unknown')})
    audits = []
    refs = [card_no] + [e['bill_no'] for e in earnings if e.get('bill_no')] + [r['id'] for r in redemptions if r.get('id')]
    for x in db.audit.find({'ref': {'$in': refs}}).sort('created_at', ASCENDING):
        audits.append({'event': x.get('event'), 'ref': x.get('ref'), 'details': x.get('details'), 'user': users.get(x.get('user_id'), 'system'), 'created_at': x.get('created_at').isoformat() if isinstance(x.get('created_at'), datetime) else x.get('created_at')})
    lot_ids = [x['_id'] for x in db.coin_lots.find({'member_id': mid}, {'_id': 1})]
    for x in db.expiry_log.find({'lot_id': {'$in': lot_ids}}).sort('created_at', ASCENDING):
        audits.append({'event': 'COIN_EXPIRY', 'ref': str(x.get('lot_id')), 'details': f"expired={x.get('expired_coins')};expiry={x.get('expiry_date')}", 'user': 'system', 'created_at': x.get('created_at').isoformat() if isinstance(x.get('created_at'), datetime) else x.get('created_at')})
    audits.sort(key=lambda x: x.get('created_at') or '')
    c, e, r = balances(mid)
    return {'member': {'card_no': m['card_no'], 'name': m['name'], 'mobile': m['mobile'], 'email': m.get('email', ''), 'address': m.get('address', ''), 'dob': m.get('dob', ''), 'anniversary': m.get('anniversary', ''), 'created_at': m.get('created_at').isoformat() if isinstance(m.get('created_at'), datetime) else m.get('created_at'), 'created_by': users.get(m.get('created_by'), 'unknown')}, 'balance': {'credited': c, 'eligible': e, 'redeemed': r}, 'earnings': earnings, 'redemptions': redemptions, 'audit': audits}

def render_reminder(key, m, **ctx):
    t = WHATSAPP_REMINDER_TEMPLATES[key]['message']
    values = {
        'NAME': m.get('name', ''),
        'MEMBER_NO': m.get('card_no', ''),
        'COINS': str(ctx.get('coins', ctx.get('eligible_coins', 0))),
        'AMOUNT': str(ctx.get('amount', 0)),
        'BUSINESS': ctx.get('business', 'SKG ONE'),
        'BALANCE': str(ctx.get('balance', ctx.get('eligible_coins', 0))),
        'DATE': ctx.get('date', ''),
    }
    for k, v in values.items():
        t = t.replace('{{' + k + '}}', str(v))
    return t

def reminder_action(key, m, **ctx):
    text = render_reminder(key, m, **ctx)
    return {
        'key': key,
        'title': WHATSAPP_REMINDER_TEMPLATES[key]['title'],
        'message': text,
        'whatsapp_link': whatsapp_link(m.get('mobile', ''), text),
    }

def member_transaction_dates(mid):
    dates = []
    dates += [x.get('bill_date') for x in db.earnings.find({'member_id': mid}, {'bill_date': 1}) if x.get('bill_date')]
    dates += [x.get('bill_date') for x in db.redemptions.find({'member_id': mid}, {'bill_date': 1}) if x.get('bill_date')]
    return [x for x in dates if x]

def reminder_qualifiers(key, days=40):
    """Find members who qualify for a date/inactivity reminder as of today's IST date."""
    expire_old()
    target = today()
    out = []
    if key in ('SMART_COINS_EXPIRING_30', 'SMART_COINS_EXPIRING_7'):
        target = today() + timedelta(days=30 if key.endswith('_30') else 7)
        target_iso = iso(target)
        pipeline = [
            {'$match': {'status': 'ACTIVE', 'expiry_on': target_iso, 'available_on': {'$lte': iso(today())}, 'remaining_coins': {'$gt': 0}}},
            {'$group': {'_id': '$member_id', 'coins': {'$sum': '$remaining_coins'}}},
        ]
        for row in db.coin_lots.aggregate(pipeline):
            m = db.members.find_one({'_id': row['_id'], 'active': True})
            if not m: continue
            credited, eligible, _ = balances(m['_id'])
            out.append({**reminder_action(key, m, coins=int(row['coins']), balance=eligible, date=target_iso),
                        'card_no': m['card_no'], 'name': m['name'], 'mobile': m['mobile'],
                        'eligible_coins': eligible, 'qualifying_coins': int(row['coins']), 'qualifying_date': target_iso})
        return sorted(out, key=lambda x: x['name'].lower())

    if key in ('BIRTHDAY_7', 'BIRTHDAY_2', 'BIRTHDAY_TODAY', 'ANNIVERSARY_7', 'ANNIVERSARY_2', 'ANNIVERSARY_TODAY'):
        is_ann = key.startswith('ANNIVERSARY')
        offset = 0 if key.endswith('_TODAY') else (7 if key.endswith('_7') else 2)
        target = today() + timedelta(days=offset)
        field = 'anniversary' if is_ann else 'dob'
        for m in db.members.find({'active': True, field: {'$nin': ['', None]}}):
            raw = m.get(field, '')
            try:
                md = datetime.strptime(raw, '%Y-%m-%d').date()
            except (ValueError, TypeError):
                continue
            if md.month == target.month and md.day == target.day:
                _, eligible, _ = balances(m['_id'])
                out.append({**reminder_action(key, m, balance=eligible),
                            'card_no': m['card_no'], 'name': m['name'], 'mobile': m['mobile'],
                            'eligible_coins': eligible, 'qualifying_date': target.isoformat(),
                            'event_date': raw})
        return sorted(out, key=lambda x: x['name'].lower())

    if key == 'NO_TRANSACTION_40':
        cutoff = today() - timedelta(days=days)
        for m in db.members.find({'active': True}):
            dates = member_transaction_dates(m['_id'])
            created = m.get('created_at')
            joined = created.date() if isinstance(created, datetime) else None
            last = max(dates) if dates else (iso(joined) if joined else '')
            if not last or last >= iso(cutoff):
                continue
            _, eligible, _ = balances(m['_id'])
            out.append({**reminder_action(key, m, balance=eligible, eligible_coins=eligible),
                        'card_no': m['card_no'], 'name': m['name'], 'mobile': m['mobile'],
                        'last_transaction': last, 'eligible_coins': eligible,
                        'qualifying_date': iso(cutoff)})
        return sorted(out, key=lambda x: x['last_transaction'] or '')
    raise HTTPException(400, 'Unsupported reminder template.')

@app.post('/earn')
def earn(x: EarnReq, authorization: str | None = Header(None)):
    u = auth(authorization); expire_old(); require_store_access(u, x.store)
    if x.amount < 500: raise HTTPException(400, 'Minimum bill for earning is ₹500.')
    m = member(x.card_no); d = transaction_date(x.bill_date, u)
    if d > today(): raise HTTPException(400, 'Bill date cannot be in the future')
    if db.earnings.find_one({'bill_no': x.bill_no}) or db.redemptions.find_one({'bill_no': x.bill_no}): raise HTTPException(409, 'Bill number already used.')
    coins = int((x.amount / 10) * STORES[x.store]['coins_per_10'])
    lot = {'member_id': m['_id'], 'source_type': 'PURCHASE', 'bill_no': x.bill_no, 'bill_date': iso(d), 'amount': x.amount, 'coins': coins, 'remaining_coins': coins, 'available_on': iso(d + timedelta(days=1)), 'expiry_on': iso(add_year(d)), 'store': x.store, 'status': 'ACTIVE', 'created_by': u['_id'], 'created_at': now()}
    try:
        db.earnings.insert_one({**lot}); db.coin_lots.insert_one(lot)
    except DuplicateKeyError: raise HTTPException(409, 'Bill number already used.')
    audit('EARN', x.bill_no, f'{x.store};{coins};bill_date={d}', u['_id'])
    _, eligible_after, _ = balances(m['_id'])
    action = reminder_action('SMART_COINS_EARNED', m, coins=coins, amount=f'{x.amount:,.2f}', business=STORES[x.store]['name'], balance=eligible_after)
    audit('WHATSAPP_READY', x.bill_no, 'template=SMART_COINS_EARNED')
    return {'coins': coins, 'available_on': lot['available_on'], 'expiry_on': lot['expiry_on'], 'bill_date': iso(d), 'whatsapp': action}

@app.get('/redeem/preview')
def preview(card_no: str, amount: float, authorization: str | None = Header(None)):
    u = auth(authorization); expire_old()
    if u.get('role') != 'ADMIN' and u.get('store') == 'SMART':
        raise HTTPException(403, 'Redemption is disabled at S-Mart.')
    m = member(card_no); credited, eligible, _ = balances(m['_id'])
    maximum = max(0, min(eligible, int((amount / 5) * COINS_PER_RUPEE), credited - 1))
    return {'credited': credited, 'eligible': eligible, 'maximum_coins': maximum, 'maximum_value': maximum / COINS_PER_RUPEE}

@app.post('/redeem')
def redeem(x: RedeemReq, authorization: str | None = Header(None)):
    u = auth(authorization); expire_old()
    if x.store == 'SMART': raise HTTPException(403, 'Redemption is disabled at S-Mart.')
    require_store_access(u, x.store)
    if x.amount < 500: raise HTTPException(400, 'Minimum bill for redemption is ₹500.')
    m = member(x.card_no); d = transaction_date(x.bill_date, u); credited, eligible, _ = balances(m['_id'])
    maxcoins = max(0, min(eligible, int((x.amount / 5) * COINS_PER_RUPEE), credited - 1))
    coins = maxcoins if x.requested_value is None else int(x.requested_value * COINS_PER_RUPEE)
    if coins <= 0: raise HTTPException(400, 'No redeemable Smart Coins available.')
    if coins > maxcoins: raise HTTPException(400, f'Maximum redeemable coins are {maxcoins:,}.')
    value = coins / COINS_PER_RUPEE; cash = round(x.amount - value, 2); fresh = int((cash / 10) * STORES[x.store]['coins_per_10'])
    if db.earnings.find_one({'bill_no': x.bill_no}) or db.redemptions.find_one({'bill_no': x.bill_no}): raise HTTPException(409, 'Bill number already used.')
    rid = secrets.token_hex(8)
    red = {'redemption_id': rid, 'member_id': m['_id'], 'bill_no': x.bill_no, 'bill_date': iso(d), 'amount': x.amount, 'coins': coins, 'value': value, 'cash_paid': cash, 'fresh_coins': fresh, 'store': x.store, 'status': 'COMPLETED', 'created_by': u['_id'], 'created_at': now()}
    db.redemptions.insert_one(red)
    rem = coins
    for lot in lots(m['_id']):
        take = min(rem, lot['remaining_coins'])
        if take:
            db.coin_lots.update_one({'_id': lot['_id']}, {'$inc': {'remaining_coins': -take}, '$set': {'status': 'CONSUMED' if lot['remaining_coins'] == take else 'ACTIVE'}})
            db.allocations.insert_one({'redemption_id': rid, 'lot_id': lot['_id'], 'coins': take})
            rem -= take
        if rem == 0: break
    if fresh > 0:
        db.coin_lots.insert_one({'member_id': m['_id'], 'source_type': 'REDEMPTION_CASH', 'bill_no': x.bill_no, 'amount': cash, 'coins': fresh, 'remaining_coins': fresh, 'available_on': iso(d + timedelta(days=1)), 'expiry_on': iso(add_year(d)), 'store': x.store, 'status': 'ACTIVE', 'created_by': u['_id'], 'created_at': now()})
    audit('REDEEM', rid, f'{x.store};{coins};value={value};cash={cash};fresh={fresh};bill_date={d}', u['_id'])
    filename = f'SmartCoins_{rid}.pdf'; c = canvas.Canvas(str(RECEIPTS / filename)); c.setFont('Helvetica-Bold', 18); c.drawString(50, 800, 'SMART COINS'); c.setFont('Helvetica', 11); y = 770
    for k, v in [('Receipt', rid), ('Store', STORES[x.store]['name']), ('Card', x.card_no), ('Member', m['name']), ('Bill', x.bill_no), ('Bill Amount', f'₹{x.amount:,.2f}'), ('Coins Redeemed', f'{coins:,}'), ('Redemption Value', f'₹{value:,.2f}'), ('Cash Paid', f'₹{cash:,.2f}'), ('Fresh Coins', f'{fresh:,}'), ('Date', now().strftime('%Y-%m-%d %H:%M:%S'))]: c.drawString(50, y, f'{k}: {v}'); y -= 25
    c.save()
    _, eligible_after, _ = balances(m['_id'])
    action = reminder_action('SMART_COINS_REDEEMED', m, coins=coins, business=STORES[x.store]['name'], balance=eligible_after)
    audit('WHATSAPP_READY', rid, 'template=SMART_COINS_REDEEMED')
    return {'receipt': filename, 'receipt_url': f'/receipts/{filename}', 'coins': coins, 'value': value, 'cash_paid': cash, 'fresh_coins': fresh, 'bill_date': iso(d), 'whatsapp': action}

@app.post('/admin/corrections')
def correction(x: CorrectionReq, authorization: str | None = Header(None)):
    u = auth(authorization); require_admin(u)
    if not x.reason.strip(): raise HTTPException(400, 'Correction reason is required.')
    if x.transaction_type.upper() == 'EARN':
        e = db.earnings.find_one({'bill_no': x.transaction_id})
        if not e: raise HTTPException(404, 'Bill credit transaction not found.')
        if e.get('status') == 'REVERSED': raise HTTPException(409, 'Transaction is already reversed.')
        db.earnings.update_one({'_id': e['_id']}, {'$set': {'status': 'REVERSED', 'reversed_at': now(), 'reversed_by': u['_id'], 'reversal_reason': x.reason}})
        db.coin_lots.update_many({'bill_no': x.transaction_id, 'source_type': 'PURCHASE'}, {'$set': {'status': 'REVERSED', 'remaining_coins': 0, 'reversed_at': now(), 'reversed_by': u['_id']}})
        audit('CORRECTION', x.transaction_id, f'EARN reversed;reason={x.reason}', u['_id'])
        return {'ok': True, 'message': 'Bill credit reversed; original history retained.'}
    if x.transaction_type.upper() == 'REDEEM':
        r = db.redemptions.find_one({'redemption_id': x.transaction_id})
        if not r: raise HTTPException(404, 'Redemption transaction not found.')
        if r.get('status') == 'REVERSED': raise HTTPException(409, 'Transaction is already reversed.')
        for a in db.allocations.find({'redemption_id': r['redemption_id']}):
            db.coin_lots.update_one({'_id': a['lot_id']}, {'$inc': {'remaining_coins': a['coins']}, '$set': {'status': 'ACTIVE'}})
        db.redemptions.update_one({'_id': r['_id']}, {'$set': {'status': 'REVERSED', 'reversed_at': now(), 'reversed_by': u['_id'], 'reversal_reason': x.reason}})
        db.coin_lots.update_many({'bill_no': r['bill_no'], 'source_type': 'REDEMPTION_CASH'}, {'$set': {'status': 'REVERSED', 'remaining_coins': 0, 'reversed_at': now(), 'reversed_by': u['_id']}})
        audit('CORRECTION', r['redemption_id'], f'REDEEM reversed;reason={x.reason}', u['_id'])
        return {'ok': True, 'message': 'Redemption reversed; original history retained.'}
    raise HTTPException(400, 'transaction_type must be EARN or REDEEM.')

@app.get('/receipts/{filename}')
def receipt(filename: str, authorization: str | None = Header(None)):
    auth(authorization); p = RECEIPTS / Path(filename).name
    if not p.exists(): raise HTTPException(404, 'Receipt not found')
    return FileResponse(p, media_type='application/pdf', filename=p.name)

@app.get('/dashboard')
def dashboard(authorization: str | None = Header(None)):
    u = auth(authorization); require_admin(u); expire_old(); rows = []
    for code, s in STORES.items():
        e = list(db.earnings.aggregate([{'$match': {'store': code, 'status': {'$ne': 'REVERSED'}}}, {'$group': {'_id': None, 'sales': {'$sum': '$amount'}, 'coins': {'$sum': '$coins'}}}]))
        r = list(db.redemptions.aggregate([{'$match': {'store': code, 'status': 'COMPLETED'}}, {'$group': {'_id': None, 'count': {'$sum': 1}, 'value': {'$sum': '$value'}, 'fresh': {'$sum': '$fresh_coins'}}}]))
        rows.append({'store': s['name'], 'percentage': s['percentage'], 'sales': e[0]['sales'] if e else 0, 'coins_issued': e[0]['coins'] if e else 0, 'redemptions': r[0]['count'] if r else 0, 'redemption_value': r[0]['value'] if r else 0, 'fresh_coins': r[0]['fresh'] if r else 0})
    return {'members': db.members.count_documents({'active': True}), 'stores': rows, 'role': u.get('role'), 'store': u.get('store')}

@app.get('/reminders/templates')
def reminder_templates(authorization: str | None = Header(None)):
    u = auth(authorization); require_admin(u)
    return [{'key': k, **v} for k, v in WHATSAPP_REMINDER_TEMPLATES.items()]

@app.get('/reminders/qualify/{key}')
def qualify_reminder(key: str, days: int = 40, authorization: str | None = Header(None)):
    u = auth(authorization); require_admin(u)
    if key not in WHATSAPP_REMINDER_TEMPLATES or key in ('MEMBERSHIP_CREATED', 'SMART_COINS_EARNED', 'SMART_COINS_REDEEMED'):
        raise HTTPException(400, 'This reminder is event-triggered and does not have a scheduled qualification query.')
    rows = reminder_qualifiers(key, days=days)
    return {'key': key, 'title': WHATSAPP_REMINDER_TEMPLATES[key]['title'], 'count': len(rows), 'members': rows}

@app.get('/reminders/inactive')
def inactive_members(days: int = 40, authorization: str | None = Header(None)):
    u = auth(authorization); require_admin(u)
    rows = reminder_qualifiers('NO_TRANSACTION_40', days=days)
    return rows

@app.get('/users')
def list_users(authorization: str | None = Header(None)):
    u = auth(authorization); require_admin(u)
    return [{'username': x['username'], 'role': x['role'], 'store': x.get('store'), 'active': x['active']} for x in db.users.find().sort('username', ASCENDING)]

@app.post('/users')
def create_user(x: UserReq, authorization: str | None = Header(None)):
    u = auth(authorization); require_admin(u); username = x.username.strip()
    if not username or not x.password: raise HTTPException(400, 'Username and password are required.')
    if x.role not in ('ADMIN', 'STORE'): raise HTTPException(400, 'Role must be ADMIN or STORE.')
    if x.role == 'STORE' and x.store not in STORES: raise HTTPException(400, 'A store must be assigned to a store user.')
    if x.role == 'ADMIN': x.store = None
    if db.users.find_one({'username': username}): raise HTTPException(409, 'That username already exists.')
    salt = secrets.token_hex(16); db.users.insert_one({'username': username, 'password_hash': hashpw(x.password, salt), 'salt': salt, 'role': x.role, 'store': x.store, 'active': True, 'created_at': now()})
    audit('USER_CREATE', username, f'role={x.role};store={x.store}', u['_id']); return {'username': username, 'role': x.role, 'store': x.store}

@app.post('/users/{username}/toggle')
def toggle_user(username: str, authorization: str | None = Header(None)):
    u = auth(authorization); require_admin(u); target = db.users.find_one({'username': username})
    if not target: raise HTTPException(404, 'User not found')
    if target['username'] == u['username']: raise HTTPException(400, "You can't deactivate your own account.")
    new_active = not target['active']
    if not new_active and target['role'] == 'ADMIN' and db.users.count_documents({'role': 'ADMIN', 'active': True}) <= 1: raise HTTPException(400, 'At least one active admin must remain.')
    db.users.update_one({'_id': target['_id']}, {'$set': {'active': new_active}}); audit('USER_TOGGLE', username, f'active={new_active}', u['_id'])
    return {'username': username, 'active': new_active}

@app.post('/backup')
def backup(authorization: str | None = Header(None)):
    u = auth(authorization); require_admin(u); out = BASE / 'mongo_backups' / datetime.now().strftime('%Y%m%d_%H%M%S'); out.mkdir(parents=True, exist_ok=True)
    try: subprocess.run(['mongodump', '--uri', MONGODB_URI, '--db', MONGODB_DB, '--out', str(out)], check=True, capture_output=True, text=True)
    except FileNotFoundError: raise HTTPException(500, 'mongodump is not installed on the server')
    except subprocess.CalledProcessError as e: raise HTTPException(500, e.stderr[-1000:])
    return {'path': str(out)}
