"""Hot-cache encoding shared by the Redis and memory adapters (MODULES.md §3.2)."""

from collections.abc import Mapping
from datetime import datetime
from typing import Any

import orjson

from spm.core.enums import Confidence
from spm.core.models import FairBand, ShopPrice

BAND_FIELDS = (
    "p25", "p50", "p75", "mad", "total_weight", "n_eff", "n_shops", "confidence",
    "newest_obs_at", "mean_age_h", "half_life_h", "rebase_at", "computed_at",
)  # fmt: skip


def _text(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def _enc(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Confidence):
        return value.value
    return str(value)


def encode_band(band: FairBand) -> dict[str, str]:
    """``spm:band:{area}:{product}`` hash fields; None becomes the empty string."""
    return {name: _enc(getattr(band, name)) for name in BAND_FIELDS}


def decode_band(area_id: str, product_id: str, raw: Mapping[Any, Any]) -> FairBand:
    f = {_text(k): _text(v) for k, v in raw.items()}

    def num(name: str) -> float | None:
        return float(f[name]) if f.get(name) else None

    def ts(name: str) -> datetime | None:
        return datetime.fromisoformat(f[name]) if f.get(name) else None

    computed_at = ts("computed_at")
    assert computed_at is not None, "band hash without computed_at"
    return FairBand(
        product_id=product_id,
        area_id=area_id,
        p25=num("p25"),
        p50=num("p50"),
        p75=num("p75"),
        mad=num("mad"),
        total_weight=float(f["total_weight"]),
        n_eff=float(f["n_eff"]),
        n_shops=int(f["n_shops"]),
        confidence=Confidence(f["confidence"]),
        newest_obs_at=ts("newest_obs_at"),
        mean_age_h=num("mean_age_h"),
        half_life_h=int(f["half_life_h"]),
        rebase_at=ts("rebase_at"),
        computed_at=computed_at,
    )


def encode_shop_price(sp: ShopPrice) -> bytes:
    """``spm:shopprice`` hash value: §3.2's fields plus ``support_weight``."""
    return orjson.dumps(
        {
            "price": sp.price,
            "verified": sp.verified,
            "verified_at": sp.verified_at,
            "last_observed_at": sp.last_observed_at,
            "n_reports": sp.n_reports,
            "support_weight": sp.support_weight,
        }
    )


def decode_shop_price(area_id: str, product_id: str, shop_id: str, raw: Any) -> ShopPrice:
    d = orjson.loads(raw)
    return ShopPrice(
        product_id=product_id,
        shop_id=shop_id,
        area_id=area_id,
        price=float(d["price"]),
        verified=bool(d["verified"]),
        support_weight=float(d.get("support_weight", 0.0)),
        n_reports=int(d["n_reports"]),
        last_observed_at=datetime.fromisoformat(d["last_observed_at"]),
        verified_at=datetime.fromisoformat(d["verified_at"]) if d.get("verified_at") else None,
    )
