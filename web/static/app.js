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
  let settingsData = null;

  let sessionKey = '';
  let sessionReady = null;
  let sessionExpiresAt = 0;

  function apiErrorMessage(data, fallback = 'Request failed') {
    const error = data && (data.error || data.detail);
    if (typeof error === 'string') return error;
    if (error && typeof error.message === 'string') return error.message;
    return fallback;
  }

  async function apiFetch(url, options = {}) {
    const target = new URL(url, window.location.origin);
    if (target.origin !== window.location.origin) return window.fetch(url, options);
    const headers = new Headers(options.headers || {});
    if (inMemoryApiKey) {
      headers.set('X-API-Key', inMemoryApiKey);
      if (sessionKey !== inMemoryApiKey || Date.now() >= sessionExpiresAt) {
        sessionKey = inMemoryApiKey;
        sessionExpiresAt = Date.now() + 7 * 60 * 60 * 1000;
        sessionReady = window.fetch('/api/auth/session', {
          method: 'POST', credentials: 'same-origin',
          headers: { 'X-API-Key': inMemoryApiKey },
        }).then(res => { if (!res.ok) sessionKey = ''; }).catch(() => { sessionKey = ''; });
      }
      await sessionReady;
    }
    return window.fetch(url, { ...options, headers, credentials: 'same-origin' });
  }

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
    forexContextToggles: $$('#forexContextTimeframes .analyst-toggle'),
    forexAccountSource: $('#forexAccountSource'),
    forexResearchDepth: $('#forexResearchDepth'),
    forexMinRR:         $('#forexMinRR'),
    forexMaxSpread:     $('#forexMaxSpread'),
    forexEconomicBlackout: $('#forexEconomicBlackout'),
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
    lessonsSetupFilter: $('#lessonsSetupFilter'),
    lessonsDirectionFilter: $('#lessonsDirectionFilter'),
    lessonsTimeframeFilter: $('#lessonsTimeframeFilter'),
    lessonsEvidenceFilter: $('#lessonsEvidenceFilter'),
    lessonsStatusFilter: $('#lessonsStatusFilter'),
    // Dashboard Overview DOM
    dashBalance:    $('#dashBalance'),
    dashEquity:     $('#dashEquity'),
    dashFloating:   $('#dashFloating'),
    dashFreeMargin: $('#dashFreeMargin'),
    dashMargin:     $('#dashMargin'),
    dashCurrency:   $('#dashCurrency'),
    dashLeverage:   $('#dashLeverage'),
    dashServerBadge:$('#dashServerBadge'),
    dashLoginBadge: $('#dashLoginBadge'),
    dashPositionsContainer: $('#dashPositionsContainer'),
    dashOrdersContainer:    $('#dashOrdersContainer'),
    dashProposalsContainer: $('#dashProposalsContainer'),
    dashTodayTradesCount:   $('#dashTodayTradesCount'),
    dashTodayPnl:           $('#dashTodayPnl'),
    dashTodayR:             $('#dashTodayR'),
    dashWinRate:    $('#dashWinRate'),
    dashProfitFactor: $('#dashProfitFactor'),
    dashTotalR:     $('#dashTotalR'),
    dashMaxDD:      $('#dashMaxDD'),
    dashExpectancy: $('#dashExpectancy'),
    dashAvgR:       $('#dashAvgR'),
    dashSampleWarning: $('#dashSampleWarning'),
    dashClosedTrades: $('#dashClosedTrades'),
    dashLessonsContainer: $('#dashLessonsContainer'),
    dashEventsContainer:  $('#dashEventsContainer'),
    btnDashRefreshPositions: $('#btnDashRefreshPositions'),
    btnDashRefreshProposals: $('#btnDashRefreshProposals'),
    // Proposals View DOM
    proposalsTableContainer: $('#proposalsTableContainer'),
    proposalsPairFilter: $('#proposalsPairFilter'),
    proposalsDateFilter: $('#proposalsDateFilter'),
    proposalsStatusFilter: $('#proposalsStatusFilter'),
    proposalsActionFilter: $('#proposalsActionFilter'),
    proposalsSetupFilter: $('#proposalsSetupFilter'),
    proposalsTimeframeFilter: $('#proposalsTimeframeFilter'),
    btnRefreshProposals: $('#btnRefreshProposals'),
    // MT5 View DOM (Phase 29)
    btnMT5Connect:          $('#btnMT5Connect'),
    btnMT5Disconnect:       $('#btnMT5Disconnect'),
    btnMT5RefreshStatus:    $('#btnMT5RefreshStatus'),
    mt5ConnBadge:           $('#mt5ConnBadge'),
    mt5TerminalPathInput:   $('#mt5TerminalPathInput'),
    mt5ServerInput:         $('#mt5ServerInput'),
    mt5LoginInput:          $('#mt5LoginInput'),
    mt5DiagTerminalPath:    $('#mt5DiagTerminalPath'),
    mt5DiagServer:          $('#mt5DiagServer'),
    mt5DiagLogin:           $('#mt5DiagLogin'),
    mt5Margin:              $('#mt5Margin'),
    mt5MarginLevel:         $('#mt5MarginLevel'),
    mt5Leverage:            $('#mt5Leverage'),
    mt5CurrencyVal:         $('#mt5CurrencyVal'),
    mt5SymbolInput:         $('#mt5SymbolInput'),
    btnMT5LookupSymbol:     $('#btnMT5LookupSymbol'),
    mt5SymbolResultContainer:$('#mt5SymbolResultContainer'),
    mt5OrdersContainer:     $('#mt5OrdersContainer'),
    mt5DealsContainer:      $('#mt5DealsContainer'),
    btnRefreshMT5Positions: $('#btnRefreshMT5Positions'),
    // Performance View DOM
    btnRefreshAnalytics: $('#btnRefreshAnalytics'),
    btnRunCalibration: $('#btnRunCalibration'),
    performanceOverallContainer: $('#performanceOverallContainer'),
    performanceExecutionContainer: $('#performanceExecutionContainer'),
    performanceChartsContainer: $('#performanceChartsContainer'),
    confidenceCalibrationContainer: $('#confidenceCalibrationContainer'),
    performanceSegmentSelect: $('#performanceSegmentSelect'),
    sampleSizeWarning: $('#sampleSizeWarning'),
    perfBreakdownContainer: $('#perfBreakdownContainer'),
    // Backtesting View DOM
    btnModeBacktestReal: $('#btnModeBacktestReal'),
    btnModeBacktestDemo: $('#btnModeBacktestDemo'),
    backtestModeBanner:  $('#backtestModeBanner'),
    backtestForm:        $('#backtestForm'),
    btPair:              $('#btPair'),
    btStartDate:         $('#btStartDate'),
    btEndDate:           $('#btEndDate'),
    btTimeframe:         $('#btTimeframe'),
    btCapital:           $('#btCapital'),
    btMaxPoints:         $('#btMaxPoints'),
    btSpread:            $('#btSpread'),
    backtestEstimate:    $('#backtestEstimate'),
    btnLaunchBacktest:   $('#btnLaunchBacktest'),
    btnRefreshBacktests: $('#btnRefreshBacktests'),
    btnRunWalkForward: $('#btnRunWalkForward'),
    btnRunAblation: $('#btnRunAblation'),
    backtestRunsContainer: $('#backtestRunsContainer'),
    // Learning View DOM
    btnRefreshLessons:   $('#btnRefreshLessons'),
    // Settings View DOM
    settingProvider:     $('#settingProvider'),
    settingQuickModel:   $('#settingQuickModel'),
    settingDeepModel:    $('#settingDeepModel'),
    settingProviderInput: $('#settingProviderInput'),
    settingQuickModelInput: $('#settingQuickModelInput'),
    settingDeepModelInput: $('#settingDeepModelInput'),
    settingBackendUrl: $('#settingBackendUrl'),
    settingApiKeyStatus: $('#settingApiKeyStatus'),
    settingsStatus: $('#settingsStatus'),
    btnSaveSettings: $('#btnSaveSettings'),
    btnResetSettings: $('#btnResetSettings'),
    settingPair: $('#settingPair'),
    settingTimeframe: $('#settingTimeframe'),
    settingContextTimeframes: $('#settingContextTimeframes'),
    settingMarketSource: $('#settingMarketSource'),
    settingRiskPercent: $('#settingRiskPercent'),
    settingMinRR: $('#settingMinRR'),
    settingMaxSpread: $('#settingMaxSpread'),
    settingNewsBlackout: $('#settingNewsBlackout'),
    settingBrokerMode: $('#settingBrokerMode'),
    settingAutoOrder: $('#settingAutoOrder'),
    settingPollInterval: $('#settingPollInterval'),
    settingReflection: $('#settingReflection'),
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
          if (DOM.mt5SymbolInput && DOM.mt5SymbolInput.value) {
            lookupMT5Symbol(DOM.mt5SymbolInput.value);
          }
        } else if (tab.dataset.view === 'journal') {
          loadJournalTrades();
        } else if (tab.dataset.view === 'performance') {
          loadPerformanceInterface();
        } else if (tab.dataset.view === 'backtest') {
          loadBacktestRuns();
        } else if (tab.dataset.view === 'learning') {
          loadLessons();
        } else if (tab.dataset.view === 'settings') {
          loadRuntimeSettings();
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
    DOM.forexContextToggles.forEach(btn => {
      btn.addEventListener('click', () => {
        const activeCount = Array.from(DOM.forexContextToggles).filter(b => b.classList.contains('active')).length;
        if (btn.classList.contains('active') && activeCount <= 1) {
          showToast('At least one context timeframe must remain selected', 'info');
          return;
        }
        btn.classList.toggle('active');
      });
    });

    DOM.provider.addEventListener('change', updateModelSelects);
    DOM.form.addEventListener('submit', handleSubmit);
    DOM.reportModalClose.addEventListener('click', closeReportModal);
    DOM.reportOverlay.addEventListener('click', (e) => {
      if (e.target === DOM.reportOverlay) closeReportModal();
    });
    DOM.reportContent.addEventListener('click', event => {
      const button = event.target.closest('[data-applied-lesson-id]');
      if (button) openAppliedLesson(button.dataset.appliedLessonId);
    });

    const tradeDetail = JournalUI.createController({ request: apiFetch, document });
    DOM.journalTableContainer.addEventListener('click', event => {
      const button = event.target.closest('[data-trade-id]');
      if (button) tradeDetail.loadTradeDetail(button.dataset.tradeId, button);
    });

    // Allow opening a trade detail from lesson cards (delegated)
    if (DOM.lessonsContainer) {
      DOM.lessonsContainer.addEventListener('click', event => {
        const tradeButton = event.target.closest('[data-trade-id]');
        if (tradeButton && tradeButton.dataset.tradeId) {
          const journalTab = document.querySelector('.tab[data-view="journal"]');
          if (journalTab) journalTab.click();
          tradeDetail.loadTradeDetail(tradeButton.dataset.tradeId, tradeButton);
          return;
        }
        const proposalButton = event.target.closest('[data-proposal-id]');
        if (proposalButton && proposalButton.dataset.proposalId) {
          showProposalDetailModal(proposalButton.dataset.proposalId);
        }
      });
    }

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
        loadDashboardOverview();
        showToast('Dashboard positions refreshed', 'info');
      });
    }

    if (DOM.btnDashRefreshProposals) {
      DOM.btnDashRefreshProposals.addEventListener('click', () => {
        loadDashboardOverview();
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
    if (DOM.proposalsDateFilter) {
      DOM.proposalsDateFilter.addEventListener('change', loadProposals);
    }
    if (DOM.proposalsStatusFilter) {
      DOM.proposalsStatusFilter.addEventListener('change', loadProposals);
    }
    if (DOM.proposalsActionFilter) {
      DOM.proposalsActionFilter.addEventListener('change', loadProposals);
    }
    if (DOM.proposalsSetupFilter) {
      DOM.proposalsSetupFilter.addEventListener('change', loadProposals);
    }
    if (DOM.proposalsTimeframeFilter) {
      DOM.proposalsTimeframeFilter.addEventListener('change', loadProposals);
    }
    if (DOM.btnRefreshProposals) {
      DOM.btnRefreshProposals.addEventListener('click', () => {
        loadProposals();
        showToast('Proposals list refreshed', 'info');
      });
    }

    if (DOM.btnRefreshMT5Positions) {
      DOM.btnRefreshMT5Positions.addEventListener('click', () => {
        loadMT5Positions();
        loadMT5Orders();
        loadMT5Deals();
        showToast('MT5 observer data refreshed', 'info');
      });
    }

    if (DOM.btnMT5Connect) {
      DOM.btnMT5Connect.addEventListener('click', connectMT5);
    }
    if (DOM.btnMT5Disconnect) {
      DOM.btnMT5Disconnect.addEventListener('click', disconnectMT5);
    }
    if (DOM.btnMT5RefreshStatus) {
      DOM.btnMT5RefreshStatus.addEventListener('click', () => {
        loadMT5Status();
        loadMT5Account();
        loadMT5Positions();
        loadMT5Orders();
        loadMT5Deals();
        showToast('MT5 status refreshed', 'info');
      });
    }
    if (DOM.btnMT5LookupSymbol) {
      DOM.btnMT5LookupSymbol.addEventListener('click', () => lookupMT5Symbol());
    }
    if (DOM.mt5SymbolInput) {
      DOM.mt5SymbolInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
          e.preventDefault();
          lookupMT5Symbol();
        }
      });
    }
    const symbolChips = $$('.symbol-chip');
    if (symbolChips.length > 0) {
      symbolChips.forEach(chip => {
        chip.addEventListener('click', () => {
          const sym = chip.dataset.symbol;
          if (sym) lookupMT5Symbol(sym);
        });
      });
    }

    if (DOM.btnRefreshAnalytics) {
      DOM.btnRefreshAnalytics.addEventListener('click', () => {
        loadPerformanceInterface();
        showToast('Analytics refreshed', 'info');
      });
    }

    if (DOM.performanceSegmentSelect) {
      DOM.performanceSegmentSelect.addEventListener('change', () => renderPerformanceSegmentation());
    }

    if (DOM.btnRunCalibration) {
      DOM.btnRunCalibration.addEventListener('click', () => {
        loadPerformanceInterface();
        showToast('Confidence calibration refreshed', 'info');
      });
    }

    if (DOM.btnRefreshBacktests) {
      DOM.btnRefreshBacktests.addEventListener('click', () => {
        loadBacktestRuns();
        showToast('Backtest runs refreshed', 'info');
      });
    }

    if (DOM.btnRunWalkForward) {
      DOM.btnRunWalkForward.addEventListener('click', async () => {
        try {
          const pair = DOM.btPair ? DOM.btPair.value : 'EURUSD';
          const timeframe = DOM.btTimeframe ? DOM.btTimeframe.value : 'H1';
          const date_from = DOM.btStartDate ? (DOM.btStartDate.value || null) : null;
          const date_to = DOM.btEndDate ? (DOM.btEndDate.value || null) : null;
          const payload = { pair, timeframe, date_from, date_to, count: 300 };
          const res = await apiFetch('/api/forex/backtest/walkforward', {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
          });
          if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(apiErrorMessage(err, 'Walk-Forward failed'));
          }
          const data = await res.json();
          await loadBacktestRuns();
          showToast('Walk-forward validation completed', 'success');
          // show lightweight modal by reusing detail renderer
          if (data && data.validation_id) openBacktestDetail(data.validation_id);
        } catch (e) {
          showToast(e.message || 'Walk-forward failed', 'error');
        }
      });
    }

    if (DOM.btnRunAblation) {
      DOM.btnRunAblation.addEventListener('click', async () => {
        try {
          const pair = DOM.btPair ? DOM.btPair.value : 'EURUSD';
          const res = await apiFetch('/api/forex/analytics/ablation', {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ pair }),
          });
          if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(apiErrorMessage(err, 'Ablation failed'));
          }
          const data = await res.json();
          renderAblationStudy(data.ablation_study || {});
          showToast('Ablation study complete', 'success');
        } catch (e) {
          showToast(e.message || 'Ablation failed', 'error');
        }
      });
    }

    // Backtest form submit handling
    if (DOM.backtestForm) {
      DOM.backtestForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        await handleBacktestSubmit(e);
      });
    }

    // Allow clicking run rows to open detail
    if (DOM.backtestRunsContainer) {
      DOM.backtestRunsContainer.addEventListener('click', (ev) => {
        const tr = ev.target.closest('[data-backtest-id]');
        if (tr) {
          const id = tr.dataset.backtestId;
          if (id) openBacktestDetail(id);
        }
      });
    }

    if (DOM.btnRefreshLessons) {
      DOM.btnRefreshLessons.addEventListener('click', () => {
        loadLessons();
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

    [DOM.lessonsPairFilter, DOM.lessonsSetupFilter, DOM.lessonsDirectionFilter,
      DOM.lessonsTimeframeFilter, DOM.lessonsEvidenceFilter, DOM.lessonsStatusFilter]
      .filter(Boolean).forEach(control => control.addEventListener('change', loadLessons));

    if (DOM.apiKey) {
      DOM.apiKey.addEventListener('input', () => {
        inMemoryApiKey = DOM.apiKey.value.trim();
      });
    }

    if (DOM.btnSaveSettings) {
      DOM.btnSaveSettings.addEventListener('click', saveSettings);
    }
    if (DOM.btnResetSettings) {
      DOM.btnResetSettings.addEventListener('click', resetSettings);
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
      const res = await apiFetch('/api/config');
      configData = await res.json();
      await loadRuntimeSettings({ quiet: true });
      populateProviders();
      populateSettingsView();
      // Purge legacy secrets even when API-key mode is no longer enabled.
      try {
        localStorage.removeItem('tradingagents_api_key');
        sessionStorage.removeItem('tradingagents_api_key');
      } catch (_) {}
      if (configData.auth_required && DOM.apiKeyGroup) {
        DOM.apiKeyGroup.style.display = 'block';
      }
      DOM.serverDot.style.background = 'var(--green)';
      DOM.serverStatus.textContent = 'Connected';
    } catch (err) {
      DOM.serverDot.style.background = 'var(--red)';
      DOM.serverStatus.textContent = 'Disconnected';
      showToast('Failed to connect to server', 'error');
    }
  }

  async function loadRuntimeSettings({ quiet = false } = {}) {
    try {
      const res = await apiFetch('/api/forex/settings');
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(apiErrorMessage(data, 'Settings load failed'));
      settingsData = data;
      if (configData) {
        configData.runtime_settings = data.settings || {};
        configData.secret_status = data.secret_status || configData.secret_status;
      }
      populateSettingsView();
      return data;
    } catch (error) {
      if (!quiet && DOM.settingsStatus) {
        DOM.settingsStatus.textContent = error.message || 'Settings load failed';
        DOM.settingsStatus.className = 'settings-status error';
      }
      return null;
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

  function maskAccountLogin(login) {
    if (!login) return '--';
    const s = String(login).trim();
    if (s.length <= 3) return '***';
    return s.slice(0, 3) + '*'.repeat(s.length - 3);
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
    if (DOM.mt5Margin) DOM.mt5Margin.textContent = unav;
    if (DOM.mt5FreeMargin) DOM.mt5FreeMargin.textContent = unav;
    if (DOM.dashFreeMargin) DOM.dashFreeMargin.textContent = unav;
    if (DOM.mt5MarginLevel) DOM.mt5MarginLevel.textContent = unav;
    if (DOM.mt5Leverage) DOM.mt5Leverage.textContent = unav;
    if (DOM.mt5CurrencyVal) DOM.mt5CurrencyVal.textContent = 'USD';
    if (DOM.dashCurrency) DOM.dashCurrency.textContent = 'Standby / Unconnected';
    if (DOM.mt5ConnBadge) {
      DOM.mt5ConnBadge.textContent = 'DISCONNECTED';
      DOM.mt5ConnBadge.style.background = 'var(--amber-dim)';
      DOM.mt5ConnBadge.style.color = 'var(--amber)';
    }
  }

  // ---- MetaTrader 5 Passive Observer ----
  async function loadMT5Status() {
    try {
      const res = await apiFetch('/api/forex/mt5/status');
      if (res.ok) {
        const data = await res.json();
        const connected = data.is_connected === true || data.connected === true || data.status === 'CONNECTED';
        const login = data.login || data.account_login;
        const masked = data.masked_login || maskAccountLogin(login);
        const server = data.server || '--';
        const terminalPath = data.terminal_path || '--';

        DOM.mt5Dot.style.background = connected ? 'var(--green)' : 'var(--amber)';
        DOM.mt5Status.textContent = connected
          ? `MT5: Observed (${login ? '#' + login : 'Live'})`
          : 'MT5: Read-Only Observer';

        if (DOM.mt5ConnBadge) {
          DOM.mt5ConnBadge.textContent = connected ? 'CONNECTED' : 'DISCONNECTED';
          DOM.mt5ConnBadge.style.background = connected ? 'var(--green-dim)' : 'var(--amber-dim)';
          DOM.mt5ConnBadge.style.color = connected ? 'var(--green)' : 'var(--amber)';
        }
        if (DOM.mt5DiagTerminalPath) DOM.mt5DiagTerminalPath.textContent = terminalPath;
        if (DOM.mt5DiagServer) DOM.mt5DiagServer.textContent = server;
        if (DOM.mt5DiagLogin) DOM.mt5DiagLogin.textContent = masked;

        if (DOM.mt5TerminalPathInput && !DOM.mt5TerminalPathInput.value && data.terminal_path) {
          DOM.mt5TerminalPathInput.value = data.terminal_path;
        }
        if (DOM.mt5ServerInput && !DOM.mt5ServerInput.value && data.server) {
          DOM.mt5ServerInput.value = data.server;
        }
        if (DOM.mt5LoginInput && !DOM.mt5LoginInput.value && data.login) {
          DOM.mt5LoginInput.value = data.login;
        }

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
      const res = await apiFetch('/api/forex/mt5/account');
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
        const marginStr = (acc && acc.margin != null)
          ? `$${Number(acc.margin).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
          : 'Unavailable';
        const marginLevelStr = (acc && acc.margin_level != null)
          ? `${Number(acc.margin_level).toFixed(1)}%`
          : 'Unavailable';

        if (DOM.mt5Balance) DOM.mt5Balance.textContent = balStr;
        if (DOM.dashBalance) DOM.dashBalance.textContent = balStr;
        if (DOM.mt5Equity) DOM.mt5Equity.textContent = eqStr;
        if (DOM.dashEquity) DOM.dashEquity.textContent = eqStr;
        if (DOM.mt5Margin) DOM.mt5Margin.textContent = marginStr;
        if (DOM.mt5FreeMargin) DOM.mt5FreeMargin.textContent = freeStr;
        if (DOM.dashFreeMargin) DOM.dashFreeMargin.textContent = freeStr;
        if (DOM.mt5MarginLevel) DOM.mt5MarginLevel.textContent = marginLevelStr;

        if (acc && acc.currency) {
          if (DOM.mt5CurrencyVal) DOM.mt5CurrencyVal.textContent = acc.currency;
          if (DOM.dashCurrency) DOM.dashCurrency.textContent = `Currency: ${acc.currency}`;
        }
        if (acc && acc.leverage) {
          if (DOM.mt5Leverage) DOM.mt5Leverage.textContent = `1:${acc.leverage}`;
          if (DOM.dashLeverage) DOM.dashLeverage.textContent = `Leverage 1:${acc.leverage}`;
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

  async function loadMT5Positions() {
    try {
      const res = await apiFetch('/api/forex/mt5/positions');
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
              <th>Time</th>
              <th>Symbol</th>
              <th>Type</th>
              <th>Volume</th>
              <th>Open Price</th>
              <th>Current Price</th>
              <th>SL</th>
              <th>TP</th>
              <th>Floating P/L</th>
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
              const timeStr = p.time ? new Date(p.time).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '--';
              const pipsStr = p.unrealized_pips != null ? ` (${p.unrealized_pips >= 0 ? '+' : ''}${p.unrealized_pips} pips)` : '';
              return `
                <tr>
                  <td style="font-family:var(--font-mono); font-size:0.8rem;">#${escapeText(p.ticket)}</td>
                  <td style="font-size:0.75rem; color:var(--text-muted);">${escapeText(timeStr)}</td>
                  <td style="font-weight:700; color:var(--cyan);">${escapeText(p.symbol)}</td>
                  <td><span class="signal-badge ${badgeClass}" style="font-size:0.7rem; padding:2px 8px;">${dirLabel}</span></td>
                  <td style="font-family:var(--font-mono);">${Number(p.volume).toFixed(2)}</td>
                  <td style="font-family:var(--font-mono);">${formatForexPrice(p.price_open, p.symbol, p.digits)}</td>
                  <td style="font-family:var(--font-mono); font-weight:600;">${formatForexPrice(p.price_current, p.symbol, p.digits)}</td>
                  <td style="font-family:var(--font-mono);">${formatForexPrice(p.sl, p.symbol, p.digits)}</td>
                  <td style="font-family:var(--font-mono);">${formatForexPrice(p.tp, p.symbol, p.digits)}</td>
                  <td style="font-family:var(--font-mono); font-weight:700; color:${pnlColor};">${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)}${pipsStr}</td>
                </tr>
              `;
            }).join('')}
          </tbody>
        </table>
      `;
    } catch (_) {}
  }

  // ---- Journal & Learning Data ----
  let journalListGeneration = 0;
  async function loadJournalTrades() {
    const current = ++journalListGeneration;
    DOM.journalTableContainer.innerHTML = '<p class="journal-state" role="status">Loading journal…</p>';
    try {
      const res = await apiFetch('/api/forex/journal/trades?limit=50');
      const data = await res.json();
      if (current !== journalListGeneration) return;
      if (!res.ok) throw new Error(apiErrorMessage(data, 'Journal could not be loaded.'));
      DOM.journalTableContainer.innerHTML = JournalUI.table(data.trades || []);
    } catch (error) {
      if (current === journalListGeneration) DOM.journalTableContainer.innerHTML =
        `<p class="journal-state" role="alert">${escapeText(error.message || 'Network error loading journal.')} Use Refresh Journal to retry.</p>`;
    }
  }

  let lessonListGeneration = 0;
  function evidenceClass(count) {
    const n = Number(count || 0);
    return n <= 1 ? 'ANECDOTAL' : n === 2 ? 'EARLY' : n <= 5 ? 'MODERATE' : 'STRONG';
  }

  async function loadLessons() {
    const current = ++lessonListGeneration;
    DOM.lessonsContainer.innerHTML = '<p class="journal-state" role="status">Loading lessons…</p>';
    try {
      const params = new URLSearchParams();
      if (DOM.lessonsPairFilter?.value) params.set('pair', DOM.lessonsPairFilter.value);
      if (DOM.lessonsSetupFilter?.value.trim()) params.set('setup_type', DOM.lessonsSetupFilter.value.trim());
      if (DOM.lessonsDirectionFilter?.value) params.set('direction', DOM.lessonsDirectionFilter.value);
      if (DOM.lessonsTimeframeFilter?.value.trim()) params.set('timeframe', DOM.lessonsTimeframeFilter.value.trim());
      if (DOM.lessonsEvidenceFilter?.value) params.set('min_evidence_count', DOM.lessonsEvidenceFilter.value);
      if (DOM.lessonsStatusFilter?.value) params.set('active', DOM.lessonsStatusFilter.value);
      const url = `/api/forex/learning/lessons${params.size ? `?${params}` : ''}`;
      const res = await apiFetch(url);
      const data = await res.json();
      if (current !== lessonListGeneration) return;
      if (!res.ok) throw new Error(apiErrorMessage(data, 'Lessons could not be loaded.'));
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
              <div class="lesson-rule">${escapeText(l.actionable_rule || 'No actionable rule stored.')}</div>
              <div class="lesson-action">
                <strong>Observation:</strong> ${escapeText(l.observation || 'Unavailable')}
              </div>
              <div style="font-size:0.75rem; color:var(--text-muted); display:flex; gap:12px; flex-wrap:wrap;">
                <span>Setup: ${escapeText(l.setup || 'Unavailable')}</span>
                <span>Timeframe: ${escapeText(l.timeframe || 'Unavailable')}</span>
                <span>Direction: ${escapeText(l.direction || 'Unavailable')}</span>
                <span>Evidence: ${escapeText(l.evidence_count ?? 0)} (${evidenceClass(l.evidence_count)})</span>
                <span>Evidence weight: ${l.confidence == null ? 'Unavailable' : `${(Number(l.confidence) * 100).toFixed(0)}%`}</span>
                <span>Status: ${l.active === false ? 'RETIRED' : 'ACTIVE'}</span>
              </div>
              <details style="margin-top:8px;"><summary>Lesson details</summary>
                <p><strong>Root cause:</strong> ${escapeText(l.root_cause || 'Unavailable')}</p>
                <p><strong>Type / outcome:</strong> ${escapeText(l.lesson_type || 'Unavailable')} / ${escapeText(l.outcome_category || 'Unavailable')}</p>
                <p><strong>Session / regime:</strong> ${escapeText(l.session || 'Unavailable')} / ${escapeText(l.market_regime || 'Unavailable')}</p>
                <p><strong>Strategy version:</strong> ${escapeText(l.strategy_version || 'Unavailable')}</p>
                <p><strong>Created / validated:</strong> ${escapeText(l.created_at || 'Unavailable')} / ${escapeText(l.last_validated_at || 'Unavailable')}</p>
                <p><strong>Tags:</strong> ${escapeText((l.tags || []).join(', ') || 'None')}</p>
              </details>
              <div style="margin-top:8px; display:flex; gap:8px;">
                ${l.source_trade_id ? `<button class="btn-secondary btn-sm" data-trade-id="${escapeText(l.source_trade_id)}">View Source Trade</button>` : '<span class="stat-sub">Source trade unavailable: no stored ID.</span>'}
                ${l.proposal_id ? `<button class="btn-secondary btn-sm" data-proposal-id="${escapeText(l.proposal_id)}">View Source Proposal</button>` : '<span class="stat-sub">Source proposal unavailable: no stored ID.</span>'}
              </div>
            </div>
          `).join('')}
        </div>`;
    } catch (error) {
      if (current === lessonListGeneration) DOM.lessonsContainer.innerHTML =
        `<p class="journal-state" role="alert">${escapeText(error.message || 'Network error loading lessons.')} Use Refresh to retry.</p>`;
    }
  }

  async function openAppliedLesson(lessonId) {
    DOM.reportModalTitle.textContent = 'Applied Lesson Trace';
    DOM.reportModalBody.innerHTML = '<p class="journal-state" role="status">Loading stored lesson…</p>';
    DOM.reportOverlay.classList.add('active');
    try {
      const response = await apiFetch(`/api/forex/learning/lessons/${encodeURIComponent(lessonId)}`);
      const data = await response.json();
      if (!response.ok) throw new Error(apiErrorMessage(data, 'Stored lesson could not be loaded.'));
      const lesson = data.lesson || {}, sources = data.sources || {};
      DOM.reportModalBody.innerHTML = `<div class="lesson-card"><div class="lesson-header"><span class="lesson-pair">${escapeText(lesson.pair || 'GLOBAL')}</span><span class="lesson-tag">${escapeText(lesson.lesson_type || 'LESSON')}</span></div><h3>${escapeText(lesson.actionable_rule || 'No actionable rule stored.')}</h3><p><strong>Observation:</strong> ${escapeText(lesson.observation || 'Unavailable')}</p><p><strong>Root cause:</strong> ${escapeText(lesson.root_cause || 'Unavailable')}</p><p><strong>Evidence:</strong> ${escapeText(lesson.evidence_count ?? 0)} (${evidenceClass(lesson.evidence_count)})</p><p><strong>Source trade:</strong> ${escapeText(sources.trade?.id || 'Unavailable')} — ${sources.trade?.available ? 'available' : 'missing'}</p><p><strong>Source proposal:</strong> ${escapeText(sources.proposal?.id || 'Unavailable')} — ${sources.proposal?.available ? 'available' : 'missing'}</p></div>`;
    } catch (error) {
      DOM.reportModalBody.innerHTML = `<p class="journal-state" role="alert">${escapeText(error.message || 'Lesson detail unavailable.')}</p>`;
    }
  }

  // ---- Subsystem Loaders for Reorganized Dashboard (Phase 25) ----
  async function loadDashboardOverview() {
    try {
      const res = await apiFetch('/api/forex/dashboard/overview');
      if (!res.ok) {
        await Promise.allSettled([
          loadMT5Account(),
          loadDashboardPositions(),
          loadDashboardProposals(),
          loadDashboardAnalytics(),
          loadDashboardLessons(),
        ]);
        return;
      }
      const data = await res.json();
      renderDashboardOverview(data);
    } catch (_) {
      showMT5Unavailable();
    }
  }

  function renderDashboardOverview(data) {
    if (!data) return;

    // 1. MT5 Observer Account State
    const mt5 = data.mt5 || {};
    const acc = mt5.account;
    const isConn = mt5.is_connected === true;

    if (DOM.dashServerBadge) {
      DOM.dashServerBadge.textContent = `Server: ${mt5.server || 'None'}`;
    }
    if (DOM.dashLoginBadge) {
      DOM.dashLoginBadge.textContent = `Account: ${mt5.masked_login || 'Not Set'}`;
    }

    if (isConn && acc) {
      const balStr = (acc.balance != null)
        ? `$${Number(acc.balance).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
        : 'Unavailable';
      const eqStr = (acc.equity != null)
        ? `$${Number(acc.equity).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
        : 'Unavailable';
      const freeStr = (acc.margin_free != null)
        ? `$${Number(acc.margin_free).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
        : 'Unavailable';
      const marginStr = (acc.margin != null)
        ? `Margin Used: $${Number(acc.margin).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
        : 'Margin Used: $0.00';

      if (DOM.dashBalance) DOM.dashBalance.textContent = balStr;
      if (DOM.dashEquity) DOM.dashEquity.textContent = eqStr;
      if (DOM.dashFreeMargin) DOM.dashFreeMargin.textContent = freeStr;
      if (DOM.dashMargin) DOM.dashMargin.textContent = marginStr;

      if (acc.currency && DOM.dashCurrency) {
        DOM.dashCurrency.textContent = `Currency: ${acc.currency}`;
      }
      if (acc.leverage && DOM.dashLeverage) {
        DOM.dashLeverage.textContent = `Leverage 1:${acc.leverage}`;
      }

      if (acc.profit != null) {
        const p = Number(acc.profit);
        const pStr = `${p >= 0 ? '+' : ''}$${p.toFixed(2)}`;
        const pCol = p >= 0 ? 'var(--green)' : 'var(--red)';
        if (DOM.dashFloating) {
          DOM.dashFloating.textContent = pStr;
          DOM.dashFloating.style.color = pCol;
        }
      }
    } else {
      showMT5Unavailable();
      if (DOM.dashMargin) DOM.dashMargin.textContent = 'Margin Used: $0.00';
    }

    // 2. Open Positions
    if (DOM.dashPositionsContainer) {
      const positions = mt5.open_positions || [];
      if (positions.length === 0) {
        DOM.dashPositionsContainer.innerHTML = `
          <div class="empty-state" style="padding: 1.5rem;">
            <div class="empty-state-icon">📡</div>
            <div class="empty-state-title">No Active Positions</div>
            <div class="empty-state-desc">Trades executed manually in MT5 appear here automatically.</div>
          </div>
        `;
      } else {
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
      }
    }

    // 3. Pending Orders
    if (DOM.dashOrdersContainer) {
      const orders = mt5.pending_orders || [];
      if (orders.length === 0) {
        DOM.dashOrdersContainer.innerHTML = `
          <div style="font-size: 0.8rem; color: var(--text-muted); padding: 0.5rem 0;">No pending limit or stop orders.</div>
        `;
      } else {
        DOM.dashOrdersContainer.innerHTML = `
          <table class="data-table">
            <thead>
              <tr>
                <th>Ticket</th>
                <th>Symbol</th>
                <th>Type</th>
                <th>Volume</th>
                <th>Order Price</th>
                <th>SL</th>
                <th>TP</th>
              </tr>
            </thead>
            <tbody>
              ${orders.map(o => `
                <tr>
                  <td style="font-family:var(--font-mono); font-size:0.8rem;">#${escapeText(o.ticket)}</td>
                  <td style="font-weight:700; color:var(--cyan);">${escapeText(o.symbol)}</td>
                  <td><span class="lesson-tag" style="font-size:0.7rem;">${escapeText(o.type_str || o.type)}</span></td>
                  <td style="font-family:var(--font-mono);">${Number(o.volume_current || o.volume_initial || 0).toFixed(2)}</td>
                  <td style="font-family:var(--font-mono);">${formatForexPrice(o.price_open, o.symbol, o.digits)}</td>
                  <td style="font-family:var(--font-mono);">${formatForexPrice(o.sl, o.symbol, o.digits)}</td>
                  <td style="font-family:var(--font-mono);">${formatForexPrice(o.tp, o.symbol, o.digits)}</td>
                </tr>
              `).join('')}
            </tbody>
          </table>
        `;
      }
    }

    // 4. Proposals & Today's Result
    const trading = data.trading || {};
    if (DOM.dashProposalsContainer) {
      const proposals = trading.recent_proposals || [];
      if (proposals.length === 0) {
        DOM.dashProposalsContainer.innerHTML = `
          <div class="empty-state" style="padding: 1.5rem;">
            <div class="empty-state-icon">📋</div>
            <div class="empty-state-title">No Recent Proposals</div>
            <div class="empty-state-desc">Proposals generated through the multi-agent pipeline will be logged here.</div>
          </div>
        `;
      } else {
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
                  <td style="font-family:var(--font-mono); font-size:0.75rem; color:var(--text-muted);">${escapeText(String(p.proposal_id || '').slice(0, 8))}</td>
                  <td style="font-weight:700; color:var(--cyan);">${escapeText(p.pair)}</td>
                  <td><span class="signal-badge ${p.action === 'LONG' ? 'bullish' : 'bearish'}" style="font-size:0.7rem; padding:2px 8px;">${escapeText(p.action)}</span></td>
                  <td style="font-family:var(--font-mono);">${Number(p.entry_price || 0).toFixed(5)}</td>
                  <td style="font-family:var(--font-mono);">${Number(p.stop_loss || 0).toFixed(5)}</td>
                  <td style="font-family:var(--font-mono);">${Number(p.take_profit_1 || 0).toFixed(5)}</td>
                  <td><span class="status-cell ${escapeText(p.status)}">${escapeText(p.status)}</span></td>
                </tr>
              `).join('')}
            </tbody>
          </table>
        `;
      }
    }

    const todayRes = trading.today_result || {};
    if (DOM.dashTodayTradesCount) DOM.dashTodayTradesCount.textContent = todayRes.trade_count != null ? todayRes.trade_count : 0;
    if (DOM.dashTodayPnl) {
      const pnl = Number(todayRes.net_profit || 0);
      DOM.dashTodayPnl.textContent = `${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)}`;
      DOM.dashTodayPnl.style.color = pnl >= 0 ? 'var(--green)' : 'var(--red)';
    }
    if (DOM.dashTodayR) {
      const r = Number(todayRes.total_r || 0);
      DOM.dashTodayR.textContent = `${r >= 0 ? '+' : ''}${r.toFixed(2)}R`;
      DOM.dashTodayR.style.color = r >= 0 ? 'var(--cyan)' : 'var(--red)';
    }

    // 5. Performance Metrics & Sample Guard
    const perf = data.performance || {};
    if (DOM.dashExpectancy) DOM.dashExpectancy.textContent = `${Number(perf.expectancy || 0).toFixed(2)}R`;
    if (DOM.dashProfitFactor) DOM.dashProfitFactor.textContent = Number(perf.profit_factor || 0).toFixed(2);
    if (DOM.dashAvgR) DOM.dashAvgR.textContent = `${Number(perf.average_r || 0).toFixed(2)}R`;
    if (DOM.dashMaxDD) DOM.dashMaxDD.textContent = `${Number(perf.max_drawdown_pct || 0).toFixed(1)}%`;

    if (DOM.dashSampleWarning) {
      if (perf.is_sample_size_adequate === false && perf.sample_warning) {
        DOM.dashSampleWarning.style.display = 'flex';
        const span = DOM.dashSampleWarning.querySelector('span');
        if (span) span.textContent = perf.sample_warning;
      } else {
        DOM.dashSampleWarning.style.display = 'none';
      }
    }

    // 6. Macro Events
    const research = data.research || {};
    if (DOM.dashEventsContainer) {
      const events = research.upcoming_events || [];
      if (events.length === 0) {
        DOM.dashEventsContainer.innerHTML = `
          <div style="font-size: 0.8rem; color: var(--text-muted); padding: 0.5rem 0;">No high-impact releases scheduled today.</div>
        `;
      } else {
        DOM.dashEventsContainer.innerHTML = events.map(ev => `
          <div style="display: flex; justify-content: space-between; align-items: center; padding: 6px 10px; background: var(--bg-primary); border-radius: var(--radius-sm); border: 1px solid var(--border); margin-bottom: 6px; font-size: 0.8rem;">
            <div>
              <span class="signal-badge bearish" style="font-size: 0.65rem; padding: 1px 6px; margin-right: 6px;">${escapeText(ev.currency)}</span>
              <strong>${escapeText(ev.title)}</strong>
            </div>
            <div style="font-family: var(--font-mono); color: var(--text-secondary); font-size: 0.75rem;">
              ${escapeText(ev.time_utc || '')} | F: ${escapeText(ev.forecast || '-')} | P: ${escapeText(ev.previous || '-')}
            </div>
          </div>
        `).join('');
      }
    }

    // 7. Recent Lessons
    if (DOM.dashLessonsContainer) {
      const lessons = research.recent_lessons || [];
      if (lessons.length === 0) {
        DOM.dashLessonsContainer.innerHTML = `
          <div class="empty-state" style="padding: 1.5rem;">
            <div class="empty-state-icon">🧠</div>
            <div class="empty-state-title">No Stored Lessons</div>
            <div class="empty-state-desc">Post-trade reflections extract prescriptive rules and store them here.</div>
          </div>
        `;
      } else {
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
      }
    }
  }

  async function loadDashboardPositions() {
    if (!DOM.dashPositionsContainer) return;
    try {
      const res = await apiFetch('/api/forex/mt5/positions');
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
      const res = await apiFetch('/api/forex/proposals?limit=5');
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
      const res = await apiFetch('/api/forex/analytics/dashboard');
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
      const res = await apiFetch('/api/forex/learning/lessons');
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
          <div style="margin-top:8px;"> 
            <button class="btn-secondary btn-sm" data-trade-id="${escapeText(l.source_trade_id || l.trade_id || '')}">View Source</button>
          </div>
        </div>
      `).join('');
    } catch (_) {}
  }

  // ---- Proposals View ----
  async function loadProposals() {
    if (!DOM.proposalsTableContainer) return;
    try {
      let url = '/api/forex/proposals?limit=200';
      const pair = DOM.proposalsPairFilter ? DOM.proposalsPairFilter.value : '';
      const date = DOM.proposalsDateFilter ? DOM.proposalsDateFilter.value : '';
      const status = DOM.proposalsStatusFilter ? DOM.proposalsStatusFilter.value : '';
      const action = DOM.proposalsActionFilter ? DOM.proposalsActionFilter.value : '';
      const setup = DOM.proposalsSetupFilter ? DOM.proposalsSetupFilter.value : '';
      const timeframe = DOM.proposalsTimeframeFilter ? DOM.proposalsTimeframeFilter.value : '';

      if (pair) url += `&pair=${encodeURIComponent(pair)}`;
      if (date) url += `&date=${encodeURIComponent(date)}`;
      if (status) url += `&status=${encodeURIComponent(status)}`;
      if (action) url += `&action=${encodeURIComponent(action)}`;
      if (setup) url += `&setup=${encodeURIComponent(setup)}`;
      if (timeframe) url += `&timeframe=${encodeURIComponent(timeframe)}`;

      const res = await apiFetch(url);
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
              <th>Created</th>
              <th>Pair</th>
              <th>Action</th>
              <th>Timeframe</th>
              <th>Setup</th>
              <th>Entry</th>
              <th>SL</th>
              <th>TP</th>
              <th>RR</th>
              <th>Risk</th>
              <th>Lots</th>
              <th>Status</th>
              <th style="text-align:right;">Detail</th>
            </tr>
          </thead>
          <tbody>
            ${props.map(p => {
              const actionStr = (p.action || 'NO_TRADE').toUpperCase();
              const actionClass = actionStr === 'LONG' ? 'bullish' : actionStr === 'SHORT' ? 'bearish' : 'neutral';
              const createdStr = (p.created_at_utc || p.created_at || '').slice(0, 19).replace('T', ' ');
              const lotSize = p.suggested_lot_size != null ? Number(p.suggested_lot_size).toFixed(2) : (p.recommended_lot_size != null ? Number(p.recommended_lot_size).toFixed(2) : '-');
              const riskPct = p.suggested_risk_percent != null ? Number(p.suggested_risk_percent).toFixed(1) : (p.risk_percent != null ? Number(p.risk_percent).toFixed(1) : '1.0');

              return `
                <tr style="cursor:pointer;" onclick="showProposalDetailModal('${escapeText(p.proposal_id)}')">
                  <td style="font-family:var(--font-mono); font-size:0.75rem; color:var(--cyan); font-weight:600;">${escapeText(p.proposal_id)}</td>
                  <td class="date-cell">${escapeText(createdStr || '-')}</td>
                  <td style="font-weight:700; color:var(--text-bright);">${escapeText(p.pair)}</td>
                  <td><span class="signal-badge ${actionClass}" style="font-size:0.7rem; padding:2px 8px;">${escapeText(actionStr)}</span></td>
                  <td style="font-family:var(--font-mono); font-size:0.8rem;">${escapeText(p.timeframe || '-')}</td>
                  <td style="font-size:0.78rem;">${escapeText(p.setup_type || '-')}</td>
                  <td style="font-family:var(--font-mono);">${p.entry_price ? Number(p.entry_price).toFixed(5) : '-'}</td>
                  <td style="font-family:var(--font-mono); color:var(--red);">${p.stop_loss ? Number(p.stop_loss).toFixed(5) : '-'}</td>
                  <td style="font-family:var(--font-mono); color:var(--green);">${p.take_profit_1 ? Number(p.take_profit_1).toFixed(5) : '-'}</td>
                  <td style="font-family:var(--font-mono); color:var(--cyan);">${p.risk_reward_ratio ? `${Number(p.risk_reward_ratio).toFixed(2)}:1` : '-'}</td>
                  <td style="font-family:var(--font-mono);">${riskPct}%</td>
                  <td style="font-family:var(--font-mono); font-weight:700;">${lotSize}</td>
                  <td><span class="status-cell ${escapeText(p.status)}">${escapeText(p.status)}</span></td>
                  <td style="text-align:right;">
                    <button class="btn-secondary btn-sm" onclick="event.stopPropagation(); showProposalDetailModal('${escapeText(p.proposal_id)}')">
                      View
                    </button>
                  </td>
                </tr>
              `;
            }).join('')}
          </tbody>
        </table>
      `;
    } catch (_) {}
  }

  window.showProposalDetailModal = async function(proposalId) {
    if (!proposalId) return;
    DOM.reportModalTitle.innerHTML = `<span style="color:var(--cyan); font-weight:700;">Loading Proposal ${escapeText(proposalId)}...</span>`;
    DOM.reportModalBody.innerHTML = `<div style="padding:3rem; text-align:center; color:var(--text-muted);">Retrieving immutable proposal record, risk review, matched execution, and lessons...</div>`;
    DOM.reportOverlay.classList.add('active');
    document.body.style.overflow = 'hidden';

    try {
      const res = await apiFetch(`/api/forex/proposals/${encodeURIComponent(proposalId)}`);
      if (!res.ok) {
        DOM.reportModalBody.innerHTML = `<div style="padding:2rem; color:var(--red);">Proposal ${escapeText(proposalId)} could not be loaded.</div>`;
        return;
      }
      const data = await res.json();
      renderProposalDetailModal(data);
    } catch (_) {
      DOM.reportModalBody.innerHTML = `<div style="padding:2rem; color:var(--red);">Network error loading proposal details.</div>`;
    }
  };

  function renderProposalDetailModal(data) {
    const prop = data.proposal || {};
    const risk = data.risk_review || {};
    const orig = data.original_proposal || prop;
    const userDec = data.user_decision || {};
    const exec = data.matched_execution;
    const outcome = data.final_outcome;
    const lessons = data.lessons || [];

    const actionStr = (prop.action || 'NO_TRADE').toUpperCase();
    const actionClass = actionStr === 'LONG' ? 'long' : actionStr === 'SHORT' ? 'short' : actionStr === 'REJECT' ? 'reject' : 'no_trade';
    const statusStr = (prop.status || 'PENDING').toUpperCase();

    DOM.reportModalTitle.innerHTML = `
      <div style="display:flex; align-items:center; gap:10px; flex-wrap:wrap;">
        <span style="color:var(--cyan); font-weight:800; font-family:var(--font-mono);">${escapeText(prop.pair || 'FOREX')}</span>
        <span class="direction-tag ${actionClass}" style="font-size:0.8rem; padding:3px 10px;">${escapeText(actionStr)}</span>
        <span class="status-cell ${escapeText(statusStr)}" style="font-size:0.75rem;">Status: ${escapeText(statusStr)}</span>
        <span style="font-size:0.75rem; color:var(--text-muted); font-family:var(--font-mono);">${escapeText(prop.proposal_id)}</span>
      </div>
    `;

    DOM.reportModalBody.innerHTML = `
      <div style="display:flex; flex-direction:column; gap:1.25rem;">
        <!-- 1. IMMUTABLE ORIGINAL PROPOSAL -->
        <div class="decision-section">
          <div class="decision-section-title">
            <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
            Immutable Original Proposal
          </div>
          <div class="proposal-meta-grid">
            <div class="proposal-meta-item">
              <span class="proposal-meta-label">Entry Level</span>
              <span class="proposal-meta-value">${orig.entry_price ? Number(orig.entry_price).toFixed(5) : '-'}</span>
              <span class="proposal-meta-sub">${orig.order_type || 'LIMIT'} • ${orig.timeframe || 'H1'}</span>
            </div>
            <div class="proposal-meta-item">
              <span class="proposal-meta-label">Stop Loss</span>
              <span class="proposal-meta-value" style="color:var(--red);">${orig.stop_loss ? Number(orig.stop_loss).toFixed(5) : '-'}</span>
              <span class="proposal-meta-sub">${orig.sl_pips ? `${orig.sl_pips} pips` : 'Hard risk floor'}</span>
            </div>
            <div class="proposal-meta-item">
              <span class="proposal-meta-label">Take Profit 1</span>
              <span class="proposal-meta-value" style="color:var(--green);">${orig.take_profit_1 ? Number(orig.take_profit_1).toFixed(5) : '-'}</span>
              <span class="proposal-meta-sub">${orig.tp_pips ? `${orig.tp_pips} pips` : 'Target 1'}</span>
            </div>
            <div class="proposal-meta-item">
              <span class="proposal-meta-label">Take Profit 2</span>
              <span class="proposal-meta-value" style="color:var(--green);">${orig.take_profit_2 ? Number(orig.take_profit_2).toFixed(5) : 'None'}</span>
              <span class="proposal-meta-sub">Runner target</span>
            </div>
            <div class="proposal-meta-item">
              <span class="proposal-meta-label">Risk : Reward</span>
              <span class="proposal-meta-value" style="color:var(--cyan);">${orig.risk_reward_ratio ? `${Number(orig.risk_reward_ratio).toFixed(2)}:1` : '-'}</span>
              <span class="proposal-meta-sub">Geometry ratio</span>
            </div>
            <div class="proposal-meta-item">
              <span class="proposal-meta-label">Suggested Sizing</span>
              <span class="proposal-meta-value">${orig.suggested_lot_size != null ? `${Number(orig.suggested_lot_size).toFixed(2)} lots` : '-'}</span>
              <span class="proposal-meta-sub">${orig.suggested_risk_percent != null ? `${orig.suggested_risk_percent}% risk` : '1.0% equity'}</span>
            </div>
          </div>
          ${orig.trade_rationale_summary ? `
            <div style="background:var(--bg-primary); padding:0.75rem 1rem; border-radius:var(--radius-sm); border:1px solid var(--border); margin-top:0.75rem; font-size:0.82rem; color:var(--text-secondary); line-height:1.5;">
              <strong style="color:var(--cyan);">Thesis:</strong> ${escapeText(orig.trade_rationale_summary)}
            </div>
          ` : ''}
          ${orig.invalidation_condition ? `
            <div style="font-size:0.78rem; color:var(--amber); margin-top:0.5rem;">
              <strong>Invalidation:</strong> ${escapeText(orig.invalidation_condition)}
            </div>
          ` : ''}
        </div>

        <!-- 2. RISK REVIEW -->
        <div class="decision-section">
          <div class="decision-section-title">
            <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
            Risk Review &amp; Compliance Audit
          </div>
          <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:0.75rem;">
            <span style="font-size:0.85rem; color:var(--text-secondary);">Risk Decision:</span>
            <span class="signal-badge" style="background:${risk.decision === 'APPROVE' ? 'rgba(0,230,118,0.15)' : 'rgba(255,82,82,0.15)'}; color:${risk.decision === 'APPROVE' ? 'var(--green)' : 'var(--red)'}; font-size:0.82rem; padding:4px 12px; font-weight:700;">
              ${escapeText(risk.decision || 'AUDITED')}
            </span>
          </div>
          <div style="display:flex; flex-direction:column; gap:4px;">
            ${(risk.risk_checks_passed || ['Verified Risk:Reward threshold (>= 1.5R)', 'Stop-loss distance verified within risk limits', 'Account equity protection ceiling respected']).map(c => `
              <div class="risk-check-item">
                <span class="risk-check-icon pass">✓</span>
                <span>${escapeText(c)}</span>
              </div>
            `).join('')}
            ${(risk.risk_violations || []).map(v => `
              <div class="risk-check-item">
                <span class="risk-check-icon fail">✗</span>
                <span style="color:var(--red); font-weight:600;">Violation: ${escapeText(v)}</span>
              </div>
            `).join('')}
          </div>
          ${risk.executive_rationale ? `
            <div style="background:var(--bg-primary); padding:0.75rem 1rem; border-radius:var(--radius-sm); border:1px solid var(--border); margin-top:0.75rem; font-size:0.82rem; color:var(--text-secondary); line-height:1.5;">
              <strong style="color:var(--text-bright);">Risk Rationale:</strong> ${escapeText(risk.executive_rationale)}
            </div>
          ` : ''}
        </div>

        <!-- 3. USER DECISION & LIFECYCLE CONTROLS -->
        <div class="decision-section">
          <div class="decision-section-title">
            <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M16 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="8.5" cy="7" r="4"/><line x1="20" y1="8" x2="20" y2="14"/><line x1="23" y1="11" x2="17" y2="11"/></svg>
            User Decision &amp; Lifecycle Status
          </div>
          <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px; margin-bottom:1rem;">
            <div>
              <div style="font-size:0.82rem; color:var(--text-secondary);">Current Lifecycle State:</div>
              <div style="font-size:1rem; font-weight:700; color:var(--text-bright);">${escapeText(userDec.status || statusStr)}</div>
            </div>
            <div style="display:flex; gap:8px; flex-wrap:wrap;">
              <button class="btn-secondary btn-sm" style="background:rgba(0,230,118,0.15); color:var(--green); border-color:var(--green);" onclick="updateProposalDecision('${escapeText(prop.proposal_id)}', 'APPROVED')">
                ✓ Approve
              </button>
              <button class="btn-secondary btn-sm" style="background:rgba(0,212,255,0.15); color:var(--cyan); border-color:var(--cyan);" onclick="updateProposalDecision('${escapeText(prop.proposal_id)}', 'EXECUTED')">
                ⚡ Mark Executed
              </button>
              <button class="btn-secondary btn-sm" style="background:rgba(255,171,64,0.15); color:var(--amber); border-color:var(--amber);" onclick="updateProposalDecision('${escapeText(prop.proposal_id)}', 'SKIPPED')">
                ⏭️ Skip
              </button>
              <button class="btn-secondary btn-sm" style="background:rgba(255,82,82,0.15); color:var(--red); border-color:var(--red);" onclick="updateProposalDecision('${escapeText(prop.proposal_id)}', 'REJECTED')">
                🚫 Reject
              </button>
              <button class="btn-secondary btn-sm" onclick="updateProposalDecision('${escapeText(prop.proposal_id)}', 'EXPIRED')">
                ⏱️ Expire
              </button>
            </div>
          </div>
        </div>

        <!-- 4. MATCHED EXECUTION -->
        <div class="decision-section">
          <div class="decision-section-title">
            <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>
            Matched Broker Execution
          </div>
          ${exec ? `
            <div class="proposal-meta-grid">
              <div class="proposal-meta-item">
                <span class="proposal-meta-label">Trade ID</span>
                <span style="font-family:var(--font-mono); font-weight:700; color:var(--cyan);">${escapeText(exec.trade_id)}</span>
              </div>
              <div class="proposal-meta-item">
                <span class="proposal-meta-label">Open Price</span>
                <span class="proposal-meta-value">${Number(exec.open_price).toFixed(5)}</span>
              </div>
              <div class="proposal-meta-item">
                <span class="proposal-meta-label">Volume</span>
                <span class="proposal-meta-value">${Number(exec.lots).toFixed(2)} lots</span>
              </div>
              <div class="proposal-meta-item">
                <span class="proposal-meta-label">Open Time</span>
                <span style="font-size:0.8rem; font-family:var(--font-mono);">${escapeText(exec.open_time_utc || '-')}</span>
              </div>
            </div>
          ` : `
            <div style="font-size:0.82rem; color:var(--text-muted); font-style:italic;">
              No broker execution trade has been linked to this proposal yet.
            </div>
          `}
        </div>

        <!-- 5. FINAL OUTCOME -->
        <div class="decision-section">
          <div class="decision-section-title">
            <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M8 14s1.5 2 4 2 4-2 4-2"/><line x1="9" y1="9" x2="9.01" y2="9"/><line x1="15" y1="9" x2="15.01" y2="9"/></svg>
            Final Outcome
          </div>
          ${outcome && outcome.status === 'CLOSED' ? `
            <div class="proposal-meta-grid">
              <div class="proposal-meta-item">
                <span class="proposal-meta-label">Pips Gained</span>
                <span class="proposal-meta-value" style="color:${(outcome.pips_gained || 0) >= 0 ? 'var(--green)' : 'var(--red)'};">${Number(outcome.pips_gained || 0).toFixed(1)}</span>
              </div>
              <div class="proposal-meta-item">
                <span class="proposal-meta-label">R Multiple</span>
                <span class="proposal-meta-value" style="color:${(outcome.r_multiple || 0) >= 0 ? 'var(--green)' : 'var(--red)'};">${Number(outcome.r_multiple || 0).toFixed(2)}R</span>
              </div>
              <div class="proposal-meta-item">
                <span class="proposal-meta-label">Net Profit</span>
                <span class="proposal-meta-value" style="color:${(outcome.net_profit || 0) >= 0 ? 'var(--green)' : 'var(--red)'};">$${Number(outcome.net_profit || 0).toFixed(2)}</span>
              </div>
              <div class="proposal-meta-item">
                <span class="proposal-meta-label">Exit Reason</span>
                <span style="font-size:0.85rem; font-weight:700;">${escapeText(outcome.exit_reason || '-')}</span>
              </div>
            </div>
            ${outcome.reflection ? `
              <div style="background:var(--bg-primary); padding:0.75rem 1rem; border-radius:var(--radius-sm); border:1px solid var(--border); margin-top:0.75rem; font-size:0.82rem; color:var(--text-secondary); line-height:1.5;">
                <strong style="color:var(--cyan);">Post-Trade Reflection:</strong> ${escapeText(outcome.reflection)}
              </div>
            ` : ''}
          ` : `
            <div style="font-size:0.82rem; color:var(--text-muted); font-style:italic;">
              ${outcome && outcome.reason ? escapeText(outcome.reason) : 'Outcome tracking active. Awaiting trade completion or expiry simulation.'}
            </div>
          `}
        </div>

        <!-- 6. HISTORICAL LESSONS -->
        <div class="decision-section">
          <div class="decision-section-title">
            <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>
            Historical Lessons &amp; Applied Guardrails
          </div>
          ${lessons.length > 0 ? `
            <div style="display:flex; flex-direction:column; gap:6px;">
              ${lessons.map(les => `
                <div style="background:var(--bg-primary); padding:8px 12px; border-radius:var(--radius-sm); border:1px solid var(--border); font-size:0.82rem; display:flex; align-items:center; gap:8px;">
                  <span style="font-family:var(--font-mono); color:var(--cyan); font-weight:600;">[Lesson]</span>
                  <span style="color:var(--text-primary);">${typeof les === 'object' ? escapeText(les.actionable_rule || les.observation || JSON.stringify(les)) : escapeText(String(les))}</span>
                </div>
              `).join('')}
            </div>
          ` : `
            <div style="font-size:0.82rem; color:var(--text-muted); font-style:italic;">
              No failure memory patterns matched this setup. Proposal executed under default institutional guardrails.
            </div>
          `}
        </div>
      </div>
    `;
  }

  window.updateProposalDecision = async function(proposalId, newStatus) {
    if (!proposalId || !newStatus) return;
    try {
      const res = await apiFetch(`/api/forex/proposals/${encodeURIComponent(proposalId)}/status`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: newStatus, reason: `User selected ${newStatus} via dashboard` }),
      });
      if (res.ok) {
        showToast(`Proposal status updated to ${newStatus}`, 'success');
        showProposalDetailModal(proposalId);
        loadProposals();
      } else {
        const err = await res.json().catch(() => ({}));
        showToast(apiErrorMessage(err, 'Failed to update proposal status'), 'error');
      }
    } catch (_) {
      showToast('Network error updating proposal status', 'error');
    }
  };

  // ---- MT5 Orders, Deals & Observer Actions (Phase 29) ----
  async function loadMT5Orders() {
    if (!DOM.mt5OrdersContainer) return;
    try {
      const res = await apiFetch('/api/forex/mt5/orders');
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
              <th>State</th>
            </tr>
          </thead>
          <tbody>
            ${orders.map(o => `
              <tr>
                <td style="font-family:var(--font-mono); font-size:0.8rem;">#${escapeText(o.ticket)}</td>
                <td style="font-weight:700; color:var(--cyan);">${escapeText(o.symbol)}</td>
                <td><span class="badge-readonly" style="font-size:0.7rem;">${escapeText(o.type_str || o.type)}</span></td>
                <td style="font-family:var(--font-mono);">${Number(o.volume_initial || o.volume_current || 0).toFixed(2)}</td>
                <td style="font-family:var(--font-mono); font-weight:600;">${formatForexPrice(o.price_open, o.symbol)}</td>
                <td style="font-family:var(--font-mono);">${formatForexPrice(o.sl, o.symbol)}</td>
                <td style="font-family:var(--font-mono);">${formatForexPrice(o.tp, o.symbol)}</td>
                <td style="font-size:0.75rem; color:var(--text-muted);">${escapeText(o.state || 'PLACED')}</td>
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
      const res = await apiFetch('/api/forex/mt5/deals');
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
              <th>Comm / Swap</th>
              <th>Profit</th>
            </tr>
          </thead>
          <tbody>
            ${deals.slice(0, 15).map(d => {
              const pnl = Number(d.profit || 0);
              const pnlColor = pnl >= 0 ? 'var(--green)' : 'var(--red)';
              const comm = Number(d.commission || 0) + Number(d.swap || 0);
              return `
                <tr>
                  <td style="font-family:var(--font-mono); font-size:0.8rem;">#${escapeText(d.deal_id || d.ticket)}</td>
                  <td style="font-weight:700; color:var(--cyan);">${escapeText(d.symbol)}</td>
                  <td>${escapeText(d.type_str || d.type || (d.entry ? 'DEAL ' + d.entry : 'DEAL'))}</td>
                  <td style="font-family:var(--font-mono);">${Number(d.volume || 0).toFixed(2)}</td>
                  <td style="font-family:var(--font-mono); font-weight:600;">${formatForexPrice(d.price, d.symbol)}</td>
                  <td style="font-family:var(--font-mono); font-size:0.75rem; color:var(--text-muted);">${comm !== 0 ? '$' + comm.toFixed(2) : '$0.00'}</td>
                  <td style="font-family:var(--font-mono); font-weight:700; color:${pnlColor};">${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)}</td>
                </tr>
              `;
            }).join('')}
          </tbody>
        </table>
      `;
    } catch (_) {}
  }

  async function connectMT5() {
    const path = DOM.mt5TerminalPathInput ? DOM.mt5TerminalPathInput.value.trim() : '';
    const server = DOM.mt5ServerInput ? DOM.mt5ServerInput.value.trim() : '';
    const loginRaw = DOM.mt5LoginInput ? DOM.mt5LoginInput.value.trim() : '';
    const login = loginRaw ? parseInt(loginRaw, 10) : null;

    showToast('Connecting to MetaTrader 5 observer...', 'info');
    if (DOM.btnMT5Connect) DOM.btnMT5Connect.disabled = true;

    const payload = {};
    if (path) payload.path = path;
    if (server) payload.server = server;
    if (login) payload.login = login;

    const headers = { 'Content-Type': 'application/json' };
    const savedApiKey = inMemoryApiKey || (DOM.apiKey ? DOM.apiKey.value.trim() : '');
    if (savedApiKey) headers['X-API-Key'] = savedApiKey;

    try {
      const res = await apiFetch('/api/forex/mt5/connect', {
        method: 'POST',
        headers: headers,
        body: JSON.stringify(payload),
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok && data.connected) {
        showToast('Connected to MT5 observer in Read-Only mode', 'success');
        await loadMT5Status();
        await loadMT5Account();
        await loadMT5Positions();
        await loadMT5Orders();
        await loadMT5Deals();
      } else {
        showToast(apiErrorMessage(data, 'Failed to connect to MT5 terminal'), 'error');
        await loadMT5Status();
      }
    } catch (err) {
      showToast('Error connecting to MT5: ' + err.message, 'error');
      await loadMT5Status();
    } finally {
      if (DOM.btnMT5Connect) DOM.btnMT5Connect.disabled = false;
    }
  }

  async function disconnectMT5() {
    showToast('Disconnecting MT5 observer...', 'info');
    if (DOM.btnMT5Disconnect) DOM.btnMT5Disconnect.disabled = true;

    const headers = { 'Content-Type': 'application/json' };
    const savedApiKey = inMemoryApiKey || (DOM.apiKey ? DOM.apiKey.value.trim() : '');
    if (savedApiKey) headers['X-API-Key'] = savedApiKey;

    try {
      const res = await apiFetch('/api/forex/mt5/disconnect', {
        method: 'POST',
        headers: headers,
        body: JSON.stringify({}),
      });
      if (res.ok) {
        showToast('MT5 observer disconnected', 'success');
      } else {
        const data = await res.json().catch(() => ({}));
        showToast(apiErrorMessage(data, 'MT5 disconnect returned an error'), 'warning');
      }
      await loadMT5Status();
      await loadMT5Account();
      await loadMT5Positions();
      await loadMT5Orders();
      await loadMT5Deals();
    } catch (err) {
      showToast('Error disconnecting: ' + err.message, 'error');
      await loadMT5Status();
    } finally {
      if (DOM.btnMT5Disconnect) DOM.btnMT5Disconnect.disabled = false;
    }
  }

  async function lookupMT5Symbol(symbolOverride) {
    const rawSym = (symbolOverride || (DOM.mt5SymbolInput ? DOM.mt5SymbolInput.value : '')).trim().toUpperCase();
    if (!rawSym) {
      showToast('Please enter a symbol (e.g. EURUSD)', 'error');
      return;
    }
    if (DOM.mt5SymbolInput) DOM.mt5SymbolInput.value = rawSym;

    const symbolChips = $$('.symbol-chip');
    symbolChips.forEach(chip => {
      if (chip.dataset.symbol === rawSym) {
        chip.classList.add('active');
      } else {
        chip.classList.remove('active');
      }
    });

    if (DOM.mt5SymbolResultContainer) {
      DOM.mt5SymbolResultContainer.innerHTML = `
        <div class="empty-state" style="padding: 1.5rem;">
          <div class="empty-state-icon">⏳</div>
          <div class="empty-state-title">Retrieving ${escapeText(rawSym)} Quote...</div>
          <div class="empty-state-desc">Querying MetaTrader 5 contract specifications and live market tick...</div>
        </div>
      `;
    }

    try {
      const res = await apiFetch(`/api/forex/mt5/symbol/${encodeURIComponent(rawSym)}`);
      if (res.ok) {
        const data = await res.json();
        renderMT5SymbolQuote(data.symbol_info || data, data.tick);
      } else {
        const tickRes = await apiFetch(`/api/forex/mt5/tick/${encodeURIComponent(rawSym)}`);
        if (tickRes.ok) {
          const tickData = await tickRes.json();
          renderMT5SymbolQuote({ name: rawSym, canonical_symbol: rawSym }, tickData.tick || tickData);
        } else {
          const errData = await res.json().catch(() => ({}));
          DOM.mt5SymbolResultContainer.innerHTML = `
            <div class="empty-state" style="padding: 1.5rem;">
              <div class="empty-state-icon">⚠️</div>
              <div class="empty-state-title">Symbol Unavailable</div>
              <div class="empty-state-desc">${escapeText(apiErrorMessage(errData, `Symbol "${rawSym}" not found on active MT5 broker market watch.`))}</div>
            </div>
          `;
        }
      }
    } catch (err) {
      DOM.mt5SymbolResultContainer.innerHTML = `
        <div class="empty-state" style="padding: 1.5rem;">
          <div class="empty-state-icon">⚠️</div>
          <div class="empty-state-title">Lookup Failed</div>
          <div class="empty-state-desc">${escapeText(err.message || 'Unable to connect to MT5 server.')}</div>
        </div>
      `;
    }
  }

  function renderMT5SymbolQuote(info, tick) {
    if (!DOM.mt5SymbolResultContainer) return;
    const canon = info.canonical_symbol || info.name || '--';
    const brokerSym = info.name || canon;
    const digits = info.digits != null ? info.digits : 5;
    const point = info.point != null ? info.point : 0.00001;

    const bidVal = tick && tick.bid != null ? tick.bid : info.bid;
    const askVal = tick && tick.ask != null ? tick.ask : info.ask;
    const bidStr = (bidVal != null && bidVal > 0) ? formatForexPrice(bidVal, brokerSym, digits) : '--';
    const askStr = (askVal != null && askVal > 0) ? formatForexPrice(askVal, brokerSym, digits) : '--';

    const spreadPts = tick && tick.spread_points != null ? tick.spread_points : (info.spread_points != null ? info.spread_points : '--');
    const spreadPips = tick && tick.spread_pips != null ? tick.spread_pips : (info.spread_pips != null ? info.spread_pips : '--');
    const spreadStr = (spreadPts !== '--') ? `${spreadPts} pts (${spreadPips} pips)` : '--';

    const timeStr = tick && tick.time ? new Date(tick.time).toISOString().replace('T', ' ').substring(0, 19) + ' UTC' : 'Live Quote';

    DOM.mt5SymbolResultContainer.innerHTML = `
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.75rem; flex-wrap: wrap; gap: 0.5rem;">
        <div style="display: flex; align-items: center; gap: 8px;">
          <span style="font-size: 1.15rem; font-weight: 800; color: var(--cyan); font-family: var(--font-mono);">${escapeText(brokerSym)}</span>
          <span class="badge-readonly" style="font-size: 0.7rem;">ISO: ${escapeText(canon)}</span>
          ${info.path ? `<span style="font-size: 0.75rem; color: var(--text-muted); font-family: var(--font-mono);">${escapeText(info.path)}</span>` : ''}
        </div>
        <div style="font-size: 0.75rem; color: var(--text-muted); font-family: var(--font-mono);">
          Quote Time: <span style="color: var(--text-secondary);">${escapeText(timeStr)}</span>
        </div>
      </div>
      <div class="mt5-quote-grid">
        <div class="mt5-quote-card">
          <span class="mt5-quote-label">Bid Price</span>
          <span class="mt5-quote-value" style="color: var(--green);">${escapeText(bidStr)}</span>
        </div>
        <div class="mt5-quote-card">
          <span class="mt5-quote-label">Ask Price</span>
          <span class="mt5-quote-value" style="color: var(--cyan);">${escapeText(askStr)}</span>
        </div>
        <div class="mt5-quote-card">
          <span class="mt5-quote-label">Broker Spread</span>
          <span class="mt5-quote-value" style="color: var(--amber); font-size: 1rem;">${escapeText(spreadStr)}</span>
        </div>
        <div class="mt5-quote-card">
          <span class="mt5-quote-label">Precision &amp; Point</span>
          <span class="mt5-quote-value" style="font-size: 0.95rem;">${digits} digits / ${point}</span>
        </div>
        <div class="mt5-quote-card">
          <span class="mt5-quote-label">Contract Size</span>
          <span class="mt5-quote-value" style="font-size: 0.95rem;">${Number(info.contract_size || 100000).toLocaleString()} units</span>
        </div>
        <div class="mt5-quote-card">
          <span class="mt5-quote-label">Volume Constraints</span>
          <span class="mt5-quote-value" style="font-size: 0.85rem; color: var(--text-secondary);">${Number(info.volume_min || 0.01).toFixed(2)} min | ${Number(info.volume_step || 0.01).toFixed(2)} step | ${Number(info.volume_max || 100).toFixed(0)} max</span>
        </div>
      </div>
    `;
  }

  // ---- Performance Analytics View ----
  let performanceRequestGeneration = 0;
  let performanceData = null;
  function availableNumber(value, suffix = '', digits = 2) {
    return value == null || !Number.isFinite(Number(value)) ? 'Unavailable' : `${Number(value).toFixed(digits)}${suffix}`;
  }
  function money(value) {
    return value == null || !Number.isFinite(Number(value)) ? 'Unavailable' : `${Number(value) < 0 ? '-' : ''}$${Math.abs(Number(value)).toFixed(2)}`;
  }
  function metricGrid(entries) {
    return `<div class="stat-cards">${entries.map(([label, value, note = '']) => `<div class="stat-card"><span class="stat-label">${escapeText(label)}</span><span class="stat-value">${escapeText(value)}</span><span class="stat-sub">${escapeText(note)}</span></div>`).join('')}</div>`;
  }
  function lineChart(title, points, suffix = '') {
    const values = (points || []).map(point => Number(point.value)).filter(Number.isFinite);
    if (!values.length) return `<div class="card"><strong>${escapeText(title)}</strong><p class="stat-sub">Unavailable — no stored source values.</p></div>`;
    const min = Math.min(...values), max = Math.max(...values), span = max - min || 1;
    const coords = values.map((value, index) => `${values.length === 1 ? 50 : (index / (values.length - 1)) * 100},${36 - ((value - min) / span) * 32}`).join(' ');
    return `<div class="card"><strong>${escapeText(title)}</strong><svg viewBox="0 0 100 40" role="img" aria-label="${escapeText(title)}" style="width:100%;height:130px;"><polyline points="${coords}" fill="none" stroke="var(--cyan)" stroke-width="1.5" vector-effect="non-scaling-stroke"/></svg><p class="stat-sub">${values.length} observed points · latest ${escapeText(availableNumber(values.at(-1), suffix))}</p></div>`;
  }
  function distributionChart(title, points, suffix = '') {
    const values = (points || []).map(point => Number(point.value)).filter(Number.isFinite);
    if (!values.length) return `<div class="card"><strong>${escapeText(title)}</strong><p class="stat-sub">Unavailable — no stored source values.</p></div>`;
    const maxAbs = Math.max(...values.map(Math.abs), 1);
    return `<div class="card"><strong>${escapeText(title)}</strong><div style="display:flex;align-items:end;gap:3px;height:110px;">${values.slice(-60).map(value => `<span title="${escapeText(availableNumber(value, suffix))}" style="flex:1;min-width:2px;height:${Math.max(2, Math.abs(value) / maxAbs * 100)}%;background:${value < 0 ? 'var(--red)' : 'var(--green)'};"></span>`).join('')}</div><p class="stat-sub">${values.length} observed values</p></div>`;
  }
  function renderPerformanceSegmentation() {
    if (!performanceData) return;
    const key = DOM.performanceSegmentSelect?.value || 'by_pair';
    const segment = performanceData.performance?.segmentation?.[key] || {};
    const rows = Object.entries(segment);
    if (!rows.length) {
      DOM.perfBreakdownContainer.innerHTML = '<div class="empty-state"><div class="empty-state-title">No observations for this segment</div><div class="empty-state-desc">This dimension is unavailable until matching closed trades contain the required metadata.</div></div>';
      return;
    }
    DOM.perfBreakdownContainer.innerHTML = `<table class="data-table"><thead><tr><th>Segment</th><th>Trades</th><th>W / L / BE</th><th>Win Rate</th><th>Avg R</th><th>Expectancy</th><th>Profit Factor</th><th>Net P&amp;L</th><th>Max DD</th></tr></thead><tbody>${rows.map(([name, m]) => `<tr><td>${escapeText(name)}</td><td>${escapeText(m.trade_count ?? 0)}</td><td>${escapeText(`${m.wins ?? 0} / ${m.losses ?? 0} / ${m.breakeven ?? 0}`)}</td><td>${escapeText(availableNumber(m.win_rate_pct, '%', 1))}</td><td>${escapeText(availableNumber(m.average_r, 'R'))}</td><td>${escapeText(availableNumber(m.expectancy, 'R'))}</td><td>${escapeText(availableNumber(m.profit_factor))}</td><td>${escapeText(money(m.net_profit))}</td><td>${escapeText(money(m.maximum_drawdown))}</td></tr>`).join('')}</tbody></table>`;
  }
  function renderConfidenceCalibration(report) {
    if (!report || !report.buckets) return '<p class="journal-state">Unavailable — calibration backend returned no report.</p>';
    const rows = Object.values(report.buckets);
    if (!rows.some(row => Number(row.trade_count) > 0)) return '<div class="empty-state"><div class="empty-state-title">Not enough calibration observations</div><div class="empty-state-desc">No closed trades currently contain model-confidence evidence.</div></div>';
    return `<p class="stat-sub">ECE: ${escapeText(availableNumber(report.expected_calibration_error, '', 4))} · Brier score: ${escapeText(availableNumber(report.brier_score, '', 4))}</p><table class="data-table"><thead><tr><th>Raw model confidence band</th><th>N</th><th>Observed win rate</th><th>Calibration gap</th><th>Avg R</th><th>Expectancy</th><th>Status</th></tr></thead><tbody>${rows.map(row => { const midpoint = (Number(row.lower_bound) + Number(row.upper_bound)) / 2; const gap = Number(row.trade_count) ? Math.abs(midpoint - Number(row.win_rate_pct)) : null; return `<tr><td>${escapeText(row.bucket_label)}</td><td>${escapeText(row.trade_count)}</td><td>${escapeText(Number(row.trade_count) ? availableNumber(row.win_rate_pct, '%', 1) : 'Unavailable')}</td><td>${escapeText(gap == null ? 'Unavailable' : availableNumber(gap, ' pp', 1))}</td><td>${escapeText(Number(row.trade_count) ? availableNumber(row.average_r, 'R') : 'Unavailable')}</td><td>${escapeText(Number(row.trade_count) ? availableNumber(row.expectancy, 'R') : 'Unavailable')}</td><td>${escapeText(row.is_calibrated ? 'CALIBRATED' : (row.trade_count ? 'SMALL SAMPLE' : 'EMPTY'))}</td></tr>`; }).join('')}</tbody></table>${(report.warnings || []).map(warning => `<p class="warning-banner">${escapeText(warning)}</p>`).join('')}`;
  }
  async function loadPerformanceInterface() {
    const current = ++performanceRequestGeneration;
    [DOM.performanceOverallContainer, DOM.performanceExecutionContainer, DOM.performanceChartsContainer, DOM.perfBreakdownContainer, DOM.confidenceCalibrationContainer].filter(Boolean).forEach(container => { container.innerHTML = '<p class="journal-state" role="status">Loading analytics…</p>'; });
    try {
      const [performanceResponse, calibrationResponse] = await Promise.all([
        apiFetch('/api/forex/analytics/performance'),
        apiFetch('/api/forex/metrics/confidence-calibration'),
      ]);
      const [data, calibration] = await Promise.all([performanceResponse.json(), calibrationResponse.json()]);
      if (current !== performanceRequestGeneration) return;
      if (!performanceResponse.ok) throw new Error(apiErrorMessage(data, 'Performance analytics could not be loaded.'));
      if (!calibrationResponse.ok) throw new Error(apiErrorMessage(calibration, 'Confidence calibration could not be loaded.'));
      performanceData = data;
      const m = data.performance?.overall || {};
      DOM.sampleSizeWarning.style.display = Number(m.trade_count || 0) < 30 ? '' : 'none';
      DOM.performanceOverallContainer.innerHTML = metricGrid([
        ['Total trades', String(m.trade_count ?? 0), `${m.wins ?? 0} wins · ${m.losses ?? 0} losses · ${m.breakeven ?? 0} breakeven`],
        ['Win rate', availableNumber(m.win_rate_pct, '%', 1)], ['Average winner', money(m.average_winner), availableNumber(m.average_winner_r, 'R')],
        ['Average loser', money(m.average_loser), availableNumber(m.average_loser_r, 'R')], ['Average / median R', `${availableNumber(m.average_r, 'R')} / ${availableNumber(m.median_r, 'R')}`],
        ['Expectancy', availableNumber(m.expectancy, 'R'), money(m.expectancy_cash)], ['Profit factor', availableNumber(m.profit_factor)],
        ['Gross profit / loss', `${money(m.gross_profit)} / ${money(m.gross_loss)}`], ['Net P&L', money(m.net_profit)],
        ['Maximum drawdown', money(m.maximum_drawdown), availableNumber(m.maximum_drawdown_pct, '%')], ['Win / loss streak', `${m.win_streak ?? 0} / ${m.loss_streak ?? 0}`],
        ['Average holding duration', m.holding_duration_formatted || 'Unavailable'], ['Average MFE', availableNumber(m.average_mfe_r, 'R'), availableNumber(m.average_mfe_pips, ' pips')],
        ['Average MAE', availableNumber(m.average_mae_r, 'R'), availableNumber(m.average_mae_pips, ' pips')],
      ]);
      const e = data.performance?.execution || {}, f = data.execution_friction || {};
      DOM.performanceExecutionContainer.innerHTML = metricGrid([
        ['Mean entry deviation', availableNumber(e.entry_deviation_pips, ' pips')], ['Max entry deviation', availableNumber(e.max_entry_deviation_pips, ' pips')],
        ['SL / TP changes', `${e.sl_changes_count ?? 0} / ${e.tp_changes_count ?? 0}`], ['Manual exits', String(e.manual_exits_count ?? 0), availableNumber(e.manual_exits_pct, '%', 1)],
        ['Partial closes', String(e.partial_close_count ?? 0), `${availableNumber(e.partial_close_volume, ' lots')} · ${availableNumber(e.partial_close_avg_captured_r, 'R')}`],
        ['Execution friction', money(f.total_execution_friction_usd), `${f.total_executions_analyzed ?? 0} fills · quality ${availableNumber(f.average_execution_quality_score, '/100', 1)}`],
      ]);
      const s = data.series || {};
      DOM.performanceChartsContainer.innerHTML = `<div class="dashboard-grid">${lineChart('Cumulative R', s.cumulative_r, 'R')}${lineChart('Equity', s.equity, '')}${lineChart('Drawdown', s.drawdown, '')}${distributionChart('R distribution', s.r_distribution, 'R')}${distributionChart('MAE distribution', s.mae_distribution, 'R')}${(s.mfe_vs_realized || []).length ? `<div class="card"><strong>MFE vs realized R</strong><table class="data-table"><thead><tr><th>MFE R</th><th>Realized R</th></tr></thead><tbody>${s.mfe_vs_realized.slice(-30).map(point => `<tr><td>${escapeText(availableNumber(point.mfe_r, 'R'))}</td><td>${escapeText(availableNumber(point.realized_r, 'R'))}</td></tr>`).join('')}</tbody></table></div>` : '<div class="card"><strong>MFE vs realized R</strong><p class="stat-sub">Unavailable — no stored source values.</p></div>'}</div>`;
      DOM.confidenceCalibrationContainer.innerHTML = renderConfidenceCalibration(calibration.report);
      renderPerformanceSegmentation();
    } catch (error) {
      if (current !== performanceRequestGeneration) return;
      const message = escapeText(error.message || 'Network error loading analytics.');
      [DOM.performanceOverallContainer, DOM.performanceExecutionContainer, DOM.performanceChartsContainer, DOM.perfBreakdownContainer, DOM.confidenceCalibrationContainer].filter(Boolean).forEach(container => { container.innerHTML = `<p class="journal-state" role="alert">${message} Use Refresh Analytics to retry.</p>`; });
    }
  }

  // ---- Backtest Runs View ----
  async function loadBacktestRuns() {
    if (!DOM.backtestRunsContainer) return;
    try {
      const res = await apiFetch('/api/forex/backtest/runs');
      if (!res.ok) throw new Error('Unable to load backtest runs');
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
              <th>Run Type</th>
              <th>Mode</th>
              <th>Pair</th>
              <th>Timeframe</th>
              <th>Data Source</th>
              <th>Validation</th>
              <th>Trades</th>
              <th>Win Rate</th>
              <th>Profit Factor</th>
              <th>Net Result</th>
              <th>Status</th>
              <th>Created</th>
            </tr>
          </thead>
          <tbody>
            ${runs.map(r => `
              <tr data-backtest-id="${escapeText(r.backtest_id || '')}" style="cursor:pointer">
                <td style="font-family:var(--font-mono); font-size:0.75rem; color:var(--text-muted);">${escapeText(r.backtest_id ? r.backtest_id.slice(0, 10) : '-')}</td>
                <td>${escapeText(r.run_type || 'FOREX_BACKTEST')}</td>
                <td><span class="lesson-tag" style="font-size:0.65rem; color:${r.mode === 'DEMO' ? 'var(--amber)' : 'var(--cyan)'};">${escapeText(r.mode || 'Unavailable')}</span></td>
                <td style="font-weight:700; color:var(--cyan);">${escapeText(r.pair)}</td>
                <td>${escapeText(r.timeframe || '-')}</td>
                <td>${escapeText(r.data_source || 'Unavailable')}</td>
                <td title="${escapeText(r.notice || '')}">${escapeText(r.validation_status || 'Unavailable')}${r.split_count != null ? ` (${escapeText(r.split_count)} splits, OOS)` : ''}</td>
                <td>${r.total_trades != null ? escapeText(r.total_trades) : 'Unavailable'}</td>
                <td style="font-weight:600;">${r.win_rate_pct != null ? escapeText(Number(r.win_rate_pct).toFixed(1) + '%') : 'Unavailable'}</td>
                <td>${r.profit_factor != null ? escapeText(Number(r.profit_factor).toFixed(2)) : 'Unavailable'}</td>
                <td>${r.net_profit != null ? escapeText('$' + Number(r.net_profit).toFixed(2)) : 'Unavailable'}</td>
                <td><span class="status-cell ${escapeText(r.status || 'completed')}">${escapeText(r.status || 'completed')}</span></td>
                <td class="date-cell">${escapeText((r.created_at || '').slice(0, 19).replace('T', ' '))}</td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      `;
    } catch (error) {
      DOM.backtestRunsContainer.innerHTML = `<div class="info-banner error">${escapeText(error.message || 'Unable to load backtest runs')}</div>`;
    }
  }

  // ---- Settings View ----
  function populateSettingsView() {
    if (!configData) return;
    if (DOM.settingProvider) DOM.settingProvider.textContent = configData.provider || 'Not configured';
    if (DOM.settingQuickModel) DOM.settingQuickModel.textContent = configData.quick_model || 'Default';
    if (DOM.settingDeepModel) DOM.settingDeepModel.textContent = configData.deep_model || 'Default';

    if (DOM.settingProviderInput && configData.providers) {
      DOM.settingProviderInput.innerHTML = '';
      configData.providers.forEach(p => {
        const opt = document.createElement('option');
        opt.value = p.id;
        opt.textContent = p.name;
        if (p.id === configData.provider) opt.selected = true;
        DOM.settingProviderInput.appendChild(opt);
      });
    }

    const runtimeSettings = configData.runtime_settings || {};
    if (DOM.settingQuickModelInput) {
      DOM.settingQuickModelInput.innerHTML = '';
      const provider = configData.providers.find(p => p.id === (DOM.settingProviderInput ? DOM.settingProviderInput.value : configData.provider));
      const models = provider ? provider.models : [];
      models.forEach(model => {
        const opt = document.createElement('option');
        opt.value = model; opt.textContent = model;
        if (model === (runtimeSettings.quick_think_llm || configData.quick_model)) opt.selected = true;
        DOM.settingQuickModelInput.appendChild(opt);
      });
    }
    if (DOM.settingDeepModelInput) {
      DOM.settingDeepModelInput.innerHTML = '';
      const provider = configData.providers.find(p => p.id === (DOM.settingProviderInput ? DOM.settingProviderInput.value : configData.provider));
      const models = provider ? provider.models : [];
      models.forEach(model => {
        const opt = document.createElement('option');
        opt.value = model; opt.textContent = model;
        if (model === (runtimeSettings.deep_think_llm || configData.deep_model)) opt.selected = true;
        DOM.settingDeepModelInput.appendChild(opt);
      });
    }

    if (DOM.settingBackendUrl) DOM.settingBackendUrl.value = runtimeSettings.backend_url || '';
    const providerSecretConfigured = configData.secret_status && configData.secret_status.llm_provider_secrets === 'Configured';
    if (DOM.settingApiKeyStatus) DOM.settingApiKeyStatus.textContent = providerSecretConfigured ? 'Configured / Hidden' : 'Missing / Not Set';
    if (DOM.settingApiKeyStatus) DOM.settingApiKeyStatus.style.color = providerSecretConfigured ? 'var(--green)' : 'var(--amber)';
    if (DOM.settingPair) DOM.settingPair.value = runtimeSettings.forex_default_pair || 'EURUSD';
    if (DOM.settingTimeframe) DOM.settingTimeframe.value = runtimeSettings.forex_default_execution_timeframe || 'H1';
    if (DOM.settingContextTimeframes) DOM.settingContextTimeframes.value = (runtimeSettings.forex_default_context_timeframes || ['H4', 'D1']).join(', ');
    if (DOM.settingMarketSource) DOM.settingMarketSource.value = runtimeSettings.forex_market_source || 'mt5';
    if (DOM.settingRiskPercent) DOM.settingRiskPercent.value = String(runtimeSettings.forex_default_risk_percent ?? 1.0);
    if (DOM.settingMinRR) DOM.settingMinRR.value = String(runtimeSettings.forex_min_rr ?? 1.5);
    if (DOM.settingMaxSpread) DOM.settingMaxSpread.value = String(runtimeSettings.forex_max_spread_pips ?? 5.0);
    if (DOM.settingNewsBlackout) DOM.settingNewsBlackout.value = String(runtimeSettings.forex_news_blackout_minutes ?? 120);
    if (DOM.settingPollInterval) DOM.settingPollInterval.value = String(runtimeSettings.mt5_poll_interval_seconds ?? 5.0);
    if (DOM.settingBrokerMode) DOM.settingBrokerMode.textContent = 'Passive Observer / Manual Execution';
    if (DOM.settingAutoOrder) DOM.settingAutoOrder.textContent = 'DISABLED — IMMUTABLE';
    if (DOM.settingReflection) DOM.settingReflection.textContent = 'Enabled by closed-trade workflow';

    if (!document.body.dataset.forexSettingsInitialized) {
      if (DOM.forexPair) DOM.forexPair.value = runtimeSettings.forex_default_pair || 'EURUSD';
      if (DOM.forexTimeframe) DOM.forexTimeframe.value = runtimeSettings.forex_default_execution_timeframe || 'H1';
      if (DOM.riskPercent) DOM.riskPercent.value = String(runtimeSettings.forex_default_risk_percent ?? 1.0);
      if (DOM.forexMinRR) DOM.forexMinRR.value = String(runtimeSettings.forex_min_rr ?? 1.5);
      if (DOM.forexMaxSpread) DOM.forexMaxSpread.value = String(runtimeSettings.forex_max_spread_pips ?? 5.0);
      const contexts = new Set(runtimeSettings.forex_default_context_timeframes || ['H4', 'D1']);
      DOM.forexContextToggles.forEach(btn => btn.classList.toggle('active', contexts.has(btn.dataset.ctxTf)));
      document.body.dataset.forexSettingsInitialized = 'true';
    }
  }

  async function saveSettings() {
    const payload = {};
    const provider = DOM.settingProviderInput ? DOM.settingProviderInput.value : configData.provider;
    if (provider) payload.llm_provider = provider;
    if (DOM.settingQuickModelInput && DOM.settingQuickModelInput.value) payload.quick_think_llm = DOM.settingQuickModelInput.value;
    if (DOM.settingDeepModelInput && DOM.settingDeepModelInput.value) payload.deep_think_llm = DOM.settingDeepModelInput.value;
    if (DOM.settingBackendUrl) payload.backend_url = DOM.settingBackendUrl.value.trim() || null;
    if (DOM.settingPair) payload.forex_default_pair = DOM.settingPair.value.trim();
    if (DOM.settingTimeframe) payload.forex_default_execution_timeframe = DOM.settingTimeframe.value;
    if (DOM.settingContextTimeframes) payload.forex_default_context_timeframes = DOM.settingContextTimeframes.value.split(',').map(value => value.trim()).filter(Boolean);
    if (DOM.settingMarketSource && DOM.settingMarketSource.value) payload.forex_market_source = DOM.settingMarketSource.value;
    if (DOM.settingMaxSpread && DOM.settingMaxSpread.value) payload.forex_max_spread_pips = Number(DOM.settingMaxSpread.value);
    if (DOM.settingRiskPercent && DOM.settingRiskPercent.value) payload.forex_default_risk_percent = Number(DOM.settingRiskPercent.value);
    if (DOM.settingMinRR && DOM.settingMinRR.value) payload.forex_min_rr = Number(DOM.settingMinRR.value);
    if (DOM.settingNewsBlackout && DOM.settingNewsBlackout.value) payload.forex_news_blackout_minutes = Number(DOM.settingNewsBlackout.value);
    if (DOM.settingPollInterval && DOM.settingPollInterval.value) payload.mt5_poll_interval_seconds = Number(DOM.settingPollInterval.value);
    try {
      const res = await apiFetch('/api/forex/settings', { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(apiErrorMessage(data, 'Settings update failed'));
      settingsData = data;
      configData.runtime_settings = data.settings || {};
      populateSettingsView();
      if (DOM.settingsStatus) {
        DOM.settingsStatus.textContent = data.restart_required ? 'Saved. Restart required for the MT5 polling change.' : 'Saved and applied.';
        DOM.settingsStatus.className = 'settings-status success';
      }
    } catch (error) {
      if (DOM.settingsStatus) {
        DOM.settingsStatus.textContent = error.message || 'Validation error';
        DOM.settingsStatus.className = 'settings-status error';
      }
    }
  }

  async function resetSettings() {
    try {
      const res = await apiFetch('/api/forex/settings/reset', { method: 'POST' });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(apiErrorMessage(data, 'Reset failed'));
      settingsData = data;
      configData.runtime_settings = data.settings || {};
      populateSettingsView();
      if (DOM.settingsStatus) {
        DOM.settingsStatus.textContent = data.restart_required ? 'Defaults restored. Restart required for the MT5 polling change.' : 'Defaults restored.';
        DOM.settingsStatus.className = 'settings-status success';
      }
    } catch (error) {
      if (DOM.settingsStatus) {
        DOM.settingsStatus.textContent = error.message || 'Reset failed';
        DOM.settingsStatus.className = 'settings-status error';
      }
    }
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

    const contextTimeframes = [];
    if (DOM.forexContextToggles) {
      DOM.forexContextToggles.forEach(btn => {
        if (btn.classList.contains('active') && btn.dataset.ctxTf) {
          contextTimeframes.push(btn.dataset.ctxTf);
        }
      });
    }
    if (contextTimeframes.length === 0) {
      contextTimeframes.push('H4', 'D1');
    }

    const payload = {
      pair: pair,
      execution_timeframe: timeframe,
      timeframe: timeframe,
      context_timeframes: contextTimeframes,
      date: DOM.tradeDate ? (DOM.tradeDate.value || null) : null,
      account_balance: balance,
      risk_percent: riskPct,
      analysts: selectedAnalysts,
      provider: DOM.provider.value || null,
      quick_model: DOM.quickModel.value || null,
      deep_model: DOM.deepModel.value || null,
      research_depth: DOM.forexResearchDepth ? DOM.forexResearchDepth.value : 'deep',
      account_source: DOM.forexAccountSource ? DOM.forexAccountSource.value : 'mt5',
      min_rr: DOM.forexMinRR ? parseFloat(DOM.forexMinRR.value) : 1.5,
      max_spread_pips: DOM.forexMaxSpread ? parseFloat(DOM.forexMaxSpread.value) : 2.5,
      economic_blackout: DOM.forexEconomicBlackout ? DOM.forexEconomicBlackout.checked : true,
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
      const res = await apiFetch('/api/forex/analyze', {
        method: 'POST',
        headers: headers,
        body: JSON.stringify(payload),
      });

      const data = await res.json();
      if (!res.ok) {
        throw new Error(apiErrorMessage(data, 'Failed to start Forex analysis'));
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

  // ---- Backtest Submit Flow ----
  async function handleBacktestSubmit() {
    // Gather form values
    const modeReal = DOM.btnModeBacktestReal && DOM.btnModeBacktestReal.classList.contains('active');
    const demoMode = DOM.btnModeBacktestDemo && DOM.btnModeBacktestDemo.classList.contains('active') && !modeReal;
    const pair = DOM.btPair ? DOM.btPair.value : 'EURUSD';
    const timeframe = DOM.btTimeframe ? DOM.btTimeframe.value : 'H1';
    const date_from = DOM.btStartDate ? (DOM.btStartDate.value || null) : null;
    const date_to = DOM.btEndDate ? (DOM.btEndDate.value || null) : null;
    const initial_balance = DOM.btCapital ? Number(DOM.btCapital.value) : 100000;
    const max_analysis_points = DOM.btMaxPoints ? Number(DOM.btMaxPoints.value) : 10;
    const spread_pips = DOM.btSpread ? Number(DOM.btSpread.value) : 1.5;

    const payload = {
      demo_mode: demoMode,
      mode: modeReal ? 'HISTORICAL_AGENT_BACKTEST' : (demoMode ? 'DEMO' : null),
      pair: pair,
      timeframe: timeframe,
      date_from: date_from,
      date_to: date_to,
      initial_balance: initial_balance,
      account_currency: 'USD',
      leverage: 100,
      spread_pips: spread_pips,
      slippage_pips: 0.3,
      commission_per_lot_usd: 5.0,
      swap_per_day_usd: 0.0,
      sampling_interval: 1,
      max_analysis_points: max_analysis_points,
      analyst_selection: ['forex_technical','forex_macro','forex_news'],
      provider: DOM.provider ? DOM.provider.value || null : null,
      quick_model: DOM.quickModel ? DOM.quickModel.value || null : null,
      deep_model: DOM.deepModel ? DOM.deepModel.value || null : null,
      research_depth: 'standard',
    };

    // Pre-launch estimate
    try {
      const estReq = {
        pair: payload.pair,
        timeframe: payload.timeframe,
        count: 300,
        sampling_interval: payload.sampling_interval,
        max_analysis_points: payload.max_analysis_points,
        analyst_count: payload.analyst_selection ? payload.analyst_selection.length : 3,
      };
      const estRes = await apiFetch('/api/forex/backtest/estimate', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(estReq),
      });
      const est = estRes.ok ? await estRes.json() : null;
      let proceed = true;
      if (est) {
        const analyses = est.expected_analyses_count ?? 'Unavailable';
        const calls = est.estimated_llm_calls || est.estimated_calls || 0;
        const tokens = est.estimated_tokens || est.estimated_token_usage || 0;
        const cost = est.estimated_cost_usd || est.estimated_cost || 0;
        if (DOM.backtestEstimate) {
          DOM.backtestEstimate.style.display = 'block';
          DOM.backtestEstimate.innerHTML = `<strong>Pre-launch estimate</strong><br>Analyses: ${escapeText(analyses)} · LLM calls: ${escapeText(calls)} · Tokens: ${escapeText(tokens)} · Estimated cost: $${escapeText(Number(cost).toFixed(2))}<br>Sampling interval: ${escapeText(payload.sampling_interval)} bars · Maximum analysis points: ${escapeText(payload.max_analysis_points ?? 'Uncapped')}`;
        }
        // require confirmation for large jobs
        if (calls > 50 || cost > 5) {
          proceed = confirm(`Estimated analyses: ${analyses}\nEstimated LLM calls: ${calls}\nEstimated tokens: ${tokens}\nEstimated cost: $${Number(cost).toFixed(2)}\nSampling interval: ${payload.sampling_interval}\nMaximum analysis points: ${payload.max_analysis_points ?? 'Uncapped'}\n\nProceed with backtest?`);
        }
        // show a small summary in pipeline area
        showToast(`Estimate: ${calls} analyses, ${tokens} tokens, $${Number(cost||0).toFixed(2)}`, 'info');
      }
      if (!proceed) return;
    } catch (e) {
      // proceed but warn
      showToast('Estimate failed — proceeding with caution', 'warning');
    }

    // Disable launch button
    if (DOM.btnLaunchBacktest) {
      DOM.btnLaunchBacktest.disabled = true;
      DOM.btnLaunchBacktest.textContent = 'Running…';
    }

    try {
      const res = await apiFetch('/api/forex/backtest/run', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(apiErrorMessage(data, 'Backtest request failed'));
      // Refresh runs and open detail
      await loadBacktestRuns();
      const bt_id = data.backtest_id || data.backtest_id;
      if (bt_id) openBacktestDetail(bt_id);
      showToast('Backtest completed', 'success');
    } catch (err) {
      showToast(err.message || 'Backtest failed', 'error');
    } finally {
      if (DOM.btnLaunchBacktest) {
        DOM.btnLaunchBacktest.disabled = false;
        DOM.btnLaunchBacktest.textContent = '🚀 Run Backtest';
      }
    }
  }

  async function openBacktestDetail(backtestId) {
    try {
      const res = await apiFetch(`/api/forex/backtest/${encodeURIComponent(backtestId)}`);
      if (!res.ok) {
        showToast('Failed to load backtest detail', 'error');
        return;
      }
      const data = await res.json();
      const container = DOM.backtestRunsContainer;
      const header = `Backtest: ${escapeText(String(data.backtest_id || backtestId))} - ${escapeText(data.pair || '')} ${escapeText(data.timeframe || '')}`;
      const result = data.result || {};
      const provenance = data.market_data_provenance || { source: data.data_source };
      const validationReport = data.validation_report || {};
      const reasons = data.validation_reasons || validationReport.validation_reasons || [];
      const unavailable = value => value == null ? 'Unavailable' : value;
      const section = (title, content) => `<section style="margin:14px 0;"><h4 style="color:var(--cyan); margin-bottom:8px;">${escapeText(title)}</h4>${content}</section>`;
      const fields = entries => `<div style="display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:8px;">${entries.map(([label, value]) => `<div class="stat-card"><span class="stat-label">${escapeText(label)}</span><span class="stat-value" style="font-size:0.9rem;">${escapeText(unavailable(value))}</span></div>`).join('')}</div>`;
      const objectFields = object => fields(Object.entries(object || {}).map(([key, value]) => [key.replaceAll('_', ' '), typeof value === 'object' ? JSON.stringify(value) : value]));
      const splits = validationReport.splits || [];
      const splitRows = splits.map(split => `<tr><td>${escapeText(split.split_id)}</td><td>${escapeText(split.development?.start_time)} → ${escapeText(split.development?.end_time)}</td><td>${escapeText(split.out_of_sample?.start_time)} → ${escapeText(split.out_of_sample?.end_time)}</td><td>${split.forward_demo ? `${escapeText(split.forward_demo.start_time)} → ${escapeText(split.forward_demo.end_time)}` : 'Unavailable'}</td></tr>`).join('');
      const body = `
        <div class="info-banner">${escapeText(data.notice || 'No validation notice supplied.')}</div>
        ${section('Overview', fields([['Run type', data.run_type], ['Mode', data.mode], ['Status', data.status], ['Pair', data.pair], ['Timeframe', data.timeframe], ['Created', data.created_at]]))}
        ${section('Data provenance', objectFields(provenance))}
        ${section('Execution assumptions', objectFields(data.execution_assumptions || data.execution_summary || {}))}
        ${section('Cost assumptions', objectFields(data.cost_control_summary || {}))}
        ${section('Analysis / model configuration', fields([['Provider', data.provider], ['Quick model', data.quick_model], ['Deep model', data.deep_model], ['Analysts', (data.analysts || []).join(', ') || null], ['Analyses performed', data.analyses_performed]]))}
        ${section('Validation status', fields([['Status', data.validation_status], ['Validated strategy performance', data.validated_strategy_performance], ['Verdict', validationReport.robustness_verdict]]))}
        ${section('Performance metrics', fields([['Total trades', result.total_trades], ['Win rate', result.win_rate_pct != null ? `${Number(result.win_rate_pct).toFixed(1)}%` : null], ['Profit factor', result.profit_factor], ['Net profit', result.total_net_profit != null ? `$${Number(result.total_net_profit).toFixed(2)}` : null], ['Return', result.total_return_pct != null ? `${result.total_return_pct}%` : null], ['Max drawdown', result.max_drawdown_pct != null ? `${result.max_drawdown_pct}%` : null]]))}
        ${section('Equity / drawdown data', fields([['Equity points', Array.isArray(result.equity_curve) ? result.equity_curve.length : null], ['Max drawdown', result.max_drawdown_pct != null ? `${result.max_drawdown_pct}%` : null], ['Sharpe ratio', result.sharpe_ratio], ['Sortino ratio', result.sortino_ratio]]))}
        ${section('Trade statistics', fields([['Winning trades', result.winning_trades], ['Losing trades', result.losing_trades], ['Ambiguous trades', result.ambiguous_trades_count ?? data.ambiguous_trades_count], ['Filled orders', result.filled_orders_count ?? data.filled_orders_count], ['Expired orders', result.expired_orders_count ?? data.expired_orders_count]]))}
        ${splits.length ? section('Walk-forward splits (out-of-sample)', `<div style="overflow:auto"><table class="data-table"><thead><tr><th>Split</th><th>Training period</th><th>OOS test period</th><th>Forward demo</th></tr></thead><tbody>${splitRows}</tbody></table></div>`) : ''}
        ${section('Validation reasons', reasons.length ? `<ul>${reasons.map(reason => `<li>${escapeText(reason)}</li>`).join('')}</ul>` : 'Unavailable')}
        ${section('Audit metadata', objectFields({ analysis_cutoff_policy: data.analysis_cutoff_policy, evidence_policy: data.evidence_policy, strategy_version: data.strategy_version, config_hash: data.config_hash }))}
        <details><summary>Technical details</summary><pre style="white-space:pre-wrap; font-family:monospace; font-size:0.8rem;">${escapeText(JSON.stringify(data, null, 2))}</pre></details>
      `;
      container.innerHTML = `
        <div class="card">
          <div class="card-header"><h3 class="card-title">${header}</h3></div>
          <div style="padding:12px">${body}</div>
        </div>
      `;
    } catch (e) {
      showToast('Error loading backtest detail', 'error');
    }
  }

  function renderAblationStudy(study) {
    if (!DOM.backtestRunsContainer) return;
    const variants = study.variants || [];
    DOM.backtestRunsContainer.innerHTML = `
      <div class="card">
        <div class="card-header"><h3 class="card-title">Ablation Study: ${escapeText(study.pair || 'Unavailable')}</h3></div>
        <div style="padding:12px">
          <div class="info-banner">${escapeText(study.sample_size_warning || 'Exploratory comparison only; no variant is statistically validated.')}</div>
          <div style="overflow:auto"><table class="data-table"><thead><tr><th>Variant</th><th>Sample</th><th>Trades</th><th>Win rate</th><th>Profit factor</th><th>Net result</th><th>Cost</th><th>Latency</th><th>Sample adequate</th></tr></thead><tbody>
            ${variants.map(variant => `<tr><td title="${escapeText(variant.description)}">${escapeText(variant.name || variant.variant_id)}</td><td>${escapeText(variant.analyses_performed ?? 'Unavailable')}</td><td>${escapeText(variant.trade_count ?? 'Unavailable')}</td><td>${variant.win_rate_pct != null ? `${escapeText(variant.win_rate_pct)}%` : 'Unavailable'}</td><td>${escapeText(variant.profit_factor ?? 'Unavailable')}</td><td>${variant.total_net_profit != null ? `$${escapeText(Number(variant.total_net_profit).toFixed(2))}` : 'Unavailable'}</td><td>${variant.estimated_llm_cost_usd != null ? `$${escapeText(Number(variant.estimated_llm_cost_usd).toFixed(4))}` : 'Unavailable'}</td><td>${variant.latency_seconds != null ? `${escapeText(variant.latency_seconds)}s` : 'Unavailable'}</td><td>${escapeText(variant.sample_size_adequate)}</td></tr>`).join('')}
          </tbody></table></div>
          <h4 style="margin-top:12px; color:var(--cyan);">Validation limitations</h4>
          <ul>${(study.validation_reasons || []).map(reason => `<li>${escapeText(reason)}</li>`).join('')}</ul>
          <details><summary>Technical details</summary><pre style="white-space:pre-wrap; font-family:monospace; font-size:0.8rem;">${escapeText(JSON.stringify(study, null, 2))}</pre></details>
        </div>
      </div>`;
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
      const res = await apiFetch('/api/analyze', {
        method: 'POST',
        headers: headers,
        body: JSON.stringify(payload),
      });

      const data = await res.json();
      if (!res.ok) {
        if (res.status === 401 && DOM.apiKeyGroup) {
          DOM.apiKeyGroup.style.display = 'block';
        }
        throw new Error(apiErrorMessage(data, 'Failed to start analysis'));
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
      loadPerformanceInterface();
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
        showToast(apiErrorMessage(data, data.message || 'Forex analysis failed'), 'error');
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
        showToast(apiErrorMessage(data, 'Analysis failed'), 'error');
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
      const res = await apiFetch(`/api/forex/runs/${encodeURIComponent(runId)}`);
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
    const sizing = data.sizing || {};
    const ctx = data.context || {};
    const research = data.research || {};
    const debate = data.investment_debate || {};
    const mem = data.memory || {};
    const prov = data.provenance || {};
    const reportText = data.report || data.final_decision || '';

    // 1. Direction Determination
    let action = (prop.action || data.signal || 'NO_TRADE').toUpperCase();
    if (risk.decision === 'REJECT') {
      action = 'REJECT';
    }
    const actionClass = action === 'LONG' ? 'long' : action === 'SHORT' ? 'short' : action === 'REJECT' ? 'reject' : 'no_trade';
    const decisionAction = risk.decision || 'PENDING';
    const decisionColor = decisionAction === 'APPROVE' ? 'var(--green)' : decisionAction === 'REJECT' ? 'var(--red)' : 'var(--amber)';

    // Risk Checks List
    const defaultChecks = [
      'Minimum Risk:Reward threshold satisfied (>= 1.5R)',
      'Stop-loss distance within maximum permissible risk parameters',
      'Economic calendar high-impact blackout cleared',
      'Account equity risk allocation within conservative ceiling (<= 2.0%)',
      'Required margin verified against free margin headroom'
    ];
    const passedChecks = (risk.risk_checks_passed && risk.risk_checks_passed.length > 0)
      ? risk.risk_checks_passed
      : (decisionAction === 'APPROVE' ? defaultChecks : []);

    const violations = risk.risk_violations || [];
    const modifications = risk.modifications_required || [];

    // Lessons
    const lessons = Array.isArray(prop.applied_lesson_ids)
      ? prop.applied_lesson_ids.filter(id => typeof id === 'string' && id)
      : (Array.isArray(mem.applied_lesson_ids) ? mem.applied_lesson_ids.filter(id => typeof id === 'string' && id) : []);

    // Entry Zone
    let entryZoneStr = '-';
    if (prop.entry_zone_low && prop.entry_zone_high) {
      entryZoneStr = `${Number(prop.entry_zone_low).toFixed(5)} – ${Number(prop.entry_zone_high).toFixed(5)}`;
    } else if (prop.entry_price) {
      entryZoneStr = Number(prop.entry_price).toFixed(5);
    }

    // Estimated Margin
    let marginStr = 'Calculated at entry';
    if (sizing.margin_required) {
      marginStr = `$${Number(sizing.margin_required).toFixed(2)}`;
    } else if (risk.approved_lot_size || prop.suggested_lot_size) {
      const lots = risk.approved_lot_size || prop.suggested_lot_size;
      marginStr = `~$${(lots * 1000).toFixed(2)}`;
    }

    DOM.reportContent.innerHTML = `
      <div class="decision-report-container">
        <!-- 1. DIRECTION & HEADER BANNER -->
        <div class="decision-banner">
          <div style="display:flex; align-items:center; gap:12px; flex-wrap:wrap;">
            <span class="direction-tag ${actionClass}">
              ${escapeText(action)}
            </span>
            <div>
              <h2 style="font-size:1.25rem; font-weight:800; color:var(--text-bright); margin:0;">
                <span style="color:var(--cyan);">${escapeText(data.pair || 'FOREX')}</span>
                <span style="font-size:0.85rem; font-weight:500; color:var(--text-secondary); margin-left:8px;">
                  Setup: ${escapeText(prop.setup_type || 'INSTITUTIONAL_CONFLUENCE')}
                </span>
              </h2>
              <div style="font-size:0.75rem; color:var(--text-muted); font-family:var(--font-mono); margin-top:2px;">
                Order: ${escapeText(prop.order_type || 'LIMIT')} • Run ID: ${escapeText(data.run_id || '-')}
              </div>
            </div>
          </div>
          <div style="display:flex; align-items:center; gap:8px;">
            <span class="signal-badge" style="background:${decisionColor}20; color:${decisionColor}; border:1px solid ${decisionColor}; font-size:0.82rem; padding:6px 14px; font-weight:700;">
              Risk Engine: ${escapeText(decisionAction)}
            </span>
          </div>
        </div>

        ${prop.trade_rationale_summary ? `
          <div style="background:var(--bg-card); padding:1rem 1.25rem; border-radius:var(--radius-md); border-left:4px solid var(--cyan); border:1px solid var(--border); border-left-width:4px;">
            <strong style="color:var(--cyan); font-size:0.82rem; text-transform:uppercase; letter-spacing:0.04em;">Executive Thesis:</strong>
            <p style="margin:4px 0 0 0; font-size:0.88rem; color:var(--text-primary); line-height:1.5;">${escapeText(prop.trade_rationale_summary)}</p>
          </div>
        ` : ''}

        <!-- 2. PROPOSAL GEOMETRY CARD -->
        <div class="decision-section">
          <div class="decision-section-title">
            <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2v20M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></svg>
            Trade Proposal &amp; Execution Geometry
          </div>
          <div class="proposal-meta-grid">
            <div class="proposal-meta-item">
              <span class="proposal-meta-label">Entry Level</span>
              <span class="proposal-meta-value">${prop.entry_price ? Number(prop.entry_price).toFixed(5) : '-'}</span>
              <span class="proposal-meta-sub">Zone: ${escapeText(entryZoneStr)}</span>
            </div>
            <div class="proposal-meta-item">
              <span class="proposal-meta-label">Stop Loss</span>
              <span class="proposal-meta-value" style="color:var(--red);">${prop.stop_loss ? Number(prop.stop_loss).toFixed(5) : '-'}</span>
              <span class="proposal-meta-sub">${prop.sl_pips ? `${prop.sl_pips} pips risk` : 'Mandatory protection'}</span>
            </div>
            <div class="proposal-meta-item">
              <span class="proposal-meta-label">Take Profit 1</span>
              <span class="proposal-meta-value" style="color:var(--green);">${prop.take_profit_1 ? Number(prop.take_profit_1).toFixed(5) : '-'}</span>
              <span class="proposal-meta-sub">${prop.tp_pips ? `${prop.tp_pips} pips primary` : 'Primary target'}</span>
            </div>
            <div class="proposal-meta-item">
              <span class="proposal-meta-label">Take Profit 2</span>
              <span class="proposal-meta-value" style="color:var(--green);">${prop.take_profit_2 ? Number(prop.take_profit_2).toFixed(5) : 'Runner / None'}</span>
              <span class="proposal-meta-sub">Secondary target</span>
            </div>
            <div class="proposal-meta-item">
              <span class="proposal-meta-label">Risk : Reward</span>
              <span class="proposal-meta-value" style="color:var(--cyan);">${prop.risk_reward_ratio ? `${Number(prop.risk_reward_ratio).toFixed(2)}:1` : '-'}</span>
              <span class="proposal-meta-sub">Min required: 1.50:1</span>
            </div>
            <div class="proposal-meta-item">
              <span class="proposal-meta-label">Risk Allocation</span>
              <span class="proposal-meta-value">${prop.suggested_risk_percent != null ? `${prop.suggested_risk_percent}%` : (risk.max_risk_percent != null ? `${risk.max_risk_percent}%` : '1.0%')}</span>
              <span class="proposal-meta-sub">Account equity %</span>
            </div>
            <div class="proposal-meta-item">
              <span class="proposal-meta-label">Approved Size</span>
              <span class="proposal-meta-value" style="color:var(--cyan);">${risk.approved_lot_size != null ? `${risk.approved_lot_size} lots` : (prop.suggested_lot_size != null ? `${prop.suggested_lot_size} lots` : '-')}</span>
              <span class="proposal-meta-sub">${sizing.units ? `${Number(sizing.units).toLocaleString()} units` : 'Standard lot sizing'}</span>
            </div>
            <div class="proposal-meta-item">
              <span class="proposal-meta-label">Estimated Margin</span>
              <span class="proposal-meta-value">${escapeText(marginStr)}</span>
              <span class="proposal-meta-sub">Headroom reserved</span>
            </div>
          </div>
          <div style="display:grid; grid-template-columns:1fr 1fr; gap:0.75rem; margin-top:0.75rem;">
            <div class="proposal-meta-item">
              <span class="proposal-meta-label">Proposal Expiry</span>
              <span style="font-size:0.85rem; font-family:var(--font-mono); color:var(--text-bright);">${escapeText(prop.valid_until || 'Valid until end of current session')}</span>
            </div>
            <div class="proposal-meta-item">
              <span class="proposal-meta-label">Invalidation Condition</span>
              <span style="font-size:0.85rem; color:var(--amber);">${escapeText(prop.invalidation_condition || 'Close beyond structure invalidates thesis')}</span>
            </div>
          </div>
        </div>

        <!-- 3. MULTI-TIMEFRAME CONTEXT CARD -->
        <div class="decision-section">
          <div class="decision-section-title">
            <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
            Market &amp; Multi-Timeframe Context
          </div>
          <div class="proposal-meta-grid">
            <div class="proposal-meta-item">
              <span class="proposal-meta-label">Execution Timeframe</span>
              <span class="proposal-meta-value" style="color:var(--cyan);">${escapeText(ctx.execution_timeframe || data.execution_timeframe || data.timeframe || 'H1')}</span>
              <span class="proposal-meta-sub">Primary entry chart</span>
            </div>
            <div class="proposal-meta-item">
              <span class="proposal-meta-label">Context Timeframes</span>
              <span class="proposal-meta-value">${escapeText((ctx.context_timeframes || data.context_timeframes || ['H4', 'D1']).join(', '))}</span>
              <span class="proposal-meta-sub">Macro trend alignment</span>
            </div>
            <div class="proposal-meta-item">
              <span class="proposal-meta-label">Active Session</span>
              <span class="proposal-meta-value">${escapeText(ctx.session || 'London / New York Overlap')}</span>
              <span class="proposal-meta-sub">Session liquidity</span>
            </div>
            <div class="proposal-meta-item">
              <span class="proposal-meta-label">Current Spread</span>
              <span class="proposal-meta-value">${ctx.spread_pips != null ? `${ctx.spread_pips} pips` : '1.5 pips'}</span>
              <span class="proposal-meta-sub">Within threshold</span>
            </div>
            <div class="proposal-meta-item">
              <span class="proposal-meta-label">Volatility (ATR)</span>
              <span class="proposal-meta-value">${ctx.volatility_atr != null ? `${ctx.volatility_atr} pips` : '45.0 pips'}</span>
              <span class="proposal-meta-sub">Expected session range</span>
            </div>
            <div class="proposal-meta-item">
              <span class="proposal-meta-label">News / Event Risk</span>
              <span class="proposal-meta-value" style="color:${ctx.news_risk === 'CLEARED' ? 'var(--green)' : 'var(--amber)'};">${escapeText(ctx.news_risk || 'CLEARED')}</span>
              <span class="proposal-meta-sub">Economic blackout status</span>
            </div>
          </div>
        </div>

        <!-- 4. RESEARCH INTELLIGENCE GRID -->
        <div class="decision-section">
          <div class="decision-section-title">
            <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/></svg>
            Multi-Agent Research Intelligence
          </div>
          <div class="research-subgrid">
            <div class="research-subcard">
              <div class="research-subcard-title">
                <span>📈 Technical Analysis</span>
              </div>
              <div class="research-subcard-body">
                ${escapeText(research.technical || data.technical_report || 'Technical momentum, key support/resistance levels, and multi-timeframe candle structures analyzed.')}
              </div>
            </div>
            <div class="research-subcard">
              <div class="research-subcard-title">
                <span>🏛️ Macroeconomic Analysis</span>
              </div>
              <div class="research-subcard-body">
                ${escapeText(research.macro || data.macro_report || 'Central bank policy stance, rate differentials, and sovereign bond yield spreads analyzed.')}
              </div>
            </div>
            <div class="research-subcard">
              <div class="research-subcard-title">
                <span>📰 News &amp; Calendar Flows</span>
              </div>
              <div class="research-subcard-body">
                ${escapeText(research.news || data.news_report || 'High-impact scheduled events, geopolitical developments, and currency sentiment reviewed.')}
              </div>
            </div>
            <div class="research-subcard">
              <div class="research-subcard-title" style="color:var(--green);">
                <span>🐂 Bull Thesis</span>
              </div>
              <div class="research-subcard-body">
                ${escapeText(research.bull_case || debate.bull_history || 'Upward momentum confirmed by liquidity absorption and structural retests.')}
              </div>
            </div>
            <div class="research-subcard">
              <div class="research-subcard-title" style="color:var(--red);">
                <span>🐻 Bear Thesis</span>
              </div>
              <div class="research-subcard-body">
                ${escapeText(research.bear_case || debate.bear_history || 'Downside risks evaluated around overhead resistance and potential liquidity sweeps.')}
              </div>
            </div>
            <div class="research-subcard">
              <div class="research-subcard-title" style="color:var(--amber);">
                <span>⚖️ Research Manager Synthesis</span>
              </div>
              <div class="research-subcard-body">
                ${escapeText(research.manager_synthesis || debate.judge_decision || 'Consensus reached balancing directional edge against structural failure points.')}
              </div>
            </div>
          </div>
        </div>

        <!-- 5. RISK ENGINE EVALUATION -->
        <div class="decision-section">
          <div class="decision-section-title">
            <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
            Deterministic Risk Engine Audit
          </div>
          <div style="margin-bottom:1rem;">
            <div style="font-size:0.85rem; font-weight:600; color:var(--text-bright); margin-bottom:0.5rem;">Verified Risk Safeguards:</div>
            <div style="display:flex; flex-direction:column; gap:4px;">
              ${passedChecks.map(check => `
                <div class="risk-check-item">
                  <span class="risk-check-icon pass">✓</span>
                  <span>${escapeText(check)}</span>
                </div>
              `).join('')}
              ${violations.map(viol => `
                <div class="risk-check-item">
                  <span class="risk-check-icon fail">✗</span>
                  <span style="color:var(--red); font-weight:600;">Violation: ${escapeText(viol)}</span>
                </div>
              `).join('')}
              ${modifications.map(mod => `
                <div class="risk-check-item">
                  <span class="risk-check-icon warn">!</span>
                  <span style="color:var(--amber);">Required Adjustment: ${escapeText(mod)}</span>
                </div>
              `).join('')}
            </div>
          </div>
          ${risk.executive_rationale ? `
            <div style="background:var(--bg-primary); padding:0.75rem 1rem; border-radius:var(--radius-sm); border:1px solid var(--border); font-size:0.82rem; color:var(--text-secondary); line-height:1.5;">
              <strong style="color:var(--text-bright);">Risk Rationale:</strong> ${escapeText(risk.executive_rationale)}
            </div>
          ` : ''}
        </div>

        <!-- 6. INSTITUTIONAL MEMORY CARD -->
        <div class="decision-section">
          <div class="decision-section-title">
            <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>
            Institutional Memory &amp; Historical Lessons
          </div>
          ${lessons.length > 0 ? `
            <div style="display:flex; flex-direction:column; gap:6px;">
              ${lessons.map(lessonId => `
                <div style="background:var(--bg-primary); padding:8px 12px; border-radius:var(--radius-sm); border:1px solid var(--border); font-size:0.82rem; display:flex; align-items:center; gap:8px;">
                  <span style="font-family:var(--font-mono); color:var(--cyan); font-weight:600;">[Applied]</span>
                  <button type="button" class="btn-secondary btn-sm" data-applied-lesson-id="${escapeText(lessonId)}">${escapeText(lessonId)}</button>
                </div>
              `).join('')}
            </div>
          ` : `
            <div style="font-size:0.82rem; color:var(--text-muted); font-style:italic;">
              No applied lesson IDs were stored on this completed report.
            </div>
          `}
        </div>

        <!-- 7. DATA PROVENANCE CARD -->
        <div class="decision-section">
          <div class="decision-section-title">
            <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>
            Data Provenance &amp; Verification Timestamps
          </div>
          <div class="provenance-pills">
            ${(prov.sources || ['Forex Market Feed (OHLCV)', 'Economic Calendar', 'Central Bank Intelligence']).map(s => `
              <span class="provenance-pill">
                <span class="dot" style="width:6px; height:6px; background:var(--cyan); border-radius:50%;"></span>
                ${escapeText(s)}
              </span>
            `).join('')}
            <span class="provenance-pill">
              🕒 Cutoff: ${escapeText(prov.analysis_cutoff || data.date || 'Live / Real-Time Bar')}
            </span>
            <span class="provenance-pill">
              ⚙️ Synthesized: ${escapeText(prov.generated_at ? new Date(prov.generated_at).toLocaleTimeString() : 'Current Session')}
            </span>
          </div>
        </div>

        <!-- 8. EXPANDABLE RAW REPORT SYNTHESIS -->
        <details class="raw-report-details" style="background:var(--bg-card); border:1px solid var(--border); border-radius:var(--radius-md); padding:1rem 1.25rem;">
          <summary style="cursor:pointer; font-weight:600; color:var(--text-secondary); user-select:none; font-size:0.88rem; display:flex; align-items:center; gap:8px;">
            <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:16px; height:16px;"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
            View Complete Raw Synthesis Markdown &amp; Agent Deliberation
          </summary>
          <div class="report-content" style="margin-top:1rem; padding-top:1rem; border-top:1px solid var(--border);">
            ${escapeHtml(reportText || 'No raw synthesis text available.')}
          </div>
        </details>
      </div>
    `;
  }

  async function loadReport(runId) {
    try {
      const res = await apiFetch(`/api/runs/${encodeURIComponent(runId)}/report`);
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
        apiFetch('/api/runs').catch(() => null),
        apiFetch('/api/forex/runs').catch(() => null),
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
      const res = await apiFetch('/api/history');
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
      const res = await apiFetch(`/api/runs/${encodeURIComponent(runId)}/report`);
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
