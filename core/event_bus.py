"""Central event bus (spec section 14).

A single place that every part of the app publishes StudyEvents to. GUI
pages subscribe via the Qt signal to stay in sync without being wired
directly to ParticipantManager/SessionManager/etc (spec section 31: don't
tightly couple GUI to core logic -- use signals/events).

This module intentionally only holds the event *bus*, not the CSV writer.
Persisting events.csv is the recorder's job and arrives with the central
data recorder in a later milestone; for now events are logged via the
standard logging module and kept in a small in-memory ring buffer so the
Dashboard/Live Session pages have something to display.
"""

from __future__ import annotations

import logging
from collections import deque
from typing import Deque

from PySide6.QtCore import QObject, Signal

from models.event import StudyEvent

logger = logging.getLogger(__name__)

_RECENT_EVENTS_MAXLEN = 200


class EventBus(QObject):
    """Publish/subscribe hub for StudyEvents.

    Usage:
        event_bus.event_published.connect(my_slot)
        event_bus.publish(StudyEvent(event_type=EventType.SESSION_CREATED, ...))
    """

    event_published = Signal(object)  # emits a StudyEvent

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._recent: Deque[StudyEvent] = deque(maxlen=_RECENT_EVENTS_MAXLEN)

    def publish(self, event: StudyEvent) -> None:
        self._recent.append(event)
        logger.info(
            "EVENT %s participant=%s session=%s trial=%s value=%s",
            event.event_type.value,
            event.participant_id,
            event.session_id,
            event.trial_id,
            event.value,
        )
        self.event_published.emit(event)

    def recent_events(self, limit: int = 20) -> list[StudyEvent]:
        """Most recent events, newest last, for a live event-log widget."""
        items = list(self._recent)
        return items[-limit:]
