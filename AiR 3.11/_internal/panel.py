# panel.py
# -*- coding: utf-8 -*-
"""
AIR/IRZ++ • Panel logowania, rejestracji i główny (naprawione uruchamianie modułów .exe/.py)
Autor: ChatGPT dla Feniksa
"""

import os
import sys
import sqlite3
import hashlib
import hmac
import base64
import json
import datetime
import subprocess
import tkinter as tk
from tkinter import messagebox
import ttkbootstrap as tb
from ttkbootstrap.constants import *
from PIL import Image, ImageTk

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

# =============================
#   ŚCIEŻKI
# =============================
APPDIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "KozyManager")
os.makedirs(APPDIR, exist_ok=True)

AUTH_DB_FILE = os.path.join(APPDIR, "users.db")
SALT_FILE    = os.path.join(APPDIR, "salt.key")
SESSION_FILE = os.path.join(APPDIR, "session_token.json")

# =============================
#   SÓL
# =============================
if not os.path.exists(SALT_FILE):
    with open(SALT_FILE, "wb") as f:
        f.write(os.urandom(16))
with open(SALT_FILE, "rb") as f:
    salt = f.read()

# =============================
#   HASH + KLUCZE
# =============================
KDF_ITERATIONS = 100_000

def derive_key(password: str) -> bytes:
    """Tworzy klucz Fernet na podstawie hasła użytkownika (PBKDF2-HMAC-SHA256)."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=KDF_ITERATIONS,
    )
    return base64.urlsafe_b64encode(kdf.derive(password.encode()))

def hash_password(password: str) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, KDF_ITERATIONS)

# =============================
#   DB INIT
# =============================
def init_users_db():
    conn = sqlite3.connect(AUTH_DB_FILE)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            password_hash TEXT,
            data_key_enc_by_pwd BLOB
        )
    """)
    conn.commit()
    conn.close()

init_users_db()

# =============================
#   AUTH FUNKCJE
# =============================
def check_login(username: str, password: str):
    conn = sqlite3.connect(AUTH_DB_FILE)
    cur = conn.cursor()
    cur.execute("SELECT password_hash, data_key_enc_by_pwd FROM users WHERE username=?", (username,))
    row = cur.fetchone()
    conn.close()

    if not row:
        return None

    stored_hash_hex, data_key_enc = row
    if isinstance(data_key_enc, memoryview):
        data_key_enc = data_key_enc.tobytes()

    # porównanie hashy z stałym czasem
    try:
        stored_hash = bytes.fromhex(stored_hash_hex) if isinstance(stored_hash_hex, str) else stored_hash_hex
    except ValueError:
        return None

    if not hmac.compare_digest(stored_hash, hash_password(password)):
        return None

    try:
        fernet = Fernet(derive_key(password))
        data_key = fernet.decrypt(data_key_enc)
        return data_key
    except (InvalidToken, TypeError):
        return None

