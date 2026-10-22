"""The event envelope (MODULES.md §3.1). Payload models and helpers are added in M07."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID


@dataclass(frozen=True, slots=True)
class Event:
    id: UUID  # UUIDv7, unique per event: the idempotency key
    type: str  # e.g. "price.reported"
    version: int
    occurred_at: datetime
    source: str  # spm.api | spm.worker.scoring | spm.scheduler | …
    correlation_id: str | None
    data: dict[str, Any] = field(default_factory=dict)
