#!/bin/bash
# アニメビジネスニュース: 定期実行ラッパー（launchd から JST 6:00 / 18:00 に呼ばれる）。
# - .env から ANTHROPIC_API_KEY を読み込む
# - run.py を実行して digest.html を生成（判定はMacから＝Anthropicへの接続が安定）
# - 結果を docs/index.html に置いて GitHub に push → GitHub Pages が更新 → iPadで閲覧
# - ログは state/cron.log
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR" || exit 1
LOG="$DIR/state/cron.log"

# 定時ウェイク後にすぐ再スリープして実行が途切れるのを防ぐ。
# このスクリプト($$)が終わるまでスリープを抑止する（バックグラウンドで待機）。
/usr/bin/caffeinate -i -w $$ &

# APIキーを .env（ANTHROPIC_API_KEY=... の1行）から読み込む
if [ -f "$DIR/.env" ]; then
  set -a; . "$DIR/.env"; set +a
fi

# launchd は最小PATHなので補う（python3 と --user で入れたパッケージのため）
export PATH="/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin:$HOME/Library/Python/3.9/bin:$PATH"

mkdir -p "$DIR/state"
echo "===== $(date '+%Y-%m-%d %H:%M:%S') 実行開始 =====" >> "$LOG"

# 実行全体の時間上限（ハング対策）。RUN_TIMEOUT秒を超えたら強制終了する。
RUN_TIMEOUT="${RUN_TIMEOUT:-1200}"
/usr/bin/python3 "$DIR/run.py" >> "$LOG" 2>&1 &
PYPID=$!
( sleep "$RUN_TIMEOUT"; kill -9 "$PYPID" 2>/dev/null && echo "!!! ${RUN_TIMEOUT}秒を超過したため強制終了（ハング対策）!!!" >> "$LOG" ) &
WPID=$!
wait "$PYPID" 2>/dev/null
kill "$WPID" 2>/dev/null; wait "$WPID" 2>/dev/null

# 生成した digest を GitHub Pages 用に配置して push（クラウド公開）
if [ -f "$DIR/outputs/digest.html" ]; then
  mkdir -p "$DIR/docs"
  cp "$DIR/outputs/digest.html" "$DIR/docs/index.html"
  GIT=/usr/bin/git
  "$GIT" add docs state >> "$LOG" 2>&1
  "$GIT" commit -m "digest update $(date '+%Y-%m-%dT%H:%M:%S')" >> "$LOG" 2>&1 || echo "コミット変更なし" >> "$LOG"
  "$GIT" pull --rebase --autostash origin main >> "$LOG" 2>&1 || echo "pullスキップ" >> "$LOG"
  "$GIT" push origin main >> "$LOG" 2>&1 && echo "push成功（Pages更新）" >> "$LOG" || echo "push失敗（認証要確認）" >> "$LOG"
fi

# 次のウェイクを予約（朝の実行後→今日の夕方17:58 / 夕方の実行後→翌朝05:58）。
# pmset schedule は要root。sudoers で pmset を NOPASSWD 許可している前提。
HOUR=$(date +%H)
if [ "$HOUR" -lt 12 ]; then
  NEXT=$(date -v17H -v58M -v00S '+%m/%d/%Y %H:%M:%S')
else
  NEXT=$(date -v+1d -v05H -v58M -v00S '+%m/%d/%Y %H:%M:%S')
fi
/usr/bin/sudo /usr/bin/pmset schedule wake "$NEXT" >> "$LOG" 2>&1 \
  && echo "次回ウェイク予約: $NEXT" >> "$LOG" \
  || echo "ウェイク予約失敗（sudoers未設定の可能性）" >> "$LOG"

echo "===== $(date '+%Y-%m-%d %H:%M:%S') 実行終了 =====" >> "$LOG"
