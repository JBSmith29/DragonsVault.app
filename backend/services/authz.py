"""Authorization helpers for DragonsVault."""
from __future__ import annotations

import hmac
import hashlib

from flask import abort
from flask_login import current_user

from models import FolderShare


def require_admin() -> None:
    if not current_user.is_authenticated or not getattr(current_user, "is_admin", False):
        abort(403)


def ensure_folder_access(folder, *, write: bool = False, allow_shared: bool = False, share_token: str | None = None) -> None:
    if folder is None:
        abort(404)
    owner_id = getattr(folder, "owner_user_id", None)
    if owner_id and current_user.is_authenticated and current_user.id == owner_id:
        return
    if write:
        abort(403)
    if not allow_shared:
        abort(403)
    if not current_user.is_authenticated:
        abort(403)

    if getattr(folder, "is_public", False):
        return

    if share_token:
        token_hash = hashlib.sha256(share_token.encode("utf-8")).hexdigest()
        if getattr(folder, "share_token_hash", None):
            try:
                if hmac.compare_digest(folder.share_token_hash, token_hash):
                    return
            except Exception:
                pass
        # legacy fallback if plaintext column still exists
        if getattr(folder, "share_token", None):
            try:
                if hmac.compare_digest(folder.share_token, share_token):
                    return
            except Exception:
                pass

    share = (
        FolderShare.query.filter_by(folder_id=folder.id, shared_user_id=current_user.id).first()
        if folder.id
        else None
    )
    if share:
        return

    abort(403)
