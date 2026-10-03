
import sqlite3, shutil, os, hashlib, secrets, csv, json, zipfile
from pathlib import Path
from datetime import datetime, date, timedelta
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog

APP = "Smart Coins — Production Edition"
BASE = Path(__file__).resolve().parent
DATA = BASE/"data"; BACKUPS = BASE/"backups"; EXPORTS = BASE/"exports"
DB_PATH = DATA/"smart_coins.db"
for p in (DATA,BACKUPS,EXPORTS): p.mkdir(exist_ok=True)

DF="%Y-%m-%d"; DTF="%Y-%m-%d %H:%M:%S"
def now(): return datetime.now().strftime(DTF)
def today(): return date.today()
def fmt(v): return f"₹{float(v):,.2f}"
def hashpw(p,s=None):
    s=s or secrets.token_hex(16)
    return s, hashlib.pbkdf2_hmac("sha256",p.encode(),s.encode(),180000).hex()
def add_months(d,n):
    import calendar
    x=d.month-1+n; y=d.year+x//12; m=x%12+1
    return date(y,m,min(d.day,calendar.monthrange(y,m)[1]))

class DB:
    def __init__(self):
        self.c=sqlite3.connect(DB_PATH,timeout=30)
        self.c.row_factory=sqlite3.Row
        self.c.execute("PRAGMA foreign_keys=ON")
        self.c.execute("PRAGMA journal_mode=WAL")
        self.c.execute("PRAGMA synchronous=FULL")
        self.schema()
    def schema(self):
        self.c.executescript("""
        CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY,v TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS users(
          id INTEGER PRIMARY KEY,username TEXT UNIQUE NOT NULL,password_hash TEXT NOT NULL,
          salt TEXT NOT NULL,role TEXT NOT NULL,active INTEGER DEFAULT 1,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS members(
          id INTEGER PRIMARY KEY,card_no TEXT UNIQUE NOT NULL,name TEXT NOT NULL,phone TEXT,email TEXT,
          address TEXT,joined_on TEXT NOT NULL,active INTEGER DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS earn(
          id INTEGER PRIMARY KEY,member_id INTEGER NOT NULL,bill_no TEXT UNIQUE NOT NULL,bill_date TEXT NOT NULL,
          bill_amount REAL NOT NULL,coins INTEGER NOT NULL,eligible_on TEXT NOT NULL,expiry_on TEXT NOT NULL,
          status TEXT DEFAULT 'ACTIVE',created_by INTEGER,created_at TEXT NOT NULL,
          FOREIGN KEY(member_id) REFERENCES members(id),FOREIGN KEY(created_by) REFERENCES users(id));
        CREATE TABLE IF NOT EXISTS redeem(
          id INTEGER PRIMARY KEY,member_id INTEGER NOT NULL,bill_no TEXT UNIQUE NOT NULL,bill_date TEXT NOT NULL,
          bill_amount REAL NOT NULL,coins INTEGER NOT NULL,value REAL NOT NULL,cash_paid REAL NOT NULL,
          fresh_coins INTEGER NOT NULL,status TEXT DEFAULT 'COMPLETED',created_by INTEGER,created_at TEXT NOT NULL,
          FOREIGN KEY(member_id) REFERENCES members(id),FOREIGN KEY(created_by) REFERENCES users(id));
        CREATE TABLE IF NOT EXISTS allocations(
          id INTEGER PRIMARY KEY,redeem_id INTEGER NOT NULL,earn_id INTEGER NOT NULL,coins INTEGER NOT NULL,
          FOREIGN KEY(redeem_id) REFERENCES redeem(id),FOREIGN KEY(earn_id) REFERENCES earn(id));
        CREATE TABLE IF NOT EXISTS reversals(
          id INTEGER PRIMARY KEY,kind TEXT NOT NULL,ref_id INTEGER NOT NULL,reason TEXT NOT NULL,
          created_by INTEGER,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS day_close(
          id INTEGER PRIMARY KEY,close_date TEXT UNIQUE NOT NULL,total_sales REAL NOT NULL,total_redemptions REAL NOT NULL,
          coins_issued INTEGER NOT NULL,coins_redeemed INTEGER NOT NULL,cash_collected REAL NOT NULL,
          closed_by INTEGER,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS audit(
          id INTEGER PRIMARY KEY,event TEXT NOT NULL,ref_table TEXT,ref_id INTEGER,details TEXT,
          user_id INTEGER,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS backups(
          id INTEGER PRIMARY KEY,path TEXT NOT NULL,created_at TEXT NOT NULL,sha256 TEXT NOT NULL);
        """)
        defaults={"coin_per_rupee":"1","redeem_coins_per_rupee":"100","bill_multiple":"5",
                  "wait_days":"4","expiry_months":"4","backup_path":str(BACKUPS),"keep_backups":"30"}
        for k,v in defaults.items(): self.c.execute("INSERT OR IGNORE INTO settings VALUES(?,?)",(k,v))
        if not self.c.execute("SELECT 1 FROM users").fetchone():
            salt,h=hashpw("admin")
            self.c.execute("INSERT INTO users(username,password_hash,salt,role,created_at) VALUES(?,?,?,?,?)",
                           ("admin",h,salt,"ADMIN",now()))
        self.c.commit()
    def s(self,k): return self.c.execute("SELECT v FROM settings WHERE k=?",(k,)).fetchone()["v"]
    def audit(self,event,table=None,rid=None,details="",uid=None):
        self.c.execute("INSERT INTO audit(event,ref_table,ref_id,details,user_id,created_at) VALUES(?,?,?,?,?,?)",
                       (event,table,rid,details,uid,now()))
    def login(self,u,p):
        r=self.c.execute("SELECT * FROM users WHERE username=? AND active=1",(u,)).fetchone()
        if not r:return None
        if hashlib.pbkdf2_hmac("sha256",p.encode(),r["salt"].encode(),180000).hex()==r["password_hash"]:return r
        return None
    def members(self,q=""):
        x=f"%{q}%"
        return self.c.execute("""SELECT m.*,
        COALESCE((SELECT SUM(coins) FROM earn e WHERE e.member_id=m.id AND e.status='ACTIVE'),0) earned,
        COALESCE((SELECT SUM(coins) FROM redeem r WHERE r.member_id=m.id AND r.status='COMPLETED'),0) redeemed,
        COALESCE((SELECT SUM(e.coins)-COALESCE((SELECT SUM(a.coins) FROM allocations a JOIN redeem r2 ON r2.id=a.redeem_id WHERE a.earn_id=e.id AND r2.status='COMPLETED'),0)
          FROM earn e WHERE e.member_id=m.id AND e.status='ACTIVE' AND date(e.eligible_on)<=date(?) AND date(e.expiry_on)>=date(?)),0) eligible
        FROM members m WHERE m.card_no LIKE ? OR m.name LIKE ? OR m.phone LIKE ? ORDER BY m.name""",
        (today().isoformat(),today().isoformat(),x,x,x)).fetchall()
    def add_member(self,v,uid):
        ts=now(); cur=self.c.execute("""INSERT INTO members(card_no,name,phone,email,address,joined_on,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?)""",(v[0],v[1],v[2],v[3],v[4],today().isoformat(),ts,ts))
        self.audit("MEMBER_CREATE","members",cur.lastrowid,v[0],uid);self.c.commit()
    def earn_bill(self,card,bill,bdate,amt,uid):
        m=self.c.execute("SELECT * FROM members WHERE card_no=? AND active=1",(card,)).fetchone()
        if not m: raise ValueError("Active member not found.")
        d=datetime.strptime(bdate,DF).date(); coins=int(amt*float(self.s("coin_per_rupee")))
        if coins<=0: raise ValueError("Amount is too small.")
        if d>today(): raise ValueError("Bill date cannot be in the future.")
        el=d+timedelta(days=int(self.s("wait_days"))); ex=add_months(d,int(self.s("expiry_months")))
        cur=self.c.execute("""INSERT INTO earn(member_id,bill_no,bill_date,bill_amount,coins,eligible_on,expiry_on,created_by,created_at)
        VALUES(?,?,?,?,?,?,?,?,?)""",(m["id"],bill,d.isoformat(),amt,coins,el.isoformat(),ex.isoformat(),uid,now()))
        self.audit("BILL_CREDIT","earn",cur.lastrowid,f"{bill};{coins}",uid);self.c.commit();return coins
    def eligible(self,mid):
        return self.c.execute("""SELECT e.*,e.coins-COALESCE((SELECT SUM(a.coins) FROM allocations a JOIN redeem r ON r.id=a.redeem_id
        WHERE a.earn_id=e.id AND r.status='COMPLETED'),0) avail FROM earn e
        WHERE e.member_id=? AND e.status='ACTIVE' AND date(e.eligible_on)<=date(?) AND date(e.expiry_on)>=date(?) 
        AND e.coins-COALESCE((SELECT SUM(a.coins) FROM allocations a JOIN redeem r ON r.id=a.redeem_id WHERE a.earn_id=e.id AND r.status='COMPLETED'),0)>0
        ORDER BY date(e.expiry_on),e.id""",(mid,today().isoformat(),today().isoformat())).fetchall()
    def member_coin_balances(self, member_id):
        total_credited = self.c.execute("""
            SELECT COALESCE(SUM(coins),0) AS n
            FROM earn
            WHERE member_id=? AND status='ACTIVE'
        """, (member_id,)).fetchone()["n"]
        eligible_available = self.c.execute("""
            SELECT COALESCE(SUM(
                e.coins-COALESCE((
                    SELECT SUM(a.coins)
                    FROM allocations a
                    JOIN redeem r2 ON r2.id=a.redeem_id
                    WHERE a.earn_id=e.id AND r2.status='COMPLETED'
                ),0)
            ),0) AS n
            FROM earn e
            WHERE e.member_id=?
              AND e.status='ACTIVE'
              AND date(e.eligible_on)<=date(?)
              AND date(e.expiry_on)>=date(?)
        """, (member_id, today().isoformat(), today().isoformat())).fetchone()["n"]
        return int(total_credited), int(eligible_available)

    def redeem_bill(self,card,bill,bdate,amt,coins,uid):
        m=self.c.execute("SELECT * FROM members WHERE card_no=? AND active=1",(card,)).fetchone()
        if not m:raise ValueError("Active member not found.")
        value=coins/int(self.s("redeem_coins_per_rupee")); minimum=value*float(self.s("bill_multiple"))
        if amt<minimum:raise ValueError(f"Bill must be at least {fmt(minimum)}.")
        lots=self.eligible(m["id"]); avail=sum(int(x["avail"]) for x in lots)
        total_credited, eligible_available = self.member_coin_balances(m["id"])
        if total_credited <= 1:
            raise ValueError("Redemption is not allowed because the member must retain at least 1 credited Smart Coin.")
        if coins >= total_credited:
            raise ValueError(f"Redeemable Smart Coins must always be less than credited Smart Coins. Credited: {total_credited:,}; requested: {coins:,}.")
        if coins>avail:
            raise ValueError(f"Eligible balance is {avail:,} Smart Coins. Coins also cannot exceed the currently eligible balance.")
        cash=round(amt-value,2); fresh=int(cash*float(self.s("coin_per_rupee")))
        d=datetime.strptime(bdate,DF).date()
        cur=self.c.execute("""INSERT INTO redeem(member_id,bill_no,bill_date,bill_amount,coins,value,cash_paid,fresh_coins,created_by,created_at)
        VALUES(?,?,?,?,?,?,?,?,?,?)""",(m["id"],bill,d.isoformat(),amt,coins,value,cash,fresh,uid,now()))
        rid=cur.lastrowid; rem=coins
        for x in lots:
            u=min(rem,int(x["avail"]))
            if u:self.c.execute("INSERT INTO allocations(redeem_id,earn_id,coins) VALUES(?,?,?)",(rid,x["id"],u))
            rem-=u
            if rem==0:break
        self.audit("REDEMPTION","redeem",rid,f"{bill};{coins};{value};{cash};fresh={fresh}",uid)
        self.c.commit();return value,cash,fresh,rid
    def reverse(self,kind,rid,reason,uid):
        table="earn" if kind=="EARN" else "redeem"
        r=self.c.execute(f"SELECT * FROM {table} WHERE id=?",(rid,)).fetchone()
        if not r:raise ValueError("Transaction not found.")
        if self.c.execute("SELECT 1 FROM reversals WHERE kind=? AND ref_id=?",(kind,rid)).fetchone():raise ValueError("Already reversed.")
        self.c.execute("INSERT INTO reversals(kind,ref_id,reason,created_by,created_at) VALUES(?,?,?,?,?)",(kind,rid,reason,uid,now()))
        self.c.execute(f"UPDATE {table} SET status='REVERSED' WHERE id=?",(rid,))
        self.audit("REVERSAL",table,rid,reason,uid);self.c.commit()
    def dayclose(self,d,uid):
        if self.c.execute("SELECT 1 FROM day_close WHERE close_date=?",(d,)).fetchone():raise ValueError("Day already closed.")
        s=self.c.execute("""SELECT COALESCE(SUM(bill_amount),0) sales,COALESCE(SUM(coins),0) issued FROM earn
          WHERE bill_date=? AND status='ACTIVE'""",(d,)).fetchone()
        r=self.c.execute("""SELECT COALESCE(SUM(bill_amount),0) bills,COALESCE(SUM(coins),0) coins,COALESCE(SUM(cash_paid),0) cash
          FROM redeem WHERE bill_date=? AND status='COMPLETED'""",(d,)).fetchone()
        cur=self.c.execute("""INSERT INTO day_close(close_date,total_sales,total_redemptions,coins_issued,coins_redeemed,cash_collected,closed_by,created_at)
        VALUES(?,?,?,?,?,?,?,?)""",(d,s["sales"],r["bills"],s["issued"],r["coins"],s["cash"],uid,now()))
        self.audit("DAY_CLOSE","day_close",cur.lastrowid,d,uid);self.c.commit()
    def backup(self,target=None):
        self.c.execute("PRAGMA wal_checkpoint(FULL)");self.c.commit()
        target=Path(target or self.s("backup_path"));target.mkdir(parents=True,exist_ok=True)
        p=target/f"smart_coins_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
        shutil.copy2(DB,p)
        h=hashlib.sha256(p.read_bytes()).hexdigest()
        self.c.execute("INSERT INTO backups(path,created_at,sha256) VALUES(?,?,?)",(str(p),now(),h))
        keep=int(self.s("keep_backups"))
        old=self.c.execute("SELECT id,path FROM backups ORDER BY id DESC").fetchall()
        for x in old[keep:]:
            try:Path(x["path"]).unlink(missing_ok=True)
            except:pass
            self.c.execute("DELETE FROM backups WHERE id=?",(x["id"],))
        self.c.commit();return p
    def stats(self):
        return self.c.execute("""SELECT
        (SELECT COUNT(*) FROM members WHERE active=1)m,
        (SELECT COALESCE(SUM(coins),0) FROM earn WHERE status='ACTIVE')e,
        (SELECT COALESCE(SUM(coins),0) FROM redeem WHERE status='COMPLETED')r,
        (SELECT COALESCE(SUM(cash_paid),0) FROM redeem WHERE status='COMPLETED')cash""").fetchone()
    def history(self):
        return self.c.execute("""SELECT * FROM (
        SELECT 'CREDIT' AS type,e.id,e.created_at,m.card_no,m.name,e.bill_no,e.bill_amount AS amount,e.coins,e.status
        FROM earn e JOIN members m ON m.id=e.member_id
        UNION ALL
        SELECT 'REDEEM' AS type,r.id,r.created_at,m.card_no,m.name,r.bill_no,r.bill_amount AS amount,-r.coins AS coins,r.status
        FROM redeem r JOIN members m ON m.id=r.member_id
        ) ORDER BY created_at DESC LIMIT 1000""").fetchall()

