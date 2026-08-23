"""
tab_crosstab_spec.py
第2タブ「集計指定」（SPEC 5.3節）。設問×設問のチェック方眼（GT行＝単純集計、通常セル＝
クロス集計、左端の短縮設問文列は横スクロールしても固定表示）と、行一括・列一括の
一斉チェックボタン（表の外に設置——st.data_editorのセル単位の連動チェックは実装が
複雑になるためユーザーと合意した方式）で集計指定を行い、下部の集計指定表（集計表形式・
トリプルクロス指定表・クロス集計指定表＋グラフ種別＋AIコメントのオンオフ）で内容を確認して
確定する。実際の集計・グラフ生成・AIコメント生成（5.4節）は「集計表」「グラフ」タブで行う。
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from core.cross_plan import (
    GRAPH_CHOICES,
    GT_ROW_ID,
    build_cross_rows,
    build_grid_dataframe,
    format_suffix,
    gridable_questions,
    merge_graph_choices,
    question_label,
    validate_cross_table_graphs,
    validate_triple_cross,
)
from core.excel_report import SORT_DEFAULT, SORT_DESC

_TRIPLE_CROSS_SLOTS = 3
_LIST_CROSS_SLOTS = 5
# 既定は「％実数別表」（ユーザーとの合意事項、2026-08-22）
_TABLE_FORMAT_CHOICES = ['％実数別表', '％＆実数表']


def render() -> None:
    entries = st.session_state.get('question_definition', [])
    if not entries:
        st.info('先に「設問定義・RAWデータ確認」タブで設問定義表を作成してください。')
        return

    questions = gridable_questions(entries)
    if len(questions) < 2:
        st.info('集計方眼の対象になる単一選択・複数選択の設問が2つ以上必要です。')
        return

    st.subheader('集計指定インターフェイス')
    _render_grid(questions)

    st.divider()
    st.subheader('集計指定表')
    _render_table_format()
    _render_triple_cross(questions)
    _render_list_cross(questions)
    cross_rows = _render_cross_table(questions)

    st.divider()
    _render_confirm(questions, cross_rows)


def _render_grid(questions: list[dict]) -> None:
    ids_key = tuple(q['id'] for q in questions)
    if st.session_state.get('cross_grid_questions_key') != ids_key:
        st.session_state['cross_grid_df'] = build_grid_dataframe(questions)
        st.session_state['cross_grid_questions_key'] = ids_key
        st.session_state['cross_grid_version'] = st.session_state.get('cross_grid_version', 0) + 1

    st.caption(
        '行×列の設問の組み合わせにチェックを入れると「属性設問（行）×対象設問（列）」の'
        'クロス集計が下の集計指定表に追加されます。行×列と列×行は別の集計として扱われます'
        '（％の母数が異なるため）。GT行にチェックを入れると、その設問だけの単純集計が'
        '追加されます。自分自身どうしの交差（対角セル）は自動的に無視されます。'
    )

    col_config = {'ID': None, '短縮設問文': st.column_config.TextColumn('短縮設問文', disabled=True, pinned=True)}
    for q in questions:
        label = question_label(q)
        header = (label if len(label) <= 12 else f'{label[:12]}…') + format_suffix(q)
        col_config[q['id']] = st.column_config.CheckboxColumn(header)

    df = st.session_state['cross_grid_df']
    version = st.session_state.get('cross_grid_version', 0)
    edited = st.data_editor(
        df, width='stretch', height=min(600, 76 + 36 * len(df)), hide_index=True,
        column_config=col_config, key=f'cross_grid_editor_{version}',
    )
    st.session_state['cross_grid_df'] = edited

    st.markdown('###### 行一括・列一括')
    st.caption('選んだ行/列のチェックを一括でON/OFFします（表内のセルとの連動チェックではなく、表の外の操作ボタンです）。')
    row_labels = {GT_ROW_ID: '（GT行）', **{q['id']: question_label(q) for q in questions}}
    col_labels = {q['id']: question_label(q) for q in questions}

    bcol1, bcol2 = st.columns(2)
    with bcol1:
        row_id = st.selectbox(
            '行を選択', list(row_labels.keys()), format_func=lambda k: row_labels[k], key='bulk_row_select',
        )
        rc1, rc2 = st.columns(2)
        with rc1:
            if st.button('この行を全てON', key='bulk_row_on', width='stretch'):
                _bulk_set_row(row_id, True)
        with rc2:
            if st.button('この行を全て解除', key='bulk_row_off', width='stretch'):
                _bulk_set_row(row_id, False)
    with bcol2:
        col_id = st.selectbox(
            '列を選択', list(col_labels.keys()), format_func=lambda k: col_labels[k], key='bulk_col_select',
        )
        cc1, cc2 = st.columns(2)
        with cc1:
            if st.button('この列を全てON', key='bulk_col_on', width='stretch'):
                _bulk_set_col(col_id, True)
        with cc2:
            if st.button('この列を全て解除', key='bulk_col_off', width='stretch'):
                _bulk_set_col(col_id, False)


def _bulk_set_row(row_id: str, value: bool) -> None:
    df = st.session_state['cross_grid_df'].copy()
    bool_cols = [c for c in df.columns if c not in ('ID', '短縮設問文')]
    df.loc[df['ID'] == row_id, bool_cols] = value
    st.session_state['cross_grid_df'] = df
    st.session_state['cross_grid_version'] = st.session_state.get('cross_grid_version', 0) + 1
    st.rerun()


def _bulk_set_col(col_id: str, value: bool) -> None:
    df = st.session_state['cross_grid_df'].copy()
    df[col_id] = value
    st.session_state['cross_grid_df'] = df
    st.session_state['cross_grid_version'] = st.session_state.get('cross_grid_version', 0) + 1
    st.rerun()


def _render_table_format() -> None:
    st.markdown('###### 集計表形式')
    current = st.session_state.get('cross_table_format', _TABLE_FORMAT_CHOICES[0])
    choice = st.radio(
        '集計表形式', _TABLE_FORMAT_CHOICES, index=_TABLE_FORMAT_CHOICES.index(current),
        horizontal=True, key='cross_table_format_radio', label_visibility='collapsed',
    )
    st.session_state['cross_table_format'] = choice


_UNSELECTED = ''


def _render_triple_cross(questions: list[dict]) -> None:
    st.markdown('###### トリプルクロス指定表')
    st.caption('プルダウンから設問を選択してください。3セットまで作成できます。グラフは作成されません。')

    choices = [_UNSELECTED, *[question_label(q) for q in questions]]
    suffix_by_label = {question_label(q): format_suffix(q) for q in questions}
    # 選択値そのもの（保存・照合キー）にはサフィックスを付けず、表示だけformat_funcで
    # 付け足す——プロジェクトファイルに保存済みの選択値との互換性を保つため
    # （ユーザーとの合意事項、2026-08-23）。
    format_func = lambda v: v + suffix_by_label.get(v, '') if v else '（未選択）'  # noqa: E731

    stored = st.session_state.get('triple_cross_specs') or []
    updated = []
    for i in range(_TRIPLE_CROSS_SLOTS):
        spec = stored[i] if i < len(stored) else {'attr_large': '', 'attr_mid': '', 'target': ''}
        c1, c2, c3 = st.columns(3)
        with c1:
            attr_large = st.selectbox(
                f'属性設問（大・表最左）{i + 1}', choices, format_func=format_func,
                index=_choice_index(choices, spec.get('attr_large', '')), key=f'triple_attr_large_{i}',
            )
        with c2:
            attr_mid = st.selectbox(
                f'属性設問（中・表左）{i + 1}', choices, format_func=format_func,
                index=_choice_index(choices, spec.get('attr_mid', '')), key=f'triple_attr_mid_{i}',
            )
        with c3:
            target = st.selectbox(
                f'対象設問（表頭）{i + 1}', choices, format_func=format_func,
                index=_choice_index(choices, spec.get('target', '')), key=f'triple_target_{i}',
            )
        updated.append({'attr_large': attr_large, 'attr_mid': attr_mid, 'target': target})
    st.session_state['triple_cross_specs'] = updated


def _render_list_cross(questions: list[dict]) -> None:
    st.markdown('###### 一覧型クロス集計指定表')
    st.caption(
        'ここでの指定はExcel出力時にのみ使われます（画面の集計表・グラフタブには反映されません）。'
        '指定した属性設問のセット（表左側、対象設問すべてに共通）と、指定した対象設問（表頭、'
        '1つにつき1表）を組み合わせて、Excelの「一覧型クロス集計表」シートに出力します。'
        '例えば属性設問を3つ指定すると、対象設問がいくつあっても表左側は常にこの3設問のまま、'
        '対象設問ごとに表が作られます。プルダウンから設問を選択してください（属性・対象'
        'それぞれ5つまで指定できます）。'
    )

    choices = [_UNSELECTED, *[question_label(q) for q in questions]]
    suffix_by_label = {question_label(q): format_suffix(q) for q in questions}
    format_func = lambda v: v + suffix_by_label.get(v, '') if v else '（未選択）'  # noqa: E731

    sort_choices = [SORT_DESC, SORT_DEFAULT]
    current_sort = st.session_state.get('list_cross_sort_order', SORT_DESC)
    sort_order = st.radio(
        '列の並べ替え', sort_choices,
        index=sort_choices.index(current_sort) if current_sort in sort_choices else 0,
        horizontal=True, key='list_cross_sort_radio',
    )
    st.session_state['list_cross_sort_order'] = sort_order

    attr_col, target_col = st.columns(2)
    stored_attrs = st.session_state.get('list_cross_attrs') or []
    with attr_col:
        st.markdown('**属性設問（表左側、共通）**')
        updated_attrs = []
        for i in range(_LIST_CROSS_SLOTS):
            value = stored_attrs[i] if i < len(stored_attrs) else ''
            attr = st.selectbox(
                f'属性設問{i + 1}', choices, format_func=format_func,
                index=_choice_index(choices, value), key=f'list_cross_attr_{i}',
            )
            updated_attrs.append(attr)
        st.session_state['list_cross_attrs'] = updated_attrs

    stored_targets = st.session_state.get('list_cross_targets') or []
    with target_col:
        st.markdown('**対象設問（表頭、1つにつき1表）**')
        updated_targets = []
        for i in range(_LIST_CROSS_SLOTS):
            value = stored_targets[i] if i < len(stored_targets) else ''
            target = st.selectbox(
                f'対象設問{i + 1}', choices, format_func=format_func,
                index=_choice_index(choices, value), key=f'list_cross_target_{i}',
            )
            updated_targets.append(target)
        st.session_state['list_cross_targets'] = updated_targets


def _choice_index(choices: list[str], value: str) -> int:
    """保存済みの値が設問構成の変化などで選択肢から消えている場合は未選択に戻す"""
    return choices.index(value) if value in choices else 0


def _render_cross_table(questions: list[dict]) -> list[dict]:
    st.markdown('###### クロス集計指定表')
    grid_df = st.session_state.get('cross_grid_df')
    cross_rows = build_cross_rows(grid_df, questions) if grid_df is not None else []
    cross_rows = merge_graph_choices(cross_rows, st.session_state.get('cross_table_rows', []))

    if not cross_rows:
        st.caption('上の集計指定インターフェイスでチェックを入れると、ここに一覧が追加されます。')
        st.session_state['cross_table_rows'] = []
        return []

    st.caption(
        f'{len(cross_rows)}件の集計が指定されています。グラフ列は空欄（グラフなし）または'
        f'「{"/".join(GRAPH_CHOICES)}」のいずれかを入力してください。AIコメント列のチェックを'
        '外すと、その行はAI分析（属性間の差異の指摘）を作成しません（GT行にはAIコメントは'
        '作成されないため、チェックの有無は影響しません）。'
    )

    ai_col1, ai_col2 = st.columns(2)
    with ai_col1:
        if st.button('AIコメントを全選択', key='ai_comment_select_all', width='stretch'):
            _bulk_set_ai_comment(cross_rows, True)
    with ai_col2:
        if st.button('AIコメントを全解除', key='ai_comment_deselect_all', width='stretch'):
            _bulk_set_ai_comment(cross_rows, False)

    table_df = pd.DataFrame([
        {
            '属性設問': r['attr_label'] or 'GT', '対象設問': r['target_label'], 'グラフ': r['graph'],
            'AIコメント': r.get('ai_comment', True),
        }
        for r in cross_rows
    ]).astype({'属性設問': object, '対象設問': object, 'グラフ': object, 'AIコメント': bool})
    # 行の並び・件数が変わるたびにキーごと再構築する（同じキーのまま行数が変わると
    # data_editorの内部状態が新しい行と噛み合わなくなるため）。グラフ・AIコメント値は
    # cross_rows側に既に引き継ぎ済みなので、再構築しても入力済みの内容は失われない。
    rows_signature = '|'.join(f"{r['attr']}-{r['target']}" for r in cross_rows)
    ai_version = st.session_state.get('cross_table_ai_version', 0)
    edited = st.data_editor(
        table_df, width='stretch', hide_index=True, disabled=['属性設問', '対象設問'],
        column_config={
            '属性設問': st.column_config.TextColumn('属性設問', pinned=True),
            'AIコメント': st.column_config.CheckboxColumn('AIコメント'),
        },
        key=f'cross_table_editor_{hash(rows_signature)}_{ai_version}',
    )
    for row, (_, edited_row) in zip(cross_rows, edited.iterrows()):
        row['graph'] = str(edited_row['グラフ'] or '').strip()
        row['ai_comment'] = bool(edited_row['AIコメント'])

    st.session_state['cross_table_rows'] = cross_rows
    return cross_rows


def _bulk_set_ai_comment(cross_rows: list[dict], value: bool) -> None:
    """
    AIコメント列を全行まとめてON/OFFする。cross_grid_versionと同じ理由（st.data_editorは
    ウィジェットkeyが変わらない限り、渡したdataframeの新しい内容より内部状態を優先して
    表示してしまう）でcross_table_ai_versionを別途持ち、キーに含めて強制的に作り直す。
    """
    for row in cross_rows:
        row['ai_comment'] = value
    st.session_state['cross_table_rows'] = cross_rows
    st.session_state['cross_table_ai_version'] = st.session_state.get('cross_table_ai_version', 0) + 1
    st.rerun()


def _render_confirm(questions: list[dict], cross_rows: list[dict]) -> None:
    confirmed = st.session_state.get('cross_plan_confirmed', False)
    if confirmed:
        st.success('集計指定表は確定済みです。')
        if st.button('🔓 保護を解除する', key='unlock_cross_plan'):
            st.session_state['cross_plan_confirmed'] = False
            st.rerun()
        return

    if st.button('✅ 集計計画表を確定', type='primary', key='confirm_cross_plan'):
        warnings = [
            *validate_triple_cross(st.session_state.get('triple_cross_specs', []), questions),
            *validate_cross_table_graphs(cross_rows),
        ]
        if warnings:
            st.error('\n'.join(f'- {w}' for w in warnings))
        else:
            st.session_state['cross_plan_confirmed'] = True
            st.rerun()
