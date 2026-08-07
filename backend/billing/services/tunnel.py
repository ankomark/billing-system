"""
Onboarding a router onto the management tunnel, without a shell.

Every router this platform manages sits behind CGNAT — a Huawei LTE box, a
Safaricom router, a shared uplink — so the server cannot dial it directly. A
WireGuard tunnel gives each router a stable private address the health sweep
can reach.

Setting that up used to mean three trips to a terminal: read the server's
public key out of a root-owned file, generate a keypair on the router and copy
its public half back, then run wg-add-peer.sh over SSH. An operator adding
their second site should not need any of that, and the person doing it is
usually standing in a shop with a laptop and a phone hotspot.

So the platform does all three. It generates the keypair, allocates an address,
asks the host to add the peer, and hands back one block of RouterOS commands to
paste into WinBox. The operator's job becomes: fill in a form, paste, press
Test connection.

WHY THE KEYPAIR IS GENERATED HERE

The alternative is the router generating its own, which keeps the private key
on the device — but then its public half has to come back to us, which means a
second copy-paste in the other direction, which is exactly the friction this
exists to remove. The private key travels to the operator's browser over the
same TLS connection that already carries their API password and their tenant
token. It is written to the router and never stored here.

WHY THE HOST APPLIES THE PEER, NOT US

`wg set` needs root and the host's network namespace. Giving the web container
either of those means an RCE in Django becomes control of the server's
networking, on the machine holding the database. Instead this writes a small
request file to a spool directory; a systemd path unit on the host reads it,
validates it, and runs wg-add-peer.sh. The container gains nothing it did not
already have, and the worst a compromised web process can do here is ask for a
tunnel peer it already has permission to ask for.

The trade is that peer creation is asynchronous. That is fine: the operator has
to paste into WinBox before the tunnel can come up anyway, which takes far
longer than the watcher's poll.
"""

import base64
import ipaddress
import json
import logging
import os
import secrets
import tempfile

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding, NoEncryption, PrivateFormat, PublicFormat,
)
from django.conf import settings
from django.db import connection, transaction

logger = logging.getLogger(__name__)


class TunnelNotConfigured(RuntimeError):
    """
    The platform has no tunnel to put a router on.

    Raised rather than returning a half-built script, because a script missing
    the server's public key looks plausible, pastes without error, and produces
    a tunnel that never comes up — and the operator has no way to tell that
    from a router they miswired.
    """


class TunnelAddressExhausted(RuntimeError):
    """Every address in the tunnel subnet is spoken for."""


# ─── Keys ────────────────────────────────────────────────────────────────────

def generate_keypair():
    """
    A WireGuard keypair, as the two base64 strings RouterOS expects.

    WireGuard keys are raw X25519 — 32 bytes, base64'd. `cryptography` is
    already a dependency for field encryption, so this needs no `wg` binary in
    the image, which is just as well: the web image does not have one and
    should not grow the wireguard-tools package to get it.
    """
    private = X25519PrivateKey.generate()
    private_bytes = private.private_bytes(
        encoding=Encoding.Raw,
        format=PrivateFormat.Raw,
        encryption_algorithm=NoEncryption(),
    )
    public_bytes = private.public_key().public_bytes(
        encoding=Encoding.Raw, format=PublicFormat.Raw,
    )
    return (
        base64.b64encode(private_bytes).decode(),
        base64.b64encode(public_bytes).decode(),
    )


def is_wireguard_key(value):
    """
    Whether this looks like a WireGuard key rather than something pasted wrong.

    Checked before a value reaches the spool, so the host-side watcher is never
    asked to run a shell command containing an operator's typo. The watcher
    validates again — this is the first of two gates, not the only one.
    """
    if not isinstance(value, str) or len(value) != 44 or not value.endswith("="):
        return False
    try:
        return len(base64.b64decode(value, validate=True)) == 32
    except (ValueError, TypeError):
        return False


# ─── Addresses ───────────────────────────────────────────────────────────────

def tunnel_network():
    return ipaddress.ip_network(settings.WG_TUNNEL_SUBNET, strict=False)


# Any constant works, as long as nothing else in the codebase picks the same
# one. Advisory locks share a single namespace per database.
_ALLOCATION_LOCK_ID = 0x5C0FFEE1


@transaction.atomic
def allocate_tunnel_ip():
    """
    The next free address in the tunnel subnet.

    Two things here are easy to get subtly wrong, and both fail the same way:
    two operators handed 10.10.0.7, the second tunnel never working, and both
    configurations looking entirely correct.

    **It must read across every tenant.** RouterDevice is tenant-scoped and
    almost everything touching it should be — but a tunnel address belongs to
    one router on the whole platform, not one per operator. The `all_tenants()`
    *context manager* is what makes that true: the manager method of the same
    name only bypasses the ORM filter, while Postgres RLS keeps filtering to
    whichever operator the request is acting for. Using the queryset method
    alone reads as cross-operator and silently is not.

    **A row lock is not enough.** `select_for_update` locks rows that exist,
    and the thing being guarded against is two transactions both finding an
    address absent and both inserting it. There is nothing to lock. An advisory
    lock serialises the whole allocation instead, and being transaction-scoped
    it releases when the caller's transaction commits — which is why this must
    be called inside the same transaction that writes the row.
    """
    from billing.models import RouterDevice
    from billing.tenancy import all_tenants

    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [_ALLOCATION_LOCK_ID])

    network = tunnel_network()
    server_ip = ipaddress.ip_address(settings.WG_SERVER_TUNNEL_IP)

    taken = set()
    with all_tenants():
        rows = list(
            RouterDevice.objects.all_tenants().values_list("ip_address", flat=True)
        )
    for value in rows:
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            continue  # a hostname or something malformed; not our subnet
        if address in network:
            taken.add(address)

    for candidate in network.hosts():
        if candidate != server_ip and candidate not in taken:
            return str(candidate)

    raise TunnelAddressExhausted(
        f"No free address left in {network}. "
        "Widen WG_TUNNEL_SUBNET and reconfigure wg0 to match."
    )


