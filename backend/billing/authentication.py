"""
JWT authentication that rejects a stale operator claim.

Kept in its own module, importing only simplejwt's authentication layer.
Putting it beside the token serializer caused a circular import: that module
imports simplejwt.views, which pulls in DRF's generics -> views -> schemas,
and schemas resolves DEFAULT_AUTHENTICATION_CLASSES at import time — back into
the module still being imported, before the class existed.
"""

import time

from django.core.cache import cache
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import AuthenticationFailed

TENANT_CLAIM = "tenant_id"
VERSION_CLAIM = "tv"

_ABSENT = object()


def _revoked_key(jti):
    return f"jwt:revoked:{jti}"


def revoke_access_token(token):
    """
    Refuse this access token from now on, though it has not expired.

    What makes signing out end the session on the server. Kept in the cache
    only until the token would have expired anyway, so the list never grows.
    """
    jti = token.get("jti")
    if not jti:
        return
    ttl = int(token.get("exp", 0) - time.time())
    if ttl > 0:
        cache.set(_revoked_key(jti), 1, ttl)


class TenantAwareJWTAuthentication(JWTAuthentication):
    """
    Rejects a token whose operator claim no longer matches the account.

    Scoping is decided by the claim, so the middleware needs no database
    lookup, while authorisation is decided by the account. When someone's
    tenant or role changes those disagree until the old token expires — and the
    dangerous direction is real: a demoted platform account carries a null
    tenant claim, meaning unscoped, so it would keep seeing every operator for
    the remaining lifetime of its refresh token while its permissions had
    already been reduced.

    Failing closed is the right trade. Privilege changes are rare and the cost
    is one forced sign-in; the alternative is a window of stale platform-wide
    visibility for an account whose access was just revoked.
    """

    def authenticate(self, request):
        result = super().authenticate(request)
        if result is None:
            return None

        user, token = result
        claim = token.payload.get(TENANT_CLAIM, _ABSENT)

        # Tokens predating the claim are resolved by the middleware's database
        # fallback, so there is nothing to compare against.
        if claim is not _ABSENT and claim != user.tenant_id:
            raise AuthenticationFailed(
                "Your access has changed. Please sign in again.",
                code="tenant_claim_stale",
            )

        # Same reasoning, applied to credentials rather than scope. A password
        # reset bumps token_version, so tokens minted against the old password
        # stop here instead of working until they expire — up to a day, since
        # the blacklist app is not installed and refresh tokens live that long.
        #
        # Tokens predating the claim have no version to compare and are left
        # alone; they expire within a day of this deploying.
        jti = token.payload.get("jti")
        if jti and cache.get(_revoked_key(jti)):
            raise AuthenticationFailed(
                "You have signed out. Please sign in again.",
                code="token_revoked",
            )

        version = token.payload.get(VERSION_CLAIM, _ABSENT)
        if version is not _ABSENT and version != user.token_version:
            raise AuthenticationFailed(
                "Your password was changed. Please sign in again.",
                code="token_version_stale",
            )

        return result
