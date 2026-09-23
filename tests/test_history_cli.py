from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from sftp_file_transfer.components.history_tracker import HistoryTracker
from sftp_file_transfer.components.host_pins import (
    HostPin,
    format_fingerprint,
    get_pin,
    save_pin,
)
from sftp_file_transfer.history_cli import app

runner = CliRunner()

_SERVER_KEY_BLOB = b'\x00\x00\x00\x0bssh-ed25519-fake-blob'
_SERVER_KEY_TYPE = 'ssh-ed25519'


def _mock_transport_with_key(
    blob=_SERVER_KEY_BLOB,
    key_type=_SERVER_KEY_TYPE,
):
    """Build a mock Transport that presents a fixed host key."""
    mock_transport = MagicMock()
    server_key = mock_transport.get_remote_server_key.return_value
    server_key.asbytes.return_value = blob
    server_key.get_name.return_value = key_type
    return mock_transport


def _patched_transport(mock_transport):
    """Patch the paramiko pieces trust-host uses to reach a server."""
    return (
        patch(
            'sftp_file_transfer.history_cli.socket.create_connection',
            return_value=MagicMock(),
        ),
        patch(
            'sftp_file_transfer.history_cli.Transport',
            return_value=mock_transport,
        ),
    )


def _seed(db_path, sent_name='sent.txt', failed_name='failed.txt'):
    sent_file = db_path.parent / sent_name
    failed_file = db_path.parent / failed_name
    sent_file.touch()
    failed_file.touch()

    with HistoryTracker(db_path) as tracker:
        tracker.record_attempt(sent_file, success=True)
        tracker.record_attempt(failed_file, success=False, error='oops')

    return sent_file, failed_file


def test_list_command_shows_all_records(tmp_path):
    """Test the list command with no filters shows every record."""
    db_path = tmp_path / 'history.db'
    _seed(db_path)

    result = runner.invoke(app, ['list', '--db', str(db_path)])

    assert result.exit_code == 0
    assert 'sent.txt' in result.stdout
    assert 'failed.txt' in result.stdout


def test_list_command_filters_by_status_failed(tmp_path):
    """Test the list command with --status failed shows only failures."""
    db_path = tmp_path / 'history.db'
    _seed(db_path)

    result = runner.invoke(
        app,
        ['list', '--status', 'failed', '--db', str(db_path)],
    )

    assert result.exit_code == 0
    assert 'failed.txt' in result.stdout
    assert 'sent.txt' not in result.stdout


def test_list_command_rejects_invalid_status(tmp_path):
    """Test the list command rejects an invalid --status value."""
    db_path = tmp_path / 'history.db'
    _seed(db_path)

    result = runner.invoke(
        app,
        ['list', '--status', 'bogus', '--db', str(db_path)],
    )

    assert result.exit_code != 0


def test_failures_command_shows_only_pending(tmp_path):
    """Test the failures command shows only sent=0 rows."""
    db_path = tmp_path / 'history.db'
    _seed(db_path)

    result = runner.invoke(app, ['failures', '--db', str(db_path)])

    assert result.exit_code == 0
    assert 'failed.txt' in result.stdout
    assert 'sent.txt' not in result.stdout


def test_report_command_shows_summary_counts(tmp_path):
    """Test the report command renders aggregate counts."""
    db_path = tmp_path / 'history.db'
    _seed(db_path)

    result = runner.invoke(app, ['report', '--db', str(db_path)])

    assert result.exit_code == 0
    assert 'Total tracked' in result.stdout
    assert 'Last sent date' in result.stdout


def test_reset_command_happy_path_single_match_with_yes_flag(tmp_path):
    """Test the reset command resets a single match with --yes."""
    db_path = tmp_path / 'history.db'
    _, failed_file = _seed(db_path)
    identifier = HistoryTracker.hash_path(failed_file)

    result = runner.invoke(
        app,
        ['reset', identifier, '--yes', '--db', str(db_path)],
    )

    assert result.exit_code == 0
    assert 'Reset 1 record' in result.stdout


def test_reset_command_not_found_exits_nonzero(tmp_path):
    """Test the reset command exits non-zero when nothing matches."""
    db_path = tmp_path / 'history.db'
    _seed(db_path)

    result = runner.invoke(
        app,
        ['reset', 'does-not-exist', '--db', str(db_path)],
    )

    assert result.exit_code != 0


def test_reset_command_multiple_matches_without_yes_prompts_and_aborts(
    tmp_path,
):
    """Test the reset command prompts on multiple matches and aborts."""
    db_path = tmp_path / 'history.db'
    subdir = tmp_path / 'batch'
    subdir.mkdir()
    file_a = subdir / 'a.txt'
    file_b = subdir / 'b.txt'
    file_a.touch()
    file_b.touch()

    with HistoryTracker(db_path) as tracker:
        tracker.record_attempt(file_a, success=False, error='oops')
        tracker.record_attempt(file_b, success=False, error='oops')

    result = runner.invoke(
        app,
        ['reset', 'batch', '--db', str(db_path)],
        input='n\n',
    )

    assert result.exit_code == 0
    assert 'Reset 2 record' not in result.stdout

    with HistoryTracker(db_path) as tracker:
        pending = tracker.get_pending_failed_files()

    expected_pending = 2
    assert len(pending) == expected_pending


def test_trust_host_pins_a_new_key(tmp_path):
    """Test trust-host records the key for a never-seen endpoint."""
    pins_path = tmp_path / 'known_hosts.json'
    mock_transport = _mock_transport_with_key()
    socket_patch, transport_patch = _patched_transport(mock_transport)

    with socket_patch, transport_patch:
        result = runner.invoke(
            app,
            ['trust-host', 'sftp.example.com', '--pins', str(pins_path), '-y'],
        )

    assert result.exit_code == 0
    stored = get_pin('sftp.example.com', 22, pins_path)
    assert stored.fingerprint == format_fingerprint(_SERVER_KEY_BLOB)


