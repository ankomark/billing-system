"""
The fiber plant: OLTs, the ONUs hanging off them, and what happens to both.

Every row is owned by one operator (TenantScopedModel) and carries the same
row-level security as billing's tables -- see migration 0002. An ONU says
where a customer is plugged in, and that is not something another operator on
the platform should be able to read.

Readings are kept raw for a fortnight and as hourly rollups for a year
(tasks.prune_fiber_readings). An estate of a few hundred ONUs polled every five
minutes writes ~90k rows a day; keeping them forever is a table that only
grows, for history nobody looks at below the hour.
"""

from datetime import timedelta

from django.db import models
from django.utils import timezone

from billing.fields import EncryptedCharField
from billing.models import Customer, RouterDevice, Subscription, TenantScopedModel


class Olt(TenantScopedModel):
    """One optical line terminal, and how billing reaches it."""

    VENDOR_VSOL = "vsol"
    VENDOR_HIOSO = "hioso"
    VENDOR_SIMULATED = "simulated"
    VENDOR_CHOICES = (
        (VENDOR_VSOL, "VSOL"),
        (VENDOR_HIOSO, "HiOSO"),
        # Tests and local development only. A simulated OLT invents its ONUs,
        # so the admin refuses one outside DEBUG (see serializers).
        (VENDOR_SIMULATED, "Simulated"),
    )

    TRANSPORT_SNMP = "snmp"
    TRANSPORT_TELNET = "telnet"
    TRANSPORT_CHOICES = (
        (TRANSPORT_SNMP, "SNMP v2c"),
        (TRANSPORT_TELNET, "Telnet CLI"),
    )

    name = models.CharField(max_length=100)
    vendor = models.CharField(max_length=16, choices=VENDOR_CHOICES)
    model = models.CharField(max_length=64, blank=True)
    serial = models.CharField(max_length=64, blank=True)
    mac_address = models.CharField(max_length=17, blank=True)
    firmware = models.CharField(max_length=64, blank=True)

    # Which customer router this OLT feeds, and on which of its ports. Used to
    # place the OLT on the map and to read nothing else: the OLT itself is
    # polled directly at mgmt_ip.
    router = models.ForeignKey(
        RouterDevice, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="olts")
    uplink_interface = models.CharField(max_length=32, blank=True)

    mgmt_ip = models.GenericIPAddressField(protocol="IPv4")
    transport = models.CharField(
        max_length=8, choices=TRANSPORT_CHOICES, default=TRANSPORT_SNMP)
    snmp_port = models.PositiveIntegerField(default=161)
    # Encrypted at rest, like router passwords: a community string reads the
    # whole plant, and a CLI login changes it.
    snmp_community = EncryptedCharField(blank=True, default="")
    cli_port = models.PositiveIntegerField(default=23)
    cli_username = models.CharField(max_length=64, blank=True)
    cli_password = EncryptedCharField(blank=True, default="")

    pon_ports = models.PositiveSmallIntegerField(default=4)
    # EPON ports run at 1.25 Gbit/s line rate, ~1 Gbit/s usable each way.
    pon_capacity_mbps = models.PositiveIntegerField(default=1000)

    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)

    # Overrides for the vendor profile's OIDs or CLI commands, as JSON. Empty
    # means the profile's defaults. This is where an OID found with
    # `manage.py fiber_discover` goes without a code change.
    profile_overrides = models.JSONField(default=dict, blank=True)

    is_active = models.BooleanField(default=True)
    is_reachable = models.BooleanField(default=False)
    last_polled_at = models.DateTimeField(null=True, blank=True)
    last_ok_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=255, blank=True)
    consecutive_failures = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "mgmt_ip"], name="olt_tenant_ip_uniq"),
        ]

    def __str__(self):
        return self.name


class Splitter(TenantScopedModel):
    """A passive splitter box, placed by hand on the map."""

    olt = models.ForeignKey(Olt, on_delete=models.CASCADE, related_name="splitters")
    pon_port = models.PositiveSmallIntegerField()
    name = models.CharField(max_length=100)
    ratio = models.CharField(max_length=8, blank=True, help_text="e.g. 1:8")
    # The splitter this one hangs off, for cascaded plants (1:4 then 1:8).
    parent = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="children")
    distance_m = models.PositiveIntegerField(null=True, blank=True)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    notes = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["olt", "pon_port", "name"]

    def __str__(self):
        return f"{self.name} ({self.olt} PON {self.pon_port})"


