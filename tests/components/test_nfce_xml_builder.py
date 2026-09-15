import string

from hypothesis import given
from hypothesis import strategies as st

from sftp_file_transfer.components.nfce_xml_builder import (
    classify_and_build,
)

_BODY_ALPHABET = string.ascii_letters + string.digits + ' ._-'
_VALID_KINDS = {
    'authorized_with_protocol',
    'authorized_no_protocol',
    'cancellation_with_return',
    'cancellation_no_return',
    'unrecognized',
}


def test_authorized_doc_with_protocol_assembles_nfeproc_wrapper():
    """Test that authorized NFe with protNFe creates wrapped nfeProc."""
    nfe_fragment = (
        '<NFe Id="ID123">\n'
        '  <infNFe Id="info"><dhEmi>2026-01-01T10:00:00</dhEmi>'
        '</infNFe>\n'
        '</NFe>'
    )
    protnfe_fragment = (
        '<protNFe>\n  <infProt><nProt>123456789</nProt></infProt>\n</protNFe>'
    )
    request_xml = f'<env>{nfe_fragment}</env>'
    response_xml = f'<env>{protnfe_fragment}</env>'

    result = classify_and_build(request_xml, response_xml)

    assert result.kind == 'authorized_with_protocol'
    assert result.content.startswith(
        '<nfeProc versao="4.00" xmlns="http://www.portalfiscal.inf.br/nfe">'
    )
    assert nfe_fragment in result.content
    assert protnfe_fragment in result.content
    assert result.content.endswith('</nfeProc>')


def test_authorized_doc_without_protocol_returns_nfe_fragment_alone():
    """Test that NFe without protNFe returns fragment unmodified."""
    nfe_fragment = (
        '<NFe Id="ID456">\n'
        '  <infNFe><dhEmi>2026-01-02T14:30:00</dhEmi></infNFe>\n'
        '</NFe>'
    )
    request_xml = f'<envelope>{nfe_fragment}</envelope>'
    response_xml = '<envelope></envelope>'

    result = classify_and_build(request_xml, response_xml)

    assert result.kind == 'authorized_no_protocol'
    assert result.content == nfe_fragment


def test_cancellation_with_return_assembles_proceventonfe_wrapper():
    """Test that evento with retEvento creates wrapped procEventoNFe."""
    evento_fragment = (
        '<evento Id="evt001">\n'
        '  <infEvento><cOrgao>35</cOrgao></infEvento>\n'
        '</evento>'
    )
    retevento_fragment = (
        '<retEvento Id="retevt001">\n'
        '  <infEvento><cStat>135</cStat></infEvento>\n'
        '</retEvento>'
    )
    request_xml = f'<root>{evento_fragment}</root>'
    response_xml = f'<root>{retevento_fragment}</root>'

    result = classify_and_build(request_xml, response_xml)

    assert result.kind == 'cancellation_with_return'
    assert result.content.startswith(
        '<procEventoNFe versao="4.00" xmlns="http://www.portalfiscal.inf.br/nfe">'
    )
    assert evento_fragment in result.content
    assert retevento_fragment in result.content
    assert result.content.endswith('</procEventoNFe>')


def test_cancellation_without_return_returns_evento_fragment_alone():
    """Test that evento without retEvento returns fragment unmodified."""
    evento_fragment = (
        '<evento Id="evt002">\n'
        '  <infEvento><tpEvento>110140</tpEvento></infEvento>\n'
        '</evento>'
    )
    request_xml = f'<root>{evento_fragment}</root>'
    response_xml = '<root></root>'

    result = classify_and_build(request_xml, response_xml)

    assert result.kind == 'cancellation_no_return'
    assert result.content == evento_fragment


def test_unrecognized_returns_none_for_content_and_kind():
    """Test that unrecognized XML returns kind=unrecognized."""
    request_xml = '<unknown><data>no nfe or evento</data></unknown>'
    response_xml = '<response>empty</response>'

    result = classify_and_build(request_xml, response_xml)

    assert result.kind == 'unrecognized'
    assert result.content is None


