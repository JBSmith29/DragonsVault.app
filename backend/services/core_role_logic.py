from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import json
from pathlib import Path
import re
from typing import Iterable, Pattern, Tuple, Set


CORE_ROLE_RULES_PATH = Path(__file__).resolve().parents[1] / "core-role" / "core-role-logic.json"
_REMINDER_TEXT_RE = re.compile(r"\([^()]*\)")
_WHITESPACE_RE = re.compile(r"\s+")
_SIMPLE_TOKEN_RE = re.compile(r"^[a-z0-9]+$")


@dataclass(frozen=True)
class CoreRoleRule:
    role: str
    requires: Tuple[Pattern[str], ...]
    optional: Tuple[Pattern[str], ...]
    excludes: Tuple[Pattern[str], ...]


def _load_json(path: Path) -> dict:
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _strip_reminder_text(text: str) -> str:
    out = text
    while True:
        cleaned = _REMINDER_TEXT_RE.sub(" ", out)
        if cleaned == out:
            return cleaned
        out = cleaned


def _normalize_text(text: str, normalization: dict) -> str:
    if not text:
        return ""
    out = str(text)
    if normalization.get("strip_reminder_text"):
        out = _strip_reminder_text(out)
    lowercase = normalization.get("lowercase", True)
    if lowercase:
        out = out.lower()
    replacements = normalization.get("symbol_replacements") or {}
    for symbol, replacement in replacements.items():
        if not symbol:
            continue
        key = symbol.lower() if lowercase else symbol
        out = out.replace(key, f" {replacement} ")
    out = _WHITESPACE_RE.sub(" ", out).strip()
    return out


def _compile_token(token: str) -> Pattern[str] | None:
    token = (token or "").strip().lower()
    if not token:
        return None
    if _SIMPLE_TOKEN_RE.match(token):
        return re.compile(r"\b" + re.escape(token) + r"\b")
    return re.compile(re.escape(token))


@lru_cache(maxsize=1)
def _load_core_role_rules() -> tuple[dict, tuple[CoreRoleRule, ...]]:
    data = _load_json(CORE_ROLE_RULES_PATH)
    normalization = data.get("normalization") or {}
    rules = []
    for entry in data.get("roles") or []:
        if not isinstance(entry, dict):
            continue
        role = (entry.get("role") or "").strip().lower()
        if not role:
            continue
        requires = tuple(
            token for token in (_compile_token(v) for v in (entry.get("requires") or [])) if token
        )
        optional = tuple(
            token for token in (_compile_token(v) for v in (entry.get("optional") or [])) if token
        )
        excludes = tuple(
            token for token in (_compile_token(v) for v in (entry.get("excludes") or [])) if token
        )
        rules.append(CoreRoleRule(role=role, requires=requires, optional=optional, excludes=excludes))
    return normalization, tuple(rules)


def core_role_label(role: str) -> str:
    return (role or "").replace("_", " ").replace("-", " ").title().strip()


def derive_core_roles(
    *,
    oracle_text: str | None,
    type_line: str | None = None,
    name: str | None = None,
) -> Set[str]:
    normalization, rules = _load_core_role_rules()
    text = " ".join(part for part in (name, type_line, oracle_text) if part)
    normalized = _normalize_text(text, normalization)
    if not normalized or not rules:
        return set()
    matches: Set[str] = set()
    for rule in rules:
        if rule.requires and not all(rx.search(normalized) for rx in rule.requires):
            continue
        if rule.excludes and any(rx.search(normalized) for rx in rule.excludes):
            continue
        matches.add(rule.role)
    return matches
