"""素材パック生成: 1本の記事について、note考察の materials をアーカイブから組み立てる。

狙い: ChatGPT等に「記事1本」だけを渡すと材料が足りず、モデルが一般論で隙間を埋めて
論理飛躍が起きる。そこで過去1800本超のアーカイブから
  - 同じ話題の過去報道（時系列＝話がどう動いてきたか）
  - 記事に出てくる数字
  - 同じ件を他媒体がどう報じたか（＝論点の違い）
を抽出し、マークダウン1枚にまとめる。API は呼ばない（無料・数秒）。

使い方:
    python3 dossier.py                # 直近の深掘り候補を一覧表示
    python3 dossier.py 3              # 一覧の3番の記事で素材パックを作る
    python3 dossier.py <URL>          # URL指定
    python3 dossier.py "キーワード"    # 見出し検索で最初に当たったもの
"""
from __future__ import annotations

import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timedelta, timezone

import store
from run import normalize_url

JST = timezone(timedelta(hours=9))
OUT_DIR = "outputs"
TOP_RELATED = 18  # 関連記事の最大掲載数

# ---------------------------------------------------------------- 抽出ヘルパ

# 作品名・固有名詞になりやすい塊
_QUOTED = re.compile(r"['‘\"“『「【]([^'’\"”』」】]{2,40})['’\"”』」】]")
_KATAKANA = re.compile(r"[ァ-ヴー]{3,}")
_KANJI = re.compile(r"[一-鿿]{2,}")
_ASCII_PROPER = re.compile(r"\b[A-Z][A-Za-z0-9&.\-]{3,}\b")

# 一般語すぎて固有名詞にならないもの
_ENT_STOP = {
    "アニメ", "アニメーション", "コンテンツ", "ニュース", "ランキング", "オリジナル",
    "シリーズ", "プロジェクト", "サービス", "スタジオ", "メディア", "ビジネス",
    "The", "This", "That", "With", "From", "What", "When", "Why", "How", "New",
    "Anime", "Animation", "News", "Box", "Office", "Video", "Report", "Says",
    "映画", "作品", "制作", "発表", "配信", "放送", "記事", "今年", "今後", "世界",
    "日本", "業界", "市場", "企業", "会社", "情報",
}

# 数字（金額・部数・率など）
_NUM_PATTERNS = [
    r"[0-9][0-9,\.]*\s*(?:[兆億万千百]\s*)*円",
    r"\$[0-9][0-9,\.]*\s*(?:million|billion|M|B)?",
    r"[0-9][0-9,\.]*\s*(?:[兆億万千百]\s*)*(?:ドル|部|人|件|本|話|点|社|作品)",
    r"[0-9][0-9,\.]*\s*(?:%|％|パーセント|ポイント)",
    r"[0-9][0-9,\.]*\s*(?:million|billion|percent)",
    r"(?:前年比|前期比|同期比)\s*[0-9][0-9,\.]*\s*(?:%|％|割|倍)?",
]
_NUM_RE = re.compile("|".join(_NUM_PATTERNS), re.I)


def entities(text: str) -> set[str]:
    """固有名詞らしい塊を取り出す（関連記事の判定に使う）。
    末尾の「 - 媒体名」は先に落とす（媒体名で結合してしまうのを防ぐ）。"""
    t = unicodedata.normalize("NFKC", text or "")
    t = re.sub(r"\s[-–—|]\s*[^-–—|]{1,30}$", "", t)
    out: set[str] = set()
    for m in _QUOTED.findall(t):
        out.add(m.strip())
    out |= set(_KATAKANA.findall(t))
    out |= set(_KANJI.findall(t))
    out |= set(_ASCII_PROPER.findall(t))
    return {e for e in out if e not in _ENT_STOP and len(e) >= 2}


