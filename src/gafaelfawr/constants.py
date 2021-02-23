"""Constants for Gafaelfawr."""

ALGORITHM = "RS256"
"""JWT algorithm to use for all tokens."""

COOKIE_NAME = "gafaelfawr"
"""Name of the state cookie."""

MINIMUM_LIFETIME = 5 * 60
"""Minimum expiration lifetime for a token in seconds."""

OIDC_AUTHORIZATION_LIFETIME = 60 * 60
"""How long (in seconds) an authorization code is good for."""

SETTINGS_PATH = "/etc/gafaelfawr/gafaelfawr.yaml"
"""Default configuration path."""

# The following constants are used for field validation.  Minimum and maximum
# length are handled separately.

ACTOR_REGEX = "^(?:<bootstrap>|[a-z_][a-z0-9._-]+)$"
""""Regex matching all valid actors (including ``<bootstrap``)."""

CURSOR_REGEX = "^p?[0-9]+_[0-9]+$"
"""Regex matching a valid cursor."""

GROUPNAME_REGEX = "^[a-z_][a-zA-Z0-9._-]*$"
"""Regex matching all valid group names."""

SCOPE_REGEX = "^[a-zA-Z0-9:._-]+$"
"""Regex matching a valid scope."""

USERNAME_REGEX = "^[a-z_][a-z0-9._-]*$"
"""Regex matching all valid usernames."""
