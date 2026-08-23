"""
form_pdf.py
アンケートフォームPDF（Googleフォームの印刷/PDF保存機能で書き出したもの）からテキストを
抽出し、LLMに設問定義表の初稿（ID・形式・設問文・選択肢・短縮設問文案・短縮選択肢案・
matrix候補・n変化候補）を作らせる。RAWデータの回答パターンには一切頼らない——
Googleフォーム標準の「その他（インライン自由記述）」機能により、回答データだけからは
設計上の選択肢と回答者の自由記述を区別できないケースがあるため（SPEC 5.4.1・6節）。
"""

from __future__ import annotations

import io
import logging
import unicodedata

import pdfplumber

from core.text_normalize import fix_known_font_glitches
from llm_client import call_llm

# pdfminer（pdfplumberの内部実装）は、一部のフォントサブセットでフォントメトリクス情報が
# 欠けている場合に大量のWARNINGログを出す（例:「Could not get FontBBox from font descriptor
# because None cannot be parsed as 4 floats」）。テキスト抽出自体には影響しないため、
# ターミナルを埋め尽くさないようERRORレベル以上のみ表示するよう抑制する。
logging.getLogger('pdfminer').setLevel(logging.ERROR)

QUESTION_DEFINITION_SCHEMA = {
    'type': 'object',
    'properties': {
        'questions': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'format': {
                        'type': 'string',
                        'enum': ['SA', 'MA', 'FA'],
                        'description': 'SA=単一選択（ラジオボタン/ひとつ選んでください）、'
                                       'MA=複数選択（チェックボックス/全て選んでください）、'
                                       'FA=自由記述（選択肢が無く文章で回答する）',
                    },
                    'question_text': {'type': 'string', 'description': 'PDFに書かれている設問文そのまま（省略しない）'},
                    'short_question': {
                        'type': 'string',
                        'description': '一覧表の見出しとして使える短縮版（意味を保ったまま15〜20文字程度が目安）',
                    },
                    'options': {
                        'type': 'array',
                        'description': 'SA/MAのみ。PDFに書かれている選択肢をそのまま、上から順に全て列挙する（FAは空配列）',
                        'items': {
                            'type': 'object',
                            'properties': {
                                'text': {'type': 'string', 'description': '選択肢の原文そのまま'},
                                'short': {
                                    'type': 'string',
                                    'description': 'グラフの軸ラベルとして使える短縮版（意味を保ったまま12〜15文字程度が目安）',
                                },
                            },
                            'required': ['text', 'short'],
                        },
                    },
                    'n_note': {
                        'type': 'string',
                        'description': '設問文に「前問で〇〇とお答えの方にお伺いします」等の条件分岐がある場合、'
                                       '母数が全体から絞り込まれる旨を簡潔に記述する（無ければ空文字列）',
                    },
                },
                'required': ['format', 'question_text', 'short_question', 'options', 'n_note'],
            },
        },
    },
    'required': ['questions'],
}

_PROMPT_TEMPLATE = """以下はアンケートフォーム（Googleフォーム）をPDFに書き出したものから抽出したテキストです。
この内容から、設問を上から順に全て抽出し、構造化してください。

【PDFテキスト】
{pdf_text}

【指示】
- 挨拶文・注意事項・セクションの見出し文（設問そのものではない説明文）は除外すること。
- 設問文・選択肢は省略・要約せず、原文のまま抽出すること（「その他:」と「その他（次問に
  お答えください）」のように似ているが異なる選択肢文言も、書き換えずそのまま区別して残すこと）。
- 形式は、直後に付く指示文で判断すること: 「1 つだけマークしてください。」＝SA、
  「当てはまるものをすべて選択してください。」＝MA、そのような指示文も選択肢も無く
  自由記述欄のみの設問＝FA。
- 選択肢がある設問は、選択肢を上から出現順に全て列挙すること。
- 「（回答の内容） 質問N にスキップします」という記載は分岐（スキップロジック）を表す。
  **この「質問Nにスキップします」という部分は選択肢の実際の回答テキストではなく、PDF側の
  注記にすぎない。選択肢のoptions[].textには含めず、選択肢本体の文言のみを入れること**
  （例:「知らない　質問10にスキップします」という選択肢は、textを「知らない」とする。
  Googleフォームの実際のエクスポートデータには注記部分が含まれないため、textに残すと
  RAWデータの値と一致しなくなり集計が不正確になる）。この記載がある設問、および
  「前問で、〇〇とお答えの方にお伺いします」のように前の設問の回答で対象者が絞り込まれている
  設問は、どの選択肢がどの設問へスキップするかも含めてn_noteに簡潔に記述すること。
- 短縮設問文・短縮選択肢は、この時点での案（人が後で確認・修正する前提のたたき台）でよい。
  回答者の属性に関する設問（例:「性別」「年齢」「年代」「住所」「勤務先住所」「勤務エリア」
  「職業」「業種」「経歴」「職歴」「学歴」「所属学校」「国公立私立」「学年」「交通手段」など）は、
  上記の例のように単語だけの簡潔な形にすること。それ以外の設問も、「あなたは」「〜ですか」
  「〜について」といった会話的・冗長な要素は取り除き、体言止めの簡潔な形にすること
  （例:「〇〇を知っていますか」→「〇〇の認知」、「〇〇についてどう思いますか」→「〇〇への意見」）。
- 設問の並び順（PDFに出現する順）を保つこと。
"""


def extract_pdf_text(data: bytes) -> str:
    """
    PDFバイト列からページ順にテキストを抽出して連結する。GoogleフォームのPDF書き出しでは、
    一部の常用漢字（青・長・西・民・小・革等）が通常のCJK統合漢字ではなく「康熙部首」領域
    （U+2E80-U+2FDF）の見た目だけ同じ文字にフォントエンコードされたり、中点「・」が別の
    句読点に、「戸」が旧字体「戶」になったりする既知の癖があるため、NFKC正規化に加えて
    実データで確認済みの文字化けパターンを補正する（core.text_normalize.fix_known_font_glitches
    ——2026-08-22、これを怠ると設問定義表の選択肢テキストがRAWデータの実際の値と一致せず、
    集計から回答が漏れる実害が出ることが確認された）。ただし全ての文字化けを網羅できている
    保証はない（未知のパターンは今後見つかり次第追加する）。
    """
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        pages = [page.extract_text() or '' for page in pdf.pages]
    text = '\n'.join(pages)
    text = unicodedata.normalize('NFKC', text)
    return fix_known_font_glitches(text)


def build_prompt(pdf_text: str) -> str:
    return _PROMPT_TEMPLATE.format(pdf_text=pdf_text)


def propose_question_definitions(client, pdf_text: str, model: str) -> list[dict] | None:
    """
    LLMにフォームPDFのテキストから設問定義の初稿を作らせる。
    戻り値: [{'format','question_text','short_question','options','n_note'}, ...] または失敗時None。
    """
    if not pdf_text.strip():
        return []
    prompt = build_prompt(pdf_text)
    result = call_llm(client, prompt, QUESTION_DEFINITION_SCHEMA, 'Anthropic', model)
    if not result:
        return None
    return result.get('questions', [])
