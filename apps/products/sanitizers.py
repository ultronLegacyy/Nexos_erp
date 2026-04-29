"""
XSS Sanitization utilities for text fields.

Uses the `bleach` library to strip all HTML tags and dangerous attributes
from user-supplied text, preventing stored XSS attacks.

Usage:
    from apps.products.sanitizers import sanitize_text
    clean_name = sanitize_text(raw_name)
"""
import bleach


# No HTML tags are allowed in name/description fields
ALLOWED_TAGS: list[str] = []
ALLOWED_ATTRIBUTES: dict[str, list[str]] = {}


def sanitize_text(value: str) -> str:
    """
    Strip ALL HTML tags and attributes from the input string.

    This prevents stored XSS by ensuring that no executable HTML or
    JavaScript can be persisted in the database.

    Examples:
        >>> sanitize_text('<script>alert("xss")</script>Hello')
        'alert("xss")Hello'
        >>> sanitize_text('<b>Bold</b> text')
        'Bold text'
        >>> sanitize_text('Normal text')
        'Normal text'
    """
    if not value:
        return value
    cleaned = bleach.clean(
        value,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        strip=True,
    )
    return cleaned.strip()
