"""Manage tokens."""

from __future__ import annotations

import ipaddress
import re
import time
from datetime import datetime
from typing import List, Optional

from structlog.stdlib import BoundLogger

from ..config import Config
from ..constants import MINIMUM_LIFETIME, USERNAME_REGEX
from ..exceptions import (
    InvalidExpiresError,
    InvalidIPAddressError,
    InvalidScopesError,
    PermissionDeniedError,
)
from ..models.history import (
    HistoryCursor,
    PaginatedHistory,
    TokenChange,
    TokenChangeHistoryEntry,
)
from ..models.token import (
    AdminTokenRequest,
    Token,
    TokenData,
    TokenInfo,
    TokenType,
    TokenUserInfo,
)
from ..storage.history import TokenChangeHistoryStore
from ..storage.token import TokenDatabaseStore, TokenRedisStore
from ..util import current_datetime
from .token_cache import TokenCacheService

__all__ = ["TokenService"]


class TokenService:
    """Manage tokens.

    Parameters
    ----------
    config : `gafaelfawr.config.Config`
        Gafaelfawr configuration.
    token_cache : `gafaelfawr.services.token_cache.TokenCacheService`
        Cache of internal and notebook tokens.
    token_db_store : `gafaelfawr.storage.token.TokenDatabaseStore`
        The database backing store for tokens.
    token_redis_store : `gafaelfawr.storage.token.TokenRedisStore`
        The Redis backing store for tokens.
    token_change_store : `gafaelfawr.storage.history.TokenChangeHistoryStore`
        The backing store for history of changes to tokens.
    logger : ``structlog.stdlib.BoundLogger``
        Logger to use.
    """

    def __init__(
        self,
        *,
        config: Config,
        token_cache: TokenCacheService,
        token_db_store: TokenDatabaseStore,
        token_redis_store: TokenRedisStore,
        token_change_store: TokenChangeHistoryStore,
        logger: BoundLogger,
    ) -> None:
        self._config = config
        self._token_cache = token_cache
        self._token_db_store = token_db_store
        self._token_redis_store = token_redis_store
        self._token_change_store = token_change_store
        self._logger = logger

    async def create_session_token(
        self, user_info: TokenUserInfo, *, scopes: List[str], ip_address: str
    ) -> Token:
        """Create a new session token.

        Parameters
        ----------
        user_info : `gafaelfawr.models.token.TokenUserInfo`
            The user information to associate with the token.
        scopes : List[`str`]
            The scopes of the token.
        ip_address : `str`
            The IP address from which the request came.

        Returns
        -------
        token : `gafaelfawr.models.token.Token`
            The newly-created token.

        Raises
        ------
        gafaelfawr.exceptions.PermissionDeniedError
            If the provided username is invalid.
        """
        self._validate_username(user_info.username)
        scopes = sorted(scopes)

        token = Token()
        created = current_datetime()
        expires = created + self._config.token_lifetime
        data = TokenData(
            token=token,
            token_type=TokenType.session,
            scopes=scopes,
            created=created,
            expires=expires,
            **user_info.dict(),
        )
        history_entry = TokenChangeHistoryEntry(
            token=token.key,
            username=data.username,
            token_type=TokenType.session,
            scopes=scopes,
            expires=expires,
            actor=data.username,
            action=TokenChange.create,
            ip_address=ip_address,
            event_time=created,
        )

        await self._token_redis_store.store_data(data)
        await self._token_db_store.add(data)
        await self._token_change_store.add(history_entry)

        return token

    async def create_user_token(
        self,
        auth_data: TokenData,
        username: str,
        *,
        token_name: str,
        scopes: List[str],
        expires: Optional[datetime] = None,
        ip_address: str,
    ) -> Token:
        """Add a new user token.

        Parameters
        ----------
        auth_data : `gafaelfawr.models.token.TokenData`
            The token data for the authentication token of the user creating
            a user token.
        username : `str`
            The username for which to create a token.
        token_name : `str`
            The name of the token.
        scopes : List[`str`]
            The scopes of the token.
        expires : `datetime` or `None`
            When the token should expire.  If not given, defaults to the
            expiration of the authentication token taken from ``data``.
        ip_address : `str`
            The IP address from which the request came.

        Returns
        -------
        token : `gafaelfawr.models.token.Token`
            The newly-created token.

        Raises
        ------
        gafaelfawr.exceptions.DuplicateTokenNameError
            A token with this name for this user already exists.
        gafaelfawr.exceptions.InvalidExpiresError
            The provided expiration time was invalid.
        gafaelfawr.exceptions.PermissionDeniedError
            If the given username didn't match the user information in the
            authentication token, or if the specified username is invalid.

        Notes
        -----
        This can only be used by the user themselves, not by a token
        administrator, because this API does not provide a way to set the
        additional user information for the token.  Once the user information
        no longer needs to be tracked by the token system, it can be unified
        with ``create_token_from_admin_request``.
        """
        self._check_authorization(username, auth_data, require_same_user=True)
        self._validate_username(username)
        self._validate_expires(expires)
        self._validate_scopes(scopes, auth_data)
        scopes = sorted(scopes)

        token = Token()
        created = current_datetime()
        data = TokenData(
            token=token,
            username=username,
            token_type=TokenType.user,
            scopes=scopes,
            created=created,
            expires=expires,
            name=auth_data.name,
            email=auth_data.email,
            uid=auth_data.uid,
            groups=auth_data.groups,
        )
        history_entry = TokenChangeHistoryEntry(
            token=token.key,
            username=data.username,
            token_type=TokenType.user,
            token_name=token_name,
            scopes=scopes,
            expires=expires,
            actor=auth_data.username,
            action=TokenChange.create,
            ip_address=ip_address,
            event_time=created,
        )

        await self._token_redis_store.store_data(data)
        await self._token_db_store.add(data, token_name=token_name)
        await self._token_change_store.add(history_entry)

        self._logger.info(
            "Created new user token",
            key=token.key,
            token_name=token_name,
            token_scope=",".join(data.scopes),
        )

        return token

    async def create_token_from_admin_request(
        self,
        request: AdminTokenRequest,
        auth_data: TokenData,
        *,
        ip_address: Optional[str],
    ) -> Token:
        """Create a new service or user token from an admin request.

        Parameters
        ----------
        request : `gafaelfawr.models.token.AdminTokenRequest`
            The incoming request.
        auth_data : `gafaelfawr.models.token.TokenData`
            The data for the authenticated user making the request.
        ip_address : `str` or `None`
            The IP address from which the request came, or `None` for internal
            requests by Gafaelfawr.

        Returns
        -------
        token : `gafaelfawr.models.token.Token`
            The newly-created token.

        Raises
        ------
        gafaelfawr.exceptions.PermissionDeniedError
            If the provided username is invalid.
        """
        self._check_authorization(
            request.username, auth_data, require_admin=True
        )
        self._validate_username(request.username)
        self._validate_scopes(request.scopes)
        self._validate_expires(request.expires)

        token = Token()
        created = current_datetime()
        data = TokenData(
            token=token,
            username=request.username,
            token_type=request.token_type,
            scopes=request.scopes,
            created=created,
            expires=request.expires,
            name=request.name,
            email=request.email,
            uid=request.uid,
            groups=request.groups,
        )
        history_entry = TokenChangeHistoryEntry(
            token=token.key,
            username=data.username,
            token_type=data.token_type,
            token_name=request.token_name,
            scopes=data.scopes,
            expires=request.expires,
            actor=auth_data.username,
            action=TokenChange.create,
            ip_address=ip_address,
            event_time=created,
        )

        await self._token_redis_store.store_data(data)
        await self._token_db_store.add(data, token_name=request.token_name)
        await self._token_change_store.add(history_entry)

        if data.token_type == TokenType.user:
            self._logger.info(
                "Created new user token",
                key=token.key,
                token_name=request.token_name,
                token_scope=",".join(data.scopes),
                token_username=data.username,
            )
        else:
            self._logger.info(
                "Created new service token",
                key=token.key,
                token_scope=",".join(data.scopes),
                token_username=data.username,
            )
        return token

    async def delete_token(
        self,
        key: str,
        auth_data: TokenData,
        username: str,
        *,
        ip_address: str,
    ) -> bool:
        """Delete a token.

        Parameters
        ----------
        key : `str`
            The key of the token to delete.
        auth_data : `gafaelfawr.models.token.TokenData`
            The token data for the authentication token of the user deleting
            the token.
        username : `str`
            Constrain deletions to tokens owned by the given user.
        ip_address : `str`
            The IP address from which the request came.

        Returns
        -------
        success : `bool`
            Whether the token was found and deleted.
        """
        info = await self.get_token_info_unchecked(key, username)
        if not info:
            return False
        self._check_authorization(info.username, auth_data)

        # Recursively delete the children of this token first.  Children are
        # returned in breadth-first order, so delete them in reverse order to
        # delete the tokens farthest down in the tree first.  This minimizes
        # the number of orphaned children at any given point.
        children = await self._token_db_store.get_children(key)
        children.reverse()
        for child in children:
            await self._delete_one_token(child, auth_data, ip_address)
        success = await self._delete_one_token(key, auth_data, ip_address)

        return success

    async def get_change_history(
        self,
        auth_data: TokenData,
        *,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        username: Optional[str] = None,
        actor: Optional[str] = None,
        key: Optional[str] = None,
        token: Optional[str] = None,
        token_type: Optional[TokenType] = None,
        ip_or_cidr: Optional[str] = None,
    ) -> PaginatedHistory[TokenChangeHistoryEntry]:
        """Retrieve the change history of a token.

        Parameters
        ----------
        auth_data : `gafaelfawr.models.token.TokenData`
            Authentication information for the user making the request.
        cursor : `str`, optional
            A pagination cursor specifying where to start in the results.
        limit : `int`, optional
            Limit the number of returned results.
        since : `datetime.datetime`, optional
            Limit the results to events at or after this time.
        until : `datetime.datetime`, optional
            Limit the results to events before or at this time.
        username : `str`, optional
            Limit the results to tokens owned by this user.
        actor : `str`, optional
            Limit the results to actions performed by this user.
        key : `str`, optional
            Limit the results to this token and any subtokens of this token.
            Note that this will currently pick up direct subtokens but not
            subtokens of subtokens.
        token : `str`, optional
            Limit the results to only this token.
        token_type : `gafaelfawr.models.token.TokenType`, optional
            Limit the results to tokens of this type.
        ip_or_cidr : `str`, optional
            Limit the results to changes made from this IPv4 or IPv6 address
            or CIDR block.

        Returns
        -------
        entries : List[`gafaelfawr.models.history.TokenChangeHistoryEntry`]
            A list of changes matching the search criteria.

        Raises
        ------
        gafaelfawr.exceptions.InvalidCursorError
            The provided cursor was invalid.
        gafaelfawr.exceptions.InvalidIPAddressError
            The provided argument was syntactically invalid for both an
            IP address and a CIDR block.
        """
        self._check_authorization(username, auth_data)
        self._validate_ip_or_cidr(ip_or_cidr)
        return await self._token_change_store.list(
            cursor=HistoryCursor.from_str(cursor) if cursor else None,
            limit=limit,
            since=since,
            until=until,
            username=username,
            actor=actor,
            key=key,
            token=token,
            token_type=token_type,
            ip_or_cidr=ip_or_cidr,
        )

    async def get_data(self, token: Token) -> Optional[TokenData]:
        """Retrieve the data for a token from Redis.

        Doubles as a way to check the validity of the token.

        Parameters
        ----------
        token : `gafaelfawr.models.token.Token`
            The token.

        Returns
        -------
        data : `gafaelfawr.models.token.TokenData` or `None`
            The data underlying the token, or `None` if the token is not
            valid.
        """
        return await self._token_redis_store.get_data(token)

    async def get_internal_token(
        self,
        token_data: TokenData,
        service: str,
        scopes: List[str],
        *,
        ip_address: str,
    ) -> Token:
        """Get or create a new internal token.

        Parameters
        ----------
        token_data : `gafaelfawr.models.token.TokenData`
            The authentication data on which to base the new token.
        service : `str`
            The internal service to which the token is delegated.
        scopes : List[`str`]
            The scopes the new token should have.
        ip_address : `str`
            The IP address from which the request came.

        Returns
        -------
        token : `gafaelfawr.models.token.Token`
            The newly-created token.

        Raises
        ------
        gafaelfawr.exceptions.PermissionDeniedError
            If the username is invalid.
        """
        self._validate_scopes(scopes, token_data)
        self._validate_username(token_data.username)
        scopes = sorted(scopes)
        return await self._token_cache.get_internal_token(
            token_data, service, scopes, ip_address
        )

    async def get_notebook_token(
        self, token_data: TokenData, ip_address: str
    ) -> Token:
        """Get or create a new notebook token.

        Parameters
        ----------
        token_data : `gafaelfawr.models.token.TokenData`
            The authentication data on which to base the new token.
        ip_address : `str`
            The IP address from which the request came.

        Returns
        -------
        token : `gafaelfawr.models.token.Token`
            The newly-created token.

        Raises
        ------
        gafaelfawr.exceptions.PermissionDeniedError
            If the username is invalid.
        """
        self._validate_username(token_data.username)
        return await self._token_cache.get_notebook_token(
            token_data, ip_address
        )

    async def get_token_info(
        self, key: str, auth_data: TokenData, username: Optional[str]
    ) -> Optional[TokenInfo]:
        """Get information about a token.

        Parameters
        ----------
        key : `str`
            The key of the token.
        auth_data : `gafaelfawr.models.token.TokenData`
            The authentication data of the person requesting the token
            information, used for authorization checks.
        username : `str`, optional
            If set, constrain the result to tokens from that user and return
            `None` if the token exists but is for a different user.
        """
        info = await self.get_token_info_unchecked(key, username)
        if not info:
            return None
        self._check_authorization(info.username, auth_data)
        return info

    async def get_token_info_unchecked(
        self, key: str, username: Optional[str] = None
    ) -> Optional[TokenInfo]:
        """Get information about a token without checking authorization.

        Parameters
        ----------
        key : `str`
            The key of the token.
        username : `str`, optional
            If set, constrain the result to tokens from that user and return
            `None` if the token exists but is for a different user.
        """
        info = await self._token_db_store.get_info(key)
        if not info:
            return None
        if username and info.username != username:
            return None
        return info

    async def get_user_info(self, token: Token) -> Optional[TokenUserInfo]:
        """Get user information associated with a token."""
        data = await self.get_data(token)
        if not data:
            return None
        return TokenUserInfo(
            username=data.username,
            name=data.name,
            uid=data.uid,
            email=data.email,
            groups=data.groups,
        )

    async def list_tokens(
        self, auth_data: TokenData, username: Optional[str] = None
    ) -> List[TokenInfo]:
        """List tokens.

        Parameters
        ----------
        auth_data : `gafaelfawr.models.token.TokenData`
            The token data for the authentication token of the user making
            this modification.
        username : `str`, optional
            Limit results to the given username.

        Returns
        -------
        info : List[`gafaelfawr.models.token.TokenInfo`]
            Information for all matching tokens.

        Raises
        ------
        gafaelfawr.exceptions.PermissionDeniedError
            The user whose tokens are being listed does not match the
            authentication information.
        """
        self._check_authorization(username, auth_data)
        return await self._token_db_store.list(username=username)

    async def modify_token(
        self,
        key: str,
        auth_data: TokenData,
        username: Optional[str] = None,
        *,
        ip_address: str,
        token_name: Optional[str] = None,
        scopes: Optional[List[str]] = None,
        expires: Optional[datetime] = None,
        no_expire: bool = False,
    ) -> Optional[TokenInfo]:
        """Modify a token.

        Parameters
        ----------
        key : `str`
            The key of the token to modify.
        auth_data : `gafaelfawr.models.token.TokenData`
            The token data for the authentication token of the user making
            this modification.
        username : `str`, optional
            If given, constrain modifications to tokens owned by the given
            user.
        ip_address : `str`
            The IP address from which the request came.
        token_name : `str`, optional
            The new name for the token.
        scopes : List[`str`], optional
            The new scopes for the token.
        expires : `datetime`, optional
            The new expiration time for the token.
        no_expire : `bool`
            If set, the token should not expire.  This is a separate parameter
            because passing `None` to ``expires`` is ambiguous.

        Returns
        -------
        info : `gafaelfawr.models.token.TokenInfo` or `None`
            Information for the updated token or `None` if it was not found.

        Raises
        ------
        gafaelfawr.exceptions.InvalidExpiresError
            The provided expiration time was invalid.
        gafaelfawr.exceptions.DuplicateTokenNameError
            A token with this name for this user already exists.
        gafaelfawr.exceptions.PermissionDeniedError
            The token being modified is not owned by the user identified with
            ``auth_data`` or the user attempted to modify a token type other
            than user.
        """
        info = await self.get_token_info_unchecked(key, username)
        if not info:
            return None
        self._check_authorization(info.username, auth_data)
        if info.token_type != TokenType.user:
            msg = "Only user tokens can be modified"
            self._logger.warning("Permission denied", error=msg)
            raise PermissionDeniedError(msg)
        if scopes:
            self._validate_scopes(scopes, auth_data)
        self._validate_expires(expires)

        # Determine if the lifetime has decreased, in which case we may have
        # to update subtokens.
        update_subtoken_expires = expires and (
            not info.expires or expires <= info.expires
        )

        history_entry = TokenChangeHistoryEntry(
            token=key,
            username=info.username,
            token_type=TokenType.user,
            token_name=token_name if token_name else info.token_name,
            scopes=sorted(scopes) if scopes is not None else info.scopes,
            expires=info.expires if not (expires or no_expire) else expires,
            actor=auth_data.username,
            action=TokenChange.edit,
            old_token_name=info.token_name if token_name else None,
            old_scopes=info.scopes if scopes is not None else None,
            old_expires=info.expires if (expires or no_expire) else None,
            ip_address=ip_address,
        )

        info = await self._token_db_store.modify(
            key,
            token_name=token_name,
            scopes=sorted(scopes) if scopes else scopes,
            expires=expires,
            no_expire=no_expire,
        )
        await self._token_change_store.add(history_entry)

        # Update the expiration in Redis if needed.
        if info and (no_expire or expires):
            data = await self._token_redis_store.get_data_by_key(key)
            if data:
                data.expires = None if no_expire else expires
                await self._token_redis_store.store_data(data)
            else:
                info = None

        # Update subtokens if needed.
        if update_subtoken_expires and info:
            assert expires
            for child in await self._token_db_store.get_children(key):
                await self._modify_expires(
                    child, auth_data, expires, ip_address
                )

        if info:
            timestamp = int(info.expires.timestamp()) if info.expires else None
            self._logger.info(
                "Modified token",
                key=key,
                token_name=info.token_name,
                token_scope=",".join(info.scopes),
                expires=timestamp,
            )
        return info

    def _check_authorization(
        self,
        username: Optional[str],
        auth_data: TokenData,
        *,
        require_admin: bool = False,
        require_same_user: bool = False,
    ) -> None:
        """Check authorization for performing an action.

        Arguments
        ---------
        username : `str` or `None`
            The user whose tokens are being changed, or `None` if listing
            all tokens.
        auth_data : `gafaelfawr.models.token.TokenData`
            The authenticated user changing the tokens.
        require_admin : `bool`, optional
            If set to `True`, require the authenticated user have
            ``admin:token`` scope.  Default is `False`.
        require_same_user : `bool`, optional
            If set to `True`, require that ``username`` match the
            authenticated user as specified by ``auth_data`` and do not allow
            token admins.  Default is `False`.

        Raises
        ------
        gafaelfawr.exceptions.PermissionDeniedError
            The authenticated user doesn't have permission to manipulate
            tokens for that user.
        """
        is_admin = "admin:token" in auth_data.scopes
        if (username is None or require_admin) and not is_admin:
            msg = "Missing required admin:token scope"
            self._logger.warning("Permission denied", error=msg)
            raise PermissionDeniedError(msg)
        if username is not None and username != auth_data.username:
            if require_same_user or not is_admin:
                msg = f"Cannot act on tokens for user {username}"
                self._logger.warning("Permission denied", error=msg)
                raise PermissionDeniedError(msg)
        if not is_admin and "user:token" not in auth_data.scopes:
            msg = "Missing required user:token scope"
            self._logger.warning("Permission denied", error=msg)
            raise PermissionDeniedError(msg)

    async def _delete_one_token(
        self,
        key: str,
        auth_data: TokenData,
        ip_address: str,
    ) -> bool:
        """Helper function to delete a single token.

        This does not do cascading delete and assumes authorization has
        already been checked.

        Parameters
        ----------
        key : `str`
            The key of the token to delete.
        auth_data : `gafaelfawr.models.token.TokenData`
            The token data for the authentication token of the user deleting
            the token.
        ip_address : `str`
            The IP address from which the request came.

        Returns
        -------
        success : `bool`
            Whether the token was found and deleted.
        """
        info = await self.get_token_info_unchecked(key)
        if not info:
            return False

        history_entry = TokenChangeHistoryEntry(
            token=key,
            username=info.username,
            token_type=info.token_type,
            token_name=info.token_name,
            parent=info.parent,
            scopes=info.scopes,
            service=info.service,
            expires=info.expires,
            actor=auth_data.username,
            action=TokenChange.revoke,
            ip_address=ip_address,
        )

        await self._token_redis_store.delete(key)
        success = await self._token_db_store.delete(key)
        if success:
            await self._token_change_store.add(history_entry)
            self._logger.info("Deleted token", key=key, username=info.username)
        return success

    async def _modify_expires(
        self,
        key: str,
        auth_data: TokenData,
        expires: datetime,
        ip_address: str,
    ) -> None:
        """Change the expiration of a token if necessary.

        Used to update the expiration of subtokens when the parent token
        expiration has changed.

        Parameters
        ----------
        key : `str`
            The key of the token to update.
        auth_data : `gafaelfawr.models.token.TokenData`
            The token data for the authentication token of the user changing
            the expiration.
        expires : `datetime.datetime`
            The new expiration of the parent token.  The expiration of the
            child token will be changed if it's later than this value.
        ip_address : `str`
            The IP address from which the request came.
        """
        info = await self.get_token_info_unchecked(key)
        if not info:
            return
        if info.expires and info.expires <= expires:
            return

        history_entry = TokenChangeHistoryEntry(
            token=key,
            username=info.username,
            token_type=info.token_type,
            token_name=info.token_name,
            parent=info.parent,
            scopes=info.scopes,
            service=info.service,
            expires=expires,
            old_expires=info.expires,
            actor=auth_data.username,
            action=TokenChange.edit,
            ip_address=ip_address,
        )

        await self._token_db_store.modify(key, expires=expires)
        await self._token_change_store.add(history_entry)
        data = await self._token_redis_store.get_data_by_key(key)
        if data:
            data.expires = expires
            await self._token_redis_store.store_data(data)

    def _validate_ip_or_cidr(self, ip_or_cidr: Optional[str]) -> None:
        """Check that an IP address or CIDR block is valid.

        Arguments
        ---------
        ip_address : `str` or `None`
            `None` or a string representing an IPv4 or IPv6 address or CIDR
            block.

        Raises
        ------
        gafaelfawr.exceptions.InvalidIPAddressError
            The provided argument was syntactically invalid for both an
            IP address and a CIDR block.
        """
        if ip_or_cidr is None:
            return
        try:
            if "/" in ip_or_cidr:
                ipaddress.ip_network(ip_or_cidr)
            else:
                ipaddress.ip_address(ip_or_cidr)
        except ValueError as e:
            raise InvalidIPAddressError(f"Invalid IP address: {str(e)}")

    def _validate_expires(self, expires: Optional[datetime]) -> None:
        """Check that a provided token expiration is valid.

        Arguments
        ---------
        expires : `datetime` or `None`
            The token expiration time.

        Raises
        ------
        gafaelfawr.exceptions.InvalidExpiresError
            The provided expiration time is not valid.

        Notes
        -----
        This is not done in the model because we want to be able to return
        whatever expiration time is set in the backing store in replies, even
        if it isn't valid.  (It could be done using multiple models, but
        isn't currently.)
        """
        if not expires:
            return
        if expires.timestamp() < time.time() + MINIMUM_LIFETIME:
            msg = "token must be valid for at least five minutes"
            raise InvalidExpiresError(msg)

    def _validate_scopes(
        self,
        scopes: List[str],
        auth_data: Optional[TokenData] = None,
    ) -> None:
        """Check that the requested scopes are valid.

        Arguments
        ---------
        scopes : List[`str`]
            The requested scopes.
        auth_data : `gafaelfawr.models.token.TokenData`, optional
            The token used to authenticate the operation, if the scopes should
            be checked to ensure they are a subset.

        Raises
        ------
        gafaelfawr.exceptions.InvalidScopesError
            The requested scopes are not permitted.
        """
        if not scopes:
            return
        scopes_set = set(scopes)
        if auth_data and "admin:token" not in auth_data.scopes:
            if not (scopes_set <= set(auth_data.scopes)):
                msg = "Requested scopes are broader than your current scopes"
                raise InvalidScopesError(msg)
        if not (scopes_set <= self._config.known_scopes.keys()):
            msg = "Unknown scopes requested"
            raise InvalidScopesError(msg)

    def _validate_username(self, username: str) -> None:
        """Check that the username is valid.

        If ``auth_data`` is provided, ensure that the authenticated user as
        represented by ``auth_data`` is permitted to manipulate the tokens of
        ``username``.

        Arguments
        ---------
        username : `str`
            The user whose tokens are being changed.
        auth_data : `gafaelfawr.models.token.TokenData`
            The authenticated user changing the tokens.
        same_user : `bool`, optional
            Require that ``username`` match the authenticated user as
            specified by ``auth_data`` and do not allow token admins.

        Raises
        ------
        gafaelfawr.exceptions.PermissionDeniedError
            The username is invalid or the authenticated user doesn't have
            permission to manipulate tokens for that user.
        """
        if not re.match(USERNAME_REGEX, username):
            raise PermissionDeniedError(f"Invalid username: {username}")
