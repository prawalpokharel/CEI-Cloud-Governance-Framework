"""
Weekly Cluster Governance Report.

The roadmap describes this as "the report users forward to their boss", which
is a precise design constraint: it has to be readable by someone who does not
use the product, in an email client, without clicking anything. That rules out
JavaScript, external stylesheets, web fonts, and background images -- Outlook
strips or ignores all of them.

So: inline styles, table-based layout, no images. It looks dated because email
is dated.

Content is ordered by what a manager acts on: money, then risk, then health,
then trend. A report that opens with a dependency graph gets forwarded once.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

# Palette matched to the existing dashboard so the report does not look like
# it came from a different product.
NAVY = "#1B4F72"
BLUE = "#2874A6"
GREEN = "#196F3D"
AMBER = "#7D6608"
RED = "#922B21"
GREY = "#7B8A8B"
BORDER = "#E8EDF0"


@dataclass
class ReportData:
    cluster_name: str
    generated_at: datetime
    cost: dict[str, Any]
    health: dict[str, Any]
    vulnerabilities: dict[str, Any]
    cei: dict[str, Any]
    period_days: int = 7


def _esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


def _money(value: float) -> str:
    return f"${value:,.0f}"


def _metric_cell(label: str, value: str, colour: str, note: str = "") -> str:
    return (
        f'<td style="padding:0 8px;vertical-align:top;width:25%">'
        f'<div style="font-size:11px;color:{GREY};text-transform:uppercase;'
        f'letter-spacing:.6px">{_esc(label)}</div>'
        f'<div style="font-size:26px;font-weight:700;color:{colour};'
        f'line-height:1.2;margin-top:4px">{_esc(value)}</div>'
        + (
            f'<div style="font-size:11px;color:{GREY};margin-top:2px">'
            f'{_esc(note)}</div>'
            if note else ""
        )
        + "</td>"
    )


def _section(title: str, body: str) -> str:
    return (
        f'<tr><td style="padding:26px 24px 0 24px">'
        f'<div style="font-size:12px;font-weight:700;color:{NAVY};'
        f'text-transform:uppercase;letter-spacing:.8px;padding-bottom:10px;'
        f'border-bottom:2px solid {BORDER}">{_esc(title)}</div>'
        f'{body}</td></tr>'
    )


def render_html(data: ReportData) -> str:
    cost = data.cost.get("summary", {})
    health = data.health.get("summary", {})
    vulns = data.vulnerabilities.get("summary", {})

    waste = cost.get("wasted_monthly_usd", 0)
    annual = cost.get("wasted_annual_usd", 0)

    # --- headline metrics ---------------------------------------------
    metrics = (
        '<table role="presentation" width="100%" cellpadding="0" '
        'cellspacing="0" style="margin-top:18px"><tr>'
        + _metric_cell(
            "Monthly waste", _money(waste), GREEN,
            f"{_money(annual)}/yr" if annual else "",
        )
        + _metric_cell(
            "Cluster spend", _money(cost.get("cluster_monthly_usd", 0)), NAVY,
            f"{cost.get('waste_as_pct_of_cluster', 0)}% reclaimable",
        )
        + _metric_cell(
            "Critical issues", str(health.get("critical", 0)),
            RED if health.get("critical") else GREEN,
            f"{health.get('warning', 0)} warnings",
        )
        + _metric_cell(
            "Vulnerabilities", str(vulns.get("total_vulnerabilities", 0)),
            AMBER if vulns.get("total_vulnerabilities") else GREEN,
            f"{vulns.get('by_severity', {}).get('CRITICAL', 0)} critical",
        )
        + "</tr></table>"
    )

    # --- savings ------------------------------------------------------
    opportunities = data.cost.get("opportunities", [])[:5]
    if opportunities:
        rows = "".join(
            f'<tr>'
            f'<td style="padding:8px 0;border-bottom:1px solid {BORDER};'
            f'font-size:13px">{_esc(o["name"])}'
            f'<div style="font-size:11px;color:{GREY}">'
            f'{_esc(o["namespace"])} · '
            f'{(o["cpu_utilization"] or 0) * 100:.0f}% of requested CPU used'
            f'</div></td>'
            f'<td style="padding:8px 0;border-bottom:1px solid {BORDER};'
            f'text-align:right;font-size:13px;font-weight:700;color:{GREEN};'
            f'white-space:nowrap">{_money(o["wasted_monthly_usd"])}/mo</td>'
            f'</tr>'
            for o in opportunities
        )
        savings_body = (
            f'<p style="font-size:13px;color:#566573;line-height:1.6">'
            f'Reserved but unused capacity. These are requests nothing is '
            f'consuming, so the scheduler holds the space and you are billed '
            f'for it.</p>'
            f'<table role="presentation" width="100%" cellpadding="0" '
            f'cellspacing="0">{rows}</table>'
        )
    else:
        savings_body = (
            f'<p style="font-size:13px;color:{GREY};line-height:1.6">'
            f'No significant over-provisioning found this week.</p>'
        )

    # --- top risks ----------------------------------------------------
    top_risks = data.vulnerabilities.get("top_risks", [])[:5]
    if top_risks:
        rows = "".join(
            f'<tr>'
            f'<td style="padding:8px 0;border-bottom:1px solid {BORDER};'
            f'font-size:13px">'
            f'<strong>{_esc(r["vulnerability_id"])}</strong> '
            f'<span style="color:{GREY}">in {_esc(r["package"])}</span>'
            f'<div style="font-size:11px;color:{GREY};margin-top:2px">'
            f'{_esc(r["rationale"])}</div></td>'
            f'<td style="padding:8px 0;border-bottom:1px solid {BORDER};'
            f'text-align:right;font-size:11px;font-weight:700;'
            f'color:{RED if r["severity"] == "CRITICAL" else AMBER};'
            f'white-space:nowrap">{_esc(r["severity"])}</td>'
            f'</tr>'
            for r in top_risks
        )
        risk_body = (
            f'<p style="font-size:13px;color:#566573;line-height:1.6">'
            f'{_esc(vulns.get("headline", ""))}</p>'
            f'<table role="presentation" width="100%" cellpadding="0" '
            f'cellspacing="0">{rows}</table>'
        )
    else:
        risk_body = (
            f'<p style="font-size:13px;color:{GREY};line-height:1.6">'
            f'{_esc(vulns.get("headline", "No scan results this period."))}</p>'
        )

    # --- health -------------------------------------------------------
    findings = data.health.get("findings", [])[:5]
    if findings:
        rows = "".join(
            f'<tr>'
            f'<td style="padding:8px 0;border-bottom:1px solid {BORDER};'
            f'font-size:13px">{_esc(f["title"])}'
            f'<div style="font-size:11px;color:{GREY}">'
            f'{_esc(f["namespace"])}</div></td>'
            f'<td style="padding:8px 0;border-bottom:1px solid {BORDER};'
            f'text-align:right;font-size:11px;font-weight:700;'
            f'color:{RED if f["severity"] == "critical" else AMBER};'
            f'white-space:nowrap">{_esc(f["severity"])}</td>'
            f'</tr>'
            for f in findings
        )
        health_body = (
            f'<p style="font-size:13px;color:#566573;line-height:1.6">'
            f'Ranked by how much depends on the affected workload.</p>'
            f'<table role="presentation" width="100%" cellpadding="0" '
            f'cellspacing="0">{rows}</table>'
        )
    else:
        health_body = (
            f'<p style="font-size:13px;color:{GREEN};line-height:1.6">'
            f'No health issues detected.</p>'
        )

    period_end = data.generated_at.strftime("%d %B %Y")

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Cluster Governance Report — {_esc(data.cluster_name)}</title></head>
<body style="margin:0;padding:0;background:#F4F6F7;
font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"
style="background:#F4F6F7;padding:24px 12px">
<tr><td align="center">
<table role="presentation" width="640" cellpadding="0" cellspacing="0"
style="max-width:640px;background:#ffffff;border-radius:8px;overflow:hidden;
box-shadow:0 1px 4px rgba(0,0,0,.08)">

<tr><td style="background:{NAVY};padding:24px">
<div style="color:#ffffff;font-size:19px;font-weight:700">
Cluster Governance Report</div>
<div style="color:#AED6F1;font-size:13px;margin-top:4px">
{_esc(data.cluster_name)} · {data.period_days} days to {period_end}</div>
</td></tr>

<tr><td style="padding:20px 16px 0 16px">{metrics}</td></tr>

{_section("Where the money is going", savings_body)}
{_section("Top risks", risk_body)}
{_section("Cluster health", health_body)}

<tr><td style="padding:26px 24px 24px 24px">
<div style="border-top:1px solid {BORDER};padding-top:14px;font-size:11px;
color:{GREY};line-height:1.6">
Cost figures are list-price estimates. Actual invoices reflect Reserved
Instances, Savings Plans, and committed-use discounts, commonly 20–70% below
list — use these to rank opportunities, not to reconcile a bill.
<br><br>
Vulnerabilities are ranked by what they put at risk: severity scored against
the blast radius of the workloads running each image, whether those workloads
are reachable from outside the cluster, and whether a fix exists.
<br><br>
CloudOptimizer · CEI framework · USPTO App. No. 19/641,446
</div></td></tr>

</table></td></tr></table></body></html>"""


