"""
tab_data_export.py
第6タブ「データ抽出」。最初の機能「設問指定RAWデータ出力」——指定した設問だけを選んで、
列見出し（短縮設問文/原文設問文）・選択肢型設問の値（短縮選択肢/原文選択肢）を設問ごとに
切り替えてRAWデータをCSVとして書き出す（ユーザーとの合意事項、2026-08-23）。
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from core.cross_plan import format_suffix, question_label
from core.data_export import build_export_dataframe
from core.question_definition import FORMAT_MA, FORMAT_SA

_MAX_ROWS = 12
_UNSELECTED = ''


def render(columns: list[str], rows: list[dict], excluded_row_ids: list[int], entries: list[dict]) -> None:
    st.subheader('データ抽出')

    if not rows:
        st.info('サイドバーからRAWファイルを読み込んでください。')
        return
    if not entries:
        st.info('先に「設問定義・RAWデータ確認」タブで設問定義表を作成してください。')
        return

    _render_question_export(columns, rows, excluded_row_ids, entries)


def _render_question_export(columns: list[str], rows: list[dict], excluded_row_ids: list[int],
                             entries: list[dict]) -> None:
    by_label = {question_label(e): e for e in entries}
    choices = [_UNSELECTED, *by_label.keys()]
    # 選択肢に設問形式のサフィックス（SA=|S・MA=|M・FA=|F）を表示に付ける——選択値自体
    # （session_stateに保存される文字列）は変えない（ユーザーとの合意事項、2026-08-23）。
    display_func = lambda v: v + format_suffix(by_label[v]) if v else '（未選択）'  # noqa: E731

    title_col, serial_col, button_col = st.columns([3, 2, 2])
    with title_col:
        st.markdown('##### 設問指定RAWデータ出力')
    with serial_col:
        add_serial = st.checkbox('最左列に通し番号をつける', value=True, key='data_export_add_serial')

    st.caption('出力したい設問を上から順にプルダウンで指定してください（最大12件）。選択肢型設問は'
               '値を短縮選択肢/原文選択肢のどちらで出力するか、設問文は列見出しを短縮設問文/原文設問文の'
               'どちらにするか、それぞれ設問ごとに選べます（既定はどちらも短縮版）。')

    head1, head2, head3 = st.columns([3, 2, 2])
    with head1:
        st.caption('対象設問')
    with head2:
        st.caption('選択肢（選択肢型設問のみ）')
    with head3:
        st.caption('設問文（列見出し）')

    selections: list[dict] = []
    for i in range(_MAX_ROWS):
        qkey = f'data_export_q_{i}'
        if st.session_state.get(qkey) not in choices:
            # 設問構成が変わって以前の選択が消えている場合は未選択に戻す（st.selectboxは
            # 保存済みの値がoptionsに無いとStreamlitAPIExceptionになるため、この保護が必要）
            st.session_state.pop(qkey, None)

        c1, c2, c3 = st.columns([3, 2, 2])
        with c1:
            label = st.selectbox(
                f'設問{i + 1}', choices, key=qkey, label_visibility='collapsed', format_func=display_func,
            )
        if not label:
            continue
        entry = by_label.get(label)
        if entry is None:
            continue

        with c2:
            if entry['format'] in (FORMAT_SA, FORMAT_MA):
                option_mode = st.radio(
                    f'選択肢{i}', ['短縮型選択肢名', '原文選択肢名'], key=f'data_export_opt_{i}',
                    horizontal=True, label_visibility='collapsed',
                )
            else:
                option_mode = '短縮型選択肢名'
        with c3:
            header_mode = st.radio(
                f'設問文{i}', ['短縮型質問文', '原文設問文'], key=f'data_export_header_{i}',
                horizontal=True, label_visibility='collapsed',
            )

        selections.append({
            'entry_id': entry['id'],
            'option_mode': 'short' if option_mode == '短縮型選択肢名' else 'original',
            'header_mode': 'short' if header_mode == '短縮型質問文' else 'original',
        })

    with button_col:
        if not selections:
            st.button('📥 RAWデータを出力する', key='data_export_download_disabled', disabled=True, width='stretch')
        else:
            df = pd.DataFrame(rows)
            if excluded_row_ids:
                df = df[~df['_row_id'].isin(excluded_row_ids)]
            df = df.reset_index(drop=True)
            export_df = build_export_dataframe(df, entries, columns, selections, add_serial=add_serial)
            st.download_button(
                '📥 RAWデータを出力する', data=export_df.to_csv(index=False).encode('utf-8-sig'),
                file_name='抽出データ.csv', mime='text/csv', key='data_export_download', width='stretch',
            )
