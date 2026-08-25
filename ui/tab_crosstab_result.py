"""
tab_crosstab_result.py
第3タブ「集計表」。「集計指定」タブで確定したクロス集計指定表・トリプルクロス指定表を
実際のRAWデータに対して一括実行し（GT=単純集計、通常行=属性設問×対象設問の2wayクロス集計、
トリプルクロス=3wayクロス集計、SPEC 5.4節）、各表と属性間の差異を指摘するAIコメント
（core/commentary.py、Haikuモデルで自動一括生成、行ごとにオンオフ可——ユーザーとの
合意事項 2026-08-21）を表示する。実行結果はグラフタブと共有する
（session_state['crosstab_results']・実行ボタンはこのタブにのみ置く——ユーザーとの合意事項）。
表の見出し・軸ラベルには短縮選択肢（core/cross_execute.pyが解決済み）を使う。
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

import llm_client
from core.commentary import (
    COMMENTARY_MODEL,
    generate_cross_commentary,
    generate_list_cross_commentary,
    generate_triple_cross_commentary,
)
from core.cross_execute import run_cross_plan, run_list_cross, run_triple_cross
from core.excel_report import SORT_DESC, build_report_workbook, order_target_labels, workbook_to_bytes
from core.usage_log import DEFAULT_FX_RATE, build_entry as build_log_entry, snapshot as usage_snapshot

_TOTAL_LABEL = '全体'
_UNANSWERED_LABEL = '未回答'


def render(columns: list[str], rows: list[dict], excluded_row_ids: list[int], entries: list[dict],
           cross_table_rows: list[dict], triple_cross_specs: list[dict], list_cross_attrs: list[str],
           list_cross_targets: list[str], list_cross_sort_order: str, cross_plan_confirmed: bool,
           table_format: str, api_key: str) -> None:
    st.subheader('集計表')

    if not rows:
        st.info('サイドバーからRAWファイルを読み込んでください。')
        return
    if not cross_plan_confirmed:
        st.info('先に「集計指定」タブで集計計画表を確定してください。')
        return
    has_triple = any(all(spec.get(k, '').strip() for k in ('attr_large', 'attr_mid', 'target'))
                      for spec in triple_cross_specs)
    if not cross_table_rows and not has_triple:
        st.info('集計指定表に集計項目がありません。「集計指定」タブでチェックを入れてください。')
        return

    st.caption(
        f'{len(cross_table_rows)}件の集計（トリプルクロス{sum(1 for s in triple_cross_specs if all(s.get(k, "").strip() for k in ("attr_large", "attr_mid", "target")))}件含む）'
        'を一括実行します（表・AIコメント・グラフタブ分をまとめて作成します）。'
    )
    run_col, excel_col = st.columns(2)
    with run_col:
        if st.button('▶ 集計表とグラフを作成する', type='primary', key='run_cross_plan', width='stretch'):
            _run(columns, rows, excluded_row_ids, entries, cross_table_rows, triple_cross_specs,
                 list_cross_attrs, list_cross_targets, list_cross_sort_order or SORT_DESC, api_key)
            st.rerun()

    results = st.session_state.get('crosstab_results')
    triple_results = st.session_state.get('triple_cross_results')
    list_cross_results = st.session_state.get('list_cross_results')
    issues = st.session_state.get('crosstab_issues', [])

    excel_label = '📥 集計表をExcel出力する（クロス％実数別表・旧クロス％実数別表・GT・クロス％＆実数表・一覧型クロス集計表）'
    with excel_col:
        if results:
            wb = build_report_workbook(
                results, list_cross_groups=list_cross_results or [],
                list_cross_sort_order=list_cross_sort_order or SORT_DESC,
            )
            st.download_button(
                excel_label, data=workbook_to_bytes(wb), file_name='集計表.xlsx',
                mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                key='download_crosstab_excel', width='stretch',
            )
        else:
            st.button(excel_label, disabled=True, key='download_crosstab_excel_disabled', width='stretch')

    if issues:
        st.warning('一部の集計をスキップしました:\n' + '\n'.join(f'- {i}' for i in issues))
    if not results and not triple_results and not list_cross_results:
        return

    cross_results = [r for r in (results or []) if not r['is_gt']]
    ai_targets = [r for r in cross_results if r.get('ai_comment', True)]
    if not api_key and (ai_targets or triple_results or list_cross_results):
        st.warning('APIキーが未設定のため、AIコメントは表示されません（集計表は作成済みです）。')

    st.divider()
    for i, r in enumerate(results or []):
        title = f"{r['target_label']}（GT）" if r['is_gt'] else f"{r['attr_label']} × {r['target_label']}"
        with st.expander(title, expanded=(i == 0)):
            if r['is_gt']:
                _render_gt_table(r)
            else:
                _render_cross_table(r, table_format)
                if r.get('ai_comment', True):
                    if r.get('commentary'):
                        st.info(r['commentary'])
                    elif api_key:
                        st.caption('AIコメントの作成に失敗しました。')

    if triple_results:
        st.divider()
        st.markdown('##### トリプルクロス指定表の集計結果')
        st.caption('グラフは作成されません（SPEC 5.3.2）。')
        for r in triple_results:
            title = f"{r['attr_large_label']} × {r['attr_mid_label']} × {r['target_label']}"
            with st.expander(title):
                _render_triple_cross_table(r, table_format)
                if r.get('commentary'):
                    st.info(r['commentary'])
                elif api_key:
                    st.caption('AIコメントの作成に失敗しました。')

    if list_cross_results:
        st.divider()
        st.markdown('##### 一覧型クロス集計指定表の集計結果')
        st.caption('グラフは作成されません。列の並べ替えは「集計指定」タブの設定に従います。')
        for group in list_cross_results:
            with st.expander(group['target_label']):
                _render_list_cross_table(group, list_cross_sort_order or SORT_DESC, table_format)
                if group.get('commentary'):
                    st.info(group['commentary'])
                elif api_key:
                    st.caption('AIコメントの作成に失敗しました。')


def _run(columns: list[str], rows: list[dict], excluded_row_ids: list[int], entries: list[dict],
          cross_table_rows: list[dict], triple_cross_specs: list[dict], list_cross_attrs: list[str],
          list_cross_targets: list[str], list_cross_sort_order: str, api_key: str) -> None:
    df = pd.DataFrame(rows)
    if excluded_row_ids:
        df = df[~df['_row_id'].isin(excluded_row_ids)]
    df = df.reset_index(drop=True)

    results, issues = run_cross_plan(df, entries, columns, cross_table_rows)
    triple_results, triple_issues = run_triple_cross(df, entries, columns, triple_cross_specs)
    list_cross_results, list_cross_issues = run_list_cross(df, entries, columns, list_cross_attrs, list_cross_targets)
    issues = issues + triple_issues + list_cross_issues

    ai_targets = [r for r in results if not r['is_gt'] and r.get('ai_comment', True)]
    total_ai_calls = len(ai_targets) + len(triple_results) + len(list_cross_results)
    if api_key and total_ai_calls:
        client = llm_client.make_client('Anthropic', api_key)
        usage_before = usage_snapshot(llm_client.get_token_usage)
        with st.spinner(f'{total_ai_calls}件の集計表についてAIコメントを作成しています...'):
            for r in ai_targets:
                r['commentary'] = generate_cross_commentary(client, r, model=COMMENTARY_MODEL)
            for r in triple_results:
                r['commentary'] = generate_triple_cross_commentary(client, r, model=COMMENTARY_MODEL)
            for group in list_cross_results:
                labels = order_target_labels(group, list_cross_sort_order)
                group['commentary'] = generate_list_cross_commentary(client, group, labels, model=COMMENTARY_MODEL)
        usage_after = usage_snapshot(llm_client.get_token_usage)
        fx_rate = st.session_state.get('fx_rate', DEFAULT_FX_RATE)
        st.session_state.setdefault('usage_log', []).append(
            build_log_entry(f'AIコメント一括生成（{total_ai_calls}件）', COMMENTARY_MODEL, usage_before, usage_after,
                             fx_rate=fx_rate)
        )

    st.session_state['crosstab_results'] = results
    st.session_state['triple_cross_results'] = triple_results
    st.session_state['list_cross_results'] = list_cross_results
    st.session_state['crosstab_issues'] = issues


def _format_pct_value(value: float) -> str:
    """11.0→'11%'、14.7→'14.7%'のように末尾の無駄な0を付けない（ユーザーとの合意事項、2026-08-23）"""
    return f'{value:g}%'


def _render_gt_table(r: dict) -> None:
    """GT（単純集計）表示: 番号列＋未回答行＋全体行を付けて表示する（コア計算関数の戻り値は
    グラフ描画にも使うため、未回答行・全体行を混ぜない——表示専用にここで組み立てる）。
    未回答（実装済み、2026-08-25）は対象設問が空欄だった件数——母数（base）には含まれない。
    """
    st.caption(f"n={r['base']}")
    table = r['table'].copy().rename(columns={'度数': '実数'})
    table['%'] = table['%'].apply(_format_pct_value)
    # 番号列に数値(int)と全体行の空文字列(str)が混在すると、TextColumnとして固定表示を
    # 指定した際にpyarrowのArrow変換がint64への型推定を試みて失敗する実害があった
    # （2026-08-23、列全体を最初からstrに揃えて回避）。
    table.insert(0, '番号', [str(i) for i in range(1, len(table) + 1)])
    unanswered_n = r.get('unanswered', 0)
    total_all = r['base'] + unanswered_n
    unanswered_pct = round(unanswered_n / total_all * 100, 1) if total_all else 0.0
    extra_rows = pd.DataFrame([
        {'番号': '', '選択肢': _UNANSWERED_LABEL, '実数': unanswered_n, '%': _format_pct_value(unanswered_pct)},
        {'番号': '', '選択肢': _TOTAL_LABEL, '実数': r['base'], '%': _format_pct_value(100.0)},
    ])
    st.dataframe(
        pd.concat([table, extra_rows], ignore_index=True), hide_index=True, width='stretch',
        column_config={'番号': st.column_config.TextColumn('番号', pinned=True)},
    )


def _insert_unanswered_column(pct_df: pd.DataFrame, n_df: pd.DataFrame, base: dict,
                               unanswered: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    「全体」列の直前（無ければ最終列）に「未回答」列を挿入したコピーを返す（実装済み、
    2026-08-25）。母数（base）には含まれない、対象設問が空欄だった件数——％はその属性カテゴリの
    総数（母数＋未回答）に対する割合。cross_tabulationは列末尾に「全体」列を持つが、
    triple_cross_tabulationは持たない（現状、画面には母数列自体を出していない）ため、
    「全体」列が無ければ単純に末尾へ追加する。
    """
    pct_df = pct_df.copy()
    n_df = n_df.copy()
    loc = list(n_df.columns).index(_TOTAL_LABEL) if _TOTAL_LABEL in n_df.columns else len(n_df.columns)
    n_values, pct_values = [], []
    for idx in n_df.index:
        u = unanswered.get(idx, 0)
        b = base.get(idx, 0)
        total = u + b
        n_values.append(u)
        pct_values.append(round(u / total * 100, 1) if total else 0.0)
    n_df.insert(loc, _UNANSWERED_LABEL, n_values)
    pct_df.insert(loc, _UNANSWERED_LABEL, pct_values)
    return pct_df, n_df


