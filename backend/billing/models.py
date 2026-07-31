from django.db import models, transaction
from django.utils import timezone
from django.contrib.auth.models import AbstractUser, UserManager
from django.core.exceptions import ValidationError
from dateutil.relativedelta import relativedelta
import logging
import secrets
import string

logger = logging.getLogger(__name__)

from billing.notifications import notify_customer
from .utils import generate_invoice_number
from .fields import EncryptedCharField
from .tenancy import TenantManager, get_current_tenant_id


# =====================================================
# TENANT
# =====================================================

class Tenant(models.Model):
    """
    One WiFi operator on the platform.

    Tenant is not itself tenant-scoped — it *is* the scope. Every other model
    in this file (except User, see below) carries a FK to it.
    """

    STATUS_CHOICES = (
        ("trial",      "Trial"),
        ("active",     "Active"),
        ("past_due",   "Past due"),
        ("restricted", "Restricted"),
        ("cancelled",  "Cancelled"),
    )

    name   = models.CharField(max_length=120)
    slug   = models.SlugField(max_length=60, unique=True)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default="trial")

    # Public identity used in subscriber notifications. Replaces the hardcoded
    # "Skylink" strings currently baked into the payment and onboarding paths.
    business_name = models.CharField(max_length=120, blank=True)
    support_phone = models.CharField(max_length=20, blank=True)
    pppoe_prefix  = models.CharField(
        max_length=10,
        default="NET",
        help_text="Prefix for generated PPPoE usernames, e.g. NET-1234-ABC",
    )

    # Identifies the operator in public URLs that carry no JWT: the M-Pesa
    # callback and the hotspot captive portal. Unguessable so a portal cannot be
    # pointed at the wrong operator by editing a URL.
    public_token = models.CharField(max_length=32, unique=True, db_index=True)

    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=20, blank=True)
    created_at    = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    # Days past an invoice due date before an operator is locked out. They are
    # reminded throughout; restriction is never the first they hear of it.
    GRACE_DAYS = 14

    @property
    def is_restricted(self):
        """
        Locked out of their own dashboard, and nothing else.

        Deliberately narrow, and narrower than it once was. Their subscribers
        keep their internet, renewals keep working, money keeps reaching their
        till, walk-up customers can still buy, and every background task keeps
        running. The only thing that stops is the operator's own admin access.

        It briefly refused new walk-up business too, reasoning that an operator
        who loses only a dashboard can ignore an unpaid invoice indefinitely.
        That reversed: refusing a member of the public standing at a hotspot
        charges the cost of the dispute to someone who is not part of it. What
        the platform withholds is its own product — the dashboard — not the
        operator's ability to serve the people in front of them.
        """
        return self.status in ("restricted", "cancelled")

    def plan_limit_exceeded(self, resource):
        """
        Whether adding one more of `resource` would exceed the operator's plan.

        Returns a message to show, or None when there is room. Only ever blocks
        *growth*: an operator over their limit keeps every subscriber they
        already have, because downgrading a plan must not disconnect people who
        are already paying.

        Unlimited is 0, and an operator with no subscription is unlimited too —
        being unbilled should not mean being capped.
        """
        subscription = (
            self.__class__.objects.filter(pk=self.pk)
            .values_list("pk", flat=True)
            .first()
            and TenantSubscription.objects.all_tenants()
            .select_related("plan")
            .filter(tenant_id=self.pk)
            .first()
        )
        if subscription is None:
            return None

        plan = subscription.plan
        if resource == "customers":
            cap = plan.max_customers
            used = Customer.objects.all_tenants().filter(tenant_id=self.pk).count()
            label = "customers"
        elif resource == "routers":
            cap = plan.max_routers
            used = RouterDevice.objects.all_tenants().filter(tenant_id=self.pk).count()
            label = "routers"
        else:
            return None

        if cap and used >= cap:
            return (
                f"Your {plan.name} plan allows {cap} {label} and you have {used}. "
                "Upgrade your plan to add more."
            )
        return None

    def save(self, *args, **kwargs):
        if not self.public_token:
            self.public_token = secrets.token_urlsafe(24)[:32]
        if not self.business_name:
            self.business_name = self.name
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class TenantStatusChange(models.Model):
    """
    Audit trail for every change to an operator's standing.

    Restriction is a commercial action against a business, so "you cut us off
    without warning" needs an answer with dates on it — who changed what, when,
    and why.
    """
    tenant = models.ForeignKey(
        "Tenant", on_delete=models.CASCADE, related_name="status_changes"
    )
    from_status = models.CharField(max_length=12)
    to_status = models.CharField(max_length=12)
    reason = models.TextField(blank=True)
    # Null when the change was automatic rather than a person's decision.
    changed_by = models.ForeignKey(
        "User", null=True, blank=True, on_delete=models.SET_NULL
    )
    automatic = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["tenant", "-created_at"], name="tstatus_tenant_time_idx"),
        ]

    def __str__(self):
        return f"{self.tenant}: {self.from_status} -> {self.to_status}"


class ImpersonationLog(models.Model):
    """
    Every request a platform account makes while viewing as an operator.

    Impersonation means platform staff reading — and potentially changing —
    a real business's customer records, including phone numbers and payment
    history. That is exactly the access that has to be reconstructable
    afterwards, so each request is recorded rather than just the act of
    starting a session.

    One row per request is deliberate. It is more rows than logging only the
    start, but "who looked at this customer's details, and when" is answerable
    from it, and at this scale support sessions are rare.
    """
    platform_user = models.ForeignKey(
        "User", on_delete=models.SET_NULL, null=True, related_name="impersonations"
    )
    tenant = models.ForeignKey(
        "Tenant", on_delete=models.CASCADE, related_name="impersonations"
    )
    method = models.CharField(max_length=10)
    path = models.CharField(max_length=255)
    reason = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["tenant", "-created_at"], name="imp_tenant_time_idx"),
            models.Index(fields=["platform_user", "-created_at"], name="imp_user_time_idx"),
        ]

    def __str__(self):
        return f"{self.platform_user} viewed {self.tenant} — {self.method} {self.path}"


