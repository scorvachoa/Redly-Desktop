"""Resolución de URLs temporales y descarga en streaming.

Trata SnapCDN (``dl.snapcdn.app``) como un posible redireccionador hacia el
CDN de TikTok, sin asumir que todas las URLs son de ese dominio. Las URLs
firmadas/temporales sólo viven durante la ejecución: nunca se guardan ni se
registran completas en los logs.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urljoin

import requests
from rich.console import Console
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TextColumn,
    TransferSpeedColumn,
)

from . import config
from .exceptions import TikVidDownloadError, TikVidUnavailableError
from .models import RedirectInfo

logger = logging.getLogger("tikvid.downloader")

_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def sanitize_filename(name: str, max_length: int = config.MAX_FILENAME_LENGTH) -> str:
    """Elimina path traversal y caracteres inválidos (Windows/POSIX)."""
    cleaned = _INVALID_CHARS.sub("_", str(name or ""))
    cleaned = cleaned.replace("..", "_")
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    cleaned = re.sub(r"_+", "_", cleaned)

    if not cleaned or set(cleaned) <= {"_", "."}:
        return "archivo"

    stem, dot, ext = cleaned.rpartition(".")
    if dot and stem:
        if stem.upper() in _WINDOWS_RESERVED:
            stem = f"_{stem}"
        cleaned = f"{stem}.{ext}"

    if len(cleaned) > max_length:
        stem, dot, ext = cleaned.rpartition(".")
        if dot and len(ext) <= 10:
            keep = max_length - len(ext) - 1
            cleaned = f"{stem[:keep]}.{ext}"
        else:
            cleaned = cleaned[:max_length]
    return cleaned.strip(" .") or "archivo"


def sanitize_folder_name(
    name: str, max_length: int = config.MAX_FOLDER_LENGTH
) -> str:
    """Igual que ``sanitize_filename`` pero para nombres de carpeta."""
    cleaned = _INVALID_CHARS.sub("_", str(name or ""))
    cleaned = cleaned.replace("..", "_")
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    cleaned = re.sub(r"_+", "_", cleaned)
    cleaned = re.sub(r"[._]+$", "", cleaned)

    if not cleaned or set(cleaned) <= {"_", "."}:
        return "perfil"

    if cleaned.upper() in _WINDOWS_RESERVED:
        cleaned = f"_{cleaned}"

    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length]
    return cleaned.strip(" .") or "perfil"


def build_filename(
    video_id: str | None = None,
    quality: str | None = None,
    file_format: str | None = "mp4",
    prefix: str | None = None,
) -> str:
    """Genera un nombre seguro, por ejemplo ``Redly_7616_hd.mp4``."""
    brand = prefix or config.FILENAME_PREFIX
    parts = [brand, video_id if video_id and video_id.isdigit() else "video"]
    if quality and quality not in ("standard",):
        parts.append(quality)
    extension = (file_format or "mp4").lstrip(".").lower()
    return sanitize_filename(f"{'_'.join(parts)}.{extension}")


class DownloadManager:
    """Resuelve redirecciones y descarga archivos por streaming."""

    def __init__(
        self,
        session: requests.Session | None = None,
        download_dir: Path | str | None = None,
        *,
        debug: bool = False,
        console: Console | None = None,
    ) -> None:
        self._session = session or self._build_session()
        self.download_dir = Path(download_dir or config.DOWNLOAD_DIRECTORY)
        self.debug = debug
        self.console = console or Console()

    @staticmethod
    def _build_session() -> requests.Session:
        session = requests.Session()
        session.headers.update(config.DOWNLOAD_HEADERS)
        return session

    # ------------------------------------------------------------------
    # Resolución de redirecciones
    # ------------------------------------------------------------------
    def resolve_url(self, url: str) -> RedirectInfo:
        """Sigue las redirecciones sin descargar el cuerpo del archivo."""
        host = config.url_host(url)
        if not config.is_allowed_download_host(host):
            raise TikVidDownloadError(
                f"Host no permitido para descarga: {host or '?'}"
            )
        current = url
        hops = 0
        status: int | None = None

        while hops <= config.MAX_REDIRECTS:
            try:
                response = self._session.get(
                    current,
                    timeout=config.REQUEST_TIMEOUT,
                    allow_redirects=False,
                    stream=True,
                )
            except requests.RequestException as exc:
                raise TikVidDownloadError(
                    f"No se pudo resolver la URL temporal ({type(exc).__name__})."
                ) from exc

            try:
                status = response.status_code
                location = response.headers.get("Location")
                if status in (301, 302, 303, 307, 308) and location:
                    hops += 1
                    current = urljoin(current, location)
                    next_host = config.url_host(current)
                    if not config.is_allowed_download_host(next_host):
                        raise TikVidDownloadError(
                            f"Redirección a host no permitido: {next_host or '?'}"
                        )
                    continue
                break
            finally:
                response.close()
        else:
            raise TikVidDownloadError(
                f"Demasiadas redirecciones (máximo {config.MAX_REDIRECTS})."
            )

        info = RedirectInfo(
            original_url=url,
            final_url=current,
            redirected=hops > 0,
            status_code=status,
            hops=hops,
        )
        logger.debug(
            "resolve: redirected=%s hops=%d status=%s host=%s",
            info.redirected,
            info.hops,
            info.status_code,
            config.url_host(info.final_url),
        )
        return info

    # ------------------------------------------------------------------
    # Descarga
    # ------------------------------------------------------------------
    def download(
        self,
        url: str,
        output_path: Path | str | None = None,
        *,
        show_progress: bool = True,
        on_progress: Callable[[int, int | None], None] | None = None,
    ) -> Path:
        """Descarga ``url`` en streaming y devuelve la ruta del archivo.

        ``on_progress(bytes_descargados, total_si_se_conoce)`` se invoca desde
        el hilo que realiza la descarga (la GUI lo encola hacia el hilo UI).
        """
        info = self.resolve_url(url)

        if output_path is None:
            raise TikVidDownloadError("Se debe indicar la ruta de destino.")
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)

        try:
            response = self._session.get(
                info.final_url,
                stream=True,
                timeout=(config.REQUEST_TIMEOUT, config.DOWNLOAD_TIMEOUT),
                allow_redirects=True,
            )
        except requests.RequestException as exc:
            raise TikVidDownloadError(
                f"No se pudo iniciar la descarga ({type(exc).__name__})."
            ) from exc

        with response:
            self._ensure_successful(response)
            total = self._content_length(response)
            self._write_stream(response, target, total, show_progress, on_progress)

        logger.debug("descarga completa: %s (%d bytes)", target.name, target.stat().st_size)
        return target

    # ------------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------------
    def _ensure_successful(self, response: requests.Response) -> None:
        status = response.status_code
        if status in (401, 403, 404, 410):
            raise TikVidUnavailableError("La URL temporal ha expirado.")
        if status >= 400:
            raise TikVidDownloadError(f"El servidor respondió HTTP {status}.")

    @staticmethod
    def _content_length(response: requests.Response) -> int | None:
        raw = response.headers.get("Content-Length")
        if not raw:
            return None
        try:
            value = int(raw)
        except ValueError:
            return None
        return value if value > 0 else None

    def _write_stream(
        self,
        response: requests.Response,
        target: Path,
        total: int | None,
        show_progress: bool,
        on_progress: Callable[[int, int | None], None] | None = None,
    ) -> None:
        progress = Progress(
            TextColumn("[progress.description]"),
            BarColumn(bar_width=30),
            DownloadColumn(),
            TransferSpeedColumn(),
            console=self.console,
            transient=False,
        )
        context = progress if show_progress else _NullProgress()

        try:
            with context as active:
                task = active.add_task("Descargando", total=total)
                written = 0
                if on_progress:
                    on_progress(0, total)
                with open(target, "wb") as handle:
                    for chunk in response.iter_content(chunk_size=config.CHUNK_SIZE):
                        if not chunk:
                            continue
                        handle.write(chunk)
                        written += len(chunk)
                        active.update(task, advance=len(chunk))
                        if on_progress:
                            on_progress(written, total)
        except Exception:
            target.unlink(missing_ok=True)
            raise


class _NullProgress:
    """Sustituto de ``rich.Progress`` cuando no se quiere barra de progreso."""

    def __enter__(self) -> "_NullProgress":
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def add_task(self, description: str, total: int | None = None) -> int:
        return 0

    def update(self, task_id: int, advance: int = 0) -> None:
        return None
