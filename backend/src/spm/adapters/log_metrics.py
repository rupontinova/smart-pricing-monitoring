"""``LogMetrics`` (``SPM_METRICS=log``): one debug log line per metric call; EMF arrives in M18."""

import structlog

log = structlog.get_logger("spm.metrics")


class LogMetrics:
    def count(self, name: str, value: float = 1, **dims: str) -> None:
        log.debug("metric", kind="count", name=name, value=value, **dims)

    def gauge(self, name: str, value: float, **dims: str) -> None:
        log.debug("metric", kind="gauge", name=name, value=value, **dims)

    def timing(self, name: str, ms: float, **dims: str) -> None:
        log.debug("metric", kind="timing", name=name, value=ms, **dims)