class AdminActionLog(models.Model):
    """
    Administrative acts on accounts — the ones that change who can get in.

    Distinct from ImpersonationLog, which records reading. This records
    changing: a password reset, a role change, an account disabled. Those are
    the events someone asks about after the fact ("who reset my password, and
    when?"), and none of them are reconstructable from the accounts themselves,
    because each one overwrites the state that preceded it.

    Not tenant-scoped: a platform owner acting on an operator's account belongs
    in one timeline with an operator admin acting on their own staff, and the
    actor is frequently a platform account with no tenant at all.
    """

    RESET_PASSWORD = "reset_password"
    CHANGE_PASSWORD = "change_password"
    CHANGE_USERNAME = "change_username"
    CREATE_USER = "create_user"
    DISABLE_USER = "disable_user"
    ENABLE_USER = "enable_user"
    CHANGE_ROLE = "change_role"
    UPDATE_OPERATOR = "update_operator"
    CONFIGURE_PAYMENTS = "configure_payments"
    CHANGE_PLAN = "change_plan"

    ACTION_CHOICES = (
        (RESET_PASSWORD, "Reset password"),
        (CHANGE_PASSWORD, "Changed own password"),
        (CHANGE_USERNAME, "Changed username"),
        (CREATE_USER, "Created user"),
        (DISABLE_USER, "Disabled user"),
        (ENABLE_USER, "Enabled user"),
        (CHANGE_ROLE, "Changed role"),
        (UPDATE_OPERATOR, "Updated operator details"),
        (CONFIGURE_PAYMENTS, "Configured payment credentials"),
        (CHANGE_PLAN, "Changed plan"),
    )

    actor = models.ForeignKey(
        "User", on_delete=models.SET_NULL, null=True, related_name="admin_actions"
    )
    action = models.CharField(max_length=32, choices=ACTION_CHOICES)
    # Who it was done to. SET_NULL so deleting an account does not erase the
    # record that something was done to it.
    target_user = models.ForeignKey(
        "User", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="admin_actions_received",
    )
    target_tenant = models.ForeignKey(
        "Tenant", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="admin_actions",
    )
    # A label that survives the target being deleted.
    target_label = models.CharField(max_length=150, blank=True)
    detail = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["-created_at"], name="adminlog_time_idx"),
            models.Index(fields=["target_tenant", "-created_at"],
                         name="adminlog_tenant_time_idx"),
        ]

    def __str__(self):
        return f"{self.actor} {self.action} {self.target_label}"


def record_admin_action(actor, action, *, target_user=None, target_tenant=None, detail=""):
    """
    Write one audit row. Never raises — an audit failure must not roll back the
    action it describes, but it must be visible in the logs.
    """
    try:
        return AdminActionLog.objects.create(
            actor=actor if getattr(actor, "pk", None) else None,
            action=action,
            target_user=target_user,
            target_tenant=target_tenant or getattr(target_user, "tenant", None),
            target_label=str(target_user or target_tenant or ""),
            detail=detail[:255],
        )
    except Exception:
        logger.exception("[audit] could not record %s by %s", action, actor)
        return None


def set_tenant_status(tenant, new_status, *, reason="", changed_by=None, automatic=False):
    """Change an operator's standing and record why. Returns True if it moved."""
    if tenant.status == new_status:
        return False

    TenantStatusChange.objects.create(
        tenant=tenant,
        from_status=tenant.status,
        to_status=new_status,
        reason=reason,
        changed_by=changed_by,
        automatic=automatic,
    )
    tenant.status = new_status
    tenant.save(update_fields=["status"])
    return True


def default_tenant():
    """
    Owner of a new row when none was passed explicitly.

    Order matters:

    1. The tenant in context — set by middleware on a request, or by
       `tenant_context(...)` in a task or command. This is the normal path and
       is why serializers using `fields = "__all__"` keep working without the
       frontend ever sending a tenant.
    2. Failing that, the sole tenant, if the platform has exactly one. Keeps
       management commands and tests working on a single-operator install.
    3. Otherwise refuse. Guessing here would silently attach a customer,
       payment or router to the wrong operator.
    """
    tenant_id = get_current_tenant_id()
    if tenant_id is not None:
        return tenant_id

    count = Tenant.objects.count()

    if count == 1:
        return Tenant.objects.values_list("pk", flat=True).first()

    if count == 0:
        raise RuntimeError(
            "No Tenant exists. Run migrations, or create one before writing "
            "tenant-scoped rows."
        )

    raise RuntimeError(
        f"No tenant in context and {count} tenants exist, so the owner of this "
        "row is ambiguous. Wrap the write in tenant_context(...) or pass "
        "tenant=... explicitly."
    )


class TenantScopedModel(models.Model):
    """
    Base for every model owned by exactly one operator.

    PROTECT, not CASCADE: removing an operator must never silently destroy
    billing history. Deactivate the tenant instead; deletion is a separate,
    deliberate procedure.
    """

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.PROTECT,
        related_name="+",
        default=default_tenant,
        # blank=True keeps DRF from marking it required on `fields = "__all__"`
        # serializers; the default supplies it.
        blank=True,
    )

    # Filters by the tenant in context automatically. Reaching across operators
    # requires .all_tenants(), so an unscoped read is visible at the call site
    # instead of being the accidental default.
    #
    # Django's _base_manager stays a plain unfiltered Manager, so following a
    # FK (invoice.customer) never fails because of scoping — only queries do.
    objects = TenantManager()

    class Meta:
        abstract = True


# =====================================================
# USER MODEL
# =====================================================

class BillingUserManager(UserManager):
    """
    Makes `manage.py createsuperuser` produce a platform account.

    Without this it would build a user with the default operator role and no
    tenant, which the user_role_matches_tenant_presence constraint rejects —
    so creating a superuser would simply fail. A Django superuser is a
    platform-level account by nature, so that is what it becomes.
    """

    def create_superuser(self, username, email=None, password=None, **extra_fields):
        extra_fields.setdefault("role", "platform_owner")
        extra_fields.setdefault("tenant", None)
        return super().create_superuser(username, email, password, **extra_fields)


