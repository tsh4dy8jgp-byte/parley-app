"""User-facing diagnostics shared by the speech engines."""

from __future__ import annotations

from typing import Optional


class TTSError(RuntimeError):
    """Speech retries exhausted; safe to display without an exception traceback."""


def describe_tts_error(error: Optional[Exception]) -> str:
    """Explain HTTP 403 without displaying the service's token-bearing URL."""
    from aiohttp import ClientResponseError

    if isinstance(error, ClientResponseError) and error.status == 403:
        import edge_tts

        return (
            f"Microsoft rejected the speech request (HTTP 403; edge-tts {edge_tts.__version__}). "
            "Check that your system date and time are correct and that your network or proxy "
            "allows the Microsoft speech service. If this persists, update Parley; "
            "desktop apps need a rebuilt bundle to receive edge-tts fixes."
        )
    return str(error)
