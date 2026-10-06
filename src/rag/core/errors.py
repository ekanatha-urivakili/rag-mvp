class AppError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(self, message: str = "Bad request", *, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        if code:
            self.code = code


class Unauthorized(AppError):
    status_code = 401
    code = "unauthorized"

    def __init__(self, message: str = "Authentication required") -> None:
        super().__init__(message)


class Forbidden(AppError):
    status_code = 403
    code = "forbidden"

    def __init__(self, message: str = "Not allowed") -> None:
        super().__init__(message)


class NotFound(AppError):
    status_code = 404
    code = "not_found"

    def __init__(self, message: str = "Not found") -> None:
        super().__init__(message)


class Conflict(AppError):
    status_code = 409
    code = "conflict"


class PayloadTooLarge(AppError):
    status_code = 413
    code = "payload_too_large"


class UnsupportedMediaType(AppError):
    status_code = 415
    code = "unsupported_media_type"


class RateLimited(AppError):
    status_code = 429
    code = "rate_limited"

    def __init__(self, retry_after: int) -> None:
        super().__init__("Too many requests")
        self.retry_after = retry_after


class LLMUnavailable(Exception):
    """All providers in a route failed."""