def _render_cross_table(r: dict, table_format: str) -> None:
    pct_df, n_df = _insert_unanswered_column(r['pct'], r['n'], r['base'], r.get('unanswered', {}))

    if table_format == '％実数別表':
        st.dataframe(_format_pct(pct_df), width='stretch', column_config=_pin('％表'))
        st.dataframe(_format_n(n_df), width='stretch', column_config=_pin('実数表'))
    else:
        st.caption('各属性カテゴリにつき、上段＝実数、下段＝％。')
        st.dataframe(_stack_n_and_pct(n_df, pct_df), hide_index=True, width='stretch', column_config=_pin('属性カテゴリ'))


def _render_triple_cross_table(r: dict, table_format: str) -> None:
    pct_df, n_df = _insert_unanswered_column(r['pct'], r['n'], r['base'], r.get('unanswered', {}))
    if table_format == '％実数別表':
        st.dataframe(_format_triple_multiindex(pct_df, '％表', suffix='%'), width='stretch', column_config=_pin('％表'))
        st.dataframe(_format_triple_multiindex(n_df, '実数表', suffix=''), width='stretch', column_config=_pin('実数表'))
    else:
        st.caption('各属性カテゴリの組につき、上段＝実数、下段＝％。')
        st.dataframe(
            _stack_n_and_pct(n_df, pct_df, index_names=list(n_df.index.names)), hide_index=True, width='stretch',
            column_config=_pin(n_df.index.names[0]),
        )


