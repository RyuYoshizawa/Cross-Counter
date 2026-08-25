"""
question_definition.py
設問定義表のデータモデル（Streamlit非依存）。core/form_pdf.pyが作るLLM初稿にID・matrix候補を
付与し、ui/tab_question_definition.pyが表示する縦持ちの一覧表（1行1設問ヘッダー or 1選択肢）
との相互変換を行う。match_to_raw_columns/build_consistency_issuesは、設問定義表とRAWデータの
列見出しを突き合わせる（SPEC 5.2.3）。find_unmatched_value_entriesは列の中身（値そのもの）を
突き合わせ、その他自動バケット化（各entryのother_bucketフィールド、SPEC 5.4.1）のレビューに使う。
各entryのcondition_entry_id/condition_valuesは、分岐（スキップロジック）のある設問向けの
前提条件（SPEC 5.4.3、この設問を対象設問として集計する場合のみ効く——属性として使う場合は
他のバケット化系フラグ同様に対象外）。設定・編集はui/tab_question_definition.pyが行い、
実際にRAWデータへ適用するのはcore/cross_execute.py。
"""

from __future__ import annotations

import difflib
import re

import pandas as pd

from core.aggregate import find_unmatched_values
from core.text_normalize import fix_known_font_glitches

_FUZZY_MATCH_THRESHOLD = 0.85

FORMAT_SA = 'SA'
FORMAT_MA = 'MA'
FORMAT_FA = 'FA'
FORMAT_CHOICES = [FORMAT_SA, FORMAT_MA, FORMAT_FA]

# Googleフォーム標準の「その他」インライン自由記述（ラジオボタン＋自由記述欄が一体になった
# UI）は、フォームの編集画面（HTML方式）・印刷/PDF（PDF方式）のどちらから抽出しても、
# その選択肢のラベルは必ずこの文言になる（HTML方式はDOM構造上その他の実テキストを持たない
# 入力欄要素のため、PDF方式はプロンプトで原文のまま残すよう指示しているため——SPEC 5.4.1・
# core/form_pdf.pyのプロンプト参照）。回答者がここに入力した自由記述は、あたかも他の選択肢と
# 同じようにRAWセルへ記録されるが、Googleフォームが「その他:」という文言そのものを回答として
# 記録することは無い。そのため、この文言をそのまま選択肢一覧に残すと、常に回答数0の
# 見せかけの選択肢が集計表に出続けてしまう実害があった（2026-08-23、ユーザーがフォームの
# 実物のスクリーンショットから根本原因を特定）。
_NATIVE_OTHER_OPTION_TEXTS = ('その他:', 'その他：')

REVIEW_COLUMNS = ['ID', '必', '形式', '短縮設問文', '短縮選択肢', '設問文', '選択肢', 'matrix', 'n変化']
_REQUIRED_MARK = '※'


def count_invalid_questions(llm_questions: list) -> int:
    """
    llm_questionsのうち、辞書形式でない要素（LLMの出力がスキーマ通りでなかったもの）の数を返す。
    build_entriesを呼ぶ前にUI側で件数を見せて警告するために使う。
    """
    return sum(1 for q in llm_questions if not isinstance(q, dict))


def build_entries(llm_questions: list[dict]) -> list[dict]:
    """
    LLMの初稿にID（Q1, Q2, …連番）とmatrix候補（同じ選択肢セットが連続する場合）を付与する。
    llm_questionsの要素・選択肢要素が辞書でない場合（LLMの出力がスキーマ通りでなかった場合、
    実データで確認済み: 配列要素が文字列で返るケースがある）はスキップする——1件の異常データで
    設問定義表全体の作成が失敗するのを避けるため。件数はcount_invalid_questionsで確認できる。
    """
    entries = []
    for q in llm_questions:
        if not isinstance(q, dict):
            continue
        options = [
            {'text': o.get('text', ''), 'short': o.get('short', '')}
            for o in q.get('options', []) if isinstance(o, dict)
        ]
        # Googleフォーム標準の「その他」インライン自由記述の選択肢（常に回答数0の見せかけの
        # 選択肢になる、上記_NATIVE_OTHER_OPTION_TEXTS参照）はここで取り除き、has_native_other
        # フラグを立てる——実際の自由記述の取り込み・「その他」への自動集計そのものは既存の
        # other_text.py/other_bucket機構がそのまま担う（この設問固有の処理ではなく、この設問に
        # 「その他」自動集計を必ず適用する、という決定を表すだけのフラグ）。
        has_native_other = any(o['text'].strip() in _NATIVE_OTHER_OPTION_TEXTS for o in options)
        if has_native_other:
            options = [o for o in options if o['text'].strip() not in _NATIVE_OTHER_OPTION_TEXTS]
        # other_bucketは「対象設問として集計するとき、選択肢一覧に無い値を自動的に
        # 「その他」にまとめるか」の設問ごとの決定（SPEC 5.4.1）。既定はTrue（今まで自動で
        # 有効化していた挙動と同じ）——見つかった値が無ければ効果は無いので、SA/MA以外にも
        # 一律付けておいて害はない。人がタブ1の整合性チェックで個別にOFFにできる
        # （has_native_other=Trueの設問は、判断の余地が無いためこのレビュー対象から除外する
        # ——find_unmatched_value_entries参照）。
        entries.append({
            'id': f'Q{len(entries) + 1}',
            'format': q.get('format') or FORMAT_FA,
            'question_text': q.get('question_text', ''),
            'short_question': q.get('short_question', ''),
            'options': options,
            'n_note': q.get('n_note', ''),
            'matrix': '',
            'other_bucket': True,
            'has_native_other': has_native_other,
            'required': bool(q.get('required', False)),
            'condition_entry_id': None,
            'condition_values': [],
        })
    _assign_matrix_groups(entries)
    return entries


