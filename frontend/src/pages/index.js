import React, { useState } from 'react';
import CEIDashboard from '../components/dashboard/CEIDashboard';
import GovernancePanel from '../components/governance/GovernancePanel';
import AnalysisPanel from '../components/analysis/AnalysisPanel';
import GraphVisualization from '../components/visualization/GraphVisualization';

/**
 * CloudOptimizer — Main Application Page
 * Interactive dashboard for governance-aware infrastructure optimization
 * using the Centrality-Entropy Index (CEI) framework.
 */
export default function Home() {
  const [activeTab, setActiveTab] = useState('dashboard');
  const [analysisResults, setAnalysisResults] = useState(null);

  const tabs = [
    { id: 'dashboard', label: 'CEI Dashboard' },
    { id: 'analysis', label: 'Run Analysis' },
    { id: 'governance', label: 'Governance' },
    { id: 'graph', label: 'Dependency Graph' },
  ];

  return (
    <div className="min-h-screen bg-gray-950 text-white">
      {/* Header */}
      <header className="border-b border-gray-800 bg-gray-900">
        <div className="max-w-7xl mx-auto px-4 py-4 flex items-center justify-between">
          <div>
            <h1 className="text-xl font-bold text-blue-400">CloudOptimizer</h1>
            <p className="text-xs text-gray-500">Governance-Aware Dynamic Resource Allocation</p>
          </div>
          <div className="text-xs text-gray-600">
            USPTO Patent App. No. 63/999,378
          </div>
        </div>

        {/* Tab Navigation */}
        <div className="max-w-7xl mx-auto px-4">
          <nav className="flex space-x-1">
            {tabs.map((tab) => (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`px-4 py-2 text-sm rounded-t-lg transition-colors ${
                  activeTab === tab.id
                    ? 'bg-gray-800 text-blue-400 border-b-2 border-blue-400'
                    : 'text-gray-400 hover:text-gray-200'
                }`}
              >
                {tab.label}
              </button>
            ))}
          </nav>
        </div>
      </header>

      {/* Main Content */}
      <main className="max-w-7xl mx-auto px-4 py-6">
        {activeTab === 'dashboard' && (
          <CEIDashboard results={analysisResults} />
        )}
        {activeTab === 'analysis' && (
          <AnalysisPanel onResults={setAnalysisResults} />
        )}
        {activeTab === 'governance' && (
          <GovernancePanel />
        )}
        {activeTab === 'graph' && (
          <GraphVisualization results={analysisResults} />
        )}
      </main>
    </div>
  );
}