class User(AbstractUser):
    # Platform roles run the platform itself and see every operator.
    PLATFORM_OWNER = "platform_owner"
    PLATFORM_STAFF = "platform_staff"
    # Operator roles run one WiFi business and see only their own.
    TENANT_ADMIN = "tenant_admin"
    TENANT_STAFF = "tenant_staff"
    CUSTOMER = "customer"

    PLATFORM_ROLES = (PLATFORM_OWNER, PLATFORM_STAFF)
    TENANT_ROLES = (TENANT_ADMIN, TENANT_STAFF, CUSTOMER)

    ROLE_CHOICES = (
        (PLATFORM_OWNER, "Platform Owner"),
        (PLATFORM_STAFF, "Platform Staff"),
        (TENANT_ADMIN, "Operator Admin"),
        (TENANT_STAFF, "Operator Staff"),
        (CUSTOMER, "Customer"),
    )

    role = models.CharField(
        max_length=20,
        choices=ROLE_CHOICES,
        default=TENANT_ADMIN,
    )

    # Set when someone else chose this account's password — a platform owner
    # resetting a forgotten one. Cleared the moment the holder sets their own.
    must_change_password = models.BooleanField(default=False)

    # Bumped whenever this account's credentials or access change, and carried
    # as a JWT claim. Without it a password reset would not end the sessions it
    # was meant to end: access tokens live 30 minutes and refresh tokens a day,
    # and the token blacklist app is not installed, so an old token would keep
    # working for up to 24 hours after the password it was issued against had
    # been replaced. That is precisely the window a reset exists to close.
    token_version = models.PositiveIntegerField(default=0)

    objects = BillingUserManager()

    def invalidate_sessions(self):
        """
        End every session this account currently has.

        Call after any change that should not be survivable with an old token:
        a password change or reset, or disabling the account.
        """
        self.token_version = models.F("token_version") + 1
        self.save(update_fields=["token_version"])
        self.refresh_from_db(fields=["token_version"])

    @property
    def is_platform_staff(self):
        """Sees every operator. Queries run unscoped for these accounts."""
        return self.role in self.PLATFORM_ROLES

    @property
    def is_tenant_admin(self):
        return self.role == self.TENANT_ADMIN

    @property
    def is_tenant_member(self):
        return self.role in (self.TENANT_ADMIN, self.TENANT_STAFF)

    class Meta(AbstractUser.Meta):
        constraints = [
            # A NULL tenant means "platform staff", and platform staff run
            # unscoped — they see every operator. So an operator account that
            # somehow ended up with a NULL tenant would silently gain
            # platform-wide visibility. Enforced in the database rather than
            # only in application code, because that is a privilege boundary.
            #
            # Written as check= for readability; Django 5.1+ serialises it to
            # condition= in the migration, which is why requirements.txt now
            # floors Django at 5.1.
            models.CheckConstraint(
                check=(
                    (models.Q(role__in=("platform_owner", "platform_staff"))
                     & models.Q(tenant__isnull=True))
                    | (models.Q(role__in=("tenant_admin", "tenant_staff", "customer"))
                       & models.Q(tenant__isnull=False))
                ),
                name="user_role_matches_tenant_presence",
            ),
        ]

    def clean(self):
        super().clean()
        # Mirrors the constraint above so forms and serializers report this as
        # a validation error rather than an IntegrityError.
        if self.role in self.PLATFORM_ROLES and self.tenant_id is not None:
            raise ValidationError(
                {"tenant": "Platform accounts must not belong to an operator."}
            )
        if self.role in self.TENANT_ROLES and self.tenant_id is None:
            raise ValidationError(
                {"tenant": "Operator accounts must belong to an operator."}
            )

    # NULL means platform staff, who see every operator. Deliberately stays
    # nullable — unlike the scoped models it is never tightened.
    #
    # The 0026 backfill assigns every *existing* user to tenant #1 rather than
    # leaving them NULL. That fails closed: an account accidentally left NULL
    # would gain platform-wide visibility once scoping lands in phase 2.
    # Designating real platform staff is a phase 4 task.
    tenant = models.ForeignKey(
        "Tenant",
        on_delete=models.PROTECT,
        related_name="users",
        null=True,
        blank=True,
    )

    def __str__(self):
        return f"{self.username} ({self.role})"


# =====================================================
# ROUTER
# =====================================================

class Station(TenantScopedModel):
    """
    One physical site belonging to an operator.

    An operator with a site in two towns is still one business: one login, one
    bill, one M-Pesa till, one set of packages. A station groups the hardware at
    a place so they can be told apart when monitoring them — it is not a
    second account and deliberately owns none of the commercial concerns.

    Optional throughout. An operator with a single site never creates one, and
    a router with no station behaves exactly as it did before stations existed.
    Nothing backfills a "Main Station" onto operators who did not ask for one.
    """

    name = models.CharField(max_length=100)
    # Short label for lists and generated names, e.g. KLF for Kilifi Town.
    code = models.CharField(max_length=12, blank=True)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            # Two sites called "Kilifi" within one operator would make the
            # grouping useless. Scoped to the tenant, so different operators may
            # each have a Kilifi.
            models.UniqueConstraint(
                fields=["tenant", "name"], name="unique_station_name_per_tenant"
            ),
        ]

    def __str__(self):
        return self.name


class RouterDevice(TenantScopedModel):
    name = models.CharField(max_length=100)
    ip_address = models.GenericIPAddressField()
    username = models.CharField(max_length=100)
    password = EncryptedCharField()  # encrypted at rest; decrypted transparently on read
    api_port = models.IntegerField(default=8728)

    # failover priority: 1 = best
    priority = models.PositiveIntegerField(default=1)

    # health info
    is_active = models.BooleanField(default=True)
    is_online = models.BooleanField(default=False)
    last_seen = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default="")
    max_pppoe_sessions = models.PositiveIntegerField(default=0)  # 0 = unlimited

    # Which site this box is at. NULL means the operator has not divided their
    # estate into sites, which is the normal case for a single-location
    # business — router selection then behaves exactly as it always has.
    #
    # SET_NULL rather than CASCADE: deleting a site must never delete the
    # routers standing in it, along with every customer attached to them.
    station = models.ForeignKey(
        Station, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="routers",
    )

    def __str__(self):
        return self.name

    def record_health(self, online, *, error="", cause=""):
        """
        Update this router's health, and log the change if it is one.

        The single place is_online is written. It used to be set from six
        different call sites, each doing its own save, which is why there was
        no history: last_error is one field, so every failure destroyed the
        previous one and nothing recorded that a router had gone down at all.

        Only transitions are written to RouterEvent. The health sweep runs every
        two minutes, so logging each probe would add around 720 rows per router
        per day, all of them saying the same thing as the row before. The edges
        are where the information is.

        Returns True if the state changed.
        """
        was_online = self.is_online
        changed = was_online != online

        self.is_online = online
        fields = ["is_online", "last_error"]
        if online:
            self.last_seen = timezone.now()
            self.last_error = ""
            fields.append("last_seen")
        else:
            self.last_error = str(error)[:2000]
        self.save(update_fields=fields)

        if changed:
            try:
                RouterEvent.objects.create(
                    tenant_id=self.tenant_id,
                    router=self,
                    kind=RouterEvent.CAME_ONLINE if online else RouterEvent.WENT_OFFLINE,
                    cause="" if online else (cause or RouterEvent.CAUSE_ERROR),
                    detail="" if online else str(error)[:255],
                )
            except Exception:
                # Losing the log entry must not fail the health check that
                # produced it, but it should be visible.
                logger.exception("[router-health] could not record event for %s", self)

        return changed


