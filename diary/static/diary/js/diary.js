// 売買日記: モーダル開閉と銘柄検索（選択で株価を自動入力）
(() => {
  const overlay = document.getElementById('dy-modal');
  const openBtn = document.getElementById('dy-open-modal');
  const closeBtn = document.getElementById('dy-close-modal');

  const searchInput = document.getElementById('dy-stock-search');
  const list = document.getElementById('dy-stock-list');
  const codeInput = document.getElementById('dy-stock-code');
  const priceInput = document.getElementById('dy-price');
  const recordedAt = document.getElementById('dy-recorded-at');

  // 銘柄リスト(JP+US 約2MB)は初回モーダル表示時に一度だけ取得する。
  // ブラウザに1時間キャッシュされるので以降の遷移では再取得しない。
  let stocks = [];
  let stocksLoaded = false;
  function loadStocks() {
    if (stocksLoaded) return;
    stocksLoaded = true;
    fetch(window.DIARY_STOCK_OPTIONS_URL)
      .then((r) => r.json())
      .then((data) => {
        stocks = data.stocks;
        // 読み込み前に検索窓を触っていた場合は候補を出し直す
        if (document.activeElement === searchInput) showMatches(searchInput.value);
      })
      .catch(() => { stocksLoaded = false; });
  }

  function nowLocalValue() {
    const d = new Date();
    d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
    return d.toISOString().slice(0, 16);
  }

  function openModal() {
    loadStocks();
    overlay.hidden = false;
    if (!recordedAt.value) recordedAt.value = nowLocalValue();
    // 非表示のままでは高さを測れないため、表示後に自動リサイズを効かせ直す
    if (window.autoGrowTextareas) window.autoGrowTextareas(overlay);
    searchInput.focus();
  }

  // 日時フィールドはタップ／クリックでカレンダーピッカーを開く
  recordedAt.addEventListener('click', () => {
    if (typeof recordedAt.showPicker === 'function') {
      try { recordedAt.showPicker(); } catch (e) { /* フォーカス外などで失敗しても入力は可能 */ }
    }
  });

  // 概算金額 = 株価 × 株数 を自動表示
  const sharesInput = document.getElementById('dy-shares');
  const amountInput = document.getElementById('dy-amount');
  function updateAmount() {
    const p = parseFloat(priceInput.value);
    const n = parseInt(sharesInput.value, 10);
    amountInput.value = (p > 0 && n > 0) ? Math.round(p * n).toLocaleString() + ' 円' : '';
  }
  priceInput.addEventListener('input', updateAmount);
  sharesInput.addEventListener('input', updateAmount);

  // リスクリワード比 = (目標 − 株価) ÷ (株価 − 損切り) をリアルタイム表示
  const targetInput = document.getElementById('dy-target');
  const stopInput = document.getElementById('dy-stop');
  const rrHint = document.getElementById('dy-rr-hint');
  function updateRR() {
    const p = parseFloat(priceInput.value);
    const t = parseFloat(targetInput.value);
    const s = parseFloat(stopInput.value);
    if (stopPct.value === 'none') {
      rrHint.textContent = '長期保有: 損切りなし（リスクリワード比は算出しません）';
      rrHint.classList.remove('warn');
    } else if (p > 0 && t > 0 && s > 0 && p > s) {
      const rr = (t - p) / (p - s);
      rrHint.textContent = `リスクリワード比: ${rr.toFixed(1)}` + (rr < 1 ? '（1未満: 損大利小の計画です）' : '');
      rrHint.classList.toggle('warn', rr < 1);
    } else {
      rrHint.textContent = '';
      rrHint.classList.remove('warn');
    }
  }
  [priceInput, targetInput, stopInput].forEach((el) => el.addEventListener('input', updateRR));

  // %選択で目標株価・損切りラインを株価から自動計算
  const targetPct = document.getElementById('dy-target-pct');
  const stopPct = document.getElementById('dy-stop-pct');
  function applyStopMode() {
    // 「長期（損切りなし）」選択時は損切り欄を無効化・クリアする
    const longTerm = stopPct.value === 'none';
    stopInput.disabled = longTerm;
    if (longTerm) stopInput.value = '';
    stopInput.placeholder = longTerm ? '長期保有のため設定なし' : '自動計算 / 手入力';
  }
  function applyPcts() {
    const p = parseFloat(priceInput.value);
    applyStopMode();
    if (!(p > 0)) return;
    if (targetPct.value) targetInput.value = Math.round(p * (1 + targetPct.value / 100));
    // 数値の%のみ自動計算（'none'=長期は計算しない）
    if (stopPct.value && stopPct.value !== 'none') stopInput.value = Math.round(p * (1 - stopPct.value / 100));
    updateRR();
  }
  targetPct.addEventListener('change', applyPcts);
  stopPct.addEventListener('change', applyPcts);
  priceInput.addEventListener('input', applyPcts);  // 株価変更時も%選択中なら追従
  // 手入力したら%選択を解除（手入力を上書きしないため）
  targetInput.addEventListener('input', () => { targetPct.value = ''; });
  stopInput.addEventListener('input', () => { stopPct.value = ''; });

  // 出口計画（目標・損切り）は「買い」を選んだ時だけ表示
  const exitSection = document.getElementById('dy-exit');
  const actionRadios = [...document.querySelectorAll('#dy-form input[name="action"]')];
  function toggleExit() {
    const checked = actionRadios.find((r) => r.checked);
    const isBuy = checked && checked.value === 'buy';
    exitSection.hidden = !isBuy;
    if (!isBuy) {
      targetInput.value = '';
      stopInput.value = '';
      targetPct.value = '';
      stopPct.value = '';
    }
    applyStopMode();  // 損切り欄の有効/無効を選択状態に合わせて復帰
    updateRR();
  }
  actionRadios.forEach((r) => r.addEventListener('change', toggleExit));
  toggleExit();

  function closeModal() { overlay.hidden = true; }

  openBtn.addEventListener('click', openModal);
  closeBtn.addEventListener('click', closeModal);
  overlay.addEventListener('click', (e) => { if (e.target === overlay) closeModal(); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !overlay.hidden) closeModal(); });

  // 銘柄検索ドロップダウン
  function showMatches(query) {
    const q = query.trim().toLowerCase();
    let matches;
    if (q) {
      // ランク付け: ティッカー完全一致 > ティッカー前方一致 > 名前部分一致
      // （例:「AAPL」でApple本体がレバレッジETFより先に出るように）
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
    const label = (s) => `${s.name}（${s.ticker}）${s.country === 'US' ? ' · US' : ''}`;
    list.innerHTML = '';
    matches.forEach((s) => {
      const item = document.createElement('div');
      item.className = 'dy-stock-item';
      item.textContent = label(s);
      item.addEventListener('mousedown', (e) => {
        e.preventDefault();
        searchInput.value = label(s);
        codeInput.value = s.code;  // マスタのPK（JP:数字 / US:"US-<ticker>"）
        if (s.close !== null) priceInput.value = s.close;
        updateAmount();
        list.hidden = true;
      });
      list.appendChild(item);
    });
    list.hidden = matches.length === 0;
  }

  searchInput.addEventListener('input', () => {
    codeInput.value = '';
    showMatches(searchInput.value);
  });
  searchInput.addEventListener('focus', () => { searchInput.select(); showMatches(''); });
  searchInput.addEventListener('blur', () => { setTimeout(() => { list.hidden = true; }, 150); });
})();
