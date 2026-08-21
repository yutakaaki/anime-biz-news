"""下書き生成: 選んだ記事から、素材パックを作り → note構成案・本文下書き・X投稿案まで一気に作る。

チャットへの貼り付けを挟まずに完結させるためのスクリプト。
素材パック（dossier.py）は無料だが、ここは Claude API を呼ぶので課金される。
そのため自動実行には組み込まず、明示的に選んだときだけ動かす。

使い方:
    python3 draft.py              # 候補一覧（深掘り＋話題の速報）
    python3 draft.py 3            # 3番の記事で下書き生成
    python3 draft.py <URL>        # URL指定
    python3 draft.py 3 --push     # 生成後、GitHub Pagesへ公開（iPadで読める）
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

import anthropic

import dossier
import store

MODEL = os.environ.get("DRAFT_MODEL", "claude-opus-5")
OUT_DIR = "outputs"
WEB_DIR = "docs/draft"

SYSTEM = """あなたは日本語で「コンテンツ×AI×ビジネス」を論じるnote記事のライターです。
媒体は note「今日のアニメビジネス」。読者は業界動向を追うビジネスパーソン。
書き手本人がこの下書きに手を入れて公開します。

# 絶対的な制約
- 与えられた素材の範囲だけで書く。素材にない事実・数字・固有名詞を足さない。
  どうしても補助線が必要な場合のみ「※推測」と明記する。
- 各見出しには、根拠となる素材（日付・数字・媒体）が必ず1つ以上ひもづくこと。
  ひもづけられない見出しは作らない（これが論理飛躍の主因になる）。
- 主張は1本に絞る。あれもこれも書かない。
- 見出しに出てくる数字が宣伝目的の枕（累計部数・過去の興収など）の場合、それを主軸にしない。

# 【重要】事例の出自を必ず確認する
素材パックには日本と海外の記事が混在している。数字や作品を根拠に使う前に、
「それはどの国・どの産業構造の話か」を確認し、論点と食い違う事例は使わないこと。
- 例: 日本のアニメ制作業界（人手不足・制作費・スタジオ経営）を論じている記事で、
  ピクサー/ディズニー等の米国スタジオ制作作品の興行成績を「日本アニメの需要」の
  根拠として使ってはいけない。制作主体も産業構造も別物である。
- 日本市場を論じるなら日本の作品・企業の数字を、海外市場を論じるなら該当国の数字を使う。
- どうしても両者を並べる場合は「別の市場の話である」と明示し、対比としてのみ扱う。

# 文体（書き手の既存記事に合わせる）
- です・ます調。一人称「私」は原則使わず、読者と視点を共有する書き方にする。
- 断定を避け、「〜かもしれません」「〜のではないでしょうか」「〜だと思います」など
  思考の過程を見せる表現を適度に使う。ただし1記事で多用しすぎない。
- 短い一文と、やや長い説明文を交互に置いてリズムを作る。1段落は3〜5文。
- 煽らない。驚きや感嘆を演出しない。事実→解釈の順で淡々と積む。
- 見出しは内容を要約するのではなく、問いや言い切りで引きを作る。
  （既存記事の例:「『良い作品』の定義」「新しい観客はハリウッドからは生まれない」
   「アルゴリズムは『悪』なのか」）
- タイトルも同様に、結論や問いを含む一文にする。長めでよい。体言止めより言い切り。
  （既存記事の例:「映画産業はスタートアップになる。」
   「AIは映画に魂を入れない。でも魂を持つ人が映画を作れるようになるかも。」
   「YouTuberの人気は買えるらしい。でも『まだ人気のないもの』を誰が作るのか。」）

# 出力フォーマット（この順序・この見出しで）
## 1. タイトル案（3つ）
   上記の書き手のタイトル作法に従う。説明的な要約タイトルにしない。
## 2. この記事の主張（1〜2文）
## 3. 構成案
   表形式。列は「見出し／書くこと／根拠となる素材」。見出しは3〜4本に絞る。
## 4. 本文下書き
   **1500〜2000字**。2000字を超えないこと。サクッと読める分量を優先し、
   材料を全部使おうとしない。使う材料を絞り、論の筋を1本通すことを優先する。
   見出しは ### を使い、3〜4本。
