"""記事の取得とテキスト抽出。

- RSS/Atom フィードからの記事一覧取得（feedparser）
- 記事本文の抽出（requests + BeautifulSoup の簡易抽出）

API課金やログインの回避方針：まずRSSで取れるものはRSSで。本文はHTMLを取得して
script/style 等を除いたテキストを使う。ペイウォール記事（日経など）は本文が
取れないことがあるため、その場合はタイトル＋ディスクリプションで判定にまわす。
"""
from __future__ import annotations

import calendar
import re
import urllib.parse
from dataclasses import dataclass, field
from typing import Optional

import feedparser
import requests
from bs4 import BeautifulSoup

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
HEADERS = {"User-Agent": UA, "Accept-Language": "ja,en;q=0.8"}
TIMEOUT = 20
MAX_TEXT_CHARS = 6000  # 判定に渡す本文の上限（トークン節約）


@dataclass
class Article:
    url: str
    title: str = ""
    summary: str = ""      # RSS の概要 or meta description
    text: str = ""         # 抽出した本文
    source: str = ""
    published: str = ""
    published_ts: Optional[float] = None  # 公開日時のepoch（並び替え・鮮度フィルタ用）
    error: Optional[str] = None

    def for_classification(self) -> str:
        """判定に渡すテキスト（タイトル＋本文 or 概要）。"""
        body = self.text or self.summary
        body = body[:MAX_TEXT_CHARS]
        parts = [f"タイトル: {self.title}".strip()]
        if self.source:
            parts.append(f"媒体: {self.source}")
        parts.append(f"本文:\n{body}")
        return "\n".join(parts).strip()


