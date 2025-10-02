#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# AIR/IRZ++ – Uprawy (Feniks edition)
# Kompletny system: logowanie, 15 funkcji, ulepszona pogoda, wizualizacje, mapa pól, powiadomienia, Excel, zdjęcia, AI-asystent

import os, json, sqlite3, hashlib, binascii, logging, re
from datetime import datetime
from typing import Optional, List, Dict, Tuple

import tkinter as tk
from tkinter import messagebox, simpledialog, filedialog
from tkinter.scrolledtext import ScrolledText

from PIL import Image, ImageTk
import requests
import ttkbootstrap as tb
from plyer import notification
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import openpyxl

# ============================
# ŚCIEŻKI
# ============================
APPDATA = os.getenv("APPDATA") or os.path.expanduser("~/.config")
APPDIR = os.path.join(APPDATA, "KozyManager")
os.makedirs(APPDIR, exist_ok=True)

DB_PATH       = os.path.join(APPDIR, "users.db")
SESSION_FILE  = os.path.join(APPDIR, "session_token.json")
LOG_DIR       = os.path.join(APPDIR, "logs")
DOCS_DIR      = os.path.join(APPDIR, "docs")
os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(DOCS_DIR, exist_ok=True)

LOG_PATH      = os.path.join(LOG_DIR, "uprawy.log")

