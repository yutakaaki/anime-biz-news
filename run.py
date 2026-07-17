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
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))


def _fmt_jst(ts, fallback: str = "") -> str:
    """UTC epoch を JST の "YYYY-MM-DD HH:MM JST" に整形。ts無しは fallback。"""
    if not ts:
        return fallback
    return datetime.fromtimestamp(ts, JST).strftime("%Y-%m-%d %H:%M") + " JST"

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
JUDGE_WORKERS = int(os.environ.get("JUDGE_WORKERS", "4")) # 判定を並列実行するワーカー数（高速化）
RUN_TIMEOUT = int(os.environ.get("RUN_TIMEOUT", "900"))   # 実行の時間上限(秒)。超えたら自己強制終了（ハング対策）


def _start_watchdog() -> None:
    """RUN_TIMEOUT 秒を超えたらプロセスを強制終了する（run.py内蔵の確実な安全弁）。"""
    def _kill():
        time.sleep(RUN_TIMEOUT)
        print(f"\n!!! 実行が {RUN_TIMEOUT} 秒を超過したため強制終了（ハング対策）!!!", flush=True)
        os._exit(1)
    threading.Thread(target=_kill, daemon=True).start()


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
    _start_watchdog()
    print("収集中...")
    candidates = collect()
    seen = store.load_seen()
    window = store.load_recent()  # 直近ウィンドウ（採用済みストーリー）
    for it in window:
        it.setdefault("sources", [it.get("source", "")])  # 媒体リスト（話題度の元）
        it["is_new"] = False  # 前回の「新着」印はここでクリア（今回追加分だけを新着にする）
    recent_seen_titles = store.recent_titles(seen, days=7)
    now = time.time()

    # 既読・古い記事・クロスラン重複を仕分け（重複は「媒体加算」して話題度に反映）
    age_cutoff = now - MAX_AGE_DAYS * 86400
    to_judge: list[Article] = []
    n_read = n_dup = n_old = n_buzz = 0
    for art in candidates:
        key = normalize_url(art.url)
        if key in seen:
            n_read += 1
            continue
        if art.published_ts is not None and art.published_ts < age_cutoff:
            n_old += 1
            continue
        # 既に窓にある採用済みストーリーと同一 → その媒体リストに加算（＝話題度が増える）
        hit = next((it for it in window if dedup.is_similar(art.title, it["title"], SIM)), None)
        if hit is not None:
            if art.source and art.source not in hit["sources"]:
                hit["sources"].append(art.source)
            seen[key] = {"title": art.title, "source": art.source, "label": "媒体加算", "ts": now}
            n_buzz += 1
            continue
        # 過去に判定して落とした(対象外)ニュースの別媒体 → 判定せずスキップ
        if any(dedup.is_similar(art.title, t, SIM) for t in recent_seen_titles):
            seen[key] = {"title": art.title, "source": art.source, "label": "重複スキップ", "ts": now}
            n_dup += 1
            continue
        to_judge.append(art)

    print(f"収集 {len(candidates)} 件（既読 {n_read} / 古い {n_old} / 媒体加算 {n_buzz} / 重複スキップ {n_dup} / 新規 {len(to_judge)}）")
    if len(to_judge) > MAX_CLASSIFY:
        print(f"安全弁: 新規の先頭 {MAX_CLASSIFY} 件に絞って判定")
        to_judge = to_judge[:MAX_CLASSIFY]

    print(f"判定中...（並列 {JUDGE_WORKERS} 本）")

    def _judge_one(art: Article):
        """1記事を取得＋判定。結果 (art, Judgment) を返す。失敗時は j=例外/None。"""
        fetch_article_text(art)
        if art.error and not (art.text or art.summary):
            return art, None
        try:
            return art, classify(art.for_classification())
        except Exception as e:  # noqa: BLE001
            return art, e

    # 取得＋判定を並列実行して待ち時間を短縮（結果の反映は逐次で安全に）
    with ThreadPoolExecutor(max_workers=JUDGE_WORKERS) as ex:
        results = list(ex.map(_judge_one, to_judge))

    kept_new: list[dict] = []
    for art, j in results:
        if j is None:
            continue
        if isinstance(j, Exception):
            print(f"  [判定失敗] {art.url}: {j}")
            continue
        seen[normalize_url(art.url)] = {
            "title": art.title, "source": art.source, "label": j.label, "ts": now,
        }
        if j.keep:
            kept_new.append({
                "url": art.url, "title": art.title, "source": art.source,
                "sources": [art.source], "published": art.published,
                "published_ts": art.published_ts, "themes": j.themes, "type": j.type,
                "label": j.label, "confidence": j.confidence, "reason": j.reason,
                "ts": now, "is_new": True,  # 今回の実行で新しく入った記事
            })
            print(f"  [{j.label}] {art.title[:50]}")

    # 窓(媒体加算済み)に新着をマージ。同一URLの再判定は既存の媒体リストを引き継ぐ。
    by_key = {normalize_url(it["url"]): it for it in window}
    for it in kept_new:
        k = normalize_url(it["url"])
        old = by_key.get(k)
        if old:
            for s in old.get("sources", []):
                if s not in it["sources"]:
                    it["sources"].append(s)
        by_key[k] = it
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
        # 全メンバーの媒体リストを統合＝話題度（何媒体が報じたか）
        srcs: list[str] = []
        for m in members:
            for s in (m.get("sources") or [m.get("source", "")]):
                if s and s not in srcs:
                    srcs.append(s)
        rep["sources"] = srcs
        rep["media_count"] = len(srcs)
        rep["also"] = [s for s in srcs if s != rep.get("source", "")]
        rep["is_new"] = any(m.get("is_new") for m in members)  # 1本でも新着なら新着扱い
        # クラスタ内に「深掘り」があれば代表のtypeも深掘りに寄せる（見逃し防止）
        if any(m.get("type") == "深掘り" for m in members):
            rep["type"] = "深掘り"
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


