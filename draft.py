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

# あなたの仕事は「要約」ではなく「発見」です
素材をなぞって整理しただけの記事には価値がありません。読者が読み終えたときに
「その見方はしていなかった」と思える読み替えを1つ提示することが目的です。
そのために、次の順で考えてください（この思考過程は出力しなくてよい）。
 1. この記事の「ふつうの読み方」は何か（誰でも書ける説明）を言語化する。
 2. それを疑う問いを3つ立てる。「その数字は本当にそれを意味するか」
    「増えたのは何で、減ったのは何か」「同じ構造の別業界はどうなったか」など。
 3. web_search で裏を取りに行く。比較の基準になる数字（過去との比較、内訳、
    単価と数量の分解、別市場の同種データ）を必ず探す。総額だけで語らない。
 4. 最も裏付けが取れた読み替えを1つ選び、それを記事の主張にする。

# 事実の扱い（最重要）
- 事実・数字・固有名詞には、必ず出所を付ける。出所はアーカイブ素材（日付＋媒体）か、
  web_search で確認した情報（媒体名＋できればURL）のいずれか。
- **出所を示せない主張は書かない。** 推測を述べる場合は「推測ですが」と明示する。
- 「素材にない事実を足すな」という制約ではない。調べて確かめた事実は歓迎する。
  禁じているのは、確かめずに書くことだけ。
- 数字は必ず比較対象とセットで出す（前年比、コロナ前比、内訳、単価×数量）。
  総額の増減だけを根拠にしない。

# 【重要】事例の出自を必ず確認する
数字や作品を根拠に使う前に「それはどの国・どの産業構造の話か」を確認すること。
- 例: 日本のアニメ制作業界を論じている記事で、ピクサー/ディズニー等の米国スタジオ
  制作作品の興行成績を「日本アニメの需要」の根拠に使ってはいけない。
- 日本市場を論じるなら日本の数字を、海外市場を論じるなら該当国の数字を使う。
- 両者を並べる場合は「別の市場の話である」と明示し、対比としてのみ扱う。

# 独自の視点を作るための道具（少なくとも1つは使う）
- **他分野との類比**: 同じ構造変化が起きた別の市場を1つ持ち出す
  （音楽ライブ、ゲーム課金、出版、外食、スポーツ興行など）。
  「◯◯市場で起きたことが、いま映像で起きている」という補助線は強い。
- **分解**: 総額を「単価 × 数量」「上位作品 × それ以外」「新規客 × 常連客」に割る。
  伸びているものと縮んでいるものが同居していないかを疑う。
- **時間軸の入れ替え**: 「回復した」ではなく「何と比べて回復か」を問い直す。
- **次に壊れるもの**: この構造変化が続くと、誰が最初に困るかを名指しする。

# 文体（書き手の既存記事に合わせる）
- です・ます調。一人称「私」は原則使わず、読者と視点を共有する書き方にする。
- 断定を避け、「〜かもしれません」「〜のではないでしょうか」など思考の過程を見せる
  表現を適度に使う。ただし多用しすぎない。
- 短い一文と、やや長い説明文を交互に置いてリズムを作る。1段落は3〜5文。
- 煽らない。驚きや感嘆を演出しない。事実→解釈の順で淡々と積む。
- 見出しは内容を要約せず、問いや言い切りで引きを作る。
  （既存記事の例:「『良い作品』の定義」「新しい観客はハリウッドからは生まれない」
   「アルゴリズムは『悪』なのか」）
- タイトルも結論や問いを含む一文にする。長めでよい。体言止めより言い切り。
  （既存記事の例:「映画産業はスタートアップになる。」
   「AIは映画に魂を入れない。でも魂を持つ人が映画を作れるようになるかも。」）

# 出力フォーマット（この順序・この見出しで）
## 1. タイトル案（3つ）
## 2. この記事の主張（1〜2文）
   「ふつうの読み方」ではなく、あなたが見つけた読み替えを書く。
## 3. 根拠（表）
   列は「事実・数字／出所（媒体・日付・URL）／この主張にどう効くか」。
   web_search で得たものには出所を必ず書く。5〜8行。
## 4. 構成案
   表形式。列は「見出し／書くこと／根拠」。見出しは3〜4本。
## 5. 本文下書き
   **1500〜2000字**。2000字を超えない。材料を全部使おうとせず、論の筋を1本通す。
   見出しは ### を使い、3〜4本。
## 6. X投稿案（3本）
   各240字以内。切り口を変える。ハッシュタグと絵文字は使わない。
## 7. 採用しなかった切り口（2〜3個）
   別の読み替えの候補と、それを成立させるために必要な追加調査を1行ずつ。
   書き手が別路線を選べるようにするため。
## 8. 確認すべき点
   裏が取り切れなかった数字、出所が二次情報のもの、事例の出自に疑義がある点。
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
        # 裏付けデータを自分で取りに行かせる（比較の基準になる数字を探すため）
        tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": 12}],
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
    slug0 = re.sub(r"[^0-9A-Za-z一-鿿ぁ-ヿ]+", "-", target.get("title", "material"))[:40].strip("-")
    os.makedirs(OUT_DIR, exist_ok=True)
    mat_path = os.path.join(OUT_DIR, f"material-{slug0}.md")
    with open(mat_path, "w", encoding="utf-8") as f:
        f.write(material)
    # Web版の素材パックも書き出す（窓から外れても消えないよう generate_all が保護する）
    os.makedirs("docs/dossier", exist_ok=True)
    with open(os.path.join("docs/dossier", f"{dossier.dossier_id(target.get('url',''))}.html"),
              "w", encoding="utf-8") as f:
        f.write(dossier.build_page(target, material))
    print(f"素材パックを保存: {mat_path}")
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

    # digestを作り直して「✍️ 下書きを見る」リンクを反映（iPadから辿れるように）
    try:
        import shutil
        run.DOSSIER_LINKS.update(dossier.generate_all(window, archive))
        run.CAND_NO.update(dossier.number_map(window))
        run.DRAFT_LINKS.update(run.scan_drafts(window))
        with open(os.path.join(OUT_DIR, "digest.html"), "w", encoding="utf-8") as f:
            f.write(run.render_html(window))
        shutil.copy(os.path.join(OUT_DIR, "digest.html"), "docs/index.html")
    except Exception as e:  # noqa: BLE001
        print(f"  [digest更新スキップ] {e}")

    print(f"\n{body}\n")
    print(f"--- 保存: {md_path}")
    print(f"--- 素材: {mat_path}")
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
