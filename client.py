import tkinter as tk
from tkinter import ttk,messagebox,simpledialog
import requests, os, webbrowser
from datetime import date

class App(tk.Tk):
 def __init__(self):
  super().__init__();self.title('Smart Coins — Outlet Client');self.geometry('1200x760');self.base='http://127.0.0.1:8000';self.token='';self.role='';self.login()
 def req(self,m,p='',**kw):
  h=kw.pop('headers',{});h['Authorization']='Bearer '+self.token
  r=requests.request(m,self.base+p,headers=h,timeout=15,**kw)
  if r.status_code>=400: raise Exception(r.json().get('detail',r.text))
  return r.json()
 def login(self):
  f=ttk.Frame(self,padding=40);f.pack(expand=True);ttk.Label(f,text='SMART COINS',font=('Arial',28,'bold')).pack(pady=20);u=tk.StringVar(value='admin');p=tk.StringVar();ttk.Label(f,text='Server URL').pack(anchor='w');sv=tk.StringVar(value='http://127.0.0.1:8000');ttk.Entry(f,textvariable=sv,width=45).pack(pady=5);self.server=sv;ttk.Label(f,text='Username').pack(anchor='w');ttk.Entry(f,textvariable=u).pack(pady=5);ttk.Label(f,text='Password').pack(anchor='w');ttk.Entry(f,textvariable=p,show='*').pack(pady=5);ttk.Button(f,text='Login',command=lambda:self.dologin(u.get(),p.get())).pack(pady=15)
 def dologin(self,u,p):
  try:r=requests.post(self.server.get()+'/login',json={'username':u,'password':p},timeout=10);r.raise_for_status();x=r.json();self.token=x['token'];self.role=x['role'];self.base=self.server.get();self.showmain()
  except Exception as e:messagebox.showerror('Login failed',str(e))
 def showmain(self):
  for w in self.winfo_children():w.destroy();self.nb=ttk.Notebook(self);self.nb.pack(fill='both',expand=True,padx=10,pady=10);self.dashboard();self.members();self.earn();self.redeem();self.reminders()
 def dashboard(self):
  f=ttk.Frame(self.nb,padding=15);self.nb.add(f,text='Dashboard');self.ds=ttk.Treeview(f,columns=('store','pct','sales','issued','red','value','fresh'),show='headings');
  for c in self.ds['columns']:self.ds.heading(c,text=c.title());self.ds.column(c,width=140)
  self.ds.pack(fill='both',expand=True);ttk.Button(f,text='Refresh',command=self.refreshdash).pack(pady=8);self.refreshdash()
 def refreshdash(self):
  try:r=self.req('GET','/dashboard');
  except Exception as e:return messagebox.showerror('Error',str(e))
  for x in self.ds.get_children():self.ds.delete(x)
  for x in r['stores']:self.ds.insert('', 'end',values=(x['store'],x['percentage'],f"₹{x['sales']:,.2f}",x['coins_issued'],x['redemptions'],f"₹{x['redemption_value']:,.2f}",x['fresh_coins']))
 def members(self):
  f=ttk.Frame(self.nb,padding=15);self.nb.add(f,text='Members');
  b=ttk.Frame(f);b.pack(fill='x');self.q=tk.StringVar();ttk.Entry(b,textvariable=self.q).pack(side='left',fill='x',expand=True);ttk.Button(b,text='Search',command=self.refreshmembers).pack(side='left',padx=5);ttk.Button(b,text='New Member',command=self.newmember).pack(side='left')
  self.mt=ttk.Treeview(f,columns=('card','name','mobile','dob','ann','credited','eligible','redeemed'),show='headings');
  for c in self.mt['columns']:self.mt.heading(c,text=c.title());self.mt.column(c,width=120)
  self.mt.pack(fill='both',expand=True,pady=10);self.refreshmembers()
 def refreshmembers(self):
  try:r=self.req('GET','/members',params={'q':self.q.get()})
  except Exception as e:return messagebox.showerror('Error',str(e))
  for x in self.mt.get_children():self.mt.delete(x)
  for x in r:self.mt.insert('','end',values=(x['card_no'],x['name'],x['mobile'],x['dob'],x['anniversary'],x['credited'],x['eligible'],x['redeemed']))
 def newmember(self):
  f=tk.Toplevel(self);f.title('New Member');vs={k:tk.StringVar() for k in ['name','mobile','email','address','dob','anniversary']}
  for i,k in enumerate(vs):ttk.Label(f,text=k.title()).grid(row=i,column=0,padx=10,pady=5);ttk.Entry(f,textvariable=vs[k],width=35).grid(row=i,column=1,padx=10,pady=5)
  def go():
   try:r=self.req('POST','/members',json={k:v.get() for k,v in vs.items()});messagebox.showinfo('Created','Card: '+r['card_no']);f.destroy();self.refreshmembers()
   except Exception as e:messagebox.showerror('Error',str(e))
  ttk.Button(f,text='Create',command=go).grid(row=6,columnspan=2,pady=10)
 def earn(self):
  f=ttk.Frame(self.nb,padding=25);self.nb.add(f,text='Bill Credit');self.ev={k:tk.StringVar() for k in ['card','bill','date','amount']};self.ev['date'].set(date.today().isoformat());self.store=tk.StringVar(value='SMART')
  fields=['card','bill','date','amount']
  for i,k in enumerate(fields):ttk.Label(f,text=k.title()).grid(row=i,column=0,pady=8,sticky='w');ttk.Entry(f,textvariable=self.ev[k],width=35).grid(row=i,column=1,pady=8)
  ttk.Label(f,text='Store').grid(row=4,column=0,sticky='w');ttk.Combobox(f,textvariable=self.store,state='readonly',values=['SMART','KIDS','INSIGNIA']).grid(row=4,column=1,sticky='w');ttk.Button(f,text='Credit Coins',command=self.doearn).grid(row=5,columnspan=2,pady=15);ttk.Label(f,text='Minimum earning bill ₹500').grid(row=6,columnspan=2)
 def doearn(self):
  try:r=self.req('POST','/earn',json={'card_no':self.ev['card'].get(),'bill_no':self.ev['bill'].get(),'bill_date':self.ev['date'].get(),'amount':float(self.ev['amount'].get()),'store':self.store.get()});messagebox.showinfo('Success',f"{r['coins']:,} Smart Coins credited\nAvailable: {r['available_on']}\nExpires: {r['expiry_on']}")
  except Exception as e:messagebox.showerror('Error',str(e))
 def redeem(self):
  f=ttk.Frame(self.nb,padding=25);self.nb.add(f,text='Redemption');self.rv={k:tk.StringVar() for k in ['card','bill','date','amount','coins','value','eligible']};self.rv['date'].set(date.today().isoformat());self.rstore=tk.StringVar(value='SMART')
  for i,k in enumerate(['card','bill','date','amount','value','coins','eligible']):ttk.Label(f,text=k.replace('_',' ').title()).grid(row=i,column=0,pady=6,sticky='w');e=ttk.Entry(f,textvariable=self.rv[k],width=35);e.grid(row=i,column=1,pady=6); 
  self.rv['coins'];ttk.Button(f,text='Calculate',command=self.preview).grid(row=7,column=0,pady=10);ttk.Label(f,text='Redeem at Store').grid(row=8,column=0,sticky='w');ttk.Combobox(f,textvariable=self.rstore,state='readonly',values=['SMART','KIDS','INSIGNIA']).grid(row=8,column=1,sticky='w');ttk.Button(f,text='Redeem & Generate Receipt',command=self.doredeem).grid(row=9,columnspan=2,pady=15);ttk.Label(f,text='Minimum bill ₹500 • Bill must be 5× redemption value • 10 coins = ₹1').grid(row=10,columnspan=2)
 def preview(self):
  try:r=self.req('GET','/redeem/preview',params={'card_no':self.rv['card'].get(),'amount':float(self.rv['amount'].get())});self.rv['eligible'].set(f"{r['eligible']:,}");self.rv['coins'].set(f"{r['maximum_coins']:,}");self.rv['value'].set(f"₹{r['maximum_value']:,.2f}")
  except Exception as e:messagebox.showerror('Error',str(e))
 def doredeem(self):
  try:
   self.preview();r=self.req('POST','/redeem',json={'card_no':self.rv['card'].get(),'bill_no':self.rv['bill'].get(),'bill_date':self.rv['date'].get(),'amount':float(self.rv['amount'].get()),'store':self.rstore.get()});messagebox.showinfo('Success',f"Redeemed {r['coins']:,} coins\nValue ₹{r['value']:,.2f}\nCash paid ₹{r['cash_paid']:,.2f}\nFresh coins {r['fresh_coins']:,}\nReceipt: {r['receipt']}")
  except Exception as e:messagebox.showerror('Error',str(e))
 def reminders(self):
  f=ttk.Frame(self.nb,padding=15);self.nb.add(f,text='Reminders')
  b=ttk.Frame(f);b.pack(fill='x');ttk.Label(b,text='Inactive for (days):').pack(side='left');self.remdays=tk.StringVar(value='90');ttk.Entry(b,textvariable=self.remdays,width=6).pack(side='left',padx=5);ttk.Button(b,text='Find Inactive Members',command=self.refreshreminders).pack(side='left',padx=5)
  ttk.Label(f,text="Click a row then 'Open WhatsApp' — this opens a pre-filled chat for you to review and send by hand. It's free, but nothing is sent automatically; see README.").pack(anchor='w',pady=(5,10))
  self.rt=ttk.Treeview(f,columns=('card','name','mobile','last','eligible'),show='headings')
  for c,w in [('card',110),('name',160),('mobile',120),('last',110),('eligible',90)]:self.rt.heading(c,text=c.title());self.rt.column(c,width=w)
  self.rt.pack(fill='both',expand=True,pady=5);ttk.Button(f,text='Open WhatsApp for Selected',command=self.openwhatsapp).pack(pady=8)
  self._reminder_rows={}
 def refreshreminders(self):
  try:r=self.req('GET','/reminders/inactive',params={'days':int(self.remdays.get() or 90)})
  except Exception as e:return messagebox.showerror('Error',str(e))
  for x in self.rt.get_children():self.rt.delete(x)
  self._reminder_rows={}
  for x in r:
   iid=self.rt.insert('','end',values=(x['card_no'],x['name'],x['mobile'],x['last_transaction'] or 'Never',x['eligible_coins']))
   self._reminder_rows[iid]=x['whatsapp_link']
 def openwhatsapp(self):
  sel=self.rt.selection()
  if not sel:return messagebox.showinfo('Select a member','Select a row first.')
  link=self._reminder_rows.get(sel[0])
  if link:webbrowser.open(link)
App().mainloop()
