"""
aggregate.py
単純集計・2way/3wayクロス集計の計算（Streamlit非依存）。母数（％計算の分母）算出ロジック含む。

**値の照合は必ず正規化してから行う**（`_normalize_value`、実体はcore.text_normalize）。
設問定義表の選択肢テキストはアンケートフォームPDFからLLMが抽出したもの、RAWデータの実際の
セル値はGoogleフォームのCSV/Excelエクスポートそのものであり、次の2種類の表記ゆれで
食い違うことが実データで確認されている（2026-08-21・08-22、実際の集計結果が不正確に
なっていた実例）:
1. 全角/半角の違い（例:「一般教員(校長・教頭以外)」＝半角括弧 vs RAWデータ側の
   「一般教員（校長・教頭以外）」＝全角括弧）——NFKC正規化で吸収する。
2. PDFフォント由来の文字化け（例: 校長の「長」が康熙部首の見た目だけ同じ文字になる）——
   NFKC正規化では吸収できないため、core.text_normalize.fix_known_font_glitchesで補正する。
単純な完全一致（==）で照合すると、該当する選択肢の回答者が丸ごと集計から漏れる
（母数はcompute_base_countで別途カウントするため0にはならないが、どの選択肢にもカウント
されない「行方不明」状態になる）。
"""

from __future__ import annotations

import pandas as pd

from core.text_normalize import normalize_for_comparison

_MULTI_DELIM = ', '
_TOTAL_LABEL = '全体'
_UNANSWERED_LABEL = '未回答'


def _normalize_value(text) -> str:
    """選択肢テキストとRAWセル値の比較用正規化（core.text_normalize参照）。表示には使わない。"""
    return normalize_for_comparison(text)


def resolve_other_label(existing_labels: list[str], base: str = 'その他') -> str:
    """
    自動その他バケット化（SPEC 5.4.1）で使う行/列名を決める。設問の選択肢一覧に既に
    「その他」という表示名の選択肢が実在する場合（設計上のその他選択肢と、検出した
    その他自由記述の集計用バケットが同名衝突する）、DataFrameの列名が重複してArrow変換が
    落ちる実害があったため（実データで発生、2026-08-22）、区別できる別名にずらす。
    """
    if base not in existing_labels:
        return base
    candidate = f'{base}（自由記述）'
    suffix = 2
    while candidate in existing_labels:
        candidate = f'{base}（自由記述{suffix}）'
        suffix += 1
    return candidate


def series_matches_any(series: pd.Series, accepted: list[str], is_multi: bool) -> pd.Series:
    """
    設問の前提条件（分岐条件、SPEC 5.4.3）用: seriesの各値が、accepted（前提設問の選択肢
    テキスト）のいずれかに（正規化しても）一致するかをbool Seriesで返す。is_multi=Trueなら
    値をMULTI_DELIMで分割し、いずれかの要素が一致すればTrueとする（前提設問がMAの場合）。
    """
    norm_accepted = {_normalize_value(a) for a in accepted}

    def _match(raw) -> bool:
        value = str(raw).strip()
        if not value:
            return False
        parts = [p.strip() for p in value.split(_MULTI_DELIM)] if is_multi else [value]
        return any(_normalize_value(p) in norm_accepted for p in parts if p)

    return series.apply(_match)


def compute_base_count(series: pd.Series) -> int:
    """設問列の母数（％計算の分母）＝空欄でない有効回答数を返す"""
    return int((series.astype(str).str.strip() != '').sum())


def extract_choice_options(series: pd.Series, is_multi: bool) -> list[str]:
    """
    列の値から選択肢の集合を初出順・重複なしで抽出する。
    複数選択（is_multi=True）は', '区切りを展開してから集める。
    """
    seen: list[str] = []
    seen_set: set[str] = set()
    for raw in series:
        value = str(raw).strip()
        if not value:
            continue
        parts = value.split(_MULTI_DELIM) if is_multi else [value]
        for part in parts:
            part = part.strip()
            if part and part not in seen_set:
                seen_set.add(part)
                seen.append(part)
    return seen


