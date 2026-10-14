"""Cliente HTTP de TikVid.

Responsabilidad única: validar la URL, ejecutar el ``POST`` a
``/api/ajaxSearch``, validar la respuesta y delegar el HTML al parser.
No contiene lógica de parsing HTML.
"""

from __future__ import annotations

import logging
import re
import time
from urllib.parse import urlsplit

import requests

from . import config
from .exceptions import (
    TikVidInvalidUrlError,
    TikVidParseError,
    TikVidRequestError,
    TikVidUnavailableError,
)
from .models import TikTokResult
from .parser import TikVidParser

logger = logging.getLogger("tikvid.client")


def is_tiktok_url(url: str) -> bool:
    """Validación flexible de URLs de TikTok (cualquier subdominio)."""
    if not url or not isinstance(url, str):
        return False
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return False
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return False
    host = (parts.hostname or "").lower()
    return host == "tiktok.com" or host.endswith(".tiktok.com")


def normalize_tiktok_url(url: str) -> str:
    """Quita barra final y espacios (algunos backends dan 404 con ``/``)."""
    cleaned = (url or "").strip()
    if cleaned.count("/") == 3 and cleaned.endswith("/"):
        return cleaned[:-1]
    return cleaned.rstrip("/") if cleaned.endswith("/") and "/@" in cleaned else cleaned


_VIDEO_PATH_RE = re.compile(r"/(?:video|photo)/\d{5,30}", re.I)
_PROFILE_PATH_RE = re.compile(r"^/@([^/]+)/?$", re.I)


def is_video_url(url: str) -> bool:
    """¿Es la URL de un vídeo concreto (incluidos enlaces cortos)?"""
    if not is_tiktok_url(url):
        return False
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    if host.startswith(("vm.", "vt.")):
        return True
    return bool(_VIDEO_PATH_RE.search(parts.path))


def is_profile_url(url: str) -> bool:
    """¿Es la URL de un perfil (``@usuario`` sin ruta de vídeo)?"""
    if not is_tiktok_url(url):
        return False
    parts = urlsplit(url.strip())
    return bool(_PROFILE_PATH_RE.match(parts.path))


def input_to_profile_url(value: str) -> str:
    """Convierte ``@usuario`` o una URL de perfil en URL normalizada."""
    raw = (value or "").strip()
    if raw.startswith("@") and " " not in raw and "/" not in raw:
        raw = f"https://www.tiktok.com/{raw}"
    return normalize_tiktok_url(raw)


def profile_handle(url: str) -> str | None:
    """Extrae el identificador de un perfil (``@scout2015`` → ``scout2015``)."""
    if not is_profile_url(url):
        return None
    parts = urlsplit(url.strip())
    match = _PROFILE_PATH_RE.match(parts.path)
    return match.group(1) if match else None


def profile_folder_name(url: str) -> str | None:
    """Nombre de carpeta seguro para un perfil (``scout2015`` → ``scout2015``)."""
    from .downloader import sanitize_folder_name

    handle = profile_handle(url)
    if not handle:
        return None
    folder = sanitize_folder_name(handle)
    return None if folder == "perfil" else folder


class TikVidClient:
    """Cliente del flujo HTTP confirmado de TikVid."""

    def __init__(
        self,
        session: requests.Session | None = None,
        parser: TikVidParser | None = None,
        *,
        debug: bool = False,
    ) -> None:
        self._session = session or self._build_session()
        self._parser = parser or TikVidParser()
        self.debug = debug
        self.last_http_status: int | None = None
        self.last_logical_status: str | None = None

    @staticmethod
    def _build_session() -> requests.Session:
        session = requests.Session()
        session.headers.update(config.HEADERS)
        return session

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------
    def search(self, url: str) -> TikTokResult:
        """Envía la URL de TikTok a TikVid y devuelve los datos parseados."""
        target = (url or "").strip()
        if not is_tiktok_url(target):
            raise TikVidInvalidUrlError("La URL no parece ser una URL válida de TikTok.")

        logger.debug("endpoint: %s", config.TIKVID_AJAX_SEARCH_URL)
        payload = {"q": target, "lang": "en"}
        response = self._post(payload)

        self.last_http_status = response.status_code
        if response.status_code != 200:
            raise TikVidRequestError(
                f"TikVid no respondió correctamente (HTTP {response.status_code})."
            )

        body = self._load_json(response)
        logical_status = str(body.get("status", ""))
        self.last_logical_status = logical_status or None
        if logical_status != "ok":
            raise TikVidUnavailableError(
                f"TikVid devolvió el estado '{logical_status or 'vacío'}'."
            )

        data = body.get("data")
        if not isinstance(data, str) or not data.strip():
            raise TikVidParseError("La respuesta de TikVid no contiene HTML.")

        result = self._parser.parse(data)
        logger.debug("opciones encontradas: %d", len(result.downloads))

        if not result.downloads:
            raise TikVidUnavailableError("No se encontraron opciones de descarga.")
        return result

    # ------------------------------------------------------------------
    # HTTP con reintentos limitados
    # ------------------------------------------------------------------
    def _post(self, payload: dict[str, str]) -> requests.Response:
        last_error: Exception | None = None

        for attempt in range(1, config.RETRY_ATTEMPTS + 1):
            try:
                response = self._session.post(
                    config.TIKVID_AJAX_SEARCH_URL,
                    data=payload,
                    timeout=config.REQUEST_TIMEOUT,
                )
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_error = exc
                logger.debug(
                    "intento %d/%d fallido: %s",
                    attempt,
                    config.RETRY_ATTEMPTS,
                    type(exc).__name__,
                )
                self._sleep_backoff(attempt)
                continue

            if (
                response.status_code in config.TRANSIENT_HTTP_STATUSES
                and attempt < config.RETRY_ATTEMPTS
                ):
                logger.debug(
                    "HTTP %d transitorio, reintento %d/%d",
                    response.status_code,
                    attempt,
                    config.RETRY_ATTEMPTS,
                )
                self._sleep_backoff(attempt)
                continue

            return response

        raise TikVidRequestError(
            "TikVid no respondió correctamente."
        ) from last_error

    @staticmethod
    def _sleep_backoff(attempt: int) -> None:
        if config.RETRY_BACKOFF_SECONDS > 0:
            time.sleep(config.RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1)))

    @staticmethod
    def _load_json(response: requests.Response) -> dict:
        content_type = response.headers.get("Content-Type", "")
        try:
            body = response.json()
        except ValueError as exc:
            if "html" in content_type.lower():
                raise TikVidRequestError(
                    "TikVid no respondió correctamente: devolvió HTML en lugar de "
                    "JSON (posible protección del navegador)."
                ) from exc
            raise TikVidRequestError(
                "TikVid no respondió correctamente: respuesta no válida."
            ) from exc

        if not isinstance(body, dict):
            raise TikVidRequestError(
                "TikVid no respondió correctamente: estructura inesperada."
            )
        return body

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> "TikVidClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
