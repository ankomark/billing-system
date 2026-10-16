from django.urls import path

from . import views

urlpatterns = [
    path("api/fiber/overview/",                views.OverviewView.as_view(),          name="fiber-overview"),
    path("api/fiber/olts/",                    views.OltListView.as_view(),           name="fiber-olts"),
    path("api/fiber/olts/<int:pk>/",           views.OltDetailView.as_view(),         name="fiber-olt"),
    path("api/fiber/olts/<int:pk>/poll/",      views.OltPollView.as_view(),           name="fiber-olt-poll"),
    path("api/fiber/onus/",                    views.OnuListView.as_view(),           name="fiber-onus"),
    path("api/fiber/onus/<int:pk>/",           views.OnuDetailView.as_view(),         name="fiber-onu"),
    path("api/fiber/onus/<int:pk>/locate/",    views.OnuLocateView.as_view(),         name="fiber-onu-locate"),
    path("api/fiber/onus/<int:pk>/link/",      views.OnuLinkView.as_view(),           name="fiber-onu-link"),
    path("api/fiber/map/",                     views.MapView.as_view(),               name="fiber-map"),
    path("api/fiber/splitters/",               views.SplitterListView.as_view(),      name="fiber-splitters"),
    path("api/fiber/splitters/suggest/",       views.SplitterSuggestView.as_view(),   name="fiber-splitter-suggest"),
    path("api/fiber/splitters/<int:pk>/",      views.SplitterDetailView.as_view(),    name="fiber-splitter"),
    path("api/fiber/outages/",                 views.OutageListView.as_view(),        name="fiber-outages"),
    path("api/fiber/outages/<int:pk>/",        views.OutageDetailView.as_view(),      name="fiber-outage"),
    path("api/fiber/maintenance/",             views.MaintenanceListView.as_view(),   name="fiber-maintenance"),
    path("api/fiber/maintenance/<int:pk>/",    views.MaintenanceDetailView.as_view(), name="fiber-maintenance-detail"),
    path("api/fiber/maintenance/<int:pk>/notify/", views.MaintenanceNotifyView.as_view(), name="fiber-maintenance-notify"),
    path("api/fiber/tickets/",                 views.TicketListView.as_view(),        name="fiber-tickets"),
    path("api/fiber/tickets/<int:pk>/",        views.TicketDetailView.as_view(),      name="fiber-ticket"),
    path("api/fiber/reports/ports/",           views.PortReportView.as_view(),        name="fiber-report-ports"),
    path("api/fiber/reports/monthly/",         views.MonthlyReportView.as_view(),     name="fiber-report-monthly"),
    path("api/hotspot/line-check/",            views.LineCheckView.as_view(),         name="fiber-line-check"),
]