def numbers(text: str) -> list[str]:
    seen, out = set(), []
    for m in _NUM_RE.findall(text or ""):
        s = m if isinstance(m, str) else "".join(m)
        s = s.strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _fmt(ts) -> str:
    return datetime.fromtimestamp(ts, JST).strftime("%m/%d") if ts else "??/??"


def _when(r: dict):
    """公開日時。無ければ収集時刻で代替（アーカイブ初期の記事は published_ts が無い）。"""
    return r.get("published_ts") or r.get("ts")


def _fmt_full(ts) -> str:
    return datetime.fromtimestamp(ts, JST).strftime("%Y-%m-%d") if ts else "日付不明"


# ---------------------------------------------------------------- データ読み

def load_archive() -> list[dict]:
    """アーカイブをURL重複なしで読む（新しい記録を優先）。"""
    path = os.path.join("state", "archive.jsonl")
    by_url: dict[str, dict] = {}
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            u = r.get("url")
            if not u:
                continue
            by_url[normalize_url(u)] = r
    return list(by_url.values())


def pick_target(arg: str | None, window: list[dict], archive: list[dict]) -> dict | None:
    pool = window + archive
    if not arg:
        return None
    if arg.isdigit():
        cands = candidates(window)
        i = int(arg) - 1
        return cands[i] if 0 <= i < len(cands) else None
    if arg.startswith("http"):
        key = normalize_url(arg)
        return next((r for r in pool if normalize_url(r.get("url", "")) == key), None)
    low = arg.lower()
    return next((r for r in pool if low in (r.get("title", "") or "").lower()), None)


def candidates(items: list[dict]) -> list[dict]:
    """ネタ候補の正順（digest・素材パック・下書きで共通）。
    深掘りを新しい順に並べ、続けて複数媒体が報じた速報を話題度順に並べる。
    ※必ず集約後(_aggregate_dicts)のリストを渡すこと。集約前だと digest の
      表示と番号がずれる。"""
    deep = sorted([x for x in items if x.get("type") == "深掘り"],
                  key=lambda x: -(_when(x) or 0))
    hot = sorted([x for x in items if x.get("type") != "深掘り"
                  and len(x.get("sources") or []) >= 2],
                 key=lambda x: (-len(x.get("sources") or []), -(_when(x) or 0)))
    return deep + hot


def number_map(items: list[dict]) -> dict:
    """{正規化URL: 候補番号} を返す（digestのバッジ表示用）。"""
    return {normalize_url(c.get("url", "")): i
            for i, c in enumerate(candidates(items), 1)}


# ---------------------------------------------------------------- 関連記事

def related(target: dict, archive: list[dict], limit: int = TOP_RELATED) -> list[tuple[float, dict]]:
    """同じ話題の過去記事を、固有名詞の共有度（IDF重み付き）で探す。
    dedup（同一ニュース判定）より緩く『同じテーマを扱った別の記事』を拾う。

    IDF: アーカイブ全体で頻出する語（映画・興行収入・アニメ等）は重みを下げ、
    希少な語（作品名・企業名・人名）を重視する。手作業のストップ語リストに頼らず、
    「その話題に固有の語で結びついた記事」だけが上位に来る。"""
    import math
    from collections import Counter

    t_key = normalize_url(target.get("url", ""))
    t_ents = entities((target.get("title") or "") + " " + (target.get("reason") or ""))
    if not t_ents:
        return []

    # 文書頻度（各記事のentity集合を1回ずつ数える）
    ents_cache: list[tuple[dict, set[str]]] = []
    df: Counter = Counter()
    for r in archive:
        e = entities((r.get("title") or "") + " " + (r.get("reason") or ""))
        ents_cache.append((r, e))
        for x in e:
            df[x] += 1
    N = max(len(archive), 1)

    def idf(term: str) -> float:
        return math.log(N / (1 + df.get(term, 0)))

    scored: list[tuple[float, dict]] = []
    seen_titles: set[str] = set()
    for r, r_ents in ents_cache:
        if normalize_url(r.get("url", "")) == t_key or not r_ents:
            continue
        shared = t_ents & r_ents
        if not shared:
            continue
        # 希少語の一致だけを評価。ありふれた語しか共有していない記事は自然に沈む。
        score = sum(idf(s) * (1 + min(len(s), 8) / 8) for s in shared)
        # 弱い結合を捨てる: 希少語を2つ以上共有、または単独でも十分に希少
        strong = [x for x in shared if idf(x) >= 4.0]
        if score < 6.0 or not (len(strong) >= 2 or (strong and score >= 9.0)):
            continue
        # 同一見出しの重複（別URLで再配信）を除く
        tkey = (r.get("title") or "")[:40]
        if tkey in seen_titles:
            continue
        seen_titles.add(tkey)
        scored.append((score, r))
    scored.sort(key=lambda x: -x[0])
    return scored[:limit]