def register_user(username: str, password: str) -> bool:
    """Tworzy nowego użytkownika: zapisuje hash hasła i szyfruje unikalny data_key hasłem."""
    conn = sqlite3.connect(AUTH_DB_FILE)
    cur = conn.cursor()
    try:
        pwd_hash_hex = hash_password(password).hex()
        data_key = Fernet.generate_key()
        fernet = Fernet(derive_key(password))
        data_key_enc = fernet.encrypt(data_key)

        cur.execute(
            "INSERT INTO users(username, password_hash, data_key_enc_by_pwd) VALUES (?,?,?)",
            (username, pwd_hash_hex, data_key_enc)
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()

def save_session_token(username: str, data_key: bytes):
    token = {
        "username": username,
        "data_key": base64.urlsafe_b64encode(data_key).decode(),
        "created_at": datetime.datetime.now().isoformat(timespec="seconds")
    }
    with open(SESSION_FILE, "w", encoding="utf-8") as f:
        json.dump(token, f, indent=2)

def clear_session_token():
    if os.path.exists(SESSION_FILE):
        os.remove(SESSION_FILE)

def load_session_username():
    if not os.path.exists(SESSION_FILE):
        return None
    try:
        with open(SESSION_FILE, "r", encoding="utf-8") as f:
            token = json.load(f)
        return token.get("username")
    except Exception:
        return None

# =============================
#   URUCHAMIANIE MODUŁÓW (.exe/.py) – FIX
# =============================
def _base_dir():
    """Katalog, gdzie leży panel.exe/panel.py – tam szukamy modułów."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

def run_module(target_name: str):
    """
    Uruchamia moduł:
      - jeśli panel jest skompilowany (sys.frozen) → tylko {name}.exe
      - jeśli dev (uruchomienie .py) → {name}.exe albo {name}.py
    """
    base = _base_dir()
    exe_path = os.path.join(base, f"{target_name}.exe")
    py_path  = os.path.join(base, f"{target_name}.py")

    # jeśli panel działa jako EXE
    if getattr(sys, "frozen", False):
        if os.path.exists(exe_path):
            try:
                subprocess.Popen([exe_path], cwd=base)
                return
            except Exception as e:
                messagebox.showerror("Błąd", f"Nie udało się uruchomić {target_name}.exe:\n{e}")
        else:
            messagebox.showerror("Błąd", f"Nie znaleziono {target_name}.exe w katalogu:\n{base}")
        return

    # tryb DEV (panel.py odpalony w interpreterze)
    if os.path.exists(exe_path):
        subprocess.Popen([exe_path], cwd=base)
        return
    elif os.path.exists(py_path):
        subprocess.Popen([sys.executable, py_path], cwd=base)
        return
    else:
        messagebox.showerror("Błąd", f"Nie znaleziono modułu {target_name}.exe ani {target_name}.py w:\n{base}")

# =============================
#   PANEL GŁÓWNY
# =============================
class MainPanel(tb.Toplevel):
    def __init__(self, username: str):
        super().__init__()
        self.title("AIR/IRZ++ – Panel główny")
        self.geometry("800x500")
        self.resizable(False, False)

        # tło
        try:
            bg = Image.open("Background.jpg")
            self.bg_tk = ImageTk.PhotoImage(bg.resize((800, 500)))
            bg_label = tk.Label(self, image=self.bg_tk)
            bg_label.place(x=0, y=0, relwidth=1, relheight=1)
        except Exception:
            self.configure(bg="#202020")

        # nagłówki
        tk.Label(self, text="KOLEKTYW", font=("Arial", 24, "bold"),
                 bg="#ffffff", fg="#2b8a3e").place(relx=0.5, rely=0.1, anchor="center")
        tk.Label(self, text=f"Witaj, {username}!", font=("Arial", 14),
                 bg="#ffffff", fg="#111111").place(relx=0.5, rely=0.18, anchor="center")
        tk.Label(self, text="Wybierz moduł:", font=("Arial", 12),
                 bg="#ffffff", fg="#333333").place(relx=0.5, rely=0.25, anchor="center")

        # przyciski modułów (uwaga: podajemy NAZWY BEZ rozszerzeń)
        tb.Button(self, text="Narzędzia", bootstyle="primary", width=18,
                  command=lambda: run_module("Na")).place(relx=0.3, rely=0.45, anchor="center")
        tb.Button(self, text="Dopłaty", bootstyle="success", width=18,
                  command=lambda: run_module("doplaty")).place(relx=0.7, rely=0.45, anchor="center")
        tb.Button(self, text="Uprawy", bootstyle="info", width=18,
                  command=lambda: run_module("uprawy")).place(relx=0.3, rely=0.65, anchor="center")
        tb.Button(self, text="IRZ++", bootstyle="danger", width=18,
                  command=lambda: run_module("T-1000")).place(relx=0.7, rely=0.65, anchor="center")

        # dolny pasek z opcjami
        tb.Button(self, text="Wyloguj", bootstyle="warning", width=12,
                  command=self.logout).place(relx=0.25, rely=0.9, anchor="center")
        tb.Button(self, text="O aplikacji", bootstyle="secondary", width=12,
                  command=self.about).place(relx=0.5, rely=0.9, anchor="center")
        tb.Button(self, text="Zamknij", bootstyle="danger", width=12,
                  command=self.quit_all).place(relx=0.75, rely=0.9, anchor="center")

    def logout(self):
        clear_session_token()
        messagebox.showinfo("Wylogowano", "Sesja zakończona.")
        self.destroy()
        LoginPanel().mainloop()

    def about(self):
        messagebox.showinfo("O aplikacji", "AIR/IRZ++ Kolektyw\nPanel główny z modułami\n© 2025 Feniks")

    def quit_all(self):
        self.destroy()
        sys.exit(0)

# =============================
#   PANEL REJESTRACJI
# =============================
class RegisterPanel(tb.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("AIR/IRZ++ – Rejestracja")
        self.geometry("400x260")
        self.resizable(False, False)

        card = tb.Frame(self, padding=20, style="Card.TFrame")
        card.place(relx=0.5, rely=0.5, anchor="center")

        tb.Label(card, text="Rejestracja nowego użytkownika", font="-size 12 -weight bold").grid(row=0, column=0, columnspan=2, pady=(0, 14))

        tb.Label(card, text="Użytkownik:").grid(row=1, column=0, sticky="e", padx=6, pady=6)
        tb.Label(card, text="Hasło:").grid(row=2, column=0, sticky="e", padx=6, pady=6)
        tb.Label(card, text="Powtórz hasło:").grid(row=3, column=0, sticky="e", padx=6, pady=6)

        self.username = tb.Entry(card, width=24)
        self.password1 = tb.Entry(card, show="*", width=24)
        self.password2 = tb.Entry(card, show="*", width=24)
        self.username.grid(row=1, column=1, pady=6)
        self.password1.grid(row=2, column=1, pady=6)
        self.password2.grid(row=3, column=1, pady=6)

        tb.Button(card, text="Zarejestruj", bootstyle="success", width=16,
                  command=self.register).grid(row=4, column=0, columnspan=2, pady=(12, 6))

    def register(self):
        username = self.username.get().strip()
        p1 = self.password1.get().strip()
        p2 = self.password2.get().strip()

        if not username or not p1 or not p2:
            messagebox.showerror("Błąd", "Uzupełnij wszystkie pola", parent=self)
        elif p1 != p2:
            messagebox.showerror("Błąd", "Hasła nie są identyczne", parent=self)
        else:
            if register_user(username, p1):
                messagebox.showinfo("Sukces", f"Utworzono użytkownika {username}", parent=self)
                self.destroy()
            else:
                messagebox.showerror("Błąd", "Użytkownik już istnieje", parent=self)

# =============================
#   PANEL LOGIN
# =============================
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
            self.withdraw()
            MainPanel(username).mainloop()
        else:
            messagebox.showerror("Błąd", "Nieprawidłowe dane logowania", parent=self)

    def open_register(self):
        RegisterPanel(self)

# =============================
#   MAIN
# =============================
if __name__ == "__main__":
#(Opcjonalnie) auto-logowanie z tokenu – odkomentuj, jeśli chcesz:
    u = load_session_username()
    if u:
        root = LoginPanel()
        root.withdraw()
        MainPanel(u).mainloop()
    else:
        app = LoginPanel()
        app.mainloop()
run_module