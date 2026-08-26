"""Study event model.

Matches the events.csv schema in spec section 14:

    timestamp,event,participant_id,session_id,trial_id,episode,step,value
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

from models.enums import EventType


@dataclass
class StudyEvent:
    event_type: EventType
    participant_id: Optional[str] = None
    session_id: Optional[str] = None
    trial_id: Optional[str] = None
    episode: Optional[int] = None
    step: Optional[int] = None
    value: Optional[str] = None
    timestamp: float = field(default_factory=time.time)

    def to_csv_row(self) -> dict:
        """Flat dict matching the events.csv column order."""
        return {
            "timestamp": f"{self.timestamp:.3f}",
            "event": self.event_type.value,
            "participant_id": self.participant_id or "",
            "session_id": self.session_id or "",
            "trial_id": self.trial_id or "",
            "episode": self.episode if self.episode is not None else "",
            "step": self.step if self.step is not None else "",
            "value": self.value if self.value is not None else "",
        }
