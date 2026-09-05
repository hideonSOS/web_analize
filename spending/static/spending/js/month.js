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

/* 理想の支出テンプレートと実際（円グラフ2つ）
 *
 * 左＝理想の構成（Budget の額）、右＝この月の実際。同じ項目は同じ色。
 * ビューが json_script "sp-template" にスペックを埋め、ここは描くだけ。
 * ⚠️ 実際の円は「理想を置いた項目」だけで作る（ビュー側で揃えてある）。
 * 実際が0円の項目は右の円から自然に消えるので、凡例で「理想にあるが実際0」が分かる。
 */
(function () {
  var el = document.getElementById('sp-template');
  var dom = document.getElementById('sp-template-chart');
  if (!el || !dom || typeof echarts === 'undefined') return;

  var spec = JSON.parse(el.textContent);
  if (!spec.items || !spec.items.length) return;
  var chart = echarts.init(dom);

  function yen(v) { return '¥' + Math.round(v).toLocaleString('ja-JP'); }
  function series(name, key, center) {
    return {
      name: name, type: 'pie', radius: ['38%', '68%'], center: center,
      avoidLabelOverlap: true,
      label: { color: '#9ca3af', fontSize: 10, formatter: '{b}\n{d}%' },
      labelLine: { lineStyle: { color: '#374151' } },
      itemStyle: { borderColor: '#0b1220', borderWidth: 2 },
      emphasis: { label: { fontSize: 12, color: '#e5e7eb' } },
      data: spec.items
        .filter(function (i) { return i[key] > 0; })
        .map(function (i) { return { name: i.name, value: i[key], itemStyle: { color: i.color } }; }),
    };
  }

  // 幅が狭い（スマホ）ときは上下に並べる
  function centers() {
    return dom.clientWidth < 560 ? [['50%', '28%'], ['50%', '76%']] : [['26%', '55%'], ['74%', '55%']];
  }
  function titles(c) {
    return [
      { text: '理想の構成', subtext: yen(spec.ideal_total), left: c[0][0], top: 0, textAlign: 'center',
        textStyle: { color: '#e5e7eb', fontSize: 12 }, subtextStyle: { color: '#6b7280', fontSize: 11 } },
      { text: 'この月の実際', subtext: yen(spec.actual_total), left: c[1][0],
        top: dom.clientWidth < 560 ? '50%' : 0, textAlign: 'center',
        textStyle: { color: '#e5e7eb', fontSize: 12 }, subtextStyle: { color: '#6b7280', fontSize: 11 } },
    ];
  }

  function render() {
    var c = centers();
    chart.setOption({
      title: titles(c),
      tooltip: {
        trigger: 'item', confine: true,
        backgroundColor: '#0f172a', borderColor: '#334155', textStyle: { color: '#e5e7eb', fontSize: 12 },
        formatter: function (p) { return p.marker + p.seriesName + ' ' + p.name + '<br>' + yen(p.value) + '（' + p.percent + '%）'; },
      },
      series: [series('理想', 'ideal', c[0]), series('実際', 'actual', c[1])],
    }, true);
  }
  render();
  window.addEventListener('resize', function () { chart.resize(); render(); });
})();
