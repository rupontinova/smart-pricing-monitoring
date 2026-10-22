"""SQLAlchemy Core table definitions mirroring migration ``0001_initial`` (MODULES.md M03).

The migration is the source of truth for the DDL; ``tests/integration/test_schema.py`` compares this
metadata against the migrated database so the two can't drift apart.
"""

from typing import Literal

from geoalchemy2 import Geography
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    PrimaryKeyConstraint,
    Table,
    Text,
    Time,
    UniqueConstraint,
    func,
    literal_column,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, ENUM, JSONB, REAL, UUID

from spm.core.enums import AnomalyKind, BaseUnit, Classification, Direction, ScoringState

metadata = MetaData()


def _pg_enum(
    enum: type[BaseUnit | Classification | ScoringState | AnomalyKind | Direction], name: str
) -> ENUM:
    return ENUM(
        enum,
        name=name,
        values_callable=lambda members: [m.value for m in members],
        create_type=False,  # created by the migration
    )


base_unit = _pg_enum(BaseUnit, "base_unit")
classification = _pg_enum(Classification, "classification")
scoring_state = _pg_enum(ScoringState, "scoring_state")
anomaly_kind = _pg_enum(AnomalyKind, "anomaly_kind")
direction = _pg_enum(Direction, "direction")


def _point() -> Geography:
    # Spatial indexes are declared explicitly (shops_location_gix) rather than auto-created.
    return Geography(geometry_type="POINT", srid=4326, spatial_index=False)


TZ = DateTime(timezone=True)
NOW = text("now()")

areas = Table(
    "areas",
    metadata,
    Column("id", Text, primary_key=True),
    Column("name", Text, nullable=False),
    Column("timezone", Text, nullable=False),
    Column("center", _point(), nullable=False),
    Column(
        "quiet_start",
        Time,
        nullable=False,
        server_default=text("'22:00:00'::time without time zone"),
    ),
    Column(
        "quiet_end", Time, nullable=False, server_default=text("'07:00:00'::time without time zone")
    ),
    Column("created_at", TZ, nullable=False, server_default=NOW),
)

products = Table(
    "products",
    metadata,
    Column("id", Text, primary_key=True),
    Column("name", Text, nullable=False),
    Column("base_unit", base_unit, nullable=False),
    Column("category", Text, nullable=False),
    Column("default_half_life_h", Integer, nullable=False),
    Column("half_life_h", Integer, nullable=False),
    Column("abs_min", Numeric(12, 2), nullable=False),
    Column("abs_max", Numeric(12, 2), nullable=False),
    Column("active", Boolean, nullable=False, server_default=text("true")),
    Column("sort_order", Integer, nullable=False, server_default=text("0")),
    CheckConstraint("abs_max > abs_min", name="products_check"),
)

product_aliases = Table(
    "product_aliases",
    metadata,
    Column("alias", Text, primary_key=True),
    Column("product_id", Text, ForeignKey("products.id"), nullable=False),
    Column("lang", Text),
)

shops = Table(
    "shops",
    metadata,
    Column("id", Text, primary_key=True),
    Column("name", Text, nullable=False),
    Column("area_id", Text, ForeignKey("areas.id"), nullable=False),
    Column("location", _point(), nullable=False),
    Column("address", Text, nullable=False, server_default=text("''::text")),
    Column("active", Boolean, nullable=False, server_default=text("true")),
    Column("created_at", TZ, nullable=False, server_default=NOW),
    Index("shops_location_gix", "location", postgresql_using="gist"),
)

reporters = Table(
    "reporters",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("device_id", Text, nullable=False, unique=True),
    Column("alpha", REAL, nullable=False, server_default=text("1")),
    Column("beta", REAL, nullable=False, server_default=text("1")),
    Column("reports_total", Integer, nullable=False, server_default=text("0")),
    Column("banned", Boolean, nullable=False, server_default=text("false")),
    Column("created_at", TZ, nullable=False),
    Column("last_seen_at", TZ),
)

# Immutable raw reports, partitioned by month on received_at (spm_ensure_report_partitions).
reports = Table(
    "reports",
    metadata,
    Column("id", UUID(as_uuid=True), nullable=False),
    Column("received_at", TZ, nullable=False),
    Column("reporter_id", UUID(as_uuid=True), nullable=False),
    Column("client_report_id", UUID(as_uuid=True), nullable=False),
    Column("product_id", Text, nullable=False),
    Column("shop_id", Text, nullable=False),
    Column("area_id", Text, nullable=False),
    Column("raw_product_text", Text),
    Column("raw_price", Numeric(12, 2), nullable=False),
    Column("raw_quantity", Numeric(12, 3), nullable=False),
    Column("raw_unit", Text, nullable=False),
    Column("norm_price", Numeric(14, 4), nullable=False),
    Column("base_quantity", Numeric(12, 4), nullable=False),
    Column("observed_at", TZ, nullable=False),
    Column("location", _point()),
    Column("location_accuracy_m", REAL),
    Column("distance_to_shop_m", REAL),
    Column("location_verified", Boolean, nullable=False),
    Column("ip_hash", Text),
    Column("receipt_key", Text),
    PrimaryKeyConstraint("id", "received_at"),
    Index(
        "reports_dupe_idx",
        "reporter_id",
        "product_id",
        "shop_id",
        literal_column("observed_at DESC"),
    ),
    postgresql_partition_by="RANGE (received_at)",
)

