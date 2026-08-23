"""
tab_charts.py
第4タブ「グラフ」。実行ボタンは持たない——「集計表」タブの実行ボタンで一括計算した結果
（session_state['crosstab_results']）をグラフとして表示するだけ（ユーザーとの合意事項
2026-08-21：実行は共通ボタン1つ、集計表タブに配置）。
現状、core/charts.pyには単純集計向けの横棒グラフのみ実装済み（SPEC 5.5、5種類のうち1種類）。
GT行（単純集計）はそのまま流用できるが、属性×対象のクロス集計行（複数カテゴリ×複数選択肢の
グループ化棒グラフが必要）はグラフ種別を問わずまだ未実装——次のステップとして案内する。
"""

from __future__ import annotations

import streamlit as st

from core.charts import build_bar_chart

_GRAPH_NONE_LABELS = ('', 'なし')


def render() -> None:
    st.subheader('グラフ')

    results = st.session_state.get('crosstab_results')
    if not results:
        st.info('先に「集計表」タブの「▶ 集計表とグラフを作成する」を実行してください。')
        return

    chart_targets = [r for r in results if r.get('graph', '') not in _GRAPH_NONE_LABELS]
    if not chart_targets:
        st.caption('「集計指定」タブのクロス集計指定表で、グラフ種別を指定した行がありません。')
        return

    for i, r in enumerate(results):
        graph = r.get('graph', '')
        if graph in _GRAPH_NONE_LABELS:
            continue

        title = f"{r['target_label']}（GT）" if r['is_gt'] else f"{r['attr_label']} × {r['target_label']}"
        st.markdown(f'##### {title}')

        if r['is_gt']:
            if graph != '横棒':
                st.caption(f'グラフ種別「{graph}」は未実装です（現状は横棒グラフのみ実装済み）。')
                st.divider()
                continue
            fig = build_bar_chart(r['table'], title)
            st.plotly_chart(fig, width='stretch', key=f'chart_gt_{i}')
            _png_download_button(fig, i)
        else:
            st.caption(
                f'属性×対象のクロス集計のグラフ（指定: {graph}）は未実装です。'
                '現状はGT行（単純集計）の横棒グラフのみ対応しています。次のステップで実装予定です。'
            )
        st.divider()


def _png_download_button(fig, index: int) -> None:
    png_bytes = fig.to_image(format='png', scale=2)
    st.download_button(
        '🖼️ PNGダウンロード', data=png_bytes, file_name=f'chart_{index + 1:02d}.png',
        mime='image/png', key=f'download_png_{index}',
    )
