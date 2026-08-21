// 出来高急増ランキング横棒グラフ（Apache ECharts）
// バーの長さ = z-score（標準化された異常度）。ツールチップに倍率とΦ(z)を表示
(() => {
  const el = document.getElementById('chart-data');
  if (!el) return;
  const data = JSON.parse(el.textContent);
  if (!data.labels || !data.labels.length) return;

  // 表の出来高セルをカンマ区切りに整形
  document.querySelectorAll('.mc-table td[data-num]').forEach((td) => {
    td.textContent = Number(td.dataset.num).toLocaleString();
  });

  const wrap = document.querySelector('.mc-chart-wrap');
  wrap.style.height = Math.max(300, data.labels.length * 26 + 60) + 'px';

  // y軸の銘柄ラベルは長い（コード＋社名）。スマホでは幅を食って棒が潰れるので、
  // 画面幅に応じてラベル列の上限幅を絞り、はみ出しは「…」で切り詰める。
  // 省略された社名はツールチップに全文が出るため情報は失われない。
  const yAxisLabel = () => {
    // デスクトップは従来どおり全文表示（containLabelが自動で幅を確保）
    if (window.innerWidth >= 640) return { color: '#e5e7eb', fontSize: 11 };
    // スマホはラベル列を画面幅の28%までに絞り、はみ出しは「…」で切り詰める
    return {
      color: '#e5e7eb',
      fontSize: 10,
      width: Math.round(window.innerWidth * 0.28),
      overflow: 'truncate',
      ellipsis: '…',
    };
  };

  const chart = echarts.init(document.getElementById('vol-chart'), null, { renderer: 'canvas' });
  chart.setOption({
    backgroundColor: 'transparent',
    animationDuration: 600,
    grid: { left: 8, right: 24, top: 10, bottom: 46, containLabel: true },
    tooltip: {
      trigger: 'item',
      backgroundColor: 'rgba(11,18,32,0.95)',
      borderColor: '#facc15',
      textStyle: { color: '#e5e7eb', fontSize: 12 },
      formatter: (p) => {
        const i = p.dataIndex;
        return `${data.labels[i]}<br/>` +
          `z=${data.z[i]} ／ 平均比 ${data.ratios[i]}倍<br/>` +
          `Φ(z)=${data.p[i]}%<br/>` +
          `<span style="color:#9ca3af">${data.sectors[i]}</span>`;
      },
    },
    xAxis: {
      type: 'value',
      name: '異常度 z（σ）',
      nameLocation: 'middle',
      nameGap: 28,
      nameTextStyle: { color: '#87cefa', fontSize: 12 },
      axisLabel: { color: '#87cefa' },
      splitLine: { lineStyle: { color: 'rgba(59,130,246,0.15)' } },
      axisLine: { lineStyle: { color: 'rgba(59,130,246,0.3)' } },
    },
    yAxis: {
      type: 'category',
      data: data.labels,
      inverse: true,          // 1位を上に表示
      axisLabel: yAxisLabel(),
      axisTick: { show: false },
      axisLine: { lineStyle: { color: 'rgba(59,130,246,0.3)' } },
    },
    series: [{
      type: 'bar',
      data: data.z,
      barMaxWidth: 18,
      itemStyle: {
        color: 'rgba(250,204,21,0.5)',
        borderColor: '#facc15',
        borderWidth: 1,
        borderRadius: [0, 3, 3, 0],
      },
      emphasis: {
        itemStyle: {
          color: 'rgba(250,204,21,0.8)',
          shadowBlur: 12,
          shadowColor: 'rgba(250,204,21,0.6)',
        },
      },
    }],
  });

  // 画面回転・リサイズでラベル幅を取り直す（縦横切替に追従）
  window.addEventListener('resize', () => {
    chart.setOption({ yAxis: { axisLabel: yAxisLabel() } });
    chart.resize();
  });
})();
