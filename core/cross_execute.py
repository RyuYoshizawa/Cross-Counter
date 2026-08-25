"""
cross_execute.py
確定済みの集計指定表（GT単純集計・2wayクロス集計の指定リスト、トリプルクロス指定表）を
実際のRAWデータに対して一括実行する（SPEC 5.4節）。core/aggregate.pyの計算関数を呼び出す
だけの薄いオーケストレーション層——Streamlit非依存。設問定義表の設問文と実際のRAW列見出しは
文言がずれ得るため（SPEC 5.2.3）、必ずcore/question_definition.match_to_raw_columnsで
対応付けてからdfを参照する（entryのquestion_textをそのまま列名として使わない）。
表・グラフの見出しには短縮選択肢（無ければ原文）を使う（ユーザーとの合意事項、2026-08-21：
「集計表やグラフの表示には短縮選択肢を使ってください」）。

**その他自由記述の自動バケット化（SPEC 5.4.1）**: 対象設問（GT行・クロスの対象側・
トリプルクロスの対象側）の選択肢一覧に無い値を自動的に「その他」としてまとめて集計に含める
かどうかは、各設問定義エントリのother_bucketフィールド（bool、既定True）で決まる——タブ1
「RAWデータ確認」で人が実データを見て一度だけ確定する決定であり、警告文を組み立てるために
RAWデータを再スキャンするようなことはしない（当初はここで毎回find_unmatched_valuesを
再実行し警告文を作っていたが、実際には集計できているのに「一部の集計をスキップしました」
という警告と紛らわしく表示されてしまう問題があり、警告の要否の決定をタブ1側に一本化した、
ユーザーとの合意事項 2026-08-22）。

ただし、other_bucket=True（既定値）でもその設問の実データに未対応の値が1件も無ければ、
「その他」列は常に0件のまま集計表に残り続け、紛らわしい空列になる（実データで発生、
2026-08-25）。other_bucketは「一度でも未対応の値が見つかった設問」だけがタブ1のレビューUIに
出てくる仕組みのため、未対応の値が最初から存在しない設問ではフラグがTrueのまま一切見直されない
——これは前段落の「警告文のための再スキャン」とは別の話で、警告は一切出さず、対象設問の
実データにfind_unmatched_valuesで実在する未対応の値が無ければその他バケット自体を作らない
（列を静かに省く）だけの軽い事前チェックとして_target_other_label内で行う。属性側は対象外
——属性のその他バケット化は現状の要望に含まれないため実装していない。
"""

from __future__ import annotations

import pandas as pd

from core.aggregate import (
    compute_base_count,
    cross_tabulation,
    find_unmatched_values,
    resolve_other_label,
    simple_tabulation_multi,
    simple_tabulation_single,
    triple_cross_tabulation,
)
from core.cross_plan import gridable_questions, question_label
from core.question_definition import FORMAT_MA, match_to_raw_columns

_TOTAL_LABEL = '全体'
_UNANSWERED_LABEL = '未回答'


def _entry_to_raw_column(entries: list[dict], columns: list[str]) -> dict[str, str]:
    """設問定義エントリID→対応するRAW列名の対応表を作る"""
    matches = match_to_raw_columns(entries, columns)
    return {m['entry']['id']: raw_col for raw_col, m in matches.items()}


def _option_texts_and_labels(entry: dict) -> tuple[list[str], list[str]]:
    """設問定義エントリから (原文選択肢のリスト, 表示用ラベル＝短縮選択肢優先のリスト) を返す"""
    options = [o['text'] for o in entry['options']]
    labels = [o['short'] or o['text'] for o in entry['options']]
    return options, labels


def _target_other_label(entry: dict, labels: list[str], series: pd.Series,
                         options: list[str], is_multi: bool) -> str | None:
    """
    entryのother_bucket決定（タブ1で人が確定済み）に従って、使うべきその他ラベルを返す。
    other_bucket=Trueでも、この設問の実データに選択肢一覧と対応しない値が1件も無ければ
    常に0件の紛らわしい列になるため、その場合はNoneを返してバケット自体を作らない
    （find_unmatched_valuesで実在するかどうかだけを見る軽い事前チェック——警告文は出さない、
    ファイル冒頭のdocstring参照）。
    """
    if not entry.get('other_bucket', True):
        return None
    if not find_unmatched_values(series, options, is_multi):
        return None
    return resolve_other_label(labels)


