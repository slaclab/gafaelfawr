"""Exceptions for Gafaelfawr."""

from __future__ import annotations

from enum import Enum
from typing import ClassVar, Dict, List, Union

from fastapi import status

__all__ = [
    "DeserializeException",
    "DuplicateTokenNameError",
    "ErrorLocation",
    "FetchKeysException",
    "GitHubException",
    "InsufficientScopeError",
    "InvalidClientError",
    "InvalidCSRFError",
    "InvalidCursorError",
    "InvalidExpiresError",
    "InvalidGrantError",
    "InvalidIPAddressError",
    "InvalidRequestError",
    "InvalidReturnURLError",
    "InvalidScopesError",
    "InvalidTokenClaimsException",
    "InvalidTokenError",
    "KubernetesError",
    "KubernetesObjectError",
    "LDAPException",
    "MissingClaimsException",
    "NotConfiguredException",
    "OAuthError",
    "OAuthBearerError",
    "OIDCException",
    "PermissionDeniedError",
    "ProviderException",
    "UnauthorizedClientException",
    "UnknownAlgorithmException",
    "UnknownKeyIdException",
    "ValidationError",
    "VerifyTokenException",
]


class ErrorLocation(Enum):
    """Specifies the request component that triggered a `ValidationError`."""

    body = "body"
    header = "header"
    path = "path"
    query = "query"


class ValidationError(Exception):
    """Represents an input validation error.

    There is a global handler for this exception and all exceptions derived
    from it that returns an HTTP 422 status code with a body that's consistent
    with the error messages generated internally by FastAPI.  It should be
    used for input and parameter validation errors that cannot be caught by
    FastAPI for whatever reason.

    Parameters
    ----------
    message : `str`
        The error message (used as the ``msg`` key).
    location : `ErrorLocation`
        The part of the request giving rise to the error.
    field : `str`
        The field within that part of the request giving rise to the error.

    Notes
    -----
    The FastAPI body format supports returning multiple errors at a time as a
    list in the ``details`` key.  The Gafaelfawr code is not currently capable
    of diagnosing multiple errors at once, so this functionality hasn't been
    implemented.
    """

    error: ClassVar[str] = "validation_failed"
    """Used as the ``type`` field of the error message.

    Should be overridden by any subclass.
    """

    status_code: ClassVar[int] = status.HTTP_422_UNPROCESSABLE_ENTITY
    """HTTP status code for this type of validation error."""

    def __init__(
        self, message: str, location: ErrorLocation, field: str
    ) -> None:
        super().__init__(message)
        self.location = location
        self.field = field

    def to_dict(self) -> Dict[str, Union[List[str], str]]:
        """Convert the exception to a dictionary suitable for the exception.

        The return value is intended to be passed as the ``detail`` parameter
        to a `fastapi.HTTPException`.
        """
        return {
            "loc": [self.location.value, self.field],
            "msg": str(self),
            "type": self.error,
        }


class DuplicateTokenNameError(ValidationError):
    """The user tried to reuse the name of a token."""

    error = "duplicate_token_name"

    def __init__(self, message: str) -> None:
        super().__init__(message, ErrorLocation.body, "token_name")


class InvalidCSRFError(ValidationError):
    """Invalid or missing CSRF token."""

    error = "invalid_csrf"
    status_code = status.HTTP_403_FORBIDDEN

    def __init__(self, message: str) -> None:
        super().__init__(message, ErrorLocation.header, "X-CSRF-Token")


class InvalidCursorError(ValidationError):
    """The provided cursor was invalid."""

    error = "invalid_cursor"

    def __init__(self, message: str) -> None:
        super().__init__(message, ErrorLocation.query, "cursor")


class InvalidExpiresError(ValidationError):
    """The provided token expiration time was invalid."""

    error = "invalid_expires"

    def __init__(self, message: str) -> None:
        super().__init__(message, ErrorLocation.body, "expires")


class InvalidIPAddressError(ValidationError):
    """The provided IP address has invalid syntax."""

    error = "invalid_ip_address"

    def __init__(self, message: str) -> None:
        super().__init__(message, ErrorLocation.query, "ip_address")


class InvalidDelegateToError(ValidationError):
    """The ``delegate_to`` parameter was set to an invalid value."""

    error = "invalid_delegate_to"

    def __init__(self, message: str) -> None:
        super().__init__(message, ErrorLocation.query, "delegate_to")


class InvalidReturnURLError(ValidationError):
    """Client specified an unsafe return URL."""

    error = "invalid_return_url"

    def __init__(self, message: str, field: str) -> None:
        super().__init__(message, ErrorLocation.query, field)


class InvalidScopesError(ValidationError):
    """The provided token scopes are invalid or not available."""

    error = "invalid_scopes"

    def __init__(self, message: str) -> None:
        super().__init__(message, ErrorLocation.body, "scopes")