def _render_list_cross_table(group: dict, sort_order: str, table_format: str) -> None:
    """
    一覧型クロス集計指定表の1グループ（対象設問1つ×指定した属性設問すべて）を画面表示する
    （元はExcel出力専用だったが、ユーザーの要望で画面表示にも対応した、2026-08-23）。
    列の並べ替えは「集計指定」タブで選んだsort_order、行の構成（「全体」行＋各属性設問の
    カテゴリ、属性設問名は各グループの最初の行だけ表示）はExcel出力（core/excel_report.py）
    と同じ考え方。
    """
    labels = order_target_labels(group, sort_order)
    if table_format == '％実数別表':
        st.dataframe(_format_list_cross_table(group, labels, '％表', is_pct=True),
                     hide_index=True, width='stretch', column_config=_pin('％表'))
        st.dataframe(_format_list_cross_table(group, labels, '実数表', is_pct=False),
                     hide_index=True, width='stretch', column_config=_pin('実数表'))
    else:
        st.caption('各属性カテゴリにつき、上段＝実数、下段＝％。')
        st.dataframe(_stack_list_cross_table(group, labels), hide_index=True, width='stretch',
                     column_config=_pin('属性設問'))


def _list_cross_rows(group: dict) -> list[tuple[str, str, dict, dict, int, int]]:
    """
    (属性設問名, カテゴリ名, ％辞書, 実数辞書, 母数, 未回答) のタプル列を返す。先頭は対象設問
    全体の「全体」行。未回答（実装済み、2026-08-25）は母数に含まれない対象設問が空欄だった件数。
    """
    rows = [('', '全体', group['overall_pct'], group['overall_n'], group['overall_base'],
              group.get('overall_unanswered', 0))]
    for attr in group['attrs']:
        for cat in attr['categories']:
            rows.append((attr['attr_label'], cat['label'], cat['pct'], cat['n'], cat['base'],
                          cat.get('unanswered', 0)))
    return rows


