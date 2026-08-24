"""
raw_correction.py
RAWデータ補正機能（アフターコーディング、指示用ファイル「RAWデータ補正機能.xlsx」）。
FA（自由記述）設問の回答をAIの提案をもとに選択肢化し、既存のSA/MA設問の回答・設問定義表に
統合する。Streamlit非依存（画面はui/tab_question_definition.pyの_render_raw_correction）。

ユーザーとの合意事項（2026-08-24）:
- AI提案は正規化重複除去したユニークな回答テキストにだけ行う（実データでは自由記述が数百件に
  なり得るため。同一テキストの回答者全員に同じ提案を適用する）。
- 既存選択肢への「置き換え」を選んでも、設問定義側の元の選択肢は削除しない（新選択肢名を
  追加登録するのみ——他の未処理の回答者がまだその選択肢を持っている可能性があるため）。
- 「移動元のデータ処理」（残す/削除/選択肢化済を付けて残す）は、実行時にチェックした行だけに
  適用する。
- 「削除」はRAWセルの値を実際に空文字列へ書き換える——このアプリの他機能が守ってきた
  「RAWデータは書き換えない」方針の唯一の例外だが、これは今回のユーザー指示そのものであり、
  人が個別にチェックした上での明示的な操作である点で自動処理とは性質が異なる。

移動先設問の「その他」自動集計（SPEC 5.4.1、other_bucket/resolve_other_label、
core/aggregate.py参照）との整合（2026-08-24、実際の運用で判明）: 選択肢一覧のどれにも
一致しない値は、RAWセル自体は書き換えられずそのまま残り、集計実行時にだけ「その他」として
まとめられる（core/aggregate.pyの計算上の扱いにすぎない）。そのため、この機能が本来の目的
——一旦「その他」に丸められたデータを、新しい選択肢名で再分類する——を果たすには、
移動先設問の回答（⑤）・置き換え対象（⑥）の両方で、この「その他」バケット化を集計と同じ
ロジックで反映する必要がある。呼び出し側（ui/tab_question_definition.py）が
core.aggregate.resolve_other_labelで「その他」バケットの表示名（実在の選択肢と衝突する場合は
「その他（自由記述）」等に別名化——集計結果と食い違わないよう同じ関数をそのまま使う）を
算出し、display_destination_value/compute_destination_preview（target_is_bucket引数）に
渡す構成にしている。
"""

from __future__ import annotations

import difflib

import pandas as pd

from core.text_normalize import normalize_for_comparison
from llm_client import call_llm

_MULTI_DELIM = ', '
_MARK_SUFFIX = '（選択肢化済）'

PROPOSAL_MODEL = 'claude-haiku-4-5'

_PROPOSAL_SCHEMA = {
    'type': 'object',
    'properties': {
        'proposals': {
            'type': 'array',
            'description': '入力の回答と同じ順序・同じ件数で返すこと',
            'items': {
                'type': 'object',
                'properties': {
                    'choice_name': {
                        'type': 'string',
                        'description': 'この自由記述回答を分類する選択肢名（簡潔な名詞句）',
                    },
                },
                'required': ['choice_name'],
            },
        },
    },
    'required': ['proposals'],
}

_PROPOSAL_PROMPT = """以下はアンケートの自由記述回答です。それぞれの回答を分類するための短い選択肢名を考えてください。

【回答（{batch_count}件）】
{batch_text}

【指示】
- 内容が同じ/似た趣旨の回答には同じ選択肢名を付けること（アンケート全体の回答一覧を参考情報として
  別途渡しているので、他のバッチの回答との整合性も考慮すること）。
- 選択肢名は簡潔な名詞句（10文字程度が目安）にすること。
- 回答の件数・順序は入力と完全に一致させて返すこと（追加・削除・並べ替えをしないこと）。
"""

# 1回のLLM呼び出しに全ユニーク回答をまとめて渡すと、実データでは数百件規模になり得るため
# max_tokens上限（llm_client.py、8192）に達する恐れがある。core/form_html.pyの
# propose_short_labelsと同じ理由でバッチ分割する——バッチサイズは経験的検証をしていない
# 初期値（短い自由記述1件あたりの出力量から見積もった保守的な値）。
_PROPOSAL_BATCH_SIZE = 30


def fa_source_entries(entries: list[dict]) -> list[dict]:
    """移動元候補（FA設問のみ）を抽出する（①）"""
    return [e for e in entries if e['format'] == 'FA']


