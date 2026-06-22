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
            if similarity(titles[i], titles[j]) >= threshold:
                group.append(j)
                used[j] = True
        clusters.append(group)
    return clusters


def is_similar(a: str, b: str, threshold: float = 0.5) -> bool:
    return similarity(a, b) >= threshold