class RouterEvent(TenantScopedModel):
    """
    When a router changed state, and why.

    RouterDevice carries only current state — is_online, and a last_error that
    each new failure overwrites — so before this there was no way to answer
    "has this router been flapping?" or "how long were we down last night?".
    The health sweep ran every two minutes and threw its result away.

    Transitions only. See RouterDevice.record_health.
    """

    CAME_ONLINE = "came_online"
    WENT_OFFLINE = "went_offline"
    KIND_CHOICES = (
        (CAME_ONLINE, "Came online"),
        (WENT_OFFLINE, "Went offline"),
    )

    # Why it went down. Distinguishing "we could not open a socket" from "the
    # router answered and rejected our credentials" is the difference between
    # a network problem and a configuration one.
    CAUSE_UNREACHABLE = "unreachable"
    CAUSE_AUTH_FAILED = "auth_failed"
    CAUSE_ERROR = "error"
    CAUSE_CHOICES = (
        (CAUSE_UNREACHABLE, "Unreachable"),
        (CAUSE_AUTH_FAILED, "Authentication failed"),
        (CAUSE_ERROR, "Error"),
    )

    router = models.ForeignKey(
        RouterDevice, on_delete=models.CASCADE, related_name="events"
    )
    kind = models.CharField(max_length=16, choices=KIND_CHOICES)
    cause = models.CharField(max_length=16, choices=CAUSE_CHOICES, blank=True)
    detail = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["router", "-created_at"], name="revent_router_time_idx"),
            models.Index(fields=["tenant", "-created_at"], name="revent_tenant_time_idx"),
        ]

    def __str__(self):
        return f"{self.router} {self.kind} at {self.created_at}"


def router_uptime(router, since):
    """
    Availability for one router since `since`, derived from its transitions.

    Deliberately not a single percentage pulled from nowhere. The transition log
    says when state changed, so downtime is the sum of the offline spans, and
    the starting state is whatever the last event before the window said — or
    the current state if there were no events at all, which is the common case
    for a router that has simply been up the whole time.
    """
    now = timezone.now()
    window = (now - since).total_seconds()
    if window <= 0:
        return {"uptime_percent": 100.0, "outages": 0, "downtime_seconds": 0}

    events = list(
        RouterEvent.objects.all_tenants()
        .filter(router=router, created_at__gte=since)
        .order_by("created_at")
    )

    prior = (
        RouterEvent.objects.all_tenants()
        .filter(router=router, created_at__lt=since)
        .order_by("-created_at")
        .first()
    )
    if prior is not None:
        online = prior.kind == RouterEvent.CAME_ONLINE
    elif events:
        # No history before the window: the state at the start was the opposite
        # of whatever the first transition inside it moved to.
        online = events[0].kind == RouterEvent.WENT_OFFLINE
    else:
        online = router.is_online

    downtime = 0.0
    outages = 0
    cursor = since
    for event in events:
        if event.kind == RouterEvent.WENT_OFFLINE and online:
            online = False
            outages += 1
            cursor = event.created_at
        elif event.kind == RouterEvent.CAME_ONLINE and not online:
            downtime += (event.created_at - cursor).total_seconds()
            online = True
    if not online:
        downtime += (now - cursor).total_seconds()

    return {
        "uptime_percent": round(max(0.0, 100.0 * (1 - downtime / window)), 2),
        "outages": outages,
        "downtime_seconds": int(downtime),
    }


# =====================================================
# CUSTOMER
# =====================================================

class Customer(TenantScopedModel):
    CONNECTION_TYPES = (
        ("pppoe", "PPPoE"),
        ("hotspot", "Hotspot"),
    )

    user = models.OneToOneField(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="customer_profile",
    )

    full_name = models.CharField(max_length=255)
    # Unique per operator, not globally — the same person may subscribe to two
    # different operators on the platform. See Meta.constraints.
    phone = models.CharField(max_length=20)

    connection_type = models.CharField(
        max_length=10,
        choices=CONNECTION_TYPES,
        default="pppoe",
    )

    router = models.ForeignKey(
        RouterDevice,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )

    # Identifiers (only one should be used based on connection_type)
    pppoe_username = models.CharField(max_length=100, blank=True)
    pppoe_password = EncryptedCharField(blank=True)  # encrypted at rest
    hotspot_username = models.CharField(max_length=100, blank=True)

    # Optional caps
    custom_data_cap_gb = models.PositiveIntegerField(null=True, blank=True)

    status = models.CharField(
        max_length=10,
        choices=(("active", "Active"), ("expired", "Expired")),
        default="active",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "status"],          name="customer_tenant_status_idx"),
            models.Index(fields=["tenant", "pppoe_username"],  name="customer_tenant_pppoe_idx"),
            models.Index(fields=["tenant", "connection_type"], name="customer_tenant_conn_idx"),
        ]
        constraints = [
            # A device MAC identifies exactly one customer *of one operator*.
            # Without this, the public hotspot endpoints resolve a subscriber
            # with .filter(hotspot_username=mac).first() and get whichever row
            # the database happens to return — disclosing one customer's status
            # to another and granting access against the wrong subscription.
            #
            # Partial: hotspot_username is blank for every PPPoE customer, and
            # empty strings do collide in a plain unique index (unlike NULL).
            models.UniqueConstraint(
                fields=["tenant", "hotspot_username"],
                condition=~models.Q(hotspot_username=""),
                name="customer_tenant_hotspot_username_uniq",
            ),
            # Was globally unique. The same person may be a subscriber of two
            # different operators, so uniqueness belongs inside the tenant.
            models.UniqueConstraint(
                fields=["tenant", "phone"],
                name="customer_tenant_phone_uniq",
            ),
            # New. Uniqueness was previously enforced only by a race-prone
            # .exists() check in generate_pppoe_credentials(), with no database
            # constraint at all. PPPoE usernames must be unique on a router, and
            # routers belong to one operator, so tenant scope is the right level.
            models.UniqueConstraint(
                fields=["tenant", "pppoe_username"],
                condition=~models.Q(pppoe_username=""),
                name="customer_tenant_pppoe_username_uniq",
            ),
        ]

    def clean(self):
        # Enforce data integrity: only one identifier should be set
        if self.connection_type == "pppoe" and self.hotspot_username:
            raise ValidationError("Hotspot username should be empty for PPPoE customers")
        if self.connection_type == "hotspot" and self.pppoe_username:
            raise ValidationError("PPPoE username should be empty for hotspot customers")

    def save(self, *args, **kwargs):
        # Skip full validation on partial updates — only validate complete saves
        if not kwargs.get("update_fields"):
            self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.full_name


