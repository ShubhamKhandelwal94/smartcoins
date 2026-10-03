import os, hashlib, secrets, json, subprocess, re
from pathlib import Path
from datetime import date, datetime, timedelta
from typing import Optional
from urllib.parse import quote
from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError, PyMongoError
from reportlab.pdfgen import canvas

BASE = Path(__file__).resolve().parent; RECEIPTS = BASE / 'receipts'; RECEIPTS.mkdir(exist_ok=True)
MONGODB_URI = os.getenv('MONGODB_URI')
MONGODB_DB = os.getenv('MONGODB_DB', 'smart_coins')
if not MONGODB_URI:
    raise RuntimeError('MONGODB_URI environment variable is not set. Set it before starting the server (see .env.example).')
client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=10000)
db = client[MONGODB_DB]
app = FastAPI(title='Smart Coins Central API', version='3.0.0')

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
COINS_PER_RUPEE = 10  # redemption value: 10 Smart Coins = ₹1

# Reminders: finds members with no earn/redeem transaction in the configured
# window and builds a free, click-to-send wa.me link per member. See README
# "Inactivity reminders" for why this isn't an automatic send.
WHATSAPP_COUNTRY_CODE = os.getenv('WHATSAPP_COUNTRY_CODE', '91')
REMINDER_MESSAGE_TEMPLATE = os.getenv(
    'REMINDER_MESSAGE_TEMPLATE',
    "Hi {name}! We miss you at Smart Coins. You have {eligible} eligible coins waiting on card {card_no} — "
    "come shop with us soon before they expire. 🙂"
)


def whatsapp_link(mobile: str, text: str) -> str:
    digits = re.sub(r'\D', '', mobile or '')
    if digits.startswith('0'):
        digits = digits.lstrip('0')
    if not digits.startswith(WHATSAPP_COUNTRY_CODE) and len(digits) <= 10:
        digits = WHATSAPP_COUNTRY_CODE + digits
    return f'https://wa.me/{digits}?text={quote(text)}'


class LoginReq(BaseModel): username:str; password:str
class MemberReq(BaseModel): name:str; mobile:str; email:str=''; address:str=''; dob:str=''; anniversary:str=''
class EarnReq(BaseModel): card_no:str; bill_no:str; bill_date:str; amount:float=Field(gt=0); store:str
class RedeemReq(BaseModel): card_no:str; bill_no:str; bill_date:str; amount:float=Field(gt=0); store:str; requested_value:Optional[float]=None
class UserReq(BaseModel): username:str; password:str; role:str='STAFF'

def now(): return datetime.utcnow()
def today(): return date.today()
def iso(d): return d.isoformat()
def add_year(d):
    try:return d.replace(year=d.year+1)
    except ValueError:return d.replace(year=d.year+1,day=28)
def hashpw(p,s): return hashlib.pbkdf2_hmac('sha256',p.encode(),s.encode(),180000).hex()
def parse_date(s):
    try: return datetime.strptime(s,'%Y-%m-%d').date()
    except (ValueError,TypeError): raise HTTPException(400,f"Invalid bill date '{s}' — expected format YYYY-MM-DD.")
def audit(event,ref,details,uid=None): db.audit.insert_one({'event':event,'ref':ref,'details':details,'user_id':uid,'created_at':now()})
def auth(a):
    if not a or not a.startswith('Bearer '): raise HTTPException(401,'Login required')
    s=db.sessions.find_one({'token':a[7:]})
    if not s: raise HTTPException(401,'Invalid session')
    u=db.users.find_one({'_id':s['user_id'],'active':True})
    if not u: raise HTTPException(401,'Invalid session')
    return u
def expire_old():
    d=iso(today());
    for lot in db.coin_lots.find({'status':'ACTIVE','expiry_on':{'$lt':d},'remaining_coins':{'$gt':0}}):
        left=lot['remaining_coins']; db.coin_lots.update_one({'_id':lot['_id']},{'$set':{'status':'EXPIRED','remaining_coins':0}})
        db.expiry_log.insert_one({'lot_id':lot['_id'],'expired_coins':left,'expiry_date':lot['expiry_on'],'created_at':now()}); audit('COIN_EXPIRY',str(lot['_id']),f'expired={left};expiry={lot["expiry_on"]}')
def member(card):
    m=db.members.find_one({'card_no':card,'active':True})
    if not m: raise HTTPException(404,'Active member not found')
    return m
def balances(mid):
    credited=sum(x.get('coins',0) for x in db.coin_lots.find({'member_id':mid}))
    eligible=sum(x.get('remaining_coins',0) for x in db.coin_lots.find({'member_id':mid,'status':'ACTIVE','available_on':{'$lte':iso(today())},'expiry_on':{'$gte':iso(today())},'remaining_coins':{'$gt':0}}))
    redeemed=sum(x.get('coins',0) for x in db.redemptions.find({'member_id':mid,'status':'COMPLETED'}))
    return int(credited),int(eligible),int(redeemed)
