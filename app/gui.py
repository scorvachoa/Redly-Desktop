"""Interfaz gráfica (Tkinter) de Red.ly.

Minimalista y moderna: sidebar izquierdo con las redes sociales para cambiar
fácilmente de una a otra, tarjetas con esquinas redondeadas, tipografía del
sistema y un acento de color. Toda la red se ejecuta en hilos secundarios y
los resultados vuelven a la interfaz mediante una cola + ``after()``.
"""

from __future__ import annotations

import io
import logging
import os
import queue
import subprocess
import sys
import threading
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import tkinter as tk
from tkinter import ttk

try:  # opcional: sólo para la miniatura de la GUI
    from PIL import Image, ImageOps, ImageTk

    PIL_AVAILABLE = True
except ImportError:  # pragma: no cover - depende del entorno
    PIL_AVAILABLE = False

import requests

from . import config
from .batch import (
    BatchItemResult,
    BatchReport,
    BatchRunner,
    Manifest,
    item_output_path,
    join_output_dir,
    summarize,
)
from .client import (
    TikVidClient,
    input_to_profile_url,
    is_profile_url,
    is_tiktok_url,
    profile_folder_name,
)
from .downloader import DownloadManager, build_filename
from .exceptions import TikVidError
from .models import ProfileVideo, RedirectInfo, TikTokResult
from .platforms import PLATFORMS, get_platform, is_ready
from .savetik import SavetikSource, search_any

logger = logging.getLogger("tikvid.gui")

FONT = "Segoe UI" if sys.platform == "win32" else "Helvetica"
PLACEHOLDER = "https://www.tiktok.com/@usuario   (vídeo o perfil)"
DEFAULT_PROFILE_WANTED = 100

THUMB_W = 132
THUMB_H = 176
POLL_MS = 80
ANIM_MS = 30

# --- Sistema de diseño -------------------------------------------------
RADIUS = 12
RADIUS_SM = 8
SIDEBAR_W = 76
PAD_X = 40
ICON_PX = 28
ICON_BTN_PX = 26
ICONS_DIR = Path(__file__).resolve().parent.parent / "assets" / "icons"


def draw_round_rect(
    canvas: tk.Canvas,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    r: float = RADIUS,
    **kwargs: Any,
) -> int:
    """Dibuja un rectángulo redondeado en un Canvas (suavizado)."""
    r = min(r, (x2 - x1) / 2, (y2 - y1) / 2)
    points = [
        x1 + r, y1,
        x2 - r, y1,
        x2, y1,
        x2, y1 + r,
        x2, y2 - r,
        x2, y2,
        x2 - r, y2,
        x1 + r, y2,
        x1, y2,
        x1, y2 - r,
        x1, y1 + r,
        x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)


def tint_photo(base: Any, alpha: Any, color: str) -> Any:
    """Crea un PhotoImage tintado de ``base`` conservando su canal alfa."""
    rgb = (int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16))
    tinted = Image.new("RGBA", base.size, (*rgb, 0))
    tinted.putalpha(alpha)
    return ImageTk.PhotoImage(tinted)


@dataclass(frozen=True)
class Theme:
    """Paleta de colores de la interfaz."""

    label: str
    colors: dict[str, str]


LIGHT = Theme(
    "Claro",
    {
        "page": "#F3F4F7",
        "surface": "#FFFFFF",
        "border": "#E2E3E9",
        "hover": "#F7F8FA",
        "text": "#14141A",
        "muted": "#6E6E7C",
        "placeholder": "#A2A2AE",
        "entry": "#FFFFFF",
        "accent": "#FE2C55",
        "accent_text": "#FFFFFF",
        "accent_pressed": "#D92148",
        "ok": "#128A5C",
        "error": "#D33A3A",
        "thumb_bg": "#ECECF1",
        "rail": "#ECECF1",
        "rail_active": "#FFFFFF",
    },
)

DARK = Theme(
    "Oscuro",
    {
        "page": "#0E0E11",
        "surface": "#16161B",
        "border": "#26262F",
        "hover": "#1C1C24",
        "text": "#ECECF2",
        "muted": "#93939F",
        "placeholder": "#6B6B78",
        "entry": "#1B1B22",
        "accent": "#FF3B62",
        "accent_text": "#FFFFFF",
        "accent_pressed": "#DB2449",
        "ok": "#2FB37E",
        "error": "#FF6B6B",
        "thumb_bg": "#1F1F27",
        "rail": "#121218",
        "rail_active": "#1E1E26",
    },
)


def format_bytes(value: int | None) -> str:
    """Convierte bytes en una cadena legible (``1.2 MB``)."""
    if value is None:
        return "?"
    amount = float(value)
    units = ("B", "KB", "MB", "GB")
    index = 0
    while amount >= 1024 and index < len(units) - 1:
        amount /= 1024
        index += 1
    if index == 0:
        return f"{int(amount)} {units[index]}"
    return f"{amount:.1f} {units[index]}"


def open_in_file_manager(path: Path) -> None:
    """Abre la carpeta que contiene ``path`` en el explorador del sistema."""
    folder = path if path.is_dir() else path.parent
    if sys.platform == "win32":
        os.startfile(str(folder))  # noqa: S606 - apertura local del explorador
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(folder)])
    else:
        subprocess.Popen(["xdg-open", str(folder)])