def test_fragment_fidelity_preserves_exact_nfe_substring():
    """Test that NFe fragment appears exactly unchanged in output."""
    nfe_fragment = (
        '<NFe   Id="special_spacing" >\n\t<infNFe>content</infNFe>\n</NFe>'
    )
    protnfe_fragment = '<protNFe><infProt><nProt>1</nProt></infProt></protNFe>'
    request_xml = f'<env>{nfe_fragment}</env>'
    response_xml = f'<env>{protnfe_fragment}</env>'

    result = classify_and_build(request_xml, response_xml)

    assert nfe_fragment in result.content
    assert result.content.count(nfe_fragment) == 1


def test_fragment_fidelity_preserves_exact_evento_substring():
    """Test that evento fragment appears exactly unchanged in output."""
    evento_fragment = (
        '<evento   Id="evt_special"  >\n\t'
        '<infEvento>data</infEvento>\n'
        '</evento>'
    )
    retevento_fragment = (
        '<retEvento><infEvento><cStat>1</cStat></infEvento></retEvento>'
    )
    request_xml = f'<root>{evento_fragment}</root>'
    response_xml = f'<root>{retevento_fragment}</root>'

    result = classify_and_build(request_xml, response_xml)

    assert evento_fragment in result.content
    assert result.content.count(evento_fragment) == 1


def test_date_source_fragment_equals_nfe_for_authorized_with_protocol():
    """Test that date_source_fragment points to the NFe fragment."""
    nfe_fragment = (
        '<NFe Id="xyz">\n'
        '  <infNFe><dhEmi>2026-01-01T10:00:00</dhEmi></infNFe>\n'
        '</NFe>'
    )
    protnfe_fragment = '<protNFe><infProt><nProt>1</nProt></infProt></protNFe>'
    request_xml = f'<env>{nfe_fragment}</env>'
    response_xml = f'<env>{protnfe_fragment}</env>'

    result = classify_and_build(request_xml, response_xml)

    assert result.date_source_fragment == nfe_fragment


def test_date_source_fragment_equals_nfe_for_authorized_without_protocol():
    """Test that date_source_fragment points to the NFe fragment."""
    nfe_fragment = (
        '<NFe Id="abc">\n'
        '  <infNFe><dhEmi>2026-01-15T11:30:00</dhEmi></infNFe>\n'
        '</NFe>'
    )
    request_xml = f'<env>{nfe_fragment}</env>'
    response_xml = '<env></env>'

    result = classify_and_build(request_xml, response_xml)

    assert result.date_source_fragment == nfe_fragment


def test_date_source_fragment_equals_evento_for_cancellation_with_return():
    """Test that date_source_fragment points to the evento fragment."""
    evento_fragment = (
        '<evento Id="evt_date">\n'
        '  <infEvento><dhEvento>2026-02-01T09:00:00</dhEvento></infEvento>\n'
        '</evento>'
    )
    retevento_fragment = (
        '<retEvento><infEvento><cStat>1</cStat></infEvento></retEvento>'
    )
    request_xml = f'<root>{evento_fragment}</root>'
    response_xml = f'<root>{retevento_fragment}</root>'

    result = classify_and_build(request_xml, response_xml)

    assert result.date_source_fragment == evento_fragment


def test_date_source_fragment_equals_evento_for_cancellation_without_return():
    """Test that date_source_fragment points to the evento fragment."""
    evento_fragment = (
        '<evento Id="evt_only">\n'
        '  <infEvento><dhEvento>2026-02-15T15:45:00</dhEvento></infEvento>\n'
        '</evento>'
    )
    request_xml = f'<root>{evento_fragment}</root>'
    response_xml = '<root></root>'

    result = classify_and_build(request_xml, response_xml)

    assert result.date_source_fragment == evento_fragment