def lots(mid): return list(db.coin_lots.find({'member_id':mid,'status':'ACTIVE','available_on':{'$lte':iso(today())},'expiry_on':{'$gte':iso(today())},'remaining_coins':{'$gt':0}}).sort([('expiry_on',ASCENDING),('created_at',ASCENDING)]))
def ensure_indexes():
    db.users.create_index('username',unique=True); db.members.create_index('card_no',unique=True); db.members.create_index('mobile',unique=True,partialFilterExpression={'active':True}); db.earnings.create_index('bill_no',unique=True); db.redemptions.create_index('bill_no',unique=True); db.sessions.create_index('token',unique=True)
    if not db.users.find_one({'username':'admin'}):
        salt=secrets.token_hex(16); db.users.insert_one({'username':'admin','password_hash':hashpw('admin',salt),'salt':salt,'role':'ADMIN','active':True,'created_at':now()})
ensure_indexes()

WEB_INDEX = BASE / 'web' / 'index.html'
@app.get('/', include_in_schema=False)
def web_ui():
    if WEB_INDEX.exists():
        return FileResponse(WEB_INDEX, media_type='text/html')
    return {'status': 'Smart Coins API running'}

@app.get('/health')
def health():
    client.admin.command('ping'); return {'status':'ok','database':MONGODB_DB}
@app.post('/login')
def login(x:LoginReq):
    u=db.users.find_one({'username':x.username,'active':True})
    if not u or hashpw(x.password,u['salt'])!=u['password_hash']: raise HTTPException(401,'Invalid username/password')
    t=secrets.token_urlsafe(32); db.sessions.insert_one({'token':t,'user_id':u['_id'],'created_at':now()}); return {'token':t,'username':u['username'],'role':u['role']}
@app.get('/members')
def members(q:str='',authorization:str|None=Header(None)):
    auth(authorization); expire_old(); rg={'$regex':q,'$options':'i'}; out=[]
    for m in db.members.find({'active':True,'$or':[{'card_no':rg},{'name':rg},{'mobile':rg}]}).sort('name',ASCENDING):
        c,e,r=balances(m['_id']); out.append({'card_no':m['card_no'],'name':m['name'],'mobile':m['mobile'],'email':m.get('email',''),'address':m.get('address',''),'dob':m.get('dob',''),'anniversary':m.get('anniversary',''),'credited':c,'eligible':e,'redeemed':r})
    return out
@app.post('/members')
def create_member(x:MemberReq,authorization:str|None=Header(None)):
    u=auth(authorization); mobile=x.mobile.strip()
    if not mobile: raise HTTPException(400,'Mobile number is required')
    if db.members.find_one({'mobile':mobile,'active':True}): raise HTTPException(409,'This mobile number already has an active membership.')
    n=db.counters.find_one_and_update({'_id':'card'},{'$inc':{'value':1}},upsert=True,return_document=True); card=f'SC-{n["value"]:08d}'
    try: db.members.insert_one({'card_no':card,'name':x.name.strip(),'mobile':mobile,'email':x.email,'address':x.address,'dob':x.dob,'anniversary':x.anniversary,'active':True,'created_at':now()})
    except DuplicateKeyError: raise HTTPException(409,'Mobile number already has an active membership.')
    audit('MEMBER_CREATE',card,card,u['_id']); return {'card_no':card}
@app.post('/earn')
def earn(x:EarnReq,authorization:str|None=Header(None)):
    u=auth(authorization); expire_old()
    if x.amount<500: raise HTTPException(400,'Minimum bill for earning is ₹500.')
    if x.store not in STORES: raise HTTPException(400,'Invalid store')
    m=member(x.card_no); d=parse_date(x.bill_date)
    if d>today(): raise HTTPException(400,'Bill date cannot be in the future')
    if db.earnings.find_one({'bill_no':x.bill_no}) or db.redemptions.find_one({'bill_no':x.bill_no}): raise HTTPException(409,'Bill number already used.')
    coins=int((x.amount/10)*STORES[x.store]['coins_per_10']); lot={'member_id':m['_id'],'source_type':'PURCHASE','bill_no':x.bill_no,'bill_date':iso(d),'amount':x.amount,'coins':coins,'remaining_coins':coins,'available_on':iso(d+timedelta(days=1)),'expiry_on':iso(add_year(d)),'store':x.store,'status':'ACTIVE','created_by':u['_id'],'created_at':now()}
    db.earnings.insert_one({**lot}); db.coin_lots.insert_one(lot); audit('EARN',x.bill_no,f'{x.store};{coins}',u['_id']); return {'coins':coins,'available_on':lot['available_on'],'expiry_on':lot['expiry_on']}
