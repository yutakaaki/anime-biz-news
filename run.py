"""フルパイプライン（PoC）：収集 → 既読除外 → 二次判定 → 重複集約 → ローカルWeb出力。

使い方:
    export ANTHROPIC_API_KEY=sk-ant-...
    python3 run.py
    open outputs/digest.html

挙動:
- 既読管理: 判定済み記事は state/seen.json に記録し、次回は新着だけを判定・表示する（差分取得）。
- 重複集約: 同一ニュースが複数媒体から来た場合、タイトル類似で1本に集約し「他N媒体でも報道」。
- アーカイブ: 拾った記事を state/archive.jsonl に追記。
- チューニングで作り直したいときは RESET_STATE=1 を付けて実行（既読を無視）。
- 一次フィルタ（埋め込み）はPoCでは省略し、新着候補を全て Claude 判定にかける。
"""
from __future__ import annotations

import html
import os
import time
import urllib.parse
from datetime import datetime

import dedup
import store
from classifier import MODEL, classify
from fetcher import Article, fetch_article_text, fetch_feed
from sources import all_feeds

OUT_DIR = os.path.join(os.path.dirname(__file__), "outputs")
PER_FEED_LIMIT = int(os.environ.get("PER_FEED_LIMIT", "15"))
MAX_CLASSIFY = int(os.environ.get("MAX_CLASSIFY", "60"))  # 1回の新規判定件数の上限（安全弁）
SIM = float(os.environ.get("DEDUP_SIM", "0.35"))          # タイトル類似のしきい値
MAX_AGE_DAYS = int(os.environ.get("MAX_AGE_DAYS", "2"))   # これより古い記事は除外（既定=昨日と今日）


def normalize_url(url: str) -> str:
    """トラッキングパラメータを落として完全重複を除去するためのキー。"""
    p = urllib.parse.urlsplit(url)
    keep = [
        (k, v)
        for k, v in urllib.parse.parse_qsl(p.query)
        if not k.lower().startswith(("utm_", "fbclid", "gclid"))
    ]
    query = urllib.parse.urlencode(keep)
    path = p.path.rstrip("/")
    return urllib.parse.urlunsplit((p.scheme, p.netloc.lower(), path, query, ""))


def collect() -> list[Article]:
    """各フィードを取得し、ラウンドロビンで均等に混ぜて返す（URL完全重複は除去）。"""
    per_feed: list[list[Article]] = []
    for feed_url, source in all_feeds():
        try:
            entries = fetch_feed(feed_url, source=source, limit=PER_FEED_LIMIT)
        except Exception as e:  # noqa: BLE001
            print(f"  [feed失敗] {source}: {e}")
            continue
        per_feed.append([a for a in entries if a.url])
        print(f"  {source}: {len(entries)} 件")

    seen_urls: set[str] = set()
    articles: list[Article] = []
    for i in range(max((len(f) for f in per_feed), default=0)):
        for feed in per_feed:
            if i >= len(feed):
                continue
            art = feed[i]
            key = normalize_url(art.url)
            if key in seen_urls:
                continue
            seen_urls.add(key)
            articles.append(art)
    return articles


