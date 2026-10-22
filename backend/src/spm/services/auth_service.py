"""Anonymous device identity: one reporter per device, identified by a JWT."""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from spm.adapters.tokens import JwtTokens
from spm.constants import AUTH_RATE_LIMIT_PER_HOUR_PER_IP, TRUST_PRIOR_ALPHA, TRUST_PRIOR_BETA
from spm.core.errors import Forbidden, RateLimited, Unauthorized
from spm.core.models import Reporter
from spm.ports import Clock, IdGenerator, RateLimiter, ReporterRepo


class ReportersUnitOfWork(Protocol):
    @property
    def reporters(self) -> ReporterRepo: ...
    async def commit(self) -> None: ...


@dataclass(frozen=True, slots=True)
class DeviceToken:
    token: str
    reporter_id: UUID
    expires_at: datetime


class AuthService:
    def __init__(
        self,
        uow_factory: Callable[[], AbstractAsyncContextManager[ReportersUnitOfWork]],
        tokens: JwtTokens,
        ids: IdGenerator,
        clock: Clock,
        rate_limiter: RateLimiter | None,
    ) -> None:
        self._uow_factory = uow_factory
        self._tokens = tokens
        self._ids = ids
        self._clock = clock
        self._rate_limiter = rate_limiter

    async def reporter_for_device(self, device_id: str) -> Reporter:
        """The device's reporter, created on first use (same device -> same reporter)."""
        async with self._uow_factory() as uow:
            existing = await uow.reporters.get_by_device(device_id)
            if existing is not None:
                return existing
            await uow.reporters.create(
                Reporter(
                    self._ids.new(),
                    device_id,
                    TRUST_PRIOR_ALPHA,
                    TRUST_PRIOR_BETA,
                    0,
                    False,
                    self._clock.now(),
                )
            )
            await uow.commit()
            # Re-read: a concurrent first login may have created it (create is a no-op then).
            created = await uow.reporters.get_by_device(device_id)
        assert created is not None
        return created

    async def register_device(self, device_id: str, ip_hash: str) -> DeviceToken:
        if self._rate_limiter is not None:
            allowed, retry_after = await self._rate_limiter.hit(
                "auth", ip_hash, AUTH_RATE_LIMIT_PER_HOUR_PER_IP, 3600
            )
            if not allowed:
                raise RateLimited("Too many sign-ins from this network.", retry_after_s=retry_after)
        reporter = await self.reporter_for_device(device_id)
        if reporter.banned:
            raise Forbidden("This device is blocked.")
        token, expires_at = self._tokens.issue(reporter.id, reporter.device_id)
        return DeviceToken(token, reporter.id, expires_at)

    async def authenticate(self, token: str) -> Reporter:
        """Token -> reporter (401 if invalid or unknown, 403 if banned); records last_seen."""
        claims = self._tokens.verify(token)
        async with self._uow_factory() as uow:
            reporter = await uow.reporters.get(claims.reporter_id)
            if reporter is None or reporter.device_id != claims.device_id:
                raise Unauthorized("Unknown reporter.")
            if reporter.banned:
                raise Forbidden("This device is blocked.")
            await uow.reporters.touch(reporter.id, self._clock.now())  # at most hourly
            await uow.commit()
        return reporter