# =====================================================
# ROUTER FAILOVER LOG
# =====================================================

class RouterFailoverLog(TenantScopedModel):
    customer = models.ForeignKey(
        Customer,
        on_delete=models.CASCADE,
        related_name="failover_logs"
    )
    from_router = models.ForeignKey(
        RouterDevice,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="failover_from"
    )
    to_router = models.ForeignKey(
        RouterDevice,
        on_delete=models.CASCADE,
        related_name="failover_to"
    )
    reason = models.CharField(max_length=50)  # auto_failover | admin_manual
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.customer.full_name} → {self.to_router.name}"


# =====================================================
# PACKAGE
# =====================================================

class Package(TenantScopedModel):
    DURATION_UNITS = [
        ("minutes", "Minutes"),
        ("hours", "Hours"),
        ("days", "Days"),
        ("weeks", "Weeks"),
        ("months", "Months"),
        ("years", "Years"),
    ]

    name = models.CharField(max_length=100)

    download_speed = models.PositiveIntegerField(help_text="Mbps")
    upload_speed = models.PositiveIntegerField(help_text="Mbps")

    price = models.DecimalField(max_digits=10, decimal_places=2)

    duration_value = models.PositiveIntegerField(
        help_text="Number of time units (e.g. 30, 12, 1)"
    )
    duration_unit = models.CharField(
        max_length=10,
        choices=DURATION_UNITS,
        default="days",
    )

    monthly_data_cap_gb = models.PositiveIntegerField(
        default=0,
        help_text="0 = unlimited"
    )

    is_hotspot = models.BooleanField(
        default=False,
        help_text="Is this a hotspot-only package?"
    )

    def clean(self):
        if self.duration_value <= 0:
            raise ValidationError("Duration value must be greater than zero")

    def calculate_expiry(self, start_date=None):
        start = start_date or timezone.now()

        if self.duration_unit == "minutes":
            return start + timezone.timedelta(minutes=self.duration_value)
        if self.duration_unit == "hours":
            return start + timezone.timedelta(hours=self.duration_value)
        if self.duration_unit == "days":
            return start + timezone.timedelta(days=self.duration_value)
        if self.duration_unit == "weeks":
            return start + timezone.timedelta(weeks=self.duration_value)
        if self.duration_unit == "months":
            return start + relativedelta(months=self.duration_value)
        if self.duration_unit == "years":
            return start + relativedelta(years=self.duration_value)

        raise ValueError("Invalid duration unit")

    def __str__(self):
        return (
            f"{self.name} | "
            f"{self.download_speed}/{self.upload_speed} Mbps | "
            f"{self.duration_value} {self.duration_unit}"
        )
# =====================================================
# SUBSCRIPTION
# =====================================================

class Subscription(TenantScopedModel):
    STATUS_CHOICES = (
        ("active", "Active"),
        ("expired", "Expired"),
        ("suspended", "Suspended"),
    )

    customer = models.ForeignKey(
        Customer, on_delete=models.CASCADE, related_name="subscriptions"
    )
    package = models.ForeignKey(Package, on_delete=models.CASCADE)
    start_date = models.DateTimeField(default=timezone.now)
    expiry_date = models.DateTimeField(blank=True, null=True)
    status = models.CharField(
        max_length=10, choices=STATUS_CHOICES, default="active"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "status"],                  name="sub_tenant_status_idx"),
            models.Index(fields=["tenant", "expiry_date"],             name="sub_tenant_expiry_idx"),
            models.Index(fields=["tenant", "status", "expiry_date"],   name="sub_tenant_status_expiry_idx"),
            # Kept WITHOUT tenant: enforce_subscription_expiry sweeps every
            # operator, so it needs an index that does not lead with tenant.
            models.Index(fields=["status", "expiry_date"],              name="subscription_status_expiry_idx"),
        ]

    def save(self, *args, **kwargs):
        creating = self.pk is None

        if not self.expiry_date:
            self.expiry_date = self.package.calculate_expiry(self.start_date)

        # Keep DB writes atomic
        with transaction.atomic():
            super().save(*args, **kwargs)

            # Local imports to avoid circular import issues
            from .models import Invoice
            from .services.pppoe_service import generate_pppoe_credentials

            # Auto-create invoice only on creation
            if creating:
                Invoice.objects.create(
                    customer=self.customer,
                    subscription=self,
                    invoice_number=generate_invoice_number(),
                    total_amount=self.package.price,
                    payment_status="unpaid",
                )

            # Auto-generate PPPoE credentials only once
            if creating and self.customer.connection_type == "pppoe":
                if not self.customer.pppoe_username:
                    username, password = generate_pppoe_credentials(self.customer)
                    self.customer.pppoe_username = username
                    self.customer.pppoe_password = password
                    self.customer.save()

        # External side-effects outside the DB transaction (production safety)
        if creating and self.customer.connection_type == "pppoe":
            if self.customer.pppoe_username and self.customer.pppoe_password:
                try:
                    notify_customer(
                        self.customer.phone,
                        (
                            f"Your {self.customer.tenant.business_name or self.customer.tenant.name} "
                            "PPPoE account is ready!\n"
                            f"Username: {self.customer.pppoe_username}\n"
                            f"Password: {self.customer.pppoe_password}\n"
                            f"Package: {self.package.name}\n"
                            f"Expires: {self.expiry_date}"
                        )
                    )
                except Exception:
                    # Notification failures should not break billing state
                    pass

    def __str__(self):
        return f"{self.customer.full_name} - {self.package.name}"


