"""Representation for a ``Link`` HTTP header."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

__all__ = ["LinkData"]

_LINK_REGEX = r' *<(?P<target>[^>]+)>; rel="(?P<type>[^"]+)"'
"""Matches a component of a valid ``Link`` header."""


@dataclass
class LinkData:
    """Holds the data returned in an RFC 8288 ``Link`` header."""

    prev_url: Optional[str]
    """The URL of the previous page, or `None` for the first page."""

    next_url: Optional[str]
    """The URL of the next page, or `None` for the last page."""

    first_url: Optional[str]
    """The URL of the first page."""

    @classmethod
    def from_header(cls, header: Optional[str]) -> LinkData:
        """Parse an RFC 8288 ``Link`` with pagination URLs."""
        links = {}
        if header:
            elements = header.split(",")
            for element in elements:
                match = re.match(_LINK_REGEX, element)
                if match and match.group("type") in ("prev", "next", "first"):
                    links[match.group("type")] = match.group("target")

        return cls(
            prev_url=links.get("prev"),
            next_url=links.get("next"),
            first_url=links.get("first"),
        )