def main() -> int:
    print("収集中...")
    candidates = collect()
    seen = store.load_seen()
    recent = store.recent_titles(seen, days=7)
    now = time.time()

    # 既読・古い記事・クロスラン重複を除外して、新規だけを判定対象に
    age_cutoff = now - MAX_AGE_DAYS * 86400
    to_judge: list[Article] = []
    n_read = n_dup = n_old = 0
    for art in candidates:
        key = normalize_url(art.url)
        if key in seen:
            n_read += 1
            continue
        if art.published_ts is not None and art.published_ts < age_cutoff:
            # 公開日が判り、かつ古い記事は除外（判定もしない）。
            n_old += 1
            continue
        if any(dedup.is_similar(art.title, t, SIM) for t in recent):
            # 既出ニュースの別ソース。判定せず既読として記録。
            seen[key] = {"title": art.title, "source": art.source, "label": "重複スキップ", "ts": now}
            n_dup += 1
            continue
        to_judge.append(art)

    print(f"収集 {len(candidates)} 件（既読 {n_read} / 古い {n_old} / 重複スキップ {n_dup} / 新規 {len(to_judge)}）")
    if len(to_judge) > MAX_CLASSIFY:
        print(f"安全弁: 新規の先頭 {MAX_CLASSIFY} 件に絞って判定")
        to_judge = to_judge[:MAX_CLASSIFY]

    kept_new: list[dict] = []
    print("判定中...")
    for art in to_judge:
        fetch_article_text(art)
        if art.error and not (art.text or art.summary):
            continue
        try:
            j = classify(art.for_classification())
        except Exception as e:  # noqa: BLE001
            print(f"  [判定失敗] {art.url}: {e}")
            continue
        seen[normalize_url(art.url)] = {
            "title": art.title, "source": art.source, "label": j.label, "ts": now,
        }
        if j.keep:
            kept_new.append({
                "url": art.url, "title": art.title, "source": art.source,
                "published": art.published, "published_ts": art.published_ts,
                "themes": j.themes, "label": j.label, "confidence": j.confidence,
                "reason": j.reason, "ts": now,
            })
            print(f"  [{j.label}] {art.title[:50]}")

    # 直近ウィンドウ（直近 MAX_AGE_DAYS 日の該当ニュース）に新着をマージし、常に全部表示する。
    # → 実行が新着0でも空にならず、取りこぼしにも強い。
    by_key = {normalize_url(it["url"]): it for it in store.load_recent()}
    for it in kept_new:
        by_key[normalize_url(it["url"])] = it  # 新しい判定で上書き
    window = list(by_key.values())

    clusters = _aggregate_dicts(window)
    os.makedirs(OUT_DIR, exist_ok=True)
    out_path = os.path.join(OUT_DIR, "digest.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(render_html(clusters))

    store.save_recent(window, MAX_AGE_DAYS)
    store.append_archive(kept_new)
    store.save_seen(seen)
    print(f"\n新着 {len(kept_new)} 件 / 表示 {len(clusters)} 件（直近{MAX_AGE_DAYS}日・集約後） → {out_path}")
    return 0


def _aggregate_dicts(items: list[dict]) -> list[dict]:
    """直近ウィンドウの記事(dict)をタイトル類似で集約。代表1本＋他媒体名(also)を付ける。"""
    titles = [it["title"] for it in items]
    out: list[dict] = []
    for group in dedup.cluster(titles, SIM):
        members = [items[k] for k in group]
        # 代表: 対象を優先 → 該当分野(themes)が多い → 理由が長い（情報量が多い）順
        members.sort(key=lambda m: (
            0 if m.get("label") == "対象" else 1,
            -len(m.get("themes") or []),
            -len(m.get("reason") or ""),
        ))
        rep = dict(members[0])
        rep["also"] = [m.get("source", "") for m in members[1:] if m.get("source")]
        out.append(rep)
    return out


_THEME_COLOR = {"コンテンツ": "#5a3e9e", "AI": "#0b6e8c", "ビジネス": "#1a7f37"}


def _theme_chips(themes: list) -> str:
    chips = []
    for t in themes:
        c = _THEME_COLOR.get(t, "#666")
        chips.append(
            f'<span style="background:{c};color:#fff;padding:2px 8px;border-radius:10px;'
            f'font-size:12px;margin-right:4px">{html.escape(t)}</span>'
        )
    return "".join(chips)


def render_html(items: list[dict]) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    # 該当分野(themes)が多い順（3分野=最優先）→ 同数なら公開日の新しい順。
    items = sorted(
        items,
        key=lambda x: (len(x.get("themes") or []), x.get("published_ts") or 0),
        reverse=True,
    )
    cards = []
    for it in items:
        label = it.get("label", "")
        badge = "#1a7f37" if label == "対象" else "#9a6700"
        others = it.get("also") or []
        also = ""
        if others:
            uniq = "、".join(dict.fromkeys(others))
            also = f'<div style="font-size:12px;color:#888;margin-top:6px">他{len(others)}媒体でも報道: {html.escape(uniq)}</div>'
        chips = _theme_chips(it.get("themes") or [])
        cards.append(
            f"""<article style="border:1px solid #ddd;border-radius:8px;padding:14px;margin:10px 0">
  <div style="font-size:12px;color:#666">{html.escape(it.get("source", ""))} ・ {html.escape(it.get("published", ""))}</div>
  <h3 style="margin:6px 0"><a href="{html.escape(it.get("url", ""))}" target="_blank">{html.escape(it.get("title", ""))}</a></h3>
  <div>{chips}<span style="background:{badge};color:#fff;padding:2px 8px;border-radius:10px;font-size:12px">{label} / 確信度{html.escape(it.get("confidence", ""))}</span></div>
  <p style="color:#444;font-size:14px;margin:8px 0 0">{html.escape(it.get("reason", ""))}</p>
  {also}
</article>"""
        )
    body = "\n".join(cards) or f"<p>直近{MAX_AGE_DAYS}日間に該当ニュースはありませんでした</p>"
    return f"""<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>コンテンツ×AI×ビジネス ニュース</title></head>
<body style="font-family:system-ui,'Hiragino Sans',sans-serif;max-width:760px;margin:24px auto;padding:0 16px">
<h1>コンテンツ × AI × ビジネス ニュース</h1>
<p style="color:#666">生成: {now} ・ モデル: {html.escape(MODEL)} ・ 直近{MAX_AGE_DAYS}日 {len(items)} 件（分野が多い順）</p>
{body}
</body></html>"""


if __name__ == "__main__":
    raise SystemExit(main())
