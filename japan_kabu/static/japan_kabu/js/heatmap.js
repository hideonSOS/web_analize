/* セクターヒートマップ（Finviz風ツリーマップ）
 *
 * 外部ライブラリ依存なしの自前実装。
 * - 面積 = 時価総額、色 = 前日比（±3%でクリップして赤⇔グレー⇔緑を補間）
 * - 配置は squarified treemap アルゴリズム（タイルをなるべく正方形に近づける）
 * - 2段構成: まずセクター枠を敷き、各枠の中（ヘッダー18pxを除く）に銘柄タイルを敷く
 * - リサイズ時は再レイアウト（debounce 200ms）
 */
(function () {
  const dataEl = document.getElementById('heatmap-data');
  const root = document.getElementById('hm-treemap');
  if (!dataEl || !root) return;

  const data = JSON.parse(dataEl.textContent);
  const isUSD = data.currency === 'USD';
  const tooltip = document.getElementById('hm-tooltip');

  // ---- 色スケール（Finviz準拠の3色） ----------------------------------
  const NEG = [246, 53, 56];    // -3%: 赤
  const MID = [65, 69, 84];     //  0%: グレー
  const POS = [48, 204, 90];    // +3%: 緑
  const CLIP = 3;               // ±3%でクリップ

  function color(chg) {
    const t = Math.max(-CLIP, Math.min(CLIP, chg)) / CLIP;  // -1..1
    const from = t < 0 ? NEG : POS;
    const k = Math.abs(t);
    const rgb = MID.map((m, i) => Math.round(m + (from[i] - m) * k));
    return 'rgb(' + rgb.join(',') + ')';
  }

  // ---- 表示フォーマット ------------------------------------------------
  function fmtCap(v) {
    if (isUSD) {
      if (v >= 1e12) return '$' + (v / 1e12).toFixed(2) + 'T';
      if (v >= 1e9) return '$' + (v / 1e9).toFixed(1) + 'B';
      return '$' + (v / 1e6).toFixed(0) + 'M';
    }
    if (v >= 1e12) return (v / 1e12).toFixed(2) + '兆円';
    return (v / 1e8).toFixed(0) + '億円';
  }
  function fmtChg(chg) {
    return (chg > 0 ? '+' : '') + chg.toFixed(2) + '%';
  }

  // ---- squarified treemap ----------------------------------------------
  // items: [{value, ...}]（value降順ソート済み）を rect{x,y,w,h} に敷き詰め、
  // 各itemに {x,y,w,h} を書き込む
  function squarify(items, rect) {
    const total = items.reduce((a, b) => a + b.value, 0);
    if (total <= 0 || rect.w <= 0 || rect.h <= 0) return;
    const scale = (rect.w * rect.h) / total;
    let rest = items.map(it => ({ it, area: it.value * scale }))
                    .filter(x => x.area > 0);
    let r = { x: rect.x, y: rect.y, w: rect.w, h: rect.h };
    let row = [];
    while (rest.length) {
      const side = Math.min(r.w, r.h);
      const next = rest[0];
      if (row.length && worst(row.concat(next), side) > worst(row, side)) {
        layoutRow(row, r);        // これ以上足すと悪化 → 行を確定
        row = [];
        continue;
      }
      row.push(next);
      rest.shift();
    }
    if (row.length) layoutRow(row, r);
  }

  // 行のアスペクト比の悪さ（大きいほど細長い）
  function worst(row, side) {
    const s = row.reduce((a, b) => a + b.area, 0);
    let maxA = -Infinity, minA = Infinity;
    for (const x of row) { maxA = Math.max(maxA, x.area); minA = Math.min(minA, x.area); }
    const s2 = s * s, side2 = side * side;
    return Math.max((side2 * maxA) / s2, s2 / (side2 * minA));
  }

  // 行を短辺に沿って配置し、残り領域 r を縮める
  function layoutRow(row, r) {
    const s = row.reduce((a, b) => a + b.area, 0);
    const horizontal = r.w >= r.h;          // 横長なら縦の行（左端に縦積み）
    const side = horizontal ? r.h : r.w;
    const thick = s / side;
    let off = 0;
    for (const x of row) {
      const len = x.area / thick;
      if (horizontal) {
        Object.assign(x.it, { x: r.x, y: r.y + off, w: thick, h: len });
      } else {
        Object.assign(x.it, { x: r.x + off, y: r.y, w: len, h: thick });
      }
      off += len;
    }
    if (horizontal) { r.x += thick; r.w -= thick; }
    else { r.y += thick; r.h -= thick; }
  }

  // ---- 描画 ------------------------------------------------------------
  const HEAD_H = 18;   // セクターヘッダー高さ（heatmap.css と揃える）

  function render() {
    root.innerHTML = '';
    const W = root.clientWidth, H = root.clientHeight;
    if (!W || !H) return;

    const sectors = data.sectors.map(sec => ({ value: sec.cap, sec }));
    squarify(sectors, { x: 0, y: 0, w: W, h: H });

    for (const sn of sectors) {
      if (sn.w == null) continue;
      const sec = sn.sec;
      const box = document.createElement('div');
      box.className = 'hm-sector';
      box.style.cssText = `left:${sn.x}px;top:${sn.y}px;width:${sn.w}px;height:${sn.h}px;`;

      const head = document.createElement('div');
      head.className = 'hm-sector-head';
      head.textContent = `${sec.name} ${fmtChg(sec.chg)}`;
      head.title = `${sec.name}（${sec.count}銘柄） ${fmtChg(sec.chg)}`;
      if (sn.h < HEAD_H + 8) head.style.display = 'none';  // 枠が低すぎたらヘッダー省略
      box.appendChild(head);

      const innerH = sn.h - (head.style.display === 'none' ? 0 : HEAD_H);
      const tiles = sec.tiles.map(t => ({ value: t.cap, t }));
      squarify(tiles, { x: 0, y: 0, w: Math.max(sn.w - 2, 0), h: Math.max(innerH - 2, 0) });

      for (const tn of tiles) {
        if (tn.w == null) continue;
        const t = tn.t;
        const el = document.createElement('div');
        el.className = 'hm-tile';
        el.style.cssText =
          `left:${tn.x}px;top:${tn.y + (head.style.display === 'none' ? 0 : HEAD_H)}px;` +
          `width:${tn.w}px;height:${tn.h}px;background:${color(t.chg)};`;

        // タイル内ラベル（小さすぎる場合は省略。文字あふれで隣を汚さない）
        const label = t.code || t.name;
        if (tn.w > 34 && tn.h > 22) {
          const code = document.createElement('span');
          code.className = 't-code';
          code.textContent = label;
          const fs = Math.max(9, Math.min(tn.w / (label.length * 0.68), tn.h * 0.32, 24));
          code.style.fontSize = fs + 'px';
          el.appendChild(code);
          if (tn.h > 40 && fs >= 10) {
            const chg = document.createElement('span');
            chg.className = 't-chg';
            chg.textContent = fmtChg(t.chg);
            chg.style.fontSize = Math.max(9, fs * 0.75) + 'px';
            el.appendChild(chg);
          }
        }

        el.addEventListener('mousemove', ev => showTip(ev, sec, t));
        el.addEventListener('mouseleave', hideTip);
        box.appendChild(el);
      }
      root.appendChild(box);
    }
  }

  // ---- ツールチップ ----------------------------------------------------
  function showTip(ev, sec, t) {
    if (!tooltip) return;
    const codePart = t.code ? `${t.code} ` : '';
    tooltip.innerHTML =
      `<div class="tt-name">${codePart}${escapeHtml(t.name)}</div>` +
      `<div>前日比: ${fmtChg(t.chg)}</div>` +
      `<div>時価総額: ${fmtCap(t.cap)}</div>` +
      `<div style="color:#9ca3af">${escapeHtml(sec.name)}</div>`;
    tooltip.hidden = false;
    const pad = 14;
    let x = ev.clientX + pad, y = ev.clientY + pad;
    const r = tooltip.getBoundingClientRect();
    if (x + r.width > window.innerWidth - 8) x = ev.clientX - r.width - pad;
    if (y + r.height > window.innerHeight - 8) y = ev.clientY - r.height - pad;
    tooltip.style.left = x + 'px';
    tooltip.style.top = y + 'px';
  }
  function hideTip() { if (tooltip) tooltip.hidden = true; }
  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g,
      c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  // ---- 凡例・一覧表の色塗り --------------------------------------------
  document.querySelectorAll('.hm-swatch').forEach(el => {
    el.style.background = color(parseFloat(el.dataset.chg));
  });
  document.querySelectorAll('.hm-cell-chg').forEach(el => {
    el.style.color = color(parseFloat(el.dataset.chg));
  });
  document.querySelectorAll('[data-cap]').forEach(el => {
    el.textContent = fmtCap(parseFloat(el.dataset.cap));
  });

  // ---- 初期描画とリサイズ追従 ------------------------------------------
  render();
  let timer = null;
  window.addEventListener('resize', () => {
    clearTimeout(timer);
    timer = setTimeout(render, 200);
  });
})();
