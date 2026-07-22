const btn = document.getElementById('js-btn');
const nav = document.getElementById('js-nav');

// 要素が無いページで例外を投げると、このファイル内の後続処理
// （テキストエリアの自動拡張）まで丸ごと止まるため、必ず存在確認する
if (btn && nav) {
	btn.addEventListener('click', function(){
		btn.classList.toggle('active');
		nav.classList.toggle('active');
	});
}   // ifブロックで閉じているため、直後のIIFEと連結して呼び出し扱いされる心配はない

// テキストエリアを入力量に応じて自動で伸ばす（全ページ共通）
// スクロールバーで隠れる領域を作らないための処理。スマホで読みやすくするのが目的。
(() => {
  function grow(ta) {
    // 手動でサイズを変えた欄は尊重して自動調整しない
    if (ta.dataset.manualResize === '1') return;
    const cs = getComputedStyle(ta);
    // ⚠️ scrollHeight は border を含まない。box-sizing:border-box では height に
    // border が含まれるため、scrollHeight をそのまま入れると border 分だけ足りず
    // 最終行がはみ出す。overflow-y:hidden なのでスクロールバーも出ず、
    // 「文字が黙って消える」状態になる。必ず border を足し戻すこと。
    const extra = cs.boxSizing === 'border-box'
      ? parseFloat(cs.borderTopWidth) + parseFloat(cs.borderBottomWidth)
      : -(parseFloat(cs.paddingTop) + parseFloat(cs.paddingBottom));
    ta.style.height = 'auto';
    const h = Math.ceil(ta.scrollHeight + extra);
    ta.style.height = h + 'px';
    ta.dataset.autoHeight = String(h);   // 手動リサイズ検出用に控えておく
  }

  function init(ta) {
    if (ta.dataset.autogrow) return;   // 二重登録を防ぐ
    ta.dataset.autogrow = '1';
    ta.style.overflowY = 'hidden';     // スクロールバーを出さない
    ta.addEventListener('input', () => grow(ta));
    // 表示された瞬間に高さが確定するよう、フォーカス時にも計算し直す
    ta.addEventListener('focus', () => grow(ta));
    // 右下のハンドルでドラッグされたら、以後その欄は自動調整をやめる
    // （やめないと次の入力で高さが戻り、手動調整が無駄になる）
    ta.addEventListener('mouseup', () => {
      const cur = Math.round(ta.getBoundingClientRect().height);
      const auto = Number(ta.dataset.autoHeight || 0);
      if (auto && Math.abs(cur - auto) > 2) ta.dataset.manualResize = '1';
    });
    grow(ta);
  }

  function initAll(root) {
    (root || document).querySelectorAll('textarea').forEach(init);
  }

  initAll();

  // 折りたたみ（<details>）やモーダルの中は、開くまで高さが測れない。
  // 開いた時点で測り直す。
  document.querySelectorAll('details').forEach((d) => {
    d.addEventListener('toggle', () => {
      if (d.open) d.querySelectorAll('textarea').forEach(grow);
    });
  });

  // 画面幅が変わると文字の折り返しが変わり行数が増減するため、測り直す
  // （スマホの回転・ウィンドウリサイズで高さが足りなくなるのを防ぐ）
  let resizeTimer;
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      document.querySelectorAll('textarea[data-autogrow]').forEach(grow);
    }, 120);
  });

  // 他スクリプト（モーダルを開く処理など）から呼べるようにしておく
  window.autoGrowTextareas = initAll;
})();
