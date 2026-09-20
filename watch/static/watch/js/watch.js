// 監視: 銘柄検索（カルテと同じ JSON・文字を入れてから候補）と並び替え
(() => {
  const input = document.getElementById('wt-stock-search');
  const list = document.getElementById('wt-stock-list');
  const codeInput = document.getElementById('wt-stock-code');
  const unit = document.getElementById('wt-unit');
  if (!input) return;
  let stocks = [], loaded = false;
  function load() {
    if (loaded) return;
    loaded = true;
    fetch(window.WATCH_STOCK_OPTIONS_URL).then((r) => r.json()).then((d) => {
      stocks = d.stocks;
      if (document.activeElement === input) showMatches(input.value);
    }).catch(() => { loaded = false; });
  }
  function showMatches(query) {
    const q = query.trim().toLowerCase();
    if (!q) { list.hidden = true; return; }
    const scored = [];
    for (const s of stocks) {
      const t = s.ticker.toLowerCase();
      let rank;
      if (t === q) rank = 0; else if (t.startsWith(q)) rank = 1; else if (s.name.toLowerCase().includes(q)) rank = 2; else continue;
      scored.push([rank, s]);
    }
    scored.sort((a, b) => a[0] - b[0]);
    const matches = scored.slice(0, 15).map((x) => x[1]);
    list.innerHTML = '';
    matches.forEach((s) => {
      const item = document.createElement('div');
      item.className = 'wt-stock-item';
      item.textContent = `${s.name}（${s.ticker}）${s.country === 'US' ? ' · US' : ''}`;
      item.addEventListener('mousedown', (e) => {
        e.preventDefault();
        codeInput.value = s.code;
        input.value = `${s.name}（${s.ticker}）`;
        if (unit) unit.textContent = s.country === 'US' ? '$' : '円';
        list.hidden = true;
        const t = document.getElementById('wt-target'); if (t) t.focus();
      });
      list.appendChild(item);
    });
    list.hidden = matches.length === 0;
  }
  input.addEventListener('focus', () => { load(); showMatches(input.value); });
  input.addEventListener('input', () => { codeInput.value = ''; showMatches(input.value); });
  input.addEventListener('blur', () => { setTimeout(() => { list.hidden = true; }, 150); });
})();

(() => {
  const box = document.getElementById('wt-list');
  if (!box || typeof Sortable === 'undefined') return;
  function csrfToken() {
    const m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    if (m) return decodeURIComponent(m[1]);
    const i = document.querySelector('[name=csrfmiddlewaretoken]'); return i ? i.value : '';
  }
  Sortable.create(box, {
    handle: '.wt-handle', animation: 150, ghostClass: 'wt-ghost',
    onEnd() {
      const order = [...box.querySelectorAll('.wt-row')].map((el) => parseInt(el.dataset.pk, 10));
      fetch(box.dataset.reorderUrl, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
        body: JSON.stringify({ order }) }).catch(() => {});
    },
  });
})();
