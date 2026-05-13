"""Deck legality checker.

Validates a deck folder against published format rules. Uses the Scryfall
legality flags already loaded in the cache, plus format-specific structural
rules (singleton, deck size, commander color identity, sideboard) that
Scryfall does not encode.

The service is intentionally read-only and deterministic: given the same deck
contents and cache epoch, it always produces the same verdict. This makes it
safe to memoize or render client-side.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from sqlalchemy.orm import selectinload

from extensions import db
from models import Card, Folder
from core.domains.cards.services import scryfall_cache as sc
from core.domains.decks.services.commander_utils import (
    split_commander_names,
    split_commander_oracle_ids,
)
from core.domains.decks.services.deck_utils import BASIC_LANDS
from shared.mtg import _lookup_print_data


__all__ = [
    "SUPPORTED_FORMATS",
    "FormatDefinition",
    "LegalityIssue",
    "LegalityReport",
    "available_formats",
    "evaluate_folder_legality",
]


# Snow-covered lands count as basics for legality/singleton purposes.
_BASIC_LAND_NAMES: frozenset[str] = frozenset(
    {name.lower() for name in BASIC_LANDS}
    | {f"snow-covered {name.lower()}" for name in BASIC_LANDS}
)


# ---------------------------------------------------------------------------
# Format definitions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FormatDefinition:
    """Declarative description of a format's structural rules."""

    key: str
    label: str
    #: Scryfall legality key (e.g. "commander", "modern").
    legality_key: str
    #: Exact deck size required (``None`` means no minimum/exact check).
    deck_size: int | None = None
    #: Minimum deck size (used when no exact size is required).
    min_deck_size: int | None = None
    #: Maximum non-basic copies of a single card allowed in the mainboard.
    max_copies: int = 4
    #: True when singleton rules apply (only one non-basic copy).
    singleton: bool = False
    #: True when a commander card is required in the command zone.
    requires_commander: bool = False
    #: True when deck cards must share the commander's color identity.
    enforce_color_identity: bool = False
    #: Maximum sideboard size (``None`` means no sideboard check).
    sideboard_size: int | None = 15
    #: Legalities treated as acceptable for the format.
    allow_legality_values: frozenset[str] = field(
        default_factory=lambda: frozenset({"legal"})
    )
    #: Short blurb shown alongside the verdict.
    description: str = ""


SUPPORTED_FORMATS: tuple[FormatDefinition, ...] = (
    FormatDefinition(
        key="commander",
        label="Commander",
        legality_key="commander",
        deck_size=100,
        max_copies=1,
        singleton=True,
        requires_commander=True,
        enforce_color_identity=True,
        sideboard_size=0,
        description="100-card singleton with one commander. Cards must match the commander's color identity.",
    ),
    FormatDefinition(
        key="standard",
        label="Standard",
        legality_key="standard",
        min_deck_size=60,
        max_copies=4,
        description="60+ card mainboard, up to 4 copies of each non-basic card.",
    ),
    FormatDefinition(
        key="pioneer",
        label="Pioneer",
        legality_key="pioneer",
        min_deck_size=60,
        max_copies=4,
        description="60+ card mainboard, up to 4 copies of each non-basic card.",
    ),
    FormatDefinition(
        key="modern",
        label="Modern",
        legality_key="modern",
        min_deck_size=60,
        max_copies=4,
        description="60+ card mainboard, up to 4 copies of each non-basic card.",
    ),
    FormatDefinition(
        key="legacy",
        label="Legacy",
        legality_key="legacy",
        min_deck_size=60,
        max_copies=4,
        description="60+ card mainboard, up to 4 copies of each non-basic card.",
    ),
    FormatDefinition(
        key="vintage",
        label="Vintage",
        legality_key="vintage",
        min_deck_size=60,
        max_copies=4,
        # Vintage uniquely allows restricted cards at 1 copy.
        allow_legality_values=frozenset({"legal", "restricted"}),
        description="60+ card mainboard. Restricted cards may appear once.",
    ),
    FormatDefinition(
        key="pauper",
        label="Pauper",
        legality_key="pauper",
        min_deck_size=60,
        max_copies=4,
        description="Common-only 60+ card mainboard, up to 4 copies.",
    ),
    FormatDefinition(
        key="brawl",
        label="Brawl",
        legality_key="brawl",
        deck_size=60,
        max_copies=1,
        singleton=True,
        requires_commander=True,
        enforce_color_identity=True,
        sideboard_size=0,
        description="60-card singleton Standard-legal deck with a commander.",
    ),
)


