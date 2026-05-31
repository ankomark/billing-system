from django.urls import path, include
from .views import home,RevenueDashboardView, UnpaidInvoicesView,PendingInvoicesView,FailedMpesaTransactionsView,CustomerViewSet, PackageViewSet, SubscriptionViewSet,InvoiceViewSet,PaymentViewSet,MpesaSTKPushView,MpesaSTKCallbackView,ThrottledLoginView
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView
from .views import CustomerSuspendResumeView,HotspotStatusView,PppoeStatusView,PPPoEUsageView,AdminDisconnectPPPoEView,AdminUsageDailyView, AdminUsageAlertsView
from billing.views import TestMpesaView, TestSmsView, TestWhatsappView,PPPoELiveStatusView,PPPoEControlView,AdminRouterListView, HotspotUsageDailyView
from .views import ResendVoucherView,PPPoERenewView,SystemSettingsView,AdminPPPoESessionsView,CustomerReconnectPPPoEView,PPPoEUsageDailyView, PPPoEUsageMonthlyView,AdminDeactivateAccessView
from .views import UserProfileView,HotspotVoucherValidateView,PPPoECustomerPortalView,AdminBroadcastView,AdminRouterHealthView, AdminFailoverLogsView,AdminMigrateCustomerView, AdminAccessLookupView
router = DefaultRouter()
router.register("customers", CustomerViewSet, basename="customer")
router.register("packages", PackageViewSet, basename="package")
router.register("subscriptions", SubscriptionViewSet, basename="subscription")
router.register("invoices", InvoiceViewSet, basename="invoice")
router.register("payments", PaymentViewSet, basename="payment")


urlpatterns = [
    path("", home, name="home"),

    # ✅ AUTH
    path("api/auth/login/", ThrottledLoginView.as_view(), name="token_obtain_pair"),
    path("api/auth/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("api/auth/profile/", UserProfileView.as_view(), name="auth-profile"),
    path("api/system/settings/",SystemSettingsView.as_view(),name="system-settings"),

    # ✅ REPORTS & DASHBOARDS
    path("api/reports/revenue/", RevenueDashboardView.as_view(), name="revenue-dashboard"),
    path("api/dashboard/invoices/unpaid/", UnpaidInvoicesView.as_view(), name="unpaid-invoices"),
    path("api/dashboard/invoices/pending/", PendingInvoicesView.as_view(), name="pending-invoices"),
    path("api/dashboard/mpesa/failed/", FailedMpesaTransactionsView.as_view(), name="failed-mpesa"),

    # ✅ MPESA
    path("api/mpesa/stk-push/", MpesaSTKPushView.as_view(), name="mpesa_stk_push"),
    path("api/mpesa/stk-callback/", MpesaSTKCallbackView.as_view(), name="mpesa_stk_callback"),
    path("api/hotspot/validate/", HotspotVoucherValidateView.as_view(),name="hotspot-voucher-validate"),
    path("api/admin/customers/<int:customer_id>/action/",CustomerSuspendResumeView.as_view(),name="customer-suspend-resume"),
    path( "api/admin/customers/<int:customer_id>/resend-voucher/",ResendVoucherView.as_view(), name="resend-voucher"),
    path("api/hotspot/status/", HotspotStatusView.as_view(), name="hotspot-status"),
    path("api/pppoe/portal/", PPPoECustomerPortalView.as_view(), name="pppoe-portal"),
    path("api/pppoe/renew/", PPPoERenewView.as_view(), name="pppoe-renew"),
    path("api/customers/<int:customer_id>/pppoe-status/",PppoeStatusView.as_view(),name="pppoe-status"),
    path("api/system/test/mpesa/", TestMpesaView.as_view()),
    path("api/system/test/sms/", TestSmsView.as_view()),
    path("api/system/test/whatsapp/", TestWhatsappView.as_view()),
    path("api/admin/broadcast/", AdminBroadcastView.as_view(), name="admin-broadcast"),
    path( "api/pppoe/live-status/", PPPoELiveStatusView.as_view(), name="pppoe-live-status"),
    path("api/pppoe/control/", PPPoEControlView.as_view()),
    path("api/pppoe/usage/", PPPoEUsageView.as_view(), name="pppoe-usage"),
    path( "api/admin/pppoe/sessions/", AdminPPPoESessionsView.as_view(), name="admin-pppoe-sessions"),
    path("api/admin/pppoe/disconnect/", AdminDisconnectPPPoEView.as_view()),
    path("api/pppoe/reconnect/", CustomerReconnectPPPoEView.as_view()),
    path("api/admin/routers/health/", AdminRouterHealthView.as_view(), name="admin-router-health"),
    path("api/admin/routers/failovers/", AdminFailoverLogsView.as_view(), name="admin-failover-logs"),
    path("api/admin/failover/logs/",AdminFailoverLogsView.as_view(),name="admin-failover-logs"),
    path("api/admin/routers/", AdminRouterListView.as_view(), name="admin-routers"),
    path("api/admin/customers/migrate/",AdminMigrateCustomerView.as_view(),name="admin-migrate-customer"),
    path("api/pppoe/usage/daily/", PPPoEUsageDailyView.as_view()),
    path("api/pppoe/usage/monthly/", PPPoEUsageMonthlyView.as_view()),
    path("api/hotspot/usage/daily/", HotspotUsageDailyView.as_view()),
    path("api/admin/usage/daily/", AdminUsageDailyView.as_view()),
    path("api/admin/usage/Alerts/", AdminUsageAlertsView.as_view()),
    path("admin/access-lookup/",AdminAccessLookupView.as_view(),name="admin-access-lookup"),
    path("admin/access-deactivate/",AdminDeactivateAccessView.as_view(),name="admin-access-deactivate"),

    

    # ✅ CRUD (ViewSets)
    path("api/", include(router.urls)),
]

