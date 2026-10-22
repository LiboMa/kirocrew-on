# shellcheck shell=bash
# The steps of one KiroCrew boundary on a host that may carry several — one per person (D49). Sourced by al2023.sh (every
# boundary on the host, at each deploy) and boundary.sh (one new boundary on the current release). Runs as root.
# The callers set NODE_ROOT, REGION and log(); ETC, DATA, SYSTEMD, ON_CURRENT and KC_CURRENT default to the host's paths.
: "${ETC:=/etc/kirocrew-on}"
: "${DATA:=/var/lib/octopus/boundaries}"
: "${SYSTEMD:=/etc/systemd/system}"
: "${ON_CURRENT:=/opt/kirocrew-on/current}"
: "${KC_CURRENT:=/opt/kirocrew/current}"
BOUNDARY_NAME='^[a-z][a-z0-9-]{1,30}$'

prune_releases() {  # prune_releases DIR CURRENT TARGET KEEP: remove releases but the current one, the one being installed and
  # the newest KEEP others (2026-10-20: 73 releases filled the 100 GB root disk); each is in the artifact bucket
  # (releases/<sha>.tar.gz) should a rollback need an older one
  local name kept=0
  for name in $(ls -1t "$1"); do
    [ -d "$1/$name" ] || continue
    if [ "$1/$name" = "$2" ] || [ "$1/$name" = "$3" ]; then continue; fi
    if [ "$kept" -lt "$4" ]; then kept=$((kept + 1)); continue; fi
    rm -rf -- "${1:?}/$name"
  done
}

toml_port() {  # toml_port FILE KEY: the port a top-level key of a boundary config holds
  grep -E "^$2[[:space:]]*=" "$1" | grep -oE '[0-9]+' | head -1 || true
}

other_boundaries() {  # other_boundaries PRIMARY: the other boundaries configured on this host, by name
  local file name
  for file in "$ETC"/boundary-*.toml; do
    [ -e "$file" ] || continue
    name="${file##*/boundary-}"
    name="${name%.toml}"
    [[ "$name" =~ $BOUNDARY_NAME ]] || continue   # boundary-<name>.check.toml is a check's copy, not a boundary
    [ "$name" = "$1" ] || printf '%s\n' "$name"
  done
}

free_port() {  # free_port LOW HIGH KEY: the first port in LOW..HIGH that no boundary config uses for KEY and nothing listens on
  local port file used
  for port in $(seq "$1" "$2"); do
    used=""
    for file in "$ETC"/boundary-*.toml; do
      [ -e "$file" ] && [ "$(toml_port "$file" "$3")" = "$port" ] && used=1 && break
    done
    [ -z "$used" ] || continue
    [ -z "$(ss -Hltn "sport = :$port" 2>/dev/null)" ] || continue
    echo "$port"
    return 0
  done
  echo "端口 $1–$2 已用尽（$3）" >&2
  return 1
}

boundary_env() {  # boundary_env NAME: the tools' environment (Claude Code and Codex through Bedrock with the instance role)
  cat > "$ETC/boundary-$1.env" <<EOF
CLAUDE_CODE_USE_BEDROCK=1
AWS_REGION=$REGION
PATH=/opt/kirocrew-tools/bin:$NODE_ROOT/bin:/usr/local/bin:/usr/bin:/bin
EOF
  chmod 0644 "$ETC/boundary-$1.env"
}

boundary_codex() {  # boundary_codex NAME: Codex through Amazon Bedrock for the boundary's OS user (written once, kept after)
  local crew="crew-$1" home="$DATA/$1/home"
  if [ ! -f "$home/.codex/config.toml" ]; then
    install -d -m 0700 -o "$crew" -g "$crew" "$home/.codex"
    printf 'model_provider = "amazon-bedrock"\nmodel = "openai.gpt-6-astra"\n' > "$home/.codex/config.toml"
    chown "$crew:$crew" "$home/.codex/config.toml"
  fi
}

boundary_limits() {  # boundary_limits NAME MEMORY_GIB CPU_PERCENT: drop-ins capping the boundary's Gateway and Worker
  [[ "$2" =~ ^[1-9][0-9]?$ ]] && [[ "$3" =~ ^[1-9][0-9]{1,3}$ ]] || {
    echo "资源上限无效：内存 $2 GiB、CPU $3%" >&2; return 2; }
  install -d -m 0755 "$SYSTEMD/octopus-gateway@$1.service.d" "$SYSTEMD/octopus-worker@$1.service.d"
  # MemoryHigh throttles before MemoryMax ends the tasks; the Gateway's fingerprint reads these files (boundary_restart).
  # OOMPolicy=continue: a process over the limit (one agent's build, one preview) ends alone, not every task of the unit.
  printf '[Service]\nMemoryHigh=%sM\nMemoryMax=%sG\nCPUQuota=%s%%\nTasksMax=4096\nOOMPolicy=continue\n' "$(( $2 * 768 ))" "$2" "$3" \
    > "$SYSTEMD/octopus-gateway@$1.service.d/limits.conf"
  # The Worker also runs the Session previews (npm install, dev servers): octopus/apps.py.
  printf '[Service]\nMemoryMax=4G\nOOMPolicy=continue\n' > "$SYSTEMD/octopus-worker@$1.service.d/limits.conf"
}

