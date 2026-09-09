/* 練習ページ: 銘柄検索（売買日記と同じ JSON）と、ルール固定の損切り線・利確線・許容株数の表示。
   円グラフと振り返りタブは contra.js が描く（同じ要素IDを使う） */
(() => {
  const search = document.getElementById('ct-search');
  const list = document.getElementById('ct-list');
  const codeInput = document.getElementById('ct-code');
  const price = document.getElementById('ct-price');
  const shares = document.getElementById('ct-shares');
  const calc = document.getElementById('ct-calc');
  const unitEls = document.querySelectorAll('.ct-unit');
  if (!search || !window.CT) return;
  let stocks = [], loaded = false, currency = 'USD';

  function load() {
    if (loaded || !window.DIARY_STOCK_OPTIONS_URL) return;
    loaded = true;
    fetch(window.DIARY_STOCK_OPTIONS_URL).then((r) => r.json()).then((d) => {
      stocks = d.stocks || [];
      if (document.activeElement === search) show(search.value);
    }).catch(() => { loaded = false; });
  }
  function show(q) {
    q = (q || '').trim().toLowerCase();
    if (!q) { list.hidden = true; return; }
    const hits = [];
    for (const s of stocks) {
      const t = (s.ticker || '').toLowerCase(), n = (s.name || '').toLowerCase();
      if (t.startsWith(q) || n.includes(q)) { hits.push(s); if (hits.length >= 12) break; }
    }
    list.innerHTML = hits.map((s) =>
      `<div class="dy-stock-item" data-code="${s.code}" data-ticker="${s.ticker}" data-close="${s.close ?? ''}" data-country="${s.country}">` +
      `<b>${s.ticker}</b> ${s.name}${s.close ? ` <span>${s.country === 'US' ? '$' : '¥'}${s.close}</span>` : ''}</div>`).join('');
    list.hidden = hits.length === 0;
  }
  search.addEventListener('focus', load);
  search.addEventListener('input', () => show(search.value));
  list.addEventListener('click', (e) => {
    const item = e.target.closest('.dy-stock-item');
    if (!item) return;
    codeInput.value = item.dataset.code;
    search.value = item.dataset.ticker;
    currency = item.dataset.country === 'US' ? 'USD' : 'JPY';
    unitEls.forEach((el) => { el.textContent = currency === 'USD' ? '$' : '円'; });
    if (item.dataset.close && !price.value) price.value = item.dataset.close;
    list.hidden = true;
    update();
  });
  document.addEventListener('click', (e) => { if (!list.contains(e.target) && e.target !== search) list.hidden = true; });

  // ルール固定: 損切り線・利確線は設定の%で決まる。許容株数 = 軍資金×リスク% ÷ 1株あたりの損失
  function update() {
    const p = parseFloat(price.value), n = parseInt(shares.value, 10);
    const C = window.CT;
    if (!(p > 0)) { calc.textContent = '銘柄と価格を入れると、損切り線・利確線と許容株数を出します。'; return; }
    const unit = currency === 'USD' ? '$' : '¥';
    const stopPrice = p * (1 - C.stop / 100), targetPrice = p * (1 + C.target / 100);
    const perShare = p * C.stop / 100;
    const budget = C.capital * C.riskPct / 100;
    const maxN = Math.floor(budget / perShare);
    let s = `損切り線 ${unit}${stopPrice.toFixed(2)}（−${C.stop}%）／ 利確線 ${unit}${targetPrice.toFixed(2)}（+${C.target}%）。` +
            `許容株数 <b>${maxN}株</b>（1株の最大損失 ${unit}${perShare.toFixed(2)}・上限 ${unit}${Math.round(budget).toLocaleString()}）`;
    if (n > 0) {
      const risk = n * perShare;
      s += `<br>この株数の最大損失 <b>${unit}${Math.round(risk).toLocaleString()}</b>（軍資金の ${(risk / C.capital * 100).toFixed(2)}%）` +
           (n > maxN ? ' <span class="ct-stale">⚠ 上限超え（裁量として記録されます）</span>' : ' ✔ ルール内');
    } else {
      s += `<br><a href="#" id="ct-fill">${maxN}株を入れる</a>`;
    }
    calc.innerHTML = s;
    const fill = document.getElementById('ct-fill');
    if (fill) fill.addEventListener('click', (e) => { e.preventDefault(); shares.value = maxN; update(); });
  }
  [price, shares].forEach((el) => el.addEventListener('input', update));
})();

/* なぜ買ったか: 「＋ 理由を追加」で入力欄を増やす（1行1理由） */
(() => {
  const list = document.getElementById('ct-reasons');
  const btn = document.getElementById('ct-add-reason');
  if (!list || !btn) return;
  btn.addEventListener('click', () => {
    const inp = document.createElement('input');
    inp.type = 'text'; inp.name = 'reasons'; inp.placeholder = '例: サポートラインを見て判断';
    list.appendChild(inp);
    inp.focus();
  });
})();
