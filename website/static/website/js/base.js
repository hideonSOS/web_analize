const btn = document.getElementById('js-btn');
const nav = document.getElementById('js-nav');

btn.addEventListener('click', function(){
	btn.classList.toggle('active');
	nav.classList.toggle('active');
});   // ← 直後のIIFEと連結して呼び出し扱いされるのを防ぐため、セミコロンは必須

// テキストエリアを入力量に応じて自動で伸ばす（全ページ共通）
// スクロールバーで隠れる領域を作らないための処理。スマホで読みやすくするのが目的。
(() => {
  function grow(ta) {
    ta.style.height = 'auto';
    ta.style.height = ta.scrollHeight + 'px';
  }

  function init(ta) {
    if (ta.dataset.autogrow) return;   // 二重登録を防ぐ
    ta.dataset.autogrow = '1';
    ta.style.overflowY = 'hidden';     // スクロールバーを出さない
    ta.addEventListener('input', () => grow(ta));
    // 表示された瞬間に高さが確定するよう、フォーカス時にも計算し直す
    ta.addEventListener('focus', () => grow(ta));
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