_TYPE_STYLE = {"深掘り": "#b3541e", "速報": "#37507a", "その他": "#888"}


def _badge(text: str, color: str) -> str:
    return (f'<span style="background:{color};color:#fff;padding:2px 8px;border-radius:10px;'
            f'font-size:12px;margin-right:4px">{html.escape(text)}</span>')


def _card(it: dict) -> str:
    label = it.get("label", "")
    itype = it.get("type", "その他")
    mc = it.get("media_count", 1)
    chips = _theme_chips(it.get("themes") or [])
    buzz = _badge(f"🔥{mc}媒体", "#c0392b") if mc >= 2 else _badge(f"{mc}媒体", "#888")
    type_b = _badge(itype, _TYPE_STYLE.get(itype, "#888"))
    label_b = _badge(f"{label}/確信度{it.get('confidence','')}", "#1a7f37" if label == "対象" else "#9a6700")
    others = it.get("also") or []
    also = ""
    if others:
        uniq = "、".join(dict.fromkeys(others))
        also = f'<div style="font-size:12px;color:#888;margin-top:6px">報道媒体: {html.escape(uniq)}</div>'
    when = _fmt_jst(it.get("published_ts"), it.get("published", ""))
    # 新着は色分け（黄色い枠＋淡い背景＋NEWバッジ）。既出は通常のグレー枠。
    is_new = bool(it.get("is_new"))
    new_b = _badge("🆕NEW", "#e8a33d") if is_new else ""
    style = ("border:1px solid #f0c36d;border-left:5px solid #e8a33d;background:#fffdf3;"
             if is_new else "border:1px solid #ddd;")
    return f"""<article style="{style}border-radius:8px;padding:14px;margin:10px 0">
  <div style="font-size:12px;color:#666">{html.escape(it.get("source", ""))} ・ {html.escape(when)}</div>
  <h3 style="margin:6px 0"><a href="{html.escape(it.get("url", ""))}" target="_blank">{html.escape(it.get("title", ""))}</a></h3>
  <div>{new_b}{buzz}{type_b}{chips}{label_b}</div>
  <p style="color:#444;font-size:14px;margin:8px 0 0">{html.escape(it.get("reason", ""))}</p>
  {also}
</article>"""


def _sort_key(x: dict):
    # 公開日時の新しい順（話題度・分野数はバッジで表示）
    return (x.get("published_ts") or 0,)


def render_html(items: list[dict]) -> str:
    now = datetime.now(JST).strftime("%Y-%m-%d %H:%M") + " JST"
    deep = sorted([x for x in items if x.get("type") == "深掘り"], key=_sort_key, reverse=True)
    rest = sorted([x for x in items if x.get("type") != "深掘り"], key=_sort_key, reverse=True)

    def section(title, note, arr):
        if not arr:
            return ""
        body = "\n".join(_card(it) for it in arr)
        return (f'<h2 style="margin:24px 0 4px;font-size:18px">{title}'
                f'<span style="color:#888;font-size:13px;font-weight:normal"> {note}</span></h2>{body}')

    n_new_deep = sum(1 for x in deep if x.get("is_new"))
    n_new_rest = sum(1 for x in rest if x.get("is_new"))
    sections = (
        section("📝 考察ネタ候補（深掘り）", f"{len(deep)}件（🆕新着{n_new_deep}）・新しい順", deep)
        + section("⚡ 速報・その他", f"{len(rest)}件（🆕新着{n_new_rest}）・新しい順", rest)
    )
    body = sections or f"<p>直近{MAX_AGE_DAYS}日間に該当ニュースはありませんでした</p>"
    return f"""<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>コンテンツ×AI×ビジネス ニュース</title></head>
<body style="font-family:system-ui,'Hiragino Sans',sans-serif;max-width:760px;margin:24px auto;padding:0 16px">
<h1>コンテンツ × AI × ビジネス ニュース</h1>
<p style="color:#666">生成: {now} ・ モデル: {html.escape(MODEL)} ・ 直近{MAX_AGE_DAYS}日 {len(items)}件（深掘り{len(deep)}／速報他{len(rest)}）・🆕新着 {n_new_deep + n_new_rest}件</p>
{body}
</body></html>"""


if __name__ == "__main__":
    raise SystemExit(main())