def test_trust_host_never_authenticates(tmp_path):
    """Test reading a host key never sends the password.

    The whole point is to inspect an untrusted server safely, so the
    command must stop at the key exchange.
    """
    pins_path = tmp_path / 'known_hosts.json'
    mock_transport = _mock_transport_with_key()
    socket_patch, transport_patch = _patched_transport(mock_transport)

    with socket_patch, transport_patch:
        runner.invoke(
            app,
            ['trust-host', 'sftp.example.com', '--pins', str(pins_path), '-y'],
        )

    mock_transport.auth_password.assert_not_called()
    mock_transport.auth_publickey.assert_not_called()
    mock_transport.connect.assert_not_called()


def test_trust_host_shows_both_fingerprints_on_a_mismatch(tmp_path):
    """Test a changed key is shown against the stored one before asking."""
    pins_path = tmp_path / 'known_hosts.json'
    save_pin(
        'sftp.example.com',
        22,
        HostPin(
            key_type=_SERVER_KEY_TYPE,
            fingerprint='SHA256:the-stored-one',
            pinned_at='2026-09-22T14:03:11',
        ),
        pins_path,
    )
    mock_transport = _mock_transport_with_key()
    socket_patch, transport_patch = _patched_transport(mock_transport)

    with socket_patch, transport_patch:
        result = runner.invoke(
            app,
            ['trust-host', 'sftp.example.com', '--pins', str(pins_path)],
            input='n\n',
        )

    assert 'SHA256:the-stored-one' in result.stdout
    assert format_fingerprint(_SERVER_KEY_BLOB) in result.stdout
    assert 'MISMATCH' in result.stdout


def test_trust_host_declining_leaves_the_stored_pin(tmp_path):
    """Test answering no keeps the previously trusted key."""
    pins_path = tmp_path / 'known_hosts.json'
    original = HostPin(
        key_type=_SERVER_KEY_TYPE,
        fingerprint='SHA256:the-stored-one',
        pinned_at='2026-09-22T14:03:11',
    )
    save_pin('sftp.example.com', 22, original, pins_path)
    socket_patch, transport_patch = _patched_transport(
        _mock_transport_with_key(),
    )

    with socket_patch, transport_patch:
        result = runner.invoke(
            app,
            ['trust-host', 'sftp.example.com', '--pins', str(pins_path)],
            input='n\n',
        )

    assert result.exit_code == 0
    assert get_pin('sftp.example.com', 22, pins_path) == original


def test_trust_host_confirming_overwrites_the_stored_pin(tmp_path):
    """Test answering yes re-pins the endpoint to the observed key."""
    pins_path = tmp_path / 'known_hosts.json'
    save_pin(
        'sftp.example.com',
        22,
        HostPin(
            key_type=_SERVER_KEY_TYPE,
            fingerprint='SHA256:the-stored-one',
            pinned_at='2026-09-22T14:03:11',
        ),
        pins_path,
    )
    socket_patch, transport_patch = _patched_transport(
        _mock_transport_with_key(),
    )

    with socket_patch, transport_patch:
        result = runner.invoke(
            app,
            ['trust-host', 'sftp.example.com', '--pins', str(pins_path)],
            input='y\n',
        )

    assert result.exit_code == 0
    stored = get_pin('sftp.example.com', 22, pins_path)
    assert stored.fingerprint == format_fingerprint(_SERVER_KEY_BLOB)


def test_trust_host_reports_an_already_trusted_key(tmp_path):
    """Test a matching key needs no confirmation and changes nothing."""
    pins_path = tmp_path / 'known_hosts.json'
    socket_patch, transport_patch = _patched_transport(
        _mock_transport_with_key(),
    )
    with socket_patch, transport_patch:
        runner.invoke(
            app,
            ['trust-host', 'sftp.example.com', '--pins', str(pins_path), '-y'],
        )
    before = pins_path.read_text(encoding='utf-8')

    socket_patch, transport_patch = _patched_transport(
        _mock_transport_with_key(),
    )
    with socket_patch, transport_patch:
        result = runner.invoke(
            app,
            ['trust-host', 'sftp.example.com', '--pins', str(pins_path)],
        )

    assert result.exit_code == 0
    assert 'already trusted' in result.stdout.lower()
    assert pins_path.read_text(encoding='utf-8') == before


def test_trust_host_exits_nonzero_when_unreachable(tmp_path):
    """Test an unreachable server is reported as a failure."""
    pins_path = tmp_path / 'known_hosts.json'

    with patch(
        'sftp_file_transfer.history_cli.socket.create_connection',
        side_effect=OSError('refused'),
    ):
        result = runner.invoke(
            app,
            ['trust-host', 'sftp.example.com', '--pins', str(pins_path), '-y'],
        )

    assert result.exit_code == 1
    assert not pins_path.exists()


def test_trust_host_uses_the_given_port(tmp_path):
    """Test --port pins the endpoint under that port, not the default."""
    pins_path = tmp_path / 'known_hosts.json'
    socket_patch, transport_patch = _patched_transport(
        _mock_transport_with_key(),
    )

    with socket_patch, transport_patch:
        runner.invoke(
            app,
            [
                'trust-host',
                'sftp.example.com',
                '--port',
                '2222',
                '--pins',
                str(pins_path),
                '-y',
            ],
        )

    assert get_pin('sftp.example.com', 2222, pins_path) is not None
    assert get_pin('sftp.example.com', 22, pins_path) is None
