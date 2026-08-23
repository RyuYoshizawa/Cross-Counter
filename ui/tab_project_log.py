"""
tab_project_log.py
第5タブ「プロジェクトログ」。LLM API呼び出しの作業ログ（作業日時・トークン使用量・
為替レート・概算コスト）を一覧表示する（ユーザーとの合意事項、2026-08-21）。最上行に
合計（トークンは合計、コストは合計、為替レートは平均値）を表示する。タブ上部に為替レートの
入力欄があり、既定値（150円/USD）を変更できる（ユーザーとの合意事項、2026-08-22）——
変更は以降に記録するログにのみ適用され、既に記録済みのログはそのときのレートのまま残る。
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import streamlit as st

from core.usage_log import DEFAULT_FX_RATE

_COLUMNS = ['作業日時', '内容', 'モデル', '入力トークン', '出力トークン',
            'キャッシュ読込', 'キャッシュ書込', '為替レート（円/USD）', '概算コスト（円）']


def render() -> None:
    st.subheader('プロジェクトログ')

    current_rate = st.session_state.get('fx_rate', DEFAULT_FX_RATE)
    new_rate = st.number_input(
        '為替レート（円/USD）', min_value=0.01, value=float(current_rate), step=1.0, key='fx_rate_input',
        help='以降に記録するログのコスト計算に使う為替レートです（既定値150円/USD）。'
             '実際の為替APIとは連携していない概算換算のため、必要に応じてここで当日のレートに'
             '調整してください。変更しても、既に記録済みのログの値は変わりません。',
    )
    st.session_state['fx_rate'] = new_rate

    log = st.session_state.get('usage_log', [])
    if not log:
        st.info('まだAPI呼び出しの記録がありません（設問定義表の作成やAIコメント生成を行うと記録されます）。')
        return

    st.caption(
        '概算コストです。為替レートは記録した時点でこの欄に入力されていた値であり、'
        '実際の市場レートのリアルタイム取得ではありません。合計行の為替レートは平均値、'
        'それ以外は合計値です。'
    )

    df = pd.DataFrame(log)
    total_row = {
        '作業日時': '合計', '内容': f'{len(df)}件', 'モデル': '',
        '入力トークン': int(df['input_tokens'].sum()), '出力トークン': int(df['output_tokens'].sum()),
        'キャッシュ読込': int(df['cache_read_tokens'].sum()), 'キャッシュ書込': int(df['cache_creation_tokens'].sum()),
        '為替レート（円/USD）': round(df['fx_rate'].mean(), 1), '概算コスト（円）': round(df['cost_jpy'].sum(), 1),
    }
    rows = [total_row] + [
        {
            '作業日時': _format_ts(r['timestamp']), '内容': r['purpose'], 'モデル': r['model'],
            '入力トークン': r['input_tokens'], '出力トークン': r['output_tokens'],
            'キャッシュ読込': r['cache_read_tokens'], 'キャッシュ書込': r['cache_creation_tokens'],
            '為替レート（円/USD）': r['fx_rate'], '概算コスト（円）': r['cost_jpy'],
        }
        for r in reversed(log)  # 新しい記録を上に
    ]
    st.dataframe(
        pd.DataFrame(rows, columns=_COLUMNS), hide_index=True, width='stretch',
        column_config={'作業日時': st.column_config.TextColumn('作業日時', pinned=True)},
    )


def _format_ts(iso_ts: str) -> str:
    try:
        return datetime.fromisoformat(iso_ts).strftime('%Y-%m-%d %H:%M:%S')
    except ValueError:
        return iso_ts
