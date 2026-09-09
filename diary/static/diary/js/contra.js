/* 逆張りトレード: 銘柄検索（売買日記と同じ JSON）・許容株数の逆算・累積損益チャート */
(() => {
  // ---- 銘柄検索 -----------------------------------------------------------
  const search = document.getElementById('ct-search');
  const list = document.getElementById('ct-list');
  const codeInput = document.getElementById('ct-code');
  const price = document.getElementById('ct-price');
  const stop = document.getElementById('ct-stop');
  const target = document.getElementById('ct-target');
  const shares = document.getElementById('ct-shares');
  const calc = document.getElementById('ct-calc');
  const unitEls = document.querySelectorAll('.ct-unit');
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
    if (!q || !list) { if (list) list.hidden = true; return; }
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
  if (search) {
    search.addEventListener('focus', load);
    search.addEventListener('input', () => show(search.value));
    list.addEventListener('click', (e) => {
      const item = e.target.closest('.dy-stock-item');
      if (!item) return;
      codeInput.value = item.dataset.code;
      search.value = `${item.dataset.ticker}`;
      currency = item.dataset.country === 'US' ? 'USD' : 'JPY';
      unitEls.forEach((el) => { el.textContent = currency === 'USD' ? '$' : '円'; });
      if (item.dataset.close && !price.value) price.value = item.dataset.close;
      list.hidden = true;
      update();
    });
    document.addEventListener('click', (e) => { if (!list.contains(e.target) && e.target !== search) list.hidden = true; });
  }

  // ---- 許容株数（1〜2%ルール）と損切り線・利確線 ---------------------------
  function update() {
    if (!calc) return;
    const p = parseFloat(price.value), sp = parseFloat(stop.value), tp = parseFloat(target.value);
    const n = parseInt(shares.value, 10);
    if (!(p > 0 && sp > 0 && tp > 0)) { calc.textContent = '銘柄と価格を入れると、損切り線・利確線と許容株数を出します。'; return; }
    const unit = currency === 'USD' ? '$' : '¥';
    const rate = currency === 'USD' ? window.CT.fx : 1;
    const stopPrice = p * (1 - sp / 100), targetPrice = p * (1 + tp / 100);
    const budget = window.CT.capital * window.CT.riskPct / 100;
    const perShare = p * sp / 100 * rate;
    const maxN = Math.floor(budget / perShare);
    const c = window.CT.cost;
    const be = (sp + 2 * c) / ((sp + 2 * c) + (tp - 2 * c)) * 100;
    let s = `損切り線 ${unit}${stopPrice.toFixed(2)}（−${sp}%）／ 利確線 ${unit}${targetPrice.toFixed(2)}（+${tp}%）／ ` +
            `分岐勝率 ${be.toFixed(0)}%（コスト込み）。許容株数 <b>${maxN}株</b>（1株あたりの最大損失 ¥${Math.round(perShare).toLocaleString()}・上限 ¥${Math.round(budget).toLocaleString()}）`;
    if (n > 0) {
      const risk = n * perShare;
      s += `<br>この株数の最大損失 <b>¥${Math.round(risk).toLocaleString()}</b>（資金の ${(risk / window.CT.capital * 100).toFixed(2)}%）` +
           (n > maxN ? ' <span class="ct-stale">⚠ 上限超え。入れるなら裁量として記録されます</span>' : ' ✔ ルール内');
    } else {
      s += `<br><a href="#" id="ct-fill">${maxN}株を入れる</a>`;
    }
    calc.innerHTML = s;
    const fill = document.getElementById('ct-fill');
    if (fill) fill.addEventListener('click', (e) => { e.preventDefault(); shares.value = maxN; update(); });
  }
  [price, stop, target, shares].forEach((el) => el && el.addEventListener('input', update));

  // ---- 累積損益（コスト込み%） ---------------------------------------------
  const dataEl = document.getElementById('ct-curve-data');
  const dom = document.getElementById('ct-curve');
  if (dataEl && dom && typeof echarts !== 'undefined') {
    const curve = JSON.parse(dataEl.textContent);
    if (curve.length) {
      const c = echarts.init(dom);
      c.setOption({
        backgroundColor: 'transparent',
        grid: { left: 48, right: 16, top: 24, bottom: 28 },
        tooltip: { trigger: 'axis', backgroundColor: '#111827', borderColor: '#374151', textStyle: { color: '#e5e7eb', fontSize: 12 },
                   valueFormatter: (v) => (v == null ? '-' : v.toFixed(2) + '%') },
        xAxis: { type: 'category', data: curve.map((r) => r[0]), axisLabel: { color: '#9ca3af', fontSize: 10 } },
        yAxis: { type: 'value', axisLabel: { color: '#9ca3af', formatter: '{value}%' }, splitLine: { lineStyle: { color: '#1f2937' } } },
        series: [{ name: '累積損益（コスト込み）', type: 'line', step: 'end', showSymbol: true, symbolSize: 6,
                   data: curve.map((r) => r[1]), lineStyle: { color: '#34d399', width: 2 },
                   areaStyle: { color: 'rgba(52,211,153,.08)' },
                   markLine: { silent: true, symbol: 'none', lineStyle: { color: '#6b7280', type: 'dashed' }, data: [{ yAxis: 0 }] } }],
      });
      window.addEventListener('resize', () => c.resize());
    }
  }
})();