# =====================================================
# INVOICE
# =====================================================

class Invoice(TenantScopedModel):
    PAYMENT_STATUS = (
        ("paid", "Paid"),
        ("unpaid", "Unpaid"),
        ("pending", "Pending"),
    )

    customer = models.ForeignKey(
        Customer, on_delete=models.CASCADE, related_name="invoices"
    )
    subscription = models.OneToOneField(
        Subscription, on_delete=models.CASCADE, related_name="invoice"
    )
    invoice_number = models.CharField(max_length=50, unique=True)
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)
    payment_status = models.CharField(
        max_length=10, choices=PAYMENT_STATUS, default="unpaid"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "payment_status"], name="invoice_tenant_status_idx"),
            models.Index(fields=["tenant", "created_at"],     name="invoice_tenant_created_idx"),
        ]

    def __str__(self):
        return self.invoice_number


# =====================================================
# VOUCHER UTILS
# =====================================================

def generate_voucher_code():
    prefix = "WIFI"
    random_part = "".join(
        secrets.choice(string.ascii_uppercase + string.digits)
        for _ in range(6)
    )
    return f"{prefix}-{random_part}"


# =====================================================
# VOUCHER
# =====================================================

class Voucher(TenantScopedModel):
    code = models.CharField(max_length=30, unique=True)
    subscription = models.ForeignKey(
        Subscription, on_delete=models.CASCADE, related_name="vouchers"
    )

    bound_mac = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        help_text="MAC address bound on first use",
    )

    first_used_at = models.DateTimeField(null=True, blank=True)

    expires_at = models.DateTimeField()
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def is_valid(self):
        return (
            self.is_active
            and timezone.now() <= self.expires_at
        )

    def __str__(self):
        return self.code

# =====================================================
# PAYMENT
# =====================================================

class Payment(TenantScopedModel):
    PAYMENT_METHODS = (
        ("cash", "Cash"),
        ("mpesa", "M-Pesa"),
        ("bank", "Bank"),
    )

    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="payments")
    subscription = models.ForeignKey(Subscription, on_delete=models.CASCADE, related_name="payments")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    method = models.CharField(max_length=10, choices=PAYMENT_METHODS)
    reference = models.CharField(max_length=100, blank=True)
    paid_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "paid_at"],           name="payment_tenant_paid_idx"),
            models.Index(fields=["tenant", "method"],            name="payment_tenant_method_idx"),
            models.Index(fields=["tenant", "paid_at", "method"], name="payment_tenant_paid_method_idx"),
        ]

    def save(self, *args, **kwargs):
        creating = self.pk is None
        super().save(*args, **kwargs)

        if not creating:
            return

        from billing.router_service import (
            enable_customer_access,
            pick_best_router_for_new_customer,
        )

        customer = self.customer
        subscription = self.subscription
        package = subscription.package
        voucher_code = None

        # Resolve the best router BEFORE opening a DB transaction.
        # pick_best_router_for_new_customer() makes TCP connections to every
        # router (up to 5 s per router). Holding DB row locks during that
        # blocking I/O would block concurrent payments for other customers.
        assigned_router = None
        if not customer.router:
            # Must pass the customer: selection is scoped to their operator's
            # routers, otherwise a payment could provision them onto another
            # operator's hardware.
            router, _ = pick_best_router_for_new_customer(customer)
            assigned_router = router

        # DB-only changes inside the transaction (no blocking I/O here)
        with transaction.atomic():
            invoice = subscription.invoice
            invoice.payment_status = "paid"
            invoice.save(update_fields=["payment_status"])

            subscription.status = "active"
            subscription.save(update_fields=["status"])

            if assigned_router:
                customer.router = assigned_router
                customer.save(update_fields=["router"])

            # Voucher is a DB write — belongs inside the transaction
            if customer.connection_type == "hotspot":
                voucher = Voucher.objects.create(
                    code=generate_voucher_code(),
                    subscription=subscription,
                    expires_at=subscription.expiry_date,
                )
                voucher_code = voucher.code

        # Capture primitives for the closure (avoids stale ORM objects)
        customer_id = customer.id
        phone = customer.phone
        pkg_name = package.name
        expiry = subscription.expiry_date
        pppoe_username = customer.pppoe_username
        pppoe_password = customer.pppoe_password
        connection_type = customer.connection_type
        _voucher_code = voucher_code

        # Branding belongs to the operator, not the platform. This customer is
        # theirs and has never heard of us, so the message must carry their
        # business name and their support number.
        tenant_id = customer.tenant_id
        brand = customer.tenant.business_name or customer.tenant.name
        support = customer.tenant.support_phone

        def _post_payment_effects():
            # Runs after the DB transaction commits — safe to call external systems
            from billing.models import Customer as _Customer
            from billing.tenancy import tenant_context

            fresh = (
                _Customer.objects.all_tenants()
                .select_related("router")
                .get(id=customer_id)
            )

            support_line = f"\nSupport: {support}" if support else ""

            if connection_type == "hotspot" and _voucher_code:
                message = (
                    f"Welcome to {brand}!\n\n"
                    f"Package: {pkg_name}\n"
                    f"Valid Until: {expiry:%d %b %Y %I:%M %p}\n\n"
                    f"Voucher Code: {_voucher_code}\n\n"
                    "Just stay connected — auto-login will work."
                    f"{support_line}"
                )
            elif connection_type == "pppoe":
                message = (
                    f"Welcome to {brand}!\n\n"
                    "Your PPPoE account is ready:\n"
                    f"Username: {pppoe_username}\n"
                    f"Password: {pppoe_password}\n\n"
                    f"Package: {pkg_name}\n"
                    f"Valid Until: {expiry:%d %b %Y %I:%M %p}\n\n"
                    "Use these details on your router."
                    f"{support_line}"
                )
            else:
                return

            # Their routers, their SMS credentials.
            with tenant_context(tenant_id):
                # Through the task rather than inline, so a router that is
                # briefly unreachable — a reboot, a power cut — is retried
                # instead of costing this customer the access they just paid
                # for. If it still cannot, the operator is told.
                try:
                    from billing.tasks.provisioning import ensure_customer_access_task
                    ensure_customer_access_task.delay(customer_id, reason="payment")
                except Exception:
                    # No broker reachable. Better to try once here than to
                    # leave a paying customer with nothing because the queue
                    # was down.
                    logger.exception(
                        "[payment] could not queue provisioning for %s, "
                        "attempting inline", customer_id)
                    enable_customer_access(fresh)

                try:
                    notify_customer(phone, message)
                except Exception:
                    pass

        transaction.on_commit(_post_payment_effects)

    def __str__(self):
        return f"{self.customer.full_name} - {self.amount}"


