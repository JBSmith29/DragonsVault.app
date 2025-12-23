"""
Monkeypatch services.scryfall_cache to accept flexible signatures used in routes:
- find_by_set_cn(set_code, collector_number, [name], prefer_lang=?)
- image_for_print(set_code, collector_number, [is_foil] or prefer_lang=?, is_foil=?)
Import this module BEFORE importing routes (done in app.py).
"""
from typing import Any, Optional
try:
    from services import scryfall_cache as sc  # type: ignore
except Exception:  # pragma: no cover
    sc = None

if sc is not None:
    # Capture originals
    _orig_find = getattr(sc, "find_by_set_cn", None)
    _orig_image = getattr(sc, "image_for_print", None)

    def _compat_find_by_set_cn(*args: Any, **kwargs: Any) -> Optional[dict]:
        if _orig_find is None:
            return None
        # Normalize
        prefer_lang = kwargs.pop("prefer_lang", None)
        set_code = args[0] if len(args) > 0 else None
        collector_number = args[1] if len(args) > 1 else None
        name = args[2] if len(args) > 2 else None

        # Try (set, cn, name, prefer_lang=?)
        try:
            return _orig_find(set_code, collector_number, name, prefer_lang=prefer_lang)
        except TypeError:
            pass
        # Try (set, cn, name)
        try:
            return _orig_find(set_code, collector_number, name)
        except TypeError:
            pass
        # Try (set, cn, prefer_lang=?)
        try:
            return _orig_find(set_code, collector_number, prefer_lang=prefer_lang)
        except TypeError:
            pass
        # Try (set, cn)
        try:
            return _orig_find(set_code, collector_number)
        except Exception:
            return None

    def _compat_image_for_print(set_code: str, collector_number: str, *args: Any, **kwargs: Any) -> Optional[str]:
        if _orig_image is None:
            return None
        prefer_lang = kwargs.pop("prefer_lang", None)
        is_foil = kwargs.pop("is_foil", False)
        # Try (set, cn, prefer_lang=?, is_foil=?)
        try:
            return _orig_image(set_code, collector_number, prefer_lang=prefer_lang, is_foil=is_foil)
        except TypeError:
            pass
        # Try (set, cn, is_foil) positional
        try:
            return _orig_image(set_code, collector_number, is_foil)
        except TypeError:
            pass
        # Fallback (set, cn)
        try:
            return _orig_image(set_code, collector_number)
        except Exception:
            return None

    # Monkeypatch the module so later imports get the compat functions
    sc.find_by_set_cn = _compat_find_by_set_cn  # type: ignore[attr-defined]
