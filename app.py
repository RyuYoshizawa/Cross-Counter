"""
app.py
Cross_Counter: 薄いオーケストレーター。page_config・サイドバー呼び出し・タブ振り分けのみを担う。
分析ロジックはcore/、画面はui/に分離。
"""

from pathlib import Path

import streamlit as st

from ui import (
    auth,
    sidebar,
    tab_charts,
    tab_crosstab_result,
    tab_crosstab_spec,
    tab_data_export,
    tab_project_log,
    tab_question_definition,
)

APP_DIR = Path(__file__).parent

st.set_page_config(page_title='Cross Counter GF', page_icon='📊', layout='wide')
st.logo(str(APP_DIR / 'CC_logo.png'), size='large')

auth.require_login()

settings = sidebar.render_sidebar()

if st.session_state.get('project') is None:
    st.info('サイドバーでプロジェクト名を入力するとプロジェクトが始まります。')
    st.stop()

columns = settings['columns']
entries = st.session_state.get('question_definition', [])
cross_table_rows = st.session_state.get('cross_table_rows', [])
triple_cross_specs = st.session_state.get('triple_cross_specs', [])
list_cross_attrs = st.session_state.get('list_cross_attrs', [])
list_cross_targets = st.session_state.get('list_cross_targets', [])
list_cross_sort_order = st.session_state.get('list_cross_sort_order', '')
cross_plan_confirmed = st.session_state.get('cross_plan_confirmed', False)
table_format = st.session_state.get('cross_table_format', '')

tabs = st.tabs(['設問定義・RAWデータ確認', '集計指定', '集計表', 'グラフ', 'データ抽出', 'プロジェクトログ'])

with tabs[0]:
    tab_question_definition.render(columns, settings['rows'], settings['raw_filename'], settings['raw_encoding'],
                                    settings['excluded_row_ids'], settings['api_key'])
with tabs[1]:
    tab_crosstab_spec.render()
with tabs[2]:
    tab_crosstab_result.render(columns, settings['rows'], settings['excluded_row_ids'], entries,
                                cross_table_rows, triple_cross_specs, list_cross_attrs, list_cross_targets,
                                list_cross_sort_order, cross_plan_confirmed, table_format, settings['api_key'])
with tabs[3]:
    tab_charts.render()
with tabs[4]:
    tab_data_export.render(columns, settings['rows'], settings['excluded_row_ids'], entries)
with tabs[5]:
    tab_project_log.render()
