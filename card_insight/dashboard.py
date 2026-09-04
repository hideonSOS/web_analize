"""HTML ダッシュボード出力(アプリの画面イメージのたたき台)。

単一 HTML に JSON データを埋め込み、Chart.js(cdnjs)で描画する。
アプリ本体に組み込む際は、ここで作っている `payload` をそのまま API のレスポンス形にする想定。
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


def build_payload(
    monthly: pd.DataFrame,
    by_cat: pd.DataFrame,
    subs: pd.DataFrame,
    merchants: pd.DataFrame,
    distortions: pd.DataFrame,
    candidates: pd.DataFrame,
    capacity: pd.DataFrame,
    reconcile_summary: pd.DataFrame,
    monthly_all: pd.DataFrame | None = None,
    investment_monthly: pd.DataFrame | None = None,
    exclusion_summary: pd.DataFrame | None = None,
) -> dict:
    def recs(df: pd.DataFrame) -> list[dict]:
        if df is None or len(df) == 0:
            return []
        d = df.copy()
        for c in d.columns:
            if pd.api.types.is_datetime64_any_dtype(d[c]):
                d[c] = d[c].dt.strftime("%Y-%m-%d")
        return json.loads(d.to_json(orient="records", force_ascii=False))

    return {
        "monthly": recs(monthly),
        "by_category": recs(by_cat),
        "subscriptions": recs(subs),
        "merchants": recs(merchants),
        "distortions": recs(distortions),
        "candidates": recs(candidates),
        "capacity": recs(capacity),
        "reconcile": recs(reconcile_summary),
        "monthly_all": recs(monthly_all),
        "investment_monthly": recs(investment_monthly),
        "exclusion": recs(exclusion_summary),
    }


_TEMPLATE = """<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><title>カード支出ダッシュボード(たたき台)</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js"></script>
<style>
 body{font-family:"Segoe UI","Yu Gothic UI",sans-serif;margin:0;background:#f6f7f9;color:#222}
 header{background:#1f3b5a;color:#fff;padding:14px 24px;font-size:18px}
 main{padding:16px 24px;display:grid;grid-template-columns:1fr 1fr;gap:16px}
 .card{background:#fff;border-radius:10px;padding:14px 16px;box-shadow:0 1px 3px rgba(0,0,0,.08)}
 .card h2{font-size:14px;margin:0 0 8px;color:#1f3b5a}
 .wide{grid-column:1/3}
 table{border-collapse:collapse;width:100%;font-size:12px}
 th,td{border-bottom:1px solid #e5e7eb;padding:4px 6px;text-align:left;vertical-align:top}
 td.num{text-align:right;font-variant-numeric:tabular-nums}
 .kpi{display:flex;gap:16px;flex-wrap:wrap}
 .kpi div{flex:1;min-width:180px;background:#eef3f8;border-radius:8px;padding:10px}
 .kpi b{display:block;font-size:20px}
 .tag{display:inline-block;padding:1px 6px;border-radius:4px;font-size:11px;background:#e5e7eb}
 .tag.裁量{background:#fde2e2}.tag.要確認{background:#fff3cd}.tag.準必須{background:#e2f0d9}.tag.必須{background:#d9e8f5}
 small{color:#666}
</style></head><body>
<header>支出ダッシュボード(たたき台) <small style="color:#cfd8e3">Zaim × 楽天e-navi 統合台帳</small></header>
<main>
 <section class="card wide"><h2>入金力の試算</h2><div class="kpi" id="kpi"></div></section>
 <section class="card"><h2>全支出の月次推移(支払元別)</h2><canvas id="c_all"></canvas></section>
 <section class="card"><h2>投資入金の実績(楽天証券への振込)</h2><canvas id="c_inv"></canvas></section>
 <section class="card"><h2>カード支出の月次推移(サブスク / 年会費 / 変動)</h2><canvas id="c_month"></canvas></section>
 <section class="card"><h2>カテゴリ別(直近12か月)</h2><canvas id="c_cat"></canvas></section>
 <section class="card"><h2>加盟店別 上位(直近12か月)</h2><canvas id="c_merchant"></canvas></section>
 <section class="card"><h2>突合状況(Zaim × e-navi)と除外</h2><div id="recon"></div><div id="excl" style="margin-top:8px"></div>
   <small>除外は集計に入れていない行(カード引落・証券振込・二重計上疑いなど)。</small></section>
 <section class="card wide"><h2>歪みの指摘</h2><div id="dist"></div></section>
 <section class="card wide"><h2>節約候補(年間効果額順)</h2><div id="cand"></div></section>
 <section class="card wide"><h2>サブスク一覧</h2><div id="subs"></div></section>
</main>
<script>
const D = __PAYLOAD__;
const yen = n => (n==null?"":Number(n).toLocaleString("ja-JP"));
function table(el, rows, cols, numCols){
  if(!rows.length){document.getElementById(el).innerHTML="<small>該当なし</small>";return;}
  cols = cols || Object.keys(rows[0]);
  let h="<table><tr>"+cols.map(c=>"<th>"+c+"</th>").join("")+"</tr>";
  for(const r of rows){h+="<tr>"+cols.map(c=>{
    const v=r[c]; const isN=typeof v==="number";
    if(c==="necessity"||c==="優先度") return "<td><span class='tag "+v+"'>"+v+"</span></td>";
    return "<td class='"+(isN?"num":"")+"'>"+(isN?yen(v):(v??""))+"</td>";}).join("")+"</tr>";}
  document.getElementById(el).innerHTML=h+"</table>";
}
// KPI
document.getElementById("kpi").innerHTML = D.capacity.map(r=>"<div>"+r["項目"]+"<b>¥"+yen(r["値"])+"</b></div>").join("");
// 全支出(支払元別)
const MA = (D.monthly_all||[]).filter(r=>r.ym>="2024-01");
new Chart(document.getElementById("c_all"),{type:"bar",data:{labels:MA.map(r=>r.ym),
 datasets:[{label:"カード",data:MA.map(r=>r.card),backgroundColor:"#1f77b4"},
           {label:"現金",data:MA.map(r=>r.cash),backgroundColor:"#2ca02c"},
           {label:"銀行",data:MA.map(r=>r.bank),backgroundColor:"#9467bd"},
           {label:"未設定",data:MA.map(r=>r.unset),backgroundColor:"#bcbd22"}]},
 options:{scales:{x:{stacked:true},y:{stacked:true}},plugins:{legend:{position:"bottom"}}}});
// 投資入金
const IV = (D.investment_monthly||[]);
new Chart(document.getElementById("c_inv"),{type:"bar",data:{labels:IV.map(r=>r.ym),datasets:[{label:"入金",data:IV.map(r=>r.amount),backgroundColor:"#d62728"}]},
 options:{plugins:{legend:{display:false}}}});
table("excl", D.exclusion||[]);
// 月次
new Chart(document.getElementById("c_month"),{type:"bar",data:{labels:D.monthly.map(r=>r.ym),
 datasets:[{label:"サブスク",data:D.monthly.map(r=>r["サブスク"]),backgroundColor:"#1f77b4"},
           {label:"年会費",data:D.monthly.map(r=>r["年会費"]),backgroundColor:"#9467bd"},
           {label:"変動",data:D.monthly.map(r=>r["変動"]),backgroundColor:"#ff7f0e"}]},
 options:{scales:{x:{stacked:true},y:{stacked:true}},plugins:{legend:{position:"bottom"}}}});
// カテゴリ
const catTotals={}; for(const r of D.by_category){for(const k in r){if(k!=="ym")catTotals[k]=(catTotals[k]||0)+r[k];}}
const catKeys=Object.keys(catTotals).sort((a,b)=>catTotals[b]-catTotals[a]);
new Chart(document.getElementById("c_cat"),{type:"doughnut",data:{labels:catKeys,datasets:[{data:catKeys.map(k=>catTotals[k])}]},
 options:{plugins:{legend:{position:"right"}}}});
// 加盟店
const topM=D.merchants.slice(0,15);
new Chart(document.getElementById("c_merchant"),{type:"bar",data:{labels:topM.map(r=>r.merchant),datasets:[{label:"合計",data:topM.map(r=>r.total),backgroundColor:"#2ca02c"}]},
 options:{indexAxis:"y",plugins:{legend:{display:false}}}});
table("recon", D.reconcile);
table("dist", D.distortions);
table("cand", D.candidates);
table("subs", D.subscriptions, ["merchant","判定","kind","necessity","category","months","median","月額換算","年額換算","last","直近月に発生","note"]);
</script></body></html>
"""


def write_dashboard(path: str | Path, payload: dict) -> Path:
    path = Path(path)
    html = _TEMPLATE.replace("__PAYLOAD__", json.dumps(payload, ensure_ascii=False))
    path.write_text(html, encoding="utf-8")
    return path
