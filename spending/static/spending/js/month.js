/* 月内の使い方（日別の棒＋累計の線）
 *
 * 「月末に向けてどう積み上がったか」を見るため、日別だけでなく累計も重ねる。
 * 平常月の日割りペース（点線）と比べて、速いか遅いかが分かる。
 */
(function () {
  var el = document.getElementById('sp-daily');
  var dom = document.getElementById('sp-daily-chart');
  if (!el || !dom || typeof echarts === 'undefined') return;

  var spec = JSON.parse(el.textContent);
  var chart = echarts.init(dom);

  function yen(v) { return '¥' + Math.round(v).toLocaleString('ja-JP'); }

  // 平常月のペース（日割り）を累計で引いた基準線
  var pace = spec.days.map(function (d) { return spec.baseline_daily * d; });

  chart.setOption({
    grid: { left: 58, right: 58, top: 34, bottom: 32 },
    legend: {
      top: 0, textStyle: { color: '#9ca3af', fontSize: 11 }, inactiveColor: '#374151',
      data: ['日別', '累計', '平常月のペース'],
    },
    tooltip: {
      trigger: 'axis', confine: true,
      backgroundColor: '#0f172a', borderColor: '#334155',
      textStyle: { color: '#e5e7eb', fontSize: 12 },
      formatter: function (ps) {
        if (!ps.length) return '';
        var s = ps[0].axisValue + '日<br>';
        ps.forEach(function (p) {
          if (p.value) s += p.marker + p.seriesName + ' ' + yen(p.value) + '<br>';
        });
        return s;
      },
    },
    xAxis: {
      type: 'category', data: spec.days,
      axisLabel: { color: '#6b7280', fontSize: 10 },
      axisLine: { lineStyle: { color: '#334155' } },
    },
    yAxis: [
      {
        type: 'value', name: '日別', nameTextStyle: { color: '#6b7280', fontSize: 10 },
        axisLabel: { color: '#6b7280', fontSize: 10,
          formatter: function (v) { return v >= 10000 ? (v / 10000) + '万' : v; } },
        splitLine: { lineStyle: { color: '#111827' } },
      },
      {
        type: 'value', name: '累計', nameTextStyle: { color: '#6b7280', fontSize: 10 },
        axisLabel: { color: '#6b7280', fontSize: 10,
          formatter: function (v) { return v >= 10000 ? (v / 10000) + '万' : v; } },
        splitLine: { show: false },
      },
    ],
    series: [
      { name: '日別', type: 'bar', itemStyle: { color: '#1e90ff' }, data: spec.values },
      {
        name: '累計', type: 'line', yAxisIndex: 1, smooth: true, symbol: 'none',
        lineStyle: { color: '#34d399', width: 2 },
        areaStyle: { color: 'rgba(52,211,153,.08)' },
        data: spec.cumulative,
      },
      {
        name: '平常月のペース', type: 'line', yAxisIndex: 1, symbol: 'none',
        lineStyle: { color: '#6b7280', width: 1, type: 'dashed' },
        data: pace,
      },
    ],
  });

  window.addEventListener('resize', function () { chart.resize(); });
})();
