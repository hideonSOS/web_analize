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
  if (closeEl) closeEl.textContent = d.close === null ? '―' : (isUS ? '$' + d.close.toLocaleString() : d.close.toLocaleString() + '円')
    + (d.price_date ? `（${d.price_date} 終値）` : '');
  const fyEl = $('kt-ind-fy');
  if (fyEl) fyEl.textContent = `決算期: ${d.fy_end}期` + (isUS ? '（PERは実績TTM）' : '（PERは来期予想）');

  // PER の根拠が国で違う（日本株=来期予想 / 米国株=実績TTM）
  const labelFor = (def) => (def.key === 'per' ? (isUS ? 'PER（実績）' : 'PER（予想）') : def.label);

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
    trendChart.setOption({
      backgroundColor: 'transparent', animationDuration: 500,
      legend: { textStyle: { color: '#e5e7eb' }, top: 0 },
      grid: { left: 10, right: 20, top: 36, bottom: 24, containLabel: true },
      tooltip: { trigger: 'axis', ...TOOLTIP, axisPointer: { type: 'shadow' }, valueFormatter: (v) => (v === null ? '―' : v.toLocaleString() + (d.trend_unit || '億円')) },
      xAxis: { type: 'category', data: d.trend.labels, axisLabel: { color: AXIS }, axisLine: { lineStyle: { color: 'rgba(59,130,246,0.3)' } } },
      yAxis: { type: 'value', axisLabel: { color: AXIS, formatter: (v) => v.toLocaleString() }, name: d.trend_unit || '億円', nameTextStyle: { color: AXIS, fontSize: 11 }, splitLine: { lineStyle: { color: GRID } } },
      series: [
        { name: '売上高', type: 'bar', data: d.trend.sales, itemStyle: { color: 'rgba(59,130,246,0.7)', borderRadius: [3, 3, 0, 0] } },
        { name: '営業利益', type: 'bar', data: d.trend.op, itemStyle: { color: 'rgba(250,204,21,0.7)', borderRadius: [3, 3, 0, 0] } },
        { name: '純利益', type: 'bar', data: d.trend.np, itemStyle: { color: 'rgba(34,197,94,0.7)', borderRadius: [3, 3, 0, 0] } },
      ],
    });
  }
  window.addEventListener('resize', () => charts.forEach((c) => c.resize()));
})();
