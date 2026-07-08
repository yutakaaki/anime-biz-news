"""Claude による二次判定。

- 構造化出力（output_config.format / json_schema）で label/confidence/reason を取得
- 安定する system プロンプト（ルーブリック＋few-shot）はプロンプトキャッシュ対象
- モデルは既定で claude-opus-4-8。コスト調整したい場合は環境変数 MODEL で
  claude-sonnet-4-6 / claude-haiku-4-5 などに変更可（精度はユーザー判断で）。
"""
from __future__ import annotations  # noqa: F404 (Python 3.9 で X | None 注釈を有効化)

import json
import os
from dataclasses import dataclass

import anthropic

from rubric import OUTPUT_SCHEMA, SYSTEM_PROMPT

# 本番モデルは haiku（PoC検証で20本中100%・取りこぼし0・ノイズ0、opus同等で約1/20コスト）。
# 精度を上げたい場合は MODEL=claude-opus-4-8 等で上書き可。
MODEL = os.environ.get("MODEL", "claude-haiku-4-5")
_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    """ANTHROPIC_API_KEY を環境変数から読む。キー未設定なら呼び出し時にだけ失敗。

    クラウド(GitHub Actions)からの接続は断続的に失敗することがあるため、
    リトライ回数を増やし（指数バックオフで粘る）、タイムアウトも長めにする。
    """
    global _client
    if _client is None:
        _client = anthropic.Anthropic(max_retries=2, timeout=60.0)
    return _client


def _thinking_param(model: str):
    """モデルごとの思考設定。None なら思考なし。

    haiku は思考を無効化（出力トークンを大幅削減し、APIレート制限の回避＋高速化）。
    環境変数 HAIKU_THINK に数値を入れると、その budget で思考を有効化できる。
    adaptive 思考は opus/sonnet 系のみ対応。
    """
    if "haiku" in model:
        budget = int(os.environ.get("HAIKU_THINK", "0"))
        return {"type": "enabled", "budget_tokens": budget} if budget > 0 else None
    return {"type": "adaptive"}


@dataclass
class Judgment:
    themes: list          # ["コンテンツ","AI","ビジネス"] の部分集合
    label: str            # 対象 / グレー / 対象外
    confidence: str       # 高 / 中 / 低
    reason: str

    @property
    def keep(self) -> bool:
        """取りこぼし最小化：対象・グレーは拾う。"""
        return self.label in ("対象", "グレー")


_SYSTEM = [
    {
        "type": "text",
        "text": SYSTEM_PROMPT,
        "cache_control": {"type": "ephemeral"},  # 記事ごとに再利用（プロンプトキャッシュ）
    }
]
_OUTPUT_CONFIG = {"format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}}


def _extract_json(resp) -> dict | None:
    """応答から最初のテキストブロック(JSON)を取り出す。思考のみで本文が無ければ None。"""
    for block in resp.content:
        if block.type == "text" and block.text.strip():
            return json.loads(block.text)
    return None


def classify(article_text: str) -> Judgment:
    messages = [{"role": "user", "content": f"次の記事を判定してください。\n\n{article_text}"}]

    kwargs = dict(
        model=MODEL, max_tokens=1024, system=_SYSTEM,
        messages=messages, output_config=_OUTPUT_CONFIG,
    )
    tp = _thinking_param(MODEL)
    if tp is not None:  # 思考ありのモデルは出力枠を広げる
        kwargs["thinking"] = tp
        kwargs["max_tokens"] = 4096
    resp = _get_client().messages.create(**kwargs)
    data = _extract_json(resp)

    if data is None:
        # 思考が長引いて本文(JSON)が出なかった等。思考なしで一度だけリトライ（黙って落とさない）。
        resp = _get_client().messages.create(
            model=MODEL,
            max_tokens=1024,
            system=_SYSTEM,
            messages=messages,
            output_config=_OUTPUT_CONFIG,
        )
        data = _extract_json(resp)

    if data is None:
        raise RuntimeError(f"判定JSONを取得できません (stop_reason={resp.stop_reason})")

    return Judgment(
        themes=data.get("themes", []),
        label=data["label"],
        confidence=data["confidence"],
        reason=data["reason"],
    )
