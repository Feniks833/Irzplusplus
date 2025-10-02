import os
import json
import sqlite3
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path
from PIL import Image, ImageTk   # pillow do obrazka

APP_TITLE = "AIR / IRZ++ — Mapa stada"
ANIMAL_TYPES = ["krowa", "byk", "jałówka", "cielę"]
STATUS_TYPES = ["zdrowa", "obserwacja", "chora", "świeżo narodzona"]

TYPE_COLORS = {
    "krowa": "#4C9AFF",
    "byk": "#FF6B6B",
    "jałówka": "#6BCB77",
    "cielę": "#FFD93D",
}

STATUS_RINGS = {
    "zdrowa": "#34C759",
    "obserwacja": "#FFD60A",
    "chora": "#FF3B30",
    "świeżo narodzona": "#5AC8FA",
}

BG_COLOR = "#1d1f24"
CANVAS_BG = "#15171b"

# -----------------------------
# Token -> login
# -----------------------------
def get_login_from_token():
    appdata = os.getenv("APPDATA") or str(Path.home())
    folder = Path(appdata) / "KozyManager"
    folder.mkdir(parents=True, exist_ok=True)
    token_file = folder / "session_token.json"

    if not token_file.exists():
        # fallback: utwórz testowy token
        token_file.write_text(json.dumps({"login": "feniks"}), encoding="utf-8")

    try:
        data = json.loads(token_file.read_text(encoding="utf-8"))
        return data.get("login", "unknown")
    except Exception:
        return "unknown"

# -----------------------------
# DB utils
# -----------------------------
def get_db_path():
    appdata = os.getenv("APPDATA") or str(Path.home())
    folder = Path(appdata) / "KozyManager"
    folder.mkdir(parents=True, exist_ok=True)
    login = get_login_from_token()
    return folder / f"Kozy_{login}.db"

