"""Jerarquía de excepciones de Red.ly (cliente TikVid).

Todas las excepciones propias heredan de :class:`TikVidError` para poder
capturar el fallo completo con un único ``except``.
"""

from __future__ import annotations


class TikVidError(Exception):
    """Error base del cliente TikVid de Red.ly."""


class TikVidRequestError(TikVidError):
    """Error de red o de respuesta HTTP/JSON inesperada."""


class TikVidParseError(TikVidError):
    """El HTML recibido no pudo ser interpretado."""


class TikVidInvalidUrlError(TikVidError):
    """La URL introducida no parece una URL de TikTok válida."""


class TikVidDownloadError(TikVidError):
    """Error durante la resolución o descarga de un archivo."""


class TikVidUnavailableError(TikVidError):
    """TikVid respondió, pero el contenido solicitado no está disponible."""
