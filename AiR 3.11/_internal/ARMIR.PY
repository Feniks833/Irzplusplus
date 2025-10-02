# main.py
# -*- coding: utf-8 -*-
import os
import sys
import csv
import json
import datetime
import sqlite3
import logging
import hashlib
import hmac
import base64
import tempfile
import shutil
import subprocess
from typing import Optional, Tuple, List, Sequence

# GUI
import tkinter as tk
from tkinter import simpledialog, messagebox, filedialog
from tkinter import Menu
import ttkbootstrap as tb
from ttkbootstrap.constants import *
from PIL import Image, ImageTk

# Crypto
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

# Eksport (bez pandas/numpy)
from pathlib import Path
from openpyxl import Workbook
from reportlab.lib.pagesizes import A4, landscape
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet



# =============================
#   ŚCIEŻKI / LOGI
# =============================
APP_NAME = "IRZ – Menedżer zwierząt"
APPDIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "KozyManager")
os.makedirs(APPDIR, exist_ok=True)

SALT_FILE    = os.path.join(APPDIR, "salt.key")
AUTH_DB_FILE = os.path.join(APPDIR, "users.db")    # baza autoryzacyjna
USER_DB_MASK = os.path.join(APPDIR, "Kozy_{}.db")  # baza danych per użytkownik

logging.basicConfig(
    filename=os.path.join(APPDIR, "app.log"),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logging.info("Aplikacja startuje")

# =============================
#   ZASOBY (PyInstaller)
# =============================
def resource_path(rel_path: str) -> str:
    base_path = getattr(sys, "_MEIPASS", os.path.abspath("."))
    return os.path.join(base_path, rel_path)

# =============================
#   KDF / HASH HASEŁ
# =============================
KDF_ITERATIONS = 100_000
if not os.path.exists(SALT_FILE):
    with open(SALT_FILE, "wb") as f:
        f.write(os.urandom(16))
        logging.info("Wygenerowano nowy salt w salt.key")
with open(SALT_FILE, "rb") as f:
    salt = f.read()

def derive_fernet_key(password: str) -> bytes:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=KDF_ITERATIONS)
    return base64.urlsafe_b64encode(kdf.derive(password.encode()))

def hash_password(password: str) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, KDF_ITERATIONS)

# =============================
#   GLOBALNE / MAPY
# =============================
SESSION_FERNET: Optional[Fernet] = None
CURRENT_USER: str = ""
user_conn: Optional[sqlite3.Connection] = None
user_cursor: Optional[sqlite3.Cursor]   = None

# =============================
#   BAZA AUTH
# =============================
auth_conn   = sqlite3.connect(AUTH_DB_FILE)
auth_cursor = auth_conn.cursor()
auth_cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        username TEXT PRIMARY KEY,
        password_hash BLOB
    )