def run_cross_plan(df: pd.DataFrame, entries: list[dict], columns: list[str],
                    cross_rows: list[dict]) -> tuple[list[dict], list[str]]:
    """
    集計指定表（各要素={'attr': id|None, 'target': id, 'attr_label', 'target_label', 'graph',
    'ai_comment'}）を実行する。対応するRAW列が見つからない設問はスキップし、理由をissuesに積む。
    戻り値: (results, issues)。resultsの各要素:
      GT行:   {'is_gt': True, 'attr_id': None, 'target_id', 'attr_label': 'GT', 'target_label',
                'graph', 'ai_comment': bool, 'base': int, 'unanswered': int, 'table': DataFrame}
      クロス行: {'is_gt': False, 'attr_id', 'target_id', 'attr_label', 'target_label', 'graph',
                'ai_comment': bool, 'pct': DataFrame, 'n': DataFrame, 'base': {属性ラベル: int},
                'unanswered': {属性ラベル: int}}
    unanswered（実装済み、2026-08-25）は、対象設問が空欄だった件数——母数（base）には
    含まれない「未回答」の件数を画面・Excelの表に「全体」列/行の直前として追加表示するために
    使う（core.aggregate.cross_tabulationのdocstring参照）。
    """
    entry_to_col = _entry_to_raw_column(entries, columns)
    by_id = {e['id']: e for e in entries}

    results: list[dict] = []
    issues: list[str] = []

    for row in cross_rows:
        target_entry = by_id.get(row['target'])
        target_col = entry_to_col.get(row['target']) if target_entry else None
        if target_entry is None or target_col is None:
            issues.append(f"「{row['target_label']}」に対応するRAW列が見つからないため、集計をスキップしました。")
            continue
        target_options, target_labels = _option_texts_and_labels(target_entry)
        target_is_multi = target_entry['format'] == FORMAT_MA
        target_other = _target_other_label(target_entry, target_labels, df[target_col],
                                            target_options, target_is_multi)

        if row['attr'] is None:
            base = compute_base_count(df[target_col])
            table = (
                simple_tabulation_multi(df[target_col], target_options, target_labels, other_label=target_other)
                if target_is_multi
                else simple_tabulation_single(df[target_col], target_options, target_labels, other_label=target_other)
            )
            results.append({
                'is_gt': True, 'attr_id': None, 'target_id': row['target'],
                'attr_label': 'GT', 'target_label': row['target_label'], 'graph': row.get('graph', ''),
                'ai_comment': row.get('ai_comment', True), 'base': base,
                'unanswered': len(df[target_col]) - base, 'table': table,
            })
            continue

        attr_entry = by_id.get(row['attr'])
        attr_col = entry_to_col.get(row['attr']) if attr_entry else None
        if attr_entry is None or attr_col is None:
            issues.append(f"「{row['attr_label']}」に対応するRAW列が見つからないため、集計をスキップしました。")
            continue
        attr_options, attr_labels = _option_texts_and_labels(attr_entry)
        attr_is_multi = attr_entry['format'] == FORMAT_MA

        cross = cross_tabulation(
            df, attr_col=attr_col, attr_options=attr_options, attr_is_multi=attr_is_multi,
            target_col=target_col, target_options=target_options, target_is_multi=target_is_multi,
            attr_display_labels=attr_labels, target_display_labels=target_labels,
            target_other_label=target_other,
        )
        results.append({
            'is_gt': False, 'attr_id': row['attr'], 'target_id': row['target'],
            'attr_label': row['attr_label'], 'target_label': row['target_label'], 'graph': row.get('graph', ''),
            'ai_comment': row.get('ai_comment', True), 'pct': cross['pct'], 'n': cross['n'], 'base': cross['base'],
            'unanswered': cross['unanswered'],
        })

    return results, issues


