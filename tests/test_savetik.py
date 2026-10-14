"""Tests de la fuente SavTikPro (mocks; sin red real)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import requests

from app import config
from app.client import (
    input_to_profile_url,
    is_profile_url,
    is_video_url,
    normalize_tiktok_url,
)
from app.exceptions import TikVidError, TikVidRequestError, TikVidUnavailableError
from app.models import DownloadOption, TikTokResult
from app.savetik import SavetikSource, anonymous_key, search_any

FIXTURES = Path(__file__).parent / "fixtures"
PROFILE_HTML = (FIXTURES / "savetik_profile_page.html").read_text(encoding="utf-8")
DETAIL_HTML = (FIXTURES / "savetik_detail.html").read_text(encoding="utf-8")
PERFIL = "https://www.tiktok.com/@scout2015"
VIDEO = "https://www.tiktok.com/@scout2015/video/6718335390845095173"


def make_response(status_code: int = 200, payload: object = None) -> MagicMock:
    response = MagicMock(spec=requests.Response)
    response.status_code = status_code
    response.headers = {}
    if isinstance(payload, Exception):
        response.json.side_effect = payload
    else:
        response.json.return_value = payload
    return response


class FakeSession:
    """Sesión falsa: encola respuestas y registra los payloads enviados."""

    def __init__(self, responses: list[MagicMock]) -> None:
        self._responses = list(responses)
        self.posts: list[dict[str, str]] = []
        self.closed = False

    def get(self, *args: object, **kwargs: object) -> MagicMock:
        return make_response(200, {"status": "ok"})

    def post(self, url: str, data: dict[str, str], timeout: float = 0) -> MagicMock:
        self.posts.append(dict(data))
        if not self._responses:
            raise AssertionError("No quedan respuestas encoladas")
        return self._responses.pop(0)

    def close(self) -> None:
        self.closed = True


def make_source(responses: list[MagicMock]) -> tuple[SavetikSource, FakeSession]:
    session = FakeSession(responses)
    source = SavetikSource(session=session)  # type: ignore[arg-type]
    source._warmed = True
    return source, session


# ----------------------------------------------------------------------
# Clasificación de URLs
# ----------------------------------------------------------------------
def test_detecta_perfil_y_video() -> None:
    assert is_profile_url(PERFIL)
    assert is_profile_url(PERFIL + "/")
    assert not is_profile_url(VIDEO)
    assert is_video_url(VIDEO)
    assert is_video_url("https://vm.tiktok.com/ZMxxxxx/")


def test_normaliza_barra_final() -> None:
    assert normalize_tiktok_url(PERFIL + "/") == PERFIL
    assert input_to_profile_url("@scout2015") == PERFIL


# ----------------------------------------------------------------------
# Tarjetas
# ----------------------------------------------------------------------
def test_parse_cards_extrae_campos() -> None:
    source, _ = make_source([])
    items = source._parse_cards(PROFILE_HTML)

    assert len(items) == 3
    first = items[0]
    assert first.id == "7694475743509646622"
    assert first.title == "Why did Stella do THAT? #pool #fail"
    assert first.channel == "Scout, Suki & Stella"
    assert first.views == "1.8K"
    assert first.url.startswith("https://pro.snapcdn.app/dl?token=FAKE_TOKEN_A")
    assert first.format == "mp4"
    assert first.thumbnail == "https://cdn.example/thumb1.jpeg"


def test_anonymous_key_es_estable() -> None:
    assert anonymous_key("https://x/a") == anonymous_key("https://x/a")
    assert anonymous_key("https://x/a") != anonymous_key("https://x/b")


# ----------------------------------------------------------------------
# list_profile
# ----------------------------------------------------------------------
def body_profile(data: str, next_cursor: str | None = None) -> dict:
    payload: dict = {"status": "ok", "pageType": "profile", "data": data}
    if next_cursor is not None:
        payload["next_cursor"] = next_cursor
    return payload


def test_list_profile_pagina_con_cursor() -> None:
    page2 = PROFILE_HTML.replace("7123456789012345678", "9999999999999999999")
    source, session = make_source(
        [
            make_response(200, body_profile(PROFILE_HTML, next_cursor="1759000000")),
            make_response(200, body_profile(page2)),
        ]
    )

    items = source.list_profile(PERFIL)

    assert [item.id for item in items] == [
        "7694475743509646622",
        "7001234567890123456",
        "7123456789012345678",
        "9999999999999999999",
    ]
    assert len(session.posts) == 2
    assert session.posts[0]["cursor"] == "0"
    assert session.posts[0]["page"] == "1"
    assert session.posts[1]["cursor"] == "1759000000"
    assert session.posts[1]["page"] == "2"


def test_list_profile_respeta_max_videos() -> None:
    source, session = make_source([make_response(200, body_profile(PROFILE_HTML))])

    items = source.list_profile(PERFIL, max_videos=2)

    assert len(items) == 2
    assert len(session.posts) == 1


def test_list_profile_quita_barra_final_del_payload() -> None:
    source, session = make_source([make_response(200, body_profile(PROFILE_HTML))])

    source.list_profile(PERFIL + "/", max_videos=1)

    assert session.posts[0]["q"] == PERFIL


def test_list_profile_rechaza_url_de_video() -> None:
    source, _ = make_source([])
    with pytest.raises(TikVidRequestError):
        source.list_profile(VIDEO)


def test_list_profile_dedupe_entre_paginas() -> None:
    source, session = make_source(
        [
            make_response(200, body_profile(PROFILE_HTML, next_cursor="111")),
            make_response(200, body_profile(PROFILE_HTML)),
        ]
    )

    items = source.list_profile(PERFIL)

    assert len(items) == 3  # la página 2 sólo repetía


def test_list_profile_cursor_vacio_no_se_envia() -> None:
    source, session = make_source([make_response(200, body_profile(PROFILE_HTML))])

    source.list_profile(PERFIL, max_videos=1)

    assert "cursor" in session.posts[0]
    assert session.posts[0]["cursor"] != ""


# ----------------------------------------------------------------------
# search_video / search_any
# ----------------------------------------------------------------------
def test_search_video_parsea_detalle() -> None:
    source, _ = make_source(
        [make_response(200, {"status": "ok", "pageType": "detail", "data": DETAIL_HTML})]
    )

    result = source.search_video(VIDEO)

    assert result.video_id == "6718335390845095173"
    assert len(result.downloads) >= 2


def test_search_video_sin_datos_lanza_no_disponible() -> None:
    source, _ = make_source([make_response(200, {"status": "ok", "data": ""})])

    with pytest.raises(TikVidUnavailableError):
        source.search_video(VIDEO)


def test_status_fail_lanza_no_disponible_con_mensaje() -> None:
    source, _ = make_source(
        [
            make_response(
                200,
                {"status": "fail", "statusCode": 404, "message": "Video not found."},
            )
        ]
    )

    with pytest.raises(TikVidUnavailableError, match="Video not found"):
        source.search_video(VIDEO)


def test_http_404_lanza_no_disponible() -> None:
    source, _ = make_source([make_response(404, None)])

    with pytest.raises(TikVidUnavailableError):
        source.list_profile(PERFIL)


def test_reintenta_errores_transitorios(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "RETRY_BACKOFF_SECONDS", 0)
    source, session = make_source(
        [make_response(503, None), make_response(200, body_profile(PROFILE_HTML))]
    )

    items = source.list_profile(PERFIL, max_videos=1)

    assert len(items) == 1
    assert len(session.posts) == 2


def test_search_any_hace_fallback_a_tikvid() -> None:
    fuente_mala, _ = make_source([make_response(200, {"status": "ok", "data": ""})])
    tikvid = MagicMock()
    esperado = TikTokResult(
        video_id="1",
        downloads=[DownloadOption(name="MP4", url="https://cdn.example/x.mp4")],
    )
    tikvid.search.return_value = esperado

    result = search_any(VIDEO, savetik=fuente_mala, tikvid=tikvid)

    assert result is esperado
    tikvid.search.assert_called_once_with(VIDEO)


def test_search_any_propaga_error_si_ambas_fallan() -> None:
    fuente_mala, _ = make_source([make_response(200, {"status": "ok", "data": ""})])
    tikvid = MagicMock()
    tikvid.search.side_effect = TikVidError("sin suerte")

    with pytest.raises(TikVidError, match="sin suerte"):
        search_any(VIDEO, savetik=fuente_mala, tikvid=tikvid)


def test_body_json_no_dict_lanza_error() -> None:
    fuente, _ = make_source([make_response(200, [1, 2, 3])])

    with pytest.raises(TikVidRequestError):
        fuente.list_profile(PERFIL)
