# Contributing

Thanks for your interest. This repository is a public case study: a clean-room
implementation built from a written specification and tested only against
synthetic data. Contributions are welcome under these ground rules.

## Ground rules

- **No real data.** Never add a real cadastral reference, address, inventory
  export, database or downloaded image. Tests and examples use the generators
  in `catastro_enrich.synthetic`.
- **No secrets.** No tokens, internal URLs, user paths or corporate e-mails.
  CI runs `gitleaks` and a leak test on every push.
- **Public services only.** New data sources must be free, public and
  documented; record their licence and attribution in `docs/`.
- **Keep the driver contract.** New cadastre drivers implement
  `CadastreDriver` and return `CadastreResult` with error codes as data,
  never exceptions for business errors.

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
ruff check .
pytest
```

Code and identifiers are in English; documentation is bilingual
(`README.md` in English, `README.es.md` in Spanish). Public functions carry
type hints. Add an entry to `CHANGELOG.md` for user-visible changes.

## Pull requests

1. Open an issue first for anything beyond a bug fix.
2. One topic per pull request, with tests.
3. CI must be green: tests on Python 3.11 and 3.12, `ruff`, `gitleaks` and
   the leak test.
