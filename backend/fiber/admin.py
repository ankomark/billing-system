from django.contrib import admin

from .models import FiberOutage, MaintenanceWindow, Olt, Onu, RepairTicket, Splitter


@admin.register(Olt)
class OltAdmin(admin.ModelAdmin):
    list_display = ("name", "vendor", "model", "mgmt_ip", "is_reachable", "last_polled_at", "tenant")
    exclude = ("snmp_community", "cli_password")


@admin.register(Onu)
class OnuAdmin(admin.ModelAdmin):
    list_display = ("__str__", "olt", "pon_port", "onu_index", "status", "rx_power_dbm", "distance_m")
    list_filter = ("status", "olt")
    search_fields = ("mac_address", "label", "olt_description")


admin.site.register(Splitter)
admin.site.register(FiberOutage)
admin.site.register(MaintenanceWindow)
admin.site.register(RepairTicket)
