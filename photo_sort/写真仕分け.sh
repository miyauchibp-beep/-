#!/bin/sh
# 使い方: ./写真仕分け.sh ~/ピクチャ/整備写真
if [ -z "$1" ]; then
  echo "使い方: ./写真仕分け.sh 整備写真のフォルダ"
  exit 1
fi
if ! python3 -c "import PIL, numpy" 2>/dev/null; then
  echo "必要な部品を入れます（パスワードを聞かれたらPCのログインパスワードを入力）"
  sudo apt install -y python3-pil python3-numpy || exit 1
fi
python3 "$(dirname "$0")/photo_sort.py" "$@"
