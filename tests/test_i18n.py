"""Çeviri testleri: her kaynak metnin her dilde karşılığı var mı, yer tutucular korunmuş mu."""

import ast
import os
import re
import string
import sys
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)

from boreas import i18n  # noqa: E402
from boreas.translations import MESSAGES, ORDER, _T  # noqa: E402


def gui_sources() -> set:
    """Arayüz kodunda _("...") ile çevrilen metinler ve _() ile kullanılan sabit tabloların değerleri."""
    keys = set()
    for f in ("boreas/gui/app.py", "boreas/gui/fans.py", "boreas/i18n.py"):
        with open(os.path.join(ROOT, f), encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "_" and n.args \
                    and isinstance(n.args[0], ast.Constant):
                keys.add(n.args[0].value)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from boreas.gui import app as A, fans as F
    keys |= {l for _, l in A.GPU_LABELS + A.AURA_MODES + A.AURA_SPEEDS}
    keys |= set(A.GPU_HINTS.values()) | set(A.THEME_NAMES.values())
    keys |= {t for p in A.PAGES.values() for t in p} - {"Boreas"}
    keys |= set(i18n.BUILTIN_NAMES.values())
    for d in (F.TITLES, F.PPT_LABELS, F.NV_LABELS):
        keys |= set(d.values())
    keys |= {"charging", "discharging", "full", "not charging", "Saved", "Not saved", "Applying…"}
    return keys


def fields(s: str) -> set:
    return {f for _, f, _, _ in string.Formatter().parse(s) if f}


class TestCatalog(unittest.TestCase):
    def test_every_language_listed(self):
        self.assertEqual(set(ORDER) | {"en"}, set(i18n.LANGUAGES))

    def test_every_entry_has_all_languages(self):
        for src, tr in _T.items():
            self.assertEqual(len(tr), len(ORDER), src)
            for t in tr:
                self.assertTrue(t.strip(), src)

    def test_placeholders_kept(self):
        for src, tr in MESSAGES.items():
            for lang, t in tr.items():
                self.assertEqual(fields(t), fields(src), f"{lang}: {src}")

    def test_all_gui_text_translated(self):
        missing = sorted(gui_sources() - set(MESSAGES))
        self.assertEqual(missing, [])


class TestLookup(unittest.TestCase):
    def tearDown(self):
        i18n.set_language("en")
        os.environ.pop("BOREAS_LANG", None)

    def test_english_is_default(self):
        os.environ.pop("BOREAS_LANG", None)
        self.assertEqual(i18n.choose(None), "en")
        self.assertEqual(i18n._("Quit"), "Quit")

    def test_choose_order(self):
        class S:
            def value(self, k, d=None):
                return "de_DE" if k == "language" else d
        self.assertEqual(i18n.choose(S()), "de")
        os.environ["BOREAS_LANG"] = "pt_BR.UTF-8"
        self.assertEqual(i18n.choose(S()), "pt")
        os.environ["BOREAS_LANG"] = "xx"
        self.assertEqual(i18n.choose(S()), "de")  # bilinmeyen dil atlanır

    def test_daemon_messages_translated(self):
        i18n.set_language("tr")
        self.assertEqual(i18n.tr_msg("Could not write the cpu fan curve: Battery not found"),
                         "cpu fan eğrisi yazılamadı: Pil bulunamadı")
        self.assertEqual(i18n.tr_msg("Could not write to the SMU: busy"), "SMU'ya yazılamadı: busy")
        self.assertEqual(i18n.tr_msg("CPU temperature limit must be 75..96"), "CPU sıcaklık limiti 75..96 olmalı")
        self.assertEqual(i18n.tr_msg("something new"), "something new")

    def test_daemon_messages_match_templates(self):
        """Servisin ürettiği iletiler katalogdaki şablonlarla eşleşmeli (kaynak değişirse test kırılır)."""
        with open(os.path.join(ROOT, "boreas/daemon.py"), encoding="utf-8") as fh:
            src = fh.read()
        for m in re.findall(r'set_notice\((f?"[^"]+")', src):
            text = m.lstrip("f").strip('"')
            if text.startswith("{CONFLICT_NOTICE}"):
                continue
            if text.endswith(". "):  # iki parçalı Eco iletisi: ikinci parça ayrı dizgede
                text += "It will be applied after a reboot."
            pattern = re.sub(r"\{[^}]+\}", "X", text)
            i18n.set_language("tr")
            self.assertNotEqual(i18n.tr_msg(pattern), pattern, text)

    def test_mode_names(self):
        i18n.set_language("es")
        self.assertEqual(i18n.mode_name({"id": 2, "name": "Silent", "builtin": True}), "Silencioso")
        self.assertEqual(i18n.mode_name({"id": 3, "name": "Özel 1", "builtin": False}), "Personalizado 1")
        self.assertEqual(i18n.mode_name({"id": 4, "name": "Oyun", "builtin": False}), "Oyun")


class TestNoLongDashes(unittest.TestCase):
    def test_no_em_or_en_dash(self):
        """Projede uzun çizgi (U+2014, U+2013) kullanılmaz."""
        bad = []
        for base, dirs, files in os.walk(ROOT):
            dirs[:] = [d for d in dirs if d not in (".git", "__pycache__")]
            for name in files:
                if name.endswith((".png", ".svg", ".pyc", ".log", ".txt")) or name == "LICENSE":
                    continue
                path = os.path.join(base, name)
                with open(path, encoding="utf-8", errors="ignore") as fh:
                    for no, line in enumerate(fh, 1):
                        if chr(0x2014) in line or chr(0x2013) in line:
                            bad.append(f"{os.path.relpath(path, ROOT)}:{no}")
        self.assertEqual(bad, [])
