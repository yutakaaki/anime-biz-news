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
