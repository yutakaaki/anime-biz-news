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
    # アニメ・業界特化の深掘り/ニュース媒体（低ノイズ）
    ("https://www.animenewsnetwork.com/news/rss.xml", "Anime News Network"),
    ("https://news.animenomics.com/feed", "Animenomics"),
]

# Google ニュース検索クエリ（メインテーマ: コンテンツ × AI × ビジネス）
GOOGLE_QUERIES_JA = [
    # コンテンツ × ビジネス
    "アニメ 興行収入",
    "アニメ 配信 契約",
    "アニメ 製作委員会 出資",
    "アニメ ライセンス 海外",
    "アニメ制作会社 決算 OR 買収",
    "コンテンツ 海外売上 OR 市場規模",
    # コンテンツ × AI
    "生成AI アニメ OR 映像 OR 映画",
    "AI 映像制作 OR アニメ制作",
    "生成AI 著作権 アニメ OR 映画 OR 映像",
    "AI 吹き替え OR 翻訳 アニメ OR 映画",
    "AI エンタメ 投資 OR 資金調達",
    # ペイウォール系・大手新聞を site: 指定で収集（本文は取れないが見出し＋概要で判定・表示）
    "アニメ OR コンテンツ産業 site:nikkei.com",
    "アニメ OR コンテンツ産業 site:business.nikkei.com",
    "アニメ OR コンテンツ産業 site:forbesjapan.com",
    "アニメ OR コンテンツ産業 site:sankei.com",
    "アニメ OR コンテンツ産業 site:yomiuri.co.jp",
    "アニメ OR コンテンツ産業 site:newsweekjapan.jp",
    "アニメ OR 生成AI site:gamebiz.jp",
]
GOOGLE_QUERIES_EN = [
    # コンテンツ × ビジネス
    "anime box office",
    "anime licensing deal",
    "anime streaming rights",
    "anime studio acquisition OR investment",
    # コンテンツ × AI
    "generative AI animation OR anime",
    "AI film production OR video generation studio",
    "generative AI content copyright OR licensing",
    "AI dubbing OR localization film OR anime",
    # コラム/深掘りが多い一般媒体を site: 指定でテーマ絞り収集
    "anime OR animation OR \"generative AI\" site:hollywoodreporter.com",
    "anime OR animation OR \"generative AI\" site:reuters.com",
    "anime OR animation OR \"generative AI\" site:economist.com",
    "anime OR animation OR \"generative AI\" site:forbes.com",
    "anime OR animation OR \"generative AI\" site:newsweek.com",
]


def _label(q: str, lang: str) -> str:
    """digest表示用のソース名。site: クエリは媒体ドメインを見やすく出す。"""
    if "site:" in q:
        dom = q.split("site:", 1)[1].strip()
        return f"Googleニュース({dom})"
    prefix = "Googleニュース" if lang == "ja" else "Google News"
    return f"{prefix}: {q}"


def all_feeds() -> list[tuple[str, str]]:
    feeds = list(DIRECT_FEEDS)
    for q in GOOGLE_QUERIES_JA:
        feeds.append((google_news_rss(q, "ja"), _label(q, "ja")))
    for q in GOOGLE_QUERIES_EN:
        feeds.append((google_news_rss(q, "en"), _label(q, "en")))
    return feeds
