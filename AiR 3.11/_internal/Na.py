#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# KozyManager AIR/IRZ++ – Uprawy (15 funkcji, ttkbootstrap UI, autologowanie)
# Autor: ChatGPT dla Feniks

import os, json, sqlite3, hashlib, binascii, logging, re
from datetime import datetime
from typing import Optional, List, Dict, Tuple

import tkinter as tk
from tkinter import messagebox, simpledialog, filedialog
from tkinter.scrolledtext import ScrolledText

from PIL import Image, ImageTk
import requests
import ttkbootstrap as tb

# ============================
# ŚCIEŻKI
# ============================
APPDATA = os.getenv("APPDATA") or os.path.expanduser("~/.config")
APPDIR = os.path.join(APPDATA, "KozyManager")
os.makedirs(APPDIR, exist_ok=True)

DB_PATH       = os.path.join(APPDIR, "users.db")
SESSION_FILE  = os.path.join(APPDIR, "session_token.json")
LOG_DIR       = os.path.join(APPDIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)
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
    # Użytkownicy
    cur.execute("""CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY,
        username TEXT UNIQUE,
        password_hash TEXT,
        salt TEXT
    )""")
    # Uprawy
    cur.execute("""CREATE TABLE IF NOT EXISTS crops (
        id INTEGER PRIMARY KEY,
        owner TEXT,
        name TEXT,
        sow_date TEXT,
        notes TEXT
    )""")
    # Plany zabiegów
    cur.execute("""CREATE TABLE IF NOT EXISTS plans (
        id INTEGER PRIMARY KEY,
        owner TEXT,
        crop_id INTEGER,
        task TEXT,
        date TEXT
    )""")
    # Monitoring manualny
    cur.execute("""CREATE TABLE IF NOT EXISTS monitoring (
        id INTEGER PRIMARY KEY,
        owner TEXT,
        crop_id INTEGER,
        ph REAL,
        moisture REAL,
        notes TEXT,
        date TEXT
    )""")
    # Finanse
    cur.execute("""CREATE TABLE IF NOT EXISTS finances (
        id INTEGER PRIMARY KEY,
        owner TEXT,
        crop_id INTEGER,
        cost REAL,      -- koszt dodatni
        income REAL,    -- przychód dodatni
        note TEXT,
        date TEXT
    )""")
    # Plony (do optymalizacji)
    cur.execute("""CREATE TABLE IF NOT EXISTS yields (
        id INTEGER PRIMARY KEY,
        owner TEXT,
        crop_id INTEGER,
        year INTEGER,
        yield_t_ha REAL
    )""")
    # Historia działań
    cur.execute("""CREATE TABLE IF NOT EXISTS actions (
        id INTEGER PRIMARY KEY,
        owner TEXT,
        ts TEXT,
        entry TEXT
    )""")
    # Alerty
    cur.execute("""CREATE TABLE IF NOT EXISTS alerts (
        id INTEGER PRIMARY KEY,
        owner TEXT,
        message TEXT,
        due TEXT,        -- 'YYYY-MM-DD HH:MM'
        done INTEGER DEFAULT 0
    )""")
    conn.commit()
    conn.close()

init_db()

# ============================
# HASŁA (PBKDF2)
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
    token = {
        "username": username,
        "data_key": data_key,
        "created_at": datetime.now().isoformat(timespec="seconds")
    }
    with open(SESSION_FILE, "w", encoding="utf-8") as f:
        json.dump(token, f, indent=2)

def load_session_username() -> Optional[str]:
    if not os.path.exists(SESSION_FILE):
        return None
    try:
        with open(SESSION_FILE, "r", encoding="utf-8") as f:
            tok = json.load(f)
        return tok.get("username")
    except Exception:
        return None

def clear_session():
    try:
        if os.path.exists(SESSION_FILE):
            os.remove(SESSION_FILE)
    except Exception as e:
        logging.exception("clear_session: %s", e)

# ============================
# AUTH
# ============================
def register_user(username: str, password: str) -> bool:
    ph, salt = pbkdf2_hash(password)
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("INSERT INTO users (username,password_hash,salt) VALUES (?,?,?)",
                    (username, ph, salt))
        conn.commit()
        conn.close()
        return True
    except sqlite3.IntegrityError:
        return False

def check_login(username: str, password: str) -> Optional[str]:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT password_hash, salt FROM users WHERE username=?", (username,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    if verify_password(row[0], row[1], password):
        # zwróć "data_key" – wykorzystamy hash jako klucz sesji
        return row[0]
    return None

