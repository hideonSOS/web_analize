/* 月内の使い方（今月の支出の累積の棒＋平均支出額の累積の線）
 *
 * ユーザー指示（2026-09-06・明示）: 線＝毎月の平均支出額の累積（avg_daily×日。下がらない）、
 * 棒＝今月の支出の累積。両方とも累積で同じスケール。線を越えた日の棒は赤。
 * ⚠️ 日別の棒・水平線・日ごとの目安曲線・右軸はすべて却下済み。戻さないこと
 */
(function () {
  var el = document.getElementById('sp-daily');
  var dom = document.getElementById('sp-daily-chart');
  if (!el || !dom || typeof echarts === 'undefined') return;

  var spec = JSON.parse(el.textContent);
  var chart = echarts.init(dom);

  function yen(v) { return '¥' + Math.round(v).toLocaleString('ja-JP'); }
  var refName = (spec.target_source === 'manual' ? '目標の累積' : '平均支出額の累積');

  var bars = spec.values.map(function (v, i) {
    if (v === null || v === undefined) return null;         // 当月の未来日
    return { value: v, itemStyle: { color: v > spec.reference[i] ? '#f87171' : '#1e90ff' } };
  });

  chart.setOption({
    grid: { left: 62, right: 16, top: 34, bottom: 32 },
    legend: {
      top: 0, textStyle: { color: '#9ca3af', fontSize: 11 }, inactiveColor: '#374151',
      data: ['今月の支出（累積）', refName],
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
      type: 'value', name: '円（累積）', nameTextStyle: { color: '#6b7280', fontSize: 10 },
      axisLabel: { color: '#6b7280', fontSize: 10,
        formatter: function (v) { return v >= 10000 ? (v / 10000) + '万' : v; } },
      splitLine: { lineStyle: { color: '#111827' } },
    },
    series: [
      { name: '今月の支出（累積）', type: 'bar', data: bars },
      {
        name: refName, type: 'line', symbol: 'none',
        lineStyle: { color: '#fbbf24', width: 2 },
        data: spec.reference,
      },
    ],
  });

  window.addEventListener('resize', function () { chart.resize(); });
})();