""")
auth_conn.commit()

# =============================
#   BAZA DANYCH UŻYTKOWNIKA
# =============================
def ensure_tables(cur: sqlite3.Cursor, conn: sqlite3.Connection):
    # KARTA ZWIERZĘCIA
    cur.execute("""
    CREATE TABLE IF NOT EXISTS dane (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kolczyk TEXT UNIQUE,
        gatunek TEXT,
        data_urodzenia TEXT,
        data_oznakowania TEXT,
        plec TEXT,
        rasa_kod TEXT,
        matka_kolczyk TEXT,
        siedziba_stada TEXT,
        pochodzenie TEXT,
        data_przybycia TEXT,
        status TEXT,
        data_statusu TEXT,
        imie BLOB,
        uwagi BLOB
    )
    """)
    cur.execute("PRAGMA table_info(dane)")
    d_exists = {r[1] for r in cur.fetchall()}
    needed = [
        ("kolczyk","TEXT"), ("gatunek","TEXT"), ("data_urodzenia","TEXT"),
        ("data_oznakowania","TEXT"), ("plec","TEXT"), ("rasa_kod","TEXT"),
        ("matka_kolczyk","TEXT"), ("siedziba_stada","TEXT"), ("pochodzenie","TEXT"),
        ("data_przybycia","TEXT"), ("status","TEXT"), ("data_statusu","TEXT"),
        ("imie","BLOB"), ("uwagi","BLOB")
    ]
    for col, ctype in needed:
        if col not in d_exists:
            cur.execute(f"ALTER TABLE dane ADD COLUMN {col} {ctype}")

    # REJESTR ZDARZEŃ
    cur.execute("""
    CREATE TABLE IF NOT EXISTS zdarzenia (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kolczyk TEXT,
        gatunek TEXT,
        typ TEXT,
        data_zdarzenia TEXT,
        z_siedziby TEXT,
        do_siedziby TEXT,
        dokument_nr TEXT,
        srodek_transportu TEXT,
        kierowca TEXT,
        powod TEXT,
        kraj_docelowy TEXT,
        nowy_kolczyk TEXT,
        szczegoly BLOB,
        created_at TEXT
    )
    """)
    cur.execute("PRAGMA table_info(zdarzenia)")
    z_exists = {r[1] for r in cur.fetchall()}
    z_needed = [
        ("kolczyk","TEXT"),("gatunek","TEXT"),("typ","TEXT"),("data_zdarzenia","TEXT"),
        ("z_siedziby","TEXT"),("do_siedziby","TEXT"),("dokument_nr","TEXT"),
        ("srodek_transportu","TEXT"),("kierowca","TEXT"),("powod","TEXT"),
        ("kraj_docelowy","TEXT"),("nowy_kolczyk","TEXT"),("szczegoly","BLOB"),("created_at","TEXT")
    ]
    for col, ctype in z_needed:
        if col not in z_exists:
            cur.execute(f"ALTER TABLE zdarzenia ADD COLUMN {col} {ctype}")

    conn.commit()

def init_user_db(username: str):
    global user_conn, user_cursor
    if user_conn:
        user_conn.close()
    dbfile = USER_DB_MASK.format(username)
    user_conn   = sqlite3.connect(dbfile)
    user_cursor = user_conn.cursor()
    ensure_tables(user_cursor, user_conn)
    logging.info(f"DB OK: {dbfile}")

# =============================
#   SZYFROWANIE DANYCH
# =============================
def encrypt_text(text: str) -> bytes:
    assert SESSION_FERNET, "Brak klucza sesji!"
    return SESSION_FERNET.encrypt(text.encode())

def decrypt_text(token: bytes) -> str:
    assert SESSION_FERNET, "Brak klucza sesji!"
    return SESSION_FERNET.decrypt(token).decode()

# =============================
#   REJESTRACJA / LOGOWANIE
# =============================
def register_user():
    username = simpledialog.askstring("Rejestracja", "Nazwa użytkownika:", parent=app)
    if not username:
        return
    password = simpledialog.askstring("Rejestracja", "Hasło:", show="*", parent=app)
    if not password:
        return
    auth_cursor.execute("SELECT 1 FROM users WHERE username=?", (username,))
    if auth_cursor.fetchone():
        messagebox.showerror("Błąd", "Użytkownik już istnieje.", parent=app); return
    auth_cursor.execute("INSERT INTO users(username, password_hash) VALUES(?,?)",
                        (username, hash_password(password)))
    auth_conn.commit()
    messagebox.showinfo("Sukces", "Rejestracja zakończona.", parent=app)

def login_user() -> bool:
    global SESSION_FERNET, CURRENT_USER
    username = simpledialog.askstring("Logowanie", "Nazwa użytkownika:", parent=app)
    if not username: return False
    password = simpledialog.askstring("Logowanie", "Hasło:", show="*", parent=app)
    if not password: return False
    auth_cursor.execute("SELECT password_hash FROM users WHERE username=?", (username,))
    row = auth_cursor.fetchone()
    if not row:
        messagebox.showerror("Błąd", "Nie ma takiego użytkownika.", parent=app); return False
    if not hmac.compare_digest(row[0], hash_password(password)):
        messagebox.showerror("Błąd", "Nieprawidłowe hasło.", parent=app); return False
    SESSION_FERNET = Fernet(derive_fernet_key(password))
    CURRENT_USER   = username
    init_user_db(username)
    return True

# =============================
#   HELPERY
# =============================
def validate_date(label: str, val: str) -> bool:
    if not val: return True
    try:
        datetime.datetime.strptime(val, "%d-%m-%Y")
        return True
    except Exception:
        messagebox.showerror("Błąd", f"Niepoprawna {label}: {val} (użyj DD-MM-RRRR).", parent=app)
        return False

# =============================
#   ŁADNA TABELA (filtry + sortowanie)
# =============================
class FilterableTable(tb.Frame):
    def __init__(self, master, headers: List[str], rows: List[Sequence], height=20):
        super().__init__(master, padding=8)
        self.headers = headers
        self.rows = rows[:]
        self.filtered = rows[:]
        self.sort_state = {}

        self.filter_bar = tb.Frame(self, padding=(0, 0, 0, 6))
        self.filter_bar.pack(side="top", fill="x")
        self.filter_vars: List[tk.StringVar] = []
        for i, h in enumerate(headers):
            var = tk.StringVar()
            ent = tb.Entry(self.filter_bar, textvariable=var, width=max(10, min(24, len(h)+4)))
            ent.grid(row=0, column=i, padx=4, pady=2)
            ent.bind("<KeyRelease>", lambda e: self._apply_filters())
            self.filter_vars.append(var)

        self.tree = tb.Treeview(self, columns=headers, show="headings", height=height, bootstyle="info")
        self.tree.pack(side="top", fill="both", expand=True)

        vsb = tb.Scrollbar(self, orient="vertical", command=self.tree.yview)
        hsb = tb.Scrollbar(self, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscroll=vsb.set, xscroll=hsb.set)
        vsb.place(relx=1, rely=0, relheight=1, anchor="ne")
        hsb.pack(side="bottom", fill="x")

        for c in headers:
            self.tree.heading(c, text=c, command=lambda col=c: self._toggle_sort(col))
            self.tree.column(c, width=max(90, min(260, int(11*len(c)))))

        self._refresh()

    def _refresh(self):
        self.tree.delete(*self.tree.get_children())
        for r in self.filtered:
            self.tree.insert("", "end", values=r)

    def _apply_filters(self):
        terms = [v.get().strip().lower() for v in self.filter_vars]
        def ok(row):
            for i, term in enumerate(terms):
                if term and term not in str(row[i]).lower():
                    return False
            return True
        self.filtered = [r for r in self.rows if ok(r)]
        self._refresh()

    def _toggle_sort(self, col: str):
        idx = self.headers.index(col)
        asc = not self.sort_state.get(col, True)
        self.sort_state[col] = asc
        self.filtered.sort(key=lambda r: str(r[idx]).lower(), reverse=not asc)
        self._refresh()

# =============================
#   KARTA ZWIERZĘCIA
# =============================
def add_record():
    """Szybki tryb (dialogi). Zostawiamy dla wygody."""
    if not SESSION_FERNET:
        messagebox.showerror("Błąd", "Zaloguj się ponownie.", parent=app); return

    kolczyk = simpledialog.askstring("Karta zwierzęcia", "Numer kolczyka (PL…):", parent=app)
    if not kolczyk: return

    gatunek = simpledialog.askstring("Karta zwierzęcia", "Gatunek (koza/owca/bydło/...):", parent=app) or ""
    data_uro = simpledialog.askstring("Karta zwierzęcia", "Data urodzenia (DD-MM-RRRR):", parent=app) or ""
    data_ozn = simpledialog.askstring("Karta zwierzęcia", "Data oznakowania (DD-MM-RRRR):", parent=app) or ""
    plec     = simpledialog.askstring("Karta zwierzęcia", "Płeć (M/K):", parent=app) or ""
    rasa     = simpledialog.askstring("Karta zwierzęcia", "Kod rasy (np. PLxxx):", parent=app) or ""
    matka    = simpledialog.askstring("Karta zwierzęcia", "Numer kolczyka matki (opcjonalnie):", parent=app) or ""
    siedziba = simpledialog.askstring("Karta zwierzęcia", "Numer siedziby stada (PL…):", parent=app) or ""
    pochodz  = simpledialog.askstring("Karta zwierzęcia", "Pochodzenie (urodzenie/przywóz/zakup):", parent=app) or ""
    data_prz = simpledialog.askstring("Karta zwierzęcia", "Data przybycia (DD-MM-RRRR):", parent=app) or ""
    status   = simpledialog.askstring("Karta zwierzęcia", "Status (żywe/padłe/ubój/zbyte):", parent=app) or "żywe"
    data_st  = simpledialog.askstring("Karta zwierzęcia", "Data statusu (DD-MM-RRRR) – opcjonalnie:", parent=app) or ""
    imie     = simpledialog.askstring("Karta zwierzęcia", "Imię (opcjonalnie):", parent=app) or ""
    uwagi    = simpledialog.askstring("Karta zwierzęcia", "Uwagi (opcjonalnie):", parent=app) or ""

    if not all([validate_date("data urodzenia", data_uro),
                validate_date("data oznakowania", data_ozn),
                validate_date("data przybycia", data_prz),
                validate_date("data statusu", data_st)]):
        return

    user_cursor.execute("""
        INSERT OR REPLACE INTO dane
        (kolczyk, gatunek, data_urodzenia, data_oznakowania, plec, rasa_kod, matka_kolczyk,
         siedziba_stada, pochodzenie, data_przybycia, status, data_statusu, imie, uwagi)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        kolczyk, gatunek, data_uro, data_ozn, plec, rasa, matka,
        siedziba, pochodz, data_prz, status, data_st,
        (encrypt_text(imie) if imie else None),
        (encrypt_text(uwagi) if uwagi else None),
    ))
    user_conn.commit()
    messagebox.showinfo("Sukces", "Zapisano kartę zwierzęcia.", parent=app)

