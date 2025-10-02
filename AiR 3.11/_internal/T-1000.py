# main.py
# -*- coding: utf-8 -*-
"""
IRZ – Menedżer zwierząt (v2.5, 'AIR/IRZ++')
- Szyfrowanie: envelope encryption (per-user data_key)
- Reset hasła: zmiana z hasłem + odzyskiwanie przez pytanie kontrolne
- Pandas/Numpy: raporty, import/eksport XLSX
- Więcej eksportów: HTML, Markdown, Parquet, Feather
- Wysuwane menu (drawer) + Szybki eksport
- PRAGMA + indeksy
- Uładniony UI, comboboxy, walidacje
"""
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
from typing import Optional, Tuple, List, Sequence, Dict, Callable

# GUI
import tkinter as tk
from tkinter import simpledialog, messagebox, filedialog
from tkinter import Menu
import ttkbootstrap as tb
from ttkbootstrap.constants import *
from PIL import Image, ImageTk

# Crypto
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

# Data / eksport
from pathlib import Path

# Data science
import numpy as np



# =============================
#   APP / ŚCIEŻKI / LOGI
# =============================
APP_NAME = "IRZ – Menedżer zwierząt"
APPDIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "KozyManager")
os.makedirs(APPDIR, exist_ok=True)


SALT_FILE    = os.path.join(APPDIR, "salt.key")
AUTH_DB_FILE = os.path.join(APPDIR, "users.db")    # baza autoryzacyjna
USER_DB_MASK = os.path.join(APPDIR, "Kozy_{}.db")  # baza danych per użytkownik
CACHE_FILE = os.path.join(APPDIR, "cache_meta.json")
CACHE_TTL_DAYS = 3650
APPDIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "KozyManager")
SESSION_FILE = os.path.join(APPDIR, "session_token.json")

CURRENT_USER = None
SESSION_FERNET = None
# --- SESSION TOKEN (auto-login) ---
def save_session_token(username: str, data_key: bytes) -> None:
    """Zapisz token: nazwa użytkownika + klucz danych (base64)."""
    try:
        os.makedirs(APPDIR, exist_ok=True)
        tok = {
            "username": username,
            "data_key": base64.urlsafe_b64encode(data_key).decode("ascii"),
            "created": datetime.datetime.now().isoformat()
        }
        with open(SESSION_FILE, "w", encoding="utf-8") as f:
            json.dump(tok, f)
        logging.info("Zapisano token sesji.")
    except Exception as e:
        logging.error(f"Nie udało się zapisać tokenu: {e}")

def clear_session_token() -> None:
    """Usuń zapisany token."""
    try:
        if os.path.exists(SESSION_FILE):
            os.remove(SESSION_FILE)
            logging.info("Usunięto token sesji.")
    except Exception as e:
        logging.error(f"Nie udało się usunąć tokenu: {e}")

def try_auto_login() -> bool:
    """Jeśli jest token, ustaw CURRENT_USER/SESSION_FERNET i DB."""
    global CURRENT_USER, SESSION_FERNET
    if not os.path.exists(SESSION_FILE):
        return False
    try:
        with open(SESSION_FILE, "r", encoding="utf-8") as f:
            tok = json.load(f)
        username = tok.get("username")
        dk_b64   = tok.get("data_key")
        if not username or not dk_b64:
            return False
        dk = base64.urlsafe_b64decode(dk_b64.encode("ascii"))
        SESSION_FERNET = Fernet(dk)
        CURRENT_USER   = username
        init_user_db(username)
        logging.info(f"Auto-login przez token ({username})")
        return True
    except Exception as e:
        logging.error(f"Auto-login nieudany: {e}")
        return False



logging.basicConfig(
    filename=os.path.join(APPDIR, "app.log"),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logging.info("Aplikacja startuje T-1000")
def generate_cache(current_version="T-1000"):
    try:
        import pandas as pd
        import numpy as np
        from reportlab.lib.pagesizes import A4

        # tutaj możesz zrobić rzeczy ciężkie, np. testowe dataframe/czcionki
        _ = pd.DataFrame({"x":[1,2,3]}).to_dict()

        meta = {
            "created": datetime.datetime.now().isoformat(),
            "version": current_version
        }
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)
        logging.info("Cache wygenerowany.")
    except Exception as e:
        logging.error(f"Nie udało się wygenerować cache: {e}")

# =============================
#   ZASOBY (PyInstaller)
# =============================
def resource_path(rel_path: str) -> str:
    base_path = getattr(sys, "_MEIPASS", os.path.abspath("."))
    return os.path.join(base_path, rel_path)
def is_cache_valid(current_version="v2.5") -> bool:
    try:
        if not os.path.exists(CACHE_FILE):
            return False
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            meta = json.load(f)

        # Sprawdź TTL
        created = datetime.datetime.fromisoformat(meta.get("created"))
        if (datetime.datetime.now() - created).days > CACHE_TTL_DAYS:
            return False

        # Sprawdź wersję
        if meta.get("version") != current_version:
            return False

        return True
    except Exception:
        return False


# =============================
#   KDF / HASH / KLUCZE
# =============================
KDF_ITERATIONS = 100_000
ADMIN_CODE = "170487130711"

# Wersja klucza admina (na wypadek przyszłej rotacji)
ADMIN_KEY_LABEL = "v1"

def derive_admin_fernet_key(admin_code: str, salt: bytes) -> bytes:
    """
    Klucz admina wyprowadzany z (ADMIN_CODE + salt) PBKDF2 → base64 do Fernet.
    Sól wiążemy lokalnie z instancją (salt.key), więc nawet znając kod,
    ktoś bez pliku salt.key nie odtworzy klucza.
    """
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=b"ADM|" + salt, iterations=KDF_ITERATIONS)
    raw = kdf.derive(("ADM|"+ADMIN_KEY_LABEL+"|"+admin_code).encode("utf-8"))
    return base64.urlsafe_b64encode(raw)
if not os.path.exists(SALT_FILE):
    with open(SALT_FILE, "wb") as f:
        f.write(os.urandom(16))
        logging.info("Wygenerowano nowy salt w salt.key")
with open(SALT_FILE, "rb") as f:
    salt = f.read()

def _kdf_bytes(password: str, length=32) -> bytes:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=length, salt=salt, iterations=KDF_ITERATIONS)
    return kdf.derive(password.encode())

def derive_fernet_key_from_password(password: str) -> bytes:
    return base64.urlsafe_b64encode(_kdf_bytes(password, 32))

def derive_fernet_key_from_answer(answer: str) -> bytes:
    return base64.urlsafe_b64encode(_kdf_bytes("ANS|" + answer, 32))

def hash_password(password: str) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, KDF_ITERATIONS)

def hash_answer(answer: str) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", ("ANS|" + answer).encode(), salt, KDF_ITERATIONS)

def gen_data_key() -> bytes:
    return base64.urlsafe_b64encode(os.urandom(32))

# =============================
#   GLOBALNE SESYJNE
# =============================
SESSION_FERNET: Optional[Fernet] = None  # klucz danych (data_key) w sesji
CURRENT_USER: str = ""
user_conn: Optional[sqlite3.Connection] = None
user_cursor: Optional[sqlite3.Cursor]   = None


# =============================
#   BAZA AUTH + SCHEMA UPGRADE
# =============================

