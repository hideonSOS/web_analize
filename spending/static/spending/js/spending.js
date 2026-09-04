/* 支出の月次推移（ECharts・積み上げ棒）
 *
 * ビューが json_script "sp-monthly" にスペックを埋め、ここは描くだけ
 * （portfolio/dashboard.js と同じスペック駆動の方針）。
 */
(function () {
  var el = document.getElementById('sp-monthly');
  var dom = document.getElementById('sp-monthly-chart');
  if (!el || !dom || typeof echarts === 'undefined') return;

  var spec = JSON.parse(el.textContent);
  var chart = echarts.init(dom);

  function yen(v) { return '¥' + Math.round(v).toLocaleString('ja-JP'); }

  chart.setOption({
    grid: { left: 62, right: 16, top: 34, bottom: 40 },
    legend: {
      top: 0, textStyle: { color: '#9ca3af', fontSize: 11 },
      inactiveColor: '#374151',
    },
    tooltip: {
      trigger: 'axis', confine: true,
      backgroundColor: '#0f172a', borderColor: '#334155',
      textStyle: { color: '#e5e7eb', fontSize: 12 },
      axisPointer: { type: 'shadow', shadowStyle: { color: 'rgba(30,144,255,.08)' } },
      formatter: function (ps) {
        if (!ps.length) return '';
        var total = ps.reduce(function (a, p) { return a + (p.value || 0); }, 0);
        var s = ps[0].axisValue + '<br>';
        ps.forEach(function (p) {
          if (p.value) s += p.marker + p.seriesName + ' ' + yen(p.value) + '<br>';
        });
        return s + '<b>合計 ' + yen(total) + '</b>';
      },
    },
    xAxis: {
      type: 'category', data: spec.months,
      axisLabel: { color: '#6b7280', fontSize: 10 },
      axisLine: { lineStyle: { color: '#334155' } },
    },
    yAxis: {
      type: 'value',
      axisLabel: {
        color: '#6b7280', fontSize: 10,
        formatter: function (v) { return v >= 10000 ? (v / 10000) + '万' : v; },
      },
      splitLine: { lineStyle: { color: '#111827' } },
    },
    // 月数が多いときは直近を表示（ドラッグで過去へ遡れる）
    dataZoom: spec.months.length > 18
      ? [{ type: 'inside', start: 60, end: 100 }, { type: 'slider', height: 14, bottom: 6,
           borderColor: '#334155', fillerColor: 'rgba(30,144,255,.18)',
           textStyle: { color: '#6b7280', fontSize: 9 } }]
      : undefined,
    series: spec.series.map(function (s) {
      return {
        name: s.name, type: 'bar', stack: 'total',
        itemStyle: { color: s.color },
        emphasis: { focus: 'series' },
        data: s.data,
      };
    }),
  });

  // 棒をクリックしたらその月の月次分析へ飛ぶ（一番自然な導線）
  chart.on('click', function (p) {
    if (p && p.name) window.location.href = '/spending/month/?ym=' + encodeURIComponent(p.name);
  });
  chart.getZr().on('click', function (ev) {
    // 棒そのものを外しても、その位置の月へ飛べるようにする
    if (ev.target) return;   // 棒の上なら上の handler が処理する
    var x = [ev.offsetX, ev.offsetY];
    if (!chart.containPixel({ gridIndex: 0 }, x)) return;
    var idx = chart.convertFromPixel({ seriesIndex: 0 }, x)[0];
    var ym = spec.months[Math.round(idx)];
    if (ym) window.location.href = '/spending/month/?ym=' + encodeURIComponent(ym);
  });
  chart.getZr().on('mousemove', function (ev) {
    var over = chart.containPixel({ gridIndex: 0 }, [ev.offsetX, ev.offsetY]);
    chart.getZr().setCursorStyle(over ? 'pointer' : 'default');
  });

  window.addEventListener('resize', function () { chart.resize(); });
})();
