/* 短期トレード: 成績の円グラフ（勝率 vs 最低勝率）。入力は売買日記側なのでここには無い */
(() => {
  const dataEl = document.getElementById('ct-donut-data');
  const dom = document.getElementById('ct-donut');
  if (!dataEl || !dom || typeof echarts === 'undefined') return;
  const d = JSON.parse(dataEl.textContent);
  const n = (d.wins || 0) + (d.losses || 0);
  const rate = d.win_rate == null ? null : Math.round(d.win_rate);
  const ok = rate != null && rate >= d.min_rate;
  const c = echarts.init(dom);
  c.setOption({
    backgroundColor: 'transparent',
    tooltip: { trigger: 'item', backgroundColor: '#111827', borderColor: '#374151',
               textStyle: { color: '#e5e7eb', fontSize: 12 }, formatter: (p) => `${p.name} ${p.value}件` },
    series: [
      {
        // 勝ち/負けのドーナツ。中央に勝率
        type: 'pie', radius: ['62%', '88%'], center: ['50%', '50%'],
        avoidLabelOverlap: false, label: { show: false }, labelLine: { show: false },
        itemStyle: { borderColor: '#0b1220', borderWidth: 3 },
        data: n ? [
          { name: '勝ち', value: d.wins, itemStyle: { color: '#4ade80' } },
          { name: '負け', value: d.losses, itemStyle: { color: '#f87171' } },
        ] : [{ name: '未決済', value: 1, itemStyle: { color: '#1f2937' } }],
      },
      {
        // 最低勝率（分岐勝率）の目印: 外側の細いリングを 0〜min_rate% だけ塗る
        type: 'pie', radius: ['92%', '96%'], center: ['50%', '50%'], silent: true,
        label: { show: false }, labelLine: { show: false }, startAngle: 90,
        data: [
          { value: d.min_rate, itemStyle: { color: '#facc15' } },
          { value: 100 - d.min_rate, itemStyle: { color: 'rgba(255,255,255,.06)' } },
        ],
      },
    ],
    graphic: [
      { type: 'text', left: 'center', top: '38%',
        style: { text: rate == null ? '—' : `${rate}%`, fill: rate == null ? '#6b7280' : (ok ? '#4ade80' : '#f87171'),
                 fontSize: 30, fontWeight: 700, textAlign: 'center' } },
      { type: 'text', left: 'center', top: '60%',
        // 勝率はルール決済（利確・損切り）だけ。早期利確しか無いときはその旨を出す
        style: { text: n ? `${d.wins}勝 ${d.losses}敗` : (d.early ? `早期利確 ${d.early}件のみ` : 'まだ決済なし'), fill: '#9ca3af', fontSize: 12, textAlign: 'center' } },
    ],
  });
  window.addEventListener('resize', () => c.resize());
})();

/* 振り返り一覧の切り替え（損切り／利確／裁量・期限）。1つだけ表示 */
(() => {
  const tabs = document.getElementById('ct-reflect-tabs');
  if (!tabs) return;
  tabs.addEventListener('click', (e) => {
    const btn = e.target.closest('.ct-tab');
    if (!btn) return;
    tabs.querySelectorAll('.ct-tab').forEach((b) => b.classList.toggle('active', b === btn));
    document.querySelectorAll('.ct-reflect-col[data-panel]').forEach((p) => { p.hidden = p.dataset.panel !== btn.dataset.panel; });
  });
})();