_Q_ID_PATTERN = re.compile(r'^Q\d+$')


def add_manual_entry(entries: list[dict], question_text: str, format: str, short_question: str,
                      option_texts: list[str], at_start: bool = False, index: int | None = None,
                      entry_id: str | None = None) -> list[dict]:
    """
    設問定義表に手動で1行追加する（例: フォームには無いがRAWデータには必ず入る「タイムスタンプ」
    列など、フォームPDFからは作れない設問定義を補うため）。設問文はRAW列見出しと一致させると
    match_to_raw_columnsで自動対応付けされる。追加後はmatrix候補を再計算する（挿入位置によって
    連番・連続性が変わるため）。挿入位置はindex（0起点、範囲外は末尾側にクランプ）優先、指定が
    無ければat_start（先頭/末尾）に従う。
    IDは既定では他の設問と同じQ1/Q2/…の通し番号を振り直すが、entry_idを指定するとその固定IDを
    使い、以後の呼び出しでも（Q番号のような通し番号の振り直しから除外して）そのまま保持する
    ——タイムスタンプ列のように、毎回同じ固定IDで参照したい設問向け。
    """
    new_entry = {
        'id': entry_id or '',
        'format': format,
        'question_text': question_text,
        'short_question': short_question,
        'options': [{'text': t, 'short': ''} for t in option_texts],
        'n_note': '',
        'matrix': '',
        'other_bucket': True,
        'has_native_other': False,
        'required': False,
        'condition_entry_id': None,
        'condition_values': [],
    }
    if index is not None:
        position = max(0, min(index, len(entries)))
        updated = [*entries[:position], new_entry, *entries[position:]]
    else:
        updated = [new_entry, *entries] if at_start else [*entries, new_entry]
    _renumber_entries(updated)
    _assign_matrix_groups(updated)
    return updated


def _renumber_entries(entries: list[dict]) -> None:
    """
    Q1/Q2/…の通し番号を振り直す。固定ID（Qn形式でない非空のID、例: 'time'）を持つ設問は
    通し番号の対象から除外し、位置そのまま・IDそのままにする（add_manual_entryのentry_id参照）。
    """
    i = 1
    for entry in entries:
        entry['matrix'] = ''
        if entry['id'] and not _Q_ID_PATTERN.match(entry['id']):
            continue
        entry['id'] = f'Q{i}'
        i += 1


def _assign_matrix_groups(entries: list[dict]) -> None:
    """
    選択肢のテキスト集合が完全一致する設問が2つ以上連続する場合、マトリクス集計候補として
    セットごとに m{グループ番号}-{セット内連番} を付与する（entriesを直接書き換える）。
    """
    group_no = 0
    i = 0
    n = len(entries)
    while i < n:
        opts = tuple(o['text'] for o in entries[i]['options'])
        if not opts:
            i += 1
            continue
        j = i
        while j < n and tuple(o['text'] for o in entries[j]['options']) == opts:
            j += 1
        if j - i >= 2:
            group_no += 1
            for k, idx in enumerate(range(i, j), 1):
                entries[idx]['matrix'] = f'm{group_no}-{k}'
        i = j


