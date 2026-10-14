"""Descarga en masa secuencial con manifiesto de reanudación.

Diseño (límites de la especificación §25):

* Los vídeos se descargan **uno a uno, en serie** (nunca en paralelo).
* ``downloads/manifest.json`` guarda sólo estado (`ok`/`error`), nombre de
  archivo y bytes: **nunca** URLs ni tokens, que caducan en ~1 hora.
* Reejecutar el lote omite los vídeos ya `ok` (con archivo presente) y vuelve
  a intentar los fallidos (con tokens frescos de la nueva consulta).
* Pausa configurable entre vídeos para no golpear al servicio.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from . import config
from .downloader import (
    DownloadManager,
    build_filename,
    sanitize_filename,
    sanitize_folder_name,
)
from .exceptions import TikVidDownloadError, TikVidUnavailableError
from .models import ProfileVideo

logger = logging.getLogger("tikvid.batch")

MANIFEST_VERSION = 1

StatusCallback = Callable[[str], None]
ItemStartCallback = Callable[[int, int, ProfileVideo], None]
ProgressCallback = Callable[[int, int | None], None]
ItemDoneCallback = Callable[[int, int, "BatchItemResult"], None]


def join_output_dir(root: str | Path, subdir: str | None) -> Path:
    """Une la raíz de destino con una subcarpeta opcional."""
    base = Path(root)
    if not subdir:
        return base
    folder = sanitize_folder_name(subdir)
    if folder == "perfil":
        return base
    return base / folder


def item_output_path(download_dir: Path | str, item: ProfileVideo) -> Path:
    """Ruta de salida de un vídeo del lote (compartida con el filtrado)."""
    extension = (item.format or "mp4").lstrip(".").lower()
    if item.id.isdigit():
        name = build_filename(video_id=item.id, file_format=extension)
    else:
        name = sanitize_filename(f"{config.FILENAME_PREFIX}_{item.id}.{extension}")
    return Path(download_dir) / name


@dataclass
class BatchItemResult:
    """Resultado de un vídeo dentro del lote (sin URLs)."""

    key: str
    state: str  # "ok" | "skipped" | "error"
    file: str | None = None
    bytes: int | None = None
    title: str | None = None
    error: str | None = None


@dataclass
class BatchReport:
    """Resumen de la ejecución de un lote."""

    results: list[BatchItemResult] = field(default_factory=list)

    @property
    def downloaded(self) -> int:
        return sum(1 for r in self.results if r.state == "ok")

    @property
    def skipped(self) -> int:
        return sum(1 for r in self.results if r.state == "skipped")

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if r.state == "error")

    @property
    def errors(self) -> list[BatchItemResult]:
        return [r for r in self.results if r.state == "error"]


class Manifest:
    """Estado persistente del lote (JSON pequeño, escritura atómica)."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path or config.MANIFEST_PATH)
        self._data: dict = {"version": MANIFEST_VERSION, "items": {}}
        self.load()

    def load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            logger.warning("manifiesto ilegible (%s); se empieza de cero", type(exc).__name__)
            return
        if isinstance(raw, dict) and isinstance(raw.get("items"), dict):
            self._data = {"version": MANIFEST_VERSION, "items": raw["items"]}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        tmp.replace(self.path)

    def get(self, key: str) -> dict | None:
        entry = self._data["items"].get(key)
        return entry if isinstance(entry, dict) else None

    def mark(
        self,
        key: str,
        state: str,
        *,
        file: str | None = None,
        bytes_count: int | None = None,
        title: str | None = None,
        error: str | None = None,
    ) -> None:
        entry: dict = {"state": state, "file": file, "updated": int(time.time())}
        if bytes_count is not None:
            entry["bytes"] = bytes_count
        if title:
            entry["title"] = title
        if error:
            entry["error"] = error
        self._data["items"][key] = entry
        self.save()

    def is_done(self, key: str, output_path: Path) -> bool:
        entry = self.get(key)
        if not entry or entry.get("state") != "ok":
            return False
        try:
            return output_path.is_file() and output_path.stat().st_size > 0
        except OSError:
            return False


class BatchRunner:
    """Ejecuta un lote de :class:`ProfileVideo` en orden secuencial."""

    def __init__(
        self,
        manager: DownloadManager | None = None,
        manifest: Manifest | None = None,
        *,
        delay: float | None = None,
        resume: bool = True,
        on_status: StatusCallback | None = None,
        on_item_start: ItemStartCallback | None = None,
        on_item_progress: ProgressCallback | None = None,
        on_item_done: ItemDoneCallback | None = None,
    ) -> None:
        self.manager = manager or DownloadManager()
        self.manifest = manifest if manifest is not None else Manifest()
        self.delay = config.BATCH_DELAY_SECONDS if delay is None else max(0.0, delay)
        self.resume = resume
        self.on_status = on_status
        self.on_item_start = on_item_start
        self.on_item_progress = on_item_progress
        self.on_item_done = on_item_done

    # ------------------------------------------------------------------
    def run(self, items: Sequence[ProfileVideo], *, limit: int | None = None) -> BatchReport:
        """Descarga ``items`` en orden; ``limit`` recorta la lista recibida."""
        selected = list(items)
        if limit is not None and limit > 0:
            selected = selected[:limit]

        report = BatchReport()
        total = len(selected)
        logger.info("lote: %d vídeos (delay=%.1fs resume=%s)", total, self.delay, self.resume)

        for index, item in enumerate(selected, start=1):
            key = item.id
            target = self._target_for(item)
            result: BatchItemResult

            if self.resume and self.manifest.is_done(key, target):
                result = BatchItemResult(
                    key=key,
                    state="skipped",
                    file=target.name,
                    bytes=target.stat().st_size,
                    title=item.title,
                )
            else:
                result = self._download_one(index, total, item, key, target)

            report.results.append(result)
            if self.on_item_done:
                self.on_item_done(index, total, result)

            if result.state in ("ok", "error") and index < total and self.delay > 0:
                time.sleep(self.delay)

        return report

    # ------------------------------------------------------------------
    def _download_one(
        self,
        index: int,
        total: int,
        item: ProfileVideo,
        key: str,
        target: Path,
    ) -> BatchItemResult:
        if self.on_item_start:
            self.on_item_start(index, total, item)

        try:
            info = self.manager.resolve_url(item.url)
            path = self.manager.download(
                info.final_url,
                target,
                show_progress=False,
                on_progress=self.on_item_progress,
            )
        except KeyboardInterrupt:
            raise
        except (TikVidUnavailableError, TikVidDownloadError) as exc:
            logger.warning("vídeo %s fallido: %s", key, exc)
            result = BatchItemResult(
                key=key, state="error", title=item.title, error=str(exc)
            )
            self.manifest.mark(key, "error", title=item.title, error=str(exc))
            return result

        size = path.stat().st_size
        self.manifest.mark(
            key, "ok", file=path.name, bytes_count=size, title=item.title
        )
        logger.info("vídeo %s ok (%d bytes)", key, size)
        return BatchItemResult(
            key=key, state="ok", file=path.name, bytes=size, title=item.title
        )

    def _target_for(self, item: ProfileVideo) -> Path:
        root = Path(self.manager.download_dir)
        target_dir = join_output_dir(root, item.subdir)
        return item_output_path(target_dir, item)


def summarize(report: BatchReport) -> str:
    """Frase de resumen corta para CLI/GUI."""
    return (
        f"{report.downloaded} descargado(s) · "
        f"{report.skipped} omitido(s) · "
        f"{report.failed} fallo(s)"
    )