auth_conn   = sqlite3.connect(AUTH_DB_FILE)
auth_cursor = auth_conn.cursor()
auth_cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        username TEXT PRIMARY KEY,
        password_hash BLOB,
        sec_q TEXT,
        sec_ans_hash BLOB,
        data_key_enc_by_pwd BLOB,
        data_key_enc_by_sec BLOB,
        created_at TEXT,
        updated_at TEXT
    )
""")
auth_conn.commit()

def ensure_auth_columns():
    auth_cursor.execute("PRAGMA table_info(users)")
    cols = {r[1] for r in auth_cursor.fetchall()}
    wanted = [
        ("sec_q","TEXT"), ("sec_ans_hash","BLOB"),
        ("data_key_enc_by_pwd","BLOB"), ("data_key_enc_by_sec","BLOB"),
        ("data_key_enc_by_admin","BLOB"), ("admin_key_label","TEXT"),
        ("created_at","TEXT"), ("updated_at","TEXT")
    ]
    for name, ctype in wanted:
        if name not in cols:
            auth_cursor.execute(f"ALTER TABLE users ADD COLUMN {name} {ctype}")
    auth_conn.commit()

ensure_auth_columns()

# =============================
#   BAZA UŻYTKOWNIKA
# =============================
def ensure_tables(cur: sqlite3.Cursor, conn: sqlite3.Connection):
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

    # INDEKSY
    cur.execute("CREATE INDEX IF NOT EXISTS idx_dane_kolczyk ON dane(kolczyk)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_zdarzenia_kolczyk ON zdarzenia(kolczyk)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_zdarzenia_data ON zdarzenia(data_zdarzenia)")
    conn.commit()

def init_user_db(username: str):
    global user_conn, user_cursor
    if user_conn:
        user_conn.close()
    dbfile = USER_DB_MASK.format(username)
    user_conn   = sqlite3.connect(dbfile)
    user_cursor = user_conn.cursor()
    user_cursor.execute("PRAGMA journal_mode=WAL;")
    user_cursor.execute("PRAGMA synchronous=NORMAL;")
    user_cursor.execute("PRAGMA foreign_keys=ON;")
    user_conn.commit()
    ensure_tables(user_cursor, user_conn)
    logging.info(f"DB OK: {dbfile}")

# =============================
#   ENCRYPT / DECRYPT HELPERS
# =============================
def encrypt_text(text: str) -> bytes:
    assert SESSION_FERNET, "Brak klucza sesji!"
    return SESSION_FERNET.encrypt(text.encode())

def decrypt_text(token: Optional[bytes]) -> str:
    if not token:
        return ""
    assert SESSION_FERNET, "Brak klucza sesji!"
    try:
        return SESSION_FERNET.decrypt(token).decode()
    except InvalidToken:
        return "<nieczytelne>"

def _load_data_key_via_password(username: str, password: str) -> Optional[bytes]:
    auth_cursor.execute("SELECT data_key_enc_by_pwd FROM users WHERE username=?", (username,))
    row = auth_cursor.fetchone()
    if not row or not row[0]:
        return None
    enc = row[0]
    key = derive_fernet_key_from_password(password)
    try:
        return Fernet(key).decrypt(enc)
    except InvalidToken:
        return None

def _load_data_key_via_answer(username: str, answer: str) -> Optional[bytes]:
    auth_cursor.execute("SELECT data_key_enc_by_sec FROM users WHERE username=?", (username,))
    row = auth_cursor.fetchone()
    if not row or not row[0]:
        return None
    enc = row[0]
    key = derive_fernet_key_from_answer(answer)
    try:
        return Fernet(key).decrypt(enc)
    except InvalidToken:
        return None
def _load_data_key_via_admin(username: str, admin_code: str) -> Optional[bytes]:
    auth_cursor.execute("SELECT data_key_enc_by_admin, admin_key_label FROM users WHERE username=?", (username,))
    row = auth_cursor.fetchone()
    if not row:
        return None
    enc, lab = row[0], row[1]
    if not enc:
        return None
    if lab and lab != ADMIN_KEY_LABEL:
        # Miejsce na ewentualną migrację wersji klucza admina
        pass
    try:
        key = derive_admin_fernet_key(admin_code, salt)
        return Fernet(key).decrypt(enc)
    except InvalidToken:
        return None


def _store_keys(username: str, data_key: bytes, password: Optional[str], answer: Optional[str], admin_enable: bool=True):
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    enc_pwd = None
    enc_sec = None
    enc_adm = None
    if password:
        enc_pwd = Fernet(derive_fernet_key_from_password(password)).encrypt(data_key)
    if answer:
        enc_sec = Fernet(derive_fernet_key_from_answer(answer)).encrypt(data_key)
    if admin_enable and ADMIN_CODE:
        enc_adm = Fernet(derive_admin_fernet_key(ADMIN_CODE, salt)).encrypt(data_key)
    auth_cursor.execute("""
        UPDATE users SET
            data_key_enc_by_pwd = COALESCE(?, data_key_enc_by_pwd),
            data_key_enc_by_sec = COALESCE(?, data_key_enc_by_sec),
            data_key_enc_by_admin = COALESCE(?, data_key_enc_by_admin),
            admin_key_label = COALESCE(?, admin_key_label),
            updated_at = ?
        WHERE username = ?
    """, (enc_pwd, enc_sec, enc_adm, (ADMIN_KEY_LABEL if enc_adm else None), now, username))
    auth_conn.commit()


# =============================
#   REJESTRACJA / LOGOWANIE / RESET
# =============================
def register_user():
    username = simpledialog.askstring("Rejestracja", "Nazwa użytkownika:", parent=app)
    if not username:
        return
    password = simpledialog.askstring("Rejestracja", "Hasło:", show="*", parent=app)
    if not password:
        return
    confirm = simpledialog.askstring("Rejestracja", "Powtórz hasło:", show="*", parent=app)
    if password != confirm:
        messagebox.showerror("Błąd", "Hasła nie są identyczne.", parent=app); return

    sec_q = simpledialog.askstring("Rejestracja", "Pytanie kontrolne (do odzyskiwania hasła):", parent=app)
    sec_a = simpledialog.askstring("Rejestracja", "Odpowiedź:", show="*", parent=app)
    if not sec_q or not sec_a:
        messagebox.showerror("Błąd", "Pytanie i odpowiedź są wymagane.", parent=app); return

    auth_cursor.execute("SELECT 1 FROM users WHERE username=?", (username,))
    if auth_cursor.fetchone():
        messagebox.showerror("Błąd", "Użytkownik już istnieje.", parent=app); return

    data_key = gen_data_key()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    auth_cursor.execute("""
        INSERT INTO users(username, password_hash, sec_q, sec_ans_hash, data_key_enc_by_pwd, data_key_enc_by_sec, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?)
    """, (
        username,
        hash_password(password),
        sec_q,
        hash_answer(sec_a),
        Fernet(derive_fernet_key_from_password(password)).encrypt(data_key),
        Fernet(derive_fernet_key_from_answer(sec_a)).encrypt(data_key),
        now, now
    ))
    auth_conn.commit()

    # DOPISZ kopię admina (trzeci sposób odzyskania dk)
    _store_keys(username, data_key, password=None, answer=None, admin_enable=True)

    messagebox.showinfo("Sukces", "Rejestracja zakończona.", parent=app)


def login_user() -> bool:
    global SESSION_FERNET, CURRENT_USER
    username = simpledialog.askstring("Logowanie", "Nazwa użytkownika:", parent=app)
    if not username: return False
    password = simpledialog.askstring("Logowanie", "Hasło:", show="*", parent=app)
    if not password: return False

    # --- TRYB ADMINA: wpisano sekret jako hasło ---
    if password == ADMIN_CODE:
        dk = _load_data_key_via_admin(username, ADMIN_CODE)
        if dk is None:
            messagebox.showerror("Admin", "Brak kopii klucza admina dla tego konta.\n"
                                         "Zaloguj się zwyczajnie raz (albo ustaw pytanie/zmień hasło), by dopisać kopię admina.", parent=app)
            return False
        SESSION_FERNET = Fernet(dk)
        CURRENT_USER   = username
        init_user_db(username)
        messagebox.showinfo("Admin", f"Tryb administratora: pełny dostęp do {username}.", parent=app)
        return True

    # --- TOR STANDARDOWY ---
    auth_cursor.execute("SELECT password_hash FROM users WHERE username=?", (username,))
    row = auth_cursor.fetchone()
    if not row:
        messagebox.showerror("Błąd", "Nie ma takiego użytkownika.", parent=app); return False
    if not hmac.compare_digest(row[0], hash_password(password)):
        messagebox.showerror("Błąd", "Nieprawidłowe hasło.", parent=app); return False

    dk = _load_data_key_via_password(username, password)
    if dk is None:
        # legacy: klucz = hasło
        SESSION_FERNET = Fernet(derive_fernet_key_from_password(password))
        save_session_token(username, derive_fernet_key_from_password(password))
        messagebox.showinfo(
            "Migracja szyfrowania",
            "Wykryto stary format szyfrowania (klucz = hasło).\n"
            "Zalecam migrację do 'envelope encryption' (Ustawienia → Migracja szyfrowania).",
            parent=app
        )
    else:
        SESSION_FERNET = Fernet(dk)
        save_session_token(username, dk)

        # Po udanym logowaniu dopisz kopię admina, jeśli brak
        try:
            auth_cursor.execute("SELECT data_key_enc_by_admin FROM users WHERE username=?", (username,))
            r = auth_cursor.fetchone()
            if r and not r[0] and ADMIN_CODE:
                _store_keys(username, dk, password=None, answer=None, admin_enable=True)
        except Exception:
            pass

    CURRENT_USER   = username
    init_user_db(username)
    save_session_token(username, dk)

    return True


def change_password():
    username = simpledialog.askstring("Zmiana hasła", "Nazwa użytkownika:", parent=app)
    if not username: return
    old = simpledialog.askstring("Zmiana hasła", "Stare hasło:", show="*", parent=app)
    if not old: return
    new = simpledialog.askstring("Zmiana hasła", "Nowe hasło:", show="*", parent=app)
    if not new: return
    confirm = simpledialog.askstring("Zmiana hasła", "Powtórz nowe hasło:", show="*", parent=app)
    if new != confirm:
        messagebox.showerror("Błąd", "Nowe hasła się różnią.", parent=app); return

    auth_cursor.execute("SELECT password_hash FROM users WHERE username=?", (username,))
    row = auth_cursor.fetchone()
    if not row or not hmac.compare_digest(row[0], hash_password(old)):
        messagebox.showerror("Błąd", "Nieprawidłowe stare hasło.", parent=app); return

    dk = _load_data_key_via_password(username, old)
    if dk is None:
        if not migrate_encryption_for_username(username, old):
            return
        dk = _load_data_key_via_password(username, old)
        if dk is None:
            messagebox.showerror("Błąd", "Migracja nie powiodła się.", parent=app); return

    auth_cursor.execute("UPDATE users SET password_hash=?, updated_at=? WHERE username=?",
                        (hash_password(new), datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), username))
    auth_conn.commit()

    _store_keys(username, dk, password=new, answer=None, admin_enable=True)
    messagebox.showinfo("Sukces", "Hasło zmienione.", parent=app)


def recover_password():
    username = simpledialog.askstring("Odzyskiwanie hasła", "Nazwa użytkownika:", parent=app)
    if not username: return
    auth_cursor.execute("SELECT sec_q, sec_ans_hash FROM users WHERE username=?", (username,))
    row = auth_cursor.fetchone()
    if not row or not row[0]:
        messagebox.showerror("Błąd", "Brak pytania kontrolnego dla użytkownika.", parent=app); return
    question, ans_hash = row[0], row[1]
    answer = simpledialog.askstring("Odzyskiwanie hasła", question, show="*", parent=app)
    if not answer:
        return
    if not hmac.compare_digest(ans_hash, hash_answer(answer)):
        messagebox.showerror("Błąd", "Niepoprawna odpowiedź.", parent=app); return

    dk = _load_data_key_via_answer(username, answer)
    if dk is None:
        messagebox.showerror("Błąd", "Brak możliwości odzyskania (brak zapisanego klucza).", parent=app); return

    new = simpledialog.askstring("Odzyskiwanie hasła", "Nowe hasło:", show="*", parent=app)
    if not new: return
    confirm = simpledialog.askstring("Odzyskiwanie hasła", "Powtórz nowe hasło:", show="*", parent=app)
    if new != confirm:
        messagebox.showerror("Błąd", "Nowe hasła się różnią.", parent=app); return

    auth_cursor.execute("UPDATE users SET password_hash=?, updated_at=? WHERE username=?",
                        (hash_password(new), datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), username))
    auth_conn.commit()

    _store_keys(username, dk, password=new, answer=None, admin_enable=True)
    messagebox.showinfo("Sukces", "Hasło ustawione na nowe.", parent=app)


def set_security_question():
    username = CURRENT_USER or simpledialog.askstring("Pytanie kontrolne", "Nazwa użytkownika:", parent=app)
    if not username: return
    password = simpledialog.askstring("Pytanie kontrolne", "Hasło użytkownika:", show="*", parent=app)
    if not password: return

    auth_cursor.execute("SELECT password_hash FROM users WHERE username=?", (username,))
    row = auth_cursor.fetchone()
    if not row or not hmac.compare_digest(row[0], hash_password(password)):
        messagebox.showerror("Błąd", "Nieprawidłowe hasło.", parent=app); return

    dk = _load_data_key_via_password(username, password)
    if dk is None:
        if not migrate_encryption_for_username(username, password):
            return
        dk = _load_data_key_via_password(username, password)
        if dk is None:
            messagebox.showerror("Błąd", "Nie udało się ustawić pytania (klucz).", parent=app); return

    q = simpledialog.askstring("Pytanie kontrolne", "Treść pytania:", parent=app)
    a = simpledialog.askstring("Pytanie kontrolne", "Odpowiedź:", show="*", parent=app)
    if not q or not a:
        return

    auth_cursor.execute("UPDATE users SET sec_q=?, sec_ans_hash=?, updated_at=? WHERE username=?",
                        (q, hash_answer(a), datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), username))
    auth_conn.commit()

    _store_keys(username, dk, password=None, answer=a, admin_enable=True)
    messagebox.showinfo("Sukces", "Pytanie kontrolne zapisane.", parent=app)

def admin_change_password():
    """
    Zmiana hasła dowolnego użytkownika BEZ znajomości starego hasła,
    z zachowaniem dostępu do istniejących danych (bez zmiany data_key).
    Wymaga, aby konto miało już 'data_key_enc_by_admin'.
    """
    user = simpledialog.askstring("Admin: zmiana hasła", "Konto użytkownika:", parent=app)
    if not user:
        return

    dk = _load_data_key_via_admin(user, ADMIN_CODE)
    if dk is None:
        messagebox.showerror(
            "Admin",
            "Brak kopii klucza admina dla tego konta.\n"
            "Zaloguj się na to konto zwyczajnie, aby dopisać kopię admina.",
            parent=app
        )
        return

    new = simpledialog.askstring("Admin: nowe hasło", "Nowe hasło:", show="*", parent=app)
    if not new:
        return
    confirm = simpledialog.askstring("Admin: nowe hasło", "Powtórz nowe hasło:", show="*", parent=app)
    if new != confirm:
        messagebox.showerror("Błąd", "Nowe hasła się różnią.", parent=app); return

    auth_cursor.execute(
        "UPDATE users SET password_hash=?, updated_at=? WHERE username=?",
        (hash_password(new), datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), user)
    )
    auth_conn.commit()

    _store_keys(user, dk, password=new, answer=None, admin_enable=True)

    messagebox.showinfo("Admin", f"Hasło użytkownika '{user}' zostało zmienione.", parent=app)

def admin_change_password_hidden():
    """
    Ukryta zmiana hasła w trybie admina.
    Wywoływana tylko z poziomu admina, brak menu ani widocznych przycisków.
    """
    if not CURRENT_USER or CURRENT_USER == "":
        messagebox.showerror("Admin", "Nie jesteś w trybie admina!", parent=app)
        return

    # pytamy o konto, którego hasło chcemy zmienić
    user = simpledialog.askstring("Admin", "Konto do zmiany hasła:", parent=app)
    if not user:
        return

    dk = _load_data_key_via_admin(user, ADMIN_CODE)
    if dk is None:
        messagebox.showerror(
            "Admin",
            f"Brak kopii klucza admina dla konta {user}.\n"
            f"Zaloguj się normalnie na {user}, aby dopisać kopię.",
            parent=app
        )
        return

    new = simpledialog.askstring("Admin", f"Nowe hasło dla {user}:", show="*", parent=app)
    if not new:
        return
    confirm = simpledialog.askstring("Admin", "Powtórz nowe hasło:", show="*", parent=app)
    if new != confirm:
        messagebox.showerror("Błąd", "Nowe hasła się różnią.", parent=app)
        return

    # aktualizacja
    auth_cursor.execute(
        "UPDATE users SET password_hash=?, updated_at=? WHERE username=?",
        (hash_password(new), datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), user)
    )
    auth_conn.commit()

    # odśwież kopię klucza przy nowym haśle
    _store_keys(user, dk, password=new, answer=None, admin_enable=True)

    messagebox.showinfo("Admin", f"Hasło użytkownika '{user}' zostało zmienione.", parent=app)



# =============================
#   MIGRACJA SZYFROWANIA
# =============================
def migrate_encryption_for_username(username: str, password: str) -> bool:
    try:
        dbfile = USER_DB_MASK.format(username)
        if not os.path.exists(dbfile):
            dk = gen_data_key()
            _store_keys(username, dk, password=password, answer=None)
            return True

        with sqlite3.connect(dbfile) as conn:
            cur = conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL;")
            cur.execute("PRAGMA synchronous=NORMAL;")
            conn.commit()

            old_f = Fernet(derive_fernet_key_from_password(password))
            new_dk = gen_data_key()
            new_f  = Fernet(new_dk)

            cur.execute("SELECT id, imie, uwagi FROM dane")
            for rid, im, uw in cur.fetchall():
                def _conv(tok):
                    if tok is None:
                        return None
                    try:
                        txt = old_f.decrypt(tok).decode()
                        return new_f.encrypt(txt.encode())
                    except Exception:
                        return tok
                cur.execute("UPDATE dane SET imie=?, uwagi=? WHERE id=?", (_conv(im), _conv(uw), rid))

            cur.execute("SELECT id, szczegoly FROM zdarzenia")
            for rid, sz in cur.fetchall():
                if sz is None:
                    continue
                try:
                    txt = old_f.decrypt(sz).decode()
                    cur.execute("UPDATE zdarzenia SET szczegoly=? WHERE id=?", (new_f.encrypt(txt.encode()), rid))
                except Exception:
                    pass

            conn.commit()

        _store_keys(username, new_dk, password=password, answer=None, admin_enable=True)
        return True
    except Exception as e:
        logging.exception("Migracja nie powiodła się: %s", e)
        messagebox.showerror("Migracja", f"Nie udało się przeprowadzić migracji:\n{e}", parent=app)
        return False

def migrate_current_user():
    if not CURRENT_USER:
        messagebox.showerror("Migracja", "Zaloguj się najpierw.", parent=app); return
    pwd = simpledialog.askstring("Migracja", "Podaj swoje (obecne) hasło:", show="*", parent=app)
    if not pwd: return
    if migrate_encryption_for_username(CURRENT_USER, pwd):
        messagebox.showinfo("Migracja", "Zakończono migrację szyfrowania.", parent=app)

# =============================
#   WALIDACJE / DATY
# =============================
def validate_date(label: str, val: str) -> bool:
    if not val: return True
    try:
        datetime.datetime.strptime(val, "%d-%m-%Y")
        return True
    except Exception:
        messagebox.showerror("Błąd", f"Niepoprawna {label}: {val} (użyj DD-MM-RRRR).", parent=app)
        return False

def parse_date_ui(s: str) -> Optional[datetime.date]:
    if not s:
        return None
    try:
        return datetime.datetime.strptime(s, "%d-%m-%Y").date()
    except Exception:
        return None

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
#   KARTA ZWIERZĘCIA (dialogi)
# =============================
def add_record():
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

class AddRecordDialog(tb.Toplevel):
    def __init__(self, master):
        super().__init__(master)
        self.title("Karta zwierzęcia – edycja")
        self.geometry("700x600")
        self.resizable(True, True)
        self.result = None

        # --- kontener z przewijaniem ---
        canvas = tk.Canvas(self, borderwidth=0)
        frame = tb.Frame(canvas, padding=14, style="Card.TFrame")
        vsb = tb.Scrollbar(self, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)

        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        canvas.create_window((0, 0), window=frame, anchor="nw")

        def on_frame_configure(event):
            canvas.configure(scrollregion=canvas.bbox("all"))
        frame.bind("<Configure>", on_frame_configure)

        # --- pola formularza ---
        def row(r, label, var, width=28):
            tb.Label(frame, text=label).grid(row=r, column=0, sticky="e", padx=6, pady=4)
            e = tb.Entry(frame, textvariable=var, width=width)
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

        try:
            cb_g = tb.Combobox(frame, values=["koza","owca","bydło","inne"], textvariable=self.v_gatunek, state="readonly", width=26)
            cb_g.grid(row=2, column=1, sticky="w", padx=6, pady=4)
            cb_s = tb.Combobox(frame, values=["żywe","padłe","ubój","zbyte"], textvariable=self.v_status, state="readonly", width=26)
            cb_s.grid(row=11, column=1, sticky="w", padx=6, pady=4)
            cb_p = tb.Combobox(frame, values=["M","K"], textvariable=self.v_plec, state="readonly", width=6)
            cb_p.grid(row=5, column=1, sticky="w", padx=6, pady=4)
        except Exception:
            pass

        # --- przyciski na dole ---
        btns = tb.Frame(frame, padding=(0,8,0,0))
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

# =============================
#   WSPARCIE „SZYBKIEGO EKSPORTU”
# =============================
_LAST_TABLE_CONTEXT: Dict[str, Optional[object]] = {"headers": None, "rows_fn": None, "title": None}

def _remember_table(headers: List[str], rows_fn: Callable[[], List[tuple]], title: str):
    _LAST_TABLE_CONTEXT["headers"] = headers
    _LAST_TABLE_CONTEXT["rows_fn"] = rows_fn
    _LAST_TABLE_CONTEXT["title"] = title

def quick_export_current(fmt: str):
    headers = _LAST_TABLE_CONTEXT.get("headers")
    rows_fn = _LAST_TABLE_CONTEXT.get("rows_fn")
    title   = _LAST_TABLE_CONTEXT.get("title") or "Widok"
    if not headers or not rows_fn:
        messagebox.showerror("Eksport", "Brak aktywnego widoku do eksportu.", parent=app); return
    rows = rows_fn()
    name = f'{(title or "widok").replace(" ","_")}_{datetime.datetime.now().strftime("%Y%m%d_%H%M%S")}'
    path = _save_dialog(name, fmt)
    if not path: return
    _export_rows_cols(headers, rows, path, fmt, title=title)

# =============================
#   WYSUWANE MENU (DRAWER)
# =============================
class SlideMenu(tk.Frame):
    reset_password = ""
    reset_password_dialog = ""
    def __init__(self, master, width=300):
        super().__init__(master, bg="#222")
        self.width = width
        self.opened = False
        self.place(relx=1.0, rely=0, relwidth=0, relheight=1.0, anchor="ne")

        cont = tb.Frame(self, padding=12, style="Card.TFrame")
        cont.place(relx=0, rely=0, relwidth=1.0, relheight=1.0)
        tb.Label(cont, text="Szybkie akcje", font="-size 12 -weight bold").pack(anchor="w", pady=(0,8))

        tb.Button(cont, text="Eksport widoku → XLSX", bootstyle="info",
                  command=lambda: quick_export_current("xlsx")).pack(fill="x", pady=4)
        tb.Button(cont, text="Eksport widoku → HTML", bootstyle="info",
                  command=lambda: quick_export_current("html")).pack(fill="x", pady=4)
        tb.Button(cont, text="Eksport widoku → Markdown", bootstyle="info",
                  command=lambda: quick_export_current("md")).pack(fill="x", pady=4)
        tb.Button(cont, text="Eksport zdarzeń CSV", bootstyle="secondary",
                  command=lambda: export_events_csv()).pack(fill="x", pady=4)
        tb.Separator(cont).pack(fill="x", pady=8)
        tb.Button(cont, text="Analiza spójności", bootstyle="secondary",
                  command=analiza_spojnosci).pack(fill="x", pady=4)
        tb.Button(cont, text="Reset hasła…", bootstyle="danger",
                  command=self.reset_password_dialog).pack(fill="x", pady=4)

    def toggle(self):
        if self.opened:
            self._animate_hide()
        else:
            self._animate_show()

    def _animate_show(self):
        self.opened = True
        for i in range(0, 21):
            self.place(relx=1.0, rely=0, relwidth=i/20, relheight=1.0, anchor="ne")
            self.update_idletasks()
            self.after(6)

    def _animate_hide(self):
        for i in range(20, -1, -1):
            self.place(relx=1.0, rely=0, relwidth=i/20, relheight=1.0, anchor="ne")
            self.update_idletasks()
            self.after(6)
        self.opened = False

# =============================
#   WYŚWIETLANIE TABEL
# =============================
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

    # zapamiętaj kontekst widoku do „szybkiego eksportu”
    _remember_table(headers, lambda: table.filtered[:], title)

def show_data(where_clause: Optional[str]=None, params: Tuple=(), order_by: Optional[str]=None):
    if not SESSION_FERNET:
        messagebox.showerror("Błąd", "Zaloguj się ponownie.", parent=app); return
    q = ("SELECT kolczyk,gatunek,data_urodzenia,plec,rasa_kod,siedziba_stada,status,data_statusu,imie "
         "FROM dane")
    if where_clause: q += f" WHERE {where_clause}"
    if order_by:     q += f" ORDER BY {order_by}"
    user_cursor.execute(q, params); rows_db = user_cursor.fetchall()
    if not rows_db:
        messagebox.showinfo("Informacja", "Brak wyników.", parent=app); return

    rows = []
    for k, g, du, p, r, ss, status, dst, im_b in rows_db:
        try:
            imie = decrypt_text(im_b) if im_b else ""
        except Exception:
            imie = "<błąd>"
        rows.append((
            k or "", g or "", du or "", p or "", r or "",
            ss or "", status or "", dst or "", imie
        ))



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
#   ZDARZENIA
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
#   EKSPORTER – KLASYKA + NOWE FORMATY
# =============================
class Exporter:
    def __init__(self): pass
    @staticmethod
    def _normalize_rows(rows: List[tuple]) -> List[List[str]]:
        return [[("" if v is None else str(v)) for v in r] for r in rows]

    def to_csv(self, columns: List[str], rows: List[tuple], path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(columns)
            for r in self._normalize_rows(rows):
                w.writerow(r)
        return path

    def to_xlsx(self, columns: List[str], rows: List[tuple], path: str, sheet="Dane"):
        from openpyxl import Workbook

        Path(path).parent.mkdir(parents=True, exist_ok=True)
        wb = Workbook()
        ws = wb.active
        ws.title = sheet
        ws.append(list(columns))
        for r in self._normalize_rows(rows):
            ws.append(r)
        for i, col in enumerate(columns, start=1):
            values = [str(col)]
            for rr in rows:
                v = rr[i-1] if i-1 < len(rr) else ""
                values.append("" if v is None else str(v))
            width = min(max(len(str(v)) for v in values) + 2, 60)
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
            mv = max((len(str(r[i])) if i < len(r) and r[i] is not None else 0) for r in rows) if rows else 0
            widths.append(max(len(c), mv))
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
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
        from reportlab.lib import colors
        from reportlab.lib.styles import getSampleStyleSheet
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

    # --- NOWE FORMATY ---
    def to_html(self, columns: List[str], rows: List[tuple], path: str, title="Dane"):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        def esc(x):
            s = "" if x is None else str(x)
            return (s.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;"))
        html = [f"<!doctype html><meta charset='utf-8'><title>{esc(title)}</title>",
                "<style>table{border-collapse:collapse;font-family:sans-serif} th,td{border:1px solid #ddd;padding:6px} th{background:#2b8a3e;color:#fff}</style>",
                f"<h3>{esc(title)}</h3>", "<table>", "<thead><tr>"]
        html += [f"<th>{esc(c)}</th>" for c in columns]
        html += ["</tr></thead><tbody>"]
        for r in rows:
            html.append("<tr>")
            for i in range(len(columns)):
                v = "" if i>=len(r) or r[i] is None else str(r[i])
                html.append(f"<td>{esc(v)}</td>")
            html.append("</tr>")
        html += ["</tbody></table>"]
        Path(path).write_text("\n".join(html), encoding="utf-8")
        return path

    def to_markdown(self, columns: List[str], rows: List[tuple], path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        def esc(x):
            s = "" if x is None else str(x)
            return s.replace("|","\\|")
        lines = ["| " + " | ".join(columns) + " |",
                 "| " + " | ".join(["---"]*len(columns)) + " |"]
        for r in rows:
            row = []
            for i in range(len(columns)):
                v = "" if i>=len(r) or r[i] is None else str(r[i])
                row.append(esc(v))
            lines.append("| " + " | ".join(row) + " |")
        Path(path).write_text("\n".join(lines)+"\n", encoding="utf-8")
        return path

    def to_parquet(self, columns: List[str], rows: List[tuple], path: str):
        

        Path(path).parent.mkdir(parents=True, exist_ok=True)
        import pandas as pd
        df = pd.DataFrame([[("" if v is None else v) for v in r] for r in rows], columns=columns)
        df.to_parquet(path, index=False)
        return path

    def to_feather(self, columns: List[str], rows: List[tuple], path: str):
        import pandas as pd

        Path(path).parent.mkdir(parents=True, exist_ok=True)
        df = pd.DataFrame([[("" if v is None else v) for v in r] for r in rows], columns=columns)
        df.to_feather(path)
        return path

EXPORTER = Exporter()

# =============================
#   IMPORT / EKSPORT – FUNKCJE
# =============================
def _save_dialog(default_name: str, fmt: str) -> Optional[str]:
    ext = f".{fmt}"
    types = [("Excel XLSX","*.xlsx"),("CSV","*.csv"),("JSON","*.json"),("PDF","*.pdf"),
             ("TXT","*.txt"),("XML","*.xml"),("HTML","*.html"),("Markdown","*.md"),
             ("Parquet","*.parquet"),("Feather","*.feather"),("SQLite","*.sqlite *.db"),
             ("Wszystkie pliki", "*.*")]
    return filedialog.asksaveasfilename(
        parent=app, title="Gdzie zapisać?", defaultextension=ext,
        initialfile=default_name+ext, filetypes=types
    )

def _choose_format() -> Optional[str]:
    win = tb.Toplevel(app); win.title("Eksport – format"); win.resizable(False, False)
    fmt = tk.StringVar(value="xlsx")
    tb.Label(win, text="Wybierz format eksportu:").pack(padx=12, pady=10)
    combo = tb.Combobox(
        win,
        values=["csv","xlsx","json","pdf","txt","xml","html","md","parquet","feather"],
        textvariable=fmt, state="readonly", width=18
    )
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
        elif fmt == "html":   EXPORTER.to_html(columns, rows, path, title=title)
        elif fmt == "md":     EXPORTER.to_markdown(columns, rows, path)
        elif fmt == "parquet":EXPORTER.to_parquet(columns, rows, path)
        elif fmt == "feather":EXPORTER.to_feather(columns, rows, path)
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

def export_events_csv():  # kompatybilność i skrót
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
#   IMPORT (CSV/XLSX)
# =============================
def _read_csv_auto(path: str) -> List[dict]:
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

def import_dane_xlsx():
    import pandas as pd

    if not SESSION_FERNET:
        messagebox.showerror("Błąd","Zaloguj się ponownie.", parent=app); return
    path = filedialog.askopenfilename(parent=app, title="Wybierz XLSX z kartami", filetypes=[("Excel XLSX","*.xlsx")])
    if not path: return

    try:
        df = pd.read_excel(path, dtype=str).fillna("")
        required = ["kolczyk","gatunek","data_urodzenia","data_oznakowania","plec","rasa_kod",
                    "matka_kolczyk","siedziba_stada","pochodzenie","data_przybycia",
                    "status","data_statusu","imie","uwagi"]
        missing = [c for c in required if c not in df.columns]
        if missing:
            messagebox.showerror("Import", f"Brakuje kolumn: {', '.join(missing)}", parent=app); return
        count = 0
        for _, r in df.iterrows():
            imie_b = encrypt_text(r["imie"]) if r["imie"] else None
            uwagi_b= encrypt_text(r["uwagi"]) if r["uwagi"] else None
            user_cursor.execute("""
                INSERT OR REPLACE INTO dane
                (kolczyk,gatunek,data_urodzenia,data_oznakowania,plec,rasa_kod,matka_kolczyk,
                 siedziba_stada,pochodzenie,data_przybycia,status,data_statusu,imie,uwagi)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                r["kolczyk"], r["gatunek"], r["data_urodzenia"], r["data_oznakowania"], r["plec"], r["rasa_kod"],
                r["matka_kolczyk"], r["siedziba_stada"], r["pochodzenie"], r["data_przybycia"],
                r["status"], r["data_statusu"], imie_b, uwagi_b
            ))
            count += 1
        user_conn.commit()
        messagebox.showinfo("Import", f"Zaimportowano {count} kart z {os.path.basename(path)}.", parent=app)
    except Exception as e:
        messagebox.showerror("Import", f"Nie udało się wczytać XLSX.\n\n{e}", parent=app)

