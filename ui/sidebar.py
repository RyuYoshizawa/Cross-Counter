"""
sidebar.py
設定サイドバー：ブランディング（姉妹アプリAfter Coder/Word_Counterと共通のKOTONOHA意匠）、
LLM設定、プロジェクト名・分析データの特徴、アンケートフォームHTML/PDF・RAWファイルの取込、
プロジェクトファイルのダウンロード・アップロード。
"""

from __future__ import annotations

import streamlit as st

from core.cross_plan import apply_grid_checks, build_grid_dataframe, gridable_questions, grid_checks_from_df
from core.ingest import read_raw_file
from core.project import build_project, deserialize_project, serialize_project
from ui.auth import render_logout_button

# 新規プロジェクト開始時にクリアすべきsession_stateキー（APIキーは意図的に含めない）
_PROJECT_RESET_KEYS = [
    'raw_uploader', '_loaded_raw_file_id', 'raw_columns', 'raw_rows',
    'raw_filename', 'raw_encoding', 'excluded_row_ids',
    '_native_other_processed_report', '_native_other_dismissed_ids',
    '_loaded_project_file_id', 'new_project_name', 'new_project_description',
    'question_definition', 'question_definition_confirmed', 'question_definition_source',
    'form_pdf_uploader', '_loaded_form_pdf_file_id', 'form_pdf_bytes', 'form_pdf_name',
    'form_html_uploader', '_loaded_form_html_file_id', 'form_html_bytes', 'form_html_name',
    'cross_grid_df', 'cross_grid_questions_key', 'cross_grid_version', 'cross_table_ai_version',
    'cross_table_format', 'triple_cross_specs', 'list_cross_attrs', 'list_cross_targets',
    'list_cross_sort_order', 'cross_table_rows', 'cross_plan_confirmed',
    'crosstab_results', 'triple_cross_results', 'list_cross_results', 'crosstab_issues', 'usage_log',
    'data_export_add_serial',
    # 12はui/tab_data_export.pyの_MAX_ROWSと合わせること
    *(f'data_export_q_{i}' for i in range(12)),
    *(f'data_export_opt_{i}' for i in range(12)),
    *(f'data_export_header_{i}' for i in range(12)),
]

# 姉妹アプリ（After Coder）のサイドバー見出しと同じCSSクラス・数値を踏襲し、
# KOTONOHAブランドとしての見た目を揃える（ユーザー指定のレイアウト変更、2026-08-21）。
_TITLE_CSS = """
<style>
.cc-sidebar-title-wrap { margin-top: -10px; }
.cc-sidebar-title-jp   { font-size: 15px !important; font-weight: bold; color: #ffe5b4; margin: 0; white-space: nowrap; }
.cc-sidebar-title-en   { font-size: 40px !important; font-weight: bold; color: #72C6EF; margin: 2px 0 0 0; }
</style>
"""


def render_sidebar() -> dict:
    # プロジェクトファイル復元は、下のnew_project_name/new_project_description等の
    # ウィジェットがこの関数内で先に描画されてしまう前（＝次の実行の一番最初）に行う必要が
    # ある。ウィジェット描画後にst.session_state[key]へ直接代入するとStreamlitAPIException
    # になるため（設問手動追加フォームで踏んだのと同じ制約）、アップロード検知時は復元データを
    # 一時キーに退避してst.rerun()し、次の実行のこのタイミングで反映する。
    pending_restore = st.session_state.pop('_pending_project_restore', None)
    if pending_restore is not None:
        _restore_project_state(pending_restore)

    st.sidebar.markdown(_TITLE_CSS, unsafe_allow_html=True)
    st.sidebar.markdown(
        "<div class='cc-sidebar-title-wrap'>"
        "<p class='cc-sidebar-title-jp'>アンケートクロス集計支援ツール</p>"
        "<p class='cc-sidebar-title-en'>Cross Counter GF</p>"
        "</div>",
        unsafe_allow_html=True,
    )
    st.sidebar.markdown('*KOTONOHA by Marketing Junction*')
    st.sidebar.divider()

    st.sidebar.header('⚙️ 設定')
    api_key = st.sidebar.text_input(
        'APIキー', type='password', placeholder='sk-ant-...', key='api_key',
        help='Anthropic ConsoleでAPIキーを取得してください。設問定義表の初稿作成（PDF方式）・'
             '短縮設問文の作成（HTML方式）・AIコメント作成で使用します。保存はセッション中のみで、'
             'プロジェクトファイルには含まれません。',
    )
    st.sidebar.text_input(
        'プロジェクト名', placeholder='例：R8年度 教員向けアンケート', key='new_project_name',
        help='プロジェクトファイル名やレポート見出しにも使われます。',
    )
    st.sidebar.text_area(
        '分析データの特徴', placeholder='例：青森県の小中高校教員を対象とした教育改革に関するアンケートです。'
        '回答者は教員・校長・教頭・学校職員です。',
        height=120, key='new_project_description',
        help='任意項目です。調査内容や目的、調査対象者など、データの特徴を記入しておくと後で見返す際に'
             '役立ちます。',
    )

    st.sidebar.divider()
    _render_form_html_upload(api_key)
    _render_form_pdf_upload(api_key)
    _render_raw_upload(api_key)

    _sync_or_create_project()

    st.sidebar.divider()
    st.sidebar.subheader('プロジェクトファイル')
    _render_project_file_actions(api_key)

    st.sidebar.divider()
    with st.sidebar:
        render_logout_button()

    return {
        'columns': st.session_state.get('raw_columns', []),
        'rows': st.session_state.get('raw_rows', []),
        'raw_filename': st.session_state.get('raw_filename', ''),
        'raw_encoding': st.session_state.get('raw_encoding', ''),
        'excluded_row_ids': st.session_state.get('excluded_row_ids', []),
        'api_key': api_key,
    }


