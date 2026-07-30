"""
Puts the current operator into context for the duration of a request.

The tenant is read from a signed JWT claim rather than by loading the user.
DRF authenticates inside the view, so doing it here as well meant two user
queries on every request.

The claim decides *scoping* only — which operator's rows a request may see.
Authorisation still runs off request.user in the view, so a token whose claims
disagree with the database cannot grant access it should not have.
"""

import logging

from django.utils.deprecation import MiddlewareMixin
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError

from .auth_tokens import TENANT_CLAIM
from .tenancy import reset_current_tenant_id, set_current_tenant_id

logger = logging.getLogger(__name__)


class TenantMiddleware(MiddlewareMixin):
    """
    Resolves the operator from the request and scopes queries to it.

    The context is left unset — meaning unscoped — for:

    - platform staff, whose tenant claim is null: they are meant to see every
      operator
    - unauthenticated requests, which reach only public endpoints; those
      resolve their own operator from a voucher code, invoice number, or the
      token in the URL

    Any failure while resolving is treated as unauthenticated. A malformed
    token must not 500 here — the view's own authentication will reject it a
    moment later, with a proper error.
    """

    def process_request(self, request):
        request._tenant_token = None
        request.tenant_id = None

        tenant_id = self._tenant_from_token(request)

        if tenant_id is not None:
            request.tenant_id = tenant_id
            request._tenant_token = set_current_tenant_id(tenant_id)

    @staticmethod
    def _tenant_from_token(request):
        auth = JWTAuthentication()
        try:
            header = auth.get_header(request)
            if header is None:
                return None
            raw = auth.get_raw_token(header)
            if raw is None:
                return None

            # Validates the signature and expiry, but does not touch the
            # database — that is the whole point of carrying the claim.
            validated = auth.get_validated_token(raw)

            if TENANT_CLAIM in validated:
                # Present and null means platform staff, who run unscoped.
                return validated[TENANT_CLAIM]

            # Token predates the claim. Falling through to "unscoped" would
            # hand an operator admin platform-wide visibility for the lifetime
            # of their existing token, so pay for the lookup instead. This path
            # disappears as old tokens expire.
            user = auth.get_user(validated)
            return getattr(user, "tenant_id", None)

        except (InvalidToken, TokenError):
            return None
        except Exception:
            logger.exception("[tenancy] Failed to resolve tenant from request")
            return None

    def process_response(self, request, response):
        self._reset(request)
        return response

    def process_exception(self, request, exception):
        # Without this, a view that raises would leave the ContextVar set on a
        # worker thread that goes on to serve another operator's request.
        self._reset(request)
        return None

    @staticmethod
    def _reset(request):
        token = getattr(request, "_tenant_token", None)
        if token is not None:
            reset_current_tenant_id(token)
            request._tenant_token = None
