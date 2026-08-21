// 時価総額ランキング横棒グラフ（Apache ECharts）
(() => {
  const el = document.getElementById('chart-data');
  if (!el) return;
  const data = JSON.parse(el.textContent);
  if (!data.labels || !data.labels.length) return;

  const isUsd = data.currency === 'USD';

  // 円 → 「X.XX兆円」「X,XXX億円」／ ドル → 「$X.XXT」「$X,XXXB」表記
  const fmtYen = (v) => {
    if (isUsd) {
      if (v >= 1e12) return '$' + (v / 1e12).toFixed(2) + 'T';
      return '$' + Math.round(v / 1e9).toLocaleString() + 'B';
    }
    if (v >= 1e12) return (v / 1e12).toFixed(2) + '兆円';
    return Math.round(v / 1e8).toLocaleString() + '億円';
  };

  // 表の時価総額セルも整形
  document.querySelectorAll('.mc-table td[data-yen]').forEach((td) => {
    td.textContent = fmtYen(Number(td.dataset.yen));
  });

  // 1銘柄あたりの高さを確保（スマホでも読めるように）
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

  const chart = echarts.init(document.getElementById('mc-chart'), null, { renderer: 'canvas' });
  chart.setOption({
    backgroundColor: 'transparent',
    animationDuration: 600,
    grid: { left: 8, right: 24, top: 10, bottom: 30, containLabel: true },
    tooltip: {
      trigger: 'item',
      backgroundColor: 'rgba(11,18,32,0.95)',
      borderColor: '#3b82f6',
      textStyle: { color: '#e5e7eb', fontSize: 12 },
      formatter: (p) => {
        const i = p.dataIndex;
        return `${data.labels[i]}<br/>${fmtYen(p.value)}<br/>` +
          `<span style="color:#9ca3af">${data.markets[i]}・${data.sectors[i]}</span>`;
      },
    },
    xAxis: {
      type: 'value',
      axisLabel: { color: '#87cefa', formatter: (v) => fmtYen(v) },
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
      data: data.values,
      barMaxWidth: 18,
      itemStyle: {
        color: 'rgba(59,130,246,0.55)',
        borderColor: '#3b82f6',
        borderWidth: 1,
        borderRadius: [0, 3, 3, 0],
      },
      emphasis: {
        itemStyle: {
          color: 'rgba(59,130,246,0.85)',
          shadowBlur: 12,
          shadowColor: 'rgba(59,130,246,0.6)',
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