# =====================================================
# EXPIRY REMINDER LOG
# =====================================================

class ExpiryReminderLog(TenantScopedModel):
    REMINDER_TYPES = (
        ("3_days", "3 Days Before"),
        ("1_day", "1 Day Before"),
    )

    subscription = models.ForeignKey(
        Subscription, on_delete=models.CASCADE, related_name="reminder_logs"
    )
    reminder_type = models.CharField(max_length=10, choices=REMINDER_TYPES)
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("subscription", "reminder_type")

    def __str__(self):
        return f"{self.subscription} - {self.reminder_type}"


# =====================================================
# ACCESS AUDIT LOG
# =====================================================

class AccessAuditLog(TenantScopedModel):
    ACTION_CHOICES = (
        ("deactivate", "Deactivate"),
        ("activate", "Activate"),
    )

    customer = models.ForeignKey(
        Customer, on_delete=models.CASCADE, related_name="audit_logs"
    )
    subscription = models.ForeignKey(
        "Subscription", on_delete=models.SET_NULL, null=True, blank=True
    )
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.customer.full_name} — {self.action} — {self.created_at:%Y-%m-%d %H:%M}"


# =====================================================
# SYSTEM SETTINGS
# =====================================================

class SystemSetting(TenantScopedModel):
    """
    Generic key/value store for system configuration:
    - MPESA_CONSUMER_KEY
    - MPESA_CONSUMER_SECRET
    - MPESA_SHORTCODE
    - MPESA_PASSKEY
    - MPESA_CALLBACK_URL
    - AT_USERNAME
    - AT_API_KEY
    - WHATSAPP_TOKEN
    - WHATSAPP_PHONE_ID
    """
    # Unique per operator, not globally: each operator holds their own M-Pesa
    # and messaging credentials under the same key names, so their payments
    # settle to their own till.
    key = models.CharField(max_length=200)
    value = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "key"],
                name="systemsetting_tenant_key_uniq",
            ),
        ]

    def __str__(self):
        return self.key


# =====================================================
# MPESA TRANSACTIONS (RECONCILIATION)
# =====================================================

class MpesaTransaction(TenantScopedModel):
    RESULT_STATUS = (
        ("success", "Success"),
        ("failed", "Failed"),
    )

    mpesa_receipt = models.CharField(max_length=50, unique=True, blank=True, null=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    phone_number = models.CharField(max_length=20, blank=True, null=True)

    account_reference = models.CharField(max_length=100, blank=True, null=True)
    merchant_request_id = models.CharField(max_length=100, blank=True, null=True)
    checkout_request_id = models.CharField(max_length=100, blank=True, null=True)

    invoice = models.ForeignKey(
        Invoice,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mpesa_transactions",
    )
    payment = models.ForeignKey(
        Payment,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mpesa_transactions",
    )

    processed = models.BooleanField(default=False)
    error_message = models.TextField(blank=True)

    raw_payload = models.JSONField()
    status = models.CharField(max_length=10, choices=RESULT_STATUS)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "status"],              name="mpesa_tenant_status_idx"),
            models.Index(fields=["tenant", "processed"],           name="mpesa_tenant_processed_idx"),
            models.Index(fields=["tenant", "status", "processed"], name="mpesa_tenant_status_proc_idx"),
            models.Index(fields=["tenant", "created_at"],          name="mpesa_tenant_created_idx"),
        ]

    def __str__(self):
        return f"{self.mpesa_receipt or 'NO-RECEIPT'} - {self.status}"


# =====================================================
# USAGE TRACKING
# =====================================================

class PPPoEUsageSnapshot(TenantScopedModel):
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="usage_snapshots")
    date = models.DateField()
    rx_bytes = models.BigIntegerField(default=0)
    tx_bytes = models.BigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("customer", "date")


class PPPoEUsageState(TenantScopedModel):
    """
    Stores last seen router counters so we can compute deltas safely.
    """
    customer = models.OneToOneField("Customer", on_delete=models.CASCADE, related_name="pppoe_usage_state")
    last_rx_bytes = models.BigIntegerField(default=0)
    last_tx_bytes = models.BigIntegerField(default=0)
    last_seen_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"UsageState({self.customer_id})"


class PPPoEUsageRecord(TenantScopedModel):
    """
    Stores usage deltas per interval (e.g., every 5 minutes).
    """
    customer = models.ForeignKey("Customer", on_delete=models.CASCADE, related_name="pppoe_usage_records")
    router = models.ForeignKey("RouterDevice", null=True, blank=True, on_delete=models.SET_NULL)
    period_start = models.DateTimeField(default=timezone.now)
    period_end = models.DateTimeField(default=timezone.now)

    download_bytes = models.BigIntegerField(default=0)  # rx delta
    upload_bytes = models.BigIntegerField(default=0)    # tx delta

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["customer", "period_start"]),
        ]

    def __str__(self):
        return f"{self.customer_id} {self.period_start:%Y-%m-%d %H:%M}"


class HotspotUsageState(TenantScopedModel):
    """
    Stores last seen counters per hotspot user
    """
    customer = models.OneToOneField(
        "Customer",
        on_delete=models.CASCADE,
        related_name="hotspot_usage_state"
    )
    last_rx_bytes = models.BigIntegerField(default=0)
    last_tx_bytes = models.BigIntegerField(default=0)
    last_seen_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"HotspotState({self.customer_id})"


class HotspotUsageRecord(TenantScopedModel):
    """
    Delta-based usage records (safe for reconnects)
    """
    customer = models.ForeignKey(
        "Customer",
        on_delete=models.CASCADE,
        related_name="hotspot_usage_records"
    )
    router = models.ForeignKey(
        "RouterDevice",
        null=True,
        blank=True,
        on_delete=models.SET_NULL
    )

    period_start = models.DateTimeField()
    period_end = models.DateTimeField()

    download_bytes = models.BigIntegerField(default=0)
    upload_bytes = models.BigIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["customer", "period_start"]),
        ]


