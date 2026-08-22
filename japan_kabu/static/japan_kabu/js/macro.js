/* マクロ指標ページのチャート描画（ECharts・stock_detail と同じCDN読込）
   チャートの仕様（要素ID・系列・基準線）はビューが組んで json_script（#macro-data）で
   渡す。日米で系列数が違ってもこのファイルは触らなくてよい（スペック駆動）。
   月次の全履歴を渡し、初期表示だけ直近10年（120ヶ月）にズームしておく。 */
(() => {
  const el = document.getElementById('macro-data');
  if (!el || typeof echarts === 'undefined') return;
  const charts = JSON.parse(el.textContent);

  const AXIS = { color: '#9ca3af' };

  charts.forEach((spec) => {
    const dom = document.getElementById(spec.el);
    if (!dom || !spec.series.length || !spec.series[0].data.length) return;

    // カテゴリ軸は最長の系列の日付に合わせ、他の系列は辞書引きで欠測を null にして重ねる
    // （日本のコアコアは1971年開始など、系列ごとに開始年が違うため）
    const longest = spec.series.reduce((a, b) => (b.data.length > a.data.length ? b : a));
    const labels = longest.data.map((r) => r[0]);

    const series = spec.series.map((s) => {
      const by = Object.fromEntries(s.data);
      return {
        name: s.name, type: 'line', showSymbol: false,
        lineStyle: { width: 1.6 }, color: s.color,
        areaStyle: s.area ? { color: 'rgba(74,222,128,.08)' } : undefined,
        data: labels.map((d) => by[d] ?? null),
      };
    });
    if (spec.mark) {
      series[0].markLine = {
        silent: true, symbol: 'none',
        lineStyle: { color: '#facc15', type: 'dashed', width: 1 },
        label: { color: '#facc15', fontSize: 11, formatter: spec.mark.label,
                 position: 'insideEndTop' },
        data: [{ yAxis: spec.mark.v }],
      };
    }

    const c = echarts.init(dom);
    c.setOption({
      backgroundColor: 'transparent',
      grid: { left: 48, right: 16, top: 30, bottom: 56 },
      tooltip: {
        trigger: 'axis',
        backgroundColor: '#111827', borderColor: '#374151',
        textStyle: { color: '#e5e7eb', fontSize: 12 },
        valueFormatter: (v) => (v == null ? '-' : v.toFixed(1) + '%'),
      },
      legend: { textStyle: { color: '#cbd5e1' }, top: 0 },
      xAxis: { type: 'category', data: labels, axisLabel: AXIS,
               axisLine: { lineStyle: { color: '#374151' } } },
      yAxis: { type: 'value', axisLabel: { ...AXIS, formatter: '{value}%' },
               splitLine: { lineStyle: { color: '#1f2937' } } },
      dataZoom: [
        { type: 'slider', height: 18, bottom: 8,
          start: Math.max(0, 100 - (120 / labels.length) * 100), end: 100,
          borderColor: '#374151', backgroundColor: '#0b1220',
          fillerColor: 'rgba(30,144,255,.15)', textStyle: { color: '#6b7280' } },
        { type: 'inside' },
      ],
      series,
    });
    window.addEventListener('resize', () => c.resize());
  });
})();
