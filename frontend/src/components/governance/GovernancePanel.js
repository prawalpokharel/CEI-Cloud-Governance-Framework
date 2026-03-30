import React, { useState } from 'react';

/**
 * Governance Panel - Configure compliance frameworks and policies.
 * Maps to Patent Module 104: Governance Policy Store.
 */
export default function GovernancePanel() {
  const [framework, setFramework] = useState('fedramp');

  const frameworks = {
    fedramp: { name: 'FedRAMP', desc: 'Federal Risk and Authorization Management Program', minReplicas: 2, dataSov: true, encryption: true, riskWeight: 0.9 },
    cmmc: { name: 'CMMC', desc: 'Cybersecurity Maturity Model Certification', minReplicas: 2, dataSov: true, encryption: true, riskWeight: 0.85 },
    hipaa: { name: 'HIPAA', desc: 'Health Insurance Portability and Accountability Act', minReplicas: 2, dataSov: true, encryption: true, riskWeight: 0.8 },
    standard: { name: 'Standard', desc: 'No specific compliance framework', minReplicas: 1, dataSov: false, encryption: false, riskWeight: 0.2 },
  };

  const criticalities = [
    { level: 'mission_critical', weight: 1.0, desc: 'Cannot be modified without manual approval' },
    { level: 'business_critical', weight: 0.75, desc: 'Requires k-hop validation before modification' },
    { level: 'operational', weight: 0.5, desc: 'Standard CEI-based optimization permitted' },
    { level: 'development', weight: 0.25, desc: 'Aggressive optimization allowed' },
    { level: 'test', weight: 0.1, desc: 'Consolidation recommended when underutilized' },
  ];

  const current = frameworks[framework];

  return (
    <div className="space-y-6">
      <h2 className="text-lg font-bold text-white">Governance Configuration</h2>

      {/* Framework Selection */}
      <div className="bg-gray-900 rounded-lg p-4 border border-gray-800">
        <h3 className="text-sm font-semibold text-gray-400 mb-3">Compliance Framework</h3>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {Object.entries(frameworks).map(([key, fw]) => (
            <button key={key} onClick={() => setFramework(key)}
              className={`p-3 rounded-lg border text-left transition-colors ${
                framework === key ? 'border-blue-500 bg-blue-900/20' : 'border-gray-700 hover:border-gray-600'
              }`}>
              <p className="text-sm font-semibold text-white">{fw.name}</p>
              <p className="text-xs text-gray-500 mt-1">{fw.desc}</p>
            </button>
          ))}
        </div>
      </div>

      {/* Framework Details */}
      <div className="bg-gray-900 rounded-lg p-4 border border-gray-800">
        <h3 className="text-sm font-semibold text-gray-400 mb-3">{current.name} Constraints</h3>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
          <ConstraintCard label="Min Replicas" value={current.minReplicas} />
          <ConstraintCard label="Data Sovereignty" value={current.dataSov ? 'Required' : 'Not Required'} />
          <ConstraintCard label="Encryption" value={current.encryption ? 'Required' : 'Not Required'} />
          <ConstraintCard label="Risk Weight" value={current.riskWeight.toFixed(2)} />
        </div>
      </div>

      {/* Mission Criticality Levels */}
      <div className="bg-gray-900 rounded-lg p-4 border border-gray-800">
        <h3 className="text-sm font-semibold text-gray-400 mb-3">Mission Criticality Classifications</h3>
        <div className="space-y-2">
          {criticalities.map((c) => (
            <div key={c.level} className="flex items-center justify-between bg-gray-800 rounded p-3">
              <div>
                <span className="text-sm font-medium text-white capitalize">{c.level.replace('_', ' ')}</span>
                <p className="text-xs text-gray-500">{c.desc}</p>
              </div>
              <span className="text-sm font-mono text-blue-400">{c.weight.toFixed(2)}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function ConstraintCard({ label, value }) {
  return (
    <div className="bg-gray-800 rounded p-3">
      <p className="text-xs text-gray-500">{label}</p>
      <p className="text-sm font-semibold text-white mt-1">{value}</p>
    </div>
  );
}
