import base64
from datetime import date, timedelta
from unittest.mock import Mock, patch

from sftp_file_transfer.components.nfce_generator import (
    run_nfce_extraction,
)


def _b64(text):
    """Base64-encode text the way extract_invoice_content really returns
    it, so mocked values survive a real base64-decode step."""
    return base64.b64encode(text.encode('utf-8')).decode('ascii')


def _make_mock_row(chave, uf, invoice_id, status, json_exp):
    """Create a mock row object with standard fields."""
    return Mock(
        chave=chave,
        uf=uf,
        invoice_id=invoice_id,
        status=status,
        json_exp=json_exp,
    )


def _make_mock_paths(authorized, no_protocol, cancellation):
    """Create a mock paths object with standard fields."""
    return Mock(
        authorized=authorized,
        no_protocol=no_protocol,
        cancellation=cancellation,
    )


def _make_mock_build_result(kind, content, date_source_fragment):
    """Create a mock classify_and_build result."""
    return Mock(
        kind=kind,
        content=content,
        date_source_fragment=date_source_fragment,
    )


def test_skipped_row_when_output_already_exists(tmp_path):
    """Test that a row is counted as skipped when output already exists."""
    engine = Mock()
    output_dir = str(tmp_path)
    lookback_days = 30

    row = _make_mock_row('ABC123', 'PE', 1, 'approved', '{"json": "data"}')
    mock_paths = _make_mock_paths('/path/auth', '/path/nopr', '/path/canc')

    with (
        patch(
            'sftp_file_transfer.components.nfce_generator.fetch_pending_invoice_rows',
            return_value=[row],
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.build_output_paths',
            return_value=mock_paths,
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.already_exists',
            return_value=True,
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.extract_invoice_content',
        ) as mock_extract,
        patch(
            'sftp_file_transfer.components.nfce_generator.classify_and_build',
        ) as mock_classify,
    ):
        result = run_nfce_extraction(engine, output_dir, lookback_days)

        assert result.total == 1
        assert result.skipped == 1
        assert result.generated == 0
        assert result.cancellations == 0
        assert result.no_protocol == 0
        assert result.errors == 0
        mock_extract.assert_not_called()
        mock_classify.assert_not_called()


def test_authorized_with_protocol_increments_generated(tmp_path):
    """Test that authorized_with_protocol increments generated count."""
    engine = Mock()
    output_dir = str(tmp_path)
    lookback_days = 30

    row = _make_mock_row('XYZ789', 'SP', 2, 'approved', '{"data": "value"}')
    mock_paths = _make_mock_paths('/path/auth', '/path/nopr', '/path/canc')
    mock_result = _make_mock_build_result(
        'authorized_with_protocol',
        '<nfeProc>content</nfeProc>',
        '<NFe>date_fragment</NFe>',
    )

    with (
        patch(
            'sftp_file_transfer.components.nfce_generator.fetch_pending_invoice_rows',
            return_value=[row],
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.build_output_paths',
            return_value=mock_paths,
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.already_exists',
            return_value=False,
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.extract_invoice_content',
            side_effect=[_b64('request_b64'), _b64('response_b64')],
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.classify_and_build',
            return_value=mock_result,
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.write_document',
        ) as mock_write,
        patch(
            'sftp_file_transfer.components.nfce_generator.extract_document_datetime',
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.apply_document_datetime',
        ),
    ):
        result = run_nfce_extraction(engine, output_dir, lookback_days)

        assert result.total == 1
        assert result.generated == 1
        assert result.skipped == 0
        assert result.cancellations == 0
        assert result.no_protocol == 0
        assert result.errors == 0
        mock_write.assert_called_once()
        call_args = mock_write.call_args
        assert call_args[0][0] == '/path/auth'


