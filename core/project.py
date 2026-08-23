"""
project.py
プロジェクトファイル（.json）のシリアライズ/デシリアライズ。
RAWデータ全体（行・列）とクリーニングでの除外行IDを保存し、いつでも除外を見直せるようにする
（除外を確定した後のデータだけを保存すると、後から復活できなくなるため）。
APIキーは保存しない。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

PROJECT_SCHEMA_VERSION = 1

_REQUIRED_KEYS = {
    'schema_version', 'name', 'description',
    'raw_filename', 'raw_encoding', 'columns', 'rows', 'excluded_row_ids',
    'question_definition', 'question_definition_confirmed',
    'cross_grid_checks', 'cross_table_format', 'triple_cross_specs',
    'cross_table_rows', 'cross_plan_confirmed', 'usage_log',
}


def build_project(name: str, description: str, raw_filename: str, raw_encoding: str,
                   columns: list[str], rows: list[dict], excluded_row_ids: list[int],
                   question_definition: list[dict] | None = None,
                   question_definition_confirmed: bool = False,
                   cross_grid_checks: list[list[str]] | None = None,
                   cross_table_format: str = '',
                   triple_cross_specs: list[dict] | None = None,
                   list_cross_attrs: list[str] | None = None,
                   list_cross_targets: list[str] | None = None,
                   list_cross_sort_order: str = '',
                   cross_table_rows: list[dict] | None = None,
                   cross_plan_confirmed: bool = False,
                   usage_log: list[dict] | None = None) -> dict:
    """
    プロジェクトdictを組み立てる。RAWデータ（raw_filename以降）は未取込でもよい
    （新方式ではプロジェクト名だけで開始し、設問定義表→RAW取込の順に進められるため）。
    rowsの各要素は'_row_id'キー（安定な行番号）を持つ。
    question_definitionは設問定義表のエントリ一覧（core/question_definition.py参照）、
    question_definition_confirmedは設問定義表が確定（保護）済みかどうか、
    cross_grid_checksは集計指定インターフェイスのチェック済みセル一覧（core/cross_plan.py
    のgrid_checks_from_df/apply_grid_checks参照、[行ID, 列ID]のリスト）、
    cross_table_formatは集計表形式（「％＆実数表」/「％実数別表」）、
    triple_cross_specsはトリプルクロス指定表の入力内容（最大3セット）、
    list_cross_attrs/list_cross_targets/list_cross_sort_orderは一覧型クロス集計指定表の入力内容
    （Excel出力専用）——属性設問のセット（表左側、全ての対象設問に共通、最大5件）と対象設問の
    リスト（表頭、1つにつき1表、最大5件）を別々に持つ（個々のペアではない、SPEC 5.3.3参照）。
    後から追加した項目のため必須キーには含めず、旧バージョンのプロジェクトファイル読み込み時は
    空欄扱いになる（deserialize_project参照）、
    cross_table_rowsはクロス集計指定表の内容（属性・対象・グラフ種別・AIコメントのオンオフを含む）、
    cross_plan_confirmedは集計指定表が確定（保護）済みかどうか、
    usage_logはLLM API使用量の作業ログ（core/usage_log.py参照、プロジェクトログタブで表示）。
    """
    return {
        'schema_version': PROJECT_SCHEMA_VERSION,
        'saved_at': datetime.now(timezone.utc).isoformat(),
        'name': name,
        'description': description,
        'raw_filename': raw_filename,
        'raw_encoding': raw_encoding,
        'columns': columns,
        'rows': rows,
        'excluded_row_ids': excluded_row_ids,
        'question_definition': question_definition or [],
        'question_definition_confirmed': question_definition_confirmed,
        'cross_grid_checks': cross_grid_checks or [],
        'cross_table_format': cross_table_format,
        'triple_cross_specs': triple_cross_specs or [],
        'list_cross_attrs': list_cross_attrs or [],
        'list_cross_targets': list_cross_targets or [],
        'list_cross_sort_order': list_cross_sort_order,
        'cross_table_rows': cross_table_rows or [],
        'cross_plan_confirmed': cross_plan_confirmed,
        'usage_log': usage_log or [],
    }


def serialize_project(project: dict) -> str:
    """プロジェクトdictをJSON文字列に変換する"""
    return json.dumps(project, ensure_ascii=False, indent=2)


def deserialize_project(raw: str) -> dict:
    """
    JSON文字列からプロジェクトdictを復元する。
    必須キーの欠損やJSON構文エラーの場合は分かりやすいメッセージのValueErrorを送出する。
    """
    try:
        project = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f'JSONとして読み込めませんでした: {e}') from e

    if not isinstance(project, dict):
        raise ValueError('プロジェクトファイルの形式が不正です（オブジェクトではありません）')

    missing = _REQUIRED_KEYS - project.keys()
    if missing:
        raise ValueError(f'プロジェクトファイルに必要な項目がありません: {", ".join(sorted(missing))}')

    if project['schema_version'] > PROJECT_SCHEMA_VERSION:
        raise ValueError(
            f'このプロジェクトファイルは新しいバージョン（schema_version={project["schema_version"]}）で'
            f'保存されています。アプリを更新してください。'
        )

    return project
