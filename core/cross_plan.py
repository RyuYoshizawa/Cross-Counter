"""
cross_plan.py
集計指定インターフェイス・集計指定表のデータモデル（Streamlit非依存、SPEC 5.3節）。
設問定義表のSA/MA設問から行×列のチェック方眼の骨組みを作り、GT行（単純集計）・
通常セル（クロス集計）のチェック状態から集計指定表（単純集計・クロス集計のリスト）を
組み立てる。実際の集計計算（5.4節）は別モジュールで行う（本モジュールは指定内容のみを扱う）。
"""

from __future__ import annotations

import pandas as pd

from core.question_definition import FORMAT_FA, FORMAT_MA, FORMAT_SA

GT_ROW_ID = '__GT__'
GRAPH_CHOICES = ['縦棒', '横棒', '円', '帯', '折れ線']
_META_COLUMNS = ('ID', '短縮設問文')

_FORMAT_SUFFIX = {FORMAT_SA: '|S', FORMAT_MA: '|M', FORMAT_FA: '|F'}


def gridable_questions(entries: list[dict]) -> list[dict]:
    """集計方眼の行・列として使える設問（SA/MA）のみを抽出する（FAは集計軸にならない）"""
    return [e for e in entries if e['format'] in (FORMAT_SA, FORMAT_MA)]


def question_label(entry: dict) -> str:
    """
    設問の識別ラベル（照合キーとしても使われる——集計指定表・トリプルクロス指定表・
    データ抽出タブの選択値は、このラベルの文字列そのものをプロジェクトファイルに保存する。
    そのため既存の保存済みプロジェクトとの互換性維持のためサフィックスは付けない
    ——表示用のサフィックス付きラベルはformat_suffix()/question_display_label()を使うこと。
    """
    return entry['short_question'] or entry['question_text']


def format_suffix(entry: dict) -> str:
    """設問形式のサフィックス（SA=|S・MA=|M・FA=|F）。表示専用（画面上で一目で形式が
    分かるように、ユーザーとの合意事項 2026-08-23）——照合キーには使わないこと。"""
    return _FORMAT_SUFFIX.get(entry['format'], '')


def question_display_label(entry: dict) -> str:
    """question_labelに表示専用の設問形式サフィックスを付けたもの（照合には使わないこと）"""
    return question_label(entry) + format_suffix(entry)


def build_grid_dataframe(questions: list[dict]) -> pd.DataFrame:
    """
    集計指定方眼の初期DataFrameを作る。1行目はGT（単純集計）行、以降は設問ごとの行。
    列キーは各設問のID（Q1, Q2…）——短縮設問文は長い/重複しうるため列キーには使わず、
    画面表示ラベルはui側のcolumn_configで別途付ける。「短縮設問文」列は表示専用（グリッドの
    照合はID列で行うため、ここに表示用のformat_suffixを含めても他の処理に影響しない）。
    """
    ids = [q['id'] for q in questions]
    rows = [{'ID': GT_ROW_ID, '短縮設問文': '（単純集計＝GT）', **{qid: False for qid in ids}}]
    for q in questions:
        rows.append({'ID': q['id'], '短縮設問文': question_display_label(q), **{qid: False for qid in ids}})
    return pd.DataFrame(rows, columns=[*_META_COLUMNS, *ids])