@app.get('/redeem/preview')
def preview(card_no:str,amount:float,authorization:str|None=Header(None)):
    auth(authorization); expire_old(); m=member(card_no); credited,eligible,_=balances(m['_id']); maximum=max(0,min(eligible,int((amount/5)*COINS_PER_RUPEE),credited-1)); return {'credited':credited,'eligible':eligible,'maximum_coins':maximum,'maximum_value':maximum/COINS_PER_RUPEE}
@app.post('/redeem')
def redeem(x:RedeemReq,authorization:str|None=Header(None)):
    u=auth(authorization); expire_old()
    if x.amount<500: raise HTTPException(400,'Minimum bill for redemption is ₹500.')
    if x.store not in STORES: raise HTTPException(400,'Invalid store')
    m=member(x.card_no); d=parse_date(x.bill_date); credited,eligible,_=balances(m['_id']); maxcoins=max(0,min(eligible,int((x.amount/5)*COINS_PER_RUPEE),credited-1))
    if x.requested_value is None: coins=maxcoins
    else: coins=int(x.requested_value*COINS_PER_RUPEE)
    if coins<=0: raise HTTPException(400,'No redeemable Smart Coins available.')
    if coins>maxcoins: raise HTTPException(400,f'Maximum redeemable coins are {maxcoins:,}.')
    value=coins/COINS_PER_RUPEE; cash=round(x.amount-value,2); fresh=int((cash/10)*STORES[x.store]['coins_per_10'])
    if db.earnings.find_one({'bill_no':x.bill_no}) or db.redemptions.find_one({'bill_no':x.bill_no}): raise HTTPException(409,'Bill number already used.')
    rid=secrets.token_hex(8); red={'redemption_id':rid,'member_id':m['_id'],'bill_no':x.bill_no,'bill_date':iso(d),'amount':x.amount,'coins':coins,'value':value,'cash_paid':cash,'fresh_coins':fresh,'store':x.store,'status':'COMPLETED','created_by':u['_id'],'created_at':now()}; db.redemptions.insert_one(red)
    rem=coins
    for lot in lots(m['_id']):
        take=min(rem,lot['remaining_coins']);
        if take: db.coin_lots.update_one({'_id':lot['_id']},{'$inc':{'remaining_coins':-take},'$set':{'status':'CONSUMED' if lot['remaining_coins']==take else 'ACTIVE'}}); db.allocations.insert_one({'redemption_id':rid,'lot_id':lot['_id'],'coins':take}); rem-=take
        if rem==0: break
    if fresh>0:
        db.coin_lots.insert_one({'member_id':m['_id'],'source_type':'REDEMPTION_CASH','bill_no':x.bill_no,'bill_date':iso(d),'amount':cash,'coins':fresh,'remaining_coins':fresh,'available_on':iso(d+timedelta(days=1)),'expiry_on':iso(add_year(d)),'store':x.store,'status':'ACTIVE','created_by':u['_id'],'created_at':now()})
    audit('REDEEM',rid,f'{x.store};{coins};value={value};cash={cash};fresh={fresh}',u['_id'])
    filename=f'SmartCoins_{rid}.pdf'; c=canvas.Canvas(str(RECEIPTS/filename)); c.setFont('Helvetica-Bold',18); c.drawString(50,800,'SMART COINS'); c.setFont('Helvetica',11); y=770
    for k,v in [('Receipt',rid),('Store',STORES[x.store]['name']),('Card',x.card_no),('Member',m['name']),('Bill',x.bill_no),('Bill Amount',f'₹{x.amount:,.2f}'),('Coins Redeemed',f'{coins:,}'),('Redemption Value',f'₹{value:,.2f}'),('Cash Paid',f'₹{cash:,.2f}'),('Fresh Coins',f'{fresh:,}'),('Date',datetime.now().strftime('%Y-%m-%d %H:%M:%S'))]: c.drawString(50,y,f'{k}: {v}'); y-=25
    c.save(); return {'receipt':filename,'receipt_url':f'/receipts/{filename}','coins':coins,'value':value,'cash_paid':cash,'fresh_coins':fresh}
@app.get('/receipts/{filename}')
def receipt(filename:str,authorization:str|None=Header(None)):
    auth(authorization); p=RECEIPTS/Path(filename).name
    if not p.exists(): raise HTTPException(404,'Receipt not found')
    return FileResponse(p,media_type='application/pdf',filename=p.name)