def test_unrecognized_returns_none_for_date_source_fragment():
    """Test that unrecognized docs return None for date_source_fragment."""
    request_xml = '<mystery><tag>data</tag></mystery>'
    response_xml = '<response></response>'

    result = classify_and_build(request_xml, response_xml)

    assert result.date_source_fragment is None


def test_multiline_nfe_fragment_detected_across_lines():
    """Test that NFe spanning multiple lines is correctly detected."""
    nfe_fragment = (
        '<NFe Id="multiline">\n'
        '  <infNFe>\n'
        '    <ide><dhEmi>2026-01-01T00:00:00</dhEmi></ide>\n'
        '  </infNFe>\n'
        '</NFe>'
    )
    protnfe_fragment = '<protNFe><nProt>1</nProt></protNFe>'
    request_xml = f'<?xml version="1.0"?>\n{nfe_fragment}'
    response_xml = f'<?xml version="1.0"?>\n{protnfe_fragment}'

    result = classify_and_build(request_xml, response_xml)

    assert result.kind == 'authorized_with_protocol'
    assert nfe_fragment in result.content


def test_multiline_evento_fragment_detected_across_lines():
    """Test that evento spanning multiple lines is correctly detected."""
    evento_fragment = (
        '<evento Id="evt_multi">\n'
        '  <infEvento>\n'
        '    <dhEvento>2026-02-01T00:00:00</dhEvento>\n'
        '  </infEvento>\n'
        '</evento>'
    )
    retevento_fragment = '<retEvento><cStat>135</cStat></retEvento>'
    request_xml = f'<?xml version="1.0"?>\n{evento_fragment}'
    response_xml = f'<?xml version="1.0"?>\n{retevento_fragment}'

    result = classify_and_build(request_xml, response_xml)

    assert result.kind == 'cancellation_with_return'
    assert evento_fragment in result.content


def test_protif_in_response_without_nfe_in_request_ignores_response():
    """Test that protNFe in response is only used if NFe in request."""
    protnfe_fragment = '<protNFe><nProt>1</nProt></protNFe>'
    request_xml = '<root><other></other></root>'
    response_xml = f'<env>{protnfe_fragment}</env>'

    result = classify_and_build(request_xml, response_xml)

    assert result.kind == 'unrecognized'
    assert result.content is None


def test_retvento_in_response_without_evento_in_request_ignores_response():
    """Test that retEvento in response is only used if evento in request."""
    retevento_fragment = '<retEvento><cStat>1</cStat></retEvento>'
    request_xml = '<root><other></other></root>'
    response_xml = f'<env>{retevento_fragment}</env>'

    result = classify_and_build(request_xml, response_xml)

    assert result.kind == 'unrecognized'
    assert result.content is None


def test_only_nfe_detected_not_arbitrary_nfetag_substring():
    """Test that only well-formed <NFe> tags are recognized."""
    request_xml = '<root>MyNFe is great but no actual tag</root>'
    response_xml = '<root></root>'

    result = classify_and_build(request_xml, response_xml)

    assert result.kind == 'unrecognized'


def test_only_evento_detected_not_arbitrary_eventotag_substring():
    """Test that only well-formed <evento> tags are recognized."""
    request_xml = '<root>evento data here but no actual tag</root>'
    response_xml = '<root></root>'

    result = classify_and_build(request_xml, response_xml)

    assert result.kind == 'unrecognized'


def test_nfe_takes_precedence_if_both_nfe_and_evento_present():
    """Test that NFe is checked before evento."""
    nfe_fragment = '<NFe Id="x"><infNFe></infNFe></NFe>'
    evento_fragment = '<evento Id="y"><infEvento></infEvento></evento>'
    protnfe_fragment = '<protNFe><nProt>1</nProt></protNFe>'
    request_xml = f'{nfe_fragment}{evento_fragment}'
    response_xml = f'{protnfe_fragment}'

    result = classify_and_build(request_xml, response_xml)

    assert result.kind == 'authorized_with_protocol'
    assert nfe_fragment in result.content


