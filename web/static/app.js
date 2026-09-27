/* ========================================================================
   TradingAgents Dashboard — Application Logic
   ======================================================================== */

(function () {
  'use strict';

  // ---- State ----
  let configData = null;
  let currentRunId = null;
  let eventSource = null;

  // ---- DOM refs ----
  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => document.querySelectorAll(sel);

  const DOM = {
    tabs:           $$('.tab'),
    views:          $$('.view'),
    form:           $('#analysisForm'),
    ticker:         $('#ticker'),
    tradeDate:      $('#tradeDate'),
    apiKeyGroup:    $('#apiKeyGroup'),
    apiKey:         $('#apiKey'),
    provider:       $('#provider'),
    quickModel:     $('#quickModel'),
    deepModel:      $('#deepModel'),
    analystToggles: $$('.analyst-toggle'),
    btnRun:         $('#btnRun'),
    pipelineCard:   $('#pipelineCard'),
    pipelineContent:$('#pipelineContent'),
    reportContent:  $('#reportContent'),
    historyContent: $('#historyContent'),
    reportOverlay:  $('#reportOverlay'),
    reportModalTitle: $('#reportModalTitle'),
    reportModalBody:  $('#reportModalBody'),
    reportModalClose: $('#reportModalClose'),
    toast:          $('#toast'),
    serverDot:      $('#serverDot'),
    serverStatus:   $('#serverStatus'),
  };

  // ---- Init ----
  async function init() {
    setDefaultDate();
    bindEvents();
    await loadConfig();
    await loadHistory();
    await loadRuns();
  }

  function setDefaultDate() {
    const today = new Date().toISOString().split('T')[0];
    DOM.tradeDate.value = today;
  }

  // ---- Tabs ----
  function bindEvents() {
    DOM.tabs.forEach(tab => {
      tab.addEventListener('click', () => {
        DOM.tabs.forEach(t => { t.classList.remove('active'); t.setAttribute('aria-selected', 'false'); });
        DOM.views.forEach(v => v.classList.remove('active'));
        tab.classList.add('active');
        tab.setAttribute('aria-selected', 'true');
        $(`#view-${tab.dataset.view}`).classList.add('active');
        if (tab.dataset.view === 'history') {
          loadRuns();
          loadHistory();
        }
      });
    });

    DOM.analystToggles.forEach(btn => {
      btn.addEventListener('click', () => btn.classList.toggle('active'));
    });

    DOM.provider.addEventListener('change', updateModelSelects);
    DOM.form.addEventListener('submit', handleSubmit);
    DOM.reportModalClose.addEventListener('click', closeReportModal);
    DOM.reportOverlay.addEventListener('click', (e) => {
      if (e.target === DOM.reportOverlay) closeReportModal();
    });
    if (DOM.apiKey) {
      DOM.apiKey.addEventListener('input', () => {
        localStorage.setItem('tradingagents_api_key', DOM.apiKey.value.trim());
      });
    }
  }

  // ---- Config ----
  async function loadConfig() {
    try {
      const res = await fetch('/api/config');
      configData = await res.json();
      populateProviders();
      if (configData.auth_required && DOM.apiKeyGroup) {
        DOM.apiKeyGroup.style.display = 'block';
        const savedKey = localStorage.getItem('tradingagents_api_key');
        if (savedKey && DOM.apiKey) {
          DOM.apiKey.value = savedKey;
        }
      }
      DOM.serverDot.style.background = 'var(--green)';
      DOM.serverStatus.textContent = 'Connected';
    } catch (err) {
      DOM.serverDot.style.background = 'var(--red)';
      DOM.serverStatus.textContent = 'Disconnected';
      showToast('Failed to connect to server', 'error');
    }
  }

  function populateProviders() {
    if (!configData) return;
    DOM.provider.innerHTML = '';

    configData.providers.forEach(p => {
      const opt = document.createElement('option');
      opt.value = p.id;
      opt.textContent = p.name;
      if (p.id === configData.provider) opt.selected = true;
      DOM.provider.appendChild(opt);
    });

    updateModelSelects();
  }

  function updateModelSelects() {
    if (!configData) return;
    const providerId = DOM.provider.value;
    const provider = configData.providers.find(p => p.id === providerId);
    if (!provider) return;

    [DOM.quickModel, DOM.deepModel].forEach(sel => {
      sel.innerHTML = '';
      provider.models.forEach(m => {
        const opt = document.createElement('option');
        opt.value = m;
        opt.textContent = m;
        sel.appendChild(opt);
      });
    });

    // Try to pre-select current config models
    if (configData.quick_model) {
      const match = [...DOM.quickModel.options].find(o => o.value === configData.quick_model);
      if (match) match.selected = true;
    }
    if (configData.deep_model) {
      const match = [...DOM.deepModel.options].find(o => o.value === configData.deep_model);
      if (match) match.selected = true;
    }
  }

  // ---- Submit Analysis ----
  async function handleSubmit(e) {
    e.preventDefault();

    const analysts = [];
    DOM.analystToggles.forEach(btn => {
      if (btn.classList.contains('active')) {
        analysts.push(btn.dataset.analyst);
      }
    });

    if (analysts.length === 0) {
      showToast('Select at least one analyst', 'error');
      return;
    }

    const payload = {
      ticker: DOM.ticker.value.trim().toUpperCase() || 'NVDA',
      date: DOM.tradeDate.value || null,
      analysts: analysts,
      provider: DOM.provider.value || null,
      quick_model: DOM.quickModel.value || null,
      deep_model: DOM.deepModel.value || null,
    };

    DOM.btnRun.disabled = true;
    DOM.btnRun.innerHTML = `
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="animation: spin 1s linear infinite;">
        <circle cx="12" cy="12" r="10" stroke-dasharray="31" stroke-dashoffset="10"/>
      </svg>
      Initializing...
    `;

    const headers = { 'Content-Type': 'application/json' };
    const savedApiKey = (DOM.apiKey ? DOM.apiKey.value.trim() : '') || localStorage.getItem('tradingagents_api_key');
    if (savedApiKey) {
      headers['X-API-Key'] = savedApiKey;
    }

    try {
      const res = await fetch('/api/analyze', {
        method: 'POST',
        headers: headers,
        body: JSON.stringify(payload),
      });

      const data = await res.json();
      if (!res.ok) {
        if (res.status === 401 && DOM.apiKeyGroup) {
          DOM.apiKeyGroup.style.display = 'block';
        }
        throw new Error(data.detail || 'Failed to start analysis');
      }

      currentRunId = data.run_id;
      showPipeline(payload.ticker, payload.date || 'today');
      connectSSE(data.run_id);
      showToast(`Analysis started for ${payload.ticker}`, 'success');

    } catch (err) {
      showToast(err.message, 'error');
      resetRunButton();
    }
  }

  // ---- Pipeline UI ----
  const PIPELINE_NODES = [
    { id: 'market_analyst', label: 'Market Analyst', icon: '📈' },
    { id: 'social_media_analyst', label: 'Sentiment Analyst', icon: '💬' },
    { id: 'news_analyst', label: 'News Analyst', icon: '📰' },
    { id: 'fundamentals_analyst', label: 'Fundamentals Analyst', icon: '📊' },
    { id: 'bull_researcher', label: 'Bull Researcher', icon: '🐂' },
    { id: 'bear_researcher', label: 'Bear Researcher', icon: '🐻' },
    { id: 'research_manager', label: 'Research Manager', icon: '🎯' },
    { id: 'trader', label: 'Trader', icon: '💼' },
    { id: 'aggressive_debater', label: 'Aggressive Debater', icon: '🔥' },
    { id: 'conservative_debater', label: 'Conservative Debater', icon: '🛡️' },
    { id: 'neutral_debater', label: 'Neutral Debater', icon: '⚖️' },
    { id: 'risk_manager', label: 'Risk Manager', icon: '📋' },
    { id: 'portfolio_manager', label: 'Portfolio Manager', icon: '🏦' },
  ];

  function showPipeline(ticker, date) {
    DOM.pipelineContent.innerHTML = `
      <div class="pipeline-container">
        <div class="pipeline-header">
          <span class="pipeline-title">Analyzing <strong style="color:var(--cyan)">${escapeText(ticker)}</strong> — ${escapeText(date)}</span>
          <span class="pipeline-percent" id="progressPercent">0%</span>
        </div>
        <div class="progress-bar-track">
          <div class="progress-bar-fill" id="progressBar"></div>
        </div>
        <div class="pipeline-nodes" id="pipelineNodes">
          ${PIPELINE_NODES.map(n => `
            <div class="pipeline-node" id="node-${escapeText(n.id)}">
              <div class="node-indicator"></div>
              <span class="node-label">${n.icon} ${escapeText(n.label)}</span>
            </div>
          `).join('')}
        </div>
      </div>
    `;
  }

  // ---- SSE ----
  let reconnectAttempts = 0;
  const MAX_RECONNECT_ATTEMPTS = 3;

  function connectSSE(runId) {
    if (eventSource) {
      eventSource.close();
      eventSource = null;
    }

    eventSource = new EventSource(`/api/runs/${encodeURIComponent(runId)}/events`);

    eventSource.addEventListener('status', (e) => {
      try {
        const data = JSON.parse(e.data);
        DOM.btnRun.innerHTML = `
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="animation: spin 1s linear infinite;">
            <circle cx="12" cy="12" r="10" stroke-dasharray="31" stroke-dashoffset="10"/>
          </svg>
          ${escapeText(data.message || 'Running...')}
        `;
      } catch (_) {}
    });

    eventSource.addEventListener('node', (e) => {
      try {
        const data = JSON.parse(e.data);
        updatePipelineNode(data);
      } catch (_) {}
    });

    eventSource.addEventListener('complete', (e) => {
      try {
        const data = JSON.parse(e.data);
        if (eventSource) {
          eventSource.close();
          eventSource = null;
        }

        // Mark all nodes as done
        PIPELINE_NODES.forEach(n => {
          const el = document.getElementById(`node-${n.id}`);
          if (el) { el.classList.remove('active'); el.classList.add('done'); }
        });

        const bar = document.getElementById('progressBar');
        const pct = document.getElementById('progressPercent');
        if (bar) bar.style.width = '100%';
        if (pct) pct.textContent = '100%';

        // Show signal in pipeline
        const container = document.querySelector('.pipeline-container');
        if (container) {
          const signalText = escapeText(data.signal || 'REVIEW');
          const signalClass = (data.signal || '').toLowerCase().replace(/[^a-z]/g, '');
          container.insertAdjacentHTML('beforeend', `
            <div style="margin-top:1.5rem; text-align:center;">
              <div style="font-size:0.8rem; color:var(--text-muted); margin-bottom:0.5rem; text-transform:uppercase; letter-spacing:0.1em;">Signal</div>
              <span class="signal-badge ${signalClass}">${signalText}</span>
            </div>
          `);
        }

        resetRunButton();
        showToast(`Analysis complete: ${data.signal || 'Done'}`, 'success');
        loadReport(runId);
        loadRuns();
        loadHistory();
      } catch (err) {
        resetRunButton();
      }
    });

    eventSource.addEventListener('error', (e) => {
      try {
        const data = JSON.parse(e.data);
        if (eventSource) {
          eventSource.close();
          eventSource = null;
        }

        // Mark remaining as error
        PIPELINE_NODES.forEach(n => {
          const el = document.getElementById(`node-${n.id}`);
          if (el && !el.classList.contains('done')) {
            el.classList.remove('active');
          }
        });

        resetRunButton();
        showToast(data.error || 'Analysis failed', 'error');
        loadRuns();
      } catch {
        // SSE transport error; handled in eventSource.onerror
      }
    });

    eventSource.onerror = async () => {
      if (eventSource) {
        eventSource.close();
        eventSource = null;
      }
      await handleEventDisconnect(runId);
    };
  }

  async function handleEventDisconnect(runId) {
    try {
      const res = await fetch(`/api/runs/${encodeURIComponent(runId)}`);
      if (res.ok) {
        const run = await res.json();
        if (run.status === 'completed') {
          PIPELINE_NODES.forEach(n => {
            const el = document.getElementById(`node-${n.id}`);
            if (el) { el.classList.remove('active'); el.classList.add('done'); }
          });
          const bar = document.getElementById('progressBar');
          const pct = document.getElementById('progressPercent');
          if (bar) bar.style.width = '100%';
          if (pct) pct.textContent = '100%';

          resetRunButton();
          showToast(`Analysis complete: ${run.signal || 'Done'}`, 'success');
          loadReport(runId);
          await loadRuns();
          return;
        } else if (run.status === 'failed') {
          PIPELINE_NODES.forEach(n => {
            const el = document.getElementById(`node-${n.id}`);
            if (el && !el.classList.contains('done')) {
              el.classList.remove('active');
            }
          });
          resetRunButton();
          showToast(run.error || 'Analysis failed', 'error');
          await loadRuns();
          return;
        } else if (reconnectAttempts < MAX_RECONNECT_ATTEMPTS) {
          reconnectAttempts++;
          setTimeout(() => {
            connectSSE(runId);
          }, 1500);
          return;
        }
      }
    } catch (err) {
      console.warn('Failed to check run status after SSE error:', err);
    }

    reconnectAttempts = 0;
    resetRunButton();
    showToast('Event connection lost. Check history or retry.', 'warning');
    await loadRuns();
  }

  let completedNodes = new Set();

  function updatePipelineNode(data) {
    // Mark previous active as done
    $$('.pipeline-node.active').forEach(el => {
      el.classList.remove('active');
      el.classList.add('done');
    });

    // Try to match by label/node name
    const matchId = findNodeId(data.node || data.label);
    if (matchId) {
      completedNodes.add(matchId);
      const el = document.getElementById(`node-${matchId}`);
      if (el) {
        el.classList.add('active');
      }
    }

    // Update progress bar
    const progress = data.progress || (completedNodes.size / PIPELINE_NODES.length);
    const bar = document.getElementById('progressBar');
    const pct = document.getElementById('progressPercent');
    if (bar) bar.style.width = `${Math.round(progress * 100)}%`;
    if (pct) pct.textContent = `${Math.round(progress * 100)}%`;
  }

  function findNodeId(hint) {
    if (!hint) return null;
    const lower = hint.toLowerCase().replace(/[^a-z_]/g, '');
    // Direct match
    const direct = PIPELINE_NODES.find(n => n.id === hint || n.id === lower);
    if (direct) return direct.id;
    // Fuzzy match
    const fuzzy = PIPELINE_NODES.find(n =>
      lower.includes(n.id.replace(/_/g, '')) ||
      n.label.toLowerCase().replace(/\s/g, '').includes(lower)
    );
    return fuzzy ? fuzzy.id : null;
  }

  function resetRunButton() {
    DOM.btnRun.disabled = false;
    DOM.btnRun.innerHTML = `
      <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"/></svg>
      Launch Analysis
    `;
    completedNodes = new Set();
  }

  // ---- Report ----
  async function loadReport(runId) {
    try {
      const res = await fetch(`/api/runs/${runId}/report`);
      if (!res.ok) return;
      const report = await res.json();
      renderReport(report);

      // Switch to report tab
      DOM.tabs.forEach(t => { t.classList.remove('active'); t.setAttribute('aria-selected', 'false'); });
      DOM.views.forEach(v => v.classList.remove('active'));
      const reportTab = document.querySelector('[data-view="report"]');
      reportTab.classList.add('active');
      reportTab.setAttribute('aria-selected', 'true');
      $('#view-report').classList.add('active');
    } catch (err) {
      console.error('Failed to load report:', err);
    }
  }

  function renderReport(report) {
    const signalText = escapeText(report.signal || 'REVIEW');
    const signalClass = (report.signal || '').toLowerCase().replace(/[^a-z]/g, '');

    const sections = [
      { title: '📈 Market Analysis',       key: 'market_report',     icon: '📈' },
      { title: '💬 Sentiment Analysis',    key: 'sentiment_report',  icon: '💬' },
      { title: '📰 News Analysis',         key: 'news_report',       icon: '📰' },
      { title: '📊 Fundamentals Analysis', key: 'fundamentals_report', icon: '📊' },
    ];

    let html = `
      <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:1.5rem; flex-wrap:wrap; gap:1rem;">
        <div>
          <h1 style="font-size:1.5rem; font-weight:800; color:var(--text-bright); margin-bottom:4px;">
            ${escapeText(report.ticker)} Analysis Report
          </h1>
          <p style="font-size:0.85rem; color:var(--text-muted);">Date: ${escapeText(report.date)}</p>
        </div>
        <span class="signal-badge ${signalClass}" style="font-size:1rem; padding:8px 24px;">
          ${signalText}
        </span>
      </div>
    `;

    // Analyst sections
    sections.forEach(sec => {
      const content = report[sec.key];
      if (content) {
        html += createReportSection(sec.title, content, true);
      }
    });

    // Investment Debate
    if (report.investment_debate) {
      const debate = report.investment_debate;
      let debateContent = '';
      if (debate.bull_history) debateContent += `### 🐂 Bull Case\n${debate.bull_history}\n\n`;
      if (debate.bear_history) debateContent += `### 🐻 Bear Case\n${debate.bear_history}\n\n`;
      if (debate.judge_decision) debateContent += `### 🎯 Research Manager Decision\n${debate.judge_decision}\n\n`;
      html += createReportSection('⚔️ Investment Debate', debateContent);
    }

    // Trader Plan
    if (report.trader_plan) {
      html += createReportSection('💼 Trader Investment Plan', report.trader_plan);
    }

    // Risk Debate
    if (report.risk_debate) {
      const risk = report.risk_debate;
      let riskContent = '';
      if (risk.aggressive_history) riskContent += `### 🔥 Aggressive\n${risk.aggressive_history}\n\n`;
      if (risk.conservative_history) riskContent += `### 🛡️ Conservative\n${risk.conservative_history}\n\n`;
      if (risk.neutral_history) riskContent += `### ⚖️ Neutral\n${risk.neutral_history}\n\n`;
      if (risk.judge_decision) riskContent += `### 🏦 Portfolio Manager\n${risk.judge_decision}\n\n`;
      html += createReportSection('🎯 Risk Management Debate', riskContent);
    }

    // Final Decision
    if (report.final_decision) {
      html += createReportSection('🏆 Final Trade Decision', report.final_decision, true);
    }

    DOM.reportContent.innerHTML = html;

    // Bind section toggles
    $$('.report-section-header').forEach(header => {
      header.addEventListener('click', () => {
        header.parentElement.classList.toggle('open');
      });
    });
  }

  function createReportSection(title, content, openByDefault = false) {
    return `
      <div class="report-section ${openByDefault ? 'open' : ''}">
        <div class="report-section-header">
          <div class="report-section-title">${title}</div>
          <svg class="report-section-arrow" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"/></svg>
        </div>
        <div class="report-section-body">
          <div class="report-content">${escapeHtml(content)}</div>
        </div>
      </div>
    `;
  }

  // ---- History ----
  let inMemoryRuns = [];
  let diskHistory = [];

  async function loadRuns() {
    try {
      const res = await fetch('/api/runs');
      if (res.ok) {
        const data = await res.json();
        inMemoryRuns = data.runs || [];
        renderCombinedHistory();
      }
    } catch (err) {
      console.error('Failed to load runs:', err);
    }
  }

  async function loadHistory() {
    try {
      const res = await fetch('/api/history');
      if (res.ok) {
        const data = await res.json();
        diskHistory = data.history || [];
        renderCombinedHistory();
      }
    } catch (err) {
      console.error('Failed to load history:', err);
    }
  }

  function renderCombinedHistory() {
    const seenIds = new Set();
    const combined = [];

    // Add active / in-memory runs first
    inMemoryRuns.forEach(r => {
      seenIds.add(r.run_id);
      if (r.report_id) {
        seenIds.add(r.report_id);
      }
      if (r.report_path) {
        const parts = r.report_path.replace(/\\/g, '/').split('/');
        const folder = parts.length > 1 ? parts[parts.length - 2] : '';
        if (folder) seenIds.add(folder);
      }
      combined.push(r);
    });

    // Add saved reports from disk
    diskHistory.forEach(h => {
      const id = h.run_id || h.id;
      const isKnownLive = (h.live_run_id && seenIds.has(h.live_run_id));
      if (!seenIds.has(id) && !isKnownLive) {
        seenIds.add(id);
        combined.push({
          run_id: id,
          ticker: h.ticker,
          date: h.date || h.timestamp || '',
          provider: h.provider || 'Saved',
          status: h.status || 'completed',
          signal: h.signal || null,
          started_at: h.timestamp || h.date || '',
          time: h.time || '',
        });
      }
    });

    if (combined.length === 0) {
      DOM.historyContent.innerHTML = `
        <div class="empty-state">
          <div class="empty-state-icon">📊</div>
          <div class="empty-state-title">No Analysis History</div>
          <div class="empty-state-desc">Run your first analysis to see results here.</div>
        </div>
      `;
      return;
    }

    DOM.historyContent.innerHTML = `
      <table class="history-table">
        <thead>
          <tr>
            <th>Ticker</th>
            <th>Date</th>
            <th>Provider</th>
            <th>Status</th>
            <th>Signal</th>
            <th>Time</th>
          </tr>
        </thead>
        <tbody id="historyTableBody">
          ${combined.map(run => {
            const signalClass = (run.signal || 'unknown').toLowerCase().replace(/[^a-z]/g, '');
            let timeStr = run.time || '-';
            if (!run.time && run.started_at) {
              try {
                const startedAt = new Date(run.started_at);
                if (!isNaN(startedAt.getTime())) {
                  timeStr = startedAt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
                }
              } catch (_) {}
            }

            return `
              <tr data-run-id="${escapeText(run.run_id)}">
                <td class="ticker-cell">${escapeText(run.ticker)}</td>
                <td class="date-cell">${escapeText(run.date)}</td>
                <td style="font-size:0.82rem;">${escapeText(run.provider || '-')}</td>
                <td><span class="status-cell ${escapeText(run.status)}">${escapeText(run.status)}</span></td>
                <td>${run.signal ? `<span class="signal-badge ${signalClass}" style="font-size:0.75rem; padding:3px 10px;">${escapeText(run.signal)}</span>` : '-'}</td>
                <td class="date-cell">${escapeText(timeStr)}</td>
              </tr>
            `;
          }).join('')}
        </tbody>
      </table>
    `;

    // Attach click listeners to rows safely without inline eval
    const tbody = document.getElementById('historyTableBody');
    if (tbody) {
      tbody.querySelectorAll('tr').forEach(row => {
        row.addEventListener('click', () => {
          const runId = row.getAttribute('data-run-id');
          if (runId) {
            window.__viewReport(runId);
          }
        });
      });
    }
  }

  // View report from history
  window.__viewReport = async function(runId) {
    try {
      const res = await fetch(`/api/runs/${encodeURIComponent(runId)}/report`);
      if (res.ok) {
        const report = await res.json();
        showReportModal(report);
      } else {
        showToast('Report not available for this run', 'error');
      }
    } catch (err) {
      showToast('Failed to load report', 'error');
    }
  };

  function showReportModal(report) {
    const signalText = escapeText(report.signal || 'REVIEW');
    const signalClass = (report.signal || '').toLowerCase().replace(/[^a-z]/g, '');
    DOM.reportModalTitle.innerHTML = `
      <span style="color:var(--cyan)">${escapeText(report.ticker)}</span>
      <span class="signal-badge ${signalClass}" style="font-size:0.75rem; padding:3px 10px;">
        ${signalText}
      </span>
    `;
    DOM.reportModalBody.innerHTML = `<div class="report-content">${escapeHtml(report.final_decision || 'No decision available.')}</div>`;
    DOM.reportOverlay.classList.add('active');
    document.body.style.overflow = 'hidden';
  }

  function closeReportModal() {
    DOM.reportOverlay.classList.remove('active');
    document.body.style.overflow = '';
  }

  // ---- Utilities ----
  function escapeText(str) {
    if (str == null) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function escapeHtml(text) {
    if (!text) return '';
    return String(text)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;')
      .replace(/\n/g, '<br>')
      .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
      .replace(/### (.*?)(<br>|$)/g, '<h3>$1</h3>')
      .replace(/## (.*?)(<br>|$)/g, '<h2>$1</h2>')
      .replace(/# (.*?)(<br>|$)/g, '<h1>$1</h1>');
  }

  function showToast(message, type = 'info') {
    DOM.toast.textContent = message;
    DOM.toast.className = `toast ${type} show`;
    setTimeout(() => DOM.toast.classList.remove('show'), 4000);
  }

  // CSS animation for spinner
  const style = document.createElement('style');
  style.textContent = `@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }`;
  document.head.appendChild(style);

  // ---- Boot ----
  document.addEventListener('DOMContentLoaded', init);
})();
