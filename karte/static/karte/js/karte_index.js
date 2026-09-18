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
    if (!q) { list.hidden = true; return; }   // 空では候補を出さない（フォーカスだけで一覧が被るのを防ぐ 2026-09-19）
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

  // ランキング等から「カルテが無い銘柄」を開いたとき（?q=コード）: 検索窓に入れて候補を出す。
  // 自動では作らない（登録画面を経由する、がユーザー方針 2026-09-16）
  const pre = (input.dataset.prefill || '').trim();
  if (pre) { input.value = pre; input.focus(); load(); }
})();


// カードの並び替え（推しを前に）。ハンドル(⠿)を掴んでドラッグ → 離した時点で保存
(() => {
  const grid = document.getElementById('kt-cards');
  if (!grid || typeof Sortable === 'undefined') return;
  function csrfToken() {
    const m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    if (m) return decodeURIComponent(m[1]);
    const input = document.querySelector('[name=csrfmiddlewaretoken]');
    return input ? input.value : '';
  }
  Sortable.create(grid, {
    handle: '.kt-card-handle', animation: 150, ghostClass: 'kt-sortable-ghost',
    onEnd() {
      const order = [...grid.querySelectorAll('.kt-card-wrap')].map((el) => el.dataset.code);
      fetch(grid.dataset.reorderUrl, {
        method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
        body: JSON.stringify({ order }),
      }).catch(() => {});
    },
  });
})();