# ---------------------------------------------------------------- 出力

def build_markdown(target: dict, rel: list[tuple[float, dict]]) -> str:
    title = target.get("title", "")
    ts = target.get("published_ts")
    src_list = target.get("sources") or [target.get("source", "")]
    body_nums = numbers(title + " " + (target.get("reason") or ""))

    rel_items = [r for _, r in rel]
    rel_sorted = sorted(rel_items, key=lambda x: (_when(x) or 0))

    L: list[str] = []
    # 全文をそのままChatGPT等に貼る前提なので、指示を先頭に置く（材料より前に読ませる）
    L.append("以下は1つのニュースと、その話題に関する過去報道の一次材料です。")
    L.append("この材料の範囲だけで、note記事の構成案を作ってください。")
    L.append("")
    L.append("**制約**")
    L.append("- 材料にない事実や推測を足さないこと（足す場合は「推測」と明記）")
    L.append("- 各見出しに、根拠となる材料（日付・数字・媒体）を紐づけること")
    L.append("- 主張は1本に絞り、時系列または対比で展開すること")
    L.append("- 見出しの数字が宣伝目的の枕（累計部数・過去の興収など）の場合は主軸に据えないこと")
    L.append("")
    L.append("---")
    L.append("")
    L.append(f"# 素材パック: {title}")
    L.append("")
    L.append(f"- **公開**: {_fmt_full(ts)}")
    L.append(f"- **媒体**: {'、'.join(s for s in src_list if s)}")
    L.append(f"- **URL**: {target.get('url','')}")
    L.append(f"- **分野**: {'/'.join(target.get('themes') or [])} ／ **種別**: {target.get('type','')}")
    if target.get("reason"):
        L.append(f"- **収集ツールの判定理由**: {target['reason']}")
    L.append("")

    L.append("## 1. この記事に出てくる数字")
    if body_nums:
        for n in body_nums:
            L.append(f"- {n}")
    else:
        L.append("- （見出し・要約からは数字を抽出できず。本文を確認してください）")
    L.append("")

    L.append("## 2. 同じ話題の過去報道（時系列）")
    L.append("")
    if rel_sorted:
        L.append("この話がどう動いてきたか。考察の骨格はここから作れます。")
        L.append("")
        L.append("| 日付 | 媒体 | 見出し |")
        L.append("|---|---|---|")
        for r in rel_sorted:
            s = (r.get("sources") or [r.get("source", "")])[0]
            t = (r.get("title") or "").replace("|", "｜")
            L.append(f"| {_fmt(_when(r))} | {s[:18]} | [{t[:70]}]({r.get('url','')}) |")
    else:
        L.append("（アーカイブに関連記事が見つかりませんでした＝この話題は初出の可能性）")
    L.append("")

    # 関連記事に含まれる数字＝定量的な推移の材料
    rel_nums: list[tuple[str, str]] = []
    for r in rel_sorted:
        for n in numbers((r.get("title") or "") + " " + (r.get("reason") or "")):
            rel_nums.append((_fmt(r.get("published_ts")), n))
    if rel_nums:
        L.append("### 関連報道に出てきた数字（推移の材料）")
        for d, n in rel_nums[:25]:
            L.append(f"- {d}: {n}")
        L.append("")

    L.append("## 3. 論点の広がり（関連記事の分野内訳）")
    from collections import Counter
    th = Counter()
    for r in rel_items:
        for t in (r.get("themes") or []):
            th[t] += 1
    deep_n = sum(1 for r in rel_items if r.get("type") == "深掘り")
    if th:
        L.append(f"- 分野: " + "、".join(f"{k} {v}件" for k, v in th.most_common()))
    L.append(f"- 関連記事 {len(rel_items)}件（うち深掘り {deep_n}件）")
    L.append("")

    L.append("## 4. 考察の切り口候補（材料から機械的に提示）")
    hints: list[str] = []
    if len(rel_sorted) >= 4:
        span_days = 0
        tss = [_when(r) for r in rel_sorted if _when(r)]
        if len(tss) >= 2:
            span_days = int((max(tss) - min(tss)) / 86400)
        hints.append(
            f"**推移で語る**: 関連報道が{len(rel_sorted)}本・約{span_days}日にわたって存在します。"
            "「いつ何が起き、何が変わったか」を時系列で追うと、飛躍のない構成になります。")
    if len(src_list) >= 2:
        hints.append(
            f"**注目度で語る**: この件は{len(src_list)}媒体が報じています（{'、'.join(s for s in src_list if s)}）。"
            "各媒体がどこを強調したかの差＝論点です。")
    if th.get("AI") and th.get("ビジネス"):
        hints.append("**AI×お金の接点**: 関連記事にAIとビジネスの両方があります。技術の話を収益・コスト構造に接続できます。")
    if rel_nums:
        hints.append("**数字の比較**: 上の数字を並べ、増減や桁の変化を主張の根拠に使えます。")
    if deep_n >= 2:
        hints.append(f"**先行分析への応答**: 関連する深掘り記事が{deep_n}本あります。既存の見方に賛成/反論する形が書きやすいです。")
    if not hints:
        hints.append("関連材料が少ない話題です。単独の事実として短く扱うか、X向けの素材に回すのが向いています。")
    for h in hints:
        L.append(f"- {h}")
    L.append("")

    return "\n".join(L)


