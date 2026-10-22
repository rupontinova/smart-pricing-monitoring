"""Initial schema (MODULES.md M03).

Revision ID: 0001
Revises:
Create Date: 2026-10-22
"""

import re
from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Months of report partitions created ahead of the current one (current month through +12).
PARTITION_MONTHS_AHEAD = 12

SCHEMA = r"""
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE TYPE base_unit       AS ENUM ('kg','l','dozen');
CREATE TYPE classification  AS ENUM ('NORMAL','PENDING','SHOP_OUTLIER','MARKET_SHIFT','BAD_REPORT','EXPIRED');
CREATE TYPE scoring_state   AS ENUM ('queued','scored','failed');
CREATE TYPE anomaly_kind    AS ENUM ('SHOP_OUTLIER','MARKET_SHIFT','BAD_REPORT');
CREATE TYPE direction       AS ENUM ('up','down');

CREATE TABLE areas (
  id text PRIMARY KEY, name text NOT NULL, timezone text NOT NULL,
  center geography(Point,4326) NOT NULL,
  quiet_start time NOT NULL DEFAULT '22:00', quiet_end time NOT NULL DEFAULT '07:00',
  created_at timestamptz NOT NULL DEFAULT now());

CREATE TABLE products (
  id text PRIMARY KEY, name text NOT NULL, base_unit base_unit NOT NULL, category text NOT NULL,
  default_half_life_h int NOT NULL, half_life_h int NOT NULL,
  abs_min numeric(12,2) NOT NULL, abs_max numeric(12,2) NOT NULL CHECK (abs_max > abs_min),
  active boolean NOT NULL DEFAULT true, sort_order int NOT NULL DEFAULT 0);

CREATE TABLE product_aliases (alias text PRIMARY KEY, product_id text NOT NULL REFERENCES products(id), lang text);

CREATE TABLE shops (
  id text PRIMARY KEY, name text NOT NULL, area_id text NOT NULL REFERENCES areas(id),
  location geography(Point,4326) NOT NULL, address text NOT NULL DEFAULT '',
  active boolean NOT NULL DEFAULT true, created_at timestamptz NOT NULL DEFAULT now());
CREATE INDEX shops_location_gix ON shops USING gist(location);

CREATE TABLE reporters (
  id uuid PRIMARY KEY, device_id text NOT NULL UNIQUE,
  alpha real NOT NULL DEFAULT 1, beta real NOT NULL DEFAULT 1,
  reports_total int NOT NULL DEFAULT 0, banned boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL, last_seen_at timestamptz);

-- Immutable raw reports, partitioned by month on received_at
CREATE TABLE reports (
  id uuid NOT NULL, received_at timestamptz NOT NULL,
  reporter_id uuid NOT NULL, client_report_id uuid NOT NULL,
  product_id text NOT NULL, shop_id text NOT NULL, area_id text NOT NULL,
  raw_product_text text, raw_price numeric(12,2) NOT NULL, raw_quantity numeric(12,3) NOT NULL, raw_unit text NOT NULL,
  norm_price numeric(14,4) NOT NULL, base_quantity numeric(12,4) NOT NULL,
  observed_at timestamptz NOT NULL,
  location geography(Point,4326), location_accuracy_m real, distance_to_shop_m real,
  location_verified boolean NOT NULL, ip_hash text, receipt_key text,
  PRIMARY KEY (id, received_at)
) PARTITION BY RANGE (received_at);
CREATE INDEX reports_dupe_idx ON reports (reporter_id, product_id, shop_id, observed_at DESC);
CREATE TABLE reports_default PARTITION OF reports DEFAULT;

CREATE TABLE report_idempotency (
  reporter_id uuid NOT NULL, client_report_id uuid NOT NULL, report_id uuid NOT NULL,
  received_at timestamptz NOT NULL, PRIMARY KEY (reporter_id, client_report_id));

-- Mutable scoring view (denormalized so that every statistics query hits only this table)
CREATE TABLE report_assessments (
  report_id uuid PRIMARY KEY, received_at timestamptz NOT NULL,
  reporter_id uuid NOT NULL, product_id text NOT NULL, shop_id text NOT NULL, area_id text NOT NULL,
  norm_price numeric(14,4) NOT NULL, observed_at timestamptz NOT NULL,
  location_verified boolean NOT NULL, ip_hash text,
  scoring_state scoring_state NOT NULL DEFAULT 'queued',
  classification classification, direction direction,
  z_shop real, z_market real, reference_price numeric(14,4),
  insufficient_data boolean NOT NULL DEFAULT false, reason text,
  trust_settled boolean NOT NULL DEFAULT false, resolved_by uuid,
  scored_at timestamptz, updated_at timestamptz NOT NULL DEFAULT now());
CREATE INDEX ra_area_idx ON report_assessments (product_id, area_id, observed_at DESC)
  INCLUDE (norm_price, classification, shop_id, reporter_id, location_verified);
CREATE INDEX ra_shop_idx ON report_assessments (product_id, shop_id, observed_at DESC);
CREATE INDEX ra_pending_idx ON report_assessments (product_id, shop_id) WHERE classification = 'PENDING';

CREATE TABLE anomalies (
  id uuid PRIMARY KEY, kind anomaly_kind NOT NULL, direction direction,
  product_id text NOT NULL, area_id text NOT NULL, shop_id text,
  observed_price numeric(14,4), reference_price numeric(14,4), magnitude_pct real,
  report_ids uuid[] NOT NULL, status text NOT NULL DEFAULT 'open' CHECK (status IN ('open','resolved')),
  dedup_key text NOT NULL, detected_at timestamptz NOT NULL, resolved_at timestamptz);
CREATE UNIQUE INDEX anomalies_open_dedup ON anomalies (dedup_key) WHERE status = 'open';
CREATE INDEX anomalies_lookup ON anomalies (product_id, area_id, detected_at DESC);

CREATE TABLE band_state (
  product_id text NOT NULL, area_id text NOT NULL,
  p25 numeric(14,4), p50 numeric(14,4), p75 numeric(14,4), mad numeric(14,4),
  total_weight real NOT NULL DEFAULT 0, n_eff real NOT NULL DEFAULT 0, n_shops int NOT NULL DEFAULT 0,
  confidence text NOT NULL DEFAULT 'stale', newest_obs_at timestamptz, mean_age_h real,
  half_life_h int NOT NULL, rebase_at timestamptz, computed_at timestamptz NOT NULL,
  PRIMARY KEY (product_id, area_id));

CREATE TABLE shop_prices (
  product_id text NOT NULL, shop_id text NOT NULL, area_id text NOT NULL,
  price numeric(14,4) NOT NULL, verified boolean NOT NULL, support_weight real NOT NULL,
  n_reports int NOT NULL, last_observed_at timestamptz NOT NULL, verified_at timestamptz,
  updated_at timestamptz NOT NULL, PRIMARY KEY (product_id, shop_id));

CREATE TABLE price_stats_daily (
  id bigserial PRIMARY KEY, product_id text NOT NULL, area_id text NOT NULL, shop_id text,
  day date NOT NULL, p25 numeric(14,4), p50 numeric(14,4), p75 numeric(14,4), mad numeric(14,4),
  n_reports int NOT NULL, total_weight real NOT NULL, final boolean NOT NULL DEFAULT false);
CREATE UNIQUE INDEX psd_uniq ON price_stats_daily (product_id, area_id, COALESCE(shop_id,'*'), day);

CREATE TABLE price_checks (
  id uuid PRIMARY KEY, created_at timestamptz NOT NULL, reporter_id uuid,
  product_id text NOT NULL, area_id text NOT NULL, shop_id text,
  quoted_norm_price numeric(14,4) NOT NULL, base_quantity numeric(12,4) NOT NULL,
  verdict text NOT NULL, band_p50 numeric(14,4), cheapest_norm_price numeric(14,4), potential_saving numeric(14,4));
CREATE INDEX price_checks_time ON price_checks (created_at DESC);

CREATE TABLE subscriptions (
  id uuid PRIMARY KEY, reporter_id uuid NOT NULL REFERENCES reporters(id),
  area_id text NOT NULL REFERENCES areas(id), product_id text REFERENCES products(id),  -- NULL = all products
  kinds text[] NOT NULL, channels text[] NOT NULL, email text, webpush jsonb,
  active boolean NOT NULL DEFAULT true, created_at timestamptz NOT NULL);
CREATE INDEX subs_lookup ON subscriptions (area_id, product_id) WHERE active;

CREATE TABLE digests (
  id uuid PRIMARY KEY, area_id text NOT NULL, day date NOT NULL, content jsonb NOT NULL,
  created_at timestamptz NOT NULL, UNIQUE (area_id, day));

CREATE TABLE alerts (
  id uuid PRIMARY KEY, reporter_id uuid NOT NULL, subscription_id uuid,
  anomaly_id uuid, digest_id uuid, kind text NOT NULL, title text NOT NULL, body text NOT NULL,
  data jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL, read_at timestamptz,
  delivered jsonb NOT NULL DEFAULT '{}');
CREATE INDEX alerts_inbox ON alerts (reporter_id, created_at DESC);

CREATE TABLE outbox (
  id bigserial PRIMARY KEY, event_id uuid NOT NULL UNIQUE, event_type text NOT NULL,
  payload jsonb NOT NULL, created_at timestamptz NOT NULL, published_at timestamptz, attempts int NOT NULL DEFAULT 0);
CREATE INDEX outbox_unpublished ON outbox (created_at) WHERE published_at IS NULL;

CREATE TABLE processed_events (
  consumer text NOT NULL, event_id uuid NOT NULL, processed_at timestamptz NOT NULL,
  PRIMARY KEY (consumer, event_id));
"""

