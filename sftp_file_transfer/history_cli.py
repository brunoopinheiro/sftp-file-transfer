import os
import socket
from datetime import date, datetime
from typing import List, Optional

import typer
from paramiko import Transport
from rich.console import Console
from rich.prompt import Confirm, Prompt
from rich.table import Table
from typer import Argument, Context, Option, Typer

from sftp_file_transfer.components.history_tracker import (
    HistoryTracker,
    SendHistory,
    resolve_history_db_path,
)
from sftp_file_transfer.components.host_pins import (
    HostPin,
    format_fingerprint,
    get_pin,
    resolve_host_pins_path,
    save_pin,
)
from sftp_file_transfer.components.logger_setup import (
    log_startup_banner,
    setup_logger,
)

app = Typer()
console = Console()
logger = setup_logger()

_DB_HELP = (
    'Path to the send_history.db ledger (defaults to HISTORY_DB_PATH env var).'
)
_PINS_HELP = (
    'Path to the host key pin file (defaults to SFTP_HOST_PINS_PATH env var).'
)
_DEFAULT_SFTP_PORT = 22
_HOST_KEY_PROBE_TIMEOUT_SEC = 15


def _resolve_db_path(db: Optional[str]) -> str:
    """Resolve the database path, falling back to the .env file/default.

    Args:
        db: Explicit database path (--db), or None to resolve
            HISTORY_DB_PATH from the .env file — the same value every
            other entry point (scheduled.py, MonitorDaemon) uses.

    Returns:
        str: The resolved database path.
    """
    return db or str(resolve_history_db_path())


def _resolve_pins_path(pins: Optional[str]) -> str:
    """Resolve the host key pin path, falling back to env var/default.

    Args:
        pins: Explicit pin file path (--pins), or None to resolve
            SFTP_HOST_PINS_PATH from the .env file — the same value
            every other entry point uses.

    Returns:
        str: The resolved pin file path.
    """
    return pins or str(resolve_host_pins_path())


def _read_host_key(host: str, port: int) -> tuple:
    """Read a server's host key without authenticating to it.

    Stops after the key exchange, so no credential is ever offered to a
    server whose identity hasn't been confirmed yet.

    Args:
        host: SFTP hostname to inspect.
        port: SFTP port to inspect.

    Returns:
        tuple: The key type and its SHA256 fingerprint.
    """
    sock = socket.create_connection(
        (host, port),
        timeout=_HOST_KEY_PROBE_TIMEOUT_SEC,
    )
    transport = Transport(sock)
    try:
        transport.start_client()
        key = transport.get_remote_server_key()
        return key.get_name(), format_fingerprint(key.asbytes())
    finally:
        transport.close()


def _render_host_key_table(
    host: str,
    port: int,
    stored: Optional[HostPin],
    key_type: str,
    fingerprint: str,
) -> None:
    """Show the stored and observed host keys side by side."""
    table = Table(title=f'SFTP host key for {host}:{port}')
    table.add_column('Field')
    table.add_column('Value')
    if stored is None:
        table.add_row('Stored', '[yellow]no pin yet[/]')
    else:
        table.add_row('Stored', f'{stored.key_type} {stored.fingerprint}')
    table.add_row('Observed', f'{key_type} {fingerprint}')
    if stored is None:
        status = '[yellow]no pin yet[/]'
    elif stored.key_type == key_type and stored.fingerprint == fingerprint:
        status = '[green]matches[/]'
    else:
        status = '[red]MISMATCH[/]'
    table.add_row('Status', status)
    console.print(table)


def _do_trust_host(
    host: str,
    port: int,
    pins_path: str,
    yes: bool,
) -> None:
    """Read a server's host key and pin it after confirmation.

    Args:
        host: SFTP hostname to trust.
        port: SFTP port to trust.
        pins_path: Path to the host key pin file.
        yes: If True, skip the confirmation prompt.

    Returns:
        None.
    """
    try:
        key_type, fingerprint = _read_host_key(host, port)
    except Exception as e:
        console.print(f'[red]Could not read the host key: {e}[/]')
        raise typer.Exit(1)

    stored = get_pin(host, port, pins_path)
    _render_host_key_table(host, port, stored, key_type, fingerprint)

    if (
        stored is not None
        and stored.key_type == key_type
        and stored.fingerprint == fingerprint
    ):
        console.print('[green]This host key is already trusted.[/]')
        return

    if stored is not None:
        console.print(
            '[red]The host key changed. This is expected after a server '
            'rebuild or key rotation. If you did not expect it, the '
            'connection may be intercepted -- do not continue.[/]',
        )

    if not yes and not Confirm.ask('Trust this host key?'):
        raise typer.Exit(0)

    save_pin(
        host,
        port,
        HostPin(
            key_type=key_type,
            fingerprint=fingerprint,
            pinned_at=datetime.now().isoformat(timespec='seconds'),
        ),
        pins_path,
    )
    logger.warning(
        f'Host key for {host}:{port} trusted via the CLI '
        f'({key_type} {fingerprint}).',
    )
    console.print(f'[green]Trusted {key_type} {fingerprint}.[/]')


