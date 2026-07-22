// 画像アップロード: ファイル選択後に選んだファイル名をボタンに表示する
(() => {
  const pairs = [
    ['kt-exec-photo-input', 'kt-file-label', '＋ 写真を選ぶ'],
    ['kt-shot-input', 'kt-shot-label', '＋ 画像を選ぶ'],
  ];
  pairs.forEach(([inputId, labelId, defaultText]) => {
    const input = document.getElementById(inputId);
    const label = document.getElementById(labelId);
    if (!input || !label) return;
    input.addEventListener('change', () => {
      label.textContent = input.files.length ? input.files[0].name : defaultText;
    });
  });
})();

// カルテ詳細: セクションの並び替え（銘柄ごとに順番をカスタマイズできる）
// ハンドル(.kt-drag-handle)を掴んだ時だけドラッグする（テキスト選択を妨げないため）。
// 離した時点で新しい順番をサーバへ保存する（ページ内に保存ボタンは増やさない）。
(() => {
  const box = document.getElementById('kt-sections');
  if (!box || typeof Sortable === 'undefined') return;
  const url = box.dataset.reorderUrl;

  function csrfToken() {
    const m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    if (m) return decodeURIComponent(m[1]);
    const input = document.querySelector('[name=csrfmiddlewaretoken]');
    return input ? input.value : '';
  }

  Sortable.create(box, {
    handle: '.kt-drag-handle',
    animation: 150,
    ghostClass: 'kt-sortable-ghost',
    chosenClass: 'kt-sortable-chosen',
    onEnd() {
      const order = [...box.querySelectorAll('.kt-sortable')].map((el) => el.dataset.key);
      fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
        body: JSON.stringify({ order }),
      }).then((r) => {
        if (!r.ok) console.error('セクション順の保存に失敗しました', r.status);
      }).catch((e) => console.error('セクション順の保存に失敗しました', e));
      // KPIグラフ(ECharts)は移動後にサイズが変わることがあるので測り直させる
      window.dispatchEvent(new Event('resize'));
    },
  });
})();

// カルテ詳細: 3年の株価推移（終値ベース）。1年高値/安値を補助線で重ねる
(() => {
  const el = document.getElementById('price-data');
  const box = document.getElementById('kt-price-chart');
  if (!el || !box) return;
  const d = JSON.parse(el.textContent);
  if (!d || !d.values || !d.values.length) return;

  // 1年レンジの上下を水平線で示す（今どのあたりを買おうとしているかの目安）
  const marks = [];
  if (d.high_1y != null) marks.push({ yAxis: d.high_1y, name: '1年高値' });
  if (d.low_1y != null) marks.push({ yAxis: d.low_1y, name: '1年安値' });

  const chart = echarts.init(box, null, { renderer: 'canvas' });
  chart.setOption({
    backgroundColor: 'transparent',
    animationDuration: 500,
    grid: { left: 10, right: 16, top: 16, bottom: 20, containLabel: true },
    tooltip: {
      trigger: 'axis',
      backgroundColor: 'rgba(11,18,32,0.95)',
      borderColor: '#3b82f6',
      textStyle: { color: '#e5e7eb', fontSize: 12 },
    },
    xAxis: {
      type: 'category',
      data: d.dates,
      axisLabel: { color: '#87cefa', fontSize: 10 },
      axisLine: { lineStyle: { color: 'rgba(59,130,246,0.3)' } },
    },
    yAxis: {
      type: 'value',
      scale: true,           // 株価は0から描くと動きが潰れるため0起点にしない
      axisLabel: { color: '#87cefa', fontSize: 10 },
      splitLine: { lineStyle: { color: 'rgba(59,130,246,0.12)' } },
    },
    series: [{
      type: 'line',
      data: d.values,
      showSymbol: false,
      smooth: false,
      lineStyle: { width: 1.5, color: '#3b82f6' },
      areaStyle: { color: 'rgba(59,130,246,0.12)' },
      markLine: marks.length ? {
        silent: true,
        symbol: 'none',
        lineStyle: { color: '#fcd34d', type: 'dashed', width: 1 },
        label: { color: '#fcd34d', fontSize: 10, formatter: '{b}' },
        data: marks,
      } : undefined,
    }],
  });

  window.addEventListener('resize', () => chart.resize());
})();

// カルテ詳細: 手入力KPIの時系列グラフ（Apache ECharts）
// KPI名ごとに1系列。期のラベルは各KPIの入力順（文字列ソート）に従う。
(() => {
  const el = document.getElementById('kpi-data');
  const box = document.getElementById('kt-kpi-chart');
  if (!el || !box) return;
  const groups = JSON.parse(el.textContent);
  if (!groups.length) return;

  // 全KPIの期を統合してx軸にする
  const periods = [...new Set(groups.flatMap((g) => g.periods))].sort();
  const palette = ['#3b82f6', '#facc15', '#22c55e', '#f472b6', '#38bdf8', '#fb923c'];

  const series = groups.map((g, i) => {
    const map = new Map(g.periods.map((p, j) => [p, g.values[j]]));
    return {
      name: g.unit ? `${g.name}（${g.unit}）` : g.name,
      type: 'line',
      data: periods.map((p) => (map.has(p) ? map.get(p) : null)),
      connectNulls: true,
      smooth: true,
      symbolSize: 6,
      lineStyle: { width: 2, color: palette[i % palette.length] },
      itemStyle: { color: palette[i % palette.length] },
    };
  });

  const chart = echarts.init(box, null, { renderer: 'canvas' });
  chart.setOption({
    backgroundColor: 'transparent',
    animationDuration: 500,
    legend: { textStyle: { color: '#e5e7eb', fontSize: 11 }, top: 0, type: 'scroll' },
    grid: { left: 10, right: 20, top: 40, bottom: 24, containLabel: true },
    tooltip: {
      trigger: 'axis',
      backgroundColor: 'rgba(11,18,32,0.95)',
      borderColor: '#3b82f6',
      textStyle: { color: '#e5e7eb', fontSize: 12 },
    },
    xAxis: {
      type: 'category',
      data: periods,
      axisLabel: { color: '#87cefa', fontSize: 10 },
      axisLine: { lineStyle: { color: 'rgba(59,130,246,0.3)' } },
    },
    yAxis: {
      type: 'value',
      scale: true,
      axisLabel: { color: '#87cefa' },
      splitLine: { lineStyle: { color: 'rgba(59,130,246,0.12)' } },
    },
    series,
  });

  window.addEventListener('resize', () => chart.resize());
})();
