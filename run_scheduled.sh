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
export PATH="/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin:/opt/homebrew/bin:$HOME/.local/bin:$HOME/Library/Python/3.9/bin:$PATH"

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
  # 接続がハングしても短時間で諦める（macOSに timeout コマンドが無いので自前で kill）。
  # http.lowSpeedLimit/Time: 転送が 30 秒間ほぼ停止したら中断。
  GITC="$GIT -c http.lowSpeedLimit=1000 -c http.lowSpeedTime=30"
  git_timeout() {  # $1=秒 残りは実行コマンド。指定秒でハングを強制終了。
    local secs="$1"; shift
    "$@" >> "$LOG" 2>&1 &
    local gpid=$!
    ( sleep "$secs"; kill -9 "$gpid" 2>/dev/null ) &
    local kpid=$!
    wait "$gpid" 2>/dev/null; local rc=$?
    kill "$kpid" 2>/dev/null; wait "$kpid" 2>/dev/null
    return $rc
  }
  "$GIT" add docs state >> "$LOG" 2>&1
  "$GIT" commit -m "digest update $(date '+%Y-%m-%dT%H:%M:%S')" >> "$LOG" 2>&1 || echo "コミット変更なし" >> "$LOG"
  git_timeout 60 $GITC pull --rebase --autostash origin main || echo "pullスキップ（timeout/失敗）" >> "$LOG"
  # push は不通のことがあるので最大3回リトライ（各60秒でタイムアウト）。
  PUSH_OK=0
  for i in 1 2 3; do
    if git_timeout 60 $GITC push origin main; then
      echo "push成功（Pages更新, 試行$i）" >> "$LOG"; PUSH_OK=1; break
    fi
    echo "push失敗 試行$i → リトライ" >> "$LOG"; sleep 10
  done
  [ "$PUSH_OK" = 1 ] || echo "push最終失敗（3回とも不通。次回実行で再送されます）" >> "$LOG"

  # push できてもGitHub側のPagesデプロイが失敗することがある（503やランナー確保失敗）。
  # 公開されなければiPadで読めないので、デプロイ結果を確認して失敗なら再トリガーする。
  if [ "$PUSH_OK" = 1 ] && command -v gh > /dev/null 2>&1; then
    REPO=yutakaaki/anime-biz-news
    for attempt in 1 2 3; do
      sleep 60  # ビルド完了を待つ（通常25〜75秒）
      ST=$(gh api "repos/$REPO/pages/builds/latest" --jq '.status' 2>/dev/null || echo "unknown")
      if [ "$ST" = "built" ]; then
        echo "Pagesデプロイ成功（確認$attempt回目）" >> "$LOG"; break
      fi
      if [ "$ST" = "building" ] || [ "$ST" = "queued" ]; then
        echo "Pagesデプロイ進行中（確認$attempt回目）" >> "$LOG"; continue
      fi
      echo "Pagesデプロイ失敗(status=$ST) → 再トリガー" >> "$LOG"
      gh api -X POST "repos/$REPO/pages/builds" >> "$LOG" 2>&1 || echo "再トリガー失敗" >> "$LOG"
    done
  fi
fi

# 次のウェイクを予約。実行時刻が曜日によって変わるので、スケジュール表から
# 「次の便」を計算して、その2分前に起こす。plist の StartCalendarInterval と
# 同じ内容を持たせること（毎日2:00と14:00、月曜18:00、火曜21:00）。
# pmset schedule は要root。sudoers で pmset を NOPASSWD 許可している前提。
NEXT=$(/usr/bin/python3 - <<'PYWAKE'
from datetime import datetime, timedelta
# (曜日, 時) 曜日は月=0..日=6、None は毎日
SCHEDULE = [(None, 2), (None, 14), (0, 18), (1, 21)]
now = datetime.now()
best = None
for day_ahead in range(8):
    d = now + timedelta(days=day_ahead)
    for wd, hour in SCHEDULE:
        if wd is not None and d.weekday() != wd:
            continue
        run = d.replace(hour=hour, minute=0, second=0, microsecond=0)
        wake = run - timedelta(minutes=2)
        if wake > now and (best is None or wake < best):
            best = wake
    if best:
        break
print(best.strftime('%m/%d/%Y %H:%M:%S'))
PYWAKE
)
/usr/bin/sudo /usr/bin/pmset schedule wake "$NEXT" >> "$LOG" 2>&1 \
  && echo "次回ウェイク予約: $NEXT" >> "$LOG" \
  || echo "ウェイク予約失敗（sudoers未設定の可能性）" >> "$LOG"

echo "===== $(date '+%Y-%m-%d %H:%M:%S') 実行終了 =====" >> "$LOG"
