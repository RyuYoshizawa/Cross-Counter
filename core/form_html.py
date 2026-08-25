"""
form_html.py
アンケートフォームHTML（Googleフォームの編集画面をブラウザの「名前を付けて保存」
（ウェブページ、HTMLのみ）で保存したもの）から設問定義表の初稿を作る。LLMを使わず、
DOM構造から機械的・決定的に抽出する——PDF＋LLM方式で繰り返し見つかった、フォント
レンダリング起因の文字化け（core/text_normalize.py参照）が原理的に起こらないため
（2026-08-22、ユーザーとの合意事項：「①専用の抽出システムを持つ、②不具合は調査・修復、
③それでもダメならPDF方式」の①にあたる）。

**使う構造的な手がかり**（Googleの内部CSSクラス名は実装の詳細でありGoogleのUI更新で
変わり得るため、できるだけ避ける。role属性・aria-label・jsnameは比較的安定していると
考えられる——特にaria-labelは実際のユーザー向け日本語UI文言そのものであり、Googleが
気軽に変えるものではない）:
- 設問カードの境界: jsname="Mxc6Ne"（実データで実際の設問数と一致することを確認済み）
- 設問文: カード内の aria-label="質問" (role="textbox") のうち最初の1件
- 必須: カード内の aria-label="必須" (role="checkbox") のaria-checked
- 選択肢: カード内の role="list" の直接の子要素のうち、role="radio"/role="checkbox"を
  含むものそれぞれ1件（**カード全体ではなくrole="list"の中だけを見ることが重要**——
  「質問の形式」ドロップダウン自体がradio styleの項目を大量に持つため、カード全体を見ると
  実際の選択肢と誤認する。role="list"が1つも無く、かつaria-label="質問"のrole="textbox"が
  2件以上あれば自由記述（FA、2件目は回答欄のプレースホルダー）とみなす。role="list"が
  複数ある場合はグリッド形式の可能性が高く、非対応として扱う
- セクション見出し: aria-label="セクション タイトル（省略可）" / "説明（省略可）"
  (role="textbox")。スキップロジックの説明文言（「前問で、〇〇とお答えの方に
  お伺いします」等）がここに入っていることが多く、直後の設問のn_noteに使う
  （PDF方式で使っていたのと同じヒューリスティック）

**分岐先ナビゲーションのノイズ除去**: 選択肢のテキストには「次のセクションに進む」等の
分岐先表示が地の文として混入するため、既知のパターンを除去する（_BRANCH_NOISE_PATTERNS）。

**非対応の設問形式**（均等目盛・グリッド・日付・時刻・ファイルアップロード）は取り込まず、
件数を警告として返す（ユーザーとの合意事項、2026-08-22）。
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

from core.question_definition import FORMAT_FA, FORMAT_MA, FORMAT_SA
from core.text_normalize import fix_known_font_glitches
from llm_client import call_llm

# HTML方式は構造そのものが既に確定情報として取れるため、短縮設問文・短縮選択肢の案作成
# だけをLLMに頼む——PDF方式のような構造判断そのものは不要なので、低コストのHaikuで十分
# （ユーザーとの合意事項、2026-08-22）。
SHORT_LABEL_MODEL = 'claude-haiku-4-5'

_SHORT_LABEL_SCHEMA = {
    'type': 'object',
    'properties': {
        'questions': {
            'type': 'array',
            'description': '入力の設問と同じ順序・同じ件数で返すこと',
            'items': {
                'type': 'object',
                'properties': {
                    'short_question': {
                        'type': 'string',
                        'description': '一覧表・グラフの見出しとして使える短縮版（意味を保ったまま15〜20文字程度が目安）',
                    },
                    'options': {
                        'type': 'array',
                        'description': '入力の選択肢と同じ順序・同じ件数（自由記述設問は空配列）',
                        'items': {
                            'type': 'object',
                            'properties': {
                                'short': {
                                    'type': 'string',
                                    'description': 'グラフの軸ラベルとして使える短縮版（意味を保ったまま12〜15文字程度が目安）',
                                },
                            },
                            'required': ['short'],
                        },
                    },
                },
                'required': ['short_question', 'options'],
            },
        },
    },
    'required': ['questions'],
}

_SHORT_LABEL_PROMPT = """以下はアンケートの設問一覧です（形式は確定済み、選択肢も確定済み）。
各設問について、一覧表・グラフの見出しとして使える短縮版を作成してください。

【設問一覧】
{questions_text}

