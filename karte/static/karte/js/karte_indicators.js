// カルテ詳細「指標（自動）」セクション（Apache ECharts）。2026-09-16 に旧「銘柄別指標」ページを吸収。
// データはこの銘柄1件分だけ（#indicator-data）。描画は旧 stock_detail.js と同じ（検索・切替は無し）。
(() => {
  const dataEl = document.getElementById('indicator-data');
  const defsEl = document.getElementById('indicator-defs');
  if (!dataEl || !defsEl || typeof echarts === 'undefined') return;
  const d = JSON.parse(dataEl.textContent);
  if (!d) return;
  const defs = JSON.parse(defsEl.textContent);
  const defByKey = new Map(defs.map((x) => [x.key, x]));
  const $ = (id) => document.getElementById(id);
  const AXIS = '#87cefa';
  const GRID = 'rgba(59,130,246,0.12)';
  const TOOLTIP = { backgroundColor: 'rgba(11,18,32,0.95)', borderColor: '#3b82f6', textStyle: { color: '#e5e7eb', fontSize: 12 } };
  const isUS = d.country === 'US';
  const charts = [];

  // 見出し: 現在値と決算期
  const closeEl = $('kt-ind-close');
  // 米国株: ドル表記はそのままに円換算を併記（ユーザー要望 2026-09-16）。為替は d.fx（最新ドル円）
  const fx = isUS && d.fx && d.fx.rate ? d.fx.rate : null;
  const yen = (v) => Math.round(v).toLocaleString() + '円';
  if (closeEl) closeEl.textContent = d.close === null ? '―'
    : (isUS ? '$' + d.close.toLocaleString() + (fx ? `（約${yen(d.close * fx)}）` : '') : d.close.toLocaleString() + '円')
    + (d.price_date ? `（${d.price_date} 終値）` : '');
  const fyEl = $('kt-ind-fy');
  if (fyEl) fyEl.textContent = `決算期: ${d.fy_end}期` + (d.per_basis === 'forecast' ? '（PERは来期予想）' : '（PERは実績TTM）') + (d.basis_note ? `・${d.basis_note}` : '')
    + (fx ? `・$1=${fx.toFixed(2)}円（${d.fx.date}）` : '')
    + (isUS && d.fin_currency && d.fin_currency !== 'USD' ? `・決算は${d.fin_currency === 'JPY' ? '円' : d.fin_currency}建て（PER/PBRは株価を換算して計算）` : '');

  // PER の根拠が国で違う（日本株=来期予想 / 米国株=実績TTM）
  const labelFor = (def) => (def.key === 'per' ? (d.per_basis === 'forecast' ? 'PER（予想）' : 'PER（実績）') : def.label);

  // ---- 6枚のカード（値＋ミニ横棒） ----
  defs.forEach((def, i) => {
    const lb = $('ind-label-' + i); if (lb) lb.textContent = labelFor(def);
    const v = d.ind[def.key];
    const el = $('ind-value-' + i);
    if (v === null || v === undefined) { el.textContent = '算出不可'; el.className = 'ind-value ind-na'; }
    else { el.textContent = v.toFixed(2) + def.unit; el.className = 'ind-value' + (v < 0 ? ' neg' : ''); }
    const neg = v !== null && v < 0;
    const color = v === null ? 'rgba(107,114,128,0.35)' : neg ? 'rgba(239,68,68,0.6)' : 'rgba(59,130,246,0.6)';
    const border = v === null ? '#6b7280' : neg ? '#ef4444' : '#3b82f6';
    const box = $('ind-chart-' + i); if (!box) return;
    const c = echarts.init(box, null, { renderer: 'canvas' });
    c.setOption({
      backgroundColor: 'transparent', animationDuration: 400,
      grid: { left: 4, right: 12, top: 6, bottom: 20, containLabel: true }, tooltip: { show: false },
      xAxis: { type: 'value', min: def.min, max: def.max, axisLabel: { color: AXIS, fontSize: 10 }, splitLine: { lineStyle: { color: GRID } }, axisLine: { show: false } },
      yAxis: { type: 'category', data: [''], axisLabel: { show: false }, axisTick: { show: false }, axisLine: { show: false } },
      series: [{ type: 'bar', data: [v === null ? 0 : v], barWidth: 20, itemStyle: { color, borderColor: border, borderWidth: 1, borderRadius: [0, 3, 3, 0] } }],
    });
    charts.push(c);
  });

  // ---- 指標の推移（四半期・TTM）折れ線。タブで指標を切替 ----
  const histBox = $('hist-chart');
  let histKey = defs[0].key;
  let histChart = null;
  function renderHist() {
    if (!histBox || !d.hist) return;
    if (!histChart) { histChart = echarts.init(histBox, null, { renderer: 'canvas' }); charts.push(histChart); }
    const def = defByKey.get(histKey);
    histChart.setOption({
      backgroundColor: 'transparent', animationDuration: 500,
      grid: { left: 10, right: 24, top: 16, bottom: 24, containLabel: true },
      tooltip: { trigger: 'axis', ...TOOLTIP, formatter: (ps) => { const p = ps[0]; return `${p.axisValue}<br/>` + (p.value === null || p.value === undefined ? '算出不可' : `${labelFor(def)}: ${p.value}${def.unit}`); } },
      xAxis: { type: 'category', data: d.hist.labels, boundaryGap: false, axisLabel: { color: AXIS, fontSize: 10 }, axisLine: { lineStyle: { color: 'rgba(59,130,246,0.3)' } }, splitLine: { show: false } },
      yAxis: { type: 'value', scale: true, axisLabel: { color: AXIS }, splitLine: { lineStyle: { color: GRID } } },
      series: [{ type: 'line', data: d.hist[histKey], smooth: true, connectNulls: true, symbolSize: 6,
        lineStyle: { color: '#3b82f6', width: 2 }, itemStyle: { color: '#3b82f6' },
        areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: 'rgba(59,130,246,0.35)' }, { offset: 1, color: 'rgba(59,130,246,0.02)' }]) } }],
    }, { replaceMerge: ['series'] });
    document.querySelectorAll('#hist-tabs button').forEach((b) => {
      b.classList.toggle('active', b.dataset.key === histKey);
      if (b.dataset.key === 'per') b.textContent = isUS ? 'PER（実績）' : 'PER（予想）';
    });
  }
  document.querySelectorAll('#hist-tabs button').forEach((b) => b.addEventListener('click', () => { histKey = b.dataset.key; renderHist(); }));
  renderHist();

  // ---- 業績推移（億円／百万ドル）グループ棒 ----
  const trendBox = $('trend-chart');
  if (trendBox && d.trend) {
    const trendChart = echarts.init(trendBox, null, { renderer: 'canvas' });
    charts.push(trendChart);
    // 米国株で為替があれば Y 軸は億円（百万ドル × レート ÷ 100 = 億円）。tooltip にドルも併記
    // 財務が USD のときだけ換算。JPY 建て（PayPay 等の ADR）は元から億円なので触らない
    const toYen = (fx && d.fin_currency === 'USD') ? (v) => (v === null || v === undefined ? null : Math.round(v * fx / 100)) : null;
    const conv = (arr) => (toYen ? arr.map(toYen) : arr);
    const unitName = toYen ? '億円' : (d.trend_unit || '億円');
    const KEY = { '売上高': 'sales', '営業利益': 'op', '純利益': 'np' };
    // 前年がごく小さい黒字だと桁外れ（PayPay 24/4–25/3 の営業利益 +322,718%）になるので 1,000% 以上は丸める
    const pct = (v) => (v === null || v === undefined ? '—' : (v >= 1000 ? '+999%超' : (v > 0 ? '+' : '') + v.toFixed(1) + '%'));
    const cls = (v) => (v === null || v === undefined ? 'kt-na' : (v > 0 ? 'up' : (v < 0 ? 'down' : '')));
    const amt = (v) => (v === null || v === undefined ? '—' : v.toLocaleString());
    // 四半期（直近の決算を含む・既定）／通期 をタブで切替（ユーザー指摘 2026-09-16: 通期だけでは判断に使えず、
    // 前年同期比も比べられない）
    let mode = (d.trend_q && d.trend_q.labels && d.trend_q.labels.length) ? 'q' : 'fy';
    function renderTrend() {
      const src = mode === 'q' ? d.trend_q : d.trend;
      const fmt = (v, i, seriesName) => {
        if (v === null || v === undefined) return '―';
        const key = KEY[seriesName];
        const yoy = src['yoy_' + key][i];
        const tail = yoy === null || yoy === undefined ? '' : `　前年同期比 ${pct(yoy)}`;
        if (!toYen) return v.toLocaleString() + (d.trend_unit || '億円') + tail;
        const usd = src[key][i];
        return v.toLocaleString() + '億円（$' + (usd === null ? '―' : usd.toLocaleString()) + 'M）' + tail;
      };
      trendChart.setOption({
        backgroundColor: 'transparent', animationDuration: 500,
        legend: { textStyle: { color: '#e5e7eb' }, top: 0 },
        grid: { left: 10, right: 20, top: 36, bottom: 24, containLabel: true },
        tooltip: { trigger: 'axis', ...TOOLTIP, axisPointer: { type: 'shadow' },
          formatter: (ps) => ps[0].axisValue + (mode === 'q' ? '（3か月）' : '（12か月）') + (fx ? `（$1=${fx.toFixed(2)}円）` : '') + '<br/>'
            + ps.map((p) => `${p.marker}${p.seriesName}: ${fmt(p.value, p.dataIndex, p.seriesName)}`).join('<br/>') },
        xAxis: { type: 'category', data: src.labels, axisLabel: { color: AXIS, fontSize: 10 }, axisLine: { lineStyle: { color: 'rgba(59,130,246,0.3)' } } },
        yAxis: { type: 'value', axisLabel: { color: AXIS, formatter: (v) => v.toLocaleString() }, name: unitName, nameTextStyle: { color: AXIS, fontSize: 11 }, splitLine: { lineStyle: { color: GRID } } },
        series: [
          { name: '売上高', type: 'bar', data: conv(src.sales), itemStyle: { color: 'rgba(59,130,246,0.7)', borderRadius: [3, 3, 0, 0] } },
          { name: '営業利益', type: 'bar', data: conv(src.op), itemStyle: { color: 'rgba(250,204,21,0.7)', borderRadius: [3, 3, 0, 0] } },
          { name: '純利益', type: 'bar', data: conv(src.np), itemStyle: { color: 'rgba(34,197,94,0.7)', borderRadius: [3, 3, 0, 0] } },
        ],
      }, { replaceMerge: ['series', 'xAxis'] });
      document.querySelectorAll('#trend-tabs button').forEach((b) => b.classList.toggle('active', b.dataset.mode === mode));
      // 前年同期比の表（新しい期が上）。金額はグラフと同じ単位（換算後）
      const tbl = $('trend-table');
      if (tbl) {
        const S = conv(src.sales), O = conv(src.op), N = conv(src.np);
        let h = `<thead><tr><th>期間</th><th>売上高<i>${unitName}</i></th><th>前年比</th><th>営業利益</th><th>前年比</th><th>利益率</th><th>純利益</th><th>前年比</th></tr></thead><tbody>`;
        for (let i = src.labels.length - 1; i >= 0; i--) {
          h += `<tr><td>${src.labels[i]}</td>`
            + `<td class="num">${amt(S[i])}</td><td class="num ${cls(src.yoy_sales[i])}">${pct(src.yoy_sales[i])}</td>`
            + `<td class="num">${amt(O[i])}</td><td class="num ${cls(src.yoy_op[i])}">${pct(src.yoy_op[i])}</td>`
            + `<td class="num">${src.margin[i] === null || src.margin[i] === undefined ? '—' : src.margin[i].toFixed(1) + '%'}</td>`
            + `<td class="num">${amt(N[i])}</td><td class="num ${cls(src.yoy_np[i])}">${pct(src.yoy_np[i])}</td></tr>`;
        }
        tbl.innerHTML = h + '</tbody>';
      }
    }
    document.querySelectorAll('#trend-tabs button').forEach((b) => b.addEventListener('click', () => { mode = b.dataset.mode; renderTrend(); }));
    renderTrend();
  }
  window.addEventListener('resize', () => charts.forEach((c) => c.resize()));
})();