class UsageRecord(TenantScopedModel):
    CONNECTION_TYPES = (
        ("pppoe", "PPPoE"),
        ("hotspot", "Hotspot"),
    )

    customer = models.ForeignKey(
        Customer,
        on_delete=models.CASCADE,
        related_name="usage_records",
    )

    connection_type = models.CharField(
        max_length=10,
        choices=CONNECTION_TYPES,
    )

    date = models.DateField()
    rx_bytes = models.BigIntegerField(default=0)
    tx_bytes = models.BigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("customer", "date", "connection_type")
        ordering = ["date"]

    def total_mb(self):
        return (self.rx_bytes + self.tx_bytes) / (1024 * 1024)

    def __str__(self):
        return f"{self.customer.full_name} - {self.date}"


# =====================================================
# PLATFORM BILLING
# =====================================================
# The second billing layer, and the one place where confusing the two would be
# expensive. Everything above bills a *subscriber* on behalf of an operator,
# and settles into that operator's till. Everything below bills an *operator*
# on behalf of the platform, and settles into the platform's own till.
#
# The names are deliberately unlike Invoice/Payment, and platform invoice
# numbers use a PINV- prefix against the subscriber INV-, so the two are never
# mistaken for each other in a database, a log line, or an M-Pesa statement.

class PlatformSetting(models.Model):
    """
    Platform-wide configuration — notably the platform's own M-Pesa till.

    Deliberately NOT tenant-scoped, unlike SystemSetting: these are the
    platform owner's credentials, used to collect from operators. Keeping them
    in a separate table means an operator administering their own settings can
    never read or overwrite them.
    """
    key = models.CharField(max_length=200, unique=True)
    value = models.TextField(blank=True)

    def __str__(self):
        return self.key


class PlatformPlan(models.Model):
    """
    What the platform charges an operator. A global catalogue, not per-operator.
    """
    name = models.CharField(max_length=80)
    slug = models.SlugField(max_length=60, unique=True)

    price = models.DecimalField(
        max_digits=10, decimal_places=2,
        help_text="Charged to the operator each billing period",
    )
    billing_period_days = models.PositiveIntegerField(default=30)

    # 0 means unlimited. Enforcement of these is phase 6 — recorded here so a
    # plan can be defined now and enforced when suspension lands.
    max_customers = models.PositiveIntegerField(default=0)
    max_routers = models.PositiveIntegerField(default=0)

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["price"]

    def __str__(self):
        return f"{self.name} ({self.price})"


class TenantSubscription(TenantScopedModel):
    """One operator's current plan with the platform."""

    STATUS_CHOICES = (
        ("trialing", "Trialing"),
        ("active", "Active"),
        ("past_due", "Past due"),
        ("cancelled", "Cancelled"),
    )

    plan = models.ForeignKey(PlatformPlan, on_delete=models.PROTECT)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default="trialing")

    current_period_start = models.DateTimeField(default=timezone.now)
    current_period_end = models.DateTimeField()
    trial_ends_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            # One plan per operator at a time. Changing plan updates this row.
            models.UniqueConstraint(fields=["tenant"], name="one_platform_plan_per_tenant"),
        ]

    def save(self, *args, **kwargs):
        if not self.current_period_end:
            self.current_period_end = self.current_period_start + timezone.timedelta(
                days=self.plan.billing_period_days
            )
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.tenant} on {self.plan.name}"


class TenantInvoice(TenantScopedModel):
    """What an operator owes the platform for one billing period."""

    STATUS_CHOICES = (
        ("unpaid", "Unpaid"),
        ("paid", "Paid"),
        ("void", "Void"),
    )

    subscription = models.ForeignKey(
        TenantSubscription, on_delete=models.PROTECT, related_name="invoices"
    )
    # PINV- prefix, globally unique — same reasoning as subscriber invoice
    # numbers: the platform M-Pesa callback carries no tenant context and
    # resolves the operator from this value alone.
    number = models.CharField(max_length=50, unique=True)

    period_start = models.DateTimeField()
    period_end = models.DateTimeField()

    amount = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="unpaid")

    due_date = models.DateTimeField()
    issued_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-issued_at"]
        constraints = [
            # One invoice per operator per period — the generator is idempotent
            # and safe to re-run after a partial failure.
            models.UniqueConstraint(
                fields=["tenant", "period_start"],
                name="one_platform_invoice_per_tenant_period",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "status"], name="tinvoice_tenant_status_idx"),
            models.Index(fields=["status", "due_date"], name="tinvoice_status_due_idx"),
        ]

    @property
    def is_overdue(self):
        return self.status == "unpaid" and self.due_date < timezone.now()

    def __str__(self):
        return self.number


class TenantPayment(TenantScopedModel):
    """An operator paying the platform. Settles into the platform's till."""

    METHODS = (
        ("mpesa", "M-Pesa"),
        ("bank", "Bank"),
        ("manual", "Manual"),
    )

    invoice = models.ForeignKey(
        TenantInvoice, on_delete=models.PROTECT, related_name="payments"
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    method = models.CharField(max_length=10, choices=METHODS)
    reference = models.CharField(max_length=100, blank=True)
    paid_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-paid_at"]

    def save(self, *args, **kwargs):
        creating = self.pk is None
        super().save(*args, **kwargs)

        if not creating:
            return

        # Settle the invoice and roll the billing period forward.
        with transaction.atomic():
            invoice = self.invoice
            invoice.status = "paid"
            invoice.save(update_fields=["status"])

            subscription = invoice.subscription
            subscription.status = "active"
            subscription.current_period_start = invoice.period_end
            subscription.current_period_end = invoice.period_end + timezone.timedelta(
                days=subscription.plan.billing_period_days
            )
            subscription.save(update_fields=[
                "status", "current_period_start", "current_period_end",
            ])

            # Restriction is lifted here rather than waiting for a sweep, so an
            # operator who pays is not left locked out.
            #
            # Through set_tenant_status, not a direct write: this used to set
            # the column itself, so being reinstated by paying left no
            # TenantStatusChange row and the history showed a restriction that
            # apparently never ended.
            tenant = invoice.tenant
            if tenant.status in ("past_due", "restricted"):
                set_tenant_status(
                    tenant, "active",
                    reason=f"Payment {self.reference or self.id} received",
                    automatic=True,
                )

    def __str__(self):
        return f"{self.tenant} paid {self.amount}"
