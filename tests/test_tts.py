"""Speech retry behavior and diagnostics, without contacting Microsoft."""

import asyncio
from types import SimpleNamespace

import edge_tts
import pytest
from aiohttp import ClientResponseError, WSServerHandshakeError
from yarl import URL

from parley.audiobook import builder
from parley.dialog_tts.tts import CachedBackend, EdgeBackend
from parley.dialog_tts.voices import VoiceSpec
from parley.tts_errors import TTSError, describe_tts_error


def forbidden(error_type=WSServerHandshakeError):
    request = SimpleNamespace(real_url=URL("https://speech.example/?Sec-MS-GEC=secret"))
    return error_type(request, (), status=403, message="Invalid response status")


@pytest.fixture(params=["dialogue", "audiobook"])
def engine(request, monkeypatch):
    async def no_sleep(_):
        pass

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    if request.param == "dialogue":
        backend = EdgeBackend(retries=3)

        async def synth():
            return await backend.synth("Hello.", VoiceSpec("en-US-AvaNeural"))
    else:
        async def synth():
            return await builder._synthesize_chunk(
                "Hello.", "en-US-AvaNeural", "+0%", "+0%", "+0Hz"
            )
    return synth


def test_persistent_403_has_diagnostics_and_cause(engine, monkeypatch):
    error = forbidden()
    calls = []

    class Rejected:
        def __init__(self, *args, **kwargs):
            calls.append(args)

        async def stream(self):
            raise error
            yield  # make an async generator

    monkeypatch.setattr(edge_tts, "Communicate", Rejected)
    with pytest.raises(TTSError) as caught:
        asyncio.run(engine())
    message = str(caught.value)
    assert len(calls) == 3
    assert caught.value.__cause__ is error
    assert "HTTP 403" in message and edge_tts.__version__ in message
    assert "date and time" in message and "network or proxy" in message
    assert "rebuilt bundle" in message
    assert "secret" not in message and "Sec-MS-GEC" not in message


def test_retry_discards_partial_audio(engine, monkeypatch):
    calls = []

    class Interrupted:
        def __init__(self, *args, **kwargs):
            calls.append(args)
            self.attempt = len(calls)

        async def stream(self):
            if self.attempt == 1:
                yield {"type": "audio", "data": b"partial"}
                raise ConnectionError("connection lost")
            yield {"type": "audio", "data": b"complete"}

    monkeypatch.setattr(edge_tts, "Communicate", Interrupted)
    assert asyncio.run(engine()) == b"complete"
    assert len(calls) == 2


@pytest.mark.parametrize("error_type", [ClientResponseError, WSServerHandshakeError])
def test_403_formatter_handles_http_and_websocket_errors(error_type):
    assert "HTTP 403" in describe_tts_error(forbidden(error_type))


def test_other_errors_keep_their_description():
    assert describe_tts_error(ConnectionError("connection lost")) == "connection lost"


def test_failed_audio_is_not_cached(tmp_path, monkeypatch):
    class Rejected:
        def __init__(self, *args, **kwargs):
            pass

        async def stream(self):
            yield {"type": "audio", "data": b"partial"}
            raise forbidden()

    monkeypatch.setattr(edge_tts, "Communicate", Rejected)
    backend = CachedBackend(EdgeBackend(retries=1), tmp_path)
    with pytest.raises(RuntimeError, match="HTTP 403"):
        asyncio.run(backend.synth("Hello.", VoiceSpec("en-US-AvaNeural")))
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("engine", ["dialogue", "audiobook"])
def test_cli_displays_speech_error_without_token_bearing_traceback(engine, monkeypatch):
    import sys

    error = forbidden()

    def fail(_):
        raise TTSError(describe_tts_error(error)) from error

    if engine == "dialogue":
        from parley.dialog_tts import cli

        monkeypatch.setattr(cli, "cmd_render", fail)
        invoke = lambda: cli.main(["render", "example.txt"])
    else:
        from parley.audiobook import cli

        async def run(args):
            fail(args)

        monkeypatch.setattr(cli, "run", run)
        monkeypatch.setattr(sys, "argv", ["audiobook"])
        invoke = cli.main

    with pytest.raises(SystemExit) as caught:
        invoke()
    assert "HTTP 403" in str(caught.value)
    assert "secret" not in str(caught.value)
    assert caught.value.__suppress_context__
