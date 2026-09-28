/* ========================================================================
   TradingAgents Dashboard — Multi-Asset Forex & Equities Terminal
   ======================================================================== */

(function () {
  'use strict';

  // ---- State ----
  let configData = null;
  let currentRunId = null;
  let eventSource = null;
  let currentAssetMode = 'forex'; // 'forex' or 'equities'
  let activePipelineType = 'forex';
  let inMemoryApiKey = '';

  // ---- DOM refs ----
  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => document.querySelectorAll(sel);

  const DOM = {
    tabs:           $$('.tab'),
    views:          $$('.view'),
    form:           $('#analysisForm'),
    modeForex:      $('#modeForex'),
    modeEquities:   $('#modeEquities'),
    forexControls:  $('#forexControls'),
    equitiesControls: $('#equitiesControls'),
    forexPair:      $('#forexPair'),
    forexTimeframe: $('#forexTimeframe'),
    accountBalance: $('#accountBalance'),
    riskPercent:    $('#riskPercent'),
    forexAnalystToggles: $$('#forexAnalystToggles .analyst-toggle'),
    ticker:         $('#ticker'),
    tradeDate:      $('#tradeDate'),
    apiKeyGroup:    $('#apiKeyGroup'),
    apiKey:         $('#apiKey'),
    provider:       $('#provider'),
    quickModel:     $('#quickModel'),
    deepModel:      $('#deepModel'),
    analystToggles: $$('#analystToggles .analyst-toggle'),
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
    mt5Dot:         $('#mt5Dot'),
    mt5Status:      $('#mt5Status'),
    mt5Balance:     $('#mt5Balance'),
    mt5Equity:      $('#mt5Equity'),
    mt5Floating:    $('#mt5Floating'),
    mt5FreeMargin:  $('#mt5FreeMargin'),
    mt5PositionsContainer: $('#mt5PositionsContainer'),
    journalTableContainer: $('#journalTableContainer'),
    btnRefreshJournal: $('#btnRefreshJournal'),
    lessonsContainer: $('#lessonsContainer'),
    lessonsPairFilter: $('#lessonsPairFilter'),
    statWinRate:    $('#statWinRate'),
    statProfitFactor: $('#statProfitFactor'),
    statTotalR:     $('#statTotalR'),
    statMaxDD:      $('#statMaxDD'),
    statTotalTrades: $('#statTotalTrades'),
    // Dashboard Overview DOM
    dashBalance:    $('#dashBalance'),
    dashEquity:     $('#dashEquity'),
    dashFloating:   $('#dashFloating'),
    dashFreeMargin: $('#dashFreeMargin'),
    dashCurrency:   $('#dashCurrency'),
    dashLeverage:   $('#dashLeverage'),
    dashPositionsContainer: $('#dashPositionsContainer'),
    dashProposalsContainer: $('#dashProposalsContainer'),
    dashWinRate:    $('#dashWinRate'),
    dashProfitFactor: $('#dashProfitFactor'),
    dashTotalR:     $('#dashTotalR'),
    dashMaxDD:      $('#dashMaxDD'),
    dashClosedTrades: $('#dashClosedTrades'),
    dashLessonsContainer: $('#dashLessonsContainer'),
    btnDashRefreshPositions: $('#btnDashRefreshPositions'),
    btnDashRefreshProposals: $('#btnDashRefreshProposals'),
    // Proposals View DOM
    proposalsTableContainer: $('#proposalsTableContainer'),
    proposalsPairFilter: $('#proposalsPairFilter'),
    proposalsStatusFilter: $('#proposalsStatusFilter'),
    btnRefreshProposals: $('#btnRefreshProposals'),
    // MT5 View DOM
    mt5OrdersContainer: $('#mt5OrdersContainer'),
    mt5DealsContainer: $('#mt5DealsContainer'),
    btnRefreshMT5Positions: $('#btnRefreshMT5Positions'),
    // Performance View DOM
    btnRefreshAnalytics: $('#btnRefreshAnalytics'),
    perfBreakdownContainer: $('#perfBreakdownContainer'),
    // Backtesting View DOM
    btnModeBacktestReal: $('#btnModeBacktestReal'),
    btnModeBacktestDemo: $('#btnModeBacktestDemo'),
    backtestModeBanner:  $('#backtestModeBanner'),
    backtestForm:        $('#backtestForm'),
    btnLaunchBacktest:   $('#btnLaunchBacktest'),
    btnRefreshBacktests: $('#btnRefreshBacktests'),
    backtestRunsContainer: $('#backtestRunsContainer'),
    // Learning View DOM
    btnRefreshLessons:   $('#btnRefreshLessons'),
    // Settings View DOM
    settingProvider:     $('#settingProvider'),
    settingQuickModel:   $('#settingQuickModel'),
    settingDeepModel:    $('#settingDeepModel'),
  };

  // Pipeline Definitions
  const EQUITIES_PIPELINE_NODES = [
    { id: 'market_analyst', label: 'Market Analyst', icon: '📈' },
    { id: 'social_media_analyst', label: 'Sentiment Analyst', icon: '💬' },
    { id: 'news_analyst', label: 'News Analyst', icon: '📰' },
    { id: 'fundamentals_analyst', label: 'Fundamentals Analyst', icon: '📊' },
    { id: 'bull_researcher', label: 'Bull Researcher', icon: '🐂' },
    { id: 'bear_researcher', label: 'Bear Researcher', icon: '🐻' },
    { id: 'research_manager', label: 'Research Manager', icon: '🎯' },
    { id: 'trader', label: 'Trader', icon: '💼' },
    { id: 'risk_manager', label: 'Risk Manager', icon: '📋' },
    { id: 'portfolio_manager', label: 'Portfolio Manager', icon: '🏦' },
  ];

  const FOREX_PIPELINE_NODES = [
    { id: 'preparing_data', label: 'Market Data & Indicators', icon: '📊' },
    { id: 'technical_analyst', label: 'Technical Analyst', icon: '📈' },
    { id: 'macro_analyst', label: 'Macro Analyst', icon: '🌍' },
    { id: 'news_analyst', label: 'News Analyst', icon: '📰' },
    { id: 'bull_bear_debate', label: 'Bull / Bear Debate', icon: '⚔️' },
    { id: 'research_manager', label: 'Research Manager', icon: '🎯' },
    { id: 'trader', label: 'Trader Proposal', icon: '💼' },
    { id: 'risk_evaluator', label: 'Deterministic Risk Engine', icon: '🛡️' },
  ];

  // ---- Init ----
  async function init() {
    setDefaultDate();
    bindEvents();
    await loadConfig();
    await loadMT5Status();
    await loadDashboardOverview();
  }

  function setDefaultDate() {
    const today = new Date().toISOString().split('T')[0];
    if (DOM.tradeDate) DOM.tradeDate.value = today;
  }

  // ---- Event Bindings ----
  function bindEvents() {
    // Tab switching for 9 subsystems
    DOM.tabs.forEach(tab => {
      tab.addEventListener('click', () => {
        DOM.tabs.forEach(t => { t.classList.remove('active'); t.setAttribute('aria-selected', 'false'); });
        DOM.views.forEach(v => v.classList.remove('active'));
        tab.classList.add('active');
        tab.setAttribute('aria-selected', 'true');
        const viewEl = $(`#view-${tab.dataset.view}`);
        if (viewEl) viewEl.classList.add('active');

        if (tab.dataset.view === 'dashboard') {
          loadDashboardOverview();
        } else if (tab.dataset.view === 'proposals') {
          loadProposals();
        } else if (tab.dataset.view === 'mt5') {
          loadMT5Status();
          loadMT5Account();
          loadMT5Positions();
          loadMT5Orders();
          loadMT5Deals();
        } else if (tab.dataset.view === 'journal') {
          loadJournalTrades();
        } else if (tab.dataset.view === 'performance') {
          loadAnalyticsSummary();
          loadPerformanceBreakdown();
        } else if (tab.dataset.view === 'backtest') {
          loadBacktestRuns();
        } else if (tab.dataset.view === 'learning') {
          loadLessons(DOM.lessonsPairFilter ? DOM.lessonsPairFilter.value : '');
        } else if (tab.dataset.view === 'settings') {
          populateSettingsView();
        }
      });
    });

    // Asset class switcher
    if (DOM.modeForex && DOM.modeEquities) {
      DOM.modeForex.addEventListener('click', () => switchAssetMode('forex'));
      DOM.modeEquities.addEventListener('click', () => switchAssetMode('equities'));
    }

    // Analyst toggles
    DOM.analystToggles.forEach(btn => {
      btn.addEventListener('click', () => btn.classList.toggle('active'));
    });
    DOM.forexAnalystToggles.forEach(btn => {
      btn.addEventListener('click', () => {
        if (btn.classList.contains('mandatory-stage') || btn.disabled) return;
        btn.classList.toggle('active');
      });
    });

    DOM.provider.addEventListener('change', updateModelSelects);
    DOM.form.addEventListener('submit', handleSubmit);
    DOM.reportModalClose.addEventListener('click', closeReportModal);
    DOM.reportOverlay.addEventListener('click', (e) => {
      if (e.target === DOM.reportOverlay) closeReportModal();
    });

    if (DOM.btnRefreshJournal) {
      DOM.btnRefreshJournal.addEventListener('click', () => {
        loadMT5Account();
        loadMT5Positions();
        loadJournalTrades();
        showToast('Forex journal refreshed', 'info');
      });
    }

    if (DOM.btnDashRefreshPositions) {
      DOM.btnDashRefreshPositions.addEventListener('click', () => {
        loadDashboardPositions();
        showToast('Dashboard positions refreshed', 'info');
      });
    }

    if (DOM.btnDashRefreshProposals) {
      DOM.btnDashRefreshProposals.addEventListener('click', () => {
        loadDashboardProposals();
        showToast('Dashboard proposals refreshed', 'info');
      });
    }

    if (DOM.btnRefreshProposals) {
      DOM.btnRefreshProposals.addEventListener('click', () => {
        loadProposals();
        showToast('Proposals refreshed', 'info');
      });
    }

    if (DOM.proposalsPairFilter) {
      DOM.proposalsPairFilter.addEventListener('change', loadProposals);
    }
    if (DOM.proposalsStatusFilter) {
      DOM.proposalsStatusFilter.addEventListener('change', loadProposals);
    }

    if (DOM.btnRefreshMT5Positions) {
      DOM.btnRefreshMT5Positions.addEventListener('click', () => {
        loadMT5Positions();
        loadMT5Orders();
        loadMT5Deals();
        showToast('MT5 observer data refreshed', 'info');
      });
    }

    if (DOM.btnRefreshAnalytics) {
      DOM.btnRefreshAnalytics.addEventListener('click', () => {
        loadAnalyticsSummary();
        loadPerformanceBreakdown();
        showToast('Analytics refreshed', 'info');
      });
    }

    if (DOM.btnRefreshBacktests) {
      DOM.btnRefreshBacktests.addEventListener('click', () => {
        loadBacktestRuns();
        showToast('Backtest runs refreshed', 'info');
      });
    }

    if (DOM.btnRefreshLessons) {
      DOM.btnRefreshLessons.addEventListener('click', () => {
        loadLessons(DOM.lessonsPairFilter ? DOM.lessonsPairFilter.value : '');
        showToast('Lessons refreshed', 'info');
      });
    }

    if (DOM.btnModeBacktestReal && DOM.btnModeBacktestDemo) {
      DOM.btnModeBacktestReal.addEventListener('click', () => {
        DOM.btnModeBacktestReal.classList.add('active');
        DOM.btnModeBacktestDemo.classList.remove('active');
        if (DOM.backtestModeBanner) {
          DOM.backtestModeBanner.className = 'info-banner';
          DOM.backtestModeBanner.innerHTML = '<span>Historical Agent Backtest: Full point-in-time multi-agent execution. Zero lookahead leakage.</span>';
        }
      });
      DOM.btnModeBacktestDemo.addEventListener('click', () => {
        DOM.btnModeBacktestDemo.classList.add('active');
        DOM.btnModeBacktestReal.classList.remove('active');
        if (DOM.backtestModeBanner) {
          DOM.backtestModeBanner.className = 'warning-banner';
          DOM.backtestModeBanner.innerHTML = '<span>Demo Mode: Fast synthetic bar simulation. Strictly illustrative.</span>';
        }
      });
    }

    if (DOM.lessonsPairFilter) {
      DOM.lessonsPairFilter.addEventListener('change', () => {
        loadLessons(DOM.lessonsPairFilter.value);
      });
    }

    if (DOM.apiKey) {
      DOM.apiKey.addEventListener('input', () => {
        inMemoryApiKey = DOM.apiKey.value.trim();
      });
    }
  }

  function switchAssetMode(mode) {
    currentAssetMode = mode;
    if (mode === 'forex') {
      DOM.modeForex.classList.add('active');
      DOM.modeEquities.classList.remove('active');
      DOM.forexControls.style.display = 'block';
      DOM.equitiesControls.style.display = 'none';
    } else {
      DOM.modeEquities.classList.add('active');
      DOM.modeForex.classList.remove('active');
      DOM.equitiesControls.style.display = 'block';
      DOM.forexControls.style.display = 'none';
    }
  }

  // ---- Config & Auth ----
  async function loadConfig() {
    try {
      const res = await fetch('/api/config');
      configData = await res.json();
      populateProviders();
      populateSettingsView();
      if (configData.auth_required && DOM.apiKeyGroup) {
        DOM.apiKeyGroup.style.display = 'block';
        // Security hardening: purge any legacy stored secrets from web storage
        try {
          localStorage.removeItem('tradingagents_api_key');
          sessionStorage.removeItem('tradingagents_api_key');
        } catch (_) {}
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

    if (configData.quick_model) {
      const match = [...DOM.quickModel.options].find(o => o.value === configData.quick_model);
      if (match) match.selected = true;
    }
    if (configData.deep_model) {
      const match = [...DOM.deepModel.options].find(o => o.value === configData.deep_model);
      if (match) match.selected = true;
    }
  }

  // ---- Price & Precision Helpers ----
  function formatForexPrice(price, symbol, digits) {
    if (price == null || price === '' || isNaN(Number(price))) return '-';
    const num = Number(price);
    const d = digits != null ? digits : (symbol && String(symbol).toUpperCase().includes('JPY') ? 3 : 5);
    return num.toFixed(d);
  }

  function showMT5Unavailable() {
    const unav = 'Unavailable';
    if (DOM.mt5Balance) DOM.mt5Balance.textContent = unav;
    if (DOM.dashBalance) DOM.dashBalance.textContent = unav;
    if (DOM.mt5Equity) DOM.mt5Equity.textContent = unav;
    if (DOM.dashEquity) DOM.dashEquity.textContent = unav;
    if (DOM.mt5Floating) {
      DOM.mt5Floating.textContent = unav;
      DOM.mt5Floating.style.color = 'var(--text-muted)';
    }
    if (DOM.dashFloating) {
      DOM.dashFloating.textContent = unav;
      DOM.dashFloating.style.color = 'var(--text-muted)';
    }
    if (DOM.mt5FreeMargin) DOM.mt5FreeMargin.textContent = unav;
    if (DOM.dashFreeMargin) DOM.dashFreeMargin.textContent = unav;
    if (DOM.dashCurrency) DOM.dashCurrency.textContent = 'Standby / Unconnected';
  }

  // ---- MetaTrader 5 Passive Observer ----
  async function loadMT5Status() {
    try {
      const res = await fetch('/api/forex/mt5/status');
      if (res.ok) {
        const data = await res.json();
        const connected = data.is_connected === true || data.connected === true || data.status === 'CONNECTED';
        const login = data.login || data.account_login;
        DOM.mt5Dot.style.background = connected ? 'var(--green)' : 'var(--amber)';
        DOM.mt5Status.textContent = connected
          ? `MT5: Observed (${login ? '#' + login : 'Live'})`
          : 'MT5: Read-Only Observer';
        if (!connected) {
          showMT5Unavailable();
        }
      } else {
        DOM.mt5Dot.style.background = 'var(--amber)';
        DOM.mt5Status.textContent = 'MT5: Read-Only Observer';
        showMT5Unavailable();
      }
    } catch (_) {
      DOM.mt5Dot.style.background = 'var(--amber)';
      DOM.mt5Status.textContent = 'MT5: Read-Only Observer';
      showMT5Unavailable();
    }
  }

  async function loadMT5Account() {
    try {
      const res = await fetch('/api/forex/mt5/account');
      if (res.ok) {
        const data = await res.json();
        const acc = data.account || data;
        const balStr = (acc && acc.balance != null)
          ? `$${Number(acc.balance).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
          : 'Unavailable';
        const eqStr = (acc && acc.equity != null)
          ? `$${Number(acc.equity).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
          : 'Unavailable';
        const freeStr = (acc && acc.margin_free != null)
          ? `$${Number(acc.margin_free).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
          : 'Unavailable';

        if (DOM.mt5Balance) DOM.mt5Balance.textContent = balStr;
        if (DOM.dashBalance) DOM.dashBalance.textContent = balStr;
        if (DOM.mt5Equity) DOM.mt5Equity.textContent = eqStr;
        if (DOM.dashEquity) DOM.dashEquity.textContent = eqStr;
        if (DOM.mt5FreeMargin) DOM.mt5FreeMargin.textContent = freeStr;
        if (DOM.dashFreeMargin) DOM.dashFreeMargin.textContent = freeStr;

        if (acc && acc.currency && DOM.dashCurrency) {
          DOM.dashCurrency.textContent = `Currency: ${acc.currency}`;
        }
        if (acc && acc.leverage && DOM.dashLeverage) {
          DOM.dashLeverage.textContent = `Leverage 1:${acc.leverage}`;
        }

        if (acc && acc.profit != null) {
          const p = Number(acc.profit);
          const pStr = `${p >= 0 ? '+' : ''}$${p.toFixed(2)}`;
          const pCol = p >= 0 ? 'var(--green)' : 'var(--red)';
          if (DOM.mt5Floating) {
            DOM.mt5Floating.textContent = pStr;
            DOM.mt5Floating.style.color = pCol;
          }
          if (DOM.dashFloating) {
            DOM.dashFloating.textContent = pStr;
            DOM.dashFloating.style.color = pCol;
          }
        } else {
          if (DOM.mt5Floating) {
            DOM.mt5Floating.textContent = 'Unavailable';
            DOM.mt5Floating.style.color = 'var(--text-muted)';
          }
          if (DOM.dashFloating) {
            DOM.dashFloating.textContent = 'Unavailable';
            DOM.dashFloating.style.color = 'var(--text-muted)';
          }
        }
      } else {
        showMT5Unavailable();
      }
    } catch (_) {
      showMT5Unavailable();
    }
  }
      showMT5Unavailable();
    }
  }

  async function loadMT5Positions() {
    try {
      const res = await fetch('/api/forex/mt5/positions');
      if (!res.ok) return;
      const data = await res.json();
      const positions = data.positions || [];

      if (positions.length === 0) {
        DOM.mt5PositionsContainer.innerHTML = `
          <div class="empty-state" style="padding: 2rem;">
            <div class="empty-state-icon">📡</div>
            <div class="empty-state-title">No Active MT5 Positions</div>
            <div class="empty-state-desc">Manual trades opened on your MT5 terminal appear here automatically.</div>
          </div>
        `;
        return;
      }

      DOM.mt5PositionsContainer.innerHTML = `
        <table class="data-table">
          <thead>
            <tr>
              <th>Ticket</th>
              <th>Symbol</th>
              <th>Type</th>
              <th>Volume</th>
              <th>Open Price</th>
              <th>Stop Loss</th>
              <th>Take Profit</th>
              <th>Current Price</th>
              <th>Profit</th>
            </tr>
          </thead>
          <tbody>
            ${positions.map(p => {
              const pnl = Number(p.profit || 0);
              const pnlColor = pnl >= 0 ? 'var(--green)' : 'var(--red)';
              const typeStr = String(p.type || '').toUpperCase();
              const isBuy = typeStr === 'LONG' || typeStr === 'BUY' || p.type === 0;
              const dirLabel = isBuy ? 'BUY' : 'SELL';
              const badgeClass = isBuy ? 'bullish' : 'bearish';
              return `
                <tr>
                  <td style="font-family:var(--font-mono); font-size:0.8rem;">#${escapeText(p.ticket)}</td>
                  <td style="font-weight:700; color:var(--cyan);">${escapeText(p.symbol)}</td>
                  <td><span class="signal-badge ${badgeClass}" style="font-size:0.7rem; padding:2px 8px;">${dirLabel}</span></td>
                  <td style="font-family:var(--font-mono);">${Number(p.volume).toFixed(2)}</td>
                  <td style="font-family:var(--font-mono);">${formatForexPrice(p.price_open, p.symbol, p.digits)}</td>
                  <td style="font-family:var(--font-mono);">${formatForexPrice(p.sl, p.symbol, p.digits)}</td>
                  <td style="font-family:var(--font-mono);">${formatForexPrice(p.tp, p.symbol, p.digits)}</td>
                  <td style="font-family:var(--font-mono);">${formatForexPrice(p.price_current, p.symbol, p.digits)}</td>
                  <td style="font-family:var(--font-mono); font-weight:700; color:${pnlColor};">${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)}</td>
                </tr>
              `;
            }).join('')}
          </tbody>
        </table>
      `;
    } catch (_) {}
  }

  // ---- Journal & Learning Data ----
  async function loadJournalTrades() {
    try {
      const res = await fetch('/api/forex/journal/trades?limit=50');
      if (!res.ok) return;
      const data = await res.json();
      const trades = data.trades || [];

      if (trades.length === 0) {
        DOM.journalTableContainer.innerHTML = `
          <div class="empty-state" style="padding: 2rem;">
            <div class="empty-state-icon">📖</div>
            <div class="empty-state-title">Journal Empty</div>
            <div class="empty-state-desc">Proposals and executed trades will be recorded here with audit timeline trails.</div>
          </div>
        `;
        return;
      }

      DOM.journalTableContainer.innerHTML = `
        <table class="data-table">
          <thead>
            <tr>
              <th>Trade ID</th>
              <th>Pair</th>
              <th>Action</th>
              <th>Status</th>
              <th>Open Price</th>
              <th>Close Price</th>
              <th>Pips Gained</th>
              <th>R-Multiple</th>
              <th>Exit Reason</th>
              <th>Reflection Tags</th>
            </tr>
          </thead>
          <tbody>
            ${trades.map(t => {
              const pips = t.pips_gained != null ? Number(t.pips_gained).toFixed(1) : '-';
              const r = t.r_multiple != null ? `${Number(t.r_multiple).toFixed(2)}R` : '-';
              const isProfit = (t.pips_gained || 0) >= 0;
              const pipsColor = isProfit ? 'var(--green)' : 'var(--red)';
              const tags = (t.tags || []).map(tg => `<span class="lesson-tag" style="margin-right:4px;">${escapeText(tg)}</span>`).join('');

              return `
                <tr>
                  <td style="font-family:var(--font-mono); font-size:0.75rem; color:var(--text-muted);">${escapeText(t.trade_id)}</td>
                  <td style="font-weight:700; color:var(--cyan);">${escapeText(t.pair)}</td>
                  <td><span class="signal-badge ${t.action === 'LONG' ? 'bullish' : 'bearish'}" style="font-size:0.7rem; padding:2px 8px;">${escapeText(t.action)}</span></td>
                  <td><span class="status-cell ${escapeText(t.status)}">${escapeText(t.status)}</span></td>
                  <td style="font-family:var(--font-mono);">${Number(t.open_price).toFixed(5)}</td>
                  <td style="font-family:var(--font-mono);">${t.close_price ? Number(t.close_price).toFixed(5) : '-'}</td>
                  <td style="font-family:var(--font-mono); font-weight:700; color:${pipsColor};">${pips}</td>
                  <td style="font-family:var(--font-mono); font-weight:700; color:${pipsColor};">${r}</td>
                  <td style="font-size:0.8rem;">${escapeText(t.exit_reason || '-')}</td>
                  <td>${tags || '-'}</td>
                </tr>
              `;
            }).join('')}
          </tbody>
        </table>
      `;
    } catch (_) {}
  }

  async function loadLessons(pairFilter = '') {
    try {
      let url = '/api/forex/learning/lessons';
      if (pairFilter) url += `?pair=${encodeURIComponent(pairFilter)}`;
      const res = await fetch(url);
      if (!res.ok) return;
      const data = await res.json();
      const lessons = data.lessons || [];

      if (lessons.length === 0) {
        DOM.lessonsContainer.innerHTML = `
          <div class="empty-state">
            <div class="empty-state-icon">🧠</div>
            <div class="empty-state-title">No Stored Lessons</div>
            <div class="empty-state-desc">Post-trade reflections automatically extract prescriptive rules and store them here for future prompt injection.</div>
          </div>
        `;
        return;
      }

      DOM.lessonsContainer.innerHTML = `
        <div class="lesson-grid">
          ${lessons.map(l => `
            <div class="lesson-card">
              <div class="lesson-header">
                <span class="lesson-pair">${escapeText(l.pair || 'GLOBAL')}</span>
                <span class="lesson-tag">${escapeText(l.outcome_category || 'GENERAL')}</span>
              </div>
              <div class="lesson-rule">${escapeText(l.rule_violated || l.observation || 'Operational Rule')}</div>
              <div class="lesson-action">
                <strong>Directive:</strong> ${escapeText(l.actionable_rule || l.observation || 'Follow established parameters.')}
              </div>
              <div style="font-size:0.75rem; color:var(--text-muted); display:flex; justify-content:space-between;">
                <span>Setup: ${escapeText(l.setup_type || 'ALL')}</span>
                <span>Confidence: ${(Number(l.confidence_score || 0.9) * 100).toFixed(0)}%</span>
              </div>
            </div>
          `).join('')}
        </div>
      `;
    } catch (_) {}
  }

  async function loadAnalyticsSummary() {
    try {
      const res = await fetch('/api/forex/analytics/dashboard');
      if (!res.ok) return;
      const data = await res.json();
      const m = data.metrics || {};

      if (m.win_rate != null) {
        DOM.statWinRate.textContent = `${(Number(m.win_rate) * 100).toFixed(1)}%`;
      }
      if (m.total_trades != null) {
        DOM.statTotalTrades.textContent = `${m.total_trades} closed trades`;
      }
      if (m.profit_factor != null) {
        DOM.statProfitFactor.textContent = Number(m.profit_factor).toFixed(2);
      }
      if (m.total_r_multiple != null) {
        DOM.statTotalR.textContent = `${Number(m.total_r_multiple).toFixed(1)}R`;
      }
      if (m.max_drawdown_pct != null) {
        DOM.statMaxDD.textContent = `${Number(m.max_drawdown_pct).toFixed(1)}%`;
      }
    } catch (_) {}
  }

  // ---- Subsystem Loaders for Reorganized Dashboard ----
  async function loadDashboardOverview() {
    await Promise.allSettled([
      loadMT5Account(),
      loadDashboardPositions(),
      loadDashboardProposals(),
      loadDashboardAnalytics(),
      loadDashboardLessons(),
    ]);
  }

  async function loadDashboardPositions() {
    if (!DOM.dashPositionsContainer) return;
    try {
      const res = await fetch('/api/forex/mt5/positions');
      if (!res.ok) return;
      const data = await res.json();
      const positions = data.positions || [];
      if (positions.length === 0) {
        DOM.dashPositionsContainer.innerHTML = `
          <div class="empty-state" style="padding: 2rem;">
            <div class="empty-state-icon">📡</div>
            <div class="empty-state-title">No Active Positions</div>
            <div class="empty-state-desc">Trades executed manually in MT5 appear here automatically.</div>
          </div>
        `;
        return;
      }
      DOM.dashPositionsContainer.innerHTML = `
        <table class="data-table">
          <thead>
            <tr>
              <th>Ticket</th>
              <th>Symbol</th>
              <th>Type</th>
              <th>Volume</th>
              <th>Current Price</th>
              <th>Profit</th>
            </tr>
          </thead>
          <tbody>
            ${positions.map(p => {
              const pnl = Number(p.profit || 0);
              const pnlColor = pnl >= 0 ? 'var(--green)' : 'var(--red)';
              const typeStr = String(p.type || '').toUpperCase();
              const isBuy = typeStr === 'LONG' || typeStr === 'BUY' || p.type === 0;
              return `
                <tr>
                  <td style="font-family:var(--font-mono); font-size:0.8rem;">#${escapeText(p.ticket)}</td>
                  <td style="font-weight:700; color:var(--cyan);">${escapeText(p.symbol)}</td>
                  <td><span class="signal-badge ${isBuy ? 'bullish' : 'bearish'}" style="font-size:0.7rem; padding:2px 8px;">${isBuy ? 'BUY' : 'SELL'}</span></td>
                  <td style="font-family:var(--font-mono);">${Number(p.volume).toFixed(2)}</td>
                  <td style="font-family:var(--font-mono);">${formatForexPrice(p.price_current, p.symbol, p.digits)}</td>
                  <td style="font-family:var(--font-mono); font-weight:700; color:${pnlColor};">${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)}</td>
                </tr>
              `;
            }).join('')}
          </tbody>
        </table>
      `;
    } catch (_) {}
  }

  async function loadDashboardProposals() {
    if (!DOM.dashProposalsContainer) return;
    try {
      const res = await fetch('/api/forex/proposals?limit=5');
      if (!res.ok) return;
      const data = await res.json();
      const proposals = data.proposals || [];
      if (proposals.length === 0) {
        DOM.dashProposalsContainer.innerHTML = `
          <div class="empty-state" style="padding: 2rem;">
            <div class="empty-state-icon">📋</div>
            <div class="empty-state-title">No Recent Proposals</div>
            <div class="empty-state-desc">Proposals generated through the multi-agent pipeline will be logged here.</div>
          </div>
        `;
        return;
      }
      DOM.dashProposalsContainer.innerHTML = `
        <table class="data-table">
          <thead>
            <tr>
              <th>ID</th>
              <th>Pair</th>
              <th>Action</th>
              <th>Entry</th>
              <th>SL</th>
              <th>TP</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            ${proposals.map(p => `
              <tr>
                <td style="font-family:var(--font-mono); font-size:0.75rem; color:var(--text-muted);">${escapeText(p.proposal_id.slice(0, 8))}</td>
                <td style="font-weight:700; color:var(--cyan);">${escapeText(p.pair)}</td>
                <td><span class="signal-badge ${p.action === 'LONG' ? 'bullish' : 'bearish'}" style="font-size:0.7rem; padding:2px 8px;">${escapeText(p.action)}</span></td>
                <td style="font-family:var(--font-mono);">${Number(p.entry_price).toFixed(5)}</td>
                <td style="font-family:var(--font-mono);">${Number(p.stop_loss).toFixed(5)}</td>
                <td style="font-family:var(--font-mono);">${Number(p.take_profit_1).toFixed(5)}</td>
                <td><span class="status-cell ${escapeText(p.status)}">${escapeText(p.status)}</span></td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      `;
    } catch (_) {}
  }

  async function loadDashboardAnalytics() {
    try {
      const res = await fetch('/api/forex/analytics/dashboard');
      if (!res.ok) return;
      const data = await res.json();
      const m = data.metrics || {};
      if (m.win_rate != null && DOM.dashWinRate) DOM.dashWinRate.textContent = `${(Number(m.win_rate) * 100).toFixed(1)}%`;
      if (m.total_trades != null && DOM.dashClosedTrades) DOM.dashClosedTrades.textContent = `${m.total_trades} trades`;
      if (m.profit_factor != null && DOM.dashProfitFactor) DOM.dashProfitFactor.textContent = Number(m.profit_factor).toFixed(2);
      if (m.total_r_multiple != null && DOM.dashTotalR) DOM.dashTotalR.textContent = `${Number(m.total_r_multiple).toFixed(1)}R`;
      if (m.max_drawdown_pct != null && DOM.dashMaxDD) DOM.dashMaxDD.textContent = `${Number(m.max_drawdown_pct).toFixed(1)}%`;
    } catch (_) {}
  }

  async function loadDashboardLessons() {
    if (!DOM.dashLessonsContainer) return;
    try {
      const res = await fetch('/api/forex/learning/lessons');
      if (!res.ok) return;
      const data = await res.json();
      const lessons = (data.lessons || []).slice(0, 2);
      if (lessons.length === 0) {
        DOM.dashLessonsContainer.innerHTML = `
          <div class="empty-state" style="padding: 2rem;">
            <div class="empty-state-icon">🧠</div>
            <div class="empty-state-title">No Stored Lessons</div>
            <div class="empty-state-desc">Post-trade reflections extract prescriptive rules and store them here.</div>
          </div>
        `;
        return;
      }
      DOM.dashLessonsContainer.innerHTML = lessons.map(l => `
        <div class="lesson-card" style="margin-bottom: 0.75rem;">
          <div class="lesson-header">
            <span class="lesson-pair">${escapeText(l.pair || 'GLOBAL')}</span>
            <span class="lesson-tag">${escapeText(l.outcome_category || 'GENERAL')}</span>
          </div>
          <div class="lesson-rule" style="font-size:0.8rem;">${escapeText(l.rule_violated || l.observation || 'Operational Rule')}</div>
          <div class="lesson-action" style="padding:6px 10px; font-size:0.75rem;">
            ${escapeText(l.actionable_rule || l.observation || 'Follow risk guidelines.')}
          </div>
        </div>
      `).join('');
    } catch (_) {}
  }

  // ---- Proposals View ----
  async function loadProposals() {
    if (!DOM.proposalsTableContainer) return;
    try {
      let url = '/api/forex/proposals?limit=100';
      const pair = DOM.proposalsPairFilter ? DOM.proposalsPairFilter.value : '';
      const status = DOM.proposalsStatusFilter ? DOM.proposalsStatusFilter.value : '';
      if (pair) url += `&pair=${encodeURIComponent(pair)}`;
      if (status) url += `&status=${encodeURIComponent(status)}`;

      const res = await fetch(url);
      if (!res.ok) return;
      const data = await res.json();
      const props = data.proposals || [];

      if (props.length === 0) {
        DOM.proposalsTableContainer.innerHTML = `
          <div class="empty-state" style="padding: 2.5rem;">
            <div class="empty-state-icon">📋</div>
            <div class="empty-state-title">No Proposals Found</div>
            <div class="empty-state-desc">No proposals match the current filter criteria.</div>
          </div>
        `;
        return;
      }

      DOM.proposalsTableContainer.innerHTML = `
        <table class="data-table">
          <thead>
            <tr>
              <th>Proposal ID</th>
              <th>Pair</th>
              <th>Action</th>
              <th>Timeframe</th>
              <th>Setup</th>
              <th>Entry</th>
              <th>SL</th>
              <th>TP1</th>
              <th>RR</th>
              <th>Risk %</th>
              <th>Lots</th>
              <th>Status</th>
              <th>Created</th>
            </tr>
          </thead>
          <tbody>
            ${props.map(p => `
              <tr>
                <td style="font-family:var(--font-mono); font-size:0.75rem; color:var(--text-muted);">${escapeText(p.proposal_id)}</td>
                <td style="font-weight:700; color:var(--cyan);">${escapeText(p.pair)}</td>
                <td><span class="signal-badge ${p.action === 'LONG' ? 'bullish' : 'bearish'}" style="font-size:0.7rem; padding:2px 8px;">${escapeText(p.action)}</span></td>
                <td>${escapeText(p.timeframe || '-')}</td>
                <td style="font-size:0.75rem;">${escapeText(p.setup_type || '-')}</td>
                <td style="font-family:var(--font-mono);">${Number(p.entry_price).toFixed(5)}</td>
                <td style="font-family:var(--font-mono);">${Number(p.stop_loss).toFixed(5)}</td>
                <td style="font-family:var(--font-mono);">${Number(p.take_profit_1).toFixed(5)}</td>
                <td style="font-family:var(--font-mono);">${Number(p.risk_reward_ratio || 0).toFixed(2)}</td>
                <td style="font-family:var(--font-mono);">${Number(p.risk_percent || 1).toFixed(1)}%</td>
                <td style="font-family:var(--font-mono); font-weight:700;">${p.recommended_lot_size != null ? Number(p.recommended_lot_size).toFixed(2) : '-'}</td>
                <td><span class="status-cell ${escapeText(p.status)}">${escapeText(p.status)}</span></td>
                <td class="date-cell">${escapeText((p.created_at || '').slice(0, 19).replace('T', ' '))}</td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      `;
    } catch (_) {}
  }

  // ---- MT5 Orders & Deals ----
  async function loadMT5Orders() {
    if (!DOM.mt5OrdersContainer) return;
    try {
      const res = await fetch('/api/forex/mt5/orders');
      if (!res.ok) return;
      const data = await res.json();
      const orders = data.orders || [];
      if (orders.length === 0) {
        DOM.mt5OrdersContainer.innerHTML = `
          <div class="empty-state" style="padding: 2rem;">
            <div class="empty-state-icon">⏳</div>
            <div class="empty-state-title">No Pending Orders</div>
            <div class="empty-state-desc">Limit and stop orders on MT5 appear here.</div>
          </div>
        `;
        return;
      }
      DOM.mt5OrdersContainer.innerHTML = `
        <table class="data-table">
          <thead>
            <tr>
              <th>Ticket</th>
              <th>Symbol</th>
              <th>Type</th>
              <th>Volume</th>
              <th>Price</th>
              <th>SL</th>
              <th>TP</th>
            </tr>
          </thead>
          <tbody>
            ${orders.map(o => `
              <tr>
                <td style="font-family:var(--font-mono); font-size:0.8rem;">#${escapeText(o.ticket)}</td>
                <td style="font-weight:700; color:var(--cyan);">${escapeText(o.symbol)}</td>
                <td>${escapeText(o.type_str || o.type)}</td>
                <td style="font-family:var(--font-mono);">${Number(o.volume_initial || o.volume_current || 0).toFixed(2)}</td>
                <td style="font-family:var(--font-mono);">${formatForexPrice(o.price_open, o.symbol)}</td>
                <td style="font-family:var(--font-mono);">${formatForexPrice(o.sl, o.symbol)}</td>
                <td style="font-family:var(--font-mono);">${formatForexPrice(o.tp, o.symbol)}</td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      `;
    } catch (_) {}
  }

  async function loadMT5Deals() {
    if (!DOM.mt5DealsContainer) return;
    try {
      const res = await fetch('/api/forex/mt5/deals');
      if (!res.ok) return;
      const data = await res.json();
      const deals = data.deals || [];
      if (deals.length === 0) {
        DOM.mt5DealsContainer.innerHTML = `
          <div class="empty-state" style="padding: 2rem;">
            <div class="empty-state-icon">📜</div>
            <div class="empty-state-title">No Recent Deals</div>
            <div class="empty-state-desc">Closed deal transactions from MT5 history appear here.</div>
          </div>
        `;
        return;
      }
      DOM.mt5DealsContainer.innerHTML = `
        <table class="data-table">
          <thead>
            <tr>
              <th>Deal ID</th>
              <th>Symbol</th>
              <th>Type</th>
              <th>Volume</th>
              <th>Price</th>
              <th>Profit</th>
            </tr>
          </thead>
          <tbody>
            ${deals.slice(0, 10).map(d => {
              const pnl = Number(d.profit || 0);
              const pnlColor = pnl >= 0 ? 'var(--green)' : 'var(--red)';
              return `
                <tr>
                  <td style="font-family:var(--font-mono); font-size:0.8rem;">#${escapeText(d.deal_id || d.ticket)}</td>
                  <td style="font-weight:700; color:var(--cyan);">${escapeText(d.symbol)}</td>
                  <td>${escapeText(d.type_str || d.type)}</td>
                  <td style="font-family:var(--font-mono);">${Number(d.volume || 0).toFixed(2)}</td>
                  <td style="font-family:var(--font-mono);">${formatForexPrice(d.price, d.symbol)}</td>
                  <td style="font-family:var(--font-mono); font-weight:700; color:${pnlColor};">${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)}</td>
                </tr>
              `;
            }).join('')}
          </tbody>
        </table>
      `;
    } catch (_) {}
  }

  // ---- Performance Breakdown View ----
  async function loadPerformanceBreakdown() {
    if (!DOM.perfBreakdownContainer) return;
    try {
      const res = await fetch('/api/forex/journal/performance');
      if (!res.ok) return;
      const data = await res.json();
      const byPair = data.performance?.by_pair || {};
      const pairs = Object.keys(byPair);
      if (pairs.length === 0) {
        DOM.perfBreakdownContainer.innerHTML = `
          <div class="empty-state" style="padding: 2.5rem;">
            <div class="empty-state-icon">📊</div>
            <div class="empty-state-title">No Performance Breakdown Yet</div>
            <div class="empty-state-desc">Detailed metrics across pairs, timeframes, and setups will calculate automatically as trades are closed.</div>
          </div>
        `;
        return;
      }

      DOM.perfBreakdownContainer.innerHTML = `
        <table class="data-table">
          <thead>
            <tr>
              <th>Currency Pair</th>
              <th>Trades</th>
              <th>Win Rate</th>
              <th>Profit Factor</th>
              <th>Total R</th>
              <th>Net P&amp;L</th>
              <th>Avg R</th>
            </tr>
          </thead>
          <tbody>
            ${pairs.map(p => {
              const m = byPair[p] || {};
              const wr = m.win_rate != null ? `${(Number(m.win_rate) * 100).toFixed(1)}%` : '-';
              const pf = m.profit_factor != null ? Number(m.profit_factor).toFixed(2) : '-';
              const r = m.total_r_multiple != null ? `${Number(m.total_r_multiple).toFixed(2)}R` : '-';
              const pnl = Number(m.net_pnl || m.gross_profit || 0);
              const pnlColor = pnl >= 0 ? 'var(--green)' : 'var(--red)';
              const avgR = m.average_r != null ? `${Number(m.average_r).toFixed(2)}R` : '-';
              return `
                <tr>
                  <td style="font-weight:700; color:var(--cyan);">${escapeText(p)}</td>
                  <td>${escapeText(m.total_trades || 0)}</td>
                  <td style="color:var(--green); font-weight:600;">${wr}</td>
                  <td>${pf}</td>
                  <td style="color:var(--cyan); font-weight:600;">${r}</td>
                  <td style="font-weight:700; color:${pnlColor};">${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)}</td>
                  <td>${avgR}</td>
                </tr>
              `;
            }).join('')}
          </tbody>
        </table>
      `;
    } catch (_) {}
  }

  // ---- Backtest Runs View ----
  async function loadBacktestRuns() {
    if (!DOM.backtestRunsContainer) return;
    try {
      const res = await fetch('/api/forex/backtest/runs');
      if (!res.ok) return;
      const data = await res.json();
      const runs = data.runs || [];
      if (runs.length === 0) {
        DOM.backtestRunsContainer.innerHTML = `
          <div class="empty-state" style="padding: 2.5rem;">
            <div class="empty-state-icon">🧪</div>
            <div class="empty-state-title">No Backtests Run Yet</div>
            <div class="empty-state-desc">Historical agent backtests and walk-forward evaluations appear here.</div>
          </div>
        `;
        return;
      }
      DOM.backtestRunsContainer.innerHTML = `
        <table class="data-table">
          <thead>
            <tr>
              <th>Backtest ID</th>
              <th>Mode</th>
              <th>Pair</th>
              <th>Timeframe</th>
              <th>Trades</th>
              <th>Win Rate</th>
              <th>Status</th>
              <th>Created</th>
            </tr>
          </thead>
          <tbody>
            ${runs.map(r => `
              <tr>
                <td style="font-family:var(--font-mono); font-size:0.75rem; color:var(--text-muted);">${escapeText(r.backtest_id ? r.backtest_id.slice(0, 10) : '-')}</td>
                <td><span class="lesson-tag" style="font-size:0.65rem;">${escapeText(r.mode || 'HISTORICAL')}</span></td>
                <td style="font-weight:700; color:var(--cyan);">${escapeText(r.pair)}</td>
                <td>${escapeText(r.timeframe || '-')}</td>
                <td>${escapeText(r.trade_count || r.trades_count || 0)}</td>
                <td style="color:var(--green); font-weight:600;">${r.win_rate != null ? (Number(r.win_rate) * 100).toFixed(1) + '%' : '-'}</td>
                <td><span class="status-cell ${escapeText(r.status || 'completed')}">${escapeText(r.status || 'completed')}</span></td>
                <td class="date-cell">${escapeText((r.created_at || '').slice(0, 19).replace('T', ' '))}</td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      `;
    } catch (_) {}
  }

  // ---- Settings View ----
  function populateSettingsView() {
    if (!configData) return;
    if (DOM.settingProvider) DOM.settingProvider.textContent = configData.provider || 'Not configured';
    if (DOM.settingQuickModel) DOM.settingQuickModel.textContent = configData.quick_model || 'Default';
    if (DOM.settingDeepModel) DOM.settingDeepModel.textContent = configData.deep_model || 'Default';
  }

  // ---- Submit Analysis ----
  async function handleSubmit(e) {
    e.preventDefault();

    if (currentAssetMode === 'forex') {
      await handleForexSubmit();
    } else {
      await handleEquitiesSubmit();
    }
  }

  // Forex Launch Flow
  async function handleForexSubmit() {
    const pair = DOM.forexPair.value || 'EURUSD';
    const timeframe = DOM.forexTimeframe.value || 'H1';
    const balance = parseFloat(DOM.accountBalance.value) || 100000;
    const riskPct = parseFloat(DOM.riskPercent.value) || 1.0;

    const selectedAnalysts = [];
    DOM.forexAnalystToggles.forEach(btn => {
      if (btn.classList.contains('active')) {
        const val = btn.dataset.fxanalyst;
        if (val === 'technical' || val === 'forex_technical') selectedAnalysts.push('forex_technical');
        else if (val === 'macro' || val === 'forex_macro') selectedAnalysts.push('forex_macro');
        else if (val === 'news' || val === 'forex_news') selectedAnalysts.push('forex_news');
      }
    });

    if (selectedAnalysts.length === 0) {
      showToast('At least one Forex analyst (Technical, Macro, or News) must be selected', 'error');
      return;
    }

    const payload = {
      pair: pair,
      execution_timeframe: timeframe,
      timeframe: timeframe,
      account_balance: balance,
      risk_percent: riskPct,
      analysts: selectedAnalysts,
      provider: DOM.provider.value || null,
      quick_model: DOM.quickModel.value || null,
      deep_model: DOM.deepModel.value || null,
    };

    DOM.btnRun.disabled = true;
    DOM.btnRun.innerHTML = `
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="animation: spin 1s linear infinite;">
        <circle cx="12" cy="12" r="10" stroke-dasharray="31" stroke-dashoffset="10"/>
      </svg>
      Analyzing Forex...
    `;

    const headers = { 'Content-Type': 'application/json' };
    const savedApiKey = inMemoryApiKey || (DOM.apiKey ? DOM.apiKey.value.trim() : '');
    if (savedApiKey) headers['X-API-Key'] = savedApiKey;

    try {
      const res = await fetch('/api/forex/analyze', {
        method: 'POST',
        headers: headers,
        body: JSON.stringify(payload),
      });

      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || 'Failed to start Forex analysis');
      }

      currentRunId = data.run_id;
      activePipelineType = 'forex';
      showPipeline(pair, `${timeframe} • ${riskPct}% Risk`, FOREX_PIPELINE_NODES);
      connectForexSSE(data.run_id, pair);
      showToast(`Forex analysis started for ${pair}`, 'success');

    } catch (err) {
      showToast(err.message, 'error');
      resetRunButton();
    }
  }

  // Equities Launch Flow (Existing Functionality Preserved)
  async function handleEquitiesSubmit() {
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
      Analyzing Equities...
    `;

    const headers = { 'Content-Type': 'application/json' };
    const savedApiKey = inMemoryApiKey || (DOM.apiKey ? DOM.apiKey.value.trim() : '');
    if (savedApiKey) headers['X-API-Key'] = savedApiKey;

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
      activePipelineType = 'equities';
      showPipeline(payload.ticker, payload.date || 'today', EQUITIES_PIPELINE_NODES);
      connectEquitiesSSE(data.run_id);
      showToast(`Analysis started for ${payload.ticker}`, 'success');

    } catch (err) {
      showToast(err.message, 'error');
      resetRunButton();
    }
  }

  // ---- Pipeline Rendering ----
  function showPipeline(symbol, subtitle, nodes) {
    completedNodes.clear();
    DOM.pipelineContent.innerHTML = `
      <div class="pipeline-container">
        <div class="pipeline-header">
          <span class="pipeline-title">Analyzing <strong style="color:var(--cyan)">${escapeText(symbol)}</strong> — ${escapeText(subtitle)}</span>
          <span class="pipeline-percent" id="progressPercent">0%</span>
        </div>
        <div class="progress-bar-track">
          <div class="progress-bar-fill" id="progressBar"></div>
        </div>
        <div class="pipeline-nodes" id="pipelineNodes">
          ${nodes.map(n => `
            <div class="pipeline-node" id="node-${escapeText(n.id)}">
              <div class="node-indicator"></div>
              <span class="node-label">${n.icon} ${escapeText(n.label)}</span>
            </div>
          `).join('')}
        </div>
      </div>
    `;
  }

  let completedNodes = new Set();

  function updatePipelineStep(nodeId, progressVal) {
    $$('.pipeline-node.active').forEach(el => {
      el.classList.remove('active');
      el.classList.add('done');
    });

    completedNodes.add(nodeId);
    const el = document.getElementById(`node-${nodeId}`);
    if (el) {
      el.classList.add('active');
    }

    const nodesList = activePipelineType === 'forex' ? FOREX_PIPELINE_NODES : EQUITIES_PIPELINE_NODES;
    const progress = progressVal || (completedNodes.size / nodesList.length);
    const bar = document.getElementById('progressBar');
    const pct = document.getElementById('progressPercent');
    if (bar) bar.style.width = `${Math.min(100, Math.round(progress * 100))}%`;
    if (pct) pct.textContent = `${Math.min(100, Math.round(progress * 100))}%`;
  }

  // ---- SSE: Forex ----
  function connectForexSSE(runId, pair) {
    if (eventSource) {
      eventSource.close();
      eventSource = null;
    }

    eventSource = new EventSource(`/api/forex/runs/${encodeURIComponent(runId)}/events`);

    // Dynamic pipeline events
    FOREX_PIPELINE_NODES.forEach((node, idx) => {
      eventSource.addEventListener(node.id, (e) => {
        const progress = (idx + 1) / (FOREX_PIPELINE_NODES.length + 1);
        updatePipelineStep(node.id, progress);
        try {
          const data = JSON.parse(e.data);
          if (data.status) {
            DOM.btnRun.innerHTML = `
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="animation: spin 1s linear infinite;">
                <circle cx="12" cy="12" r="10" stroke-dasharray="31" stroke-dashoffset="10"/>
              </svg>
              ${escapeText(node.label)}...
            `;
          }
        } catch (_) {}
      });
    });

    eventSource.addEventListener('complete', async (e) => {
      if (eventSource) {
        eventSource.close();
        eventSource = null;
      }

      FOREX_PIPELINE_NODES.forEach(n => {
        const el = document.getElementById(`node-${n.id}`);
        if (el) { el.classList.remove('active'); el.classList.add('done'); }
      });
      const bar = document.getElementById('progressBar');
      const pct = document.getElementById('progressPercent');
      if (bar) bar.style.width = '100%';
      if (pct) pct.textContent = '100%';

      resetRunButton();
      showToast(`Forex analysis complete for ${pair}!`, 'success');

      await loadForexReport(runId);
      loadJournalTrades();
      loadLessons();
      loadAnalyticsSummary();
      loadRuns();
      loadHistory();
    });

    eventSource.addEventListener('error', (e) => {
      if (eventSource) {
        eventSource.close();
        eventSource = null;
      }
      resetRunButton();
      try {
        const data = JSON.parse(e.data);
        showToast(data.message || data.error || 'Forex analysis failed', 'error');
      } catch (_) {
        showToast('Forex analysis stream error', 'error');
      }
    });

    eventSource.onerror = () => {
      if (eventSource) {
        eventSource.close();
        eventSource = null;
      }
      resetRunButton();
    };
  }

  // ---- SSE: Equities ----
  function connectEquitiesSSE(runId) {
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

        EQUITIES_PIPELINE_NODES.forEach(n => {
          const el = document.getElementById(`node-${n.id}`);
          if (el) { el.classList.remove('active'); el.classList.add('done'); }
        });

        const bar = document.getElementById('progressBar');
        const pct = document.getElementById('progressPercent');
        if (bar) bar.style.width = '100%';
        if (pct) pct.textContent = '100%';

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
      if (eventSource) {
        eventSource.close();
        eventSource = null;
      }
      resetRunButton();
      try {
        const data = JSON.parse(e.data);
        showToast(data.error || 'Analysis failed', 'error');
      } catch (_) {}
    });

    eventSource.onerror = () => {
      if (eventSource) {
        eventSource.close();
        eventSource = null;
      }
      resetRunButton();
    };
  }

  function updatePipelineNode(data) {
    $$('.pipeline-node.active').forEach(el => {
      el.classList.remove('active');
      el.classList.add('done');
    });

    const matchId = findNodeId(data.node || data.label);
    if (matchId) {
      completedNodes.add(matchId);
      const el = document.getElementById(`node-${matchId}`);
      if (el) el.classList.add('active');
    }

    const progress = data.progress || (completedNodes.size / EQUITIES_PIPELINE_NODES.length);
    const bar = document.getElementById('progressBar');
    const pct = document.getElementById('progressPercent');
    if (bar) bar.style.width = `${Math.round(progress * 100)}%`;
    if (pct) pct.textContent = `${Math.round(progress * 100)}%`;
  }

  function findNodeId(hint) {
    if (!hint) return null;
    const lower = hint.toLowerCase().replace(/[^a-z_]/g, '');
    const direct = EQUITIES_PIPELINE_NODES.find(n => n.id === hint || n.id === lower);
    if (direct) return direct.id;
    const fuzzy = EQUITIES_PIPELINE_NODES.find(n =>
      lower.includes(n.id.replace(/_/g, '')) ||
      n.label.toLowerCase().replace(/\s/g, '').includes(lower)
    );
    return fuzzy ? fuzzy.id : null;
  }

  // ---- Report Loading & Rendering ----
  async function loadForexReport(runId) {
    try {
      const res = await fetch(`/api/forex/runs/${encodeURIComponent(runId)}`);
      if (!res.ok) {
        if (res.status === 404) {
          showToast('Forex run not found', 'error');
        } else {
          showToast('Failed to load Forex report', 'error');
        }
        return;
      }
      const data = await res.json();
      const reportPayload = data.report || {};
      renderForexReport(reportPayload, data.run);

      // Switch to report tab automatically
      const reportTab = [...DOM.tabs].find(t => t.dataset.view === 'report');
      if (reportTab) reportTab.click();
    } catch (_) {
      showToast('Error retrieving Forex report', 'error');
    }
  }

  function renderForexReport(data) {
    const prop = data.proposal || {};
    const risk = data.risk_decision || {};
    const reportText = data.report || '';

    const action = prop.action || 'NO_TRADE';
    const actionColor = action === 'LONG' ? 'var(--green)' : action === 'SHORT' ? 'var(--red)' : 'var(--amber)';
    const decisionAction = risk.decision || 'PENDING';
    const decisionColor = decisionAction === 'APPROVE' ? 'var(--green)' : decisionAction === 'REJECT' ? 'var(--red)' : 'var(--amber)';

    DOM.reportContent.innerHTML = `
      <div class="card" style="margin-bottom:1.5rem;">
        <div class="card-header" style="justify-content:space-between;">
          <h2 class="card-title">
            <span style="color:var(--cyan); font-weight:800;">${escapeText(data.pair || 'FOREX')}</span>
            <span class="signal-badge" style="background:${actionColor}20; color:${actionColor}; border:1px solid ${actionColor}; font-size:0.8rem; padding:4px 12px;">
              ${escapeText(action)}
            </span>
            <span class="signal-badge" style="background:${decisionColor}20; color:${decisionColor}; border:1px solid ${decisionColor}; font-size:0.8rem; padding:4px 12px;">
              Risk Engine: ${escapeText(decisionAction)}
            </span>
          </h2>
          <span style="font-size:0.8rem; color:var(--text-muted); font-family:var(--font-mono);">${escapeText(data.run_id)}</span>
        </div>

        <!-- Metric Summary Cards -->
        <div class="stat-cards">
          <div class="stat-card">
            <span class="stat-label">Entry Price</span>
            <span class="stat-value">${prop.entry_price ? Number(prop.entry_price).toFixed(5) : '-'}</span>
            <span class="stat-sub">Order: ${escapeText(prop.order_type || 'MARKET')}</span>
          </div>
          <div class="stat-card">
            <span class="stat-label">Stop Loss</span>
            <span class="stat-value" style="color:var(--red);">${prop.stop_loss ? Number(prop.stop_loss).toFixed(5) : '-'}</span>
            <span class="stat-sub">${prop.sl_pips ? `${prop.sl_pips} pips risk` : ''}</span>
          </div>
          <div class="stat-card">
            <span class="stat-label">Take Profit</span>
            <span class="stat-value" style="color:var(--green);">${prop.take_profit_1 ? Number(prop.take_profit_1).toFixed(5) : '-'}</span>
            <span class="stat-sub">${prop.tp_pips ? `${prop.tp_pips} pips target` : ''}</span>
          </div>
          <div class="stat-card">
            <span class="stat-label">Risk : Reward</span>
            <span class="stat-value" style="color:var(--cyan);">${prop.risk_reward_ratio ? `${Number(prop.risk_reward_ratio).toFixed(2)}:1` : '-'}</span>
            <span class="stat-sub">${risk.approved_lot_size ? `${risk.approved_lot_size} lots approved` : 'Standard Lot Sizing'}</span>
          </div>
        </div>

        ${prop.trade_rationale_summary ? `
          <div style="background:var(--bg-primary); padding:1rem; border-radius:var(--radius-md); border-left:3px solid var(--cyan); margin-bottom:1.5rem;">
            <strong>Executive Thesis:</strong> ${escapeText(prop.trade_rationale_summary)}
          </div>
        ` : ''}

        <!-- Full Markdown Synthesis -->
        <div class="report-content" style="margin-top:1.5rem;">
          ${escapeHtml(reportText)}
        </div>
      </div>
    `;
  }

  async function loadReport(runId) {
    try {
      const res = await fetch(`/api/runs/${encodeURIComponent(runId)}/report`);
      if (res.ok) {
        const report = await res.json();
        renderEquitiesReport(report);
      }
    } catch (_) {}
  }

  function renderEquitiesReport(report) {
    const signalText = escapeText(report.signal || 'REVIEW');
    const signalClass = (report.signal || '').toLowerCase().replace(/[^a-z]/g, '');

    DOM.reportContent.innerHTML = `
      <div class="card">
        <div class="card-header" style="justify-content:space-between;">
          <h2 class="card-title">
            <span style="color:var(--cyan); font-weight:800;">${escapeText(report.ticker)}</span>
            <span class="signal-badge ${signalClass}">${signalText}</span>
          </h2>
          <span style="font-size:0.8rem; color:var(--text-muted);">${escapeText(report.date || '')}</span>
        </div>
        <div class="report-content">
          ${escapeHtml(report.final_decision || 'No final decision available.')}
        </div>
      </div>
    `;
  }

  // ---- History & Runs ----
  let inMemoryRuns = [];
  let diskHistory = [];

  async function loadRuns() {
    try {
      const [eqRes, fxRes] = await Promise.all([
        fetch('/api/runs').catch(() => null),
        fetch('/api/forex/runs').catch(() => null),
      ]);
      const eqRuns = eqRes && eqRes.ok ? (await eqRes.json()).runs || [] : [];
      const fxRuns = fxRes && fxRes.ok ? (await fxRes.json()).runs || [] : [];
      eqRuns.forEach(r => { r.asset_type = r.asset_type || 'equities'; r.run_type = r.run_type || 'equities'; });
      fxRuns.forEach(r => { r.asset_type = 'forex'; r.run_type = 'forex'; });
      inMemoryRuns = [...fxRuns, ...eqRuns];
      renderCombinedHistory();
    } catch (_) {}
  }

  async function loadHistory() {
    try {
      const res = await fetch('/api/history');
      if (res.ok) {
        const data = await res.json();
        diskHistory = data.reports || [];
        renderCombinedHistory();
      }
    } catch (_) {}
  }

  function renderCombinedHistory() {
    const seenIds = new Set();
    const combined = [];

    inMemoryRuns.forEach(r => {
      seenIds.add(r.run_id);
      combined.push(r);
    });

    diskHistory.forEach(h => {
      const id = h.run_id || h.id;
      if (!seenIds.has(id)) {
        seenIds.add(id);
        const isForex = h.asset_type === 'forex' || h.run_type === 'forex' || Boolean(h.pair);
        combined.push({
          run_id: id,
          ticker: h.ticker || h.pair || 'ASSET',
          asset_type: isForex ? 'forex' : 'equities',
          run_type: isForex ? 'forex' : 'equities',
          date: h.date || h.timestamp || '',
          provider: h.provider || 'Saved',
          status: h.status || 'completed',
          signal: h.signal || null,
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
      <table class="data-table">
        <thead>
          <tr>
            <th>Asset</th>
            <th>Type</th>
            <th>Date</th>
            <th>Provider</th>
            <th>Status</th>
            <th>Signal</th>
            <th>Run ID</th>
          </tr>
        </thead>
        <tbody id="historyTableBody">
          ${combined.map(run => {
            const signalClass = (run.signal || 'unknown').toLowerCase().replace(/[^a-z]/g, '');
            const assetType = run.asset_type || run.run_type || (run.pair ? 'forex' : 'equities');
            const assetBadge = assetType === 'forex' ? 'FOREX' : 'EQUITY';
            return `
              <tr data-run-id="${escapeText(run.run_id)}" data-asset-type="${escapeText(assetType)}">
                <td style="font-weight:700; color:var(--cyan);">${escapeText(run.ticker || run.pair)}</td>
                <td><span class="lesson-tag" style="font-size:0.65rem;">${escapeText(assetBadge)}</span></td>
                <td>${escapeText(run.date)}</td>
                <td>${escapeText(run.provider || '-')}</td>
                <td><span class="status-cell ${escapeText(run.status)}">${escapeText(run.status)}</span></td>
                <td>${run.signal ? `<span class="signal-badge ${signalClass}" style="font-size:0.75rem; padding:2px 8px;">${escapeText(run.signal)}</span>` : '-'}</td>
                <td style="font-family:var(--font-mono); font-size:0.75rem; color:var(--text-muted);">${escapeText(run.run_id)}</td>
              </tr>
            `;
          }).join('')}
        </tbody>
      </table>
    `;

    const tbody = document.getElementById('historyTableBody');
    if (tbody) {
      tbody.querySelectorAll('tr').forEach(row => {
        row.addEventListener('click', () => {
          const runId = row.getAttribute('data-run-id');
          const assetType = row.getAttribute('data-asset-type');
          if (runId) viewReport(runId, assetType);
        });
      });
    }
  }

  async function viewReport(runId, assetType) {
    if (assetType === 'forex' || runId.startsWith('fx_') || runId.startsWith('fxrun_')) {
      await loadForexReport(runId);
      return;
    }
    try {
      const res = await fetch(`/api/runs/${encodeURIComponent(runId)}/report`);
      if (res.ok) {
        const report = await res.json();
        showReportModal(report);
      } else {
        showToast('Report not available for this run', 'error');
      }
    } catch (_) {
      showToast('Failed to load report', 'error');
    }
  }

  function showReportModal(report) {
    const signalText = escapeText(report.signal || 'REVIEW');
    const signalClass = (report.signal || '').toLowerCase().replace(/[^a-z]/g, '');
    DOM.reportModalTitle.innerHTML = `
      <span style="color:var(--cyan)">${escapeText(report.ticker || report.pair)}</span>
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

  function resetRunButton() {
    DOM.btnRun.disabled = false;
    DOM.btnRun.innerHTML = `
      <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"/></svg>
      Launch Analysis
    `;
  }

  // ---- Formatting Helpers ----
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
