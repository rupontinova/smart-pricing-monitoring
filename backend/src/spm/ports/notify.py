from typing import Any, Protocol

from spm.core.enums import Channel


class Notifier(Protocol):
    async def send(
        self, channel: Channel, target: dict[str, Any], title: str, body: str, data: dict[str, Any]
    ) -> None: ...
