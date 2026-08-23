"""
charts.py
グラフ生成（Plotly、画面表示用）。core/aggregate.pyの集計結果を受け取って描画するだけで、
集計ロジック自体は持たない。
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go


def build_bar_chart(tabulation: pd.DataFrame, title: str, short_labels: dict[str, str] | None = None) -> go.Figure:
    """
    単純集計結果（'選択肢'・'度数'・'%'列を持つDataFrame）から横棒グラフを作る。
    度数の多い順（下から上に多い順）に並べ、short_labelsが与えられていれば軸ラベルにのみ
    短縮版を使う（渡されたtabulation自体の表記は変えない）。
    """
    short_labels = short_labels or {}
    ordered = tabulation.sort_values('度数', ascending=True)
    axis_labels = [short_labels.get(opt, opt) for opt in ordered['選択肢']]

    fig = go.Figure(go.Bar(
        x=ordered['%'], y=axis_labels, orientation='h',
        text=[f'{n}件（{p}%）' for n, p in zip(ordered['度数'], ordered['%'])],
        textposition='outside',
    ))
    fig.update_layout(
        title=title[:80] + ('…' if len(title) > 80 else ''),
        xaxis_title='%', margin=dict(l=10, r=10, t=40, b=10),
        height=max(240, 32 * len(ordered) + 80),
    )
    return fig
