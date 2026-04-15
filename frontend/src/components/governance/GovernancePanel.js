import React, { useState } from 'react';

/**
 * Governance Panel - Configure compliance frameworks and policies.
 * Maps to Patent Module 104: Governance Policy Store.
 *
 * Light theme to match the /demo and main page treatment.
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
    <div style={s.wrap}>
      <h2 style={s.heading}>Governance Configuration</h2>

      <div style={s.card}>
        <h3 style={s.cardTitle}>Compliance Framework</h3>
        <div style={s.frameworkGrid}>
          {Object.entries(frameworks).map(([key, fw]) => {
            const active = framework === key;
            return (
              <button
                key={key}
                onClick={() => setFramework(key)}
                style={{
                  ...s.frameworkButton,
                  ...(active ? s.frameworkButtonActive : {}),
                }}
              >
                <p style={s.frameworkName}>{fw.name}</p>
                <p style={s.frameworkDesc}>{fw.desc}</p>
              </button>
            );
          })}
        </div>
      </div>

      <div style={s.card}>
        <h3 style={s.cardTitle}>{current.name} Constraints</h3>
        <div style={s.constraintGrid}>
          <ConstraintCard label="Min Replicas" value={current.minReplicas} />
          <ConstraintCard
            label="Data Sovereignty"
            value={current.dataSov ? 'Required' : 'Not Required'}
          />
          <ConstraintCard
            label="Encryption"
            value={current.encryption ? 'Required' : 'Not Required'}
          />
          <ConstraintCard
            label="Risk Weight"
            value={current.riskWeight.toFixed(2)}
          />
        </div>
      </div>

      <div style={s.card}>
        <h3 style={s.cardTitle}>Mission Criticality Classifications</h3>
        <div style={s.criticalityList}>
          {criticalities.map((c) => (
            <div key={c.level} style={s.criticalityRow}>
              <div>
                <span style={s.criticalityLevel}>
                  {c.level.replace('_', ' ')}
                </span>
                <p style={s.criticalityDesc}>{c.desc}</p>
              </div>
              <span style={s.criticalityWeight}>{c.weight.toFixed(2)}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function ConstraintCard({ label, value }) {
  return (
    <div style={s.constraintCard}>
      <p style={s.constraintLabel}>{label}</p>
      <p style={s.constraintValue}>{value}</p>
    </div>
  );
}

const s = {
  wrap: { display: 'flex', flexDirection: 'column', gap: 16 },
  heading: {
    margin: 0,
    fontSize: 18,
    fontWeight: 600,
    color: '#1B4F72',
  },
  card: {
    background: 'white',
    border: '1px solid #E5E8EB',
    borderRadius: 8,
    padding: 18,
    boxShadow: '0 1px 3px rgba(0,0,0,0.05)',
  },
  cardTitle: {
    margin: '0 0 14px 0',
    fontSize: 13,
    fontWeight: 600,
    color: '#566573',
    textTransform: 'uppercase',
    letterSpacing: '0.5px',
  },
  frameworkGrid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))',
    gap: 10,
  },
  frameworkButton: {
    background: 'white',
    border: '1px solid #E5E8EB',
    borderRadius: 6,
    padding: 12,
    textAlign: 'left',
    cursor: 'pointer',
    transition: 'border-color 0.15s, background 0.15s',
  },
  frameworkButtonActive: {
    borderColor: '#2874A6',
    background: '#EBF5FB',
    boxShadow: '0 0 0 1px #2874A6',
  },
  frameworkName: {
    margin: 0,
    fontSize: 14,
    fontWeight: 600,
    color: '#1B4F72',
  },
  frameworkDesc: {
    margin: '4px 0 0 0',
    fontSize: 12,
    color: '#7B8A8B',
    lineHeight: 1.4,
  },
  constraintGrid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))',
    gap: 12,
  },
  constraintCard: {
    background: '#F4F6F7',
    borderRadius: 6,
    padding: 12,
  },
  constraintLabel: {
    margin: 0,
    fontSize: 11,
    color: '#7B8A8B',
    textTransform: 'uppercase',
    letterSpacing: '0.5px',
  },
  constraintValue: {
    margin: '6px 0 0 0',
    fontSize: 14,
    fontWeight: 600,
    color: '#1B4F72',
  },
  criticalityList: { display: 'flex', flexDirection: 'column', gap: 8 },
  criticalityRow: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    background: '#F4F6F7',
    borderRadius: 6,
    padding: '12px 14px',
  },
  criticalityLevel: {
    fontSize: 14,
    fontWeight: 600,
    color: '#1B4F72',
    textTransform: 'capitalize',
  },
  criticalityDesc: {
    margin: '2px 0 0 0',
    fontSize: 12,
    color: '#7B8A8B',
  },
  criticalityWeight: {
    fontFamily: 'monospace',
    fontSize: 14,
    fontWeight: 600,
    color: '#2874A6',
  },
};
