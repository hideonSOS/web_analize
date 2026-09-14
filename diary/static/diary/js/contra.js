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

/* 画像の貼り付け（Ctrl+V）: .ct-paste にフォーカスして貼ると、同じフォーム内の .ct-paste-data に
   data URL（1200px 幅・JPEG 0.85 に縮小）を入れ、.ct-paste-preview に表示する。
   フォーム内のテキスト欄に貼っても拾う。サーバーは data URL をそのまま DB に持つ */
(() => {
  const MAX_W = 1200;
  function handle(file, form) {
    const data = form.querySelector('.ct-paste-data');
    const prev = form.querySelector('.ct-paste-preview');
    const zone = form.querySelector('.ct-paste');
    if (!data) return;
    const img = new Image();
    img.onload = () => {
      const scale = Math.min(1, MAX_W / img.width);
      const cv = document.createElement('canvas');
      cv.width = Math.round(img.width * scale); cv.height = Math.round(img.height * scale);
      cv.getContext('2d').drawImage(img, 0, 0, cv.width, cv.height);
      const url = cv.toDataURL('image/jpeg', 0.85);
      data.value = url;
      if (prev) { prev.src = url; prev.hidden = false; }
      if (zone) zone.textContent = '✔ 画像を貼り付けました（送信で保存）。貼り直すにはもう一度 Ctrl+V';
      URL.revokeObjectURL(img.src);
    };
    img.src = URL.createObjectURL(file);
  }
  document.addEventListener('paste', (e) => {
    const form = (e.target && e.target.closest) ? e.target.closest('form') : null;
    if (!form || !form.querySelector('.ct-paste-data')) return;
    const items = (e.clipboardData && e.clipboardData.items) || [];
    for (const it of items) {
      if (it.type && it.type.startsWith('image/')) { e.preventDefault(); handle(it.getAsFile(), form); return; }
    }
  });
  // 貼り付け欄はクリックでフォーカス（tabindex 付き）。Enter/Space でも何もしない
  document.querySelectorAll('.ct-paste').forEach((z) => z.addEventListener('click', () => z.focus()));
})();

/* 損切り・利確シミュレーター（2026-09-14）: 金額を入れると、ページのルール（data 属性）で
   損切り／利確したときの金額を出す。コストは片道 c% を往復（2c）で引く（contra.breakeven と同じ定義）。
   保存はしない（最後の入力だけ localStorage に覚える。短期と練習で別キー） */
(() => {
  const root = document.getElementById('ct-sim');
  const input = document.getElementById('ct-sim-amount');
  if (!root || !input) return;
  const stop = parseFloat(root.dataset.stop) / 100, target = parseFloat(root.dataset.target) / 100;
  const cost = parseFloat(root.dataset.cost) / 100, capital = parseFloat(root.dataset.capital) || 0;
  const riskPct = parseFloat(root.dataset.risk) || 0;
  const key = 'ct-sim-amount:' + (root.dataset.key || 'contra');
  const $ = (id) => document.getElementById(id);
  const money = (v, sign) => (sign ? (v < 0 ? '−' : '+') : '') + '$' + Math.abs(v).toLocaleString('en-US', { maximumFractionDigits: 0 });

  function render() {
    const a = parseFloat(input.value);
    const vis = $('ct-sim-vis');
    if (!(a > 0)) { vis.hidden = true; return; }
    vis.hidden = false;
    const loss = a * (stop + 2 * cost);         // 損切り: 値幅＋往復コスト
    const gain = a * (target - 2 * cost);       // 利確: 値幅−往復コスト
    $('ct-sim-loss').textContent = money(-loss, true);
    $('ct-sim-loss-after').textContent = '残り ' + money(a - loss);
    $('ct-sim-entry').textContent = money(a);
    $('ct-sim-gain').textContent = money(gain, true);
    $('ct-sim-gain-after').textContent = '合計 ' + money(a + gain);
    // 棒の幅は金額に比例（左=失う額、右=得る額）
    const total = loss + gain;
    $('ct-sim-seg-stop').style.width = (loss / total * 100).toFixed(1) + '%';
    $('ct-sim-seg-target').style.width = (gain / total * 100).toFixed(1) + '%';
    $('ct-sim-seg-stop-l').textContent = money(-loss, true);
    $('ct-sim-seg-target-l').textContent = money(gain, true);
    $('ct-sim-ratio').textContent = (gain / loss).toFixed(1);
    // 軍資金のリスク%との突き合わせ（1〜2%ルール）
    const budget = capital * riskPct / 100;
    $('ct-sim-cap').textContent = capital.toLocaleString('en-US');
    $('ct-sim-risk-pct').textContent = riskPct;
    $('ct-sim-budget').textContent = money(budget);
    const ratio = budget > 0 ? loss / budget : 0;
    const v = $('ct-sim-verdict');
    if (budget <= 0) { v.textContent = '軍資金が未設定'; v.className = ''; }
    else if (ratio <= 1) { v.textContent = '上限内 ✔'; v.className = 'up'; }
    else { v.textContent = '上限を超える ✖（' + money(Math.floor(budget / (stop + 2 * cost))) + ' まで）'; v.className = 'down'; }
    const g = $('ct-sim-gauge');
    g.style.width = Math.min(100, ratio * 100).toFixed(1) + '%';
    g.className = 'ct-sim-gauge-fill' + (ratio > 1 ? ' over' : (ratio > 0.8 ? ' warn' : ''));
    $('ct-sim-gauge-l').textContent = budget > 0 ? '損失上限の ' + Math.round(ratio * 100) + '%' : '';
    try { localStorage.setItem(key, String(a)); } catch (_) { /* 保存できなくても動く */ }
  }
  input.addEventListener('input', render);
  root.querySelectorAll('.ct-sim-q').forEach((b) => b.addEventListener('click', () => { input.value = b.dataset.v; render(); }));
  try { const saved = localStorage.getItem(key); if (saved) input.value = saved; } catch (_) { /* noop */ }
  render();
})();
