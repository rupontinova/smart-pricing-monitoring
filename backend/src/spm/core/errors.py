"""Domain errors: one subclass per code in the error code registry (MODULES.md §2.5).

The API turns any ``SpmError`` into an RFC 9457 problem response; ``extra`` becomes extra members
of the problem body (e.g. ``suggestions``, ``hints``, ``distance_m``).
"""

from typing import ClassVar


class SpmError(Exception):
    """Base class for every expected, user-facing error."""

    code: ClassVar[str] = "INTERNAL"
    status: ClassVar[int] = 500
    title: ClassVar[str] = "Internal error"

    def __init__(self, detail: str = "", **extra: object) -> None:
        super().__init__(detail or self.title)
        self.detail = detail
        self.extra: dict[str, object] = extra


class ValidationError(SpmError):
    code = "VALIDATION_ERROR"
    status = 422
    title = "Request validation failed"


class UnknownProduct(SpmError):
    code = "UNKNOWN_PRODUCT"
    status = 422
    title = "Unknown product"


class UnitMismatch(SpmError):
    code = "UNIT_MISMATCH"
    status = 422
    title = "Unit does not match the product"


class UnknownUnit(SpmError):
    code = "UNKNOWN_UNIT"
    status = 422
    title = "Unknown unit"


class QuantityOutOfRange(SpmError):
    code = "QUANTITY_OUT_OF_RANGE"
    status = 422
    title = "Quantity is outside the allowed range"


class ObservedAtOutOfRange(SpmError):
    code = "OBSERVED_AT_OUT_OF_RANGE"
    status = 422
    title = "Observation time is outside the allowed range"


class UnknownShop(SpmError):
    code = "UNKNOWN_SHOP"
    status = 422
    title = "Unknown shop"


class PriceOutOfBounds(SpmError):
    code = "PRICE_OUT_OF_BOUNDS"
    status = 422
    title = "Price is outside the plausible range"


class TooFarFromShop(SpmError):
    code = "TOO_FAR_FROM_SHOP"
    status = 422
    title = "Too far from the shop"


class RateLimited(SpmError):
    code = "RATE_LIMITED"
    status = 429
    title = "Too many requests"

    def __init__(self, detail: str = "", *, retry_after_s: int, **extra: object) -> None:
        super().__init__(detail, retry_after_s=retry_after_s, **extra)
        self.retry_after_s = retry_after_s


class Unauthorized(SpmError):
    code = "UNAUTHORIZED"
    status = 401
    title = "Unauthorized"


class Forbidden(SpmError):
    code = "FORBIDDEN"
    status = 403
    title = "Forbidden"


class NotFound(SpmError):
    code = "NOT_FOUND"
    status = 404
    title = "Not found"


class Internal(SpmError):
    code = "INTERNAL"
    status = 500
    title = "Internal server error"


class BadRequest(SpmError):
    code = "BAD_REQUEST"
    status = 400
    title = "Bad request"


class MethodNotAllowed(SpmError):
    code = "METHOD_NOT_ALLOWED"
    status = 405
    title = "Method not allowed"


class PayloadTooLarge(SpmError):
    code = "PAYLOAD_TOO_LARGE"
    status = 413
    title = "Request body too large"


class ServiceUnavailable(SpmError):
    code = "SERVICE_UNAVAILABLE"
    status = 503
    title = "Service unavailable"


ERROR_TYPES: tuple[type[SpmError], ...] = (
    ValidationError,
    UnknownProduct,
    UnitMismatch,
    UnknownUnit,
    QuantityOutOfRange,
    ObservedAtOutOfRange,
    UnknownShop,
    PriceOutOfBounds,
    TooFarFromShop,
    RateLimited,
    Unauthorized,
    Forbidden,
    NotFound,
    Internal,
    BadRequest,
    MethodNotAllowed,
    PayloadTooLarge,
    ServiceUnavailable,
)
