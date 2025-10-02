#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HUD-AWARYJNY v10 — AIR/IRZ++ (Feniks)
Tryby: Prosty (15 funkcji) / Zaawansowany (25+ funkcji)
- Panel boczny z przewijaniem
- Kolorowe przyciski, czarny tekst, pionowy pasek postępu po prawej
- Każda funkcja: wybór użytkownika (co / skąd / dokąd / które)
- Pobieranie plików z GitHub Release (checkboxy) + rozpakowanie ZIP
- Uruchamianie i zatrzymywanie procesów z wybranego folderu
- Kopia/przywracanie KozyManager (AppData <-> wybór)
- Raport TXT (pełny log) na Pulpicie

Autor: Feniks x ChatGPT (2025)
"""

import os, sys, socket, shutil, subprocess, time, threading, tempfile, platform, zipfile, json, webbrowser, hashlib
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from urllib import request, error
from datetime import datetime

APP_NAME = "HUD-AWARYJNY v10"
DEFAULT_OWNER_REPO = "Feniks833/Irzplusplus"  # ustaw własne repo jeśli trzeba
UA = {"User-Agent": "HUD-Awaryjny/1.0 (+Feniks)"}

# =========================================================
#                         UTILS
# =========================================================

def run_cmd(cmd, timeout=12, shell=True):
    """Uruchom komendę i zwróć (rc, out). Nie rzuca wyjątku."""
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True, timeout=timeout, shell=shell)
        return 0, out
    except subprocess.CalledProcessError as e:
        return e.returncode, (e.output or "").strip()
    except Exception as e:
        return 1, f"Błąd uruchomienia: {e}"

def http_get(url: str, timeout=10) -> tuple[int, str | None]:
    req = request.Request(url, headers=UA)
    try:
        with request.urlopen(req, timeout=timeout) as r:
            return r.getcode(), r.read().decode("utf-8", errors="replace")
    except error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            body = None
        return e.code, body
    except Exception:
        return 0, None

def http_stream(url: str, timeout=30, chunk=1024*256):
    req = request.Request(url, headers=UA)
    resp = request.urlopen(req, timeout=timeout)
    total = int(resp.headers.get("Content-Length") or 0)
    read = 0
    while True:
        buf = resp.read(chunk)
        if not buf: break
        read += len(buf)
        yield buf, read, total

def tcp_check(host: str, port: int, timeout: int = 4) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False

def dns_lookup(host: str) -> str | None:
    try:
        return socket.gethostbyname(host)
    except Exception:
        return None

def human_bytes(n: int) -> str:
    v = float(n)
    for unit in ["B","KB","MB","GB","TB","PB"]:
        if v < 1024.0: return f"{v:.1f} {unit}"
        v /= 1024.0
    return f"{v:.1f} EB"

def disk_free(path: str) -> str:
    try:
        st = shutil.disk_usage(path)
        return human_bytes(st.free)
    except Exception:
        return "?"

def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024*1024), b""):
            h.update(chunk)
    return h.hexdigest()

def get_appdata():
    return os.environ.get("APPDATA") or os.path.expanduser("~")

def ensure_dir(p):
    os.makedirs(p, exist_ok=True)
    return p

def write_text(path, text):
    ensure_dir(os.path.dirname(path))
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)

def fetch_latest_release_assets(owner_repo: str) -> list[dict]:
    code, body = http_get(f"https://api.github.com/repos/{owner_repo}/releases/latest")
    if code != 200 or not body: return []
    try:
        data = json.loads(body)
        return data.get("assets") or []
    except Exception:
        return []

def download_to(url: str, dest_dir: str, progress_cb=None) -> str:
    ensure_dir(dest_dir)
    name = url.split("/")[-1] or "plik.bin"
    dest = os.path.join(dest_dir, name)
    read_prev = 0
    with open(dest, "wb") as f:
        for chunk, read, total in http_stream(url, timeout=60):
            f.write(chunk)
            if total > 0 and progress_cb:
                pct = int(read * 100 / total)
                if pct != read_prev:
                    progress_cb(pct)
                    read_prev = pct
    return dest

def unzip_file(zip_path: str, dest_dir: str):
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(dest_dir)

def find_in_tree(root_dir: str, exts=(".exe",)) -> list[str]:
    hits = []
    for base, _, files in os.walk(root_dir):
        for n in files:
            if n.lower().endswith(exts):
                hits.append(os.path.join(base, n))
    return hits

def safe_terminate(p: subprocess.Popen):
    try:
        if p.poll() is None:
            p.terminate()
            try: p.wait(timeout=3)
            except Exception: p.kill()
    except Exception:
        pass

# =========================================================
#                      UI: SCROLLABLE PANEL
# =========================================================

class ScrollPanel(tk.Frame):
    """Boczny panel z przewijaniem (scroll)."""
    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)
        self.canvas = tk.Canvas(self, bg="#dce7ec", highlightthickness=0)
        self.scroll = tk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas)

        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.create_window((0,0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scroll.set)

        self.canvas.pack(side="left", fill="both", expand=True)
        self.scroll.pack(side="right", fill="y")

# =========================================================
#                        GŁÓWNA APLIKACJA
# =========================================================

class HUDApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.mode = tk.StringVar(value="simple")  # simple / advanced
        self.owner_repo = tk.StringVar(value=DEFAULT_OWNER_REPO)
        self.started: dict[str, subprocess.Popen] = {}
        self.results: dict[str, bool] = {}
        self.log_lines: list[str] = []
        self._build_ui()

    # -------------------- BUILD UI --------------------
    def _build_ui(self):
        self.root.title(APP_NAME)
        self.root.geometry("1480x900")
        self.root.configure(bg="#0f1116")

        # style
        s = ttk.Style()
        try: s.theme_use("clam")
        except Exception: pass
        neon = "#00ffff"
        s.configure("Title.TLabel", font=("Segoe UI", 13, "bold"))
        s.configure("Head.TLabel", background="#b0c4de", foreground="black", font=("Segoe UI", 11, "bold"))
        s.configure("Sidebar.TButton", font=("Segoe UI", 10, "bold"), foreground="black", padding=6)
        s.map("Sidebar.TButton", background=[("active", "#00d2d2"), ("!disabled", neon)])
        s.configure("Neon.Vertical.TProgressbar", troughcolor="#dfeef5", background=neon)

        # topbar
        top = ttk.Frame(self.root); top.pack(fill="x", pady=(8,4))
        ttk.Label(top, text=APP_NAME, style="Title.TLabel").pack(side="left", padx=8)
        ttk.Label(top, text="Tryb:", padding=(10,0)).pack(side="left")
        ttk.Radiobutton(top, text="Prosty (15)", variable=self.mode, value="simple", command=self._rebuild_sidebar).pack(side="left", padx=6)
        ttk.Radiobutton(top, text="Zaawansowany (25+)", variable=self.mode, value="advanced", command=self._rebuild_sidebar).pack(side="left", padx=6)
        ttk.Label(top, text="  Repo (owner/repo):").pack(side="left", padx=(20,6))
        ttk.Entry(top, textvariable=self.owner_repo, width=28).pack(side="left")
        ttk.Button(top, text="Zmień repo", command=self._confirm_repo).pack(side="left", padx=6)

        # main layout
        body = ttk.Frame(self.root); body.pack(fill="both", expand=True)
        # sidebar scroll
        self.sidebar = ScrollPanel(body)
        self.sidebar.pack(side="left", fill="y", padx=(0,4))
        # center output
        center = ttk.Frame(body); center.pack(side="left", fill="both", expand=True)
        self.out = tk.Text(center, bg="white", fg="black", wrap="word")
        self.out.pack(fill="both", expand=True, padx=12, pady=12)
        # right progress
        right = ttk.Frame(body); right.pack(side="left", fill="y")
        self.progress_var = tk.IntVar(value=0)
        self.pb = ttk.Progressbar(right, orient="vertical", length=520, mode="determinate",
                                  maximum=100, variable=self.progress_var, style="Neon.Vertical.TProgressbar")
        self.pb.pack(padx=10, pady=10)

        # bottom bar
        bottom = ttk.Frame(self.root); bottom.pack(fill="x")
        ttk.Button(bottom, text="Zapisz RAPORT (TXT)", command=self._save_report).pack(side="right", padx=8, pady=6)
        ttk.Button(bottom, text="Wyczyść log", command=lambda: self._set_text("")).pack(side="right", padx=8)

        self._rebuild_sidebar()

    def _confirm_repo(self):
        messagebox.showinfo("Repo", f"Ustawiono repo: {self.owner_repo.get()}")

    def _rebuild_sidebar(self):
        for w in self.sidebar.inner.winfo_children():
            w.destroy()

        self._add_head("🔍 Diagnostyka")
        self._btn("Sprawdź Internet", self.ui_check_internet)
        self._btn("Ping hostów (wybór)", self.ui_ping_hosts)
        self._btn("Sprawdź DNS (wybór)", self.ui_check_dns)
        self._btn("Połączenia sieci (netstat)", self.ui_netstat)
        self._btn("Szybka diagnostyka (CPU/RAM/dysk)", self.ui_quick_sys)

        self._add_head("📦 Pliki i kopie")
        self._btn("Sprawdź miejsce na dysku", self.ui_disk_free)
        self._btn("Test zapisu w folderze", self.ui_folder_rw)
        self._btn("Sprawdź/rozpakuj ZIP", self.ui_check_unzip)
        self._btn("Kopia KozyManager → wybrany", self.ui_backup_kozy)
        self._btn("PRZYWRÓĆ KozyManager ← wybrany", self.ui_restore_kozy)
        self._btn("Wyczyść TEMP", self.ui_clear_temp)

        self._add_head("🐑 Moduły AIR")
        self._btn("Uruchom Panel.exe (wskaż)", self.ui_launch_panel)
        self._btn("Uruchom WYBRANE .exe w folderze", self.ui_start_many)
        self._btn("Zatrzymaj WYBRANE .exe w folderze", self.ui_stop_many)

        self._add_head("⬇️ Repozytorium / Aktualizacje")
        self._btn("Wybierz pliki z GitHub (Release)", self.ui_choose_assets)
        self._btn("Skanuj pobrane i uruchom Panel.exe", self.ui_scan_and_run_panel)
        self._btn("Weryfikacja SHA256 plików", self.ui_sha256_verify)

        self._add_head("🛠 Dodatkowe")
        self._btn("Tryb awaryjny AIR (pakiet testów)", self.ui_air_safemode)
        self._btn("Sprawdź zależności .dll (folder)", self.ui_check_dll_missing)
        self._btn("Pokaż procesy AIR", self.ui_show_air_procs)
        self._btn("Przenieś folder AIR (wybór→wybór)", self.ui_move_air)
        self._btn("Zapisz pełny log i otwórz", self._save_report_and_open)

        if self.mode.get() == "advanced":
            self._add_head("🚀 Zaawansowane PLUS")
            self._btn("Sprawdź AppData\\KozyManager", self.ui_check_appdata_kozy)
            self._btn("Uprawnienia do folderu (wybór)", self.ui_check_perms)
            self._btn("Lista interfejsów sieciowych", self.ui_list_ifaces)
            self._btn("Szybki test ARiMR + GitHub", self.ui_quick_web_pair)
            self._btn("Skany: wszystko naraz", self.ui_full_scans)

    def _add_head(self, title: str):
        lbl = tk.Label(self.sidebar.inner, text=title, bg="#b0c4de", fg="black", font=("Segoe UI", 11, "bold"))
        lbl.pack(fill="x", pady=(8,2), padx=6)

    def _btn(self, text: str, cmd):
        b = tk.Button(self.sidebar.inner, text=text, command=lambda: self._run_with_progress(cmd),
                      bg="#00ffff", fg="black", font=("Segoe UI", 10, "bold"))
        b.pack(fill="x", pady=2, padx=8)

    # -------------------- LOG / OUTPUT --------------------
    def _append(self, msg: str):
        line = f"{time.strftime('%H:%M:%S')} {msg}"
        self.log_lines.append(line)
        self.out.insert("end", line + "\n")
        self.out.see("end")

    def _set_text(self, txt: str):
        self.out.delete("1.0", "end")
        if txt: self.out.insert("end", txt)

    def _run_with_progress(self, fn):
        def worker():
            self.progress_var.set(0)
            try:
                fn()
            finally:
                self.progress_var.set(100)
        threading.Thread(target=worker, daemon=True).start()

    # =========================================================
    #                      OKIENKA WYBORU
    # =========================================================
    def ui_check_internet(self):
        win = tk.Toplevel(self.root); win.title("Internet — wybór testów")
        urls = {
            "GitHub (https://github.com)": tk.BooleanVar(value=True),
            "Google (https://google.com)": tk.BooleanVar(value=False),
            "ARiMR (https://www.arimr.gov.pl/)": tk.BooleanVar(value=False),
        }
        for k,v in urls.items(): tk.Checkbutton(win, text=k, variable=v).pack(anchor="w")
        custom = tk.StringVar(value="")
        tk.Label(win, text="Własny adres (opcjonalnie):").pack(anchor="w", pady=(6,2))
        tk.Entry(win, textvariable=custom, width=48).pack(anchor="w")

        def go():
            to_check = []
            for k,v in urls.items():
                if v.get():
                    to_check.append(k.split("(")[1].split(")")[0])
            if custom.get().strip():
                u = custom.get().strip()
                if not u.startswith("http"): u = "https://" + u
                to_check.append(u)
            self._progress_task("Sprawdzenie internetu", lambda: self._do_check_urls(to_check), key="Internet")
            win.destroy()
        tk.Button(win, text="Sprawdź", command=go).pack(pady=8)

    def ui_ping_hosts(self):
        win = tk.Toplevel(self.root); win.title("Ping — wybierz hosty")
        entries: list[tk.Entry] = []
        def add_row(val):
            v = tk.StringVar(value=val)
            e = tk.Entry(win, textvariable=v, width=30); e.pack(anchor="w", pady=2); entries.append(e)
        for x in ["8.8.8.8", "github.com", "arimr.gov.pl"]:
            add_row(x)
        tk.Button(win, text="Dodaj wiersz", command=lambda: add_row("")).pack(pady=4)
        def go():
            hosts = [e.get().strip() for e in entries if e.get().strip()]
            self._progress_task("Ping hostów", lambda: self._do_ping(hosts), key="Ping")
            win.destroy()
        tk.Button(win, text="Start", command=go).pack(pady=6)

    def ui_check_dns(self):
        win = tk.Toplevel(self.root); win.title("DNS — wybór nazw")
        names: list[tk.Entry] = []
        def add_row(v):
            sv = tk.StringVar(value=v)
            e = tk.Entry(win, textvariable=sv, width=32); e.pack(anchor="w", pady=2); names.append(e)
        for x in ["google.com", "github.com", "arimr.gov.pl"]:
            add_row(x)
        tk.Button(win, text="Dodaj nazwę", command=lambda: add_row("")).pack()
        def go():
            qs = [e.get().strip() for e in names if e.get().strip()]
            self._progress_task("DNS", lambda: self._do_dns(qs), key="DNS")
            win.destroy()
        tk.Button(win, text="Sprawdź", command=go).pack(pady=6)

    def ui_netstat(self):
        self._progress_task("Połączenia sieciowe", self._do_netstat, key="Netstat")

    def ui_quick_sys(self):
        self._progress_task("Szybka diagnostyka", self._do_quick_sys, key="Szybka diagnostyka")

    def ui_disk_free(self):
        win = tk.Toplevel(self.root); win.title("Dysk — wybierz lokalizację")
        where = tk.StringVar(value=os.path.expanduser("~"))
        tk.Entry(win, textvariable=where, width=56).pack(side="left", padx=(0,6), pady=6)
        tk.Button(win, text="Wybierz…", command=lambda: self._pick_dir(where)).pack(side="left")
        def go():
            self._progress_task("Dysk", lambda: self._do_disk(where.get()), key="Dysk")
            win.destroy()
        tk.Button(win, text="Sprawdź", command=go).pack(pady=6)

    def ui_folder_rw(self):
        win = tk.Toplevel(self.root); win.title("Test zapisu/odczytu — wybierz folder")
        folder = tk.StringVar(value=os.path.expanduser("~"))
        tk.Entry(win, textvariable=folder, width=56).pack(side="left", padx=(0,6), pady=6)
        tk.Button(win, text="Wybierz…", command=lambda: self._pick_dir(folder)).pack(side="left")
        def go():
            self._progress_task("Test folderu", lambda: self._do_folder_rw(folder.get()), key="Folder")
            win.destroy()
        tk.Button(win, text="Testuj", command=go).pack(pady=6)

    def ui_check_unzip(self):
        win = tk.Toplevel(self.root); win.title("ZIP — plik i folder docelowy")
        zip_path = tk.StringVar(value="")
        dest_dir = tk.StringVar(value=os.path.join(os.path.expanduser("~"), "Desktop", "ZIP_test"))
        row1 = ttk.Frame(win); row1.pack(fill="x", pady=2)
        tk.Entry(row1, textvariable=zip_path, width=56).pack(side="left", padx=(0,6))
        tk.Button(row1, text="Wybierz ZIP…", command=lambda: self._pick_file(zip_path, [("ZIP", "*.zip")])).pack(side="left")
        row2 = ttk.Frame(win); row2.pack(fill="x", pady=2)
        tk.Entry(row2, textvariable=dest_dir, width=56).pack(side="left", padx=(0,6))
        tk.Button(row2, text="Folder…", command=lambda: self._pick_dir(dest_dir)).pack(side="left")
        def go():
            self._progress_task("Test ZIP", lambda: self._do_unzip(zip_path.get(), dest_dir.get()), key="ZIP")
            win.destroy()
        tk.Button(win, text="Rozpakuj", command=go).pack(pady=6)

    def ui_backup_kozy(self):
        win = tk.Toplevel(self.root); win.title("Kopia KozyManager (źródło→cel)")
        appdata = get_appdata()
        src = tk.StringVar(value=os.path.join(appdata, "KozyManager"))
        dst = tk.StringVar(value=os.path.join(os.path.expanduser("~"), "Desktop", "KozyManager_backup"))
        self._row_pick(win, "Źródło:", src, pick_dir=True)
        self._row_pick(win, "Cel:", dst, pick_dir=True)
        def go():
            self._progress_task("Kopia Kozy", lambda: self._do_copy(src.get(), dst.get()), key="Backup Kozy")
            win.destroy()
        tk.Button(win, text="Kopiuj", command=go).pack(pady=6)

    def ui_restore_kozy(self):
        win = tk.Toplevel(self.root); win.title("PRZYWRÓĆ KozyManager (źródło→AppData)")
        src = tk.StringVar(value=os.path.join(os.path.expanduser("~"), "Desktop", "KozyManager_backup"))
        self._row_pick(win, "Skąd:", src, pick_dir=True)
        def go():
            self._progress_task("Przywracanie Kozy", lambda: self._do_restore_kozy(src.get()), key="Restore Kozy")
            win.destroy()
        tk.Button(win, text="Przywróć", command=go).pack(pady=6)

    def ui_clear_temp(self):
        self._progress_task("Czyszczenie TEMP", self._do_clear_temp, key="TEMP")

    def ui_launch_panel(self):
        exe = tk.StringVar(value="")
        win = tk.Toplevel(self.root); win.title("Uruchom Panel.exe")
        self._row_pick(win, "Plik .exe:", exe, pick_file=[("Program", "*.exe"), ("Wszystkie", "*.*")])
        def go():
            self._progress_task("Panel", lambda: self._do_launch(exe.get()), key="Panel")
            win.destroy()
        tk.Button(win, text="Start", command=go).pack(pady=6)

    def ui_start_many(self):
        self._ui_many_exec(title="Uruchom WYBRANE .exe", action="start")

    def ui_stop_many(self):
        self._ui_many_exec(title="Zatrzymaj WYBRANE .exe", action="stop")

    def _ui_many_exec(self, title: str, action: str):
        win = tk.Toplevel(self.root); win.title(title)
        folder = tk.StringVar(value=os.path.expanduser("~"))
        self._row_pick(win, "Folder:", folder, pick_dir=True)

        box = tk.Frame(win); box.pack(fill="both", expand=True, pady=(4,0))
        vars_map: dict[str, tk.BooleanVar] = {}

        def scan():
            for w in box.winfo_children(): w.destroy()
            if os.path.isdir(folder.get()):
                exes = [os.path.join(folder.get(), n) for n in os.listdir(folder.get()) if n.lower().endswith(".exe")]
            else:
                exes = []
            if not exes:
                tk.Label(box, text="Brak .exe w folderze").pack(anchor="w")
            for p in exes:
                v = tk.BooleanVar(value=True)
                tk.Checkbutton(box, text=os.path.basename(p), variable=v).pack(anchor="w")
                vars_map[p] = v

        tk.Button(win, text="Skanuj folder", command=scan).pack(pady=6)
        def go():
            chosen = [p for p,v in vars_map.items() if v.get()]
            if not chosen:
                messagebox.showinfo("Info", "Nic nie zaznaczono."); return
            if action == "start":
                self._progress_task("Start wielu", lambda: self._do_start_many(chosen), key="StartMany")
            else:
                self._progress_task("Stop wielu", lambda: self._do_stop_many(chosen), key="StopMany")
            win.destroy()
        tk.Button(win, text="Wykonaj", command=go).pack(pady=6)

    def ui_choose_assets(self):
        win = tk.Toplevel(self.root); win.title("Wybierz pliki z GitHub (Release)")
        assets = fetch_latest_release_assets(self.owner_repo.get())
        if not assets:
            tk.Label(win, text="Brak listy plików (sprawdź internet/repo)").pack(pady=6)
            return
        vars_map: dict[int, tuple[tk.BooleanVar, dict]] = {}
        for a in assets:
            var = tk.BooleanVar(value=False)
            size = human_bytes(a.get("size",0))
            text = f"{a.get('name','(bez nazwy)')} [{size}]"
            tk.Checkbutton(win, text=text, variable=var).pack(anchor="w")
            vars_map[a.get("id", id(a))] = (var, a)
        dest = tk.StringVar(value=os.path.join(os.path.expanduser("~"), "Desktop", "AIR_downloads"))
        auto_unzip = tk.BooleanVar(value=True)
        row = ttk.Frame(win); row.pack(fill="x", pady=4)
        tk.Entry(row, textvariable=dest, width=56).pack(side="left", padx=(0,6))
        tk.Button(row, text="Folder…", command=lambda: self._pick_dir(dest)).pack(side="left")
        tk.Checkbutton(win, text="Po pobraniu rozpakuj pliki ZIP", variable=auto_unzip).pack(anchor="w")

        def go():
            chosen = [a for _id,(v,a) in vars_map.items() if v.get()]
            if not chosen:
                messagebox.showinfo("Info", "Nic nie zaznaczono."); return
            def worker():
                for a in chosen:
                    url = a.get("browser_download_url")
                    name = a.get("name","plik.bin")
                    self._append(f"Pobieranie: {name}")
                    path = download_to(url, dest.get(), progress_cb=self.progress_var.set)
                    self._append(f"Zapisano: {path}")
                    if auto_unzip.get() and path.lower().endswith(".zip"):
                        sub = os.path.join(dest.get(), os.path.splitext(os.path.basename(path))[0])
                        ensure_dir(sub)
                        unzip_file(path, sub)
                        self._append(f"Rozpakowano do: {sub}")
                self.results["Pobieranie"] = True
                self.progress_var.set(100)
            threading.Thread(target=worker, daemon=True).start()
            win.destroy()
        tk.Button(win, text="Pobierz zaznaczone", command=go).pack(pady=8)

    def ui_scan_and_run_panel(self):
        win = tk.Toplevel(self.root); win.title("Skanuj pobrane i uruchom Panel.exe")
        where = tk.StringVar(value=os.path.join(os.path.expanduser("~"), "Desktop", "AIR_downloads"))
        self._row_pick(win, "Szukaj w:", where, pick_dir=True)
        def go():
            def worker():
                exes = find_in_tree(where.get(), (".exe",))
                panel = [p for p in exes if os.path.basename(p).lower()=="panel.exe"]
                self._append(f"Znaleziono .exe: {len(exes)}  •  Panel.exe: {len(panel)}")
                if panel:
                    if messagebox.askyesno("Panel", f"Znaleziono Panel.exe:\n{panel[0]}\nUruchomić?"):
                        self._do_launch(panel[0])
                else:
                    self._append("Nie znaleziono Panel.exe")
            self._run_with_progress(worker)
            win.destroy()
        tk.Button(win, text="Skanuj i pytaj", command=go).pack(pady=8)

    def ui_sha256_verify(self):
        win = tk.Toplevel(self.root); win.title("Weryfikacja SHA256 plików")
        folder = tk.StringVar(value=os.path.join(os.path.expanduser("~"), "Desktop", "AIR_downloads"))
        self._row_pick(win, "Folder:", folder, pick_dir=True)
        patterns = tk.StringVar(value=".exe,.zip")
        tk.Label(win, text="Rozszerzenia (np. .exe,.zip):").pack(anchor="w")
        tk.Entry(win, textvariable=patterns, width=40).pack(anchor="w")
        def go():
            def worker():
                exts = tuple([x.strip().lower() for x in patterns.get().split(",") if x.strip()])
                if not exts: exts = (".exe",".zip")
                hits = find_in_tree(folder.get(), exts)
                if not hits:
                    self._append("Brak plików do sumy SHA256.")
                for p in hits:
                    try:
                        digest = sha256_file(p)
                        self._append(f"SHA256 {os.path.basename(p)} = {digest}")
                    except Exception as e:
                        self._append(f"SHA256 błąd {p}: {e}")
            self._run_with_progress(worker)
            win.destroy()
        tk.Button(win, text="Oblicz", command=go).pack(pady=6)

    def ui_air_safemode(self):
        def worker():
            self._do_check_urls(["https://github.com"])
            self._do_quick_sys()
            self._do_disk(os.path.expanduser("~"))
        self._run_with_progress(worker)

    def ui_check_dll_missing(self):
        win = tk.Toplevel(self.root); win.title("Sprawdź brakujące .dll (folder)")
        folder = tk.StringVar(value=os.path.expanduser("~"))
        self._row_pick(win, "Folder:", folder, pick_dir=True)
        def go():
            def worker():
                exes = find_in_tree(folder.get(), (".exe",))
                if not exes: 
                    self._append("Brak .exe w folderze.")
                    return
                self._append(f"Znalezione .exe: {len(exes)}")
                self._append("Uwaga: szczegółowa analiza .dll wymaga narzędzi zewnętrznych (Dependency Walker / dumpbin).")
                for p in exes[:20]:
                    self._append(f"- {os.path.basename(p)}")
            self._run_with_progress(worker)
            win.destroy()
        tk.Button(win, text="Skanuj", command=go).pack(pady=6)

    def ui_show_air_procs(self):
        def worker():
            if os.name=="nt":
                rc, out = run_cmd("tasklist")
            else:
                rc, out = run_cmd("ps aux")
            self._append(out[:100000] if out else "(brak)")
        self._run_with_progress(worker)

    def ui_move_air(self):
        win = tk.Toplevel(self.root); win.title("Przenieś AIR")
        src = tk.StringVar(value=os.path.join(os.path.expanduser("~"), "Desktop", "AIR_downloads"))
        dst = tk.StringVar(value=os.path.join(os.path.expanduser("~"), "Desktop", "AIR_moved"))
        self._row_pick(win, "Skąd:", src, pick_dir=True)
        self._row_pick(win, "Dokąd:", dst, pick_dir=True)
        def go():
            def worker():
                if not os.path.isdir(src.get()):
                    self._append("Źródło nie istnieje.")
                    return
                if os.path.abspath(src.get()) == os.path.abspath(dst.get()):
                    self._append("Źródło i cel są takie same.")
                    return
                if os.path.exists(dst.get()):
                    shutil.rmtree(dst.get())
                shutil.copytree(src.get(), dst.get())
                self._append(f"Przeniesiono: {src.get()} → {dst.get()}")
            self._run_with_progress(worker)
            win.destroy()
        tk.Button(win, text="Przenieś", command=go).pack(pady=6)

    def _save_report_and_open(self):
        self._save_report()
        # Otwórz w notatniku
        path = self._last_report_path
        if path and os.path.exists(path):
            if os.name=="nt":
                os.startfile(path)  # type: ignore
            else:
                subprocess.Popen(["xdg-open", path])

    def ui_check_appdata_kozy(self):
        def worker():
            path = os.path.join(get_appdata(), "KozyManager")
            if os.path.isdir(path):
                files = sum(len(files) for _,_,files in os.walk(path))
                self._append(f"KozyManager istnieje w AppData: plików={files}")
            else:
                self._append("Brak KozyManager w AppData.")
        self._run_with_progress(worker)

    def ui_check_perms(self):
        win = tk.Toplevel(self.root); win.title("Uprawnienia do folderu")
        folder = tk.StringVar(value=os.path.expanduser("~"))
        self._row_pick(win, "Folder:", folder, pick_dir=True)
        def go():
            path = folder.get()
            writable = os.access(path, os.W_OK)
            readable = os.access(path, os.R_OK)
            execable = os.access(path, os.X_OK)
            self._append(f"Uprawnienia [{path}]  R:{readable}  W:{writable}  X:{execable}")
            win.destroy()
        tk.Button(win, text="Sprawdź", command=go).pack(pady=6)

    def ui_list_ifaces(self):
        def worker():
            if os.name=="nt":
                rc, out = run_cmd("ipconfig /all")
            else:
                rc, out = run_cmd("ip a")
            self._append(out[:100000] if out else "(brak)")
        self._run_with_progress(worker)

    def ui_quick_web_pair(self):
        def worker():
            self._do_check_urls(["https://www.arimr.gov.pl/", "https://github.com/"])
        self._run_with_progress(worker)

    def ui_full_scans(self):
        def worker():
            self._do_check_urls(["https://github.com", "https://google.com"])
            self._do_dns(["github.com", "google.com"])
            self._do_netstat()
            self._do_quick_sys()
            self._do_disk(os.path.expanduser("~"))
        self._run_with_progress(worker)

    # =========================================================
    #                   IMPLEMENTACJE DZIAŁAŃ
    # =========================================================
    def _do_check_urls(self, urls: list[str]) -> str:
        if not urls: return "Brak adresów do sprawdzenia"
        ok = 0
        for u in urls:
            if not u.startswith("http"): u = "https://" + u
            code, _ = http_get(u, timeout=8)
            self._append(f"{u} → {code if code else 'brak'} {'OK' if code and 200<=code<500 else 'Błąd'}")
            if code and 200<=code<500: ok += 1
        self.results["Internet"] = ok == len(urls)
        return f"Sprawdzono {len(urls)} adresów, OK: {ok}"

    def _do_ping(self, hosts: list[str]) -> str:
        if not hosts: return "Brak hostów do pingowania"
        total_ok = 0
        for h in hosts:
            cmd = f"ping -n 2 {h}" if os.name=="nt" else f"ping -c 2 {h}"
            rc, out = run_cmd(cmd, timeout=8)
            self._append(out or f"{h}: (brak)")
            if rc == 0: total_ok += 1
        self.results["Ping"] = total_ok == len(hosts)
        return f"Zakończono ping: OK {total_ok}/{len(hosts)}"

    def _do_dns(self, names: list[str]) -> str:
        if not names: return "Brak nazw do DNS"
        ok = 0
        for n in names:
            ip = dns_lookup(n)
            self._append(f"DNS {n} → {ip or 'BŁĄD'}")
            if ip: ok += 1
        self.results["DNS"] = ok == len(names)
        return f"DNS OK: {ok}/{len(names)}"

    def _do_netstat(self) -> str:
        if os.name=="nt":
            rc, out = run_cmd("netstat -ano")
        else:
            rc, out = run_cmd("netstat -an")
        self._append(out[:100000] if out else "(brak)")
        self.results["Netstat"] = True
        return "netstat ok"

    def _do_quick_sys(self) -> str:
        # CPU
        cpus = os.cpu_count() or 1
        # RAM (przybliżenie bez zewnętrznych pakietów)
        ram = None
        try:
            if hasattr(os, "sysconf"):
                ram = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        except Exception:
            pass
        self._append(f"CPU logiczne: {cpus}")
        self._append(f"Pamięć RAM: {human_bytes(ram) if ram else '?'}")
        self.results["Szybka diagnostyka"] = True
        return "Szybka diagnostyka OK"

    def _do_disk(self, path: str) -> str:
        if not path: return "Nie wskazano lokalizacji"
        free = disk_free(path)
        self._append(f"Wolne miejsce [{path}]: {free}")
        self.results["Dysk"] = True
        return "Dysk OK"

    def _do_folder_rw(self, path: str) -> str:
        if not path: return "Nie wskazano folderu"
        ensure_dir(path)
        tmp = os.path.join(path, "._hud_test.tmp")
        try:
            with open(tmp, "wb") as f: f.write(b"test")
            os.remove(tmp)
            self._append(f"Test zapisu/odczytu OK: {path}")
            self.results["Folder"] = True
            return "Folder test OK"
        except Exception as e:
            self._append(f"Test folderu BŁĄD: {e}")
            self.results["Folder"] = False
            return "Folder test BŁĄD"

    def _do_unzip(self, zip_path: str, dest: str) -> str:
        if not zip_path or not os.path.isfile(zip_path): return "Wskaż ZIP"
        ensure_dir(dest)
        unzip_file(zip_path, dest)
        self._append(f"Rozpakowano do: {dest}")
        self.results["ZIP"] = True
        return "ZIP OK"

    def _do_copy(self, src: str, dst: str) -> str:
        if not os.path.isdir(src): return "Źródło nie istnieje"
        if os.path.exists(dst): shutil.rmtree(dst)
        shutil.copytree(src, dst)
        self._append(f"Kopia: {src} → {dst}")
        self.results["Backup Kozy"] = True
        return "Backup OK"

    def _do_restore_kozy(self, src: str) -> str:
        if not os.path.isdir(src): return "Źródło nie istnieje"
        dst = os.path.join(get_appdata(), "KozyManager")
        if os.path.exists(dst): shutil.rmtree(dst)
        shutil.copytree(src, dst)
        self._append(f"Przywrócono do: {dst}")
        self.results["Restore Kozy"] = True
        return "Restore OK"

    def _do_clear_temp(self) -> str:
        tmp = tempfile.gettempdir()
        try:
            # bezpieczniej: usuwaj wnętrze, nie sam katalog
            for name in os.listdir(tmp):
                p = os.path.join(tmp, name)
                try:
                    if os.path.isdir(p): shutil.rmtree(p, ignore_errors=True)
                    else: os.remove(p)
                except Exception: pass
            self._append("TEMP wyczyszczony")
            self.results["TEMP"] = True
            return "TEMP OK"
        except Exception as e:
            self._append(f"TEMP błąd: {e}")
            self.results["TEMP"] = False
            return "TEMP BŁĄD"

    def _do_launch(self, exe_path: str) -> str:
        if not exe_path or not os.path.isfile(exe_path): return "Wskaż poprawny .exe"
        p = subprocess.Popen([exe_path], cwd=os.path.dirname(exe_path))
        self.started[exe_path] = p
        self._append(f"Start: {os.path.basename(exe_path)} (PID {p.pid})")
        self.results["Start EXE"] = True
        return "Start OK"

    def _do_start_many(self, exe_list: list[str]) -> str:
        cnt = 0
        for pth in exe_list:
            if os.path.isfile(pth):
                try:
                    p = subprocess.Popen([pth], cwd=os.path.dirname(pth))
                    self.started[pth] = p
                    cnt += 1
                    self._append(f"Start: {os.path.basename(pth)} (PID {p.pid})")
                except Exception as e:
                    self._append(f"Start błąd {pth}: {e}")
        self.results["StartMany"] = cnt == len(exe_list)
        return f"Uruchomiono {cnt}/{len(exe_list)}"

    def _do_stop_many(self, exe_list: list[str]) -> str:
        cnt = 0
        for pth in exe_list:
            p = self.started.get(pth)
            if p and p.poll() is None:
                safe_terminate(p); cnt += 1
                self._append(f"Stop: {os.path.basename(pth)}")
        self.results["StopMany"] = True
        return f"Zatrzymano {cnt}"

    # =========================================================
    #                          HELPERS
    # =========================================================
    def _row_pick(self, win, label, var: tk.StringVar, pick_dir=False, pick_file=None):
        r = ttk.Frame(win); r.pack(fill="x", pady=2)
        ttk.Label(r, text=label, width=12).pack(side="left")
        tk.Entry(r, textvariable=var, width=56).pack(side="left", padx=(6,6))
        if pick_dir:
            tk.Button(r, text="Wybierz…", command=lambda: self._pick_dir(var)).pack(side="left")
        elif pick_file:
            tk.Button(r, text="Wybierz…", command=lambda: self._pick_file(var, pick_file)).pack(side="left")

    def _pick_dir(self, var: tk.StringVar):
        d = filedialog.askdirectory()
        if d: var.set(d)

    def _pick_file(self, var: tk.StringVar, types):
        p = filedialog.askopenfilename(filetypes=types)
        if p: var.set(p)

    def _progress_task(self, label: str, fn, key=None):
        def worker():
            self.progress_var.set(0)
            try:
                res = fn()
                if isinstance(res, str) and res:
                    self._append(f"✅ {res}")
                else:
                    self._append(f"✅ {label} zakończone")
                if key: self.results[key] = True
            except Exception as e:
                self._append(f"❌ {label}: {e}")
                if key: self.results[key] = False
            self.progress_var.set(100)
        threading.Thread(target=worker, daemon=True).start()

    # =========================================================
    #                          RAPORT
    # =========================================================
    def _save_report(self):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = os.path.join(os.path.expanduser("~"), "Desktop", f"HUD_raport_{ts}.txt")
        self._last_report_path = dest
        try:
            with open(dest, "w", encoding="utf-8") as f:
                f.write(f"{APP_NAME} — raport {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
                f.write("=== PODSUMOWANIE FUNKCJI ===\n")
                if self.results:
                    for k,v in self.results.items():
                        f.write(f"{k}: {'OK' if v else 'BŁĄD'}\n")
                else:
                    f.write("(brak wyników)\n")
                f.write("\n=== LOG DZIAŁAŃ ===\n")
                for line in self.log_lines:
                    f.write(line + "\n")
            self._append(f"Raport zapisany: {dest}")
        except Exception as e:
            self._append(f"Nie udało się zapisać raportu: {e}")

# =========================================================
#                            MAIN
# =========================================================

def main():
    root = tk.Tk()
    HUDApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()
