"""Shared exit-phrase detection for the voice/text front ends (main.py CLI, osiris_chat.py GUI)."""

from __future__ import annotations

import re

EXIT_PHRASES = {"exit", "quit", "goodbye", "stop", "stop listening", "bye"}
# An exit phrase spoken at the end of a short utterance still means "stop"
# ("and stop", "okay, stop listening"), but a negated or question form must not
# ("don't stop", "should I stop?").
_NEGATION_RE = re.compile(r"\b(?:don't|do not|dont|never|can't|cannot|won't|will not)\b")
_MAX_EXIT_UTTERANCE_WORDS = 5


def is_exit_command(text: str) -> bool:
    normalized = text.strip().lower().rstrip(".!?").strip()
    if not normalized:
        return False
    if normalized in EXIT_PHRASES:
        return True
    if "?" in text or _NEGATION_RE.search(normalized):
        return False
    words = normalized.replace(",", " ").split()
    if len(words) > _MAX_EXIT_UTTERANCE_WORDS:
        return False
    trailing = " ".join(words[-2:])
    return words[-1] in EXIT_PHRASES or trailing in EXIT_PHRASES