class NotFoundError(ValidationError):
    """The named resource does not exist."""

    error = "not_found"
    status_code = status.HTTP_404_NOT_FOUND


class OAuthError(Exception):
    """An OAuth-related error occurred.

    This class represents both OpenID Connect errors and OAuth 2.0 errors,
    including errors when parsing Authorization headers and bearer tokens.
    """

    error: ClassVar[str] = "invalid_request"
    """The RFC 6749 or RFC 6750 error code for this exception."""

    message: ClassVar[str] = "Unknown error"
    """The summary message to use when logging this error."""

    hide_error: ClassVar[bool] = False
    """Whether to hide the details of the error from the client."""


class InvalidClientError(OAuthError):
    """The provided client_id and client_secret could not be validated.

    This corresponds to the ``invalid_client`` error in RFC 6749: "Client
    authentication failed (e.g., unknown client, no client authentication
    included, or unsupported authentication method)."
    """

    error = "invalid_client"
    message = "Unauthorized client"


class InvalidGrantError(OAuthError):
    """The provided authorization code is not valid.

    This corresponds to the ``invalid_grant`` error in RFC 6749: "The provided
    authorization grant (e.g., authorization code, resource owner credentials)
    or refresh token is invalid, expired, revoked, does not match the
    redirection URI used in the authorization request, or was issued to
    another client."
    """

    error = "invalid_grant"
    message = "Invalid authorization code"
    hide_error = True


class UnsupportedGrantTypeError(OAuthError):
    """The grant type is not supported.

    This corresponds to the ``unsupported_grant_type`` error in RFC 6749: "The
    authorization grant type is not supported by the authorization server."
    """

    error = "unsupported_grant_type"
    message = "Unsupported grant type"


class OAuthBearerError(OAuthError):
    """An error that can be returned as a ``WWW-Authenticate`` challenge.

    Represents the subset of OAuth 2.0 errors defined in RFC 6750 as valid
    errors to return in a ``WWW-Authenticate`` header.  The string form of
    this exception is suitable for use as the ``error_description`` attribute
    of a ``WWW-Authenticate`` header.
    """

    status_code: int = status.HTTP_400_BAD_REQUEST
    """The status code to use for this HTTP error."""


class InvalidRequestError(OAuthBearerError):
    """The provided Authorization header could not be parsed.

    This corresponds to the ``invalid_request`` error in RFC 6749 and 6750:
    "The request is missing a required parameter, includes an unsupported
    parameter or parameter value, repeats the same parameter, uses more than
    one method for including an access token, or is otherwise malformed."
    """

    error = "invalid_request"
    message = "Invalid request"


class InvalidTokenError(OAuthBearerError):
    """The provided token was invalid.

    This corresponds to the ``invalid_token`` error in RFC 6750: "The access
    token provided is expired, revoked, malformed, or invalid for other
    reasons."
    """

    error = "invalid_token"
    message = "Invalid token"
    status_code = status.HTTP_401_UNAUTHORIZED


class InsufficientScopeError(OAuthBearerError):
    """The provided token does not have the right authorization scope.

    This corresponds to the ``insufficient_scope`` error in RFC 6750: "The
    request requires higher privileges than provided by the access token."
    """

    error = "insufficient_scope"
    message = "Permission denied"
    status_code = status.HTTP_403_FORBIDDEN


class DeserializeException(Exception):
    """A stored object could not be decrypted or deserialized.

    Used for data stored in the backing store, such as sessions or user
    tokens.  Should normally be treated the same as a missing object, but
    reported separately so that an error can be logged.
    """


class KubernetesError(Exception):
    """An error occurred during Kubernetes secret processing."""


class KubernetesObjectError(Exception):
    """A Kubernetes object could not be parsed."""


class NotConfiguredException(Exception):
    """The requested operation was not configured."""


class PermissionDeniedError(Exception):
    """The user does not have permission to perform this operation."""


class ProviderException(Exception):
    """An authentication provider returned an error from an API call."""


class GitHubException(ProviderException):
    """GitHub returned an error from an API call."""


class OIDCException(ProviderException):
    """The OpenID Connect provider returned an error from an API call."""


class LDAPException(ProviderException):
    """Group information for the user in LDAP was invalid."""


class UnauthorizedClientException(Exception):
    """The client is not authorized to request an authorization code.

    This corresponds to the ``unauthorized_client`` error in RFC 6749.
    """


class VerifyTokenException(Exception):
    """Base exception class for failure in verifying a token."""


class FetchKeysException(VerifyTokenException):
    """Cannot retrieve the keys from an issuer."""


class InvalidTokenClaimsException(VerifyTokenException):
    """One of the claims in the token is of an invalid format."""


class MissingClaimsException(VerifyTokenException):
    """The token is missing required claims."""


class UnknownAlgorithmException(VerifyTokenException):
    """The issuer key was for an unsupported algorithm."""


class UnknownKeyIdException(VerifyTokenException):
    """The reqeusted key ID was not found for an issuer."""
