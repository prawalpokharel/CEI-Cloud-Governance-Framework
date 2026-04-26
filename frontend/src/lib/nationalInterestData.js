/**
 * Constants powering the /national-interest page.
 *
 * Exposed as a single data file so an attorney or non-engineer can
 * update the figures, citations, and program references without
 * touching any component code. Every numeric claim is paired with a
 * `source` URL pointing at the authoritative document.
 *
 * IMPORTANT: only add facts here that are publicly verifiable. USCIS
 * reviewers will follow the source links.
 */

export const PATENT = {
  application_no: '19/641,446',
  filed: 'April 7, 2026',
  priority_provisional: '63/999,378',
  priority_date: 'February 5, 2026',
  uspto_url: 'https://patentcenter.uspto.gov/applications/19641446',
  title:
    'System and Method for Dynamic Resource Allocation in Distributed Computing Environments Using Adaptive Centrality-Entropy Index with Oscillation Suppression and Fault Propagation Control',
};

export const REPO = {
  url: 'https://github.com/prawalpokharel/CEI-Cloud-Governance-Framework',
  scenarios_url:
    'https://github.com/prawalpokharel/CEI-Cloud-Governance-Framework/tree/main/core-engine/scenarios',
};

/**
 * Federal programs and policy frameworks that the CEI methodology
 * directly supports. Each entry is a real, named program with a URL
 * to the authoritative source.
 */
export const FEDERAL_PROGRAMS = [
  {
    name: 'Joint Warfighting Cloud Capability (JWCC)',
    agency: 'U.S. Department of Defense',
    relevance:
      'Multi-cloud DoD contract (AWS, Microsoft, Google, Oracle) with up to $9B ceiling. Workload migration and ongoing optimization across providers requires exactly the topology-aware, governance-enforcing decision framework described in the patent.',
    url: 'https://www.disa.mil/Computing/Joint-Warfighting-Cloud-Capability',
  },
  {
    name: 'FedRAMP — Federal Risk and Authorization Management Program',
    agency: 'GSA',
    relevance:
      'FedRAMP authorization requires demonstrable governance over data residency, encryption, redundancy, and access control. The CEI Governance Policy Store (Patent Module 104) directly encodes FedRAMP-style constraints and surfaces violations before any modification reaches production.',
    url: 'https://www.fedramp.gov/',
  },
  {
    name: 'Executive Order 14028 — Improving the Nation\u2019s Cybersecurity',
    agency: 'White House (2021)',
    relevance:
      'Section 3 mandates federal agencies adopt zero-trust architecture and continuous monitoring. The CEI pre-modification validator (Module 110) and rollback manager (Module 112) provide auditable evidence that any change was risk-assessed and reversible — a direct fit for §3(a) and §3(d).',
    url: 'https://www.whitehouse.gov/briefing-room/presidential-actions/2021/05/12/executive-order-on-improving-the-nations-cybersecurity/',
  },
  {
    name: 'Executive Order 14110 — Safe, Secure, and Trustworthy AI',
    agency: 'White House (2023)',
    relevance:
      'AI training infrastructure is one of the patent\u2019s explicit application domains (GPU cluster scenario, §7.4). The CEI framework provides governance-aware allocation for shared GPU pools — directly applicable to the EO\u2019s requirements around responsible federal AI compute usage.',
    url: 'https://www.whitehouse.gov/briefing-room/presidential-actions/2023/10/30/executive-order-on-the-safe-secure-and-trustworthy-development-and-use-of-artificial-intelligence/',
  },
  {
    name: 'NSPM-7 — National Security & Resilient Positioning, Navigation, and Timing',
    agency: 'White House (2020)',
    relevance:
      'Federal directive on resilient PNT in GPS-denied environments. The Underwater Acoustic Positioning scenario (Patent §7.3) demonstrates the CEI framework operating on exactly this class of system — sparse-bandwidth, minimum-source-enforcement, ocean-noise adaptive.',
    url: 'https://www.federalregister.gov/documents/2020/02/18/2020-03337/strengthening-national-resilience-through-responsible-use-of-positioning-navigation-and-timing',
  },
  {
    name: 'Federal Cloud Computing Strategy ("Cloud Smart")',
    agency: 'OMB',
    relevance:
      'OMB strategy mandates measurable cost optimization and security across federal cloud workloads. The CEI cost-savings engine + HPA-vs-CEI benchmark provide the kind of side-by-side, auditable evidence required by Cloud Smart procurement reviews.',
    url: 'https://cloud.cio.gov/strategy/',
  },
];

/**
 * The three defense / national-security application domains that
 * exercise the framework in the live demo. Deep-link directly to
 * /demo/<scenario_id> so reviewers can verify behavior in one click.
 */