def find_unmatched_values(series: pd.Series, options: list[str], is_multi: bool) -> list[str]:
    """
    RAWデータの値のうち、設問定義の選択肢一覧のどれにも（正規化しても）対応しない値を
    検出する。「機械的な検証」用——正規化しても対応しない値は、選択肢の言い回しが
    根本的に異なるか、Googleフォーム標準「その他」インライン自由記述（SPEC 5.4.1）である
    可能性が高い。出現順・重複なしで返す（空リスト＝問題なし）。
    """
    norm_options = {_normalize_value(o) for o in options}
    seen: list[str] = []
    seen_set: set[str] = set()
    for raw in series:
        value = str(raw).strip()
        if not value:
            continue
        parts = [p.strip() for p in value.split(_MULTI_DELIM)] if is_multi else [value]
        for part in parts:
            if not part or part in seen_set:
                continue
            if _normalize_value(part) not in norm_options:
                seen_set.add(part)
                seen.append(part)
    return seen


def extract_other_values(series: pd.Series, options: list[str], is_multi: bool) -> pd.Series:
    """
    行ごとに、選択肢一覧のどれにも対応しない値（＝その他自由記述の可能性がある値）を
    抽出して返す（該当なしの行は空文字列）。複数選択の場合、該当する要素だけを
    ', '区切りで連結する。SPEC 5.4.1: RAWデータの最右に追加する新しい列の中身に使う——
    元のseries自体は書き換えない（呼び出し側で別列として保持すること）。
    """
    norm_options = {_normalize_value(o) for o in options}

    def _extract(raw) -> str:
        value = str(raw).strip()
        if not value:
            return ''
        parts = [p.strip() for p in value.split(_MULTI_DELIM)] if is_multi else [value]
        others = [p for p in parts if p and _normalize_value(p) not in norm_options]
        return _MULTI_DELIM.join(others)

    return series.apply(_extract)


def simple_tabulation_single(series: pd.Series, options: list[str],
                              display_labels: list[str] | None = None,
                              other_label: str | None = None) -> pd.DataFrame:
    """
    単一選択の単純集計。選択肢ごとの度数・%（母数＝有効回答数に対する構成比、合計100%）を返す。
    optionsは設問定義の原文選択肢（RAWデータとの照合に正規化した上で使う）。display_labels
    （省略可、optionsと同じ長さ）を渡すと、戻り値の'選択肢'列にはこちらを表示用に使う
    （短縮選択肢をラベルにしたい場合など。照合自体はoptions側の原文で行う）。
    other_labelを指定すると、選択肢一覧のどれにも一致しない値（Googleフォーム標準「その他」
    インライン自由記述の可能性がある値、SPEC 5.4.1）を最後の行としてまとめて集計に含める
    （指定しなければ従来通り集計から除外される＝その他選択者が丸ごと漏れる）。
    """
    base = compute_base_count(series)
    values = series.astype(str).str.strip()
    norm_values = values.apply(_normalize_value)
    freq = norm_values[values != ''].value_counts()

    labels = display_labels or options
    rows = []
    matched_norm = {_normalize_value(o) for o in options}
    for opt, label in zip(options, labels):
        n = int(freq.get(_normalize_value(opt), 0))
        rows.append({'選択肢': label, '度数': n, '%': round(n / base * 100, 1) if base else 0.0})
    if other_label:
        other_n = int(sum(c for v, c in freq.items() if v not in matched_norm))
        rows.append({'選択肢': other_label, '度数': other_n, '%': round(other_n / base * 100, 1) if base else 0.0})
    return pd.DataFrame(rows, columns=['選択肢', '度数', '%'])


def simple_tabulation_multi(series: pd.Series, options: list[str],
                             display_labels: list[str] | None = None,
                             other_label: str | None = None) -> pd.DataFrame:
    """
    複数選択の単純集計。選択肢ごとの度数・%（母数＝回答者数に対する選択率、合計は100%を超え得る）を返す。
    optionsとdisplay_labels・other_labelの扱いはsimple_tabulation_singleと同じ。
    """
    base = compute_base_count(series)
    row_sets = [
        {_normalize_value(p) for p in str(v).split(_MULTI_DELIM)} if str(v).strip() else set()
        for v in series
    ]

    labels = display_labels or options
    rows = []
    matched_norm = {_normalize_value(o) for o in options}
    for opt, label in zip(options, labels):
        norm_opt = _normalize_value(opt)
        n = sum(1 for s in row_sets if norm_opt in s)
        rows.append({'選択肢': label, '度数': n, '%': round(n / base * 100, 1) if base else 0.0})
    if other_label:
        n = sum(1 for s in row_sets if s - matched_norm)
        rows.append({'選択肢': other_label, '度数': n, '%': round(n / base * 100, 1) if base else 0.0})
    return pd.DataFrame(rows, columns=['選択肢', '度数', '%'])


