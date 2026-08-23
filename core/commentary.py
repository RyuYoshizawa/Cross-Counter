"""
commentary.py
クロス集計結果について、属性カテゴリ間の回答傾向の違いを指摘する短いAIコメントを生成する
（集計表タブ、ユーザーとの合意事項 2026-08-21：「属性間の差異の指摘」を主眼にする）。
数値計算自体はcore/aggregate.py・core/cross_execute.pyが担い、本モジュールはその結果を
材料にしたプロンプト整形とLLM呼び出しのみを行う。集計指定表の全行を実行時に自動で一括生成する
設計のため、表の数だけAPI呼び出しが発生する——コストを抑えるため既定モデルは低コストの
Haikuとする（ユーザーとの合意事項）。GT行（単純集計、属性による分類が無い）は対象外——
「差異」を語れる比較対象が無いため、is_gt=Falseのクロス集計結果のみに使う。
「集計指定」タブのクロス集計指定表でAIコメント列のチェックを外した行は、そもそも呼び出し側
（ui/tab_crosstab_result.py）が本モジュールを呼ばない。
"""

from __future__ import annotations

import pandas as pd

from llm_client import call_llm

COMMENTARY_MODEL = 'claude-haiku-4-5'
_TOTAL_LABEL = '全体'

_SCHEMA = {
    'type': 'object',
    'properties': {
        'commentary': {
            'type': 'string',
            'description': '属性カテゴリ間で回答傾向にどのような違いがあるかを指摘する日本語のコメント（1〜2文）',
        },
    },
    'required': ['commentary'],
}

# 実際の出力例で「同じ指摘を言い換えて2文目で繰り返す」「誇張表現（圧倒的に、劇的に等）」が
# 冗長との指摘を受け、1〜2文・誇張なし・体言止め可という具体的なスタイルに絞り込んだ
# （ユーザーとの合意事項、2026-08-21）。
_PROMPT_TEMPLATE = """以下はアンケートのクロス集計結果です。「{attr_label}」の回答カテゴリ（行）ごとに、
「{target_label}」への回答がどう分布しているか（％、母数）を示しています。

{table_text}

この表を見て、属性カテゴリの間で回答傾向にどのような違いがあるかを指摘する、簡潔な日本語の
コメントを書いてください。次の点を守ること:
- 1〜2文程度にまとめる（同じ指摘を言葉を変えて繰り返さない。1文で言い切れるなら1文でよい）
- 数値をそのまま読み上げるのではなく、最も目立つ差1つに絞って言及する
- 「圧倒的に」「劇的に」「顕著である」のような大げさな言い回しは避け、「特に」「やや」など
  控えめな表現にする
- 文末は「〜が顕著。」のような体言止め・形容詞止めでよい（無理に「である」調の完全な文で
  終える必要はない）
- 表にない情報を推測して補わないこと
"""


def build_table_text(attr_label: str, target_label: str, pct_df: pd.DataFrame, base: dict[str, int]) -> str:
    """
    LLMに渡すクロス集計結果をMarkdown表のプレーンテキストに整形する。「全体」行・列は
    実際の属性カテゴリ同士の比較対象ではない（単なる合計の参考値）ため、比較の材料としては
    渡さない——含めるとAIが「全体」を1カテゴリのように扱って的外れな比較をすることがある。
    """
    pct_df = pct_df.drop(index=_TOTAL_LABEL, errors='ignore').drop(columns=_TOTAL_LABEL, errors='ignore')
    header = f'| {attr_label} | 母数 | ' + ' | '.join(str(c) for c in pct_df.columns) + ' |'
    sep = '|' + '---|' * (len(pct_df.columns) + 2)
    lines = [header, sep]
    for idx in pct_df.index:
        cells = ' | '.join(f'{pct_df.loc[idx, c]}%' for c in pct_df.columns)
        lines.append(f'| {idx} | {base.get(idx, 0)}件 | {cells} |')
    return '\n'.join(lines)


def generate_cross_commentary(client, result: dict, model: str = COMMENTARY_MODEL) -> str | None:
    """
    run_cross_plan()の1件分の結果（is_gt=Falseのクロス集計）からAIコメントを生成する。
    失敗時（LLM呼び出し失敗）はNoneを返す——呼び出し側はコメント無しで表だけ表示すればよい。
    """
    table_text = build_table_text(result['attr_label'], result['target_label'], result['pct'], result['base'])
    prompt = _PROMPT_TEMPLATE.format(
        attr_label=result['attr_label'], target_label=result['target_label'], table_text=table_text,
    )
    output = call_llm(client, prompt, _SCHEMA, 'Anthropic', model)
    if not output:
        return None
    return output.get('commentary')


# トリプルクロス指定表（SPEC 5.3.2）・一覧型クロス集計指定表（SPEC 5.3.3）にもAIコメントを
# 作成できるようにしてほしいというユーザーの要望を受け、2026-08-23に追加。行ごとのAIコメント
# オンオフ列は今のところ持たない（クロス集計指定表と違い、指定表自体にその列を設けていない
# ため）——確定済みの全行・全グループに対して実行のたびにまとめて生成する。

