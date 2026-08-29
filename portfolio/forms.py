"""資産登録の各フォーム

方針: 入力は「何をいくつ持っているか」だけ（ユーザー合意済み）。
保有理由・セクター等の判断項目は持たない（カルテ・売買日記の役割）。
"""
from django import forms

from japan_kabu.impulse import IMPULSE_SECTORS
from japan_kabu.models import Stock

from .models import CashFlow, Holding, Product


def _sector_choices():
    """セクターインパルスと同じ語彙のセクター選択肢（国別optgroup）

    値はセクター名そのもの（日米で同名の「AI・半導体」は同じ値＝同じテーマ扱い）。
    テンプレート側のJSが、入力された銘柄コードに応じて片方のグループだけを表示する。
    """
    jp = [(s['name'], s['name']) for s in IMPULSE_SECTORS['JP']]
    us = [(s['name'], s['name']) for s in IMPULSE_SECTORS['US']]
    return [
        ('', '指定なし（公式業種で表示）'),
        ('日本株セクター', jp),
        ('米国株セクター', us),
    ]


class StockHoldingForm(forms.Form):
    """個別株（日本株・米国株）の期首登録

    code は「4桁コード（日本株）」または「ティッカー（米国株）」を1欄で受ける。
    ⚠️ 日本株は表示コード+'0'（普通株）で確定させる。display_code で引くと
    優先株（9434など）を掴む恐れがある（CLAUDE.md の銘柄解決の罠）。
    """
    code = forms.CharField(label='銘柄コード / ティッカー', max_length=12)
    quantity = forms.FloatField(label='株数', min_value=0.0001)
    avg_cost = forms.FloatField(label='平均取得単価', min_value=0)
    sector = forms.ChoiceField(label='セクター', choices=_sector_choices, required=False)
    account = forms.ChoiceField(label='口座区分', choices=Holding.ACCOUNT_CHOICES, required=False)
    baseline_date = forms.DateField(label='棚卸し日', widget=forms.DateInput(attrs={'type': 'date'}))

    def clean(self):
        cleaned = super().clean()
        raw = (cleaned.get('code') or '').strip().upper()
        if not raw:
            return cleaned
        stock = None
        # 日本株の4桁コードは数字始まりだが英字を含むものがある（130A=QPS研究所 等）。
        # isdigit()判定だと英字入りコードを米国ティッカー扱いして弾いてしまう（実際に起きた）
        if len(raw) == 4 and raw[0].isdigit():
            stock = Stock.objects.filter(code=raw + '0', country='JP').first()
        if stock is None:
            stock = Stock.objects.filter(code=f'US-{raw}', country='US').first()
        if stock is None:
            raise forms.ValidationError(
                f'「{raw}」が銘柄マスタに見つかりません。日本株は4桁コード、'
                '米国株はティッカーで入力してください。')
        cleaned['stock'] = stock
        return cleaned


class FundHoldingForm(forms.Form):
    """投資信託の期首登録

    商品は事前に設定済みのものをプルダウンから選ぶだけ（ユーザー合意:
    フォームで新規商品は作らない。商品の追加・コード管理は商品編集カードで行う）。
    """
    product = forms.ModelChoiceField(
        label='商品', queryset=Product.objects.filter(category='fund'))
    quantity = forms.FloatField(label='口数', min_value=0.0001)
    avg_cost = forms.FloatField(label='取得時基準価額（1万口あたり円）', min_value=0)
    account = forms.ChoiceField(label='口座区分', choices=Holding.ACCOUNT_CHOICES, required=False)
    baseline_date = forms.DateField(label='棚卸し日', widget=forms.DateInput(attrs={'type': 'date'}))


class MetalHoldingForm(forms.Form):
    """金・銀の期首登録。商品マスタは金/銀それぞれ自動で1件作って使い回す"""
    metal = forms.ChoiceField(label='種類', choices=Product.METAL_CHOICES)
    quantity = forms.FloatField(label='グラム数', min_value=0.0001)
    avg_cost = forms.FloatField(label='平均取得単価（円/g）', min_value=0)
    # 口座区分は貴金属には不要（NISA対象外のためユーザー要望で削除）
    baseline_date = forms.DateField(label='棚卸し日', widget=forms.DateInput(attrs={'type': 'date'}))

    def get_or_create_product(self):
        metal = self.cleaned_data['metal']
        label = dict(Product.METAL_CHOICES)[metal]
        product, _ = Product.objects.get_or_create(
            category='metal', metal=metal, defaults={'name': label})
        return product


class ProductEditForm(forms.ModelForm):
    """商品情報の編集（ISIN・協会コードを追記して自動取得へ乗せる）"""
    class Meta:
        model = Product
        fields = ['name', 'isin', 'assoc_fund_code']


class CashBaselineForm(forms.Form):
    """現金の期首残高（PortfolioSetting に保存する）"""
    amount = forms.FloatField(label='現金残高（円）', min_value=0)
    date = forms.DateField(label='棚卸し日', widget=forms.DateInput(attrs={'type': 'date'}))


class CashFlowForm(forms.ModelForm):
    """入出金の記録"""
    class Meta:
        model = CashFlow
        fields = ['date', 'kind', 'amount', 'memo']
        widgets = {'date': forms.DateInput(attrs={'type': 'date'})}