def _render_form_html_upload(api_key: str) -> None:
    """
    アップロードされたHTMLのバイト列をsession_stateに置くだけで、実際の解析はui/tab_question_
    definition.py側（メイン画面）で行う（PDFアップロードと同じ分担方針）。HTML方式は
    Googleフォームの編集画面をブラウザで「名前を付けて保存」（ウェブページ、HTMLのみ）した
    ものを使う——LLMを使わずDOM構造から機械的に抽出するため、PDF方式で繰り返し見つかった
    フォント文字化けが原理的に起こらない（2026-08-22、ユーザーとの合意事項で新設。PDF方式は
    「うまく解析できない場合の最後の手段」として引き続き利用可能）。
    """
    uploaded = st.sidebar.file_uploader(
        'アンケートフォームデータ（HTML・推奨）', type=['html', 'htm'], key='form_html_uploader',
        help='Googleフォームの編集画面をブラウザで「名前を付けて保存」→「ウェブページ、HTMLのみ」'
             'で保存したファイルを指定してください。文字化けの心配が無く、PDF方式より確実です。',
    )
    if uploaded is None or uploaded.file_id == st.session_state.get('_loaded_form_html_file_id'):
        return
    if not api_key:
        st.sidebar.warning('先にAPIキーを入力してください。')
        return

    st.session_state['_loaded_form_html_file_id'] = uploaded.file_id
    st.session_state['form_html_bytes'] = uploaded.read()
    st.session_state['form_html_name'] = uploaded.name
    st.session_state['question_definition'] = []
    st.session_state['question_definition_confirmed'] = False
    st.rerun()


def _render_form_pdf_upload(api_key: str) -> None:
    """
    アップロードされたPDFのバイト列をsession_stateに置くだけで、実際のテキスト抽出・LLM呼び出しは
    ui/tab_question_definition.py側（メイン画面）で行う——スピナー・エラー表示をメイン画面に
    出したいのと、サイドバーは入力の受け口に徹する方針のため。HTML方式でうまく解析できない
    場合の最後の手段として維持する（2026-08-22、ユーザーとの合意事項）。
    """
    uploaded = st.sidebar.file_uploader(
        'アンケートフォームデータ（PDF・最後の手段）', type=['pdf'], key='form_pdf_uploader',
        help='Googleフォームの印刷/PDF保存機能で書き出したPDFを指定してください。HTML方式が'
             'うまくいかない場合にお使いください。',
    )
    if uploaded is None or uploaded.file_id == st.session_state.get('_loaded_form_pdf_file_id'):
        return
    if not api_key:
        st.sidebar.warning('先にAPIキーを入力してください。')
        return

    st.session_state['_loaded_form_pdf_file_id'] = uploaded.file_id
    st.session_state['form_pdf_bytes'] = uploaded.read()
    st.session_state['form_pdf_name'] = uploaded.name
    st.session_state['question_definition'] = []
    st.session_state['question_definition_confirmed'] = False
    st.rerun()