def fetch_feed(feed_url: str, source: str = "", limit: int = 20) -> list[Article]:
    """フィードを取得して Article のリストにする（本文未取得）。

    feedparser.parse(URL) は内部のHTTP取得にタイムアウトが無く、応答の遅いフィードが
    1つでもあると永久にハングする。requests でタイムアウト付き取得してから parse する。
    """
    resp = requests.get(feed_url, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    parsed = feedparser.parse(resp.content)
    out: list[Article] = []
    for entry in parsed.entries[:limit]:
        summary = entry.get("summary", "") or entry.get("description", "")
        summary = _html_to_text(summary)
        out.append(
            Article(
                url=entry.get("link", ""),
                title=_html_to_text(entry.get("title", "")),
                summary=summary,
                source=source or parsed.feed.get("title", ""),
                published=entry.get("published", "") or entry.get("updated", ""),
                published_ts=_entry_ts(entry),
            )
        )
    return out


def _entry_ts(entry) -> "float | None":
    """フィードエントリの公開日時を epoch 秒で返す（取れなければ None）。"""
    st = entry.get("published_parsed") or entry.get("updated_parsed")
    if not st:
        return None
    try:
        return calendar.timegm(st)
    except Exception:  # noqa: BLE001
        return None


def fetch_article_text(article: Article) -> Article:
    """記事URLを取得して本文テキストを埋める。失敗しても error にして返す。"""
    try:
        resp = requests.get(article.url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
    except Exception as e:  # noqa: BLE001
        article.error = f"fetch failed: {e}"
        return article

    soup = BeautifulSoup(resp.text, "html.parser")
    if not article.title:
        if soup.title and soup.title.string:
            article.title = soup.title.string.strip()
    # meta description を summary の補完に
    if not article.summary:
        md = soup.find("meta", attrs={"name": "description"}) or soup.find(
            "meta", attrs={"property": "og:description"}
        )
        if md and md.get("content"):
            article.summary = md["content"].strip()

    # 公開日時（RSSが無い媒体用）。meta から拾えたら埋める。
    if article.published_ts is None:
        for attrs in ({"property": "article:published_time"}, {"name": "pubdate"},
                      {"itemprop": "datePublished"}, {"name": "date"}):
            tag = soup.find("meta", attrs=attrs)
            val = tag.get("content") if tag else None
            if not val:
                continue
            try:
                from datetime import datetime as _dt
                d = _dt.fromisoformat(val.replace("Z", "+00:00"))
                article.published_ts = d.timestamp()
                article.published = article.published or val
                break
            except Exception:  # noqa: BLE001
                continue
    if article.published_ts is None:
        # meta が無い媒体（Newsweek日本版など）は <time datetime> を使う
        for t in soup.find_all("time"):
            val = t.get("datetime")
            if not val:
                continue
            try:
                from datetime import datetime as _dt
                d = _dt.fromisoformat(val.replace("Z", "+00:00"))
                article.published_ts = d.timestamp()
                article.published = article.published or val
                break
            except Exception:  # noqa: BLE001
                continue

    article.text = _extract_main_text(soup)
    if not article.text and not article.summary:
        article.error = "no text extracted (paywall?)"
    return article


def fetch_one(url: str) -> Article:
    """URL単体を取得して本文まで埋める（検証用）。"""
    return fetch_article_text(Article(url=url))


def _extract_main_text(soup: BeautifulSoup) -> str:
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav", "aside", "form"]):
        tag.decompose()
    # article 要素があれば優先、なければ body
    container = soup.find("article") or soup.body or soup
    paragraphs = [p.get_text(" ", strip=True) for p in container.find_all("p")]
    text = "\n".join(p for p in paragraphs if len(p) > 1)
    if not text:  # p が無いページ向けフォールバック
        text = container.get_text("\n", strip=True)
    return _normalize(text)


def _html_to_text(s: str) -> str:
    if not s:
        return ""
    return _normalize(BeautifulSoup(s, "html.parser").get_text(" ", strip=True))


def _normalize(s: str) -> str:
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


# ---------------------------------------------------------------- HTML一覧の収集

# RSSを廃止した媒体は、一覧ページのHTMLから記事リンクを拾う。
# Googleニュース経由だとリダイレクトURLしか得られず本文が取れないため、
# 直接URLを取りにいくことで「タイトルのみ判定」を回避する。
# (サイト名, 一覧URL, 記事リンクの正規表現, ベースURL)
HTML_LISTINGS = [
    ("Forbes JAPAN", "https://forbesjapan.com/", r"^/articles/detail/\d+", "https://forbesjapan.com"),
    ("Newsweek日本版", "https://www.newsweekjapan.jp/", r"^/articles/-/\d+", "https://www.newsweekjapan.jp"),
]


def fetch_listing(list_url: str, pattern: str, base: str, source: str,
                  limit: int = 15) -> list[Article]:
    """一覧ページのHTMLから記事リンクを拾って Article にする（本文は未取得）。
    公開日時はここでは取れないので、判定後に本文側から補完される。"""
    resp = requests.get(list_url, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    pat = re.compile(pattern)
    out: list[Article] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not pat.match(href):
            continue
        url = href if href.startswith("http") else base + href
        if url in seen:
            continue
        seen.add(url)
        title = a.get_text(" ", strip=True)
        out.append(Article(url=url, title=title, source=source))
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------- 本文中リンク

# 記事ではないリンク（ナビ・広告・パズル連載・SNS共有など）を落とす
_LINK_SKIP_URL = re.compile(
    r"/(tag|tags|category|categories|author|authors|topics?|search|newsletter|"
    r"subscribe|subscription|privacy|terms|about|contact|advertis\w*|sitemap|"
    r"video|videos|gallery|photos|podcast|events?|jobs|shop|store)/"
    r"|wordle|/pips|connections|strands|crossword|sudoku|puzzle"
    r"|facebook\.com|twitter\.com|x\.com/|instagram\.com|linkedin\.com|youtube\.com"
    r"|mailto:|javascript:|/feed|\.(jpg|png|gif|pdf|mp4)$",
    re.I,
)
# 記事タイトルになっていないアンカー文字列
_LINK_SKIP_TEXT = re.compile(
    r"^(read more|続きを読む|もっと見る|click here|here|subscribe|sign up|sign in|"
    r"log ?in|share|次へ|前へ|一覧|トップ|home|more|関連記事|詳細|こちら|"
    r"editorial standards|reprints?\s*&?\s*permissions?|forbes|"
    r"privacy|terms|cookie\w*|プライバシー|利用規約)\s*$",
    re.I,
)


def _looks_like_article(url: str) -> bool:
    """記事URLらしいか。著者ページ・規約ページ等を落とすための軽い判定。
    末尾セグメントが十分に長い（見出しスラッグ）か、記事IDらしい数字を含むこと。"""
    path = urllib.parse.urlsplit(url).path.rstrip("/")
    seg = path.rsplit("/", 1)[-1] if "/" in path else path
    return len(seg) >= 20 or bool(re.search(r"\d{4,}", seg))


def inbody_links(article: "Article", limit: int = 12,
                 resolve_titles: bool = False) -> list[tuple[str, str]]:
    """記事本文の中に貼られたリンクを (アンカー文字列, URL) で返す。

    記者が「関連している」と判断して張ったリンクは、こちらのアーカイブ検索では
    出てこない別角度の記事であることが多い。素材パックの幅を広げるために使う。
    ナビゲーション・広告・パズル連載などは除外する。
    """
    if not article.url:
        return []
    try:
        resp = requests.get(article.url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
    except Exception:  # noqa: BLE001
        return []
    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav", "aside", "form"]):
        tag.decompose()
    container = soup.find("article") or soup.body or soup
    base = urllib.parse.urlsplit(article.url)
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for a in container.find_all("a", href=True):
        href = a["href"].strip()
        text = a.get_text(" ", strip=True)
        if not text or len(text) < 12 or _LINK_SKIP_TEXT.match(text):
            continue
        if href.startswith("/"):
            href = f"{base.scheme}://{base.netloc}{href}"
        if not href.startswith("http") or _LINK_SKIP_URL.search(href):
            continue
        if not _looks_like_article(href):
            continue
        key = href.split("?")[0].rstrip("/")
        if key in seen or key == article.url.split("?")[0].rstrip("/"):
            continue
        # 記事っぽいパスか（年月やスラッグを含む）で軽く絞る
        path = urllib.parse.urlsplit(href).path
        if len(path) < 12:
            continue
        seen.add(key)
        out.append((text[:110], href))
        if len(out) >= limit:
            break

    if resolve_titles:
        # アンカー文字列が短い場合はリンク先の <title> を取りに行く
        # （記者が本文中に張るリンクは語句だけのことが多いため）
        fixed: list[tuple[str, str]] = []
        for text, href in out:
            if len(text) >= 28:
                fixed.append((text, href))
                continue
            try:
                r = requests.get(href, headers=HEADERS, timeout=TIMEOUT)
                t = BeautifulSoup(r.text, "html.parser").find("title")
                title = _normalize(t.get_text(" ", strip=True)) if t else ""
                title = re.sub(r"\s*[|｜\-–—]\s*[^|｜\-–—]{1,30}$", "", title)
                fixed.append(((title or text)[:110], href))
            except Exception:  # noqa: BLE001
                fixed.append((text, href))
        out = fixed
    return out