def build_cross_rows(grid_df: pd.DataFrame, questions: list[dict]) -> list[dict]:
    """
    方眼のチェック状態から集計指定表（単純集計・クロス集計）のリストを組み立てる。
    GT行でチェックした列は単純集計（attr=None）、通常行×列のチェックはクロス集計
    （属性=行, 対象=列）として追加する。対角セル（自分自身との交差）は除外する
    （SPEC 5.3.1「自分自身との交差は集計対象から除外する」）。グラフ指定は含めない
    （呼び出し側でmerge_graph_choicesにより引き継ぐ）。
    """
    labels = {q['id']: question_label(q) for q in questions}
    ids = list(labels.keys())
    rows_by_id = {row['ID']: row for _, row in grid_df.iterrows()}

    result: list[dict] = []
    gt_row = rows_by_id.get(GT_ROW_ID)
    if gt_row is not None:
        for qid in ids:
            if bool(gt_row.get(qid)):
                result.append({'attr': None, 'target': qid, 'attr_label': 'GT', 'target_label': labels[qid]})

    for qid in ids:
        row = rows_by_id.get(qid)
        if row is None:
            continue
        for col_id in ids:
            if col_id == qid:
                continue
            if bool(row.get(col_id)):
                result.append({
                    'attr': qid, 'target': col_id,
                    'attr_label': labels[qid], 'target_label': labels[col_id],
                })
    return result


def merge_graph_choices(cross_rows: list[dict], previous_rows: list[dict]) -> list[dict]:
    """再構築後もグラフ種別・AIコメントのオンオフ指定を(attr, target)キーで引き継ぐ"""
    previous = {(r['attr'], r['target']): r for r in previous_rows}
    for row in cross_rows:
        prev = previous.get((row['attr'], row['target']))
        row['graph'] = prev.get('graph', '') if prev else ''
        row['ai_comment'] = prev.get('ai_comment', True) if prev else True
    return cross_rows


def validate_triple_cross(specs: list[dict], questions: list[dict]) -> list[str]:
    """
    トリプルクロス指定表の検証。3項目のうちどれか1つでも入力されていれば3つとも入力必須で、
    入力値は既存設問の短縮設問文（未確定なら設問文）と一致していなければならない。
    """
    valid_labels = {question_label(q) for q in questions}
    warnings: list[str] = []
    for i, spec in enumerate(specs, 1):
        values = [spec.get('attr_large', '').strip(), spec.get('attr_mid', '').strip(), spec.get('target', '').strip()]
        if not any(values):
            continue
        if not all(values):
            warnings.append(f'トリプルクロス指定表{i}行目: 3項目とも入力してください。')
            continue
        for label, value in zip(['属性設問（大）', '属性設問（中）', '対象設問'], values):
            if value not in valid_labels:
                warnings.append(f'トリプルクロス指定表{i}行目: {label}「{value}」に一致する設問が見つかりません。')
    return warnings


def validate_cross_table_graphs(cross_rows: list[dict]) -> list[str]:
    """クロス集計指定表のグラフ列が空欄または既定の5種類のいずれかであることを確認する"""
    warnings = []
    for row in cross_rows:
        graph = row.get('graph', '')
        if graph and graph not in GRAPH_CHOICES:
            attr = row['attr_label'] or 'GT'
            warnings.append(
                f"「{attr}×{row['target_label']}」のグラフ指定「{graph}」が不正です"
                f"（{'/'.join(GRAPH_CHOICES)}のいずれか、または空欄にしてください）。"
            )
    return warnings


def grid_checks_from_df(grid_df: pd.DataFrame) -> list[list[str]]:
    """プロジェクトファイル保存用: チェック済みセルだけを[行ID, 列ID]のリストで書き出す"""
    ids = [c for c in grid_df.columns if c not in _META_COLUMNS]
    checks = []
    for _, row in grid_df.iterrows():
        for col_id in ids:
            if bool(row.get(col_id)):
                checks.append([row['ID'], col_id])
    return checks


def apply_grid_checks(grid_df: pd.DataFrame, checks: list[list[str]]) -> pd.DataFrame:
    """プロジェクトファイル読込用: [行ID, 列ID]のリストから該当セルをTrueに戻す"""
    df = grid_df.copy()
    df = df.set_index('ID', drop=False)
    for row_id, col_id in checks:
        if row_id in df.index and col_id in df.columns:
            df.loc[row_id, col_id] = True
    return df.reset_index(drop=True)
