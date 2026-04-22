"""Pure print-summary helpers for Scryfall cache wrappers."""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional


def display_name_for_print(pr: Dict[str, Any]) -> str:
    """Prefer face names and de-duplicate repeated split labels."""
    faces = pr.get("card_faces") or []
    if faces and isinstance(faces, list):
        names = [(face or {}).get("name", "").strip() for face in faces if face]
        names = [name for name in names if name]
        if not names:
            return pr.get("name") or ""
        if len(names) >= 2 and names[0].casefold() == names[-1].casefold():
            return names[0]
        return " // ".join(names)
    return pr.get("name") or ""


def type_label_for_print(pr: Dict[str, Any]) -> str:
    """Combine unique face type lines for DFC and adventure prints."""
    faces = pr.get("card_faces") or []
    if faces and isinstance(faces, list):
        type_lines = []
        seen = set()
        for face in faces:
            type_line = ((face or {}).get("type_line") or "").strip()
            if not type_line:
                continue
            type_key = type_line.casefold()
            if type_key in seen:
                continue
            seen.add(type_key)
            type_lines.append(type_line)
        if type_lines:
            if len(type_lines) >= 2 and type_lines[0].casefold() == type_lines[-1].casefold():
                return type_lines[0]
            return " // ".join(type_lines)
    return (pr.get("type_line") or "").strip()


def image_for_print(
    print_obj: Dict[str, Any],
    *,
    image_uris_fn: Callable[[Dict[str, Any]], Dict[str, Optional[str]]],
) -> Dict[str, Optional[str]]:
    """Return image URIs for a single print plus a stable label."""
    uris = dict(image_uris_fn(print_obj) or {})
    uris.setdefault("small", None)
    uris.setdefault("normal", None)
    uris.setdefault("large", None)
    collector_number = print_obj.get("collector_number")
    set_code = (print_obj.get("set") or "").upper()
    uris["label"] = f"{set_code} #{collector_number}" if set_code or collector_number else ""
    return uris


def resolve_print_bundle(
    set_code: str,
    collector_number: str,
    *,
    name_hint: Optional[str] = None,
    find_by_set_cn_fn: Callable[[str, str, Optional[str]], Optional[Dict[str, Any]]],
    image_uris_fn: Callable[[Dict[str, Any]], Dict[str, Optional[str]]],
) -> Optional[Dict[str, Any]]:
    """Build the standard print summary payload for routes and viewmodels."""
    print_obj = find_by_set_cn_fn(set_code, collector_number, name_hint)
    if not print_obj:
        return None
    return {
        "print": print_obj,
        "display_name": display_name_for_print(print_obj),
        "type_label": type_label_for_print(print_obj),
        "image": image_for_print(print_obj, image_uris_fn=image_uris_fn),
    }
