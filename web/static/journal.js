/* Stored journal evidence only: no calculations, simulated events or broker calls. */
(function (root) {
  'use strict';
  const unavailable = 'Unavailable';
  const escape = value => String(value).replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
  const present = value => value !== null && value !== undefined && value !== '';
  function text(value) {
    if (!present(value)) return unavailable;
    if (Array.isArray(value)) return value.length ? value.map(text).join(', ') : 'None recorded';
    if (typeof value === 'object') return Object.keys(value).length ? escape(JSON.stringify(value)) : unavailable;
    return escape(value);
  }
  function number(value, digits = 2) {
    return present(value) && Number.isFinite(Number(value)) ? Number(value).toFixed(digits) : unavailable;
  }
  function time(value) {
    if (!present(value)) return unavailable;
    const date = new Date(value);
    return Number.isFinite(date.getTime()) ? escape(date.toISOString().replace('T', ' ').replace('Z', ' UTC')) : unavailable;
  }
  function fields(rows) {
    return `<dl class="trade-fields">${rows.map(([label, value, kind]) =>
      `<div><dt>${escape(label)}</dt><dd>${kind === 'time' ? time(value) : text(value)}</dd></div>`).join('')}</dl>`;
  }
  function section(title, body, wide = false) {
    return `<section class="trade-section${wide ? ' trade-section-wide' : ''}"><h3>${escape(title)}</h3>${body}</section>`;
  }
  function table(trades) {
    if (!trades.length) return '<p class="journal-state">Journal empty. No recorded trades are available.</p>';
    return `<table class="data-table journal-table"><caption class="sr-only">Recorded Forex trades. Use Details to investigate a trade.</caption>
      <thead><tr>${['Trade ID', 'Pair', 'Direction', 'Status', 'Open Time (UTC)', 'Close Time (UTC)', 'Entry', 'Exit', 'Lots', 'Pips', 'R', 'Net PnL', 'Exit Reason', 'Details'].map(h => `<th scope="col">${h}</th>`).join('')}</tr></thead>
      <tbody>${trades.map(t => `<tr>
        <td>${text(t.trade_id)}</td><td>${text(t.pair)}</td><td>${text(t.action)}</td><td>${text(t.status)}</td>
        <td>${time(t.open_time_utc)}</td><td>${time(t.close_time_utc)}</td><td>${number(t.open_price, 5)}</td>
        <td>${number(t.close_price, 5)}</td><td>${number(t.lots)}</td><td>${number(t.pips_gained, 1)}</td>
        <td>${number(t.r_multiple)}</td><td>${number(t.net_profit)}</td><td>${text(t.exit_reason)}</td>
        <td><button type="button" class="btn-secondary btn-sm" data-trade-id="${escape(t.trade_id)}" aria-label="Details for trade ${escape(t.trade_id)}">Details</button></td>
      </tr>`).join('')}</tbody></table>`;
  }
  function fills(executions) {
    if (!executions.length) return '<p class="journal-state">No execution fills recorded.</p>';
    return `<div class="journal-scroll" tabindex="0" role="region" aria-label="Execution fills"><table class="data-table"><thead><tr>
      ${['Deal ID', 'Time (UTC)', 'Type', 'Price', 'Lots', 'Slippage (pips)', 'Spread (pips)'].map(h => `<th scope="col">${h}</th>`).join('')}
      </tr></thead><tbody>${executions.map(e => `<tr><td>${text(e.deal_id)}</td><td>${time(e.timestamp_utc)}</td>
      <td>${text(e.order_type)}</td><td>${number(e.price, 5)}</td><td>${number(e.volume)}</td>
      <td>${number(e.slippage_pips)}</td><td>${number(e.spread_at_open_pips)}</td></tr>`).join('')}</tbody></table></div>`;
  }
  function detail(data) {
    if (!data || !data.trade) return '<p class="journal-state">No trade detail is available.</p>';
    const t = data.trade, meta = t.metadata || {}, p = data.original_proposal || {};
    const reference = data.proposal || {}, risk = data.risk_decision || {};
    const metrics = data.metrics || {}, m = metrics.is_available === false ? {} : metrics;
    const comparison = data.execution_comparison || {}, reflection = data.reflection || {};
    const ticket = meta.broker_ticket ?? meta.ticket;
    return `<p class="trade-evidence-note">Stored evidence · Times shown in UTC · Missing values are unavailable. Opening this panel does not recalculate results.</p>
      <div class="trade-detail-grid">` +
      section('Identity', fields([
        ['Trade ID', t.trade_id], ['Proposal ID', t.proposal_id], ['Broker ticket', ticket],
        ['Pair', t.pair], ['Direction', t.action], ['Status', t.status],
        ['Setup', p.setup_type ?? reference.setup_type ?? meta.setup_type],
        ['Timeframe', p.timeframe ?? reference.timeframe ?? meta.timeframe], ['Session', meta.session ?? p.session],
        ['Post-close processing', meta.post_close_status],
      ])) +
      section('Original proposal', (data.original_proposal ? '<p class="trade-evidence-note">Immutable saved proposal snapshot</p>' :
        '<p class="journal-state">Unavailable: no immutable proposal snapshot was stored.</p>') + fields([
        ['Proposed entry', p.entry_price], ['Entry zone low', p.entry_zone_low], ['Entry zone high', p.entry_zone_high],
        ['Stop', p.stop_loss], ['TP1', p.take_profit_1], ['TP2', p.take_profit_2],
        ['Proposed risk (%)', p.suggested_risk_percent], ['Proposed lot size', p.suggested_lot_size],
        ['Risk/reward', p.risk_reward_ratio], ['Invalidation', p.invalidation_condition], ['Reasoning', p.reasoning],
        ['Provider', p.provider], ['Quick model', p.quick_model], ['Deep model', p.deep_model],
        ['Prompt version', p.prompt_version], ['Strategy version', p.strategy_version], ['System version', p.system_version],
      ])) +
      section('Risk decision', fields([
        ['Decision', risk.decision ?? risk.action], ['Violations', risk.risk_violations],
        ['Modifications', risk.modifications_required], ['Checks passed', risk.risk_checks_passed],
        ['Approved lots', risk.position_size_lots ?? risk.approved_lot_size],
        ['Approved risk (%)', risk.max_risk_percent ?? risk.risk_percent ?? risk.approved_risk_percent],
        ['Sizing details', risk.position_sizing ?? risk.sizing_details ?? meta.sizing_details], ['Reasoning', risk.executive_rationale ?? risk.reasoning],
      ])) +
      section('Actual execution', fields([
        ['Actual entry', t.open_price], ['Initial size (lots)', meta.initial_lots], ['Journal size (lots)', t.lots],
        ['Open time', t.open_time_utc, 'time'], ['Spread (pips)', comparison.spread_pips ?? meta.spread_at_open_pips],
        ['Commission', t.commission], ['Slippage (pips)', comparison.slippage_pips ?? meta.slippage_pips], ['Broker ticket', ticket],
      ]) + fills(data.executions || [])) +
      section('Exit', fields([
        ['Actual exit', t.close_price], ['Close time', t.close_time_utc, 'time'], ['Exit reason', t.exit_reason],
        ['Commission', t.commission], ['Swap', t.swap], ['Gross PnL', t.gross_profit], ['Net PnL', t.net_profit],
        ['Pips', t.pips_gained], ['R multiple', t.r_multiple], ['Outcome classification', meta.outcome_category],
      ])) +
      section('Excursion metrics', fields([
        ['MFE price', m.mfe_price], ['MFE pips', m.mfe_pips], ['MFE R', m.mfe_r], ['MFE timestamp', m.mfe_time_utc, 'time'],
        ['MAE price', m.mae_price], ['MAE pips', m.mae_pips], ['MAE R', m.mae_r], ['MAE timestamp', m.mae_time_utc, 'time'],
        ['Exit efficiency (%)', m.exit_efficiency_pct], ['Precision', metrics.precision], ['Source', metrics.source],
        ['Resolution', metrics.resolution], ['Unavailable reason', metrics.unavailable_reason ??
          (!data.metrics ? 'No excursion metrics were stored.' : undefined)],
      ])) +
      section('Execution comparison', fields([
        ['Proposed entry', comparison.proposed_entry], ['Actual entry', comparison.actual_entry],
        ['Entry deviation (pips)', comparison.entry_deviation_pips], ['Proposed lots', comparison.proposed_volume],
        ['Actual lots', comparison.actual_volume], ['Volume deviation', comparison.volume_deviation],
        ['Slippage (pips)', comparison.slippage_pips], ['Slippage cost', comparison.slippage_cost],
        ['Timing deviation (seconds)', comparison.timing_deviation_seconds], ['Total execution friction', comparison.total_execution_friction],
      ]) + (data.execution_comparison ? `<details><summary>All stored execution quality fields</summary><pre>${text(JSON.stringify(comparison, null, 2))}</pre></details>` : '')) +
      section('Reflection', fields([
        ['Summary', reflection.summary], ['Rating', reflection.rating], ['Tags', reflection.tags],
      ])) +
      section('Lessons', (data.lessons || []).length ? (data.lessons || []).map(lesson =>
        `<article class="trade-lesson"><h4>${text(lesson.lesson_id)}</h4>${fields([
          ['Directive', lesson.actionable_rule], ['Confidence', lesson.confidence ?? lesson.confidence_score],
          ['Source trade', lesson.source_trade_id ?? lesson.trade_id], ['Source proposal', lesson.proposal_id],
          ['Observation', lesson.observation], ['Root cause', lesson.root_cause], ['Recorded at', lesson.created_at_utc ?? lesson.created_at, 'time'],
        ])}</article>`).join('') : '<p class="journal-state">No lessons recorded for this trade.</p>', true) +
      '</div><section class="trade-section"><h3>Chronological lifecycle timeline</h3><div id="tradeTimeline">' +
      timeline(data.events || []) + '</div></section>';
  }

  function timeline(events) {
    if (!events.length) return '<p class="journal-state">No lifecycle events recorded for this trade.</p>';
    const category = event => {
      const kind = String(event.event_type || '');
      if (/REFLECT|LESSON|LEARNING/.test(kind) || /Reflection|Learning/.test(event.actor || '')) return 'learning';
      if (/MFE|MAE|OUTCOME|ANALYTIC/.test(kind)) return 'analytics';
      if (/CLOSE/.test(kind)) return 'close';
      if (/SL_|TP_|MODIFIED|BREAKEVEN|BREAK_EVEN/.test(kind) && !kind.startsWith('PROPOSAL')) return 'modification';
      if (/RISK|APPROVED|REJECTED/.test(kind)) return 'risk';
      if (/PROPOSAL/.test(kind)) return 'proposal';
      if (/ORDER|DEAL|POSITION|RECONCILIATION/.test(kind)) return 'broker';
      return 'other';
    };
    const stamp = event => Number.isFinite(Date.parse(event.timestamp_utc)) ? Date.parse(event.timestamp_utc) : Infinity;
    const ordered = [...events].sort((a, b) => stamp(a) - stamp(b) || String(a.event_id || '').localeCompare(String(b.event_id || '')));
    const iconFor = c => {
      switch (c) {
        case 'learning': return '🧠';
        case 'analytics': return '📊';
        case 'close': return '🏁';
        case 'modification': return '🔧';
        case 'risk': return '🛡️';
        case 'proposal': return '📋';
        case 'broker': return '🏦';
        default: return '•';
      }
    };

    // Toolbar with compact/expanded toggle, export and print actions
    const toolbar = `<div class="timeline-toolbar" role="toolbar" aria-label="Timeline controls">
        <button type="button" class="btn-sm" data-action="toggle-compact">Compact</button>
        <button type="button" class="btn-sm" data-action="export-json">Export JSON</button>
        <button type="button" class="btn-sm" data-action="print">Print</button>
      </div>`;

    return `${toolbar}<div class="timeline-root">` +
      `<ol class="trade-timeline">${ordered.map(event => {
      const meta = event.metadata || event.payload || {};
      const summary = ['deal_ticket', 'remaining_volume', 'volume_closed', 'rating', 'lessons_extracted', 'reason']
        .filter(key => present(meta[key])).map(key => `${key}: ${String(meta[key]).slice(0, 120)}`).join(' · ');
      const cat = category(event);
      return `<li class="timeline-event timeline-${cat}"><div class="timeline-event-header">
        <span class="timeline-icon">${iconFor(cat)}</span><h4>${text(event.event_type)}</h4><span>${time(event.timestamp_utc)}</span></div>
        <p class="trade-evidence-note">Actor: ${text(event.actor)} · Source: ${text(event.source)} · Event: ${text(event.event_id)}</p>
        <p>${text(event.description)}</p>${fields([
          ['Price', event.price ?? meta.price ?? meta.new_price], ['Volume', event.volume ?? meta.volume ?? meta.volume_closed],
          ['Previous value', event.old_value ?? meta.old_value ?? meta.old_sl ?? meta.old_tp],
          ['New value', event.new_value ?? meta.new_value ?? meta.new_sl ?? meta.new_tp],
        ])}${summary ? `<p>${escape(summary)}</p>` : ''}
        <details><summary>Raw event metadata</summary><pre>${escape(JSON.stringify(event, null, 2))}</pre></details></li>`;
    }).join('')}</ol>`;
  }

  function createController({ request, document: doc }) {
    const dialog = doc.getElementById('tradeDetailDialog');
    const body = doc.getElementById('tradeDetailBody');
    const title = doc.getElementById('tradeDetailTitle');
    const close = doc.getElementById('tradeDetailClose');
    let abort, generation = 0, opener, selected, lastData = null;
    function state(kind, message, retry = false) {
      body.dataset.state = kind;
      body.innerHTML = `<div class="journal-state" role="${kind === 'error' ? 'alert' : 'status'}"><p>${escape(message)}</p>${retry ? '<button type="button" class="btn-secondary" data-retry-trade>Retry loading trade</button>' : ''}</div>`;
    }
    async function loadTradeDetail(tradeId, trigger) {
      selected = tradeId;
      if (!dialog.open) {
        opener = trigger || doc.activeElement;
        dialog.showModal();
        close.focus();
      }
      title.textContent = `Trade ${tradeId}`;
      abort?.abort();
      abort = new AbortController();
      const current = ++generation;
      state('loading', 'Loading stored trade evidence…');
      body.setAttribute('aria-busy', 'true');
      try {
        const response = await request(`/api/forex/journal/trades/${encodeURIComponent(tradeId)}`, { signal: abort.signal });
        const data = await response.json();
        if (current !== generation) return;
        if (response.status === 404) { state('not-found', 'Trade not found. It may no longer exist.'); return; }
        if (!response.ok) {
          state('error', typeof data.error?.message === 'string' ? data.error.message : 'Trade could not be loaded.', true);
          return;
        }
        if (!data.trade) { state('empty', 'No trade detail is available.'); return; }
        body.dataset.state = 'success';
        lastData = data;
        body.innerHTML = detail(data);
      } catch (error) {
        if (current === generation && error.name !== 'AbortError') state('error', 'Network error loading trade details. Please retry.', true);
      } finally {
        if (current === generation) body.setAttribute('aria-busy', 'false');
      }
    }
    close.addEventListener('click', () => dialog.close());
    // Native dialog handles Escape and traps keyboard focus.
    dialog.addEventListener('close', () => { ++generation; abort?.abort(); opener?.focus(); });
    body.addEventListener('click', event => {
      if (event.target.closest('[data-retry-trade]')) return loadTradeDetail(selected);
      const actionBtn = event.target.closest('[data-action]');
      if (actionBtn) {
        const action = actionBtn.dataset.action;
        const win = doc.defaultView || (typeof window !== 'undefined' ? window : null);
        if (action === 'toggle-compact') {
          const root = doc.getElementById('tradeTimeline')?.querySelector('.timeline-root');
          if (root) root.classList.toggle('timeline-compact');
          return;
        }
        if (action === 'export-json') {
          if (!lastData) return;
          try {
            const payload = JSON.stringify(lastData.events || lastData, null, 2);
            const blob = (win && win.Blob) ? new (win.Blob)([payload], { type: 'application/json' }) : null;
            if (blob && win && win.URL) {
              const url = win.URL.createObjectURL(blob);
              const a = doc.createElement('a');
              a.href = url; a.download = `trade-${lastData.trade?.trade_id || 'timeline'}.json`;
              doc.body.appendChild(a); a.click(); a.remove();
              win.URL.revokeObjectURL(url);
            }
          } catch (e) { /* silent */ }
          return;
        }
        if (action === 'print') {
          if (!lastData) return;
          try {
            const eventsHtml = timeline(lastData.events || []);
            const printer = win ? win.open('', '_blank') : null;
            if (printer) {
              printer.document.write(`<html><head><title>Trade timeline</title><style>body{font-family:Arial,Helvetica,sans-serif;padding:12px;color:#111} .trade-timeline{list-style:none;padding-left:0} .timeline-event{margin-bottom:12px;border-left:1px solid #ccc;padding-left:12px}</style></head><body>${eventsHtml}</body></html>`);
              printer.document.close();
              printer.focus();
              printer.print();
            }
          } catch (e) { /* silent */ }
          return;
        }
      }
    });
    return { loadTradeDetail };
  }
  const api = { table, detail, timeline, createController };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.JournalUI = api;
})(typeof window !== 'undefined' ? window : globalThis);
