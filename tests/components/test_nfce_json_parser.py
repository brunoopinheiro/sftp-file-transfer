import string

from hypothesis import given
from hypothesis import strategies as st
from sftp_file_transfer.components.nfce_json_parser import (
    extract_invoice_content,
)

_BASE64_ALPHABET = string.ascii_letters + string.digits + '+/='


def _build_invoice_json(block, payload, escaped):
    """Build a minimal Invoice/<block>/Content JSON blob, optionally
    with backslash-escaped quotes."""
    if escaped:
        return (
            '{\\"Invoice\\":{\\"' + block + '\\":{\\"Content\\":\\"'
            + payload + '\\"}}}'
        )
    return '{"Invoice":{"' + block + '":{"Content":"' + payload + '"}}}'


def test_extracts_request_block_with_plain_quotes():
    """Test that extract_invoice_content returns base64 from Request block."""
    json_text = (
        '{"Invoice":{"Request":{"Content":"QUJD"},'
        '"Response":{"Content":"WFla"}},"Payload":{"Content":"other"}}'
    )
    result = extract_invoice_content(json_text, 'Request')
    assert result == 'QUJD'


def test_extracts_response_block_with_plain_quotes():
    """Test that extract_invoice_content returns base64 from Response block."""
    json_text = (
        '{"Invoice":{"Request":{"Content":"QUJD"},'
        '"Response":{"Content":"WFla"}},"Payload":{"Content":"other"}}'
    )
    result = extract_invoice_content(json_text, 'Response')
    assert result == 'WFla'


def test_extracts_with_backslash_escaped_quotes():
    """Test that extraction works with backslash-escaped quote characters."""
    json_text = (
        r'{\"Invoice\":{\"Request\":{\"Content\":\"QUJD\"},'
        r'\"Response\":{\"Content\":\"WFla\"}}}'
    )
    result = extract_invoice_content(json_text, 'Request')
    assert result == 'QUJD'


def test_returns_none_when_block_not_present():
    """Test that None is returned when the requested block is missing."""
    json_text = (
        '{"Invoice":{"Request":{"Content":"QUJD"}},'
        '"Payload":{"Content":"other"}}'
    )
    result = extract_invoice_content(json_text, 'Response')
    assert result is None


def test_returns_empty_string_when_content_is_empty():
    """Test that empty string (not None) is returned for empty Content."""
    json_text = '{"Invoice":{"Request":{"Content":""}}}'
    result = extract_invoice_content(json_text, 'Request')
    assert not result
    assert result is not None


def test_respects_invoice_payload_scoping_boundary():
    """Test that search is scoped to Invoice section up to Payload."""
    json_text = (
        '{"Invoice":{"Request":{"Content":"REAL"}},'
        '"Payload":{"Content":"WRONG"},'
        '"Extra":{"Response":{"Content":"SHOULDNOTMATCH"}}}'
    )
    result = extract_invoice_content(json_text, 'Response')
    assert result is None


@given(
    payload=st.text(alphabet=_BASE64_ALPHABET, max_size=40),
    block=st.sampled_from(['Request', 'Response']),
    escaped=st.booleans(),
)
def test_extract_invoice_content_round_trips_arbitrary_base64_payload(
    payload, block, escaped,
):
    """Test that any base64-alphabet payload placed in the target block
    is extracted unchanged, regardless of escaping style."""
    json_text = _build_invoice_json(block, payload, escaped)

    result = extract_invoice_content(json_text, block)

    assert result == payload


@given(
    text=st.text(max_size=200),
    block=st.sampled_from(['Request', 'Response']),
)
def test_extract_invoice_content_never_raises_on_arbitrary_text(
    text, block,
):
    """Test that extract_invoice_content never raises on arbitrary
    (possibly malformed) input text."""
    result = extract_invoice_content(text, block)

    assert result is None or isinstance(result, str)
