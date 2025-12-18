from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions

from billing.services.voucher_service import validate_voucher
from billing.router_service import enable_customer_access


class HotspotVoucherValidateView(APIView):
    """
    Public endpoint for hotspot voucher or M-Pesa receipt validation
    """
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        """
        Body:
        {
          "code": "WIFI-ABC123" or "QDW7H9X1Y2",
          "mac_address": "AA:BB:CC:DD:EE:FF"
        }
        """

        code = request.data.get("code")
        mac_address = request.data.get("mac_address")

        if not code or not mac_address:
            return Response(
                {"detail": "code and mac_address are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ✅ Validate voucher / receipt
        subscription = validate_voucher(code)

        if not subscription:
            return Response(
                {"detail": "Invalid or expired voucher"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        customer = subscription.customer

        # ✅ Bind MAC address (hotspot user)
        customer.hotspot_username = mac_address
        customer.status = "active"
        customer.save()

        # ✅ Enable router access
        enable_customer_access(customer)

        return Response(
            {
                "detail": "Access granted",
                "expires_at": subscription.expiry_date,
            },
            status=status.HTTP_200_OK,
        )
