"""
cleaning.py
文字化けセルの検出。あくまで「候補」を洗い出すだけで、除外の確定は常にui側
（人の確認）で行う——自動削除はしない。
TEST回答の判定はキーワード一致では実用的な精度が出せなかった（選択肢文自体に
「テスト」を含む・アンケートの話題自体が学校の試験に触れる、等の理由で誤検知が
避けられないため——ui/tab_question_definition.pyの経緯コメント参照）。回答フォームのテスト投稿は
通常、正式公開前の動作確認として最初の方の回答に集中する傾向があるため、キーワードで
絞り込まず、先頭付近の行を人がそのまま目視確認する方式にしている。
"""

from __future__ import annotations

import re

import pandas as pd

# デコード失敗時の目印であるUnicode置換文字（U+FFFD）を文字化けの判定基準にする。
# 「?」の連続等も文字化けの兆候になり得るが、自由記述の中で偶然「???」と書かれるなど
# 誤検知の余地が大きいため、確実性の高い置換文字のみを対象にする。
_MOJIBAKE_PATTERN = re.compile('�')


def find_mojibake_candidates(df: pd.DataFrame) -> dict[int, list[str]]:
    """
    文字化け（Unicode置換文字を含む）セルを検出し、{行インデックス: [列名, ...]} を返す。
    """
    result: dict[int, list[str]] = {}
    for col in df.columns:
        hit = df[col].astype(str).str.contains(_MOJIBAKE_PATTERN, regex=True, na=False)
        for idx in df.index[hit]:
            result.setdefault(idx, []).append(col)
    return result