def _render_records_table(
    rows: List[SendHistory],
    title: str = 'Send History',
    show_error: bool = False,
) -> None:
    """Render a table of send-history records to the console.

    Args:
        rows: List of SendHistory ORM records from HistoryTracker.
        title: Title to display above the table.
        show_error: Whether to include error columns.

    Returns:
        None.
    """
    table = Table(title=title)
    table.add_column('Status')
    table.add_column('File')
    table.add_column('Date')
    table.add_column('Attempts', justify='right')
    if show_error:
        table.add_column('Last Error')
        table.add_column('Last Attempt')

    for row in rows:
        sent = bool(row.sent)
        status = '[green]sent[/]' if sent else '[red]failed[/]'
        style = 'dim' if sent else None
        cells = [
            status,
            row.file_name,
            row.file_date,
            str(row.attempts),
        ]
        if show_error:
            cells += [
                row.last_error or '-',
                row.last_attempt_at or '-',
            ]
        table.add_row(*cells, style=style)

    console.print(table)


def _do_reset(identifier: str, db_path: str, yes: bool) -> None:
    """Reset a record back to pending for retry.

    Resolves matching records via HistoryTracker.find_records(). If
    multiple matches exist and yes=False, displays them and prompts
    for confirmation. Resets all matches and prints the result table.

    Args:
        identifier: sha256 path_hash or substring of local path.
        db_path: Path to the send_history.db database.
        yes: If True, skip confirmation prompt for multiple matches.

    Returns:
        None.
    """
    with HistoryTracker(db_path) as tracker:
        matches = tracker.find_records(identifier)
        if not matches:
            console.print(
                f"[red]No record found matching '{identifier}'.[/]",
            )
            raise typer.Exit(1)

        if len(matches) > 1 and not yes:
            _render_records_table(
                matches,
                title='Multiple matches -- confirm to reset all',
            )
            if not Confirm.ask(
                f'Reset all {len(matches)} matched record(s)?',
            ):
                raise typer.Exit(0)

        updated = tracker.reset_record(identifier)
        console.print(
            f'[green]Reset {len(updated)} record(s) to pending.[/]',
        )
        _render_records_table(updated)


@app.command('list')
def list_(
    status: Optional[str] = Option(
        None,
        '--status',
        '-s',
        help="Filter by status: 'sent' or 'failed'. Omit for all.",
    ),
    since: Optional[str] = Option(
        None,
        '--since',
        help='ISO date, inclusive lower bound.',
    ),
    until: Optional[str] = Option(
        None,
        '--until',
        help='ISO date, inclusive upper bound.',
    ),
    db: Optional[str] = Option(
        None,
        '--db',
        '-D',
        help=_DB_HELP,
    ),
) -> None:
    """List tracked send-history records.

    Args:
        status: Filter by 'sent' or 'failed', or None for all records.
        since: ISO date string for inclusive lower bound, or None.
        until: ISO date string for inclusive upper bound, or None.
        db: Path to the database, defaults to env var/hardcoded path.

    Returns:
        None.
    """
    sent: Optional[bool] = None
    if status is not None:
        if status not in {'sent', 'failed'}:
            raise typer.BadParameter("status must be 'sent' or 'failed'")
        sent = status == 'sent'

    try:
        since_d = date.fromisoformat(since) if since else None
        until_d = date.fromisoformat(until) if until else None
    except ValueError:
        console.print('[red]Invalid date format, expected YYYY-MM-DD.[/]')
        raise typer.Exit(1)

    with HistoryTracker(_resolve_db_path(db)) as tracker:
        rows = tracker.list_records(sent=sent, since=since_d, until=until_d)
    _render_records_table(rows, show_error=True)


@app.command()
def failures(
    db: Optional[str] = Option(
        None,
        '--db',
        '-D',
        help=_DB_HELP,
    ),
) -> None:
    """List records that have not yet been sent successfully.

    Args:
        db: Path to the database, defaults to env var/hardcoded path.

    Returns:
        None.
    """
    with HistoryTracker(_resolve_db_path(db)) as tracker:
        rows = tracker.list_records(sent=False)
    _render_records_table(rows, title='Pending / Failed', show_error=True)