def _value_set(value: str, is_multi: bool) -> set[str]:
    """1セルの正規化済み値集合を返す（単一選択でも判定を共通化するため集合にする）"""
    value = str(value).strip()
    if not value:
        return set()
    parts = value.split(_MULTI_DELIM) if is_multi else [value]
    return {_normalize_value(p) for p in parts if p.strip()}


def _attr_mask(values: pd.Series, opt: str, is_multi: bool) -> pd.Series:
    """属性設問の値がoptに該当する行のマスクを返す（正規化して比較）"""
    norm_opt = _normalize_value(opt)
    if is_multi:
        return values.apply(lambda v: norm_opt in _value_set(v, True))
    return values.astype(str).str.strip().apply(_normalize_value) == norm_opt


def _target_distribution(target_values: pd.Series, options: list[str], labels: list[str],
                          is_multi: bool, other_label: str | None = None) -> dict[str, int]:
    """
    対象設問の値集合から、選択肢ごとの度数（正規化して照合）を返す。other_labelを指定すると、
    選択肢一覧のどれにも一致しない値の件数を最後のキーとして追加する（simple_tabulation_*の
    other_labelと同じ設計）。
    """
    valid = target_values.astype(str).str.strip()
    matched_norm = {_normalize_value(o) for o in options}
    if is_multi:
        sets = [_value_set(v, True) for v in valid]
        result = {
            label: sum(1 for s in sets if _normalize_value(opt) in s)
            for opt, label in zip(options, labels)
        }
        if other_label:
            result[other_label] = sum(1 for s in sets if s - matched_norm)
        return result
    norm = valid.apply(_normalize_value)
    freq = norm[valid != ''].value_counts()
    result = {label: int(freq.get(_normalize_value(opt), 0)) for opt, label in zip(options, labels)}
    if other_label:
        result[other_label] = int(sum(c for v, c in freq.items() if v not in matched_norm))
    return result


