import tkinter as tk
from PIL import Image, ImageTk
import subprocess
import os

APP_TITLE = "AIR / IRZ++ — Tutorial"
WINDOW_MIN_W, WINDOW_MIN_H = 1024, 640


class Slide:
    def __init__(self, title, image_path, description):
        self.title = title
        self.image_path = image_path
        self.description = description
        self.image_obj = None  # tutaj zapiszemy załadowany obraz


SLIDES = [
    Slide(
        "Panel startowy",
        "Pa.png",
        "Panel startowy AIR — punkt wejścia do systemu. Stąd uruchomisz IRZ++, Uprawy, "
        "Narzędzia i Dopłaty. Sprawdź konfigurację i kliknij „Kontynuuj”, aby rozpocząć."
    ),
    Slide(
        "IRZ++",
        "T-1000.png",
        "IRZ++ obsługuje rejestry zwierząt i synchronizację z systemem ARiMR. "
        "Tutaj importujesz/eksportujesz dane, weryfikujesz poprawność i generujesz raporty."
    ),
    Slide(
        "Uprawy",
        "up.png",
        "Moduł Uprawy służy do prowadzenia ewidencji pól, planowania zasiewów i nawożenia "
        "oraz przygotowania raportów zgodności."
    ),
    Slide(
        "Narzędzia",
        "na.png",
        "Moduł Narzędzia zawiera funkcje dodatkowe: diagnostykę systemu, testy łączności, "
        "kontrolę plików i wsparcie techniczne."
    ),
    Slide(
        "Dopłaty",
        "dop.png",
        "Moduł Dopłaty umożliwia przygotowanie i kontrolę wniosków o płatności bezpośrednie "
        "oraz inne formy wsparcia. W tym miejscu zweryfikujesz dane i wygenerujesz pliki do ARiMR."
    ),
]


class TutorialApp(tk.Tk):
    def __init__(self, slides):
        super().__init__()
        self.title(APP_TITLE)
        self.minsize(WINDOW_MIN_W, WINDOW_MIN_H)

        self.slides = slides
        self.current_index = 0
        self.bg_img = None

        # Canvas tła
        self.canvas = tk.Canvas(self, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        # Ramka na opis
        self.frame = tk.Frame(self.canvas, bg="#ffffff", bd=3, relief="groove")
        self.frame.place(relx=0.5, rely=0.85, anchor="s", relwidth=0.85, relheight=0.28)

        self.title_label = tk.Label(self.frame, text="", font=("Arial", 18, "bold"), bg="white")
        self.title_label.pack(pady=(10, 5))

        self.desc_label = tk.Label(
            self.frame,
            text="",
            wraplength=900,
            justify="center",
            bg="white",
            font=("Arial", 12)
        )
        self.desc_label.pack(pady=(0, 15))

        # Przyciski nawigacji
        nav_frame = tk.Frame(self.frame, bg="white")
        nav_frame.pack()

        self.back_btn = tk.Button(
            nav_frame,
            text="Wstecz",
            bg="#E53935",
            fg="white",
            font=("Arial", 12, "bold"),
            width=12,
            command=self.prev_slide
        )
        self.back_btn.grid(row=0, column=0, padx=15)

        self.next_btn = tk.Button(
            nav_frame,
            text="Kontynuuj",
            bg="#43A047",
            fg="white",
            font=("Arial", 12, "bold"),
            width=12,
            command=self.next_slide
        )
        self.next_btn.grid(row=0, column=1, padx=15)

        # Preload obrazów
        self.preload_images()

        # Pokaż pierwszy slajd
        self.show_slide(0)

    def preload_images(self):
        """Ładowanie wszystkich obrazów na starcie"""
        for slide in self.slides:
            try:
                img = Image.open(slide.image_path)
                img = img.resize((WINDOW_MIN_W, WINDOW_MIN_H))
                slide.image_obj = ImageTk.PhotoImage(img)
            except Exception:
                slide.image_obj = None

    def show_slide(self, index):
        slide = self.slides[index]

        # Tło
        self.canvas.delete("all")
        if slide.image_obj:
            self.canvas.create_image(0, 0, image=slide.image_obj, anchor="nw")
        else:
            self.canvas.configure(bg="gray")

        # Teksty
        self.title_label.config(text=slide.title)
        self.desc_label.config(text=slide.description)

        # Ukryj „Wstecz” na pierwszym
        if index == 0:
            self.back_btn["state"] = "disabled"
        else:
            self.back_btn["state"] = "normal"

        # Zmień przycisk na „Zakończ” na ostatnim
        if index == len(self.slides) - 1:
            self.next_btn.config(text="Zakończ", command=self.finish_and_run)
        else:
            self.next_btn.config(text="Kontynuuj", command=self.next_slide)

    def next_slide(self):
        if self.current_index < len(self.slides) - 1:
            self.current_index += 1
            self.show_slide(self.current_index)

    def prev_slide(self):
        if self.current_index > 0:
            self.current_index -= 1
            self.show_slide(self.current_index)

    def finish_and_run(self):
        """Zamyka tutorial i odpala panel.exe"""
        self.destroy()
        exe_path = os.path.join(os.getcwd(), "panel.exe")
        if os.path.exists(exe_path):
            try:
                subprocess.Popen([exe_path], shell=True)
            except Exception as e:
                print("Błąd przy uruchamianiu panel.exe:", e)
        else:
            print("Nie znaleziono panel.exe w katalogu:", os.getcwd())


if __name__ == "__main__":
    app = TutorialApp(SLIDES)
    app.mainloop()
