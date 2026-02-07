"""Magic Comprehensive Rules helpers for lookup and search."""

from __future__ import annotations

import re
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional


_RULES_TEXT: Optional[str] = None
_RULES_LINES: Optional[List[str]] = None
_RULES_META: Optional[Dict[str, Any]] = None
_RULES_WORKBOOK: Optional[List[Dict[str, Any]]] = None

_RULES_FILENAME = "MagicCompRules-2026-01-16.txt"
_RULES_PATH = Path(__file__).resolve().parents[3] / "static" / "docs" / _RULES_FILENAME
_RULES_SOURCE_URL = "https://media.wizards.com/2026/downloads/MagicCompRules%2020260116.txt"
_RULES_MIN_BYTES = 200_000
_EFFECTIVE_RE = re.compile(r"effective as of ([A-Za-z]+\\s+\\d{1,2},\\s+\\d{4})", re.IGNORECASE)


def _load_rules_text() -> str:
    global _RULES_TEXT, _RULES_LINES
    if _RULES_TEXT is None:
        if not _RULES_PATH.exists() or _RULES_PATH.stat().st_size < _RULES_MIN_BYTES:
            downloaded = _download_rules_text()
            if downloaded:
                _RULES_TEXT = downloaded
                _RULES_LINES = [line.strip() for line in downloaded.splitlines()]
                return _RULES_TEXT
        if not _RULES_PATH.exists():
            _RULES_TEXT = ""
            _RULES_LINES = []
            return _RULES_TEXT
        raw = _RULES_PATH.read_text(encoding="utf-8", errors="ignore")
        raw = raw.lstrip("\ufeff")
        _RULES_TEXT = raw
        _RULES_LINES = [line.strip() for line in raw.splitlines()]
    return _RULES_TEXT or ""