def cross_tabulation(df: pd.DataFrame, attr_col: str, attr_options: list[str], attr_is_multi: bool,
                      target_col: str, target_options: list[str], target_is_multi: bool,
                      attr_display_labels: list[str] | None = None,
                      target_display_labels: list[str] | None = None,
                      target_other_label: str | None = None) -> dict:
    """
    属性設問（行）×対象設問（列）の2wayクロス集計（SPEC 5.3.1）。
    属性カテゴリごとに回答者を分類し（属性が複数選択の場合、1人が複数カテゴリに属し得る）、
    そのカテゴリ内での対象設問の分布を%化する——母数はそのカテゴリに属する回答者のうち
    対象設問が空欄でない件数（属性側でグルーピングした上での対象側の母数、という設計）。
    行・列の最後に「全体」（属性カテゴリを横断した合計行、対象カテゴリを横断した合計列）を
    付ける（ユーザー提示のフォーマット例、2026-08-21）。
    attr_options/target_optionsは設問定義の原文選択肢（RAWデータとの照合に使う）、
    attr_display_labels/target_display_labelsは表示用ラベル（短縮選択肢など、省略時は原文）。
    target_other_labelを指定すると、対象設問の選択肢一覧に無い値（SPEC 5.4.1のその他自由記述の
    可能性がある値）を「全体」列の手前にまとめて集計する列として追加する（属性側は対象外——
    属性のその他バケット化は現状の要望に含まれないため実装していない）。
    戻り値: {'pct': DataFrame(index=attr_labels+['未回答', '全体'], columns=target_labels(+other)+['全体']),
             'n': DataFrame(同形状), 'base': {属性ラベル: int},
             'unanswered': {属性ラベル: int}}
    unanswered（実装済み、2026-08-25）は、その属性カテゴリに属する回答者のうち対象設問が
    空欄だった件数——母数（base）には含まれない「未回答」の件数を別途知りたいという要望を受けて
    追加した。pct_df/n_dfのDataFrame自体には含めず（呼び出し側が「全体」列の直前に独立した列として
    挿入する、画面表示・Excel出力それぞれの都合に合わせて配置できるように）、baseと同じキーを持つ
    別辞書として返す。
    加えて、属性設問そのものが空欄だった回答者（今までどの属性カテゴリ行にも現れていなかった）を
    まとめる「未回答」行を、「全体」行の直前に自動的に追加する（同日中の追加要望——列だけでなく
    行にも同じ考え方を適用してほしいとのこと）。この行の値は他の属性カテゴリ行と全く同じ計算
    （そのカテゴリ＝属性が空欄だった回答者に対する対象設問の分布）で、base/unanswered辞書にも
    通常の属性ラベルと同じキー（'未回答'）で入る——「未回答」行×「未回答」列の交点は自然に
    「属性・対象の両方が空欄だった回答者数」になる。
    """
    attr_labels = attr_display_labels or attr_options
    target_labels = target_display_labels or target_options
    all_target_labels = [*target_labels, target_other_label] if target_other_label else list(target_labels)
    all_attr_labels = [*attr_labels, _UNANSWERED_LABEL]

    attr_values = df[attr_col].astype(str).str.strip()
    target_values = df[target_col]

    pct_rows: dict[str, dict[str, float]] = {}
    n_rows: dict[str, dict[str, int]] = {}
    base: dict[str, int] = {}
    unanswered: dict[str, int] = {}

    def _add_attr_row(row_label: str, mask: pd.Series) -> None:
        sub_target = target_values[mask]
        sub_base = compute_base_count(sub_target)
        base[row_label] = sub_base
        unanswered[row_label] = len(sub_target) - sub_base

        row_n = _target_distribution(sub_target, target_options, target_labels, target_is_multi,
                                      other_label=target_other_label)
        n_rows[row_label] = row_n
        pct_rows[row_label] = {
            label: round(n / sub_base * 100, 1) if sub_base else 0.0
            for label, n in row_n.items()
        }

    for attr_opt, attr_label in zip(attr_options, attr_labels):
        _add_attr_row(attr_label, _attr_mask(attr_values, attr_opt, attr_is_multi))
    _add_attr_row(_UNANSWERED_LABEL, attr_values == '')

    pct_df = pd.DataFrame(pct_rows).T.reindex(index=all_attr_labels, columns=all_target_labels)
    n_df = pd.DataFrame(n_rows).T.reindex(index=all_attr_labels, columns=all_target_labels)

    # 全体列（行ごとの母数をそのまま表示——MAの対象設問は列合計が100%を超え得るため、
    # 「全体列」はあくまでその行の母数を示す参考列とする）
    n_df[_TOTAL_LABEL] = pd.Series(base).reindex(all_attr_labels)
    pct_df[_TOTAL_LABEL] = 100.0

    # 全体行（属性カテゴリを横断した合計。属性がMAだと単純合算では二重カウントになるため、
    # 母集団全体に対して対象設問を再集計する）
    total_base = compute_base_count(target_values)
    total_row_n = _target_distribution(target_values, target_options, target_labels, target_is_multi,
                                        other_label=target_other_label)
    total_row_n[_TOTAL_LABEL] = total_base
    n_df.loc[_TOTAL_LABEL] = pd.Series(total_row_n)
    pct_df.loc[_TOTAL_LABEL] = {
        label: round(n / total_base * 100, 1) if total_base else 0.0
        for label, n in total_row_n.items()
    }
    base[_TOTAL_LABEL] = total_base
    unanswered[_TOTAL_LABEL] = len(target_values) - total_base

    return {'pct': pct_df, 'n': n_df, 'base': base, 'unanswered': unanswered}


