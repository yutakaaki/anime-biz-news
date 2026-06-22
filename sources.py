"""収集元の定義。

2種類：
1. Google ニュース RSS 検索（キーワードがヒットしたものを広く収集。日英対応）
2. 指定サイトの直接フィード（カテゴリ別フィードがあればそれを使う）

直接フィードURLは「候補」。取れない/存在しないものは fetcher 側で握りつぶす。
PoC段階なので少数から。日経はログイン/有料中心のため初期スコープ外。
"""
from __future__ import annotations

import urllib.parse


def google_news_rss(query: str, lang: str = "ja") -> str:
    q = urllib.parse.quote(query)
    if lang == "ja":
        return f"https://news.google.com/rss/search?q={q}&hl=ja&gl=JP&ceid=JP:ja"
    return f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"


# (フィードURL, 媒体名)
DIRECT_FEEDS = [
    ("https://variety.com/feed/", "Variety"),
    ("https://deadline.com/feed/", "Deadline"),
    ("https://www.cartoonbrew.com/feed", "Cartoon Brew"),
    ("https://animeanime.jp/rss/index.rdf", "アニメ！アニメ！"),
    ("https://0115765.com/feed", "アニメ業界ニュース(0115765)"),
    ("http://animationbusiness.info/feed", "アニメーションビジネス・ジャーナル"),
    ("https://branc.jp/feed", "Branc"),
]

# Google ニュース検索クエリ（取りこぼし最小化のため広め）
GOOGLE_QUERIES_JA = [
    "アニメ 興行収入",
    "アニメ 配信 契約",
    "アニメ 製作委員会 出資",
    "アニメ ライセンス 海外",
    "アニメ制作会社 決算 OR 買収",
    "コンテンツ 海外売上 OR 市場規模",
]
GOOGLE_QUERIES_EN = [
    "anime box office",
    "anime licensing deal",
    "anime streaming rights",
    "anime studio acquisition OR investment",
]


def all_feeds() -> list[tuple[str, str]]:
    feeds = list(DIRECT_FEEDS)
    for q in GOOGLE_QUERIES_JA:
        feeds.append((google_news_rss(q, "ja"), f"Googleニュース: {q}"))
    for q in GOOGLE_QUERIES_EN:
        feeds.append((google_news_rss(q, "en"), f"Google News: {q}"))
    return feeds
