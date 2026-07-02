"""タイトル類似によるクロスソース重複集約（追加ライブラリ不要の軽量版）。

同一ニュースが複数媒体から流入する（例: 「Ninja Hattori 8 Lions TV Asahi ライセンス契約」が
英語3媒体）。日英をまたいだ集約は埋め込みが要るため将来対応。ここは同一言語の言い換え
見出しを、有意トークンの Jaccard 類似で束ねる。
"""
from __future__ import annotations

import re
import unicodedata

# ありふれて区別に効かない語（英）。媒体名やニュース一般語も弱く除去。
_STOP = {
    "a", "an", "the", "and", "or", "of", "to", "for", "in", "on", "at", "as", "by",
    "is", "are", "be", "with", "from", "into", "over", "after", "new", "news",
    "latest", "com", "co", "jp", "anime", "first",
    # 一般的すぎて固有名にならない語（box office 等のノイズ対策）
    "box", "office", "weekend", "report", "says", "video",
}
# 日本語の助詞・一般語（bigramノイズ低減用）
_JA_STOP = {"する", "した", "して", "こと", "これ", "それ", "という", "など", "また"}


def _strip_source(title: str) -> str:
    """末尾の「 - 媒体名」「（媒体名）」等を弱く除去（媒体名は重複判定のノイズ）。"""
    title = re.sub(r"\s[-–—|]\s*[^-–—|]{1,30}$", "", title)
    title = re.sub(r"[（(][^（）()]{1,20}[）)]\s*$", "", title)
    return title


def tokens(title: str) -> set[str]:
    """有意トークン集合：英数語(2文字以上,ストップ語除く) + CJK文字bigram。"""
    t = unicodedata.normalize("NFKC", _strip_source(title)).lower()
    words = {w for w in re.findall(r"[a-z0-9]{2,}", t) if w not in _STOP}
    cjk = re.findall(r"[぀-ヿ一-鿿]", t)
    bigrams = {cjk[i] + cjk[i + 1] for i in range(len(cjk) - 1)}
    bigrams -= _JA_STOP
    return words | bigrams


def similarity(a: str, b: str) -> float:
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


OVERLAP_MIN = 0.45  # 重なり係数（小さい方の集合基準）のしきい値
MIN_SHARED = 3      # 重なり係数で集約する際に必要な共有トークン数（一般語のみの誤マージ防止）


def should_merge(a: str, b: str, threshold: float = 0.5) -> bool:
    """同一ニュースとみなすか。Jaccardが低くても、言い換え見出しを救うため
    「小さい方の集合に対する重なり（overlap係数）が高く、共有語が十分」なら集約する。
    """
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return False
    inter = len(ta & tb)
    if inter == 0:
        return False
    if inter / len(ta | tb) >= threshold:          # 通常のJaccard
        return True
    # 共有する語が多く、片方にほぼ含まれる（言い換え見出しの同一ニュース）
    overlap = inter / min(len(ta), len(tb))
    if overlap >= OVERLAP_MIN and inter >= MIN_SHARED and min(len(ta), len(tb)) >= 4:
        return True
    # 4文字以上の固有名（英数語）を2つ以上共有＝同一の出来事（例: MIXI＋Runway）。
    # 1エンティティ由来（toy story 等）では誤マージしないよう、一般語は _STOP で除外済み。
    strong = [t for t in (ta & tb) if t.isascii() and t.isalnum() and len(t) >= 4]
    return len(strong) >= 2


def cluster(titles: list[str], threshold: float = 0.5) -> list[list[int]]:
    """貪欲法でタイトルを近接クラスタにまとめ、各クラスタのインデックス列を返す。"""
    n = len(titles)
    used = [False] * n
    clusters: list[list[int]] = []
    for i in range(n):
        if used[i]:
            continue
        group = [i]
        used[i] = True
        for j in range(i + 1, n):
            if used[j]:
                continue
            if should_merge(titles[i], titles[j], threshold):
                group.append(j)
                used[j] = True
        clusters.append(group)
    return clusters


def is_similar(a: str, b: str, threshold: float = 0.5) -> bool:
    return should_merge(a, b, threshold)
