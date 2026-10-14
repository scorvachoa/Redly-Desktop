"""Tests de la interfaz gráfica (Tkinter, sin red)."""

from __future__ import annotations

import tkinter as tk

import pytest

from app.gui import DARK, LIGHT, RedlyGUI, format_bytes
from app.models import DownloadOption, TikTokResult


@pytest.fixture(scope="module")
def app():
    try:
        window = RedlyGUI()
    except tk.TclError:  # pragma: no cover - entorno sin escritorio
        pytest.skip("No hay display disponible para Tkinter")
    window.withdraw()
    yield window
    window.destroy()


@pytest.fixture(autouse=True)
def _estado_inicial(app: RedlyGUI):
    """Cada test empieza con tema claro y sin proceso en marcha."""
    app._theme = LIGHT
    app._busy = False
    app._set_footer()
    app._entry.configure(state="normal")
    app._apply_theme()
    yield


def fake_result() -> TikTokResult:
    return TikTokResult(
        video_id="7616480500147965206",
        title="Mirador de Yanahuara",
        thumbnail=None,
        downloads=[
            DownloadOption(
                name="Download MP4",
                url="https://dl.snapcdn.app/get?token=fake1",
                format="mp4",
                quality="standard",
            ),
            DownloadOption(
                name="Download MP4 HD",
                url="https://dl.snapcdn.app/get?token=fake2",
                format="mp4",
                quality="hd",
            ),
            DownloadOption(
                name="Download MP3",
                url="https://dl.snapcdn.app/get?token=fake3",
                format="mp3",
                quality="audio",
            ),
        ],
    )


def test_format_bytes() -> None:
    assert format_bytes(None) == "?"
    assert format_bytes(0) == "0 B"
    assert format_bytes(1024) == "1.0 KB"
    assert format_bytes(3 * 1024 * 1024) == "3.0 MB"


def test_ventana_se_crea(app: RedlyGUI) -> None:
    assert app.title() == "Red.ly"
    assert app._result is None
    assert app._placeholder_active is True


def test_url_placeholder_no_se_analiza(app: RedlyGUI) -> None:
    app.start_analysis()
    assert app._busy is False
    assert "URL válida" in app._status.cget("text")


def test_url_invalida_no_se_analiza(app: RedlyGUI) -> None:
    app._entry.configure(state="normal")
    app._entry.delete(0, tk.END)
    app._entry.insert(0, "https://example.com/video/1")
    app._placeholder_active = False
    app.start_analysis()
    assert app._busy is False
    assert "URL válida" in app._status.cget("text")


def test_carga_resultado_y_tarjetas(app: RedlyGUI) -> None:
    result = fake_result()
    app._on_result((result, None))

    assert app._result is not None
    assert len(app._cards) == 3
    assert app._selected == 0
    assert app._download_button.cget("state") == "normal"
    assert app._title_label.cget("text") == "Mirador de Yanahuara"
    assert "7616480500147965206" in app._meta_label.cget("text")


def test_seleccion_de_opcion(app: RedlyGUI) -> None:
    app._on_result((fake_result(), None))
    app._select_option(2)

    assert app._selected == 2
    _card, strip, index = app._cards[2]
    assert index == 2
    colors = app._theme.colors
    assert strip.cget("bg") == colors["accent"]
    assert app._cards[0][1].cget("bg") == colors["border"]


def test_cambio_de_tema(app: RedlyGUI) -> None:
    before = app._entry.cget("bg")
    app._toggle_theme()
    assert app._theme is DARK
    assert app._entry.cget("bg") != before
    assert app._entry.cget("bg") == DARK.colors["entry"]

    app._toggle_theme()
    assert app._theme is LIGHT
    assert app._entry.cget("bg") == before


def test_tema_no_destruye_tarjetas(app: RedlyGUI) -> None:
    app._on_result((fake_result(), None))
    app._toggle_theme()
    assert len(app._cards) == 3
    colors = app._theme.colors
    assert app._cards[0][0].cget("bg") == colors["surface"]
    app._toggle_theme()
    assert app._cards[0][0].cget("bg") == LIGHT.colors["surface"]


def test_error_detiene_el_proceso(app: RedlyGUI) -> None:
    app._set_busy(True, "Analizando vídeo…")
    app._on_error("TikVid no respondió correctamente.")
    assert app._busy is False
    assert app._footer.cget("text") == "TikVid no respondió correctamente."
