"""
tab_question_definition.py
第1タブ「設問定義・RAWデータ確認」。前半は設問定義表（アンケートフォームのHTML（推奨、
core/form_html.py・LLM不使用で構造から機械的に抽出）またはPDF（最後の手段、core/form_pdf.py・
LLMで構造化）から初稿を作成し、縦持ちの一覧表で編集・確定する。RAWデータの回答パターンには
頼らない——SPEC 5.1・6節）。後半はRAWデータの確認・クリーニング（形式確認・設問定義表との
整合性チェック・TEST回答削除・文字化けセル候補、SPEC 5.2節）。
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

import llm_client
from core.cleaning import find_blank_response_candidates, find_mojibake_candidates
from core.cross_plan import gridable_questions, question_label
from core.form_html import SHORT_LABEL_MODEL, parse_form_html, propose_short_labels
from core.form_pdf import extract_pdf_text, propose_question_definitions
from core.other_text import build_other_text_columns
from core.question_definition import (
    FORMAT_CHOICES,
    FORMAT_FA,
    FORMAT_MA,
    FORMAT_SA,
    add_manual_entry,
    apply_review_edits,
    build_consistency_issues,
    build_entries,
    count_invalid_questions,
    find_unmatched_value_entries,
    to_review_dataframe,
)
from core.usage_log import DEFAULT_FX_RATE, build_entry as build_log_entry, snapshot as usage_snapshot

# 質問数が多い・構造判断（SA/MA/FA・matrix候補）の精度が重要なため、コスト重視のhaikuではなく
# 標準モデルを使う。
DRAFT_MODEL = 'claude-sonnet-5'
_DEFAULT_INITIAL_ROWS = 50


def render(columns: list[str], rows: list[dict], raw_filename: str, raw_encoding: str,
           excluded_row_ids: list[int], api_key: str) -> None:
    st.subheader('設問定義表')
    _render_question_definition(api_key)

    st.divider()
    _render_raw_data_check(columns, rows, raw_filename, raw_encoding, excluded_row_ids)


def _render_question_definition(api_key: str) -> None:
    entries = st.session_state.get('question_definition', [])
    if not entries:
        _render_import(api_key)
        return

    confirmed = st.session_state.get('question_definition_confirmed', False)

    warnings = st.session_state.pop('_question_definition_warnings', None)
    if warnings:
        st.warning('\n'.join(warnings))

    # 確定（保護）済みでも、タイムスタンプ等の手動追加は行えるようにする——以前はここが
    # 未確定時のみ表示され、確定後は保護解除しないと追加できなかった（確定した状態で
    # 集計まで進めた後にタイムスタンプの追加漏れに気づく、という実際の使われ方に合わない
    # 実害があったため、確定状態に関わらず常に表示するよう変更、2026-08-22）。
    _render_manual_entry_form(entries)
    _render_condition_review(entries)

    if confirmed:
        st.success(
            '設問定義表は確定済みです。以降の一覧表・グラフ・集計では短縮設問文・短縮選択肢が'
            '使われます（空欄の項目は原文が使われます）。'
        )
        st.info('念のため、サイドバー下部からプロジェクトファイルをダウンロードしておくことをお勧めします。')
        if st.button('🔓 保護を解除する', key='unlock_question_definition'):
            st.session_state['question_definition_confirmed'] = False
            st.rerun()
        st.dataframe(
            to_review_dataframe(entries), width='stretch', height=500, hide_index=True,
            column_config={
                'ID': st.column_config.TextColumn('ID', pinned=True),
                '必': st.column_config.TextColumn('必', width='small'),
            },
        )
        return

    st.caption(
        'アンケートフォームのPDFから作成した設問定義表です。「設問文」「選択肢」列は変更できません。'
        '「形式」（SA/MA/FAを直接入力）「短縮設問文」「短縮選択肢」「matrix」「n変化」は自由に'
        '編集できます。内容に問題がなければ「設問定義表の確定」を押してください（確定後は一覧表・'
        'グラフ・集計で短縮版が使われます。未確定のままなら原文が使われます）。'
    )

    review_df = to_review_dataframe(entries)
    edited_df = st.data_editor(
        review_df, width='stretch', height=500, hide_index=True, key='question_definition_editor',
        disabled=['ID', '必', '設問文', '選択肢'],
        column_config={
            'ID': st.column_config.TextColumn('ID', pinned=True),
            '必': st.column_config.TextColumn('必', width='small'),
        },
        # 形式にSelectboxColumnを使うと、同じ表内の他の空欄セルが"None"と表示されてしまう
        # Streamlitの既知の不具合があるため、自由入力＋apply_review_edits側での検証にしている。
        # 「必」（必須設問マーク）はフォーム自体から読み取った情報で人が編集するものではないため
        # disabledに含める（2026-08-25、ユーザーとの合意事項）。
    )

    col1, col2 = st.columns(2)
    with col1:
        if st.button('✅ 設問定義表の確定', type='primary', key='confirm_question_definition'):
            updated_entries, warnings = apply_review_edits(edited_df, entries)
            st.session_state['question_definition'] = updated_entries
            st.session_state['question_definition_confirmed'] = True
            if warnings:
                st.session_state['_question_definition_warnings'] = warnings
            st.rerun()
    with col2:
        source = st.session_state.get('question_definition_source', 'html')
        label = '🆕 HTMLから作り直す' if source == 'html' else '🆕 PDFから作り直す'
        if st.button(label, key='rebuild_question_definition'):
            # form_html_bytes/form_pdf_bytesは消さない。サイドバーでアップロード済みの同じ
            # ファイルを使って再作成する（別のファイルに差し替えたい場合はサイドバーで
            # 新しいファイルを選び直せばよい）。
            st.session_state['question_definition'] = []
            st.rerun()


_TIMESTAMP_TEXT = 'タイムスタンプ'
_TIMESTAMP_ENTRY_ID = 'time'


def _render_manual_entry_form(entries: list[dict]) -> None:
    """
    フォームPDFには出てこないがRAWデータには存在する列（Googleフォームが自動追加する
    「タイムスタンプ」列など）を、設問定義表に手動で1行追加できるようにする。RAW列見出しと
    一字一句同じ設問文を入れれば、設問定義表とRAWデータの整合性チェックで自動的に対応付けられる。
    最頻出のケースである「タイムスタンプ」（FA・設問文/短縮設問文とも「タイムスタンプ」）を
    既定値にしておき、そのまま送信ボタンを押すだけで追加できるようにしている——以前はRAW
    アップロード時に自動追加していたが、HTMLからの設問定義作成前にRAWを先にアップロードすると
    「設問定義表がまだ空でない」状態になってしまい、HTML読み込みの初回作成フローが動かなく
    なる実害があった（2026-08-22）ため、このユーザー操作によるフォーム送信方式に一本化した。
    """
    with st.expander('➕ 設問を手動で追加（フォームPDFに無い列、タイムスタンプなど）', expanded=False):
        st.caption('RAWデータの列見出しと一字一句同じ設問文を入力すると、整合性チェックで自動的に対応付けられます。')
        # st.formで囲むと送信時に入力欄が自動でクリアされる（clear_on_submit）。フォーム内は
        # 送信までウィジェットの値変化で再実行されないため、選択肢欄は形式に関わらず常時表示し、
        # SA/MA以外では単に無視する（動的な表示切替はフォームの外でしかできないため）。
        with st.form('manual_entry_form', clear_on_submit=True):
            new_text = st.text_input('設問文', value=_TIMESTAMP_TEXT)
            new_short = st.text_input('短縮設問文（任意）', value=_TIMESTAMP_TEXT)
            new_format = st.selectbox('形式', FORMAT_CHOICES, index=FORMAT_CHOICES.index(FORMAT_FA))
            new_options_text = st.text_area('選択肢（1行に1つ、SA/MAの場合のみ使用）')
            at_start = st.radio('挿入位置', ['先頭に追加', '末尾に追加'], horizontal=True) == '先頭に追加'
            submitted = st.form_submit_button('追加する')

        if submitted:
            if not new_text.strip():
                st.error('設問文を入力してください。')
                return
            option_texts = (
                [line.strip() for line in new_options_text.splitlines() if line.strip()]
                if new_format in (FORMAT_SA, FORMAT_MA) else []
            )
            # タイムスタンプは毎回同じ固定ID（'time'）にする——Q番号は他の設問の追加・削除で
            # ずれ得るため、常に同じIDで参照したい列にはQ番号の通し番号を使わない。
            entry_id = _TIMESTAMP_ENTRY_ID if new_text.strip() == _TIMESTAMP_TEXT else None
            if entry_id and any(e['id'] == entry_id for e in entries):
                st.warning('「タイムスタンプ」は既に追加済みです。')
                return
            st.session_state['question_definition'] = add_manual_entry(
                entries, question_text=new_text.strip(), format=new_format,
                short_question=new_short.strip(), option_texts=option_texts, at_start=at_start,
                entry_id=entry_id,
            )
            st.rerun()


def _preceding_gridable(entries: list[dict], target_id: str) -> dict | None:
    """
    target_idの設問より前にある直近のSA/MA設問を返す（前提条件のゲート設問の既定候補）。
    分岐の説明文言（n_note）はほぼ必ず「前の設問」「直前の設問」を指すため、この既定値を
    出発点にすれば人が選び直す手間がほとんどのケースで不要になる。
    """
    idx = next((i for i, e in enumerate(entries) if e['id'] == target_id), None)
    if idx is None:
        return None
    for e in reversed(entries[:idx]):
        if e['format'] in (FORMAT_SA, FORMAT_MA):
            return e
    return None


def _render_condition_editor(entry: dict, entries: list[dict], gate_candidates: list[dict],
                              label_by_id: dict[str, str], key_prefix: str,
                              default_on: bool = False, checkbox_label: str | None = None,
                              help_text: str | None = None) -> None:
    """
    entry1件分の前提条件チェックボックス＋ゲート設問・対象値の選択UIをコンパクトな2行構成
    （1行目: チェックボックス（ラベル＝対象設問名）、2行目: ゲート設問＋対象回答を横並び）で
    表示する（実装済み、2026-08-25。当初は縦積みで冗長という指摘を受け整理）。entryを直接
    書き換える。
    """
    is_on = st.checkbox(
        checkbox_label or label_by_id[entry['id']], value=default_on,
        key=f'{key_prefix}_on_{entry["id"]}', help=help_text,
    )
    if not is_on:
        entry['condition_entry_id'] = None
        entry['condition_values'] = []
        return

    gate_pool = [e for e in gate_candidates if e['id'] != entry['id']]
    if not gate_pool:
        st.caption('前提条件に使えるSA/MA設問がありません。')
        entry['condition_entry_id'] = None
        entry['condition_values'] = []
        return

    gate_keys = {f'{e["id"]}: {label_by_id[e["id"]]}': e for e in gate_pool}
    keys_list = list(gate_keys.keys())
    default_gate_id = entry.get('condition_entry_id') or (_preceding_gridable(entries, entry['id']) or {}).get('id')
    default_key = next((k for k, e in gate_keys.items() if e['id'] == default_gate_id), None)

    col1, col2 = st.columns(2)
    with col1:
        gate_key = st.selectbox(
            '前提条件となる設問', keys_list, index=keys_list.index(default_key) if default_key else 0,
            key=f'{key_prefix}_gate_{entry["id"]}', label_visibility='collapsed',
        )
    gate_entry = gate_keys[gate_key]

    option_texts = [o['text'] for o in gate_entry['options']]
    default_values = entry.get('condition_values', []) if entry.get('condition_entry_id') == gate_entry['id'] else []
    with col2:
        selected_values = st.multiselect(
            '対象とする回答', option_texts, default=default_values,
            key=f'{key_prefix}_values_{entry["id"]}', label_visibility='collapsed',
            placeholder='対象とする回答を選択',
        )
    entry['condition_entry_id'] = gate_entry['id'] if selected_values else None
    entry['condition_values'] = selected_values


def _render_condition_review(entries: list[dict]) -> None:
    """
    分岐（スキップロジック）がある設問向けの前提条件設定（SPEC 5.4.3、2026-08-25）。
    「この設問は〈設問A〉が〈値〉の回答者のみが対象」という条件を設問ごとに設定できる。
    設定すると、この設問を対象設問として集計するとき（GT・クロスの対象側・トリプルクロスの
    対象側・一覧型クロスの対象側）、集計表に「回答条件あり」の表示と、本来の対象者数に対する
    回答率が加わる（5.4.2節）——未設定の設問は今まで通り全回答者が対象（属性として使う場合は
    この設定に関わらず対象外、other_bucket等と同じ扱い）。前提条件となる設問自身がさらに前提条件を
    持っていても、RAWデータ上は「対象外の回答者は前提設問のセルも空欄のまま」になるため、
    直接の前提設問1段だけを見れば連鎖的な分岐も自動的に正しく絞り込める（再帰的な解決は不要）。

    **n変化列との連携（2026-08-25、ユーザーとの合意事項）**: 分岐箇所はフォームPDF/HTML
    解析の時点でn_note（表の「n変化」列、フォームのセクション説明文言から拾った自由記述の
    メモ）に既に記録されていることが多い。ただしn_noteは自由記述で、どの設問のどの回答値が
    条件かを機械的に確定はできない（表現のブレがあり誤判定の方が実害が大きい）ため、対象の
    回答値そのものは人に選んでもらう必要がある。その代わり、n_noteがある設問だけを対象一覧に
    絞り込み、ゲート設問は「直前の設問」を既定選択にしておくことで、チェックを入れて対象の
    回答を選ぶだけで済むようにし、全設問からの検索・選択の手間を無くした。n_noteが無い設問
    向けの詳細設定は下の折りたたみに残す。
    """
    gate_candidates = gridable_questions(entries)
    if len(gate_candidates) < 1:
        return

    st.markdown('##### 前提条件（分岐条件）の設定')
    st.caption(
        '「n変化」列にメモがある設問（分岐で対象者が絞られる設問）です。チェックを入れて対象の'
        '回答を選ぶと、集計表に回答条件・回答率が表示されます（未チェックの設問は今まで通り'
        '全回答者を対象に計算します）。ゲート設問は既定で直前の設問——チェックボックスにマウスを'
        '乗せるとn変化のメモが確認できます。'
    )

    label_by_id = {e['id']: question_label(e) for e in entries}
    noted_entries = [e for e in entries if e.get('n_note', '').strip()]
    if not noted_entries:
        st.caption('「n変化」列にメモがある設問はありません。')
    for entry in noted_entries:
        _render_condition_editor(
            entry, entries, gate_candidates, label_by_id, key_prefix='condition_noted',
            default_on=bool(entry.get('condition_entry_id')),
            checkbox_label=label_by_id[entry['id']], help_text=f'n変化のメモ: {entry["n_note"]}',
        )

    other_configured = [
        e for e in entries if e.get('condition_entry_id') and not e.get('n_note', '').strip()
    ]
    with st.expander('➕ n変化にメモが無い設問にも前提条件を設定する（詳細設定）', expanded=bool(other_configured)):
        if other_configured:
            st.caption('この詳細設定で設定済み:')
            for entry in other_configured:
                gate_label = label_by_id.get(entry['condition_entry_id'], '（対応する設問なし）')
                values = '、'.join(entry.get('condition_values', []))
                col1, col2 = st.columns([6, 1])
                with col1:
                    st.write(f'- 「{label_by_id[entry["id"]]}」は「{gate_label}」が「{values}」の場合のみ対象')
                with col2:
                    if st.button('解除', key=f'condition_manual_clear_{entry["id"]}'):
                        entry['condition_entry_id'] = None
                        entry['condition_values'] = []
                        st.rerun()
            st.divider()

        target_options = {f'{e["id"]}: {label_by_id[e["id"]]}': e['id'] for e in entries}
        target_key = st.selectbox('対象設問（この条件を適用する設問）', list(target_options.keys()), key='condition_target_select')
        target_entry = next(e for e in entries if e['id'] == target_options[target_key])
        _render_condition_editor(
            target_entry, entries, gate_candidates, label_by_id, key_prefix='condition_manual',
            default_on=bool(target_entry.get('condition_entry_id')),
        )


def _render_import(api_key: str) -> None:
    """
    HTML方式（推奨、core/form_html.py）とPDF方式（core/form_pdf.py、最後の手段）、両方の
    アップロードを検知する。両方アップロードされている場合はHTML方式を優先する
    （2026-08-22、ユーザーとの合意事項:「①専用の抽出システムを持つ、②不具合は調査・修復、
    ③それでもダメならPDF方式」）。
    """
    html_bytes = st.session_state.get('form_html_bytes')
    pdf_bytes = st.session_state.get('form_pdf_bytes')

    if html_bytes is not None:
        _render_html_import(api_key)
    elif pdf_bytes is not None:
        _render_pdf_import(api_key)
    else:
        st.caption(
            'サイドバーからアンケートフォームのデータをアップロードすると、設問定義表の初稿を'
            '作成します。**HTML方式**（Googleフォームの編集画面をブラウザで「名前を付けて保存」'
            '→「ウェブページ、HTMLのみ」で保存したもの）を推奨します——LLMを使わずページの'
            '構造から機械的に抽出するため、PDF方式で繰り返し起きていた文字化けの心配が'
            'ありません。うまく解析できない場合は**PDF方式**（Googleフォームの印刷/PDF保存'
            '機能で書き出したもの）もお使いいただけます。'
        )


def _render_html_import(api_key: str) -> None:
    """
    HTMLのアップロード自体はサイドバー（ui/sidebar.py）が受け持ち、バイト列を
    session_state['form_html_bytes']に置く。ここではそれを検知して解析する（LLMは使わない）。
    APIキーが無くても構造そのものは作成できる——短縮設問文・短縮選択肢の作成にのみ
    （任意で）Haikuモデルを使う。
    """
    html_bytes = st.session_state.get('form_html_bytes')
    html_name = st.session_state.get('form_html_name', '')
    st.caption(f'アップロード済み（HTML方式）: {html_name}')

    try:
        html_text = html_bytes.decode('utf-8')
    except UnicodeDecodeError as e:
        st.error(f'HTMLの読み込みに失敗しました（文字コードがUTF-8ではない可能性があります）: {e}')
        return

    with st.spinner('HTMLを解析しています...'):
        try:
            questions, parse_warnings = parse_form_html(html_text)
        except Exception as e:
            st.error(f'HTMLの解析に失敗しました: {e}。PDF方式もお試しください。')
            return

    if not questions:
        st.error('設問を抽出できませんでした。保存したページが編集画面か確認するか、PDF方式をお試しください。')
        return

    if api_key:
        client = llm_client.make_client('Anthropic', api_key)
        usage_before = usage_snapshot(llm_client.get_token_usage)
        with st.spinner('短縮設問文・短縮選択肢の案を作成しています...'):
            questions, label_warnings = propose_short_labels(client, questions, SHORT_LABEL_MODEL)
        usage_after = usage_snapshot(llm_client.get_token_usage)
        fx_rate = st.session_state.get('fx_rate', DEFAULT_FX_RATE)
        st.session_state.setdefault('usage_log', []).append(
            build_log_entry('短縮設問文・短縮選択肢の作成', SHORT_LABEL_MODEL, usage_before, usage_after, fx_rate=fx_rate)
        )
        parse_warnings = [*parse_warnings, *label_warnings]
    else:
        parse_warnings = [*parse_warnings, 'APIキーが未設定のため、短縮設問文・短縮選択肢は作成されませんでした（人が後で入力できます）。']

    if parse_warnings:
        st.session_state['_question_definition_warnings'] = parse_warnings

    st.session_state['question_definition'] = build_entries(questions)
    st.session_state['question_definition_confirmed'] = False
    st.session_state['question_definition_source'] = 'html'
    st.rerun()


def _render_pdf_import(api_key: str) -> None:
    """
    PDFのアップロード自体はサイドバー（ui/sidebar.py）が受け持ち、バイト列を
    session_state['form_pdf_bytes']に置く。ここではそれを検知してテキスト抽出・LLM呼び出しを
    行う（スピナー・エラー表示をメイン画面に出すため、処理自体はこちら側で行う）。
    """
    pdf_bytes = st.session_state.get('form_pdf_bytes')
    pdf_name = st.session_state.get('form_pdf_name', '')

    st.caption(f'アップロード済み（PDF方式）: {pdf_name}')
    if not api_key:
        st.info('サイドバーでAnthropic APIキーを設定すると設問定義表を作成できます。')
        return

    with st.spinner('PDFを読み込んでいます...'):
        try:
            pdf_text = extract_pdf_text(pdf_bytes)
        except Exception as e:
            st.error(f'PDFの読み込みに失敗しました: {e}')
            return

    if not pdf_text.strip():
        st.error('PDFからテキストを抽出できませんでした（画像のみのPDFの可能性があります）。')
        return

    client = llm_client.make_client('Anthropic', api_key)
    usage_before = usage_snapshot(llm_client.get_token_usage)
    with st.spinner('LLMに設問定義表の作成を依頼しています...'):
        questions = propose_question_definitions(client, pdf_text, DRAFT_MODEL)
    usage_after = usage_snapshot(llm_client.get_token_usage)
    fx_rate = st.session_state.get('fx_rate', DEFAULT_FX_RATE)
    st.session_state.setdefault('usage_log', []).append(
        build_log_entry('設問定義表の初稿作成', DRAFT_MODEL, usage_before, usage_after, fx_rate=fx_rate)
    )

    if questions is None:
        st.error(f'設問定義表の作成に失敗しました: {llm_client.get_last_error()}')
        return
    if not questions:
        st.warning('設問が見つかりませんでした。')
        return

    invalid_count = count_invalid_questions(questions)
    if invalid_count:
        st.session_state['_question_definition_warnings'] = [
            f'{invalid_count}件の設問データがLLMから想定外の形式で返され、除外しました。'
            'PDFの内容と設問数を見比べ、不足があれば「🆕 PDFから作り直す」で再作成してください。'
        ]

    st.session_state['question_definition'] = build_entries(questions)
    st.session_state['question_definition_confirmed'] = False
    st.session_state['question_definition_source'] = 'pdf'
    st.rerun()


def _render_other_text_extraction(columns: list[str], df: pd.DataFrame, entries: list[dict]) -> None:
    """
    SA/MA設問の「選択肢一覧に無い値」（Googleフォーム標準の「その他」自由記述の可能性がある値）を
    検出し、ボタン操作でRAWデータの最右に新しい列として追加する（SPEC 5.4.1）。元の列・値は
    書き換えない。追加後に再度押しても、既に追加済みの列は重複追加しない。
    """
    other_columns = build_other_text_columns(df, entries, columns)
    if not other_columns:
        return

    existing = set(st.session_state.get('raw_columns', []))
    new_columns = {name: series for name, series in other_columns.items() if name not in existing}
    if not new_columns:
        st.caption('その他自由記述の列は追加済みです。')
        return

    st.info(
        f'選択肢一覧に無い値（「その他」の自由記述の可能性）が見つかった設問が{len(new_columns)}件あります。'
        'ボタンを押すと、回答者とのつながりを保ったまま、RAWデータの最右に新しい列として追加できます'
        '（元の列・値は変更しません）。あわせて、追加した列に対応するFA（自由記述）の設問定義も'
        '自動的に追加するため、整合性チェックに「対応する設問定義が見つかりません」と出ることはありません。'
    )
    if st.button('➕ その他自由記述をRAWデータに追加する', key='extract_other_text_button'):
        rows = st.session_state.get('raw_rows', [])
        for name, series in new_columns.items():
            values = series.tolist()
            for row, value in zip(rows, values):
                row[name] = value
        st.session_state['raw_columns'] = st.session_state.get('raw_columns', []) + list(new_columns.keys())
        st.session_state['raw_rows'] = rows

        updated_entries = st.session_state.get('question_definition', [])
        for name in new_columns:
            updated_entries = add_manual_entry(
                updated_entries, question_text=name, format=FORMAT_FA,
                short_question=name, option_texts=[], at_start=False,
            )
        st.session_state['question_definition'] = updated_entries

        st.success(f'{len(new_columns)}件の列と、対応する設問定義（FA）を追加しました。')
        st.rerun()


def _render_other_bucket_review(columns: list[str], df: pd.DataFrame, entries: list[dict]) -> None:
    """
    SA/MA設問のうち、選択肢一覧に無い値があるものを一覧表示し、対象設問として集計するときに
    自動的に「その他」としてまとめるかどうか（各entryのother_bucketフィールド）を、設問ごとに
    一度だけ人が確定できるようにする（SPEC 5.4.1）。この決定は設問定義表（プロジェクトデータ）
    に保存され、以後の集計実行（集計表タブ）では毎回RAWデータを再スキャンして警告を出す
    のではなく、ここで確定した値をそのまま使う——実データで「集計するたびに同じ警告が出て
    紛らわしい」という指摘を受け、判断を一度だけここで行う設計に変更した（2026-08-22）。
    """
    unmatched_entries = find_unmatched_value_entries(entries, columns, df)
    if not unmatched_entries:
        return

    st.markdown('##### その他自動集計の確認')
    st.caption(
        f'{len(unmatched_entries)}件の設問で、選択肢一覧のどれにも一致しない値が見つかりました'
        '（選択肢テキストの言い回しのずれ、または「その他」の自由記述の可能性があります）。'
        'チェックを入れた設問は、対象設問として集計するときにこれらの値を自動的に「その他」'
        'としてまとめます（属性として使う場合、この決定に関わらずバケット化されません）。'
    )
    for item in unmatched_entries:
        entry = item['entry']
        unmatched = item['unmatched']
        label = entry['short_question'] or entry['question_text']
        title = label if len(label) <= 40 else f'{label[:40]}…'
        preview = '、'.join(f'「{v}」' for v in unmatched[:5])
        more = f'ほか{len(unmatched) - 5}件' if len(unmatched) > 5 else ''
        entry['other_bucket'] = st.checkbox(
            f'「{title}」（{len(unmatched)}種類: {preview}{more}）を「その他」として自動集計に含める',
            value=entry.get('other_bucket', True), key=f'other_bucket_{entry["id"]}',
        )


def _pending_native_other_entries(entries: list[dict], columns: list[str], df: pd.DataFrame) -> list[dict]:
    """
    has_native_other=Trueの設問のうち、その他自由記述の抽出（RAWデータへの列追加）がまだ
    行われていない（かつユーザーが今回のセッションで「いいえ」を選んでいない）ものを返す。
    アップロード順（フォーム→RAW／RAW→フォーム／ほぼ同時）に関わらず判定できるよう、
    「RAWデータへの列追加が済んでいるか」という状態そのもので判定する（RAWアップロード時の
    1回限りの判定だと、フォームデータとRAWデータをほぼ同時にアップロードした場合に判定が
    抜けてしまう実例が見つかったため、2026-08-23に一度限りの判定から常時判定に設計変更した）。
    """
    native_entries = [e for e in entries if e.get('has_native_other')]
    if not native_entries:
        return []
    existing = set(st.session_state.get('raw_columns', []))
    dismissed = st.session_state.get('_native_other_dismissed_ids', set())
    pending = []
    for entry in native_entries:
        if entry['id'] in dismissed:
            continue
        entry_columns = build_other_text_columns(df, [entry], columns)
        if any(name not in existing for name in entry_columns):
            pending.append(entry)
    return pending


def _render_native_other_confirm(pending_entries: list[dict], columns: list[str], df: pd.DataFrame) -> None:
    """
    Googleフォーム標準の「その他」インライン自由記述（ラジオボタン＋自由記述欄が一体になった
    UI）が使われている設問のうち、まだ処理していないものがある場合の確認画面（SPEC 5.4.1、
    2026-08-23）。この形式の回答は選択肢一覧のどれとも一致しないため、確認なしに黙って処理
    してしまうと不整合の原因が分かりにくい——ユーザーの実際のフォームで根本原因が判明した
    のを機に設けた。「はい」を押すと、その他自由記述の抽出・FA設問の新設（core/other_text.py、
    既存の仕組み）を実行する。「いいえ」を押すとこのセッション中はこれらの設問について再度
    確認しない（対象設問として集計する際の「その他」への自動集計自体はother_bucket決定に
    従って引き続き正しく行われるため、後回しにしても集計の正しさに影響は無い——あとから
    「その他自由記述の列を追加する」ボタンで手動でも処理できる）。
    """
    labels = [e['short_question'] or e['question_text'] for e in pending_entries]

    st.warning(
        '以下の設問にGoogleフォーム独自の「その他」回答形式（ラジオボタンに自由記述欄が'
        '直接付いた形式）が使われています。この形式の回答はそのままでは選択肢一覧のどれとも'
        '一致せず、CrossCounterで正しく集計できません。よろしければ、これらの自由記述を'
        '各設問の末尾に「（設問名）_その他自由記述」というFA（自由記述）の設問として新設し、'
        '元の設問では「その他」としてまとめて自動集計する形に変更します'
        '（元のRAWデータの値そのものは変更しません）。\n\n'
        + '\n'.join(f'- {label}' for label in labels)
    )
    col_yes, col_no = st.columns(2)
    with col_yes:
        if st.button('✅ はい、処理する', type='primary', key='confirm_native_other_yes', width='stretch'):
            _apply_native_other_processing(pending_entries, columns, df)
            st.rerun()
    with col_no:
        if st.button('❌ いいえ、今は処理しない', key='confirm_native_other_no', width='stretch'):
            dismissed = st.session_state.get('_native_other_dismissed_ids', set())
            st.session_state['_native_other_dismissed_ids'] = dismissed | {e['id'] for e in pending_entries}
            st.rerun()


def _apply_native_other_processing(native_entries: list[dict], columns: list[str], df: pd.DataFrame) -> None:
    """確認「はい」後の処理。その他自由記述の抽出・FA設問の新設を行う（RAWデータは変更しない）。"""
    other_columns = build_other_text_columns(df, native_entries, columns)
    if not other_columns:
        st.session_state['_native_other_processed_report'] = []
        return

    rows = st.session_state['raw_rows']
    for name, series in other_columns.items():
        values = series.tolist()
        for row, value in zip(rows, values):
            row[name] = value
    st.session_state['raw_columns'] = st.session_state['raw_columns'] + list(other_columns.keys())
    st.session_state['raw_rows'] = rows

    updated_entries = st.session_state.get('question_definition', [])
    for name in other_columns:
        updated_entries = add_manual_entry(
            updated_entries, question_text=name, format=FORMAT_FA,
            short_question=name, option_texts=[], at_start=False,
        )
    st.session_state['question_definition'] = updated_entries
    st.session_state['_native_other_processed_report'] = list(other_columns.keys())


def _render_raw_data_check(columns: list[str], rows: list[dict], raw_filename: str, raw_encoding: str,
                            excluded_row_ids: list[int]) -> None:
    st.subheader('RAWデータ確認')

    report = st.session_state.pop('_native_other_processed_report', None)
    if report is not None:
        if report:
            st.success(
                'Googleフォーム独自の「その他」回答形式の処理が完了しました。'
                f'{len(report)}件の設問について、末尾にFA（自由記述）の設問として追加しました:\n'
                + '\n'.join(f'- {name}' for name in report)
            )
        else:
            st.success(
                'Googleフォーム独自の「その他」回答形式が使われている設問を確認しましたが、'
                '該当する自由記述の回答は見つかりませんでした。'
            )

    if not rows:
        st.info('サイドバーからRAWファイル（CSV/Excel）を読み込んでください。')
        return

    df = pd.DataFrame(rows)
    entries = st.session_state.get('question_definition', [])

    pending_entries = _pending_native_other_entries(entries, columns, df)
    if pending_entries:
        _render_native_other_confirm(pending_entries, columns, df)
        return

    st.markdown('##### 形式確認')
    st.caption(
        f'ファイル: {raw_filename} ／ 文字コード: {raw_encoding} ／ '
        f'設問数: {len(columns)} ／ 回答数: {len(df)}'
    )
    if not entries:
        st.caption('設問定義表がまだありません。上の「設問定義表」でPDFから作成すると、RAWデータとの整合性を確認できます。')
    else:
        issues = build_consistency_issues(entries, columns)
        if not issues:
            st.success('設問定義表とRAWデータの列に矛盾は見つかりませんでした。')
        else:
            st.warning(f'設問定義表とRAWデータの対応に{len(issues)}件の問題が見つかりました。')
            st.markdown('\n'.join(f'- {issue}' for issue in issues))
        _render_other_bucket_review(columns, df, entries)
        _render_other_text_extraction(columns, df, entries)

    st.divider()
    mojibake_candidates = find_mojibake_candidates(df[columns])
    blank_candidates = find_blank_response_candidates(df[columns])

    st.markdown('##### テスト回答削除')
    st.caption(
        '回答順で先頭n件（既定50件、調整可）の回答データを表示します。目視して不要なデータは'
        '削除してください。キーワードによる自動絞り込みは、選択肢文自体に「テスト」を含む場合や'
        'アンケートの話題自体が学校の試験に触れる場合に正当な回答まで誤検出してしまい実用的'
        'ではなかったため採用していません。下の表（約10行分の窓）をスクロールしながら、'
        'テスト投稿だと判断した行をクリックして選択してください。'
    )
    n_initial = st.number_input(
        '確認する先頭行数', min_value=10, max_value=len(df), step=10,
        value=min(_DEFAULT_INITIAL_ROWS, len(df)), key='initial_rows_n',
    )
    test_selected_row_ids = _render_initial_rows_table(df, columns, int(n_initial))

    st.divider()
    st.markdown('##### 文字化けセル候補')
    if not mojibake_candidates:
        st.caption('文字化け（置換文字）を含むセルは見つかりませんでした。')
    else:
        st.caption(
            f'{len(mojibake_candidates)}件の行で文字化けの疑いがあるセルが見つかりました。'
            '除外する行のチェックを確認してください（初期状態は全てチェック済み＝除外）。'
        )
        for row_id, cols in mojibake_candidates.items():
            label = f'行{row_id}（文字化けの疑いがある列: {", ".join(cols)}）'
            st.checkbox(label, value=True, key=f'mojibake_excl_{row_id}')

    st.divider()
    st.markdown('##### 空欄提出候補')
    if not blank_candidates:
        st.caption('実質的に何も回答していない行（空欄提出）は見つかりませんでした。')
    else:
        st.caption(
            f'{len(blank_candidates)}件の行で、ほぼ全ての設問が空欄のまま提出されているのが'
            '見つかりました（タイムスタンプ等、フォームが自動で埋める列以外に回答が無い行）。'
            'このような行は分岐の無いあらゆる集計表に同じ「未回答」件数として現れます。'
            '除外する行のチェックを確認してください（初期状態は全てチェック済み＝除外）。'
        )
        for row_id in blank_candidates:
            answered = {
                col: str(df.at[row_id, col]) for col in columns
                if str(df.at[row_id, col]).strip() != ''
            }
            preview = '、'.join(f'{k}={v}' for k, v in answered.items()) or '（全設問が空欄）'
            st.checkbox(f'行{row_id}（{preview}）', value=True, key=f'blank_excl_{row_id}')

    st.divider()
    if st.button('✅ この内容で除外行を確定する', type='primary', key='confirm_exclusions'):
        excluded = set(test_selected_row_ids)
        for row_id in mojibake_candidates:
            if st.session_state.get(f'mojibake_excl_{row_id}', True):
                excluded.add(row_id)
        for row_id in blank_candidates:
            if st.session_state.get(f'blank_excl_{row_id}', True):
                excluded.add(row_id)
        st.session_state['excluded_row_ids'] = sorted(excluded)
        st.rerun()

    current_excluded = st.session_state.get('excluded_row_ids', [])
    valid_count = len(df) - len(current_excluded)
    st.metric('プロジェクト用データの有効回答数', f'{valid_count}件',
              delta=f'-{len(current_excluded)}件（除外）' if current_excluded else None)


def _render_initial_rows_table(df: pd.DataFrame, columns: list[str], n: int) -> list[int]:
    """
    先頭n行の全カラムを、約10行分の高さのスクロール窓に表示する。キーワードで絞り込まず
    原文全体をそのまま見せ、テスト投稿だと判断した行を人がクリックして選択する。
    戻り値は選択された行の_row_idリスト（除外対象の確定に使う）。
    """
    initial_df = df.iloc[:n]
    row_ids = initial_df['_row_id'].tolist()
    event = st.dataframe(
        initial_df[['_row_id', *columns]], height=400, width='stretch', hide_index=True,
        on_select='rerun', selection_mode='multi-row', key='initial_rows_table',
        column_config={'_row_id': st.column_config.NumberColumn('_row_id', pinned=True)},
    )
    selected_positions = event.selection.rows if event and event.selection else []
    return [row_ids[i] for i in selected_positions]
