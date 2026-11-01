#!/bin/sh
# git credential helper：push 時從 data/github_token.txt 讀 token，不把 token 存進 git 設定
[ "$1" = get ] || exit 0
echo username=x-access-token
echo "password=$(tr -d '\r\n' < data/github_token.txt)"