# Ładny formularz karty (jedno okno)
class AddRecordDialog(tb.Toplevel):
    def __init__(self, master):
        super().__init__(master)
        self.title("Karta zwierzęcia – edycja")
        self.geometry("620x520")
        self.resizable(False, False)
        self.result = None

        frm = tb.Frame(self, padding=14, style="Card.TFrame")
        frm.pack(fill=BOTH, expand=True)

        def row(r, label, var, width=28):
            tb.Label(frm, text=label).grid(row=r, column=0, sticky="e", padx=6, pady=4)
            e = tb.Entry(frm, textvariable=var, width=width)
            e.grid(row=r, column=1, sticky="w", padx=6, pady=4)
            return e

        self.v_kolczyk = tk.StringVar()
        self.v_gatunek = tk.StringVar()
        self.v_du = tk.StringVar()
        self.v_dozn = tk.StringVar()
        self.v_plec = tk.StringVar()
        self.v_rasa = tk.StringVar()
        self.v_matka = tk.StringVar()
        self.v_siedz = tk.StringVar()
        self.v_poch = tk.StringVar()
        self.v_dprz = tk.StringVar()
        self.v_status = tk.StringVar(value="żywe")
        self.v_dst = tk.StringVar()
        self.v_imie = tk.StringVar()
        self.v_uwagi = tk.StringVar()

        r = 0
        row(r:=r+1, "Kolczyk (PL…):", self.v_kolczyk)
        row(r:=r+1, "Gatunek:", self.v_gatunek)
        row(r:=r+1, "Data urodzenia (DD-MM-RRRR):", self.v_du)
        row(r:=r+1, "Data oznakowania (DD-MM-RRRR):", self.v_dozn)
        row(r:=r+1, "Płeć (M/K):", self.v_plec, width=8)
        row(r:=r+1, "Kod rasy:", self.v_rasa)
        row(r:=r+1, "Kolczyk matki:", self.v_matka)
        row(r:=r+1, "Siedziba stada:", self.v_siedz)
        row(r:=r+1, "Pochodzenie:", self.v_poch)
        row(r:=r+1, "Data przybycia (DD-MM-RRRR):", self.v_dprz)
        row(r:=r+1, "Status:", self.v_status)
        row(r:=r+1, "Data statusu (DD-MM-RRRR):", self.v_dst)
        row(r:=r+1, "Imię:", self.v_imie)
        row(r:=r+1, "Uwagi:", self.v_uwagi)

        btns = tb.Frame(frm, padding=(0,8,0,0))
        btns.grid(row=r+2, column=0, columnspan=2, sticky="e")
        tb.Button(btns, text="Anuluj", bootstyle="secondary", command=self.destroy).pack(side="right", padx=6)
        tb.Button(btns, text="Zapisz", bootstyle="success", command=self._save).pack(side="right", padx=6)

        self.grab_set()

    def _save(self):
        vals = { 
            "kolczyk": self.v_kolczyk.get().strip(),
            "gatunek": self.v_gatunek.get().strip(),
            "data_urodzenia": self.v_du.get().strip(),
            "data_oznakowania": self.v_dozn.get().strip(),
            "plec": self.v_plec.get().strip(),
            "rasa_kod": self.v_rasa.get().strip(),
            "matka_kolczyk": self.v_matka.get().strip(),
            "siedziba_stada": self.v_siedz.get().strip(),
            "pochodzenie": self.v_poch.get().strip(),
            "data_przybycia": self.v_dprz.get().strip(),
            "status": self.v_status.get().strip() or "żywe",
            "data_statusu": self.v_dst.get().strip(),
            "imie": self.v_imie.get(),
            "uwagi": self.v_uwagi.get(),
        }
        for key,label in [("data_urodzenia","data urodzenia"), ("data_oznakowania","data oznakowania"),
                          ("data_przybycia","data przybycia"), ("data_statusu","data statusu")]:
            if not validate_date(label, vals[key]): 
                return
        if not vals["kolczyk"]:
            messagebox.showerror("Błąd", "Kolczyk jest wymagany.", parent=self); return

        user_cursor.execute("""
            INSERT OR REPLACE INTO dane
            (kolczyk, gatunek, data_urodzenia, data_oznakowania, plec, rasa_kod, matka_kolczyk,
             siedziba_stada, pochodzenie, data_przybycia, status, data_statusu, imie, uwagi)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            vals["kolczyk"], vals["gatunek"], vals["data_urodzenia"], vals["data_oznakowania"],
            vals["plec"], vals["rasa_kod"], vals["matka_kolczyk"], vals["siedziba_stada"],
            vals["pochodzenie"], vals["data_przybycia"], vals["status"], vals["data_statusu"],
            (encrypt_text(vals["imie"]) if vals["imie"] else None),
            (encrypt_text(vals["uwagi"]) if vals["uwagi"] else None),
        ))
        user_conn.commit()
        messagebox.showinfo("Sukces", "Zapisano kartę zwierzęcia.", parent=self)
        self.destroy()

def add_record_form():
    if not SESSION_FERNET:
        messagebox.showerror("Błąd", "Zaloguj się ponownie.", parent=app); return
    AddRecordDialog(app)

def _rows_to_text(rows: list, headers: list) -> str:
    widths = [max(len(h), max((len(str((r[i] if r[i] is not None else ""))) for r in rows), default=0)) for i, h in enumerate(headers)]
    def fmt_row(vals): return " | ".join(str(v if v is not None else "").ljust(w) for v, w in zip(vals, widths))
    out = [fmt_row(headers), "-+-".join("-"*w for w in widths)]
    for r in rows:
        out.append(fmt_row(r))
    return "\n".join(out)

def _show_table_window(title: str, headers: List[str], rows: List[tuple]):
    win = tb.Toplevel(app)
    win.title(title)
    win.geometry("1100x580")
    card = tb.Frame(win, padding=10, style="Card.TFrame")
    card.pack(fill=BOTH, expand=True)

    table = FilterableTable(card, headers, rows, height=20)
    table.pack(fill=BOTH, expand=True)

    actions = tb.Frame(card, padding=(0,8,0,0))
    actions.pack(side="bottom", anchor="w")
    def _export_current():
        fmt = _choose_format()
        if not fmt: return
        name = f"{title.replace(' ','_')}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"
        path = _save_dialog(name, fmt)
        if not path: return
        _export_rows_cols(headers, table.filtered, path, fmt, title=title)
    tb.Button(actions, text="⬆ Eksportuj widok…", bootstyle="info", command=_export_current).pack(side="left", padx=6)

# =============================
#   WYSZUKIWANIA / LISTY
# =============================
def show_data(where_clause: Optional[str]=None, params: Tuple=(), order_by: Optional[str]=None):
    if not SESSION_FERNET:
        messagebox.showerror("Błąd", "Zaloguj się ponownie.", parent=app); return
    q = ("SELECT kolczyk,gatunek,data_urodzenia,plec,rasa_kod,siedziba_stada,status,imie "
         "FROM dane")
    if where_clause: q += f" WHERE {where_clause}"
    if order_by:     q += f" ORDER BY {order_by}"
    user_cursor.execute(q, params); rows_db = user_cursor.fetchall()
    if not rows_db:
        messagebox.showinfo("Informacja", "Brak wyników.", parent=app); return

    rows = []
    for k, g, du, p, r, ss, st, im in rows_db:
        try: imie = decrypt_text(im) if im else ""
        except Exception: imie = "<błąd>"
        rows.append((k or "", g or "", du or "", p or "", r or "", ss or "", st or "", imie))

    headers = ["kolczyk","gatunek","data_urodzenia","plec","rasa_kod","siedziba_stada","status","data_statusu","imie"]
    _show_table_window("Kartoteka", headers, rows)

def search_by_name():
    if not SESSION_FERNET:
        messagebox.showerror("Błąd", "Zaloguj się ponownie.", parent=app); return
    fragment = simpledialog.askstring("Szukaj po imieniu", "Fragment imienia:", parent=app)
    if not fragment: return
    user_cursor.execute("SELECT kolczyk,imie FROM dane")
    rows = user_cursor.fetchall(); hits = []
    for k, im in rows:
        try:
            name = decrypt_text(im) if im else ""
        except Exception:
            continue
        if fragment.lower() in name.lower():
            hits.append((k, name))
    if not hits:
        messagebox.showinfo("Wyniki", "Brak pasujących rekordów.", parent=app); return
    headers = ["kolczyk","imie"]
    _show_table_window("Wyniki – imię", headers, hits)

def search_by_last3():
    if not SESSION_FERNET:
        messagebox.showerror("Błąd", "Zaloguj się ponownie.", parent=app); return
    last3 = simpledialog.askstring("Szukaj po kolczyku", "Ostatnie 3 cyfry:", parent=app)
    if not last3 or not last3.isdigit() or len(last3)!=3:
        messagebox.showerror("Błąd", "Podaj dokładnie 3 cyfry.", parent=app); return
    show_data(where_clause="substr(kolczyk, length(kolczyk)-2, 3) = ?", params=(last3,))

# =============================
#   ZDARZENIA – UNIWERSALNE
# =============================
EVENT_TYPES = ["urodzenie","przybycie","wybycie","przemieszczenie","uboj","padniecie","zmiana_oznakowania","korekta","status"]

def register_event():
    if not SESSION_FERNET:
        messagebox.showerror("Błąd", "Zaloguj się ponownie.", parent=app); return

    kolczyk = simpledialog.askstring("Zdarzenie", "Numer kolczyka (PL…):", parent=app)
    if not kolczyk: return

    user_cursor.execute("SELECT gatunek FROM dane WHERE kolczyk=?", (kolczyk,))
    row = user_cursor.fetchone()
    gatunek = row[0] if row and row[0] else (simpledialog.askstring("Zdarzenie", "Gatunek (koza/owca/bydło/...):", parent=app) or "")

    typ = simpledialog.askstring("Zdarzenie", f"Typ zdarzenia {EVENT_TYPES}:", parent=app)
    if not typ or typ not in EVENT_TYPES:
        messagebox.showerror("Błąd", f"Wybierz jeden z: {', '.join(EVENT_TYPES)}", parent=app); return

    data = simpledialog.askstring("Zdarzenie", "Data zdarzenia (DD-MM-RRRR):", parent=app) or ""
    if not validate_date("data zdarzenia", data): return

    z_siedziby = do_siedziby = dokument = srodek = kierowca = powod = kraj = nowy_kolczyk = ""
    if typ in ("przemieszczenie","przybycie","wybycie"):
        if typ in ("przybycie","przemieszczenie"):
            z_siedziby = simpledialog.askstring("Zdarzenie", "Z siedziby (PL…):", parent=app) or ""
        if typ in ("wybycie","przemieszczenie"):
            do_siedziby = simpledialog.askstring("Zdarzenie", "Do siedziby (PL…):", parent=app) or ""
        dokument = simpledialog.askstring("Zdarzenie", "Nr dokumentu:", parent=app) or ""
        srodek   = simpledialog.askstring("Zdarzenie", "Środek transportu / nr rej.:", parent=app) or ""
        kierowca = simpledialog.askstring("Zdarzenie", "Kierowca / firma:", parent=app) or ""
        if typ == "wybycie":
            kraj = simpledialog.askstring("Zdarzenie", "Kraj docelowy (np. PL/DE) – opcjonalnie:", parent=app) or ""
        powod   = simpledialog.askstring("Zdarzenie", "Powód (np. sprzedaż):", parent=app) or ""
    elif typ == "uboj":
        powod   = simpledialog.askstring("Zdarzenie", "Rodzaj uboju (rzeźnia/gospodarczy):", parent=app) or ""
        dokument = simpledialog.askstring("Zdarzenie", "Nr dokumentu:", parent=app) or ""
    elif typ == "padniecie":
        powod = simpledialog.askstring("Zdarzenie", "Przyczyna (jeśli znana):", parent=app) or ""
    elif typ == "zmiana_oznakowania":
        nowy_kolczyk = simpledialog.askstring("Zdarzenie", "Nowy numer kolczyka:", parent=app) or ""
        dokument     = simpledialog.askstring("Zdarzenie", "Nr protokołu/zgłoszenia:", parent=app) or ""

    szczegoly = simpledialog.askstring("Zdarzenie", "Uwagi / szczegóły (opcjonalnie):", parent=app) or ""
    sz_b = encrypt_text(szczegoly) if szczegoly else None

    user_cursor.execute("""
        INSERT INTO zdarzenia (
            kolczyk, gatunek, typ, data_zdarzenia, z_siedziby, do_siedziby,
            dokument_nr, srodek_transportu, kierowca, powod, kraj_docelowy,
            nowy_kolczyk, szczegoly, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        kolczyk, gatunek, typ, data, z_siedziby, do_siedziby,
        dokument, srodek, kierowca, powod, kraj, nowy_kolczyk,
        sz_b, datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ))
    user_conn.commit()
    messagebox.showinfo("Zapisano", f"Zdarzenie zapisane ({gatunek}, {typ}).", parent=app)