class RedlyGUI(tk.Tk):
    """Ventana principal del descargador Red.ly."""

    def __init__(self, *, debug: bool = False) -> None:
        super().__init__()
        self.debug = debug
        self.title("Red.ly")
        self.geometry("940x760")
        self.minsize(740, 640)
        self.configure(bg=LIGHT.colors["page"])

        self._settings = config.load_settings()
        saved_theme = str(self._settings.get("theme", "light")).lower()
        self._theme: Theme = DARK if saved_theme == "dark" else LIGHT
        self._paint: list[tuple[tk.Widget, str, str | None]] = []
        self._queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._busy = False
        self._busy_offset = 0
        self._progress: tuple[int, int | None] = (0, None)
        self._progress_known = False
        self._result: TikTokResult | None = None
        self._selected: int | None = None
        self._cards: list[tuple[tk.Frame, tk.Frame, int]] = []
        self._thumb_photo: Any = None
        self._saved_path: Path | None = None
        self._placeholder_active = False
        self._placeholder_text = PLACEHOLDER
        self._profile_items: list[ProfileVideo] | None = None
        self._batch_pos: tuple[int, int] | None = None
        self._profile_subdir: str | None = None
        self._profile_rows: list[tuple[tk.Frame, tk.BooleanVar, str]] = []
        self._done_ids: set[str] = set()
        self._checked_ids: set[str] = set()
        self._profile_pages = 0
        self._profile_skipped = 0

        self._active_platform = "tiktok"
        self._platform_items: dict[str, dict[str, Any]] = {}
        self._icon_images: dict[tuple[str, str], Any] = {}

        self.manifest = Manifest()
        self.client = TikVidClient(debug=debug)
        self.source = SavetikSource(debug=debug)
        self.manager = DownloadManager(debug=debug)

        self._handlers: dict[str, Callable[[Any], None]] = {
            "result": self._on_result,
            "profile": self._on_profile,
            "status": self._on_status_message,
            "resolved": self._on_resolved,
            "progress": self._on_progress,
            "saved": self._on_saved,
            "item_start": self._on_item_start,
            "item_done": self._on_item_done,
            "batch_done": self._on_batch_done,
            "error": self._on_error,
        }

        self._build_sidebar()
        self._build_right()
        self._build_header()
        self._build_body()
        self._apply_theme()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(POLL_MS, self._poll)
        self.after(ANIM_MS, self._animate)

    # ==================================================================
    # Construcción de la interfaz
    # ==================================================================
    def _track(self, widget: tk.Widget, bg: str, fg: str | None = None) -> tk.Widget:
        self._paint.append((widget, bg, fg))
        return widget

    def _label(
        self,
        parent: tk.Widget,
        text: str,
        *,
        size: int = 11,
        weight: str = "normal",
        bg: str = "page",
        fg: str = "text",
        **kwargs: Any,
    ) -> tk.Label:
        kwargs.setdefault("anchor", "w")
        kwargs.setdefault("justify", "left")
        widget = tk.Label(parent, text=text, font=(FONT, size, weight), **kwargs)
        return self._track(widget, bg, fg)  # type: ignore[return-value]

    def _frame(self, parent: tk.Widget, bg: str = "page", **kwargs: Any) -> tk.Frame:
        widget = tk.Frame(parent, **kwargs)
        return self._track(widget, bg)  # type: ignore[return-value]

    def _separator(self, parent: tk.Widget) -> tk.Frame:
        widget = tk.Frame(parent, height=1)
        return self._track(widget, "border")  # type: ignore[return-value]

    def _button(
        self,
        parent: tk.Widget,
        text: str,
        command: Callable[[], None],
        *,
        style: str = "primary",
    ) -> tk.Button:
        primary = style == "primary"
        widget = tk.Button(
            parent,
            text=text,
            font=(FONT, 11, "bold"),
            command=command,
            bd=0,
            relief="flat",
            cursor="hand2",
            padx=26,
            pady=12,
            activeforeground=self._theme.colors["text"] if not primary else self._theme.colors["accent_text"],
        )
        self._track(widget, "accent" if primary else "page", "accent_text" if primary else "muted")
        return widget

    @staticmethod
    def _set_button_enabled(
        button: tk.Button, enabled: bool, colors: dict[str, str], primary: bool = True
    ) -> None:
        if primary:
            bg = colors["accent"] if enabled else colors["border"]
            fg = colors["accent_text"] if enabled else colors["muted"]
        else:
            bg = colors["page"] if enabled else colors["page"]
            fg = colors["muted"] if enabled else colors["border"]
        button.configure(
            state="normal" if enabled else "disabled",
            bg=bg,
            fg=fg,
            activebackground=colors["accent_pressed"] if enabled else bg,
            cursor="hand2" if enabled else "arrow",
        )

    # ==================================================================
    # Sidebar (rail de plataformas)
    # ==================================================================
    def _build_sidebar(self) -> None:
        colors = self._theme.colors
        self._sidebar = tk.Frame(self, width=SIDEBAR_W, bg=colors["rail"])
        self._sidebar.pack(side="left", fill="y")
        self._sidebar.pack_propagate(False)
        self._track(self._sidebar, "rail")

        brand = tk.Label(
            self._sidebar,
            text="",
            bg=colors["rail"],
            cursor="hand2",
        )
        brand.pack(pady=(26, 4))
        self._brand_label = brand
        self._track(brand, "rail")
        brand.bind("<Button-1>", lambda _e: self._set_status("Red.ly · Escritorio", "ok"))

        tk.Frame(self._sidebar, height=1, bg=colors["border"]).pack(fill="x", padx=18, pady=(10, 14))
        self._sidebar_sep_top = self._sidebar.winfo_children()[-1]
        self._track(self._sidebar_sep_top, "border")

        for platform in PLATFORMS:
            self._build_platform_button(platform)

        spacer = tk.Frame(self._sidebar, bg=colors["rail"])
        spacer.pack(fill="both", expand=True)
        self._spacer = spacer
        self._track(spacer, "rail")

        self._about_button = tk.Label(
            self._sidebar,
            text="",
            font=(FONT, 13),
            bg=colors["rail"],
            fg=colors["muted"],
            cursor="hand2",
            width=26,  # en Label con imagen Tk lo interpreta en píxeles
            pady=6,
        )
        self._about_button.pack(side="bottom", pady=(0, 14))
        self._track(self._about_button, "rail", "muted")
        self._about_button.bind("<Button-1>", lambda _e: self._show_about())
        self._bind_hover(self._about_button, "about")

        self._theme_button = tk.Label(
            self._sidebar,
            text="",
            font=(FONT, 15),
            bg=colors["rail"],
            fg=colors["muted"],
            cursor="hand2",
            width=26,
            pady=8,
        )
        self._theme_button.pack(side="bottom", pady=(0, 18))
        self._track(self._theme_button, "rail", "muted")
        self._theme_button.bind("<Button-1>", lambda _e: self._toggle_theme())
        self._bind_hover(self._theme_button, "theme")

    def _bind_hover(self, widget: tk.Label, icon: str | None = None) -> None:
        def enter(_e: tk.Event) -> None:
            widget.configure(fg=self._theme.colors["text"])
            if icon is not None:
                self._set_button_icon(widget, icon, "text")

        def leave(_e: tk.Event) -> None:
            widget.configure(fg=self._theme.colors["muted"])
            if icon is not None:
                self._set_button_icon(widget, icon, "muted")

        widget.bind("<Enter>", enter)
        widget.bind("<Leave>", leave)

    def _build_platform_button(self, platform) -> None:
        colors = self._theme.colors
        wrap = tk.Frame(self._sidebar, bg=colors["rail"], height=56)
        wrap.pack(fill="x", pady=2)
        wrap.pack_propagate(False)
        self._track(wrap, "rail")

        canvas = tk.Canvas(
            wrap,
            width=44,
            height=44,
            highlightthickness=0,
            bd=0,
            bg=colors["rail"],
            cursor="hand2",
        )
        canvas.pack(pady=6)
        self._track(canvas, "rail")

        def redraw(_event: tk.Event | None = None, p=platform, c=canvas, w=wrap) -> None:
            self._redraw_platform_item(p, c, w)

        canvas.bind("<Configure>", redraw)
        for widget in (canvas, wrap):
            widget.bind(
                "<Button-1>",
                lambda _e, p=platform: self._on_platform_click(p.slug),
            )
            widget.bind(
                "<Enter>",
                lambda _e, p=platform, c=canvas, w=wrap: self._hover_platform(
                    p, c, w, True
                ),
            )
            widget.bind(
                "<Leave>",
                lambda _e, p=platform, c=canvas, w=wrap: self._hover_platform(
                    p, c, w, False
                ),
            )

        self._platform_items[platform.slug] = {
            "platform": platform,
            "canvas": canvas,
            "wrap": wrap,
            "hover": False,
        }

    def _build_icon_images(self) -> None:
        """Carga los PNG de ``assets/icons`` y crea variantes tintadas."""
        self._icon_images = {}
        if not PIL_AVAILABLE:
            return
        colors = self._theme.colors
        brand_path = ICONS_DIR / "fire.png"
        if brand_path.exists():
            try:
                fire = Image.open(brand_path).convert("RGBA").resize(
                    (30, 30), Image.LANCZOS
                )
                brand_img = ImageTk.PhotoImage(fire)
                self._icon_images[("__brand__", "original")] = brand_img
                label = getattr(self, "_brand_label", None)
                if label is not None:
                    label.configure(image=brand_img)
            except OSError:
                logger.warning("No se pudo cargar el icono %s", brand_path)
        for platform in PLATFORMS:
            path = ICONS_DIR / f"{platform.slug}.png"
            if not path.exists():
                continue
            try:
                base = Image.open(path).convert("RGBA").resize(
                    (ICON_PX, ICON_PX), Image.LANCZOS
                )
            except OSError:
                logger.warning("No se pudo cargar el icono %s", path)
                continue
            alpha = base.getchannel("A")
            self._icon_images[(platform.slug, "original")] = ImageTk.PhotoImage(base)
            for role, color in (
                ("muted", colors["muted"]),
                ("text", colors["text"]),
                ("inverse", "#FFFFFF"),
            ):
                self._icon_images[(platform.slug, role)] = tint_photo(
                    base, alpha, color
                )
        self._build_button_icons(colors)

    def _build_button_icons(self, colors: dict[str, str]) -> None:
        """Iconos PNG de los botones del sidebar (candado, estrella, info, modos)."""
        mapping = {
            "about": "info.png",
            # se muestra el modo al que se cambiará al pulsar
            "theme": "dark-mode.png" if self._theme is LIGHT else "light-mode.png",
        }
        for key, filename in mapping.items():
            path = ICONS_DIR / filename
            if not path.exists():
                continue
            try:
                base = Image.open(path).convert("RGBA").resize(
                    (ICON_BTN_PX, ICON_BTN_PX), Image.LANCZOS
                )
            except OSError:
                logger.warning("No se pudo cargar el icono %s", path)
                continue
            alpha = base.getchannel("A")
            for role in ("muted", "text"):
                self._icon_images[(f"__btn_{key}__", role)] = tint_photo(
                    base, alpha, colors[role]
                )

    def _set_button_icon(self, widget: tk.Label, key: str, role: str) -> None:
        img = self._icon_images.get((f"__btn_{key}__", role))
        if img is not None:
            try:
                widget.configure(image=img)
            except tk.TclError:
                pass

    def _refresh_button_icons(self) -> None:
        for key, attr in (
            ("about", "_about_button"),
            ("theme", "_theme_button"),
        ):
            button = getattr(self, attr, None)
            if button is not None:
                self._set_button_icon(button, key, "muted")

    def _draw_platform_icon(
        self, canvas, platform, cx: float, cy: float, s: float, fg: str, role: str
    ) -> None:
        slug = platform.slug
        img = self._icon_images.get((slug, role))
        if img is not None:
            canvas.create_image(cx, cy, image=img, anchor="center")
            return
        if slug == "youtube":
            w, h = s * 1.6, s * 1.15
            draw_round_rect(
                canvas, cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2,
                r=4, outline=fg, width=2,
            )
            t = s * 0.34
            canvas.create_polygon(
                cx - t * 0.5, cy - t, cx - t * 0.5, cy + t, cx + t * 1.2, cy,
                fill=fg, outline=fg,
            )
        elif slug == "tiktok":
            r = s * 0.4
            canvas.create_oval(
                cx - r * 1.7, cy + r * 0.1, cx + r * 0.3, cy + r * 1.3,
                fill=fg, outline="",
            )
            stem_x = cx + r * 0.3
            canvas.create_line(
                stem_x, cy + r, stem_x, cy - s * 0.95,
                fill=fg, width=2, capstyle="round",
            )
            canvas.create_line(
                stem_x, cy - s * 0.95, cx + s * 0.8, cy - s * 0.55,
                fill=fg, width=2, capstyle="round",
            )
        elif slug == "facebook":
            canvas.create_text(
                cx, cy, text="f", font=(FONT, int(s * 1.9), "bold"), fill=fg
            )
        elif slug == "instagram":
            w = s * 1.55
            draw_round_rect(
                canvas, cx - w / 2, cy - w / 2, cx + w / 2, cy + w / 2,
                r=5, outline=fg, width=2,
            )
            r = s * 0.52
            canvas.create_oval(cx - r, cy - r, cx + r, cy + r, outline=fg, width=2)
            d = s * 0.13
            canvas.create_oval(
                cx + w * 0.2 - d, cy - w * 0.28 - d,
                cx + w * 0.2 + d, cy - w * 0.28 + d,
                fill=fg, outline="",
            )
        elif slug == "twitter":
            m = s * 0.7
            canvas.create_line(
                cx - m, cy - m, cx + m, cy + m, fill=fg, width=3, capstyle="round"
            )
            canvas.create_line(
                cx + m, cy - m, cx - m, cy + m, fill=fg, width=3, capstyle="round"
            )
        elif slug == "threads":
            canvas.create_text(
                cx, cy, text="@", font=(FONT, int(s * 1.5), "bold"), fill=fg
            )
        else:
            canvas.create_text(
                cx, cy, text=platform.monogram,
                font=(FONT, 9, "bold"), fill=fg,
            )

    def _redraw_platform_item(self, platform, canvas, wrap) -> None:
        try:
            w = max(canvas.winfo_width(), 1)
            h = max(canvas.winfo_height(), 1)
        except tk.TclError:
            return
        colors = self._theme.colors
        is_active = platform.slug == self._active_platform
        item = self._platform_items.get(platform.slug, {})
        hovering = bool(item.get("hover"))

        canvas.delete("all")
        pad = 2
        bg_role = "rail_active" if is_active else "rail"
        bg = colors[bg_role]
        if is_active:
            # en tema oscuro el icono original (negro) no se ve: se usa blanco
            role = "inverse" if self._theme is DARK else "original"
            fg = "#FFFFFF" if self._theme is DARK else platform.accent
        elif hovering and platform.ready:
            role, fg = "text", colors["text"]
        else:
            role, fg = "muted", colors["muted"]
        draw_round_rect(
            canvas, pad, pad, w - pad, h - pad, r=(w - 2 * pad) / 2,
            fill=bg, outline="",
        )
        if is_active:
            canvas.create_rectangle(0, 10, 3, h - 10, fill=colors["accent"], outline="")
        self._draw_platform_icon(
            canvas, platform, w / 2, h / 2, min(w, h) * 0.42, fg, role
        )
        try:
            wrap.configure(bg=colors["rail"])
        except tk.TclError:
            pass

    def _hover_platform(self, platform, canvas, wrap, hovering: bool) -> None:
        item = self._platform_items.get(platform.slug)
        if item is not None:
            item["hover"] = hovering
        self._redraw_platform_item(platform, canvas, wrap)

    def _refresh_sidebar(self) -> None:
        colors = self._theme.colors
        try:
            self._sidebar.configure(bg=colors["rail"])
        except tk.TclError:
            return
        for child in self._sidebar.winfo_children():
            try:
                cls = child.winfo_class()
                if cls in ("Frame", "Label", "Canvas"):
                    if child in (
                        getattr(self, "_theme_button", None),
                        getattr(self, "_about_button", None),
                    ):
                        child.configure(bg=colors["rail"], fg=colors["muted"])
                    elif child is getattr(self, "_brand_label", None):
                        child.configure(bg=colors["rail"])
                    elif child is getattr(self, "_spacer", None):
                        child.configure(bg=colors["rail"])
                    elif cls == "Frame" and int(child.cget("height") or 0) <= 1:
                        child.configure(bg=colors["border"])
                    elif cls == "Frame":
                        child.configure(bg=colors["rail"])
                    else:
                        child.configure(bg=colors["rail"])
            except tk.TclError:
                continue
        for item in self._platform_items.values():
            self._redraw_platform_item(item["platform"], item["canvas"], item["wrap"])

    def _on_platform_click(self, slug: str) -> None:
        platform = get_platform(slug)
        if platform is None or slug == self._active_platform:
            return
        self._active_platform = slug
        self._refresh_sidebar()
        self._apply_platform_labels()
        if is_ready(platform):
            self._set_status(f"Plataforma activa: {platform.name}", "ok")
        else:
            self._set_status(f"{platform.name} — Próximamente (sólo TikTok disponible)", "muted")

    @staticmethod
    def _platform_placeholder(platform) -> str:
        if platform.slug == "tiktok":
            return PLACEHOLDER
        host = platform.hosts[0] if platform.hosts else "ejemplo.com"
        return f"https://www.{host}/…   (Próximamente)"

    def _apply_platform_labels(self) -> None:
        platform = get_platform(self._active_platform)
        if platform is None:
            return
        name = platform.name
        section = getattr(self, "_url_section_label", None)
        if section is not None:
            section.configure(text=f"URL DE {name.upper()}")
        subtitle = getattr(self, "_header_subtitle", None)
        if subtitle is not None:
            subtitle.configure(text=f"Descargador de {name} · Escritorio")
        empty = getattr(self, "_empty", None)
        if empty is not None:
            if platform.slug == "tiktok":
                empty.configure(
                    text="Introduce una URL pública de TikTok y pulsa Analizar.\n"
                    "Acepta la URL de un vídeo o la de un perfil (@usuario):\n"
                    "los perfiles se descargan en lote, uno a uno.",
                )
            else:
                empty.configure(
                    text=f"{name} está «Próximamente».\n"
                    "Sólo TikTok está implementado hoy.\n"
                    "Vuelve a la pestaña de TikTok para analizar y descargar.",
                )
        self._placeholder_text = self._platform_placeholder(platform)
        ready = is_ready(platform)
        try:
            self._entry.configure(state="normal")
            if self._placeholder_active:
                self._entry.delete(0, tk.END)
                self._entry.insert(0, self._placeholder_text)
            if not ready:
                self._entry.configure(state="disabled")
        except tk.TclError:
            pass
        self._set_button_enabled(self._analyze_button, ready, self._theme.colors)

    def _build_right(self) -> None:
        self._right = self._frame(self, "page")
        self._right.pack(side="left", fill="both", expand=True)

    def _show_coming_soon(self, platform, kind: str = "download") -> None:
        kind_label = "transcripción" if kind == "transcript" else "descarga"
        colors = self._theme.colors
        messagebox = tk.Toplevel(self)
        messagebox.title(f"{platform.name} — Próximamente")
        messagebox.configure(bg=colors["page"])
        messagebox.geometry("440x230")
        messagebox.resizable(False, False)
        messagebox.transient(self)

        card = tk.Frame(messagebox, bg=colors["surface"], highlightthickness=1, highlightbackground=colors["border"])
        card.pack(fill="both", expand=True, padx=16, pady=16)

        header_row = tk.Frame(card, bg=colors["surface"])
        header_row.pack(fill="x", padx=20, pady=(18, 0))
        mono = tk.Label(
            header_row,
            text=platform.monogram,
            font=(FONT, 11, "bold"),
            bg=platform.accent,
            fg="#FFFFFF",
            width=3,
            pady=4,
        )
        mono.pack(side="left")
        tk.Label(
            header_row,
            text=platform.name,
            font=(FONT, 14, "bold"),
            bg=colors["surface"],
            fg=colors["text"],
        ).pack(side="left", padx=(10, 0))

        tk.Label(
            card,
            text=(
                f"La {kind_label} de {platform.name} está «Próximamente».\n"
                f"{platform.blurb}\n\n"
                "Hoy sólo TikTok está implementado: no se inventan endpoints\n"
                "sin una fuente comprobada."
            ),
            font=(FONT, 11),
            bg=colors["surface"],
            fg=colors["muted"],
            justify="left",
        ).pack(anchor="w", padx=20, pady=(12, 0))

        tk.Button(
            card,
            text="Entendido",
            command=messagebox.destroy,
            font=(FONT, 11, "bold"),
            bd=0,
            relief="flat",
            bg=colors["accent"],
            fg=colors["accent_text"],
            activebackground=colors["accent_pressed"],
            cursor="hand2",
            padx=22,
            pady=9,
        ).pack(anchor="e", padx=20, pady=(14, 16))

    def _show_transcript_coming_soon(self) -> None:
        fake = get_platform("tiktok")
        self._show_coming_soon(fake, "transcript")

    def _show_about(self) -> None:
        window = getattr(self, "_about_window", None)
        if window is not None:
            try:
                if window.winfo_exists():
                    window.lift()
                    window.focus_force()
                    return
            except tk.TclError:
                pass
        window = tk.Toplevel(self)
        self._about_window = window
        window.title("Acerca de Red.ly")
        window.configure(bg=self._theme.colors["page"])
        window.geometry("420x215")
        window.resizable(False, False)
        window.transient(self)
        body = tk.Frame(window, bg=self._theme.colors["page"])
        body.pack(fill="both", expand=True, padx=24, pady=24)
        tk.Label(
            body,
            text="Red.ly",
            font=(FONT, 16, "bold"),
            bg=self._theme.colors["page"],
            fg=self._theme.colors["accent"],
        ).pack(anchor="w")
        tk.Label(
            body,
            text=(
                "Descargador de TikTok · Escritorio\n"
                "Versión 0.2.0\n\n"
                "Sólo contenido público; sin evasión de CAPTCHA."
            ),
            font=(FONT, 11),
            bg=self._theme.colors["page"],
            fg=self._theme.colors["muted"],
            justify="left",
        ).pack(anchor="w", pady=(10, 0))
        tk.Label(
            body,
            text="Para más detalles visita:",
            font=(FONT, 11),
            bg=self._theme.colors["page"],
            fg=self._theme.colors["muted"],
        ).pack(anchor="w", pady=(14, 0))
        link = tk.Label(
            body,
            text="https://red-ly.vercel.app/",
            font=(FONT, 11, "bold"),
            bg=self._theme.colors["page"],
            fg=self._theme.colors["accent"],
            cursor="hand2",
        )
        link.pack(anchor="w", pady=(2, 0))
        link.bind("<Button-1>", lambda _e: self._open_website())
        # ajusta la altura real al contenido (el enlace se salía de la ventana)
        window.update_idletasks()
        window.geometry(f"420x{window.winfo_reqheight()}")

    @staticmethod
    def _open_website() -> None:
        webbrowser.open("https://red-ly.vercel.app/")

    def _build_header(self) -> None:
        header = self._frame(self._right, "surface", height=58)
        header.pack(side="top", fill="x")
        header.pack_propagate(False)

        self._label(header, "Red.ly", size=16, weight="bold", bg="surface").pack(
            side="left", padx=(PAD_X, 10)
        )
        self._header_subtitle = self._label(
            header, "Descargador de TikTok · Escritorio", size=10, bg="surface", fg="muted"
        )
        self._header_subtitle.pack(side="left")

        self._separator(self._right).pack(side="top", fill="x")

    def _build_body(self) -> None:
        body = self._frame(self._right, "page")
        body.pack(side="top", fill="both", expand=True)
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(0, weight=1)

        content = self._frame(body, "page")
        content.grid(row=0, column=0, sticky="new", padx=PAD_X, pady=(28, 10))
        content.grid_columnconfigure(0, weight=1)

        # --- URL -------------------------------------------------------
        self._url_section_label = self._label(
            content, "URL DE TIKTOK", size=9, weight="bold", fg="muted"
        )
        self._url_section_label.grid(row=0, column=0, sticky="w", pady=(0, 8))

        url_row = self._frame(content, "page")
        url_row.grid(row=1, column=0, sticky="ew")
        url_row.grid_columnconfigure(0, weight=1)

        entry_wrap = self._frame(url_row, "page")
        entry_wrap.grid(row=0, column=0, sticky="ew")
        entry_wrap.grid_columnconfigure(0, weight=1)
        entry_bg = tk.Canvas(entry_wrap, highlightthickness=0, bd=0, bg=self._theme.colors["border"])
        entry_bg.place(x=0, y=0, relwidth=1.0, relheight=1.0)
        entry_bg.bind("<Configure>", lambda _e: self._draw_entry_bg(entry_bg))
        self._entry_bg = entry_bg

        self._entry = tk.Entry(
            entry_wrap, font=(FONT, 12), relief="flat", bd=10, highlightthickness=0
        )
        self._track(self._entry, "entry", "text")
        self._entry.grid(row=0, column=0, sticky="ew", padx=1, pady=1)
        self._entry.bind("<Return>", lambda _event: self.start_analysis())
        self._entry.bind("<FocusIn>", self._clear_placeholder)
        self._entry.bind("<FocusIn>", lambda _e: self._draw_entry_bg(entry_bg, focused=True), add="+")
        self._entry.bind("<FocusOut>", self._show_placeholder)
        self._entry.bind("<FocusOut>", lambda _e: self._draw_entry_bg(entry_bg), add="+")
        self._set_placeholder()

        self._analyze_button = self._button(url_row, "Analizar", self.start_analysis)
        self._analyze_button.grid(row=0, column=1, padx=(12, 0), sticky="nsew")

        # --- Vídeos nuevos a buscar (perfil) ---------------------------
        wanted_row = self._frame(content, "page")
        wanted_row.grid(row=2, column=0, sticky="w", pady=(10, 0))
        self._label(wanted_row, "Vídeos nuevos a buscar", size=10, fg="muted").pack(
            side="left"
        )
        self._wanted_var = tk.IntVar(value=DEFAULT_PROFILE_WANTED)
        self._wanted_spin = tk.Spinbox(
            wanted_row,
            from_=1,
            to=1000,
            textvariable=self._wanted_var,
            width=6,
            font=(FONT, 11),
            relief="flat",
            bd=6,
            highlightthickness=1,
        )
        self._track(self._wanted_spin, "entry", "text")
        self._wanted_spin.pack(side="left", padx=(10, 0))

        self._status = self._label(
            content, "Pega una URL de TikTok para empezar", size=10, fg="muted"
        )
        self._status.grid(row=3, column=0, sticky="w", pady=(14, 0))

        # --- Estado vacío ---------------------------------------------
        self._separator(content).grid(row=4, column=0, sticky="ew", pady=(24, 0))

        self._empty = self._label(
            content,
            "Introduce una URL pública de TikTok y pulsa Analizar.\n"
            "Acepta la URL de un vídeo o la de un perfil (@usuario):\n"
            "los perfiles se descargan en lote, uno a uno.",
            size=11,
            fg="muted",
            justify="center",
            anchor="center",
        )
        self._empty.grid(row=5, column=0, sticky="ew", pady=(48, 48))

        # --- Perfil: historial + lista ---------------------------------
        self._profile_section = self._frame(content, "page")
        self._profile_section.grid(row=6, column=0, sticky="new", pady=(8, 0))
        self._profile_section.grid_columnconfigure(0, weight=1)
        self._profile_section.grid_remove()

        self._profile_stats = self._label(
            content, "", size=10, fg="muted"
        )
        self._profile_stats.grid(row=7, column=0, sticky="w", pady=(10, 0))
        self._profile_stats.grid_remove()

        history_bar = self._frame(content, "page")
        history_bar.grid(row=8, column=0, sticky="ew", pady=(10, 0))
        self._history_bar = history_bar
        history_bar.grid_remove()
        self._reanalyze_button = self._button(
            history_bar, "Volver a analizar", self.start_analysis, style="ghost"
        )
        self._reanalyze_button.pack(side="left")
        self._clear_history_button = self._button(
            history_bar, "Borrar historial", self._clear_history, style="ghost"
        )
        self._clear_history_button.pack(side="left", padx=(10, 0))

        self._profile_canvas = tk.Canvas(
            content, height=220, highlightthickness=0, bd=0
        )
        self._profile_canvas.grid(row=9, column=0, sticky="ew", pady=(10, 0))
        self._profile_canvas.grid_remove()
        self._profile_scroll = ttk.Scrollbar(
            content, orient="vertical", command=self._profile_canvas.yview
        )
        self._profile_scroll.grid(row=9, column=1, sticky="ns")
        self._profile_scroll.grid_remove()
        self._profile_canvas.configure(yscrollcommand=self._profile_scroll.set)
        self._profile_rows_frame = self._frame(self._profile_canvas, "page")
        self._profile_canvas_window = self._profile_canvas.create_window(
            (0, 0), window=self._profile_rows_frame, anchor="nw"
        )
        self._profile_rows_frame.bind(
            "<Configure>",
            lambda _e: self._profile_canvas.configure(
                scrollregion=self._profile_canvas.bbox("all")
            ),
        )
        self._profile_canvas.bind(
            "<Configure>",
            lambda _e: self._profile_canvas.itemconfigure(
                self._profile_canvas_window, width=_e.width
            ),
        )

        # --- Resultado -------------------------------------------------
        self._result_card = self._frame(content, "surface")
        self._result_card.grid(row=10, column=0, sticky="ew", pady=(4, 0))
        self._result_card.grid_remove()
        self._result_bg = tk.Canvas(
            self._result_card, highlightthickness=0, bd=0, bg=self._theme.colors["surface"]
        )
        self._result_bg.place(x=0, y=0, relwidth=1.0, relheight=1.0)
        self._result_bg.bind("<Configure>", lambda _e: self._draw_result_card_bg())

        thumb_holder = self._frame(self._result_card, "thumb_bg")
        thumb_holder.place(x=0, y=0, width=THUMB_W, relheight=1.0)
        self._thumb_label = self._label(
            thumb_holder,
            "Sin vista\nprevia",
            size=10,
            bg="thumb_bg",
            fg="muted",
            justify="center",
            anchor="center",
        )
        self._thumb_label.place(relx=0.5, rely=0.5, anchor="center")

        info = self._frame(self._result_card, "surface")
        info.grid(row=0, column=1, sticky="nsew", padx=(THUMB_W + 26, 24), pady=22)
        self._result_card.grid_columnconfigure(1, weight=1)

        self._title_label = self._label(info, "", size=14, weight="bold")
        self._title_label.grid(row=0, column=0, sticky="ew")

        self._meta_label = self._label(info, "", size=10, fg="muted")
        self._meta_label.grid(row=1, column=0, sticky="w", pady=(10, 0))

        # --- Opciones ---------------------------------------------------
        self._options_title = self._label(
            content, "OPCIONES DE DESCARGA", size=9, weight="bold", fg="muted"
        )
        self._options_title.grid(row=11, column=0, sticky="w", pady=(26, 0))
        self._options_title.grid_remove()

        self._options_list = self._frame(content, "page")
        self._options_list.grid(row=12, column=0, sticky="new", pady=(10, 0))
        self._options_list.grid_remove()
        self._options_list.grid_columnconfigure(0, weight=1)

        # --- Acciones ---------------------------------------------------
        actions = self._frame(content, "page")
        actions.grid(row=13, column=0, sticky="ew", pady=(20, 0))
        actions.grid_columnconfigure(0, weight=1)
        self._actions = actions
        actions.grid_remove()

        self._download_button = self._button(
            actions, "Descargar", self.start_download
        )
        self._download_button.grid(row=0, column=0, sticky="w")
        self._download_button.configure(state="disabled")

        self._folder_button = self._button(
            actions, "Abrir carpeta", self._open_folder, style="ghost"
        )
        self._folder_button.grid(row=0, column=1, padx=(12, 0), sticky="w")
        self._folder_button.grid_remove()

        # --- Progreso ---------------------------------------------------
        progress = self._frame(content, "page")
        progress.grid(row=14, column=0, sticky="ew", pady=(20, 0))
        progress.grid_columnconfigure(0, weight=1)
        self._progress_frame = progress
        self._progress_frame.grid_remove()

        self._bar = tk.Canvas(progress, height=8, highlightthickness=0, bd=0)
        self._track(self._bar, "page")
        self._bar.grid(row=0, column=0, sticky="ew")
        self._bar.bind("<Configure>", lambda _e: self._draw_bar())

        self._progress_text = self._label(progress, "", size=10, fg="muted")
        self._progress_text.grid(row=1, column=0, sticky="w", pady=(8, 0))

        # --- Pie ---------------------------------------------------------
        self._footer = self._label(body, "", size=10, fg="error")
        self._footer.grid(row=1, column=0, sticky="new", padx=PAD_X, pady=(0, 18))
        self._footer.grid_remove()

    # ==================================================================
    # Tema
    # ==================================================================
    def _toggle_theme(self) -> None:
        self._theme = DARK if self._theme is LIGHT else LIGHT
        self._settings["theme"] = "dark" if self._theme is DARK else "light"
        config.save_settings(self._settings)
        self._apply_theme()

    def _prune_paint(self) -> None:
        """Descarta widgets destruidos de la lista de repintado."""
        alive: list[tuple[tk.Widget, str, str | None]] = []
        for widget, bg_role, fg_role in self._paint:
            try:
                if widget.winfo_exists():
                    alive.append((widget, bg_role, fg_role))
            except tk.TclError:
                continue
        self._paint = alive

    def _apply_theme(self) -> None:
        colors = self._theme.colors
        self.configure(bg=colors["page"])
        self._build_icon_images()
        self._refresh_button_icons()
        self._prune_paint()
        for widget, bg_role, fg_role in self._paint:
            self._paint_widget(widget, colors, bg_role, fg_role)
        if self._placeholder_active:
            self._entry.configure(fg=colors["placeholder"])
        self._draw_bar()
        self._draw_result_card_bg()
        entry_bg = getattr(self, "_entry_bg", None)
        if entry_bg is not None:
            try:
                self._draw_entry_bg(entry_bg)
            except tk.TclError:
                pass
        self._refresh_sidebar()
        self._refresh_cards()
        self._refresh_profile_rows()

    @staticmethod
    def _paint_widget(
        widget: tk.Widget,
        colors: dict[str, str],
        bg_role: str,
        fg_role: str | None,
    ) -> None:
        try:
            kind = widget.winfo_class()
        except tk.TclError:  # widget destruido
            return
        bg = colors.get(bg_role, colors["page"])
        fg = colors.get(fg_role or "", colors["text"])
        if bg_role == "accent":
            active = colors["accent_pressed"]
        elif bg_role in {"page", "surface", "hover", "entry"}:
            active = colors["hover"]
        else:
            active = bg

        try:
            if kind in ("Label", "Message"):
                widget.configure(bg=bg, fg=fg)  # type: ignore[arg-type]
            elif kind in ("Frame", "Canvas"):
                widget.configure(bg=bg)  # type: ignore[arg-type]
            elif kind == "Entry":
                widget.configure(  # type: ignore[arg-type]
                    bg=bg,
                    fg=fg,
                    insertbackground=fg,
                    highlightbackground=colors["border"],
                    highlightcolor=colors["accent"],
                )
            elif kind == "Spinbox":
                widget.configure(  # type: ignore[arg-type]
                    bg=bg,
                    fg=fg,
                    buttonbackground=bg,
                    insertbackground=fg,
                    highlightbackground=colors["border"],
                    highlightcolor=colors["accent"],
                )
            elif kind == "Checkbutton":
                widget.configure(  # type: ignore[arg-type]
                    bg=bg,
                    fg=fg,
                    selectcolor=colors["surface"],
                    activebackground=bg,
                    activeforeground=colors["accent"],
                    highlightthickness=0,
                )
            elif kind == "Button":
                widget.configure(  # type: ignore[arg-type]
                    bg=bg,
                    fg=fg,
                    activebackground=active,
                    activeforeground=colors["text"],
                    disabledforeground=colors["muted"],
                )
        except tk.TclError:  # widget destruido
            return

    # ==================================================================
    # Placeholder de la entrada
    # ==================================================================
    def _set_placeholder(self) -> None:
        if not self._entry.get():
            self._entry.insert(0, self._placeholder_text)
            self._placeholder_active = True
            self._entry.configure(fg=self._theme.colors["placeholder"])

    def _clear_placeholder(self, _event: tk.Event | None = None) -> None:
        if self._placeholder_active:
            self._entry.delete(0, tk.END)
        self._placeholder_active = False
        self._entry.configure(fg=self._theme.colors["text"])

    def _show_placeholder(self, _event: tk.Event | None = None) -> None:
        if not self._entry.get():
            self._set_placeholder()

    # ==================================================================
    # Estado / utilidades visuales
    # ==================================================================
    def _set_status(self, text: str, role: str = "muted") -> None:
        self._status.configure(text=text, fg=self._theme.colors[role])

    def _set_footer(self, text: str = "", role: str = "error") -> None:
        if text:
            self._footer.configure(text=text, fg=self._theme.colors[role])
            self._footer.grid()
        else:
            self._footer.grid_remove()

    def _set_busy(self, busy: bool, status: str | None = None) -> None:
        self._busy = busy
        colors = self._theme.colors
        self._entry.configure(state="disabled" if busy else "normal")
        self._set_button_enabled(self._analyze_button, not busy, colors, primary=True)
        can_download = (self._result is not None and self._selected is not None) or bool(
            self._profile_items
        )
        self._set_button_enabled(
            self._download_button,
            not busy and can_download,
            colors,
            primary=True,
        )
        if busy:
            self._progress_frame.grid()
        elif self._result is None:
            self._progress_frame.grid_remove()
        if status is not None:
            self._set_status(status)
        self._draw_bar()

    def _draw_bar(self) -> None:
        canvas = self._bar
        try:
            width = max(canvas.winfo_width(), 1)
            height = max(canvas.winfo_height(), 1)
        except tk.TclError:
            return
        colors = self._theme.colors
        canvas.delete("all")
        draw_round_rect(
            canvas, 0, 0, width - 1, height - 1, r=height / 2,
            fill=colors["border"], outline="",
        )

        if self._busy:
            segment = max(int(width * 0.28), 48)
            start = (self._busy_offset % (width + segment)) - segment
            draw_round_rect(
                canvas, start, 0, start + segment, height, r=height / 2,
                fill=colors["accent"], outline="",
            )
            return

        done, total = self._progress
        if self._progress_known and total:
            filled = int(width * min(done / total, 1.0))
            if filled > 0:
                draw_round_rect(
                    canvas, 0, 0, filled, height, r=height / 2,
                    fill=colors["accent"], outline="",
                )

    def _draw_result_card_bg(self) -> None:
        canvas = getattr(self, "_result_bg", None)
        if canvas is None:
            return
        try:
            w = max(canvas.winfo_width(), 1)
            h = max(canvas.winfo_height(), 1)
        except tk.TclError:
            return
        colors = self._theme.colors
        canvas.delete("all")
        canvas.configure(bg=colors["border"])
        draw_round_rect(
            canvas, 1, 1, w - 2, h - 2, r=RADIUS,
            fill=colors["surface"], outline="",
        )

    def _draw_entry_bg(self, canvas: tk.Canvas, *, focused: bool = False) -> None:
        try:
            w = max(canvas.winfo_width(), 1)
            h = max(canvas.winfo_height(), 1)
        except tk.TclError:
            return
        colors = self._theme.colors
        edge = colors["accent"] if focused else colors["border"]
        bw = 2 if focused else 1
        try:
            canvas.configure(bg=edge)
        except tk.TclError:
            return
        canvas.delete("all")
        draw_round_rect(
            canvas, bw, bw, w - 1 - bw, h - 1 - bw, r=RADIUS_SM,
            fill=colors["entry"], outline="",
        )

    def _animate(self) -> None:
        if self._busy:
            self._busy_offset += 7
            self._draw_bar()
        try:
            self.after(ANIM_MS, self._animate)
        except tk.TclError:
            return

    # ==================================================================
    # Hilos de trabajo
    # ==================================================================
    def _spawn(self, target: Callable[[], None]) -> None:
        def runner() -> None:
            try:
                target()
            except TikVidError as exc:
                self._queue.put(("error", str(exc)))
            except Exception as exc:  # pragma: no cover - red / fallo inesperado
                logger.exception("fallo en hilo de trabajo")
                self._queue.put(("error", f"Error inesperado: {exc}"))

        threading.Thread(target=runner, daemon=True).start()

    def _poll(self) -> None:
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                handler = self._handlers.get(kind)
                if handler:
                    handler(payload)
        except queue.Empty:
            pass
        except tk.TclError:
            return
        try:
            self.after(POLL_MS, self._poll)
        except tk.TclError:
            return

    # ==================================================================
    # Análisis
    # ==================================================================
    def start_analysis(self) -> None:
        if self._busy:
            return
        platform = get_platform(self._active_platform)
        if platform is None or not is_ready(platform):
            name = platform.name if platform else ""
            self._set_status(f"{name} — Próximamente (sólo TikTok disponible)", "muted")
            return
        if self._placeholder_active:
            url = ""
        else:
            url = self._entry.get().strip()

        self._set_footer()
        self._folder_button.grid_remove()
        self._progress = (0, None)
        self._progress_known = False
        self._progress_text.configure(text="")
        self._profile_items = None
        self._batch_pos = None
        self._result = None
        self._selected = None
        self._profile_subdir = None
        self._done_ids = set()
        self._checked_ids = set()
        self._download_button.configure(text="Descargar")
        self._rebuild_options()
        self._options_title.grid_remove()
        self._options_list.grid_remove()
        self._result_card.grid_remove()
        self._hide_profile_section()

        try:
            url = input_to_profile_url(url) if url.startswith("@") else url
        except Exception:  # pragma: no cover - normalización defensiva
            pass

        if not is_tiktok_url(url):
            self._set_status("La URL no parece una URL válida de TikTok.", "error")
            return

        if is_profile_url(url):
            self._set_busy(True, "Listando perfil…")
            self._spawn(lambda: self._profile_worker(url))
            return

        self._set_busy(True, "Analizando vídeo…")
        self._spawn(lambda: self._analysis_worker(url))

    def _analysis_worker(self, url: str) -> None:
        result = search_any(url, savetik=self.source, tikvid=self.client, debug=self.debug)
        thumbnail = self._fetch_thumbnail(result.thumbnail)
        self._queue.put(("result", (result, thumbnail)))

    def _profile_worker(self, url: str) -> None:
        subdir = profile_folder_name(url)
        wanted = self._wanted_var.get() or DEFAULT_PROFILE_WANTED
        wanted = max(1, min(int(wanted), 1000))
        download_dir = self.manager.download_dir

        def is_done(item: ProfileVideo) -> bool:
            target = item_output_path(join_output_dir(download_dir, subdir), item)
            return self.manifest.is_done(item.id, target)

        def on_page(page: int, pending: int, skipped: int) -> None:
            self._queue.put(
                (
                    "status",
                    f"Listando perfil… página {page} · {pending} pendiente(s) · "
                    f"{skipped} ya descargado(s)",
                )
            )

        items, skipped, pages = self.source.list_profile_pending(
            url, is_done, max_pending=wanted, on_page=on_page
        )
        items = [item.model_copy(update={"subdir": subdir}) for item in items]
        thumbnail = self._fetch_thumbnail(items[0].thumbnail if items else None)
        self._queue.put(
            ("profile", (items, thumbnail, {"skipped": skipped, "pages": pages, "subdir": subdir}))
        )

    @staticmethod
    def _fetch_thumbnail(url: str | None) -> bytes | None:
        if not url:
            return None
        try:
            response = requests.get(
                url,
                timeout=config.REQUEST_TIMEOUT,
                headers={"User-Agent": config.HEADERS["User-Agent"]},
            )
            if response.status_code != 200 or len(response.content) > 5_000_000:
                return None
            return response.content
        except requests.RequestException:
            return None

    def _on_result(self, payload: tuple[TikTokResult, bytes | None]) -> None:
        result, thumbnail = payload
        self._result = result
        self._set_busy(False)

        self._empty.grid_remove()
        self._hide_profile_section()
        self._result_card.grid()
        self._title_label.configure(text=result.title or "(sin título)")
        self._meta_label.configure(
            text=f"ID  {result.video_id or '—'}      Opciones  {len(result.downloads)}"
        )
        self._set_thumbnail(thumbnail)

        self._rebuild_options()
        self._options_title.grid()
        self._options_list.grid()
        self._actions.grid()

        self._set_status(f"Listo · {len(result.downloads)} opciones encontradas", "ok")
        if result.downloads:
            self._select_option(0)
        else:
            self._set_footer("No se encontraron opciones de descarga.", "error")

    def _on_profile(
        self,
        payload: tuple[list[ProfileVideo], bytes | None, dict],
    ) -> None:
        items, thumbnail, meta = payload
        self._set_busy(False)
        self._set_thumbnail(thumbnail)

        self._profile_subdir = meta.get("subdir")
        self._profile_skipped = int(meta.get("skipped", 0))
        self._profile_pages = int(meta.get("pages", 0))

        if not items and self._profile_skipped > 0:
            self._empty.grid_remove()
            self._result_card.grid()
            self._title_label.configure(text="Sin vídeos pendientes")
            self._meta_label.configure(
                text=f"{self._profile_skipped} ya descargado(s). Usa «Borrar historial» o analiza de nuevo."
            )
            self._set_status("Todo el perfil ya estaba descargado", "ok")
            self._actions.grid()
            self._download_button.configure(text="Descargar 0 vídeos")
            self._set_button_enabled(self._download_button, False, self._theme.colors)
            self._update_history_button()
            return

        if not items:
            self._set_status("El perfil no devolvió vídeos.", "error")
            return

        self._profile_items = items
        self._done_ids = set()
        for item in items:
            entry = self.manifest.get(item.id)
            if entry and entry.get("state") == "ok":
                self._done_ids.add(item.id)
        self._checked_ids = {i.id for i in items if i.id not in self._done_ids}

        self._download_button.configure(
            text=f"Descargar {len(self._checked_ids)} vídeos"
        )

        self._empty.grid_remove()
        self._result_card.grid()
        self._title_label.configure(text=f"Perfil con {len(items)} vídeo(s)")
        dest = str(join_output_dir(self.manager.download_dir, self._profile_subdir))
        self._meta_label.configure(
            text=f"Modo lote  ·  destino {dest}  ·  uno a uno, con pausa"
        )
        self._options_title.grid_remove()
        self._options_list.grid_remove()
        self._actions.grid()

        self._profile_stats.configure(
            text=(
                f"Listados {len(items)}  ·  Pendientes {len(self._checked_ids)}  ·  "
                f"Ya bajados {len(self._done_ids)}  ·  Páginas {self._profile_pages}  ·  "
                f"Omitidos al listar {self._profile_skipped}"
            )
        )
        self._profile_stats.grid()
        self._history_bar.grid()
        self._update_history_button()
        self._build_profile_rows(items)
        self._profile_section.grid()
        self._profile_canvas.grid()
        self._profile_scroll.grid()
        self._set_button_enabled(
            self._download_button,
            not self._busy and bool(self._checked_ids),
            self._theme.colors,
        )
        self._set_status("Perfil listo para descargar", "ok")

    def _hide_profile_section(self) -> None:
        self._profile_section.grid_remove()
        self._profile_stats.grid_remove()
        self._history_bar.grid_remove()
        self._profile_canvas.grid_remove()
        self._profile_scroll.grid_remove()
        self._profile_rows.clear()

    def _build_profile_rows(self, items: list[ProfileVideo]) -> None:
        for widget in self._profile_rows_frame.winfo_children():
            widget.destroy()
        self._profile_rows.clear()
        self._prune_paint()

        for index, item in enumerate(items):
            row = self._build_profile_row(index, item)
            row.grid(row=index, column=0, sticky="ew", pady=(0, 4))
            self._profile_rows_frame.grid_columnconfigure(0, weight=1)

    def _build_profile_row(self, index: int, item: ProfileVideo) -> tk.Frame:
        colors = self._theme.colors
        is_done = item.id in self._done_ids
        row = self._frame(self._profile_rows_frame, "surface")
        row.grid_columnconfigure(1, weight=1)

        bg_canvas = tk.Canvas(row, highlightthickness=0, bd=0, bg=colors["border"])
        bg_canvas.place(x=0, y=0, relwidth=1.0, relheight=1.0)
        bg_canvas.bind(
            "<Configure>",
            lambda _e, c=bg_canvas: self._draw_profile_row_bg(c),
        )

        var = tk.BooleanVar(value=item.id in self._checked_ids)
        check = tk.Checkbutton(
            row,
            variable=var,
            command=lambda i=item.id: self._toggle_checked(i),
            bg=colors["surface"],
            activebackground=colors["surface"],
            highlightthickness=0,
            bd=0,
            state="disabled" if is_done else "normal",
        )
        check.grid(row=0, column=0, padx=(14, 6), pady=8)

        idx_label = self._label(
            row, str(index + 1), size=10, bg="surface", fg="muted", width=3
        )
        idx_label.grid(row=0, column=1, padx=(0, 8))

        body = self._frame(row, "surface")
        body.grid(row=0, column=2, sticky="ew")
        body.grid_columnconfigure(0, weight=1)
        title = self._label(
            body,
            item.display_name(),
            size=11,
            bg="surface",
            fg="muted" if is_done else "text",
        )
        title.grid(row=0, column=0, sticky="w")
        meta_bits = [item.channel or "—"]
        if item.views:
            meta_bits.append(f"{item.views} vistas")
        meta = self._label(body, " · ".join(meta_bits), size=9, bg="surface", fg="muted")
        meta.grid(row=1, column=0, sticky="w")

        if is_done:
            badge = self._label(
                row, "Ya descargado", size=9, bg="surface", fg="ok", anchor="e"
            )
            badge.grid(row=0, column=3, padx=(8, 12))

        fmt = self._label(
            row, (item.format or "mp4").upper(), size=9, bg="surface", fg="muted", anchor="e"
        )
        fmt.grid(row=0, column=4, padx=(4, 14))

        self._draw_profile_row_bg(bg_canvas)
        self._profile_rows.append((row, var, item.id))
        return row

    def _draw_profile_row_bg(self, canvas: tk.Canvas) -> None:
        try:
            w = max(canvas.winfo_width(), 1)
            h = max(canvas.winfo_height(), 1)
        except tk.TclError:
            return
        colors = self._theme.colors
        canvas.delete("all")
        canvas.configure(bg=colors["border"])
        draw_round_rect(
            canvas, 1, 1, w - 2, h - 2, r=RADIUS_SM,
            fill=colors["surface"], outline="",
        )

    def _toggle_checked(self, item_id: str) -> None:
        if item_id in self._done_ids:
            return
        if item_id in self._checked_ids:
            self._checked_ids.discard(item_id)
        else:
            self._checked_ids.add(item_id)
        count = len(self._checked_ids)
        self._download_button.configure(text=f"Descargar {count} vídeos")
        self._set_button_enabled(
            self._download_button,
            not self._busy and count > 0,
            self._theme.colors,
        )

    def _refresh_profile_rows(self) -> None:
        colors = self._theme.colors
        for row, var, item_id in self._profile_rows:
            try:
                if not row.winfo_exists():
                    continue
                for child in row.winfo_children():
                    if isinstance(child, tk.Canvas):
                        self._draw_profile_row_bg(child)
                        break
            except tk.TclError:
                continue

    def _update_history_button(self) -> None:
        count = sum(
            1
            for key in (self.manifest._data.get("items") or {})
            if (self.manifest._data["items"][key] or {}).get("state") == "ok"
        )
        try:
            self._clear_history_button.configure(
                text=f"Borrar historial ({count})",
                state="normal" if count else "disabled",
            )
        except tk.TclError:
            pass

    def _clear_history(self) -> None:
        self.manifest._data["items"] = {}
        self.manifest.save()
        self._done_ids = set()
        if self._profile_items:
            self._checked_ids = {i.id for i in self._profile_items}
            self._build_profile_rows(self._profile_items)
            self._download_button.configure(
                text=f"Descargar {len(self._checked_ids)} vídeos"
            )
            self._profile_stats.configure(
                text=(
                    f"Listados {len(self._profile_items)}  ·  "
                    f"Pendientes {len(self._checked_ids)}  ·  Ya bajados 0  ·  "
                    f"Páginas {self._profile_pages}"
                )
            )
        self._update_history_button()
        self._set_status("Historial borrado.", "ok")

    def _on_status_message(self, text: str) -> None:
        self._set_status(text)

    def _set_thumbnail(self, data: bytes | None) -> None:
        if not data or not PIL_AVAILABLE:
            return
        try:
            image = Image.open(io.BytesIO(data)).convert("RGB")
            image = ImageOps.fit(image, (THUMB_W, THUMB_H), method=Image.LANCZOS)
            self._thumb_photo = ImageTk.PhotoImage(image)
            self._thumb_label.configure(
                image=self._thumb_photo, text="", width=THUMB_W, height=THUMB_H
            )
        except Exception:  # imagen corrupta: se omite la vista previa
            logger.debug("no se pudo cargar la miniatura", exc_info=True)

    # ==================================================================
    # Opciones
    # ==================================================================
    def _rebuild_options(self) -> None:
        for frame, _strip, _index in self._cards:
            frame.destroy()
        self._cards.clear()
        self._selected = None
        self._prune_paint()

        if self._result is None:
            return

        for index, option in enumerate(self._result.downloads):
            card, strip = self._build_option_card(index, option.name, option.label())
            card.grid(row=index, column=0, sticky="ew", pady=(0, 10))
            self._cards.append((card, strip, index))

    def _build_option_card(
        self, index: int, name: str, meta: str
    ) -> tuple[tk.Frame, tk.Canvas]:
        colors = self._theme.colors
        card = self._frame(self._options_list, "surface")
        card.grid_columnconfigure(1, weight=1)

        strip = tk.Canvas(card, highlightthickness=0, bd=0, bg=colors["border"])
        strip.place(x=0, y=0, relwidth=1.0, relheight=1.0)
        strip.bind(
            "<Configure>",
            lambda _e, c=strip, i=index: self._draw_option_card_bg(c, i),
        )

        number = self._label(
            card, str(index + 1), size=12, weight="bold", bg="surface", fg="accent"
        )
        number.grid(row=0, column=0, padx=(20, 12), pady=16, sticky="w")

        label = self._label(card, name, size=12, weight="bold", bg="surface")
        label.grid(row=0, column=1, sticky="w")

        badge = self._label(card, meta, size=10, bg="surface", fg="muted", anchor="e")
        badge.grid(row=0, column=2, padx=(12, 20), sticky="e")

        def select(_event: tk.Event | None = None, chosen: int = index) -> None:
            self._select_option(chosen)

        def enter(_event: tk.Event | None = None, chosen: int = index) -> None:
            if self._selected != chosen:
                self._draw_option_card_bg(strip, chosen, hover=True)

        def leave(_event: tk.Event | None = None, chosen: int = index) -> None:
            if self._selected != chosen:
                self._draw_option_card_bg(strip, chosen)

        for widget in (card, strip, number, label, badge):
            widget.bind("<Button-1>", select)
            widget.bind("<Enter>", enter)
            widget.bind("<Leave>", leave)
            widget.configure(cursor="hand2")

        self._draw_option_card_bg(strip, index)
        return card, strip

    def _draw_option_card_bg(self, canvas: tk.Canvas, index: int, *, hover: bool = False) -> None:
        try:
            w = max(canvas.winfo_width(), 1)
            h = max(canvas.winfo_height(), 1)
        except tk.TclError:
            return
        colors = self._theme.colors
        selected = index == self._selected
        if selected:
            edge = colors["accent"]
            bw = 2
        elif hover:
            edge = colors["hover"]
            bw = 1
        else:
            edge = colors["border"]
            bw = 1
        try:
            canvas.configure(bg=edge)
        except tk.TclError:
            return
        canvas.delete("all")
        draw_round_rect(
            canvas,
            bw, bw, w - 1 - bw, h - 1 - bw,
            r=RADIUS_SM,
            fill=colors["surface"],
            outline="",
        )

    def _select_option(self, index: int) -> None:
        if self._result is None or not 0 <= index < len(self._result.downloads):
            return
        self._selected = index
        self._refresh_cards()
        self._set_button_enabled(
            self._download_button,
            not self._busy,
            self._theme.colors,
            primary=True,
        )
        option = self._result.downloads[index]
        self._set_status(f"Seleccionada: {option.name}")

    def _refresh_cards(self) -> None:
        for card, strip, index in self._cards:
            try:
                self._draw_option_card_bg(strip, index, hover=False)
            except tk.TclError:
                continue

    # ==================================================================
    # Descarga
    # ==================================================================
    def start_download(self) -> None:
        if self._busy:
            return

        if self._profile_items:
            self._start_batch()
            return

        if self._result is None or self._selected is None:
            return
        option = self._result.downloads[self._selected]

        filename = build_filename(
            video_id=self._result.video_id,
            quality=option.quality,
            file_format=option.format,
        )
        target = Path(self.manager.download_dir) / filename

        self._set_footer()
        self._folder_button.grid_remove()
        self._progress = (0, None)
        self._progress_known = False
        self._batch_pos = None
        self._progress_text.configure(text="Resolviendo URL…")
        self._set_busy(True, "Resolviendo URL temporal…")
        self._spawn(lambda: self._download_worker(option.url, target))

    def _start_batch(self) -> None:
        all_items = self._profile_items or []
        items = [i for i in all_items if i.id in self._checked_ids]
        if not items:
            self._set_status("No hay vídeos marcados para descargar.", "info")
            return
        self._set_footer()
        self._folder_button.grid_remove()
        self._progress = (0, None)
        self._progress_known = False
        self._batch_pos = (0, len(items))
        self._progress_text.configure(text=f"Preparando {len(items)} vídeo(s)…")
        self._set_busy(True, f"Descargando lote (0/{len(items)})…")
        self._spawn(lambda: self._batch_worker(items))

    def _batch_worker(self, items: list[ProfileVideo]) -> None:
        runner = BatchRunner(
            self.manager,
            self.manifest,
            delay=config.BATCH_DELAY_SECONDS,
            resume=True,
            on_status=lambda text: self._queue.put(("status", text)),
            on_item_start=lambda index, total, item: self._queue.put(
                ("item_start", (index, total, item))
            ),
            on_item_progress=lambda done, total: self._queue.put(
                ("progress", (done, total))
            ),
            on_item_done=lambda _index, _total, result: self._queue.put(
                ("item_done", result)
            ),
        )
        report = runner.run(items)
        self._queue.put(("batch_done", report))

    def _download_worker(self, url: str, target: Path) -> None:
        info = self.manager.resolve_url(url)
        self._queue.put(("resolved", info))
        path = self.manager.download(
            info.final_url,
            target,
            show_progress=False,
            on_progress=lambda done, total: self._queue.put(
                ("progress", (done, total))
            ),
        )
        self._queue.put(("saved", path))

    def _on_resolved(self, info: RedirectInfo) -> None:
        host = config.url_host(info.final_url) or "?"
        if info.redirected:
            self._set_status(f"Redirect detectado → {host}", "ok")
            self._progress_text.configure(text=f"CDN: {host}")
        else:
            self._set_status(f"URL directa del CDN ({host})", "ok")
            self._progress_text.configure(text=f"CDN: {host}")

    def _on_progress(self, payload: tuple[int, int | None]) -> None:
        done, total = payload
        self._progress = (done, total)
        self._progress_known = True
        self._draw_bar()

        prefix = ""
        if self._batch_pos:
            prefix = f"Vídeo {self._batch_pos[0]}/{self._batch_pos[1]} · "

        if total:
            percent = min(int(done * 100 / total), 100)
            self._progress_text.configure(
                text=f"{prefix}{format_bytes(done)} de {format_bytes(total)}  ·  {percent}%"
            )
        else:
            self._progress_text.configure(text=f"{prefix}{format_bytes(done)} descargados")

    def _on_item_start(self, payload: tuple[int, int, ProfileVideo]) -> None:
        index, total, item = payload
        self._batch_pos = (index, total)
        self._progress = (0, None)
        self._progress_known = False
        self._draw_bar()
        self._progress_text.configure(text=f"Vídeo {index}/{total} · {item.display_name()}")
        self._set_status(f"Descargando vídeo {index} de {total}…")

    def _on_item_done(self, result: BatchItemResult) -> None:
        index, total = self._batch_pos or (0, 0)
        if result.state == "ok":
            self._done_ids.add(result.key)
            self._checked_ids.discard(result.key)
            self._set_status(f"✓ {index}/{total} · {result.file}", "ok")
        elif result.state == "skipped":
            self._set_status(f"↷ {index}/{total} · ya descargado", "muted")
        else:
            self._set_status(f"✗ {index}/{total} · {result.error}", "error")
        self._update_history_button()

    def _on_batch_done(self, report: BatchReport) -> None:
        self._set_busy(False)
        self._batch_pos = None
        self._progress = (1, 1)
        self._progress_known = True
        self._draw_bar()
        summary = summarize(report)
        self._progress_text.configure(text=summary)
        self._set_status(f"Lote terminado · {summary}", "ok" if report.failed == 0 else "error")
        folder = join_output_dir(self.manager.download_dir, self._profile_subdir)
        self._saved_path = folder
        self._set_footer(f"Guardados en  {folder}", "ok")
        self._folder_button.grid()
        remaining = len(self._checked_ids)
        self._download_button.configure(
            text=f"Descargar {remaining} vídeos" if remaining else "Descargar de nuevo"
        )
        self._set_button_enabled(
            self._download_button,
            remaining > 0,
            self._theme.colors,
        )
        if self._profile_items:
            self._build_profile_rows(self._profile_items)
            self._update_history_button()

    def _on_saved(self, path: Any) -> None:
        target = Path(path)
        self._saved_path = target
        self._set_busy(False)
        self._progress = (1, 1)
        self._progress_known = True
        self._draw_bar()
        self._progress_text.configure(
            text=f"{target.name}  ·  {format_bytes(target.stat().st_size)}"
        )
        self._set_status("Descarga completada", "ok")
        self._set_footer(f"Guardado en  {target}", "ok")
        self._folder_button.grid()
        self._refresh_cards()
        if self._result and self._result.video_id:
            self.manifest.mark(
                self._result.video_id,
                "ok",
                file=target.name,
                bytes_count=target.stat().st_size,
                title=self._result.title,
            )
            self._update_history_button()

    def _on_error(self, message: str) -> None:
        self._set_busy(False)
        self._set_status("Se produjo un error", "error")
        self._set_footer(message, "error")
        self._progress_text.configure(text="")
        self._progress_known = False
        self._draw_bar()
        self._refresh_cards()

    def _open_folder(self) -> None:
        path = self._saved_path or join_output_dir(
            self.manager.download_dir, self._profile_subdir
        )
        folder = path if path.is_dir() else path.parent
        folder.mkdir(parents=True, exist_ok=True)
        try:
            open_in_file_manager(folder)
        except OSError:
            self._set_footer(f"No se pudo abrir: {folder}", "error")

    # ==================================================================
    # Ciclo de vida
    # ==================================================================
    def _on_close(self) -> None:
        try:
            self.client.close()
        except Exception:  # pragma: no cover
            pass
        try:
            self.source.close()
        except Exception:  # pragma: no cover
            pass
        self.destroy()


def run(debug: bool = False) -> int:
    """Arranca la interfaz gráfica."""
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    app = RedlyGUI(debug=debug)
    app.mainloop()
    return 0
