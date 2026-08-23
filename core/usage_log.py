"""
usage_log.py
LLM API使用量のログ記録（プロジェクトログタブ、ユーザーとの合意事項 2026-08-21）。
1回の作業単位（PDFからの設問定義表初稿作成、AIコメントの一括生成など）ごとに、日時・
モデル・トークン使用量・為替レート・概算コストを1件のログエントリとして記録する。
Streamlit非依存——llm_client.get_token_usage()の呼び出し前後のスナップショット差分から
1エントリを組み立てるだけの薄いヘルパー。

**為替レートについて**: 実際の為替APIとは連携していない。llm_client.calc_cost_jpyは常に
_BAKED_IN_RATE（1USD=150円）でコストを計算するため、まずそのレートでUSD換算前のコストを
逆算し、build_entryに渡されたfx_rate（プロジェクトログタブの入力欄でユーザーが変更可能、
2026-08-22）で改めて円換算する。これにより、実際のAPI呼び出し自体を変更せずに、
記録するレートだけを後から調整できる。過去に記録済みのログエントリは変更しない
（その時点で使っていたレートのまま残る——作業記録として正しい挙動）。
"""

from __future__ import annotations

from datetime import datetime, timezone

DEFAULT_FX_RATE = 150.0
_BAKED_IN_RATE = 150.0  # llm_client.calc_cost_jpyが内部で使う固定レート（USD逆算に使う）


def snapshot(get_token_usage) -> dict:
    """llm_client.get_token_usage()のスナップショットを撮る（build_entry呼び出し前後で使う）"""
    return get_token_usage()


def build_entry(purpose: str, model: str, usage_before: dict, usage_after: dict,
                 fx_rate: float = DEFAULT_FX_RATE) -> dict:
    """
    usage_before/usage_after（get_token_usage()のスナップショット）の差分から1件のログ
    エントリを作る。fx_rateを指定すると、その時点でのUSDコストをそのレートで円換算して記録する
    （既定は150円/USD）。
    """
    cost_jpy_at_baked_rate = usage_after['cost_jpy'] - usage_before['cost_jpy']
    cost_usd = cost_jpy_at_baked_rate / _BAKED_IN_RATE
    return {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'purpose': purpose,
        'model': model,
        'input_tokens': usage_after['input'] - usage_before['input'],
        'output_tokens': usage_after['output'] - usage_before['output'],
        'cache_read_tokens': usage_after['cache_read'] - usage_before['cache_read'],
        'cache_creation_tokens': usage_after['cache_creation'] - usage_before['cache_creation'],
        'cost_jpy': round(cost_usd * fx_rate, 1),
        'fx_rate': fx_rate,
    }