def test_cancellation_with_return_increments_cancellations(tmp_path):
    """Test that cancellation_with_return increments cancellations count."""
    engine = Mock()
    output_dir = str(tmp_path)
    lookback_days = 30

    row = _make_mock_row('DEF456', 'MG', 3, 'cancelled', '{"cancel": true}')
    mock_paths = _make_mock_paths('/path/auth', '/path/nopr', '/path/canc')
    mock_result = _make_mock_build_result(
        'cancellation_with_return',
        '<procEventoNFe>content</procEventoNFe>',
        '<evento>date_fragment</evento>',
    )

    with (
        patch(
            'sftp_file_transfer.components.nfce_generator.fetch_pending_invoice_rows',
            return_value=[row],
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.build_output_paths',
            return_value=mock_paths,
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.already_exists',
            return_value=False,
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.extract_invoice_content',
            side_effect=[_b64('req'), _b64('resp')],
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.classify_and_build',
            return_value=mock_result,
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.write_document',
        ) as mock_write,
        patch(
            'sftp_file_transfer.components.nfce_generator.extract_document_datetime',
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.apply_document_datetime',
        ),
    ):
        result = run_nfce_extraction(engine, output_dir, lookback_days)

        assert result.total == 1
        assert result.cancellations == 1
        assert result.generated == 0
        assert result.no_protocol == 0
        assert result.skipped == 0
        assert result.errors == 0
        mock_write.assert_called_once()
        call_args = mock_write.call_args
        assert call_args[0][0] == '/path/canc'


def test_authorized_no_protocol_increments_no_protocol(tmp_path):
    """Test that authorized_no_protocol increments no_protocol count."""
    engine = Mock()
    output_dir = str(tmp_path)
    lookback_days = 30

    row = _make_mock_row('GHI123', 'RJ', 4, 'approved', '{"no_proto": true}')
    mock_paths = _make_mock_paths('/path/auth', '/path/nopr', '/path/canc')
    mock_result = _make_mock_build_result(
        'authorized_no_protocol',
        '<NFe>content</NFe>',
        '<NFe>date_fragment</NFe>',
    )

    with (
        patch(
            'sftp_file_transfer.components.nfce_generator.fetch_pending_invoice_rows',
            return_value=[row],
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.build_output_paths',
            return_value=mock_paths,
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.already_exists',
            return_value=False,
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.extract_invoice_content',
            side_effect=[_b64('req'), _b64('resp')],
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.classify_and_build',
            return_value=mock_result,
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.write_document',
        ) as mock_write,
        patch(
            'sftp_file_transfer.components.nfce_generator.extract_document_datetime',
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.apply_document_datetime',
        ),
    ):
        result = run_nfce_extraction(engine, output_dir, lookback_days)

        assert result.total == 1
        assert result.no_protocol == 1
        assert result.generated == 0
        assert result.cancellations == 0
        assert result.skipped == 0
        assert result.errors == 0
        mock_write.assert_called_once()
        call_args = mock_write.call_args
        assert call_args[0][0] == '/path/nopr'


def test_unrecognized_increments_errors_no_write(tmp_path):
    """Test that unrecognized kind increments errors without writing."""
    engine = Mock()
    output_dir = str(tmp_path)
    lookback_days = 30

    row = _make_mock_row('JKL789', 'BA', 5, 'unknown', '{"unknown": true}')
    mock_paths = _make_mock_paths('/path/auth', '/path/nopr', '/path/canc')
    mock_result = _make_mock_build_result(
        'unrecognized',
        None,
        None,
    )

    with (
        patch(
            'sftp_file_transfer.components.nfce_generator.fetch_pending_invoice_rows',
            return_value=[row],
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.build_output_paths',
            return_value=mock_paths,
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.already_exists',
            return_value=False,
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.extract_invoice_content',
            side_effect=[_b64('req'), _b64('resp')],
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.classify_and_build',
            return_value=mock_result,
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.write_document',
        ) as mock_write,
    ):
        result = run_nfce_extraction(engine, output_dir, lookback_days)

        assert result.total == 1
        assert result.errors == 1
        assert result.generated == 0
        assert result.cancellations == 0
        assert result.no_protocol == 0
        assert result.skipped == 0
        mock_write.assert_not_called()


