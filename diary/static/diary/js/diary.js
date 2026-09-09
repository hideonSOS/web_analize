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
    setCurrency('JP');   // 既定は円。米国株を選んだ時点で$へ切替
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

  // 通貨単位（銘柄の国で切替）。米国株は$・日本株は円で入力/保存する。
  // ※アプリは米国株を一貫してドルで扱う（Stock.close・指標・ランキングも$）。
  //   円換算して混ぜると損益に為替が混入するため、記録もドルのまま持つ。
  let selectedCurrency = 'JPY';
  function setCurrency(country) {
    selectedCurrency = country === 'US' ? 'USD' : 'JPY';
    const unit = selectedCurrency === 'USD' ? '$' : '円';
    document.querySelectorAll('#dy-form .dy-cur-unit').forEach((el) => { el.textContent = unit; });
    updateAmount();
  }

  // 概算金額 = 株価 × 株数 を自動表示（選択中の通貨に合わせる）
  const sharesInput = document.getElementById('dy-shares');
  const amountInput = document.getElementById('dy-amount');
  function updateAmount() {
    const p = parseFloat(priceInput.value);
    const n = parseInt(sharesInput.value, 10);
    if (!(p > 0 && n > 0)) { amountInput.value = ''; return; }
    const amt = Math.round(p * n).toLocaleString();
    amountInput.value = selectedCurrency === 'USD' ? '$' + amt : amt + ' 円';
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
    // 売りのときだけ「短期トレードの決済理由」を出す（追跡中の同じ銘柄があれば決済として記録される）
    const sellExtra = document.getElementById('dy-sell-extra');
    if (sellExtra) sellExtra.hidden = !(checked && checked.value === 'sell');
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

  // 短期で追跡（ルール固定）: チェックすると目標・損切りを既定%（利確+10/損切り-5）で
  // 埋めてロックする。手入力で崩せないようにするのが目的（サーバー側でも同じ値で上書きする）。
  // 許容株数（資金×リスク% ÷ 1株あたりの損失）も出す
  const trackBox = document.getElementById('dy-track');
  const trackHint = document.getElementById('dy-track-hint');
  function applyTrackLock() {
    if (!trackBox || !window.CONTRA) return;
    const on = trackBox.checked;
    [targetInput, stopInput].forEach((el) => { el.readOnly = on; el.classList.toggle('dy-locked', on); });
    [targetPct, stopPct].forEach((el) => { el.disabled = on; });
    if (on) {
      const p = parseFloat(priceInput.value);
      const C = window.CONTRA;
      if (p > 0) {
        targetInput.value = (p * (1 + C.target / 100)).toFixed(2);
        stopInput.value = (p * (1 - C.stop / 100)).toFixed(2);
        const perShare = p * C.stop / 100;
        const maxN = Math.floor(C.capital * C.riskPct / 100 / perShare);
        const n = parseInt(sharesInput.value, 10);
        trackHint.textContent = `ルール固定: 利確 ${targetInput.value}（+${C.target}%）／損切り ${stopInput.value}（−${C.stop}%）。` +
          `許容株数 ${maxN}株（資金 $${C.capital.toLocaleString()} × ${C.riskPct}%）` +
          (n > maxN ? ' ⚠ 上限超え（裁量として記録されます）' : n > 0 ? ' ✔ ルール内' : '');
        trackHint.classList.toggle('warn', n > maxN);
      } else {
        trackHint.textContent = '株価を入れるとルールの価格と許容株数を出します。';
      }
      updateRR();
    } else {
      trackHint.textContent = 'チェックすると目標・損切りはルールの価格に固定され、手入力できなくなります。許容株数も出します（結果は「短期トレードの結果追跡」ページ）';
      trackHint.classList.remove('warn');
    }
  }
  if (trackBox) {
    trackBox.addEventListener('change', applyTrackLock);
    priceInput.addEventListener('input', () => { if (trackBox.checked) applyTrackLock(); });
    sharesInput.addEventListener('input', () => { if (trackBox.checked) applyTrackLock(); });
    // 売りに切り替えたらチェックを外す（追跡は買いだけ）
    actionRadios.forEach((r) => r.addEventListener('change', () => {
      if (r.checked && r.value !== 'buy' && trackBox.checked) { trackBox.checked = false; applyTrackLock(); }
    }));
  }

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
        setCurrency(s.country);    // 米国株なら入力単位を$へ（保存はドルのまま）
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
