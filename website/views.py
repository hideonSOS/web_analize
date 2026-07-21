from django.http import Http404
from django.shortcuts import render

# ナビゲーション・トップページ共通の機能一覧（実装が決まり次第、名称と説明を差し替える）
FEATURES = {
    1: {'name': '時価総額ランキング', 'description': '日本株の時価総額TOPを横棒グラフで表示', 'url': 'japan_kabu:index'},
    2: {'name': '出来高急増ランキング', 'description': '出来高の統計的異常度（対数z-score）ランキング', 'url': 'japan_kabu:volume'},
    3: {'name': '銘柄カルテ', 'description': 'IR資料を読みながら共通雛形に記入する詳細分析', 'url': 'karte:index'},
    4: {'name': '売買日記', 'description': '売買判断の記録と振り返り。その後の値動きを自動表示', 'url': 'diary:index'},
    5: {'name': '銘柄別指標', 'description': 'PER/PBR/ROE/ROA等を棒グラフで表示（現在はソニーグループのみ）',
        'url': 'japan_kabu:stock_detail', 'url_args': ['6758']},
}


def index(request):
    return render(request, 'website/index.html', {'features': FEATURES})


def feature(request, num):
    if num not in FEATURES:
        raise Http404
    context = {'features': FEATURES, 'num': num, 'feature': FEATURES[num]}
    return render(request, 'website/feature.html', context)
