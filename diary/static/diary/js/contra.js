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

/* 損切り・利確シミュレーター（2026-09-14）: 買値を入れると、ページのルール（data 属性）で
   損切りライン・利確ラインの価格を一瞬で出す（本人の使い方: 逆張りで「ここまでは落ちない」ラインの
   近くで買うとき、上下のラインを見る）。株数があれば金額、サポート価格があれば損切り線との関係も出す。
   コストは片道 c% を往復（2c）で引く（contra.breakeven と同じ定義）。
   ⚠️ スクラッチの計算機なので入力は記憶しない（2026-09-29 ユーザー指示: 開くたびに空欄）。以前の localStorage は消す */
(() => {
  const root = document.getElementById('ct-sim');
  const price = document.getElementById('ct-sim-price');
  if (!root || !price) return;
  const shares = document.getElementById('ct-sim-shares'), support = document.getElementById('ct-sim-support');
  // 損切り率もボタンで切り替える（3・5・7・10%。2026-09-30）。既定はページの設定、無ければ 5%
  const stopBtns = [...document.querySelectorAll('#ct-sim-stops button')];
  let stopPct = parseFloat(root.dataset.stop);
  if (stopBtns.length && !stopBtns.some((b) => parseFloat(b.dataset.rate) === stopPct)) stopPct = 5;
  let stop = stopPct / 100;
  function markStop() { stopBtns.forEach((b) => b.classList.toggle('active', parseFloat(b.dataset.rate) === stopPct)); }
  stopBtns.forEach((b) => b.addEventListener('click', () => {
    stopPct = parseFloat(b.dataset.rate); stop = stopPct / 100; markStop(); render();
  }));
  markStop();
  // 利確率はボタンで切り替える（5・7・10・15%。2026-09-30）。既定はページの設定、無ければ 10%
  const rateBtns = [...document.querySelectorAll('#ct-sim-rates button')];
  let targetPct = parseFloat(root.dataset.target);
  if (!rateBtns.some((b) => parseFloat(b.dataset.rate) === targetPct)) targetPct = 10;
  let target = targetPct / 100;
  function markRate() { rateBtns.forEach((b) => b.classList.toggle('active', parseFloat(b.dataset.rate) === targetPct)); }
  rateBtns.forEach((b) => b.addEventListener('click', () => {
    targetPct = parseFloat(b.dataset.rate); target = targetPct / 100; markRate(); render();
  }));
  markRate();
  const cost = parseFloat(root.dataset.cost) / 100, capital = parseFloat(root.dataset.capital) || 0;
  const riskPct = parseFloat(root.dataset.risk) || 0;
  try { localStorage.removeItem('ct-sim:' + (root.dataset.key || 'contra')); } catch (_) { /* noop */ }
  const $ = (id) => document.getElementById(id);
  // 通貨記号は付けない（ドルでも円でも使う数値計算。2026-09-30 ユーザー指示）
  const px = (v) => v.toLocaleString('ja-JP', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const money = (v, sign) => (sign ? (v < 0 ? '−' : '+') : '') + Math.abs(v).toLocaleString('ja-JP', { maximumFractionDigits: 0 });

  // 目盛り: 損切り／−2.5／0／+2.5／+5／+7.5／利確（保有中のレンジバー contra._ticks と同じ並び）
  function ticks(p) {
    const lo = -stop, hi = target, span = hi - lo;
    const out = [];
    const push = (pct, kind) => out.push({ pct, kind, pos: (pct - lo) / span * 100, price: p * (1 + pct) });
    push(lo, 'stop');
    // 損切り率を変えても間の目盛り（−2.5 刻み）が出るよう、損切り線の上の最初の 2.5% 刻みから始める
    for (let x = Math.ceil((lo + 1e-9) / 0.025) * 0.025; x < hi - 1e-9; x += 0.025) {
      if (Math.abs(x - lo) < 1e-9) continue;
      push(x, Math.abs(x) < 1e-9 ? 'entry' : (Math.abs(x - target / 2) < 1e-9 ? 'half' : 'minor'));
    }
    push(hi, 'target');
    return out;
  }

  function render() {
    const p = parseFloat(price.value);
    const vis = $('ct-sim-vis');
    if (!(p > 0)) { vis.hidden = true; return; }
    vis.hidden = false;
    const sp = p * (1 - stop), tp = p * (1 + target);
    $('ct-sim-stop-pct').textContent = stopPct; $('ct-sim-target-pct').textContent = targetPct;
    $('ct-sim-stop-price').textContent = px(sp);
    $('ct-sim-entry-price').textContent = px(p);
    $('ct-sim-target-price').textContent = px(tp);
    // 建値ストップ（2026-09-24）: +7% に届いたら損切りを建値へ。利確 +10% のときだけ（2026-09-30・contra.BE_TARGET_PCT）
    const beEl = $('ct-sim-be'), beP = parseFloat(root.dataset.be);
    if (beEl) {
      if (targetPct === 10 && beP > 0 && beP < targetPct) {
        beEl.hidden = false;
        beEl.innerHTML = `🛡 <b>${px(p * (1 + beP / 100))}</b>（+${beP}%）に届いたら、損切りを <b>${px(sp)}</b> → 建値 <b>${px(p)}</b> に上げる`;
      } else { beEl.hidden = true; }
    }
    // 目盛りバー（価格）
    const tk = ticks(p);
    $('ct-sim-track').innerHTML = tk.map((k) => `<span class="ct-slit ${k.kind}" style="left:${k.pos.toFixed(1)}%"></span>`).join('');
    $('ct-sim-ticks').innerHTML = tk.map((k) => `<span class="ct-tick ${k.kind}" style="left:${k.pos.toFixed(1)}%">${px(k.price)}<i>${Math.abs(k.pct) < 1e-9 ? '0' : (k.pct > 0 ? '+' : '') + (k.pct * 100).toFixed(1).replace(/\.0$/, '')}%</i></span>`).join('');
    // サポート（ここまでは落ちない価格）との関係
    const sv = parseFloat(support.value), sbox = $('ct-sim-support-box'), smsg = $('ct-sim-support-msg');
    if (sv > 0) {
      sbox.hidden = false;
      const maxBuy = sv / (1 - stop);   // サポートを損切り線に合わせるときの買値の上限
      if (sv >= p) {
        smsg.innerHTML = `サポート ${px(sv)} は買値以上です。買値より下の価格を入れてください`; sbox.className = 'ct-sim-support warn';
      } else if (sv >= sp) {
        smsg.innerHTML = `✔ サポート <b>${px(sv)}</b> は損切りライン <b>${px(sp)}</b> より<b>上</b>。サポートで反発するなら損切りに掛からない（余裕 ${((sv / sp - 1) * 100).toFixed(1)}%）`;
        sbox.className = 'ct-sim-support ok';
      } else {
        smsg.innerHTML = `✖ サポート <b>${px(sv)}</b> は損切りライン <b>${px(sp)}</b> より<b>下</b>。サポートまで落ちると損切りに掛かる → サポートを損切り線に合わせるなら買値は <b>${px(maxBuy)}</b> 以下`;
        sbox.className = 'ct-sim-support ng';
      }
    } else { sbox.hidden = true; }
    // 金額（株数があるとき）
    const n = parseInt(shares.value, 10), mbox = $('ct-sim-money');
    if (n > 0) {
      mbox.hidden = false;
      const a = p * n, loss = a * (stop + 2 * cost), gain = a * (target - 2 * cost);
      $('ct-sim-amount').textContent = money(a);
      $('ct-sim-loss').textContent = money(-loss, true) + '（残り ' + money(a - loss) + '）';
      $('ct-sim-gain').textContent = money(gain, true) + '（合計 ' + money(a + gain) + '）';
      const total = loss + gain;
      $('ct-sim-seg-stop').style.width = (loss / total * 100).toFixed(1) + '%';
      $('ct-sim-seg-target').style.width = (gain / total * 100).toFixed(1) + '%';
      $('ct-sim-seg-stop-l').textContent = money(-loss, true);
      $('ct-sim-seg-target-l').textContent = money(gain, true);
      const budget = capital * riskPct / 100, ratio = budget > 0 ? loss / budget : 0;
      $('ct-sim-cap').textContent = capital.toLocaleString('ja-JP');
      $('ct-sim-risk-pct').textContent = riskPct;
      $('ct-sim-budget').textContent = money(budget);
      const v = $('ct-sim-verdict');
      if (budget <= 0) { v.textContent = '軍資金が未設定'; v.className = ''; }
      else if (ratio <= 1) { v.textContent = '上限内 ✔'; v.className = 'up'; }
      else { v.textContent = '上限を超える ✖（' + Math.floor(budget / (p * (stop + 2 * cost))) + '株まで）'; v.className = 'down'; }
      const g = $('ct-sim-gauge');
      g.style.width = Math.min(100, ratio * 100).toFixed(1) + '%';
      g.className = 'ct-sim-gauge-fill' + (ratio > 1 ? ' over' : (ratio > 0.8 ? ' warn' : ''));
      $('ct-sim-gauge-l').textContent = budget > 0 ? '損失上限の ' + Math.round(ratio * 100) + '%' : '';
    } else { mbox.hidden = true; }
  }
  [price, shares, support].forEach((el) => el.addEventListener('input', render));
  // 戻る・再読み込みでブラウザが入力を復元することがあるので、開いたときに必ず初期状態にする。
  // 株数だけは 1 を既定にする（2026-09-30 ユーザー指示）
  [price, support].forEach((el) => { el.value = ''; });
  shares.value = '1';
  render();
})();

/* チャートの心情・コメントの印（2026-09-29・案B）: 印をタップ（クリック）すると、その日の内容を吹き出しで出す。
   スマホはカーソルを乗せられない（title が出ない）ので、タップで読めるようにする。もう一度押すか外を押すと閉じる */
(() => {
  let pop = null, owner = null;
  function close() { if (pop) { pop.remove(); pop = null; owner = null; } }
  document.addEventListener('click', (e) => {
    const m = e.target.closest('.cv-mark');
    if (!m) { if (pop && !e.target.closest('.cv-pop')) close(); return; }
    e.preventDefault();
    if (owner === m) { close(); return; }
    close();
    pop = document.createElement('div');
    pop.className = 'cv-pop ' + [...m.classList].filter((c) => c !== 'cv-mark' && c !== 'edge').join(' ');
    const head = document.createElement('div');
    head.className = 'cv-pop-head';
    head.textContent = m.textContent + '  ' + (m.dataset.when || '');
    const body = document.createElement('div');
    body.textContent = m.dataset.text || '';
    pop.append(head, body);
    const box = m.offsetParent || m.parentElement;
    box.appendChild(pop);
    // 印の真下に出す。右端・左端ではみ出さないように寄せる
    const left = m.offsetLeft + m.offsetWidth / 2, w = Math.min(320, box.clientWidth - 8);
    pop.style.width = w + 'px';
    pop.style.left = Math.max(4, Math.min(left - w / 2, box.clientWidth - w - 4)) + 'px';
    pop.style.top = (m.offsetTop + m.offsetHeight + 6) + 'px';
    owner = m;
  });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') close(); });
})();

// 振り返りチャート（購入前の足つき・2026-09-30）: 開いたときは右端＝取得日〜売却後を見せる。左へスクロールで購入前
// 勝ち／負けのタブや折りたたみで隠れている間は幅が 0 で、読み込み時にスクロールしても効かない
// （2026-09-30 ユーザー指摘: 初期値は右に振った状態に）。見えるようになった（幅 0 → 正）時点で右端へ。
// ユーザーが左へ戻した後は、窓の幅が変わっても勝手に戻さない
document.querySelectorAll('.rv-scroll.has-pre').forEach((el) => {
  let shown = false;
  const toRight = () => {
    if (el.clientWidth > 0 && !shown) { el.scrollLeft = el.scrollWidth; shown = true; }
    else if (el.clientWidth === 0) shown = false;     // また隠れたら、次に見えたときに右端へ
  };
  toRight();
  if (window.ResizeObserver) new ResizeObserver(toRight).observe(el);
});

/* シミュレーターの2面タブと「到達価格から逆算」（2026-09-30 ユーザー指示）。
   「最高値 5000 に届くと仮定して、いくらで入れば +5% を取れるか」: 買値の上限 = 到達価格 ÷ (1 + 利確率)。
   現在価格からの距離ではなく、到達価格から値幅を逆算する。現在価格は任意（その買値まで あと何% かを添える）。
   入力・タブは記憶しない（開くたびに空欄・①のタブ） */
(() => {
  const root = document.getElementById('ct-sim');
  const tabs = document.getElementById('ct-sim-tabs');
  if (!root || !tabs) return;
  const panels = [...root.querySelectorAll('.ct-sim-panel')];
  tabs.addEventListener('click', (e) => {
    const b = e.target.closest('[data-sim-panel]');
    if (!b) return;
    tabs.querySelectorAll('.ct-tab').forEach((x) => x.classList.toggle('active', x === b));
    panels.forEach((p) => { p.hidden = p.dataset.simPanel !== b.dataset.simPanel; });
  });

  const reach = document.getElementById('ct-rev-reach'), cur = document.getElementById('ct-rev-cur');
  if (!reach) return;
  const $ = (id) => document.getElementById(id);
  const px = (v) => v.toLocaleString('ja-JP', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const pct = (v) => (v > 0 ? '+' : (v < 0 ? '−' : '')) + Math.abs(v).toFixed(1) + '%';
  const stopPct = parseFloat(root.dataset.stop) || 5, cost = (parseFloat(root.dataset.cost) || 0) / 100;
  const btns = [...document.querySelectorAll('#ct-rev-rates button')];
  let rate = parseFloat(root.dataset.target);
  if (!btns.some((b) => parseFloat(b.dataset.rate) === rate)) rate = 10;
  const mark = () => btns.forEach((b) => b.classList.toggle('active', parseFloat(b.dataset.rate) === rate));
  btns.forEach((b) => b.addEventListener('click', () => { rate = parseFloat(b.dataset.rate); mark(); render(); }));
  mark();

  function render() {
    const R = parseFloat(reach.value), C = parseFloat(cur.value);
    const vis = $('ct-rev-vis');
    if (!(R > 0)) { vis.hidden = true; return; }
    vis.hidden = false;
    const buyOf = (r) => R / (1 + r / 100);
    const buy = buyOf(rate);
    $('ct-rev-pct').textContent = rate;
    $('ct-rev-buy').textContent = px(buy);
    $('ct-rev-width').textContent = px(R - buy);
    $('ct-rev-stop-pct').textContent = stopPct;
    document.querySelectorAll('.ct-rev-stop-pct2').forEach((el) => { el.textContent = stopPct; });
    $('ct-rev-stop').textContent = px(buy * (1 - stopPct / 100));
    $('ct-rev-reach-v').textContent = px(R);
    // コスト（片道×2）込みで +r% を手元に残すなら、さらに少し下で入る
    $('ct-rev-cost').innerHTML = cost > 0
      ? `コスト往復 ${(cost * 100).toFixed(2).replace(/0+$/, '').replace(/\.$/, '')}%×2 も込みで +${rate}% を残すなら <b>${px(R / (1 + rate / 100 + 2 * cost))}</b> 以下`
      : '';
    const msg = $('ct-rev-cur-msg');
    if (C > 0) {
      msg.hidden = false;
      const need = (buy / C - 1) * 100;
      if (C <= buy) {
        msg.className = 'ct-sim-support ok';
        msg.innerHTML = `✔ 現在価格 <b>${px(C)}</b> は買値の上限 <b>${px(buy)}</b> 以下。到達価格まで <b>${pct((R / C - 1) * 100)}</b> の値幅`;
      } else {
        msg.className = 'ct-sim-support warn';
        msg.innerHTML = `現在価格 <b>${px(C)}</b> から <b>${pct(need)}</b> 下がって <b>${px(buy)}</b> 以下になるまで待つ（今入ると到達価格まで ${pct((R / C - 1) * 100)}）`;
      }
    } else { msg.hidden = true; }
    const table = document.querySelector('.ct-rev-table');
    table.classList.toggle('no-cur', !(C > 0));
    $('ct-rev-rows').innerHTML = btns.map((b) => {
      const r = parseFloat(b.dataset.rate), v = buyOf(r);
      const d = C > 0 ? (v / C - 1) * 100 : null;
      const curCell = d === null ? '' : (C <= v ? '<td class="ct-rev-cur-col ok">買える ✔</td>' : `<td class="ct-rev-cur-col wait">${pct(d)}</td>`);
      return `<tr class="${r === rate ? 'active' : ''}"><td>+${r}%</td><td>${px(v)}</td><td>${px(R - v)}</td><td class="stop">${px(v * (1 - stopPct / 100))}</td>${curCell || '<td class="ct-rev-cur-col"></td>'}</tr>`;
    }).join('');
  }
  [reach, cur].forEach((el) => el.addEventListener('input', render));
  reach.value = ''; cur.value = '';
  render();
})();
