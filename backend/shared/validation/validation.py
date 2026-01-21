"""Input validation helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, List

from flask import current_app, has_app_context


@dataclass
class ValidationError(ValueError):
    message: str
    field: str | None = None
    invalid: List[Any] | None = None

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.message


def log_validation_error(err: ValidationError, *, context: str | None = None) -> None:
    if not has_app_context():
        return
    suffix = f" ({context})" if context else ""
    current_app.logger.warning(
        "Validation error%s: field=%s invalid=%s message=%s",
        suffix,
        err.field,
        err.invalid,
        err.message,
    )


def parse_positive_int(value: Any, *, field: str = "id", min_value: int = 1) -> int:
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ValidationError(f"Missing {field}.", field=field, invalid=[value])
    try:
        out = int(value)
    except (TypeError, ValueError):
        raise ValidationError(f"Invalid {field}.", field=field, invalid=[value])
    if out < min_value:
        raise ValidationError(f"Invalid {field}.", field=field, invalid=[value])
    return out


def parse_optional_positive_int(value: Any, *, field: str = "id", min_value: int = 1) -> int | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return parse_positive_int(value, field=field, min_value=min_value)


def _flatten(values: Iterable[Any]) -> Iterable[Any]:
    for value in values:
        if isinstance(value, (list, tuple, set)):
            yield from _flatten(value)
        else:
            yield value


def parse_positive_int_list(values: Iterable[Any], *, field: str = "ids", min_value: int = 1) -> list[int]:
    invalid: list[Any] = []
    output: list[int] = []
    for raw in _flatten(values):
        if raw is None:
            continue
        text = str(raw).strip()
        if not text:
            continue
        parts = [part.strip() for part in text.split(",")] if "," in text else [text]
        for part in parts:
            if not part:
                continue
            try:
                val = int(part)
            except (TypeError, ValueError):
                invalid.append(part)
                continue
            if val < min_value:
                invalid.append(part)
                continue
            output.append(val)
    if invalid:
        raise ValidationError(f"Invalid {field}.", field=field, invalid=invalid)
    return list(dict.fromkeys(output))
