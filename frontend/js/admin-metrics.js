/* Phase 6.4 — /admin/metrics dashboard
 * GET /api/v1/admin/metrics/summary?days=N → { period_days, models: [...], winner: <model_name or null> }
 * Shows: period picker (1d/7d/30d), total calls card, winner card, comparison table.
 */
window.AdminMetrics = (function () {
  const root = () => document.getElementById('admin-metrics-root');
  const PERIODS = [1, 7, 30];

  let days = 7;
  let summary = null;

  function el(tag, props = {}, children = []) {
    const e = document.createElement(tag);
    Object.entries(props).forEach(([k, v]) => {
      if (k === 'class') e.className = v;
      else if (k === 'style') e.style.cssText = v;
      else if (k.startsWith('on') && typeof v === 'function') e.addEventListener(k.slice(2), v);
      else if (v !== undefined && v !== null) e.setAttribute(k, v);
    });
    (Array.isArray(children) ? children : [children]).forEach(c => {
      if (c == null) return;
      e.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
    });
    return e;
  }

  async function load() {
    try {
      summary = await api(`/admin/metrics/summary?days=${days}`);
    } catch (e) {
      toast('加载失败: ' + e.message, 'error');
      summary = { period_days: days, models: [], winner: null };
    }
  }

  function periodPicker() {
    const wrap = el('div', { class: 'period-pick' });
    PERIODS.forEach(d => {
      const btn = el('button', { type: 'button' }, `${d}d`);
      if (d === days) btn.classList.add('active');
      btn.addEventListener('click', async () => {
        if (d === days) return;
        days = d;
        await load();
        render();
      });
      wrap.appendChild(btn);
    });
    return wrap;
  }

  function totalCallsCard() {
    const total = (summary?.models || []).reduce((s, m) => s + (m.total_calls || 0), 0);
    const cards = el('div', { class: 'metric-cards' }, [
      el('div', { class: 'stat-card' }, [
        el('div', { class: 'value' }, String(total)),
        el('div', { class: 'label' }, `💬 总请求数 (${summary?.period_days ?? days}d)`),
      ]),
      el('div', { class: 'stat-card' }, [
        el('div', { class: 'value' }, String((summary?.models || []).length)),
        el('div', { class: 'label' }, '🤖 参与模型数'),
      ]),
      el('div', { class: 'stat-card' }, [
        el('div', { class: 'value' }, avgSatisfaction()),
        el('div', { class: 'label' }, '😊 平均满意度'),
      ]),
    ]);
    return cards;
  }

  function avgSatisfaction() {
    const ms = summary?.models || [];
    if (ms.length === 0) return '—';
    const valid = ms.filter(m => typeof m.satisfaction_rate === 'number');
    if (valid.length === 0) return '—';
    const avg = valid.reduce((s, m) => s + m.satisfaction_rate, 0) / valid.length;
    return (avg * 100).toFixed(1) + '%';
  }

  function winnerCard() {
    if (!summary?.winner) {
      return el('div', { class: 'winner-card', style: 'background:var(--gray-200);color:var(--gray-500)' }, [
        el('div', { class: 'label' }, '🏆 胜出模型'),
        el('div', { class: 'value' }, '暂无数据 (需要 ≥10 个反馈样本)'),
      ]);
    }
    // Find the winner row for stats
    const winnerRow = (summary.models || []).find(m => m.model_name === summary.winner);
    return el('div', { class: 'winner-card' }, [
      el('div', { class: 'label' }, '🏆 胜出模型 (按满意度)'),
      el('div', { class: 'value' }, summary.winner),
      winnerRow ? el('div', { style: 'margin-top:6px;font-size:13px;opacity:0.9' },
        `${(winnerRow.satisfaction_rate * 100).toFixed(1)}% 满意度 · ${winnerRow.total_calls} 次调用`) : null,
    ]);
  }

  function tableHeader() {
    return el('div', { class: 'model-row header' }, [
      el('div', {}, '模型'),
      el('div', {}, '调用次数'),
      el('div', {}, '满意度'),
      el('div', {}, '平均延迟'),
    ]);
  }

  function tableRow(m) {
    const rate = typeof m.satisfaction_rate === 'number' ? (m.satisfaction_rate * 100).toFixed(1) + '%' : '—';
    const lat  = typeof m.avg_latency_ms === 'number' ? Math.round(m.avg_latency_ms) + ' ms' : '—';
    const isWinner = summary?.winner && m.model_name === summary.winner;
    return el('div', { class: 'model-row' }, [
      el('div', {}, [
        el('div', { style: 'font-weight:500' }, [
          isWinner ? el('span', { style: 'margin-right:6px' }, '🏆') : null,
          m.model_name,
        ]),
        el('div', { style: 'font-size:11px;color:var(--gray-500)' }, `#${m.model_id}`),
      ]),
      el('div', {}, String(m.total_calls ?? 0)),
      el('div', {}, rate),
      el('div', {}, lat),
    ]);
  }

  async function render() {
    const r = root();
    if (!r) return;
    r.innerHTML = '<div class="admin-spa"><h1>📊 模型指标仪表盘</h1><div>加载中...</div></div>';
    await load();
    r.innerHTML = '';

    const wrap = el('div', { class: 'admin-spa' });
    wrap.appendChild(el('h1', {}, '📊 模型指标仪表盘'));

    const toolbar = el('div', { class: 'toolbar' }, [
      el('span', { style: 'color:var(--gray-500);font-size:13px' }, '时间周期:'),
      periodPicker(),
    ]);
    wrap.appendChild(toolbar);

    wrap.appendChild(totalCallsCard());
    wrap.appendChild(winnerCard());

    wrap.appendChild(el('h2', {}, '模型对比'));

    const models = summary?.models || [];
    wrap.appendChild(tableHeader());
    if (models.length === 0) {
      wrap.appendChild(el('div', { style: 'padding:24px;color:var(--gray-500);text-align:center' }, '此周期内暂无指标数据'));
    } else {
      models.forEach(m => wrap.appendChild(tableRow(m)));
    }

    r.appendChild(wrap);
  }

  return { render };
})();
