from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView

from .views import (
    home,
    health_check,
    ThrottledLoginView,
    UserProfileView,
    ChangePasswordView,
    TenantUserViewSet,
    OperatorPasswordResetView,
    OperatorWarningView,
    OperatorMpesaSetupView,
    OperatorMpesaTestView,
    OperatorPlanView,
    AdminActionLogView,
    RouterEventsView,
    OperatorAnalyticsView,
    StationViewSet,
    PlatformHealthView,
    PlatformAnalyticsView,

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
    HotspotPackagesView,
    HotspotPurchaseView,
    HotspotPaymentStatusView,
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

    # Platform billing (charges operators, not subscribers)
    PlatformPlanViewSet,
    TenantInvoiceListView,
    MyPlatformSubscriptionView,
    RecordTenantPaymentView,
    TenantStatusView,
    PlatformOverviewView,
    PlatformOperatorListView,
    PlatformOperatorDetailView,
)

# ─── DRF ViewSet router ───────────────────────────────────────────────────────
router = DefaultRouter()
router.register("customers",     CustomerViewSet,     basename="customer")
router.register("packages",      PackageViewSet,      basename="package")
router.register("subscriptions", SubscriptionViewSet, basename="subscription")
router.register("invoices",      InvoiceViewSet,      basename="invoice")
router.register("payments",      PaymentViewSet,      basename="payment")
router.register("platform/plans", PlatformPlanViewSet, basename="platform-plan")
# An operator admin managing their own staff. Scoped to their tenant by the
# viewset, not by the URL.
router.register("users",         TenantUserViewSet,   basename="user")
# An operator's physical sites. Grouping only — no separate billing,
# packages or credentials hang off a station.
router.register("stations",      StationViewSet,      basename="station")

urlpatterns = [
    # ─── Root ────────────────────────────────────────────────────────────────
    path("",        home,         name="home"),
    path("health/", health_check, name="health-check"),

    # ─── Auth ────────────────────────────────────────────────────────────────
    path("api/auth/login/",   ThrottledLoginView.as_view(), name="token_obtain_pair"),
    path("api/auth/refresh/", TokenRefreshView.as_view(),   name="token_refresh"),
    path("api/auth/profile/", UserProfileView.as_view(),    name="auth-profile"),
    path("api/auth/change-password/", ChangePasswordView.as_view(), name="change-password"),

    # ─── Reports & dashboards ────────────────────────────────────────────────
    path("api/reports/revenue/",            RevenueDashboardView.as_view(),         name="revenue-dashboard"),
    path("api/reports/revenue/daily/",      DailyRevenueView.as_view(),             name="daily-revenue"),
    path("api/reports/analytics/",          OperatorAnalyticsView.as_view(),        name="operator-analytics"),
    path("api/dashboard/invoices/unpaid/",  UnpaidInvoicesView.as_view(),           name="unpaid-invoices"),
    path("api/dashboard/invoices/pending/", PendingInvoicesView.as_view(),          name="pending-invoices"),
    path("api/dashboard/mpesa/failed/",     FailedMpesaTransactionsView.as_view(),  name="failed-mpesa"),

    # ─── M-Pesa ──────────────────────────────────────────────────────────────
    path("api/mpesa/stk-push/",     MpesaSTKPushView.as_view(),     name="mpesa-stk-push"),

    # Per-operator callback. The token identifies whose Daraja app posted, so
    # the right credentials are loaded without inferring the operator from the
    # payload. This is the URL new operators should register with Safaricom.
    path(
        "api/mpesa/callback/<str:tenant_token>/",
        MpesaSTKCallbackView.as_view(),
        name="mpesa-callback-tenant",
    ),

    # Legacy shared callback. Kept working because it is already registered
    # with Safaricom for the original operator — changing a live callback URL
    # requires their approval. It resolves the operator from the invoice
    # number, which is globally unique for exactly this reason.
    path("api/mpesa/stk-callback/", MpesaSTKCallbackView.as_view(), name="mpesa-stk-callback"),

    # ─── Hotspot (public) ────────────────────────────────────────────────────
    # Walk-up purchase: no account, no JWT. The operator comes from ?t=.
    path("api/hotspot/packages/",       HotspotPackagesView.as_view(),      name="hotspot-packages"),
    path("api/hotspot/purchase/",       HotspotPurchaseView.as_view(),      name="hotspot-purchase"),
    path("api/hotspot/payment-status/", HotspotPaymentStatusView.as_view(), name="hotspot-payment-status"),
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
    path("api/admin/routers/events/",       RouterEventsView.as_view(),             name="router-events"),
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

    # ─── Platform billing ────────────────────────────────────────────────────
    # The platform charging operators. Distinct from /api/invoices/ and
    # /api/payments/, which are operators charging subscribers.
    path("api/platform/invoices/",     TenantInvoiceListView.as_view(),      name="platform-invoices"),
    path("api/platform/my-account/",   MyPlatformSubscriptionView.as_view(), name="platform-my-account"),
    path("api/platform/payments/",     RecordTenantPaymentView.as_view(),    name="platform-record-payment"),
    path("api/platform/overview/",  PlatformOverviewView.as_view(),     name="platform-overview"),
    path("api/platform/health/",    PlatformHealthView.as_view(),       name="platform-health"),
    path("api/platform/analytics/", PlatformAnalyticsView.as_view(),    name="platform-analytics"),
    path("api/platform/operators/", PlatformOperatorListView.as_view(), name="platform-operators"),
    path("api/platform/operators/<int:tenant_id>/", PlatformOperatorDetailView.as_view(), name="platform-operator-detail"),
    path("api/platform/operators/<int:tenant_id>/status/", TenantStatusView.as_view(), name="platform-tenant-status"),
    path("api/platform/operators/<int:tenant_id>/reset-password/", OperatorPasswordResetView.as_view(), name="platform-operator-reset-password"),
    path("api/platform/operators/<int:tenant_id>/warn/", OperatorWarningView.as_view(), name="platform-operator-warn"),
    path("api/platform/operators/<int:tenant_id>/plan/", OperatorPlanView.as_view(), name="platform-operator-plan"),
    path("api/platform/audit/", AdminActionLogView.as_view(), name="admin-audit-log"),
    path("api/platform/operators/<int:tenant_id>/mpesa/", OperatorMpesaSetupView.as_view(), name="platform-operator-mpesa"),
    path("api/platform/operators/<int:tenant_id>/mpesa/test/", OperatorMpesaTestView.as_view(), name="platform-operator-mpesa-test"),

    # ─── DRF ViewSets ────────────────────────────────────────────────────────
    path("api/", include(router.urls)),
]
