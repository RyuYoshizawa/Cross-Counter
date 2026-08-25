"""
cleaning.py
文字化けセル・空欄提出・必須未回答の検出。あくまで「候補」を洗い出すだけで、除外の確定は
常にui側（人の確認）で行う——自動削除はしない。
TEST回答の判定はキーワード一致では実用的な精度が出せなかった（選択肢文自体に
「テスト」を含む・アンケートの話題自体が学校の試験に触れる、等の理由で誤検知が
避けられないため——ui/tab_question_definition.pyの経緯コメント参照）。回答フォームのテスト投稿は
通常、正式公開前の動作確認として最初の方の回答に集中する傾向があるため、キーワードで
絞り込まず、先頭付近の行を人がそのまま目視確認する方式にしている。
find_missing_required_candidatesは設問定義表の「必」マークを使うため、RAWデータより先に
設問定義表を作成しておく必要がある（ui/sidebar.pyがRAWアップロード自体をブロックする、
2026-08-25）。
"""

from __future__ import annotations

import re

import pandas as pd

from core.question_definition import match_to_raw_columns

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


def find_blank_response_candidates(df: pd.DataFrame, max_answered: int = 1) -> list[int]:
    """
    実質的に何も回答していない行（空欄提出）を検出する。実データで、タイムスタンプだけ
    記録され他の全設問が空欄のまま提出された回答が複数件見つかった実例がある（2026-08-25）
    ——このような行は分岐の無いあらゆる集計表に同じ「未回答」件数として現れ、原因調査に
    時間がかかった。特定の列（タイムスタンプ等）を決め打ちで除外はしない——フォームによって
    列名が異なり得るため、代わりに「値が入っている列の数」で判定する: max_answered以下の
    列にしか値が無い行を候補とする（既定1件——Googleフォームは通常タイムスタンプ列を自動で
    埋めるため、それ以外に一切回答が無い行を「実質空欄」とみなすには「1列だけ値がある」を
    許容ラインにするのが妥当）。行インデックスのリストを返す（無ければ空リスト）。
    """
    if df.empty:
        return []
    non_blank_counts = df.astype(str).apply(lambda col: col.str.strip() != '').sum(axis=1)
    return df.index[non_blank_counts <= max_answered].tolist()


def find_missing_required_candidates(entries: list[dict], columns: list[str], df: pd.DataFrame) -> dict[int, list[str]]:
    """
    必須設問（各entryのrequiredフィールド、フォームHTML/PDFから読み取った「必」マーク、
    SPEC 5.1.1）に回答していない行を検出する（タブ1「RAWデータ確認」、2026-08-25）。
    「空欄提出候補」（ほぼ全設問が空欄の行）とは別の観点——他の設問には答えているのに、
    特定の必須設問だけ抜けているケースも拾う。設問定義表とRAWデータの列を対応付けたうえで
    （match_to_raw_columns）、必須設問の列が空欄の行を集める。
    戻り値: {行インデックス: [未回答の必須設問ラベル, ...]}（無ければ空辞書）。
    """
    required_entries = [e for e in entries if e.get('required')]
    if not required_entries or not columns:
        return {}
    matches = match_to_raw_columns(entries, columns)
    entry_to_col = {m['entry']['id']: col for col, m in matches.items()}

    result: dict[int, list[str]] = {}
    for entry in required_entries:
        col = entry_to_col.get(entry['id'])
        if col is None or col not in df.columns:
            continue
        label = entry['short_question'] or entry['question_text']
        blank = df[col].astype(str).str.strip() == ''
        for idx in df.index[blank]:
            result.setdefault(idx, []).append(label)
    return result