def run_triple_cross(df: pd.DataFrame, entries: list[dict], columns: list[str],
                      triple_cross_specs: list[dict]) -> tuple[list[dict], list[str]]:
    """
    トリプルクロス指定表（各要素={'attr_large','attr_mid','target'}、値は短縮設問文/設問文の
    ラベル）を実行する。3項目のいずれかが空の行はスキップする（validate_triple_crossで
    既に確定操作時に警告済みのため、ここでは単に無視する）。
    戻り値: (results, issues)。resultsの各要素:
      {'attr_large_label', 'attr_mid_label', 'target_label', 'pct': DataFrame（MultiIndex行）,
       'n': DataFrame（同形状）, 'base': {(属性大, 属性中): int},
       'unanswered': {(属性大, 属性中): int}}
    """
    questions = gridable_questions(entries)
    by_label = {question_label(q): q for q in questions}
    entry_to_col = _entry_to_raw_column(entries, columns)

    results: list[dict] = []
    issues: list[str] = []

    for i, spec in enumerate(triple_cross_specs, 1):
        large_label = spec.get('attr_large', '').strip()
        mid_label = spec.get('attr_mid', '').strip()
        target_label_in = spec.get('target', '').strip()
        if not (large_label and mid_label and target_label_in):
            continue

        large_entry = by_label.get(large_label)
        mid_entry = by_label.get(mid_label)
        target_entry = by_label.get(target_label_in)
        if not (large_entry and mid_entry and target_entry):
            issues.append(f'トリプルクロス指定表{i}行目: 一致する設問が見つからないため、集計をスキップしました。')
            continue

        large_col = entry_to_col.get(large_entry['id'])
        mid_col = entry_to_col.get(mid_entry['id'])
        target_col = entry_to_col.get(target_entry['id'])
        if not (large_col and mid_col and target_col):
            issues.append(f'トリプルクロス指定表{i}行目: 対応するRAW列が見つからないため、集計をスキップしました。')
            continue

        large_options, large_labels = _option_texts_and_labels(large_entry)
        mid_options, mid_labels = _option_texts_and_labels(mid_entry)
        target_options, target_labels = _option_texts_and_labels(target_entry)
        target_is_multi = target_entry['format'] == FORMAT_MA
        target_other = _target_other_label(target_entry, target_labels, df[target_col],
                                            target_options, target_is_multi)

        cross = triple_cross_tabulation(
            df,
            attr_large_col=large_col, attr_large_options=large_options,
            attr_large_is_multi=large_entry['format'] == FORMAT_MA,
            attr_mid_col=mid_col, attr_mid_options=mid_options,
            attr_mid_is_multi=mid_entry['format'] == FORMAT_MA,
            target_col=target_col, target_options=target_options,
            target_is_multi=target_is_multi,
            attr_large_labels=large_labels, attr_mid_labels=mid_labels, target_labels=target_labels,
            target_other_label=target_other,
        )
        results.append({
            'attr_large_label': large_label, 'attr_mid_label': mid_label, 'target_label': target_label_in,
            'pct': cross['pct'], 'n': cross['n'], 'base': cross['base'], 'unanswered': cross['unanswered'],
        })

    return results, issues