# ─── Asking the host to add the peer ─────────────────────────────────────────

def queue_peer(name, public_key, tunnel_ip):
    """
    Ask the host to add this peer, and return the request id.

    Written atomically — a temporary file in the same directory, then renamed —
    because the watcher fires on file creation, and a partially written JSON
    document is a request the host cannot parse and the operator never hears
    about.

    Everything here is validated before it is written. The watcher validates it
    again on the other side; neither should be the only thing standing between
    an operator's form and a root shell.
    """
    if not is_wireguard_key(public_key):
        raise ValueError("public_key is not a WireGuard key")

    if ipaddress.ip_address(tunnel_ip) not in tunnel_network():
        raise ValueError(f"{tunnel_ip} is outside {settings.WG_TUNNEL_SUBNET}")

    # The name reaches a shell argument on the host. Nothing but the safe set,
    # and the watcher rejects anything else too.
    safe_name = "".join(c for c in str(name) if c.isalnum() or c in "-_")[:40]
    if not safe_name:
        safe_name = "router"

    spool = settings.WG_SPOOL_DIR
    if not spool:
        raise TunnelNotConfigured("WG_SPOOL_DIR is not set")

    request_id = secrets.token_hex(8)
    payload = {
        "id": request_id,
        "name": safe_name,
        "public_key": public_key,
        "tunnel_ip": tunnel_ip,
    }

    os.makedirs(spool, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(dir=spool, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(payload, handle)
        os.replace(temp_path, os.path.join(spool, f"{request_id}.json"))
    except Exception:
        # Leaving a .tmp behind would be picked up by nothing and cleaned by
        # nobody.
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        raise

    logger.info(
        "[tunnel] queued peer %s for %s at %s", request_id, safe_name, tunnel_ip
    )
    return request_id


def peer_result(request_id):
    """
    What the host made of a request, or None while it has not answered yet.

    Only ever advisory. Whether the tunnel actually works is answered by Test
    connection, which dials the router — this just lets the interface say
    "still applying" rather than showing nothing at all.
    """
    path = os.path.join(settings.WG_SPOOL_DIR or "", f"{request_id}.result")
    try:
        with open(path) as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


# ─── The script the operator pastes ──────────────────────────────────────────

def build_router_script(*, tunnel_ip, private_key, api_username, api_password,
                        api_port=8728):
    """
    One block of RouterOS commands: tunnel, API access, and the firewall rule.

    Deliberately one block rather than a numbered procedure. An operator
    working through eight separate steps in a shop, on a phone hotspot, will
    lose their place — and a half-configured router is harder to diagnose than
    an unconfigured one, because most of it looks right.

    The order matters: the tunnel is built before the firewall rule that
    depends on its interface existing.
    """
    if not settings.WG_SERVER_PUBLIC_KEY:
        raise TunnelNotConfigured(
            "WG_SERVER_PUBLIC_KEY is not set. Run "
            "`sudo cat /etc/wireguard/server-public.key` on the server and put "
            "it in the platform's .env, then redeploy."
        )
    if not settings.WG_ENDPOINT_HOST:
        raise TunnelNotConfigured("WG_ENDPOINT_HOST is not set.")

    interface = settings.WG_INTERFACE_NAME
    subnet_bits = tunnel_network().prefixlen

    # Plain ASCII throughout, deliberately. This is pasted into a RouterOS
    # terminal, and box-drawing characters and typographic dashes are exactly
    # the sort of thing that arrives mangled and turns a comment line into a
    # parse error halfway down the block.
    return "\n".join([
        "# --- SmartBill: paste this whole block into WinBox > New Terminal ---",
        "",
        "# 1. The management tunnel.",
        f"/interface/wireguard/add name={interface} listen-port=13231 "
        f'private-key="{private_key}"',
        f"/ip/address/add address={tunnel_ip}/{subnet_bits} interface={interface}",
        f"/interface/wireguard/peers/add interface={interface} "
        f'public-key="{settings.WG_SERVER_PUBLIC_KEY}" '
        f"endpoint-address={settings.WG_ENDPOINT_HOST} "
        f"endpoint-port={settings.WG_ENDPOINT_PORT} "
        f"allowed-address={settings.WG_SERVER_TUNNEL_IP}/32 "
        "persistent-keepalive=25s",
        "",
        "# 2. API access, reachable only from the platform's tunnel address.",
        f"/ip/service/set api disabled=no port={api_port} "
        f"address={settings.WG_SERVER_TUNNEL_IP}/32",
        f'/user/add name={api_username} password="{api_password}" group=full '
        f'address={settings.WG_SERVER_TUNNEL_IP}/32 comment="SmartBill"',
        "",
        "# 3. Let it through the firewall. Without this the port is silently",
        "#    dropped, which looks exactly like a wrong password.",
        f"/ip/firewall/filter/add chain=input action=accept protocol=tcp "
        f"dst-port={api_port} src-address={settings.WG_SERVER_TUNNEL_IP} "
        f'in-interface={interface} comment="SmartBill API via tunnel" '
        "place-before=0",
        "",
        "# 4. Keep the clock right. Session expiry depends on it, and these",
        "#    boards lose the time on every power cut.",
        "/system/ntp/client/set enabled=yes "
        "servers=time.cloudflare.com,pool.ntp.org",
        "",
        f"# Then check it reached us:  /ping {settings.WG_SERVER_TUNNEL_IP} count=3",
    ])