class Onu(TenantScopedModel):
    """
    One customer-side ONU, as the OLT reports it.

    Identified by (olt, pon_port, onu_index), which is how the OLT names it;
    the MAC is kept beside that because an ONU moved to another port gets a new
    index and keeps its MAC, and the collector follows it by MAC.
    """

    STATUS_ONLINE = "online"
    STATUS_OFFLINE = "offline"
    STATUS_UNKNOWN = "unknown"
    STATUS_CHOICES = (
        (STATUS_ONLINE, "Online"),
        (STATUS_OFFLINE, "Offline"),
        (STATUS_UNKNOWN, "Unknown"),
    )

    # Why it last went down. The distinction is the whole point of Phase 2:
    # LOS is light gone (a cut, a pulled connector); dying gasp is the ONU
    # announcing its own power failing -- the customer's mains, not our fibre.
    DOWN_LOS = "los"
    DOWN_DYING_GASP = "dying_gasp"
    DOWN_UNKNOWN = "unknown"
    DOWN_CHOICES = (
        (DOWN_LOS, "Loss of signal"),
        (DOWN_DYING_GASP, "Power off (dying gasp)"),
        (DOWN_UNKNOWN, "Unknown"),
    )

    olt = models.ForeignKey(Olt, on_delete=models.CASCADE, related_name="onus")
    pon_port = models.PositiveSmallIntegerField()
    onu_index = models.PositiveIntegerField()
    mac_address = models.CharField(max_length=17, blank=True, db_index=True)
    serial = models.CharField(max_length=64, blank=True)
    # What the OLT has stored as its description, and the operator's own label.
    olt_description = models.CharField(max_length=128, blank=True)
    label = models.CharField(max_length=128, blank=True)
    model = models.CharField(max_length=64, blank=True)

    status = models.CharField(max_length=8, choices=STATUS_CHOICES, default=STATUS_UNKNOWN)
    distance_m = models.PositiveIntegerField(null=True, blank=True)
    rx_power_dbm = models.FloatField(null=True, blank=True)
    tx_power_dbm = models.FloatField(null=True, blank=True)
    olt_rx_power_dbm = models.FloatField(null=True, blank=True)
    temperature_c = models.FloatField(null=True, blank=True)
    voltage_v = models.FloatField(null=True, blank=True)
    last_down_reason = models.CharField(max_length=12, choices=DOWN_CHOICES, blank=True)
    last_down_at = models.DateTimeField(null=True, blank=True)
    last_up_at = models.DateTimeField(null=True, blank=True)

    # Raw counters as last read, so the next poll can take a delta. Counters
    # wrap and reset on reboot; see collector._delta.
    rx_bytes = models.BigIntegerField(null=True, blank=True)
    tx_bytes = models.BigIntegerField(null=True, blank=True)

    first_seen = models.DateTimeField(default=timezone.now)
    last_seen = models.DateTimeField(default=timezone.now)
    # Still reported by the OLT. An ONU deleted from the OLT's table stops
    # being reported; it is kept (history, customers) but marked gone.
    present = models.BooleanField(default=True)

    # Phase 3: where it physically is. Captured once at install.
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    location_note = models.CharField(max_length=255, blank=True)
    located_at = models.DateTimeField(null=True, blank=True)
    located_by = models.CharField(max_length=150, blank=True)
    splitter = models.ForeignKey(
        Splitter, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="onus")

    # Phase 4: a slow slide in received power, set by the trend sweep.
    degrading = models.BooleanField(default=False)
    degrading_since = models.DateTimeField(null=True, blank=True)
    trend_db_per_day = models.FloatField(null=True, blank=True)

    class Meta:
        ordering = ["olt", "pon_port", "onu_index"]
        constraints = [
            models.UniqueConstraint(
                fields=["olt", "pon_port", "onu_index"], name="onu_position_uniq"),
        ]
        indexes = [
            models.Index(fields=["tenant", "status"], name="onu_tenant_status_idx"),
        ]

    def __str__(self):
        return self.label or self.olt_description or f"{self.olt} {self.position}"

    @property
    def position(self):
        return f"PON{self.pon_port}:{self.onu_index}"


class OnuCustomer(TenantScopedModel):
    """
    A paying customer whose device was seen behind this ONU.

    Found, not typed: the OLT's MAC table says which device MACs sit behind
    each ONU, and billing knows every customer's device MACs (linking.py). An
    entry ages out when the device has not been seen there for 30 days, so a
    customer who moves house does not stay on the old ONU's outage list.
    """

    SOURCE_FDB = "fdb"
    SOURCE_MANUAL = "manual"
    SOURCE_CHOICES = ((SOURCE_FDB, "OLT MAC table"), (SOURCE_MANUAL, "Set by hand"))

    onu = models.ForeignKey(Onu, on_delete=models.CASCADE, related_name="customer_links")
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="onu_links")
    device_mac = models.CharField(max_length=17, blank=True)
    source = models.CharField(max_length=8, choices=SOURCE_CHOICES, default=SOURCE_FDB)
    first_seen = models.DateTimeField(default=timezone.now)
    last_seen = models.DateTimeField(default=timezone.now)

    STALE_AFTER = timedelta(days=30)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["onu", "customer"], name="onu_customer_uniq"),
        ]