def available_formats() -> list[dict[str, Any]]:
    """Return JSON-friendly descriptors for all supported formats."""
    return [
        {
            "key": fmt.key,
            "label": fmt.label,
            "deck_size": fmt.deck_size,
            "min_deck_size": fmt.min_deck_size,
            "max_copies": fmt.max_copies,
            "singleton": fmt.singleton,
            "requires_commander": fmt.requires_commander,
            "enforce_color_identity": fmt.enforce_color_identity,
            "sideboard_size": fmt.sideboard_size,
            "description": fmt.description,
        }
        for fmt in SUPPORTED_FORMATS
    ]


def _format_by_key(key: str) -> FormatDefinition | None:
    normalized = (key or "").strip().lower()
    for fmt in SUPPORTED_FORMATS:
        if fmt.key == normalized:
            return fmt
    return None


# ---------------------------------------------------------------------------
# Issue / report dataclasses
# ---------------------------------------------------------------------------


#: Severity ordered by priority: ``error`` blocks play, ``warning`` flags
#: missing data, and ``info`` explains adjustments (e.g. basics).
SEVERITIES = ("error", "warning", "info")


@dataclass
class LegalityIssue:
    severity: str
    code: str
    message: str
    card_name: str | None = None
    card_id: int | None = None
    oracle_id: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "card_name": self.card_name,
            "card_id": self.card_id,
            "oracle_id": self.oracle_id,
            "meta": dict(self.meta) if self.meta else {},
        }


@dataclass
class LegalityReport:
    format_key: str
    format_label: str
    legal: bool
    deck_size: int
    mainboard_size: int
    sideboard_size: int
    commander_names: list[str]
    issues: list[LegalityIssue]
    summary: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": {"key": self.format_key, "label": self.format_label},
            "legal": self.legal,
            "deck_size": self.deck_size,
            "mainboard_size": self.mainboard_size,
            "sideboard_size": self.sideboard_size,
            "commander_names": list(self.commander_names),
            "issues": [issue.to_dict() for issue in self.issues],
            "summary": dict(self.summary),
        }


# ---------------------------------------------------------------------------
# Card enrichment
# ---------------------------------------------------------------------------


def _is_basic_land_name(name: str | None) -> bool:
    return bool(name) and name.strip().lower() in _BASIC_LAND_NAMES


def _legality_for_print(print_data: dict | None, legality_key: str) -> str:
    if not print_data:
        return "unknown"
    legalities = print_data.get("legalities") or {}
    value = legalities.get(legality_key)
    return str(value).lower() if value else "unknown"


def _color_identity(print_data: dict | None) -> set[str]:
    if not print_data:
        return set()
    raw = print_data.get("color_identity") or []
    return {str(ch).upper() for ch in raw if ch}


@dataclass
class _EnrichedCard:
    card: Card
    print_data: dict | None
    type_line: str
    name: str
    oracle_id: str | None
    color_identity: set[str]
    is_basic: bool


def _enrich(cards: Iterable[Card]) -> list[_EnrichedCard]:
    """Hydrate cards with Scryfall cache metadata for legality checks."""
    enriched: list[_EnrichedCard] = []
    for card in cards:
        name = (card.name or "").strip()
        type_line = (card.type_line or "").strip()
        print_data = _lookup_print_data(
            getattr(card, "set_code", None),
            getattr(card, "collector_number", None),
            name,
            getattr(card, "oracle_id", None),
        )
        if not type_line:
            type_line = str((print_data or {}).get("type_line") or "")
        enriched.append(
            _EnrichedCard(
                card=card,
                print_data=print_data,
                type_line=type_line,
                name=name,
                oracle_id=card.oracle_id,
                color_identity=_color_identity(print_data),
                is_basic=_is_basic_land_name(name),
            )
        )
    return enriched


# ---------------------------------------------------------------------------
# Public evaluation API
# ---------------------------------------------------------------------------