def _format_list_cross_table(group: dict, labels: list[str], first_col_label: str, *, is_pct: bool) -> pd.DataFrame:
    data = []
    prev_attr = object()
    for attr_label, category, pct, n, base, unanswered in _list_cross_rows(group):
        shown_attr = attr_label if attr_label != prev_attr else ''
        prev_attr = attr_label
        values = pct if is_pct else n
        row_dict = {first_col_label: shown_attr, '属性カテゴリ': category}
        for label in labels:
            v = values.get(label, 0)
            row_dict[label] = (_format_pct_value(v) if v else '') if is_pct else (str(int(v)) if v else '')
        total = base + unanswered
        if is_pct:
            u_pct = round(unanswered / total * 100, 1) if total else 0.0
            row_dict[_UNANSWERED_LABEL] = _format_pct_value(u_pct) if unanswered else ''
            row_dict['全体'] = f'n={base}'
        else:
            row_dict[_UNANSWERED_LABEL] = str(unanswered) if unanswered else ''
            row_dict['全体'] = str(int(base))
        data.append(row_dict)
    return pd.DataFrame(data)


def _stack_list_cross_table(group: dict, labels: list[str]) -> pd.DataFrame:
    """属性カテゴリごとに実数行・％行を交互に並べたDataFrameを作る（％＆実数表のフォーマット）"""
    rows = []
    prev_attr = object()
    for attr_label, category, pct, n, base, unanswered in _list_cross_rows(group):
        shown_attr = attr_label if attr_label != prev_attr else ''
        prev_attr = attr_label
        total = base + unanswered
        u_pct = round(unanswered / total * 100, 1) if total else 0.0
        rows.append({
            '属性設問': shown_attr, '属性カテゴリ': category, '種別': '実数',
            **{label: n.get(label, 0) for label in labels},
            _UNANSWERED_LABEL: unanswered, '全体': base,
        })
        rows.append({
            '属性設問': '', '属性カテゴリ': '', '種別': '％',
            **{label: f"{pct.get(label, 0)}%" for label in labels},
            _UNANSWERED_LABEL: f'{u_pct}%', '全体': f'n={base}',
        })
    return pd.DataFrame(rows)


