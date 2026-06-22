"""精度検証用のラベル付きサンプル（ユーザー提供, 2026-06-18）。

gold: "対象" / "対象外"（ユーザーの2値ラベル）。
判定側は 対象/グレー/対象外 の3値を返すので、検証では
{対象, グレー} を「拾う(keep)」、{対象外} を「落とす(drop)」とみなして比較する。
"""

LABELED_EXAMPLES = [
    # --- 正例（対象） ---
    ("https://forbesjapan.com/articles/detail/99442", "対象"),
    ("https://variety.com/2026/film/focus/annecys-mifa-2026-1236782136/", "対象"),
    ("https://variety.com/2026/global/news/overwatch-arnold-tsang-manga-azuki-1236783222/", "対象"),
    ("https://www.billboard-japan.com/special/detail/5323", "対象"),
    ("https://www.cartoonbrew.com/distribution/fox-22-billion-roku-acquisition-263017.html", "対象"),
    ("https://deadline.com/2026/06/box-office-toy-story-5-1236962629/", "対象"),
    ("https://variety.com/2026/film/news/doraemon-the-movie-india-theatrical-debut-1236784704/", "対象"),
    ("https://variety.com/2026/film/news/kpop-demon-hunters-one-year-anniversary-1236783864/", "対象"),
    ("https://animeanime.jp/article/2026/06/15/100063.html", "対象"),
    ("https://0115765.com/archives/190252", "対象"),
    ("https://news.yahoo.co.jp/expert/articles/f55f935e41429f5bcf40e8ff305444deab6bad92", "対象"),
    ("https://www.nikkei.com/article/DGXZQOUB1283Y0S6A610C2000000/", "対象"),
    ("https://variety.com/2026/film/news/streaming-titles-diversity-ucla-hollywood-report-1236783246/", "対象"),
    ("https://variety.com/2026/tv/news/netflix-prime-video-disney-wbd-asian-ip-apac-growth-apos-1236783695/", "対象"),
    # --- 負例（対象外） ---
    ("https://deadline.com/2026/06/jeff-daniels-brendan-fraser-starman-1236960521/", "対象外"),
    ("https://variety.com/2026/film/global/pippi-longstocking-animated-series-studiocanal-heyday-films-1236785957/", "対象外"),
    ("https://0115765.com/archives/191148", "対象外"),
    ("https://animeanime.jp/article/2026/06/19/100217.html", "対象外"),
    ("https://animeanime.jp/article/2026/06/19/100216.html", "対象外"),
    ("https://animeanime.jp/article/2026/06/19/100215.html", "対象外"),
    # run.py 実地テストで判明した追加の境界事例（2026-06-20）
    ("http://animationbusiness.info/archives/17244", "対象外"),  # ドキュメンタリーの資金調達
    ("https://dentsu-ho.com/articles/9584", "対象外"),  # 電通オウンドメディア（自社実績PR）
    # 非アニメコンテンツのノイズ（2026-06-21 digest確認で追加）
    ("https://variety.com/2026/film/global/the-fears-artist-we-wont-get-old-together-transilvania-1236786470/", "対象外"),  # 欧州実写アートフィルムの配給（アニメ無関係）
]