def evaluate_folder_legality(folder: Folder, format_key: str) -> LegalityReport:
    """Evaluate an entire folder (deck) against a specific format.

    ``folder`` must be loaded within a SQLAlchemy session. Cards are fetched
    fresh to guarantee the check reflects persisted state.
    """
    fmt = _format_by_key(format_key)
    if fmt is None:
        raise ValueError(f"Unsupported format: {format_key!r}")

    if not sc.cache_ready():
        sc.ensure_cache_loaded()

    cards = (
        db.session.query(Card)
        .options(selectinload(Card.folder))
        .filter(Card.folder_id == folder.id)
        .all()
    )
    enriched = _enrich(cards)
    return _evaluate(fmt, folder, enriched)


# ---------------------------------------------------------------------------
# Internal evaluation
# ---------------------------------------------------------------------------


def _evaluate(fmt: FormatDefinition, folder: Folder, cards: list[_EnrichedCard]) -> LegalityReport:
    issues: list[LegalityIssue] = []
    mainboard_count = sum(max(0, int(c.card.quantity or 0)) for c in cards)
    commander_names = split_commander_names(getattr(folder, "commander_name", None))
    commander_oracle_ids = split_commander_oracle_ids(
        getattr(folder, "commander_oracle_id", None)
    )
    commander_color_identity = _compute_commander_color_identity(
        cards, commander_names, commander_oracle_ids
    )

    _check_deck_size(fmt, mainboard_count, issues)
    _check_copy_limits(fmt, cards, issues)
    _check_card_legality(fmt, cards, issues)
    if fmt.requires_commander:
        _check_commander(fmt, folder, cards, commander_names, issues)
    if fmt.enforce_color_identity and commander_names:
        _check_color_identity(fmt, cards, commander_color_identity, commander_names, issues)

    summary = _summarize(issues)
    legal = summary.get("error", 0) == 0

    return LegalityReport(
        format_key=fmt.key,
        format_label=fmt.label,
        legal=legal,
        deck_size=mainboard_count,
        mainboard_size=mainboard_count,
        sideboard_size=0,  # sideboards are not modeled yet; placeholder for future.
        commander_names=list(commander_names),
        issues=issues,
        summary=summary,
    )


def _compute_commander_color_identity(
    cards: list[_EnrichedCard],
    commander_names: list[str],
    commander_oracle_ids: list[str],
) -> set[str]:
    """Union of color identities across all listed commanders."""
    if not commander_names and not commander_oracle_ids:
        return set()
    lowered_names = {n.lower() for n in commander_names}
    oracles = {o for o in commander_oracle_ids if o}
    identity: set[str] = set()
    for enriched in cards:
        if enriched.oracle_id and enriched.oracle_id in oracles:
            identity.update(enriched.color_identity)
            continue
        if enriched.name.lower() in lowered_names:
            identity.update(enriched.color_identity)
    return identity


def _check_deck_size(fmt: FormatDefinition, count: int, issues: list[LegalityIssue]) -> None:
    if fmt.deck_size is not None and count != fmt.deck_size:
        issues.append(
            LegalityIssue(
                severity="error",
                code="deck_size",
                message=(
                    f"{fmt.label} requires exactly {fmt.deck_size} cards "
                    f"(this deck has {count})."
                ),
                meta={"expected": fmt.deck_size, "actual": count},
            )
        )
    elif fmt.min_deck_size is not None and count < fmt.min_deck_size:
        issues.append(
            LegalityIssue(
                severity="error",
                code="min_deck_size",
                message=(
                    f"{fmt.label} requires at least {fmt.min_deck_size} cards "
                    f"(this deck has {count})."
                ),
                meta={"minimum": fmt.min_deck_size, "actual": count},
            )
        )


def _check_copy_limits(
    fmt: FormatDefinition,
    cards: list[_EnrichedCard],
    issues: list[LegalityIssue],
) -> None:
    """Enforce singleton or per-card copy caps.

    Copies are aggregated by ``oracle_id`` when available, falling back to the
    lowercase name to keep the check robust even if oracle ids aren't filled.
    """
    aggregate: dict[str, dict[str, Any]] = {}
    for enriched in cards:
        if enriched.is_basic:
            continue
        qty = max(0, int(enriched.card.quantity or 0))
        if qty <= 0:
            continue
        key = enriched.oracle_id or enriched.name.lower()
        entry = aggregate.setdefault(
            key, {"count": 0, "name": enriched.name, "ids": [], "oracle_id": enriched.oracle_id}
        )
        entry["count"] += qty
        entry["ids"].append(enriched.card.id)

    cap = 1 if fmt.singleton else fmt.max_copies
    for key, entry in aggregate.items():
        if entry["count"] <= cap:
            continue
        message = (
            f"'{entry['name']}' appears {entry['count']} times — "
            f"{fmt.label} allows at most {cap}."
        )
        issues.append(
            LegalityIssue(
                severity="error",
                code="copy_limit",
                message=message,
                card_name=entry["name"],
                oracle_id=entry["oracle_id"],
                meta={"count": entry["count"], "allowed": cap},
            )
        )