def test_content_contains_both_fragments_in_order():
    """Test that wrapped content has NFe before protNFe."""
    nfe_fragment = '<NFe Id="1"></NFe>'
    protnfe_fragment = '<protNFe></protNFe>'
    request_xml = f'<env>{nfe_fragment}</env>'
    response_xml = f'<env>{protnfe_fragment}</env>'

    result = classify_and_build(request_xml, response_xml)

    nfe_pos = result.content.find(nfe_fragment)
    protnfe_pos = result.content.find(protnfe_fragment)
    assert nfe_pos < protnfe_pos
    assert nfe_pos >= 0
    assert protnfe_pos >= 0


def test_evento_content_contains_both_fragments_in_order():
    """Test that wrapped evento content has evento before retEvento."""
    evento_fragment = '<evento Id="1"></evento>'
    retevento_fragment = '<retEvento></retEvento>'
    request_xml = f'<root>{evento_fragment}</root>'
    response_xml = f'<root>{retevento_fragment}</root>'

    result = classify_and_build(request_xml, response_xml)

    evento_pos = result.content.find(evento_fragment)
    retevento_pos = result.content.find(retevento_fragment)
    assert evento_pos < retevento_pos
    assert evento_pos >= 0
    assert retevento_pos >= 0


def test_empty_request_returns_unrecognized():
    """Test that empty request_xml returns unrecognized."""
    request_xml = ''
    response_xml = '<response></response>'

    result = classify_and_build(request_xml, response_xml)

    assert result.kind == 'unrecognized'
    assert result.content is None


def test_nfe_with_multiple_attributes_parsed_correctly():
    """Test NFe with various attribute formats is detected."""
    nfe_fragment = (
        '<NFe Id="abc123" xmlns="http://test" '
        'versao="4.00">\n'
        '<infNFe></infNFe>\n'
        '</NFe>'
    )
    request_xml = f'<root>{nfe_fragment}</root>'
    response_xml = '<root></root>'

    result = classify_and_build(request_xml, response_xml)

    assert result.kind == 'authorized_no_protocol'
    assert result.content == nfe_fragment


@given(body=st.text(alphabet=_BODY_ALPHABET, max_size=40))
def test_nfe_fragment_fidelity_for_arbitrary_body_content(body):
    """Test that arbitrary inner NFe body content is preserved
    byte-for-byte in the assembled nfeProc output, for any body that
    doesn't itself contain a closing tag."""
    nfe_fragment = f'<NFe Id="x">{body}</NFe>'
    protnfe_fragment = '<protNFe><infProt><nProt>1</nProt></infProt></protNFe>'
    request_xml = f'<env>{nfe_fragment}</env>'
    response_xml = f'<env>{protnfe_fragment}</env>'

    result = classify_and_build(request_xml, response_xml)

    assert result.kind == 'authorized_with_protocol'
    assert nfe_fragment in result.content


@given(body=st.text(alphabet=_BODY_ALPHABET, max_size=40))
def test_evento_fragment_fidelity_for_arbitrary_body_content(body):
    """Test that arbitrary inner evento body content is preserved
    byte-for-byte in the assembled procEventoNFe output."""
    evento_fragment = f'<evento Id="y">{body}</evento>'
    retevento_fragment = (
        '<retEvento><infEvento><cStat>1</cStat></infEvento></retEvento>'
    )
    request_xml = f'<root>{evento_fragment}</root>'
    response_xml = f'<root>{retevento_fragment}</root>'

    result = classify_and_build(request_xml, response_xml)

    assert result.kind == 'cancellation_with_return'
    assert evento_fragment in result.content


@given(
    request_xml=st.text(max_size=200),
    response_xml=st.text(max_size=200),
)
def test_classify_and_build_never_raises_on_arbitrary_text(
    request_xml,
    response_xml,
):
    """Test that classify_and_build never raises and always returns one
    of the known kinds, for arbitrary (possibly malformed) input."""
    result = classify_and_build(request_xml, response_xml)

    assert result.kind in _VALID_KINDS
