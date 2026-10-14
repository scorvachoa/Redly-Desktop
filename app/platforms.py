"""Registro de plataformas soportadas por Red.ly (escritorio).

Fuente única para la UI (menú «Plataformas») y el futuro crecimiento.
Hoy sólo TikTok está verificado y funcional; el resto existe con estado
«Próximamente» para no inventar APIs (`base.md` §2).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Platform:
    """Una plataforma del registro."""

    slug: str
    name: str
    monogram: str
    accent: str
    ready: bool
    transcript_ready: bool
    blurb: str
    hosts: tuple[str, ...]


PLATFORMS: tuple[Platform, ...] = (
    Platform(
        slug="tiktok",
        name="TikTok",
        monogram="TT",
        accent="#fe2c55",
        ready=True,
        transcript_ready=False,
        blurb="Un vídeo suelto o un perfil completo, con reanudación.",
        hosts=("tiktok.com", "vm.tiktok.com", "vt.tiktok.com"),
    ),
    Platform(
        slug="youtube",
        name="YouTube",
        monogram="YT",
        accent="#ff0000",
        ready=False,
        transcript_ready=False,
        blurb="Vídeos, Shorts y directos de YouTube.",
        hosts=("youtube.com", "youtu.be", "m.youtube.com"),
    ),
    Platform(
        slug="facebook",
        name="Facebook",
        monogram="FB",
        accent="#1877f2",
        ready=False,
        transcript_ready=False,
        blurb="Publicaciones y Reels de Facebook.",
        hosts=("facebook.com", "fb.watch", "m.facebook.com"),
    ),
    Platform(
        slug="instagram",
        name="Instagram",
        monogram="IG",
        accent="#e1306c",
        ready=False,
        transcript_ready=False,
        blurb="Reels, vídeos e historias públicas.",
        hosts=("instagram.com", "instagr.am"),
    ),
    Platform(
        slug="twitter",
        name="X (Twitter)",
        monogram="X",
        accent="#0f1419",
        ready=False,
        transcript_ready=False,
        blurb="Vídeos incrustados en publicaciones de X.",
        hosts=("twitter.com", "x.com", "t.co"),
    ),
    Platform(
        slug="threads",
        name="Threads",
        monogram="TH",
        accent="#101010",
        ready=False,
        transcript_ready=False,
        blurb="Publicaciones con vídeo de Threads.",
        hosts=("threads.net", "www.threads.net"),
    ),
)


def get_platform(slug: str | None) -> Platform | None:
    """Busca una plataforma por su segmento de URL."""
    if not slug:
        return None
    key = slug.strip().lower()
    return next((p for p in PLATFORMS if p.slug == key), None)


def is_ready(platform: Platform, kind: str = "download") -> bool:
    """¿Está lista la funcionalidad pedida (``download`` o ``transcript``)?"""
    if kind == "transcript":
        return platform.transcript_ready
    return platform.ready