def _check_card_legality(
    fmt: FormatDefinition,
    cards: list[_EnrichedCard],
    issues: list[LegalityIssue],
) -> None:
    seen_bad: set[str] = set()
    for enriched in cards:
        status = _legality_for_print(enriched.print_data, fmt.legality_key)
        if status in fmt.allow_legality_values:
            continue
        key = f"{fmt.key}:{enriched.oracle_id or enriched.name.lower()}"
        if key in seen_bad:
            continue
        seen_bad.add(key)

        severity = "error"
        code = "card_not_legal"
        human_status = status.replace("_", " ") if status != "unknown" else "unknown"
        message = (
            f"'{enriched.name}' is {human_status} in {fmt.label}."
        )
        if status == "unknown":
            severity = "warning"
            code = "card_legality_unknown"
            message = (
                f"'{enriched.name}' has no legality data in the local cache; "
                f"unable to verify against {fmt.label}."
            )
        issues.append(
            LegalityIssue(
                severity=severity,
                code=code,
                message=message,
                card_name=enriched.name,
                card_id=enriched.card.id,
                oracle_id=enriched.oracle_id,
                meta={"legality": status},
            )
        )


def _check_commander(
    fmt: FormatDefinition,
    folder: Folder,
    cards: list[_EnrichedCard],
    commander_names: list[str],
    issues: list[LegalityIssue],
) -> None:
    if not commander_names:
        issues.append(
            LegalityIssue(
                severity="error",
                code="commander_missing",
                message=f"{fmt.label} requires a commander, but none is assigned.",
            )
        )
        return
    in_deck = {c.name.lower() for c in cards if c.name}
    for name in commander_names:
        if name.lower() not in in_deck:
            issues.append(
                LegalityIssue(
                    severity="warning",
                    code="commander_not_in_deck",
                    message=(
                        f"Commander '{name}' is not present in the deck rows. "
                        "Add it to the folder so copy counts and color identity are correct."
                    ),
                    card_name=name,
                )
            )


def _check_color_identity(
    fmt: FormatDefinition,
    cards: list[_EnrichedCard],
    commander_identity: set[str],
    commander_names: list[str],
    issues: list[LegalityIssue],
) -> None:
    if not commander_identity and commander_names:
        # We know the commander but couldn't resolve its identity: flag a warning
        # rather than silently passing.
        issues.append(
            LegalityIssue(
                severity="warning",
                code="commander_identity_unknown",
                message=(
                    "Unable to determine commander color identity from the cache; "
                    "color-identity checks were skipped."
                ),
                meta={"commander": commander_names},
            )
        )
        return

    lowered_commander_names = {n.lower() for n in commander_names}
    for enriched in cards:
        if enriched.is_basic:
            continue
        if enriched.name.lower() in lowered_commander_names:
            continue
        card_identity = enriched.color_identity
        extra = card_identity - commander_identity
        if not extra:
            continue
        issues.append(
            LegalityIssue(
                severity="error",
                code="color_identity",
                message=(
                    f"'{enriched.name}' has colors {sorted(card_identity)} outside "
                    f"the commander identity {sorted(commander_identity)}."
                ),
                card_name=enriched.name,
                card_id=enriched.card.id,
                oracle_id=enriched.oracle_id,
                meta={
                    "card_identity": sorted(card_identity),
                    "commander_identity": sorted(commander_identity),
                    "extra": sorted(extra),
                },
            )
        )


def _summarize(issues: list[LegalityIssue]) -> dict[str, int]:
    summary = {sev: 0 for sev in SEVERITIES}
    for issue in issues:
        if issue.severity in summary:
            summary[issue.severity] += 1
    return summary
