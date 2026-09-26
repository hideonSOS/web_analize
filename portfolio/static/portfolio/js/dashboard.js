/* 資産ダッシュボードのドーナツ描画（ECharts・スペック駆動）
 *
 * ビュー(views.index)が json_script "pf-donuts" に仕様を埋め込み、
 * ここは仕様を読んで描くだけ（マクロページの macro.js と同じ方針）。
 * ホバー/タップで「名前・金額・割合」のツールチップと拡大強調が出る。
 */
(function () {
  var specEl = document.getElementById('pf-donuts');
  if (!specEl || typeof echarts === 'undefined') return;
  var spec = JSON.parse(specEl.textContent);
  var charts = [];

  function yen(v) {
    return '¥' + Math.round(v).toLocaleString('ja-JP');
  }

  function build(domId, rows, center, link) {
    var dom = document.getElementById(domId);
    if (!dom || !rows || !rows.length) return;
    var total = rows.reduce(function (a, r) { return a + r.value; }, 0);
    var chart = echarts.init(dom);
    var option = {
      tooltip: {
        trigger: 'item',
        confine: true,             // スマホで画面外にはみ出さないように
        backgroundColor: '#0f172a',
        borderColor: '#334155',
        textStyle: { color: '#e5e7eb', fontSize: 12 },
        formatter: function (p) {
          var pct = total ? (p.value / total * 100).toFixed(1) : '0.0';
          return p.name + '<br>' + yen(p.value) + '（' + pct + '%）';
        },
      },
      series: [{
        type: 'pie',
        radius: ['60%', '90%'],
        label: { show: false },
        labelLine: { show: false },
        itemStyle: { borderColor: '#0b1220', borderWidth: 2 },
        emphasis: {
          scale: true,
          scaleSize: 5,
          itemStyle: { shadowBlur: 12, shadowColor: 'rgba(30,144,255,.5)' },
        },
        data: rows.map(function (r) {
          return { name: r.name, value: r.value, itemStyle: { color: r.color } };
        }),
      }],
    };
    if (center) {
      // 中央の「現金比率 / X%」。title の text(小・上) + subtext(大・下) で2段にする
      option.title = {
        text: center.label,
        subtext: center.value,
        left: 'center',
        top: '34%',
        itemGap: 2,
        textStyle: { color: '#6b7280', fontSize: 10, fontWeight: 'normal' },
        subtextStyle: { color: '#e5e7eb', fontSize: 22, fontWeight: 'bold' },
      };
    }
    chart.setOption(option);
    // リンク付きドーナツはクリックで対応する分析ページへ遷移する
    if (link) {
      dom.style.cursor = 'pointer';
      chart.on('click', function () { window.location.href = link; });
    }
    charts.push(chart);
  }

  if (spec.main) {
    build('pf-donut-main', spec.main.rows,
          { label: spec.main.center_label, value: spec.main.center_value }, null);
  }
  (spec.subs || []).forEach(function (sub, i) {
    build('pf-donut-sub' + i, sub.rows, null, sub.link || null);
  });

  window.addEventListener('resize', function () {
    charts.forEach(function (c) { c.resize(); });
  });
})();