def change_status():
    if not SESSION_FERNET:
        messagebox.showerror("Błąd", "Zaloguj się ponownie.", parent=app); return

    kolczyk = simpledialog.askstring("Status", "Numer kolczyka (PL…):", parent=app)
    if not kolczyk: return

    user_cursor.execute("SELECT gatunek FROM dane WHERE kolczyk=?", (kolczyk,))
    row = user_cursor.fetchone()
    if not row:
        messagebox.showerror("Błąd", "Nie znaleziono zwierzęcia o podanym numerze.", parent=app); return
    gatunek = row[0] or "?"

    dozw = ["żywe","padłe","ubój","zbyte"]
    nowy_status = simpledialog.askstring("Status", f"Nowy status {dozw}:", parent=app)
    if not nowy_status or nowy_status not in dozw:
        messagebox.showerror("Błąd", f"Wybierz jeden z: {', '.join(dozw)}", parent=app); return

    data_st = simpledialog.askstring("Status", "Data statusu (DD-MM-RRRR):", parent=app) or ""
    if not validate_date("data statusu", data_st): return

    powod = ""
    dokument = ""
    if nowy_status in ("ubój","zbyte","padłe"):
        powod = simpledialog.askstring("Status", "Powód (np. rzeźnia/sprzedaż/przyczyna zgonu):", parent=app) or ""
        if nowy_status in ("ubój","zbyte"):
            dokument = simpledialog.askstring("Status", "Nr dokumentu (WZ/faktura/inna podstawa):", parent=app) or ""

    user_cursor.execute("UPDATE dane SET status=?, data_statusu=? WHERE kolczyk=?", (nowy_status, data_st, kolczyk))
    user_conn.commit()

    user_cursor.execute("""
        INSERT INTO zdarzenia (kolczyk, gatunek, typ, data_zdarzenia, powod, dokument_nr, created_at)
        VALUES (?,?,?,?,?,?,?)
    """, (kolczyk, gatunek, "status", data_st or "", powod, dokument,
          datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    user_conn.commit()

    messagebox.showinfo("Zapisano", f"Zmieniono status ({gatunek}) na: {nowy_status}.", parent=app)

def show_events_for_animal():
    if not SESSION_FERNET:
        messagebox.showerror("Błąd", "Zaloguj się ponownie.", parent=app); return
    kolczyk = simpledialog.askstring("Historia zdarzeń", "Numer kolczyka (PL…):", parent=app)
    if not kolczyk: return
    user_cursor.execute("""
        SELECT data_zdarzenia, typ, z_siedziby, do_siedziby, dokument_nr, powod, nowy_kolczyk, szczegoly
        FROM zdarzenia
        WHERE kolczyk=?
        ORDER BY date(substr(data_zdarzenia,7,4) || '-' || substr(data_zdarzenia,4,2) || '-' || substr(data_zdarzenia,1,2)) ASC
    """, (kolczyk,))
    rows = user_cursor.fetchall()
    if not rows:
        messagebox.showinfo("Informacja", "Brak zdarzeń dla tego zwierzęcia.", parent=app); return

    out = []
    for dz, typ, z, do, dok, powd, nowy, sz in rows:
        try: sz_txt = decrypt_text(sz) if sz else ""
        except Exception: sz_txt = "<błąd>"
        out.append((dz or "—", typ or "", z or "—", do or "—", dok or "—", powd or "—", nowy or "—", sz_txt))
    headers = ["data","typ","z_siedziby","do_siedziby","dokument","powod","nowy_kolczyk","szczegoly"]
    _show_table_window(f"Historia – {kolczyk}", headers, out)

# =============================
#   EKSPORTER (bez pandas/numpy)
# =============================
class Exporter:
    def __init__(self): pass

    @staticmethod
    def _normalize_rows(rows: List[tuple]) -> List[List[str]]:
        norm = []
        for r in rows:
            norm.append([("" if v is None else str(v)) for v in r])
        return norm

    def to_csv(self, columns: List[str], rows: List[tuple], path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(columns)
            for r in self._normalize_rows(rows):
                w.writerow(r)
        return path

    def to_xlsx(self, columns: List[str], rows: List[tuple], path: str, sheet="Dane"):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        wb = Workbook()
        ws = wb.active
        ws.title = sheet
        ws.append(list(columns))
        for r in self._normalize_rows(rows):
            ws.append(r)
        # auto-szerokości
        for i, col in enumerate(columns, start=1):
            values = [str(col)]
            for r in rows:
                v = r[i-1] if i-1 < len(r) else ""
                values.append("" if v is None else str(v))
            width = min(max(len(v) for v in values) + 2, 60)
            ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = width
        wb.save(path)
        return path

    def to_json(self, columns: List[str], rows: List[tuple], path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        data = []
        for r in self._normalize_rows(rows):
            data.append({c: (r[i] if i < len(r) else "") for i, c in enumerate(columns)})
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return path

    def to_txt(self, columns: List[str], rows: List[tuple], path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        if not rows:
            Path(path).write_text("(brak danych)\n", encoding="utf-8")
            return path
        widths = []
        for i, c in enumerate(columns):
            max_val = max((len(str(r[i])) if i < len(r) and r[i] is not None else 0) for r in rows) if rows else 0
            widths.append(max(len(c), max_val))
        def fmt_row(vals): 
            return " | ".join(str(vals[i] if i < len(vals) and vals[i] is not None else "").ljust(widths[i]) 
                              for i in range(len(columns)))
        lines = [fmt_row(columns), "-+-".join("-"*w for w in widths)]
        for r in rows:
            lines.append(fmt_row(r))
        Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def to_xml(self, columns: List[str], rows: List[tuple], path: str, root="rows", row="row"):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        def esc(s): 
            s = "" if s is None else str(s)
            return (s.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
                     .replace('"',"&quot;").replace("'","&apos;"))
        out = [f"<{root}>"]
        for r in rows:
            out.append(f"  <{row}>")
            for i, c in enumerate(columns):
                v = r[i] if i < len(r) else ""
                out.append(f"    <{c}>{esc(v)}</{c}>")
            out.append(f"  </{row}>")
        out.append(f"</{root}>")
        Path(path).write_text("\n".join(out), encoding="utf-8")
        return path

    def to_pdf(self, columns: List[str], rows: List[tuple], path: str, title="Eksport danych", landscape_mode=True):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        pagesize = landscape(A4) if landscape_mode else A4
        doc = SimpleDocTemplate(path, pagesize=pagesize, leftMargin=24, rightMargin=24, topMargin=24, bottomMargin=24)
        styles = getSampleStyleSheet()
        story = [Paragraph(title, styles["Title"]), Spacer(1, 12)]
        data_tab = [list(columns)]
        for r in rows:
            data_tab.append([("" if v is None else str(v)) for v in r])
        if len(data_tab) == 1:
            story.append(Paragraph("(brak danych)", styles["Normal"]))
            doc.build(story); return path
        table = Table(data_tab, repeatRows=1)
        table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#2b8a3e")),
            ('TEXTCOLOR', (0,0), (-1,0), colors.white),
            ('ALIGN', (0,0), (-1,-1), 'LEFT'),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('FONTSIZE', (0,0), (-1,0), 10),
            ('FONTSIZE', (0,1), (-1,-1), 9),
            ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.whitesmoke, colors.HexColor("#f6f6f6")]),
            ('GRID', (0,0), (-1,-1), 0.25, colors.grey),
            ('LEFTPADDING', (0,0), (-1,-1), 6),
            ('RIGHTPADDING', (0,0), (-1,-1), 6),
            ('TOPPADDING', (0,0), (-1,-1), 3),
            ('BOTTOMPADDING', (0,0), (-1,-1), 3),
        ]))
        story.append(table)
        doc.build(story)
        return path

    def to_sqlite(self, columns: List[str], rows: List[tuple], path: str, table="export"):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        safe_cols = [c.strip().replace(" ", "_") for c in columns]
        placeholders = ",".join(["?"] * len(safe_cols))
        with sqlite3.connect(path) as conn:
            cur = conn.cursor()
            cur.execute(f'DROP TABLE IF EXISTS "{table}"')
            cols_def = ", ".join([f'"{c}" TEXT' for c in safe_cols])
            cur.execute(f'CREATE TABLE "{table}" ({cols_def})')
            for r in rows:
                vals = [("" if (i>=len(r) or r[i] is None) else str(r[i])) for i in range(len(safe_cols))]
                cur.execute(f'INSERT INTO "{table}" VALUES ({placeholders})', vals)
            conn.commit()
        return path

