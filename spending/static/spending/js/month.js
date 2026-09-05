/* 月内の使い方（日別の棒＋平均支出額の横線）
 *
 * 縦軸は金額、横軸は日。棒＝今月その日に使った額、横線＝平常月の1日あたり平均支出額
 * （サーバー側で算出。目標額があれば 目標額÷日数）。線を越えた日の棒は赤。
 * **スケールは1つ、目安線は水平線1本だけ**。
 * ⚠️ 累計線や右軸を足さないこと（スケールが2桁違って読めない、と指摘された）。
 * ⚠️ 日ごとに違う目安（日別平均の曲線）にしないこと（「意味が分からない、平均支出額で
 *    いい」と指摘された）。
 */
(function () {
  var el = document.getElementById('sp-daily');
  var dom = document.getElementById('sp-daily-chart');
  if (!el || !dom || typeof echarts === 'undefined') return;

  var spec = JSON.parse(el.textContent);
  var chart = echarts.init(dom);

  function yen(v) { return '¥' + Math.round(v).toLocaleString('ja-JP'); }
  var refName = (spec.target_source === 'manual' ? '目標（1日あたり）' : '平均支出額（1日あたり）');

  var bars = spec.values.map(function (v) {
    if (v === null || v === undefined) return null;         // 当月の未来日
    return { value: v, itemStyle: { color: v > spec.avg_daily ? '#f87171' : '#1e90ff' } };
  });

  chart.setOption({
    grid: { left: 58, right: 16, top: 34, bottom: 32 },
    legend: {
      top: 0, textStyle: { color: '#9ca3af', fontSize: 11 }, inactiveColor: '#374151',
      data: ['今月の日別', refName],
    },
    tooltip: {
      trigger: 'axis', confine: true,
      backgroundColor: '#0f172a', borderColor: '#334155',
      textStyle: { color: '#e5e7eb', fontSize: 12 },
      formatter: function (ps) {
        if (!ps.length) return '';
        var s = ps[0].axisValue + '日<br>';
        ps.forEach(function (p) {
          var v = (p.value !== null && typeof p.value === 'object') ? p.value.value : p.value;
          if (v === null || v === undefined) return;
          s += p.marker + p.seriesName + ' ' + yen(v) + '<br>';
        });
        return s;
      },
    },
    xAxis: {
      type: 'category', data: spec.days,
      axisLabel: { color: '#6b7280', fontSize: 10 },
      axisLine: { lineStyle: { color: '#334155' } },
    },
    yAxis: {
      type: 'value', name: '円', nameTextStyle: { color: '#6b7280', fontSize: 10 },
      axisLabel: { color: '#6b7280', fontSize: 10,
        formatter: function (v) { return v >= 10000 ? (v / 10000) + '万' : v; } },
      splitLine: { lineStyle: { color: '#111827' } },
    },
    series: [
      { name: '今月の日別', type: 'bar', data: bars },
      {
        name: refName, type: 'line', symbol: 'none',
        lineStyle: { color: '#fbbf24', width: 1.5, type: 'dashed' },
        data: spec.days.map(function () { return spec.avg_daily; }),
      },
    ],
  });

  window.addEventListener('resize', function () { chart.resize(); });
})();
