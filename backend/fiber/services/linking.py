"""
Which paying customers sit behind which ONU, found rather than typed.

The OLT learns the MAC of every device that sends traffic through an ONU.
Billing already knows every hotspot customer's device MACs (CustomerDevice),
so the join is exact: a phone billing has sold a package to, seen behind ONU 7,
makes its owner one of ONU 7's customers. Hand-kept spreadsheets of "who is on
which box" go stale the first time someone moves; this refreshes every poll.

PPPoE subscribers are not found this way: behind their ONU the OLT sees their
own router's MAC, which billing does not hold. Those are linked by hand
(source=manual), and manual links are never aged out.
"""

from datetime import timedelta

from billing.models import CustomerDevice
from billing.utils import normalize_mac

from ..models import OnuCustomer


def record_fdb(olt, fdb, now):
    """`fdb` is {onu: [device MACs]}. Returns how many links were created or refreshed."""
    if not fdb:
        return 0
    wanted = {}
    for onu, macs in fdb.items():
        for mac in macs:
            m = normalize_mac(mac)
            if m:
                wanted.setdefault(m, onu)
    # Both cases: rows written before billing normalised MACs on save can be
    # lower-case, and an exact IN on one spelling would miss them.
    spellings = list(wanted) + [m.lower() for m in wanted]
    devices = (CustomerDevice.objects.filter(tenant_id=olt.tenant_id, mac_address__in=spellings)
               .select_related("customer"))
    count = 0
    for dev in devices:
        onu = wanted.get(normalize_mac(dev.mac_address))
        if onu is None:
            continue
        link, created = OnuCustomer.objects.get_or_create(
            onu=onu, customer=dev.customer,
            defaults={"tenant_id": olt.tenant_id, "device_mac": normalize_mac(dev.mac_address),
                      "first_seen": now, "last_seen": now})
        if not created:
            link.last_seen = now
            link.device_mac = normalize_mac(dev.mac_address)
            link.save(update_fields=["last_seen", "device_mac"])
        count += 1
    return count


def fresh_links(now):
    """Links that still mean something: manual ones, and FDB ones seen in 30 days."""
    from django.db.models import Q

    return OnuCustomer.objects.filter(
        Q(source=OnuCustomer.SOURCE_MANUAL) | Q(last_seen__gte=now - OnuCustomer.STALE_AFTER))


def customers_for(onus, now):
    from billing.models import Customer

    ids = fresh_links(now).filter(onu__in=onus).values_list("customer_id", flat=True)
    return Customer.objects.filter(pk__in=ids)


def onus_for_customer(customer, now):
    return [l.onu for l in fresh_links(now).filter(customer=customer).select_related("onu", "onu__olt")]


def prune_stale(now=None):
    from django.utils import timezone

    now = now or timezone.now()
    cutoff = now - OnuCustomer.STALE_AFTER - timedelta(days=60)
    return OnuCustomer.objects.filter(
        source=OnuCustomer.SOURCE_FDB, last_seen__lt=cutoff).delete()[0]