EXPORTER = Exporter()

# =============================
#   IMPORT / EKSPORT – FUNKCJE (bez pandas)
# =============================
def _save_dialog(default_name: str, fmt: str) -> Optional[str]:
    ext = f".{fmt}"
    types = [("Excel XLSX","*.xlsx"),("CSV","*.csv"),("JSON","*.json"),("PDF","*.pdf"),
             ("TXT","*.txt"),("XML","*.xml"),("SQLite","*.sqlite *.db"),("Wszystkie pliki", "*.*")]
    return filedialog.asksaveasfilename(
        parent=app, title="Gdzie zapisać?", defaultextension=ext,
        initialfile=default_name+ext, filetypes=types
    )

def _choose_format() -> Optional[str]:
    win = tb.Toplevel(app); win.title("Eksport – format"); win.resizable(False, False)
    fmt = tk.StringVar(value="xlsx")
    tb.Label(win, text="Wybierz format eksportu:").pack(padx=12, pady=10)
    combo = tb.Combobox(win, values=["csv","xlsx","json","pdf","txt","xml","sqlite"], textvariable=fmt, state="readonly", width=18)
    combo.pack(padx=12, pady=6)
    ok = {'go': False}
    def ok_(): ok.update(go=True); win.destroy()
    tb.Button(win, text="OK", bootstyle="success", command=ok_).pack(padx=12, pady=10)
    win.grab_set(); win.wait_window()
    return fmt.get() if ok['go'] else None

