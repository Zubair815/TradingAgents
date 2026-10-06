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
  let backtestActionInFlight = false;
  let lastBacktestDetail = null;
  let mt5Connected = false;
  let mt5RefreshPromise = null;
  let mt5AutoRefreshTimer = null;
  const MT5_DEFAULT_POLL_INTERVAL_MS = 3000;
  const PROPOSAL_PAGE_SIZE = 50;
  let proposalOffset = 0;
  let proposalLoadSequence = 0;
  let proposalActionInFlight = false;
  let dashboardLoadSequence = 0;

  let sessionKey = '';
  let sessionReady = null;
  let sessionExpiresAt = 0;

  function apiErrorMessage(data, fallback = 'Request failed') {
    const error = data && (data.error || data.detail);
    if (typeof error === 'string') return error;
    if (error && typeof error.message === 'string') return error.message;
    if (Array.isArray(error)) {
      const messages = error.map(item => item && item.msg).filter(Boolean);
      if (messages.length) return messages.join('; ');
    }
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
    manualBalanceGroup: $('#manualBalanceGroup'),
    manualAccountFields: $('#manualAccountFields'),
    accountEquity: $('#accountEquity'),
    accountFreeMargin: $('#accountFreeMargin'),
    accountLeverage: $('#accountLeverage'),
    accountCurrency: $('#accountCurrency'),
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
    btnCancelAnalysis: $('#btnCancelAnalysis'),
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
    journalPairFilter: $('#journalPairFilter'),
    journalStatusFilter: $('#journalStatusFilter'),
    journalStartDate: $('#journalStartDate'),
    journalEndDate: $('#journalEndDate'),
    btnApplyJournalFilters: $('#btnApplyJournalFilters'),
    btnClearJournalFilters: $('#btnClearJournalFilters'),
    btnJournalPrevious: $('#btnJournalPrevious'),
    btnJournalNext: $('#btnJournalNext'),
    journalResultSummary: $('#journalResultSummary'),
    journalPageStatus: $('#journalPageStatus'),
    lessonsContainer: $('#lessonsContainer'),
    lessonsPairFilter: $('#lessonsPairFilter'),
    lessonsSetupFilter: $('#lessonsSetupFilter'),
    lessonsDirectionFilter: $('#lessonsDirectionFilter'),
    lessonsTimeframeFilter: $('#lessonsTimeframeFilter'),
    lessonsEvidenceFilter: $('#lessonsEvidenceFilter'),
    lessonsStatusFilter: $('#lessonsStatusFilter'),
    lessonsResultSummary: $('#lessonsResultSummary'),
    // Dashboard Overview DOM
    dashPair:       $('#dashPair'),
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
    dashLastUpdated: $('#dashLastUpdated'),
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
    btnClearProposalFilters: $('#btnClearProposalFilters'),
    btnProposalPrevious: $('#btnProposalPrevious'),
    btnProposalNext: $('#btnProposalNext'),
    proposalsResultSummary: $('#proposalsResultSummary'),
    proposalPageStatus: $('#proposalPageStatus'),
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
    mt5Currency:            $('#mt5Currency'),
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
    performanceInitialCapital: $('#performanceInitialCapital'),
    performanceStatus: $('#performanceStatus'),
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
    btSplits:            $('#btSplits'),
    backtestEstimate:    $('#backtestEstimate'),
    btnLaunchBacktest:   $('#btnLaunchBacktest'),
    btnRefreshBacktests: $('#btnRefreshBacktests'),
    btnRunWalkForward: $('#btnRunWalkForward'),
    btnRunAblation: $('#btnRunAblation'),
    backtestRunsContainer: $('#backtestRunsContainer'),
    // Learning View DOM
    btnRefreshLessons:   $('#btnRefreshLessons'),
    btnClearLessonFilters: $('#btnClearLessonFilters'),
    // Settings View DOM
    settingProvider:     $('#settingProvider'),
    settingQuickModel:   $('#settingQuickModel'),
    settingDeepModel:    $('#settingDeepModel'),
    settingProviderInput: $('#settingProviderInput'),
    settingQuickModelInput: $('#settingQuickModelInput'),
    settingQuickCustomModel: $('#settingQuickCustomModel'),
    settingDeepModelInput: $('#settingDeepModelInput'),
    settingDeepCustomModel: $('#settingDeepCustomModel'),
    settingMaxTokens: $('#settingMaxTokens'),
    settingTemperature: $('#settingTemperature'),
    settingBackendUrl: $('#settingBackendUrl'),
    settingApiKeyStatus: $('#settingApiKeyStatus'),
    settingPairSelect: $('#settingPairSelect'),
    settingPair: $('#settingPair'),
    settingTimeframe: $('#settingTimeframe'),
    settingContextTimeframes: $('#settingContextTimeframes'),
    settingContextToggles: $$('#settingContextToggles .analyst-toggle'),
    settingMarketSource: $('#settingMarketSource'),
    settingRiskPercent: $('#settingRiskPercent'),
    settingMinRR: $('#settingMinRR'),
    settingMaxSpread: $('#settingMaxSpread'),
    settingNewsBlackout: $('#settingNewsBlackout'),
    settingBrokerMode: $('#settingBrokerMode'),
    settingAutoOrder: $('#settingAutoOrder'),
    settingPollInterval: $('#settingPollInterval'),
    settingReflection: $('#settingReflection'),
    // Per-card action buttons & statuses
    btnSaveLlmSettings: $('#btnSaveLlmSettings'),
    btnResetLlmSettings: $('#btnResetLlmSettings'),
    settingsLlmStatus: $('#settingsLlmStatus'),
    btnSaveForexSettings: $('#btnSaveForexSettings'),
    btnResetForexSettings: $('#btnResetForexSettings'),
    settingsForexStatus: $('#settingsForexStatus'),
    btnSaveRiskSettings: $('#btnSaveRiskSettings'),
    btnResetRiskSettings: $('#btnResetRiskSettings'),
    settingsRiskStatus: $('#settingsRiskStatus'),
    btnSaveMt5Settings: $('#btnSaveMt5Settings'),
    btnResetMt5Settings: $('#btnResetMt5Settings'),
    settingsMt5Status: $('#settingsMt5Status'),
    // Global batch actions
    btnSaveSettings: $('#btnSaveSettings'),
    btnResetSettings: $('#btnResetSettings'),
    settingsStatus: $('#settingsStatus'),
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
    syncManualAccountFields();
    await loadConfig();
    await loadMT5Status();
    await loadDashboardOverview();
  }

  function setDefaultDate() {
    const today = new Date().toISOString().split('T')[0];
    if (DOM.tradeDate) {
      DOM.tradeDate.value = today;
      DOM.tradeDate.max = today;
    }
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

        stopMT5AutoRefresh();
        if (tab.dataset.view === 'dashboard') {
          loadDashboardOverview();
        } else if (tab.dataset.view === 'proposals') {
          loadProposals();
        } else if (tab.dataset.view === 'mt5') {
          refreshMT5Data({ notify: false });
          startMT5AutoRefresh();
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
        if (tab.dataset.view === 'analyze') {
          loadRuns();
          loadHistory();
        }
      });
    });

    document.addEventListener('visibilitychange', () => {
      if (document.hidden) {
        stopMT5AutoRefresh();
      } else if (isMT5ViewActive()) {
        refreshMT5Data({ liveOnly: true, notify: false });
        startMT5AutoRefresh();
      }
    });

    // Asset class switcher
    if (DOM.modeForex && DOM.modeEquities) {
      DOM.modeForex.addEventListener('click', () => switchAssetMode('forex'));
      DOM.modeEquities.addEventListener('click', () => switchAssetMode('equities'));
    }

    // Analyst toggles
    DOM.analystToggles.forEach(btn => {
      btn.addEventListener('click', () => {
        btn.classList.toggle('active');
        btn.setAttribute('aria-pressed', String(btn.classList.contains('active')));
      });
    });
    DOM.forexAnalystToggles.forEach(btn => {
      btn.addEventListener('click', () => {
        if (btn.classList.contains('mandatory-stage') || btn.disabled) return;
        btn.classList.toggle('active');
        btn.setAttribute('aria-pressed', String(btn.classList.contains('active')));
      });
    });
    DOM.forexContextToggles.forEach(btn => {
      btn.setAttribute('aria-pressed', String(btn.classList.contains('active')));
      btn.addEventListener('click', () => {
        const activeCount = Array.from(DOM.forexContextToggles).filter(b => b.classList.contains('active')).length;
        if (btn.classList.contains('active') && activeCount <= 1) {
          showToast('At least one context timeframe must remain selected', 'info');
          return;
        }
        btn.classList.toggle('active');
        btn.setAttribute('aria-pressed', String(btn.classList.contains('active')));
      });
    });

    if (DOM.forexAccountSource) {
      DOM.forexAccountSource.addEventListener('change', syncManualAccountFields);
    }

    DOM.provider.addEventListener('change', updateModelSelects);
    DOM.form.addEventListener('submit', handleSubmit);
    if (DOM.btnCancelAnalysis) DOM.btnCancelAnalysis.addEventListener('click', cancelActiveAnalysis);
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
          showProposalDetailModal(proposalButton.dataset.proposalId, { sourceContext: true });
        }
      });
    }

    if (DOM.btnRefreshJournal) {
      DOM.btnRefreshJournal.addEventListener('click', async () => {
        const loaded = await loadJournalTrades();
        showToast(loaded ? 'Forex journal refreshed' : 'Forex journal could not be refreshed', loaded ? 'success' : 'error');
      });
    }
    DOM.btnApplyJournalFilters?.addEventListener('click', () => { journalOffset = 0; loadJournalTrades(); });
    DOM.btnClearJournalFilters?.addEventListener('click', () => {
      [DOM.journalPairFilter, DOM.journalStatusFilter, DOM.journalStartDate, DOM.journalEndDate].forEach(input => { if (input) input.value = ''; });
      journalOffset = 0; loadJournalTrades();
    });
    DOM.btnJournalPrevious?.addEventListener('click', () => { journalOffset = Math.max(0, journalOffset - JOURNAL_PAGE_SIZE); loadJournalTrades(); });
    DOM.btnJournalNext?.addEventListener('click', () => { journalOffset += JOURNAL_PAGE_SIZE; loadJournalTrades(); });

    if (DOM.btnDashRefreshPositions) {
      DOM.btnDashRefreshPositions.addEventListener('click', async () => {
        const loaded = await loadDashboardOverview();
        showToast(loaded ? 'Dashboard refreshed' : 'Dashboard could not be refreshed', loaded ? 'success' : 'error');
      });
    }

    if (DOM.btnDashRefreshProposals) {
      DOM.btnDashRefreshProposals.addEventListener('click', async () => {
        const loaded = await loadDashboardOverview();
        showToast(loaded ? 'Dashboard refreshed' : 'Dashboard could not be refreshed', loaded ? 'success' : 'error');
      });
    }
    DOM.dashPair?.addEventListener('change', () => loadDashboardOverview());

    if (DOM.btnRefreshProposals) {
      DOM.btnRefreshProposals.addEventListener('click', async () => {
        const loaded = await loadProposals();
        showToast(loaded ? 'Proposals refreshed' : 'Proposals could not be refreshed', loaded ? 'success' : 'error');
      });
    }

    [DOM.proposalsPairFilter, DOM.proposalsDateFilter, DOM.proposalsStatusFilter,
      DOM.proposalsActionFilter, DOM.proposalsSetupFilter, DOM.proposalsTimeframeFilter]
      .forEach(input => input?.addEventListener('change', () => { proposalOffset = 0; loadProposals(); }));
    DOM.btnClearProposalFilters?.addEventListener('click', () => {
      [DOM.proposalsPairFilter, DOM.proposalsDateFilter, DOM.proposalsStatusFilter,
        DOM.proposalsActionFilter, DOM.proposalsSetupFilter, DOM.proposalsTimeframeFilter]
        .forEach(input => { if (input) input.value = ''; });
      proposalOffset = 0;
      loadProposals();
    });
    DOM.btnProposalPrevious?.addEventListener('click', () => {
      proposalOffset = Math.max(0, proposalOffset - PROPOSAL_PAGE_SIZE);
      loadProposals();
    });
    DOM.btnProposalNext?.addEventListener('click', () => {
      proposalOffset += PROPOSAL_PAGE_SIZE;
      loadProposals();
    });

    if (DOM.btnRefreshMT5Positions) {
      DOM.btnRefreshMT5Positions.addEventListener('click', () => refreshMT5Data({ collectionsOnly: true }));
    }

    if (DOM.btnMT5Connect) {
      DOM.btnMT5Connect.addEventListener('click', connectMT5);
    }
    if (DOM.btnMT5Disconnect) {
      DOM.btnMT5Disconnect.addEventListener('click', disconnectMT5);
    }
    if (DOM.btnMT5RefreshStatus) {
      DOM.btnMT5RefreshStatus.addEventListener('click', () => refreshMT5Data());
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
      DOM.btnRefreshAnalytics.addEventListener('click', async () => {
        const result = await loadPerformanceInterface();
        if (result) showToast(result.performanceOk && result.calibrationOk ? 'Analytics refreshed' : 'Analytics partially refreshed', result.performanceOk || result.calibrationOk ? 'success' : 'error');
      });
    }

    if (DOM.performanceSegmentSelect) {
      DOM.performanceSegmentSelect.addEventListener('change', () => renderPerformanceSegmentation());
    }

    if (DOM.performanceInitialCapital) {
      DOM.performanceInitialCapital.addEventListener('input', () => {
        if (Number(DOM.performanceInitialCapital.value) > 0) DOM.performanceInitialCapital.setCustomValidity('');
      });
    }

    if (DOM.btnRunCalibration) {
      DOM.btnRunCalibration.addEventListener('click', async () => {
        const loaded = await loadConfidenceCalibration();
        showToast(loaded ? 'Confidence calibration refreshed' : 'Confidence calibration could not be refreshed', loaded ? 'success' : 'error');
      });
    }

    if (DOM.btnRefreshBacktests) {
      DOM.btnRefreshBacktests.addEventListener('click', async () => {
        DOM.btnRefreshBacktests.disabled = true;
        const loaded = await loadBacktestRuns();
        DOM.btnRefreshBacktests.disabled = false;
        showToast(loaded ? 'Backtest runs refreshed' : 'Backtest runs could not be refreshed', loaded ? 'success' : 'error');
      });
    }

    if (DOM.btnRunWalkForward) {
      DOM.btnRunWalkForward.addEventListener('click', async () => {
        if (!validateBacktestInputs({ requireDates: true })) return;
        if (!setBacktestBusy(true, 'Running walk-forward…')) return;
        try {
          const pair = DOM.btPair ? DOM.btPair.value : 'EURUSD';
          const timeframe = DOM.btTimeframe ? DOM.btTimeframe.value : 'H1';
          const date_from = DOM.btStartDate ? (DOM.btStartDate.value || null) : null;
          const date_to = DOM.btEndDate ? (DOM.btEndDate.value || null) : null;
          const max_analysis_points = DOM.btMaxPoints ? Number(DOM.btMaxPoints.value) : 10;
          const n_splits = DOM.btSplits ? Number(DOM.btSplits.value) : 1;
          const payload = { pair, timeframe, date_from, date_to, count: 300,
            max_analysis_points, spread_pips: DOM.btSpread ? Number(DOM.btSpread.value) : 1.5,
            initial_balance: DOM.btCapital ? Number(DOM.btCapital.value) : 100000,
            analyst_selection: ['forex_technical', 'forex_macro', 'forex_news'],
            ...getBacktestModelConfig() };
          const estimateRes = await apiFetch('/api/forex/backtest/estimate', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ pair, timeframe, count: 300, max_analysis_points,
              analyst_count: 3, workflow: 'WALK_FORWARD', n_splits }),
          });
          if (estimateRes.ok) {
            const estimate = await estimateRes.json();
            if (Number(estimate.estimated_cost_usd || 0) >= 1) {
              if (!confirm(`Walk-forward estimate: ${estimate.expected_analyses_count} analyses, ${estimate.estimated_llm_calls} LLM calls, $${Number(estimate.estimated_cost_usd).toFixed(2)}. Proceed?`)) return;
              payload.confirm_expensive = true;
            }
          }
          let res = await apiFetch(`/api/forex/backtest/walkforward?n_splits=${encodeURIComponent(n_splits)}`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
          });
          let data = await res.json().catch(() => ({}));
          const serverError = data && (data.detail || data.error);
          if (res.status === 409 && serverError && serverError.code === 'COST_CONFIRMATION_REQUIRED') {
            const estimate = serverError.estimate || {};
            const confirmed = confirm(`The verified walk-forward dataset requires an estimated ${estimate.estimated_llm_calls ?? 'unknown'} LLM calls and ${estimate.estimated_cost_usd == null ? 'an unavailable cost' : '$' + Number(estimate.estimated_cost_usd).toFixed(2)}. Proceed?`);
            if (!confirmed) return;
            payload.confirm_expensive = true;
            res = await apiFetch(`/api/forex/backtest/walkforward?n_splits=${encodeURIComponent(n_splits)}`, {
              method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
            });
            data = await res.json().catch(() => ({}));
          }
          if (!res.ok) {
            throw new Error(apiErrorMessage(data, 'Walk-Forward failed'));
          }
          await loadBacktestRuns();
          showToast('Walk-forward validation completed', 'success');
          // show lightweight modal by reusing detail renderer
          if (data && data.validation_id) openBacktestDetail(data.validation_id);
        } catch (e) {
          showToast(e.message || 'Walk-forward failed', 'error');
        } finally {
          setBacktestBusy(false);
        }
      });
    }

    if (DOM.btnRunAblation) {
      DOM.btnRunAblation.addEventListener('click', async () => {
        if (!validateBacktestInputs({ requireDates: true })) return;
        if (!setBacktestBusy(true, 'Running ablation…')) return;
        try {
          const pair = DOM.btPair ? DOM.btPair.value : 'EURUSD';
          const timeframe = DOM.btTimeframe ? DOM.btTimeframe.value : 'H1';
          const date_from = DOM.btStartDate ? (DOM.btStartDate.value || null) : null;
          const date_to = DOM.btEndDate ? (DOM.btEndDate.value || null) : null;
          const max_analysis_points = DOM.btMaxPoints ? Number(DOM.btMaxPoints.value) : 10;
          const estimateRes = await apiFetch('/api/forex/backtest/estimate', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ pair, timeframe, count: 300, max_analysis_points,
              analyst_count: 3, workflow: 'ABLATION', variant_count: 10 }),
          });
          let confirm_expensive = false;
          if (estimateRes.ok) {
            const estimate = await estimateRes.json();
            if (Number(estimate.estimated_cost_usd || 0) >= 1) {
              confirm_expensive = confirm(`Historical ablation estimate: ${estimate.expected_analyses_count} analyses, ${estimate.estimated_llm_calls} LLM calls, $${Number(estimate.estimated_cost_usd).toFixed(2)}. Every variant uses the same verified candles and PIT rules. Proceed?`);
              if (!confirm_expensive) return;
            }
          }
          const ablationPayload = { pair, timeframe, date_from, date_to, max_analysis_points,
            spread_pips: DOM.btSpread ? Number(DOM.btSpread.value) : 1.5,
            initial_balance: DOM.btCapital ? Number(DOM.btCapital.value) : 100000,
            ...getBacktestModelConfig(),
            confirm_expensive };
          let res = await apiFetch('/api/forex/backtest/ablation', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(ablationPayload),
          });
          let data = await res.json().catch(() => ({}));
          const serverError = data && (data.detail || data.error);
          if (res.status === 409 && serverError && serverError.code === 'COST_CONFIRMATION_REQUIRED') {
            const estimate = serverError.estimate || {};
            const confirmed = confirm(`The verified ablation dataset requires an estimated ${estimate.estimated_llm_calls ?? 'unknown'} LLM calls and ${estimate.estimated_cost_usd == null ? 'an unavailable cost' : '$' + Number(estimate.estimated_cost_usd).toFixed(2)}. Proceed?`);
            if (!confirmed) return;
            ablationPayload.confirm_expensive = true;
            res = await apiFetch('/api/forex/backtest/ablation', {
              method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(ablationPayload),
            });
            data = await res.json().catch(() => ({}));
          }
          if (!res.ok) {
            throw new Error(apiErrorMessage(data, 'Ablation failed'));
          }
          renderAblationStudy(data || {});
          showToast('Historical multi-agent ablation complete', 'success');
        } catch (e) {
          showToast(e.message || 'Ablation failed', 'error');
        } finally {
          setBacktestBusy(false);
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
        const listButton = ev.target.closest('[data-backtest-list]');
        if (listButton) {
          loadBacktestRuns();
          return;
        }
        const exportButton = ev.target.closest('[data-backtest-export]');
        if (exportButton && lastBacktestDetail) {
          exportBacktestDetail(lastBacktestDetail);
          return;
        }
        const tr = ev.target.closest('[data-backtest-id]');
        if (tr) {
          const id = tr.dataset.backtestId;
          if (id) openBacktestDetail(id);
        }
      });
    }

    if (DOM.btnRefreshLessons) {
      DOM.btnRefreshLessons.addEventListener('click', async () => {
        DOM.btnRefreshLessons.disabled = true;
        DOM.btnRefreshLessons.setAttribute('aria-busy', 'true');
        const loaded = await loadLessons();
        DOM.btnRefreshLessons.disabled = false;
        DOM.btnRefreshLessons.removeAttribute('aria-busy');
        showToast(loaded ? 'Lessons refreshed' : 'Lessons could not be refreshed', loaded ? 'success' : 'error');
      });
    }

    if (DOM.btnClearLessonFilters) {
      DOM.btnClearLessonFilters.addEventListener('click', () => {
        [DOM.lessonsPairFilter, DOM.lessonsSetupFilter, DOM.lessonsDirectionFilter,
          DOM.lessonsTimeframeFilter, DOM.lessonsEvidenceFilter, DOM.lessonsStatusFilter]
          .filter(Boolean).forEach(control => { control.value = ''; });
        loadLessons();
      });
    }

    if (DOM.btnModeBacktestReal && DOM.btnModeBacktestDemo) {
      DOM.btnModeBacktestReal.addEventListener('click', () => {
        DOM.btnModeBacktestReal.classList.add('active');
        DOM.btnModeBacktestDemo.classList.remove('active');
        DOM.btnModeBacktestReal.setAttribute('aria-pressed', 'true');
        DOM.btnModeBacktestDemo.setAttribute('aria-pressed', 'false');
        syncBacktestDateRequirements();
        if (DOM.backtestModeBanner) {
          DOM.backtestModeBanner.className = 'info-banner';
          DOM.backtestModeBanner.innerHTML = '<span>Historical Agent Backtest: Full point-in-time multi-agent execution. Zero lookahead leakage.</span>';
        }
      });
      DOM.btnModeBacktestDemo.addEventListener('click', () => {
        DOM.btnModeBacktestDemo.classList.add('active');
        DOM.btnModeBacktestReal.classList.remove('active');
        DOM.btnModeBacktestDemo.setAttribute('aria-pressed', 'true');
        DOM.btnModeBacktestReal.setAttribute('aria-pressed', 'false');
        syncBacktestDateRequirements();
        if (DOM.backtestModeBanner) {
          DOM.backtestModeBanner.className = 'warning-banner';
          DOM.backtestModeBanner.innerHTML = '<span>Demo Mode: Fast synthetic bar simulation. Strictly illustrative.</span>';
        }
      });
      syncBacktestDateRequirements();
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

    // Per-card Settings Actions
    if (DOM.btnSaveLlmSettings) {
      DOM.btnSaveLlmSettings.addEventListener('click', saveLlmSettings);
    }
    if (DOM.btnResetLlmSettings) {
      DOM.btnResetLlmSettings.addEventListener('click', resetLlmSettings);
    }
    if (DOM.btnSaveForexSettings) {
      DOM.btnSaveForexSettings.addEventListener('click', saveForexSettings);
    }
    if (DOM.btnResetForexSettings) {
      DOM.btnResetForexSettings.addEventListener('click', resetForexSettings);
    }
    if (DOM.btnSaveRiskSettings) {
      DOM.btnSaveRiskSettings.addEventListener('click', saveRiskSettings);
    }
    if (DOM.btnResetRiskSettings) {
      DOM.btnResetRiskSettings.addEventListener('click', resetRiskSettings);
    }
    if (DOM.btnSaveMt5Settings) {
      DOM.btnSaveMt5Settings.addEventListener('click', saveMt5Settings);
    }
    if (DOM.btnResetMt5Settings) {
      DOM.btnResetMt5Settings.addEventListener('click', resetMt5Settings);
    }

    // Provider change & custom model toggles
    if (DOM.settingProviderInput) {
      DOM.settingProviderInput.addEventListener('change', onSettingProviderChange);
    }
    if (DOM.settingQuickModelInput) {
      DOM.settingQuickModelInput.addEventListener('change', () => {
        if (DOM.settingQuickCustomModel) {
          const isCustom = DOM.settingQuickModelInput.value === '__custom__';
          DOM.settingQuickCustomModel.style.display = isCustom ? 'block' : 'none';
          if (isCustom) DOM.settingQuickCustomModel.focus();
        }
      });
    }
    if (DOM.settingDeepModelInput) {
      DOM.settingDeepModelInput.addEventListener('change', () => {
        if (DOM.settingDeepCustomModel) {
          const isCustom = DOM.settingDeepModelInput.value === '__custom__';
          DOM.settingDeepCustomModel.style.display = isCustom ? 'block' : 'none';
          if (isCustom) DOM.settingDeepCustomModel.focus();
        }
      });
    }

    // Context timeframe toggle chips
    DOM.settingContextToggles.forEach(btn => {
      btn.setAttribute('aria-pressed', String(btn.classList.contains('active')));
      btn.addEventListener('click', () => {
        const activeCount = Array.from(DOM.settingContextToggles).filter(b => b.classList.contains('active')).length;
        if (btn.classList.contains('active') && activeCount <= 1) {
          showToast('At least one context timeframe must remain selected', 'info');
          return;
        }
        btn.classList.toggle('active');
        btn.setAttribute('aria-pressed', String(btn.classList.contains('active')));
        if (DOM.settingContextTimeframes) {
          const selected = Array.from(DOM.settingContextToggles)
            .filter(b => b.classList.contains('active'))
            .map(b => b.dataset.settingCtxTf);
          DOM.settingContextTimeframes.value = selected.join(', ');
        }
      });
    });
  }

  function switchAssetMode(mode) {
    currentAssetMode = mode;
    if (mode === 'forex') {
      DOM.modeForex.classList.add('active');
      DOM.modeEquities.classList.remove('active');
      DOM.modeForex.setAttribute('aria-pressed', 'true');
      DOM.modeEquities.setAttribute('aria-pressed', 'false');
      DOM.forexControls.style.display = 'block';
      DOM.equitiesControls.style.display = 'none';
    } else {
      DOM.modeEquities.classList.add('active');
      DOM.modeForex.classList.remove('active');
      DOM.modeEquities.setAttribute('aria-pressed', 'true');
      DOM.modeForex.setAttribute('aria-pressed', 'false');
      DOM.equitiesControls.style.display = 'block';
      DOM.forexControls.style.display = 'none';
    }
  }

  function syncManualAccountFields() {
    const manual = DOM.forexAccountSource && DOM.forexAccountSource.value === 'manual';
    if (DOM.manualBalanceGroup) DOM.manualBalanceGroup.style.display = manual ? 'block' : 'none';
    if (DOM.manualAccountFields) DOM.manualAccountFields.style.display = manual ? 'grid' : 'none';
    [DOM.accountBalance, DOM.accountEquity, DOM.accountFreeMargin, DOM.accountLeverage, DOM.accountCurrency]
      .filter(Boolean)
      .forEach(input => {
        input.required = manual;
        input.disabled = !manual;
        input.setAttribute('aria-required', String(manual));
      });
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
    if (price == null || price === '' || !Number.isFinite(Number(price))) return 'Unavailable';
    const num = Number(price);
    const d = digits != null ? digits : (symbol && String(symbol).toUpperCase().includes('JPY') ? 3 : 5);
    return num.toFixed(d);
  }

  function actionBadgeClass(action) {
    const value = String(action || 'UNKNOWN').toUpperCase();
    if (value === 'LONG' || value === 'BUY') return 'bullish';
    if (value === 'SHORT' || value === 'SELL') return 'bearish';
    if (value === 'REJECT' || value === 'REJECTED') return 'reject';
    return 'neutral';
  }

  function availableMetric(value, suffix = '', digits = 2) {
    return value == null || value === '' || !Number.isFinite(Number(value))
      ? 'Unavailable'
      : `${Number(value).toFixed(digits)}${suffix}`;
  }

  function renderLoadError(container, label) {
    if (container) container.innerHTML = `<div class="empty-state"><div class="empty-state-title">Unavailable: ${escapeText(label)}</div><div class="empty-state-desc">The previous values may be stale. Retry when the local service is available.</div></div>`;
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
    if (DOM.mt5Currency) DOM.mt5Currency.textContent = 'Disconnected / Standby';
    if (DOM.mt5CurrencyVal) DOM.mt5CurrencyVal.textContent = unav;
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
        mt5Connected = connected;
        const login = data.login || data.account_login;
        const masked = data.masked_login || maskAccountLogin(login);
        const server = data.server || '--';
        const terminalPath = data.terminal_path || '--';

        DOM.mt5Dot.style.background = connected ? 'var(--green)' : 'var(--amber)';
        const hasIdentity = masked && masked !== 'Not Set' && masked !== '--';
        DOM.mt5Status.textContent = connected
          ? `MT5: Observed (${hasIdentity ? '#' + masked : 'Live terminal'})`
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
        if (!connected) {
          showMT5Unavailable();
        }
        return true;
      } else {
        mt5Connected = false;
        DOM.mt5Dot.style.background = 'var(--amber)';
        DOM.mt5Status.textContent = 'MT5: Read-Only Observer';
        showMT5Unavailable();
        return false;
      }
    } catch (_) {
      mt5Connected = false;
      DOM.mt5Dot.style.background = 'var(--amber)';
      DOM.mt5Status.textContent = 'MT5: Read-Only Observer';
      showMT5Unavailable();
      return false;
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
          if (DOM.mt5Currency) DOM.mt5Currency.textContent = `Observed account · ${acc.currency}`;
          if (DOM.mt5CurrencyVal) DOM.mt5CurrencyVal.textContent = acc.currency;
          if (DOM.dashCurrency) DOM.dashCurrency.textContent = `Currency: ${acc.currency}`;
        }
        if (acc && acc.leverage) {
          if (DOM.mt5Leverage) DOM.mt5Leverage.textContent = `1:${acc.leverage}`;
          if (DOM.dashLeverage) DOM.dashLeverage.textContent = `Leverage 1:${acc.leverage}`;
        }
        if (acc && acc.server && DOM.mt5DiagServer) DOM.mt5DiagServer.textContent = acc.server;
        if (acc && acc.masked_login && DOM.mt5DiagLogin) DOM.mt5DiagLogin.textContent = acc.masked_login;
        if (acc && acc.masked_login && acc.masked_login !== 'Not Set') {
          DOM.mt5Status.textContent = `MT5: Observed (#${acc.masked_login})`;
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
        return true;
      } else {
        showMT5Unavailable();
        return false;
      }
    } catch (_) {
      showMT5Unavailable();
      return false;
    }
  }

  async function loadMT5Positions() {
    try {
      const res = await apiFetch('/api/forex/mt5/positions');
      if (!res.ok) {
        renderLoadError(DOM.mt5PositionsContainer, 'MT5 positions');
        return false;
      }
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
        return true;
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
      return true;
    } catch (_) {
      renderLoadError(DOM.mt5PositionsContainer, 'MT5 positions');
      return false;
    }
  }

  // ---- Journal & Learning Data ----
  let journalListGeneration = 0;
  const JOURNAL_PAGE_SIZE = 50;
  let journalOffset = 0;
  async function loadJournalTrades() {
    const current = ++journalListGeneration;
    if (DOM.btnRefreshJournal) DOM.btnRefreshJournal.disabled = true;
    DOM.journalTableContainer.innerHTML = '<p class="journal-state" role="status">Loading journal…</p>';
    try {
      const params = new URLSearchParams({limit: String(JOURNAL_PAGE_SIZE), offset: String(journalOffset)});
      if (DOM.journalPairFilter?.value.trim()) params.set('pair', DOM.journalPairFilter.value.trim().toUpperCase());
      if (DOM.journalStatusFilter?.value) params.set('status', DOM.journalStatusFilter.value);
      if (DOM.journalStartDate?.value) params.set('start_date', `${DOM.journalStartDate.value}T00:00:00+00:00`);
      if (DOM.journalEndDate?.value) params.set('end_date', `${DOM.journalEndDate.value}T23:59:59.999999+00:00`);
      const res = await apiFetch(`/api/forex/journal/trades?${params}`);
      const data = await res.json();
      if (current !== journalListGeneration) return;
      if (!res.ok) throw new Error(apiErrorMessage(data, 'Journal could not be loaded.'));
      DOM.journalTableContainer.innerHTML = JournalUI.table(data.trades || []);
      const total = Number(data.total || 0);
      const from = total ? journalOffset + 1 : 0;
      const to = Math.min(journalOffset + Number(data.count || 0), total);
      if (DOM.journalResultSummary) DOM.journalResultSummary.textContent = `Showing ${from}–${to} of ${total} stored trades.`;
      if (DOM.journalPageStatus) DOM.journalPageStatus.textContent = `Page ${Math.floor(journalOffset / JOURNAL_PAGE_SIZE) + 1}`;
      if (DOM.btnJournalPrevious) DOM.btnJournalPrevious.disabled = journalOffset === 0;
      if (DOM.btnJournalNext) DOM.btnJournalNext.disabled = !data.has_more;
      return true;
    } catch (error) {
      if (current === journalListGeneration) DOM.journalTableContainer.innerHTML =
        `<p class="journal-state" role="alert">${escapeText(error.message || 'Network error loading journal.')} Use Refresh Journal to retry.</p>`;
      return false;
    } finally {
      if (current === journalListGeneration && DOM.btnRefreshJournal) DOM.btnRefreshJournal.disabled = false;
    }
  }

  let lessonListGeneration = 0;
  function evidenceClass(count) {
    const n = Number(count || 0);
    return n <= 1 ? 'ANECDOTAL' : n === 2 ? 'EARLY' : n <= 5 ? 'MODERATE' : 'STRONG';
  }

  async function loadLessons() {
    const current = ++lessonListGeneration;
    if (DOM.lessonsResultSummary) DOM.lessonsResultSummary.textContent = '';
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
      const hasFilters = params.size > 0 && !(params.size === 1 && params.get('active') === 'true');
      if (DOM.lessonsResultSummary) {
        DOM.lessonsResultSummary.textContent = `Showing ${lessons.length} lesson${lessons.length === 1 ? '' : 's'}${hasFilters ? ' matching the selected filters' : ''}.`;
      }

      if (lessons.length === 0) {
        DOM.lessonsContainer.innerHTML = `
          <div class="empty-state">
            <div class="empty-state-icon">🧠</div>
            <div class="empty-state-title">${hasFilters ? 'No Matching Lessons' : 'No Stored Lessons'}</div>
            <div class="empty-state-desc">${hasFilters ? 'No stored lessons match the selected filters. Adjust the filters or use Clear Filters.' : 'Post-trade reflections automatically extract prescriptive rules and store them here for future prompt injection.'}</div>
          </div>
        `;
        return true;
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
              <div style="margin-top:8px; display:flex; gap:8px; flex-wrap:wrap;">
                ${l.source_trade_id && l.source_availability?.trade ? `<button class="btn-secondary btn-sm" data-trade-id="${escapeText(l.source_trade_id)}">View Source Trade</button>` : '<span class="stat-sub">Source trade unavailable.</span>'}
                ${l.proposal_id && l.source_availability?.proposal ? `<button class="btn-secondary btn-sm" data-proposal-id="${escapeText(l.proposal_id)}">View Source Proposal</button>` : '<span class="stat-sub">Source proposal unavailable.</span>'}
              </div>
            </div>
          `).join('')}
        </div>`;
      return true;
    } catch (error) {
      if (current === lessonListGeneration) {
        if (DOM.lessonsResultSummary) DOM.lessonsResultSummary.textContent = '';
        DOM.lessonsContainer.innerHTML =
          `<p class="journal-state" role="alert">${escapeText(error.message || 'Network error loading lessons.')} Use Refresh to retry.</p>`;
      }
      return false;
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
  function setDashboardRefreshState(loading) {
    [DOM.btnDashRefreshPositions, DOM.btnDashRefreshProposals].forEach(button => {
      if (button) button.disabled = loading;
    });
    if (loading && DOM.dashLastUpdated) DOM.dashLastUpdated.textContent = 'Refreshing dashboard…';
  }

  function renderDashboardUnavailable(message = 'Dashboard data is unavailable.') {
    if (DOM.dashServerBadge) DOM.dashServerBadge.textContent = 'Server: Unavailable';
    if (DOM.dashLoginBadge) DOM.dashLoginBadge.textContent = 'Account: Unavailable';
    [DOM.dashBalance, DOM.dashEquity, DOM.dashFloating, DOM.dashFreeMargin]
      .forEach(element => { if (element) element.textContent = 'Unavailable'; });
    if (DOM.dashMargin) DOM.dashMargin.textContent = 'Margin Used: Unavailable';
    if (DOM.dashCurrency) DOM.dashCurrency.textContent = 'Account data unavailable';
    if (DOM.dashLeverage) DOM.dashLeverage.textContent = 'Leverage: Unavailable';
    renderLoadError(DOM.dashPositionsContainer, 'positions');
    renderLoadError(DOM.dashOrdersContainer, 'pending orders');
    renderLoadError(DOM.dashProposalsContainer, 'proposals');
    [DOM.dashTodayTradesCount, DOM.dashTodayPnl, DOM.dashTodayR,
      DOM.dashExpectancy, DOM.dashProfitFactor, DOM.dashAvgR, DOM.dashMaxDD]
      .forEach(element => { if (element) element.textContent = 'Unavailable'; });
    renderLoadError(DOM.dashEventsContainer, 'economic calendar');
    renderLoadError(DOM.dashLessonsContainer, 'lessons');
    if (DOM.dashSampleWarning) DOM.dashSampleWarning.style.display = 'none';
    if (DOM.dashLastUpdated) DOM.dashLastUpdated.textContent = message;
  }

  async function loadDashboardOverview() {
    const sequence = ++dashboardLoadSequence;
    setDashboardRefreshState(true);
    try {
      const pair = DOM.dashPair?.value || 'EURUSD';
      const res = await apiFetch(`/api/forex/dashboard/overview?pair=${encodeURIComponent(pair)}`);
      if (!res.ok) {
        const payload = await res.json().catch(() => ({}));
        throw new Error(apiErrorMessage(payload, 'Dashboard data is unavailable.'));
      }
      const data = await res.json();
      if (sequence !== dashboardLoadSequence) return false;
      renderDashboardOverview(data);
      if (DOM.dashLastUpdated) {
        DOM.dashLastUpdated.textContent = `Last refreshed ${new Date().toLocaleString()} · calendar day ${data.research?.calendar_date_utc || 'Unavailable'} UTC`;
      }
      return true;
    } catch (error) {
      if (sequence === dashboardLoadSequence) {
        renderDashboardUnavailable(error.message || 'Dashboard data is unavailable.');
      }
      return false;
    } finally {
      if (sequence === dashboardLoadSequence) setDashboardRefreshState(false);
    }
  }

  function renderDashboardOverview(data) {
    if (!data) return;

    // 1. MT5 Observer Account State
    const mt5 = data.mt5 || {};
    const acc = mt5.account;
    const isConn = mt5.is_connected === true;
    const accountAvailable = mt5.account_available === true;

    if (DOM.dashServerBadge) {
      DOM.dashServerBadge.textContent = `Server: ${mt5.server || 'None'}`;
    }
    if (DOM.dashLoginBadge) {
      DOM.dashLoginBadge.textContent = `Account: ${mt5.masked_login || 'Not Set'}`;
    }

    if (isConn && accountAvailable && acc) {
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
        : 'Margin Used: Unavailable';

      if (DOM.dashBalance) DOM.dashBalance.textContent = balStr;
      if (DOM.dashEquity) DOM.dashEquity.textContent = eqStr;
      if (DOM.dashFreeMargin) DOM.dashFreeMargin.textContent = freeStr;
      if (DOM.dashMargin) DOM.dashMargin.textContent = marginStr;

      if (DOM.dashCurrency) DOM.dashCurrency.textContent = acc.currency ? `Currency: ${acc.currency}` : 'Currency: Unavailable';
      if (DOM.dashLeverage) DOM.dashLeverage.textContent = acc.leverage ? `Leverage 1:${acc.leverage}` : 'Leverage: Unavailable';

      if (acc.profit != null) {
        const p = Number(acc.profit);
        const pStr = `${p >= 0 ? '+' : ''}$${p.toFixed(2)}`;
        const pCol = p >= 0 ? 'var(--green)' : 'var(--red)';
        if (DOM.dashFloating) {
          DOM.dashFloating.textContent = pStr;
          DOM.dashFloating.style.color = pCol;
        }
      } else if (DOM.dashFloating) {
        DOM.dashFloating.textContent = 'Unavailable';
        DOM.dashFloating.style.color = 'var(--text-muted)';
      }
    } else {
      [DOM.dashBalance, DOM.dashEquity, DOM.dashFloating, DOM.dashFreeMargin]
        .forEach(element => { if (element) element.textContent = 'Unavailable'; });
      if (DOM.dashFloating) DOM.dashFloating.style.color = 'var(--text-muted)';
      if (DOM.dashMargin) DOM.dashMargin.textContent = 'Margin Used: Unavailable';
      if (DOM.dashCurrency) DOM.dashCurrency.textContent = isConn ? 'Connected · Account unavailable' : 'Standby / Unconnected';
      if (DOM.dashLeverage) DOM.dashLeverage.textContent = 'Leverage: Unavailable';
    }

    // 2. Open Positions
    if (DOM.dashPositionsContainer) {
      const positions = mt5.open_positions || [];
      if (mt5.positions_available !== true) {
        renderLoadError(DOM.dashPositionsContainer, isConn ? 'positions' : 'positions (MT5 disconnected)');
      } else if (positions.length === 0) {
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
      if (mt5.orders_available !== true) {
        renderLoadError(DOM.dashOrdersContainer, isConn ? 'pending orders' : 'pending orders (MT5 disconnected)');
      } else if (orders.length === 0) {
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
                  ${(() => {
                    const proposalId = String(p.proposal_id || 'Unavailable');
                    return `<td title="${escapeText(proposalId)}" style="font-family:var(--font-mono); font-size:0.75rem; color:var(--text-muted);">${escapeText(proposalId)}</td>`;
                  })()}
                  <td style="font-weight:700; color:var(--cyan);">${escapeText(p.pair)}</td>
                  <td><span class="signal-badge ${actionBadgeClass(p.action)}" style="font-size:0.7rem; padding:2px 8px;">${escapeText(p.action || 'UNKNOWN')}</span></td>
                  <td style="font-family:var(--font-mono);">${formatForexPrice(p.entry_price, p.pair, p.digits)}</td>
                  <td style="font-family:var(--font-mono);">${formatForexPrice(p.stop_loss, p.pair, p.digits)}</td>
                  <td style="font-family:var(--font-mono);">${formatForexPrice(p.take_profit_1, p.pair, p.digits)}</td>
                  <td><span class="status-cell ${escapeText(p.status)}">${escapeText(p.status)}</span></td>
                </tr>
              `).join('')}
            </tbody>
          </table>
        `;
      }
    }

    const todayRes = trading.today_result || {};
    if (DOM.dashTodayTradesCount) DOM.dashTodayTradesCount.textContent = todayRes.trade_count != null ? todayRes.trade_count : 'Unavailable';
    if (DOM.dashTodayPnl) {
      const pnl = todayRes.net_profit == null ? null : Number(todayRes.net_profit);
      DOM.dashTodayPnl.textContent = pnl == null ? 'Unavailable' : `${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)}`;
      DOM.dashTodayPnl.style.color = pnl == null ? 'var(--text-muted)' : (pnl >= 0 ? 'var(--green)' : 'var(--red)');
    }
    if (DOM.dashTodayR) {
      const r = todayRes.total_r == null ? null : Number(todayRes.total_r);
      DOM.dashTodayR.textContent = r == null ? 'Unavailable' : `${r >= 0 ? '+' : ''}${r.toFixed(2)}R`;
      DOM.dashTodayR.style.color = r == null ? 'var(--text-muted)' : (r >= 0 ? 'var(--cyan)' : 'var(--red)');
    }

    // 5. Performance Metrics & Sample Guard
    const perf = data.performance || {};
    if (DOM.dashExpectancy) DOM.dashExpectancy.textContent = availableMetric(perf.expectancy, 'R');
    if (DOM.dashProfitFactor) DOM.dashProfitFactor.textContent = availableMetric(perf.profit_factor);
    if (DOM.dashAvgR) DOM.dashAvgR.textContent = availableMetric(perf.average_r, 'R');
    if (DOM.dashMaxDD) DOM.dashMaxDD.textContent = availableMetric(perf.max_drawdown_pct, '%', 1);

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
      if (research.calendar_available !== true) {
        renderLoadError(DOM.dashEventsContainer, 'economic calendar');
      } else if (events.length === 0) {
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
      if (!res.ok) {
        renderLoadError(DOM.dashPositionsContainer, 'positions');
        return;
      }
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
    } catch (_) {
      renderLoadError(DOM.dashPositionsContainer, 'positions');
    }
  }

  async function loadDashboardProposals() {
    if (!DOM.dashProposalsContainer) return;
    try {
      const res = await apiFetch('/api/forex/proposals?limit=5');
      if (!res.ok) {
        renderLoadError(DOM.dashProposalsContainer, 'proposals');
        return;
      }
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
                <td><span class="signal-badge ${actionBadgeClass(p.action)}" style="font-size:0.7rem; padding:2px 8px;">${escapeText(p.action || 'UNKNOWN')}</span></td>
                <td style="font-family:var(--font-mono);">${formatForexPrice(p.entry_price, p.pair, p.digits)}</td>
                <td style="font-family:var(--font-mono);">${formatForexPrice(p.stop_loss, p.pair, p.digits)}</td>
                <td style="font-family:var(--font-mono);">${formatForexPrice(p.take_profit_1, p.pair, p.digits)}</td>
                <td><span class="status-cell ${escapeText(p.status)}">${escapeText(p.status)}</span></td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      `;
    } catch (_) {
      renderLoadError(DOM.dashProposalsContainer, 'proposals');
    }
  }

  async function loadDashboardAnalytics() {
    try {
      const res = await apiFetch('/api/forex/analytics/dashboard');
      if (!res.ok) {
        if (DOM.dashProfitFactor) DOM.dashProfitFactor.textContent = 'Unavailable';
        if (DOM.dashMaxDD) DOM.dashMaxDD.textContent = 'Unavailable';
        return;
      }
      const data = await res.json();
      const m = data.metrics || {};
      if (m.win_rate != null && DOM.dashWinRate) DOM.dashWinRate.textContent = `${(Number(m.win_rate) * 100).toFixed(1)}%`;
      if (m.total_trades != null && DOM.dashClosedTrades) DOM.dashClosedTrades.textContent = `${m.total_trades} trades`;
      if (m.profit_factor != null && DOM.dashProfitFactor) DOM.dashProfitFactor.textContent = Number(m.profit_factor).toFixed(2);
      if (m.total_r_multiple != null && DOM.dashTotalR) DOM.dashTotalR.textContent = `${Number(m.total_r_multiple).toFixed(1)}R`;
      if (m.max_drawdown_pct != null && DOM.dashMaxDD) DOM.dashMaxDD.textContent = `${Number(m.max_drawdown_pct).toFixed(1)}%`;
    } catch (_) {
      if (DOM.dashProfitFactor) DOM.dashProfitFactor.textContent = 'Unavailable';
      if (DOM.dashMaxDD) DOM.dashMaxDD.textContent = 'Unavailable';
    }
  }

  async function loadDashboardLessons() {
    if (!DOM.dashLessonsContainer) return;
    try {
      const res = await apiFetch('/api/forex/learning/lessons');
      if (!res.ok) {
        renderLoadError(DOM.dashLessonsContainer, 'lessons');
        return;
      }
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
    } catch (_) {
      renderLoadError(DOM.dashLessonsContainer, 'lessons');
    }
  }

  // ---- Proposals View ----
  async function loadProposals() {
    if (!DOM.proposalsTableContainer) return false;
    const sequence = ++proposalLoadSequence;
    if (DOM.btnRefreshProposals) DOM.btnRefreshProposals.disabled = true;
    DOM.proposalsTableContainer.innerHTML = '<div class="empty-state"><div class="empty-state-title">Loading proposals…</div><div class="empty-state-desc">Retrieving saved proposal evidence and lifecycle state.</div></div>';
    try {
      let url = `/api/forex/proposals?limit=${PROPOSAL_PAGE_SIZE}&offset=${proposalOffset}`;
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
      if (sequence !== proposalLoadSequence) return false;
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        DOM.proposalsTableContainer.innerHTML = `<div class="empty-state"><div class="empty-state-title">Proposals unavailable</div><div class="empty-state-desc">${escapeText(apiErrorMessage(err, 'Could not load proposals.'))}</div></div>`;
        return false;
      }
      const data = await res.json();
      const props = data.proposals || [];
      const total = Number(data.total ?? props.length);
      const options = data.filter_options || {};
      const syncOptions = (select, values, defaultLabel) => {
        if (!select) return;
        const selected = select.value;
        select.innerHTML = `<option value="">${escapeText(defaultLabel)}</option>` +
          (values || []).map(value => `<option value="${escapeText(value)}">${escapeText(value)}</option>`).join('');
        if ([...select.options].some(option => option.value === selected)) select.value = selected;
      };
      syncOptions(DOM.proposalsPairFilter, options.pairs, 'All Pairs');
      syncOptions(DOM.proposalsStatusFilter, options.statuses, 'All Statuses');
      syncOptions(DOM.proposalsSetupFilter, options.setups, 'All Setups');
      syncOptions(DOM.proposalsTimeframeFilter, options.timeframes, 'All TFs');
      if (DOM.proposalsResultSummary) {
        const start = total === 0 ? 0 : proposalOffset + 1;
        const end = Math.min(proposalOffset + props.length, total);
        DOM.proposalsResultSummary.textContent = `Showing ${start}–${end} of ${total} proposals`;
      }
      if (DOM.proposalPageStatus) DOM.proposalPageStatus.textContent = `Page ${Math.floor(proposalOffset / PROPOSAL_PAGE_SIZE) + 1}`;
      if (DOM.btnProposalPrevious) DOM.btnProposalPrevious.disabled = proposalOffset === 0;
      if (DOM.btnProposalNext) DOM.btnProposalNext.disabled = proposalOffset + props.length >= total;

      if (props.length === 0) {
        DOM.proposalsTableContainer.innerHTML = `
          <div class="empty-state" style="padding: 2.5rem;">
            <div class="empty-state-icon">📋</div>
            <div class="empty-state-title">No Proposals Found</div>
            <div class="empty-state-desc">No proposals match the current filter criteria.</div>
          </div>
        `;
        return true;
      }

      DOM.proposalsTableContainer.innerHTML = `
        <table class="data-table">
          <thead>
            <tr>
              <th>Proposal ID</th>
              <th>Created (UTC)</th>
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
              const actionClass = actionBadgeClass(actionStr);
              const createdStr = (p.created_at_utc || p.created_at || '').slice(0, 19).replace('T', ' ');
              const lotSize = p.suggested_lot_size != null ? Number(p.suggested_lot_size).toFixed(2) : (p.recommended_lot_size != null ? Number(p.recommended_lot_size).toFixed(2) : '-');
              const riskPct = p.suggested_risk_percent != null ? Number(p.suggested_risk_percent).toFixed(1) : (p.risk_percent != null ? Number(p.risk_percent).toFixed(1) : null);

              return `
                <tr style="cursor:pointer;" onclick="showProposalDetailModal('${escapeText(p.proposal_id)}')">
                  <td style="font-family:var(--font-mono); font-size:0.75rem; color:var(--cyan); font-weight:600;">${escapeText(p.proposal_id)}</td>
                  <td class="date-cell">${escapeText(createdStr || '-')}</td>
                  <td style="font-weight:700; color:var(--text-bright);">${escapeText(p.pair)}</td>
                  <td><span class="signal-badge ${actionClass}" style="font-size:0.7rem; padding:2px 8px;">${escapeText(actionStr)}</span></td>
                  <td style="font-family:var(--font-mono); font-size:0.8rem;">${escapeText(p.timeframe || '-')}</td>
                  <td style="font-size:0.78rem;">${escapeText(p.setup_type || '-')}</td>
                  <td style="font-family:var(--font-mono);">${formatForexPrice(p.entry_price, p.pair, p.digits)}</td>
                  <td style="font-family:var(--font-mono); color:var(--red);">${formatForexPrice(p.stop_loss, p.pair, p.digits)}</td>
                  <td style="font-family:var(--font-mono); color:var(--green);">${formatForexPrice(p.take_profit_1, p.pair, p.digits)}</td>
                  <td style="font-family:var(--font-mono); color:var(--cyan);">${p.risk_reward_ratio ? `${Number(p.risk_reward_ratio).toFixed(2)}:1` : '-'}</td>
                  <td style="font-family:var(--font-mono);">${riskPct == null ? 'Unavailable' : `${riskPct}%`}</td>
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
      return true;
    } catch (_) {
      if (sequence === proposalLoadSequence) renderLoadError(DOM.proposalsTableContainer, 'proposals');
      return false;
    } finally {
      if (sequence === proposalLoadSequence && DOM.btnRefreshProposals) DOM.btnRefreshProposals.disabled = false;
    }
  }

  window.showProposalDetailModal = async function(proposalId, options = {}) {
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
      renderProposalDetailModal(data, options);
    } catch (_) {
      DOM.reportModalBody.innerHTML = `<div style="padding:2rem; color:var(--red);">Network error loading proposal details.</div>`;
    }
  };

  function renderProposalDetailModal(data, options = {}) {
    const prop = data.proposal || {};
    const risk = data.risk_review || {};
    const orig = data.original_proposal || {};
    const immutableEvidenceAvailable = data.immutable_evidence_available === true;
    const userDec = data.user_decision || {};
    const exec = data.matched_execution;
    const outcome = data.final_outcome;
    const lessons = data.lessons || [];
    const sourceContext = options.sourceContext === true;
    const allowedActions = data.allowed_actions || [];

    const actionStr = (prop.action || 'NO_TRADE').toUpperCase();
    const actionClass = actionStr === 'LONG' ? 'long' : actionStr === 'SHORT' ? 'short' : actionStr === 'REJECT' ? 'reject' : 'no_trade';
    const statusStr = (prop.status || 'PENDING').toUpperCase();

    DOM.reportModalTitle.innerHTML = `
      <div style="display:flex; align-items:center; gap:10px; flex-wrap:wrap;">
        ${sourceContext ? '<span style="font-weight:700; color:var(--text-bright);">Lesson Source:</span>' : ''}
        <span style="color:var(--cyan); font-weight:800; font-family:var(--font-mono);">${escapeText(prop.pair || 'FOREX')}</span>
        <span class="direction-tag ${actionClass}" style="font-size:0.8rem; padding:3px 10px;">${escapeText(actionStr)}</span>
        <span class="status-cell ${escapeText(statusStr)}" style="font-size:0.75rem;">Status: ${escapeText(statusStr)}</span>
        <span style="font-size:0.75rem; color:var(--text-muted); font-family:var(--font-mono);">${escapeText(prop.proposal_id)}</span>
      </div>
    `;

    DOM.reportModalBody.innerHTML = `
      <div style="display:flex; flex-direction:column; gap:1.25rem;">
        ${sourceContext ? `
          <div class="info-banner" style="display:block; line-height:1.65; margin-bottom:0;">
            <div><strong>What this is:</strong> This is the original trade plan that produced the stored Learning lesson. It is historical evidence, not a new trade instruction.</div>
            <div style="margin-top:6px; color:var(--text-primary);">
              The system proposed a <strong>${escapeText(actionStr)}</strong> ${escapeText(prop.pair || 'Forex')} ${escapeText(prop.setup_type || 'trade')} on the ${escapeText(prop.timeframe || 'recorded')} timeframe
              at ${formatForexPrice(orig.entry_price, orig.pair || prop.pair, orig.digits)}, with stop loss ${formatForexPrice(orig.stop_loss, orig.pair || prop.pair, orig.digits)} and target ${formatForexPrice(orig.take_profit_1, orig.pair || prop.pair, orig.digits)}.
              ${risk.decision ? `Risk review: <strong>${escapeText(risk.decision)}</strong>.` : 'Risk review was unavailable.'}
              ${outcome && outcome.status === 'CLOSED' ? `The linked trade closed with <strong>${availableMetric(outcome.pips_gained, ' pips', 1)}</strong>, <strong>${availableMetric(outcome.r_multiple, 'R')}</strong>, and ${outcome.net_profit == null ? 'unavailable net profit' : `<strong>$${Number(outcome.net_profit).toFixed(2)}</strong> net profit`}.` : 'No completed linked-trade outcome is available.'}
            </div>
          </div>
        ` : ''}
        <!-- 1. IMMUTABLE ORIGINAL PROPOSAL -->
        <div class="decision-section">
          <div class="decision-section-title">
            <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
            ${sourceContext ? 'Original Trade Plan' : 'Immutable Original Proposal'}
          </div>
          ${immutableEvidenceAvailable ? '' : '<div class="info-banner error">Immutable proposal evidence is unavailable for this record. Current mutable fields are not substituted.</div>'}
          <div class="proposal-meta-grid">
            <div class="proposal-meta-item">
              <span class="proposal-meta-label">Entry Level</span>
              <span class="proposal-meta-value">${formatForexPrice(orig.entry_price, orig.pair || prop.pair, orig.digits)}</span>
              <span class="proposal-meta-sub">${escapeText(orig.order_type || 'Unavailable')} • ${escapeText(orig.timeframe || 'Unavailable')}</span>
            </div>
            <div class="proposal-meta-item">
              <span class="proposal-meta-label">Stop Loss</span>
              <span class="proposal-meta-value" style="color:var(--red);">${formatForexPrice(orig.stop_loss, orig.pair || prop.pair, orig.digits)}</span>
              <span class="proposal-meta-sub">${orig.sl_pips != null ? `${orig.sl_pips} pips` : 'Unavailable'}</span>
            </div>
            <div class="proposal-meta-item">
              <span class="proposal-meta-label">Take Profit 1</span>
              <span class="proposal-meta-value" style="color:var(--green);">${formatForexPrice(orig.take_profit_1, orig.pair || prop.pair, orig.digits)}</span>
              <span class="proposal-meta-sub">${orig.tp_pips != null ? `${orig.tp_pips} pips` : 'Unavailable'}</span>
            </div>
            <div class="proposal-meta-item">
              <span class="proposal-meta-label">Take Profit 2</span>
              <span class="proposal-meta-value" style="color:var(--green);">${formatForexPrice(orig.take_profit_2, orig.pair || prop.pair, orig.digits)}</span>
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
              <span class="proposal-meta-sub">${orig.suggested_risk_percent != null ? `${orig.suggested_risk_percent}% risk` : 'Risk unavailable'}</span>
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
              ${escapeText(risk.decision || 'UNAVAILABLE')}
            </span>
          </div>
          <div style="display:flex; flex-direction:column; gap:4px;">
            ${(risk.risk_checks_passed || []).map(c => `
              <div class="risk-check-item">
                <span class="risk-check-icon pass">✓</span>
                <span>${escapeText(c)}</span>
              </div>
            `).join('') || '<div class="risk-check-item"><span>Risk checks unavailable</span></div>'}
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
            ${sourceContext ? 'Historical Lifecycle Status' : 'User Decision &amp; Lifecycle Status'}
          </div>
          <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px; margin-bottom:1rem;">
            <div>
              <div style="font-size:0.82rem; color:var(--text-secondary);">Current Lifecycle State:</div>
              <div style="font-size:1rem; font-weight:700; color:var(--text-bright);">${escapeText(userDec.status || statusStr)}</div>
            </div>
            <div style="display:flex; gap:8px; flex-wrap:wrap;">
              ${sourceContext ? `
                <span class="badge-readonly">Read-only historical evidence</span>
              ` : `
                ${allowedActions.includes('WAIT') ? `<button data-proposal-action class="btn-secondary btn-sm" onclick="updateProposalDecision('${escapeText(prop.proposal_id)}', 'WAIT')">⏸ Hold</button>` : ''}
                ${allowedActions.includes('SKIPPED') ? `<button data-proposal-action class="btn-secondary btn-sm" style="color:var(--amber);" onclick="updateProposalDecision('${escapeText(prop.proposal_id)}', 'SKIPPED')">⏭️ Skip</button>` : ''}
                ${allowedActions.includes('REJECTED') ? `<button data-proposal-action class="btn-secondary btn-sm" style="color:var(--red);" onclick="updateProposalDecision('${escapeText(prop.proposal_id)}', 'REJECTED')">🚫 Reject</button>` : ''}
                ${allowedActions.includes('CANCELLED') ? `<button data-proposal-action class="btn-secondary btn-sm" onclick="updateProposalDecision('${escapeText(prop.proposal_id)}', 'CANCELLED')">Cancel</button>` : ''}
                ${allowedActions.includes('EXPIRED') ? `<button data-proposal-action class="btn-secondary btn-sm" onclick="updateProposalDecision('${escapeText(prop.proposal_id)}', 'EXPIRED')">⏱️ Expire</button>` : ''}
                ${allowedActions.length === 0 ? '<span class="badge-readonly">Terminal lifecycle state</span>' : ''}
              `}
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
                <span class="proposal-meta-value">${formatForexPrice(exec.open_price, exec.pair || prop.pair, exec.digits)}</span>
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
                <span class="proposal-meta-value">${availableMetric(outcome.pips_gained, '', 1)}</span>
              </div>
              <div class="proposal-meta-item">
                <span class="proposal-meta-label">R Multiple</span>
                <span class="proposal-meta-value">${availableMetric(outcome.r_multiple, 'R')}</span>
              </div>
              <div class="proposal-meta-item">
                <span class="proposal-meta-label">Net Profit</span>
                <span class="proposal-meta-value">${outcome.net_profit == null ? 'Unavailable' : `$${Number(outcome.net_profit).toFixed(2)}`}</span>
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
              Lesson context unavailable or no lessons were persisted for this proposal.
            </div>
          `}
        </div>
      </div>
    `;
  }

  window.updateProposalDecision = async function(proposalId, newStatus) {
    if (!proposalId || !newStatus || proposalActionInFlight) return;
    proposalActionInFlight = true;
    DOM.reportModalBody.querySelectorAll('[data-proposal-action]').forEach(button => { button.disabled = true; });
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
    } finally {
      proposalActionInFlight = false;
      DOM.reportModalBody.querySelectorAll('[data-proposal-action]').forEach(button => { button.disabled = false; });
    }
  };

  // ---- MT5 Orders, Deals & Observer Actions (Phase 29) ----
  async function loadMT5Orders() {
    if (!DOM.mt5OrdersContainer) return true;
    try {
      const res = await apiFetch('/api/forex/mt5/orders');
      if (!res.ok) {
        renderLoadError(DOM.mt5OrdersContainer, 'MT5 orders');
        return false;
      }
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
        return true;
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
      return true;
    } catch (_) {
      renderLoadError(DOM.mt5OrdersContainer, 'MT5 orders');
      return false;
    }
  }

  async function loadMT5Deals() {
    if (!DOM.mt5DealsContainer) return true;
    try {
      const res = await apiFetch('/api/forex/mt5/deals');
      if (!res.ok) {
        renderLoadError(DOM.mt5DealsContainer, 'MT5 deals');
        return false;
      }
      const data = await res.json();
      const deals = data.deals || [];
      const tradeDeals = deals
        .filter(d => ['DEAL_TYPE_BUY', 'DEAL_TYPE_SELL'].includes(String(d.type || '').toUpperCase()) && d.symbol && Number(d.volume) > 0)
        .sort((a, b) => Date.parse(b.time || 0) - Date.parse(a.time || 0));
      if (tradeDeals.length === 0) {
        DOM.mt5DealsContainer.innerHTML = `
          <div class="empty-state" style="padding: 2rem;">
            <div class="empty-state-icon">📜</div>
            <div class="empty-state-title">No Recent Deals</div>
            <div class="empty-state-desc">Closed deal transactions from MT5 history appear here.</div>
          </div>
        `;
        return true;
      }
      DOM.mt5DealsContainer.innerHTML = `
        ${tradeDeals.length < deals.length ? `<p class="trade-evidence-note">${deals.length - tradeDeals.length} non-trade balance or account adjustment(s) are intentionally excluded.</p>` : ''}
        <table class="data-table">
          <thead>
            <tr>
              <th>Deal ID</th>
              <th>Time (UTC)</th>
              <th>Symbol</th>
              <th>Type</th>
              <th>Volume</th>
              <th>Price</th>
              <th>Costs</th>
              <th>Gross</th>
              <th>Net</th>
            </tr>
          </thead>
          <tbody>
            ${tradeDeals.slice(0, 15).map(d => {
              const gross = Number(d.profit || 0);
              const costs = Number(d.commission || 0) + Number(d.swap || 0) + Number(d.fee || 0);
              const net = gross + costs;
              const pnlColor = net >= 0 ? 'var(--green)' : 'var(--red)';
              const dealTime = d.time && Number.isFinite(Date.parse(d.time))
                ? new Date(d.time).toISOString().replace('T', ' ').replace('Z', ' UTC') : 'Unavailable';
              return `
                <tr>
                  <td style="font-family:var(--font-mono); font-size:0.8rem;">#${escapeText(d.deal_id || d.ticket)}</td>
                  <td style="font-family:var(--font-mono); font-size:0.75rem;">${escapeText(dealTime)}</td>
                  <td style="font-weight:700; color:var(--cyan);">${escapeText(d.symbol)}</td>
                  <td>${escapeText(d.type_str || d.type || (d.entry ? 'DEAL ' + d.entry : 'DEAL'))}</td>
                  <td style="font-family:var(--font-mono);">${Number(d.volume || 0).toFixed(2)}</td>
                  <td style="font-family:var(--font-mono); font-weight:600;">${formatForexPrice(d.price, d.symbol)}</td>
                  <td style="font-family:var(--font-mono); font-size:0.75rem; color:var(--text-muted);">${costs >= 0 ? '+' : ''}$${costs.toFixed(2)}</td>
                  <td style="font-family:var(--font-mono);">${gross >= 0 ? '+' : ''}$${gross.toFixed(2)}</td>
                  <td style="font-family:var(--font-mono); font-weight:700; color:${pnlColor};">${net >= 0 ? '+' : ''}$${net.toFixed(2)}</td>
                </tr>
              `;
            }).join('')}
          </tbody>
        </table>
      `;
      return true;
    } catch (_) {
      renderLoadError(DOM.mt5DealsContainer, 'MT5 deals');
      return false;
    }
  }

  function isMT5ViewActive() {
    return Boolean(document.querySelector('#view-mt5.active'));
  }

  function getMT5PollIntervalMs() {
    const seconds = Number(configData?.runtime_settings?.mt5_poll_interval_seconds ?? 3.0);
    return Number.isFinite(seconds) && seconds > 0
      ? Math.max(100, seconds * 1000)
      : MT5_DEFAULT_POLL_INTERVAL_MS;
  }

  function stopMT5AutoRefresh() {
    if (mt5AutoRefreshTimer !== null) {
      window.clearInterval(mt5AutoRefreshTimer);
      mt5AutoRefreshTimer = null;
    }
  }

  function startMT5AutoRefresh() {
    stopMT5AutoRefresh();
    if (document.hidden || !isMT5ViewActive()) return;
    mt5AutoRefreshTimer = window.setInterval(() => {
      if (!document.hidden && isMT5ViewActive() && mt5Connected) {
        refreshMT5Data({ liveOnly: true, notify: false });
      }
    }, getMT5PollIntervalMs());
  }

  async function refreshMT5Data({ collectionsOnly = false, liveOnly = false, notify = true } = {}) {
    if (mt5RefreshPromise) return mt5RefreshPromise;
    const buttons = [DOM.btnMT5RefreshStatus, DOM.btnRefreshMT5Positions].filter(Boolean);
    buttons.forEach(button => { button.disabled = true; });
    const loaders = liveOnly
      ? [loadMT5Account, loadMT5Positions, loadMT5Deals]
      : collectionsOnly
        ? [loadMT5Positions, loadMT5Orders, loadMT5Deals]
        : [loadMT5Status, loadMT5Account, loadMT5Positions, loadMT5Orders, loadMT5Deals];
    mt5RefreshPromise = (async () => {
      const outcomes = await Promise.all(loaders.map(loader => loader()));
      const failures = outcomes.filter(result => result !== true).length;
      if (notify) {
        if (failures === 0) showToast('MT5 observer data refreshed', 'success');
        else if (failures < outcomes.length) showToast('MT5 refresh completed with unavailable sections', 'warning');
        else showToast('MT5 data could not be refreshed', 'error');
      }
      return failures === 0;
    })();
    try {
      return await mt5RefreshPromise;
    } finally {
      mt5RefreshPromise = null;
      buttons.forEach(button => { button.disabled = false; });
    }
  }

  async function connectMT5() {
    const path = DOM.mt5TerminalPathInput ? DOM.mt5TerminalPathInput.value.trim() : '';
    const server = DOM.mt5ServerInput ? DOM.mt5ServerInput.value.trim() : '';
    const loginRaw = DOM.mt5LoginInput ? DOM.mt5LoginInput.value.trim() : '';
    if (loginRaw && !/^\d+$/.test(loginRaw)) {
      showToast('Account login must contain digits only', 'error');
      DOM.mt5LoginInput?.focus();
      return;
    }
    const login = loginRaw ? Number(loginRaw) : null;
    if (login !== null && (!Number.isSafeInteger(login) || login <= 0)) {
      showToast('Account login must be a positive whole number', 'error');
      DOM.mt5LoginInput?.focus();
      return;
    }

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
        await refreshMT5Data({ notify: false });
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
      await refreshMT5Data({ notify: false });
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
        renderMT5SymbolQuote(data.symbol_info || data, data.tick, data.tick_status);
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

  function renderMT5SymbolQuote(info, tick, tickStatus = null) {
    if (!DOM.mt5SymbolResultContainer) return;
    const canon = info.canonical_symbol || info.name || '--';
    const brokerSym = info.name || canon;
    const digits = info.digits != null ? info.digits : 5;
    const point = info.point != null ? info.point : 0.00001;

    const liveTickUnavailable = tickStatus === 'UNAVAILABLE';
    const bidVal = tick && tick.bid != null ? tick.bid : (liveTickUnavailable ? null : info.bid);
    const askVal = tick && tick.ask != null ? tick.ask : (liveTickUnavailable ? null : info.ask);
    const bidStr = (bidVal != null && bidVal > 0) ? formatForexPrice(bidVal, brokerSym, digits) : '--';
    const askStr = (askVal != null && askVal > 0) ? formatForexPrice(askVal, brokerSym, digits) : '--';

    const spreadPts = tick && tick.spread_points != null ? tick.spread_points : (info.spread_points != null ? info.spread_points : '--');
    const spreadPips = tick && tick.spread_pips != null ? tick.spread_pips : (info.spread_pips != null ? info.spread_pips : '--');
    const spreadStr = (spreadPts !== '--') ? `${spreadPts} pts (${spreadPips} pips)` : '--';

    const timeStr = tick && tick.time ? new Date(tick.time).toISOString().replace('T', ' ').substring(0, 19) + ' UTC' : 'Live Quote';

    DOM.mt5SymbolResultContainer.innerHTML = `
      ${liveTickUnavailable ? '<div class="warning-banner" role="status">Market is closed or the latest MT5 quote is stale. Contract specifications are shown below; stale bid and ask prices are intentionally hidden.</div>' : ''}
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.75rem; flex-wrap: wrap; gap: 0.5rem;">
        <div style="display: flex; align-items: center; gap: 8px;">
          <span style="font-size: 1.15rem; font-weight: 800; color: var(--cyan); font-family: var(--font-mono);">${escapeText(brokerSym)}</span>
          <span class="badge-readonly" style="font-size: 0.7rem;">ISO: ${escapeText(canon)}</span>
          ${info.path ? `<span style="font-size: 0.75rem; color: var(--text-muted); font-family: var(--font-mono);">${escapeText(info.path)}</span>` : ''}
        </div>
        <div style="font-size: 0.75rem; color: var(--text-muted); font-family: var(--font-mono);">
          Quote Time: <span style="color: var(--text-secondary);">${escapeText(liveTickUnavailable ? 'Unavailable (market closed / stale)' : timeStr)}</span>
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
  let calibrationRequestGeneration = 0;
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
  function performanceDate(value) {
    if (!value) return 'Unavailable';
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? 'Unavailable' : date.toLocaleString();
  }
  function lineChart(title, points, suffix = '', note = '') {
    const values = (points || []).map(point => Number(point.value)).filter(Number.isFinite);
    if (!values.length) return `<div class="card"><strong>${escapeText(title)}</strong><p class="stat-sub">Unavailable — no stored source values.</p></div>`;
    const min = Math.min(...values), max = Math.max(...values), span = max - min || 1;
    const formatValue = value => (title === 'Equity' || title === 'Drawdown') ? money(value) : availableNumber(value, suffix);
    const coords = values.map((value, index) => `${values.length === 1 ? 50 : (index / (values.length - 1)) * 100},${36 - ((value - min) / span) * 32}`).join(' ');
    const dated = (points || []).filter(point => point.timestamp);
    const period = dated.length ? `${performanceDate(dated[0].timestamp)} → ${performanceDate(dated.at(-1).timestamp)}` : 'Dates unavailable';
    return `<div class="card"><strong>${escapeText(title)}</strong><svg viewBox="0 0 100 40" role="img" aria-label="${escapeText(`${title}: ${values.length} points, minimum ${formatValue(min)}, maximum ${formatValue(max)}, latest ${formatValue(values.at(-1))}`)}" style="width:100%;height:130px;"><polyline points="${coords}" fill="none" stroke="var(--cyan)" stroke-width="1.5" vector-effect="non-scaling-stroke"/></svg><p class="stat-sub">Range ${escapeText(formatValue(min))} to ${escapeText(formatValue(max))} · latest ${escapeText(formatValue(values.at(-1)))}<br>${escapeText(period)}${note ? ` · ${escapeText(note)}` : ''}</p></div>`;
  }
  function distributionChart(title, points, suffix = '') {
    const values = (points || []).map(point => Number(point.value)).filter(Number.isFinite);
    if (!values.length) return `<div class="card"><strong>${escapeText(title)}</strong><p class="stat-sub">Unavailable — no stored source values.</p></div>`;
    const maxAbs = Math.max(...values.map(Math.abs), 1);
    return `<div class="card"><strong>${escapeText(title)}</strong><div role="img" aria-label="${escapeText(`${title}: ${values.map(value => availableNumber(value, suffix)).join(', ')}`)}" style="display:flex;align-items:end;gap:3px;height:110px;">${values.slice(-60).map(value => `<span title="${escapeText(availableNumber(value, suffix))}" aria-hidden="true" style="flex:1;min-width:2px;height:${Math.max(2, Math.abs(value) / maxAbs * 100)}%;background:${value < 0 ? 'var(--red)' : 'var(--green)'};"></span>`).join('')}</div><p class="stat-sub">${values.length} values · range ${escapeText(availableNumber(Math.min(...values), suffix))} to ${escapeText(availableNumber(Math.max(...values), suffix))}</p></div>`;
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
    DOM.perfBreakdownContainer.innerHTML = `<div class="data-table-container"><table class="data-table"><thead><tr><th>Segment</th><th>Trades</th><th>W / L / BE</th><th>Win Rate</th><th>Avg R</th><th>Expectancy</th><th>Profit Factor</th><th>Net P&amp;L</th><th>Max DD</th></tr></thead><tbody>${rows.map(([name, m]) => `<tr><td>${escapeText(name)}</td><td>${escapeText(m.trade_count ?? 0)}</td><td>${escapeText(`${m.wins ?? 0} / ${m.losses ?? 0} / ${m.breakeven ?? 0}`)}</td><td>${escapeText(availableNumber(m.win_rate_pct, '%', 1))}</td><td>${escapeText(availableNumber(m.average_r, 'R'))}</td><td>${escapeText(availableNumber(m.expectancy, 'R'))}</td><td>${escapeText(availableNumber(m.profit_factor))}</td><td>${escapeText(money(m.net_profit))}</td><td>${escapeText(money(m.maximum_drawdown))}</td></tr>`).join('')}</tbody></table></div>`;
  }
  function renderConfidenceCalibration(report) {
    if (!report || !report.buckets) return '<p class="journal-state">Unavailable — calibration backend returned no report.</p>';
    const rows = Object.values(report.buckets);
    if (!rows.some(row => Number(row.trade_count) > 0)) return '<div class="empty-state"><div class="empty-state-title">Not enough calibration observations</div><div class="empty-state-desc">No closed trades currently contain model-confidence evidence.</div></div>';
    return `<p class="stat-sub">ECE: ${escapeText(availableNumber(report.expected_calibration_error, '', 4))} · Brier score: ${escapeText(availableNumber(report.brier_score, '', 4))}</p><div class="data-table-container"><table class="data-table"><thead><tr><th>Raw model confidence band</th><th>N</th><th>Observed win rate</th><th>Calibration gap</th><th>Avg R</th><th>Expectancy</th><th>Status</th></tr></thead><tbody>${rows.map(row => { const midpoint = (Number(row.lower_bound) + Number(row.upper_bound)) / 2; const gap = Number(row.trade_count) ? Math.abs(midpoint - Number(row.win_rate_pct)) : null; return `<tr><td>${escapeText(row.bucket_label)}</td><td>${escapeText(row.trade_count)}</td><td>${escapeText(Number(row.trade_count) ? availableNumber(row.win_rate_pct, '%', 1) : 'Unavailable')}</td><td>${escapeText(gap == null ? 'Unavailable' : availableNumber(gap, ' pp', 1))}</td><td>${escapeText(Number(row.trade_count) ? availableNumber(row.average_r, 'R') : 'Unavailable')}</td><td>${escapeText(Number(row.trade_count) ? availableNumber(row.expectancy, 'R') : 'Unavailable')}</td><td>${escapeText(row.is_calibrated ? 'CALIBRATED' : (row.trade_count ? 'SMALL SAMPLE' : 'EMPTY'))}</td></tr>`; }).join('')}</tbody></table></div>${(report.warnings || []).map(warning => `<p class="warning-banner">${escapeText(warning)}</p>`).join('')}`;
  }
  function performanceCapital() {
    const value = Number(DOM.performanceInitialCapital?.value);
    if (!Number.isFinite(value) || value <= 0) {
      DOM.performanceInitialCapital?.setCustomValidity('Enter an analytical capital greater than zero.');
      DOM.performanceInitialCapital?.reportValidity();
      return null;
    }
    DOM.performanceInitialCapital?.setCustomValidity('');
    return value;
  }
  async function loadPerformanceAnalytics() {
    const current = ++performanceRequestGeneration;
    const capital = performanceCapital();
    if (capital == null) return false;
    if (DOM.btnRefreshAnalytics) DOM.btnRefreshAnalytics.disabled = true;
    [DOM.performanceOverallContainer, DOM.performanceExecutionContainer, DOM.performanceChartsContainer, DOM.perfBreakdownContainer].filter(Boolean).forEach(container => { container.innerHTML = '<p class="journal-state" role="status">Loading performance analytics…</p>'; });
    try {
      const performanceResponse = await apiFetch(`/api/forex/analytics/performance?initial_capital=${encodeURIComponent(capital)}`);
      const data = await performanceResponse.json();
      if (current !== performanceRequestGeneration) return;
      if (!performanceResponse.ok) throw new Error(apiErrorMessage(data, 'Performance analytics could not be loaded.'));
      performanceData = data;
      const m = data.performance?.overall || {};
      DOM.sampleSizeWarning.style.display = Number(m.trade_count || 0) < 30 ? '' : 'none';
      if (!Number(m.trade_count)) {
        const empty = '<div class="empty-state"><div class="empty-state-title">No closed trades</div><div class="empty-state-desc">Performance statistics become available after a trade has a stored closing outcome.</div></div>';
        DOM.performanceOverallContainer.innerHTML = empty;
        DOM.performanceExecutionContainer.innerHTML = empty;
        DOM.performanceChartsContainer.innerHTML = empty;
        DOM.perfBreakdownContainer.innerHTML = empty;
        if (DOM.performanceStatus) DOM.performanceStatus.textContent = `Generated ${performanceDate(data.generated_at_utc)} · $${capital.toLocaleString()} analytical baseline · ${data.sample?.open_trades_excluded || 0} open trades excluded.`;
        return true;
      }
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
      const analyzedFills = Number(f.total_executions_analyzed || 0);
      const excludedFills = Number(f.total_executions_excluded || 0);
      DOM.performanceExecutionContainer.innerHTML = metricGrid([
        ['Mean entry deviation', availableNumber(e.entry_deviation_pips, ' pips')], ['Max entry deviation', availableNumber(e.max_entry_deviation_pips, ' pips')],
        ['SL / TP changes', `${e.sl_changes_count ?? 0} / ${e.tp_changes_count ?? 0}`], ['Manual exits', String(e.manual_exits_count ?? 0), availableNumber(e.manual_exits_pct, '%', 1)],
        ['Partial closes', String(e.partial_close_count ?? 0), `${availableNumber(e.partial_close_volume, ' lots')} · ${availableNumber(e.partial_close_avg_captured_r, 'R')}`],
        ['Execution friction', analyzedFills ? money(f.total_execution_friction_usd) : 'Unavailable', `${analyzedFills} analyzed · ${excludedFills} unavailable · quality ${analyzedFills ? availableNumber(f.average_execution_quality_score, '/100', 1) : 'Unavailable'}`],
      ]);
      const s = data.series || {};
      const baseline = `$${Number(data.capital_basis?.initial_capital || capital).toLocaleString()} starting capital`;
      DOM.performanceChartsContainer.innerHTML = `<div class="dashboard-grid">${lineChart('Cumulative R', s.cumulative_r, 'R')}${lineChart('Equity', s.equity, '', baseline)}${lineChart('Drawdown', s.drawdown, '', baseline)}${distributionChart('R distribution', s.r_distribution, 'R')}${distributionChart('MAE distribution', s.mae_distribution, 'R')}${(s.mfe_vs_realized || []).length ? `<div class="card"><strong>MFE vs realized R</strong><div class="data-table-container"><table class="data-table"><thead><tr><th>MFE R</th><th>Realized R</th></tr></thead><tbody>${s.mfe_vs_realized.slice(-30).map(point => `<tr><td>${escapeText(availableNumber(point.mfe_r, 'R'))}</td><td>${escapeText(availableNumber(point.realized_r, 'R'))}</td></tr>`).join('')}</tbody></table></div></div>` : '<div class="card"><strong>MFE vs realized R</strong><p class="stat-sub">Unavailable — no stored source values.</p></div>'}</div>`;
      renderPerformanceSegmentation();
      if (DOM.performanceStatus) DOM.performanceStatus.textContent = `Generated ${performanceDate(data.generated_at_utc)} · ${baseline} · ${data.sample?.closed_trades || 0} closed trades · ${data.sample?.open_trades_excluded || 0} open excluded.`;
      return true;
    } catch (error) {
      if (current !== performanceRequestGeneration) return;
      const message = escapeText(error.message || 'Network error loading analytics.');
      [DOM.performanceOverallContainer, DOM.performanceExecutionContainer, DOM.performanceChartsContainer, DOM.perfBreakdownContainer].filter(Boolean).forEach(container => { container.innerHTML = `<p class="journal-state" role="alert">${message} Use Refresh Analytics to retry.</p>`; });
      return false;
    } finally {
      if (current === performanceRequestGeneration && DOM.btnRefreshAnalytics) DOM.btnRefreshAnalytics.disabled = false;
    }
  }
  async function loadConfidenceCalibration() {
    const current = ++calibrationRequestGeneration;
    if (DOM.btnRunCalibration) DOM.btnRunCalibration.disabled = true;
    if (DOM.confidenceCalibrationContainer) DOM.confidenceCalibrationContainer.innerHTML = '<p class="journal-state" role="status">Loading confidence calibration…</p>';
    try {
      const response = await apiFetch('/api/forex/metrics/confidence-calibration');
      const data = await response.json();
      if (current !== calibrationRequestGeneration) return false;
      if (!response.ok) throw new Error(apiErrorMessage(data, 'Confidence calibration could not be loaded.'));
      DOM.confidenceCalibrationContainer.innerHTML = renderConfidenceCalibration(data.report);
      return true;
    } catch (error) {
      if (current === calibrationRequestGeneration && DOM.confidenceCalibrationContainer) DOM.confidenceCalibrationContainer.innerHTML = `<p class="journal-state" role="alert">${escapeText(error.message || 'Network error loading confidence calibration.')} Use Refresh Calibration to retry.</p>`;
      return false;
    } finally {
      if (current === calibrationRequestGeneration && DOM.btnRunCalibration) DOM.btnRunCalibration.disabled = false;
    }
  }
  async function loadPerformanceInterface() {
    const [performance, calibration] = await Promise.allSettled([loadPerformanceAnalytics(), loadConfidenceCalibration()]);
    return {
      performanceOk: performance.status === 'fulfilled' && performance.value === true,
      calibrationOk: calibration.status === 'fulfilled' && calibration.value === true,
    };
  }

  // ---- Backtest Runs View ----
  async function loadBacktestRuns() {
    if (!DOM.backtestRunsContainer) return false;
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
        lastBacktestDetail = null;
        return true;
      }
      DOM.backtestRunsContainer.innerHTML = `
        <div class="backtest-table-scroll" tabindex="0" role="region" aria-label="Backtest runs; scroll horizontally to view all columns">
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
        </div>
      `;
      lastBacktestDetail = null;
      return true;
    } catch (error) {
      DOM.backtestRunsContainer.innerHTML = `<div class="info-banner error">${escapeText(error.message || 'Unable to load backtest runs')}</div>`;
      return false;
    }
  }

  // ---- Settings View ----
  function populateModelOptions(selectElem, customInputElem, selectedModel, providerId) {
    if (!selectElem || !configData || !configData.providers) return;
    selectElem.innerHTML = '';
    const provider = configData.providers.find(p => p.id === providerId);
    const models = provider ? [...provider.models] : [];

    const effectiveModel = selectedModel ? String(selectedModel).trim() : '';
    let foundMatch = false;

    // Prepend active model if it is not in the predefined list
    if (effectiveModel && !models.includes(effectiveModel)) {
      models.unshift(effectiveModel);
    }

    models.forEach(m => {
      const opt = document.createElement('option');
      opt.value = m;
      opt.textContent = m;
      if (m === effectiveModel) {
        opt.selected = true;
        foundMatch = true;
      }
      selectElem.appendChild(opt);
    });

    // Append custom option
    const customOpt = document.createElement('option');
    customOpt.value = '__custom__';
    customOpt.textContent = 'Custom Model ID...';
    if (!foundMatch && effectiveModel) {
      customOpt.selected = true;
    }
    selectElem.appendChild(customOpt);

    if (customInputElem) {
      if (selectElem.value === '__custom__') {
        customInputElem.style.display = 'block';
        customInputElem.value = effectiveModel;
      } else {
        customInputElem.style.display = 'none';
        customInputElem.value = '';
      }
    }
  }

  function onSettingProviderChange() {
    if (!DOM.settingProviderInput) return;
    const providerId = DOM.settingProviderInput.value;
    populateModelOptions(
      DOM.settingQuickModelInput,
      DOM.settingQuickCustomModel,
      '',
      providerId
    );
    populateModelOptions(
      DOM.settingDeepModelInput,
      DOM.settingDeepCustomModel,
      '',
      providerId
    );
    setSectionStatus(DOM.settingsLlmStatus, 'Provider changed. Review both model selections before saving.', 'info');
  }

  function setSectionStatus(statusElem, message, type = 'success') {
    if (!statusElem) return;
    statusElem.textContent = message;
    statusElem.className = 'settings-status ' + type;
    if (type === 'success') {
      setTimeout(() => {
        if (statusElem.textContent === message) {
          statusElem.textContent = '';
          statusElem.className = 'settings-status';
        }
      }, 6000);
    }
  }

  function getModelValue(selectElem, customInputElem) {
    if (!selectElem) return null;
    if (selectElem.value === '__custom__') {
      return customInputElem && customInputElem.value ? customInputElem.value.trim() : null;
    }
    return selectElem.value || null;
  }

  function populateSettingsView() {
    if (!configData) return;
    if (DOM.settingProvider) DOM.settingProvider.textContent = configData.provider || 'Not configured';
    if (DOM.settingQuickModel) DOM.settingQuickModel.textContent = configData.quick_model || 'Default';
    if (DOM.settingDeepModel) DOM.settingDeepModel.textContent = configData.deep_model || 'Default';

    const runtimeSettings = configData.runtime_settings || {};
    const currentProvider = runtimeSettings.llm_provider || configData.provider || 'openrouter';

    if (DOM.settingProviderInput && configData.providers) {
      DOM.settingProviderInput.innerHTML = '';
      configData.providers.forEach(p => {
        const opt = document.createElement('option');
        opt.value = p.id;
        opt.textContent = p.name;
        if (p.id === currentProvider) opt.selected = true;
        DOM.settingProviderInput.appendChild(opt);
      });
    }

    const activeProvider = DOM.settingProviderInput ? DOM.settingProviderInput.value : currentProvider;
    populateModelOptions(
      DOM.settingQuickModelInput,
      DOM.settingQuickCustomModel,
      runtimeSettings.quick_think_llm || configData.quick_model || '',
      activeProvider
    );
    populateModelOptions(
      DOM.settingDeepModelInput,
      DOM.settingDeepCustomModel,
      runtimeSettings.deep_think_llm || configData.deep_model || '',
      activeProvider
    );

    if (DOM.settingMaxTokens) {
      const maxTokens = runtimeSettings.max_tokens != null ? runtimeSettings.max_tokens : configData.max_tokens;
      DOM.settingMaxTokens.value = maxTokens != null ? String(maxTokens) : '';
    }
    if (DOM.settingTemperature) {
      DOM.settingTemperature.value = runtimeSettings.temperature != null ? String(runtimeSettings.temperature) : (configData.temperature != null ? String(configData.temperature) : '');
    }
    if (DOM.settingBackendUrl) {
      DOM.settingBackendUrl.value = runtimeSettings.backend_url || '';
    }

    const providerSecretConfigured = configData.secret_status && configData.secret_status.llm_provider_secrets === 'Configured';
    if (DOM.settingApiKeyStatus) {
      DOM.settingApiKeyStatus.textContent = providerSecretConfigured ? 'Configured / Hidden' : 'Missing / Not Set';
      DOM.settingApiKeyStatus.style.color = providerSecretConfigured ? 'var(--green)' : 'var(--amber)';
    }

    const activePair = runtimeSettings.forex_default_pair || 'EURUSD';
    if (DOM.settingPair) {
      if (!Array.from(DOM.settingPair.options).some(option => option.value === activePair)) {
        DOM.settingPair.add(new Option(`${activePair} — Custom registered pair`, activePair));
      }
      DOM.settingPair.value = activePair;
    }
    if (DOM.settingTimeframe) DOM.settingTimeframe.value = runtimeSettings.forex_default_execution_timeframe || 'H1';
    const contexts = runtimeSettings.forex_default_context_timeframes || ['H4', 'D1'];
    if (DOM.settingContextTimeframes) {
      DOM.settingContextTimeframes.value = contexts.join(', ');
    }
    const ctxSet = new Set(contexts);
    DOM.settingContextToggles.forEach(btn => {
      btn.classList.toggle('active', ctxSet.has(btn.dataset.settingCtxTf));
      btn.setAttribute('aria-pressed', String(btn.classList.contains('active')));
    });
    if (DOM.settingMarketSource) DOM.settingMarketSource.value = runtimeSettings.forex_market_source || 'mt5';
    if (DOM.settingRiskPercent) DOM.settingRiskPercent.value = String(runtimeSettings.forex_default_risk_percent ?? 1.0);
    if (DOM.settingMinRR) DOM.settingMinRR.value = String(runtimeSettings.forex_min_rr ?? 1.5);
    if (DOM.settingMaxSpread) DOM.settingMaxSpread.value = String(runtimeSettings.forex_max_spread_pips ?? 5.0);
    if (DOM.settingNewsBlackout) DOM.settingNewsBlackout.value = String(runtimeSettings.forex_news_blackout_minutes ?? 120);
    if (DOM.settingPollInterval) DOM.settingPollInterval.value = String(runtimeSettings.mt5_poll_interval_seconds ?? 3.0);
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

  // 1. LLM Settings
  function validateSettingsInputs(inputs, statusElem) {
    const invalid = inputs.filter(input => input && !input.checkValidity());
    if (!invalid.length) return true;
    invalid[0].reportValidity();
    setSectionStatus(statusElem, invalid[0].validationMessage || 'Review the highlighted value.', 'error');
    return false;
  }

  async function saveLlmSettings() {
    if (!validateSettingsInputs(
      [DOM.settingQuickCustomModel, DOM.settingDeepCustomModel, DOM.settingMaxTokens, DOM.settingTemperature, DOM.settingBackendUrl],
      DOM.settingsLlmStatus
    )) return;
    const payload = {};
    if (DOM.settingProviderInput && DOM.settingProviderInput.value) {
      payload.llm_provider = DOM.settingProviderInput.value;
    }
    const quick = getModelValue(DOM.settingQuickModelInput, DOM.settingQuickCustomModel);
    if (quick) payload.quick_think_llm = quick;
    const deep = getModelValue(DOM.settingDeepModelInput, DOM.settingDeepCustomModel);
    if (deep) payload.deep_think_llm = deep;
    if (DOM.settingMaxTokens && DOM.settingMaxTokens.value) {
      payload.max_tokens = parseInt(DOM.settingMaxTokens.value, 10);
    }
    if (DOM.settingTemperature) {
      const val = DOM.settingTemperature.value.trim();
      payload.temperature = val !== '' ? parseFloat(val) : null;
    }
    if (DOM.settingBackendUrl) {
      payload.backend_url = DOM.settingBackendUrl.value.trim() || null;
    }
    try {
      const res = await apiFetch('/api/forex/settings', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(apiErrorMessage(data, 'LLM settings update failed'));
      settingsData = data;
      configData.runtime_settings = data.settings || {};
      populateSettingsView();
      setSectionStatus(DOM.settingsLlmStatus, 'LLM configuration saved and applied.', 'success');
      showToast('LLM configuration saved', 'success');
    } catch (error) {
      setSectionStatus(DOM.settingsLlmStatus, error.message || 'Validation error', 'error');
      showToast(error.message || 'LLM save failed', 'error');
    }
  }

  async function resetLlmSettings() {
    try {
      const res = await apiFetch('/api/forex/settings/reset', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ keys: ['llm_provider', 'quick_think_llm', 'deep_think_llm', 'backend_url', 'max_tokens', 'temperature'] })
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(apiErrorMessage(data, 'LLM reset failed'));
      settingsData = data;
      configData.runtime_settings = data.settings || {};
      populateSettingsView();
      setSectionStatus(DOM.settingsLlmStatus, 'LLM defaults restored.', 'success');
      showToast('LLM defaults restored', 'info');
    } catch (error) {
      setSectionStatus(DOM.settingsLlmStatus, error.message || 'Reset failed', 'error');
      showToast(error.message || 'LLM reset failed', 'error');
    }
  }

  // 2. Forex Defaults
  async function saveForexSettings() {
    if (!validateSettingsInputs([DOM.settingPair, DOM.settingTimeframe, DOM.settingMarketSource], DOM.settingsForexStatus)) return;
    const payload = {};
    if (DOM.settingPair && DOM.settingPair.value) {
      payload.forex_default_pair = DOM.settingPair.value.trim().toUpperCase();
    }
    if (DOM.settingTimeframe) payload.forex_default_execution_timeframe = DOM.settingTimeframe.value;
    if (DOM.settingContextToggles && DOM.settingContextToggles.length > 0) {
      const selected = Array.from(DOM.settingContextToggles)
        .filter(b => b.classList.contains('active'))
        .map(b => b.dataset.settingCtxTf);
      payload.forex_default_context_timeframes = selected.length > 0 ? selected : ['H4', 'D1'];
    } else if (DOM.settingContextTimeframes) {
      payload.forex_default_context_timeframes = DOM.settingContextTimeframes.value
        .split(',')
        .map(v => v.trim().toUpperCase())
        .filter(Boolean);
    }
    if (DOM.settingMarketSource && DOM.settingMarketSource.value) {
      payload.forex_market_source = DOM.settingMarketSource.value;
    }
    try {
      const res = await apiFetch('/api/forex/settings', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(apiErrorMessage(data, 'Forex settings update failed'));
      settingsData = data;
      configData.runtime_settings = data.settings || {};
      populateSettingsView();
      setSectionStatus(DOM.settingsForexStatus, 'Forex defaults saved and applied.', 'success');
      showToast('Forex defaults saved', 'success');
    } catch (error) {
      setSectionStatus(DOM.settingsForexStatus, error.message || 'Validation error', 'error');
      showToast(error.message || 'Forex save failed', 'error');
    }
  }

  async function resetForexSettings() {
    try {
      const res = await apiFetch('/api/forex/settings/reset', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ keys: ['forex_default_pair', 'forex_default_execution_timeframe', 'forex_default_context_timeframes', 'forex_market_source'] })
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(apiErrorMessage(data, 'Forex reset failed'));
      settingsData = data;
      configData.runtime_settings = data.settings || {};
      populateSettingsView();
      setSectionStatus(DOM.settingsForexStatus, 'Forex defaults restored.', 'success');
      showToast('Forex defaults restored', 'info');
    } catch (error) {
      setSectionStatus(DOM.settingsForexStatus, error.message || 'Reset failed', 'error');
      showToast(error.message || 'Forex reset failed', 'error');
    }
  }

  // 3. Risk Controls
  async function saveRiskSettings() {
    if (!validateSettingsInputs(
      [DOM.settingRiskPercent, DOM.settingMinRR, DOM.settingMaxSpread, DOM.settingNewsBlackout],
      DOM.settingsRiskStatus
    )) return;
    const payload = {};
    if (DOM.settingRiskPercent && DOM.settingRiskPercent.value) {
      payload.forex_default_risk_percent = Number(DOM.settingRiskPercent.value);
    }
    if (DOM.settingMinRR && DOM.settingMinRR.value) {
      payload.forex_min_rr = Number(DOM.settingMinRR.value);
    }
    if (DOM.settingMaxSpread && DOM.settingMaxSpread.value) {
      payload.forex_max_spread_pips = Number(DOM.settingMaxSpread.value);
    }
    if (DOM.settingNewsBlackout && DOM.settingNewsBlackout.value) {
      payload.forex_news_blackout_minutes = Number(DOM.settingNewsBlackout.value);
    }
    try {
      const res = await apiFetch('/api/forex/settings', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(apiErrorMessage(data, 'Risk settings update failed'));
      settingsData = data;
      configData.runtime_settings = data.settings || {};
      populateSettingsView();
      setSectionStatus(DOM.settingsRiskStatus, 'Risk controls saved and applied.', 'success');
      showToast('Risk controls saved', 'success');
    } catch (error) {
      setSectionStatus(DOM.settingsRiskStatus, error.message || 'Validation error', 'error');
      showToast(error.message || 'Risk save failed', 'error');
    }
  }

  async function resetRiskSettings() {
    try {
      const res = await apiFetch('/api/forex/settings/reset', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ keys: ['forex_default_risk_percent', 'forex_min_rr', 'forex_max_spread_pips', 'forex_news_blackout_minutes'] })
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(apiErrorMessage(data, 'Risk reset failed'));
      settingsData = data;
      configData.runtime_settings = data.settings || {};
      populateSettingsView();
      setSectionStatus(DOM.settingsRiskStatus, 'Risk controls restored to defaults.', 'success');
      showToast('Risk controls restored', 'info');
    } catch (error) {
      setSectionStatus(DOM.settingsRiskStatus, error.message || 'Reset failed', 'error');
      showToast(error.message || 'Risk reset failed', 'error');
    }
  }

  // 4. MT5 Observer Settings
  async function saveMt5Settings() {
    if (!validateSettingsInputs([DOM.settingPollInterval], DOM.settingsMt5Status)) return;
    const payload = {};
    if (DOM.settingPollInterval && DOM.settingPollInterval.value) {
      payload.mt5_poll_interval_seconds = Number(DOM.settingPollInterval.value);
    }
    try {
      const res = await apiFetch('/api/forex/settings', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(apiErrorMessage(data, 'MT5 settings update failed'));
      settingsData = data;
      configData.runtime_settings = data.settings || {};
      populateSettingsView();
      const msg = data.restart_required
        ? 'MT5 poll interval saved. Service restart required to take effect.'
        : 'MT5 observer settings saved and applied.';
      setSectionStatus(DOM.settingsMt5Status, msg, 'success');
      showToast(data.restart_required ? 'MT5 settings saved (restart required)' : 'MT5 observer settings saved', 'success');
    } catch (error) {
      setSectionStatus(DOM.settingsMt5Status, error.message || 'Validation error', 'error');
      showToast(error.message || 'MT5 save failed', 'error');
    }
  }

  async function resetMt5Settings() {
    try {
      const res = await apiFetch('/api/forex/settings/reset', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ keys: ['mt5_poll_interval_seconds'] })
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(apiErrorMessage(data, 'MT5 reset failed'));
      settingsData = data;
      configData.runtime_settings = data.settings || {};
      populateSettingsView();
      const msg = data.restart_required
        ? 'MT5 defaults restored. Service restart required to take effect.'
        : 'MT5 settings restored to defaults.';
      setSectionStatus(DOM.settingsMt5Status, msg, 'success');
      showToast(data.restart_required ? 'MT5 defaults restored (restart required)' : 'MT5 settings restored to defaults', 'info');
    } catch (error) {
      setSectionStatus(DOM.settingsMt5Status, error.message || 'Reset failed', 'error');
      showToast(error.message || 'MT5 reset failed', 'error');
    }
  }

  // Global Batch Actions (Save All / Reset All)
  async function saveSettings() {
    if (!validateSettingsInputs([
      DOM.settingQuickCustomModel, DOM.settingDeepCustomModel, DOM.settingMaxTokens,
      DOM.settingTemperature, DOM.settingBackendUrl, DOM.settingPair, DOM.settingTimeframe,
      DOM.settingMarketSource, DOM.settingRiskPercent, DOM.settingMinRR,
      DOM.settingMaxSpread, DOM.settingNewsBlackout, DOM.settingPollInterval
    ], DOM.settingsStatus)) return;
    const payload = {};
    if (DOM.settingProviderInput && DOM.settingProviderInput.value) {
      payload.llm_provider = DOM.settingProviderInput.value;
    }
    const quick = getModelValue(DOM.settingQuickModelInput, DOM.settingQuickCustomModel);
    if (quick) payload.quick_think_llm = quick;
    const deep = getModelValue(DOM.settingDeepModelInput, DOM.settingDeepCustomModel);
    if (deep) payload.deep_think_llm = deep;
    if (DOM.settingMaxTokens && DOM.settingMaxTokens.value) {
      payload.max_tokens = parseInt(DOM.settingMaxTokens.value, 10);
    }
    if (DOM.settingTemperature) {
      const val = DOM.settingTemperature.value.trim();
      payload.temperature = val !== '' ? parseFloat(val) : null;
    }
    if (DOM.settingBackendUrl) payload.backend_url = DOM.settingBackendUrl.value.trim() || null;
    if (DOM.settingPair && DOM.settingPair.value) {
      payload.forex_default_pair = DOM.settingPair.value.trim().toUpperCase();
    }
    if (DOM.settingTimeframe) payload.forex_default_execution_timeframe = DOM.settingTimeframe.value;
    if (DOM.settingContextToggles && DOM.settingContextToggles.length > 0) {
      const selected = Array.from(DOM.settingContextToggles)
        .filter(b => b.classList.contains('active'))
        .map(b => b.dataset.settingCtxTf);
      payload.forex_default_context_timeframes = selected.length > 0 ? selected : ['H4', 'D1'];
    } else if (DOM.settingContextTimeframes) {
      payload.forex_default_context_timeframes = DOM.settingContextTimeframes.value.split(',').map(v => v.trim().toUpperCase()).filter(Boolean);
    }
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
      setSectionStatus(DOM.settingsStatus, data.restart_required ? 'Saved. Restart required for the MT5 polling change.' : 'Saved and applied.', 'success');
      showToast(data.restart_required ? 'All settings saved (restart required)' : 'All settings saved and applied', 'success');
    } catch (error) {
      setSectionStatus(DOM.settingsStatus, error.message || 'Validation error', 'error');
      showToast(error.message || 'Save all failed', 'error');
    }
  }

  async function resetSettings() {
    if (!confirm('Reset every Settings category to its project defaults? This cannot be undone.')) return;
    try {
      const res = await apiFetch('/api/forex/settings/reset', { method: 'POST' });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(apiErrorMessage(data, 'Reset failed'));
      settingsData = data;
      configData.runtime_settings = data.settings || {};
      populateSettingsView();
      setSectionStatus(DOM.settingsStatus, data.restart_required ? 'Defaults restored. Restart required for the MT5 polling change.' : 'Defaults restored.', 'success');
      showToast(data.restart_required ? 'All defaults restored (restart required)' : 'All defaults restored', 'info');
    } catch (error) {
      setSectionStatus(DOM.settingsStatus, error.message || 'Reset failed', 'error');
      showToast(error.message || 'Reset all failed', 'error');
    }
  }

  // ---- Submit Analysis ----
  function renderAnalysisPending(symbol) {
    if (!DOM.reportContent) return;
    DOM.reportContent.innerHTML = `
      <div class="empty-state" role="status" aria-live="polite">
        <div class="empty-state-title">Analysis in progress</div>
        <div class="empty-state-desc">Preparing a new report for ${escapeText(symbol)}. Previous report content has been cleared.</div>
      </div>`;
  }

  function renderAnalysisFailure(message, state = 'failed') {
    if (!DOM.reportContent) return;
    DOM.reportContent.innerHTML = `
      <div class="empty-state" role="alert" data-analysis-state="${escapeText(state)}">
        <div class="empty-state-title">${state === 'cancelled' ? 'Analysis cancelled' : 'Analysis unavailable'}</div>
        <div class="empty-state-desc">${escapeText(message || 'No report was produced.')}</div>
      </div>`;
  }

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
    if (!DOM.form.checkValidity()) {
      DOM.form.reportValidity();
      return;
    }
    const pair = DOM.forexPair.value;
    const timeframe = DOM.forexTimeframe.value;
    const riskPct = Number(DOM.riskPercent.value);
    const accountSource = DOM.forexAccountSource ? DOM.forexAccountSource.value : 'mt5';

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
      showToast('Select at least one context timeframe', 'error');
      return;
    }

    const payload = {
      pair: pair,
      execution_timeframe: timeframe,
      timeframe: timeframe,
      context_timeframes: contextTimeframes,
      date: DOM.tradeDate ? (DOM.tradeDate.value || null) : null,
      account_balance: accountSource === 'manual' ? Number(DOM.accountBalance.value) : null,
      account_equity: accountSource === 'manual' ? Number(DOM.accountEquity.value) : null,
      account_free_margin: accountSource === 'manual' ? Number(DOM.accountFreeMargin.value) : null,
      account_leverage: accountSource === 'manual' ? Number(DOM.accountLeverage.value) : null,
      account_currency: accountSource === 'manual' ? DOM.accountCurrency.value.trim().toUpperCase() : null,
      risk_percent: riskPct,
      analysts: selectedAnalysts,
      provider: DOM.provider.value || null,
      quick_model: DOM.quickModel.value || null,
      deep_model: DOM.deepModel.value || null,
      research_depth: DOM.forexResearchDepth ? DOM.forexResearchDepth.value : 'deep',
      account_source: accountSource,
      min_rr: Number(DOM.forexMinRR.value),
      max_spread_pips: Number(DOM.forexMaxSpread.value),
      economic_blackout: DOM.forexEconomicBlackout ? DOM.forexEconomicBlackout.checked : true,
    };

    DOM.btnRun.disabled = true;
    DOM.btnRun.innerHTML = `
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="animation: spin 1s linear infinite;">
        <circle cx="12" cy="12" r="10" stroke-dasharray="31" stroke-dashoffset="10"/>
      </svg>
      Analyzing Forex...
    `;
    renderAnalysisPending(pair);

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
      if (DOM.btnCancelAnalysis) DOM.btnCancelAnalysis.style.display = 'block';
      showPipeline(pair, `${timeframe} • ${riskPct}% Risk`, FOREX_PIPELINE_NODES);
      connectForexSSE(data.run_id, pair);
      showToast(`Forex analysis started for ${pair}`, 'success');

    } catch (err) {
      showToast(err.message, 'error');
      renderAnalysisFailure(err.message || 'Forex analysis could not start.');
      resetRunButton();
    }
  }

  async function cancelActiveAnalysis() {
    if (!currentRunId || !activePipelineType) return;
    const cancellingType = activePipelineType;
    if (DOM.btnCancelAnalysis) DOM.btnCancelAnalysis.disabled = true;
    try {
      const prefix = activePipelineType === 'forex' ? '/api/forex/runs' : '/api/runs';
      const response = await apiFetch(`${prefix}/${encodeURIComponent(currentRunId)}/cancel`, {
        method: 'POST',
      });
      const data = await response.json();
      if (!response.ok) throw new Error(apiErrorMessage(data, 'Cancellation failed'));
      if (data.status === 'cancelled') {
        if (eventSource) {
          eventSource.close();
          eventSource = null;
        }
        const pct = document.getElementById('progressPercent');
        if (pct) pct.textContent = 'Cancelled';
        renderAnalysisFailure('Analysis cancelled. No new report was produced.', 'cancelled');
        resetRunButton();
        showToast(cancellingType === 'forex' ? 'Forex analysis cancelled' : 'Equity analysis cancelled', 'info');
        loadRuns();
      } else {
        showToast('Analysis cancellation requested', 'info');
      }
    } catch (error) {
      if (DOM.btnCancelAnalysis) DOM.btnCancelAnalysis.disabled = false;
      showToast(error.message || 'Cancellation failed', 'error');
    }
  }

  // ---- Backtest Submit Flow ----
  function isHistoricalBacktestMode() {
    return Boolean(DOM.btnModeBacktestReal && DOM.btnModeBacktestReal.classList.contains('active'));
  }

  function syncBacktestDateRequirements() {
    const required = isHistoricalBacktestMode();
    [DOM.btStartDate, DOM.btEndDate].filter(Boolean).forEach(input => {
      input.required = required;
      input.setAttribute('aria-required', String(required));
    });
  }

  function validateBacktestInputs({ requireDates = isHistoricalBacktestMode() } = {}) {
    const today = new Date().toISOString().slice(0, 10);
    [DOM.btStartDate, DOM.btEndDate].filter(Boolean).forEach(input => {
      input.required = requireDates;
      input.setAttribute('aria-required', String(requireDates));
      input.max = today;
      input.setCustomValidity('');
    });
    if (DOM.btStartDate && DOM.btEndDate && DOM.btStartDate.value && DOM.btEndDate.value && DOM.btStartDate.value >= DOM.btEndDate.value) {
      DOM.btEndDate.setCustomValidity('End Date must be later than Start Date.');
    }
    const valid = DOM.backtestForm ? DOM.backtestForm.checkValidity() : true;
    if (!valid && DOM.backtestForm) DOM.backtestForm.reportValidity();
    return valid;
  }

  function getBacktestModelConfig() {
    const runtime = (configData && configData.runtime_settings) || {};
    return {
      provider: runtime.llm_provider || (configData && configData.provider) || null,
      quick_model: runtime.quick_think_llm || (configData && configData.quick_model) || null,
      deep_model: runtime.deep_think_llm || (configData && configData.deep_model) || null,
    };
  }

  function setBacktestBusy(busy, message = 'Running backtest…') {
    if (busy && backtestActionInFlight) return false;
    backtestActionInFlight = busy;
    [DOM.btnLaunchBacktest, DOM.btnRunWalkForward, DOM.btnRunAblation]
      .filter(Boolean)
      .forEach(button => {
        button.disabled = busy;
        button.setAttribute('aria-busy', String(busy));
      });
    if (DOM.btnLaunchBacktest) {
      DOM.btnLaunchBacktest.textContent = busy ? message : '🚀 Run Backtest';
    }
    return true;
  }

  function exportBacktestDetail(data) {
    const id = String(data.backtest_id || 'backtest').replace(/[^a-zA-Z0-9_-]/g, '_');
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `${id}.json`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    showToast('Backtest evidence exported', 'success');
  }

  async function handleBacktestSubmit() {
    // Gather form values
    const modeReal = DOM.btnModeBacktestReal && DOM.btnModeBacktestReal.classList.contains('active');
    const demoMode = DOM.btnModeBacktestDemo && DOM.btnModeBacktestDemo.classList.contains('active') && !modeReal;
    if (!validateBacktestInputs({ requireDates: modeReal })) return;
    if (!setBacktestBusy(true, demoMode ? 'Running demo…' : 'Running historical backtest…')) return;
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
      ...getBacktestModelConfig(),
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
        provider: payload.provider || 'openai',
        quick_model: payload.quick_model || 'gpt-4.1-mini',
        deep_model: payload.deep_model || 'gpt-4.1',
        research_depth: payload.research_depth || 'standard',
        workflow: demoMode ? 'DEMO' : 'BACKTEST',
      };
      const estRes = await apiFetch('/api/forex/backtest/estimate', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(estReq),
      });
      const est = estRes.ok ? await estRes.json() : null;
      let proceed = true;
      if (est) {
        const analyses = est.expected_analyses_count ?? 'Unavailable';
        const calls = est.estimated_llm_calls ?? est.estimated_calls ?? 'Unavailable';
        const inputTokens = est.estimated_input_tokens ?? 'Unavailable';
        const outputTokens = est.estimated_output_tokens ?? 'Unavailable';
        const tokens = est.estimated_tokens ?? est.estimated_token_usage ?? 'Unavailable';
        const cost = est.estimated_cost_usd ?? est.estimated_cost;
        const costText = cost == null
          ? 'Unavailable (model pricing not configured)'
          : `$${Number(cost).toFixed(4)} (range $${Number(est.estimated_cost_low_usd).toFixed(4)}–$${Number(est.estimated_cost_high_usd).toFixed(4)})`;
        const callsByModel = `${est.estimated_quick_model_calls ?? 'Unavailable'} quick / ${est.estimated_deep_model_calls ?? 'Unavailable'} deep`;
        if (DOM.backtestEstimate) {
          DOM.backtestEstimate.style.display = 'block';
          DOM.backtestEstimate.innerHTML = `<strong>Pre-launch estimate — not actual usage</strong><br>Analyses: ${escapeText(analyses)} · LLM calls: ${escapeText(calls)} (${escapeText(callsByModel)})<br>Input tokens: ${escapeText(inputTokens)} · Output tokens: ${escapeText(outputTokens)} · Total tokens: ${escapeText(tokens)}<br>Estimated cost: ${escapeText(costText)}<br>Sampling interval: ${escapeText(payload.sampling_interval)} bars · Maximum analysis points: ${escapeText(payload.max_analysis_points ?? 'Uncapped')}<br><span class="stat-sub">Retries, tool loops, caching, prompt size, and provider price changes can alter actual cost.</span>`;
        }
        // require confirmation for large jobs
        if (cost != null && cost >= 1) {
          proceed = confirm(`Estimated analyses: ${analyses}\nEstimated LLM calls: ${calls}\nEstimated tokens: ${tokens}\nEstimated cost: $${Number(cost).toFixed(2)}\nSampling interval: ${payload.sampling_interval}\nMaximum analysis points: ${payload.max_analysis_points ?? 'Uncapped'}\n\nProceed with backtest?`);
          if (proceed) payload.confirm_expensive = true;
        }
        // show a small summary in pipeline area
        showToast(`Estimate: ${calls} LLM calls, ${tokens} tokens, ${cost == null ? 'cost unavailable' : '$' + Number(cost).toFixed(4)}`, 'info');
      }
      if (!proceed) {
        setBacktestBusy(false);
        return;
      }
    } catch (e) {
      // proceed but warn
      showToast('Estimate failed — proceeding with caution', 'warning');
    }

    try {
      let res = await apiFetch('/api/forex/backtest/run', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      });
      let data = await res.json();
      const serverError = data && (data.detail || data.error);
      if (res.status === 409 && serverError && serverError.code === 'COST_CONFIRMATION_REQUIRED') {
        const estimate = serverError.estimate || {};
        const confirmed = confirm(`The verified historical dataset requires an estimated ${estimate.estimated_llm_calls ?? 'unknown'} LLM calls and ${estimate.estimated_cost_usd == null ? 'an unavailable cost' : '$' + Number(estimate.estimated_cost_usd).toFixed(2)}. Proceed?`);
        if (!confirmed) return;
        payload.confirm_expensive = true;
        res = await apiFetch('/api/forex/backtest/run', {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
        });
        data = await res.json();
      }
      if (!res.ok) throw new Error(apiErrorMessage(data, 'Backtest request failed'));
      // Refresh runs and open detail
      await loadBacktestRuns();
      const bt_id = data.backtest_id || data.backtest_id;
      if (bt_id) openBacktestDetail(bt_id);
      showToast('Backtest completed', 'success');
    } catch (err) {
      const message = err.message || 'Backtest failed';
      if (DOM.backtestEstimate) {
        DOM.backtestEstimate.style.display = 'block';
        DOM.backtestEstimate.className = 'info-banner error';
        DOM.backtestEstimate.setAttribute('role', 'alert');
        DOM.backtestEstimate.innerHTML = `<div><strong>Historical backtest could not start</strong><br>${escapeText(message)}<br><span class="stat-sub">No result was created and no synthetic data was substituted.</span></div>`;
      }
      showToast('Historical backtest could not start', 'error');
    } finally {
      setBacktestBusy(false);
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
      lastBacktestDetail = data;
      if (data.mode === 'HISTORICAL_AGENT_ABLATION' && data.result) {
        renderAblationStudy(data.result);
        return;
      }
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
          <div class="card-header">
            <h3 class="card-title">${header}</h3>
            <div class="toolbar-group">
              <button type="button" class="btn-secondary btn-sm" data-backtest-list>← Back to Runs</button>
              <button type="button" class="btn-secondary btn-sm" data-backtest-export>Export JSON</button>
            </div>
          </div>
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
          <p><strong>Selection status:</strong> ${escapeText(study.selection_status || 'DESCRIPTIVE_ONLY')} · <strong>Best observed:</strong> ${escapeText(study.best_observed_variant_name || 'Unavailable')} · Observed historical result only; not predictive or statistically validated.</p>
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
    if (!DOM.form.checkValidity()) {
      DOM.form.reportValidity();
      return;
    }
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
      ticker: DOM.ticker.value.trim().toUpperCase(),
      date: DOM.tradeDate.value,
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
    renderAnalysisPending(payload.ticker);

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
      if (DOM.btnCancelAnalysis) DOM.btnCancelAnalysis.style.display = 'block';
      showPipeline(payload.ticker, payload.date || 'today', EQUITIES_PIPELINE_NODES);
      connectEquitiesSSE(data.run_id);
      showToast(`Analysis started for ${payload.ticker}`, 'success');

    } catch (err) {
      showToast(err.message, 'error');
      renderAnalysisFailure(err.message || 'Equity analysis could not start.');
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
        const message = apiErrorMessage(data, data.message || 'Forex analysis failed');
        showToast(message, 'error');
        renderAnalysisFailure(message);
      } catch (_) {
        showToast('Forex analysis stream error', 'error');
        renderAnalysisFailure('The Forex analysis progress stream failed. No completed report is available.');
      }
    });

    eventSource.addEventListener('cancelled', () => {
      if (eventSource) {
        eventSource.close();
        eventSource = null;
      }
      const pct = document.getElementById('progressPercent');
      if (pct) pct.textContent = 'Cancelled';
      resetRunButton();
      showToast('Forex analysis cancelled', 'info');
      renderAnalysisFailure('Analysis cancelled. No new report was produced.', 'cancelled');
      loadRuns();
    });

    eventSource.onerror = () => {
      if (eventSource) {
        eventSource.close();
        eventSource = null;
      }
      resetRunButton();
      renderAnalysisFailure('Connection to the Forex analysis progress stream was lost.');
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

    eventSource.addEventListener('complete', async (e) => {
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
        await loadReport(runId);
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
        const message = apiErrorMessage(data, 'Analysis failed');
        showToast(message, 'error');
        renderAnalysisFailure(message);
      } catch (_) {
        renderAnalysisFailure('The equity analysis failed. No completed report is available.');
      }
    });

    eventSource.addEventListener('cancelled', () => {
      if (eventSource) {
        eventSource.close();
        eventSource = null;
      }
      const pct = document.getElementById('progressPercent');
      if (pct) pct.textContent = 'Cancelled';
      renderAnalysisFailure('Analysis cancelled. No new report was produced.', 'cancelled');
      resetRunButton();
      showToast('Analysis cancelled', 'info');
      loadRuns();
    });

    eventSource.onerror = () => {
      if (eventSource) {
        eventSource.close();
        eventSource = null;
      }
      resetRunButton();
      renderAnalysisFailure('Connection to the equity analysis progress stream was lost.');
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
        const message = res.status === 404 ? 'Forex run not found' : 'Failed to load Forex report';
        if (res.status === 404) {
          showToast(message, 'error');
        } else {
          showToast(message, 'error');
        }
        renderAnalysisFailure(message);
        return false;
      }
      const data = await res.json();
      const reportPayload = data.report || {};
      if (!data.report) {
        renderAnalysisFailure('Completed Forex report is unavailable.');
        return false;
      }
      renderForexReport(reportPayload, data.run);

      // Switch to report tab automatically
      const reportTab = [...DOM.tabs].find(t => t.dataset.view === 'report');
      if (reportTab) reportTab.click();
      return true;
    } catch (_) {
      showToast('Error retrieving Forex report', 'error');
      renderAnalysisFailure('Forex report could not be retrieved.');
      return false;
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
    let action = (prop.action || data.signal || 'UNAVAILABLE').toUpperCase();
    if (risk.decision === 'REJECT') {
      action = 'REJECT';
    }
    const actionClass = action === 'LONG' ? 'long' : action === 'SHORT' ? 'short' : action === 'REJECT' ? 'reject' : 'no_trade';
    const decisionAction = risk.decision || 'UNAVAILABLE';
    const decisionColor = decisionAction === 'APPROVE' ? 'var(--green)' : decisionAction === 'REJECT' ? 'var(--red)' : 'var(--amber)';

    // Risk Checks List
    const passedChecks = (risk.risk_checks_passed && risk.risk_checks_passed.length > 0)
      ? risk.risk_checks_passed
      : [];

    const violations = risk.risk_violations || [];
    const modifications = risk.modifications_required || [];

    // Lessons
    const lessons = Array.isArray(prop.applied_lesson_ids)
      ? prop.applied_lesson_ids.filter(id => typeof id === 'string' && id)
      : (Array.isArray(mem.applied_lesson_ids) ? mem.applied_lesson_ids.filter(id => typeof id === 'string' && id) : []);

    // Entry Zone
    let entryZoneStr = 'Unavailable';
    if (prop.entry_zone_low != null && prop.entry_zone_high != null) {
      entryZoneStr = `${formatForexPrice(prop.entry_zone_low, data.pair, prop.digits)} – ${formatForexPrice(prop.entry_zone_high, data.pair, prop.digits)}`;
    } else if (prop.entry_price != null) {
      entryZoneStr = formatForexPrice(prop.entry_price, data.pair, prop.digits);
    }

    // Estimated Margin
    let marginStr = 'Unavailable';
    if (sizing.margin_required != null) {
      marginStr = `$${Number(sizing.margin_required).toFixed(2)}`;
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
                  Setup: ${escapeText(prop.setup_type || 'Unavailable')}
                </span>
              </h2>
              <div style="font-size:0.75rem; color:var(--text-muted); font-family:var(--font-mono); margin-top:2px;">
                Order: ${escapeText(prop.order_type || 'Unavailable')} • Run ID: ${escapeText(data.run_id || '-')}
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
            <span class="stat-sub">Model confidence: ${prop.confidence == null ? 'Unavailable' : escapeText(`${Number(prop.confidence).toFixed(1)}% raw score`)}</span>
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
              <span class="proposal-meta-value">${formatForexPrice(prop.entry_price, data.pair, prop.digits)}</span>
              <span class="proposal-meta-sub">Zone: ${escapeText(entryZoneStr)}</span>
            </div>
            <div class="proposal-meta-item">
              <span class="proposal-meta-label">Stop Loss</span>
              <span class="proposal-meta-value" style="color:var(--red);">${formatForexPrice(prop.stop_loss, data.pair, prop.digits)}</span>
              <span class="proposal-meta-sub">${prop.sl_pips != null ? `${prop.sl_pips} pips risk` : 'Unavailable'}</span>
            </div>
            <div class="proposal-meta-item">
              <span class="proposal-meta-label">Take Profit 1</span>
              <span class="proposal-meta-value" style="color:var(--green);">${formatForexPrice(prop.take_profit_1, data.pair, prop.digits)}</span>
              <span class="proposal-meta-sub">${prop.tp_pips != null ? `${prop.tp_pips} pips primary` : 'Unavailable'}</span>
            </div>
            <div class="proposal-meta-item">
              <span class="proposal-meta-label">Take Profit 2</span>
              <span class="proposal-meta-value" style="color:var(--green);">${formatForexPrice(prop.take_profit_2, data.pair, prop.digits)}</span>
              <span class="proposal-meta-sub">${prop.take_profit_2 != null ? 'Stored secondary target' : 'Unavailable'}</span>
            </div>
            <div class="proposal-meta-item">
              <span class="proposal-meta-label">Risk : Reward</span>
              <span class="proposal-meta-value" style="color:var(--cyan);">${prop.risk_reward_ratio != null ? `${Number(prop.risk_reward_ratio).toFixed(2)}:1` : 'Unavailable'}</span>
              <span class="proposal-meta-sub">Min required: ${data.min_rr != null ? `${Number(data.min_rr).toFixed(2)}:1` : 'Unavailable'}</span>
            </div>
            <div class="proposal-meta-item">
              <span class="proposal-meta-label">Risk Allocation</span>
              <span class="proposal-meta-value">${prop.suggested_risk_percent != null ? `${prop.suggested_risk_percent}%` : (risk.max_risk_percent != null ? `${risk.max_risk_percent}%` : 'Unavailable')}</span>
              <span class="proposal-meta-sub">Account equity %</span>
            </div>
            <div class="proposal-meta-item">
              <span class="proposal-meta-label">Approved Size</span>
              <span class="proposal-meta-value" style="color:var(--cyan);">${risk.approved_lot_size != null ? `${risk.approved_lot_size} lots` : (prop.suggested_lot_size != null ? `${prop.suggested_lot_size} lots` : 'Unavailable')}</span>
              <span class="proposal-meta-sub">${sizing.units != null ? `${Number(sizing.units).toLocaleString()} units` : 'Unavailable'}</span>
            </div>
            <div class="proposal-meta-item">
              <span class="proposal-meta-label">Estimated Margin</span>
              <span class="proposal-meta-value">${escapeText(marginStr)}</span>
              <span class="proposal-meta-sub">${sizing.margin_required != null ? 'Stored sizing result' : 'Unavailable'}</span>
            </div>
          </div>
          <div style="display:grid; grid-template-columns:1fr 1fr; gap:0.75rem; margin-top:0.75rem;">
            <div class="proposal-meta-item">
              <span class="proposal-meta-label">Proposal Expiry</span>
              <span style="font-size:0.85rem; font-family:var(--font-mono); color:var(--text-bright);">${escapeText(prop.valid_until || 'Unavailable')}</span>
            </div>
            <div class="proposal-meta-item">
              <span class="proposal-meta-label">Invalidation Condition</span>
              <span style="font-size:0.85rem; color:var(--amber);">${escapeText(prop.invalidation_condition || 'Unavailable')}</span>
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
              <span class="proposal-meta-value" style="color:var(--cyan);">${escapeText(ctx.execution_timeframe || data.execution_timeframe || data.timeframe || 'Unavailable')}</span>
              <span class="proposal-meta-sub">Primary entry chart</span>
            </div>
            <div class="proposal-meta-item">
              <span class="proposal-meta-label">Context Timeframes</span>
              <span class="proposal-meta-value">${escapeText((ctx.context_timeframes || data.context_timeframes || []).join(', ') || 'Unavailable')}</span>
              <span class="proposal-meta-sub">Macro trend alignment</span>
            </div>
            <div class="proposal-meta-item">
              <span class="proposal-meta-label">Active Session</span>
              <span class="proposal-meta-value">${escapeText(ctx.session || 'Unavailable')}</span>
              <span class="proposal-meta-sub">Session liquidity</span>
            </div>
            <div class="proposal-meta-item">
              <span class="proposal-meta-label">Current Spread</span>
              <span class="proposal-meta-value">${ctx.spread_pips != null ? `${ctx.spread_pips} pips` : 'Unavailable'}</span>
              <span class="proposal-meta-sub">Threshold: ${data.max_spread_pips != null ? `${data.max_spread_pips} pips` : 'Unavailable'}</span>
            </div>
            <div class="proposal-meta-item">
              <span class="proposal-meta-label">Volatility (ATR)</span>
              <span class="proposal-meta-value">${ctx.volatility_atr != null ? `${ctx.volatility_atr} pips` : 'Unavailable'}</span>
              <span class="proposal-meta-sub">Expected session range</span>
            </div>
            <div class="proposal-meta-item">
              <span class="proposal-meta-label">News / Event Risk</span>
              <span class="proposal-meta-value" style="color:${ctx.news_risk === 'CLEARED' ? 'var(--green)' : 'var(--amber)'};">${escapeText(ctx.news_risk || 'Unavailable')}</span>
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
                ${escapeText(research.technical || data.technical_report || 'Unavailable')}
              </div>
            </div>
            <div class="research-subcard">
              <div class="research-subcard-title">
                <span>🏛️ Macroeconomic Analysis</span>
              </div>
              <div class="research-subcard-body">
                ${escapeText(research.macro || data.macro_report || 'Unavailable')}
              </div>
            </div>
            <div class="research-subcard">
              <div class="research-subcard-title">
                <span>📰 News &amp; Calendar Flows</span>
              </div>
              <div class="research-subcard-body">
                ${escapeText(research.news || data.news_report || 'Unavailable')}
              </div>
            </div>
            <div class="research-subcard">
              <div class="research-subcard-title" style="color:var(--green);">
                <span>🐂 Bull Thesis</span>
              </div>
              <div class="research-subcard-body">
                ${escapeText(research.bull_case || debate.bull_history || 'Unavailable')}
              </div>
            </div>
            <div class="research-subcard">
              <div class="research-subcard-title" style="color:var(--red);">
                <span>🐻 Bear Thesis</span>
              </div>
              <div class="research-subcard-body">
                ${escapeText(research.bear_case || debate.bear_history || 'Unavailable')}
              </div>
            </div>
            <div class="research-subcard">
              <div class="research-subcard-title" style="color:var(--amber);">
                <span>⚖️ Research Manager Synthesis</span>
              </div>
              <div class="research-subcard-body">
                ${escapeText(research.manager_synthesis || debate.judge_decision || 'Unavailable')}
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
              ${passedChecks.length === 0 ? '<div class="risk-check-item"><span>Risk checks unavailable</span></div>' : ''}
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
            ${(Array.isArray(prov.sources) && prov.sources.length ? prov.sources : ['Unavailable']).map(s => `
              <span class="provenance-pill">
                <span class="dot" style="width:6px; height:6px; background:var(--cyan); border-radius:50%;"></span>
                ${escapeText(s)}
              </span>
            `).join('')}
            <span class="provenance-pill">
              🕒 Cutoff: ${escapeText(prov.analysis_cutoff || data.analysis_cutoff || data.date || 'Unavailable')}
            </span>
            <span class="provenance-pill">
              ⚙️ Synthesized: ${escapeText(prov.generated_at ? new Date(prov.generated_at).toLocaleString() : 'Unavailable')}
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
        return true;
      }
      const data = await res.json().catch(() => ({}));
      const message = apiErrorMessage(data, 'Completed analysis report is unavailable.');
      renderAnalysisFailure(message);
      showToast(message, 'error');
      return false;
    } catch (_) {
      renderAnalysisFailure('Completed analysis report could not be loaded.');
      showToast('Completed analysis report could not be loaded', 'error');
      return false;
    }
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
        diskHistory = data.history || data.reports || [];
        renderCombinedHistory();
      }
    } catch (_) {}
  }

  function renderCombinedHistory() {
    if (!DOM.historyContent) return;
    const seenIds = new Set();
    const combined = [];

    inMemoryRuns.forEach(r => {
      seenIds.add(r.run_id);
      combined.push(r);
    });

    diskHistory.forEach(h => {
      const id = h.live_run_id || h.run_id || h.id;
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

    combined.sort((left, right) => {
      const leftStamp = Date.parse(left.started_at || left.date || left.timestamp || '') || 0;
      const rightStamp = Date.parse(right.started_at || right.date || right.timestamp || '') || 0;
      return rightStamp - leftStamp;
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
    if (DOM.btnCancelAnalysis) {
      DOM.btnCancelAnalysis.style.display = 'none';
      DOM.btnCancelAnalysis.disabled = false;
    }
    currentRunId = null;
    activePipelineType = null;
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
