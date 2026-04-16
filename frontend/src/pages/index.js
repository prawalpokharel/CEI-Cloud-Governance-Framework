import React, { useState } from 'react';
import Link from 'next/link';
import Head from 'next/head';
import CEIDashboard from '../components/dashboard/CEIDashboard';
import QuickWins from '../components/dashboard/QuickWins';
import GovernancePanel from '../components/governance/GovernancePanel';
import AnalysisPanel from '../components/analysis/AnalysisPanel';
import GraphVisualization from '../components/visualization/GraphVisualization';
import useIsMobile from '../hooks/useIsMobile';

/**
 * CloudOptimizer — Main Application Page
 * Interactive dashboard for governance-aware infrastructure optimization
 * using the Centrality-Entropy Index (CEI) framework.
 */
export default function Home() {
  const [activeTab, setActiveTab] = useState('dashboard');
  const [analysisResults, setAnalysisResults] = useState(null);
  const isMobile = useIsMobile();

  const tabs = [
    { id: 'dashboard', label: 'CEI Dashboard' },
    { id: 'quickwins', label: 'Quick Wins' },
    { id: 'analysis', label: 'Run Analysis' },
    { id: 'governance', label: 'Governance' },
    { id: 'graph', label: 'Dependency Graph' },
  ];

  return (
    <>
      <Head>
        <title>CloudOptimizer — Governance-Aware Resource Allocation</title>
      </Head>
      <div style={styles.page}>
        <header style={styles.header}>
          <div
            style={{
              ...styles.headerInner,
              ...(isMobile ? styles.headerInnerMobile : {}),
            }}
          >
            <div style={styles.brand}>
              <h1
                style={{
                  ...styles.title,
                  ...(isMobile ? { fontSize: 22 } : {}),
                }}
              >
                CloudOptimizer
              </h1>
              <p
                style={{
                  ...styles.subtitle,
                  ...(isMobile ? { fontSize: 12 } : {}),
                }}
              >
                Governance-Aware Dynamic Resource Allocation · CEI Framework
              </p>
            </div>
            <div
              style={{
                ...styles.headerRight,
                ...(isMobile ? styles.headerRightMobile : {}),
              }}
            >
              <Link href="/connect" style={styles.connectButton}>
                Connect Cloud →
              </Link>
              <Link href="/upload" style={styles.demoButton}>
                Upload
              </Link>
              <Link href="/demo" style={styles.demoButton}>
                {isMobile ? 'Demos' : 'Live Scenarios'}
              </Link>
              <Link href="/national-interest" style={styles.reviewersButton}>
                {isMobile ? 'Reviewers' : 'For Reviewers'}
              </Link>
              {!isMobile && (
                <div style={styles.patentBadge}>
                  <div style={styles.patentLabel}>USPTO</div>
                  <div style={styles.patentNumber}>App. No. 19/641,446</div>
                </div>
              )}
            </div>
          </div>

          <div style={styles.tabsBar} className="tabs-scroll">
            <nav style={styles.tabs}>
              {tabs.map((tab) => (
                <button
                  key={tab.id}
                  onClick={() => setActiveTab(tab.id)}
                  style={{
                    ...styles.tab,
                    ...(activeTab === tab.id ? styles.tabActive : {}),
                  }}
                >
                  {tab.label}
                </button>
              ))}
            </nav>
          </div>
        </header>

        <main style={styles.main}>
          {!analysisResults && (
            <div style={styles.cta}>
              <h2 style={styles.ctaTitle}>Get Started</h2>
              <p style={styles.ctaText}>
                Run analysis on your own data via the <strong>Run Analysis</strong>{' '}
                tab, or jump into a pre-built scenario to see the framework in
                action across five domains.
              </p>
              <Link href="/demo" style={styles.ctaButton}>
                Browse Live Scenarios →
              </Link>
            </div>
          )}

          {activeTab === 'dashboard' && (
            <CEIDashboard results={analysisResults} />
          )}
          {activeTab === 'quickwins' && (
            <QuickWins results={analysisResults} />
          )}
          {activeTab === 'analysis' && (
            <AnalysisPanel onResults={setAnalysisResults} />
          )}
          {activeTab === 'governance' && <GovernancePanel />}
          {activeTab === 'graph' && (
            <GraphVisualization results={analysisResults} />
          )}
        </main>

        <footer style={styles.footer}>
          <div>
            CloudOptimizer · Patent-pending CEI framework · Prawal Pokharel
          </div>
          <div style={styles.footerLinks}>
            <Link href="/connect" style={styles.footerLink}>
              Connect Cloud
            </Link>
            <Link href="/upload" style={styles.footerLink}>
              Upload
            </Link>
            <Link href="/demo" style={styles.footerLink}>
              Live Demo
            </Link>
            <Link href="/national-interest" style={styles.footerLink}>
              For Reviewers
            </Link>
            <a
              href="https://github.com/prawalpokharel/CEI-Cloud-Governance-Framework"
              style={styles.footerLink}
            >
              GitHub
            </a>
          </div>
        </footer>
      </div>
    </>
  );
}