def to_review_dataframe(entries: list[dict]) -> pd.DataFrame:
    """設問定義表画面用の縦持ちDataFrameを組み立てる（1行1設問ヘッダー or 1選択肢）"""
    rows = []
    for entry in entries:
        rows.append({
            'ID': entry['id'], '必': _REQUIRED_MARK if entry.get('required') else '',
            '形式': entry['format'],
            '短縮設問文': entry['short_question'], '短縮選択肢': '',
            '設問文': entry['question_text'], '選択肢': '',
            'matrix': entry['matrix'], 'n変化': entry['n_note'],
        })
        for opt in entry['options']:
            rows.append({
                'ID': '', '必': '', '形式': '', '短縮設問文': '', '短縮選択肢': opt['short'],
                '設問文': '', '選択肢': opt['text'], 'matrix': '', 'n変化': '',
            })
    # pandas 3.xの既定の文字列dtype（str）だと、st.data_editorでの表示時に空文字列が
    # "None"と表示されてしまう（Arrow変換周りの相性問題）。従来のobject dtypeに戻すことで
    # 空文字列がそのまま空欄として表示されるようにする。
    return pd.DataFrame(rows, columns=REVIEW_COLUMNS).astype(object)


def apply_review_edits(edited_df: pd.DataFrame, entries: list[dict]) -> tuple[list[dict], list[str]]:
    """
    編集後のDataFrameをentriesに反映する。ID列で設問ヘッダー行を判別し、それ以外の行は
    直前のヘッダーに属する選択肢として順番に対応付ける（設問文・選択肢の原文自体は
    変更不可のためentries側の値をそのまま保つ）。
    形式はSA/MA/FA（大文字小文字は問わない）以外が入力された場合は変更を無視し、警告として
    返す（戻り値の2つ目）。st.column_config.SelectboxColumnは同じ表の他の空欄セルが
    "None"と表示されてしまう既知の不具合があるため使わず、自由入力＋検証で対応している。
    """
    entries_by_id = {e['id']: e for e in entries}
    warnings: list[str] = []
    current_entry = None
    option_index = 0
    for _, row in edited_df.iterrows():
        rid = str(row.get('ID') or '').strip()
        if rid:
            current_entry = entries_by_id.get(rid)
            option_index = 0
            if current_entry is None:
                continue
            new_format = str(row.get('形式') or '').strip().upper()
            if new_format in FORMAT_CHOICES:
                current_entry['format'] = new_format
            elif new_format and new_format != current_entry['format']:
                warnings.append(f'{rid}: 不明な形式「{new_format}」は無視しました（SA/MA/FAのいずれかを入力してください）')
            current_entry['short_question'] = str(row.get('短縮設問文') or '').strip()
            current_entry['matrix'] = str(row.get('matrix') or '').strip()
            current_entry['n_note'] = str(row.get('n変化') or '').strip()
        else:
            if current_entry is None or option_index >= len(current_entry['options']):
                continue
            current_entry['options'][option_index]['short'] = str(row.get('短縮選択肢') or '').strip()
            option_index += 1
    return list(entries_by_id.values()), warnings


def _normalize_question_text(text: str) -> str:
    """
    比較用に正規化する: 先頭の設問番号（PDF上の「12。」等）・末尾の必須マーク「*」を除去、
    改行・全角空白を半角スペースに統一、前後の空白を除去し、既知のPDFフォント文字化け
    （core.text_normalize）も補正する。
    """
    text = fix_known_font_glitches(text)
    text = text.replace('　', ' ').replace('\r\n', '\n')
    text = re.sub(r'\s+', ' ', text).strip()
    text = re.sub(r'^\d+[。.．]\s*', '', text)
    return text.rstrip('*').strip()


