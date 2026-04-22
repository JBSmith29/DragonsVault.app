"""Shared oracle-print analysis helpers for recomputation jobs."""

from __future__ import annotations

from typing import Iterable


_DASH = "\u2014"
_EXCLUDED_SET_TYPES = {"token", "memorabilia", "art_series"}
_TYPAL_TRIGGER_TYPES = {"creature", "tribal", "kindred"}
_TYPE_LINE_SKIP_TOKENS = {
    "artifact",
    "battle",
    "basic",
    "creature",
    "enchantment",
    "instant",
    "kindred",
    "land",
    "legendary",
    "ongoing",
    "planeswalker",
    "scheme",
    "snow",
    "sorcery",
    "token",
    "tribal",
    "vanguard",
    "world",
}


def score_print(print_data: dict) -> int:
    score = 0
    if print_data.get("lang") == "en":
        score += 3
    if (print_data.get("set_type") or "") not in _EXCLUDED_SET_TYPES:
        score += 2
    if "paper" in (print_data.get("games") or []):
        score += 1
    if not print_data.get("digital"):
        score += 1
    return score


def select_best_print(prints: Iterable[dict]) -> dict | None:
    best = None
    best_score = -1
    for print_data in prints:
        if not isinstance(print_data, dict):
            continue
        score = score_print(print_data)
        if score > best_score:
            best = print_data
            best_score = score
    return best


def join_faces(faces: list[dict], key: str) -> str | None:
    parts = []
    for face in faces:
        if not isinstance(face, dict):
            continue
        value = face.get(key)
        if value:
            parts.append(value)
    if not parts:
        return None
    return "\n\n//\n\n".join(parts)


def oracle_text_from_print(print_data: dict) -> str:
    return print_data.get("oracle_text") or join_faces(print_data.get("card_faces") or [], "oracle_text") or ""


def type_line_from_print(print_data: dict) -> str:
    return print_data.get("type_line") or join_faces(print_data.get("card_faces") or [], "type_line") or ""


def iter_type_lines(print_data: dict) -> Iterable[str]:
    type_line = print_data.get("type_line")
    if type_line:
        yield type_line
    for face in print_data.get("card_faces") or []:
        if not isinstance(face, dict):
            continue
        face_line = face.get("type_line")
        if face_line:
            yield face_line


def split_type_line(type_line: str) -> tuple[str, str] | None:
    if not type_line:
        return None
    if _DASH in type_line:
        left, right = type_line.split(_DASH, 1)
    elif " - " in type_line:
        left, right = type_line.split(" - ", 1)
    else:
        return None
    return left.strip(), right.strip()


def typal_from_type_line(type_line: str) -> set[str]:
    split = split_type_line(type_line)
    if not split:
        return set()
    left, right = split
    left_norm = left.lower()
    if not any(token in left_norm for token in _TYPAL_TRIGGER_TYPES):
        return set()
    out: set[str] = set()
    for token in right.split():
        token = token.strip()
        if not token:
            continue
        if not any(char.isalpha() for char in token):
            continue
        token_norm = token.lower()
        if token_norm in _TYPE_LINE_SKIP_TOKENS:
            continue
        out.add(token_norm)
    return out


def collect_keywords(prints: Iterable[dict]) -> set[str]:
    keywords: set[str] = set()
    for print_data in prints:
        if not isinstance(print_data, dict):
            continue
        for keyword in print_data.get("keywords") or []:
            if not isinstance(keyword, str):
                continue
            normalized = keyword.strip().lower()
            if normalized:
                keywords.add(normalized)
    return keywords


def collect_typals(prints: Iterable[dict]) -> set[str]:
    typals: set[str] = set()
    for print_data in prints:
        if not isinstance(print_data, dict):
            continue
        for type_line in iter_type_lines(print_data):
            typals.update(typal_from_type_line(type_line))
    return typals


def build_oracle_mock(print_data: dict) -> dict:
    return {
        "name": print_data.get("name") or "",
        "oracle_text": oracle_text_from_print(print_data),
        "type_line": type_line_from_print(print_data),
        "card_faces": print_data.get("card_faces") or [],
        "layout": print_data.get("layout") or "",
        "produced_mana": print_data.get("produced_mana") or [],
    }


def analyze_oracle_prints(
    prints: Iterable[dict],
    *,
    get_land_tags_for_card_fn,
    derive_evergreen_keywords_fn,
    derive_core_roles_fn,
    core_role_label_fn,
    get_roles_for_card_fn=None,
    get_subroles_for_card_fn=None,
    get_primary_role_fn=None,
    derive_deck_tags_fn=None,
    ensure_fallback_tag_fn=None,
) -> dict | None:
    best = select_best_print(prints) or (prints[0] if prints else None)
    if not best:
        return None
    mock = build_oracle_mock(best)
    oracle_text = mock["oracle_text"]
    type_line = mock["type_line"]
    keywords = collect_keywords(prints)
    typals = collect_typals(prints)
    land_tags = get_land_tags_for_card_fn(mock)
    evergreen = derive_evergreen_keywords_fn(
        oracle_text=oracle_text,
        type_line=type_line,
        name=mock["name"],
        keywords=keywords,
        typals=typals,
        colors=best.get("color_identity") or best.get("colors"),
    )
    if land_tags:
        evergreen |= set(land_tags)
    core_roles = derive_core_roles_fn(
        oracle_text=oracle_text,
        type_line=type_line,
        name=mock["name"],
    )
    core_role_tags: set[str] = set()
    for role in core_roles:
        label = core_role_label_fn(role)
        if label:
            core_role_tags.add(label)

    analysis = {
        "best": best,
        "mock": mock,
        "oracle_text": oracle_text,
        "type_line": type_line,
        "keywords": keywords,
        "typals": typals,
        "land_tags": set(land_tags or []),
        "evergreen": evergreen,
        "core_roles": core_roles,
        "core_role_tags": core_role_tags,
    }

    if get_roles_for_card_fn and get_subroles_for_card_fn and get_primary_role_fn:
        roles = get_roles_for_card_fn(mock)
        subroles = get_subroles_for_card_fn(mock)
        analysis["roles"] = roles
        analysis["subroles"] = subroles
        analysis["primary_role"] = get_primary_role_fn(roles)

    if derive_deck_tags_fn:
        deck_tags = derive_deck_tags_fn(
            oracle_text=oracle_text,
            type_line=type_line,
            keywords=keywords,
            typals=typals,
            roles=analysis.get("roles") or [],
        )
        if not core_role_tags and ensure_fallback_tag_fn:
            deck_tags = ensure_fallback_tag_fn(deck_tags, evergreen)
        analysis["deck_tags"] = deck_tags

    return analysis


__all__ = [
    "analyze_oracle_prints",
    "build_oracle_mock",
    "collect_keywords",
    "collect_typals",
    "join_faces",
    "oracle_text_from_print",
    "score_print",
    "select_best_print",
    "split_type_line",
    "typal_from_type_line",
    "type_line_from_print",
]
