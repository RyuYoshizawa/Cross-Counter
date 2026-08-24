"""
data_export.py
「データ抽出」タブ「設問指定RAWデータ出力」機能（Streamlit非依存）。指定した設問だけを
選び、列見出し（短縮設問文/原文設問文）・選択肢型設問の値（短縮選択肢/原文選択肢）を
設問ごとに切り替えてRAWデータを書き出す。設問定義表の設問文と実際のRAW列見出しは
文言がずれ得るため（SPEC 5.2.3）、必ずmatch_to_raw_columnsで対応付けてからdfを参照する。
"""

from __future__ import annotations

import pandas as pd

from core.question_definition import FORMAT_MA, FORMAT_SA, match_to_raw_columns
from core.text_normalize import normalize_for_comparison

_MULTI_DELIM = ', '


def build_full_raw_dataframe(rows: list[dict], columns: list[str], excluded_row_ids: list[int]) -> pd.DataFrame:
    """
    現在のRAWデータ（st.session_state['raw_rows']）を、加工・選択肢の絞り込み・短縮化を一切
    行わずそのまま書き出す（検証用、ユーザーとの合意事項2026-08-24: 「RAWデータを改変しない」
    方針自体は変えないが、その他自由記述の抽出・RAWデータ補正等で変化していく作業中のRAW
    データの「現状」を人が確認できないと検証時に致命的、という指摘を受けて追加）。
    _row_id・除外対象（テスト回答削除で除外指定されているか）の列を先頭に付け、以降は
    raw_columns（columns引数）の順番そのまま。
    """
    df = pd.DataFrame(rows)
    excluded = set(excluded_row_ids)
    out = pd.DataFrame(index=df.index)
    out['_row_id'] = df['_row_id']
    out['除外対象'] = df['_row_id'].isin(excluded)
    for col in columns:
        out[col] = df[col]
    return out


def build_export_dataframe(df: pd.DataFrame, entries: list[dict], columns: list[str],
                            selections: list[dict], *, add_serial: bool = True) -> pd.DataFrame:
    """
    selections: [{'entry_id': str, 'option_mode': 'short'|'original', 'header_mode': 'short'|'original'}, ...]
    （指定順に列を並べる。対応するentry/RAW列が見つからないものはスキップする。）
    add_serial=Trueなら最左列に1始まりの通し番号列「ID」を追加する（見出しは文字通り「ID」）。
    """
    entry_to_col = {m['entry']['id']: raw_col for raw_col, m in match_to_raw_columns(entries, columns).items()}
    by_id = {e['id']: e for e in entries}

    out = pd.DataFrame(index=df.index)
    if add_serial:
        out.insert(0, 'ID', range(1, len(df) + 1))

    for sel in selections:
        entry = by_id.get(sel['entry_id'])
        if entry is None:
            continue
        raw_col = entry_to_col.get(entry['id'])
        if raw_col is None or raw_col not in df.columns:
            continue

        header_source = entry['short_question'] or entry['question_text'] if sel['header_mode'] == 'short' else entry['question_text']
        header = _dedupe_header(header_source, out.columns)

        if entry['format'] in (FORMAT_SA, FORMAT_MA) and sel['option_mode'] == 'short':
            out[header] = _remap_option_values(df[raw_col], entry, entry['format'] == FORMAT_MA)
        else:
            out[header] = df[raw_col].values

    return out


def _remap_option_values(series: pd.Series, entry: dict, is_multi: bool) -> pd.Series:
    """
    選択肢の原文値を短縮選択肢に置き換える（対応が無い値——その他自由記述等——はそのまま残す）。
    照合はNFKC正規化した上で行う（全角/半角の表記ゆれで置き換え漏れが起きるのを防ぐ、
    core.aggregateの値照合と同じ配慮）。
    """
    mapping = {normalize_for_comparison(o['text']): (o['short'] or o['text']) for o in entry['options']}

    def _remap_one(part: str) -> str:
        return mapping.get(normalize_for_comparison(part), part)

    def _remap(raw) -> str:
        value = str(raw).strip()
        if not value:
            return ''
        if not is_multi:
            return _remap_one(value)
        parts = [p.strip() for p in value.split(_MULTI_DELIM)]
        return _MULTI_DELIM.join(_remap_one(p) for p in parts)

    return series.apply(_remap)


def _dedupe_header(header: str, existing_columns) -> str:
    """同じ設問を複数行で選んだ場合など、列名が重複して既存列を上書きしないようにする"""
    if header not in existing_columns:
        return header
    suffix = 2
    while f'{header}_{suffix}' in existing_columns:
        suffix += 1
    return f'{header}_{suffix}'
