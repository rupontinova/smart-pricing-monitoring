from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True, slots=True)
class MetricCall:
    kind: Literal["count", "gauge", "timing"]
    name: str
    value: float
    dims: dict[str, str] = field(default_factory=dict)


class MemoryMetrics:
    """Records every call for assertions."""

    def __init__(self) -> None:
        self.calls: list[MetricCall] = []

    def count(self, name: str, value: float = 1, **dims: str) -> None:
        self.calls.append(MetricCall("count", name, value, dims))

    def gauge(self, name: str, value: float, **dims: str) -> None:
        self.calls.append(MetricCall("gauge", name, value, dims))

    def timing(self, name: str, ms: float, **dims: str) -> None:
        self.calls.append(MetricCall("timing", name, ms, dims))

    def total(self, name: str, **dims: str) -> float:
        """Sum of ``count`` values for ``name`` whose dims include ``dims``."""
        return sum(
            c.value
            for c in self.calls
            if c.kind == "count" and c.name == name and dims.items() <= c.dims.items()
        )
