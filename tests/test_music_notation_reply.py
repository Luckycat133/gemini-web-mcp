"""A score is a text observation, independent of media or upstream state."""

import pytest

from src.constants import is_music_notation_reply


_TUNE = "X:1\nT:Synthetic melody\nM:4/4\nL:1/8\nK:G"
_NOTES = "| G2 A2 B2 c2 | d4 G4 |]"


def _fenced(tune=_TUNE, notes=_NOTES, *, fence="```", language="abc"):
    return f"{fence}{language}\n{tune}\n{notes}\n{fence}"


@pytest.mark.parametrize("text", [
    "Here is the score.\n\n" + _fenced() + "\n\nEnd of the score.",
    _fenced(_TUNE.replace("M:4/4", "M:C|").replace("K:G", "K:F#m clef=bass"),
            '[V:1] "Em7" !p! ^F,2 [Ace]2 z4 | % actual notes'),
    _fenced("%abc-2.1\n" + _TUNE.replace("M:4/4", "M:(2+3)/8").replace("K:G", "K:none"),
            "%%MIDI program 0\nV:1\nC D E2 |]", fence="~~~~", language="ABC").replace("\n", "\r\n"),
    "~~~text\nABC is a notation language.\n~~~\n" + _fenced(),
    _fenced(notes="C"),
])
def test_complete_abc_fence_contains_basic_tune_headers_and_notes(text):
    assert is_music_notation_reply(text)


@pytest.mark.parametrize("text", [
    None,
    [],
    "ABC notation is available for this request.",
    f"{_TUNE}\n{_NOTES}",
    _fenced(language="plaintext"),
    _fenced(language="abc-example"),
    "> " + _fenced().replace("\n", "\n> "),
    _fenced().rsplit("\n", 1)[0],
    _fenced(fence="````").removesuffix("````") + "```",
    "~~~~text\n" + _fenced() + "\n~~~~",
    _fenced(_TUNE.replace("X:1\n", "")),
    _fenced(_TUNE.replace("X:1", "X:one")),
    _fenced(_TUNE.replace("M:4/4\n", "")),
    _fenced(_TUNE.replace("M:4/4", "M:(+)/8")),
    _fenced(_TUNE.replace("L:1/8\n", "")),
    _fenced(_TUNE.replace("L:1/8", "L:1/0")),
    _fenced(_TUNE.replace("\nK:G", "")),
    _fenced(_TUNE.replace("K:G", "K:not a key")),
    _fenced(notes=""),
    _fenced(notes="w: D E G A\n% A B C D"),
    _fenced(notes='[K:E] "Am" !fermata! z2 x2 Z4 |]'),
    _fenced(notes="The score is still being generated."),
    _fenced(notes="X:2\nK:G\n" + _NOTES),
])
def test_abc_without_complete_structure_or_actual_notes_is_not_a_notation_reply(text):
    assert not is_music_notation_reply(text)
