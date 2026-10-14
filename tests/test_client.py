"""Tests del cliente HTTP con mocking (sin dependencia de TikVid real)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import requests

from app import config
from app.client import TikVidClient, is_tiktok_url
from app.exceptions import (
    TikVidInvalidUrlError,
    TikVidParseError,
    TikVidRequestError,
    TikVidUnavailableError,
)

FIXTURE = Path(__file__).parent / "fixtures" / "ajax_search_response.html"
TIKTOK_URL = "https://www.tiktok.com/@usuario/video/123456789"


def make_response(
    status_code: int = 200,
    payload: object = None,
    text: str = "",
    content_type: str = "application/json",
) -> MagicMock:
    response = MagicMock(spec=requests.Response)
    response.status_code = status_code
    response.headers = {"Content-Type": content_type}
    response.text = text
    if isinstance(payload, Exception):
        response.json.side_effect = payload
    else:
        response.json.return_value = payload
    return response


def make_session(response: MagicMock) -> MagicMock:
    session = MagicMock(spec=requests.Session)
    session.post.return_value = response
    session.headers = {}
    return session


def test_acepta_urls_de_tiktok() -> None:
    assert is_tiktok_url("https://www.tiktok.com/@user/video/1")
    assert is_tiktok_url("https://tiktok.com/@user/video/1")
    assert is_tiktok_url("https://m.tiktok.com/v/1.html")
    assert is_tiktok_url("https://vm.tiktok.com/ZMxxxx/")


def test_rechaza_urls_que_no_son_de_tiktok() -> None:
    assert not is_tiktok_url("https://example.com/video/1")
    assert not is_tiktok_url("https://tiktok.com.evil.com/video/1")
    assert not is_tiktok_url("ftp://tiktok.com/x")
    assert not is_tiktok_url("")
    assert not is_tiktok_url(None)  # type: ignore[arg-type]


def test_search_parsea_respuesta_ok() -> None:
    payload = {"status": "ok", "data": FIXTURE.read_text(encoding="utf-8")}
    session = make_session(make_response(payload=payload))
    client = TikVidClient(session=session)

    result = client.search(TIKTOK_URL)

    assert result.video_id == "6718335390845095173"
    assert len(result.downloads) == 4
    assert client.last_http_status == 200
    assert client.last_logical_status == "ok"

    _, kwargs = session.post.call_args
    assert kwargs["data"] == {"q": TIKTOK_URL, "lang": "en"}
    assert kwargs["timeout"] == config.REQUEST_TIMEOUT
    assert session.post.call_args[0][0] == config.TIKVID_AJAX_SEARCH_URL


def test_url_invalida_lanza_error() -> None:
    client = TikVidClient(session=make_session(make_response()))
    with pytest.raises(TikVidInvalidUrlError):
        client.search("https://example.com/no-tiktok")
    client = TikVidClient(session=make_session(make_response()))
    with pytest.raises(TikVidInvalidUrlError):
        client.search("")


def test_status_no_ok_lanza_unavailable() -> None:
    session = make_session(make_response(payload={"status": "error"}))
    client = TikVidClient(session=session)
    with pytest.raises(TikVidUnavailableError):
        client.search(TIKTOK_URL)


def test_respuesta_no_json_lanza_request_error() -> None:
    session = make_session(
        make_response(payload=ValueError("no json"), content_type="text/html")
    )
    client = TikVidClient(session=session)
    with pytest.raises(TikVidRequestError):
        client.search(TIKTOK_URL)


def test_data_vacia_lanza_parse_error() -> None:
    session = make_session(make_response(payload={"status": "ok", "data": ""}))
    client = TikVidClient(session=session)
    with pytest.raises(TikVidParseError):
        client.search(TIKTOK_URL)


def test_sin_opciones_lanza_unavailable() -> None:
    session = make_session(
        make_response(payload={"status": "ok", "data": "<div><h3>x</h3></div>"})
    )
    client = TikVidClient(session=session)
    with pytest.raises(TikVidUnavailableError):
        client.search(TIKTOK_URL)


def test_http_500_lanza_request_error() -> None:
    session = make_session(make_response(status_code=500, payload={"status": "ok"}))
    client = TikVidClient(session=session)
    with pytest.raises(TikVidRequestError):
        client.search(TIKTOK_URL)


def test_reintentos_en_error_deconexion(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "RETRY_BACKOFF_SECONDS", 0)
    session = MagicMock(spec=requests.Session)
    session.headers = {}
    session.post.side_effect = [
        requests.ConnectionError("fallo"),
        requests.ConnectionError("fallo"),
        make_response(payload={"status": "ok", "data": FIXTURE.read_text("utf-8")}),
    ]
    client = TikVidClient(session=session)

    result = client.search(TIKTOK_URL)

    assert session.post.call_count == 3
    assert len(result.downloads) == 4


def test_reintentos_agotados(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "RETRY_BACKOFF_SECONDS", 0)
    session = MagicMock(spec=requests.Session)
    session.headers = {}
    session.post.side_effect = requests.Timeout("tiempo agotado")
    client = TikVidClient(session=session)

    with pytest.raises(TikVidRequestError):
        client.search(TIKTOK_URL)
    assert session.post.call_count == config.RETRY_ATTEMPTS