_TRIPLE_PROMPT_TEMPLATE = """以下はアンケートのクロス集計結果です。「{attr_large_label}」×「{attr_mid_label}」の
組み合わせ（行）ごとに、「{target_label}」への回答がどう分布しているか（％、母数）を示しています。

{table_text}

この表を見て、属性の組み合わせの間で回答傾向にどのような違いがあるかを指摘する、簡潔な日本語の
コメントを書いてください。次の点を守ること:
- 1〜2文程度にまとめる（同じ指摘を言葉を変えて繰り返さない。1文で言い切れるなら1文でよい）
- 数値をそのまま読み上げるのではなく、最も目立つ差1つに絞って言及する
- 「圧倒的に」「劇的に」「顕著である」のような大げさな言い回しは避け、「特に」「やや」など
  控えめな表現にする
- 文末は「〜が顕著。」のような体言止め・形容詞止めでよい（無理に「である」調の完全な文で
  終える必要はない）
- 表にない情報を推測して補わないこと
"""


def build_triple_table_text(attr_large_label: str, attr_mid_label: str, target_label: str,
                             pct_df: pd.DataFrame, base: dict) -> str:
    """トリプルクロス集計結果（MultiIndex行）をMarkdown表のプレーンテキストに整形する"""
    header = f'| {attr_large_label} | {attr_mid_label} | 母数 | ' + ' | '.join(str(c) for c in pct_df.columns) + ' |'
    sep = '|' + '---|' * (len(pct_df.columns) + 3)
    lines = [header, sep]
    for idx in pct_df.index:
        large, mid = idx
        cells = ' | '.join(f'{pct_df.loc[idx, c]}%' for c in pct_df.columns)
        lines.append(f'| {large} | {mid} | {base.get(idx, 0)}件 | {cells} |')
    return '\n'.join(lines)


def generate_triple_cross_commentary(client, result: dict, model: str = COMMENTARY_MODEL) -> str | None:
    """run_triple_cross()の1件分の結果からAIコメントを生成する。失敗時はNoneを返す。"""
    table_text = build_triple_table_text(
        result['attr_large_label'], result['attr_mid_label'], result['target_label'],
        result['pct'], result['base'],
    )
    prompt = _TRIPLE_PROMPT_TEMPLATE.format(
        attr_large_label=result['attr_large_label'], attr_mid_label=result['attr_mid_label'],
        target_label=result['target_label'], table_text=table_text,
    )
    output = call_llm(client, prompt, _SCHEMA, 'Anthropic', model)
    if not output:
        return None
    return output.get('commentary')


_LIST_CROSS_PROMPT_TEMPLATE = """以下はアンケートのクロス集計結果です。対象設問「{target_label}」への回答が、
複数の属性設問（{attr_labels}）のカテゴリごとにどう分布しているか（％、母数）を示しています。

{table_text}

この表を見て、属性設問・カテゴリの間で回答傾向にどのような違いがあるかを指摘する、簡潔な日本語の
コメントを書いてください。次の点を守ること:
- 1〜2文程度にまとめる（同じ指摘を言葉を変えて繰り返さない。1文で言い切れるなら1文でよい）
- 数値をそのまま読み上げるのではなく、最も目立つ差1つに絞って言及する
- 「圧倒的に」「劇的に」「顕著である」のような大げさな言い回しは避け、「特に」「やや」など
  控えめな表現にする
- 文末は「〜が顕著。」のような体言止め・形容詞止めでよい（無理に「である」調の完全な文で
  終える必要はない）
- 表にない情報を推測して補わないこと
"""


def build_list_cross_table_text(group: dict, labels: list[str]) -> str:
    """
    一覧型クロス集計の1グループ（対象設問1つ×複数の属性設問）をMarkdown表のプレーンテキストに
    整形する。「全体」行（属性設問を横断した対象設問全体の分布）は実際のカテゴリ同士の比較
    対象ではないため、他の集計と同じく比較材料には含めない。
    """
    header = '| 属性設問 | カテゴリ | 母数 | ' + ' | '.join(labels) + ' |'
    sep = '|' + '---|' * (len(labels) + 3)
    lines = [header, sep]
    for attr in group['attrs']:
        for cat in attr['categories']:
            cells = ' | '.join(f"{cat['pct'].get(label, 0)}%" for label in labels)
            lines.append(f"| {attr['attr_label']} | {cat['label']} | {cat['base']}件 | {cells} |")
    return '\n'.join(lines)


def generate_list_cross_commentary(client, group: dict, labels: list[str], model: str = COMMENTARY_MODEL) -> str | None:
    """run_list_cross()の1グループ分の結果からAIコメントを生成する。失敗時はNoneを返す。"""
    table_text = build_list_cross_table_text(group, labels)
    attr_labels = '、'.join(a['attr_label'] for a in group['attrs'])
    prompt = _LIST_CROSS_PROMPT_TEMPLATE.format(
        target_label=group['target_label'], attr_labels=attr_labels, table_text=table_text,
    )
    output = call_llm(client, prompt, _SCHEMA, 'Anthropic', model)
    if not output:
        return None
    return output.get('commentary')