# ============================
#   PANEL LOGIN (TTKBOOTSTRAP)
# ============================
class LoginPanel(tb.Window):
    def __init__(self):
        super().__init__(themename="darkly")
        self.title("AIR/IRZ++ – Panel logowania")
        self.geometry("480x360")
        self.resizable(False, False)

        # tło
        try:
            bg = Image.open("Background.jpg")
            self.bg_tk = ImageTk.PhotoImage(bg.resize((480, 360)))
            bg_label = tk.Label(self, image=self.bg_tk)
            bg_label.place(x=0, y=0, relwidth=1, relheight=1)
        except Exception:
            self.configure(bg="#1f1f1f")

        card = tb.Frame(self, padding=20, style="Card.TFrame")
        card.place(relx=0.5, rely=0.5, anchor="center")

        tb.Label(card, text="Logowanie do AIR/IRZ++", font="-size 14 -weight bold").grid(row=0, column=0, columnspan=2, pady=(0, 14))

        tb.Label(card, text="Użytkownik:").grid(row=1, column=0, sticky="e", padx=6, pady=6)
        tb.Label(card, text="Hasło:").grid(row=2, column=0, sticky="e", padx=6, pady=6)

        self.username = tb.Entry(card, width=24)
        self.password = tb.Entry(card, show="*", width=24)
        self.username.grid(row=1, column=1, pady=6)
        self.password.grid(row=2, column=1, pady=6)

        btns = tb.Frame(card)
        btns.grid(row=3, column=0, columnspan=2, pady=(12, 6))
        tb.Button(btns, text="Zaloguj", bootstyle="success", width=16,
                  command=self.login).pack(side="left", padx=4)
        tb.Button(btns, text="Rejestracja", bootstyle="secondary", width=16,
                  command=self.open_register).pack(side="left", padx=4)

    def login(self):
        username = self.username.get().strip()
        password = self.password.get().strip()
        if not username or not password:
            messagebox.showerror("Błąd", "Podaj nazwę i hasło", parent=self)
            return

        data_key = check_login(username, password)
        if data_key:
            save_session_token(username, data_key)
            messagebox.showinfo("Sukces", f"Zalogowano jako {username}", parent=self)
            self.destroy()  # ✅ ważne: zamykamy okno logowania
            MainPanel(username).mainloop()
        else:
            messagebox.showerror("Błąd", "Nieprawidłowe dane logowania", parent=self)

    def open_register(self):
        RegisterPanel(self)