@app.get('/dashboard')
def dashboard(authorization:str|None=Header(None)):
    auth(authorization); expire_old(); rows=[]
    for code,s in STORES.items():
        e=list(db.earnings.aggregate([{'$match':{'store':code}},{'$group':{'_id':None,'sales':{'$sum':'$amount'},'coins':{'$sum':'$coins'}}}]))
        r=list(db.redemptions.aggregate([{'$match':{'store':code,'status':'COMPLETED'}},{'$group':{'_id':None,'count':{'$sum':1},'value':{'$sum':'$value'},'fresh':{'$sum':'$fresh_coins'}}}]))
        rows.append({'store':s['name'],'percentage':s['percentage'],'sales':e[0]['sales'] if e else 0,'coins_issued':e[0]['coins'] if e else 0,'redemptions':r[0]['count'] if r else 0,'redemption_value':r[0]['value'] if r else 0,'fresh_coins':r[0]['fresh'] if r else 0})
    return {'members':db.members.count_documents({'active':True}),'stores':rows}
@app.get('/reminders/inactive')
def inactive_members(days:int=90,authorization:str|None=Header(None)):
    """Active members with no earn/redeem transaction in the last `days` days
    (default 90, i.e. ~3 months). 'Last transaction' falls back to the
    member's join date if they've never transacted at all."""
    auth(authorization); expire_old()
    cutoff=iso(today()-timedelta(days=days)); out=[]
    for m in db.members.find({'active':True}):
        dates=[r['bill_date'] for r in db.earnings.find({'member_id':m['_id']},{'bill_date':1})]
        dates+=[r['bill_date'] for r in db.redemptions.find({'member_id':m['_id']},{'bill_date':1})]
        last=max(dates) if dates else iso(m['created_at'].date()) if isinstance(m.get('created_at'),datetime) else ''
        if last and last>=cutoff: continue
        credited,eligible,_=balances(m['_id'])
        text=REMINDER_MESSAGE_TEMPLATE.format(name=m['name'],card_no=m['card_no'],eligible=eligible,credited=credited)
        out.append({'card_no':m['card_no'],'name':m['name'],'mobile':m['mobile'],'last_transaction':last or None,'eligible_coins':eligible,'message':text,'whatsapp_link':whatsapp_link(m['mobile'],text)})
    out.sort(key=lambda r:r['last_transaction'] or '')
    return out
def require_admin(u):
    if u['role'] != 'ADMIN':
        raise HTTPException(403, 'Admin only')

@app.get('/users')
def list_users(authorization:str|None=Header(None)):
    u=auth(authorization); require_admin(u)
    return [{'username':x['username'],'role':x['role'],'active':x['active']} for x in db.users.find().sort('username',ASCENDING)]

@app.post('/users')
def create_user(x:UserReq,authorization:str|None=Header(None)):
    u=auth(authorization); require_admin(u)
    username=x.username.strip()
    if not username or not x.password: raise HTTPException(400,'Username and password are required.')
    if x.role not in ('ADMIN','STAFF'): raise HTTPException(400,'Role must be ADMIN or STAFF.')
    if db.users.find_one({'username':username}): raise HTTPException(409,'That username already exists.')
    salt=secrets.token_hex(16)
    db.users.insert_one({'username':username,'password_hash':hashpw(x.password,salt),'salt':salt,'role':x.role,'active':True,'created_at':now()})
    audit('USER_CREATE',username,x.role,u['_id'])
    return {'username':username,'role':x.role}

@app.post('/users/{username}/toggle')
def toggle_user(username:str,authorization:str|None=Header(None)):
    u=auth(authorization); require_admin(u)
    target=db.users.find_one({'username':username})
    if not target: raise HTTPException(404,'User not found')
    if target['username']==u['username']: raise HTTPException(400,"You can't deactivate your own account.")
    new_active = not target['active']
    if not new_active and target['role']=='ADMIN' and db.users.count_documents({'role':'ADMIN','active':True})<=1:
        raise HTTPException(400,'At least one active admin must remain.')
    db.users.update_one({'_id':target['_id']},{'$set':{'active':new_active}})
    audit('USER_TOGGLE',username,f'active={new_active}',u['_id'])
    return {'username':username,'active':new_active}

@app.post('/backup')
def backup(authorization:str|None=Header(None)):
    u=auth(authorization); require_admin(u)
    out=BASE/'mongo_backups'/datetime.now().strftime('%Y%m%d_%H%M%S'); out.mkdir(parents=True,exist_ok=True)
    try: subprocess.run(['mongodump','--uri',MONGODB_URI,'--db',MONGODB_DB,'--out',str(out)],check=True,capture_output=True,text=True)
    except FileNotFoundError: raise HTTPException(500,'mongodump is not installed on the server')
    except subprocess.CalledProcessError as e: raise HTTPException(500,e.stderr[-1000:])
    return {'path':str(out)}