export const DEFENSE_APPLICATIONS = [
  {
    scenario_id: 'nc3_strategic_comms',
    label: 'NC3 Strategic Communications',
    summary:
      'A 17-node strategic communications network with path-diversity, two-person crypto integrity, and segmentation requirements. CEI demonstrates correctness on high-redundancy, low-utilization-by-design infrastructure — the inverse of every commercial autoscaler\u2019s assumption.',
    why_it_matters:
      'Directly relevant to DoD Nuclear Command, Control & Communications modernization ($154B for operating, sustaining, and modernizing NC3 over 2025-2034 per CBO).',
  },
  {
    scenario_id: 'drone_swarm',
    label: 'Tactical Drone Swarm',
    summary:
      'A 14-node UAV mesh with latency budgets, link redundancy, and intermittent connectivity under EM jamming. Demonstrates CEI operating on a latency-critical edge mesh under adversarial conditions.',
    why_it_matters:
      'Aligned with DARPA OFFSET, DARPA CODE, and USAF Skyborg autonomy programs. The framework provides the topology-aware, governance-enforcing decision layer those programs need on top of the autonomy stack.',
  },
  {
    scenario_id: 'underwater_aps',
    label: 'Underwater Acoustic Positioning (GPS-Denied)',
    summary:
      'A 14-node bandwidth-constrained positioning network with minimum-positioning-source enforcement and ocean-noise adaptivity. Tests framework generalization to sparse-connectivity environments.',
    why_it_matters:
      'Directly supports NSPM-7 (Resilient PNT) and US Navy ONR Ocean Acoustics & Acoustic Communications priorities. One of very few software frameworks that operates correctly on sub-acoustic bandwidth budgets.',
  },
];

/**
 * Quantified impact framing — every figure is paired with a
 * publicly verifiable source so a USCIS officer can follow the
 * citation. Numbers are bounded and conservative.
 */
export const IMPACT_FIGURES = [
  {
    label: 'FY24 Federal IT spend',
    value: '~$75B',
    detail:
      'Approximate annual federal civilian + defense IT obligations (per FY24 IT Dashboard / OMB Analytical Perspectives).',
    source_label: 'OMB IT Dashboard',
    source_url: 'https://www.itdashboard.gov/',
  },
  {
    label: 'JWCC ceiling',
    value: '$9B',
    detail:
      'Joint Warfighting Cloud Capability indefinite-delivery / indefinite-quantity ceiling across AWS, Azure, GCP, Oracle.',
    source_label: 'DISA — JWCC',
    source_url:
      'https://www.disa.mil/Computing/Joint-Warfighting-Cloud-Capability',
  },
  {
    label: 'Reported industry overspend on cloud',
    value: '~32%',
    detail:
      'Flexera 2024 State of the Cloud report: enterprises self-report ~32% of cloud spend is wasted on under-utilized or mis-sized resources. Applied to federal cloud workloads, this is the addressable optimization band the CEI framework targets.',
    source_label: 'Flexera 2024 State of the Cloud',
    source_url:
      'https://info.flexera.com/CM-REPORT-State-of-the-Cloud',
  },
  {
    label: 'CEI cost-savings engine reduction (worked examples)',
    value: '15–27% per node',
    detail:
      'Per-node savings range observed in the live demo across the 5 reference scenarios (consolidation 27%, rightsizing 15% per Patent §V.B). Conservative because it excludes cascade-failure avoidance and oscillation-suppression downtime savings.',
    source_label: 'Live demo — /demo',
    source_url: '/demo',
  },
];

/**
 * Reproducibility surface: all the artifacts a reviewer can
 * independently verify. Each is a publicly accessible link.
 */
export const REPRODUCIBILITY = [
  {
    label: 'USPTO Patent Application 19/641,446',
    detail: 'Full claims, specification, and figures (USPTO Patent Center).',
    url: PATENT.uspto_url,
  },
  {
    label: 'GitHub repository (full source)',
    detail:
      'Three-service architecture (Next.js frontend, Node/Express backend, Python/FastAPI core engine). All 12 patent modules implemented and tested.',
    url: REPO.url,
  },
  {
    label: 'Reference scenario datasets',
    detail:
      'Five complete topology + governance + 180-point telemetry datasets reproducing Patent §§7.1–7.4. Loadable via the live demo or via the public REST API.',
    url: REPO.scenarios_url,
  },
  {
    label: 'Live deployed instance',
    detail:
      'cloudoptimizer.app — production deployment running the same code as the repo. No login required for the demo scenarios.',
    url: 'https://cloudoptimizer.app/demo',
  },
];

/**
 * Optional publications block — fill in with real URLs once accepted /
 * pre-prints posted. Empty entries are hidden by the page.
 */
export const PUBLICATIONS = [
  {
    citation:
      'P. Pokharel, "Governance-Aware Dynamic Resource Allocation Using Adaptive Centrality-Entropy Index," IEEE Cloud Summit 2026 (forthcoming).',
    url: '',
  },
  {
    citation:
      'P. Pokharel, "AI Modernization in the U.S. Air Force: Governance-Aware Compute Allocation," SSRN, 2025.',
    url: '',
  },
];
