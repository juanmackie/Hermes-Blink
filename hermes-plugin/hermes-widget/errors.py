"""Typed errors shared by the widget store and its domain modules."""

from __future__ import annotations

class StoreError(Exception):
    """Base class for widget-store failures."""

    code = "store_error"


class PublicationError(StoreError):
    """A publication is malformed or violates the publication contract."""

    code = "invalid_publication"


class PublicationTooLarge(PublicationError):
    """A publication or decoded image exceeds a hard limit."""

    code = "publication_too_large"


class AssetNotFound(StoreError):
    """An immutable asset does not exist."""

    code = "asset_not_found"


class AssetUnavailable(StoreError):
    """An asset record exists but its immutable file is unavailable or altered."""

    code = "asset_unavailable"


class RenderNotReady(PublicationError):
    """A device acknowledged rendering before downloading the revision."""

    code = "render_not_downloaded"


class RateLimitError(StoreError):
    """Too many requests in a rate window."""

    code = "rate_limited"


class PairingError(StoreError):
    """A pairing code was unknown, already used, or expired."""

    code = "invalid_or_expired_code"


class ActionIntentError(StoreError):
    """An action tap could not be safely queued."""

    code = "invalid_action_intent"
