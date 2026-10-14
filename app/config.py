"""Configuración centralizada del proyecto.

Aquí viven ENDPOINTS, HEADERS, SELECTORS, TIMEOUTS y utilidades de
máscara de URLs. Nada de secretos ni cookies personales.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_env_file(path: Path) -> None:
    """Carga un archivo .env sin dependencias externas.

    No sobreescribe variables ya presentes en el entorno.
    """
    if not path.is_file():
        return
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _env(key: str, default: str) -> str:
    return os.environ.get(key, default)


_load_env_file(PROJECT_ROOT / ".env")

# --------------------------------------------------------------------------
# Endpoints (confirmados con DevTools; no inventar otros)
# --------------------------------------------------------------------------
TIKVID_BASE_URL = _env("TIKVID_BASE_URL", "https://tikvid.io").rstrip("/")
TIKVID_AJAX_SEARCH_URL = _env(
    "TIKVID_AJAX_SEARCH_URL", f"{TIKVID_BASE_URL}/api/ajaxSearch"
)

# Fuente de listados de perfil (misma familia de producto que TikVid, build
# más nueva: resuelve perfiles con paginación por cursor). Sin API documentada.
SAVETIK_BASE_URL = _env("SAVETIK_BASE_URL", "https://savetikpro.com").rstrip("/")
SAVETIK_AJAX_SEARCH_URL = _env(
    "SAVETIK_AJAX_SEARCH_URL", f"{SAVETIK_BASE_URL}/api/ajaxSearch"
)
SAVETIK_HOME_PATH = _env("SAVETIK_HOME_PATH", "/en")
SAVETIK_LANG = _env("SAVETIK_LANG", "en")

# --------------------------------------------------------------------------
# Timeouts y reintentos
# --------------------------------------------------------------------------
REQUEST_TIMEOUT = float(_env("REQUEST_TIMEOUT", "30"))
DOWNLOAD_TIMEOUT = float(_env("DOWNLOAD_TIMEOUT", "120"))
MAX_REDIRECTS = int(_env("MAX_REDIRECTS", "5"))
RETRY_ATTEMPTS = int(_env("RETRY_ATTEMPTS", "3"))
RETRY_BACKOFF_SECONDS = float(_env("RETRY_BACKOFF_SECONDS", "0.8"))
TRANSIENT_HTTP_STATUSES = frozenset({429, 500, 502, 503, 504})

# --------------------------------------------------------------------------
# Descargas
# --------------------------------------------------------------------------
DOWNLOAD_DIRECTORY = Path(_env("DOWNLOAD_DIRECTORY", "downloads"))
CHUNK_SIZE = 1024 * 1024
MAX_FILENAME_LENGTH = 120
MAX_FOLDER_LENGTH = 64
FILENAME_PREFIX = _env("FILENAME_PREFIX", "Redly")

# Configuración de la GUI (tema, etc.): JSON pequeño en el home del usuario.
SETTINGS_PATH = Path(
    _env("REDLY_SETTINGS_PATH", str(Path.home() / ".redly" / "settings.json"))
)

# --------------------------------------------------------------------------
# Lote / descarga en masa
# --------------------------------------------------------------------------
MANIFEST_PATH = Path(_env("MANIFEST_PATH", "downloads/manifest.json"))
BATCH_DELAY_SECONDS = float(_env("BATCH_DELAY_SECONDS", "1.0"))
BATCH_PAGE_DELAY_SECONDS = float(_env("BATCH_PAGE_DELAY_SECONDS", "1.0"))
BATCH_MAX_PAGES = int(_env("BATCH_MAX_PAGES", "100"))
BATCH_DEFAULT_MAX_VIDEOS = int(_env("BATCH_DEFAULT_MAX_VIDEOS", "0"))  # 0 = sin límite
SAVETIK_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "en-US,en;q=0.9",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
    "Origin": SAVETIK_BASE_URL,
    "Referer": f"{SAVETIK_BASE_URL}{SAVETIK_HOME_PATH}",
}

# --------------------------------------------------------------------------
# Headers (navegador normal, sin cookies ni tokens personales)
# --------------------------------------------------------------------------
HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "en-US,en;q=0.9",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
    "Origin": TIKVID_BASE_URL,
    "Referer": f"{TIKVID_BASE_URL}/",
    "sec-ch-ua": '"Chromium";v="131", "Not_A Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-site": "same-origin",
    "sec-fetch-mode": "cors",
    "sec-fetch-dest": "empty",
}

DOWNLOAD_HEADERS: dict[str, str] = {
    "User-Agent": HEADERS["User-Agent"],
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
}

# --------------------------------------------------------------------------
# Lista blanca de hosts de CDN para la resolución de URLs temporales
# (evita usar el gestor como proxy hacia cualquier destino).
# --------------------------------------------------------------------------
ALLOWED_DOWNLOAD_HOSTS: tuple[str, ...] = (
    "snapcdn.app",
    "pro.snapcdn.app",
    "dl.snapcdn.app",
    "tiktokcdn.com",
    "tiktokcdn-us.com",
    "tiktokcdn-eu.com",
    "tokcdn.com",
    "tik-cdn.com",
    "s1.tik-cdn.com",
    "s2.tik-cdn.com",
)


# --------------------------------------------------------------------------
# Selectores HTML (con fallbacks; TikVid puede cambiar su plantilla)
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Selectors:
    """Selectores CSS priorizados: se prueba el primero y se itera."""

    thumbnail: tuple[str, ...] = (
        "div.thumbnail img",
        "div.image-tik img",
        ".tik-left img",
        "video[poster]",
        "img.thumbnail",
        "img",
    )
    title: tuple[str, ...] = (
        "div.thumbnail h3",
        "div.content h3",
        ".tik-video h3",
        ".video-data h3",
        "h3",
        "h1",
        "[class*='title']",
    )
    video_id_input: tuple[str, ...] = (
        "#TikTokId",
        "input[name='TikTokId']",
        "input[name='video_id']",
        "input[id*='tiktok' i]",
        "input[id*='video' i]",
    )
    download_primary: tuple[str, ...] = (
        "a.tik-button-dl",
        ".dl-action a[href]",
        ".download a[href]",
        "a[download]",
    )
    download_href_hosts: tuple[str, ...] = (
        "dl.snapcdn.app",
        "tiktokcdn",
        "tokcdn",
        "tik-cdn",
    )


SELECTORS = Selectors()

# Texto de botón que identifica una opción de descarga en el fallback final.
DOWNLOAD_TEXT_PATTERN = re.compile(
    r"\bdownload\b.*\b(mp4|mp3|m4a|aac|wav|webm|video|audio)\b", re.IGNORECASE
)
FORMAT_PATTERN = re.compile(r"\b(mp4|mp3|m4a|aac|wav|webm)\b", re.IGNORECASE)
TIKTOK_VIDEO_ID_PATTERN = re.compile(r"tiktok\.com/@[^/\s]+/video/(\d{6,25})", re.I)

# Excluidos del fallback de descarga (enlaces de navegación, no de archivo).
EXCLUDED_DOWNLOAD_CLASSES = ("more-video", "close", "popup")


# --------------------------------------------------------------------------
# Utilidades de saneado / máscara
# --------------------------------------------------------------------------
_TOKEN_KEY_PATTERN = re.compile(r"(token|signature|sign|auth|key)=[^&\s]+", re.I)


def mask_url(url: str, visible: int = 28) -> str:
    """Oculta tokens y firmas de una URL para poder mostrarla en logs."""
    if not url:
        return ""
    masked = _TOKEN_KEY_PATTERN.sub(lambda m: f"{m.group(1)}=<oculto>", url)
    if len(masked) > visible:
        return f"{masked[:visible]}...<long={len(url)}>"
    return masked


def url_host(url: str) -> str | None:
    """Devuelve solo el host de una URL (seguro para logs)."""
    from urllib.parse import urlsplit

    try:
        return urlsplit(url).hostname
    except ValueError:
        return None


def is_allowed_download_host(host: str | None) -> bool:
    """¿El host está en la lista blanca de CDNs?"""
    if not host:
        return False
    lower = host.lower()
    return any(lower == allowed or lower.endswith(f".{allowed}") for allowed in ALLOWED_DOWNLOAD_HOSTS)


# --------------------------------------------------------------------------
# Ajustes de la GUI (tema, etc.): best-effort, nunca lanza al leer/escribir.
# --------------------------------------------------------------------------
def load_settings() -> dict:
    """Lee ``settings.json`` del usuario; devuelve ``{}`` si no existe o falla."""
    try:
        raw = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def save_settings(data: dict) -> None:
    """Escritura atómica de los ajustes de la GUI."""
    try:
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = SETTINGS_PATH.with_suffix(SETTINGS_PATH.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(SETTINGS_PATH)
    except OSError:
        pass
