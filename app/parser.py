"""Parser del HTML devuelto por ``POST /api/ajaxSearch``.

El HTML de TikVid puede cambiar, asi que cada dato se busca con una lista
de selectores priorizados y, si ninguno funciona, se devuelve ``None`` en
lugar de lanzar un error.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from .config import (
    EXCLUDED_DOWNLOAD_CLASSES,
    FORMAT_PATTERN,
    DOWNLOAD_TEXT_PATTERN,
    SELECTORS,
    TIKVID_BASE_URL,
    TIKTOK_VIDEO_ID_PATTERN,
)
from .models import DownloadOption, TikTokResult

logger = logging.getLogger("tikvid.parser")

_WHITESPACE_RE = re.compile(r"\s+")


class TikVidParser:
    """Extrae metadatos y opciones de descarga del HTML de TikVid."""

    def parse(self, html: str) -> TikTokResult:
        """Parsea el HTML de ``ajaxSearch`` y devuelve un :class:`TikTokResult`."""
        soup = BeautifulSoup(html or "", "html.parser")
        result = TikTokResult(
            video_id=self._extract_video_id(soup),
            title=self._extract_title(soup),
            thumbnail=self._extract_thumbnail(soup),
            downloads=self._extract_downloads(soup),
        )
        logger.debug(
            "parse: title=%s video_id=%s downloads=%d",
            "si" if result.title else "no",
            result.video_id or "-",
            len(result.downloads),
        )
        return result

    # ------------------------------------------------------------------
    # Selectores con fallback
    # ------------------------------------------------------------------
    @staticmethod
    def _select_first(soup: BeautifulSoup, selectors: tuple[str, ...]) -> Tag | None:
        for selector in selectors:
            try:
                found = soup.select_one(selector)
            except Exception:  # selector invalido: el HTML de TikVid puede cambiar
                logger.debug("selector descartado: %s", selector)
                continue
            if found is not None:
                return found
        return None

    @staticmethod
    def _clean_text(value: str | None) -> str | None:
        if not value:
            return None
        cleaned = _WHITESPACE_RE.sub(" ", value.replace("\xa0", " ")).strip()
        return cleaned or None

    # ------------------------------------------------------------------
    # Titulo
    # ------------------------------------------------------------------
    def _extract_title(self, soup: BeautifulSoup) -> str | None:
        node = self._select_first(soup, SELECTORS.title)
        if node is None:
            return None
        return self._clean_text(node.get_text(" ", strip=True))

    # ------------------------------------------------------------------
    # Thumbnail
    # ------------------------------------------------------------------
    def _extract_thumbnail(self, soup: BeautifulSoup) -> str | None:
        node = self._select_first(soup, SELECTORS.thumbnail)
        if node is None:
            return None
        raw = node.get("poster") if node.name == "video" else node.get("src")
        if not raw:
            raw = node.get("data-src") or node.get("data-original")
        if not raw:
            return None
        value = str(raw).strip()
        if not value or value.startswith("data:"):
            return None
        return urljoin(TIKVID_BASE_URL, value)

    # ------------------------------------------------------------------
    # ID del video
    # ------------------------------------------------------------------
    def _extract_video_id(self, soup: BeautifulSoup) -> str | None:
        for selector in SELECTORS.video_id_input:
            try:
                node = soup.select_one(selector)
            except Exception:
                continue
            if node is None:
                continue
            value = node.get("value") or node.get("data-value")
            if value and str(value).strip().isdigit():
                return str(value).strip()

        for anchor in soup.find_all("a", href=True):
            match = TIKTOK_VIDEO_ID_PATTERN.search(str(anchor["href"]))
            if match:
                return match.group(1)

        for element in soup.find_all(True):
            for attr in ("data-video-id", "data-item-id", "data-id"):
                value = element.get(attr)
                if value and str(value).strip().isdigit():
                    return str(value).strip()

        match = TIKTOK_VIDEO_ID_PATTERN.search(soup.get_text(" ", strip=True))
        if match:
            return match.group(1)
        return None

    # ------------------------------------------------------------------
    # Opciones de descarga
    # ------------------------------------------------------------------
    def _extract_downloads(self, soup: BeautifulSoup) -> list[DownloadOption]:
        anchors = self._collect_download_anchors(soup)
        options: list[DownloadOption] = []
        seen: set[str] = set()

        for anchor in anchors:
            raw_href = self._href_of(anchor)
            if not raw_href:
                continue
            url = urljoin(TIKVID_BASE_URL, raw_href)
            if url in seen:
                continue
            seen.add(url)

            name = self._clean_text(anchor.get_text(" ", strip=True)) or "Download"
            options.append(
                DownloadOption(
                    name=name,
                    url=url,
                    format=self._infer_format(name, url),
                    quality=self._infer_quality(name),
                )
            )

        return options

    def _collect_download_anchors(self, soup: BeautifulSoup) -> list[Tag]:
        for selector in SELECTORS.download_primary:
            try:
                found = soup.select(selector)
            except Exception:
                continue
            usable = [a for a in found if self._is_download_anchor(a)]
            if usable:
                return usable

        href_hosts = SELECTORS.download_href_hosts
        fallback: list[Tag] = []
        for anchor in soup.find_all("a", href=True):
            href = str(anchor.get("href", "")).lower()
            if any(host in href for host in href_hosts) and self._is_download_anchor(
                anchor
            ):
                fallback.append(anchor)
        if fallback:
            return fallback

        # Ultimo recurso: cualquier enlace cuyo texto parezca un boton de descarga.
        for anchor in soup.find_all("a", href=True):
            text = self._clean_text(anchor.get_text(" ", strip=True)) or ""
            if DOWNLOAD_TEXT_PATTERN.search(text) and self._is_download_anchor(anchor):
                fallback.append(anchor)
        return fallback

    @staticmethod
    def _is_download_anchor(anchor: Tag) -> bool:
        href = str(anchor.get("href", "")).strip()
        if not href or href.startswith(("#", "/", "javascript:", "data:")):
            return False
        if not href.lower().startswith(("http://", "https://")):
            return False
        classes = " ".join(anchor.get("class") or []).lower()
        if any(excluded in classes for excluded in EXCLUDED_DOWNLOAD_CLASSES):
            return False
        if anchor.find_parent(id="popup_play") is not None:
            return False
        return True

    @staticmethod
    def _href_of(anchor: Tag) -> str | None:
        for attr in ("href", "data-href", "data-url"):
            value = anchor.get(attr)
            if value:
                return str(value).strip()
        return None

    # ------------------------------------------------------------------
    # Formato y calidad a partir del texto del boton
    # ------------------------------------------------------------------
    @staticmethod
    def _infer_format(name: str, url: str) -> str | None:
        match = FORMAT_PATTERN.search(name)
        if match:
            return match.group(1).lower()
        lowered_url = url.lower()
        for candidate in ("mp4", "mp3", "m4a", "aac", "webm", "wav"):
            if f".{candidate}" in lowered_url:
                return candidate
        if "mime_type=video" in lowered_url:
            return "mp4"
        if "mime_type=audio" in lowered_url:
            return "mp3"
        return None

    @staticmethod
    def _infer_quality(name: str) -> str | None:
        lowered = name.lower()
        if "mp3" in lowered or "audio" in lowered or "m4a" in lowered:
            return "audio"
        if re.search(r"\b(hd|1080|720|fullhd)\b", lowered):
            return "hd"
        if re.search(r"\b(sd|480|360)\b", lowered):
            return "sd"
        if "mp4" in lowered or "video" in lowered:
            return "standard"
        return None