/* 個別株分析ページの散布図（構成比×損益率・点の大きさ=評価額） */
(function () {
  var specEl = document.getElementById('pf-scatter-spec');
  var dom = document.getElementById('pf-scatter');
  if (!specEl || !dom || typeof echarts === 'undefined') return;
  var spec = JSON.parse(specEl.textContent);
  if (!spec.points || !spec.points.length) {
    dom.innerHTML = '<div style="color:#6b7280; font-size:13px; padding:40px 0; text-align:center">' +
                    '損益率を計算できる銘柄がまだありません</div>';
    return;
  }
  var maxV = Math.max.apply(null, spec.points.map(function (p) { return p.value; }));
  // 4つの区画: 縦線＝均等に持ったときの構成比（100÷銘柄数）、横線＝損益 0%。
  // 区画ごとに色と名前を直接描く（2026-09-27 ユーザー指摘: 見出し右端の注記が「右上」の説明に読めて、
  // 一番儲かっている PFE が見直し候補に見えた。注記ではなくグラフの中で区画を名指しする）
  var eq = spec.equal_weight;
  var ws = spec.points.map(function (p) { return p.weight; });
  var ps = spec.points.map(function (p) { return p.pnl_pct; });
  var xMax = Math.ceil(Math.max.apply(null, ws.concat([eq * 2])) * 1.12 / 5) * 5;
  var yHi = Math.max.apply(null, ps.concat([5])) * 1.15, yLo = Math.min.apply(null, ps.concat([-5])) * 1.15;
  var yStep = Math.max(5, Math.ceil((yHi - yLo) / 6 / 5) * 5);     // 目盛りが 0 を通るきりの良い間隔
  var yMax = Math.ceil(yHi / yStep) * yStep, yMin = Math.floor(yLo / yStep) * yStep;
  function area(x0, y0, x1, y1, name, color, pos) {
    return [{ coord: [x0, y0], name: name,
              itemStyle: { color: color },
              label: { position: pos, color: '#cbd5e1', fontSize: 11, fontWeight: 'bold' } },
            { coord: [x1, y1] }];
  }
  var chart = echarts.init(dom);
  chart.setOption({
    grid: { left: 48, right: 56, top: 40, bottom: 40 },
    tooltip: {
      trigger: 'item', confine: true,
      backgroundColor: '#0f172a', borderColor: '#334155',
      textStyle: { color: '#e5e7eb', fontSize: 12 },
      formatter: function (p) {
        var d = p.data;
        return d.name + '<br>構成比 ' + d.value[0] + '%／損益率 ' +
               (d.value[1] >= 0 ? '+' : '') + d.value[1] + '%<br>評価額 ¥' +
               Math.round(d.value[2]).toLocaleString('ja-JP');
      },
    },
    xAxis: {
      min: 0, max: xMax, interval: 5,
      name: '構成比%', nameTextStyle: { color: '#6b7280', fontSize: 10 },
      axisLabel: { color: '#6b7280', fontSize: 10 },
      axisLine: { lineStyle: { color: '#334155' } },
      splitLine: { lineStyle: { color: '#111827' } },
    },
    yAxis: {
      min: yMin, max: yMax, interval: yStep,
      name: '損益率%', nameTextStyle: { color: '#6b7280', fontSize: 10 },
      axisLabel: { color: '#6b7280', fontSize: 10 },
      axisLine: { lineStyle: { color: '#334155' } },
      splitLine: { lineStyle: { color: '#111827' } },
    },
    series: [{
      type: 'scatter',
      // 点の面積を評価額に比例させる（最大40px・最小10px）
      symbolSize: function (v) {
        return Math.max(10, Math.sqrt(v[2] / maxV) * 40);
      },
      label: {
        show: true, position: 'top', color: '#87cefa', fontSize: 10,
        // 点の横は短いコード（PFE・2502）。正式名はホバー／タップで出す（名前だと重なって読めない）
        formatter: function (p) { return p.data.code || p.data.name; },
      },
      emphasis: { itemStyle: { shadowBlur: 12, shadowColor: 'rgba(30,144,255,.6)' } },
      markLine: {
        silent: true, symbol: 'none',
        lineStyle: { color: '#475569', type: 'dashed' },
        label: { show: false },
        data: [{ yAxis: 0 }, { xAxis: eq, label: { show: true, formatter: '均等 ' + eq.toFixed(1) + '%',
                                                   color: '#6b7280', fontSize: 10, position: 'end' } }],
      },
      markArea: {
        silent: true,
        data: [
          area(eq, 0, xMax, yMax, '主力で含み益（稼ぎ頭・偏りすぎに注意）', 'rgba(34,197,94,.08)', 'insideTopRight'),
          area(eq, yMin, xMax, 0, '主力で含み損（見直し候補）', 'rgba(239,68,68,.12)', 'insideBottomRight'),
          area(0, 0, eq, yMax, '少額で含み益', 'rgba(34,197,94,.03)', 'insideTopLeft'),
          area(0, yMin, eq, 0, '少額で含み損', 'rgba(239,68,68,.04)', 'insideBottomLeft'),
        ],
      },
      data: spec.points.map(function (p) {
        return {
          name: p.name, code: p.code,
          value: [p.weight, p.pnl_pct, p.value],
          itemStyle: {
            color: p.pnl_pct >= 0 ? 'rgba(74,222,128,.75)' : 'rgba(248,113,113,.75)',
          },
        };
      }),
    }],
  });
  window.addEventListener('resize', function () { chart.resize(); });
})();