def test_exception_in_classify_increments_errors_continues(tmp_path):
    """Test that exception in classify increments errors and continues
    processing."""
    expected_total = 2
    engine = Mock()
    output_dir = str(tmp_path)
    lookback_days = 30

    row_fail = _make_mock_row('ERR111', 'SP', 10, 'approved', '{"fail": true}')
    row_ok = _make_mock_row('OK111', 'MG', 11, 'approved', '{"ok": true}')
    mock_paths = _make_mock_paths('/path/auth', '/path/nopr', '/path/canc')
    mock_result_ok = _make_mock_build_result(
        'authorized_with_protocol',
        '<nfeProc>content</nfeProc>',
        '<NFe>date</NFe>',
    )

    with (
        patch(
            'sftp_file_transfer.components.nfce_generator.fetch_pending_invoice_rows',
            return_value=[row_fail, row_ok],
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.build_output_paths',
            return_value=mock_paths,
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.already_exists',
            return_value=False,
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.extract_invoice_content',
            side_effect=[
                _b64('req'),
                _b64('resp'),
                _b64('req'),
                _b64('resp'),
            ],
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.classify_and_build',
            side_effect=[Exception('Processing failed'), mock_result_ok],
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.write_document',
        ) as mock_write,
        patch(
            'sftp_file_transfer.components.nfce_generator.extract_document_datetime',
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.apply_document_datetime',
        ),
    ):
        result = run_nfce_extraction(engine, output_dir, lookback_days)

        assert result.total == expected_total
        assert result.errors == 1
        assert result.generated == 1
        assert result.cancellations == 0
        assert result.no_protocol == 0
        assert result.skipped == 0
        mock_write.assert_called_once()


def test_fetch_pending_called_with_correct_since_date(tmp_path):
    """Test that fetch_pending_invoice_rows is called with correct since
    date."""
    engine = Mock()
    output_dir = str(tmp_path)
    lookback_days = 15

    with patch(
        'sftp_file_transfer.components.nfce_generator.fetch_pending_invoice_rows',
        return_value=[],
    ) as mock_fetch:
        run_nfce_extraction(engine, output_dir, lookback_days)

        expected_since = date.today() - timedelta(days=lookback_days)
        mock_fetch.assert_called_once_with(engine, expected_since)


def test_fetch_pending_called_with_zero_lookback(tmp_path):
    """Test that fetch is called with today's date when lookback_days is 0."""
    engine = Mock()
    output_dir = str(tmp_path)
    lookback_days = 0

    with patch(
        'sftp_file_transfer.components.nfce_generator.fetch_pending_invoice_rows',
        return_value=[],
    ) as mock_fetch:
        run_nfce_extraction(engine, output_dir, lookback_days)

        expected_since = date.today()
        mock_fetch.assert_called_once_with(engine, expected_since)


def test_returns_all_zero_summary_when_empty_fetch(tmp_path):
    """Test that all-zero summary is returned when fetch returns empty list."""
    engine = Mock()
    output_dir = str(tmp_path)
    lookback_days = 30

    with patch(
        'sftp_file_transfer.components.nfce_generator.fetch_pending_invoice_rows',
        return_value=[],
    ):
        result = run_nfce_extraction(engine, output_dir, lookback_days)

        assert result.total == 0
        assert result.generated == 0
        assert result.cancellations == 0
        assert result.no_protocol == 0
        assert result.skipped == 0
        assert result.errors == 0


def test_cancellation_no_return_increments_cancellations(tmp_path):
    """Test that cancellation_no_return also increments cancellations."""
    engine = Mock()
    output_dir = str(tmp_path)
    lookback_days = 30

    row = _make_mock_row('MNO111', 'BA', 6, 'cancelled', '{"no_ret": true}')
    mock_paths = _make_mock_paths('/path/auth', '/path/nopr', '/path/canc')
    mock_result = _make_mock_build_result(
        'cancellation_no_return',
        '<evento>content</evento>',
        '<evento>date_fragment</evento>',
    )

    with (
        patch(
            'sftp_file_transfer.components.nfce_generator.fetch_pending_invoice_rows',
            return_value=[row],
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.build_output_paths',
            return_value=mock_paths,
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.already_exists',
            return_value=False,
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.extract_invoice_content',
            side_effect=[_b64('req'), _b64('resp')],
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.classify_and_build',
            return_value=mock_result,
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.write_document',
        ) as mock_write,
        patch(
            'sftp_file_transfer.components.nfce_generator.extract_document_datetime',
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.apply_document_datetime',
        ),
    ):
        result = run_nfce_extraction(engine, output_dir, lookback_days)

        assert result.total == 1
        assert result.cancellations == 1
        assert result.generated == 0
        assert result.no_protocol == 0
        assert result.skipped == 0
        assert result.errors == 0
        mock_write.assert_called_once()
        call_args = mock_write.call_args
        assert call_args[0][0] == '/path/canc'


