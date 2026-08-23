"""
ingest.py
RAWファイル（CSV/Excel）の読込。文字コード自動判定を行う（Streamlit非依存）。
"""

from __future__ import annotations

import io

import pandas as pd
from charset_normalizer import from_bytes


def read_raw_file(filename: str, data: bytes) -> tuple[pd.DataFrame, str]:
    """
    アップロードされたRAWファイル（CSV/Excel）を読み込み、(DataFrame, 検出した文字コード) を返す。
    全列を文字列として読み込む（自由記述の数字や選択肢コードを勝手に数値化させないため）。
    欠損セルは空文字列にする。
    """
    if filename.lower().endswith(('.xlsx', '.xls')):
        df = pd.read_excel(io.BytesIO(data), dtype=str)
        return df.fillna(''), 'excel'

    encoding = detect_encoding(data)
    df = pd.read_csv(io.BytesIO(data), encoding=encoding, dtype=str)
    return df.fillna(''), encoding


def detect_encoding(data: bytes) -> str:
    """
    CSVバイト列から文字コードを自動判定する。GoogleフォームのCSVエクスポートは
    utf-8-sigが多いが、Excel経由での再保存等でcp932（Shift-JIS系）になっているケースも
    あるため、決め打ちにせずcharset-normalizerで判定する。判定できない場合はutf-8にフォールバック。
    """
    best = from_bytes(data).best()
    if best is None:
        return 'utf-8'
    return best.encoding
