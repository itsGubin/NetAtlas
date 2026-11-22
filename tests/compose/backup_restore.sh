#!/usr/bin/env bash
# Runs the commands in docs/backup.md against a running Compose stack, end to end.
# The commands are read from the document, so the instructions themselves are what is tested.
set -euo pipefail

BASE_URL="${NETATLAS_URL:-http://localhost:8000}"
DOC=docs/backup.md
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

block() {
  awk -v marker="<!-- ci:$1 -->" '
    $0 == marker { found = 1; next }
    found && /^```sh$/ { inside = 1; next }
    inside && /^```$/ { exit }
    inside { print }
  ' "$DOC"
}

run_block() {
  local commands
  commands=$(block "$1")
  [ -n "$commands" ] || { echo "No $1 commands in $DOC" >&2; exit 1; }
  bash -euo pipefail -c "$commands"
}

api() {
  curl -fsS -H "Authorization: Bearer $TOKEN" "$@"
}

json() {
  python3 -c "import json, sys; data = json.load(sys.stdin); print($1)"
}

wait_until_up() {
  for _ in $(seq 1 60); do
    curl -fsS -o /dev/null "$BASE_URL/api/status/" && return 0
    sleep 1
  done
  echo "NetAtlas did not come back" >&2
  exit 1
}

check_restored() {
  api "$BASE_URL/api/devices/?page_size=200" > "$WORK/devices.json"
  python3 - "$WORK/devices.json" <<'PY'
import json, sys
names = {device["name"] for device in json.load(open(sys.argv[1]))["results"]}
assert "before-backup" in names, names
assert "after-backup" not in names, names
PY
  test "$(api "$BASE_URL/api/pages/$PAGE_ID/" | json 'data["body"]')" = "restored page text"
  api "$BASE_URL/api/attachments/$ATTACHMENT_ID/download/" -o "$WORK/downloaded.png"
  test "$(sha256sum < "$WORK/downloaded.png")" = "$ATTACHMENT_SHA"
  actions=$(api "$BASE_URL/api/history/device/$DEVICE_ID/" | json '[e["action"] for e in data["results"]]')
  test "$actions" = "['created']"
}

echo "== Create data"
TOKEN=$(docker compose exec -T app python manage.py shell -v0 -c "
import os
from django.contrib.auth import get_user_model
from netatlas.accounts.services import create_token
user = get_user_model().objects.get(username=os.environ['NETATLAS_ADMIN_USERNAME'])
print(create_token(user, 'backup-test').secret)
" | tail -n 1 | tr -d '\r')
SITE_ID=$(api "$BASE_URL/api/sites/" | json 'data["results"][0]["id"]')
DEVICE_ID=$(api -H 'Content-Type: application/json' \
  -d "{\"name\": \"before-backup\", \"kind\": \"physical\", \"site_id\": $SITE_ID}" \
  "$BASE_URL/api/devices/" | json 'data["id"]')
PAGE_ID=$(api "$BASE_URL/api/items/device/$DEVICE_ID/page/" | json 'data["id"]')
api -X PATCH -H 'Content-Type: application/json' -d '{"body": "restored page text"}' \
  "$BASE_URL/api/pages/$PAGE_ID/" > /dev/null
python3 -c "
import os, sys
sys.stdout.buffer.write(b'\x89PNG\r\n\x1a\n' + os.urandom(2048))
" > "$WORK/photo.png"
ATTACHMENT_SHA=$(sha256sum < "$WORK/photo.png")
ATTACHMENT_ID=$(api -F "file=@$WORK/photo.png" "$BASE_URL/api/pages/$PAGE_ID/attachments/" | json 'data["id"]')

echo "== Back up"
run_block backup
wait_until_up
dump=$(ls -t backups/netatlas-*.dump | head -n 1)
stamp=${dump#backups/netatlas-}
stamp=${stamp%.dump}
test -s "backups/netatlas-$stamp.dump"
test -s "backups/media-$stamp.tar.gz"
test -s "backups/env-$stamp"
test "$(stat -c %a backups)" = 700
test "$(stat -c %a "backups/env-$stamp")" = 600

echo "== Change data after the backup, then lose everything"
api -H 'Content-Type: application/json' \
  -d "{\"name\": \"after-backup\", \"kind\": \"physical\", \"site_id\": $SITE_ID}" \
  "$BASE_URL/api/devices/" > /dev/null
docker compose down -v
rm .env
if curl -fsS -o /dev/null --max-time 3 "$BASE_URL/api/status/"; then
  echo "The stack should be gone" >&2
  exit 1
fi

echo "== Restore"
commands=$(block restore | sed "s/^stamp=.*/stamp=$stamp/")
[ -n "$commands" ]
bash -euo pipefail -c "$commands"
wait_until_up
check_restored

echo "== Upgrade"
commands=$(block upgrade | sed '/^git pull$/d')
[ -n "$commands" ]
bash -euo pipefail -c "$commands"
wait_until_up
check_restored

echo "Backup, restore and upgrade all work."