# ============================
# LOGGING
# ============================
logging.basicConfig(
    filename=LOG_PATH,
    level=logging.DEBUG,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logging.info("Start uprawy.py")

# ============================
# BAZA DANYCH
# ============================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY,
        username TEXT UNIQUE,
        password_hash TEXT,
        salt TEXT
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS crops (
        id INTEGER PRIMARY KEY,
        owner TEXT,
        name TEXT,
        sow_date TEXT,
        notes TEXT
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS plans (
        id INTEGER PRIMARY KEY,
        owner TEXT,
        crop_id INTEGER,
        task TEXT,
        date TEXT
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS monitoring (
        id INTEGER PRIMARY KEY,
        owner TEXT,
        crop_id INTEGER,
        ph REAL,
        moisture REAL,
        notes TEXT,
        date TEXT
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS finances (
        id INTEGER PRIMARY KEY,
        owner TEXT,
        crop_id INTEGER,
        cost REAL,
        income REAL,
        note TEXT,
        date TEXT
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS yields (
        id INTEGER PRIMARY KEY,
        owner TEXT,
        crop_id INTEGER,
        year INTEGER,
        yield_t_ha REAL
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS actions (
        id INTEGER PRIMARY KEY,
        owner TEXT,
        ts TEXT,
        entry TEXT
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS alerts (
        id INTEGER PRIMARY KEY,
        owner TEXT,
        message TEXT,
        due TEXT,
        done INTEGER DEFAULT 0
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS fields (
        id INTEGER PRIMARY KEY,
        owner TEXT,
        name TEXT,
        x1 INTEGER, y1 INTEGER,
        x2 INTEGER, y2 INTEGER
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS documents (
        id INTEGER PRIMARY KEY,
        owner TEXT,
        crop_id INTEGER,
        filename TEXT,
        added TEXT
    )""")
    conn.commit()
    conn.close()

init_db()

# ============================
# HASŁA
# ============================
def pbkdf2_hash(password: str, salt: bytes = None, iterations: int = 200_000) -> Tuple[str, str]:
    if salt is None:
        salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return binascii.hexlify(dk).decode(), binascii.hexlify(salt).decode()

def verify_password(stored_hash_hex: str, stored_salt_hex: str, attempt: str) -> bool:
    salt = binascii.unhexlify(stored_salt_hex.encode())
    test_hash, _ = pbkdf2_hash(attempt, salt)
    return test_hash == stored_hash_hex

# ============================
# SESJA
# ============================
def save_session_token(username: str, data_key: str):
    token = {"username": username, "data_key": data_key, "created_at": datetime.now().isoformat(timespec="seconds")}
    with open(SESSION_FILE, "w", encoding="utf-8") as f:
        json.dump(token, f, indent=2)

def load_session_username() -> Optional[str]:
    if not os.path.exists(SESSION_FILE): return None
    try:
        with open(SESSION_FILE, "r", encoding="utf-8") as f:
            tok = json.load(f)
        return tok.get("username")
    except Exception:
        return None

def clear_session():
    if os.path.exists(SESSION_FILE): os.remove(SESSION_FILE)

# ============================
# AUTH
# ============================
def register_user(username: str, password: str) -> bool:
    ph, salt = pbkdf2_hash(password)
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("INSERT INTO users (username,password_hash,salt) VALUES (?,?,?)",(username, ph, salt))
        conn.commit(); conn.close()
        return True
    except sqlite3.IntegrityError:
        return False

def check_login(username: str, password: str) -> Optional[str]:
    conn = sqlite3.connect(DB_PATH); cur = conn.cursor()
    cur.execute("SELECT password_hash, salt FROM users WHERE username=?", (username,))
    row = cur.fetchone(); conn.close()
    if not row: return None
    return row[0] if verify_password(row[0], row[1], password) else None

# ============================
# PANEL LOGIN
# ============================
class LoginPanel(tb.Window):
    def __init__(self):
        super().__init__(themename="darkly")
        self.title("AIR/IRZ++ – Panel logowania")
        self.geometry("480x360"); self.resizable(False, False)

        u = load_session_username()
        if u: self.withdraw(); MainPanel(u).mainloop(); return

        try:
            bg = Image.open("Background.jpg")
            self.bg_tk = ImageTk.PhotoImage(bg.resize((480, 360)))
            tk.Label(self, image=self.bg_tk).place(x=0,y=0,relwidth=1,relheight=1)
        except: self.configure(bg="#1f1f1f")

        card = tb.Frame(self, padding=20, style="Card.TFrame"); card.place(relx=0.5,rely=0.5,anchor="center")
        tb.Label(card,text="Logowanie do AIR/IRZ++",font="-size 14 -weight bold").grid(row=0,column=0,columnspan=2,pady=(0,14))
        tb.Label(card,text="Użytkownik:").grid(row=1,column=0,sticky="e",padx=6,pady=6)
        tb.Label(card,text="Hasło:").grid(row=2,column=0,sticky="e",padx=6,pady=6)
        self.username = tb.Entry(card,width=24); self.password = tb.Entry(card,show="*",width=24)
        self.username.grid(row=1,column=1,pady=6); self.password.grid(row=2,column=1,pady=6)
        btns = tb.Frame(card); btns.grid(row=3,column=0,columnspan=2,pady=(12,6))
        tb.Button(btns,text="Zaloguj",bootstyle="success",width=16,command=self.login).pack(side="left",padx=4)
        tb.Button(btns,text="Rejestracja",bootstyle="secondary",width=16,command=self.open_register).pack(side="left",padx=4)

    def login(self):
        u,p=self.username.get().strip(),self.password.get().strip()
        if not u or not p: messagebox.showerror("Błąd","Podaj nazwę i hasło",parent=self); return
        data_key = check_login(u,p)
        if data_key: save_session_token(u,data_key); messagebox.showinfo("Sukces",f"Zalogowano jako {u}",parent=self); self.destroy(); MainPanel(u).mainloop()
        else: messagebox.showerror("Błąd","Nieprawidłowe dane logowania",parent=self)

    def open_register(self): RegisterPanel(self)

class RegisterPanel(tb.Toplevel):
    def __init__(self,parent):
        super().__init__(parent); self.title("Rejestracja"); self.geometry("360x240"); self.resizable(False,False)
        tb.Label(self,text="Nowy użytkownik:",font="-size 12").pack(pady=10)
        self.user=tb.Entry(self,width=24); self.user.pack(pady=6)
        tb.Label(self,text="Hasło:").pack(); self.passw=tb.Entry(self,width=24,show="*"); self.passw.pack(pady=6)
        tb.Button(self,text="Zarejestruj",bootstyle="success",command=self.do_register).pack(pady=10)
    def do_register(self):
        u,p=self.user.get().strip(),self.passw.get().strip()
        if not u or not p: messagebox.showerror("Błąd","Podaj dane",parent=self); return
        if register_user(u,p): messagebox.showinfo("OK","Zarejestrowano",parent=self); self.destroy()
        else: messagebox.showerror("Błąd","Użytkownik istnieje",parent=self)

# ============================
# PANEL GŁÓWNY (MainPanel)
# ============================
# ============================
# PANEL GŁÓWNY
# ============================
class MainPanel(tb.Window):
    def __init__(self, username):
        super().__init__(themename="darkly")
        self.username = username
        self.title(f"AIR/IRZ++ – Uprawy ({username})")
        self.geometry("1200x800")
        self.resizable(True, True)

        # tło
        try:
            bg = Image.open("UPR.jpg")
            self.bg_img = ImageTk.PhotoImage(bg.resize((1200, 800)))
            tk.Label(self, image=self.bg_img).place(x=0, y=0, relwidth=1, relheight=1)
        except:
            self.configure(bg="#2b2b2b")

        # Notebook
        self.nb = tb.Notebook(self, bootstyle="info")
        self.nb.pack(fill="both", expand=True, padx=6, pady=6)

        self.tabs = {}
        for tab in [
            "Uprawy", "Planowanie", "Pogoda", "Dokumentacja",
            "Monitoring", "Optymalizacja", "Finanse", "Historia",
            "Alerty", "Wizualizacje", "Mapa pól", "Asystent AI"
        ]:
            frame = tb.Frame(self.nb)
            self.nb.add(frame, text=tab)
            self.tabs[tab] = frame

        # inicjalizacja zakładek
        self._init_uprawy()
        self._init_planowanie()
        self._init_pogoda()
        self._init_dokumentacja()
        self._init_monitoring()
        self._init_opt()
        self._init_finanse()
        self._init_historia()
        self._init_alerty()
        self._init_wizualizacje()
        self._init_mapa()
        self._init_ai()

    # ============================
    # ZAKŁADKA UPRAWY
    # ============================
    def _init_uprawy(self):
        f = self.tabs["Uprawy"]
        tb.Button(f, text="Dodaj uprawę", bootstyle="success", command=self.add_crop).pack(pady=5)
        tb.Button(f, text="Eksport do Excela", bootstyle="info", command=self.export_crops_excel).pack(pady=5)
        self.crops_list = ScrolledText(f, height=20)
        self.crops_list.pack(fill="both", expand=True, padx=10, pady=10)
        self.load_crops()

    def add_crop(self):
        name = simpledialog.askstring("Uprawa", "Nazwa uprawy:")
        if not name: return
        conn = sqlite3.connect(DB_PATH); cur = conn.cursor()
        cur.execute("INSERT INTO crops (owner,name,sow_date,notes) VALUES (?,?,?,?)",
                    (self.username, name, datetime.now().date().isoformat(), ""))
        conn.commit(); conn.close()
        self.log_action(f"Dodano uprawę {name}")
        self.load_crops()

    def load_crops(self):
        self.crops_list.delete("1.0", tk.END)
        conn = sqlite3.connect(DB_PATH); cur = conn.cursor()
        for row in cur.execute("SELECT name,sow_date,notes FROM crops WHERE owner=?",(self.username,)):
            self.crops_list.insert(tk.END, f"{row[0]} | siew: {row[1]} | {row[2]}\n")
        conn.close()

    def export_crops_excel(self):
        path = filedialog.asksaveasfilename(defaultextension=".xlsx")
        if not path: return
        wb = openpyxl.Workbook(); ws = wb.active; ws.append(["Uprawa","Data siewu","Notatki"])
        conn = sqlite3.connect(DB_PATH); cur = conn.cursor()
        for row in cur.execute("SELECT name,sow_date,notes FROM crops WHERE owner=?",(self.username,)):
            ws.append(row)
        conn.close(); wb.save(path)
        messagebox.showinfo("OK","Wyeksportowano do Excela")

    # ============================
    # ZAKŁADKA PLANOWANIE
    # ============================
    def _init_planowanie(self):
        f = self.tabs["Planowanie"]
        tb.Button(f, text="Dodaj plan", command=self.add_plan).pack(pady=5)
        self.plans = ScrolledText(f, height=20); self.plans.pack(fill="both", expand=True)
        self.load_plans()

    def add_plan(self):
        task = simpledialog.askstring("Plan", "Wpisz zadanie:")
        if not task: return
        conn = sqlite3.connect(DB_PATH); cur = conn.cursor()
        cur.execute("INSERT INTO plans (owner,crop_id,task,date) VALUES (?,?,?,?)",
                    (self.username, None, task, datetime.now().date().isoformat()))
        conn.commit(); conn.close()
        self.log_action(f"Nowy plan: {task}")
        self.load_plans()

    def load_plans(self):
        self.plans.delete("1.0", tk.END)
        conn = sqlite3.connect(DB_PATH); cur = conn.cursor()
        for row in cur.execute("SELECT task,date FROM plans WHERE owner=?",(self.username,)):
            self.plans.insert(tk.END,f"{row[1]} | {row[0]}\n")
        conn.close()

    # ============================
    # ZAKŁADKA POGODA
    # ============================
    def _init_pogoda(self):
        f = self.tabs["Pogoda"]
        tb.Button(f,text="Pobierz prognozę",command=self.show_weather).pack(pady=5)
        self.weather_box = ScrolledText(f,height=20); self.weather_box.pack(fill="both",expand=True)

    def show_weather(self):
        try:
            url="https://api.open-meteo.com/v1/forecast?latitude=52.6&longitude=16.7&daily=temperature_2m_max,temperature_2m_min,precipitation_sum&timezone=Europe%2FWarsaw"
            data=requests.get(url,timeout=10).json()
            dates=data["daily"]["time"]; tmin=data["daily"]["temperature_2m_min"]; tmax=data["daily"]["temperature_2m_max"]; rain=data["daily"]["precipitation_sum"]
            self.weather_box.delete("1.0",tk.END)
            self.weather_box.insert(tk.END,"Data        Min°C  Max°C  Opady  Ikona\n")
            self.weather_box.insert(tk.END,"-----------------------------------\n")
            for d,lo,hi,r in zip(dates,tmin,tmax,rain):
                icon="☀️"
                if r>0: icon="🌧️"
                if hi<0: icon="❄️"
                self.weather_box.insert(tk.END,f"{d}   {lo:4}   {hi:4}   {r:4.1f}mm  {icon}\n")
        except Exception as e:
            self.weather_box.insert(tk.END,f"Błąd pobierania: {e}\n")

    # ============================
    # ZAKŁADKA DOKUMENTACJA
    # ============================
    def _init_dokumentacja(self):
        f=self.tabs["Dokumentacja"]
        tb.Button(f,text="Dodaj plik",command=self.add_doc).pack(pady=5)
        self.docs=ScrolledText(f,height=20); self.docs.pack(fill="both",expand=True)
        self.load_docs()

    def add_doc(self):
        path=filedialog.askopenfilename()
        if not path: return
        name=os.path.basename(path)
        new=os.path.join(DOCS_DIR,name); open(new,"wb").write(open(path,"rb").read())
        conn=sqlite3.connect(DB_PATH); cur=conn.cursor()
        cur.execute("INSERT INTO documents (owner,crop_id,filename,added) VALUES (?,?,?,?)",
                    (self.username,None,name,datetime.now().isoformat()))
        conn.commit(); conn.close()
        self.log_action(f"Dodano dokument {name}")
        self.load_docs()

    def load_docs(self):
        self.docs.delete("1.0",tk.END)
        conn=sqlite3.connect(DB_PATH); cur=conn.cursor()
        for row in cur.execute("SELECT filename,added FROM documents WHERE owner=?",(self.username,)):
            self.docs.insert(tk.END,f"{row[1]} | {row[0]}\n")
        conn.close()

    # ============================
    # ZAKŁADKA MONITORING
    # ============================
    def _init_monitoring(self):
        f=self.tabs["Monitoring"]
        tb.Button(f,text="Dodaj wpis monitoringu",command=self.add_mon).pack(pady=5)
        self.mons=ScrolledText(f,height=20); self.mons.pack(fill="both",expand=True)
        self.load_mons()

    def add_mon(self):
        ph=simpledialog.askfloat("Monitoring","Podaj pH gleby:")
        moist=simpledialog.askfloat("Monitoring","Podaj wilgotność (%):")
        conn=sqlite3.connect(DB_PATH); cur=conn.cursor()
        cur.execute("INSERT INTO monitoring (owner,crop_id,ph,moisture,notes,date) VALUES (?,?,?,?,?,?)",
                    (self.username,None,ph,moist,"",datetime.now().date().isoformat()))
        conn.commit(); conn.close()
        self.log_action("Nowy wpis monitoringu")
        self.load_mons()

    def load_mons(self):
        self.mons.delete("1.0",tk.END)
        conn=sqlite3.connect(DB_PATH); cur=conn.cursor()
        for row in cur.execute("SELECT date,ph,moisture FROM monitoring WHERE owner=?",(self.username,)):
            self.mons.insert(tk.END,f"{row[0]} | pH {row[1]} | wilg {row[2]}%\n")
        conn.close()

    # ============================
    # ZAKŁADKA OPTYMALIZACJA
    # ============================
    def _init_opt(self):
        f=self.tabs["Optymalizacja"]
        tb.Button(f,text="Analiza plonów",command=self.analyze_yields).pack(pady=5)
        self.optbox=ScrolledText(f,height=20); self.optbox.pack(fill="both",expand=True)

    def analyze_yields(self):
        self.optbox.delete("1.0",tk.END)
        conn=sqlite3.connect(DB_PATH); cur=conn.cursor()
        for row in cur.execute("SELECT year,yield_t_ha FROM yields WHERE owner=?",(self.username,)):
            self.optbox.insert(tk.END,f"Rok {row[0]} | Plon {row[1]} t/ha\n")
        conn.close()

    # ============================
    # ZAKŁADKA FINANSE
    # ============================
    def _init_finanse(self):
        f=self.tabs["Finanse"]
        tb.Button(f,text="Dodaj wpis finansowy",command=self.add_fin).pack(pady=5)
        tb.Button(f,text="Eksport do Excela",command=self.export_fin_excel).pack(pady=5)
        self.finbox=ScrolledText(f,height=20); self.finbox.pack(fill="both",expand=True)
        self.load_fin()

    def add_fin(self):
        cost=simpledialog.askfloat("Finanse","Koszt [zł]:")
        income=simpledialog.askfloat("Finanse","Przychód [zł]:")
        note=simpledialog.askstring("Finanse","Opis:")
        conn=sqlite3.connect(DB_PATH); cur=conn.cursor()
        cur.execute("INSERT INTO finances (owner,crop_id,cost,income,note,date) VALUES (?,?,?,?,?,?)",
                    (self.username,None,cost,income,note,datetime.now().date().isoformat()))
        conn.commit(); conn.close()
        self.log_action("Dodano wpis finansowy")
        self.load_fin()

    def load_fin(self):
        self.finbox.delete("1.0",tk.END)
        conn=sqlite3.connect(DB_PATH); cur=conn.cursor()
        for row in cur.execute("SELECT date,cost,income,note FROM finances WHERE owner=?",(self.username,)):
            self.finbox.insert(tk.END,f"{row[0]} | koszt {row[1]} zł | przychód {row[2]} zł | {row[3]}\n")
        conn.close()

    def export_fin_excel(self):
        path=filedialog.asksaveasfilename(defaultextension=".xlsx")
        if not path: return
        wb=openpyxl.Workbook(); ws=wb.active; ws.append(["Data","Koszt","Przychód","Opis"])
        conn=sqlite3.connect(DB_PATH); cur=conn.cursor()
        for row in cur.execute("SELECT date,cost,income,note FROM finances WHERE owner=?",(self.username,)):
            ws.append(row)
        conn.close(); wb.save(path)
        messagebox.showinfo("OK","Wyeksportowano do Excela")

    # ============================
    # ZAKŁADKA HISTORIA
    # ============================
    def _init_historia(self):
        f=self.tabs["Historia"]
        self.hist=ScrolledText(f,height=25); self.hist.pack(fill="both",expand=True)
        self.load_historia()

    def log_action(self,entry:str):
        conn=sqlite3.connect(DB_PATH); cur=conn.cursor()
        cur.execute("INSERT INTO actions (owner,ts,entry) VALUES (?,?,?)",(self.username,datetime.now().isoformat(),entry))
        conn.commit(); conn.close()

    def load_historia(self):
        self.hist.delete("1.0",tk.END)
        conn=sqlite3.connect(DB_PATH); cur=conn.cursor()
        for row in cur.execute("SELECT ts,entry FROM actions WHERE owner=? ORDER BY ts DESC",(self.username,)):
            self.hist.insert(tk.END,f"{row[0]} | {row[1]}\n")
        conn.close()

    # ============================
    # ZAKŁADKA ALERTY
    # ============================
    def _init_alerty(self):
        f=self.tabs["Alerty"]
        tb.Button(f,text="Dodaj alert",command=self.add_alert).pack(pady=5)
        self.alerts=ScrolledText(f,height=20); self.alerts.pack(fill="both",expand=True)
        self.load_alerts()

    def add_alert(self):
        msg=simpledialog.askstring("Alert","Treść alertu:")
        if not msg: return
        due=simpledialog.askstring("Alert","Data (YYYY-MM-DD):")
        conn=sqlite3.connect(DB_PATH); cur=conn.cursor()
        cur.execute("INSERT INTO alerts (owner,message,due,done) VALUES (?,?,?,0)",(self.username,msg,due))
        conn.commit(); conn.close()
        notification.notify(title="Nowy alert",message=msg,timeout=5)
        self.load_alerts()

    def load_alerts(self):
        self.alerts.delete("1.0",tk.END)
        conn=sqlite3.connect(DB_PATH); cur=conn.cursor()
        for row in cur.execute("SELECT message,due,done FROM alerts WHERE owner=?",(self.username,)):
            self.alerts.insert(tk.END,f"{row[1]} | {row[0]} | {'✓' if row[2] else '✗'}\n")
        conn.close()

    # ============================
    # ZAKŁADKA WIZUALIZACJE
    # ============================
    def _init_wizualizacje(self):
        f=self.tabs["Wizualizacje"]
        tb.Button(f,text="Wykres plonów",command=self.plot_yields).pack(pady=5)
        tb.Button(f,text="Wykres finansów",command=self.plot_fin).pack(pady=5)
        tb.Button(f,text="Wykres monitoringu",command=self.plot_mon).pack(pady=5)

    def plot_yields(self):
        conn=sqlite3.connect(DB_PATH); cur=conn.cursor()
        years=[]; vals=[]
        for row in cur.execute("SELECT year,yield_t_ha FROM yields WHERE owner=?",(self.username,)):
            years.append(row[0]); vals.append(row[1])
        conn.close()
        if not years: messagebox.showinfo("Brak danych","Brak danych plonów"); return
        fig,ax=plt.subplots(); ax.bar(years,vals); ax.set_title("Plony t/ha"); self._show_plot(fig)

    def plot_fin(self):
        conn=sqlite3.connect(DB_PATH); cur=conn.cursor()
        cost=0; inc=0
        for row in cur.execute("SELECT cost,income FROM finances WHERE owner=?",(self.username,)):
            cost+=row[0] or 0; inc+=row[1] or 0
        conn.close()
        fig,ax=plt.subplots(); ax.pie([cost,inc],labels=["Koszty","Przychody"],autopct="%1.1f%%"); ax.set_title("Finanse"); self._show_plot(fig)

    def plot_mon(self):
        conn=sqlite3.connect(DB_PATH); cur=conn.cursor()
        dates=[]; phs=[]
        for row in cur.execute("SELECT date,ph FROM monitoring WHERE owner=?",(self.username,)):
            dates.append(row[0]); phs.append(row[1])
        conn.close()
        if not dates: messagebox.showinfo("Brak","Brak danych monitoringu"); return
        fig,ax=plt.subplots(); ax.plot(dates,phs,marker="o"); ax.set_title("pH gleby"); self._show_plot(fig)

    def _show_plot(self,fig):
        win=tb.Toplevel(self); win.title("Wykres")
        canvas=FigureCanvasTkAgg(fig,master=win); canvas.draw(); canvas.get_tk_widget().pack(fill="both",expand=True)

    # ============================
    # ZAKŁADKA MAPA PÓL
    # ============================
    def _init_mapa(self):
        f=self.tabs["Mapa pól"]
        self.canvas=tk.Canvas(f,bg="white"); self.canvas.pack(fill="both",expand=True)
        self.canvas.bind("<Button-1>",self._map_click)
        tb.Button(f,text="Zapisz pola",command=self.save_fields).pack()
        self.fields_temp=[]
        self.load_fields()

    def _map_click(self,e):
        if len(self.fields_temp)==1:
            x1,y1=self.fields_temp[0]; x2,y2=e.x,e.y
            self.canvas.create_rectangle(x1,y1,x2,y2,outline="green")
            name=simpledialog.askstring("Pole","Nazwa pola:")
            if not name: name="pole"
            conn=sqlite3.connect(DB_PATH); cur=conn.cursor()
            cur.execute("INSERT INTO fields (owner,name,x1,y1,x2,y2) VALUES (?,?,?,?,?,?)",(self.username,name,x1,y1,x2,y2))
            conn.commit(); conn.close()
            self.log_action(f"Dodano pole {name}")
            self.fields_temp=[]
        else:
            self.fields_temp=[(e.x,e.y)]

    def load_fields(self):
        conn=sqlite3.connect(DB_PATH); cur=conn.cursor()
        for row in cur.execute("SELECT name,x1,y1,x2,y2 FROM fields WHERE owner=?",(self.username,)):
            self.canvas.create_rectangle(row[1],row[2],row[3],row[4],outline="blue")
            self.canvas.create_text((row[1]+row[3])//2,(row[2]+row[4])//2,text=row[0])
        conn.close()

    def save_fields(self):
        messagebox.showinfo("OK","Pola zapisane w bazie")

    # ============================
    # ZAKŁADKA ASYSTENT AI
    # ============================
    def _init_ai(self):
        f=self.tabs["Asystent AI"]
        tb.Button(f,text="Podaj parametry",command=self.ai_suggest).pack(pady=5)
        self.aibox=ScrolledText(f,height=20); self.aibox.pack(fill="both",expand=True)

    def ai_suggest(self):
        ph=simpledialog.askfloat("AI","Podaj pH gleby:")
        moist=simpledialog.askfloat("AI","Podaj wilgotność %:")
        txt="Sugestie:\n"
        if ph and ph<5.5: txt+="- Zalecane wapnowanie\n"
        if moist and moist<30: txt+="- Potrzebne nawodnienie\n"
        if ph and 6<=ph<=7: txt+="- pH optymalne dla pszenicy\n"
        if moist and moist>70: txt+="- Gleba zbyt mokra, ryzyko chorób\n"
        self.aibox.insert(tk.END,txt+"\n")

# ============================
# MAIN
# ============================
def main():
    u=load_session_username()
    if u: MainPanel(u).mainloop()
    else: LoginPanel().mainloop()

if __name__=="__main__":
    main()