def _pin(column: str) -> dict:
    """表の最左列を横スクロールしても固定表示する（ユーザーとの合意事項、2026-08-23）"""
    return {column: st.column_config.TextColumn(column, pinned=True)}


def _format_pct(pct_df: pd.DataFrame) -> pd.DataFrame:
    """
    最左列の見出しは「属性カテゴリ」ではなく「％表」にする——別途st.captionで表の種類を
    示す代わりに、列見出しにその情報を持たせる（ユーザーとの合意事項、2026-08-23）。
    """
    display = pct_df.astype(str) + '%'
    display.insert(0, '％表', display.index)
    return display.reset_index(drop=True)


def _format_n(n_df: pd.DataFrame) -> pd.DataFrame:
    """数値の後ろに「件」は付けない（ユーザーとの合意事項、2026-08-23）"""
    display = n_df.astype(str)
    display.insert(0, '実数表', display.index)
    return display.reset_index(drop=True)


def _format_triple_multiindex(table: pd.DataFrame, first_col_label: str, suffix: str) -> pd.DataFrame:
    """
    トリプルクロスの％表/実数表を整形する。属性大列（MultiIndexの第1階層）は見本のセル結合に
    近い見た目にするため、同じグループの最初の行だけ値を表示し、以降は空欄にする
    （ユーザーとの合意事項、2026-08-23）。属性中列（第2階層）はクロス集計と同じく全行表示。
    """
    display = (table.astype(str) + suffix) if suffix else table.astype(str)
    mid_name = table.index.names[1]
    display.insert(0, mid_name, [t[1] for t in table.index])
    large_values: list[str] = []
    prev = object()
    for t in table.index:
        large_values.append(t[0] if t[0] != prev else '')
        prev = t[0]
    display.insert(0, first_col_label, large_values)
    return display.reset_index(drop=True)


def _stack_n_and_pct(n_df: pd.DataFrame, pct_df: pd.DataFrame, index_names: list[str] | None = None) -> pd.DataFrame:
    """属性カテゴリごとに実数行・％行を交互に並べたDataFrameを作る（％＆実数表のフォーマット）"""
    index_names = index_names or ['属性カテゴリ']
    rows = []
    for idx in n_df.index:
        idx_values = idx if isinstance(idx, tuple) else (idx,)
        base_cols = dict(zip(index_names, idx_values))
        rows.append({**base_cols, '種別': '実数', **{c: n_df.loc[idx, c] for c in n_df.columns}})
        rows.append({**{k: '' for k in base_cols}, '種別': '％', **{c: f'{pct_df.loc[idx, c]}%' for c in pct_df.columns}})
    return pd.DataFrame(rows)
