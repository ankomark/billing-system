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

from .auth_tokens import ROLE_CLAIM, TENANT_CLAIM
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

    # Platform staff set this to view the platform as one operator sees it.
    IMPERSONATE_HEADER = "HTTP_X_IMPERSONATE_TENANT"
    IMPERSONATE_REASON_HEADER = "HTTP_X_IMPERSONATE_REASON"

    def process_request(self, request):
        request._tenant_token = None
        request.tenant_id = None
        request.impersonating = None

        tenant_id = self._tenant_from_token(request)

        # Impersonation. Only a platform account may do this, and it grants no
        # new access — it narrows an account that could already see everything
        # down to a single operator, which is what makes it safe to offer.
        # Permissions are still evaluated against the platform account.
        if tenant_id is None:
            impersonated = self._impersonation_target(request)
            if impersonated is not None:
                tenant_id = impersonated
                request.impersonating = impersonated

        if tenant_id is not None:
            request.tenant_id = tenant_id
            request._tenant_token = set_current_tenant_id(tenant_id)

    def _impersonation_target(self, request):
        """
        Resolve the operator a platform account is viewing as.

        Returns None unless the caller genuinely holds a platform token — a
        null tenant claim alone is not enough, since an unauthenticated request
        also has no claim.
        """
        raw = request.META.get(self.IMPERSONATE_HEADER)
        if not raw:
            return None

        role = self._role_from_token(request)
        if role not in ("platform_owner", "platform_staff"):
            # Not an error worth failing the request over — the permission
            # layer will reject them anyway — but it is worth seeing.
            logger.warning(
                "[tenancy] Impersonation header ignored for non-platform role %r", role
            )
            return None

        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _role_from_token(request):
        auth = JWTAuthentication()
        try:
            header = auth.get_header(request)
            if header is None:
                return None
            raw = auth.get_raw_token(header)
            if raw is None:
                return None
            return auth.get_validated_token(raw).get(ROLE_CLAIM)
        except (InvalidToken, TokenError):
            return None
        except Exception:
            return None

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
        self._audit_impersonation(request, response)
        self._reset(request)
        return response

    def _audit_impersonation(self, request, response):
        """
        Record the request, after the view has run.

        Deliberately after: request.user is resolved by then, and a request
        the permission layer rejected is not worth recording as access.
        """
        tenant_id = getattr(request, "impersonating", None)
        if tenant_id is None or response.status_code >= 400:
            return

        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return

        try:
            from .models import ImpersonationLog
            ImpersonationLog.objects.create(
                platform_user=user,
                tenant_id=tenant_id,
                method=request.method,
                path=request.path[:255],
                reason=(request.META.get(self.IMPERSONATE_REASON_HEADER) or "")[:255],
            )
        except Exception:
            # Never fail a support request because the audit write failed —
            # but make sure it is impossible to miss in the logs.
            logger.exception(
                "[tenancy] Could not record impersonation of tenant %s by %s",
                tenant_id, getattr(user, 'username', '?'),
            )

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
