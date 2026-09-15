import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class NfceDocument:
    """Represents a classified and built NFce document.

    Attributes:
        kind: Type of document ('authorized_with_protocol',
               'authorized_no_protocol', 'cancellation_with_return',
               'cancellation_no_return', or 'unrecognized').
        content: The assembled XML content (or None for unrecognized).
        date_source_fragment: The exact XML fragment to extract date from
                             (or None for unrecognized).
    """

    kind: str
    content: Optional[str] = None
    date_source_fragment: Optional[str] = None


def classify_and_build(request_xml: str, response_xml: str) -> NfceDocument:
    """Classify and build an NFce document from request and response XML.

    Extracts digitally signed XML fragments using regex and assembles output
    via string concatenation only—never parses/re-serializes, which would
    break digital signatures.

    Args:
        request_xml: XML with the sent request (search for NFe or evento).
        response_xml: XML with the response (search for protNFe or retEvento).

    Returns:
        NfceDocument with kind, content (or None), and date_source_fragment
        (or None). Never raises; returns 'unrecognized' for unmatched input.

    Document kinds:
        - 'authorized_with_protocol': NFe + protNFe found in response.
        - 'authorized_no_protocol': NFe found, no protNFe.
        - 'cancellation_with_return': evento + retEvento in response.
        - 'cancellation_no_return': evento found, no retEvento.
        - 'unrecognized': neither NFe nor evento found in request.

    Examples:
        Classify an authorized NFe without protocol (no protNFe response):

        >>> request = '<NFe><infNFe Id="NFe123"></infNFe></NFe>'
        >>> response = ''
        >>> doc = classify_and_build(request, response)
        >>> doc.kind
        'authorized_no_protocol'
    """
    # Try to find NFe fragment first (takes precedence if both present).
    nfe_match = re.search(r'<NFe\b.*?</NFe>', request_xml, re.DOTALL)

    if nfe_match:
        nfe_fragment = nfe_match.group()
        protnfe_match = re.search(
            r'<protNFe\b.*?</protNFe>', response_xml, re.DOTALL
        )

        if protnfe_match:
            protnfe_fragment = protnfe_match.group()
            content = (
                '<nfeProc versao="4.00" xmlns="http://www.portalfiscal.inf.br/nfe">'
                + nfe_fragment
                + protnfe_fragment
                + '</nfeProc>'
            )
            return NfceDocument(
                kind='authorized_with_protocol',
                content=content,
                date_source_fragment=nfe_fragment,
            )
        else:
            return NfceDocument(
                kind='authorized_no_protocol',
                content=nfe_fragment,
                date_source_fragment=nfe_fragment,
            )

    # Try to find evento fragment.
    evento_match = re.search(r'<evento\b.*?</evento>', request_xml, re.DOTALL)

    if evento_match:
        evento_fragment = evento_match.group()
        retevento_match = re.search(
            r'<retEvento\b.*?</retEvento>', response_xml, re.DOTALL
        )

        if retevento_match:
            retevento_fragment = retevento_match.group()
            content = (
                '<procEventoNFe versao="4.00" xmlns="http://www.portalfiscal.inf.br/nfe">'
                + evento_fragment
                + retevento_fragment
                + '</procEventoNFe>'
            )
            return NfceDocument(
                kind='cancellation_with_return',
                content=content,
                date_source_fragment=evento_fragment,
            )
        else:
            return NfceDocument(
                kind='cancellation_no_return',
                content=evento_fragment,
                date_source_fragment=evento_fragment,
            )

    # Neither NFe nor evento found.
    return NfceDocument(
        kind='unrecognized',
        content=None,
        date_source_fragment=None,
    )
