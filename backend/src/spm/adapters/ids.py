"""UUIDv7 generation (time-ordered, which keeps B-tree indexes local)."""

import uuid

import uuid_utils


def new_uuid7() -> uuid.UUID:
    return uuid.UUID(bytes=uuid_utils.uuid7().bytes)


class UuidV7Generator:
    """Production ``IdGenerator``: a new random UUIDv7 per call."""

    def new(self) -> uuid.UUID:
        return new_uuid7()
