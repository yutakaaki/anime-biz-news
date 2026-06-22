#!/bin/bash
# アニメビジネスニュース: 定期実行ラッパー（launchd から JST 6:00 / 18:00 に呼ばれる）。
# - .env から ANTHROPIC_API_KEY を読み込む
# - run.py を実行して digest.html を更新
# - state/cron.log にログ、完了時にmacOS通知で新着件数を知らせる
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR" || exit 1

# APIキーを .env（ANTHROPIC_API_KEY=... の1行）から読み込む
if [ -f "$DIR/.env" ]; then
  set -a; . "$DIR/.env"; set +a
fi

# launchd は最小PATHなので補う（python3 と --user で入れたパッケージのため）
export PATH="/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin:$HOME/Library/Python/3.9/bin:$PATH"

mkdir -p "$DIR/state"
echo "===== $(date '+%Y-%m-%d %H:%M:%S') 実行開始 =====" >> "$DIR/state/cron.log"

/usr/bin/python3 "$DIR/run.py" >> "$DIR/state/cron.log" 2>&1
echo "===== $(date '+%Y-%m-%d %H:%M:%S') 実行終了 =====" >> "$DIR/state/cron.log"