@app.command()
def report(
    db: Optional[str] = Option(
        None,
        '--db',
        '-D',
        help=_DB_HELP,
    ),
) -> None:
    """Show a summary report of the send-history ledger.

    Args:
        db: Path to the database, defaults to env var/hardcoded path.

    Returns:
        None.
    """
    with HistoryTracker(_resolve_db_path(db)) as tracker:
        summary = tracker.get_summary()

    table = Table(title='Send History Report', show_header=False)
    table.add_column('Metric')
    table.add_column('Value')
    date_range = (
        f'{summary["date_range_start"]} .. {summary["date_range_end"]}'
        if summary['date_range_start']
        else 'n/a'
    )
    table.add_row('Total tracked', str(summary['total']))
    table.add_row('Sent', str(summary['total_sent']))
    table.add_row('Pending / Failed', str(summary['total_pending']))
    table.add_row('Date range', date_range)
    table.add_row(
        'Last sent date',
        str(summary['last_sent_date'] or 'never'),
    )
    console.print(table)


@app.command()
def reset(
    identifier: str = Argument(
        ...,
        help='sha256 path_hash or a substring of the local path.',
    ),
    yes: bool = Option(
        False,
        '--yes',
        '-y',
        help='Skip the confirmation prompt.',
    ),
    db: Optional[str] = Option(
        None,
        '--db',
        '-D',
        help=_DB_HELP,
    ),
) -> None:
    """Force a record back to pending so it gets retried.

    Args:
        identifier: sha256 path_hash or substring of the local path.
        yes: If True, skip confirmation prompt for multiple matches.
        db: Path to the database, defaults to env var/hardcoded path.

    Returns:
        None.
    """
    _do_reset(identifier, _resolve_db_path(db), yes)


@app.command('trust-host')
def trust_host(
    host: str = Argument(
        ...,
        help='SFTP hostname whose key should be trusted.',
    ),
    port: int = Option(
        _DEFAULT_SFTP_PORT,
        '--port',
        '-p',
        help='SFTP port.',
    ),
    yes: bool = Option(
        False,
        '--yes',
        '-y',
        help='Skip the confirmation prompt.',
    ),
    pins: Optional[str] = Option(
        None,
        '--pins',
        help=_PINS_HELP,
    ),
) -> None:
    """Trust (or re-trust) an SFTP server's host key.

    Args:
        host: SFTP hostname whose key should be trusted.
        port: SFTP port.
        yes: If True, skip the confirmation prompt.
        pins: Path to the pin file, defaults to env var/default.

    Returns:
        None.
    """
    _do_trust_host(host, port, _resolve_pins_path(pins), yes)


def _interactive_reset(db_path: str) -> None:
    """Interactively prompt and reset a record.

    Args:
        db_path: Path to the send_history.db database.

    Returns:
        None.
    """
    identifier = Prompt.ask('Enter a path substring or hash')
    _do_reset(identifier, db_path, yes=False)


def _interactive_trust_host(pins_path: str) -> None:
    """Interactively prompt for a host and trust its key.

    Args:
        pins_path: Path to the host key pin file.

    Returns:
        None.
    """
    host = Prompt.ask('SFTP host', default=os.getenv('SFTP_HOST', ''))
    port = Prompt.ask(
        'SFTP port',
        default=os.getenv('SFTP_PORT', str(_DEFAULT_SFTP_PORT)),
    )
    _do_trust_host(host, int(port), pins_path, yes=False)


def _interactive_menu(db: Optional[str]) -> None:
    """Display an interactive menu for history management.

    Loops until the user chooses to exit, offering options to list
    records, show failures, show report, or reset a record.

    Args:
        db: Path to the database, or None to use env var/default.

    Returns:
        None.
    """
    db_path = _resolve_db_path(db)
    options = {
        '1': (
            'List records',
            lambda: list_(status=None, since=None, until=None, db=db_path),
        ),
        '2': ('Show failures', lambda: failures(db=db_path)),
        '3': ('Show report', lambda: report(db=db_path)),
        '4': (
            'Reset / requeue a record',
            lambda: _interactive_reset(db_path),
        ),
        '5': (
            'Trust an SFTP host key',
            lambda: _interactive_trust_host(_resolve_pins_path(None)),
        ),
        '0': ('Exit', None),
    }
    while True:
        console.print('\n[bold]SFTP Send History -- Menu[/]')
        for key, (label, _) in options.items():
            console.print(f'  {key}. {label}')
        choice = Prompt.ask(
            'Choose an option',
            choices=list(options),
            default='0',
        )
        if choice == '0':
            break
        try:
            options[choice][1]()
        except typer.Exit:
            pass


@app.callback(invoke_without_command=True)
def main_callback(
    ctx: Context,
    db: Optional[str] = Option(
        None,
        '--db',
        '-D',
        help=_DB_HELP,
    ),
) -> None:
    """Typer app callback; launches the interactive menu if no
    subcommand is invoked.

    Args:
        ctx: Typer context object.
        db: Path to the database, defaults to env var/hardcoded path.

    Returns:
        None.
    """
    log_startup_banner(logger, 'sftp-file-transfer-history')
    if ctx.invoked_subcommand:
        return
    _interactive_menu(db)


if __name__ == '__main__':
    app()
