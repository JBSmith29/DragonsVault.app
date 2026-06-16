"""Helpers for safely embedding dynamic data into HTML templates.

Two recurring sinks in the templates bypassed Jinja autoescaping:

* ``{{ some_json | safe }}`` where ``some_json`` was ``json.dumps(...)``. Raw
  ``json.dumps`` does not escape ``<``, ``>``, ``&`` or ``'``, so a value
  containing ``</script>`` (or a quote inside a single-quoted attribute) could
  break out of the surrounding ``<script>``/attribute. ``safe_json_dumps``
  produces output that is safe in both contexts (the same escaping Jinja's
  ``tojson`` filter applies).

* ``{{ value | replace('\\n', '<br>') | safe }}`` which inserted ``<br>`` for
  newlines but left the surrounding text unescaped. ``nl2br`` escapes the text
  first, then inserts real line breaks.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from markupsafe import Markup, escape

__all__ = ["safe_json_dumps", "nl2br"]

# Characters that are harmless inside a JSON string but dangerous when that JSON
# is embedded directly in HTML (a <script> body or an HTML attribute) or in an
# inline script (the U+2028 / U+2029 line terminators break JavaScript).
_JSON_HTML_ESCAPES = (
    ("<", "\\u003c"),
    (">", "\\u003e"),
    ("&", "\\u0026"),
    ("'", "\\u0027"),
    (" ", "\\u2028"),
    (" ", "\\u2029"),
)


def safe_json_dumps(obj: Any, **kwargs: Any) -> str:
    """``json.dumps`` whose result is safe to embed in HTML via ``| safe``.

    Accepts the same keyword arguments as ``json.dumps``. The escaped sequences
    remain valid JSON, so ``json.loads`` round-trips the output unchanged.
    """
    kwargs.setdefault("ensure_ascii", True)
    rendered = json.dumps(obj, **kwargs)
    for char, replacement in _JSON_HTML_ESCAPES:
        rendered = rendered.replace(char, replacement)
    return rendered


def nl2br(value: Optional[str]) -> Markup:
    """Escape ``value`` and convert newlines to ``<br>`` for safe rendering."""
    if value is None:
        return Markup("")
    escaped = str(escape(value))
    return Markup(escaped.replace("\n", "<br>"))
