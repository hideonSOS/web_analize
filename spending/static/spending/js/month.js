/* 月内の使い方（日別の棒＋累計の線）
 *
 * 「月末に向けてどう積み上がったか」を見るため、日別だけでなく累計も重ねる。
 * 目安線（点線）は直近月の日別累計の平均で、月末まで予め引く。累計が下なら余裕、上なら使いすぎ。
 * 当月は今日までしか累計を描かない（先を描くと今日の値のまま横一線になり、線が
 * 機能していないように見える。実際に指摘された）。
 */
(function () {
  var el = document.getElementById('sp-daily');
  var dom = document.getElementById('sp-daily-chart');
  if (!el || !dom || typeof echarts === 'undefined') return;

  var spec = JSON.parse(el.textContent);
  var chart = echarts.init(dom);

  function yen(v) { return '¥' + Math.round(v).toLocaleString('ja-JP'); }

  // 目安線: 直近月の日別累計の平均（サーバー側で算出）。月初から月末まで予め引く。
  // ⚠️ 直線（月合計÷日数）に戻さないこと。月内の使い方は毎月同じ形に波打つので、
  // 直線では「今日時点で速いか遅いか」が読めない（ユーザー指摘済み）
  var pace = spec.reference;
  var paceName = (spec.target_source === 'manual' ? '目標の推移' : '平常月の推移');

  chart.setOption({
    grid: { left: 58, right: 58, top: 34, bottom: 32 },
    legend: {
      top: 0, textStyle: { color: '#9ca3af', fontSize: 11 }, inactiveColor: '#374151',
      data: ['日別', '今月の累計', paceName],
    },
    tooltip: {
      trigger: 'axis', confine: true,
      backgroundColor: '#0f172a', borderColor: '#334155',
      textStyle: { color: '#e5e7eb', fontSize: 12 },
      formatter: function (ps) {
        if (!ps.length) return '';
        var s = ps[0].axisValue + '日<br>';
        ps.forEach(function (p) {
          if (p.value === null || p.value === undefined) return;   // 当月の未来日
          if (p.value || p.seriesName !== '日別') s += p.marker + p.seriesName + ' ' + yen(p.value) + '<br>';
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
        name: '今月の累計', type: 'line', yAxisIndex: 1, smooth: false, symbol: 'none',
        lineStyle: { color: '#34d399', width: 2 },
        areaStyle: { color: 'rgba(52,211,153,.08)' },
        data: spec.cumulative,
      },
      {
        name: paceName, type: 'line', yAxisIndex: 1, symbol: 'none',
        lineStyle: { color: '#a78bfa', width: 1.5, type: 'dashed' },
        data: pace,
      },
    ],
  });

  window.addEventListener('resize', function () { chart.resize(); });
})();
