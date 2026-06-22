# アニメ／コンテンツIP ビジネスニュース収集ツール（PoC）

アニメ業界の「ビジネス」ニュース（決算・売上・ライセンス・興行・M&A・業界構造など）
だけを取りこぼしなく集めるためのプロトタイプ。

構成（PoC）：
- 収集：RSS優先（Googleニュース検索RSS＋指定サイトの直接フィード）。`sources.py`
- 判定：Claude API による二次判定（一次の埋め込みフィルタは将来追加）。`classifier.py`
- 基準：`rubric.py`（「主題で判定」「IP×お金/経営/権利＝対象、個人/新作発表＝対象外」）
- 配信：ローカルWeb（`outputs/digest.html`）。将来クラウドWebアプリ化。

## セットアップ

```bash
cd anime-biz-news
python3 -m pip install --user -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...   # console.anthropic.com で取得
```

### APIキーの取得（未取得の場合）
1. https://console.anthropic.com にログイン
2. 「API Keys」→「Create Key」でキーを発行
3. 上記の `export` で環境変数にセット（`~/.zshrc` に書いておくと毎回不要）

## 使い方

精度検証（ラベル付き20本で判定精度を確認）：
```bash
python3 validate.py
```
- 「正例の取りこぼし」が0に近いほど良い（取りこぼし最小化の方針）。

本番パイプライン（収集→判定→ローカルWeb出力）：
```bash
python3 run.py
open outputs/digest.html
```

## モデルとコスト
- 既定の判定モデルは `claude-haiku-4-5`。PoC検証（ラベル20本）で正答率100%・
  正例の取りこぼし0・ノイズ0を達成し、opus と同等の精度を約1/20のコストで出せたため。
- 精度を上げたい/比較したい場合は環境変数で変更可：
  ```bash
  MODEL=claude-opus-4-8 python3 validate.py
  ```
  「主題で判定」の微妙なケースを増やした際は validate.py で精度を見ながら選ぶ。
- 収集（RSS取得・本文抽出）は無料。課金は判定の Claude API のみ。
- system プロンプト（ルーブリック＋few-shot）はプロンプトキャッシュ対象で、
  連続判定時のコストを抑える。

## 自動化（1日2回・ローカル / macOS launchd）
Macのバックグラウンドで JST 6:00 と 18:00 に自動実行し digest.html を更新する。

```bash
cd ~/Documents/anime-biz-news

# 1) APIキーを .env に保存（現在のシェルにキーがあれば下記1行でOK。無ければ .env を手で編集）
echo "ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY" > .env

# 2) 手動で一度ラッパーを実行して動作確認（digest更新＋通知が出る）
./run_scheduled.sh
tail -n 20 state/cron.log

# 3) launchd に登録（6:00 / 18:00 に自動実行）
cp com.anime-biz-news.digest.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.anime-biz-news.digest.plist

# 解除したいとき:
#   launchctl unload ~/Library/LaunchAgents/com.anime-biz-news.digest.plist
```

- 実行時刻にMacがスリープでも、起動後に一度だけ実行される。完了時にmacOS通知で新着件数を表示。
- digest は `outputs/digest.html`。`file://` のパスをブラウザでブックマークしておくと開きやすい。
- ログは `state/cron.log`。秘密情報・生成物は `.gitignore` 済み。

## 既読管理・重複集約
- **差分取得（既読管理）**: 判定済み記事は `state/seen.json` に記録し、次回以降は
  新着だけを判定・表示する。毎回開いて「新しいものだけ」読む運用。
- **重複集約**: 同一ニュースが複数媒体から来た場合、タイトル類似（しきい値は環境変数
  `DEDUP_SIM`、既定0.35）で1本に集約し「他N媒体でも報道」と表示。同一ラン内・ラン跨ぎの
  両方に対応（同一言語の言い換え見出し向け。日英をまたぐ集約は埋め込み導入時に強化）。
- **アーカイブ**: 拾った記事は `state/archive.jsonl` に追記（取りこぼし防止の記録）。
- **作り直し**: ルーブリックのチューニング中など、既読を無視して全件を判定し直したいときは
  `RESET_STATE=1 python3 run.py`。

## 既知の制約（PoC）
- 一次フィルタ（埋め込み）未実装。新着候補を全て判定にかける（`run.py` の MAX_CLASSIFY で安全弁）。
- 重複集約はタイトル類似のみ。日英をまたいだ同一ニュースの集約は未対応（埋め込みで強化予定）。
- ペイウォール記事（日経など）は本文が取れず判定をスキップすることがある。
- Google ニュースRSSのリンクは `news.google.com` のリダイレクト経由のため本文抽出が
  できず、タイトル＋概要で判定する（直接フィードの記事は本文まで取得できる）。
  将来はリダイレクト解決で発行元URLに辿るのが課題。
