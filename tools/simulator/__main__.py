"""``python -m simulator [SCENARIO ...] [--all] [--mode inprocess|http]`` (``make sim``).

In-process runs use their own database (``spm_sim`` on the ``SPM_DATABASE_URL`` server, recreated
for every scenario) and Redis DB 14, so they never touch dev or test data.
"""

import argparse
import asyncio
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

import redis.asyncio as aioredis
from sqlalchemy import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from simulator.evaluate import OUT_DIR, Evaluation, evaluate, write_report
from simulator.generator import SimStream, generate
from simulator.runner import Progress, RunResult, run_http, run_inprocess
from simulator.scenario import load_scenario, scenario_names
from spm.adapters.catalog_yaml import load_catalog
from spm.adapters.postgres.catalog_seed import seed_catalog
from spm.adapters.postgres.db import make_session_factory
from spm.adapters.postgres.migrate import recreate_database, upgrade
from spm.adapters.redis.hot_cache import RedisHotCache
from spm.config import Settings, get_settings
from spm.logging import configure_logging

SIM_DATABASE = "spm_sim"
SIM_REDIS_DB = 14


def _sim_urls(
    settings: Settings, database_url: str | None, redis_url: str | None
) -> tuple[str, str]:
    db = database_url or make_url(settings.database_url).set(
        database=SIM_DATABASE
    ).render_as_string(hide_password=False)
    redis = redis_url or settings.redis_url.rsplit("/", 1)[0] + f"/{SIM_REDIS_DB}"
    return db, redis


def _progress(name: str) -> Progress:
    def show(done: int, total: int) -> None:
        if done == total or done % 100 == 0:
            print(
                f"\r  {name}: {done}/{total} reports", end="" if done < total else "\n", flush=True
            )

    return show


async def _inprocess(
    stream: SimStream, settings: Settings, db_url: str, redis_url: str
) -> RunResult:
    engine = create_async_engine(db_url, pool_size=5)
    redis = aioredis.from_url(redis_url, decode_responses=True)
    try:
        await redis.flushdb()
        sf = make_session_factory(engine)
        await seed_catalog(load_catalog(), sf, RedisHotCache(redis))
        return await run_inprocess(stream, sf, redis, settings, _progress(stream.scenario.name))
    finally:
        await redis.aclose()
        await engine.dispose()


async def _http(stream: SimStream, settings: Settings, base_url: str, admin_key: str) -> RunResult:
    engine = create_async_engine(settings.database_url)
    try:
        return await run_http(
            stream,
            base_url,
            admin_key,
            make_session_factory(engine),
            progress=_progress(stream.scenario.name),
        )
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="simulator", description=__doc__)
    parser.add_argument(
        "scenarios", nargs="*", help=f"names or YAML paths ({', '.join(scenario_names())})"
    )
    parser.add_argument("--all", action="store_true", help="run every scenario in scenarios/")
    parser.add_argument("--mode", choices=("inprocess", "http"), default="inprocess")
    parser.add_argument("--base-url", default="http://localhost:8000", help="http mode")
    parser.add_argument("--admin-key", help="http mode (default: SPM_ADMIN_API_KEY)")
    parser.add_argument("--database-url", help="in-process database (recreated!)")
    parser.add_argument("--redis-url", help="in-process Redis (flushed!)")
    parser.add_argument("--days", type=int, help="override the scenario length")
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    args = parser.parse_args(argv)

    names = scenario_names() if args.all else args.scenarios
    if not names:
        parser.error("name a scenario or pass --all")
    settings = get_settings()
    configure_logging(settings, logging.WARNING)
    db_url, redis_url = _sim_urls(settings, args.database_url, args.redis_url)
    catalog = load_catalog()

    failed: list[str] = []
    for name in names:
        scenario = load_scenario(name)
        if args.days:
            scenario = scenario.model_copy(update={"days": args.days})
        stream = generate(scenario, catalog)
        n = len(stream.reports)
        print(f"{scenario.name}: {n} reports over {scenario.days} days ({args.mode})")
        if args.mode == "inprocess":
            asyncio.run(recreate_database(db_url))
            upgrade("head", db_url)
            run = asyncio.run(_inprocess(stream, settings, db_url, redis_url))
        else:
            run = asyncio.run(
                _http(
                    stream,
                    settings,
                    args.base_url,
                    args.admin_key or settings.admin_api_key.get_secret_value(),
                )
            )
        ev: Evaluation = evaluate(run)
        path = write_report(ev, datetime.now(UTC), args.out)
        for t in ev.targets:
            mark = "PASS" if t.passed else "FAIL"
            print(f"  [{mark}] {t.description} {t.threshold:g}: {t.value}")
        print(f"  report: {path}")
        if not ev.passed:
            failed.append(scenario.name)
    if failed:
        print(f"Targets missed: {', '.join(failed)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