class Login(tk.Tk):
    def __init__(self):
        super().__init__();self.title(APP);self.geometry("420x280");self.resizable(False,False)
        ttk.Label(self,text="Smart Coins",font=("Arial",24,"bold")).pack(pady=25)
        f=ttk.Frame(self,padding=20);f.pack(fill="x")
        self.u=tk.StringVar(value="admin");self.p=tk.StringVar()
        ttk.Label(f,text="Username").pack(anchor="w");ttk.Entry(f,textvariable=self.u).pack(fill="x",pady=5)
        ttk.Label(f,text="Password").pack(anchor="w");ttk.Entry(f,textvariable=self.p,show="•").pack(fill="x",pady=5)
        ttk.Button(f,text="Login",command=self.go).pack(pady=12)
        ttk.Label(self,text="First login: admin / admin — change this immediately.",foreground="red").pack()
        self.db=DB()
    def go(self):
        u=self.db.login(self.u.get(),self.p.get())
        if not u:messagebox.showerror("Login failed","Invalid username or password.");return
        self.destroy();Main(u).mainloop()

class Main(tk.Tk):
    def __init__(self,user):
        super().__init__();self.user=user;self.db=DB();self.title(APP);self.geometry("1280x780")
        self.protocol("WM_DELETE_WINDOW",self.close);self.style();self.ui();self.refresh();self.after(60000,self.auto)
    def style(self):
        s=ttk.Style(self)
        try:s.theme_use("clam")
        except:pass
        s.configure("Treeview",rowheight=27);s.configure("H.TLabel",font=("Arial",22,"bold"))
    def ui(self):
        top=ttk.Frame(self,padding=12);top.pack(fill="x")
        ttk.Label(top,text="Smart Coins",style="H.TLabel").pack(side="left")
        ttk.Label(top,text=f"  {self.user['username']} ({self.user['role']})").pack(side="left")
        ttk.Button(top,text="Backup",command=self.backup).pack(side="right")
        self.nb=ttk.Notebook(self);self.nb.pack(fill="both",expand=True,padx=10,pady=10)
        self.tabs={}
        for n in ["Dashboard","Members","Bill Credit","Redemption","Transactions","Reports","Admin"]:
            fr=ttk.Frame(self.nb,padding=12);self.nb.add(fr,text=n);self.tabs[n]=fr
        self.dashboard();self.members();self.credit();self.redemption();self.transactions();self.reports();self.admin()
    def dashboard(self):
        f=self.tabs["Dashboard"];self.stat={}
        row=ttk.Frame(f);row.pack(fill="x")
        for k,l in [("m","Active Members"),("e","Coins Issued"),("r","Coins Redeemed"),("cash","Cash Collected")]:
            x=ttk.LabelFrame(row,text=l,padding=15);x.pack(side="left",fill="x",expand=True,padx=4)
            self.stat[k]=tk.StringVar(value="0");ttk.Label(x,textvariable=self.stat[k],font=("Arial",20,"bold")).pack()
        ttk.Label(f,text="Recent Activity",font=("Arial",14,"bold")).pack(anchor="w",pady=15)
        self.dtree=self.tree(f,[("type","Type",90),("created_at","Time",150),("card_no","Card",120),("name","Name",170),("bill_no","Bill",130),("amount","Amount",110),("coins","Coins",100),("status","Status",100)])
        self.dtree.pack(fill="both",expand=True)
    def tree(self,p,cols):
        t=ttk.Treeview(p,columns=[x[0] for x in cols],show="headings")
        for k,l,w in cols:t.heading(k,text=l);t.column(k,width=w,anchor="center")
        return t
    def members(self):
        f=self.tabs["Members"];left=ttk.LabelFrame(f,text="Member",padding=12);left.pack(side="left",fill="y")
        self.mv={k:tk.StringVar() for k in ["card","name","phone","email","address"]}
        self.mv["card"].set(self.next_card_no())
        for i,(k,l) in enumerate([("card","Card No (Auto)"),("name","Name"),("phone","Phone"),("email","Email"),("address","Address")]):
            ttk.Label(left,text=l).grid(row=i,column=0,sticky="w",pady=5)
            ent=ttk.Entry(left,textvariable=self.mv[k],width=30)
            ent.grid(row=i,column=1,padx=8,pady=5)
            if k=="card":
                ent.configure(state="readonly")
        ttk.Button(left,text="Add Member",command=self.addmember).grid(row=5,column=0,columnspan=2,pady=10)
        right=ttk.Frame(f);right.pack(side="left",fill="both",expand=True,padx=12)
        self.mq=tk.StringVar();ttk.Entry(right,textvariable=self.mq).pack(side="left",fill="x",expand=True);ttk.Button(right,text="Search",command=self.refresh_members).pack(side="left",padx=6)
        self.mtree=self.tree(right,[("card_no","Card",120),("name","Name",180),("phone","Phone",120),("eligible","Eligible",110),("earned","Earned",110),("redeemed","Redeemed",110)])
        self.mtree.pack(fill="both",expand=True,pady=(40,0))
    def credit(self):
        f=self.tabs["Bill Credit"];box=ttk.LabelFrame(f,text="Bill Credit",padding=20);box.pack(anchor="nw")
        self.cv={k:tk.StringVar() for k in ["card","bill","date","amount"]};self.cv["date"].set(today().isoformat())
        for i,(k,l) in enumerate([("card","Member Card"),("bill","Bill Number"),("date","Bill Date"),("amount","Bill Amount ₹")]):
            ttk.Label(box,text=l).grid(row=i,column=0,sticky="w",pady=8);ttk.Entry(box,textvariable=self.cv[k],width=35).grid(row=i,column=1,padx=12,pady=8)
        ttk.Button(box,text="Credit Smart Coins",command=self.do_credit).grid(row=4,column=0,columnspan=2,pady=12)
        ttk.Label(f,text="₹1 = 1 Smart Coin • usable after 4 days • expires after 4 calendar months",font=("Arial",11,"italic")).pack(anchor="w",pady=20)
    def redemption(self):
        f=self.tabs["Redemption"];box=ttk.LabelFrame(f,text="Redemption",padding=20);box.pack(anchor="nw")
        self.rv={k:tk.StringVar() for k in ["card","bill","date","amount","coins"]};self.rv["date"].set(today().isoformat())
        for i,(k,l) in enumerate([("card","Member Card"),("bill","Redemption Bill"),("date","Bill Date"),("amount","Bill Amount ₹"),("coins","Coins to Redeem")]):
            ttk.Label(box,text=l).grid(row=i,column=0,sticky="w",pady=8)
            ent=ttk.Entry(box,textvariable=self.rv[k],width=35)
            ent.grid(row=i,column=1,padx=12,pady=8)
            if k=="amount":
                self.rv["amount"].trace_add("write", lambda *args: self.update_redeem_coins())
            if k=="card":
                self.rv["card"].trace_add("write", lambda *args: self.update_redeem_coins())
            if k=="coins":
                ent.configure(state="readonly")
        ttk.Button(box,text="Redeem",command=self.do_redeem).grid(row=5,column=0,columnspan=2,pady=12)
        ttk.Label(box,text="Maximum = Bill ÷ 5 • 100 Smart Coins = ₹1 • redeemable coins must be less than credited coins",font=("Arial",11,"bold")).grid(row=6,column=0,columnspan=2,pady=8)
        ttk.Label(f,text="100 Smart Coins = ₹1 • maximum redemption value = bill ÷ 5 • coins are calculated automatically • cash-paid amount generates new coins",font=("Arial",11,"italic")).pack(anchor="w",pady=20)
    def transactions(self):
        f=self.tabs["Transactions"];self.ttree=self.tree(f,[("type","Type",90),("id","ID",70),("created_at","Time",150),("card_no","Card",120),("name","Name",160),("bill_no","Bill",130),("amount","Amount",110),("coins","Coins",100),("status","Status",100)])
        self.ttree.pack(fill="both",expand=True)
        b=ttk.Frame(f);b.pack(fill="x",pady=8);ttk.Button(b,text="Reverse Selected",command=self.reverse).pack(side="left")
    def reports(self):
        f=self.tabs["Reports"];ttk.Label(f,text="Day End / Reports",font=("Arial",18,"bold")).pack(anchor="w")
        self.rd=tk.StringVar(value=today().isoformat());ttk.Entry(f,textvariable=self.rd,width=20).pack(anchor="w",pady=10)
        ttk.Button(f,text="Close Day",command=self.close_day).pack(anchor="w")
        ttk.Button(f,text="Export Transactions CSV",command=self.export_csv).pack(anchor="w",pady=8)
        ttk.Button(f,text="Export Member Statement CSV",command=self.export_members).pack(anchor="w")
        ttk.Button(f,text="Open Export Folder",command=lambda:os.startfile(EXPORTS) if os.name=="nt" else None).pack(anchor="w",pady=8)
    def admin(self):
        f=self.tabs["Admin"]
        if self.user["role"]!="ADMIN": ttk.Label(f,text="Admin access required.").pack(anchor="w");return
        ttk.Label(f,text="Administration",font=("Arial",18,"bold")).pack(anchor="w")
        ttk.Button(f,text="Create User",command=self.create_user).pack(anchor="w",pady=8)
        ttk.Button(f,text="Change My Password",command=self.change_pw).pack(anchor="w")
        ttk.Button(f,text="Select Backup Location",command=self.choose_backup).pack(anchor="w",pady=8)
        ttk.Button(f,text="Database Integrity Check",command=self.integrity).pack(anchor="w")
    def next_card_no(self):
        row=self.db.c.execute("""
            SELECT COALESCE(MAX(CAST(SUBSTR(card_no, 5) AS INTEGER)),0)+1 AS n
            FROM members
            WHERE card_no LIKE 'SC-%'
        """).fetchone()
        return f"SC-{int(row['n']):08d}"

    def addmember(self):
        try:
            card = self.mv["card"].get()
            values = [
                card,
                self.mv["name"].get(),
                self.mv["phone"].get(),
                self.mv["email"].get(),
                self.mv["address"].get()
            ]
            self.db.add_member(values, self.user["id"])
            messagebox.showinfo("Success", f"Member added successfully.\nCard No: {card}")
            for k in ["name", "phone", "email", "address"]:
                self.mv[k].set("")
            self.mv["card"].set(self.next_card_no())
            self.refresh_members()
        except Exception as e:
            messagebox.showerror("Error", str(e))
    def do_credit(self):
        try:
            n=self.db.earn_bill(self.cv["card"].get(),self.cv["bill"].get(),self.cv["date"].get(),float(self.cv["amount"].get()),self.user["id"]);messagebox.showinfo("Success",f"{n:,} Smart Coins credited.");self.refresh()
        except Exception as e:messagebox.showerror("Credit failed",str(e))
    def update_redeem_coins(self):
        try:
            bill_amount = float(self.rv["amount"].get())
            if bill_amount <= 0:
                self.rv["coins"].set("")
                return
            # Business rule:
            # bill amount must be 5x the redemption value.
            # Therefore max redemption value = bill_amount / 5.
            # 100 Smart Coins = ₹1.
            max_redemption_value = bill_amount / float(self.db.s("bill_multiple"))
            bill_limited_coins = int(max_redemption_value * float(self.db.s("redeem_coins_per_rupee")))

            card = self.rv["card"].get().strip()
            max_coins = bill_limited_coins
            if card:
                member = self.db.c.execute(
                    "SELECT id FROM members WHERE card_no=? AND active=1", (card,)
                ).fetchone()
                if member:
                    total_credited, eligible_available = self.db.member_coin_balances(member["id"])
                    # Strict business rule: redeemable coins must be LESS THAN
                    # credited coins, and cannot exceed currently eligible coins.
                    strict_credited_limit = max(total_credited - 1, 0)
                    max_coins = min(bill_limited_coins, eligible_available, strict_credited_limit)

            if max_coins > 0:
                self.rv["coins"].set(f"{max_coins:,}")
            else:
                self.rv["coins"].set("0")
        except (ValueError, TypeError):
            self.rv["coins"].set("")

    def do_redeem(self):
        try:
            v,c,fresh,rid=self.db.redeem_bill(self.rv["card"].get(),self.rv["bill"].get(),self.rv["date"].get(),float(self.rv["amount"].get()),int(self.rv["coins"].get().replace(",", "")),self.user["id"])
            messagebox.showinfo("Success",f"Redemption value: {fmt(v)}\nCash paid: {fmt(c)}\nFresh Smart Coins: {fresh:,}");self.refresh()
        except Exception as e:messagebox.showerror("Redemption failed",str(e))
    def reverse(self):
        s=self.ttree.selection()
        if not s:return
        vals=self.ttree.item(s[0],"values");kind="EARN" if vals[0]=="CREDIT" else "REDEEM"
        reason=tk.simpledialog.askstring("Reason","Enter reversal reason:",parent=self)
        if not reason:return
        try:self.db.reverse(kind,int(vals[1]),reason,self.user["id"]);self.refresh()
        except Exception as e:messagebox.showerror("Reversal failed",str(e))
    def close_day(self):
        try:self.db.dayclose(self.rd.get(),self.user["id"]);messagebox.showinfo("Closed","Day closed successfully.")
        except Exception as e:messagebox.showerror("Close failed",str(e))
    def export_csv(self):
        p=EXPORTS/f"transactions_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        rows=self.db.history()
        with p.open("w",newline="",encoding="utf-8-sig") as f:
            w=csv.writer(f);w.writerow(rows[0].keys() if rows else ["No data"])
            for r in rows:w.writerow(list(r))
        messagebox.showinfo("Exported",str(p))
    def export_members(self):
        p=EXPORTS/f"members_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv";rows=self.db.members()
        with p.open("w",newline="",encoding="utf-8-sig") as f:
            w=csv.writer(f);w.writerow(rows[0].keys() if rows else ["No data"])
            for r in rows:w.writerow(list(r))
        messagebox.showinfo("Exported",str(p))
    def create_user(self):
        u=tk.simpledialog.askstring("New User","Username:",parent=self); 
        if not u:return
        p=tk.simpledialog.askstring("New User","Temporary password:",parent=self,show="*"); 
        if not p:return
        role=tk.simpledialog.askstring("Role","Role ADMIN or BILLER:",parent=self)
        if role not in ("ADMIN","BILLER"):return
        salt,h=hashpw(p)
        try:self.db.c.execute("INSERT INTO users(username,password_hash,salt,role,created_at) VALUES(?,?,?,?,?)",(u,h,salt,role,now()));self.db.c.commit();messagebox.showinfo("Created","User created.")
        except Exception as e:messagebox.showerror("Error",str(e))
    def change_pw(self):
        p=tk.simpledialog.askstring("Password","New password:",parent=self,show="*")
        if not p:return
        s,h=hashpw(p);self.db.c.execute("UPDATE users SET password_hash=?,salt=? WHERE id=?",(h,s,self.user["id"]));self.db.c.commit();messagebox.showinfo("Changed","Password changed.")
    def choose_backup(self):
        p=filedialog.askdirectory(title="Backup folder")
        if p:self.db.c.execute("UPDATE settings SET v=? WHERE k='backup_path'",(p,));self.db.c.commit();messagebox.showinfo("Saved",p)
    def integrity(self):
        r=self.db.c.execute("PRAGMA integrity_check").fetchone()[0];messagebox.showinfo("Integrity",r)
    def backup(self):
        try:messagebox.showinfo("Backup",f"Backup created:\n{self.db.backup()}")
        except Exception as e:messagebox.showerror("Backup failed",str(e))
    def refresh_members(self):
        for x in self.mtree.get_children():self.mtree.delete(x)
        for r in self.db.members(self.mq.get()):
            self.mtree.insert("", "end",values=(r["card_no"],r["name"],r["phone"] or "",f'{int(r["eligible"]):,}',f'{int(r["earned"]):,}',f'{int(r["redeemed"]):,}'))
    def refresh(self):
        s=self.db.stats()
        self.stat["m"].set(f'{s["m"]:,}');self.stat["e"].set(f'{int(s["e"]):,}');self.stat["r"].set(f'{int(s["r"]):,}');self.stat["cash"].set(fmt(s["cash"]))
        for t in (self.dtree,self.ttree):
            for x in t.get_children():t.delete(x)
        rows=self.db.history()
        for r in rows[:30]:self.dtree.insert("", "end",values=(r["type"],r["created_at"],r["card_no"],r["name"],r["bill_no"],fmt(r["amount"]),f'{int(r["coins"]):,}',r["status"]))
        for r in rows:self.ttree.insert("", "end",values=(r["type"],r["id"],r["created_at"],r["card_no"],r["name"],r["bill_no"],fmt(r["amount"]),f'{int(r["coins"]):,}',r["status"]))
        self.refresh_members()
    def auto(self):
        try:self.db.backup()
        except:pass
        self.after(24*60*60*1000,self.auto)
    def close(self):
        try:self.db.backup()
        except:pass
        self.db.c.close();self.destroy()

if __name__=="__main__":
    Login().mainloop()