const styles = {
  page: {
    minHeight: '100vh',
    background: '#F8F9FA',
    fontFamily:
      "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
    color: '#1C2833',
    display: 'flex',
    flexDirection: 'column',
  },
  header: {
    background: 'linear-gradient(135deg, #1B4F72 0%, #2874A6 100%)',
    color: 'white',
  },
  headerInner: {
    maxWidth: 1200,
    margin: '0 auto',
    padding: '20px 32px',
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    gap: 24,
    flexWrap: 'wrap',
  },
  headerInnerMobile: {
    padding: '14px 16px',
    gap: 12,
    flexDirection: 'column',
    alignItems: 'stretch',
  },
  headerRightMobile: {
    gap: 6,
    justifyContent: 'flex-start',
  },
  brand: { minWidth: 0, flex: '1 1 auto' },
  title: {
    margin: 0,
    fontSize: 26,
    fontWeight: 700,
    letterSpacing: '-0.5px',
    lineHeight: 1.2,
  },
  subtitle: {
    margin: '4px 0 0 0',
    fontSize: 13,
    opacity: 0.85,
    lineHeight: 1.4,
  },
  headerRight: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    flexWrap: 'wrap',
    justifyContent: 'flex-end',
  },
  connectButton: {
    background: 'rgba(255,255,255,0.98)',
    color: '#1B4F72',
    padding: '8px 14px',
    borderRadius: 6,
    fontSize: 12,
    fontWeight: 700,
    textDecoration: 'none',
    boxShadow: '0 1px 3px rgba(0,0,0,0.15)',
    whiteSpace: 'nowrap',
  },
  demoButton: {
    background: 'rgba(255,255,255,0.15)',
    color: 'white',
    padding: '8px 14px',
    borderRadius: 6,
    fontSize: 12,
    fontWeight: 600,
    textDecoration: 'none',
    border: '1px solid rgba(255,255,255,0.3)',
    whiteSpace: 'nowrap',
  },
  reviewersButton: {
    background: 'transparent',
    color: '#EAF4FB',
    padding: '8px 14px',
    borderRadius: 6,
    fontSize: 12,
    fontWeight: 600,
    textDecoration: 'none',
    border: '1px solid rgba(174, 214, 241, 0.55)',
    whiteSpace: 'nowrap',
  },
  patentBadge: {
    background: 'rgba(255,255,255,0.12)',
    padding: '6px 12px',
    borderRadius: 6,
    border: '1px solid rgba(255,255,255,0.22)',
    whiteSpace: 'nowrap',
  },
  patentLabel: { fontSize: 9, opacity: 0.8, letterSpacing: '1px' },
  patentNumber: { fontSize: 11, fontWeight: 600, marginTop: 1 },
  tabsBar: {
    background: 'rgba(0,0,0,0.15)',
    borderTop: '1px solid rgba(255,255,255,0.1)',
  },
  tabs: {
    maxWidth: 1200,
    margin: '0 auto',
    padding: '0 16px',
    display: 'flex',
    gap: 4,
    whiteSpace: 'nowrap',
  },
  tab: {
    padding: '12px 18px',
    fontSize: 13,
    fontWeight: 500,
    color: 'rgba(255,255,255,0.7)',
    background: 'transparent',
    border: 'none',
    borderBottom: '2px solid transparent',
    cursor: 'pointer',
    transition: 'color 0.15s, border-color 0.15s',
  },
  tabActive: {
    color: 'white',
    borderBottom: '2px solid #85C1E2',
  },
  main: {
    maxWidth: 1200,
    width: '100%',
    margin: '0 auto',
    padding: '24px 16px',
    boxSizing: 'border-box',
    flex: '1 0 auto',
  },
  cta: {
    background: 'white',
    borderRadius: 8,
    padding: 32,
    marginBottom: 24,
    boxShadow: '0 2px 8px rgba(0,0,0,0.06)',
    borderLeft: '4px solid #2874A6',
  },
  ctaTitle: {
    margin: '0 0 8px 0',
    color: '#1B4F72',
    fontSize: 20,
    fontWeight: 600,
  },
  ctaText: {
    margin: '0 0 16px 0',
    color: '#566573',
    fontSize: 14,
    lineHeight: 1.6,
  },
  ctaButton: {
    display: 'inline-block',
    background: '#2874A6',
    color: 'white',
    padding: '10px 20px',
    borderRadius: 6,
    fontSize: 13,
    fontWeight: 600,
    textDecoration: 'none',
  },
  footer: {
    background: '#1C2833',
    color: '#BDC3C7',
    padding: '18px 16px',
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    fontSize: 13,
    flexWrap: 'wrap',
    gap: 12,
    flexShrink: 0,
  },
  footerLinks: { display: 'flex', gap: 20 },
  footerLink: { color: '#85C1E2', textDecoration: 'none' },
};