def test_extract_document_datetime_called_with_date_source_fragment(tmp_path):
    """Test that extract_document_datetime is called with
    date_source_fragment."""
    engine = Mock()
    output_dir = str(tmp_path)
    lookback_days = 30

    row = _make_mock_row('PQR222', 'PE', 7, 'approved', '{"data": "test"}')
    mock_paths = _make_mock_paths('/path/auth', '/path/nopr', '/path/canc')
    date_fragment = '<NFe><infNFe><dhEmi>2026-01-01T10:00:00</dhEmi>'
    mock_result = _make_mock_build_result(
        'authorized_with_protocol',
        '<nfeProc>content</nfeProc>',
        date_fragment,
    )

    with (
        patch(
            'sftp_file_transfer.components.nfce_generator.fetch_pending_invoice_rows',
            return_value=[row],
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.build_output_paths',
            return_value=mock_paths,
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.already_exists',
            return_value=False,
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.extract_invoice_content',
            side_effect=[_b64('req'), _b64('resp')],
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.classify_and_build',
            return_value=mock_result,
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.write_document',
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.extract_document_datetime',
        ) as mock_extract_dt,
        patch(
            'sftp_file_transfer.components.nfce_generator.apply_document_datetime',
        ),
    ):
        run_nfce_extraction(engine, output_dir, lookback_days)

        mock_extract_dt.assert_called_once_with(date_fragment)


def test_apply_document_datetime_called_with_extracted_datetime(tmp_path):
    """Test that apply_document_datetime is called with path and extracted
    datetime."""
    engine = Mock()
    output_dir = str(tmp_path)
    lookback_days = 30

    row = _make_mock_row('STU333', 'RJ', 8, 'approved', '{"data": "test"}')
    mock_paths = _make_mock_paths('/path/auth', '/path/nopr', '/path/canc')
    mock_result = _make_mock_build_result(
        'authorized_with_protocol',
        '<nfeProc>content</nfeProc>',
        '<NFe>date</NFe>',
    )
    mock_datetime = Mock()

    with (
        patch(
            'sftp_file_transfer.components.nfce_generator.fetch_pending_invoice_rows',
            return_value=[row],
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.build_output_paths',
            return_value=mock_paths,
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.already_exists',
            return_value=False,
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.extract_invoice_content',
            side_effect=[_b64('req'), _b64('resp')],
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.classify_and_build',
            return_value=mock_result,
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.write_document',
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.extract_document_datetime',
            return_value=mock_datetime,
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.apply_document_datetime',
        ) as mock_apply_dt,
    ):
        run_nfce_extraction(engine, output_dir, lookback_days)

        mock_apply_dt.assert_called_once_with('/path/auth', mock_datetime)


def test_skips_datetime_application_when_date_fragment_is_none(tmp_path):
    """Test that datetime application is skipped when date_source_fragment is
    None."""
    engine = Mock()
    output_dir = str(tmp_path)
    lookback_days = 30

    row = _make_mock_row('VWX444', 'SP', 9, 'approved', '{"data": "test"}')
    mock_paths = _make_mock_paths('/path/auth', '/path/nopr', '/path/canc')
    mock_result = _make_mock_build_result(
        'authorized_with_protocol',
        '<nfeProc>content</nfeProc>',
        None,
    )

    with (
        patch(
            'sftp_file_transfer.components.nfce_generator.fetch_pending_invoice_rows',
            return_value=[row],
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.build_output_paths',
            return_value=mock_paths,
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.already_exists',
            return_value=False,
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.extract_invoice_content',
            side_effect=[_b64('req'), _b64('resp')],
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.classify_and_build',
            return_value=mock_result,
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.write_document',
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.extract_document_datetime',
        ) as mock_extract_dt,
        patch(
            'sftp_file_transfer.components.nfce_generator.apply_document_datetime',
        ) as mock_apply_dt,
    ):
        run_nfce_extraction(engine, output_dir, lookback_days)

        mock_extract_dt.assert_not_called()
        mock_apply_dt.assert_not_called()


