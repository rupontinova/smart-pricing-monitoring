"""Device tokens: HS256 JWTs with ``sub`` (reporter id), ``did`` (device id), ``iat``, ``exp``
and ``iss``.

Expiry is checked against the injected clock (not the wall clock), so tests can move time.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt

from spm.core.errors import Unauthorized
from spm.ports import Clock

ISSUER = "spm"
ALGORITHM = "HS256"


@dataclass(frozen=True, slots=True)
class TokenClaims:
    reporter_id: UUID
    device_id: str
    issued_at: datetime
    expires_at: datetime


class JwtTokens:
    def __init__(self, secret: str, ttl_d: int, clock: Clock) -> None:
        self._secret = secret
        self._ttl = timedelta(days=ttl_d)
        self._clock = clock

    def issue(self, reporter_id: UUID, device_id: str) -> tuple[str, datetime]:
        now = self._clock.now().replace(microsecond=0)
        expires_at = now + self._ttl
        claims = {
            "sub": str(reporter_id),
            "did": device_id,
            "iat": int(now.timestamp()),
            "exp": int(expires_at.timestamp()),
            "iss": ISSUER,
        }
        return jwt.encode(claims, self._secret, algorithm=ALGORITHM), expires_at

    def verify(self, token: str) -> TokenClaims:
        try:
            claims = jwt.decode(
                token,
                self._secret,
                algorithms=[ALGORITHM],
                issuer=ISSUER,
                options={"require": ["sub", "did", "iat", "exp", "iss"], "verify_exp": False},
            )
            expires_at = datetime.fromtimestamp(int(claims["exp"]), UTC)
            claims_out = TokenClaims(
                reporter_id=UUID(claims["sub"]),
                device_id=str(claims["did"]),
                issued_at=datetime.fromtimestamp(int(claims["iat"]), UTC),
                expires_at=expires_at,
            )
        except (jwt.PyJWTError, ValueError, KeyError) as exc:
            raise Unauthorized("Invalid token.") from exc
        if self._clock.now() >= expires_at:
            raise Unauthorized("Token expired.")
        return claims_out
