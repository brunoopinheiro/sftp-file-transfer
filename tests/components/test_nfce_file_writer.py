from datetime import datetime, timedelta, timezone

from hypothesis import given
from hypothesis import strategies as st

from sftp_file_transfer.components.nfce_file_writer import (
    already_exists,
    apply_document_datetime,
    build_output_paths,
    extract_document_datetime,
    write_document,
)


def test_build_output_paths_returns_expected_filenames(tmp_path):
    """Test that build_output_paths returns the three expected filenames."""
    chave = 'ABC123XYZ789'

    result = build_output_paths(tmp_path, chave)

    assert result.authorized == tmp_path / f'NFe{chave}.xml'
    assert result.cancellation == tmp_path / f'NFe{chave}_cancelamento.xml'
    assert result.no_protocol == tmp_path / f'NFe{chave}_semprotocolo.xml'


def test_build_output_paths_accepts_a_plain_string_output_dir(tmp_path):
    """Test build_output_paths works when output_dir is a str, not a Path.

    NFCE_OUTPUT_PATH always arrives as a plain string straight from the
    environment (NfceConfig never wraps it in Path), so this must not
    raise "unsupported operand type(s) for /: 'str' and 'str'".
    """
    chave = 'ABC123XYZ789'

    result = build_output_paths(str(tmp_path), chave)

    assert result.authorized == tmp_path / f'NFe{chave}.xml'


def test_already_exists_returns_false_when_no_files_exist(tmp_path):
    """Test that already_exists returns False when no files exist."""
    chave = 'TEST123'
    paths = build_output_paths(tmp_path, chave)

    assert already_exists(paths) is False


def test_already_exists_returns_true_when_authorized_exists(tmp_path):
    """Test that already_exists returns True when authorized exists."""
    chave = 'TEST123'
    paths = build_output_paths(tmp_path, chave)
    paths.authorized.touch()

    assert already_exists(paths) is True


def test_already_exists_returns_true_when_cancellation_exists(tmp_path):
    """Test that already_exists returns True when cancellation exists."""
    chave = 'TEST123'
    paths = build_output_paths(tmp_path, chave)
    paths.cancellation.touch()

    assert already_exists(paths) is True


def test_already_exists_returns_true_when_no_protocol_exists(tmp_path):
    """Test that already_exists returns True when no_protocol exists."""
    chave = 'TEST123'
    paths = build_output_paths(tmp_path, chave)
    paths.no_protocol.touch()

    assert already_exists(paths) is True


def test_write_document_writes_utf8_without_bom(tmp_path):
    """Test that write_document writes UTF-8 content with no BOM."""
    test_file = tmp_path / 'test.xml'
    content = '<test>Hello World</test>'

    write_document(test_file, content)

    raw_bytes = test_file.read_bytes()

    # Check no BOM
    assert not raw_bytes.startswith(b'\xef\xbb\xbf')

    # Check content is correct
    assert raw_bytes.decode('utf-8') == content


def test_extract_document_datetime_parses_dhemi():
    """Test that extract_document_datetime parses <dhEmi> tag."""
    xml = '<root><dhEmi>2026-07-31T04:50:41-03:00</dhEmi></root>'

    result = extract_document_datetime(xml)

    expected = datetime.fromisoformat('2026-07-31T04:50:41-03:00')
    assert result == expected


def test_extract_document_datetime_falls_back_to_dhevento():
    """Test that extract_document_datetime falls back to <dhEvento>."""
    xml = '<root><dhEvento>2026-07-31T04:50:41-03:00</dhEvento></root>'

    result = extract_document_datetime(xml)

    expected = datetime.fromisoformat('2026-07-31T04:50:41-03:00')
    assert result == expected


def test_extract_document_datetime_returns_none_when_no_tags():
    """Test that extract_document_datetime returns None when no tags."""
    xml = '<root><other>2026-07-31T04:50:41-03:00</other></root>'

    result = extract_document_datetime(xml)

    assert result is None


def test_extract_document_datetime_returns_none_on_invalid_datetime():
    """Test that extract_document_datetime returns None on invalid value."""
    xml = '<root><dhEmi>not-a-date</dhEmi></root>'

    result = extract_document_datetime(xml)

    assert result is None


def test_apply_document_datetime_sets_mtime(tmp_path):
    """Test that apply_document_datetime sets a file's mtime."""
    test_file = tmp_path / 'test.xml'
    test_file.touch()

    when = datetime(2026, 7, 31, 4, 50, 41)
    apply_document_datetime(test_file, when)

    file_mtime = test_file.stat().st_mtime
    expected_timestamp = when.timestamp()

    # Allow 1 second tolerance for filesystem precision
    assert abs(file_mtime - expected_timestamp) < 1


@given(
    dt=st.datetimes(
        min_value=datetime(2000, 1, 1),
        max_value=datetime(2099, 12, 31),
    ),
    offset_minutes=st.integers(min_value=-14 * 60, max_value=14 * 60),
    tag=st.sampled_from(['dhEmi', 'dhEvento']),
)
def test_extract_document_datetime_round_trips_any_valid_iso_value(
    dt,
    offset_minutes,
    tag,
):
    """Test that any valid ISO datetime with a UTC offset, embedded in
    either <dhEmi> or <dhEvento>, is parsed back to an equal datetime."""
    aware_dt = dt.replace(tzinfo=timezone(timedelta(minutes=offset_minutes)))
    xml = f'<root><{tag}>{aware_dt.isoformat()}</{tag}></root>'

    result = extract_document_datetime(xml)

    assert result == aware_dt


@given(text=st.text(max_size=300))
def test_extract_document_datetime_never_raises_on_arbitrary_text(text):
    """Test that extract_document_datetime never raises, only returns
    None or a datetime, for arbitrary (possibly malformed) input."""
    result = extract_document_datetime(text)

    assert result is None or isinstance(result, datetime)
