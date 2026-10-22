from typing import Literal

from pydantic import BaseModel

CheckStatus = Literal["ok", "error", "skipped"]


class HealthOut(BaseModel):
    status: Literal["ok"] = "ok"
    version: str


class ReadyOut(BaseModel):
    status: Literal["ok"] = "ok"
    checks: dict[str, CheckStatus]
