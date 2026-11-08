"""Map botocore / credential-service failures to the error codes of scanner.md section 9."""

from __future__ import annotations

from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ReadTimeoutError,
)
from botocore.exceptions import ConnectionError as BotoConnectionError

from app.aws.credentials import CredentialError

ACCESS_DENIED = {"AccessDenied", "AccessDeniedException", "UnauthorizedOperation"}
REGION_DISABLED = {"OptInRequired", "AuthFailure"}
CREDENTIAL = {
    "UnrecognizedClientException",
    "InvalidClientTokenId",
    "ExpiredToken",
    "ExpiredTokenException",
}
THROTTLED = {
    "Throttling",
    "ThrottlingException",
    "ThrottledException",
    "RequestLimitExceeded",
    "RequestThrottled",
    "TooManyRequestsException",
    "ProvisionedThroughputExceededException",
}
UNSUPPORTED = {"UnsupportedActionException", "TypeNotFoundException"}

RETRYABLE = {
    "access_denied": False,
    "credential_error": True,
    "throttled": True,
    "endpoint_unreachable": True,
    "region_disabled": False,
    "unsupported": False,
    "internal": True,
}


class ScanError(Exception):
    """A failure with the classification the task table stores."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    @property
    def retryable(self) -> bool:
        return RETRYABLE[self.code]


def classify(exc: BaseException) -> ScanError:
    if isinstance(exc, ScanError):
        return exc
    if isinstance(exc, CredentialError):
        return ScanError("credential_error", str(exc))
    if isinstance(exc, ClientError):
        error = exc.response.get("Error", {})
        code = error.get("Code", "")
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        message = f"{code}: {error.get('Message', '')}".strip()
        if code in ACCESS_DENIED:
            return ScanError("access_denied", message)
        if code in REGION_DISABLED:
            return ScanError("region_disabled", message)
        if code in CREDENTIAL:
            return ScanError("credential_error", message)
        if code in THROTTLED or status == 429:
            return ScanError("throttled", message)
        if code in UNSUPPORTED:
            return ScanError("unsupported", message)
        return ScanError("internal", message)
    if isinstance(
        exc, EndpointConnectionError | BotoConnectionError | ConnectTimeoutError | ReadTimeoutError
    ):
        return ScanError("endpoint_unreachable", type(exc).__name__)
    if isinstance(exc, BotoCoreError):
        return ScanError("internal", type(exc).__name__)
    return ScanError("internal", f"{type(exc).__name__}: {exc}")
