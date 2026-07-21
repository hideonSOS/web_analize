// 経営陣の写真: ファイル選択後に選んだファイル名をボタンに表示する
(() => {
  const input = document.getElementById('kt-exec-photo-input');
  const label = document.getElementById('kt-file-label');
  if (!input || !label) return;
  input.addEventListener('change', () => {
    label.textContent = input.files.length ? input.files[0].name : '＋ 写真を選ぶ';
  });
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