boundary_check() {  # boundary_check NAME TARGET KC: the native contract check of one boundary against the release being installed
  local crew="crew-$1" base="$DATA/$1" toml="$ETC/boundary-$1.toml" check="$ETC/boundary-$1.check.toml"
  local crew_env=(env HOME="$base/home" PATH="/opt/kirocrew-tools/bin:$NODE_ROOT/bin:/usr/local/bin:/usr/bin:/bin"
                  PYTHONPATH="$2")
  # Before the switch, current points at the previous release, or (on a fresh host) does not exist yet: the check reads a
  # copy of the boundary config with its current paths rewritten to the release being installed.
  sed -e "s#/opt/kirocrew-on/current/#$2/#g" -e "s#/opt/kirocrew/current/#$3/#g" "$toml" > "$check"
  chown "root:$crew" "$check"
  chmod 0640 "$check"
  # --check seeds the poc-* templates into KIRO_HOME and verifies every route before the native tests read them.
  runuser -u "$crew" -- "${crew_env[@]}" "$3/venv/bin/python" -s \
    -m composition.gateway --config "$check" --check
  runuser -u "$crew" -- "${crew_env[@]}" OCTOPUS_CONFIG="$check" \
    "$3/venv/bin/python" -s -m unittest discover -s "$2/tests/native"
  rm -f "$check"
}

boundary_versions() {  # boundary_versions NAME VERSION ACCEPTED: record the KiroCrew release the platform accepts for this boundary
  if [ "$2" = "$3" ]; then
    # -i.bak: the same on GNU sed (the host) and BSD sed
    sed -i.bak "s#^kirocrew_versions = .*#kirocrew_versions = [\"$2\"]#" "$ETC/boundary-$1.toml"
    rm -f "$ETC/boundary-$1.toml.bak"
  fi
}

boundary_restart() {  # boundary_restart NAME KC: the Gateway restarted only when what it loads changed, after draining; the Worker
  local name="$1" kc="$2" crew="crew-$1" base="$DATA/$1" toml="$ETC/boundary-$1.toml"
  # gateway:begin
  # The Gateway runs every native task: restart it only when what it loads changed (D34 §5.3), so a deploy of the
  # platform alone does not interrupt work in flight. The Worker and the API restart as before.
  local stamp="$base/gateway.fingerprint" print maintenance deadline running
  print="$( { readlink -f "$KC_CURRENT"
    cat "$ON_CURRENT"/composition/*.py "$ON_CURRENT/octopus/config.py" \
        "$ON_CURRENT/octopus/prompts.py" "$ON_CURRENT/octopus/__init__.py" \
        "$ON_CURRENT/pipeline_spec.py" "$toml" "$SYSTEMD/octopus-gateway@.service"
    cat "$SYSTEMD"/octopus-gateway@.service.d/*.conf "$SYSTEMD/octopus-gateway@$name.service.d/"*.conf \
        "$ETC/boundary-$name.env" 2>/dev/null || true; } | sha256sum | cut -d' ' -f1)"
  # A restart ends the native tasks in flight (postmortem #8, EC2 run-f7670bfb…): the Worker first stops taking work (its
  # .maintenance mark: the platform waits and retries) and the running tasks get up to DRAIN_MINUTES to end.
  maintenance="$base/worker/.maintenance"
  if [ "$(cat "$stamp" 2>/dev/null || true)" = "$print" ] && systemctl is-active --quiet "octopus-gateway@$name"; then
    log "边界 ${name}：Gateway 未变化，保持运行（进行中的原生任务不中断）"
  else
    if systemctl is-active --quiet "octopus-gateway@$name" && [ -d "$base/worker" ]; then
      printf 'deploy %s\n' "$(date -u +%FT%TZ)" > "$maintenance"   # the Worker only checks that it exists
      BOUNDARY_DRAINING="$maintenance"   # the installer's EXIT trap removes it if the drain is cut short
      deadline=$(( $(date +%s) + ${DRAIN_MINUTES:-30} * 60 ))
      while [ "$(date +%s)" -lt "$deadline" ]; do
        running="$(runuser -u "$crew" -- env PYTHONPATH="$ON_CURRENT" "$kc/venv/bin/python" -s -c '
import sys
from pathlib import Path
from octopus import gateway_client as g
try:
    agents = g.request("/api/spawn", port=int(sys.argv[1]), home=Path(sys.argv[2])).get("agents", [])
    print(sum(1 for a in agents if not a.get("done")))
except Exception:
    print(0)
' "$(toml_port "$toml" gateway_port)" "$base/crew" 2>/dev/null || echo 0)"
        [ "${running:-0}" = 0 ] && break
        log "边界 ${name}：等待 $running 个原生任务结束后重启 Gateway（最多 ${DRAIN_MINUTES:-30} 分钟）"
        sleep 20
      done
    fi
    # Explicit: called as a condition (al2023.sh, the other boundaries), set -e does not stop a failing step here.
    if ! systemctl restart "octopus-gateway@$name"; then
      rm -f "$maintenance"
      BOUNDARY_DRAINING=""
      echo "边界 ${name}：Gateway 未能重启（journalctl -u octopus-gateway@${name}）" >&2
      return 1
    fi
    printf '%s\n' "$print" > "$stamp"
  fi
  # gateway:end
  if ! systemctl restart "octopus-worker@$name"; then
    rm -f "$maintenance"
    BOUNDARY_DRAINING=""
    echo "边界 ${name}：Worker 未能重启（journalctl -u octopus-worker@${name}）" >&2
    return 1
  fi
  rm -f "$maintenance"   # the restarted Worker takes work again
  BOUNDARY_DRAINING=""
}