# Creates any missing monthly partitions reports_yYYYYmMM from the current (UTC) month through
# +months_ahead. Rows already sitting in reports_default for a new month are moved into the new
# partition first; otherwise attaching it would fail. Returns the number of partitions created.
ENSURE_PARTITIONS_FN = r"""
CREATE FUNCTION spm_ensure_report_partitions(months_ahead int) RETURNS int
LANGUAGE plpgsql AS $$
DECLARE
  first_month date := date_trunc('month', now() AT TIME ZONE 'UTC')::date;
  month_start date;
  lo timestamptz;
  hi timestamptz;
  part text;
  created int := 0;
BEGIN
  IF months_ahead < 0 THEN
    RAISE EXCEPTION 'months_ahead must be >= 0, got %', months_ahead;
  END IF;
  -- Serialize concurrent callers (migration vs. nightly job).
  PERFORM pg_advisory_xact_lock(hashtextextended('spm_ensure_report_partitions', 0));
  FOR i IN 0..months_ahead LOOP
    month_start := (first_month + make_interval(months => i))::date;
    part := format('reports_y%sm%s', to_char(month_start, 'YYYY'), to_char(month_start, 'MM'));
    CONTINUE WHEN to_regclass(format('public.%I', part)) IS NOT NULL;

    lo := month_start::timestamp AT TIME ZONE 'UTC';
    hi := (month_start + interval '1 month')::timestamp AT TIME ZONE 'UTC';
    EXECUTE format('CREATE TABLE public.%I (LIKE public.reports INCLUDING DEFAULTS INCLUDING CONSTRAINTS)', part);
    EXECUTE format(
      'WITH moved AS (DELETE FROM public.reports_default WHERE received_at >= %L AND received_at < %L RETURNING *)
       INSERT INTO public.%I SELECT * FROM moved', lo, hi, part);
    EXECUTE format('ALTER TABLE public.reports ATTACH PARTITION public.%I FOR VALUES FROM (%L) TO (%L)',
                   part, lo, hi);
    created := created + 1;
  END LOOP;
  RETURN created;
END
$$;
"""

DROP_ALL = r"""
DROP FUNCTION IF EXISTS spm_ensure_report_partitions(int);
DROP TABLE IF EXISTS processed_events, outbox, alerts, digests, subscriptions, price_checks,
  price_stats_daily, shop_prices, band_state, anomalies, report_assessments, report_idempotency,
  reports, reporters, shops, product_aliases, products, areas CASCADE;
DROP TYPE IF EXISTS direction, anomaly_kind, scoring_state, classification, base_unit;
"""


def _statements(sql: str) -> list[str]:
    """Split a script into statements (asyncpg prepares each one, so they can't be batched).

    Only valid for scripts without semicolons inside literals or bodies; the plpgsql function is
    executed on its own.
    """
    return [stmt.strip() for stmt in re.split(r";\s*\n", sql) if stmt.strip()]


def upgrade() -> None:
    for stmt in _statements(SCHEMA):
        op.execute(stmt)
    op.execute(ENSURE_PARTITIONS_FN)
    op.execute(f"SELECT spm_ensure_report_partitions({PARTITION_MONTHS_AHEAD})")


def downgrade() -> None:
    # The postgis extension is left in place: it is shared and created by infra/local/db-init.sql.
    for stmt in _statements(DROP_ALL):
        op.execute(stmt)
