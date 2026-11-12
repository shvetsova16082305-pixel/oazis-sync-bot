#!/usr/bin/env bash
# Настройки (config.json) и состояние (state.json) хранятся в репозитории только в зашифрованном виде: data.enc.
# Ключ — секрет STATE_KEY в GitHub (локальная копия: файл .state_key, в git не попадает).
set -e
cd "$(dirname "$0")"
[ -z "$STATE_KEY" ] && [ -f .state_key ] && STATE_KEY=$(cat .state_key) && export STATE_KEY
enc() { openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -pass env:STATE_KEY "$@"; }
case "$1" in
  unpack) enc -d -in data.enc | tar -xz
    [ -f site.enc ] && enc -d -in site.enc -out site.json || true ;;
  pack-site) enc -in site.json -out site.enc ;;
  pack)
    h=$(cat config.json state.json | sha256sum | cut -c1-64)
    [ -f data.enc ] && [ "$h" = "$(cat .data_hash 2>/dev/null)" ] && exit 0
    tar -cz config.json state.json | enc -out data.enc
    echo "$h" > .data_hash ;;
  save)
    bash secure.sh pack
    git add data.enc
    if ! git diff --cached --quiet; then
      git -c user.name=oazis-sync-bot -c user.email=actions@users.noreply.github.com commit -qm "state"
      git pull -q --rebase -X ours || true
      git push -q || true
    fi ;;
esac