def _export_rows_cols(columns: List[str], rows: List[tuple], path: str, fmt: str, title: str):
    try:
        if fmt == "csv":      EXPORTER.to_csv(columns, rows, path)
        elif fmt == "xlsx":   EXPORTER.to_xlsx(columns, rows, path, sheet=title[:28] or "Dane")
        elif fmt == "json":   EXPORTER.to_json(columns, rows, path)
        elif fmt == "pdf":    EXPORTER.to_pdf(columns, rows, path, title=title)
        elif fmt == "txt":    EXPORTER.to_txt(columns, rows, path)
        elif fmt == "xml":    EXPORTER.to_xml(columns, rows, path)
        elif fmt == "sqlite": EXPORTER.to_sqlite(columns, rows, path, table=title.replace(" ","_").lower())
        else: raise ValueError(f"Nieznany format: {fmt}")
        messagebox.showinfo("Eksport", f"Zapisano: {path}", parent=app)
    except Exception as e:
        messagebox.showerror("Eksport", f"Nie udało się zapisać.\n\n{e}", parent=app)

def export_dane():
    if not SESSION_FERNET:
        messagebox.showerror("Błąd", "Zaloguj się ponownie.", parent=app); return
    fmt = _choose_format()
    if not fmt: return
    name = f"dane_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"
    path = _save_dialog(name, fmt)
    if not path: return

    user_cursor.execute("""
        SELECT kolczyk,gatunek,data_urodzenia,data_oznakowania,plec,rasa_kod,matka_kolczyk,
               siedziba_stada,pochodzenie,data_przybycia,status,data_statusu,imie,uwagi
        FROM dane
    """)
    rows_db = user_cursor.fetchall()
    rows: List[tuple] = []
    for r in rows_db:
        imie_b, uwagi_b = r[12], r[13]
        try: imie = decrypt_text(imie_b) if imie_b else ""
        except Exception: imie = "<błąd>"
        try: uwagi = decrypt_text(uwagi_b) if uwagi_b else ""
        except Exception: uwagi = "<błąd>"
        rows.append(tuple(list(r[:12]) + [imie, uwagi]))

    cols = ["kolczyk","gatunek","data_urodzenia","data_oznakowania","plec","rasa_kod","matka_kolczyk",
            "siedziba_stada","pochodzenie","data_przybycia","status","data_statusu","imie","uwagi"]
    _export_rows_cols(cols, rows, path, fmt, title="Karty zwierząt")

def export_zdarzenia():
    if not SESSION_FERNET:
        messagebox.showerror("Błąd", "Zaloguj się ponownie.", parent=app); return
    fmt = _choose_format()
    if not fmt: return
    name = f"zdarzenia_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"
    path = _save_dialog(name, fmt)
    if not path: return

    user_cursor.execute("""
        SELECT kolczyk,gatunek,typ,data_zdarzenia,z_siedziby,do_siedziby,dokument_nr,
               srodek_transportu,kierowca,powod,kraj_docelowy,nowy_kolczyk,szczegoly,created_at
        FROM zdarzenia
        ORDER BY date(substr(data_zdarzenia,7,4) || '-' || substr(data_zdarzenia,4,2) || '-' || substr(data_zdarzenia,1,2)) ASC
    """)
    rows_db = user_cursor.fetchall()
    rows: List[tuple] = []
    for row in rows_db:
        sz = row[12]
        try: sz_txt = decrypt_text(sz) if sz else ""
        except Exception: sz_txt = "<błąd>"
        rows.append(tuple(list(row[:12]) + [sz_txt, row[13]]))

    cols = ["kolczyk","gatunek","typ","data_zdarzenia","z_siedziby","do_siedziby","dokument_nr",
            "srodek_transportu","kierowca","powod","kraj_docelowy","nowy_kolczyk","szczegoly","created_at"]
    _export_rows_cols(cols, rows, path, fmt, title="Rejestr zdarzeń")

def export_events_csv():  # kompatybilność ze starą opcją
    if not SESSION_FERNET:
        messagebox.showerror("Błąd", "Zaloguj się ponownie.", parent=app); return
    path = filedialog.asksaveasfilename(
        parent=app, title="Zapisz zdarzenia do CSV", defaultextension=".csv", filetypes=[("CSV","*.csv")]
    )
    if not path: return
    user_cursor.execute("""
        SELECT kolczyk,gatunek,typ,data_zdarzenia,z_siedziby,do_siedziby,dokument_nr,
               srodek_transportu,kierowca,powod,kraj_docelowy,nowy_kolczyk,szczegoly,created_at
        FROM zdarzenia
        ORDER BY date(substr(data_zdarzenia,7,4) || '-' || substr(data_zdarzenia,4,2) || '-' || substr(data_zdarzenia,1,2)) ASC
    """)
    rows = user_cursor.fetchall()
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=';')
        w.writerow(["kolczyk","gatunek","typ","data_zdarzenia","z_siedziby","do_siedziby","dokument_nr",
                    "srodek_transportu","kierowca","powod","kraj_docelowy","nowy_kolczyk","szczegoly(decrypted)","created_at"])
        for row in rows:
            sz = row[12]
            try: sz_txt = decrypt_text(sz) if sz else ""
            except Exception: sz_txt = ""
            w.writerow(list(row[:12]) + [sz_txt, row[13]])
    messagebox.showinfo("Eksport", f"Zapisano: {path}", parent=app)