def _render_raw_upload(api_key: str) -> None:
    """
    Googleフォーム標準「その他」インライン自由記述（has_native_other、SPEC 5.4.1）の確認は、
    ここでは行わない——当初はRAWファイルを新規選択した瞬間だけ判定していたが、アンケート
    フォームデータとRAWデータをほぼ同時にアップロードすると、判定時点でまだ設問定義表が
    出来ておらず判定が漏れる実例が見つかった（2026-08-22実装、2026-08-23同日中に実データで
    発覚）。アップロード順に依存させず、タブ側（ui/tab_question_definition.pyの
    _render_raw_data_check）で描画のたびに毎回判定する設計に変更した。RAWアップロード自体は
    常にその場で即確定する、他の項目と同じシンプルな挙動に戻している。

    **設問定義表が無くてもRAWをアップロードできる（2026-08-25、一時的にブロックしていたが撤回）**:
    「必須未回答候補」機能（設問定義の「必」マークが無いと判定できないため、取り込み順を
    「設問定義→RAW」に固定していた）はヘビーだと判断され廃止した（分岐する必須設問ごとに
    前提条件を個別設定しないと誤検出が絶えないため）。この機能が唯一の存在理由だったため、
    RAWアップロードのブロックも撤回し、アップロード順に依存しない元の設計（form-vs-RAW、
    2026-08-22/23）に戻した。
    """
    uploaded = st.sidebar.file_uploader(
        'RAWデータファイル（CSV/Excel）', type=['csv', 'xlsx', 'xls'], key='raw_uploader',
        help='Googleフォームのエクスポート形式（1行目=設問文、2行目以降=回答者ごとの行）を想定しています。',
    )
    if uploaded is None or uploaded.file_id == st.session_state.get('_loaded_raw_file_id'):
        return
    if not api_key:
        st.sidebar.warning('先にAPIキーを入力してください。')
        return

    st.session_state['_loaded_raw_file_id'] = uploaded.file_id
    try:
        df, encoding = read_raw_file(uploaded.name, uploaded.read())
    except Exception as e:
        st.sidebar.error(f'読み込みに失敗しました: {e}')
        return

    df = df.reset_index(drop=True)
    df.insert(0, '_row_id', df.index)
    st.session_state['raw_columns'] = [c for c in df.columns if c != '_row_id']
    st.session_state['raw_rows'] = df.to_dict('records')
    st.session_state['raw_filename'] = uploaded.name
    st.session_state['raw_encoding'] = encoding
    st.session_state['excluded_row_ids'] = []
    st.rerun()


def _render_project_file_actions(api_key: str) -> None:
    project = st.session_state.get('project')

    if project is not None:
        st.sidebar.download_button(
            '💾 プロジェクトファイルをダウンロード', data=serialize_project(project),
            file_name=f'{project["name"] or "project"}.json', mime='application/json',
            key='download_project', width='stretch',
        )
        # 設問データ・RAWデータの両方が取り込まれた時点で、いつでも復元できるよう
        # プロジェクトファイルのダウンロードを促す（ユーザーとの合意事項、2026-08-23）。
        has_form = bool(st.session_state.get('form_html_bytes') or st.session_state.get('form_pdf_bytes'))
        has_raw = bool(st.session_state.get('raw_rows'))
        if has_form and has_raw:
            st.sidebar.info('設問データ・RAWデータの取り込みが完了しました。念のためプロジェクトファイルをダウンロードしておくことをお勧めします。')

    project_file = st.sidebar.file_uploader(
        'プロジェクトファイルをアップロード（.json）', type=['json'], key='project_file_uploader',
    )
    if project_file is not None and project_file.file_id != st.session_state.get('_loaded_project_file_id'):
        if not api_key:
            st.sidebar.warning('先にAPIキーを入力してください。')
            return
        st.session_state['_loaded_project_file_id'] = project_file.file_id
        try:
            loaded = deserialize_project(project_file.read().decode('utf-8'))
        except ValueError as e:
            st.sidebar.error(f'読み込みに失敗しました: {e}')
        else:
            st.session_state['_pending_project_restore'] = loaded
            st.rerun()

    if project is not None:
        if st.sidebar.button('🆕 新規開始', key='new_project_button', width='stretch'):
            for key in _PROJECT_RESET_KEYS:
                st.session_state.pop(key, None)
            st.session_state['project'] = None
            st.rerun()


