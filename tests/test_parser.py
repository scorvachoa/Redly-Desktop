"""Tests del parser (usan un HTML anonimizado, sin red)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.parser import TikVidParser

FIXTURE = Path(__file__).parent / "fixtures" / "ajax_search_response.html"


@pytest.fixture(scope="module")
def fixture_html() -> str:
    return FIXTURE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def parser() -> TikVidParser:
    return TikVidParser()


def test_extrae_titulo(parser: TikVidParser, fixture_html: str) -> None:
    result = parser.parse(fixture_html)
    assert result.title is not None
    assert result.title.startswith("Scramble up ur name")
    assert "\n" not in result.title


def test_extrae_thumbnail(parser: TikVidParser, fixture_html: str) -> None:
    result = parser.parse(fixture_html)
    assert result.thumbnail is not None
    assert result.thumbnail.startswith("https://")
    assert "thumbnail.webp" in result.thumbnail


def test_extrae_video_id(parser: TikVidParser, fixture_html: str) -> None:
    result = parser.parse(fixture_html)
    assert result.video_id == "6718335390845095173"


def test_extrae_cuatro_opciones(parser: TikVidParser, fixture_html: str) -> None:
    result = parser.parse(fixture_html)
    assert len(result.downloads) == 4
    names = [option.name for option in result.downloads]
    assert names == [
        "Download MP4 [1]",
        "Download MP4 [2]",
        "Download MP4 HD",
        "Download MP3",
    ]


def test_urls_de_opciones_absolutas(parser: TikVidParser, fixture_html: str) -> None:
    result = parser.parse(fixture_html)
    for option in result.downloads:
        assert option.url.startswith("https://")
        assert " " not in option.url


def test_formato_y_calidad(parser: TikVidParser, fixture_html: str) -> None:
    result = parser.parse(fixture_html)
    por_nombre = {option.name: option for option in result.downloads}

    assert por_nombre["Download MP4 [1]"].format == "mp4"
    assert por_nombre["Download MP4 [1]"].quality == "standard"
    assert por_nombre["Download MP4 [2]"].format == "mp4"
    assert por_nombre["Download MP4 HD"].format == "mp4"
    assert por_nombre["Download MP4 HD"].quality == "hd"
    assert por_nombre["Download MP3"].format == "mp3"
    assert por_nombre["Download MP3"].quality == "audio"


def test_excluye_enlace_de_navegacion(parser: TikVidParser, fixture_html: str) -> None:
    result = parser.parse(fixture_html)
    assert all("more-video" not in option.name for option in result.downloads)
    assert all(option.url != "https://tikvid.io/" for option in result.downloads)


def test_ausencia_de_opciones(parser: TikVidParser) -> None:
    html = "<div class='thumbnail'><h3>Solo titulo</h3><img src='https://cdn/x.jpg'></div>"
    result = parser.parse(html)
    assert result.title == "Solo titulo"
    assert result.downloads == []


def test_ausencia_de_todos_los_elementos(parser: TikVidParser) -> None:
    result = parser.parse("<div><p>sin nada relevante</p></div>")
    assert result.title is None
    assert result.thumbnail is None
    assert result.video_id is None
    assert result.downloads == []


def test_html_incompleto_no_lanza_error(parser: TikVidParser) -> None:
    result = parser.parse("<div class='thumbnail'><h3>Titulo <b>cortado")
    assert result.title == "Titulo cortado"
    assert result.downloads == []


def test_html_vacio(parser: TikVidParser) -> None:
    result = parser.parse("")
    assert result.downloads == []


def test_video_id_desde_enlace_tiktok(parser: TikVidParser) -> None:
    html = (
        "<div class='thumbnail'><h3>T</h3>"
        "<a href='https://www.tiktok.com/@user/video/7000000000000000001'>ver</a>"
        "<a class='tik-button-dl' href='https://dl.snapcdn.app/get?token=fake'>"
        "Download MP4</a></div>"
    )
    result = parser.parse(html)
    assert result.video_id == "7000000000000000001"
    assert len(result.downloads) == 1


def test_fallback_sin_clases_conocidas(parser: TikVidParser) -> None:
    html = (
        "<div><h3>Sin clases</h3>"
        "<a href='https://dl.snapcdn.app/get?token=fake2'>Download MP4 HD</a></div>"
    )
    result = parser.parse(html)
    assert len(result.downloads) == 1
    assert result.downloads[0].format == "mp4"
    assert result.downloads[0].quality == "hd"


def test_duplicados_se_eliminan(parser: TikVidParser) -> None:
    html = (
        "<div><a class='tik-button-dl' href='https://dl.snapcdn.app/get?token=fake'>"
        "Download MP4</a>"
        "<a class='tik-button-dl' href='https://dl.snapcdn.app/get?token=fake'>"
        "Download MP4 otra vez</a></div>"
    )
    result = parser.parse(html)
    assert len(result.downloads) == 1