# =============================
#   IMPORT (bez pandas)
# =============================
def _read_csv_auto(path: str) -> List[dict]:
    """Czyta CSV z autodetekcją separatora; zwraca listę słowników."""
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        sample = f.read(4096)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=";,|\t,")
        except Exception:
            class _D: delimiter = ';'
            dialect = _D()
        reader = csv.DictReader(f, delimiter=dialect.delimiter)
        rows = [ {k.strip(): (v if v is not None else "") for k,v in row.items()} for row in reader ]
    return rows

def import_dane_csv():
    if not SESSION_FERNET:
        messagebox.showerror("Błąd","Zaloguj się ponownie.", parent=app); return
    path = filedialog.askopenfilename(parent=app, title="Wybierz CSV z kartami", filetypes=[("CSV","*.csv"),("Wszystkie pliki","*.*")])
    if not path: return
    try:
        rows = _read_csv_auto(path)
        cols = ["kolczyk","gatunek","data_urodzenia","data_oznakowania","plec","rasa_kod","matka_kolczyk",
                "siedziba_stada","pochodzenie","data_przybycia","status","data_statusu","imie","uwagi"]
        count = 0
        for r in rows:
            rec = {c: r.get(c,"").strip() for c in cols}
            imie_b = encrypt_text(rec["imie"]) if rec["imie"] else None
            uwagi_b= encrypt_text(rec["uwagi"]) if rec["uwagi"] else None
            user_cursor.execute("""
                INSERT OR REPLACE INTO dane
                (kolczyk,gatunek,data_urodzenia,data_oznakowania,plec,rasa_kod,matka_kolczyk,
                 siedziba_stada,pochodzenie,data_przybycia,status,data_statusu,imie,uwagi)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                rec["kolczyk"], rec["gatunek"], rec["data_urodzenia"], rec["data_oznakowania"],
                rec["plec"], rec["rasa_kod"], rec["matka_kolczyk"], rec["siedziba_stada"],
                rec["pochodzenie"], rec["data_przybycia"], rec["status"], rec["data_statusu"],
                imie_b, uwagi_b
            ))
            count += 1
        user_conn.commit()
        messagebox.showinfo("Import", f"Zaimportowano {count} wierszy z {os.path.basename(path)}.", parent=app)
    except Exception as e:
        messagebox.showerror("Import", f"Nie udało się wczytać.\n\n{e}", parent=app)

def import_zdarzenia_csv():
    if not SESSION_FERNET:
        messagebox.showerror("Błąd","Zaloguj się ponownie.", parent=app); return
    path = filedialog.askopenfilename(parent=app, title="Wybierz CSV ze zdarzeniami", filetypes=[("CSV","*.csv"),("Wszystkie pliki","*.*")])
    if not path: return
    try:
        rows = _read_csv_auto(path)
        cols = ["kolczyk","gatunek","typ","data_zdarzenia","z_siedziby","do_siedziby","dokument_nr",
                "srodek_transportu","kierowca","powod","kraj_docelowy","nowy_kolczyk","szczegoly","created_at"]
        count = 0
        for r in rows:
            rec = {c: r.get(c,"").strip() for c in cols}
            sz_b = encrypt_text(rec["szczegoly"]) if rec["szczegoly"] else None
            created = rec["created_at"] or datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            user_cursor.execute("""
                INSERT INTO zdarzenia (
                    kolczyk,gatunek,typ,data_zdarzenia,z_siedziby,do_siedziby,dokument_nr,
                    srodek_transportu,kierowca,powod,kraj_docelowy,nowy_kolczyk,szczegoly,created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                rec["kolczyk"], rec["gatunek"], rec["typ"], rec["data_zdarzenia"],
                rec["z_siedziby"], rec["do_siedziby"], rec["dokument_nr"],
                rec["srodek_transportu"], rec["kierowca"], rec["powod"],
                rec["kraj_docelowy"], rec["nowy_kolczyk"], sz_b, created
            ))
            count += 1
        user_conn.commit()
        messagebox.showinfo("Import", f"Zaimportowano {count} zdarzeń z {os.path.basename(path)}.", parent=app)
    except Exception as e:
        messagebox.showerror("Import", f"Nie udało się wczytać.\n\n{e}", parent=app)

# =============================
#   UPDATER (RAW GitHub via Updater.exe)
# =============================
def update_app():
    try:
        RAW_URL = "https://raw.githubusercontent.com/Feniks833/Irzplusplus/main/ARMIR.exe"
        if getattr(sys, 'frozen', False):
            target_exe = sys.executable
        else:
            app_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
            target_exe = os.path.join(app_dir, "ARMIR.exe")
        src_updater = resource_path("Updater.exe")
        if not os.path.exists(src_updater):
            raise FileNotFoundError("Brak Updater.exe w zasobach (dodaj --add-data).")
        tmp_dir = tempfile.gettempdir()
        dst_updater = os.path.join(tmp_dir, f"Updater_{os.getpid()}.exe")
        shutil.copyfile(src_updater, dst_updater)
        subprocess.Popen([dst_updater, "--url", RAW_URL, "--target", target_exe], close_fds=True)
        messagebox.showinfo("Aktualizacja", "Trwa aktualizacja. Aplikacja uruchomi się ponownie za chwilę.", parent=app)
        app.quit(); sys.exit(0)
    except Exception as e:
        messagebox.showerror("Błąd aktualizacji", f"Nie udało się zaktualizować:\n{e}", parent=app)

# =============================
#   UI – okno główne (ttkbootstrap)
# =============================
app = tb.Window(themename="darkly")
app.title(APP_NAME)
app.geometry("1200x780")

# Przełącznik motywu
def toggle_theme():
    try:
        current = app.style.theme.name
        app.style.theme_use("flatly" if current == "darkly" else "darkly")
    except Exception:
        pass

# TŁO: BACKGROUND.JPG (opcjonalne)
bg_label = None
try:
    bg_image = Image.open(resource_path("BACKGROUND.JPG"))
    app.bg_tk = ImageTk.PhotoImage(bg_image)
    bg_label = tk.Label(app, image=app.bg_tk, bd=0)
    bg_label.place(relx=0, rely=0, relwidth=1, relheight=1)
except Exception:
    app.configure(bg="#1f1f1f")