## 5. X投稿案（3本）
   各240字以内。それぞれ切り口を変える（数字提示型／対比型／現場の声型など）。
   ハッシュタグと絵文字は使わない。
## 6. 書き手が確認すべき点
   素材だけでは埋まらない箇所、原記事で確認すべき事実を箇条書きで。
   事例の出自（制作国・企業）に疑義がある場合もここに書く。
"""


def load_env(path: str = ".env") -> None:
    """.env を読み込む（run_scheduled.sh を介さず直接実行するとき用）。"""
    if not os.path.exists(path) or os.environ.get("ANTHROPIC_API_KEY"):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))





def generate(material: str) -> str:
    client = anthropic.Anthropic(max_retries=2, timeout=600.0)
    with client.messages.stream(
        model=MODEL,
        max_tokens=32000,
        system=SYSTEM,
        thinking={"type": "adaptive"},
        output_config={"effort": "high"},
        messages=[{"role": "user", "content": material}],
    ) as stream:
        msg = stream.get_final_message()
    parts = [b.text for b in msg.content if b.type == "text"]
    usage = msg.usage
    cost = (usage.input_tokens * 5 + usage.output_tokens * 25) / 1_000_000
    print(f"\n--- 使用トークン: 入力{usage.input_tokens} / 出力{usage.output_tokens}"
          f"（約${cost:.2f} ≒ {cost*150:.0f}円）---", file=sys.stderr)
    return "\n".join(parts)


def main() -> int:
    load_env()
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    do_push = "--push" in sys.argv
    import run
    window = run._aggregate_dicts(store.load_recent())  # digestと同じ集約後リスト
    archive = dossier.load_archive()
    cands = dossier.candidates(window)

    if not args:
        if not cands:
            print("候補がありません。")
            return 1
        print(f"ネタ候補（{len(cands)}件）: `python3 draft.py N` で下書き生成\n")
        for i, c in enumerate(cands, 1):
            n = len(c.get("sources") or [])
            mark = "深掘り" if c.get("type") == "深掘り" else f"速報{n}媒体"
            print(f"{i:2d}. [{dossier._fmt(dossier._when(c))}|{mark}] {c.get('title','')[:60]}")
        return 0

    arg = args[0]
    if arg.isdigit():
        i = int(arg) - 1
        target = cands[i] if 0 <= i < len(cands) else None
    else:
        target = dossier.pick_target(arg, window, archive)
    if target is None:
        print(f"該当記事が見つかりません: {arg}")
        return 1

    print(f"対象: {target.get('title','')[:70]}")
    print("素材パックを作成中...")
    material = dossier.build_markdown(target, dossier.related(target, archive))
    print(f"下書きを生成中...（{MODEL}・1〜3分かかります）")
    body = generate(material)

    slug = re.sub(r"[^0-9A-Za-z一-鿿ぁ-ヿ]+", "-", target.get("title", "draft"))[:40].strip("-")
    os.makedirs(OUT_DIR, exist_ok=True)
    md_path = os.path.join(OUT_DIR, f"draft-{slug}.md")
    full = (f"# 下書き: {target.get('title','')}\n\n"
            f"元記事: {target.get('url','')}\n\n---\n\n{body}\n")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(full)

    os.makedirs(WEB_DIR, exist_ok=True)
    did = dossier.dossier_id(target.get("url", ""))
    web_path = os.path.join(WEB_DIR, f"{did}.html")
    with open(web_path, "w", encoding="utf-8") as f:
        f.write(dossier.build_page(target, full))

    print(f"\n{body}\n")
    print(f"--- 保存: {md_path}")
    print(f"--- Web: {web_path}")

    if do_push:
        for cmd in (["git", "add", "docs"],
                    ["git", "commit", "-m", f"下書き: {target.get('title','')[:50]}"],
                    ["git", "push", "origin", "main"]):
            subprocess.run(cmd, capture_output=True)
        print(f"--- 公開: https://yutakaaki.github.io/anime-biz-news/draft/{did}.html")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
