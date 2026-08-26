"""Participant models.

Per spec section 8, identifiable information (name/email/phone) must be
stored separately from experimental data, and experimental artifacts must
only ever reference the pseudonymous ``participant_code`` (e.g. "P023"),
never a name.

We therefore intentionally keep two distinct dataclasses rather than one
"Participant" object with everything in it -- that separation is the whole
point, and merging them back into a single object anywhere in the codebase
would defeat the privacy design.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ParticipantIdentity:
    """Identifiable information. Lives ONLY in the identifiable database.

    Never write this alongside experimental data, never use ``name`` in a
    filename or session folder, and never pass this object into RL/device/
    recording code.
    """

    participant_code: str
    name: str
    email: str = ""
    phone: str = ""
    created_at: float = field(default_factory=time.time)


@dataclass
class ParticipantRecord:
    """Pseudonymous experimental record. Safe to pass anywhere in the app.

    Only ``participant_code`` and study-relevant fields -- no PII.
    """

    participant_code: str
    created_at: float = field(default_factory=time.time)
    demographics: dict = field(default_factory=dict)
    notes: str = ""
    prior_sessions_completed: int = 0
    modality_order: Optional[str] = None
    """Assigned counterbalancing order for Study 2, if applicable."""
