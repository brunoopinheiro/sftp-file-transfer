# CLAUDE.md

Conventions for working in this repo.

## Git workflow

- **Never commit directly on `main`.** Always create a feature/fix branch and open a PR.
- **Atomic commits.** One logical change per commit — don't bundle unrelated fixes, refactors, and features together.
- **Semantic commit messages.** Use Conventional Commits prefixes, lowercase, imperative mood: `feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:`. Example: `fix: resolve the history DB path from .env at every entry point`.

## Code standards

- **Type hints** on all new functions/methods (params and return type).
- **Docstrings** on public functions/classes — one line stating purpose, not restating the signature. Skip comments otherwise unless explaining a non-obvious why.
- **Test coverage** for new/changed code, under `tests/`, mirroring the existing layout (`tests/components/`, `tests/monitor/`).
- Use `hypothesis` for property-based tests when inputs have a natural range or shape — see `tests/components/test_file_manager.py`, `test_logger_setup.py`, `test_nfce_file_writer.py`, `test_nfce_json_parser.py`, `test_nfce_xml_builder.py`, `test_sftp_manager.py` for the existing pattern.

## Common tasks

Run via `poetry run task <name>`:

- `test` — run pytest with coverage
- `lint` / `format` — ruff check / fix + format
- `clean` — remove local runtime artifacts (`logs/`, `data/`)
- `build`, `build_scheduled`, `build_history`, `build_monitor` — PyInstaller builds
