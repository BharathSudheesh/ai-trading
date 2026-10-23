"""Notification Agent: raises an in-app alert when a recommendation needs the user's attention."""

from __future__ import annotations

import logging
from collections.abc import Callable

from ..db import Store, utcnow
from ..schemas import Action, Notification, Recommendation, UserProfile

log = logging.getLogger("trading_agent.alerts")

Channel = Callable[[Notification], None]


def log_channel(notification: Notification) -> None:
    log.info("ALERT profile=%s %s", notification.profile_id, notification.message)


class NotificationAgent:
    """Stores alerts for the dashboard and forwards them to extra channels.

    Add a channel (email, Slack, Telegram) by appending a ``Callable[[Notification], None]``.
    """

    def __init__(self, store: Store, channels: list[Channel] | None = None):
        self.store = store
        self.channels: list[Channel] = channels if channels is not None else [log_channel]

    def notify(self, profile: UserProfile, rec: Recommendation) -> Notification | None:
        if rec.replay or not profile.notify_enabled or rec.action == Action.HOLD:
            return None
        note = self.store.add_notification(Notification(
            profile_id=rec.profile_id,
            rec_id=rec.id,
            symbol=rec.symbol,
            message=f"{rec.action.value} {rec.symbol}: {rec.summary}",
            created_at=utcnow(),
        ))
        for channel in self.channels:
            try:
                channel(note)
            except Exception:  # one broken channel must not block the others
                log.exception("Notification channel failed")
        return note