def build_source_rows(df: pd.DataFrame, source_col: str) -> list[dict]:
    """
    移動元設問の非空回答を回答者ごとに返す（②、空欄を除く）。
    戻り値: [{'row_id': int, 'answer': str}, ...]（dfの行順、row_idは'_row_id'列の値）。
    """
    rows: list[dict] = []
    for row_id, value in zip(df['_row_id'], df[source_col]):
        text = str(value or '').strip()
        if text:
            rows.append({'row_id': int(row_id), 'answer': text})
    return rows


def unique_answers_for_proposal(source_rows: list[dict]) -> list[str]:
    """
    回答テキストを正規化して重複除去する（AIへの入力を減らすため）。各グループの最初の
    原文を代表テキストとして返す（出現順）。
    """
    seen: dict[str, str] = {}
    order: list[str] = []
    for row in source_rows:
        answer = row['answer']
        key = normalize_for_comparison(answer)
        if key not in seen:
            seen[key] = answer
            order.append(key)
    return [seen[key] for key in order]


def propose_choice_names(client, unique_texts: list[str], model: str = PROPOSAL_MODEL,
                          batch_size: int = _PROPOSAL_BATCH_SIZE) -> dict[str, str]:
    """
    ユニークな自由記述回答一つひとつに、AIが選択肢名を提案する。バッチ分割して呼び出し、
    systemに全ユニーク回答一覧を渡す（cache_system=True、バッチをまたいだ分類の一貫性の
    参考情報にする——2026-08-22の短縮ラベル機能で未解決のまま残っていた「バッチをまたぐと
    一貫性が落ちる」懸念への対策）。失敗したバッチの回答は結果に含めない（呼び出し側は
    空欄のまま人が埋められる）。
    戻り値: {回答原文: 選択肢名}（提案できたものだけ）。
    """
    if not unique_texts:
        return {}

    system = (
        f'以下はアンケートの自由記述回答の全件（重複除去済み、{len(unique_texts)}件）です。'
        '選択肢名を考える際、内容が同じ/似た趣旨の回答には（バッチをまたいでも）同じ選択肢名を'
        '付け、分類の一貫性を保つための参考にしてください。\n\n'
        + '\n'.join(f'- {t}' for t in unique_texts)
    )

    result: dict[str, str] = {}
    for start in range(0, len(unique_texts), batch_size):
        batch = unique_texts[start:start + batch_size]
        batch_text = '\n'.join(f'{i}. {t}' for i, t in enumerate(batch, 1))
        prompt = _PROPOSAL_PROMPT.format(batch_count=len(batch), batch_text=batch_text)
        output = call_llm(client, prompt, _PROPOSAL_SCHEMA, 'Anthropic', model,
                           system=system, cache_system=True)
        if not output:
            continue
        proposals = output.get('proposals', [])
        if len(proposals) != len(batch):
            continue
        for text, item in zip(batch, proposals):
            name = str((item or {}).get('choice_name') or '').strip()
            if name:
                result[text] = name
    return result


def display_destination_value(current_value, defined_options: list[str], is_multi: bool,
                                other_bucket_label: str | None) -> str:
    """
    移動先設問の回答セルを、集計時の「その他」自動集計（other_bucket、core.aggregate参照）と
    同じ見え方で表示する（⑤）。選択肢一覧のどれにも（正規化して）一致しない値をother_bucket_label
    （resolve_other_labelで算出、Noneならバケット化しない設問）に置き換えて見せる——RAWセルの
    実体は書き換えない、あくまで画面表示用の変換。実データで判明した実害への対応（2026-08-24）:
    Googleフォーム標準の「その他」インライン自由記述のように、集計では「その他」としてまとめて
    扱われる値が、この画面では未加工の自由記述テキストのまま表示され、再分類したい「その他」の
    行が見分けられなかった。
    """
    current = str(current_value or '').strip()
    if not current or not other_bucket_label:
        return current
    defined_norms = {normalize_for_comparison(o) for o in defined_options}
    if not is_multi:
        return other_bucket_label if normalize_for_comparison(current) not in defined_norms else current

    parts = [p.strip() for p in current.split(_MULTI_DELIM)]
    out_parts: list[str] = []
    bucket_added = False
    for part in parts:
        if normalize_for_comparison(part) in defined_norms:
            out_parts.append(part)
        elif not bucket_added:
            out_parts.append(other_bucket_label)
            bucket_added = True
        # 2つ目以降の未定義部分は同じバケット表示にまとめる（表示上重複させない）
    return _MULTI_DELIM.join(out_parts)


