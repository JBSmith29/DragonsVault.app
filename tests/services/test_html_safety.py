"""Tests for HTML-embedding safety helpers."""

import json

from shared.html_safety import nl2br, safe_json_dumps

LS = chr(0x2028)  # JavaScript line separator
PS = chr(0x2029)  # JavaScript paragraph separator


def test_safe_json_dumps_escapes_html_breakouts():
    payload = {"name": "</script><img src=x onerror=alert(1)>", "q": "o'brien"}
    out = safe_json_dumps(payload)
    # Must not contain characters that could break out of <script> or attributes.
    assert "<" not in out and ">" not in out
    assert "</script>" not in out
    assert "'" not in out  # single quotes escaped for single-quoted attributes
    # Still valid JSON that round-trips to the original object.
    assert json.loads(out) == payload


def test_safe_json_dumps_escapes_js_line_terminators():
    value = "a" + LS + "b" + PS + "c"
    out = safe_json_dumps({"x": value})
    assert LS not in out and PS not in out
    assert json.loads(out) == {"x": value}


def test_nl2br_escapes_then_breaks_lines():
    out = str(nl2br("line1\n<script>x</script>"))
    assert out == "line1<br>&lt;script&gt;x&lt;/script&gt;"


def test_nl2br_handles_none():
    assert str(nl2br(None)) == ""