def run_list_cross(df: pd.DataFrame, entries: list[dict], columns: list[str],
                    list_cross_attrs: list[str], list_cross_targets: list[str]) -> tuple[list[dict], list[str]]:
    """
    一覧型クロス集計指定表を実行する（SPEC 5.3.3）。この指定はExcel出力専用——画面の集計表・
    グラフタブには一切反映しない（ユーザーとの合意事項、2026-08-23）。list_cross_attrsは
    表左側になる属性設問のセット（全ての対象設問に共通、指定順の空文字列は無視）、
    list_cross_targetsは表頭になる対象設問のリスト（1つにつき1表）——個々のペアを指定する
    のではなく、「指定した属性設問のセットを、指定した対象設問それぞれと組み合わせる」設計
    （当初は行ごとに属性・対象のペアを指定する設計だったが、見本Excel・ユーザーの実運用に
    合わないと指摘を受け、2026-08-23に修正）。対象設問ごとに、「全体」行は1つだけ（属性設問に
    依存しない、cross_tabulationの「全体」行はどの属性を組んでも同じ値になるため属性リストの
    最初の1件だけ採用）、その下に指定された各属性設問のカテゴリ別分布を並べる——見本Excel
    （一覧型クロス集計表見本.xlsx）で確認した構成に合わせている。列の並べ替え（出現率降順/
    デフォルト順）はExcel書き出し側（core/excel_report.py）の表示時の関心事とし、ここでは
    target_labelsを常に原順（対象設問の選択肢定義順）で返す。
    戻り値: (groups, issues)。groupsの各要素:
      {'target_id', 'target_label', 'target_labels'（原順・その他バケット込み）,
       'overall_pct': {ラベル: float}, 'overall_n': {ラベル: int}, 'overall_base': int,
       'overall_unanswered': int,
       'attrs': [{'attr_id', 'attr_label', 'categories': [
           {'label', 'pct': {ラベル: float}, 'n': {ラベル: int}, 'base': int, 'unanswered': int}
       ]}]}
    unanswered/overall_unanswered（実装済み、2026-08-25）はcross_tabulationのunanswered
    （母数に含まれない未回答の件数）をそのまま引き継いだもの。
    """
    questions = gridable_questions(entries)
    by_label = {question_label(q): q for q in questions}
    entry_to_col = _entry_to_raw_column(entries, columns)

    attrs_in = [a.strip() for a in list_cross_attrs if a.strip()]
    targets_in = [t.strip() for t in list_cross_targets if t.strip()]

    groups: list[dict] = []
    issues: list[str] = []

    if not attrs_in or not targets_in:
        return groups, issues

    for target_label_in in targets_in:
        target_entry = by_label.get(target_label_in)
        target_col = entry_to_col.get(target_entry['id']) if target_entry else None
        if target_entry is None or target_col is None:
            issues.append(f'一覧型クロス集計指定表: 対象設問「{target_label_in}」に対応するRAW列が見つからないため、集計をスキップしました。')
            continue
        target_options, target_labels = _option_texts_and_labels(target_entry)
        target_is_multi = target_entry['format'] == FORMAT_MA
        target_other = _target_other_label(target_entry, target_labels, df[target_col],
                                            target_options, target_is_multi)
        all_target_labels = [*target_labels, target_other] if target_other else list(target_labels)

        attrs: list[dict] = []
        overall_pct: dict[str, float] | None = None
        overall_n: dict[str, int] | None = None
        overall_base = 0
        overall_unanswered = 0

        for attr_label_in in attrs_in:
            attr_entry = by_label.get(attr_label_in)
            attr_col = entry_to_col.get(attr_entry['id']) if attr_entry else None
            if attr_entry is None or attr_col is None:
                issues.append(f'一覧型クロス集計指定表: 属性設問「{attr_label_in}」に対応するRAW列が見つからないため、集計をスキップしました。')
                continue
            attr_options, attr_labels = _option_texts_and_labels(attr_entry)
            attr_is_multi = attr_entry['format'] == FORMAT_MA

            cross = cross_tabulation(
                df, attr_col=attr_col, attr_options=attr_options, attr_is_multi=attr_is_multi,
                target_col=target_col, target_options=target_options, target_is_multi=target_is_multi,
                attr_display_labels=attr_labels, target_display_labels=target_labels,
                target_other_label=target_other,
            )
            if overall_pct is None:
                overall_pct = cross['pct'].loc[_TOTAL_LABEL].drop(_TOTAL_LABEL).to_dict()
                overall_n = cross['n'].loc[_TOTAL_LABEL].drop(_TOTAL_LABEL).to_dict()
                overall_base = cross['base'][_TOTAL_LABEL]
                overall_unanswered = cross['unanswered'][_TOTAL_LABEL]

            # 属性設問そのものが空欄だった回答者の分布（'未回答'カテゴリ）も、実在のカテゴリと
            # 同列に末尾へ追加する（cross_tabulationが既に計算済みの行をそのまま使う、
            # 2026-08-25追加）。
            categories = [
                {
                    'label': cat,
                    'pct': cross['pct'].loc[cat].drop(_TOTAL_LABEL).to_dict(),
                    'n': cross['n'].loc[cat].drop(_TOTAL_LABEL).to_dict(),
                    'base': cross['base'][cat],
                    'unanswered': cross['unanswered'][cat],
                }
                for cat in [*attr_labels, _UNANSWERED_LABEL]
            ]
            attrs.append({'attr_id': attr_entry['id'], 'attr_label': attr_label_in, 'categories': categories})

        if not attrs:
            continue

        groups.append({
            'target_id': target_entry['id'], 'target_label': target_label_in,
            'target_labels': all_target_labels,
            'overall_pct': overall_pct, 'overall_n': overall_n, 'overall_base': overall_base,
            'overall_unanswered': overall_unanswered,
            'attrs': attrs,
        })

    # 同じ属性設問が全ての対象設問ループで見つからず、同一の警告が対象設問の数だけ
    # 繰り返されるのを避けるため、出現順を保ったまま重複を除く。
    deduped_issues = list(dict.fromkeys(issues))
    return groups, deduped_issues
