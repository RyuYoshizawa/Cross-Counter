"""
other_text.py
Googleフォーム標準「その他」（インライン自由記述）の検出結果を、RAWデータの最右に新しい列
として追加する（SPEC 5.4.1、ユーザーとの合意事項:「特定できた自由回答と回答者属性が
切れないようにどこかにまとめておければよい」）。元のRAW列・元の値は一切書き換えない——
このアプリは元データを常に保持し、除外・分類は別レイヤーで管理する方針
（TEST回答除外と同じ、SPEC 6節）。
"""

from __future__ import annotations

import pandas as pd

from core.aggregate import extract_other_values
from core.question_definition import FORMAT_MA, FORMAT_SA, match_to_raw_columns

OTHER_TEXT_SUFFIX = 'その他自由記述'


def build_other_text_columns(df: pd.DataFrame, entries: list[dict], columns: list[str]) -> dict[str, pd.Series]:
    """
    SA/MA設問ごとに、選択肢一覧に無い値（その他自由記述の可能性がある値）を行ごとに抽出し、
    {新列名: Series（該当なしの行は空文字列、dfと同じ行順）} の形で返す。値が1件も無い設問は
    結果に含めない。呼び出し側がこれをRAWデータの最右に追加する。
    """
    matches = match_to_raw_columns(entries, columns)
    entry_to_col = {m['entry']['id']: col for col, m in matches.items()}

    result: dict[str, pd.Series] = {}
    for entry in entries:
        if entry['format'] not in (FORMAT_SA, FORMAT_MA):
            continue
        col = entry_to_col.get(entry['id'])
        if col is None or col not in df.columns:
            continue
        options = [o['text'] for o in entry['options']]
        is_multi = entry['format'] == FORMAT_MA
        extracted = extract_other_values(df[col], options, is_multi)
        if (extracted != '').any():
            label = entry['short_question'] or entry['question_text']
            title = label if len(label) <= 30 else f'{label[:30]}…'
            result[f'{title}_{OTHER_TEXT_SUFFIX}'] = extracted
    return result
