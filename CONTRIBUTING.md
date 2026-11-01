# Contributing to Boreas

**English** · [Türkçe](CONTRIBUTING.tr.md)

Thanks for helping! Bug reports, support for new laptop models, translations and code are all welcome.

## Reporting a bug or a new model

Open an [issue](../../issues/new/choose) and include:

- laptop model (e.g. `ROG Strix G713PV`), distribution and kernel version (`uname -r`)
- the output of `boreasctl status`
- service logs: `journalctl -u boreasd -b --no-pager | tail -n 100`

Boreas writes fan curves, CPU and GPU power limits, the CPU temperature limit and the GPU mode directly
to the hardware. If your model is not tested yet, say so in the issue before changing anything risky.

## Workflow

1. Fork the repository and clone your fork.
2. Create a branch from `main`: `feature/short-name`, `fix/short-name` or `i18n/language-code`.
3. Make your change and run the tests (below).
4. Open a pull request against `main`. Keep it focused on one topic and describe how you tested it
   (which laptop, which distribution).

`main` is protected: changes land through pull requests after the tests pass.

## Running from source

```bash
python3 -m unittest discover -s tests   # fake sysfs tree, no root and no ASUS laptop needed
QT_QPA_PLATFORM=offscreen python3 -m unittest discover -s tests   # without a display
sudo ./install.sh                       # install your working copy
python3 -m boreas.gui.app               # run the UI from the source tree (the service must be running)
```

Requirements: Python 3.11+ and PySide6. The service (`boreas/daemon.py`) uses only the standard
library; please keep it that way.

## Code guidelines

- Hardware access goes through `boreas/hw.py` and the other backend modules, never from the UI.
- Add a test for new behaviour. Tests use a fake sysfs tree (`BOREAS_SYSFS_ROOT`), so they run anywhere.
- User-visible text is written in English and wrapped with `_()` (UI) or kept as a plain English
  message (service errors); see Translations below.
- Write code comments and docstrings in English.
- Don't add new runtime dependencies without discussing it in an issue first.

## Translations

All translations live in [`boreas/translations.py`](boreas/translations.py). Each English source text maps
to a tuple of translations in the order given by `ORDER`.

- **Fix a translation:** edit the text in the tuple.
- **Add a language:** add its code to `ORDER`, append a translation to every entry, and add its native
  name to `LANGUAGES` in [`boreas/i18n.py`](boreas/i18n.py) and to the list in `install.sh`.
- Keep placeholders such as `{name}` exactly as they are.

`tests/test_i18n.py` fails if a text is missing in any language or a placeholder is broken.

## License

By contributing, you agree that your contribution is licensed under the [GPL-3.0](LICENSE), like the
rest of the project.
