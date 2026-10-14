"""Modelos Pydantic del proyecto."""

from __future__ import annotations

from pydantic import BaseModel, Field


class DownloadOption(BaseModel):
    """Una opción de descarga ofrecida por TikVid."""

    name: str
    url: str
    format: str | None = None
    quality: str | None = None

    def label(self) -> str:
        """Etiqueta corta para la CLI, por ejemplo ``MP4 HD``."""
        parts = [self.format.upper() if self.format else "OPCION"]
        if self.quality and self.quality not in ("standard",):
            parts.append(self.quality.upper())
        return " ".join(parts)


class TikTokResult(BaseModel):
    """Metadatos y opciones extraídos de la respuesta de TikVid."""

    video_id: str | None = None
    title: str | None = None
    thumbnail: str | None = None
    downloads: list[DownloadOption] = Field(default_factory=list)


class RedirectInfo(BaseModel):
    """Resultado de resolver una URL temporal (SnapCDN u otra)."""

    original_url: str
    final_url: str
    redirected: bool
    status_code: int | None = None
    hops: int = 0

    def to_dict(self) -> dict[str, object]:
        """Representación tipo diccionario (documentada en la especificación)."""
        return self.model_dump()


class ProfileVideo(BaseModel):
    """Un vídeo listado a partir de un perfil (fuente de lote)."""

    id: str
    title: str | None = None
    channel: str | None = None
    views: str | None = None
    thumbnail: str | None = None
    url: str
    format: str | None = "mp4"
    subdir: str | None = None

    def display_name(self) -> str:
        """Nombre corto para listados y logs (nunca incluye la URL)."""
        base = self.title or self.id
        return base if len(base) <= 60 else base[:57] + "..."
