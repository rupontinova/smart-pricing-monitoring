"""Redis keyspace (MODULES.md §3.2)."""

GEO_SHOPS = "spm:geo:shops"


def rate_limit_key(scope: str, key: str, bucket: int) -> str:
    return f"spm:rl:{scope}:{key}:{bucket}"


EVENTS_STREAM = "spm:events"
EVENTS_DLQ_STREAM = "spm:events:dlq"
