# sftp-file-transfer

Automated fiscal-XML (NFC-e) generation and SFTP delivery for hotel-site servers, made in Python.

This project replaces a manual/PowerShell workflow with a single tool that:

- Extracts NFC-e fiscal XML documents directly from the Symphony/CAPS MySQL database (`FCR_INVOICE_DATA`).
- Uploads the generated files to a remote server over SFTP.
- Keeps a local SQLite ledger of what has already been sent, so nothing is re-sent and nothing is lost — failed uploads are automatically retried on the next cycle.
- Can run once (one-off CLI) or forever (a scheduled loop, packaged as a Windows `.exe` meant to be left running on a hotel's server).
- Ships a companion CLI (`sftp_history`) to inspect and, if needed, manually reset entries in that ledger.

## How it works

There are three independent entry points, each with its own executable:

| Entry point | Purpose | Module |
|---|---|---|
| `sftp_send` | One-off: send whatever files match right now, then exit. | `sftp_file_transfer/main.py` |
| `sftp-file-transfer-scheduled` | Long-running: repeats a *generate → send* cycle forever, every `POLL_INTERVAL_SECONDS`. This is what should be left running on a hotel's server. | `sftp_file_transfer/scheduled.py` |
| `sftp_history` | Inspect/reset the local send-history ledger. Interactive menu if run with no subcommand. | `sftp_file_transfer/history_cli.py` |

### The scheduled generate → send cycle

Every cycle, `sftp-file-transfer-scheduled` does two things, in order:

1. **Generation.** If the `NFCE_DB_*` / `NFCE_OUTPUT_PATH` environment variables are configured, it connects directly to the Symphony/CAPS MySQL database and extracts pending NFC-e documents itself (authorized invoices, cancellations, and no-protocol documents) as XML files. If they aren't configured, generation is skipped entirely (send-only mode).

   A generation failure is logged but never blocks step 2 — files that already exist locally still get sent.

2. **Send.** Scans each directory in `LOCAL_PATH`, and uploads over SFTP any file that isn't already recorded as `sent` in the local ledger (`HISTORY_DB_PATH`), plus retries anything that previously failed.

Both steps' outcomes (success, failure, per-file errors) are logged to a rotating log file and, for sends, recorded per-attempt in the SQLite ledger.

## Requirements

- Windows (the executables are built for Windows only).
- Network access from the server to both the SFTP target and (if using direct-DB generation) the Symphony/CAPS MySQL database.
- [Poetry](https://python-poetry.org/) — only needed for development/building, not for running the final `.exe`.

## Project setup (development)

Install dependencies with Poetry:

```bash
poetry install
```

Run the test suite:

```bash
poetry run task test
```

Lint / format:

```bash
poetry run task lint
poetry run task format
```

## Configuration (`.env`)

All variables are read from a `.env` file placed **next to the executable** (or in the repo root when running via Poetry). None of these are hardcoded in code.

### Required for every mode — SFTP credentials

```dotenv
SFTP_HOST=your_sftp_host
SFTP_PORT=your_sftp_port
SFTP_USER=your_sftp_user
SFTP_PASSWORD=your_sftp_password
```

### Required for scheduled mode — send loop

```dotenv
LOCAL_PATH="C:/path/to/dir1;C:/path/to/dir2"
REMOTE_PATH="/uploads"
FILE_EXTENSION=".xml"
HISTORY_DB_PATH="data/send_history.db"
POLL_INTERVAL_SECONDS=30
```

- `LOCAL_PATH`: semicolon-separated list of directories to scan for files to send.
- `REMOTE_PATH`: remote directory files are uploaded into.
- `FILE_EXTENSION`: optional; if unset, all files are sent regardless of extension.
- `HISTORY_DB_PATH`: optional; defaults to `data/send_history.db`.
- `POLL_INTERVAL_SECONDS`: optional; defaults to `30`.

### Generation — option A: direct database extraction (preferred)

```dotenv
NFCE_DB_HOST=your_mysql_host
NFCE_DB_PORT=3306
NFCE_DB_NAME=CHECKPOSTINGDB
NFCE_DB_USER=your_db_user
NFCE_DB_PASSWORD=your_db_password
NFCE_OUTPUT_PATH="C:/path/to/dir1"
NFCE_LOOKBACK_DAYS=5
```

- All six of `NFCE_DB_HOST`/`NFCE_DB_PORT`/`NFCE_DB_NAME`/`NFCE_DB_USER`/`NFCE_DB_PASSWORD`/`NFCE_OUTPUT_PATH` are required together — if any is missing, generation is skipped entirely (send-only mode).
- `NFCE_OUTPUT_PATH` should normally be one of the directories listed in `LOCAL_PATH`, so generated files get picked up by the send step.
- `NFCE_LOOKBACK_DAYS`: optional, defaults to `5` — how many days back to look for pending invoices on each run.

> If this program takes over invoking generation, disable any existing Windows Task Scheduler entry for the old generator script to avoid running it twice.

## Running from source (Poetry)

One-off send:

```bash
poetry run sftp_send --remote /uploads --local "C:/path/to/dir" --file_ext .xml
```

Scheduled loop (runs forever until `Ctrl+C`):

```bash
poetry run python -m sftp_file_transfer.scheduled
```

History inspection/reset:

```bash
poetry run sftp_history report
poetry run sftp_history list --status failed
poetry run sftp_history reset <hash-or-path-substring>
```

### `sftp_send` arguments

- `--timedelta`, `-T`: day offset from today for filtering by file date (0 = today, 1 = yesterday, ...). If omitted, all files in the local directory are considered.
- `--file_ext`, `-F`: filter files by extension. If omitted, all files are uploaded.
- `--remote`, `-R`: remote directory to upload into. **Required.**
- `--local`, `-L`: local directory to fetch files from. **Required.**
- `--help`: show the help message and exit.

### `sftp_history` commands

- `list [--status sent|failed] [--since YYYY-MM-DD] [--until YYYY-MM-DD] [--db PATH]` — list tracked records, optionally filtered.
- `failures [--db PATH]` — list only records that haven't been sent successfully yet.
- `report [--db PATH]` — summary counts (total, sent, pending/failed, date range, last sent date).
- `reset <hash-or-path-substring> [--yes] [--db PATH]` — requeue a record for resend.
- Running `sftp_history` with no subcommand launches an interactive menu.

## Building the `.exe` files

Executables are built with PyInstaller via `taskipy` tasks defined in `pyproject.toml`. Each entry point has its own build task and produces a standalone, single-file `.exe` under `dist/` — no Python installation is required on the machine that runs it.

Make sure the `builder` and `scheduling` dependency groups are installed first:

```bash
poetry install --with builder,scheduling
```

Then build whichever executable(s) you need:

```bash
poetry run task build            # sftp_send        -> dist/sftp-file-transfer.exe
poetry run task build_scheduled  # scheduled loop    -> dist/sftp-file-transfer-scheduled.exe
poetry run task build_history    # sftp_history CLI  -> dist/sftp-file-transfer-history.exe
```

Each task bundles the whole `sftp_file_transfer` package alongside the entry-point script, so the resulting `.exe` is fully self-contained.

## Deploying to a hotel's server

The tool is deployed generically: build once, then copy the relevant executable(s) to the server physically located at each hotel. Each site's own `.env` handles its own local files, database, and SFTP target — there's no in-app hotel-awareness needed.

1. Copy the built `.exe`(s) from `dist/` to a folder on the target server (e.g. `C:\SFTP`).
2. Place a `.env` file in that same folder with the variables documented above, tailored to that hotel's paths/credentials.
3. Make sure the local directories referenced by `LOCAL_PATH` / `NFCE_OUTPUT_PATH` exist and are writable.
4. If a legacy PowerShell generator was previously scheduled via Windows Task Scheduler for that site, disable that scheduled task once this tool takes over generation via direct-DB extraction, to avoid double-running it.

### Option 1 — long-running scheduled process (recommended)

Run `sftp-file-transfer-scheduled.exe` directly; it loops forever internally (via `aioclock`) at `POLL_INTERVAL_SECONDS`, so no OS-level scheduler is needed to trigger each cycle — only something to keep the process itself alive and start it on boot/logon.

A minimal launcher batch file (adjust paths), similar to the existing `run_uploader.bat`:

```bat
@echo off
cd /d "C:\SFTP"
start "" "sftp-file-transfer-scheduled.exe"
```

To have it start automatically and survive reboots, do one of:

- **Startup folder**: place a shortcut to the `.bat` in `shell:startup` so it launches at user logon.
- **Task Scheduler "At startup" trigger**: create a Basic Task with an "At startup" (or "At log on") trigger running the `.bat`/`.exe`, with "Run whether user is logged on or not" enabled if it must survive logoffs.
- **Windows Service**: for the strongest guarantee of surviving reboots/logoffs unattended, wrap the executable with a service manager (e.g. NSSM) so Windows itself supervises the process. Not currently required unless the simpler options above prove unreliable at a given site.

In all cases, verify the process actually restarts after a reboot before relying on it unattended, and check the rotating log file (see below) after the first few cycles to confirm generation and sends are both succeeding.

### Option 2 — one-off send via Windows Task Scheduler

If only the send leg is needed on a given site (generation handled elsewhere, or not needed), schedule `sftp-file-transfer.exe` (the `sftp_send` build) directly via Task Scheduler on whatever interval is desired, the same way `run_uploader.bat` already does.

## Logs and troubleshooting

- Application logs are written to a rotating log file (see `sftp_file_transfer/components/logger_setup.py`) — check this first if generation or sends aren't behaving as expected.
- Use `sftp_history report` for a quick health check, and `sftp_history failures` to see exactly which files are stuck and why (`last_error` column).
- Use `sftp_history reset <hash-or-path>` to force a specific file to be resent on the next cycle.