def main() -> int:
    window = store.load_recent()
    archive = load_archive()
    arg = sys.argv[1] if len(sys.argv) > 1 else None

    if not arg:
        cands = candidates(window)
        if not cands:
            print("深掘り候補が窓にありません。URLかキーワードを指定してください。")
            return 1
        print(f"直近の深掘り候補（{len(cands)}件）: 番号を指定して `python3 dossier.py N`\n")
        for i, c in enumerate(cands, 1):
            srcs = c.get("sources") or [c.get("source", "")]
            buzz = f"{len(srcs)}媒体"
            print(f"{i:2d}. [{_fmt(c.get('published_ts'))}|{buzz}] {c.get('title','')[:64]}")
        return 0

    target = pick_target(arg, window, archive)
    if target is None:
        print(f"該当記事が見つかりません: {arg}")
        return 1

    rel = related(target, archive)
    md = build_markdown(target, rel)
    os.makedirs(OUT_DIR, exist_ok=True)
    slug = re.sub(r"[^0-9A-Za-z一-鿿ぁ-ヿ]+", "-", target.get("title", "dossier"))[:40].strip("-")
    path = os.path.join(OUT_DIR, f"dossier-{slug}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(md)
    print(md)
    print(f"\n--- 保存: {path} ---")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# ---------------------------------------------------------------- Web版（iPad用）

import hashlib


def dossier_id(url: str) -> str:
    """記事URLから短い安定IDを作る（ファイル名・リンク用）。"""
    return hashlib.sha1(normalize_url(url).encode("utf-8")).hexdigest()[:10]


def _esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


_MD_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_MD_BOLD = re.compile(r"\*\*([^*]+)\*\*")


def _inline(s: str) -> str:
    s = _esc(s)
    s = _MD_LINK.sub(r'<a href="\2" target="_blank" rel="noopener">\1</a>', s)
    s = _MD_BOLD.sub(r"<strong>\1</strong>", s)
    return s


def md_to_html(md: str) -> str:
    """このツールが出力するマークダウンの部分集合をHTMLに変換する
    （見出し・箇条書き・表・リンク・強調・コードブロックのみ）。"""
    out: list[str] = []
    in_ul = in_table = in_code = False
    for raw in md.split("\n"):
        line = raw.rstrip()
        if line.startswith("```"):
            if in_code:
                out.append("</pre>")
            else:
                out.append('<pre class="code">')
            in_code = not in_code
            continue
        if in_code:
            out.append(_esc(line))
            continue
        is_row = line.startswith("|") and line.endswith("|")
        if in_ul and not line.startswith("- "):
            out.append("</ul>"); in_ul = False
        if in_table and not is_row:
            out.append("</tbody></table></div>"); in_table = False
        if not line:
            continue
        if line.startswith("### "):
            out.append(f"<h3>{_inline(line[4:])}</h3>")
        elif line.startswith("## "):
            out.append(f"<h2>{_inline(line[3:])}</h2>")
        elif line.startswith("# "):
            out.append(f"<h1>{_inline(line[2:])}</h1>")
        elif line.startswith("---"):
            out.append("<hr>")
        elif line.startswith("- "):
            if not in_ul:
                out.append("<ul>"); in_ul = True
            out.append(f"<li>{_inline(line[2:])}</li>")
        elif is_row:
            cells = [c.strip() for c in line.strip("|").split("|")]
            if set("".join(cells)) <= set("-: "):   # 区切り行
                continue
            if not in_table:
                out.append('<div class="tw"><table><thead><tr>'
                           + "".join(f"<th>{_inline(c)}</th>" for c in cells)
                           + "</tr></thead><tbody>")
                in_table = True
            else:
                out.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in cells) + "</tr>")
        else:
            out.append(f"<p>{_inline(line)}</p>")
    if in_ul:
        out.append("</ul>")
    if in_table:
        out.append("</tbody></table></div>")
    if in_code:
        out.append("</pre>")
    return "\n".join(out)