def _download_rules_text() -> str:
    try:
        request = urllib.request.Request(
            _RULES_SOURCE_URL,
            headers={"User-Agent": "DragonsVault.app rules fetcher"},
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read()
        text = raw.decode("utf-8", errors="ignore").lstrip("\ufeff")
        if text:
            _RULES_PATH.parent.mkdir(parents=True, exist_ok=True)
            _RULES_PATH.write_text(text, encoding="utf-8")
        return text
    except Exception:
        return ""


def _load_rules_lines() -> List[str]:
    if _RULES_LINES is None:
        _load_rules_text()
    return _RULES_LINES or []


def magic_rules_metadata() -> Dict[str, Any]:
    global _RULES_META
    if _RULES_META is None:
        text = _load_rules_text()
        effective = None
        match = _EFFECTIVE_RE.search(text[:4000]) if text else None
        if match:
            effective = match.group(1)
        _RULES_META = {
            "filename": _RULES_FILENAME,
            "effective_date": effective,
            "line_count": len(_RULES_LINES or []),
            "source_url": _RULES_SOURCE_URL,
        }
    return dict(_RULES_META or {})


def magic_rules_text() -> str:
    """Return the raw comprehensive rules text."""
    return _load_rules_text()


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")
    return (slug[:64] or "section")


def magic_rules_workbook() -> List[Dict[str, Any]]:
    global _RULES_WORKBOOK
    if _RULES_WORKBOOK is not None:
        return list(_RULES_WORKBOOK)

    text = _load_rules_text()
    if not text:
        _RULES_WORKBOOK = []
        return []

    lines = re.split(r"\r\n|\n|\r", text.lstrip("\ufeff"))
    chapters: List[Dict[str, Any]] = []
    current_chapter: Optional[Dict[str, Any]] = None
    current_sub: Optional[Dict[str, Any]] = None
    intro_bucket: Optional[Dict[str, Any]] = None
    in_contents = False
    contents_ended = False
    last_rule: Optional[Dict[str, Any]] = None
    in_glossary = False
    in_credits = False
    glossary_current: Optional[Dict[str, Any]] = None

    chapter_re = re.compile(r"^(\d+)\.\s+(.+)")
    sub_re = re.compile(r"^(\d{3})\.\s+(.+)")
    rule_re = re.compile(r"^(\d{3}\.\d+[a-z]?)\b")
    glossary_term_re = re.compile(r'^[A-Za-z0-9"()\[\]/:+,&\-\s]+$')
    glossary_numbered_re = re.compile(r"^\d+\.")

    def ensure_chapter(title: str) -> Dict[str, Any]:
        nonlocal current_chapter, current_sub
        for chapter in chapters:
            if chapter["title"] == title:
                current_chapter = chapter
                current_sub = None
                return chapter
        chapter = {"title": title, "id": f"chapter-{_slugify(title)}", "sections": []}
        chapters.append(chapter)
        current_chapter = chapter
        current_sub = None
        return chapter

    def ensure_sub(title: str) -> Dict[str, Any]:
        nonlocal current_chapter, current_sub
        if not current_chapter:
            current_chapter = ensure_chapter("Miscellaneous")
        for section in current_chapter["sections"]:
            if section["title"] == title:
                current_sub = section
                return section
        section = {
            "title": title,
            "id": f"section-{_slugify((current_chapter['title'] or '') + '-' + title)}",
            "rules": [],
            "notes": [],
        }
        current_chapter["sections"].append(section)
        current_sub = section
        return section

    for raw in lines:
        line = (raw or "").strip()
        if not line:
            continue
        if line == "Contents":
            in_contents = True
            contents_ended = False
            continue
        if in_contents:
            if line == "Credits":
                contents_ended = True
            if contents_ended and chapter_re.match(line):
                in_contents = False
            else:
                continue

        if line == "Introduction":
            intro_bucket = ensure_chapter("Introduction")
            ensure_sub("Overview")
            last_rule = None
            in_glossary = False
            in_credits = False
            glossary_current = None
            continue
        if line == "Glossary":
            ensure_chapter("Glossary")
            ensure_sub("Glossary")
            last_rule = None
            in_glossary = True
            in_credits = False
            glossary_current = None
            continue
        if line == "Credits":
            ensure_chapter("Credits")
            ensure_sub("Credits")
            last_rule = None
            in_glossary = False
            in_credits = True
            glossary_current = None
            continue

        if in_glossary:
            is_term = (
                not glossary_numbered_re.match(line)
                and not line.endswith(".")
                and glossary_term_re.match(line)
            )
            if is_term:
                existing = next(
                    (rule for rule in (current_sub or {}).get("rules", []) if rule["number"] == line),
                    None,
                )
                if existing:
                    glossary_current = existing
                else:
                    glossary_current = {
                        "id": f"rule-glossary-{_slugify(line)}",
                        "number": line,
                        "text": line,
                        "notes": [],
                        "kind": "glossary",
                    }
                    if current_sub is not None:
                        current_sub["rules"].append(glossary_current)
            else:
                if not glossary_current:
                    glossary_current = {
                        "id": f"rule-glossary-{_slugify('entry')}-{len((current_sub or {}).get('rules', [])) + 1}",
                        "number": "Glossary Entry",
                        "text": "Glossary Entry",
                        "notes": [],
                        "kind": "glossary",
                    }
                    if current_sub is not None:
                        current_sub["rules"].append(glossary_current)
                if glossary_current:
                    last_note = glossary_current["notes"][-1] if glossary_current["notes"] else None
                    if line != last_note:
                        glossary_current["notes"].append(line)
            continue

        if in_credits:
            if current_sub is not None:
                last_note = current_sub["notes"][-1] if current_sub["notes"] else None
                if line != last_note:
                    current_sub["notes"].append(line)
            continue

        match = sub_re.match(line)
        if match:
            ensure_sub(f"{match.group(1)}. {match.group(2)}")
            last_rule = None
            continue
        match = chapter_re.match(line)
        if match:
            ensure_chapter(f"{match.group(1)}. {match.group(2)}")
            last_rule = None
            continue
        match = rule_re.match(line)
        if match:
            if not current_sub:
                if not current_chapter:
                    current_chapter = ensure_chapter("Rules")
                current_sub = ensure_sub("General")
            existing_rule = next(
                (rule for rule in current_sub["rules"] if rule["number"] == match.group(1)),
                None,
            )
            if existing_rule:
                last_rule = existing_rule
                continue
            rule = {
                "id": f"rule-{match.group(1).replace('.', '-')}",
                "number": match.group(1),
                "text": line,
                "notes": [],
            }
            current_sub["rules"].append(rule)
            last_rule = rule
            continue

        if not current_sub:
            if not current_chapter:
                current_chapter = intro_bucket or ensure_chapter("Introduction")
            current_sub = ensure_sub("Overview")

        if current_chapter and line == current_chapter["title"]:
            continue
        if current_sub and line == current_sub["title"]:
            continue

        if current_sub.get("rules"):
            if not last_rule:
                last_rule = current_sub["rules"][-1]
            if last_rule:
                last_note = last_rule["notes"][-1] if last_rule["notes"] else None
                if line != last_note:
                    last_rule["notes"].append(line)
                continue

        last_section_note = current_sub["notes"][-1] if current_sub.get("notes") else None
        if line != last_section_note:
            current_sub["notes"].append(line)

    _RULES_WORKBOOK = chapters
    for chapter in _RULES_WORKBOOK:
        total = 0
        for section in chapter.get("sections", []):
            section["rule_count"] = len(section.get("rules") or [])
            total += section["rule_count"]
        chapter["rule_count"] = total
        chapter["section_count"] = len(chapter.get("sections") or [])
    return list(_RULES_WORKBOOK or [])


def search_magic_rules(query: str, *, limit: int = 20) -> List[Dict[str, Any]]:
    if not query:
        return []
    needle = query.strip().lower()
    if not needle:
        return []
    lines = _load_rules_lines()
    matches: List[Dict[str, Any]] = []
    for idx, line in enumerate(lines, start=1):
        if needle in line.lower():
            matches.append({"line": idx, "text": line})
            if len(matches) >= limit:
                break
    return matches


def lookup_magic_rule(rule_number: str) -> Optional[str]:
    if not rule_number:
        return None
    token = str(rule_number).strip()
    if not token:
        return None
    pattern = re.compile(rf"\\b{re.escape(token)}\\b")
    for line in _load_rules_lines():
        if pattern.search(line):
            return line
    return None