class OnuReading(TenantScopedModel):
    """One poll's view of one ONU. Raw; pruned after 14 days."""

    onu = models.ForeignKey(Onu, on_delete=models.CASCADE, related_name="readings")
    at = models.DateTimeField()
    online = models.BooleanField()
    rx_power_dbm = models.FloatField(null=True, blank=True)
    tx_power_dbm = models.FloatField(null=True, blank=True)
    distance_m = models.PositiveIntegerField(null=True, blank=True)
    # Bytes moved since the previous reading, not the raw counter.
    rx_delta = models.BigIntegerField(null=True, blank=True)
    tx_delta = models.BigIntegerField(null=True, blank=True)
    seconds = models.PositiveIntegerField(null=True, blank=True)

    RETENTION = timedelta(days=14)

    class Meta:
        indexes = [models.Index(fields=["onu", "at"], name="onureading_onu_at_idx")]


class OnuHourly(TenantScopedModel):
    """An hour of one ONU, rolled up from its readings. Kept a year."""

    onu = models.ForeignKey(Onu, on_delete=models.CASCADE, related_name="hourly")
    hour = models.DateTimeField()
    samples = models.PositiveSmallIntegerField(default=0)
    online_samples = models.PositiveSmallIntegerField(default=0)
    rx_power_avg = models.FloatField(null=True, blank=True)
    rx_power_min = models.FloatField(null=True, blank=True)
    rx_bytes = models.BigIntegerField(default=0)
    tx_bytes = models.BigIntegerField(default=0)
    # Highest per-reading rate in the hour, bits per second.
    peak_rx_bps = models.BigIntegerField(default=0)
    peak_tx_bps = models.BigIntegerField(default=0)

    RETENTION = timedelta(days=366)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["onu", "hour"], name="onuhourly_onu_hour_uniq"),
        ]


class PortHourly(TenantScopedModel):
    """An hour of one PON port: traffic and how full it ran."""

    olt = models.ForeignKey(Olt, on_delete=models.CASCADE, related_name="port_hourly")
    pon_port = models.PositiveSmallIntegerField()
    hour = models.DateTimeField()
    rx_bytes = models.BigIntegerField(default=0)
    tx_bytes = models.BigIntegerField(default=0)
    peak_rx_bps = models.BigIntegerField(default=0)
    peak_tx_bps = models.BigIntegerField(default=0)
    onus_online_avg = models.FloatField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["olt", "pon_port", "hour"], name="porthourly_uniq"),
        ]