_PAGE = """<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>素材パック: {title}</title>
<style>
 body{{font-family:-apple-system,BlinkMacSystemFont,"Hiragino Sans",sans-serif;
   line-height:1.75;margin:0;padding:16px;max-width:860px;margin:0 auto;color:#222;background:#fff}}
 h1{{font-size:20px;line-height:1.5;border-bottom:2px solid #333;padding-bottom:10px}}
 h2{{font-size:17px;margin-top:28px;border-left:5px solid #37507a;padding-left:10px}}
 h3{{font-size:15px;color:#555}}
 a{{color:#0b6e8c}}
 .tw{{overflow-x:auto;-webkit-overflow-scrolling:touch}}
 table{{border-collapse:collapse;width:100%;font-size:13px;min-width:520px}}
 th,td{{border:1px solid #ddd;padding:7px;text-align:left;vertical-align:top}}
 th{{background:#f4f6f8}}
 pre.code{{background:#f6f8fa;padding:12px;border-radius:8px;overflow-x:auto;
   font-size:13px;white-space:pre-wrap;word-break:break-word}}
 ul{{padding-left:22px}} li{{margin:4px 0}}
 .bar{{position:sticky;top:0;background:#fffdf3;border-bottom:1px solid #f0c36d;
   padding:10px 0;margin:-16px -16px 16px;padding-left:16px;padding-right:16px;z-index:9}}
 button{{background:#1a7f37;color:#fff;border:0;border-radius:8px;padding:11px 16px;
   font-size:15px;font-weight:600;cursor:pointer}}
 button:active{{opacity:.7}}
 .back{{display:inline-block;margin-left:12px;font-size:14px}}
 .hint{{font-size:12px;color:#666;margin-top:6px}}
</style></head><body>
<div class="bar">
  <button id="cp">📋 全文をコピー</button>{extra_btn}
  <a class="back" href="../index.html">← 一覧へ</a>
  <div class="hint">{hint}</div>
</div>
{body}
<textarea id="src" style="position:absolute;left:-9999px;top:0">{raw}</textarea>
<textarea id="bodysrc" style="position:absolute;left:-9999px;top:0">{body_raw}</textarea>
<script>
function copyFrom(id, btn, label){{
  var t=document.getElementById(id).value;
  function done(){{ btn.textContent='✓ コピーしました';
    setTimeout(function(){{btn.textContent=label;}},2000); }}
  if(navigator.clipboard&&navigator.clipboard.writeText){{
    navigator.clipboard.writeText(t).then(done).catch(function(){{fallback();}});
  }} else {{ fallback(); }}
  function fallback(){{ var a=document.getElementById(id); a.style.left='0'; a.select();
    document.execCommand('copy'); a.style.left='-9999px'; done(); }}
}}
document.getElementById('cp').onclick=function(){{ copyFrom('src', this, '📋 全文をコピー'); }};
var bb=document.getElementById('cpbody');
if(bb) bb.onclick=function(){{ copyFrom('bodysrc', this, '✍️ 本文だけコピー（note用）'); }};
</script></body></html>"""


