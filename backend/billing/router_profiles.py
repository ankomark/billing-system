from librouteros import connect


# ======================================================
# ROUTER CONNECTION
# ======================================================

def connect_router(router):
    return connect(
        username=router.username,
        password=router.password,
        host=router.ip_address,
        port=router.api_port,
    )


def _rate_limit(package):
    """
    MikroTik format: upload/download
    Example: 5Mbps up, 10Mbps down → 5M/10M
    """
    return f"{package.upload_speed}M/{package.download_speed}M"


# ======================================================
# PPPoE PROFILES
# ======================================================

def ensure_pppoe_profile(router, package):
    """
    Create or reuse a PPPoE profile for a package
    """

    api = connect_router(router)
    profiles = api.path("ppp", "profile")

    # ✅ Better readable profile name
    profile_name = f"PPPOE_PKG_{package.id}"

    for p in profiles:
        if p.get("name") == profile_name:
            return profile_name

    profiles.add(
        name=profile_name,
        rate_limit=_rate_limit(package),
        only_one="yes",        # ✅ Prevent multiple logins
        comment=f"Auto: {package.name}",
    )

    return profile_name


# ======================================================
# HOTSPOT PROFILES
# ======================================================

def ensure_hotspot_profile(router, package):
    """
    Create or reuse a Hotspot user profile for a package
    """

    api = connect_router(router)
    profiles = api.path("ip", "hotspot", "user", "profile")

    profile_name = f"HOTSPOT_PKG_{package.id}"

    for p in profiles:
        if p.get("name") == profile_name:
            return profile_name

    profiles.add(
        name=profile_name,
        rate_limit=_rate_limit(package),
        shared_users="1",      # ✅ One device per voucher
        comment=f"Auto: {package.name}",
    )

    return profile_name
