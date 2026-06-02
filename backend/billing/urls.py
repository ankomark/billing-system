from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView

from .views import (
    home,
    health_check,
    ThrottledLoginView,
    UserProfileView,

    # ViewSets
    CustomerViewSet,
    PackageViewSet,
    SubscriptionViewSet,
    InvoiceViewSet,
    PaymentViewSet,

    # Reports & dashboards
    RevenueDashboardView,
    UnpaidInvoicesView,
    PendingInvoicesView,
    FailedMpesaTransactionsView,
    DailyRevenueView,

    # M-Pesa
    MpesaSTKPushView,
    MpesaSTKCallbackView,

    # Hotspot
    HotspotVoucherValidateView,
    HotspotStatusView,
    HotspotReconnectView,

    # PPPoE — customer
    PPPoECustomerPortalView,
    PPPoERenewView,
    PppoeStatusView,
    PPPoELiveStatusView,
    PPPoEControlView,
    PPPoEUsageView,
    PPPoEUsageDailyView,
    PPPoEUsageMonthlyView,
    CustomerReconnectPPPoEView,

    # PPPoE — admin
    AdminPPPoESessionsView,
    AdminDisconnectPPPoEView,

    # Hotspot usage
    HotspotUsageDailyView,

    # Admin — customers
    CustomerSuspendResumeView,
    ResendVoucherView,
    AdminMigrateCustomerView,

    # Admin — access
    AdminAccessLookupView,
    AdminDeactivateAccessView,

    # Admin — usage
    AdminUsageDailyView,
    AdminUsageAlertsView,

    # Admin — routers
    AdminRouterListView,
    AdminRouterDetailView,
    AdminRouterHealthView,
    AdminFailoverLogsView,

    # Admin — broadcast
    AdminBroadcastView,

    # System
    SystemSettingsView,
    TestMpesaView,
    TestSmsView,
    TestWhatsappView,

    # Manual payment
    ManualPaymentView,
)

# ─── DRF ViewSet router ───────────────────────────────────────────────────────
router = DefaultRouter()
router.register("customers",     CustomerViewSet,     basename="customer")
router.register("packages",      PackageViewSet,      basename="package")
router.register("subscriptions", SubscriptionViewSet, basename="subscription")
router.register("invoices",      InvoiceViewSet,      basename="invoice")
router.register("payments",      PaymentViewSet,      basename="payment")

