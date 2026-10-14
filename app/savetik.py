"""Fuente de listados de perfil vía SavTikPro.

El endpoint ``POST /api/ajaxSearch`` de savetikpro (misma familia de producto
que TikVid, build más nueva) devuelve:

* ``pageType="detail"`` para un vídeo único  -> se parsea con el parser existente.
* ``pageType="profile"`` para un perfil, con ``data`` (HTML con tarjetas
  ``.video-item``) y ``next_cursor`` para paginar.

Descubrimiento sin API documentada: se envía la misma forma de payload que
TikVid (``q``, ``cursor``, ``page``, ``lang``). Reglas verificadas empíricamente:

* ``cursor`` vacío produce ``404 Video not found`` (sólo la primera página usa ``0``).
* La URL de perfil **no** debe llevar barra final.
* Las URLs de descarga son tokens JWT de ``pro.snapcdn.app`` con caducidad de
  ~1 hora: se usan en el momento y **nunca** se guardan ni se registran.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from collections.abc import Callable
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from . import config
from .client import TikVidClient, is_profile_url, normalize_tiktok_url
from .exceptions import TikVidError, TikVidRequestError, TikVidUnavailableError
from .models import ProfileVideo, TikTokResult
from .parser import TikVidParser

logger = logging.getLogger("tikvid.savetik")

_ID_IN_NAME_RE = re.compile(r"(\d{10,25})")
_EXT_IN_NAME_RE = re.compile(r"\.(mp4|mp3|m4a|webm)\b", re.I)

CardCallback = Callable[[int, int], None]


def anonymous_key(url: str) -> str:
    """Clave estable para el manifiesto SIN almacenar la URL del token."""
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


class SavetikSource:
    """Cliente de savetikpro: vídeos de perfil (lote) y un solo vídeo."""

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
        self._warmed = False
        self.last_http_status: int | None = None

    @staticmethod
    def _build_session() -> requests.Session:
        session = requests.Session()
        session.headers.update(config.SAVETIK_HEADERS)
        return session

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------
    def search_video(self, url: str) -> TikTokResult:
        """Busca un vídeo único; devuelve el mismo modelo que TikVid."""
        target = normalize_tiktok_url(url)
        body = self._post(target, cursor=None, page="1")
        html = str(body.get("data") or "")
        if not html.strip():
            raise TikVidUnavailableError("El servicio no devolvió datos para esa URL.")
        result = self._parser.parse(html)
        if not result.downloads:
            raise TikVidUnavailableError("No se encontraron opciones de descarga.")
        logger.info("savetik: %d opciones para video_id=%s", len(result.downloads), result.video_id)
        return result

    def list_profile(
        self,
        url: str,
        max_videos: int | None = None,
        on_page: CardCallback | None = None,
    ) -> list[ProfileVideo]:
        """Lista los vídeos de un perfil paginando con ``next_cursor``.

        ``on_page(n_pagina, acumulado)`` se invoca tras cada página.
        """
        target = normalize_tiktok_url(url)
        if not is_profile_url(target):
            raise TikVidRequestError("Esa URL no parece la de un perfil de TikTok.")

        limit = max_videos if max_videos and max_videos > 0 else None
        items: list[ProfileVideo] = []
        seen_ids: set[str] = set()
        seen_cursors: set[str] = set()
        cursor = "0"
        page = 1

        while page <= config.BATCH_MAX_PAGES:
            body = self._post(target, cursor=cursor, page=str(page))
            html = str(body.get("data") or "")
            if not html.strip():
                break

            added = 0
            for item in self._parse_cards(html):
                if item.id in seen_ids:
                    continue
                seen_ids.add(item.id)
                items.append(item)
                added += 1
                if limit is not None and len(items) >= limit:
                    break

            if on_page:
                on_page(page, len(items))
            logger.debug("savetik: página %d -> +%d (total %d)", page, added, len(items))

            if limit is not None and len(items) >= limit:
                break
            if added == 0 and not items:
                break

            next_cursor = body.get("next_cursor")
            if next_cursor is None or str(next_cursor) == "":
                break
            next_cursor = str(next_cursor)
            if next_cursor == cursor or next_cursor in seen_cursors:
                break
            seen_cursors.add(next_cursor)

            if config.BATCH_PAGE_DELAY_SECONDS > 0:
                time.sleep(config.BATCH_PAGE_DELAY_SECONDS)
            cursor = next_cursor
            page += 1

        logger.info("savetik: perfil listado con %d vídeos (%d páginas)", len(items), page)
        return items

    def list_profile_pending(
        self,
        url: str,
        is_done: Callable[[ProfileVideo], bool],
        max_pending: int | None = None,
        on_page: Callable[[int, int, int], None] | None = None,
    ) -> tuple[list[ProfileVideo], int, int]:
        """Lista **sólo los pendientes** según ``is_done``.

        Pagina hasta reunir ``max_pending`` pendientes (o hasta agotar el
        cursor / el tope de páginas). Devuelve ``(items, skipped, pages)``:
        ``skipped`` cuenta los ya descargados que se dejaron de lado.
        """
        target = normalize_tiktok_url(url)
        if not is_profile_url(target):
            raise TikVidRequestError("Esa URL no parece la de un perfil de TikTok.")

        limit = max_pending if max_pending and max_pending > 0 else None
        items: list[ProfileVideo] = []
        seen_ids: set[str] = set()
        seen_cursors: set[str] = set()
        cursor = "0"
        page = 1
        skipped = 0

        while page <= config.BATCH_MAX_PAGES:
            body = self._post(target, cursor=cursor, page=str(page))
            html = str(body.get("data") or "")
            if not html.strip():
                break

            added = 0
            for item in self._parse_cards(html):
                if item.id in seen_ids:
                    continue
                seen_ids.add(item.id)
                if is_done(item):
                    skipped += 1
                    continue
                items.append(item)
                added += 1
                if limit is not None and len(items) >= limit:
                    break

            if on_page:
                on_page(page, len(items), skipped)
            logger.debug(
                "savetik: página %d -> +%d pendientes (total %d, omitidos %d)",
                page,
                added,
                len(items),
                skipped,
            )

            if limit is not None and len(items) >= limit:
                break
            if added == 0 and not items and skipped == 0:
                break

            next_cursor = body.get("next_cursor")
            if next_cursor is None or str(next_cursor) == "":
                break
            next_cursor = str(next_cursor)
            if next_cursor == cursor or next_cursor in seen_cursors:
                break
            seen_cursors.add(next_cursor)

            if config.BATCH_PAGE_DELAY_SECONDS > 0:
                time.sleep(config.BATCH_PAGE_DELAY_SECONDS)
            cursor = next_cursor
            page += 1

        logger.info(
            "savetik: perfil con %d pendientes (%d omitidos, %d páginas)",
            len(items),
            skipped,
            page,
        )
        return items, skipped, page

    def close(self) -> None:
        """Cierra la sesión HTTP subyacente."""
        try:
            self._session.close()
        except Exception:  # pragma: no cover - cierre defensivo
            pass

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------
    def _post(self, q: str, cursor: str | None, page: str) -> dict:
        if not self._warmed:
            self._warm_up()

        payload = {"q": q, "page": page, "lang": config.SAVETIK_LANG}
        if cursor is not None:
            payload["cursor"] = cursor

        last_error: Exception | None = None
        for attempt in range(config.RETRY_ATTEMPTS):
            try:
                response = self._session.post(
                    config.SAVETIK_AJAX_SEARCH_URL,
                    data=payload,
                    timeout=config.REQUEST_TIMEOUT,
                )
            except requests.RequestException as exc:
                last_error = TikVidRequestError(
                    f"Error de red contra SavTikPro ({type(exc).__name__})."
                )
            else:
                self.last_http_status = response.status_code
                if response.status_code in config.TRANSIENT_HTTP_STATUSES:
                    last_error = TikVidRequestError(
                        f"SavTikPro respondió HTTP {response.status_code}."
                    )
                elif response.status_code == 404:
                    raise TikVidUnavailableError(
                        "El contenido solicitado no existe o es privado."
                    )
                elif response.status_code >= 400:
                    raise TikVidRequestError(
                        f"SavTikPro respondió HTTP {response.status_code}."
                    )
                else:
                    try:
                        body = response.json()
                    except ValueError as exc:
                        last_error = TikVidRequestError(
                            "SavTikPro devolvió una respuesta no JSON."
                        )
                    else:
                        return self._validate_body(body)

            if attempt < config.RETRY_ATTEMPTS - 1:
                time.sleep(config.RETRY_BACKOFF_SECONDS * (attempt + 1))

        assert last_error is not None
        raise last_error

    @staticmethod
    def _validate_body(body: object) -> dict:
        if not isinstance(body, dict):
            raise TikVidRequestError("Respuesta inesperada de SavTikPro.")
        status = str(body.get("status") or "").lower()
        if status not in ("ok", ""):
            message = str(body.get("message") or body.get("msg") or "").strip()
            raise TikVidUnavailableError(
                message or "SavTikPro no encontró el contenido solicitado."
            )
        return body

    def _warm_up(self) -> None:
        """Visita la página inicial (misma práctica que el navegador)."""
        try:
            self._session.get(
                urljoin(config.SAVETIK_BASE_URL, config.SAVETIK_HOME_PATH),
                timeout=config.REQUEST_TIMEOUT,
            )
        except requests.RequestException as exc:  # no fatal: el POST puede funcionar
            logger.debug("warm-up fallido (%s); se continúa igualmente", type(exc).__name__)
        self._warmed = True

    # ------------------------------------------------------------------
    # Tarjetas de perfil
    # ------------------------------------------------------------------
    def _parse_cards(self, html: str) -> list[ProfileVideo]:
        soup = BeautifulSoup(html or "", "html.parser")
        items: list[ProfileVideo] = []

        for card in soup.select("div.video-item"):
            item = self._card_to_video(card)
            if item is not None:
                items.append(item)
        return items

    def _card_to_video(self, card) -> ProfileVideo | None:
        anchor = card.select_one("a.pro-dl-link")
        if anchor is None:
            anchor = card.find("a", href=True)
        if anchor is None:
            return None

        raw_href = str(anchor.get("href") or "").strip()
        if not raw_href.lower().startswith(("http://", "https://")):
            raw_href = urljoin(config.SAVETIK_BASE_URL, raw_href)
        if not raw_href:
            return None

        data_name = str(anchor.get("data-name") or "")
        id_match = _ID_IN_NAME_RE.search(data_name) or _ID_IN_NAME_RE.search(raw_href)
        video_id = id_match.group(1) if id_match else anonymous_key(raw_href)

        ext_match = _EXT_IN_NAME_RE.search(data_name)
        file_format = (ext_match.group(1).lower() if ext_match else "mp4")

        title_node = card.select_one("p.text-title")
        channel_node = card.select_one("p.text-channel")
        views_node = card.select_one(".count-view .view-text span")
        thumb_node = card.select_one(".pro-thumbnail img")

        return ProfileVideo(
            id=video_id,
            title=self._text(title_node),
            channel=self._text(channel_node),
            views=self._text(views_node),
            thumbnail=str(thumb_node.get("src")).strip() if thumb_node and thumb_node.get("src") else None,
            url=raw_href,
            format=file_format,
        )

    @staticmethod
    def _text(node) -> str | None:
        if node is None:
            return None
        text = re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()
        return text or None


# ----------------------------------------------------------------------
# Fachada: un solo vídeo con fallback a TikVid
# ----------------------------------------------------------------------
def search_any(
    url: str,
    savetik: SavetikSource | None = None,
    tikvid: TikVidClient | None = None,
    *,
    debug: bool = False,
) -> TikTokResult:
    """Resuelve un vídeo único: intenta SavTikPro y, si falla, TikVid."""
    source = savetik or SavetikSource(debug=debug)
    try:
        return source.search_video(url)
    except TikVidError as exc:
        logger.warning("SavTikPro no pudo resolver el vídeo (%s); se usa TikVid", exc)
        client = tikvid or TikVidClient(debug=debug)
        return client.search(url)