class MaintenanceWindow(TenantScopedModel):
    """
    Planned work on part of the plant, announced in advance.

    Outages inside a window's scope and time are tagged to it rather than
    opening as faults: the customers were already told, so they are not
    texted twice, and no ticket is raised for work that is the ticket.
    """

    olt = models.ForeignKey(Olt, on_delete=models.CASCADE, related_name="maintenance")
    # Null = every port on the OLT.
    pon_port = models.PositiveSmallIntegerField(null=True, blank=True)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    reason = models.CharField(max_length=255)
    compensate = models.BooleanField(default=True)
    notified_at = models.DateTimeField(null=True, blank=True)
    notified_count = models.PositiveIntegerField(default=0)
    compensated_at = models.DateTimeField(null=True, blank=True)
    created_by = models.CharField(max_length=150, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-starts_at"]

    def covers(self, onu, at):
        return (self.olt_id == onu.olt_id
                and (self.pon_port is None or self.pon_port == onu.pon_port)
                and self.starts_at <= at <= self.ends_at)


class FiberOutage(TenantScopedModel):
    """
    ONUs that went down together, as one incident.

    The unit an operator acts on is the incident, not the ONU: twelve red
    lights on one port at 14:02 are one cut, and texting twelve households
    twelve separate stories would be wrong as well as noisy.
    """

    SCOPE_ONU = "onu"
    SCOPE_PORT = "port"
    SCOPE_OLT = "olt"
    SCOPE_CHOICES = ((SCOPE_ONU, "Single ONU"), (SCOPE_PORT, "PON port"), (SCOPE_OLT, "Whole OLT"))

    CAUSE_FIBER = "fiber_cut"
    CAUSE_POWER = "power_cut"
    CAUSE_OLT = "olt_down"
    CAUSE_CUSTOMER_POWER = "customer_power"
    CAUSE_UNKNOWN = "unknown"
    CAUSE_MAINTENANCE = "maintenance"
    CAUSE_CHOICES = (
        (CAUSE_FIBER, "Fiber cut / loss of signal"),
        (CAUSE_POWER, "Area power cut"),
        (CAUSE_OLT, "OLT unreachable"),
        (CAUSE_CUSTOMER_POWER, "Customer's power off"),
        (CAUSE_UNKNOWN, "Unknown"),
        (CAUSE_MAINTENANCE, "Planned maintenance"),
    )

    olt = models.ForeignKey(Olt, on_delete=models.CASCADE, related_name="outages")
    pon_port = models.PositiveSmallIntegerField(null=True, blank=True)
    scope = models.CharField(max_length=8, choices=SCOPE_CHOICES)
    cause = models.CharField(max_length=16, choices=CAUSE_CHOICES)
    maintenance = models.ForeignKey(
        MaintenanceWindow, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="outages")
    onus = models.ManyToManyField(Onu, related_name="outages", blank=True)

    started_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)

    onus_affected = models.PositiveIntegerField(default=0)
    customers_affected = models.PositiveIntegerField(default=0)
    # Daily value of the entitled packages the affected customers hold, KES.
    revenue_at_risk_per_day = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    # Fault zone along the fibre, from the affected ONUs' ranging distances.
    nearest_m = models.PositiveIntegerField(null=True, blank=True)
    farthest_m = models.PositiveIntegerField(null=True, blank=True)
    # The farthest ONU on the same port that stayed up: the break is beyond it
    # on the affected branch (when the branches share the trunk that far).
    farthest_unaffected_m = models.PositiveIntegerField(null=True, blank=True)

    notified_at = models.DateTimeField(null=True, blank=True)
    notified_count = models.PositiveIntegerField(default=0)
    restored_notified_at = models.DateTimeField(null=True, blank=True)
    compensated_at = models.DateTimeField(null=True, blank=True)
    compensated_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-started_at"]
        indexes = [models.Index(fields=["tenant", "-started_at"], name="outage_tenant_start_idx")]

    @property
    def is_open(self):
        return self.ended_at is None

    @property
    def duration(self):
        return (self.ended_at or timezone.now()) - self.started_at


class CompensationGrant(TenantScopedModel):
    """
    Time added to one subscription for one outage or maintenance window.

    Its own row so a grant can never be given twice: the unique constraint is
    what makes compensation safe to retry after a crash halfway through.
    """

    subscription = models.ForeignKey(
        Subscription, on_delete=models.CASCADE, related_name="fiber_compensation")
    outage = models.ForeignKey(
        FiberOutage, on_delete=models.CASCADE, null=True, blank=True, related_name="grants")
    maintenance = models.ForeignKey(
        MaintenanceWindow, on_delete=models.CASCADE, null=True, blank=True, related_name="grants")
    seconds = models.PositiveIntegerField()
    granted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["subscription", "outage"], name="grant_sub_outage_uniq",
                condition=models.Q(outage__isnull=False)),
            models.UniqueConstraint(
                fields=["subscription", "maintenance"], name="grant_sub_maint_uniq",
                condition=models.Q(maintenance__isnull=False)),
        ]


class RepairTicket(TenantScopedModel):
    """Work for a technician, raised by the system or by hand."""

    STATUS_OPEN = "open"
    STATUS_ASSIGNED = "assigned"
    STATUS_RESOLVED = "resolved"
    STATUS_CHOICES = (
        (STATUS_OPEN, "Open"), (STATUS_ASSIGNED, "Assigned"), (STATUS_RESOLVED, "Resolved"))

    KIND_OUTAGE = "outage"
    KIND_DEGRADING = "degrading"
    KIND_UNLINKED = "unlinked"
    KIND_MANUAL = "manual"
    KIND_CHOICES = (
        (KIND_OUTAGE, "Outage"), (KIND_DEGRADING, "Signal degrading"),
        (KIND_UNLINKED, "Unregistered ONU"), (KIND_MANUAL, "Manual"))

    kind = models.CharField(max_length=12, choices=KIND_CHOICES)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_OPEN)
    title = models.CharField(max_length=200)
    detail = models.TextField(blank=True)
    olt = models.ForeignKey(Olt, on_delete=models.CASCADE, null=True, blank=True, related_name="tickets")
    onu = models.ForeignKey(Onu, on_delete=models.CASCADE, null=True, blank=True, related_name="tickets")
    outage = models.ForeignKey(
        FiberOutage, on_delete=models.SET_NULL, null=True, blank=True, related_name="tickets")
    assigned_to = models.CharField(max_length=150, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["tenant", "status"], name="ticket_tenant_status_idx")]