urlpatterns = [
    # ─── Root ────────────────────────────────────────────────────────────────
    path("",        home,         name="home"),
    path("health/", health_check, name="health-check"),

    # ─── Auth ────────────────────────────────────────────────────────────────
    path("api/auth/login/",   ThrottledLoginView.as_view(), name="token_obtain_pair"),
    path("api/auth/refresh/", TokenRefreshView.as_view(),   name="token_refresh"),
    path("api/auth/profile/", UserProfileView.as_view(),    name="auth-profile"),

    # ─── Reports & dashboards ────────────────────────────────────────────────
    path("api/reports/revenue/",            RevenueDashboardView.as_view(),         name="revenue-dashboard"),
    path("api/reports/revenue/daily/",      DailyRevenueView.as_view(),             name="daily-revenue"),
    path("api/dashboard/invoices/unpaid/",  UnpaidInvoicesView.as_view(),           name="unpaid-invoices"),
    path("api/dashboard/invoices/pending/", PendingInvoicesView.as_view(),          name="pending-invoices"),
    path("api/dashboard/mpesa/failed/",     FailedMpesaTransactionsView.as_view(),  name="failed-mpesa"),

    # ─── M-Pesa ──────────────────────────────────────────────────────────────
    path("api/mpesa/stk-push/",     MpesaSTKPushView.as_view(),     name="mpesa-stk-push"),
    path("api/mpesa/stk-callback/", MpesaSTKCallbackView.as_view(), name="mpesa-stk-callback"),

    # ─── Hotspot (public) ────────────────────────────────────────────────────
    path("api/hotspot/validate/",  HotspotVoucherValidateView.as_view(), name="hotspot-validate"),
    path("api/hotspot/status/",    HotspotStatusView.as_view(),          name="hotspot-status"),
    path("api/hotspot/reconnect/", HotspotReconnectView.as_view(),       name="hotspot-reconnect"),

    # ─── Hotspot usage ───────────────────────────────────────────────────────
    path("api/hotspot/usage/daily/", HotspotUsageDailyView.as_view(), name="hotspot-usage-daily"),

    # ─── PPPoE — customer portal ─────────────────────────────────────────────
    path("api/pppoe/portal/",        PPPoECustomerPortalView.as_view(), name="pppoe-portal"),
    path("api/pppoe/renew/",         PPPoERenewView.as_view(),          name="pppoe-renew"),
    path("api/pppoe/live-status/",   PPPoELiveStatusView.as_view(),     name="pppoe-live-status"),
    path("api/pppoe/control/",       PPPoEControlView.as_view(),        name="pppoe-control"),
    path("api/pppoe/usage/",         PPPoEUsageView.as_view(),          name="pppoe-usage"),
    path("api/pppoe/usage/daily/",   PPPoEUsageDailyView.as_view(),     name="pppoe-usage-daily"),
    path("api/pppoe/usage/monthly/", PPPoEUsageMonthlyView.as_view(),   name="pppoe-usage-monthly"),
    path("api/pppoe/reconnect/",     CustomerReconnectPPPoEView.as_view(), name="pppoe-reconnect"),

    path(
        "api/customers/<int:customer_id>/pppoe-status/",
        PppoeStatusView.as_view(),
        name="pppoe-status",
    ),

    # ─── PPPoE — admin ───────────────────────────────────────────────────────
    path("api/admin/pppoe/sessions/",   AdminPPPoESessionsView.as_view(),  name="admin-pppoe-sessions"),
    path("api/admin/pppoe/disconnect/", AdminDisconnectPPPoEView.as_view(), name="admin-pppoe-disconnect"),

    # ─── Admin — customers ───────────────────────────────────────────────────
    path(
        "api/admin/customers/<int:customer_id>/action/",
        CustomerSuspendResumeView.as_view(),
        name="customer-suspend-resume",
    ),
    path(
        "api/admin/customers/<int:customer_id>/resend-voucher/",
        ResendVoucherView.as_view(),
        name="resend-voucher",
    ),
    path("api/admin/customers/migrate/", AdminMigrateCustomerView.as_view(), name="admin-migrate-customer"),

    # ─── Admin — access lookup & deactivation ────────────────────────────────
    path("api/admin/access-lookup/",     AdminAccessLookupView.as_view(),    name="admin-access-lookup"),
    path("api/admin/access-deactivate/", AdminDeactivateAccessView.as_view(), name="admin-access-deactivate"),

    # ─── Admin — usage ───────────────────────────────────────────────────────
    path("api/admin/usage/daily/",  AdminUsageDailyView.as_view(),   name="admin-usage-daily"),
    path("api/admin/usage/alerts/", AdminUsageAlertsView.as_view(),  name="admin-usage-alerts"),

    # ─── Admin — routers ─────────────────────────────────────────────────────
    path("api/admin/routers/",             AdminRouterListView.as_view(),   name="admin-routers"),
    path("api/admin/routers/<int:pk>/",    AdminRouterDetailView.as_view(), name="admin-router-detail"),
    path("api/admin/routers/health/",      AdminRouterHealthView.as_view(), name="admin-router-health"),
    path("api/admin/routers/failovers/",   AdminFailoverLogsView.as_view(), name="admin-failover-logs"),

    # ─── Admin — broadcast ───────────────────────────────────────────────────
    path("api/admin/broadcast/", AdminBroadcastView.as_view(), name="admin-broadcast"),

    # ─── System ──────────────────────────────────────────────────────────────
    path("api/system/settings/",       SystemSettingsView.as_view(), name="system-settings"),
    path("api/system/test/mpesa/",     TestMpesaView.as_view(),      name="test-mpesa"),
    path("api/system/test/sms/",       TestSmsView.as_view(),        name="test-sms"),
    path("api/system/test/whatsapp/",  TestWhatsappView.as_view(),   name="test-whatsapp"),

    # ─── Manual payment ──────────────────────────────────────────────────────
    path("api/payments/manual/", ManualPaymentView.as_view(), name="manual-payment"),

    # ─── DRF ViewSets ────────────────────────────────────────────────────────
    path("api/", include(router.urls)),
]
