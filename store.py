"""既読管理（差分取得）とアーカイブの永続化。

- seen.json: 判定済み記事を記録（key=正規化URL）。次回は新着だけ処理する。
- archive.jsonl: 拾った記事を追記（取りこぼし防止の記録）。
チューニング中に作り直したいときは RESET_STATE=1 を付けて実行すると seen を無視する。
"""
from __future__ import annotations

import json
import os
import time

STATE_DIR = os.path.join(os.path.dirname(__file__), "state")
SEEN_PATH = os.path.join(STATE_DIR, "seen.json")
ARCHIVE_PATH = os.path.join(STATE_DIR, "archive.jsonl")
RETENTION_DAYS = 21  # seen に保持する期間（これより古い既読は忘れる）


def load_seen() -> dict:
    """正規化URL -> 記録（title/source/label/ts）。RESET_STATE で空から開始。"""
    if os.environ.get("RESET_STATE"):
        return {}
    try:
        with open(SEEN_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_seen(seen: dict) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    cutoff = time.time() - RETENTION_DAYS * 86400
    pruned = {k: v for k, v in seen.items() if v.get("ts", 0) >= cutoff}
    with open(SEEN_PATH, "w", encoding="utf-8") as f:
        json.dump(pruned, f, ensure_ascii=False, indent=1)


def recent_titles(seen: dict, days: int = 7) -> list[str]:
    """直近 days 日に既読となった記事タイトル（クロスラン重複判定用）。"""
    cutoff = time.time() - days * 86400
    return [v.get("title", "") for v in seen.values() if v.get("ts", 0) >= cutoff]


RECENT_PATH = os.path.join(STATE_DIR, "recent.json")


def load_recent() -> list:
    """digest表示用の「直近ウィンドウ」（拾った記事のdictのリスト）。RESET_STATE で空から。"""
    if os.environ.get("RESET_STATE"):
        return []
    try:
        with open(RECENT_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_recent(items: list, retention_days: int) -> None:
    """公開日(無ければ判定時刻)が retention_days 以内のものだけ残して保存。"""
    os.makedirs(STATE_DIR, exist_ok=True)
    cutoff = time.time() - retention_days * 86400
    pruned = [it for it in items if (it.get("published_ts") or it.get("ts") or 0) >= cutoff]
    with open(RECENT_PATH, "w", encoding="utf-8") as f:
        json.dump(pruned, f, ensure_ascii=False, indent=1)


def append_archive(records: list[dict]) -> None:
    if not records:
        return
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(ARCHIVE_PATH, "a", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