report_idempotency = Table(
    "report_idempotency",
    metadata,
    Column("reporter_id", UUID(as_uuid=True), nullable=False),
    Column("client_report_id", UUID(as_uuid=True), nullable=False),
    Column("report_id", UUID(as_uuid=True), nullable=False),
    Column("received_at", TZ, nullable=False),
    PrimaryKeyConstraint("reporter_id", "client_report_id"),
)

# Mutable scoring view, denormalized so every statistics query hits only this table.
report_assessments = Table(
    "report_assessments",
    metadata,
    Column("report_id", UUID(as_uuid=True), primary_key=True),
    Column("received_at", TZ, nullable=False),
    Column("reporter_id", UUID(as_uuid=True), nullable=False),
    Column("product_id", Text, nullable=False),
    Column("shop_id", Text, nullable=False),
    Column("area_id", Text, nullable=False),
    Column("norm_price", Numeric(14, 4), nullable=False),
    Column("observed_at", TZ, nullable=False),
    Column("location_verified", Boolean, nullable=False),
    Column("ip_hash", Text),
    Column(
        "scoring_state",
        scoring_state,
        nullable=False,
        server_default=text("'queued'::scoring_state"),
    ),
    Column("classification", classification),
    Column("direction", direction),
    Column("z_shop", REAL),
    Column("z_market", REAL),
    Column("reference_price", Numeric(14, 4)),
    Column("insufficient_data", Boolean, nullable=False, server_default=text("false")),
    Column("reason", Text),
    Column("trust_settled", Boolean, nullable=False, server_default=text("false")),
    Column("resolved_by", UUID(as_uuid=True)),
    Column("scored_at", TZ),
    Column("updated_at", TZ, nullable=False, server_default=NOW),
)
Index(
    "ra_area_idx",
    report_assessments.c.product_id,
    report_assessments.c.area_id,
    report_assessments.c.observed_at.desc(),
    postgresql_include=[
        "norm_price",
        "classification",
        "shop_id",
        "reporter_id",
        "location_verified",
    ],
)
Index(
    "ra_shop_idx",
    report_assessments.c.product_id,
    report_assessments.c.shop_id,
    report_assessments.c.observed_at.desc(),
)
Index(
    "ra_pending_idx",
    report_assessments.c.product_id,
    report_assessments.c.shop_id,
    postgresql_where=text("classification = 'PENDING'::classification"),
)

anomalies = Table(
    "anomalies",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("kind", anomaly_kind, nullable=False),
    Column("direction", direction),
    Column("product_id", Text, nullable=False),
    Column("area_id", Text, nullable=False),
    Column("shop_id", Text),
    Column("observed_price", Numeric(14, 4)),
    Column("reference_price", Numeric(14, 4)),
    Column("magnitude_pct", REAL),
    Column("report_ids", ARRAY(UUID(as_uuid=True)), nullable=False),
    Column("status", Text, nullable=False, server_default=text("'open'::text")),
    Column("dedup_key", Text, nullable=False),
    Column("detected_at", TZ, nullable=False),
    Column("resolved_at", TZ),
    CheckConstraint("status IN ('open','resolved')", name="anomalies_status_check"),
)
Index(
    "anomalies_open_dedup",
    anomalies.c.dedup_key,
    unique=True,
    postgresql_where=text("status = 'open'::text"),
)
Index(
    "anomalies_lookup",
    anomalies.c.product_id,
    anomalies.c.area_id,
    anomalies.c.detected_at.desc(),
)

band_state = Table(
    "band_state",
    metadata,
    Column("product_id", Text, nullable=False),
    Column("area_id", Text, nullable=False),
    Column("p25", Numeric(14, 4)),
    Column("p50", Numeric(14, 4)),
    Column("p75", Numeric(14, 4)),
    Column("mad", Numeric(14, 4)),
    Column("total_weight", REAL, nullable=False, server_default=text("0")),
    Column("n_eff", REAL, nullable=False, server_default=text("0")),
    Column("n_shops", Integer, nullable=False, server_default=text("0")),
    Column("confidence", Text, nullable=False, server_default=text("'stale'::text")),
    Column("newest_obs_at", TZ),
    Column("mean_age_h", REAL),
    Column("half_life_h", Integer, nullable=False),
    Column("rebase_at", TZ),
    Column("computed_at", TZ, nullable=False),
    PrimaryKeyConstraint("product_id", "area_id"),
)

