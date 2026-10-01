import asyncio

from parley.audiobook import cli


def test_cli_warns_about_a_multilingual_voice(tmp_path, monkeypatch, capsys):
    async def build(text, voice, output, **kwargs):
        output.write_bytes(b"mp3")
        return output

    monkeypatch.setattr(cli, "build_audiobook", build)
    book = tmp_path / "book.txt"
    book.write_text("Hello.", encoding="utf-8")
    for voice, warned in [("en-US-AndrewMultilingualNeural", True), ("en-US-AvaNeural", False)]:
        args = cli.build_parser().parse_args(["-i", str(book), "--voice", voice, "-o", str(tmp_path / "b.mp3")])
        asyncio.run(cli.run(args))
        assert ("multilingual voice" in capsys.readouterr().err) == warned
