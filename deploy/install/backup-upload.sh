#!/usr/bin/env bash
# The host side of `kirocrew-on single backup` (MVP 0.2.0 P2 Task 7, S31), run as root through SSM:
#   bash /opt/kirocrew-on/current/deploy/install/backup-upload.sh <<'KC_URL'
#   <a presigned PUT URL for one S3 object>
#   KC_URL
# A consistent backup of the platform database, taken as the platform's own user, packed and uploaded to that one
# object; the URL reaches curl on its standard input only, never a command line others on the host could read.
set -euo pipefail
IFS= read -r URL
[[ "$URL" =~ ^https://[A-Za-z0-9.-]+\.amazonaws\.com/[^[:space:]\"]+$ ]] || { echo "预签名 URL 不对" >&2; exit 2; }
D="$(mktemp -d)"
trap 'rm -rf "$D"' EXIT
chown octopus "$D"
runuser -u octopus -- /usr/local/bin/kirocrew-on --config /etc/kirocrew-on/platform.toml backup --dest "$D/backup" >/dev/null
tar -czf "$D/backup.tar.gz" -C "$D" backup
printf 'url = "%s"\nupload-file = "%s"\n' "$URL" "$D/backup.tar.gz" | curl -fsS -K - >/dev/null
stat -c %s "$D/backup.tar.gz"
