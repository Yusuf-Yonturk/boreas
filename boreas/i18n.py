"""UI language. Source texts are in English; the translations live in translations.py.

Language choice: BOREAS_LANG environment variable > user setting (~/.config/boreas/boreas.conf)
> language chosen at install (/etc/xdg/boreas/boreas.conf) > English.
"""

from __future__ import annotations

import os
import re

from .translations import MESSAGES

# code → native name (the menu shows them in this order)
LANGUAGES = {
    "en": "English",
    "tr": "Türkçe",
    "zh": "简体中文",
    "hi": "हिन्दी",
    "es": "Español",
    "ar": "العربية",
    "fr": "Français",
    "pt": "Português (Brasil)",
    "ru": "Русский",
    "de": "Deutsch",
    "ja": "日本語",
}
RTL = {"ar"}
DEFAULT = "en"

current = DEFAULT
_patterns: list | None = None


def normalize(code: str | None) -> str | None:
    """'tr_TR.UTF-8', 'pt-BR', 'zh_CN' → 'tr', 'pt', 'zh'. Unknown language → None."""
    if not code:
        return None
    base = re.split(r"[_.@-]", str(code).strip().lower())[0]
    return base if base in LANGUAGES else None


def set_language(code: str | None) -> str:
    global current, _patterns
    current = normalize(code) or DEFAULT
    _patterns = None
    return current


def choose(settings=None) -> str:
    """The language code to use (does not apply it)."""
    env = normalize(os.environ.get("BOREAS_LANG"))
    if env:
        return env
    if settings is not None:
        try:
            v = normalize(settings.value("language", ""))
            if v:
                return v
        except Exception:
            pass
    return DEFAULT


def is_rtl() -> bool:
    return current in RTL


def _(text: str) -> str:
    if current == DEFAULT:
        return text
    return MESSAGES.get(text, {}).get(current) or text


def _compiled() -> list:
    """Source texts with placeholders ({name}) → full-match regular expressions."""
    global _patterns
    if _patterns is None:
        _patterns = []
        for src in MESSAGES:
            if "{" not in src:
                continue
            parts = re.split(r"\{(\w+)\}", src)
            rx = "".join(re.escape(p) if i % 2 == 0 else f"(?P<{p}>.+?)" for i, p in enumerate(parts))
            _patterns.append((re.compile(rx + r"\Z", re.S), src))
    return _patterns


def tr_msg(text: str) -> str:
    """Translates an English message from the service; the variable parts ({...}) are translated too."""
    if not text or current == DEFAULT:
        return text
    if text in MESSAGES:
        return _(text)
    for rx, src in _compiled():
        m = rx.match(text)
        if m:
            args = {k: tr_msg(v) for k, v in m.groupdict().items()}
            try:
                return _(src).format(**args)
            except (KeyError, IndexError):
                return text
    return text


BUILTIN_NAMES = {2: "Silent", 0: "Balanced", 1: "Turbo"}


def mode_name(m: dict) -> str:
    """Display name from the service's mode entry: built-in modes and "Custom N" are translated, user names are kept."""
    if m.get("builtin") and m.get("id") in BUILTIN_NAMES:
        return _(BUILTIN_NAMES[m["id"]])
    n = re.fullmatch(r"(?:Custom|Özel) (\d+)", str(m.get("name", "")))
    return _("Custom {n}").format(n=n.group(1)) if n else str(m.get("name", "?"))
