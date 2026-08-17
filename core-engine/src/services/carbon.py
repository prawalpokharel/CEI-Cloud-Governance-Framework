"""
Carbon estimation, and why it must not become its own optimizer.

A carbon-aware scheduler that moves compute to the greenest region is a
concentration engine with a halo: it optimises one objective and quietly
recreates the cost-optimization-creates-fragility problem with a different
currency. Carbon belongs as a TERM in the prescription framework -- one more
dollar-denominated column in the same NET -- not as a separate objective
that wins by default.

## What this estimates, honestly

Per-workload energy from CPU requests x a watts-per-core figure x PUE, and
carbon from grid intensity by region. Every number is an estimate of an
estimate: requests are not draw, watts-per-core varies by SKU, and grid
intensity varies hourly while the table below is annual. The output is
therefore labelled an estimate everywhere it appears, and its legitimate
uses are RELATIVE -- comparing region moves, ranking workloads, pricing the
E term of an intervention -- where the systematic errors largely cancel.

Grid intensity figures are annual public averages (gCO2e/kWh), coarse by
construction. Regions absent from the table use a global default and say so.
"""

from __future__ import annotations

from typing import Any

# Annual-average grid carbon intensity, gCO2e/kWh, by cloud region. Public
# figures, rounded hard -- the point is the ORDER (hydro Nordics vs coal-heavy
# grids differ by 30x), not the third digit.
REGION_INTENSITY: dict[str, int] = {
    # AWS
    "us-east-1": 380, "us-east-2": 440, "us-west-1": 210, "us-west-2": 120,
    "eu-west-1": 280, "eu-west-2": 200, "eu-west-3": 55, "eu-north-1": 25,
    "eu-central-1": 340, "ap-southeast-1": 480, "ap-southeast-2": 550,
    "ap-northeast-1": 460, "ap-south-1": 630, "sa-east-1": 90,
    "ca-central-1": 130,
    # Azure (representative)
    "eastus": 380, "westus2": 120, "northeurope": 280, "westeurope": 340,
    "swedencentral": 25, "francecentral": 55,
    # GCP (representative)
    "us-central1": 440, "us-west1": 120, "europe-west1": 160,
    "europe-north1": 25, "asia-south1": 630,
}

GLOBAL_DEFAULT_INTENSITY = 420  # world average, roughly

# Rough server-class draw per allocated core under typical utilisation,
# including a share of the host. Deliberately a single figure: per-SKU
# precision is unavailable from a cluster snapshot and pretending otherwise
# would be decoration.
WATTS_PER_CORE = 10.0
PUE = 1.2  # modern hyperscale power usage effectiveness

HOURS_PER_YEAR = 8760.0


def estimate(snapshot: dict) -> dict[str, Any]:
    """
    Annual energy and carbon estimate per workload and for the cluster.
    """
    region = None
    for node in snapshot.get("nodes") or []:
        if node.get("region"):
            region = node["region"]
            break
    intensity = REGION_INTENSITY.get(region or "", GLOBAL_DEFAULT_INTENSITY)
    region_known = region in REGION_INTENSITY

    rows = []
    total_kwh = 0.0
    for workload in snapshot.get("workloads") or []:
        cores = workload.get("cpu_cores_requested") or 0.0
        if not workload.get("key") or cores <= 0:
            continue
        kwh = cores * WATTS_PER_CORE * PUE * HOURS_PER_YEAR / 1000.0
        total_kwh += kwh
        rows.append({
            "workload_key": workload["key"],
            "cpu_cores_requested": cores,
            "annual_kwh_estimate": round(kwh, 1),
            "annual_kg_co2e_estimate": round(kwh * intensity / 1000.0, 1),
        })
    rows.sort(key=lambda r: -r["annual_kwh_estimate"])

    return {
        "region": region,
        "region_intensity_g_per_kwh": intensity,
        "region_known": region_known,
        "total_annual_kwh_estimate": round(total_kwh, 1),
        "total_annual_kg_co2e_estimate": round(total_kwh * intensity / 1000.0, 1),
        "per_workload": rows[:25],
        "assumptions": {
            "watts_per_core": WATTS_PER_CORE,
            "pue": PUE,
            "basis": "CPU requests, not measured draw",
            "note": (
                "An estimate of an estimate, fit for RELATIVE use -- ranking "
                "workloads, comparing region moves, pricing the carbon term "
                "of an intervention -- where systematic errors cancel. Not "
                "fit for reporting as measured emissions."
                + ("" if region_known else
                   " Region not in the intensity table; the global average "
                   "was used.")
            ),
        },
    }


def region_move_delta(
    snapshot: dict, target_region: str
) -> dict[str, Any]:
    """
    Carbon delta of moving this cluster's workloads to another region --
    WITH the fragility warning attached.

    The delta is the easy half. The half a carbon optimizer omits is that
    consolidating into the greenest region concentrates workloads into one
    failure domain, so the output carries the reminder and the prescription
    framework is where the trade should actually be decided.
    """
    current = estimate(snapshot)
    target_intensity = REGION_INTENSITY.get(target_region)
    if target_intensity is None:
        return {
            "available": False,
            "reason": f"Region {target_region!r} is not in the intensity table.",
        }

    kwh = current["total_annual_kwh_estimate"]
    delta_kg = kwh * (target_intensity - current["region_intensity_g_per_kwh"]) / 1000.0
    return {
        "available": True,
        "from_region": current["region"],
        "to_region": target_region,
        "annual_kg_co2e_delta_estimate": round(delta_kg, 1),
        "warning": (
            "A region move is also a concentration decision: consolidating "
            "into one green region puts more of the estate into one failure "
            "domain. Evaluate it through the prescription framework where "
            "fragility is priced, not on this number alone."
        ),
    }
