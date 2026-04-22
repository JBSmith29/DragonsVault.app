"""Wishlist domain constants shared across routes and services."""

ALLOWED_WISHLIST_STATUSES = {
    "open",
    "to_fetch",
    "ordered",
    "acquired",
    "removed",
    "requested",
    "rejected",
}

__all__ = ["ALLOWED_WISHLIST_STATUSES"]