def triple_cross_tabulation(df: pd.DataFrame,
                             attr_large_col: str, attr_large_options: list[str], attr_large_is_multi: bool,
                             attr_mid_col: str, attr_mid_options: list[str], attr_mid_is_multi: bool,
                             target_col: str, target_options: list[str], target_is_multi: bool,
                             attr_large_labels: list[str] | None = None,
                             attr_mid_labels: list[str] | None = None,
                             target_labels: list[str] | None = None,
                             target_other_label: str | None = None) -> dict:
    """
    属性設問（大・表最左）×属性設問（中・表左）×対象設問（表頭）の3wayクロス集計
    （SPEC 5.3.2トリプルクロス指定表）。行は(属性大, 属性中)の組み合わせ（2階層）、
    列は対象設問の選択肢。グラフ作成機能は無い（表のみ）。
    属性大・属性中の両方に該当する回答者だけを対象に対象設問の分布を%化する
    （2wayクロスと同じ設計思想——属性側でグルーピングした上での対象側の母数）。
    target_other_labelはcross_tabulationと同じ（対象設問のその他自由記述をまとめて集計する
    列を追加する）。
    戻り値: {'pct': DataFrame(MultiIndex行=(属性大, 属性中), 列=対象ラベル(+other)),
             'n': DataFrame(同形状), 'base': {(属性大, 属性中): int},
             'unanswered': {(属性大, 属性中): int}}
    unanswered（実装済み、2026-08-25）はcross_tabulationと同じ意味（母数に含まれない未回答の
    件数）——詳細はcross_tabulationのdocstring参照。
    加えて、属性大・属性中のどちらか（または両方）が空欄で、既存のどの(属性大, 属性中)行にも
    分類されていなかった回答者をまとめる('未回答','未回答')行を末尾に追加する（cross_tabulationの
    「未回答」行と同じ考え方——ただし属性が2軸あるため、片方だけ未回答/両方未回答を区別せず
    1行にまとめる。指定した属性大・属性中のどちらか一方でも実在の選択肢に一致しない限り
    この行に含まれる）。
    """
    large_labels = attr_large_labels or attr_large_options
    mid_labels = attr_mid_labels or attr_mid_options
    t_labels = target_labels or target_options
    all_t_labels = [*t_labels, target_other_label] if target_other_label else list(t_labels)

    large_values = df[attr_large_col].astype(str).str.strip()
    mid_values = df[attr_mid_col].astype(str).str.strip()
    target_values = df[target_col]

    index_tuples: list[tuple[str, str]] = []
    pct_rows: list[dict[str, float]] = []
    n_rows: list[dict[str, int]] = []
    base: dict[tuple[str, str], int] = {}
    unanswered: dict[tuple[str, str], int] = {}

    large_any_mask = pd.Series(False, index=df.index)
    for large_opt in attr_large_options:
        large_any_mask |= _attr_mask(large_values, large_opt, attr_large_is_multi)
    mid_any_mask = pd.Series(False, index=df.index)
    for mid_opt in attr_mid_options:
        mid_any_mask |= _attr_mask(mid_values, mid_opt, attr_mid_is_multi)

    def _add_row(key: tuple[str, str], mask: pd.Series) -> None:
        sub_target = target_values[mask]
        sub_base = compute_base_count(sub_target)

        row_n = _target_distribution(sub_target, target_options, t_labels, target_is_multi,
                                      other_label=target_other_label)
        row_pct = {
            label: round(n / sub_base * 100, 1) if sub_base else 0.0
            for label, n in row_n.items()
        }

        index_tuples.append(key)
        n_rows.append(row_n)
        pct_rows.append(row_pct)
        base[key] = sub_base
        unanswered[key] = len(sub_target) - sub_base

    for large_opt, large_label in zip(attr_large_options, large_labels):
        large_mask = _attr_mask(large_values, large_opt, attr_large_is_multi)
        for mid_opt, mid_label in zip(attr_mid_options, mid_labels):
            mid_mask = _attr_mask(mid_values, mid_opt, attr_mid_is_multi)
            _add_row((large_label, mid_label), large_mask & mid_mask)
    _add_row((_UNANSWERED_LABEL, _UNANSWERED_LABEL), ~(large_any_mask & mid_any_mask))

    index = pd.MultiIndex.from_tuples(index_tuples, names=['属性（大）', '属性（中）'])
    pct_df = pd.DataFrame(pct_rows, index=index).reindex(columns=all_t_labels)
    n_df = pd.DataFrame(n_rows, index=index).reindex(columns=all_t_labels)
    return {'pct': pct_df, 'n': n_df, 'base': base, 'unanswered': unanswered}