def test_skips_datetime_application_when_date_fragment_is_falsy(tmp_path):
    """Test that datetime application is skipped when date_source_fragment is
    falsy (empty string)."""
    engine = Mock()
    output_dir = str(tmp_path)
    lookback_days = 30

    row = _make_mock_row('YZA555', 'MG', 12, 'approved', '{"data": "test"}')
    mock_paths = _make_mock_paths('/path/auth', '/path/nopr', '/path/canc')
    mock_result = _make_mock_build_result(
        'authorized_with_protocol',
        '<nfeProc>content</nfeProc>',
        '',
    )

    with (
        patch(
            'sftp_file_transfer.components.nfce_generator.fetch_pending_invoice_rows',
            return_value=[row],
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.build_output_paths',
            return_value=mock_paths,
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.already_exists',
            return_value=False,
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.extract_invoice_content',
            side_effect=[_b64('req'), _b64('resp')],
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.classify_and_build',
            return_value=mock_result,
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.write_document',
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.extract_document_datetime',
        ) as mock_extract_dt,
        patch(
            'sftp_file_transfer.components.nfce_generator.apply_document_datetime',
        ) as mock_apply_dt,
    ):
        run_nfce_extraction(engine, output_dir, lookback_days)

        mock_extract_dt.assert_not_called()
        mock_apply_dt.assert_not_called()


def test_extract_invoice_content_called_with_request_and_response(tmp_path):
    """Test that extract_invoice_content is called for both Request and
    Response."""
    expected_call_count = 2
    engine = Mock()
    output_dir = str(tmp_path)
    lookback_days = 30

    row = _make_mock_row('ABC999', 'BA', 13, 'approved', '{"json": "exp"}')
    mock_paths = _make_mock_paths('/path/auth', '/path/nopr', '/path/canc')
    mock_result = _make_mock_build_result(
        'authorized_with_protocol',
        '<nfeProc>content</nfeProc>',
        None,
    )

    with (
        patch(
            'sftp_file_transfer.components.nfce_generator.fetch_pending_invoice_rows',
            return_value=[row],
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.build_output_paths',
            return_value=mock_paths,
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.already_exists',
            return_value=False,
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.extract_invoice_content',
            return_value=_b64('placeholder'),
        ) as mock_extract,
        patch(
            'sftp_file_transfer.components.nfce_generator.classify_and_build',
            return_value=mock_result,
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.write_document',
        ),
    ):
        run_nfce_extraction(engine, output_dir, lookback_days)

        assert mock_extract.call_count == expected_call_count
        call_args_list = mock_extract.call_args_list
        assert call_args_list[0] == (('{"json": "exp"}', 'Request'),)
        assert call_args_list[1] == (('{"json": "exp"}', 'Response'),)


def test_classify_and_build_called_with_extracted_xmls(tmp_path):
    """Test that classify_and_build is called with extracted XML strings."""
    engine = Mock()
    output_dir = str(tmp_path)
    lookback_days = 30

    row = _make_mock_row('DEF888', 'PE', 14, 'approved', '{"json": "data"}')
    mock_paths = _make_mock_paths('/path/auth', '/path/nopr', '/path/canc')
    mock_result = _make_mock_build_result(
        'authorized_with_protocol',
        '<nfeProc>content</nfeProc>',
        None,
    )

    request_xml = 'request_xml_content'
    response_xml = 'response_xml_content'

    with (
        patch(
            'sftp_file_transfer.components.nfce_generator.fetch_pending_invoice_rows',
            return_value=[row],
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.build_output_paths',
            return_value=mock_paths,
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.already_exists',
            return_value=False,
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.extract_invoice_content',
            side_effect=[_b64(request_xml), _b64(response_xml)],
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.classify_and_build',
        ) as mock_classify,
        patch(
            'sftp_file_transfer.components.nfce_generator.write_document',
        ),
    ):
        mock_classify.return_value = mock_result
        run_nfce_extraction(engine, output_dir, lookback_days)

        # classify_and_build must receive the DECODED xml text, not the
        # base64 that extract_invoice_content actually returns.
        mock_classify.assert_called_once_with(request_xml, response_xml)