【指示】
- short_questionは設問文の意味を保ったまま15〜20文字程度に短縮すること。
- 回答者の属性に関する設問（例:「性別」「年齢」「年代」「住所」「勤務先住所」「勤務エリア」
  「職業」「業種」「経歴」「職歴」「学歴」「所属学校」「国公立私立」「学年」「交通手段」など）は、
  上記の例のように単語だけの簡潔な形にすること（説明的な言い回しを付け足さない）。
- それ以外の設問も、「あなたは」「〜ですか」「〜について」といった会話的・冗長な要素は取り除き、
  体言止めの簡潔な形にすること（例:「〇〇を知っていますか」→「〇〇の認知」、
  「〇〇についてどう思いますか」→「〇〇への意見」）。
- 各選択肢のshortは、グラフの軸ラベルとして使える12〜15文字程度に短縮すること
  （既に十分短い選択肢はそのままでよい）。
- 設問・選択肢の順番と件数は入力と完全に一致させること（追加・削除・並べ替えをしないこと）。
- 自由記述（選択肢が無い）設問は、optionsを空配列のまま返すこと。
"""

_QUESTION_CARD_JSNAME = 'Mxc6Ne'
_QUESTION_LABEL = '質問'
_REQUIRED_LABEL = '必須'
_ANSWER_PREVIEW_LABEL = '説明'  # 記述式/パラグラフテキスト設問の回答プレビュー欄（正確に完全一致、
                                # セクションの「説明（省略可）」とは別のaria-label）
_SECTION_TITLE_LABEL = 'セクション タイトル（省略可）'
_SECTION_DESC_LABEL = '説明（省略可）'

_SKIP_NOTE_HINTS = ('とお答えの方に', '前問で')

_BRANCH_NOISE_PATTERNS = [
    re.compile(r'次のセクションに進む'),
    re.compile(r'セクション\s*\d+\s*.*?に移動'),
    re.compile(r'フォームを送信'),
]


def parse_form_html(html_text: str) -> tuple[list[dict], list[str]]:
    """
    保存されたフォーム編集画面のHTMLから設問一覧を抽出する。
    戻り値: (questions, warnings)。questionsはcore.form_pdf.propose_question_definitionsの
    戻り値と同じ形（format/question_text/short_question/options/n_note/required）で、短縮設問文・
    短縮選択肢は空文字列のまま返す（呼び出し側でLLMに作らせるか、人が手入力する）。
    """
    soup = BeautifulSoup(html_text, 'html.parser')

    def is_marker(tag) -> bool:
        if tag.get('jsname') == _QUESTION_CARD_JSNAME:
            return True
        label = tag.get('aria-label')
        return label in (_SECTION_TITLE_LABEL, _SECTION_DESC_LABEL) and tag.get('role') == 'textbox'

    markers = soup.find_all(is_marker)

    questions: list[dict] = []
    warnings: list[str] = []
    pending_note = ''

    for marker in markers:
        if marker.get('jsname') == _QUESTION_CARD_JSNAME:
            question, issue = _parse_question_card(marker, pending_note)
            pending_note = ''
            if issue:
                warnings.append(issue)
                continue
            if question:
                questions.append(question)
        else:
            text = fix_known_font_glitches(marker.get_text(' ', strip=True))
            if text and any(hint in text for hint in _SKIP_NOTE_HINTS):
                pending_note = text

    return questions, warnings


def _parse_question_card(card, pending_note: str) -> tuple[dict | None, str | None]:
    """1設問カードを解析する。(question, None) または (None, 警告文) を返す"""
    question_labels = card.find_all(attrs={'aria-label': _QUESTION_LABEL, 'role': 'textbox'})
    if not question_labels:
        return None, None  # 設問文が取れない要素はカード扱いしない（安全側）

    title = fix_known_font_glitches(question_labels[0].get_text(' ', strip=True))
    if not title:
        return None, None

    required_el = card.find(attrs={'aria-label': _REQUIRED_LABEL, 'role': 'checkbox'})
    required = bool(required_el) and required_el.get('aria-checked') == 'true'

    lists = card.find_all(attrs={'role': 'list'})
    if len(lists) == 1:
        fmt, options = _parse_choice_list(lists[0])
        if fmt is None:
            return None, f'「{_short(title)}」: 選択肢の形式判定に失敗したためスキップしました。'
    elif len(lists) > 1:
        return None, f'「{_short(title)}」: グリッド形式など複数の選択肢リストを持つ設問のためスキップしました（非対応）。'
    else:
        has_answer_preview = card.find(attrs={'aria-label': _ANSWER_PREVIEW_LABEL, 'role': 'textbox'}) is not None
        if has_answer_preview:
            fmt, options = FORMAT_FA, []
        else:
            return None, f'「{_short(title)}」: 非対応の設問形式のためスキップしました（均等目盛・日付・時刻・ファイルのアップロードのいずれかの可能性があります）。'

    return {
        'format': fmt,
        'question_text': title,
        'short_question': '',
        'options': options,
        'n_note': pending_note,
        'required': required,
    }, None


def _parse_choice_list(list_el) -> tuple[str | None, list[dict]]:
    has_radio = list_el.find(attrs={'role': 'radio'}) is not None
    has_checkbox = list_el.find(attrs={'role': 'checkbox'}) is not None
    if has_radio and not has_checkbox:
        fmt = FORMAT_SA
    elif has_checkbox and not has_radio:
        fmt = FORMAT_MA
    else:
        return None, []

    options: list[dict] = []
    for child in list_el.find_all(True, recursive=False):
        if child.find(attrs={'role': 'radio'}) is None and child.find(attrs={'role': 'checkbox'}) is None:
            continue
        text = child.get_text('\n', strip=True)
        for pattern in _BRANCH_NOISE_PATTERNS:
            text = pattern.sub('', text)
        text = fix_known_font_glitches(text.strip())
        if text:
            options.append({'text': text, 'short': ''})
    return fmt, options


def _short(text: str, limit: int = 30) -> str:
    return text if len(text) <= limit else f'{text[:limit]}…'


def _build_short_label_prompt(questions: list[dict]) -> str:
    lines = []
    for i, q in enumerate(questions, 1):
        lines.append(f'{i}. [{q["format"]}] {q["question_text"]}')
        for opt in q['options']:
            lines.append(f'   - {opt["text"]}')
    return _SHORT_LABEL_PROMPT.format(questions_text='\n'.join(lines))


# 1回のLLM呼び出し全設問分をまとめて依頼すると、設問数・選択肢数が多いフォームでは
# max_tokens上限（llm_client.py、8192）を出力が超えて打ち切られ、全設問分の短縮ラベルが
# 一切作成されない実害が実データで発生した（2026-08-22）。設問数でバッチに分割して
# 複数回呼び出すことでこれを避ける——バッチサイズは典型的な設問1件あたりの出力量
# （short_question+選択肢ごとのshort、選択肢が多い設問もあるため余裕を持たせる）から
# 経験的に決めた保守的な値。
_SHORT_LABEL_BATCH_SIZE = 8


def propose_short_labels(client, questions: list[dict], model: str = SHORT_LABEL_MODEL) -> tuple[list[dict], list[str]]:
    """
    設問一覧（parse_form_htmlの戻り値と同じ形）に対し、短縮設問文・短縮選択肢だけをLLMに
    作らせる（構造自体は既に確定しているため、この用途には低コストのHaikuで十分）。
    _SHORT_LABEL_BATCH_SIZE件ずつに分割して呼び出し、結果を結合する。一部のバッチだけ
    失敗した場合もそのバッチの設問だけ短縮ラベル無しのまま返す（全体を失敗扱いにしない
    ——人が後で個別に埋められるので、無理に信頼できないデータを使わない）。
    戻り値: (設問一覧, 警告文のリスト)。失敗したバッチが無ければ警告は空リスト。
    """
    if not questions:
        return questions, []

    updated: list[dict] = []
    warnings: list[str] = []
    for start in range(0, len(questions), _SHORT_LABEL_BATCH_SIZE):
        batch = questions[start:start + _SHORT_LABEL_BATCH_SIZE]
        labeled_batch = _propose_short_labels_batch(client, batch, model)
        if labeled_batch is None:
            updated.extend(batch)
            first, last = start + 1, start + len(batch)
            warnings.append(
                f'{first}〜{last}番目の設問（{len(batch)}件）の短縮ラベル作成に失敗したため、'
                '空欄のままにしています（人が後で入力できます）。'
            )
        else:
            updated.extend(labeled_batch)
    return updated, warnings


def _propose_short_labels_batch(client, questions: list[dict], model: str) -> list[dict] | None:
    """propose_short_labelsの1バッチ分。失敗・件数不一致時はNoneを返す。"""
    prompt = _build_short_label_prompt(questions)
    result = call_llm(client, prompt, _SHORT_LABEL_SCHEMA, 'Anthropic', model)
    if not result:
        return None
    labels = result.get('questions', [])
    if len(labels) != len(questions):
        return None

    updated = []
    for q, label in zip(questions, labels):
        new_q = dict(q)
        new_q['short_question'] = label.get('short_question', '')
        label_opts = label.get('options', [])
        new_opts = [
            {'text': opt['text'], 'short': label_opt.get('short', '')}
            for opt, label_opt in zip(q['options'], label_opts)
        ]
        if len(new_opts) < len(q['options']):
            new_opts += q['options'][len(new_opts):]
        new_q['options'] = new_opts
        updated.append(new_q)
    return updated
