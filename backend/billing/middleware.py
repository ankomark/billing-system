"""
Puts the current operator into context for the duration of a request.

DRF authenticates inside the view, not in middleware, so `request.user` is
still anonymous when this runs. The tenant is therefore read from the JWT
directly, and the view's own authentication remains the thing that decides
whether the request is allowed at all — this only decides which operator's
data it may see.
"""

import logging

from django.utils.deprecation import MiddlewareMixin
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError

from .tenancy import reset_current_tenant_id, set_current_tenant_id

logger = logging.getLogger(__name__)


class TenantMiddleware(MiddlewareMixin):
    """
    Resolves the operator from the request and scopes queries to it.

    Leaves the context unset (meaning unscoped) for:

    - platform staff, whose User.tenant is NULL — they are meant to see every
      operator
    - unauthenticated requests, which reach only public endpoints; those
      resolve their own tenant from a voucher code, invoice number or the
      tenant token in the URL

    Any exception while resolving is swallowed and treated as unauthenticated.
    A malformed token must not 500 — the view's authentication will reject it
    in a moment anyway.
    """

    def process_request(self, request):
        request._tenant_token = None
        tenant_id = None

        try:
            result = JWTAuthentication().authenticate(request)
            if result is not None:
                user, _validated = result
                tenant_id = getattr(user, "tenant_id", None)
                request.tenant_id = tenant_id
        except (InvalidToken, TokenError):
            pass
        except Exception:
            logger.exception("[tenancy] Failed to resolve tenant from request")

        if tenant_id is not None:
            request._tenant_token = set_current_tenant_id(tenant_id)

    def process_response(self, request, response):
        self._reset(request)
        return response

    def process_exception(self, request, exception):
        # Without this, a view raising would leave the ContextVar set on a
        # worker thread that goes on to serve another operator's request.
        self._reset(request)
        return None

    @staticmethod
    def _reset(request):
        token = getattr(request, "_tenant_token", None)
        if token is not None:
            reset_current_tenant_id(token)
            request._tenant_token = None