def import_zdarzenia_xlsx():
    if not SESSION_FERNET:
        messagebox.showerror("Błąd","Zaloguj się ponownie.", parent=app); return
    path = filedialog.askopenfilename(parent=app, title="Wybierz XLSX ze zdarzeniami", filetypes=[("Excel XLSX","*.xlsx")])
    if not path: return
    try:
        import pandas as pd

        df = pd.read_excel(path, dtype=str).fillna("")
        required = ["kolczyk","gatunek","typ","data_zdarzenia","z_siedziby","do_siedziby","dokument_nr",
                    "srodek_transportu","kierowca","powod","kraj_docelowy","nowy_kolczyk","szczegoly","created_at"]
        missing = [c for c in required if c not in df.columns]
        if missing:
            messagebox.showerror("Import", f"Brakuje kolumn: {', '.join(missing)}", parent=app); return
        count = 0
        for _, r in df.iterrows():
            sz_b = encrypt_text(r["szczegoly"]) if r["szczegoly"] else None
            created = r["created_at"] or datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            user_cursor.execute("""
                INSERT INTO zdarzenia (
                    kolczyk,gatunek,typ,data_zdarzenia,z_siedziby,do_siedziby,dokument_nr,
                    srodek_transportu,kierowca,powod,kraj_docelowy,nowy_kolczyk,szczegoly,created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                r["kolczyk"], r["gatunek"], r["typ"], r["data_zdarzenia"],
                r["z_siedziby"], r["do_siedziby"], r["dokument_nr"],
                r["srodek_transportu"], r["kierowca"], r["powod"],
                r["kraj_docelowy"], r["nowy_kolczyk"], sz_b, created
            ))
            count += 1
        user_conn.commit()
        messagebox.showinfo("Import", f"Zaimportowano {count} zdarzeń z {os.path.basename(path)}.", parent=app)
    except Exception as e:
        messagebox.showerror("Import", f"Nie udało się wczytać XLSX.\n\n{e}", parent=app)

# =============================
#   RAPORT XLSX (pandas)
# =============================
def export_raport_xlsx():
    if not SESSION_FERNET:
        messagebox.showerror("Raport", "Zaloguj się ponownie.", parent=app); return
    path = filedialog.asksaveasfilename(parent=app, title="Zapisz raport XLSX", defaultextension=".xlsx",
                                        filetypes=[("Excel XLSX","*.xlsx")], initialfile=f"raport_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx")
    if not path: return
    try:
        user_cursor.execute("""
            SELECT kolczyk,gatunek,data_urodzenia,data_oznakowania,plec,rasa_kod,matka_kolczyk,
                   siedziba_stada,pochodzenie,data_przybycia,status,data_statusu,imie,uwagi
            FROM dane
        """)
        rows_d = user_cursor.fetchall()
        out_d = []
        for r in rows_d:
            imie = decrypt_text(r[12]) if r[12] else ""
            uwagi= decrypt_text(r[13]) if r[13] else ""
            out_d.append(list(r[:12]) + [imie, uwagi])
        cols_d = ["kolczyk","gatunek","data_urodzenia","data_oznakowania","plec","rasa_kod","matka_kolczyk",
                  "siedziba_stada","pochodzenie","data_przybycia","status","data_statusu","imie","uwagi"]
        df_d = pd.DataFrame(out_d, columns=cols_d)

        user_cursor.execute("""
            SELECT kolczyk,gatunek,typ,data_zdarzenia,z_siedziby,do_siedziby,dokument_nr,
                   srodek_transportu,kierowca,powod,kraj_docelowy,nowy_kolczyk,szczegoly,created_at
            FROM zdarzenia
        """)
        import pandas as pd

        rows_z = user_cursor.fetchall()
        out_z = []
        for r in rows_z:
            sz = decrypt_text(r[12]) if r[12] else ""
            out_z.append(list(r[:12]) + [sz, r[13]])
        cols_z = ["kolczyk","gatunek","typ","data_zdarzenia","z_siedziby","do_siedziby","dokument_nr",
                  "srodek_transportu","kierowca","powod","kraj_docelowy","nowy_kolczyk","szczegoly","created_at"]
        df_z = pd.DataFrame(out_z, columns=cols_z)

        agg_status = df_d.groupby(["gatunek","status"], dropna=False).size().reset_index(name="liczba")
        def _to_month(s):
            try:
                return pd.to_datetime(s, dayfirst=True, errors="coerce").strftime("%Y-%m")
            except Exception:
                return None
        df_z["miesiac"] = df_z["data_zdarzenia"].apply(_to_month)
        agg_mies = df_z.groupby(["miesiac","typ"], dropna=False).size().reset_index(name="liczba").sort_values(["miesiac","typ"])

        with pd.ExcelWriter(path, engine="openpyxl") as xw:
            summary = {
                "liczba_zwierzat": [len(df_d)],
                "liczba_zdarzen": [len(df_z)],
                "ostatni_eksport": [datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")]
            }
            pd.DataFrame(summary).to_excel(xw, index=False, sheet_name="Podsumowanie")
            df_d.to_excel(xw, index=False, sheet_name="Dane")
            df_z.to_excel(xw, index=False, sheet_name="Zdarzenia")
            agg_status.to_excel(xw, index=False, sheet_name="Per_status")
            agg_mies.to_excel(xw, index=False, sheet_name="Zdarzenia_mies")
        messagebox.showinfo("Raport", f"Zapisano raport: {path}", parent=app)
    except Exception as e:
        messagebox.showerror("Raport", f"Nie udało się zapisać raportu.\n\n{e}", parent=app)

# =============================
#   ANALIZA SPÓJNOŚCI
# =============================
def analiza_spojnosci():
    if not SESSION_FERNET:
        messagebox.showerror("Analiza", "Zaloguj się ponownie.", parent=app); return
    issues = []

    user_cursor.execute("SELECT kolczyk, COUNT(*) c FROM dane GROUP BY kolczyk HAVING c>1")
    dups = user_cursor.fetchall()
    if dups:
        issues.append("Duplikaty kolczyków: " + ", ".join(f"{k} x{c}" for k,c in dups))

    user_cursor.execute("SELECT kolczyk, gatunek, status FROM dane")
    for k,g,s in user_cursor.fetchall():
        if not k or not g or not s:
            issues.append(f"Brak wymaganych pól w karcie: {k or '(brak kolczyka)'}")

    user_cursor.execute("""
        SELECT z.kolczyk, COUNT(*)
        FROM zdarzenia z
        LEFT JOIN dane d ON d.kolczyk = z.kolczyk
        WHERE d.kolczyk IS NULL
        GROUP BY z.kolczyk
    """)
    orphans = user_cursor.fetchall()
    if orphans:
        issues.append("Zdarzenia osierocone (brak karty): " + ", ".join(f"{k} x{c}" for k,c in orphans))

    def _bad_dates(col, table):
        user_cursor.execute(f"SELECT {col} FROM {table}")
        bad = 0
        for (s,) in user_cursor.fetchall():
            if s and not parse_date_ui(s):
                bad += 1
        return bad
    bd = _bad_dates("data_urodzenia", "dane") + _bad_dates("data_przybycia", "dane") + _bad_dates("data_statusu", "dane") + _bad_dates("data_zdarzenia", "zdarzenia")
    if bd:
        issues.append(f"Nieparsowalne daty (łącznie): {bd}")

    if not issues:
        messagebox.showinfo("Analiza", "Brak problemów. Dane wyglądają spójnie.", parent=app)
    else:
        messagebox.showwarning("Analiza – wykryto problemy", "\n• " + "\n• ".join(issues), parent=app)

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
app.geometry("1250x820")

# ======== UKRYTY TRIGGER ADMIN (ciąg cyfr) ========
_key_buffer = []

def _admin_secret_listener(event):
    # zbieramy tylko cyfry; bufor max 24 znaki
    ch = getattr(event, "char", "")
    if ch and ch.isdigit():
        _key_buffer.append(ch)
        if len(_key_buffer) > 24:
            del _key_buffer[0:len(_key_buffer)-24]
        if "".join(_key_buffer).endswith(ADMIN_CODE):
            admin_login_dialog()

def admin_login_dialog():
    user = simpledialog.askstring("Admin", "Konto użytkownika do otwarcia:", parent=app)
    if not user:
        return
    dk = _load_data_key_via_admin(user, ADMIN_CODE)
    if dk is None:
        messagebox.showerror("Admin", "Brak kopii klucza admina dla tego konta.\n"
                                      "Zaloguj się zwyczajnie raz, aby dopisać kopię.", parent=app)
        return
    global SESSION_FERNET, CURRENT_USER
    SESSION_FERNET = Fernet(dk)
    CURRENT_USER   = user
    init_user_db(user)

    # --- NOWE: pytanie o akcję ---
    choice = messagebox.askyesno("Admin", f"Zalogowano adminem do konta {user}.\n\nCzy chcesz zmienić jego hasło?")
    if choice:
        admin_change_password_hidden()
    else:
        show_menu()


# globalne powiązanie — nasłuch w całej aplikacji
app.bind_all("<Key>", _admin_secret_listener)

def toggle_theme():
    try:
        current = app.style.theme.name
        app.style.theme_use("flatly" if current == "darkly" else "darkly")
    except Exception:
        pass

# TŁO: BACKGROUND.JPG (opcjonalne)
bg_label = None
try:
    bg_image = Image.open(resource_path("AbC.JPG"))
    app.bg_tk = ImageTk.PhotoImage(bg_image)
    bg_label = tk.Label(app, image=app.bg_tk, bd=0)
    bg_label.place(relx=0, rely=0, relwidth=1, relheight=1)
except Exception:
    app.configure(bg="#1f1f1f")

# ======== MENU GÓRNE ========
menubar = Menu(app)

m_plk = Menu(menubar, tearoff=0)
m_plk.add_command(label="Import kart (CSV)…", command=import_dane_csv)
m_plk.add_command(label="Import kart (XLSX, pandas)…", command=import_dane_xlsx)
m_plk.add_command(label="Import zdarzeń (CSV)…", command=import_zdarzenia_csv)
m_plk.add_command(label="Import zdarzeń (XLSX, pandas)…", command=import_zdarzenia_xlsx)
m_plk.add_separator()
m_plk.add_command(label="Eksport kart…", command=export_dane)
m_plk.add_command(label="Eksport zdarzeń…", command=export_zdarzenia)
m_plk.add_command(label="Raport XLSX (pandas)…", command=export_raport_xlsx)
m_plk.add_separator()
m_plk.add_command(label="Wyjście", command=app.quit)
menubar.add_cascade(label="Plik", menu=m_plk)

m_ed = Menu(menubar, tearoff=0)
m_ed.add_command(label="Dodaj/edytuj kartę (szybko)", command=add_record)
m_ed.add_command(label="Formularz karty (ładny)", command=add_record_form)
m_ed.add_command(label="Zdarzenie", command=register_event)
m_ed.add_command(label="Zmień status", command=change_status)
m_ed.add_separator()
m_ed.add_command(label="Szukaj: imię", command=search_by_name)
m_ed.add_command(label="Szukaj: ostatnie 3 cyfry", command=search_by_last3)
menubar.add_cascade(label="Edycja", menu=m_ed)

m_widok = Menu(menubar, tearoff=0)
m_widok.add_command(label="Kartoteka (kolczyk)", command=lambda: show_data(order_by='kolczyk'))
m_widok.add_command(label="Historia zdarzeń", command=show_events_for_animal)
menubar.add_cascade(label="Widok", menu=m_widok)

m_ust = Menu(menubar, tearoff=0)
m_ust.add_command(label="Migracja szyfrowania (legacy → envelope)…", command=migrate_current_user)
m_ust.add_command(label="Ustaw pytanie kontrolne…", command=set_security_question)
m_ust.add_command(label="Reset hasła…", command=lambda: reset_password_dialog())
m_ust.add_command(label="Analiza spójności…", command=analiza_spojnosci)
menubar.add_cascade(label="Ustawienia", menu=m_ust)

m_pomoc = Menu(menubar, tearoff=0)
m_pomoc.add_command(label="Aktualizuj", command=update_app)
m_pomoc.add_command(label="O programie", command=lambda: messagebox.showinfo(
    "IRZ", "IRZ – Menedżer zwierząt (FENIX)\nv2.5 – MIT/BSD biblioteki.\nEnvelope encryption + reset hasła."))
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
tb.Button(topbar, text="Import CSV", bootstyle="secondary", command=import_dane_csv).pack(side=LEFT, padx=4)
tb.Button(topbar, text="Import XLSX", bootstyle="secondary", command=import_dane_xlsx).pack(side=LEFT, padx=4)
tb.Button(topbar, text="Eksport kart…", bootstyle="info", command=export_dane).pack(side=LEFT, padx=4)
tb.Button(topbar, text="Eksport zdarzeń…", bootstyle="info", command=export_zdarzenia).pack(side=LEFT, padx=4)
tb.Button(topbar, text="Raport XLSX", bootstyle="info", command=export_raport_xlsx).pack(side=LEFT, padx=4)
tb.Separator(topbar, orient=VERTICAL).pack(side=LEFT, fill=Y, padx=10)
tb.Button(topbar, text="Reset hasła", bootstyle="danger", command=lambda: reset_password_dialog()).pack(side=LEFT, padx=6)
tb.Button(topbar, text="☾/☀ Motyw", bootstyle="secondary", command=toggle_theme).pack(side=LEFT, padx=4)

# przycisk hamburger do wysuwania panelu
drawer = SlideMenu(app)
tb.Button(topbar, text="☰ Menu", bootstyle="secondary", command=drawer.toggle).pack(side=RIGHT, padx=6)

# ======== RESET HASŁA – WRAPPER ========
def reset_password_dialog():
    win = tb.Toplevel(app); win.title("Reset hasła"); win.resizable(False, False)
    tb.Label(win, text="Wybierz tryb resetu:").pack(padx=12, pady=(12,6))
    tb.Button(win, text="Zmiana (mam stare hasło)", bootstyle="success",
              command=lambda: (win.destroy(), change_password())).pack(padx=12, pady=6)
    tb.Button(win, text="Odzyskanie (pytanie kontrolne)", bootstyle="warning",
              command=lambda: (win.destroy(), recover_password())).pack(padx=12, pady=(0,12))
    win.grab_set()

# ======== PANEL LOGOWANIA ========
login_card = tb.Frame(app, padding=24, style="Card.TFrame")
login_card.place(relx=0.5, rely=0.35, anchor="center")
tb.Label(login_card, text="IRZ – logowanie", font="-size 16 -weight bold").grid(row=0, column=0, columnspan=3, pady=(0,12))
tb.Button(login_card, text="Zarejestruj", width=18, bootstyle="success", command=register_user).grid(row=1, column=0, padx=8, pady=6)
tb.Button(login_card, text="Zaloguj", width=18, bootstyle="primary",
          command=lambda: show_menu() if login_user() else None).grid(row=1, column=1, padx=8, pady=6)
tb.Button(login_card, text="Reset hasła…", width=18, bootstyle="warning",
          command=lambda: reset_password_dialog()).grid(row=1, column=2, padx=8, pady=6)



# ======== EKRAN MENU PO ZALOGOWANIU ========
def show_menu():
    for w in app.winfo_children():
        if w in (topbar, bg_label):
            continue
        try: w.destroy()
        except Exception: pass

    app.title(f"{APP_NAME} – {CURRENT_USER}")

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
        ("Analiza spójności", analiza_spojnosci, "secondary"),
        ("Eksport zdarzeń CSV", export_events_csv, "info"),
        ("Eksport kart…", export_dane, "info"),
        ("Eksport zdarzeń…", export_zdarzenia, "info"),
        ("Raport XLSX (pandas)", export_raport_xlsx, "info"),
        ("Import kart CSV…", import_dane_csv, "secondary"),
        ("Import kart XLSX…", import_dane_xlsx, "secondary"),
        ("Import zdarzeń CSV…", import_zdarzenia_csv, "secondary"),
        ("Import zdarzeń XLSX…", import_zdarzenia_xlsx, "secondary"),
        ("Migracja szyfrowania", migrate_current_user, "danger"),
        ("Ustaw pytanie kontrolne", set_security_question, "secondary"),
        ("Aktualizuj", update_app, "danger"),
    ]
    for i, (label, fn, style) in enumerate(actions):
        tb.Button(grid, text=label, width=28, bootstyle=style, command=fn)\
          .grid(row=i//2, column=i%2, padx=10, pady=8)

    status = tb.Label(app, text="Gotowe.", anchor="w")
    status.pack(side=BOTTOM, fill=X, padx=8, pady=6)

# Skróty
app.bind_all("<Control-e>", lambda e: export_dane())
app.bind_all("<Control-h>", lambda e: show_events_for_animal())
app.bind_all("<Control-f>", lambda e: search_by_name())


# =============================
#   AUTO-LOGIN PRZY STARCIU
# =============================
if try_auto_login():
    show_menu()  # od razu pokaż menu
else:
    # pokaż kartę logowania
    login_card.lift()

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


toggle_theme()