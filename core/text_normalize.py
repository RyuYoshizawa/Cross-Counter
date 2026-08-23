"""
text_normalize.py
アンケートフォームPDFのフォント由来の文字化け（康熙部首ブロック・字体バリアント等）を
補正する正規化ヘルパー。Streamlit非依存。core/form_pdf.py（PDF抽出時点での是正）と
core/aggregate.py（RAWデータとの値照合の都度の是正）の両方から使う——既に作成済みの
（is正されていない）設問定義表でもRAWデータとの照合が正しく通るよう、是正はソース側
だけでなく比較のたびにも行う（多重防御）。

実際のアンケートフォームPDFで確認された実例（2026-08-22、実データ検証）: 「校長」「青森市」
「西津軽郡」「保護者や地域住民」等の一部の常用漢字が、フォントのサブセット化により通常の
CJK統合漢字ではなく「CJK互換漢字部首」ブロック（U+2E80-U+2EF3）の見た目だけ同じ文字に、
また「・」（中点）が「‧」（U+2027 HYPHENATION POINT）に、「戸」が旧字体「戶」（U+6236）に
それぞれ化けるケースが見つかった。これらはNFKC正規化では吸収できない（正規化上は別字扱いの
ため）。全ての康熙部首を網羅的に列挙すると誤ったマッピングを混入させるリスクがあるため、
実データで確認できたものだけを確実に直す方針とする——新しいPDFで別の文字化けが見つかり次第
_CHAR_FIXESに追加していく。
"""

from __future__ import annotations

import unicodedata

_CHAR_FIXES: dict[str, str] = {
    '⻑': '長',  # CJK RADICAL LONG ONE → 長（校長 等）
    '⻘': '青',  # CJK RADICAL BLUE → 青（青森市 等）
    '⻄': '西',  # CJK RADICAL WEST TWO → 西（西津軽郡 等）
    '⺠': '民',  # CJK RADICAL CIVILIAN → 民（住民 等）
    '‧': '・',  # HYPHENATION POINT → 中点（カタカナ中点、「校長・教頭」等の区切り）
    '戶': '戸',  # 戶（旧字体/繁体字）→ 戸（日本語の新字体、「八戸市」等）
}


def fix_known_font_glitches(text: str) -> str:
    """既知のPDFフォント文字化けパターンを補正する（1文字ずつ置換、順序に依存しない）"""
    for broken, fixed in _CHAR_FIXES.items():
        if broken in text:
            text = text.replace(broken, fixed)
    return text


def normalize_for_comparison(text) -> str:
    """比較用の正規化: NFKC正規化＋既知の文字化け補正＋前後の空白除去"""
    text = unicodedata.normalize('NFKC', str(text))
    text = fix_known_font_glitches(text)
    return text.strip()
