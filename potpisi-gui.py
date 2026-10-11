#!/usr/bin/env python3
"""GUI za lokalno potpisivanje PDF-a karticom (pyHanko + AKD PKCS#11 modul).

Preview PDF-a s mrezom potpisnih okvira:
  - klikni okvir da ga odaberes,
  - povuci misem da ga premjestis gdje zelis,
  - "Redova mreze" mijenja broj okvira (2 stupca x N redova),
  - stranica se mijenja brojem ili strelicama i odmah se prikaze.

Pokretanje: .venv/bin/python potpisi-gui.py   ili dvoklik na potpisi-gui.command
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import subprocess
import sys
import threading
import traceback
from types import SimpleNamespace

import argparse
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk

try:
    import pypdfium2 as pdfium
except ImportError:  # preview je opcionalan
    pdfium = None

try:
    from PIL import ImageTk
except Exception:  # noqa: BLE001  (Pillow bez Tk podrske -> rezervna PPM varijanta)
    ImageTk = None

HERE = os.path.dirname(os.path.abspath(__file__))
MOD_PATH = os.path.join(HERE, "potpisi-pdf.py")
APP_ICON = os.path.join(HERE, "ikona.png")



GRID_COLS = 2
MARGIN_X, MARGIN_Y = 0.05, 0.04
GAP_X, GAP_Y = 0.02, 0.015


def sig_rects(path: str) -> list[tuple[int, list[float]]]:
    """(indeks stranice, Rect) za svaki potpis koji ima vidljiv appearance."""
    from pypdf import PdfReader

    out = []
    for pi, page in enumerate(PdfReader(path).pages):
        for a in page.get("/Annots", []) or []:
            o = a.get_object()
            if o.get("/FT") != "/Sig":
                continue
            ap = o.get("/AP", {}).get("/N")
            rect = [float(v) for v in o.get("/Rect")]
            if ap is None or abs(rect[2] - rect[0]) < 5 or abs(rect[3] - rect[1]) < 5:
                continue
            if len(ap.get_object().get_data()) < 20:  # prazan appearance = nevidljiv
                continue
            out.append((pi, rect))
    return out


def load_module():
    spec = importlib.util.spec_from_file_location("potpisi_pdf", MOD_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LIGHT = {
    "bg": "#f4f6f9", "panel": "#ffffff", "border": "#d5dbe5",
    "text": "#1f2937", "muted": "#5b6472",
    "primary": "#2563eb", "primary_hover": "#1d4ed8", "primary_press": "#1e40af",
    "primary_text": "#ffffff",
    "secondary": "#ffffff", "secondary_hover": "#eef2f9", "secondary_press": "#e2e8f4",
    "focus": "#93c5fd",
    "header_bg": "#123a63", "header_text": "#ffffff", "header_sub": "#cfe3f7",
    "ok": "#15803d", "warn": "#b45309", "err": "#b91c1c", "head": "#1d4ed8",
    "grid": "#93b4e0", "select": "#dc2626", "select_fill": "#fdeceb",
    "canvas_bg": "#e9ecf2", "shadow1": "#cdd3dd", "shadow2": "#dfe3ea",
    "dot_idle": "#9aa0a6", "dot_busy": "#2563eb", "dot_ok": "#15803d",
    "dot_warn": "#d97706", "dot_err": "#dc2626",
}

DARK = {
    "bg": "#1b1f27", "panel": "#242a34", "border": "#3b4插",   # popravljeno ispod
    "text": "#e8ebf1", "muted": "#a9b2c0",
    "primary": "#2563eb", "primary_hover": "#1d4ed8", "primary_press": "#1e40af",
    "primary_text": "#ffffff",
    "secondary": "#2b323d", "secondary_hover": "#333b48", "secondary_press": "#3c4653",
    "focus": "#7dd3fc",
    "header_bg": "#101825", "header_text": "#ffffff", "header_sub": "#a8c8ec",
    "ok": "#4ade80", "warn": "#fbbf24", "err": "#f87171", "head": "#7cc0ff",
    "grid": "#4a6b96", "select": "#f87171", "select_fill": "#3a2429",
    "canvas_bg": "#161a21", "shadow1": "#0d1015", "shadow2": "#12161c",
    "dot_idle": "#8b94a3", "dot_busy": "#60a5fa", "dot_ok": "#4ade80",
    "dot_warn": "#fbbf24", "dot_err": "#f87171",
}
DARK["border"] = "#3b4453"

PALETTE = dict(LIGHT)  # aktivna paleta (mijenja je set_theme)


# rijeci po kojima se prepoznaje razina linije u logu (kod, nije korisnicki tekst)
_TAG_WORDS_ERR = ("greška", "greska", "error", "traceback")


def grid_index_after_resize(boxes, rows: int, old_rows: int | None, old_sel: int | None,
                            box) -> int | None:
    """Koji okvir mreze odabrati nakon promjene broja redova.

    Vraca indeks u `boxes` ili None ako se izbor ne mijenja (isti raspored mreze).
    """
    if not boxes:
        return None
    if box is None:
        return 0
    if old_rows == rows:
        if old_sel is not None and old_sel < len(boxes):
            return old_sel          # isti raspored: ostani u istoj celiji
        return None                 # rucno povucen okvir: ne diramo ga
    if old_sel is not None:         # selekcija prati svoj (red, stupac)
        row, col = divmod(old_sel, GRID_COLS)
        row = min(row, rows - 1)    # ako se mreza smanjila, ostani u zadnjem redu
        return min(row * GRID_COLS + col, len(boxes) - 1)
    # rucno povucen okvir: snapi ga na najblizu celiju nove mreze
    bx = (box[0] + box[2]) / 2
    by = (box[1] + box[3]) / 2
    return min(range(len(boxes)),
               key=lambda i: abs((boxes[i][0] + boxes[i][2]) / 2 - bx)
               + abs((boxes[i][1] + boxes[i][3]) / 2 - by))


def cert_area_state(certs) -> tuple[str, list[dict]]:
    """Sto prikazati u izborniku certifikata: (nacin, potpisni certifikati).

    nacin: "none" (nema kartice/certifikata), "none_usable" (ima ih, ali nijedan ne
    smije potpisivati), "one" (jedan potpisni) ili "many" (vise njih -> padajuci izbornik).
    """
    usable = [c for c in (certs or []) if c.get("usable")]
    if not certs:
        return "none", []
    if not usable:
        return "none_usable", []
    return ("one" if len(usable) == 1 else "many"), usable


def open_file(path: str) -> None:
    """Otvori datoteku u zadanom programu (macOS/Linux/Windows)."""
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", path])
        elif sys.platform.startswith("linux"):
            subprocess.Popen(["xdg-open", path])
        elif sys.platform == "win32":
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["open", path])
    except Exception:  # noqa: BLE001
        pass


def _rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def round_rect(canvas: tk.Canvas, x1, y1, x2, y2, r, **kwargs):
    """Zaobljeni pravokutnik na canvasu (polygon sa smooth=True)."""
    pts = [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]
    return canvas.create_polygon(pts, smooth=True, **kwargs)


class RoundedButton(tk.Canvas):
    """Gumb sa zaobljenim kutovima (Tk nema native rounded gumbe)."""

    def __init__(self, parent, text, command=None, kind="primary", width=None,
                 height=36, radius=13, font=None, palette=PALETTE):
        pal = palette
        if kind == "primary":
            self.c_normal, self.c_hover, self.c_press = pal["primary"], pal["primary_hover"], pal["primary_press"]
            self.c_text, self.c_border = pal["primary_text"], pal["primary"]
            f = font or ("Helvetica", 12, "bold")
        else:
            self.c_normal, self.c_hover, self.c_press = pal["secondary"], pal["secondary_hover"], pal["secondary_press"]
            self.c_text, self.c_border = pal["text"], pal["border"]
            f = font or ("Helvetica", 12)
        fobj = tkfont.Font(font=f)
        w = width or (fobj.measure(text) + 36)
        super().__init__(parent, width=w, height=height, highlightthickness=0, bd=0,
                         bg=pal["bg"], takefocus=1)
        self.command, self._enabled = command, True
        self._shape = round_rect(self, 1, 1, w - 1, height - 1, radius, fill=self.c_normal,
                                 outline=self.c_border)
        self._label = self.create_text(w / 2, height / 2 + 1, text=text, fill=self.c_text, font=f)
        self.bind("<Enter>", lambda _e: self._paint(self.c_hover))
        self.bind("<Leave>", lambda _e: self._paint(self.c_normal))
        self.bind("<ButtonPress-1>", lambda _e: self._paint(self.c_press))
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<FocusIn>", lambda _e: self._set_focus(True))
        self.bind("<FocusOut>", lambda _e: self._set_focus(False))
        self.bind("<Return>", lambda _e: self._fire())
        self.bind("<space>", lambda _e: self._fire())

    def _paint(self, color):
        if self._enabled:
            self.itemconfigure(self._shape, fill=color)

    def _set_focus(self, on):
        self.itemconfigure(self._shape, outline=PALETTE["focus"] if on else self.c_border,
                           width=2 if on else 1)

    def _on_release(self, event):
        self._paint(self.c_hover if 0 <= event.x <= self.winfo_width() else self.c_normal)
        self._fire()

    def _fire(self):
        if self._enabled and self.command:
            self.command()

    def set_enabled(self, on: bool):
        self._enabled = bool(on)
        self.itemconfigure(self._shape, fill=self.c_normal if on else PALETTE["secondary_press"])
        self.itemconfigure(self._label, fill=self.c_text if on else PALETTE["muted"])
        self.configure(cursor="" if on else "arrow")

class ToolTip:
    """Mali tooltip za ttk/tk widgete (Tk nema ugrađene)."""

    def __init__(self, widget, text: str, delay: int = 450):
        self.widget, self.text, self.delay = widget, text, delay
        self._tip = None
        self._job = None
        widget.bind("<Enter>", self._enter, add="+")
        widget.bind("<Leave>", self._leave, add="+")
        widget.bind("<ButtonPress>", self._leave, add="+")

    def _enter(self, _event=None):
        self._job = self.widget.after(self.delay, self._show)

    def _leave(self, _event=None):
        if self._job:
            try:
                self.widget.after_cancel(self._job)
            except Exception:  # noqa: BLE001
                pass
            self._job = None
        self._hide()

    def _show(self):
        if self._tip is not None or not self.text:
            return
        x = self.widget.winfo_rootx() + 18
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 8
        tip = tk.Toplevel(self.widget)
        tip.wm_overrideredirect(True)
        tip.wm_geometry(f"+{x}+{y}")
        tk.Label(tip, text=self.text, justify="left", background="#fffbe6",
                 foreground="#202020", relief="solid", borderwidth=1,
                 font=("Helvetica", 11), padx=9, pady=5, wraplength=420).pack()
        self._tip = tip

    def _hide(self):
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.lang = "hr"   # mora prije prvog self.t() (npr. status StringVar)
        self.theme = "light"
        self.mod = load_module()
        self.doc = None
        self.page_count = 0
        self.page_size = (595.0, 842.0)
        self.zoom = 1.0
        self.grid_boxes: list[tuple[float, float, float, float]] = []
        self.box: tuple[float, float, float, float] | None = None
        self.sel_index: int | None = None
        self.drag_from: tuple[float, float] | None = None
        self._img = None
        self._resize_job = None
        self.ox = self.oy = 0

        self.pdf = tk.StringVar()
        self.token = tk.StringVar(value="Signature")
        self.visible = tk.BooleanVar(value=True)
        self.page = tk.IntVar(value=1)
        self.rows = tk.IntVar(value=7)
        self.pin = tk.StringVar()
        self.open_after = tk.BooleanVar(value=True)
        self.tsa_on = tk.BooleanVar(value=True)
        self.lt_on = tk.BooleanVar(value=True)
        self.status = tk.StringVar(value=self.t('Odaberi PDF.'))
        self.info = tk.StringVar(value="")
        self.boxinfo = tk.StringVar(value="")
        self._certs: list[dict] | None = None
        self._cert_gen = 0   # brojac zahtjeva - stariji rezultat ne smije pregaziti noviji
        self.cert_error: str | None = None
        self.mod.LANG = self.lang
        self._build_ui()

    def t(self, text: str) -> str:
        """Prijevod: isti mehanizam kao u modulu (T() s hrvatskim tekstom kao kljucem)."""
        return self.mod.T(text)

    def change_language(self, code: str):
        if code == self.lang:
            return
        self.lang = code
        self.mod.LANG = code
        self._rebuild()

    def _build_ui(self):
        root = self.root
        root.title(self.t('Lokalno potpisivanje PDF-a'))
        root.columnconfigure(0, weight=1)
        root.rowconfigure(2, weight=1)
        root.minsize(1040, 720)
        self._setup_style()
        self._setup_icon()
        self._build_header(root)
        self._build_form(root)
        self._build_preview_log(root)
        self._build_statusbar(root)
        self.set_status(self.t('Odaberi PDF pa klikni „Potpiši PDF”.'), "info")
        self.root.after(250, self._set_sash)  # garantira 2/3 : 1/3
        self.load_certs()

    # ponytail: PPM datoteka je rezerva ako Pillow nema Tk podrsku (ImageTk je primaran put)
    def _build_header(self, root):
        # ---------- zaglavlje ----------
        head = tk.Frame(root, background=PALETTE["header_bg"])
        head.grid(row=0, column=0, sticky="ew")
        tk.Label(head, text="  " + self.t('Lokalno potpisivanje PDF-a'), background=PALETTE["header_bg"],
                 foreground=PALETTE["header_text"],
                 font=("Helvetica", 17, "bold")).pack(side="left", pady=10)
        # desno u zaglavlju (s desna na lijevo): tema, pa jezik (label + izbornik zajedno),
        # pa podnaslov - tako gumb za temu nikad ne upadne izmedu natpisa i izbornika
        self._tip(
            RoundedButton(head,
                          self.t('Svijetlo') if self.theme == "dark" else self.t('Tamno'),
                          command=lambda: self.set_theme("light" if self.theme == "dark" else "dark"),
                          kind="secondary", width=104, height=28, radius=10),
            self.t('Prebaci svijetlu/tamnu temu (sve boje su podesene za citljivost).'),
        ).pack(side="right", padx=(6, 14))

        langbox = tk.Frame(head, background=PALETTE["header_bg"])
        langbox.pack(side="right", padx=(0, 4), pady=8)
        tk.Label(langbox, text=self.t('Jezik:'), background=PALETTE["header_bg"],
                 foreground=PALETTE["header_sub"], font=("Helvetica", 11)).pack(side="left",
                                                                                padx=(0, 6))
        self.lang_box = self._tip(
            ttk.Combobox(langbox, values=["Hrvatski", "English"], state="readonly", width=10),
            "Hrvatski / English", initial="Hrvatski" if self.lang == "hr" else "English",
            on_select=lambda w: self.change_language("hr" if w.get() == "Hrvatski" else "en"),
        )
        self.lang_box.pack(side="left")

        self.subtitle = tk.Label(head, text=self.t('radi preko AKD middlewarea · PKCS#11 · ništa ne ide u cloud') + "  ",
                                 background=PALETTE["header_bg"],
                                 foreground=PALETTE["header_sub"], font=("Helvetica", 11))
        self.subtitle.pack(side="right", pady=15)

    def _build_form(self, root):
        # ---------- podaci ----------
        body = ttk.Frame(root, padding=(12, 10, 12, 4))
        body.grid(row=1, column=0, sticky="ew")
        body.columnconfigure(0, weight=1)

        lf_doc = ttk.LabelFrame(body, text=self.t(' Dokument '), padding=(10, 8))
        lf_doc.grid(row=0, column=0, sticky="ew")
        lf_doc.columnconfigure(1, weight=1)
        ttk.Label(lf_doc, text=self.t('PDF:')).grid(row=0, column=0, sticky="w")
        self._tip(ttk.Entry(lf_doc, textvariable=self.pdf), self.t('PDF koji potpisuješ.\nZa jedan čist potpis odaberi original, ne već potpisani PDF.')).grid(
            row=0, column=1, sticky="ew", padx=8)
        self._tip(RoundedButton(lf_doc, self.t('Odaberi…'), command=self.pick_pdf,
                                kind="secondary", height=30, radius=11),
                  self.t('Odaberi PDF datoteku s diska.')).grid(row=0, column=2)
        self._tip(ttk.Label(lf_doc, textvariable=self.info, style="Hint.TLabel"),
                  self.t('Broj stranica i naziv učitanog dokumenta.')).grid(row=0, column=3, padx=(12, 0))

        lf_sign = ttk.LabelFrame(body, text=self.t(' Potpis '), padding=(10, 8))
        lf_sign.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        lf_sign.columnconfigure(1, weight=1)
        lf_sign.columnconfigure(6, weight=1)

        ttk.Label(lf_sign, text=self.t('Certifikat:')).grid(row=0, column=0, sticky="w")
        self.cert_slot = ttk.Frame(lf_sign)
        self.cert_slot.grid(row=0, column=1, sticky="w", padx=(8, 6))
        self._tip(RoundedButton(lf_sign, "↻", command=lambda: self.load_certs(force=True),
                                kind="secondary", width=34, height=30, radius=11),
                  self.t('Ponovno procitaj certifikate s kartice.')).grid(row=0, column=2, sticky="w")
        self._tip(ttk.Checkbutton(lf_sign, text=self.t('vidljiv potpis'), variable=self.visible),
                  self.t('Iscrtava vidljivi pečat potpisa na stranicu.\nAko je isključeno, potpis je nevidljiv (ali jednako valjan).')).grid(row=0, column=3, sticky="w", padx=(18, 0))

        nav = ttk.Frame(lf_sign)
        nav.grid(row=0, column=4, columnspan=4, sticky="w", padx=(24, 0))
        ttk.Label(nav, text=self.t('stranica:')).pack(side="left")
        self.page_spin = self._tip(
            ttk.Spinbox(nav, from_=1, to=1, textvariable=self.page, width=5, command=self.goto_page),
            self.t('Stranica na koju ide vidljivi potpis.\n◀ ▶ listaju stranice, upisani broj se odmah prikaže.'))
        self.page_spin.pack(side="left", padx=4)
        self._tip(RoundedButton(nav, "◀", command=lambda: self.step_page(-1), kind="secondary",
                                width=38, height=30, radius=11), self.t('Prethodna stranica.')).pack(side="left")
        self._tip(RoundedButton(nav, "▶", command=lambda: self.step_page(1), kind="secondary",
                                width=38, height=30, radius=11), self.t('Sljedeća stranica.')).pack(
            side="left", padx=(4, 24))
        ttk.Label(nav, text=self.t('redova mreže:')).pack(side="left")
        self._tip(ttk.Spinbox(nav, from_=1, to=12, textvariable=self.rows, width=4,
                              command=self.render), self.t('Koliko redova okvira se iscrta preko stranice (2 stupca × N redova).\nKlikni okvir da odabereš mjesto potpisa, povuci mišem da ga premjestiš.')).pack(side="left", padx=4)


        ttk.Label(lf_sign, text=self.t('PIN kartice:')).grid(row=1, column=0, sticky="w", pady=(10, 0))
        self._tip(ttk.Entry(lf_sign, textvariable=self.pin, show="•", width=14),
                  self.t('PIN kartice (ostaje samo na ovom računalu).\nPazi: kartica dopušta ograničen broj pokušaja (nakon 3 promašaja treba PUK).')).grid(row=1, column=1, sticky="w", padx=(8, 24), pady=(10, 0))
        acts = ttk.Frame(lf_sign)
        acts.grid(row=2, column=2, columnspan=5, sticky="w", pady=(10, 0))
        self.sign_btn = self._tip(
            RoundedButton(acts, self.t('Potpiši PDF'), command=self.do_sign, kind="primary"),
            self.t('Potpisuje odabrani PDF karticom i sprema <ime>-signed.pdf.\nSve ostaje lokalno - dokument i PIN se nigdje ne šalju.'))
        self.sign_btn.pack(side="left")
        self._tip(RoundedButton(acts, self.t('Provjeri potpis'), command=self.do_verify, kind="secondary"),
                  self.t('Temeljita provjera postojećih potpisa: lanac, vremenski žig i opoziv.\nMože ići na mrežu (dohvat CRL/OCSP).')).pack(side="left", padx=8)
        self._tip(RoundedButton(acts, self.t('Popis potpisa'), command=self.show_summary,
                                kind="secondary"), self.t('Brzi lokalni popis potpisa u PDF-u: polje, potpisnik, vrijeme, valjan/pouzdan,\ni u koje polje ide sljedeći potpis.')).pack(side="left")
        self._tip(ttk.Checkbutton(acts, text=self.t('otvori nakon potpisa'), variable=self.open_after),
                  self.t('Nakon potpisa automatski otvori potpisani PDF.')).pack(side="left", padx=(20, 0))

        chk = ttk.Frame(lf_sign)
        chk.grid(row=3, column=0, columnspan=7, sticky="w", pady=(8, 0))
        self._tip(ttk.Checkbutton(chk, text=self.t('vremenski žig (javni TSA)'), variable=self.tsa_on),
                  self.t('RFC3161 vremenski žig od javnog TSA (DigiCert): neovisni dokaz KADA je potpis nastao.\nTraži internet; ako ne prođe, potpis ipak nastaje (bez žiga).')).pack(side="left")
        self._tip(ttk.Checkbutton(chk, text=self.t('PAdES-LT (ugradi OCSP/CRL dokaze)'), variable=self.lt_on),
                  self.t('Ugrađuje dokaze o opozivu (OCSP/CRL) u PDF za dugoročnu provjerljivost.\nAKD certifikati ne objavljuju CRL/OCSP adrese, pa se obično preskoči.')).pack(side="left", padx=20)
        self.root.bind("<Control-Return>", lambda _e: self.do_sign())

    def _build_preview_log(self, root):
        # ---------- preview + log ----------
        pane = ttk.PanedWindow(root, orient="horizontal")
        pane.grid(row=2, column=0, sticky="nsew", padx=12, pady=(6, 6))
        self.pane = pane

        prev = ttk.Frame(pane)
        pane.add(prev, weight=2)  # 2/3 sirine: pregled PDF-a
        prev.rowconfigure(0, weight=1)
        prev.columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(prev, background=PALETTE["canvas_bg"], highlightthickness=1,
                                highlightbackground=PALETTE["border"])
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.canvas.bind("<Configure>", self.on_resize)
        self.canvas.bind("<Button-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self._tip(self.canvas, self.t('Klikni okvir da odabereš mjesto potpisa, povuci mišem da ga premjestiš.\nCrveni okvir = gdje će biti vidljivi potpis.'))
        ttk.Label(prev, textvariable=self.boxinfo, style="Hint.TLabel").grid(
            row=1, column=0, sticky="w", pady=(4, 0))

        right = ttk.Frame(pane)
        pane.add(right, weight=1)  # 1/3 sirine: log/status
        right.rowconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)
        # font: Tk-ov sistemski monospace (Menlo / Consolas / DejaVu), sa malo "zraka"
        self.log_font = tkfont.nametofont("TkFixedFont").copy()
        self.log_font.configure(size=11)
        self.log_bold = self.log_font.copy()
        self.log_bold.configure(weight="bold")
        self.log = tk.Text(right, height=12, width=46, wrap="word", font=self.log_font,
                           background=PALETTE["panel"], foreground=PALETTE["text"],
                           relief="flat", padx=12, pady=10,
                           spacing1=3, spacing2=2, spacing3=3, insertwidth=2,
                           highlightthickness=1, highlightbackground=PALETTE["border"])
        self.log.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(right, command=self.log.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=sb.set)
        self._tip(self.log, self.t('Tijek rada: potpis, provjera, upozorenja i greške.'))
        for tag, color, bold in (("ok", PALETTE["ok"], False), ("warn", PALETTE["warn"], False),
                                 ("err", PALETTE["err"], False), ("head", PALETTE["head"], True)):
            self.log.tag_configure(tag, foreground=color,
                                   font=self.log_bold if bold else self.log_font)

    def _build_statusbar(self, root):
        # ---------- statusna traka ----------
        bar = tk.Frame(root, background=PALETTE["bg"])
        bar.grid(row=3, column=0, sticky="ew")
        self.dot = tk.Canvas(bar, width=14, height=14, highlightthickness=0, background=PALETTE["bg"])
        self.dot.pack(side="left", padx=(14, 8), pady=7)
        self._dot = self.dot.create_oval(3, 3, 12, 12, fill=PALETTE["dot_idle"], outline="")
        tk.Label(bar, textvariable=self.status, background=PALETTE["bg"],
                 foreground=PALETTE["text"],
                 anchor="w").pack(side="left", fill="x", expand=True, pady=7)
        tk.Label(bar, text=self.t('neslužbeni alat · nije povezan s AKD d.o.o.'), background=PALETTE["bg"],
                 foreground=PALETTE["muted"], font=("Helvetica", 10)).pack(
            side="right", padx=(8, 14), pady=7)

        if pdfium is None:
            self.canvas.create_text(20, 20, anchor="nw", text=(
                "Preview nije dostupan.\n\nInstaliraj: .venv/bin/pip install pypdfium2"
            ), fill=PALETTE["text"])

    def _setup_style(self):
        st = ttk.Style()
        try:
            st.theme_use("clam")  # eksplicitne boje, neovisno o dark modeu
        except Exception:  # noqa: BLE001
            pass
        p = PALETTE
        st.configure(".", background=p["bg"], foreground=p["text"], fieldbackground=p["panel"])
        st.configure("TFrame", background=p["bg"])
        st.configure("TLabel", background=p["bg"], foreground=p["text"])
        st.configure("Hint.TLabel", background=p["bg"], foreground=p["muted"])
        st.configure("TLabelframe", background=p["bg"], bordercolor=p["border"],
                     relief="solid", borderwidth=1)
        st.configure("TLabelframe.Label", background=p["bg"], foreground=p["text"],
                     font=("Helvetica", 11, "bold"))
        st.configure("TEntry", fieldbackground=p["panel"], bordercolor=p["border"],
                     lightcolor=p["border"], darkcolor=p["border"], insertcolor=p["text"])
        st.configure("TCombobox", fieldbackground=p["panel"], background=p["panel"],
                     bordercolor=p["border"], arrowcolor=p["text"], lightcolor=p["border"],
                     darkcolor=p["border"])
        st.configure("TSpinbox", fieldbackground=p["panel"], bordercolor=p["border"],
                     arrowcolor=p["text"], lightcolor=p["border"], darkcolor=p["border"])
        st.configure("TCheckbutton", background=p["bg"], foreground=p["text"])
        st.map("TCheckbutton", background=[("active", p["bg"])])
        st.configure("TPanedwindow", background=p["bg"])
        st.configure("TScrollbar", background=p["border"], troughcolor=p["bg"],
                     bordercolor=p["bg"], arrowcolor=p["text"])
        self.root.option_add("*TCombobox*Listbox.background", p["panel"])
        self.root.option_add("*TCombobox*Listbox.foreground", p["text"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", p["primary"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", p["primary_text"])

    def _setup_icon(self):
        try:
            self._icon = tk.PhotoImage(file=APP_ICON)
            self.root.iconphoto(True, self._icon)
        except Exception:  # noqa: BLE001
            self._icon = None

    def set_info(self):
        """Broj stranica i naziv dokumenta - na jeziku koji je trenutno aktivan."""
        path = self.pdf.get().strip()
        if path and self.doc is not None:
            self.info.set(self.t('{n} stranica · {name}').format(
                n=self.page_count, name=os.path.basename(path)))
        else:
            self.info.set("")

    def set_theme(self, name: str):
        if name == self.theme:
            return
        self.theme = name
        PALETTE.clear()
        PALETTE.update(DARK if name == "dark" else LIGHT)
        self._rebuild()

    def _rebuild(self):
        box, sel = self.box, self.sel_index
        for child in self.root.winfo_children():
            child.destroy()
        self._build_ui()
        self.box, self.sel_index = box, sel
        self.set_info()   # info linija je StringVar - mora se ponovno postaviti
        if self.doc is not None:
            self.render()
            self.show_summary()
        self.set_status(self.t('Odaberi PDF pa klikni „Potpiši PDF”.'), "info")

    def load_certs(self, force: bool = False):
        """Popuni izbornik certifikata - samo oni koji smiju potpisivati dokumente."""
        if self._certs is not None and not force:
            self._render_cert_area()
            return
        root = self.root
        if force:
            self.write(self.t('citam certifikate s kartice…') + "\n")
        self._cert_gen += 1
        gen = self._cert_gen
        self._render_cert_area(loading=True)

        def work():
            try:
                certs = self.mod.list_certs()
                err = None
            except Exception as exc:  # noqa: BLE001
                certs, err = [], f"{type(exc).__name__}: {str(exc)[:80]}"
            root.after(0, lambda: self._cert_done(certs, err, gen))

        threading.Thread(target=work, daemon=True).start()

    def _cert_done(self, certs: list[dict], err: str | None, gen: int):
        if gen != self._cert_gen:
            return  # zastario rezultat (korisnik je u medjuvremenu ponovno ucitavao)
        self._certs = certs
        self.cert_error = err
        try:
            self._render_cert_area()
        except Exception as exc:  # noqa: BLE001 - placeholder nikad ne smije ostati
            for child in self.cert_slot.winfo_children():
                child.destroy()
            self._tip(tk.Label(self.cert_slot, text=f"{self.t('Greška')}: {type(exc).__name__}",
                               foreground=PALETTE["err"], background=PALETTE["bg"]),
                      str(exc)).pack(side="left")
            self.write(f"{self.t('Greška')}: {type(exc).__name__}: {exc}\n")
            self.set_status(self.t('nema certifikata za potpisivanje (ubaci karticu, pa ↻)'), "err")
            return
        mode, usable = cert_area_state(certs)
        if err:
            self.set_status(f"{self.t('Greška')}: {err}", "err")
        elif mode == "none":
            self.set_status(self.t('nema certifikata za potpisivanje (ubaci karticu, pa ↻)'), "warn")
        elif mode == "none_usable":
            self.set_status(self.t('kartica nema potpisni certifikat (samo identifikacijski)'), "warn")
        else:
            self.set_status(self.t('certifikati: {n} potpisni').format(n=len(usable)), "ok")

    def _set_sash(self, ratio: float = 2 / 3):
        """Postavi razdjelnik pregleda na 2/3 sirine (u nekim temama weight se ignorira)."""
        try:
            total = self.pane.winfo_width()
            if total > 200:
                self.pane.sashpos(0, int(total * ratio))
        except Exception:  # noqa: BLE001
            pass

    def _render_cert_area(self, loading: bool = False):
        slot = self.cert_slot
        for child in slot.winfo_children():
            child.destroy()
        if loading:
            self._tip(tk.Label(slot, text=self.t('citam certifikate s kartice…'), foreground=PALETTE["muted"],
                               background=PALETTE["bg"]), self.t('Kartica nije pronadena ili nema potpisnog certifikata. Ubaci karticu pa klikni ↻.')).pack(side="left")
            return
        mode, usable = cert_area_state(self._certs)
        if mode in ("none", "none_usable"):
            self.token.set("Signature")
            text = self.t('nema certifikata za potpisivanje (ubaci karticu, pa ↻)') if mode == "none" else self.t('kartica nema potpisni certifikat (samo identifikacijski)')
            self._tip(tk.Label(slot, text=text, foreground=PALETTE["warn"],
                               background=PALETTE["bg"]),
                      self.t('Kartica nije pronadena ili nema potpisnog certifikata. Ubaci karticu pa klikni ↻.')).pack(side="left")
            return
        if mode == "one":
            c = usable[0]
            self.token.set(c["token"])
            short = c["token"].split("(")[-1].rstrip(")") if "(" in c["token"] else c["token"]
            self._tip(tk.Label(slot, text=f"{c['cn']} · {short}",
                               foreground=PALETTE["text"], background=PALETTE["bg"]),
                      self.t('Jedini certifikat na kartici koji može potpisivati dokumente.')).pack(side="left")
            return
        names = [f"{c['cn']} · {c['token']}" for c in usable]
        self._token_map = {n: c["token"] for n, c in zip(names, usable)}
        self.token.set(self._token_map[names[0]])
        cb = ttk.Combobox(slot, values=names, state="readonly", width=30)
        cb.set(names[0])
        cb.bind("<<ComboboxSelected>>", lambda _e: self.token.set(self._token_map[cb.get()]))
        self._tip(cb, self.t('Nudi se samo certifikat koji smije potpisivati dokumente (non-repudiation).\nIdentifikacijski certifikat (client_auth / e-Građani) se ne prikazuje jer ne služi za potpis.')).pack(side="left")

    def _tip(self, widget, text: str, initial: str | None = None, on_select=None):
        """Zakaci tooltip (i opcionalno pocetnu vrijednost/odabir) pa vrati widget."""
        if initial is not None:
            try:
                widget.set(initial)
            except Exception:  # noqa: BLE001
                pass
        if on_select is not None:
            widget.bind("<<ComboboxSelected>>", lambda _e: on_select(widget))
        ToolTip(widget, text)
        return widget

    def set_status(self, text: str, kind: str = "info"):
        self.status.set(text)
        try:
            self.dot.itemconfigure(self._dot, fill=self.STATUS_COLORS.get(kind, "#9aa0a6"))
        except Exception:  # noqa: BLE001
            pass

    # ---------- PDF / preview ----------
    def pick_pdf(self):
        path = filedialog.askopenfilename(title=self.t('Odaberi PDF'),
                                          filetypes=[("PDF", "*.pdf")])
        if not path:
            return
        self.pdf.set(path)
        self.load_pdf()

    def load_pdf(self):
        path = self.pdf.get().strip()
        if not path or not os.path.exists(path):
            return
        self.log.delete("1.0", "end")  # novi dokument -> prazan desni panel
        self.set_status(self.t('Učitavam PDF…'), "busy")
        if pdfium is None:
            self.info.set(self.t('PDF učitan (bez preview-a)'))
            return
        try:
            self.doc = pdfium.PdfDocument(path)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(self.t('Greška'),
                                 self.t('Ne mogu otvoriti PDF:\n{x}').format(x=exc))
            return
        if hasattr(self.doc, "init_forms"):
            try:
                self.doc.init_forms()  # bez ovoga PDFium ne crta potpise/anotacije
            except Exception:  # noqa: BLE001
                pass
        self.page_count = len(self.doc)
        self.page_spin.configure(to=self.page_count)
        self.page.set(1)
        self.set_info()
        self.box = None
        self.sel_index = None
        self.render()
        self.root.after_idle(self.render)  # jos jednom kad canvas dobije konacnu velicinu
        self.show_summary()

    def export_preview(self, out_path: str) -> str:
        """Screenshot pregleda: ista slika koju GUI crta, spremljena u PNG.

        Oznaci mrezu okvira, odabrani okvir i (crveno) sve vidljive potpise na stranici.
        """
        if self.doc is None:
            raise RuntimeError("nema ucitanog PDF-a")
        from PIL import ImageDraw, ImageFont

        page_ix = max(self.page.get() - 1, 0)
        # export uvijek ~1400 px sirine (citljivo bez obzira na velicinu prozora)
        k = min(max(2.0, 1400.0 / (self.page_size[0] * self.zoom)), 4.0)
        img = (
            self.doc[page_ix]
            .render(scale=self.zoom * k, rev_byteorder=True, draw_annots=True)
            .to_pil()
            .convert("RGB")
        )
        draw = ImageDraw.Draw(img)

        def px(rect):
            # PDF tocke -> pikseli canvasa (uz Y-flip), pa u export rezoluciju
            return [v * k for v in self.box_canvas(rect, offset=False)]

        grid, sel = self.overlay_rects(offset=False)
        for rect in grid:
            draw.rectangle([v * k for v in rect], outline=_rgb(PALETTE["grid"]), width=1)
        if sel:
            draw.rectangle([v * k for v in sel], outline=_rgb(PALETTE["select"]), width=3)
        try:
            font = ImageFont.truetype(self.mod._default_font(), max(int(self.zoom * k * 11), 12))
        except Exception:  # noqa: BLE001
            font = ImageFont.load_default()
        for pi, rect in sig_rects(self.pdf.get()):
            if pi != page_ix:
                continue
            x1, y1, x2, y2 = px(rect)
            draw.rectangle([x1 - 8, y1 - 8, x2 + 8, y2 + 8], outline=_rgb(PALETTE["select"]), width=5)
            draw.text((max(x1 - 8, 0), max(y1 - 30, 0)), self.t('POTPIS'), font=font,
                      fill=_rgb(PALETTE["select"]))
        img.save(out_path)
        return out_path

    def show_summary(self):
        """Popis potpisa u odabranom PDF-u (brza lokalna provjera, bez mreze)."""
        path = self.pdf.get().strip()
        if not path or not os.path.exists(path):
            return
        trust = self.ns().trust

        def work():
            try:
                rows, nxt, note = self.mod.signature_summary(path, trust)
                text = self.mod.format_summary(rows, nxt, note)
            except Exception:  # noqa: BLE001
                text = self.t('potpisi: greška pri čitanju ({err})').format(
                    err=traceback.format_exc().strip().splitlines()[-1]) + "\n"
            first = text.strip().splitlines()[0] if text.strip() else ""
            self.root.after(0, lambda: (self.write(text), self.set_status(first, "info")))

        threading.Thread(target=work, daemon=True).start()

    def goto_page(self, *_):
        if not self.doc:
            return
        try:
            no = int(self.page.get())
        except tk.TclError:
            return
        no = min(max(no, 1), self.page_count)
        if no != self.page.get():
            self.page.set(no)
        self.sel_index = None
        self.render()

    def step_page(self, delta: int):
        if self.doc:
            self.page.set(min(max(self.page.get() + delta, 1), self.page_count))
            self.goto_page()

    def on_resize(self, _event):
        if self._resize_job:
            self.root.after_cancel(self._resize_job)
        self._resize_job = self.root.after(200, self.render)

    def render(self, *_):
        self._resize_job = None
        if not self.doc:
            return
        no = min(max(self.page.get(), 1), self.page_count)
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        if cw <= 10 or ch <= 10:  # canvas jos nije poslozen (npr. prvi prikaz)
            self.root.after(50, self.render)
            return
        page = self.doc[no - 1]
        self.page_size = page.get_size()
        self.zoom = max(min((cw - 20) / self.page_size[0], (ch - 20) / self.page_size[1]), 0.05)
        self.ox = max((cw - self.page_size[0] * self.zoom) / 2, 0)
        self.oy = max((ch - self.page_size[1] * self.zoom) / 2, 0)
        bitmap = page.render(scale=self.zoom, rev_byteorder=True, draw_annots=True)
        pil = bitmap.to_pil()
        bitmap.close()
        if ImageTk is None:  # Pillow bez Tk podrske: preview nije moguc
            self.set_status(self.t('preview nije dostupan (Pillow bez Tk podrške)'), "warn")
            return
        self._img = ImageTk.PhotoImage(pil)
        self.canvas.configure(scrollregion=(0, 0, self.page_size[0] * self.zoom,
                                            self.page_size[1] * self.zoom))
        self.rebuild_grid()
        self.redraw()
        self.canvas.xview_moveto(0)
        self.canvas.yview_moveto(0)

    def rebuild_grid(self):
        pw, ph = self.page_size
        rows = max(int(self.rows.get() or 1), 1)
        mx, my = MARGIN_X * pw, MARGIN_Y * ph
        gx, gy = GAP_X * pw, GAP_Y * ph
        cw = ((pw - 2 * mx) - gx * (GRID_COLS - 1)) / GRID_COLS
        ch = ((ph - 2 * my) - gy * (rows - 1)) / rows
        boxes = []
        for r in range(rows):
            for c in range(GRID_COLS):
                x1 = mx + c * (cw + gx)
                y1 = ph - my - (r + 1) * ch - r * gy
                boxes.append((x1, y1, x1 + cw, y1 + ch))
        self.grid_boxes = boxes
        idx = grid_index_after_resize(boxes, rows, getattr(self, "_grid_rows", None),
                                      self.sel_index, self.box)
        if idx is not None and boxes:
            self.sel_index = idx
            self.box = boxes[idx]      # okvir se smanji/poveca s celijom
        elif not boxes:
            self.box, self.sel_index = None, None
        self._grid_rows = rows

    def to_canvas(self, x, y):
        return self.ox + x * self.zoom, self.oy + (self.page_size[1] - y) * self.zoom

    def overlay_rects(self, offset: bool = True):
        """Pravokutnici overlaya (mreza, odabrani okvir) - jedan izvor istine za canvas i export."""
        grid = [self.box_canvas(b, offset=offset) for b in self.grid_boxes]
        sel = self.box_canvas(self.box, offset=offset) if self.box else None
        return grid, sel

    def box_canvas(self, b, offset: bool = True):
        """(x1,y1,x2,y2) u PDF tockama -> (x1,y1,x2,y2) u pikselima canvasa."""
        x1, y1 = self.to_canvas(b[0], b[3])
        x2, y2 = self.to_canvas(b[2], b[1])
        if not offset:
            x1, x2 = x1 - self.ox, x2 - self.ox
            y1, y2 = y1 - self.oy, y2 - self.oy
        return x1, y1, x2, y2

    def to_pdf(self, cx, cy):
        return (cx - self.ox) / self.zoom, self.page_size[1] - (cy - self.oy) / self.zoom

    def redraw(self):
        self.canvas.delete("all")
        if self._img is not None:
            w = self.page_size[0] * self.zoom
            h = self.page_size[1] * self.zoom
            ox, oy = self.ox, self.oy
            self.canvas.create_rectangle(ox + 5, oy + 5, ox + w + 6, oy + h + 6,
                                         fill=PALETTE["shadow1"], outline="")
            self.canvas.create_rectangle(ox + 2, oy + 2, ox + w + 3, oy + h + 3,
                                         fill=PALETTE["shadow2"], outline="")
            self.canvas.create_image(ox, oy, anchor="nw", image=self._img)
            self.canvas.create_rectangle(ox, oy, ox + w, oy + h, outline=PALETTE["border"])
        grid, sel = self.overlay_rects()
        for rect in grid:
            self.canvas.create_rectangle(*rect, outline=PALETTE["grid"], dash=(4, 4), width=1)
        if sel:
            self.canvas.create_rectangle(*sel, outline=PALETTE["select"], width=3,
                                         fill=PALETTE["select_fill"] if self.sel_index is not None else "")
            x1, y1, x2, y2 = self.box
            self.boxinfo.set(self.t('okvir (pt): {box}  ·  stranica {page}/{total}').format(
                box=f"{x1:.0f},{y1:.0f},{x2:.0f},{y2:.0f}",
                page=self.page.get(), total=self.page_count or 1))
        else:
            self.boxinfo.set(self.t('klikni okvir na stranici da odabereš mjesto potpisa'))

    def _hit(self, cx, cy):
        for i, b in enumerate(self.grid_boxes):
            x1, y1, x2, y2 = self.box_canvas(b)
            if x1 <= cx <= x2 and y1 <= cy <= y2:
                return i
        return None

    def on_press(self, event):
        if not self.grid_boxes:
            return
        idx = self._hit(event.x, event.y)
        if idx is not None:
            self.sel_index = idx
            self.box = self.grid_boxes[idx]
            self.visible.set(True)
            self.redraw()
        self.drag_from = (event.x, event.y)

    def on_drag(self, event):
        if not self.drag_from or not self.box:
            return
        dx = (event.x - self.drag_from[0]) / self.zoom
        dy = (event.y - self.drag_from[1]) / self.zoom
        self.drag_from = (event.x, event.y)
        x1, y1, x2, y2 = self.box
        pw, ph = self.page_size
        w, h = x2 - x1, y2 - y1
        x1 = min(max(x1 + dx, 0), pw - w)
        y1 = min(max(y1 - dy, 0), ph - h)
        self.box = (x1, y1, x1 + w, y1 + h)
        self.sel_index = None
        self.redraw()

    def on_release(self, _event):
        self.drag_from = None

    # ---------- log + akcije ----------
    def write(self, text: str):
        for line in text.splitlines(keepends=True):
            low = line.lower()
            tag = ()
            if line.startswith("==="):
                tag = ("head",)
            elif "upozorenje" in low:
                tag = ("warn",)
            elif any(w in low for w in _TAG_WORDS_ERR):
                tag = ("err",)
            elif "potpisano" in low or "trusted" in low or "valid" in low:
                tag = ("ok",)
            self.log.insert("end", line, tag)
        self.log.see("end")

    def ns(self, **over) -> SimpleNamespace:
        """Zadane vrijednosti dolaze iz pravog argparse parsera (nema drifta)."""
        args = self.mod.build_parser().parse_args([])
        args.cmd = "sign"
        for key, value in over.items():
            setattr(args, key, value)
        return args

    def _run_async(self, func, opts, done_text: str, out: str | None = None):
        self.sign_btn.set_enabled(False)
        self.set_status(self.t('Radi…'), "busy")
        self.write(f"\n=== {done_text} ===\n")

        def work():
            buf = io.StringIO()
            rc = 1
            try:
                with contextlib.redirect_stdout(buf):
                    rc = func(opts)
            except BaseException:  # noqa: BLE001
                buf.write("\n" + traceback.format_exc())
            self.root.after(0, lambda: self._finished(buf.getvalue(), rc, done_text, out))

        threading.Thread(target=work, daemon=True).start()

    def _finished(self, text: str, rc: int, done_text: str, out: str | None):
        self.write(text)
        self.sign_btn.set_enabled(True)
        self.set_status(
            f"{done_text}: {self.t('OK') if rc == 0 else self.t('greška (vidi log)')}",
            "ok" if rc == 0 else "err",
        )
        if rc == 0 and out and self.open_after.get() and os.path.exists(out):
            open_file(out)

    def do_sign(self):
        pdf = self.pdf.get().strip()
        if not pdf or not os.path.exists(pdf):
            messagebox.showerror(self.t('Greška'), self.t('Odaberi postojeći PDF.'))
            return
        if not self.pin.get():
            messagebox.showerror(self.t('Greška'), self.t('Upiši PIN kartice.'))
            return
        if self.visible.get() and self.box is None:
            messagebox.showerror(self.t('Greška'), self.t('Odaberi okvir za potpis na stranici (ili isključi vidljivi potpis).'))
            return
        out = pdf[:-4] + "-signed.pdf" if pdf.lower().endswith(".pdf") else pdf + "-signed.pdf"
        token = self.token.get()
        box = ",".join(f"{v:.0f}" for v in self.box) if self.box else "50,60,350,140"
        opts = self.ns(
            cmd="sign", input=pdf, out=out, token=token,
            ku="non_repudiation" if token.startswith("Sig") else "digital_signature",
            visible=self.visible.get(), page=self.page.get(), box=box,
        )
        os.environ["POTPIS_PIN"] = self.pin.get()
        self.write(self.t('potpisujem: {pdf}\n  -> {out}\n  okvir: {box} (stranica {page})').format(pdf=pdf, out=out, box=box, page=self.page.get()) + "\n")

        def runner(o):
            try:
                return self.mod.cmd_sign(o)
            finally:
                os.environ.pop("POTPIS_PIN", None)

        self._run_async(runner, opts, self.t('potpis'), out=out)

    def do_verify(self):
        pdf = self.pdf.get().strip()
        if not pdf or not os.path.exists(pdf):
            messagebox.showerror(self.t('Greška'), self.t('Odaberi PDF koji želiš provjeriti.'))
            return
        self._run_async(self.mod.cmd_verify, self.ns(cmd="verify", input=pdf),
                        self.t('provjera'))


def build_gui_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="potpisi-gui.py",
        description="GUI za lokalno potpisivanje PDF-a karticom (neslužbeni alat).",
    )
    ap.add_argument("pdf", nargs="?", help="PDF koji se odmah ucita")
    ap.add_argument("--lang", choices=["hr", "en"], default=None, help="jezik sucelja")
    ap.add_argument("--lib", default=None, help="putanja do PKCS#11 modula (default: autodetekcija)")
    ap.add_argument("--ca-dir", default=None, dest="ca_dir", help="mapa s AKD certifikatima")
    ap.add_argument("--offline", action="store_true",
                    help="nijedan mrezni poziv (bez TSA/ziga, LT/LTA i dohvata)")
    ap.add_argument("--screenshot", default=None, metavar="PNG",
                    help="izvezi pregled u PNG i izađi (bez otvaranja prozora)")
    return ap


def main() -> int:
    args = build_gui_parser().parse_args()
    if args.lib:
        os.environ["POTPIS_LIB"] = args.lib
    if args.ca_dir:
        os.environ["POTPIS_CA_DIR"] = args.ca_dir
    if args.offline:
        os.environ["POTPIS_OFFLINE"] = "1"

    root = tk.Tk()
    app = App(root)
    if args.lang and args.lang != app.lang:
        app.change_language(args.lang)

    if args.screenshot:
        root.geometry("1200x820+3000+3000")  # izvan ekrana, samo za export
        if args.pdf:
            app.pdf.set(args.pdf)
            app.load_pdf()
        root.update()

        def do_export():
            try:
                app.export_preview(args.screenshot)
                print("screenshot ->", args.screenshot)
            finally:
                root.quit()

        root.after(1500, do_export)
        root.mainloop()
        root.destroy()
        return 0

    if args.pdf and os.path.exists(args.pdf):
        app.pdf.set(args.pdf)
        app.load_pdf()
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
