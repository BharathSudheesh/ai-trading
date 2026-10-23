class NotFoundError(LookupError):
    """A requested profile or recommendation does not exist."""


class TradeError(ValueError):
    """A paper trade cannot be executed (no cash, no position, wrong state)."""
