#!/usr/bin/env bash
# dividend-scout 自動更新の設定
#
# 毎週土曜の朝に、株価・配当・スコアを自動で取り直すようにします。
# 設定するかどうかはケンが決めます。ここを実行しないかぎり、何も変わりません。
#
# 【変わるもの】
#   ~/Library/LaunchAgents/ に設定ファイルが1つ置かれます。
#   これは「Mac に、決まった時刻にこのコマンドを走らせてください」と伝えるためのもの。
#   解除すれば元どおりになります。他のアプリには影響しません。

cd "$(dirname "$0")"
APP_DIR="$(pwd)"
LABEL="com.ken.dividend-scout.weekly"
AGENTS="$HOME/Library/LaunchAgents"
PLIST="$AGENTS/$LABEL.plist"

echo ""
echo "=================================================="
echo "  ⏰ dividend-scout 自動更新の設定"
echo "=================================================="
echo ""

pause_exit() { echo ""; read -n 1 -s -r -p "Enter キーでこの窓を閉じます…"; exit "${1:-0}"; }

[ -d ".venv" ] || { echo "⚠ 環境がまだありません。先に 起動.command を一度実行してください。"; pause_exit 1; }

if [ -f "$PLIST" ]; then
  echo "  いまの状態: ✅ 自動更新は【オン】"
  NEXT=$(launchctl list 2>/dev/null | grep "$LABEL" || true)
  [ -n "$NEXT" ] && echo "               Mac に登録されています"
else
  echo "  いまの状態: ⏸ 自動更新は【オフ】"
fi

echo ""
echo "  自動更新をオンにすると、毎週土曜の朝9時に走ります。"
echo "  中身は「古くなったものだけ」やるので、毎回10分で終わります。"
echo ""
echo "    毎週   株価・配当・スコア   約10分   ← 毎回これだけ"
echo "    月1回  会社の基本情報       約10分   （30日たっていたら足す）"
echo "    年1回  有価証券報告書       約70分   （180日たっていて7月以降なら足す）"
echo ""
echo "    ・アプリを開いていなくても走る"
echo "    ・その時刻に Mac が寝ていたら、次に起きたときに走る"
echo "    ・Mac が2週間落ちていても、次に起きたとき足りないぶんが埋まる"
echo "    ・手で更新している最中なら、重ならないように黙って降りる"
echo "    ・失敗したらアプリの左に出る（気づかないまま古い数字を見ることはない）"
echo "    ・結果は logs/weekly.log に残る"
echo "    ・保有情報は外に出ません（このMacの中だけで完結）"
echo ""
echo "  【負担】"
echo "    通信   毎週  yfinance に 38回（一括）＋ 財務を個別に数千回"
echo "           年1回 EDINET から約 300MB（2回目以降はキャッシュを使う）"
echo "    ディスク 毎週 4千行ほど増える（DBはいま 320MB）"
echo "    CPU     1コアを2割ほど。ProcessType=Background なので"
echo "            ほかの作業を邪魔しない優先度で走ります"
echo ""
echo "  どうしますか？"
echo ""
echo "    1) オンにする"
echo "    2) オフにする"
echo "    3) いまオンなら、試しに1回すぐ走らせる"
echo "    0) 何もしない"
echo ""
read -r -p "  番号を入れて Enter: " CHOICE
echo ""

case "$CHOICE" in
  1)
    mkdir -p "$AGENTS" "$APP_DIR/logs"
    cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$APP_DIR/.venv/bin/python</string>
    <string>$APP_DIR/scripts/scheduled_update.py</string>
  </array>
  <key>WorkingDirectory</key><string>$APP_DIR</string>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Weekday</key><integer>6</integer>
    <key>Hour</key><integer>9</integer>
    <key>Minute</key><integer>0</integer>
  </dict>
  <key>StandardOutPath</key><string>$APP_DIR/logs/weekly.log</string>
  <key>StandardErrorPath</key><string>$APP_DIR/logs/weekly.log</string>
  <key>RunAtLoad</key><false/>
  <key>ProcessType</key><string>Background</string>
  <key>LowPriorityIO</key><true/>
</dict>
</plist>
PLISTEOF
    launchctl unload "$PLIST" 2>/dev/null
    if launchctl load "$PLIST" 2>/dev/null; then
      echo "  ✅ 自動更新をオンにしました。"
      echo "     毎週土曜の朝9時に走ります。次はこのあと最初に来る土曜です。"
      echo "     やめたくなったら、またこのファイルをダブルクリックして 2 を選んでください。"
    else
      echo "  ⚠ 登録に失敗しました。次を手で試してみてください:"
      echo "     launchctl load $PLIST"
    fi
    ;;
  2)
    if [ -f "$PLIST" ]; then
      launchctl unload "$PLIST" 2>/dev/null
      rm -f "$PLIST"
      echo "  ⏸ 自動更新をオフにしました。設定ファイルも消しました。"
      echo "     更新は 更新.command をダブルクリックして自分で行ってください。"
    else
      echo "  もともとオフでした。何もしていません。"
    fi
    ;;
  3)
    if [ -f "$PLIST" ]; then
      echo "  ▶ いますぐ1回走らせます（約10分。この窓は閉じてかまいません）…"
      launchctl start "$LABEL"
      echo "  結果は logs/weekly.log に出ます。"
    else
      echo "  自動更新がオフなので走らせられません。先に 1 でオンにしてください。"
    fi
    ;;
  *)
    echo "  何もしませんでした。"
    ;;
esac

pause_exit 0