def compute_destination_preview(current_value, new_name: str, is_multi: bool,
                                 replace_target: str | None, *,
                                 target_is_bucket: bool = False,
                                 defined_options: list[str] | None = None) -> str:
    """
    移動先設問の回答セルに新選択肢名を反映した結果を計算する（純粋関数、⑦）。
    SA: replace_targetの有無・一致に関わらず、常にnew_nameで上書きする（単一選択のセルは
    値を1つしか持てないため、「置き換え」も「新設」も結果は同じになる）。
    MA: target_is_bucket=Trueの場合（⑥で「その他」バケット自体を置き換え対象に選んだ場合）、
    定義済み選択肢のどれにも一致しない要素（＝集計時に「その他」としてまとめられる要素、
    display_destination_valueと同じ判定）を1件だけnew_nameに置き換える（未定義要素が無ければ
    末尾に追加）。target_is_bucket=Falseの場合は従来通り、replace_targetが指定され、かつ現在値の
    中に（正規化して）一致する要素があれば、その要素だけをnew_nameに置き換える。一致しない場合・
    replace_target未指定（新設）の場合は、既に同じ値が無ければnew_nameを末尾に追加する。
    """
    new_name = str(new_name or '').strip()
    if not is_multi:
        return new_name

    current = str(current_value or '').strip()
    parts = [p.strip() for p in current.split(_MULTI_DELIM)] if current else []

    if target_is_bucket:
        defined_norms = {normalize_for_comparison(o) for o in (defined_options or [])}
        for i, part in enumerate(parts):
            if normalize_for_comparison(part) not in defined_norms:
                parts[i] = new_name
                break
        else:
            if new_name not in parts:
                parts.append(new_name)
    elif replace_target:
        norm_target = normalize_for_comparison(replace_target)
        for i, part in enumerate(parts):
            if normalize_for_comparison(part) == norm_target:
                parts[i] = new_name
                break
        else:
            if new_name not in parts:
                parts.append(new_name)
    elif new_name not in parts:
        parts.append(new_name)

    return _MULTI_DELIM.join(parts)


def detect_similar_option(new_name: str, existing_options: list[str], threshold: float = 0.8) -> str | None:
    """
    新選択肢名案が既存の移動先選択肢と完全一致・類似していないか確認する（⑧）。
    無ければNoneを返す。しきい値は経験的検証をしていない初期値。
    """
    new_name = str(new_name or '').strip()
    if not new_name:
        return None
    norm_new = normalize_for_comparison(new_name)
    for opt in existing_options:
        norm_opt = normalize_for_comparison(opt)
        if norm_new == norm_opt:
            return f'既存の選択肢「{opt}」と同じ名前です。'
        score = difflib.SequenceMatcher(None, norm_new, norm_opt).ratio()
        if score >= threshold:
            return f'既存の選択肢「{opt}」と類似しています（類似度{score:.0%}）。'
    return None


def apply_correction(rows: list[dict], dest_col: str, source_col: str,
                      row_new_names: dict[int, str], is_multi: bool,
                      replace_target: str | None, source_handling: str, *,
                      target_is_bucket: bool = False,
                      defined_options: list[str] | None = None) -> list[str]:
    """
    チェック済み行の内容でRAWデータ（rows、st.session_state['raw_rows']と同じリスト）を
    その場で書き換える（other_text.pyと同じ「セッション状態のリストを直接書き換える」方式）。
    row_new_names: {回答ID(_row_id): 採用する新選択肢名}（チェック済み・新選択肢名が空でない
    行だけを呼び出し側で絞り込んで渡すこと）。
    source_handling: 'keep'（残す）/ 'delete'（削除）/ 'mark'（選択肢化済を付けて残す）。
    target_is_bucket/defined_options: compute_destination_previewに同じ意味でそのまま渡す
    （⑥で「その他」バケット自体を置き換え対象に選んだ場合）。
    戻り値: 実際に登録された新選択肢名（重複除去・出現順）——呼び出し側がcore.question_definition
    のadd_option_to_entryで設問定義に追加する。
    """
    registered: list[str] = []
    seen: set[str] = set()

    for row in rows:
        row_id = row.get('_row_id')
        new_name = row_new_names.get(row_id)
        if not new_name:
            continue

        current = row.get(dest_col, '')
        row[dest_col] = compute_destination_preview(
            current, new_name, is_multi, replace_target,
            target_is_bucket=target_is_bucket, defined_options=defined_options,
        )
        if new_name not in seen:
            seen.add(new_name)
            registered.append(new_name)

        if source_handling == 'delete':
            row[source_col] = ''
        elif source_handling == 'mark':
            original = str(row.get(source_col, '') or '')
            if original and not original.endswith(_MARK_SUFFIX):
                row[source_col] = original + _MARK_SUFFIX
        # 'keep' はそのまま何もしない

    return registered
