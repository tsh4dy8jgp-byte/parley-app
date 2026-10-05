"""The dialogue tutorial: steps shown in the guide panel and ready-made example dialogues."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Step:
    title: str
    body: str
    sample: Optional[str] = None
    valid: bool = True          # the sample parses as-is (checked by the tests)


STEPS = [
    Step("One line = one turn",
         "Start every line with the speaker in square brackets, then what they say. Speaker names "
         "are letters or single words: [A], [B], [Anna]. No spaces inside the brackets, "
         "so [Speaker A] will not work.",
         "[A] Good morning!\n[B] Morning! Coffee?\n[C] Yes, please."),
    Step("Give each speaker a voice",
         "Every speaker found in the text gets a row in Advanced → Voices. Leave it on Automatic "
         "(all different, alternating female and male) or pick a voice and adjust speed, pitch and "
         "volume. You can also set voices inside the text with a @voices block; whatever you change "
         "in Advanced still wins over it.",
         "@voices\nA = en-US-AvaNeural\nB = en-US-AndrewNeural   rate=-5%\n"
         "C = en-GB-SoniaNeural    pitch=+5Hz\n@end"),
    Step("Arrange the text",
         "Blank lines are ignored, so group scenes however you like. Lines starting with # are notes "
         "and are not read aloud. Keep each turn on one line, or switch on “Untagged lines continue "
         "the previous speaker” in Advanced for long paragraphs. A pause goes on its own line.",
         "# Scene 1: at the café\n[A] Is this seat free?\n\n[B] Sure, go ahead.\n[pause 1.5]\n[A] Thanks!"),
    Step("Shape a single line",
         "Add modifiers after the speaker name: slow reads the line 20 % slower, repeat=N plays it "
         "N times (good for practice), and rate / pitch / volume nudge just that line.",
         "[A slow] Could you say that again?\n[B repeat=2] Twenty-five euros.\n"
         "[C rate=+10% pitch=-5Hz] Wow, that's cheap!"),
    Step("Fix a pronunciation",
         "Write {shown|spoken} to keep one spelling in the subtitles but say another. For a word "
         "that comes up often, list it once in a @lexicon block.",
         "@lexicon\nSQL = sequel\n@end\n[A] I love {GIF|jif} files and SQL."),
    Step("Add music and sounds",
         "Put [sound Name] on its own line where a recording should play, and save a file with that "
         "name in a folder called sounds next to your text (or pick a folder in Advanced → Sounds): "
         "Anthem1.mp3, .wav, .m4a, .ogg or .flac. It is levelled to the voices; fade_in, fade_out, "
         "start, end and volume shape it. A @sounds block sets them once, and its # note becomes "
         "the subtitle. Plain narration can have sounds too.",
         "@sounds\nAnthem1   fade_out=3   # The band plays the anthem\n@end\n[A] Listen, they're playing it.\n"
         "[sound Anthem1 fade_in=1 end=1:10]\n[B] Goosebumps, every time."),
    Step("Name the characters",
         "Subtitles show the speaker tag by default. A Cast comment gives the speakers real names.",
         "# Cast: Anna = A; Ben = B; Clara = C\n[A] I'm Anna.\n[B] And I'm Ben."),
    Step("Generate",
         "Press Generate. Problems are listed with their line number and highlighted in the editor; "
         "nothing is sent to the voice service until the text is clean. Subtitles, a shadowing "
         "track and a slow version are in Advanced → Extra outputs."),
]


EXAMPLES = {
    "en": """\
# Cast: Anna = A; Ben = B; Clara = C
# One line per turn: the speaker first, then the text.

[A] Hi Ben! Have you seen Clara today?
[B] Not yet. I think she's at the café.
[pause 1]
[C] There you are! I saved you two seats.
[A] Thanks, Clara. What are you drinking?
[C slow] A flat white with oat milk.
[B repeat=2] A flat white, please.
[A] Make that two!
""",
    "de": """\
# Cast: Anna = A; Ben = B; Clara = C
# Eine Zeile pro Wortwechsel: zuerst der Sprecher, dann der Text.

[A] Hallo Ben! Hast du Clara heute schon gesehen?
[B] Noch nicht. Ich glaube, sie ist im Café.
[pause 1]
[C] Da seid ihr ja! Ich habe euch zwei Plätze freigehalten.
[A] Danke, Clara. Was trinkst du?
[C slow] Eine Melange mit Hafermilch.
[B repeat=2] Eine Melange, bitte.
[A] Für mich auch!
""",
    "fr": """\
# Cast: Anna = A; Ben = B; Clara = C
# Une ligne par réplique : d'abord le locuteur, puis le texte.

[A] Salut Ben ! Tu as vu Clara aujourd'hui ?
[B] Pas encore. Je crois qu'elle est au café.
[pause 1]
[C] Vous voilà ! Je vous ai gardé deux places.
[A] Merci, Clara. Qu'est-ce que tu bois ?
[C slow] Un café crème au lait d'avoine.
[B repeat=2] Un café crème, s'il vous plaît.
[A] Pareil pour moi !
""",
    "es": """\
# Cast: Anna = A; Ben = B; Clara = C
# Una línea por intervención: primero el hablante, luego el texto.

[A] ¡Hola, Ben! ¿Has visto a Clara hoy?
[B] Todavía no. Creo que está en la cafetería.
[pause 1]
[C] ¡Aquí estáis! Os he guardado dos sitios.
[A] Gracias, Clara. ¿Qué estás tomando?
[C slow] Un café con leche de avena.
[B repeat=2] Un café con leche, por favor.
[A] ¡Para mí también!
""",
    "ru": """\
# Cast: Анна = A; Бен = B; Клара = C
# Одна строка — одна реплика: сначала говорящий, потом текст.

[A] Привет, Бен! Ты сегодня видел Клару?
[B] Ещё нет. Кажется, она в кафе.
[pause 1]
[C] Вот вы где! Я заняла вам два места.
[A] Спасибо, Клара. Что ты пьёшь?
[C slow] Капучино на овсяном молоке.
[B repeat=2] Капучино, пожалуйста.
[A] И мне тоже!
""",
}


def example_for(locale: str) -> str:
    """The example dialogue in the locale's language, English if there is none."""
    return EXAMPLES.get(locale.split("-")[0].lower(), EXAMPLES["en"])