def test_multiple_rows_with_mixed_outcomes(tmp_path):
    """Test handling of multiple rows with different outcomes."""
    expected_total = 4
    expected_generated = 2
    expected_cancellations = 1
    expected_skipped = 1
    engine = Mock()
    output_dir = str(tmp_path)
    lookback_days = 30

    rows = [
        _make_mock_row('ROW1', 'SP', 101, 'approved', '{"json1": "a"}'),
        _make_mock_row('ROW2', 'MG', 102, 'approved', '{"json2": "b"}'),
        _make_mock_row('ROW3', 'RJ', 103, 'cancelled', '{"json3": "c"}'),
        _make_mock_row('ROW4', 'BA', 104, 'approved', '{"json4": "d"}'),
    ]

    paths = [
        _make_mock_paths('/1/auth', '/1/nopr', '/1/canc'),
        _make_mock_paths('/2/auth', '/2/nopr', '/2/canc'),
        _make_mock_paths('/3/auth', '/3/nopr', '/3/canc'),
        _make_mock_paths('/4/auth', '/4/nopr', '/4/canc'),
    ]

    # row2 (paths[1]) is skipped, so only rows 1/3/4 reach classify_and_build
    classify_results = [
        _make_mock_build_result(
            'authorized_with_protocol',
            '<nfeProc>1</nfeProc>',
            None,
        ),
        _make_mock_build_result(
            'cancellation_with_return',
            '<procEventoNFe>3</procEventoNFe>',
            None,
        ),
        _make_mock_build_result(
            'authorized_with_protocol',
            '<nfeProc>4</nfeProc>',
            None,
        ),
    ]

    def side_effect_already_exists(candidate_paths):
        return candidate_paths == paths[1]

    with (
        patch(
            'sftp_file_transfer.components.nfce_generator.fetch_pending_invoice_rows',
            return_value=rows,
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.build_output_paths',
            side_effect=paths,
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.already_exists',
            side_effect=side_effect_already_exists,
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.extract_invoice_content',
            side_effect=[_b64('r')] * 6,
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.classify_and_build',
            side_effect=classify_results,
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.write_document',
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.extract_document_datetime',
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.apply_document_datetime',
        ),
    ):
        result = run_nfce_extraction(engine, output_dir, lookback_days)

        assert result.total == expected_total
        assert result.generated == expected_generated
        assert result.no_protocol == 0
        assert result.cancellations == expected_cancellations
        assert result.skipped == expected_skipped
        assert result.errors == 0


def test_unexpected_kind_value_is_handled_defensively_as_error(tmp_path):
    """Test that a kind value outside the five known constants (which
    should never happen in practice, since classify_and_build only ever
    returns one of them) is still handled defensively as an error
    instead of crashing or silently doing nothing."""
    engine = Mock()
    output_dir = str(tmp_path)
    lookback_days = 30

    row = _make_mock_row('WEIRD1', 'SP', 99, 'approved', '{"data": "x"}')
    mock_paths = _make_mock_paths('/path/auth', '/path/nopr', '/path/canc')
    mock_result = _make_mock_build_result('mystery_kind', '<x/>', None)

    with (
        patch(
            'sftp_file_transfer.components.nfce_generator.fetch_pending_invoice_rows',
            return_value=[row],
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.build_output_paths',
            return_value=mock_paths,
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.already_exists',
            return_value=False,
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.extract_invoice_content',
            side_effect=[_b64('req'), _b64('resp')],
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.classify_and_build',
            return_value=mock_result,
        ),
        patch(
            'sftp_file_transfer.components.nfce_generator.write_document',
        ) as mock_write,
    ):
        result = run_nfce_extraction(engine, output_dir, lookback_days)

        assert result.errors == 1
        assert result.generated == 0
        assert result.cancellations == 0
        assert result.no_protocol == 0
        assert result.skipped == 0
        mock_write.assert_not_called()
