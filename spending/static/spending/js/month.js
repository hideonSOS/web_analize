/* 月内の使い方（日別の棒＋目安線）
 *
 * 縦軸は金額、横軸は日。棒＝今月その日に使った額、点線＝平常月のその日の平均
 * （直近12か月・3日移動平均。サーバー側で算出し月末まで予め引く）。
 * 棒が点線を越えた日は赤。**スケールは1つだけ**。
 * ⚠️ 累計線や右軸を足さないこと。日別と累計はスケールが2桁違い、同じグラフに
 * 混ぜると読めない（実際に指摘された）。直線の目安（月合計÷日数）にもしないこと
 * （家賃・カード引き落としの日が必ず越えて指標にならない）。
 */
(function () {
  var el = document.getElementById('sp-daily');
  var dom = document.getElementById('sp-daily-chart');
  if (!el || !dom || typeof echarts === 'undefined') return;

  var spec = JSON.parse(el.textContent);
  var chart = echarts.init(dom);

  function yen(v) { return '¥' + Math.round(v).toLocaleString('ja-JP'); }
  var refName = (spec.target_source === 'manual' ? '目標の日別配分' : '平常月の日別平均');

  var bars = spec.values.map(function (v, i) {
    if (v === null || v === undefined) return null;         // 当月の未来日
    var over = v > (spec.reference[i] || 0);
    return { value: v, itemStyle: { color: over ? '#f87171' : '#1e90ff' } };
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
          if (p.value === null || p.value === undefined) return;
          var v = (typeof p.value === 'object') ? p.value.value : p.value;
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
        name: refName, type: 'line', symbol: 'none', smooth: true,
        lineStyle: { color: '#a78bfa', width: 1.5, type: 'dashed' },
        data: spec.reference,
      },
    ],
  });

  window.addEventListener('resize', function () { chart.resize(); });
})();
