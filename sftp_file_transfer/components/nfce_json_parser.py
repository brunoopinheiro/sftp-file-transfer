"""NFCe JSON parser module.

This module provides functionality to extract content from NFCe JSON
structures.
"""

import re


def extract_invoice_content(json_text: str, block: str) -> str | None:
    """Extract base64 content from Invoice JSON structure.

    Extracts the Content value from a specified block (Request or Response)
    within an Invoice JSON structure. Handles both plain and backslash-escaped
    quotes in the JSON.

    The search is scoped to the Invoice section up to the Payload marker for
    better accuracy.

    Args:
        json_text (str): The JSON text to search.
        block (str): The block name to extract from ('Request' or 'Response').

    Returns:
        str | None: The extracted base64 content string, empty string if
            Content is empty, or None if the block/Content is not found.
    """
    try:
        # Step 1: Try to scope to Invoice...Payload substring
        scoped_match = re.search(
            r'"Invoice"\s*:\s*\{.*?"Payload"',
            json_text,
            re.DOTALL,
        )

        if scoped_match:
            search_text = scoped_match.group(0)
        else:
            search_text = json_text

        # Step 2: Search for block/Content with optional escaped quotes
        pattern = (
            r'\\?"'
            + re.escape(block)
            + r'\\?"\s*:\s*\{.*?\\?"Content\\?"\s*:\s*\\?"'
            + r'([A-Za-z0-9+/=]*)\\?"'
        )

        match = re.search(pattern, search_text, re.DOTALL)

        # Step 3: Return captured group (may be empty string) or None
        if match:
            return match.group(1)
        return None
    except Exception:
        # Step 4: Never raise exceptions, always return None or str
        return None
