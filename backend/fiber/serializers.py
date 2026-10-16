from django.conf import settings
from rest_framework import serializers

from .models import MaintenanceWindow, Olt, RepairTicket, Splitter


class OltSerializer(serializers.ModelSerializer):
    # Write-only: a community string or CLI password is never sent back out.
    # Blank on update means "keep the current one", as router passwords do.
    snmp_community = serializers.CharField(write_only=True, required=False, allow_blank=True)
    cli_password = serializers.CharField(write_only=True, required=False, allow_blank=True)
    has_snmp_community = serializers.SerializerMethodField()
    has_cli_password = serializers.SerializerMethodField()
    router_name = serializers.CharField(source="router.name", read_only=True, default=None)

    class Meta:
        model = Olt
        fields = [
            "id", "name", "vendor", "model", "serial", "mac_address", "firmware",
            "router", "router_name", "uplink_interface", "mgmt_ip", "transport",
            "snmp_port", "snmp_community", "has_snmp_community", "cli_port",
            "cli_username", "cli_password", "has_cli_password", "pon_ports",
            "pon_capacity_mbps", "latitude", "longitude", "profile_overrides",
            "is_active", "is_reachable", "last_polled_at", "last_ok_at", "last_error",
        ]
        read_only_fields = ["firmware", "is_reachable", "last_polled_at", "last_ok_at", "last_error"]

    def get_has_snmp_community(self, obj):
        return bool(obj.snmp_community)

    def get_has_cli_password(self, obj):
        return bool(obj.cli_password)

    def validate_vendor(self, value):
        if value == Olt.VENDOR_SIMULATED and not settings.DEBUG:
            raise serializers.ValidationError(
                "A simulated OLT invents its ONUs and customers; it is only for local development.")
        return value

    def update(self, instance, validated):
        for secret in ("snmp_community", "cli_password"):
            if not validated.get(secret):
                validated.pop(secret, None)
        return super().update(instance, validated)


class SplitterSerializer(serializers.ModelSerializer):
    onu_count = serializers.IntegerField(source="onus.count", read_only=True)

    class Meta:
        model = Splitter
        fields = ["id", "olt", "pon_port", "name", "ratio", "parent", "distance_m",
                  "latitude", "longitude", "notes", "onu_count"]


class MaintenanceSerializer(serializers.ModelSerializer):
    olt_name = serializers.CharField(source="olt.name", read_only=True)

    class Meta:
        model = MaintenanceWindow
        fields = ["id", "olt", "olt_name", "pon_port", "starts_at", "ends_at", "reason", "compensate",
                  "notified_at", "notified_count", "compensated_at", "created_by", "created_at"]
        read_only_fields = ["notified_at", "notified_count", "compensated_at", "created_by", "created_at"]

    def validate(self, data):
        start = data.get("starts_at", getattr(self.instance, "starts_at", None))
        end = data.get("ends_at", getattr(self.instance, "ends_at", None))
        if start and end and end <= start:
            raise serializers.ValidationError("The window must end after it starts.")
        return data


class TicketSerializer(serializers.ModelSerializer):
    olt_name = serializers.CharField(source="olt.name", read_only=True, default=None)
    onu_label = serializers.SerializerMethodField()

    class Meta:
        model = RepairTicket
        fields = ["id", "kind", "status", "title", "detail", "olt", "olt_name", "onu", "onu_label",
                  "outage", "assigned_to", "created_at", "resolved_at", "resolution"]
        read_only_fields = ["created_at", "resolved_at"]

    def get_onu_label(self, obj):
        return str(obj.onu) if obj.onu_id else None
