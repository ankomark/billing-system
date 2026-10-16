"""
Guess which ONUs share a splitter, for the operator to confirm.

The OLT cannot see a passive splitter. Two things give one away:
  - ranging distance: ONUs behind the same splitter sit within a drop
    cable's length of each other along the fibre;
  - failing together: a cut between OLT and splitter darkens all of them in
    the same incident, and nobody else on the port.

So ONUs on one port are sorted by distance and cut wherever the gap to the
next exceeds GAP_M; each run of two or more is a candidate, scored by how
often its members went down in the same incident. Nothing is written: the
operator accepts a suggestion by creating the splitter and assigning the ONUs.
"""

from collections import Counter
from itertools import combinations

from ..models import Onu

GAP_M = 120


def suggest(olt, pon_port=None):
    onus = Onu.objects.filter(olt=olt, present=True, splitter__isnull=True, distance_m__isnull=False)
    if pon_port is not None:
        onus = onus.filter(pon_port=pon_port)
    by_port = {}
    for o in onus.order_by("pon_port", "distance_m").prefetch_related("outages"):
        by_port.setdefault(o.pon_port, []).append(o)

    out = []
    for port, items in by_port.items():
        groups, current = [], [items[0]]
        for prev, nxt in zip(items, items[1:]):
            if nxt.distance_m - prev.distance_m > GAP_M:
                groups.append(current)
                current = []
            current.append(nxt)
        groups.append(current)
        for g in groups:
            if len(g) < 2:
                continue
            shared = Counter()
            outage_sets = {o.pk: {x.pk for x in o.outages.all() if x.scope == "port"} for o in g}
            for a, b in combinations(g, 2):
                shared[(a.pk, b.pk)] = len(outage_sets[a.pk] & outage_sets[b.pk])
            pairs = len(g) * (len(g) - 1) // 2
            together = sum(1 for v in shared.values() if v) / pairs if pairs else 0
            span = (g[0].distance_m, g[-1].distance_m)
            out.append({
                "olt_id": olt.pk, "pon_port": port,
                "name": f"PON{port} ~{round(sum(o.distance_m for o in g) / len(g), -1):.0f} m",
                "distance_from_m": span[0], "distance_to_m": span[1],
                "onu_ids": [o.pk for o in g], "onus": [str(o) for o in g],
                "confidence": "high" if together >= 0.5 else "medium" if len(g) >= 3 else "low",
                "failed_together_pct": round(100 * together),
            })
    return out
