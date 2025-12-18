from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import permissions, status

from billing.models import Customer
from billing.router_service import enable_customer_access, disable_customer_access
from billing.permissions import IsAdmin
from django.shortcuts import get_object_or_404


class CustomerSuspendResumeView(APIView):
    permission_classes = [IsAdmin]

    def post(self, request, customer_id):
        action = request.data.get("action")  # "suspend" | "resume"
        customer = get_object_or_404(Customer, id=customer_id)

        subscription = customer.subscriptions.filter(status="active").first()

        if action == "suspend":
            if subscription:
                subscription.status = "suspended"
                subscription.save()

            customer.status = "expired"
            customer.save()

            disable_customer_access(customer)

            return Response({"detail": "Customer suspended"})

        if action == "resume":
            if subscription:
                subscription.status = "active"
                subscription.save()

            customer.status = "active"
            customer.save()

            enable_customer_access(customer)

            return Response({"detail": "Customer resumed"})

        return Response(
            {"detail": "Invalid action"},
            status=status.HTTP_400_BAD_REQUEST,
        )