DB_PATH = get_db_path()

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS animals (
        id TEXT PRIMARY KEY,
        name TEXT,
        type TEXT,
        status TEXT,
        x REAL,
        y REAL,
        age_days INTEGER
    )
    """)
    conn.commit()
    conn.close()

def load_animals():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id, name, type, status, x, y, age_days FROM animals")
    rows = cur.fetchall()
    conn.close()

    animals = []
    for r in rows:
        animals.append({
            "id": r[0], "name": r[1], "type": r[2], "status": r[3],
            "x": r[4], "y": r[5], "age_days": r[6]
        })
    return animals

def save_animal_position(animal_id, x, y):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE animals SET x=?, y=? WHERE id=?", (x, y, animal_id))
    conn.commit()
    conn.close()

def insert_default_animals():
    defaults = [
        ("PL001", "Mela", "krowa", "zdrowa", 240, 180, 1600),
        ("PL002", "Borys", "byk", "obserwacja", 460, 220, 1200),
        ("PL003", "Luna", "jałówka", "zdrowa", 320, 340, 600),
        ("PL004", "Kira", "cielę", "świeżo narodzona", 540, 120, 7),
        ("PL005", "Rosa", "krowa", "chora", 700, 280, 2100),
    ]
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    for a in defaults:
        try:
            cur.execute("INSERT INTO animals (id, name, type, status, x, y, age_days) VALUES (?, ?, ?, ?, ?, ?, ?)", a)
        except sqlite3.IntegrityError:
            pass
    conn.commit()
    conn.close()

# -----------------------------
# Tooltip
# -----------------------------
class Tooltip:
    def __init__(self, widget):
        self.widget = widget
        self.tip = None

    def show(self, text, x, y):
        self.hide()
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry(f"+{int(x+16)}+{int(y+16)}")
        lbl = tk.Label(self.tip, text=text, bg="#111318", fg="#d9dbe0",
                       relief="solid", borderwidth=1, padx=8, pady=4, font=("Segoe UI", 9))
        lbl.pack()

    def hide(self):
        if self.tip is not None:
            self.tip.destroy()
            self.tip = None

# -----------------------------
# App
# -----------------------------
class AnimalMapApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("900x600")
        self.configure(bg=BG_COLOR)

        self.animals = load_animals()
        self.drag_data = {"item": None, "x": 0, "y": 0}
        self.tooltip = Tooltip(self)
        self.bg_image = None

        self._build_ui()

    def _build_ui(self):
        self.canvas = tk.Canvas(self, bg=CANVAS_BG, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        # ustaw tło
        self._set_background()

        self.draw_animals()

        self.canvas.tag_bind("animal", "<ButtonPress-1>", self.on_start_drag)
        self.canvas.tag_bind("animal", "<B1-Motion>", self.on_drag)
        self.canvas.tag_bind("animal", "<ButtonRelease-1>", self.on_drop)
        self.canvas.tag_bind("animal", "<Enter>", self.on_hover)
        self.canvas.tag_bind("animal", "<Leave>", lambda e: self.tooltip.hide())
        self.canvas.tag_bind("animal", "<Double-Button-1>", self.on_double_click)

    def _set_background(self):
        appdata = os.getenv("APPDATA") or str(Path.home())
        bg_path = Path(appdata) / "KozyManager" / "background.jpg"
        if bg_path.exists():
            img = Image.open(bg_path)
            img = img.resize((900, 600))  # dopasowanie do okna
            self.bg_image = ImageTk.PhotoImage(img)
            self.canvas.create_image(0, 0, anchor="nw", image=self.bg_image)

    def draw_animals(self):
        for animal in self.animals:
            self._draw_animal(animal)

    def _draw_animal(self, animal):
        x, y = animal["x"], animal["y"]
        color = TYPE_COLORS.get(animal["type"], "#ccc")
        ring = STATUS_RINGS.get(animal["status"], "#888")

        oval = self.canvas.create_oval(x-18, y-18, x+18, y+18, fill=color, outline=ring, width=3, tags=("animal", animal["id"]))
        txt = self.canvas.create_text(x, y, text=animal["id"], fill="white", font=("Segoe UI", 9, "bold"), tags=("animal", animal["id"]))
        return oval, txt

    def on_start_drag(self, event):
        item = self.canvas.find_closest(event.x, event.y)[0]
        tags = self.canvas.gettags(item)
        if "animal" in tags:
            self.drag_data["item"] = tags[1]
            self.drag_data["x"] = event.x
            self.drag_data["y"] = event.y

    def on_drag(self, event):
        if not self.drag_data["item"]:
            return
        dx = event.x - self.drag_data["x"]
        dy = event.y - self.drag_data["y"]
        self.drag_data["x"] = event.x
        self.drag_data["y"] = event.y
        for item in self.canvas.find_withtag(self.drag_data["item"]):
            self.canvas.move(item, dx, dy)

    def on_drop(self, event):
        if not self.drag_data["item"]:
            return
        coords = self.canvas.coords(self.canvas.find_withtag(self.drag_data["item"])[0])
        x = (coords[0] + coords[2]) / 2
        y = (coords[1] + coords[3]) / 2
        save_animal_position(self.drag_data["item"], x, y)
        for a in self.animals:
            if a["id"] == self.drag_data["item"]:
                a["x"], a["y"] = x, y
                break
        self.drag_data["item"] = None

    def on_hover(self, event):
        item = self.canvas.find_closest(event.x, event.y)[0]
        tags = self.canvas.gettags(item)
        if "animal" in tags:
            a = next(an for an in self.animals if an["id"] == tags[1])
            text = f"{a['id']} - {a['name']}\nTyp: {a['type']}\nStatus: {a['status']}"
            self.tooltip.show(text, event.x_root, event.y_root)

    def on_double_click(self, event):
        item = self.canvas.find_closest(event.x, event.y)[0]
        tags = self.canvas.gettags(item)
        if "animal" in tags:
            a = next(an for an in self.animals if an["id"] == tags[1])
            self.show_card(a)

    def show_card(self, animal):
        win = tk.Toplevel(self)
        win.title(f"Karta zwierzęcia {animal['id']}")
        ttk.Label(win, text=f"ID: {animal['id']}").pack(anchor="w", padx=10, pady=5)
        ttk.Label(win, text=f"Imię: {animal['name']}").pack(anchor="w", padx=10, pady=5)
        ttk.Label(win, text=f"Typ: {animal['type']}").pack(anchor="w", padx=10, pady=5)
        ttk.Label(win, text=f"Status: {animal['status']}").pack(anchor="w", padx=10, pady=5)
        ttk.Label(win, text=f"Wiek [dni]: {animal['age_days']}").pack(anchor="w", padx=10, pady=5)

# -----------------------------
# Start
# -----------------------------
if __name__ == "__main__":
    init_db()
    if not load_animals():
        insert_default_animals()
    app = AnimalMapApp()
    app.mainloop()
