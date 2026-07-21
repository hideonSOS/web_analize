// カルテ一覧: 銘柄を検索して選ぶとカルテを作成（＝ウォッチリストへの自動登録）
(() => {
  const input = document.getElementById('kt-stock-search');
  const list = document.getElementById('kt-stock-list');
  const codeInput = document.getElementById('kt-stock-code');
  const form = document.getElementById('kt-create-form');
  if (!input) return;

  let stocks = [];
  let loaded = false;
  function load() {
    if (loaded) return;
    loaded = true;
    fetch(window.KARTE_STOCK_OPTIONS_URL)
      .then((r) => r.json())
      .then((d) => {
        stocks = d.stocks;
        if (document.activeElement === input) showMatches(input.value);
      })
      .catch(() => { loaded = false; });
  }

  function showMatches(query) {
    const q = query.trim().toLowerCase();
    let matches;
    if (q) {
      // ティッカー完全一致 > 前方一致 > 名前部分一致 の順に並べる
      const scored = [];
      for (const s of stocks) {
        const t = s.ticker.toLowerCase();
        let rank;
        if (t === q) rank = 0;
        else if (t.startsWith(q)) rank = 1;
        else if (s.name.toLowerCase().includes(q)) rank = 2;
        else continue;
        scored.push([rank, s]);
      }
      scored.sort((a, b) => a[0] - b[0]);
      matches = scored.slice(0, 15).map((x) => x[1]);
    } else {
      matches = stocks.slice(0, 15);
    }
    list.innerHTML = '';
    matches.forEach((s) => {
      const item = document.createElement('div');
      item.className = 'kt-stock-item';
      item.textContent = `${s.name}（${s.ticker}）${s.country === 'US' ? ' · US' : ''}`;
      item.addEventListener('mousedown', (e) => {
        e.preventDefault();
        codeInput.value = s.code;
        form.submit();
      });
      list.appendChild(item);
    });
    list.hidden = matches.length === 0;
  }

  input.addEventListener('focus', () => { load(); showMatches(input.value); });
  input.addEventListener('input', () => showMatches(input.value));
  input.addEventListener('blur', () => { setTimeout(() => { list.hidden = true; }, 150); });
})();