# ======== MENU GÓRNE ========
menubar = Menu(app)
m_plk = Menu(menubar, tearoff=0)
m_plk.add_command(label="Import kart (CSV)…", command=import_dane_csv)
m_plk.add_command(label="Import zdarzeń (CSV)…", command=import_zdarzenia_csv)
m_plk.add_separator()
m_plk.add_command(label="Eksport kart…", command=export_dane)
m_plk.add_command(label="Eksport zdarzeń…", command=export_zdarzenia)
m_plk.add_separator()
m_plk.add_command(label="Wyjście", command=app.quit)
menubar.add_cascade(label="Plik", menu=m_plk)

m_ed = Menu(menubar, tearoff=0)
m_ed.add_command(label="Dodaj/edytuj kartę (szybko)", command=add_record)
m_ed.add_command(label="Formularz karty (ładny)", command=add_record_form)
m_ed.add_command(label="Zdarzenie", command=register_event)
m_ed.add_command(label="Zmień status", command=change_status)
menubar.add_cascade(label="Edycja", menu=m_ed)

m_widok = Menu(menubar, tearoff=0)
m_widok.add_command(label="Kartoteka (kolczyk)", command=lambda: show_data(order_by='kolczyk'))
m_widok.add_command(label="Historia zdarzeń", command=show_events_for_animal)
menubar.add_cascade(label="Widok", menu=m_widok)

m_pomoc = Menu(menubar, tearoff=0)
m_pomoc.add_command(label="Aktualizuj", command=update_app)
m_pomoc.add_command(label="O programie", command=lambda: messagebox.showinfo("IRZ", "IRZ – Menedżer zwierząt (FENIX)\nDarmowe biblioteki MIT/BSD."))
menubar.add_cascade(label="Pomoc", menu=m_pomoc)

app.config(menu=menubar)

# ======== TOOLBAR / SZYBKIE AKCJE ========
topbar = tb.Frame(app, padding=8, style="Card.TFrame")
topbar.pack(side=TOP, fill=X)

tb.Button(topbar, text="Dodaj kartę", bootstyle="success", command=add_record).pack(side=LEFT, padx=4)
tb.Button(topbar, text="Formularz karty", bootstyle="success", command=add_record_form).pack(side=LEFT, padx=4)
tb.Button(topbar, text="Zdarzenie", bootstyle="info", command=register_event).pack(side=LEFT, padx=4)
tb.Button(topbar, text="Status", bootstyle="warning", command=change_status).pack(side=LEFT, padx=4)
tb.Separator(topbar, orient=VERTICAL).pack(side=LEFT, fill=Y, padx=10)
tb.Button(topbar, text="Kartoteka", bootstyle="secondary", command=lambda: show_data(order_by='kolczyk')).pack(side=LEFT, padx=4)
tb.Button(topbar, text="Historia", bootstyle="secondary", command=show_events_for_animal).pack(side=LEFT, padx=4)
tb.Separator(topbar, orient=VERTICAL).pack(side=LEFT, fill=Y, padx=10)
tb.Button(topbar, text="Import karty CSV", bootstyle="secondary", command=import_dane_csv).pack(side=LEFT, padx=4)
tb.Button(topbar, text="Import zdarzeń CSV", bootstyle="secondary", command=import_zdarzenia_csv).pack(side=LEFT, padx=4)
tb.Button(topbar, text="Eksport kart…", bootstyle="info", command=export_dane).pack(side=LEFT, padx=4)
tb.Button(topbar, text="Eksport zdarzeń…", bootstyle="info", command=export_zdarzenia).pack(side=LEFT, padx=4)
tb.Separator(topbar, orient=VERTICAL).pack(side=LEFT, fill=Y, padx=10)
tb.Button(topbar, text="☾/☀ Motyw", bootstyle="secondary", command=toggle_theme).pack(side=LEFT, padx=4)

# ======== PANEL LOGOWANIA ========
login_card = tb.Frame(app, padding=24, style="Card.TFrame")
login_card.place(relx=0.5, rely=0.35, anchor="center")
tb.Label(login_card, text="IRZ – logowanie", font="-size 16 -weight bold").grid(row=0, column=0, columnspan=2, pady=(0,12))
tb.Button(login_card, text="Zarejestruj", width=18, bootstyle="success", command=register_user).grid(row=1, column=0, padx=8, pady=6)
tb.Button(login_card, text="Zaloguj", width=18, bootstyle="primary",
          command=lambda: show_menu() if login_user() else None).grid(row=1, column=1, padx=8, pady=6)

# ======== EKRAN MENU PO ZALOGOWANIU ========
def show_menu():
    # usuń wszystkie poza topbarem i (ew.) tłem
    for w in app.winfo_children():
        if w in (topbar, bg_label):
            continue
        try: w.destroy()
        except Exception: pass

    app.title(f"{APP_NAME} – {CURRENT_USER}")

    # kafle akcji
    grid = tb.Frame(app, padding=18, style="Card.TFrame")
    grid.place(relx=0.5, rely=0.60, anchor="center")

    actions = [
        ("Dodaj/edytuj kartę (szybko)", add_record, "success"),
        ("Formularz karty (ładny)", add_record_form, "success"),
        ("Pokaż kartotekę", lambda: show_data(order_by='kolczyk'), "secondary"),
        ("Filtr: gatunek", lambda: show_data(
            where_clause='gatunek=?',
            params=(simpledialog.askstring('Gatunek','np. koza/owca/bydło:',parent=app) or "",)
        ), "secondary"),
        ("Filtr: rasa", lambda: show_data(
            where_clause='rasa_kod=?',
            params=(simpledialog.askstring('Rasa','Kod rasy:',parent=app) or "",)
        ), "secondary"),
        ("Szukaj: imię", search_by_name, "info"),
        ("Szukaj: ostatnie 3 cyfry", search_by_last3, "info"),
        ("Zdarzenie: zwierzę", register_event, "warning"),
        ("Status: zwierzę", change_status, "warning"),
        ("Historia zdarzeń", show_events_for_animal, "secondary"),
        ("Eksport zdarzeń CSV", export_events_csv, "info"),
        ("Eksport kart…", export_dane, "info"),
        ("Eksport zdarzeń…", export_zdarzenia, "info"),
        ("Import kart CSV…", import_dane_csv, "secondary"),
        ("Import zdarzeń CSV…", import_zdarzenia_csv, "secondary"),
        ("Aktualizuj", update_app, "danger"),
    ]

    for i, (label, fn, style) in enumerate(actions):
        tb.Button(grid, text=label, width=28, bootstyle=style, command=fn)\
          .grid(row=i//2, column=i%2, padx=10, pady=10)

    status = tb.Label(app, text="Gotowe.", anchor="w")
    status.pack(side=BOTTOM, fill=X, padx=8, pady=6)

# Skróty
app.bind_all("<Control-e>", lambda e: export_dane())
app.bind_all("<Control-h>", lambda e: show_events_for_animal())
app.bind_all("<Control-f>", lambda e: search_by_name())

# =============================
#   MAINLOOP / SPRZĄTANIE
# =============================
try:
    app.mainloop()
finally:
    if user_conn:
        try: user_conn.close()
        except Exception: pass
    try: auth_conn.close()
    except Exception: pass
    logging.info("Aplikacja zakończona")

