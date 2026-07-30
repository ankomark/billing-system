"""
JWT carrying the operator and role.

TenantMiddleware needs to know which operator a request belongs to before the
view runs. Reading it from the token means a signed claim rather than a
database lookup: previously the middleware called JWTAuthentication (one user
query) and then DRF authenticated again inside the view (a second), on every
single request.

The claims are advisory for scoping only. Authorisation still reads
request.user, which DRF loads and validates in the view, so a tampered claim
cannot grant access — the signature covers it, and a token whose claims
disagree with the database still resolves permissions from the database.
"""

from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.views import TokenObtainPairView

# The authentication class lives in billing/authentication.py — importing
# simplejwt.views here pulls in DRF's schema module, which resolves
# DEFAULT_AUTHENTICATION_CLASSES at import time and would cycle back here.
from .authentication import TENANT_CLAIM  # noqa: F401  (re-exported)

ROLE_CLAIM = "role"


class TenantTokenObtainPairSerializer(TokenObtainPairSerializer):
    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        # NULL tenant means platform staff, who run unscoped.
        token[TENANT_CLAIM] = user.tenant_id
        token[ROLE_CLAIM] = user.role
        return token

    def validate(self, attrs):
        data = super().validate(attrs)
        # Saves the frontend a round-trip to /auth/profile/ purely to find out
        # which dashboard to show.
        data["role"] = self.user.role
        data["tenant_id"] = self.user.tenant_id
        data["is_platform_staff"] = self.user.is_platform_staff
        return data


class TenantTokenObtainPairView(TokenObtainPairView):
    serializer_class = TenantTokenObtainPairSerializer
