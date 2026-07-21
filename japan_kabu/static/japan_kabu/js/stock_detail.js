// 銘柄別指標ページ（Apache ECharts）
// 全銘柄分のデータはページに一括埋め込み済み。銘柄切替はフロントのみで行い、
// バックエンドへの再アクセスは発生しない（URLはhistory APIで書き換える）。
(() => {
  const all = JSON.parse(document.getElementById('all-data').textContent);
  if (!all.length) return;
  const defs = JSON.parse(document.getElementById('defs-data').textContent);
  const initialCode = JSON.parse(document.getElementById('initial-code').textContent);
  const byCode = new Map(all.map((d) => [d.code, d]));
  const defByKey = new Map(defs.map((d) => [d.key, d]));

  const $ = (id) => document.getElementById(id);
  const AXIS = '#87cefa';
  const GRID = 'rgba(59,130,246,0.12)';
  const TOOLTIP = {
    backgroundColor: 'rgba(11,18,32,0.95)',
    borderColor: '#3b82f6',
    textStyle: { color: '#e5e7eb', fontSize: 12 },
  };

  let histKey = defs[0].key;
  let currentCode = initialCode;
  const charts = [];

  // ---- 指標カードのミニ横棒グラフ（6枚）----
  const indCharts = defs.map((def, i) => {
    const c = echarts.init($('ind-chart-' + i), null, { renderer: 'canvas' });
    c.setOption({
      backgroundColor: 'transparent',
      animationDuration: 400,
      grid: { left: 4, right: 12, top: 6, bottom: 20, containLabel: true },
      tooltip: { show: false },
      xAxis: {
        type: 'value',
        min: def.min,
        max: def.max,
        axisLabel: { color: AXIS, fontSize: 10 },
        splitLine: { lineStyle: { color: GRID } },
        axisLine: { show: false },
      },
      yAxis: {
        type: 'category',
        data: [''],
        axisLabel: { show: false },
        axisTick: { show: false },
        axisLine: { show: false },
      },
      series: [{ type: 'bar', data: [0], barWidth: 20 }],
    });
    charts.push(c);
    return c;
  });

  // ---- 指標の推移（四半期・TTM）折れ線 ----
  const histChart = echarts.init($('hist-chart'), null, { renderer: 'canvas' });
  charts.push(histChart);

  function renderHist() {
    const d = byCode.get(currentCode);
    if (!d || !d.hist) return;
    const def = defByKey.get(histKey);
    histChart.setOption({
      backgroundColor: 'transparent',
      animationDuration: 500,
      grid: { left: 10, right: 24, top: 16, bottom: 24, containLabel: true },
      tooltip: {
        trigger: 'axis',
        ...TOOLTIP,
        formatter: (ps) => {
          const p = ps[0];
          return `${p.axisValue}<br/>` +
            (p.value === null || p.value === undefined
              ? '算出不可'
              : `${def.label}: ${p.value}${def.unit}`);
        },
      },
      xAxis: {
        type: 'category',
        data: d.hist.labels,
        boundaryGap: false,
        axisLabel: { color: AXIS, fontSize: 10 },
        axisLine: { lineStyle: { color: 'rgba(59,130,246,0.3)' } },
        splitLine: { show: false },
      },
      yAxis: {
        type: 'value',
        scale: true,
        axisLabel: { color: AXIS },
        splitLine: { lineStyle: { color: GRID } },
      },
      series: [{
        type: 'line',
        data: d.hist[histKey],
        smooth: true,
        connectNulls: true,
        symbolSize: 6,
        lineStyle: { color: '#3b82f6', width: 2 },
        itemStyle: { color: '#3b82f6' },
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: 'rgba(59,130,246,0.35)' },
            { offset: 1, color: 'rgba(59,130,246,0.02)' },
          ]),
        },
      }],
    }, { replaceMerge: ['series'] });

    const isUS = d.country === 'US';
    document.querySelectorAll('#hist-tabs button').forEach((b) => {
      b.classList.toggle('active', b.dataset.key === histKey);
      // カード側と同じく、PERの根拠を国別に表示する
      if (b.dataset.key === 'per') {
        b.textContent = isUS ? 'PER（実績）' : 'PER（予想）';
      }
    });
  }

  document.querySelectorAll('#hist-tabs button').forEach((b) => {
    b.addEventListener('click', () => { histKey = b.dataset.key; renderHist(); });
  });

  // ---- 業績推移（億円）グループ棒 ----
  const trendChart = echarts.init($('trend-chart'), null, { renderer: 'canvas' });
  charts.push(trendChart);

  // ---- 表示の更新 ----
  function render(code) {
    const d = byCode.get(code);
    if (!d) return;
    currentCode = code;
    const isUS = d.country === 'US';
    $('sd-name').textContent = d.name;
    // 業種が空（米国株）のときに区切り記号だけ残らないようにする
    $('sd-sub').textContent = [d.code, d.market, d.sector].filter(Boolean).join(' ／ ');
    // 通貨を国別に出し分ける（米国株を「円」と表示しない）
    $('sd-close').textContent = d.close === null ? '―'
      : isUS ? '$' + d.close.toLocaleString() : d.close.toLocaleString() + '円';
    $('sd-price-date').textContent = d.price_date ? `（${d.price_date} 終値）` : '';
    $('sd-fy').textContent = `決算期: ${d.fy_end}期`
      + (isUS ? '（PERは実績TTM）' : '');

    // PERの根拠が国で違う（日本株=来期予想 / 米国株=実績TTM）ためラベルを出し分ける
    const labelFor = (def) => (def.key === 'per'
      ? (isUS ? 'PER（実績）' : 'PER（予想）')
      : def.label);

    defs.forEach((def, i) => {
      const lb = $('ind-label-' + i);
      if (lb) lb.textContent = labelFor(def);
      const v = d.ind[def.key];
      const el = $('ind-value-' + i);
      if (v === null) {
        el.textContent = '算出不可';
        el.className = 'ind-value ind-na';
      } else {
        el.textContent = v.toFixed(2) + def.unit;
        el.className = 'ind-value' + (v < 0 ? ' neg' : '');
      }
      const neg = v !== null && v < 0;
      const color = v === null ? 'rgba(107,114,128,0.35)'
        : neg ? 'rgba(239,68,68,0.6)' : 'rgba(59,130,246,0.6)';
      const border = v === null ? '#6b7280' : neg ? '#ef4444' : '#3b82f6';
      indCharts[i].setOption({
        series: [{
          type: 'bar',
          data: [v === null ? 0 : v],
          barWidth: 20,
          itemStyle: { color, borderColor: border, borderWidth: 1, borderRadius: [0, 3, 3, 0] },
        }],
      });
    });

    trendChart.setOption({
      backgroundColor: 'transparent',
      animationDuration: 500,
      legend: { textStyle: { color: '#e5e7eb' }, top: 0 },
      grid: { left: 10, right: 20, top: 36, bottom: 24, containLabel: true },
      tooltip: {
        trigger: 'axis',
        ...TOOLTIP,
        axisPointer: { type: 'shadow' },
        // 単位は国別（日本株=億円 / 米国株=百万ドル）
        valueFormatter: (v) => (v === null ? '―' : v.toLocaleString() + (d.trend_unit || '億円')),
      },
      xAxis: {
        type: 'category',
        data: d.trend.labels,
        axisLabel: { color: AXIS },
        axisLine: { lineStyle: { color: 'rgba(59,130,246,0.3)' } },
      },
      yAxis: {
        type: 'value',
        axisLabel: { color: AXIS, formatter: (v) => v.toLocaleString() },
        name: d.trend_unit || '億円',
        nameTextStyle: { color: AXIS, fontSize: 11 },
        splitLine: { lineStyle: { color: GRID } },
      },
      series: [
        { name: '売上高', type: 'bar', data: d.trend.sales, itemStyle: { color: 'rgba(59,130,246,0.7)', borderRadius: [3, 3, 0, 0] } },
        { name: '営業利益', type: 'bar', data: d.trend.op, itemStyle: { color: 'rgba(250,204,21,0.7)', borderRadius: [3, 3, 0, 0] } },
        { name: '純利益', type: 'bar', data: d.trend.np, itemStyle: { color: 'rgba(34,197,94,0.7)', borderRadius: [3, 3, 0, 0] } },
      ],
    }, { replaceMerge: ['series'] });

    renderHist();
    input.value = `${d.name}（${d.code}）`;
    history.replaceState(null, '', `/japan_kabu/stock/${code}/`);
  }

  // ---- 銘柄名で検索できるドロップダウン ----
  const input = $('stock-search');
  const list = $('stock-list');

  function showMatches(query) {
    const q = query.trim().toLowerCase();
    const matches = (q
      ? all.filter((d) => d.name.toLowerCase().includes(q) || d.code.toLowerCase().startsWith(q))
      : all
    ).slice(0, 15);
    list.innerHTML = '';
    matches.forEach((d) => {
      const item = document.createElement('div');
      item.className = 'sd-item';
      item.textContent = `${d.name}（${d.code}）`;
      // blurより先に確定させるためmousedownで選択する
      item.addEventListener('mousedown', (e) => {
        e.preventDefault();
        render(d.code);
        list.hidden = true;
        input.blur();
      });
      list.appendChild(item);
    });
    list.hidden = matches.length === 0;
  }

  input.addEventListener('input', () => showMatches(input.value));
  input.addEventListener('focus', () => { input.select(); showMatches(''); });
  input.addEventListener('blur', () => { setTimeout(() => { list.hidden = true; }, 150); });

  window.addEventListener('resize', () => charts.forEach((c) => c.resize()));

  render(initialCode);
})();
