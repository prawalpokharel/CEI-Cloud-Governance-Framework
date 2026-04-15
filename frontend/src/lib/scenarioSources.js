/**
 * Per-scenario citation map. Each entry lists the real industry /
 * research sources whose published reference architectures and
 * operational characteristics informed the synthetic dataset for that
 * domain.
 *
 * Keep entries to publicly verifiable sources only. The patent
 * application (USPTO 19/641,446) and the repo's
 * /core-engine/scenarios directory are listed separately on the
 * detail page as the place where the same numbers are reproduced.
 */

const SCENARIO_SOURCES = {
  cloud_microservices: {
    summary:
      'Topology and tier assignments follow the AWS Well-Architected Framework reference architecture for tiered e-commerce workloads, with PCI-DSS scope boundaries drawn from the AWS PCI-DSS Compliance Guide.',
    sources: [
      {
        label: 'AWS Well-Architected Framework — Reliability & Operational Excellence pillars',
        url: 'https://docs.aws.amazon.com/wellarchitected/latest/framework/welcome.html',
      },
      {
        label: 'AWS PCI-DSS v4.0 on AWS Compliance Guide',
        url: 'https://aws.amazon.com/compliance/pci-dss-level-1-faqs/',
      },
      {
        label: 'Sam Newman, "Building Microservices" (O\'Reilly, 2nd ed., 2021) — service decomposition patterns',
        url: 'https://samnewman.io/books/building_microservices_2nd_edition/',
      },
    ],
  },
  gpu_cluster: {
    summary:
      'Heterogeneous A100 / H100 mix and inference-tier accelerators follow the NVIDIA DGX SuperPOD reference architecture; checkpoint integrity and SLO constraints follow MLCommons MLPerf training benchmark configurations.',
    sources: [
      {
        label: 'NVIDIA DGX SuperPOD Reference Architecture (DGX H100 + DGX A100)',
        url: 'https://docs.nvidia.com/dgx-superpod/index.html',
      },
      {
        label: 'MLCommons MLPerf Training Benchmark — published reference systems',
        url: 'https://mlcommons.org/benchmarks/training/',
      },
      {
        label: 'Meta AI Research SuperCluster (RSC) public technical description',
        url: 'https://ai.meta.com/blog/ai-rsc/',
      },
    ],
  },
  drone_swarm: {
    summary:
      'Mesh topology, latency budgets, and EM-jamming resilience patterns follow DARPA OFFSET swarm tactics and CODE program documentation, with Air Force Skyborg as the platform-level reference.',
    sources: [
      {
        label: 'DARPA OFFSET (Offensive Swarm-Enabled Tactics) program',
        url: 'https://www.darpa.mil/program/offensive-swarm-enabled-tactics',
      },
      {
        label: 'DARPA CODE (Collaborative Operations in Denied Environment) program',
        url: 'https://www.darpa.mil/program/collaborative-operations-in-denied-environment',
      },
      {
        label: 'US Air Force Research Laboratory — Skyborg autonomy core system',
        url: 'https://afresearchlab.com/technology/skyborg/',
      },
    ],
  },
  underwater_aps: {
    summary:
      'Bandwidth scarcity, minimum-positioning-source enforcement, and ocean-noise adaptivity follow NATO CMRE and US Navy ONR underwater acoustic positioning research; the long-baseline (LBL) array sizing follows IEEE Journal of Oceanic Engineering case studies.',
    sources: [
      {
        label: 'NATO STO Centre for Maritime Research and Experimentation (CMRE) — underwater acoustic networking publications',
        url: 'https://www.cmre.nato.int/research/projects',
      },
      {
        label: 'US Office of Naval Research (ONR) — Ocean Acoustics & Acoustic Communications',
        url: 'https://www.nre.navy.mil/our-research/ocean-battlespace-sensing/program-offices/code-322/ocean-acoustics',
      },
      {
        label: 'IEEE Journal of Oceanic Engineering — long-baseline & USBL positioning literature',
        url: 'https://ieeexplore.ieee.org/xpl/RecentIssue.jsp?punumber=48',
      },
    ],
  },
  nc3_strategic_comms: {
    summary:
      'Path-diversity, two-person crypto integrity, and network-segmentation requirements follow DoD NC3 modernization documentation and RAND Corporation NC3 enterprise studies. Low-utilization-by-design follows US Strategic Command operational posture descriptions.',
    sources: [
      {
        label: 'US DoD — Nuclear Command, Control, and Communications (NC3) public materials',
        url: 'https://www.acq.osd.mil/asda/spe/sn/nc3.html',
      },
      {
        label: 'RAND Corporation — "Modernizing the Nuclear Command, Control, and Communications System"',
        url: 'https://www.rand.org/pubs/research_reports/RR4350.html',
      },
      {
        label: 'US Strategic Command — Mission & Capabilities (NC3 oversight)',
        url: 'https://www.stratcom.mil/Mission/',
      },
    ],
  },
};

export default SCENARIO_SOURCES;