def match_to_raw_columns(entries: list[dict], columns: list[str]) -> dict[str, dict]:
    """
    設問定義表の設問文とRAW列見出しを突き合わせる。完全一致を優先し、無ければ文字列類似度
    によるあいまい一致（閾値0.85）を試みる。低確信度では対応なしのままにする（誤った対応付けで
    不整合を見逃すより、人に判断してもらう方が安全）。
    戻り値: {列名: {'entry': entry, 'method': 'exact'|'fuzzy', 'score': float}}

    同じ文言の設問が複数ある場合（例:「その他」を選択された方は、その内容を具体的にお書き
    ください、という定型文の追従質問が複数の設問に付く）、RAWデータ側もGoogleフォーム/Excelの
    仕様で列名に連番が付き文言が微妙にずれるため完全一致にはならないことが多いが、
    RAW列・設問定義とも元のフォームの並び順を保持しているため、あいまい一致を「使用済みの
    設問は候補から除外しながら列の並び順に処理する」ことで、同点スコアでも出現順に対応付けられ、
    結果として正しいペアに揃う（列の突き合わせ自体に隣接関係の特別ロジックは不要）。
    """
    normalized_columns = {col: _normalize_question_text(col) for col in columns}
    normalized_entries = [(entry, _normalize_question_text(entry['question_text'])) for entry in entries]

    result: dict[str, dict] = {}
    used_entry_ids: set[str] = set()
    remaining_columns: list[str] = []

    for col in columns:
        norm = normalized_columns[col]
        match = next((e for e, n in normalized_entries if n == norm and e['id'] not in used_entry_ids), None)
        if match is not None:
            result[col] = {'entry': match, 'method': 'exact', 'score': 1.0}
            used_entry_ids.add(match['id'])
        else:
            remaining_columns.append(col)

    for col in remaining_columns:
        norm = normalized_columns[col]
        best_entry, best_score = None, 0.0
        for entry, entry_norm in normalized_entries:
            if entry['id'] in used_entry_ids:
                continue
            score = difflib.SequenceMatcher(None, norm, entry_norm).ratio()
            if score > best_score:
                best_entry, best_score = entry, score
        if best_entry is not None and best_score >= _FUZZY_MATCH_THRESHOLD:
            result[col] = {'entry': best_entry, 'method': 'fuzzy', 'score': best_score}
            used_entry_ids.add(best_entry['id'])

    return result


def build_consistency_issues(entries: list[dict], columns: list[str]) -> list[str]:
    """
    設問定義表とRAW列の対応関係（列そのものの過不足）を確認し、問題点を箇条書きテキストの
    リストで返す（問題が無ければ空リスト）。選択肢一覧とRAWデータの実際の値の突き合わせ
    （値レベルの検証）はfind_unmatched_value_entriesに分離した——単なる警告文ではなく、
    設問ごとに「その他」自動バケット化のオンオフを人が決める操作を伴うため（SPEC 5.4.1、
    2026-08-22）。
    """
    if not entries or not columns:
        return []

    matches = match_to_raw_columns(entries, columns)
    matched_ids = {m['entry']['id'] for m in matches.values()}

    issues: list[str] = []
    for col in columns:
        if col not in matches:
            title = col if len(col) <= 60 else f'{col[:60]}…'
            issues.append(f'RAWデータの列「{title}」に対応する設問定義が見つかりません。')
    for entry in entries:
        if entry['id'] not in matched_ids:
            label = entry['short_question'] or entry['question_text']
            title = label if len(label) <= 40 else f'{label[:40]}…'
            issues.append(f'設問定義「{entry["id"]} {title}」に対応するRAW列が見つかりません。')
    return issues


def find_unmatched_value_entries(entries: list[dict], columns: list[str], df: pd.DataFrame) -> list[dict]:
    """
    SA/MA設問のうち、選択肢一覧のどれにも一致しないRAW値がある設問を検出する（実データで
    判明した実例、2026-08-21：設問定義の選択肢テキストとRAWデータのセル値が、全角/半角の
    違いやスキップロジックの注記混入等でずれ、集計結果が不正確になる——完全一致の照合だけでは
    気付けない不整合を機械的に検出する）。
    戻り値: [{'entry': entry, 'unmatched': [値のリスト]}, ...]（無ければ空リスト）。
    「対象設問として集計する場合に自動的に「その他」としてまとめるかどうか」は各entryの
    other_bucketフィールドに人が確定した値として持つ（このリスト自体はその決定には関与しない、
    確認・レビュー用の検出結果）。has_native_other=Trueの設問（Googleフォーム標準の「その他」
    インライン自由記述と判明済み、SPEC 5.4.1）は、RAWデータアップロード時点で既に自動処理
    済み（曖昧な判断の余地が無い）ため、ここでのレビュー対象からは除外する（2026-08-23）。
    """
    if not entries or not columns:
        return []
    matches = match_to_raw_columns(entries, columns)
    entry_to_col = {m['entry']['id']: col for col, m in matches.items()}

    result: list[dict] = []
    for entry in entries:
        if entry['format'] not in (FORMAT_SA, FORMAT_MA):
            continue
        if entry.get('has_native_other'):
            continue
        col = entry_to_col.get(entry['id'])
        if col is None or col not in df.columns:
            continue
        options = [o['text'] for o in entry['options']]
        unmatched = find_unmatched_values(df[col], options, entry['format'] == FORMAT_MA)
        if unmatched:
            result.append({'entry': entry, 'unmatched': unmatched})
    return result
