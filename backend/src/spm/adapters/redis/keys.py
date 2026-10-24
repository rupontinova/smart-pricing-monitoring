"""Redis keyspace (MODULES.md §3.2)."""

GEO_SHOPS = "spm:geo:shops"


def rate_limit_key(scope: str, key: str, bucket: int) -> str:
    return f"spm:rl:{scope}:{key}:{bucket}"


EVENTS_STREAM = "spm:events"
EVENTS_DLQ_STREAM = "spm:events:dlq"


def band_key(area_id: str, product_id: str) -> str:
    return f"spm:band:{area_id}:{product_id}"


def cheap_key(area_id: str, product_id: str) -> str:
    return f"spm:cheap:{area_id}:{product_id}"


def shop_price_key(area_id: str, product_id: str) -> str:
    return f"spm:shopprice:{area_id}:{product_id}"