# ============================
# PANEL REJESTRACJI
# ============================
class RegisterPanel(tb.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Rejestracja użytkownika")
        self.geometry("360x240")
        self.resizable(False, False)

        tb.Label(self, text="Nowy użytkownik:", font="-size 12").pack(pady=10)
        self.user = tb.Entry(self, width=24)
        self.user.pack(pady=6)
        tb.Label(self, text="Hasło:").pack()
        self.passw = tb.Entry(self, width=24, show="*")
        self.passw.pack(pady=6)
        tb.Button(self, text="Zarejestruj", bootstyle="success", command=self.do_register).pack(pady=10)

    def do_register(self):
        u = self.user.get().strip()
        p = self.passw.get().strip()
        if not u or not p:
            messagebox.showerror("Błąd", "Podaj dane", parent=self)
            return
        if register_user(u, p):
            messagebox.showinfo("OK", "Zarejestrowano", parent=self)
            self.destroy()
        else:
            messagebox.showerror("Błąd", "Użytkownik istnieje", parent=self)

# ============================
# PANEL GŁÓWNY
# ============================
class MainPanel(tb.Window):
    def __init__(self, username: str):
        super().__init__(themename="darkly")
        self.username = username
        self.title(f"AIR/IRZ++ – Uprawy ({username})")
        self.geometry("1100x750")
        self.resizable(False, False)

        # Tło
        try:
            bg = Image.open("UPR.jpg")
            self.bg_img = ImageTk.PhotoImage(bg.resize((1100, 750)))
            bg_label = tk.Label(self, image=self.bg_img)
            bg_label.place(x=0, y=0, relwidth=1, relheight=1)
        except Exception:
            self.configure(bg="#2b2b2b")

        # Pasek górny
        top = tb.Frame(self)
        top.pack(fill="x", padx=8, pady=6)
        tb.Label(top, text=f"Zalogowany: {self.username}", font="-size 11 -weight bold").pack(side="left")
        tb.Button(top, text="Wyloguj", bootstyle="warning-outline", command=self.logout).pack(side="right")

        # Notebook
        nb = tb.Notebook(self, bootstyle="info")
        nb.pack(fill="both", expand=True, padx=6, pady=6)

        self.tabs: Dict[str, tk.Frame] = {}
        for tab in ["Uprawy", "Planowanie", "Pogoda", "Dokumentacja", "Monitoring",
                    "Optymalizacja", "Finanse", "Historia", "Alerty"]:
            frame = tb.Frame(nb)
            nb.add(frame, text=tab)
            self.tabs[tab] = frame

        # Inicjalizacja zakładek
        self._init_uprawy_tab()
        self._init_planowanie_tab()
        self._init_pogoda_tab()
        self._init_dokumentacja_tab()
        self._init_monitoring_tab()
        self._init_opt_tab()
        self._init_finanse_tab()
        self._init_historia_tab()
        self._init_alerty_tab()

        # Harmonogram sprawdzania alertów co 60 s
        self.after(60_000, self._alert_tick)

    # ========== Wspólne ==========
    def _db(self):
        return sqlite3.connect(DB_PATH)

    def _write_action(self, text: str):
        try:
            with self._db() as conn:
                cur = conn.cursor()
                cur.execute("INSERT INTO actions (owner, ts, entry) VALUES (?,?,?)",
                            (self.username, datetime.now().isoformat(timespec="seconds"), text))
                conn.commit()
        except Exception as e:
            logging.exception("write_action: %s", e)

    def logout(self):
        clear_session()
        messagebox.showinfo("Wylogowano", "Sesja zakończona.")
        self.destroy()
        LoginPanel().mainloop()

    # ========== Zakładka: Uprawy ==========
    def _init_uprawy_tab(self):
        f = self.tabs["Uprawy"]

        header = tb.Frame(f)
        header.pack(fill="x", padx=8, pady=6)
        tb.Label(header, text="Twoje uprawy", font="-size 14 -weight bold").pack(side="left")

        btns = tb.Frame(f)
        btns.pack(fill="x", padx=8, pady=(0,6))
        tb.Button(btns, text="Dodaj", bootstyle="success", command=self.add_crop).pack(side="left", padx=3)
        tb.Button(btns, text="Edytuj", command=self.edit_crop).pack(side="left", padx=3)
        tb.Button(btns, text="Usuń", bootstyle="danger", command=self.delete_crop).pack(side="left", padx=3)
        tb.Button(btns, text="Eksport raport TXT", bootstyle="secondary", command=self.export_crops_report).pack(side="left", padx=12)

        body = tb.Frame(f)
        body.pack(fill="both", expand=True, padx=8, pady=6)

        self.crops_list = tk.Listbox(body, width=40)
        self.crops_list.pack(side="left", fill="y")
        self.crops_list.bind("<<ListboxSelect>>", lambda e: self.show_crop_details())

        self.crop_detail = ScrolledText(body, height=20)
        self.crop_detail.pack(side="left", fill="both", expand=True, padx=8)

        # combobox do filtrowania w innych zakładkach
        bottom = tb.Frame(f)
        bottom.pack(fill="x", padx=8, pady=6)
        tb.Label(bottom, text="Bieżąca uprawa (do planów/monitoringu/finansów):").pack(side="left")
        self.current_crop_var = tk.StringVar(value="")
        self.current_crop_cb = tb.Combobox(bottom, textvariable=self.current_crop_var, width=35)
        self.current_crop_cb.pack(side="left", padx=6)

        self._reload_crops()

    def _fetch_crops(self) -> List[Tuple[int, str, str, str]]:
        with self._db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id, name, sow_date, notes FROM crops WHERE owner=? ORDER BY id DESC", (self.username,))
            return cur.fetchall()

    def _reload_crops(self):
        self.crops_list.delete(0, tk.END)
        self._crops_cache = self._fetch_crops()
        names = []
        for cid, name, sow, notes in self._crops_cache:
            self.crops_list.insert(tk.END, f"{name}  ({sow})")
            names.append(name)
        self.current_crop_cb["values"] = names
        self.crop_detail.delete("1.0", "end")

    def _selected_crop_id(self) -> Optional[int]:
        idx = self.crops_list.curselection()
        if not idx: return None
        cid = self._crops_cache[idx[0]][0]
        return cid

    def _selected_crop_name(self) -> Optional[str]:
        idx = self.crops_list.curselection()
        if not idx: return None
        return self._crops_cache[idx[0]][1]

    def show_crop_details(self):
        cid = self._selected_crop_id()
        if cid is None:
            return
        _, name, sow, notes = self._crops_cache[self.crops_list.curselection()[0]]
        self.crop_detail.delete("1.0", "end")
        self.crop_detail.insert("end", f"Nazwa: {name}\nData siewu: {sow}\n\nNotatki:\n{notes or '-'}\n")

    def add_crop(self):
        name = simpledialog.askstring("Nowa uprawa", "Nazwa:", parent=self)
        if not name: return
        sow = simpledialog.askstring("Data siewu", "YYYY-MM-DD:", parent=self)
        notes = simpledialog.askstring("Notatki", "Dodatkowe informacje:", parent=self)
        with self._db() as conn:
            cur = conn.cursor()
            cur.execute("INSERT INTO crops (owner,name,sow_date,notes) VALUES (?,?,?,?)",
                        (self.username, name, sow or "", notes or ""))
            conn.commit()
        self._write_action(f"Dodano uprawę: {name}")
        self._reload_crops()

    def edit_crop(self):
        cid = self._selected_crop_id()
        if cid is None:
            messagebox.showinfo("Brak wyboru", "Zaznacz uprawę.")
            return
        name = self._crops_cache[self.crops_list.curselection()[0]][1]
        new_name = simpledialog.askstring("Edycja", "Nazwa:", initialvalue=name, parent=self)
        if new_name is None: return
        sow = simpledialog.askstring("Data siewu", "YYYY-MM-DD:", initialvalue=self._crops_cache[self.crops_list.curselection()[0]][2], parent=self)
        notes = simpledialog.askstring("Notatki", "Opis:", initialvalue=self._crops_cache[self.crops_list.curselection()[0]][3], parent=self)
        with self._db() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE crops SET name=?, sow_date=?, notes=? WHERE id=? AND owner=?",
                        (new_name, sow or "", notes or "", cid, self.username))
            conn.commit()
        self._write_action(f"Zmieniono uprawę: {name} -> {new_name}")
        self._reload_crops()

    def delete_crop(self):
        cid = self._selected_crop_id()
        if cid is None:
            messagebox.showinfo("Brak wyboru", "Zaznacz uprawę.")
            return
        if messagebox.askyesno("Potwierdź", "Usunąć uprawę i powiązane dane?"):
            with self._db() as conn:
                cur = conn.cursor()
                cur.execute("DELETE FROM crops WHERE id=? AND owner=?", (cid, self.username))
                # kasujemy powiązane dane
                cur.execute("DELETE FROM plans WHERE crop_id=?", (cid,))
                cur.execute("DELETE FROM monitoring WHERE crop_id=?", (cid,))
                cur.execute("DELETE FROM finances WHERE crop_id=?", (cid,))
                cur.execute("DELETE FROM yields WHERE crop_id=?", (cid,))
                conn.commit()
            self._write_action(f"Usunięto uprawę ID={cid}")
            self._reload_crops()

    def export_crops_report(self):
        rows = self._fetch_crops()
        if not rows:
            messagebox.showinfo("Brak danych", "Brak upraw.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".txt", title="Zapisz raport upraw")
        if not path: return
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"Raport upraw – {self.username}\n")
            f.write("="*50 + "\n\n")
            for cid, name, sow, notes in rows:
                f.write(f"Nazwa: {name}\nSiew: {sow}\nNotatki: {notes or '-'}\n\n")
        messagebox.showinfo("OK", f"Zapisano do {path}")

    # ========== Zakładka: Planowanie ==========
    def _init_planowanie_tab(self):
        f = self.tabs["Planowanie"]
        tb.Label(f, text="Planowanie zabiegów", font="-size 14 -weight bold").pack(pady=6)

        row1 = tb.Frame(f)
        row1.pack(fill="x", padx=8, pady=6)
        tb.Label(row1, text="Uprawa:").pack(side="left")
        self.plan_crop_cb = tb.Combobox(row1, width=30, values=[n for n in (self.current_crop_cb["values"] or [])])
        self.plan_crop_cb.pack(side="left", padx=6)
        tb.Label(row1, text="Data (YYYY-MM-DD):").pack(side="left", padx=(16,4))
        self.plan_date_e = tb.Entry(row1, width=14)
        self.plan_date_e.pack(side="left")
        tb.Label(row1, text="Zadanie:").pack(side="left", padx=(16,4))
        self.plan_task_e = tb.Entry(row1, width=40)
        self.plan_task_e.pack(side="left")

        row2 = tb.Frame(f)
        row2.pack(fill="x", padx=8, pady=6)
        tb.Button(row2, text="Dodaj", bootstyle="success", command=self.add_plan).pack(side="left", padx=3)
        tb.Button(row2, text="Edytuj", command=self.edit_plan).pack(side="left", padx=3)
        tb.Button(row2, text="Usuń", bootstyle="danger", command=self.delete_plan).pack(side="left", padx=3)
        tb.Button(row2, text="Odśwież", command=self.load_plans).pack(side="left", padx=12)

        self.plan_list = tk.Listbox(f)
        self.plan_list.pack(fill="both", expand=True, padx=8, pady=(0,8))

        self.load_plans()

    def _crop_id_by_name(self, name: Optional[str]) -> Optional[int]:
        if not name: return None
        with self._db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id FROM crops WHERE owner=? AND name=?", (self.username, name))
            r = cur.fetchone()
            return r[0] if r else None

    def add_plan(self):
        crop_name = self.plan_crop_cb.get().strip() or self.current_crop_var.get().strip()
        crop_id = self._crop_id_by_name(crop_name)
        date = self.plan_date_e.get().strip()
        task = self.plan_task_e.get().strip()
        if not task:
            messagebox.showwarning("Brak", "Wpisz treść zadania.")
            return
        with self._db() as conn:
            cur = conn.cursor()
            cur.execute("INSERT INTO plans (owner,crop_id,task,date) VALUES (?,?,?,?)",
                        (self.username, crop_id, task, date or ""))
            conn.commit()
        self._write_action(f"Plan: {date or '-'} {task} ({crop_name or 'bez uprawy'})")
        self.load_plans()

    def _fetch_plans(self):
        with self._db() as conn:
            cur = conn.cursor()
            cur.execute("""SELECT p.id, c.name, p.date, p.task
                           FROM plans p LEFT JOIN crops c ON p.crop_id=c.id
                           WHERE p.owner=? ORDER BY COALESCE(p.date,'9999'), p.id DESC""", (self.username,))
            return cur.fetchall()

    def load_plans(self):
        # odśwież listę możliwych upraw
        self.plan_crop_cb["values"] = [n for n in (self.current_crop_cb["values"] or [])]

        self.plan_list.delete(0, tk.END)
        self._plans_cache = self._fetch_plans()
        for pid, cname, date, task in self._plans_cache:
            self.plan_list.insert(tk.END, f"{date or '-'} | {cname or '—'} | {task}")

    def _selected_plan_id(self) -> Optional[int]:
        idx = self.plan_list.curselection()
        if not idx: return None
        return self._plans_cache[idx[0]][0]

    def edit_plan(self):
        pid = self._selected_plan_id()
        if pid is None:
            messagebox.showinfo("Brak wyboru", "Zaznacz zadanie.")
            return
        _, cname, date, task = self._plans_cache[self.plan_list.curselection()[0]]
        new_date = simpledialog.askstring("Edycja daty", "YYYY-MM-DD:", initialvalue=date or "", parent=self)
        new_task = simpledialog.askstring("Edycja zadania", "Treść:", initialvalue=task or "", parent=self)
        with self._db() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE plans SET date=?, task=? WHERE id=? AND owner=?",
                        (new_date or "", new_task or "", pid, self.username))
            conn.commit()
        self._write_action(f"Edycja planu ID={pid}")
        self.load_plans()

    def delete_plan(self):
        pid = self._selected_plan_id()
        if pid is None:
            messagebox.showinfo("Brak wyboru", "Zaznacz zadanie.")
            return
        if messagebox.askyesno("Potwierdź", "Usunąć zadanie?"):
            with self._db() as conn:
                cur = conn.cursor()
                cur.execute("DELETE FROM plans WHERE id=? AND owner=?", (pid, self.username))
                conn.commit()
            self._write_action(f"Usunięto plan ID={pid}")
            self.load_plans()

    # ========== Zakładka: Pogoda ==========
    def _init_pogoda_tab(self):
        f = self.tabs["Pogoda"]
        tb.Label(f, text="Prognoza pogody (Open-Meteo)", font="-size 14 -weight bold").pack(pady=6)

        bar = tb.Frame(f)
        bar.pack(fill="x", padx=8, pady=6)
        self.weather_city = tb.Entry(bar, width=28)
        self.weather_city.pack(side="left")
        tb.Button(bar, text="Pobierz", bootstyle="success", command=self.get_weather_by_city).pack(side="left", padx=6)
        tb.Button(bar, text="Auto (IP)", command=self.get_weather_by_ip).pack(side="left")

        self.weather_box = ScrolledText(f, height=18)
        self.weather_box.pack(fill="both", expand=True, padx=8, pady=6)

    def get_weather_by_city(self):
        city = (self.weather_city.get() or "").strip()
        if not city:
            messagebox.showwarning("Brak", "Wpisz miejscowość.")
            return
        try:
            r = requests.get(f"https://geocoding-api.open-meteo.com/v1/search?name={city}&count=1", timeout=15)
            data = r.json()
            lat, lon = data["results"][0]["latitude"], data["results"][0]["longitude"]
            self._fetch_weather(lat, lon, city)
        except Exception as e:
            self.weather_box.insert("end", f"Błąd pobierania lokalizacji: {e}\n")

    def get_weather_by_ip(self):
        try:
            ipinfo = requests.get("https://ipinfo.io/json", timeout=12).json()
            loc = ipinfo["loc"].split(",")
            lat, lon = float(loc[0]), float(loc[1])
            city = ipinfo.get("city", "Lokalizacja IP")
            self._fetch_weather(lat, lon, city)
        except Exception as e:
            self.weather_box.insert("end", f"Błąd auto-lokalizacji: {e}\n")

    def _fetch_weather(self, lat: float, lon: float, city: str):
        url = (
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lon}"
            "&daily=temperature_2m_max,temperature_2m_min,precipitation_sum"
            "&forecast_days=7&timezone=auto"
        )
        try:
            r = requests.get(url, timeout=20).json()
            self.weather_box.delete("1.0", "end")
            self.weather_box.insert("end", f"Prognoza pogody dla {city}:\n\n")
            days = r["daily"]["time"]
            tmax = r["daily"]["temperature_2m_max"]
            tmin = r["daily"]["temperature_2m_min"]
            prcp = r["daily"]["precipitation_sum"]
            for i in range(len(days)):
                self.weather_box.insert("end", f"{days[i]}: {tmin[i]}–{tmax[i]}°C, opady: {prcp[i]} mm\n")
        except Exception as e:
            self.weather_box.insert("end", f"Błąd pobierania prognozy: {e}\n")

    # ========== Zakładka: Dokumentacja ==========
    def _init_dokumentacja_tab(self):
        f = self.tabs["Dokumentacja"]
        tb.Label(f, text="Dokumentacja ARiMR (TXT)", font="-size 14 -weight bold").pack(pady=6)
        tb.Button(f, text="Generuj raport ARiMR (TXT)", bootstyle="secondary", command=self.export_arimr).pack(pady=12)

        self.doc_box = ScrolledText(f, height=18)
        self.doc_box.pack(fill="both", expand=True, padx=8, pady=6)

    def export_arimr(self):
        with self._db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT name, sow_date, notes FROM crops WHERE owner=?", (self.username,))
            rows = cur.fetchall()
        if not rows:
            messagebox.showinfo("Brak danych", "Brak upraw.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".txt", title="Zapisz raport ARiMR")
        if not path: return
        with open(path, "w", encoding="utf-8") as f:
            f.write("Raport ARiMR – ewidencja upraw\n")
            f.write("="*60 + "\n\n")
            for name, sow, notes in rows:
                f.write(f"Uprawa: {name}\nData siewu: {sow}\nOpis: {notes or '-'}\n\n")
        self.doc_box.delete("1.0", "end")
        self.doc_box.insert("end", f"Zapisano raport ARiMR do: {path}\n")
        self._write_action("Wygenerowano raport ARiMR")

    # ========== Zakładka: Monitoring ==========
    def _init_monitoring_tab(self):
        f = self.tabs["Monitoring"]
        tb.Label(f, text="Monitoring manualny (pH / wilgotność / notatki)", font="-size 14 -weight bold").pack(pady=6)

        bar = tb.Frame(f)
        bar.pack(fill="x", padx=8, pady=6)
        tb.Label(bar, text="Uprawa:").pack(side="left")
        self.mon_crop_cb = tb.Combobox(bar, width=30, values=[n for n in (self.current_crop_cb["values"] or [])])
        self.mon_crop_cb.pack(side="left", padx=6)
        tb.Button(bar, text="Dodaj wpis", bootstyle="success", command=self.add_monitoring).pack(side="left", padx=6)
        tb.Button(bar, text="Odśwież", command=self.load_monitoring).pack(side="left")

        self.mon_box = ScrolledText(f, height=18)
        self.mon_box.pack(fill="both", expand=True, padx=8, pady=6)

        self.load_monitoring()

    def add_monitoring(self):
        crop_name = self.mon_crop_cb.get().strip() or self.current_crop_var.get().strip()
        crop_id = self._crop_id_by_name(crop_name)
        ph = simpledialog.askfloat("pH", "Podaj pH:", parent=self)
        moist = simpledialog.askfloat("Wilgotność %", "Podaj wilgotność gleby:", parent=self)
        note = simpledialog.askstring("Notatki", "Opis:", parent=self)
        with self._db() as conn:
            cur = conn.cursor()
            cur.execute("""INSERT INTO monitoring (owner,crop_id,ph,moisture,notes,date)
                           VALUES (?,?,?,?,?,?)""",
                        (self.username, crop_id, ph, moist, note, datetime.now().date().isoformat()))
            conn.commit()
        self._write_action(f"Monitoring: pH={ph} Wilg={moist}% ({crop_name or '—'})")
        self.load_monitoring()

    def _fetch_monitoring(self):
        with self._db() as conn:
            cur = conn.cursor()
            cur.execute("""SELECT m.date, c.name, m.ph, m.moisture, m.notes
                           FROM monitoring m LEFT JOIN crops c ON m.crop_id=c.id
                           WHERE m.owner=? ORDER BY m.date DESC, m.id DESC""", (self.username,))
            return cur.fetchall()

    def load_monitoring(self):
        # odśwież listę upraw w comboboxie
        self.mon_crop_cb["values"] = [n for n in (self.current_crop_cb["values"] or [])]

        self.mon_box.delete("1.0", "end")
        rows = self._fetch_monitoring()
        for d, cname, ph, moist, note in rows:
            self.mon_box.insert("end", f"{d} | {cname or '—'} | pH {ph} | wilg {moist}% | {note or '-'}\n")

    # ========== Zakładka: Optymalizacja ==========
    def _init_opt_tab(self):
        f = self.tabs["Optymalizacja"]
        tb.Label(f, text="Optymalizacja plonów (porównania lat)", font="-size 14 -weight bold").pack(pady=6)

        row = tb.Frame(f); row.pack(fill="x", padx=8, pady=6)
        tb.Label(row, text="Uprawa:").pack(side="left")
        self.yld_crop_cb = tb.Combobox(row, width=30, values=[n for n in (self.current_crop_cb["values"] or [])])
        self.yld_crop_cb.pack(side="left", padx=6)
        tb.Button(row, text="Dodaj plon (t/ha)", bootstyle="success", command=self.add_yield).pack(side="left", padx=6)
        tb.Button(row, text="Analizuj", command=self.analyze_yields).pack(side="left", padx=6)

        self.opt_box = ScrolledText(f, height=18)
        self.opt_box.pack(fill="both", expand=True, padx=8, pady=6)

    def add_yield(self):
        crop_name = self.yld_crop_cb.get().strip() or self.current_crop_var.get().strip()
        cid = self._crop_id_by_name(crop_name)
        if cid is None:
            messagebox.showinfo("Brak uprawy", "Wybierz uprawę.")
            return
        year = simpledialog.askinteger("Rok", "Podaj rok (YYYY):", parent=self)
        yval = simpledialog.askfloat("Plon (t/ha)", "Podaj plon:", parent=self)
        if year is None or yval is None: return
        with self._db() as conn:
            cur = conn.cursor()
            cur.execute("INSERT INTO yields (owner,crop_id,year,yield_t_ha) VALUES (?,?,?,?)",
                        (self.username, cid, year, yval))
            conn.commit()
        self._write_action(f"Dodano plon {yval} t/ha ({crop_name}, {year})")
        self.analyze_yields()

    def analyze_yields(self):
        crop_name = self.yld_crop_cb.get().strip() or self.current_crop_var.get().strip()
        cid = self._crop_id_by_name(crop_name)
        self.opt_box.delete("1.0", "end")
        if cid is None:
            self.opt_box.insert("end", "Wybierz uprawę.\n")
            return
        with self._db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT year, yield_t_ha FROM yields WHERE owner=? AND crop_id=? ORDER BY year",
                        (self.username, cid))
            rows = cur.fetchall()
        if not rows:
            self.opt_box.insert("end", "Brak danych plonów.\n")
            return
        # prosta analiza
        vals = [v for _, v in rows]
        avg = sum(vals)/len(vals)
        trend = "stabilny"
        if len(vals) >= 2 and vals[-1] > vals[0]*1.05: trend = "rosnący"
        if len(vals) >= 2 and vals[-1] < vals[0]*0.95: trend = "spadkowy"

        self.opt_box.insert("end", f"Uprawa: {crop_name}\n")
        for y, v in rows:
            self.opt_box.insert("end", f"  {y}: {v} t/ha\n")
        self.opt_box.insert("end", f"\nŚredni plon: {avg:.2f} t/ha\nTrend: {trend}\n")
        if trend == "spadkowy":
            self.opt_box.insert("end", "\nSugestie: rozważ zmianę odmiany, termin siewu, korektę nawożenia NPK, wapnowanie (jeśli pH < 6.2).\n")

    # ========== Zakładka: Finanse ==========
    def _init_finanse_tab(self):
        f = self.tabs["Finanse"]
        tb.Label(f, text="Finanse upraw (koszty / przychody)", font="-size 14 -weight bold").pack(pady=6)

        row = tb.Frame(f); row.pack(fill="x", padx=8, pady=6)
        tb.Label(row, text="Uprawa:").pack(side="left")
        self.fin_crop_cb = tb.Combobox(row, width=30, values=[n for n in (self.current_crop_cb["values"] or [])])
        self.fin_crop_cb.pack(side="left", padx=6)
        tb.Button(row, text="Dodaj koszt", bootstyle="danger", command=lambda: self.add_finance(kind="cost")).pack(side="left", padx=6)
        tb.Button(row, text="Dodaj przychód", bootstyle="success", command=lambda: self.add_finance(kind="income")).pack(side="left", padx=6)
        tb.Button(row, text="Podsumowanie", command=self.load_finances).pack(side="left", padx=6)

        self.fin_box = ScrolledText(f, height=18)
        self.fin_box.pack(fill="both", expand=True, padx=8, pady=6)

        self.load_finances()

    def add_finance(self, kind: str):
        crop_name = self.fin_crop_cb.get().strip() or self.current_crop_var.get().strip()
        cid = self._crop_id_by_name(crop_name)
        val = simpledialog.askfloat("Kwota", "Podaj kwotę (zł):", parent=self)
        note = simpledialog.askstring("Opis", "Opis:", parent=self)
        if val is None: return
        cost = income = 0.0
        if kind == "cost": cost = abs(val)
        else: income = abs(val)
        with self._db() as conn:
            cur = conn.cursor()
            cur.execute("""INSERT INTO finances (owner,crop_id,cost,income,note,date)
                           VALUES (?,?,?,?,?,?)""",
                        (self.username, cid, cost, income, note, datetime.now().date().isoformat()))
            conn.commit()
        self._write_action(f"Finanse: {kind} {val} zł ({crop_name or '—'})")
        self.load_finances()

    def load_finances(self):
        self.fin_box.delete("1.0", "end")
        with self._db() as conn:
            cur = conn.cursor()
            cur.execute("""SELECT c.name, f.date, f.cost, f.income, f.note
                           FROM finances f LEFT JOIN crops c ON f.crop_id=c.id
                           WHERE f.owner=? ORDER BY f.date DESC, f.id DESC""", (self.username,))
            rows = cur.fetchall()
            cur.execute("""SELECT SUM(cost), SUM(income) FROM finances WHERE owner=?""", (self.username,))
            s = cur.fetchone()
        total_cost = s[0] or 0.0
        total_income = s[1] or 0.0
        profit = total_income - total_cost
        for cname, d, cost, inc, note in rows:
            self.fin_box.insert("end", f"{d} | {cname or '—'} | koszt {cost or 0:.2f} zł | przychód {inc or 0:.2f} zł | {note or '-'}\n")
        self.fin_box.insert("end", f"\nSUMA kosztów: {total_cost:.2f} zł\nSUMA przychodów: {total_income:.2f} zł\nZYSK: {profit:.2f} zł\n")

    # ========== Zakładka: Historia ==========
    def _init_historia_tab(self):
        f = self.tabs["Historia"]
        tb.Label(f, text="Historia działań", font="-size 14 -weight bold").pack(pady=6)
        tb.Button(f, text="Odśwież", command=self.load_history).pack(pady=6)
        self.hist_box = ScrolledText(f, height=20)
        self.hist_box.pack(fill="both", expand=True, padx=8, pady=6)
        self.load_history()

    def load_history(self):
        self.hist_box.delete("1.0", "end")
        with self._db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT ts, entry FROM actions WHERE owner=? ORDER BY ts DESC, id DESC", (self.username,))
            rows = cur.fetchall()
        for ts, entry in rows:
            self.hist_box.insert("end", f"{ts} | {entry}\n")
        # Dodatkowo pokaż tail logu plikowego
        try:
            if os.path.exists(LOG_PATH):
                with open(LOG_PATH, "r", encoding="utf-8") as lf:
                    lines = lf.readlines()[-50:]
                self.hist_box.insert("end", "\n--- Ostatnie wpisy logu aplikacji ---\n")
                self.hist_box.insert("end", "".join(lines))
        except Exception:
            pass

    # ========== Zakładka: Alerty ==========
    def _init_alerty_tab(self):
        f = self.tabs["Alerty"]
        tb.Label(f, text="Alerty / przypomnienia", font="-size 14 -weight bold").pack(pady=6)

        row = tb.Frame(f); row.pack(fill="x", padx=8, pady=6)
        tb.Label(row, text="Treść:").pack(side="left")
        self.alert_msg_e = tb.Entry(row, width=50)
        self.alert_msg_e.pack(side="left", padx=6)
        tb.Label(row, text="Termin (YYYY-MM-DD HH:MM):").pack(side="left", padx=(12,4))
        self.alert_due_e = tb.Entry(row, width=18)
        self.alert_due_e.pack(side="left")

        row2 = tb.Frame(f); row2.pack(fill="x", padx=8, pady=6)
        tb.Button(row2, text="Dodaj alert", bootstyle="success", command=self.add_alert).pack(side="left", padx=6)
        tb.Button(row2, text="Odśwież", command=self.load_alerts).pack(side="left", padx=6)
        tb.Button(row2, text="Oznacz jako wykonany", bootstyle="secondary", command=self.mark_alert_done).pack(side="left", padx=6)

        self.alert_list = tk.Listbox(f)
        self.alert_list.pack(fill="both", expand=True, padx=8, pady=(0,8))

        self.load_alerts()

    def _fetch_alerts(self):
        with self._db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id, message, due, done FROM alerts WHERE owner=? ORDER BY done, due", (self.username,))
            return cur.fetchall()

    def load_alerts(self):
        self.alert_list.delete(0, tk.END)
        self._alerts_cache = self._fetch_alerts()
        for aid, msg, due, done in self._alerts_cache:
            flag = "✔" if done else "⏰"
            self.alert_list.insert(tk.END, f"{flag} {due or '-'} | {msg}")

    def add_alert(self):
        msg = self.alert_msg_e.get().strip()
        due = self.alert_due_e.get().strip()
        if not msg:
            messagebox.showwarning("Brak", "Wpisz treść alertu.")
            return
        if due and not re.match(r"^\d{4}-\d{2}-\d{2}( \d{2}:\d{2})?$", due):
            messagebox.showwarning("Format", "Użyj formatu YYYY-MM-DD lub YYYY-MM-DD HH:MM.")
            return
        with self._db() as conn:
            cur = conn.cursor()
            cur.execute("INSERT INTO alerts (owner,message,due,done) VALUES (?,?,?,0)",
                        (self.username, msg, due))
            conn.commit()
        self._write_action(f"Dodano alert: {msg} ({due or 'bez terminu'})")
        self.alert_msg_e.delete(0, tk.END)
        self.alert_due_e.delete(0, tk.END)
        self.load_alerts()

    def _selected_alert_id(self) -> Optional[int]:
        idx = self.alert_list.curselection()
        if not idx: return None
        return self._alerts_cache[idx[0]][0]

    def mark_alert_done(self):
        aid = self._selected_alert_id()
        if aid is None:
            messagebox.showinfo("Brak wyboru", "Zaznacz alert.")
            return
        with self._db() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE alerts SET done=1 WHERE id=? AND owner=?", (aid, self.username))
            conn.commit()
        self._write_action(f"Zamknięto alert ID={aid}")
        self.load_alerts()

    def _alert_tick(self):
        # prosty checker terminów – pokazuje przypomnienie dla przeterminowanych, nieoznaczonych alertów
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        with self._db() as conn:
            cur = conn.cursor()
            cur.execute("""SELECT id, message, due FROM alerts
                           WHERE owner=? AND done=0 AND due IS NOT NULL AND due!='' AND due<=?""",
                        (self.username, now))
            rows = cur.fetchall()
            for aid, msg, due in rows:
                try:
                    messagebox.showinfo("Przypomnienie", f"{due}\n{msg}", parent=self)
                except Exception:
                    pass
                cur.execute("UPDATE alerts SET done=1 WHERE id=?", (aid,))
            conn.commit()
        # ponowne sprawdzenie za minutę
        self.after(60_000, self._alert_tick)

# ============================
# MAIN
# ============================
def main():
    u = load_session_username()
    if u:
        MainPanel(u).mainloop()
    else:
        LoginPanel().mainloop()

if __name__ == "__main__":
    main()