shop_prices = Table(
    "shop_prices",
    metadata,
    Column("product_id", Text, nullable=False),
    Column("shop_id", Text, nullable=False),
    Column("area_id", Text, nullable=False),
    Column("price", Numeric(14, 4), nullable=False),
    Column("verified", Boolean, nullable=False),
    Column("support_weight", REAL, nullable=False),
    Column("n_reports", Integer, nullable=False),
    Column("last_observed_at", TZ, nullable=False),
    Column("verified_at", TZ),
    Column("updated_at", TZ, nullable=False),
    PrimaryKeyConstraint("product_id", "shop_id"),
)

price_stats_daily = Table(
    "price_stats_daily",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("product_id", Text, nullable=False),
    Column("area_id", Text, nullable=False),
    Column("shop_id", Text),
    Column("day", Date, nullable=False),
    Column("p25", Numeric(14, 4)),
    Column("p50", Numeric(14, 4)),
    Column("p75", Numeric(14, 4)),
    Column("mad", Numeric(14, 4)),
    Column("n_reports", Integer, nullable=False),
    Column("total_weight", REAL, nullable=False),
    Column("final", Boolean, nullable=False, server_default=text("false")),
)
Index(
    "psd_uniq",
    price_stats_daily.c.product_id,
    price_stats_daily.c.area_id,
    func.coalesce(price_stats_daily.c.shop_id, literal_column("'*'::text")),
    price_stats_daily.c.day,
    unique=True,
)

price_checks = Table(
    "price_checks",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("created_at", TZ, nullable=False),
    Column("reporter_id", UUID(as_uuid=True)),
    Column("product_id", Text, nullable=False),
    Column("area_id", Text, nullable=False),
    Column("shop_id", Text),
    Column("quoted_norm_price", Numeric(14, 4), nullable=False),
    Column("base_quantity", Numeric(12, 4), nullable=False),
    Column("verdict", Text, nullable=False),
    Column("band_p50", Numeric(14, 4)),
    Column("cheapest_norm_price", Numeric(14, 4)),
    Column("potential_saving", Numeric(14, 4)),
)
Index("price_checks_time", price_checks.c.created_at.desc())

subscriptions = Table(
    "subscriptions",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("reporter_id", UUID(as_uuid=True), ForeignKey("reporters.id"), nullable=False),
    Column("area_id", Text, ForeignKey("areas.id"), nullable=False),
    Column("product_id", Text, ForeignKey("products.id")),  # NULL = all products
    Column("kinds", ARRAY(Text), nullable=False),
    Column("channels", ARRAY(Text), nullable=False),
    Column("email", Text),
    Column("webpush", JSONB),
    Column("active", Boolean, nullable=False, server_default=text("true")),
    Column("created_at", TZ, nullable=False),
)
Index(
    "subs_lookup",
    subscriptions.c.area_id,
    subscriptions.c.product_id,
    postgresql_where=text("active"),
)

digests = Table(
    "digests",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("area_id", Text, nullable=False),
    Column("day", Date, nullable=False),
    Column("content", JSONB, nullable=False),
    Column("created_at", TZ, nullable=False),
    UniqueConstraint("area_id", "day", name="digests_area_id_day_key"),
)

alerts = Table(
    "alerts",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("reporter_id", UUID(as_uuid=True), nullable=False),
    Column("subscription_id", UUID(as_uuid=True)),
    Column("anomaly_id", UUID(as_uuid=True)),
    Column("digest_id", UUID(as_uuid=True)),
    Column("kind", Text, nullable=False),
    Column("title", Text, nullable=False),
    Column("body", Text, nullable=False),
    Column("data", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("created_at", TZ, nullable=False),
    Column("read_at", TZ),
    Column("delivered", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
)
Index("alerts_inbox", alerts.c.reporter_id, alerts.c.created_at.desc())

outbox = Table(
    "outbox",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("event_id", UUID(as_uuid=True), nullable=False, unique=True),
    Column("event_type", Text, nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("created_at", TZ, nullable=False),
    Column("published_at", TZ),
    Column("attempts", Integer, nullable=False, server_default=text("0")),
)
Index(
    "outbox_unpublished",
    outbox.c.created_at,
    postgresql_where=text("published_at IS NULL"),
)

processed_events = Table(
    "processed_events",
    metadata,
    Column("consumer", Text, nullable=False),
    Column("event_id", UUID(as_uuid=True), nullable=False),
    Column("processed_at", TZ, nullable=False),
    PrimaryKeyConstraint("consumer", "event_id"),
)

# Objects in the database that this metadata intentionally doesn't describe.
_UNMANAGED_TABLES = {"spatial_ref_sys", "alembic_version"}


def include_name(
    name: str | None,
    type_: Literal[
        "schema", "table", "column", "index", "unique_constraint", "foreign_key_constraint"
    ],
    parent_names: object,
) -> bool:
    """Alembic filter: skip PostGIS tables and report partitions (managed by SQL, not metadata)."""
    if type_ == "table" and name is not None:
        return not (
            name in _UNMANAGED_TABLES or name.startswith("reports_y") or name == "reports_default"
        )
    return True