def render_text(data: ReportData) -> str:
    """
    Plain-text alternative.

    Not optional: a multipart email without one is more likely to be filtered
    as spam, and some clients still render text only.
    """
    cost = data.cost.get("summary", {})
    health = data.health.get("summary", {})
    vulns = data.vulnerabilities.get("summary", {})

    lines = [
        f"CLUSTER GOVERNANCE REPORT — {data.cluster_name}",
        f"{data.period_days} days to {data.generated_at.strftime('%d %B %Y')}",
        "",
        f"Monthly waste     {_money(cost.get('wasted_monthly_usd', 0))} "
        f"({_money(cost.get('wasted_annual_usd', 0))}/yr)",
        f"Cluster spend     {_money(cost.get('cluster_monthly_usd', 0))}",
        f"Critical issues   {health.get('critical', 0)} "
        f"({health.get('warning', 0)} warnings)",
        f"Vulnerabilities   {vulns.get('total_vulnerabilities', 0)} "
        f"({vulns.get('by_severity', {}).get('CRITICAL', 0)} critical)",
        "",
        "WHERE THE MONEY IS GOING",
    ]
    for o in data.cost.get("opportunities", [])[:5]:
        lines.append(
            f"  {_money(o['wasted_monthly_usd']):>10}/mo  {o['name']} "
            f"({o['namespace']})"
        )

    lines += ["", "TOP RISKS", f"  {vulns.get('headline', '')}"]
    for r in data.vulnerabilities.get("top_risks", [])[:5]:
        lines.append(
            f"  [{r['severity']:8s}] {r['vulnerability_id']} in {r['package']}"
        )
        lines.append(f"             {r['rationale']}")

    lines += ["", "CLUSTER HEALTH"]
    for f in data.health.get("findings", [])[:5]:
        lines.append(f"  [{f['severity']:8s}] {f['title']}")

    lines += [
        "",
        "Cost figures are list-price estimates and will differ from your "
        "invoice.",
        "CloudOptimizer · CEI framework",
    ]
    return "\n".join(lines)


def build_report(
    cluster_name: str,
    cost: dict,
    health: dict,
    vulnerabilities: dict,
    cei: dict,
    generated_at: datetime | None = None,
) -> dict[str, str]:
    data = ReportData(
        cluster_name=cluster_name,
        generated_at=generated_at or datetime.now(timezone.utc),
        cost=cost,
        health=health,
        vulnerabilities=vulnerabilities,
        cei=cei,
    )
    waste = cost.get("summary", {}).get("wasted_monthly_usd", 0)
    critical = health.get("summary", {}).get("critical", 0)

    # Subject carries the two numbers a manager scans for. A subject line of
    # "Your weekly report" gets archived unread.
    subject = f"{cluster_name}: {_money(waste)}/mo reclaimable"
    if critical:
        subject += f", {critical} critical issue{'s' if critical != 1 else ''}"

    return {
        "subject": subject,
        "html": render_html(data),
        "text": render_text(data),
    }