def _restore_project_state(project: dict) -> None:
    st.session_state['new_project_name'] = project['name']
    st.session_state['new_project_description'] = project['description']
    st.session_state['raw_columns'] = project['columns']
    st.session_state['raw_rows'] = project['rows']
    st.session_state['raw_filename'] = project['raw_filename']
    st.session_state['raw_encoding'] = project['raw_encoding']
    st.session_state['excluded_row_ids'] = project['excluded_row_ids']
    st.session_state['question_definition'] = project.get('question_definition', [])
    st.session_state['question_definition_confirmed'] = project.get('question_definition_confirmed', False)
    st.session_state['cross_table_format'] = project.get('cross_table_format', '')
    st.session_state['triple_cross_specs'] = project.get('triple_cross_specs', [])
    st.session_state['list_cross_attrs'] = project.get('list_cross_attrs', [])
    st.session_state['list_cross_targets'] = project.get('list_cross_targets', [])
    st.session_state['list_cross_sort_order'] = project.get('list_cross_sort_order', '')
    st.session_state['cross_table_rows'] = project.get('cross_table_rows', [])
    st.session_state['cross_plan_confirmed'] = project.get('cross_plan_confirmed', False)
    st.session_state['usage_log'] = project.get('usage_log', [])
    # 集計指定インターフェイスの方眼は、復元した設問定義表から作り直した上でチェック済み
    # セルだけを反映する（設問構成が保存時と変わっていても、一致するセルだけ復元されればよい）。
    questions = gridable_questions(st.session_state['question_definition'])
    if questions:
        grid_df = build_grid_dataframe(questions)
        st.session_state['cross_grid_df'] = apply_grid_checks(grid_df, project.get('cross_grid_checks', []))
        st.session_state['cross_grid_questions_key'] = tuple(q['id'] for q in questions)
        st.session_state['cross_grid_version'] = st.session_state.get('cross_grid_version', 0) + 1
    # projectそのものは、_sync_or_create_projectが次のレンダリングで再構築する


def _sync_or_create_project() -> None:
    """
    プロジェクト名が入力された時点でプロジェクトを新規作成し、以降は毎レンダリング最新状態に
    同期する。RAWデータは未取込でもよい（設問定義表をRAWデータより先に作れる新方式のため）。
    プロジェクト名・概要は常時表示の単一の入力欄（new_project_name/new_project_description）を
    そのまま使うため、作成後の名称変更もこのフィールドを直接編集するだけでよい。
    """
    project = st.session_state.get('project')
    name = st.session_state.get('new_project_name', '').strip()
    description = st.session_state.get('new_project_description', '').strip()
    columns = st.session_state.get('raw_columns', [])
    rows = st.session_state.get('raw_rows', [])
    excluded_row_ids = st.session_state.get('excluded_row_ids', [])
    question_definition = st.session_state.get('question_definition', [])
    question_definition_confirmed = st.session_state.get('question_definition_confirmed', False)
    cross_grid_df = st.session_state.get('cross_grid_df')
    cross_grid_checks = grid_checks_from_df(cross_grid_df) if cross_grid_df is not None else []
    cross_table_format = st.session_state.get('cross_table_format', '')
    triple_cross_specs = st.session_state.get('triple_cross_specs', [])
    list_cross_attrs = st.session_state.get('list_cross_attrs', [])
    list_cross_targets = st.session_state.get('list_cross_targets', [])
    list_cross_sort_order = st.session_state.get('list_cross_sort_order', '')
    cross_table_rows = st.session_state.get('cross_table_rows', [])
    cross_plan_confirmed = st.session_state.get('cross_plan_confirmed', False)
    usage_log = st.session_state.get('usage_log', [])

    if project is None:
        if name:
            st.session_state['project'] = build_project(
                name=name, description=description,
                raw_filename=st.session_state.get('raw_filename', ''),
                raw_encoding=st.session_state.get('raw_encoding', ''),
                columns=columns, rows=rows, excluded_row_ids=excluded_row_ids,
                question_definition=question_definition,
                question_definition_confirmed=question_definition_confirmed,
                cross_grid_checks=cross_grid_checks, cross_table_format=cross_table_format,
                triple_cross_specs=triple_cross_specs,
                list_cross_attrs=list_cross_attrs, list_cross_targets=list_cross_targets,
                list_cross_sort_order=list_cross_sort_order,
                cross_table_rows=cross_table_rows,
                cross_plan_confirmed=cross_plan_confirmed, usage_log=usage_log,
            )
            st.rerun()
        return

    project.update({
        'name': name, 'description': description,
        'columns': columns, 'rows': rows, 'excluded_row_ids': excluded_row_ids,
        'question_definition': question_definition,
        'question_definition_confirmed': question_definition_confirmed,
        'cross_grid_checks': cross_grid_checks, 'cross_table_format': cross_table_format,
        'triple_cross_specs': triple_cross_specs,
        'list_cross_attrs': list_cross_attrs, 'list_cross_targets': list_cross_targets,
        'list_cross_sort_order': list_cross_sort_order,
        'cross_table_rows': cross_table_rows,
        'cross_plan_confirmed': cross_plan_confirmed, 'usage_log': usage_log,
    })
