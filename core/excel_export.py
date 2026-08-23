"""
excel_export.py
単純集計表（度数・%）をExcelブックとして出力する（openpyxl）。
設問ごとに1シート。グラフのExcel埋め込みは今後の拡張（SPEC 7節）。
"""

from __future__ import annotations

import io
import re

import pandas as pd
from openpyxl import Workbook
from openpyxl.utils.dataframe import dataframe_to_rows


def build_workbook(results: list[dict]) -> Workbook:
    """
    results: [{'column': str, 'base_count': int, 'table': pd.DataFrame}, ...]
    各設問を1シートとして書き出す（設問文・母数を見出しに、その下に度数・%の表）。
    """
    wb = Workbook()
    wb.remove(wb.active)
    used_names: set[str] = set()
    for i, r in enumerate(results):
        sheet = wb.create_sheet(_safe_sheet_name(i, r['column'], used_names))
        sheet['A1'] = r['column']
        sheet['A2'] = f"母数（有効回答数）: {r['base_count']}件"
        for row in dataframe_to_rows(r['table'], index=False, header=True):
            sheet.append(row)
    return wb


def workbook_to_bytes(wb: Workbook) -> bytes:
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _safe_sheet_name(index: int, column: str, used_names: set[str]) -> str:
    """Excelのシート名制約（31文字以内、一部記号禁止、重複不可）に収める"""
    cleaned = re.sub(r'[\\/*?:\[\]]', '', column).strip() or 'Q'
    base = f'Q{index + 1}_{cleaned}'[:31]
    name = base
    suffix = 1
    while name in used_names:
        suffix += 1
        name = f'{base[:31 - len(str(suffix)) - 1]}_{suffix}'
    used_names.add(name)
    return name
