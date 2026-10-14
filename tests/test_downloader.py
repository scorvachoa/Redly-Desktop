"""Tests del downloader (mocks, sin red)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.downloader import DownloadManager, build_filename, sanitize_filename
from app.exceptions import TikVidDownloadError, TikVidUnavailableError

SNAP_URL = "https://dl.snapcdn.app/get?token=FAKE"
CDN_URL = "https://v16.tokcdn.com/video/file.mp4"


def make_response(status_code: int = 200, headers: dict | None = None, chunks=()):
    response = MagicMock()
    response.status_code = status_code
    response.headers = headers or {}
    response.iter_content.return_value = iter(list(chunks))
    response.__enter__.return_value = response
    return response


def make_manager(responses, tmp_path: Path) -> DownloadManager:
    session = MagicMock()
    session.get.side_effect = responses
    return DownloadManager(session=session, download_dir=tmp_path)


# ---------------------------------------------------------------------------
# Nombres de archivo
# ---------------------------------------------------------------------------
def test_sanitize_elimina_path_traversal() -> None:
    cleaned = sanitize_filename("../../etc/passwd")
    assert ".." not in cleaned
    assert "/" not in cleaned
    assert "\\" not in cleaned


def test_sanitize_elimina_caracteres_invalidos() -> None:
    cleaned = sanitize_filename('video:<>"|?*.mp4')
    for char in '<>:"|?*':
        assert char not in cleaned


def test_sanitize_nombre_reservado_de_windows() -> None:
    assert sanitize_filename("CON.mp4").startswith("_")


def test_sanitize_no_vacio() -> None:
    assert sanitize_filename("   ") == "archivo"
    assert sanitize_filename("...") == "archivo"


def test_sanitize_limita_longitud() -> None:
    cleaned = sanitize_filename("a" * 500 + ".mp4")
    assert len(cleaned) <= 120
    assert cleaned.endswith(".mp4")


def test_build_filename_como_en_especificacion() -> None:
    assert (
        build_filename(video_id="7616480500147965206", quality="hd", file_format="mp4")
        == "Redly_7616480500147965206_hd.mp4"
    )


def test_build_filename_calidad_estandar() -> None:
    assert (
        build_filename(video_id="123", quality="standard", file_format="mp4")
        == "Redly_123.mp4"
    )


def test_build_filename_sin_id() -> None:
    assert build_filename(video_id=None, quality="audio", file_format="mp3") == (
        "Redly_video_audio.mp3"
    )


# ---------------------------------------------------------------------------
# Resolución de redirecciones
# ---------------------------------------------------------------------------
def test_resolve_sin_redirect() -> None:
    manager = make_manager([make_response(status_code=200)], Path("."))
    info = manager.resolve_url(CDN_URL)
    assert info.original_url == CDN_URL
    assert info.final_url == CDN_URL
    assert info.redirected is False
    assert info.hops == 0
    assert info.status_code == 200
    assert info.to_dict()["redirected"] is False


def test_resolve_con_redirect_de_snapcdn() -> None:
    redirect = make_response(status_code=302, headers={"Location": CDN_URL})
    final = make_response(status_code=200)
    manager = make_manager([redirect, final], Path("."))

    info = manager.resolve_url(SNAP_URL)

    assert info.redirected is True
    assert info.final_url == CDN_URL
    assert info.hops == 1
    assert info.status_code == 200


def test_resolve_con_demasiadas_redirecciones() -> None:
    loop = make_response(status_code=302, headers={"Location": SNAP_URL})
    manager = make_manager([loop] * 10, Path("."))
    with pytest.raises(TikVidDownloadError):
        manager.resolve_url(SNAP_URL)


def test_resolve_error_de_red() -> None:
    import requests

    session = MagicMock()
    session.get.side_effect = requests.ConnectionError("fallo")
    manager = DownloadManager(session=session, download_dir=Path("."))
    with pytest.raises(TikVidDownloadError):
        manager.resolve_url(SNAP_URL)


# ---------------------------------------------------------------------------
# Descarga en streaming
# ---------------------------------------------------------------------------
def test_download_escribe_chunks(tmp_path: Path) -> None:
    body = make_response(
        status_code=200,
        headers={"Content-Length": "6"},
        chunks=[b"abc", b"", b"def"],
    )
    manager = make_manager([make_response(status_code=200), body], tmp_path)

    target = manager.download(CDN_URL, tmp_path / "out.mp4", show_progress=False)

    assert target.read_bytes() == b"abcdef"
    assert manager.download_dir == tmp_path


def test_download_url_expirada(tmp_path: Path) -> None:
    body = make_response(status_code=403)
    manager = make_manager([make_response(status_code=200), body], tmp_path)
    with pytest.raises(TikVidUnavailableError):
        manager.download(CDN_URL, tmp_path / "out.mp4", show_progress=False)
    assert not (tmp_path / "out.mp4").exists()


def test_download_con_error_elimina_archivo_parcial(tmp_path: Path) -> None:
    def chunks_fallan():
        yield b"abc"
        raise OSError("disco lleno")

    body = make_response(status_code=200)
    body.iter_content.return_value = chunks_fallan()
    manager = make_manager([make_response(status_code=200), body], tmp_path)

    target = tmp_path / "out.mp4"
    with pytest.raises(OSError):
        manager.download(CDN_URL, target, show_progress=False)
    assert not target.exists()