_BODY_RE = re.compile(r"^## 4\. 本文下書き\s*$(.*?)^## 5\.", re.M | re.S)


def _body_only(md: str) -> str:
    """下書きから「本文下書き」節だけを取り出し、noteに貼れる素の文章にする
    （見出しの ### は外して行として残す）。"""
    m = _BODY_RE.search(md)
    if not m:
        return ""
    body = m.group(1).strip()
    body = re.sub(r"^#{1,6}\s*", "", body, flags=re.M)   # 見出し記号を外す
    body = re.sub(r"\*\*([^*]+)\*\*", r"\1", body)        # 強調記号を外す
    return body.strip()


def build_page(target: dict, md: str) -> str:
    body_raw = _body_only(md)
    if body_raw:
        extra = ('\n  <button id="cpbody" style="background:#b3541e;margin-left:8px">'
                 '✍️ 本文だけコピー（note用）</button>')
        hint = "「本文だけコピー」はnoteにそのまま貼れます。「全文」は構成案・X投稿案・確認事項を含みます。"
    else:
        extra = ""
        hint = "コピー後、ChatGPTアプリに貼り付けてください。冒頭に指示文が入っています。"
    return _PAGE.format(title=_esc(target.get("title", "")[:60]),
                        body=md_to_html(md), raw=_esc(md),
                        body_raw=_esc(body_raw), extra_btn=extra, hint=hint)


def generate_all(window: list[dict], archive: list[dict], out_dir: str = "docs/dossier") -> dict:
    """窓の深掘り記事すべての素材パックHTMLを書き出し、{url_key: 相対リンク} を返す。
    APIは使わないので毎回作り直して問題ない（数秒・無料）。"""
    os.makedirs(out_dir, exist_ok=True)
    # 古いページを掃除（窓から外れた記事のぶん）
    for f in os.listdir(out_dir):
        if f.endswith(".html"):
            try:
                os.remove(os.path.join(out_dir, f))
            except OSError:
                pass
    links: dict[str, str] = {}
    for it in candidates(window):
        rel = related(it, archive)
        md = build_markdown(it, rel)
        did = dossier_id(it.get("url", ""))
        with open(os.path.join(out_dir, f"{did}.html"), "w", encoding="utf-8") as f:
            f.write(build_page(it, md))
        links[normalize_url(it.get("url", ""))] = f"dossier/{did}.html"
    return links
