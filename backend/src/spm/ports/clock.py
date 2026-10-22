from datetime import datetime
from typing import Protocol
from uuid import UUID


class Clock(Protocol):
    def now(self) -> datetime:
        """Current time, timezone-aware UTC."""
        ...


class IdGenerator(Protocol):
    def new(self) -> UUID:
        """A new UUIDv7."""
        ...
