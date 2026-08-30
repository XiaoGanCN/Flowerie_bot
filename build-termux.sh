#!/data/data/com.termux/files/usr/bin/bash

set -e

echo "[1/4] Updating packages"

pkg update -y
pkg upgrade -y


echo "[2/4] Installing dependencies"

pkg install -y python clang rust


echo "[3/4] Installing Python packages"

python -m pip install --upgrade pip


python -m pip install \
  -r requirements.txt \
  -i https://termux-user-repository.github.io/pypi/ \
  --extra-index-url https://pypi.tuna.tsinghua.edu.cn/simple \
  --only-binary pydantic-core,pydantic



echo "[4/4] Starting Flowerie_bot"

python main.py
