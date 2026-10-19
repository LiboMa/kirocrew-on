#!/usr/bin/env bash
# KiroCrew ON on EC2 with Shell + CloudFormation (D15). Run from a kirocrew-on checkout:
#   deploy/aws/deploy.sh up            # validate, look up CloudFront prefix list and AZ, deploy the stack
#   deploy/aws/deploy.sh push          # publish the committed HEAD to S3 and install it on the host via SSM
#   deploy/aws/deploy.sh status        # stack outputs, SSM ping, https://<cloudfront>/health
#   deploy/aws/deploy.sh tunnel [--boundary <name>]  # SSM port forwarding to a boundary's native KiroCrew (localhost:10000+port)
#   deploy/aws/deploy.sh kiro-login [--host <name> | --boundary <name>]  # one-time kiro-cli device-flow login as crew-<boundary>
#   deploy/aws/deploy.sh boundary add <name> --owner <user> [--memory <GiB>] [--cpu <%>]  # one person's boundary (D49)
#   deploy/aws/deploy.sh boundaries    # the boundaries on the platform host: ports and service state
#   deploy/aws/deploy.sh worker-host <add|up|push|tls|native|register|status> <name>  # a Worker host for Teams (D28)
#   deploy/aws/deploy.sh space <run-id> [stop]  # run a worker run's project on the host and forward it (local = remote + 10000)
#   deploy/aws/deploy.sh spaces        # list the run projects running on the host
#   deploy/aws/deploy.sh admin-password
#   deploy/aws/deploy.sh down --yes    # delete the stack (artifact bucket and data volume are retained)
# DEPLOY_DRY_RUN=1 prints the AWS calls instead of running them (secrets masked).
set -euo pipefail
cd "$(dirname "$0")/../.."
# shellcheck source=stack.env
source deploy/aws/stack.env
TEMPLATE=deploy/aws/kirocrew-on-ec2.yaml
WORKER_TEMPLATE=deploy/aws/kirocrew-on-worker.yaml
WORKER_TLS_PORT="${WORKER_TLS_PORT:-8943}"
WORKER_GATEWAY_TLS_PORT="${WORKER_GATEWAY_TLS_PORT:-8944}"
NATIVE_RELAY_END="${NATIVE_RELAY_END:-$((NATIVE_PORT + 10))}"
PREVIEW_PORT="${PREVIEW_PORT:-$NATIVE_RELAY_END}"
WORKER_PREVIEW_TLS_PORT="${WORKER_PREVIEW_TLS_PORT:-8945}"
PARAMS="/kirocrew-on/$STACK_NAME"
DRY="${DEPLOY_DRY_RUN:-}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

mask() { sed -E 's/(OriginSecret=)[^ ]+/\1****/g'; }
run() {
  if [ -n "$DRY" ]; then printf 'aws --region %s %s\n' "$REGION" "$*" | mask; else aws --region "$REGION" "$@"; fi
}
query() {  # read-only lookups also print in dry-run mode, then return a placeholder
  if [ -n "$DRY" ]; then printf 'aws --region %s %s\n' "$REGION" "${*:2}" >&2; echo "$1"; else aws --region "$REGION" "${@:2}"; fi
}

secret() {  # SecureString under /kirocrew-on/<stack>/<name>; created with a random value only when it does not exist
  local name="$PARAMS/$1" value
  if [ -n "$DRY" ]; then printf '%s' "${ORIGIN_SECRET:-dry-run-$1-000000000000000000000000}"; return; fi
  if value="$(aws --region "$REGION" ssm get-parameter --with-decryption --name "$name" \
      --query Parameter.Value --output text 2>"$WORK/ssm.err")"; then
    printf '%s' "$value"
    return
  fi
  # Throttling, KMS or access errors stop here: a fresh value would give CloudFront and the host different secrets.
  grep -q ParameterNotFound "$WORK/ssm.err" || { cat "$WORK/ssm.err" >&2; return 1; }
  value="$(python3 -c 'import secrets; print(secrets.token_urlsafe(36))')"
  (umask 077; printf '%s' "$value" > "$WORK/secret")
  aws --region "$REGION" ssm put-parameter --type SecureString --name "$name" --value "file://$WORK/secret" >/dev/null \
    || { rm -f "$WORK/secret"; return 1; }
  rm -f "$WORK/secret"
  printf '%s' "$value"
}

output() {  # output KEY [STACK]
  query "<$1>" cloudformation describe-stacks --stack-name "${2:-$STACK_NAME}" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}

stack_parameter() {  # stack_parameter STACK KEY: a deployed stack's parameter; "None" without the key, empty without the
  # stack. Any other failure (throttling, credentials) fails rather than reading as absent.
  local err="$WORK/stack-parameter.err"
  if [ -n "$DRY" ]; then
    query "" cloudformation describe-stacks --stack-name "$1" \
      --query "Stacks[0].Parameters[?ParameterKey=='$2'].ParameterValue" --output text
    return
  fi
  aws --region "$REGION" cloudformation describe-stacks --stack-name "$1" \
    --query "Stacks[0].Parameters[?ParameterKey=='$2'].ParameterValue" --output text 2>"$err" && return
  grep -q "does not exist" "$err" || { cat "$err" >&2; return 1; }
}

change_stack() {  # change_stack STACK TEMPLATE PARAMETERS_FILE CREATE|UPDATE: a change set, refused if it replaces anything
  local stack=$1 file=$2 parameters=$3 type=$4 change reason replaced
  change="kirocrew-on-$(date -u +%Y%m%d%H%M%S)"
  run cloudformation create-change-set --stack-name "$stack" --change-set-name "$change" --change-set-type "$type" \
    --template-body "file://$file" --capabilities CAPABILITY_IAM --tags Key=Project,Value=kirocrew-on \
    --parameters "file://$parameters"
  [ -n "$DRY" ] && return
  if ! aws --region "$REGION" cloudformation wait change-set-create-complete --stack-name "$stack" \
      --change-set-name "$change" 2>/dev/null; then
    reason="$(aws --region "$REGION" cloudformation describe-change-set --stack-name "$stack" --change-set-name "$change" \
      --query StatusReason --output text)"
    aws --region "$REGION" cloudformation delete-change-set --stack-name "$stack" --change-set-name "$change" || true
    case "$reason" in
      *"didn't contain changes"*|*"No updates are to be performed"*) echo "栈没有需要更新的内容"; return ;;
    esac
    echo "变更集创建失败：${reason}" >&2
    exit 1
  fi
  # Replacing a host (or anything it depends on) would strand its data on a detached volume.
  replaced="$(aws --region "$REGION" cloudformation describe-change-set --stack-name "$stack" --change-set-name "$change" \
    --query "Changes[?ResourceChange.Replacement=='True' || ResourceChange.Replacement=='Conditional'].ResourceChange.LogicalResourceId" \
    --output text)"
  if [ -n "$replaced" ] && [ "$replaced" != "None" ] && [ -z "${ALLOW_REPLACEMENT:-}" ]; then
    aws --region "$REGION" cloudformation delete-change-set --stack-name "$stack" --change-set-name "$change"
    echo "变更会替换资源：${replaced}。替换主机会让数据留在旧卷上；确认迁移方案后以 ALLOW_REPLACEMENT=1 重新执行" >&2
    exit 3
  fi
  aws --region "$REGION" cloudformation execute-change-set --stack-name "$stack" --change-set-name "$change"
  aws --region "$REGION" cloudformation wait "stack-$([ "$type" = CREATE ] && echo create || echo update)-complete" \
    --stack-name "$stack"
}

console_zone() {  # console_zone NAME: the public Route 53 zone holding NAME (the longest matching suffix)
  local zones zone name best="" id=""
  zones="$(query "/hostedzone/ZDRYRUN"$'\t'"${1#*.}." route53 list-hosted-zones \
    --query 'HostedZones[?Config.PrivateZone==`false`].[Id,Name]' --output text)" || return 1
  while read -r zone name; do
    name="${name%.}"
    if [ -n "$name" ] && { [ "$1" = "$name" ] || [[ "$1" == *".$name" ]]; } && [ ${#name} -gt ${#best} ]; then
      best="$name"; id="${zone##*/}"
    fi
  done <<<"$zones"
  [ -n "$id" ] || { echo "Route 53 中没有包含 $1 的公有托管区：先创建托管区，或清空 stack.env 的 CONSOLE_DOMAIN / PREVIEW_SESSION_DOMAIN" >&2; return 1; }
  echo "$id"
}

console_certificate() {  # console_certificate NAME: an issued certificate naming NAME or *.<parent>, in us-east-1 (the
  # only region CloudFront takes certificates from, whatever REGION is)
  local wildcard="*.${1#*.}" filter arn
  filter="CertificateSummaryList[?DomainName=='$1' || DomainName=='$wildcard' || contains(SubjectAlternativeNameSummaries || \`[]\`, '$1') || contains(SubjectAlternativeNameSummaries || \`[]\`, '$wildcard')].CertificateArn | [0]"
  if [ -n "$DRY" ]; then
    printf 'aws --region us-east-1 acm list-certificates --certificate-statuses ISSUED --query %s\n' "$filter" >&2
    echo "arn:aws:acm:us-east-1:000000000000:certificate/dry-run"; return
  fi
  arn="$(aws --region us-east-1 acm list-certificates --certificate-statuses ISSUED --query "$filter" --output text)" || return 1
  [[ "$arn" == arn:aws:acm:us-east-1:* ]] || {
    echo "us-east-1 没有覆盖 $1 的已签发 ACM 证书（${1} 或 ${wildcard}）：先申请证书并完成 DNS 验证" >&2; return 1; }
  echo "$arn"
}

preview_certificate() {  # preview_certificate DOMAIN ZONE_ID: the us-east-1 certificate for *.DOMAIN (each Session's
  # preview host, D36 phase 3) — an issued one, else a pending or newly requested one validated through a CNAME in ZONE_ID
  local name="*.$1" found arn state record waited=0 limit="${DEPLOY_CERT_WAIT_SECONDS:-2400}"
  if [ -n "$DRY" ]; then
    printf 'aws --region us-east-1 acm list-certificates --certificate-statuses ISSUED PENDING_VALIDATION（%s；没有时 request-certificate 并在 %s 写验证记录）\n' \
      "$name" "$2" >&2
    echo "arn:aws:acm:us-east-1:000000000000:certificate/dry-run-preview"; return
  fi
  # JSON, not text: the CLI applies --query to each page of text output separately.
  found="$(aws --region us-east-1 acm list-certificates --certificate-statuses ISSUED PENDING_VALIDATION \
    --query "CertificateSummaryList[?DomainName=='$name'].[CertificateArn,Status]" --output json)" || return 1
  read -r arn state <<<"$(python3 -c '
import json, sys
rows = [r for r in (json.loads(sys.stdin.read() or "[]") or []) if isinstance(r, list) and len(r) == 2]
rows.sort(key=lambda r: r[1] != "ISSUED")   # an issued certificate before one an earlier run left pending
print(*(rows[0] if rows else []))' <<<"$found")"
  if [[ "$arn" != arn:aws:acm:us-east-1:* ]]; then
    echo "申请 us-east-1 通配证书 ${name}（DNS 验证，记录写入托管区 ${2}）" >&2
    # One token per domain: ACM returns the same certificate for the same token within an hour.
    arn="$(aws --region us-east-1 acm request-certificate --domain-name "$name" --validation-method DNS \
      --idempotency-token "$(python3 -c 'import hashlib, sys; print(hashlib.sha256(sys.argv[1].encode()).hexdigest()[:32])' "$1")" \
      --query CertificateArn --output text)" || return 1
    state=PENDING_VALIDATION
  fi
  if [ "$state" != ISSUED ]; then
    for _ in $(seq 1 30); do   # ACM fills in the validation record a few seconds after the request
      record="$(aws --region us-east-1 acm describe-certificate --certificate-arn "$arn" \
        --query 'Certificate.DomainValidationOptions[0].ResourceRecord.[Name,Value]' --output text)" || return 1
      [[ "$record" == *$'\t'* && "$record" != None* ]] && break
      sleep 2
    done
    [[ "$record" == *$'\t'* && "$record" != None* ]] || { echo "ACM 未给出 ${name} 的验证记录" >&2; return 1; }
    python3 - "${record%%$'\t'*}" "${record##*$'\t'}" > "$WORK/validation.json" <<'PY'
import json, sys
name, value = sys.argv[1:]
print(json.dumps({"Changes": [{"Action": "UPSERT", "ResourceRecordSet": {"Name": name, "Type": "CNAME", "TTL": 300,
                                                                          "ResourceRecords": [{"Value": value}]}}]}))
PY
    aws route53 change-resource-record-sets --hosted-zone-id "$2" --change-batch "file://$WORK/validation.json" >/dev/null || return 1
    echo "等待证书 ${name} 完成验证（通常几分钟，最多等 $((limit / 60)) 分钟）" >&2
    while :; do
      state="$(aws --region us-east-1 acm describe-certificate --certificate-arn "$arn" --query 'Certificate.Status' \
        --output text)" || return 1
      [ "$state" = ISSUED ] && break
      [ "$state" = PENDING_VALIDATION ] || { echo "证书 ${name} 验证失败（${state}）" >&2; return 1; }
      [ "$waited" -lt "$limit" ] || {
        echo "证书 ${name} 尚未验证完成：DNS 验证记录已写入，栈未改动；稍后重新运行 up 即可（沿用这张证书）" >&2; return 1; }
      sleep 20; waited=$((waited + 20))
    done
  fi
  echo "$arn"
}

up() {
  run cloudformation validate-template --template-body "file://$TEMPLATE" >/dev/null
  local status prefix_list zone="${AVAILABILITY_ZONE:-}" image="${IMAGE_ID:-}" current origin type
  status="$(query "" cloudformation describe-stacks --stack-name "$STACK_NAME" --query 'Stacks[0].StackStatus' \
    --output text 2>/dev/null || true)"
  prefix_list="$(query pl-dryrun ec2 describe-managed-prefix-lists \
    --filters Name=prefix-list-name,Values=com.amazonaws.global.cloudfront.origin-facing \
    --query 'PrefixLists[0].PrefixListId' --output text)"
  if [ -n "$status" ] && { [ -z "$zone" ] || [ -z "$image" ]; }; then
    # An existing host keeps its image and zone: a different ImageId or subnet zone would replace it.
    current="$(query "ami-dryrun us-east-1a" ec2 describe-instances --instance-ids "$(output InstanceId)" \
      --query 'Reservations[0].Instances[0].[ImageId,Placement.AvailabilityZone]' --output text)"
    [ -n "$image" ] || image="$(awk '{print $1}' <<<"$current")"
    [ -n "$zone" ] || zone="$(awk '{print $2}' <<<"$current")"
  fi
  [ -n "$zone" ] || zone="$(query us-east-1a ec2 describe-instance-type-offerings --location-type availability-zone \
    --filters "Name=instance-type,Values=$INSTANCE_TYPE" --query 'sort(InstanceTypeOfferings[].Location)[0]' --output text)"
  [ -n "$zone" ] && [ "$zone" != "None" ] || { echo "$REGION 没有提供 $INSTANCE_TYPE 的可用区" >&2; exit 2; }
  [ -n "$image" ] || image="$(query ami-dryrun ssm get-parameter \
    --name /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64 --query Parameter.Value --output text)"
  [ -n "$status" ] || echo "首次创建：可用区 ${zone}，AMI ${image}；创建后写入 deploy/aws/stack.env 的 AVAILABILITY_ZONE 与 IMAGE_ID" >&2
  # The console's own domain (optional): its zone and certificate are looked up, never kept in the repository.
  local console="${CONSOLE_DOMAIN:-}" console_zone_id="" console_certificate_arn=""
  if [ -n "$console" ]; then
    console_zone_id="$(console_zone "$console")" || exit 2
    console_certificate_arn="$(console_certificate "$console")" || exit 2
  fi
  # Each Session's preview on its own origin (optional, D36 phase 3): one wildcard certificate, found or requested.
  local session="${PREVIEW_SESSION_DOMAIN:-}" session_zone_id="" session_certificate_arn=""
  if [ -n "$session" ]; then
    session_zone_id="$(console_zone "$session")" || exit 2
    session_certificate_arn="$(preview_certificate "$session" "$session_zone_id")" || exit 2
  fi
  origin="$(secret origin-secret)"
  # Parameters go through a 0600 file: the origin secret never appears on a command line.
  (umask 077; ORIGIN_VALUE="$origin" python3 - "$INSTANCE_TYPE" "$VOLUME_SIZE_GIB" "$zone" "$image" "$prefix_list" \
    "$API_PORT" "$NATIVE_PORT" "$NATIVE_RELAY_END" "$ALARM_EMAIL" "$console" "$console_certificate_arn" "$console_zone_id" \
    "$PREVIEW_PORT" "$session" "$session_certificate_arn" "$session_zone_id" > "$WORK/parameters.json" <<'PY'
import json, os, sys
keys = ["InstanceType", "VolumeSizeGiB", "AvailabilityZone", "ImageId", "CloudFrontPrefixListId", "ApiPort", "NativePort",
        "NativeRelayEnd", "AlarmEmail", "ConsoleDomain", "ConsoleCertificateArn", "ConsoleHostedZoneId", "PreviewPort",
        "PreviewSessionDomain", "PreviewCertificateArn", "PreviewHostedZoneId"]
values = dict(zip(keys, sys.argv[1:]), OriginSecret=os.environ["ORIGIN_VALUE"])
print(json.dumps([{"ParameterKey": k, "ParameterValue": v} for k, v in values.items()]))
PY
  )
  type=UPDATE
  [ -n "$status" ] || type=CREATE
  change_stack "$STACK_NAME" "$TEMPLATE" "$WORK/parameters.json" "$type"
  [ -n "$DRY" ] && return
  run cloudformation describe-stacks --stack-name "$STACK_NAME" --query 'Stacks[0].Outputs' --output table
}

push() {
  [ -z "$(git status --porcelain)" ] || { echo "工作区有未提交改动；push 只发布已提交的 commit" >&2; exit 2; }
  local sha bucket instance domain native console preview session archive command_id
  sha="$(git rev-parse HEAD)"
  bucket="$(output ArtifactBucket)"; instance="$(output InstanceId)"; domain="$(output CloudFrontDomain)"
  native="$(output NativeDomain)"; console="$(output ConsoleDomain)"; preview="$(output PreviewDomain)"
  session="$(output PreviewSessionDomain)"
  [ "$console" != "None" ] || console=""   # a stack from before ConsoleDomain
  [ "$preview" != "None" ] || preview=""   # a stack from before the remote preview (D36): no preview server
  [ "$session" != "None" ] || session=""   # no per-session preview origins (D36 phase 3)
  archive="$WORK/kirocrew-on-$sha.tar.gz"
  git archive --format=tar.gz --prefix="kirocrew-on-$sha/" -o "$archive" HEAD
  run s3 cp "$archive" "s3://$bucket/releases/$sha.tar.gz" --only-show-errors
  python3 - "$sha" "$bucket" "$domain" "$PARAMS" "$REGION" "$BOUNDARY" "$native" "$NATIVE_PORT" "$console" "$preview" \
    "$PREVIEW_PORT" "$session" > "$WORK/commands.json" <<'PY'
import json, sys
sha, bucket, domain, params, region, boundary, native, native_port, console, preview, preview_port, session = sys.argv[1:]
d = "/opt/kirocrew-on/incoming"
script = (f"set -euo pipefail; mkdir -p {d}; aws s3 cp s3://{bucket}/releases/{sha}.tar.gz {d}/{sha}.tar.gz "
          f"--region {region} --only-show-errors; rm -rf {d}/kirocrew-on-{sha}; tar -xzf {d}/{sha}.tar.gz -C {d}; "
          f"KIROCREW_ON_RELEASE={sha} CF_DOMAIN={domain} CONSOLE_DOMAIN={console} NATIVE_DOMAIN={native} "
          f"NATIVE_PORT={native_port} PREVIEW_DOMAIN={preview} PREVIEW_PORT={preview_port} "
          f"PREVIEW_SESSION_DOMAIN={session} "
          f"ORIGIN_SECRET_PARAM={params}/origin-secret "
          f"AWS_REGION={region} "
          f"bash {d}/kirocrew-on-{sha}/deploy/install/al2023.sh {boundary}")
print(json.dumps({"commands": [script], "executionTimeout": ["3600"]}))
PY
  command_id="$(query cmd-dryrun ssm send-command --instance-ids "$instance" --document-name AWS-RunShellScript \
    --comment "kirocrew-on $sha" --parameters "file://$WORK/commands.json" --query Command.CommandId --output text)"
  [ -n "$DRY" ] && return
  wait_command "$instance" "$command_id"
  echo "控制台：https://${console:-$domain}/"
}

wait_command() {  # wait_command INSTANCE COMMAND_ID: wait for an install command, print its tail, fail unless it succeeded
  local status
  echo "SSM 命令 $2 已发送，等待安装完成（首次安装约 10–20 分钟）"
  while :; do
    status="$(aws --region "$REGION" ssm get-command-invocation --command-id "$2" --instance-id "$1" \
      --query Status --output text 2>/dev/null || echo Pending)"
    case "$status" in Pending|InProgress|Delayed) sleep 15 ;; *) break ;; esac
  done
  aws --region "$REGION" ssm get-command-invocation --command-id "$2" --instance-id "$1" \
    --query '[StandardOutputContent,StandardErrorContent]' --output text | tail -40
  [ "$status" = "Success" ] || { echo "安装状态：$status" >&2; exit 1; }
}

status() {
  run cloudformation describe-stacks --stack-name "$STACK_NAME" --query 'Stacks[0].[StackStatus,Outputs]' --output json
  local instance domain
  instance="$(output InstanceId)"; domain="$(output CloudFrontDomain)"
  run ssm describe-instance-information --filters "Key=InstanceIds,Values=$instance" \
    --query 'InstanceInformationList[0].[PingStatus,PlatformName,AgentVersion]' --output text
  [ -n "$DRY" ] || curl -fsS "https://$domain/health" && echo
}

tunnel() {  # tunnel [--boundary <name>]: the native KiroCrew of a boundary on localhost:<10000 + its Gateway port>
  local port=5476 instance
  instance="$(output InstanceId)"
  if [ -n "${1:-}" ]; then
    [ "$1" = --boundary ] && [[ "${2:-}" =~ ^[a-z][a-z0-9-]{1,19}$ ]] || {
      echo "用法：deploy.sh tunnel [--boundary <边界名称>]" >&2; exit 2; }
    local script="grep -E '^gateway_port' /etc/kirocrew-on/boundary-$2.toml | grep -oE '[0-9]+'"
    if [ -n "$DRY" ]; then printf '%s\n' "$script"; port=5481; else port="$(remote "$instance" "$script")"; fi
  fi
  run ssm start-session --target "$instance" --document-name AWS-StartPortForwardingSession \
    --parameters "{\"portNumber\":[\"$port\"],\"localPortNumber\":[\"$((port + 10000))\"]}"
}

remote() {  # run a shell command on the host through SSM; print its stdout, or its stderr and fail when it fails
  local id status
  id="$(aws --region "$REGION" ssm send-command --instance-ids "$1" --document-name AWS-RunShellScript \
    --parameters "$(python3 -c 'import json, sys; print(json.dumps({"commands": [sys.argv[1]]}))' "$2")" \
    --query Command.CommandId --output text)"
  while :; do
    status="$(aws --region "$REGION" ssm get-command-invocation --command-id "$id" --instance-id "$1" \
      --query Status --output text 2>/dev/null || echo Pending)"
    case "$status" in Pending|InProgress|Delayed) sleep 2 ;; *) break ;; esac
  done
  aws --region "$REGION" ssm get-command-invocation --command-id "$id" --instance-id "$1" \
    --query StandardOutputContent --output text
  [ "$status" = Success ] || { aws --region "$REGION" ssm get-command-invocation --command-id "$id" \
    --instance-id "$1" --query StandardErrorContent --output text >&2; return 1; }
}

# Runs as root on the host. A worker run's project is started with `npm run dev` as the boundary's OS user in a transient
# systemd unit, on the first free loopback port from 5173 (Vite gets --host/--port; other dev servers get HOST/PORT).
# A unit that is already active is reused. Prints PORT=<port> once the port listens.
read -r -d '' SPACE_START <<'SH' || true
set -euo pipefail
[ -f "$DIR/package.json" ] || { echo "$DIR 下没有 package.json" >&2; exit 3; }
grep -Eq '"dev"[[:space:]]*:' "$DIR/package.json" || { echo "$DIR/package.json 没有 dev 脚本" >&2; exit 3; }
as_crew=(--quiet --uid="$CREW" --gid="$CREW" -p WorkingDirectory="$DIR" -p UMask=0077)
if ! systemctl is-active --quiet "$UNIT"; then
  systemctl reset-failed "$UNIT" 2>/dev/null || true
  PORT=5173
  while ss -Hltn "sport = :$PORT" | grep -q .; do PORT=$((PORT + 1)); done
  as_crew+=(--setenv=HOME="$CREW_HOME" --setenv=PATH="$NODE_BIN:/usr/local/bin:/usr/bin:/bin"
            --setenv=HOST=127.0.0.1 --setenv=PORT="$PORT")
  [ -d "$DIR/node_modules" ] || systemd-run --wait --pipe "${as_crew[@]}" "$NODE_BIN/npm" ci --no-audit --no-fund >&2
  args=()
  if grep -Eq '"dev"[[:space:]]*:[[:space:]]*"vite' "$DIR/package.json"; then
    args=(-- --host 127.0.0.1 --port "$PORT" --strictPort)
  fi
  systemd-run --unit="$UNIT" "${as_crew[@]}" "$NODE_BIN/npm" run dev "${args[@]}"
fi
PORT="$(systemctl show -p Environment --value "$UNIT" | tr ' ' '\n' | sed -n 's/^PORT=//p')"
for _ in $(seq 1 60); do
  if ss -Hltn "sport = :$PORT" | grep -q .; then echo "PORT=$PORT"; exit 0; fi
  sleep 1
done
journalctl -u "$UNIT" -n 40 --no-pager >&2
exit 1
SH

space() {
  local run="${1:-}" action="${2:-start}" instance script port
  [[ "$run" =~ ^run-[0-9a-f]{32}$ ]] && [[ "$action" =~ ^(start|stop)$ ]] || {
    echo "用法：deploy.sh space <run-id> [stop]；run-id 形如 run-<32 位十六进制>" >&2; exit 2; }
  local unit="kirocrew-space-$run"
  instance="$(output InstanceId)"
  if [ "$action" = stop ]; then
    script="systemctl stop $unit 2>/dev/null || true; systemctl reset-failed $unit 2>/dev/null || true; echo 已停止 $unit"
    if [ -n "$DRY" ]; then printf '%s\n' "$script"; else remote "$instance" "$script"; fi
    return
  fi
  # D49: whichever boundary on the host holds the Session
  script="D=\"\$(ls -d /var/lib/octopus/boundaries/*/worker/$run 2>/dev/null | head -1)\"; B=\"\${D%/worker/$run}\"; B=\"\${B##*/}\""$'\n'
  script="$script"'[ -n "$D" ] || { echo "主机上没有会话 '"$run"' 的工作区" >&2; exit 3; }'$'\n'
  script="${script}DIR=\"/var/lib/octopus/boundaries/\$B/worker/$run/workspace/project\" UNIT=$unit CREW=\"crew-\$B\""
  script="$script CREW_HOME=\"/var/lib/octopus/boundaries/\$B/home\" NODE_BIN=/opt/kirocrew-node/bin"$'\n'"$SPACE_START"
  if [ -n "$DRY" ]; then printf '%s\n' "$script"; port=5173
  else port="$(remote "$instance" "$script" | sed -n 's/^PORT=//p')"; fi
  echo "远端 $unit 监听 127.0.0.1:${port}；本机打开 http://localhost:$((port + 10000))/"
  echo "Ctrl-C 只结束转发，远端应用继续运行；停止：deploy/aws/deploy.sh space $run stop"
  run ssm start-session --target "$instance" --document-name AWS-StartPortForwardingSession \
    --parameters "{\"portNumber\":[\"$port\"],\"localPortNumber\":[\"$((port + 10000))\"]}"
}

spaces() {
  local script='for u in $(systemctl list-units --plain --no-legend "kirocrew-space-*" | cut -d" " -f1); do
  p="$(systemctl show -p Environment --value "$u" | tr " " "\n" | sed -n "s/^PORT=//p")"; r="${u%.service}"
  echo "${r#kirocrew-space-} 127.0.0.1:$p -> localhost:$((p + 10000))"; done'
  if [ -n "$DRY" ]; then printf '%s\n' "$script"; else remote "$(output InstanceId)" "$script"; fi
}

worker_host() {  # worker-host <add|up|push|tls|native|register|status> <name>: a dedicated Worker host for Teams (D28)
  local action="${1:-}" name="${2:-}"
  [[ "$action" =~ ^(add|up|push|tls|native|register|status)$ ]] && [[ "$name" =~ ^[a-z][a-z0-9-]{1,19}$ ]] || {
    echo "用法：deploy.sh worker-host <add|up|push|tls|native|register|status> <名称>；名称为小写字母开头的 2–20 位小写字母、数字或连字符" >&2
    exit 2; }
  "worker_$action" "$name" "$STACK_NAME-worker-$name"
}

relay_port() {  # relay_port STACK: the Worker host's relay port on the platform host. A host keeps its port unless another
  # host has it too (both created at once); otherwise it takes the lowest free one in the platform's deployed range.
  # Runs in a command substitution, where set -e does not apply: every lookup failure exits explicitly.
  local own end used port preview
  own="$(stack_parameter "$1" RelayPort)" || exit 2
  end="$(stack_parameter "$STACK_NAME" NativeRelayEnd)" || exit 2
  preview="$(stack_parameter "$STACK_NAME" PreviewPort)" || exit 2   # the remote preview's port is never a relay (D36)
  if [ -n "$DRY" ]; then end="${end:-$NATIVE_RELAY_END}"; else
    [[ "$end" =~ ^[0-9]+$ ]] || { echo "平台栈还没有中转端口范围（NativeRelayEnd）：先执行 deploy.sh up" >&2; exit 2; }
  fi
  used=" $(query "" cloudformation describe-stacks --output text --query \
    "Stacks[?starts_with(StackName, '$STACK_NAME-worker-') && StackName != '$1'].Parameters[] | [?ParameterKey=='RelayPort'].ParameterValue") " ||
    { echo "无法读取其他 Worker 主机的中转端口，稍后重试" >&2; exit 2; }
  if [[ "$own" =~ ^[0-9]+$ ]] && [ "$own" != "$preview" ] && [[ "$used" != *[[:space:]]$own[[:space:]]* ]]; then
    echo "$own"; return
  fi
  for ((port = NATIVE_PORT + 1; port <= end; port++)); do
    [ "$port" != "$preview" ] || continue
    [[ "$used" == *[[:space:]]$port[[:space:]]* ]] || { echo "$port"; return; }
  done
  echo "平台主机的中转端口 $((NATIVE_PORT + 1))–$end 已用完：调大 stack.env 的 NATIVE_RELAY_END 并先执行 deploy.sh up" >&2
  exit 2
}

worker_add() {  # a new (or partly set up) Worker host end to end; every step is idempotent and a failure stops the run
  local name=$1 stack=$2 step
  for step in up push tls native register; do
    echo "== worker-host $step $name"
    "worker_$step" "$name" "$stack"
  done
  echo "== kiro-login --host $name"
  kiro_login --host "$name"
  echo "Worker 主机 $name 已登记为空闲执行环境：在控制台「执行环境」中分配给 Team"
}

worker_up() {  # the Worker host stack in the platform's VPC and subnet, admitted only by the platform host
  local name=$1 stack=$2 status type relay domain origin
  run cloudformation validate-template --template-body "file://$WORKER_TEMPLATE" >/dev/null
  status="$(query "" cloudformation describe-stacks --stack-name "$stack" --query 'Stacks[0].StackStatus' \
    --output text 2>/dev/null || true)"
  type=UPDATE
  [ -n "$status" ] || type=CREATE
  relay="$(relay_port "$stack")"
  domain="$(output HostOriginDomain)"
  [ -n "$DRY" ] || [[ "$domain" == ec2-* ]] || { echo "平台栈没有输出 HostOriginDomain：先执行 deploy.sh up" >&2; exit 2; }
  # The native distribution injects the platform's origin secret; like up, it only travels in a 0600 file.
  origin="$(secret origin-secret)"
  [[ "$origin" =~ ^[A-Za-z0-9_-]{32,}$ ]] || {
    echo "源站密钥格式不符：平台主机的中转只接受 32 位以上的字母、数字、- 或 _（与 kirocrew-native.conf 相同）" >&2; exit 2; }
  (umask 077; ORIGIN_VALUE="$origin" python3 - "$(output VpcId)" "$(output SubnetId)" "$(output HostSecurityGroupId)" \
    "${IMAGE_ID:-$(output ImageId)}" "${WORKER_INSTANCE_TYPE:-$INSTANCE_TYPE}" "${WORKER_VOLUME_SIZE_GIB:-$VOLUME_SIZE_GIB}" \
    "$(output ArtifactBucket)" "$WORKER_TLS_PORT" "$STACK_NAME" "$WORKER_GATEWAY_TLS_PORT" "$relay" "$domain" \
    "$WORKER_PREVIEW_TLS_PORT" > "$WORK/worker-parameters.json" <<'PY'
import json, os, sys
keys = ["VpcId", "SubnetId", "PlatformSecurityGroupId", "ImageId", "InstanceType", "VolumeSizeGiB", "ArtifactBucket",
        "WorkerPort", "BackupTag", "GatewayTlsPort", "RelayPort", "PlatformOriginDomain", "PreviewTlsPort"]
values = dict(zip(keys, sys.argv[1:]), OriginSecret=os.environ["ORIGIN_VALUE"])
print(json.dumps([{"ParameterKey": k, "ParameterValue": v} for k, v in values.items()]))
PY
  )
  [ -z "$DRY" ] || python3 -c 'import json, sys
print(json.dumps([dict(p, ParameterValue="****") if p["ParameterKey"] == "OriginSecret" else p for p in json.load(open(sys.argv[1]))]))' \
    "$WORK/worker-parameters.json"
  change_stack "$stack" "$WORKER_TEMPLATE" "$WORK/worker-parameters.json" "$type"
  [ -n "$DRY" ] || run cloudformation describe-stacks --stack-name "$stack" --query 'Stacks[0].Outputs' --output table
}

worker_push() {  # the committed HEAD, installed in Worker host mode (WORKER_HOST=1) with one dedicated boundary <name>
  local name=$1 stack=$2 sha bucket instance native archive command_id
  [ -n "$DRY" ] || [ -z "$(git status --porcelain)" ] || { echo "工作区有未提交改动；只发布已提交的 commit" >&2; exit 2; }
  sha="$(git rev-parse HEAD)"
  bucket="$(output ArtifactBucket)"; instance="$(output InstanceId "$stack")"; native="$(output NativeDomain "$stack")"
  [ -n "$DRY" ] || [[ "$native" =~ ^[a-z0-9]+\.cloudfront\.net$ ]] || {
    echo "Worker 主机栈没有原生入口分发：先执行 deploy.sh worker-host up $name" >&2; exit 2; }
  archive="$WORK/kirocrew-on-$sha.tar.gz"
  git archive --format=tar.gz --prefix="kirocrew-on-$sha/" -o "$archive" HEAD
  run s3 cp "$archive" "s3://$bucket/releases/$sha.tar.gz" --only-show-errors
  python3 - "$sha" "$bucket" "$REGION" "$name" "$WORKER_TLS_PORT" "$WORKER_GATEWAY_TLS_PORT" "$native" \
    "$WORKER_PREVIEW_TLS_PORT" > "$WORK/commands.json" <<'PY'
import json, sys
sha, bucket, region, name, port, gateway_port, native, preview_port = sys.argv[1:]
d = "/opt/kirocrew-on/incoming"
script = (f"set -euo pipefail; mkdir -p {d}; aws s3 cp s3://{bucket}/releases/{sha}.tar.gz {d}/{sha}.tar.gz "
          f"--region {region} --only-show-errors; rm -rf {d}/kirocrew-on-{sha}; tar -xzf {d}/{sha}.tar.gz -C {d}; "
          f"WORKER_HOST=1 WORKER_TLS_PORT={port} GATEWAY_TLS_PORT={gateway_port} NATIVE_DOMAIN={native} "
          f"WORKER_PREVIEW_TLS_PORT={preview_port} "
          f"KIROCREW_ON_RELEASE={sha} AWS_REGION={region} "
          f"bash {d}/kirocrew-on-{sha}/deploy/install/al2023.sh {name}")
print(json.dumps({"commands": [script], "executionTimeout": ["3600"]}))
PY
  [ -z "$DRY" ] || cat "$WORK/commands.json"
  command_id="$(query cmd-dryrun ssm send-command --instance-ids "$instance" --document-name AWS-RunShellScript \
    --comment "kirocrew-on worker $name $sha" --parameters "file://$WORK/commands.json" --query Command.CommandId --output text)"
  [ -n "$DRY" ] && return
  wait_command "$instance" "$command_id"
}

worker_tls() {  # the Worker host's key stays on it; the platform's private CA signs its CSR (SAN = private IP + name)
  local name=$1 stack=$2 worker platform ip request sign install csr="<CSR>" cert="<certificate>"
  worker="$(output InstanceId "$stack")"; platform="$(output InstanceId)"; ip="$(output PrivateIp "$stack")"
  [ -n "$DRY" ] || [[ "$ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "无法取得 Worker 主机私网地址：$ip" >&2; exit 1; }
  request="set -euo pipefail; umask 077; K=/etc/kirocrew-on/worker-tls.key; [ -s \$K ] || openssl genrsa -out \$K 3072 2>/dev/null; openssl req -new -key \$K -subj /CN=$name"
  # The Worker host is a lower-trust boundary: only a well-formed PEM block reaches the root script on the platform.
  [ -n "$DRY" ] || csr="$(remote "$worker" "$request" | python3 -I deploy/aws/pem.py "CERTIFICATE REQUEST")"
  sign="set -euo pipefail; umask 077; D=\$(mktemp -d); trap 'rm -rf \$D' EXIT; cat > \$D/request.csr <<'PEM_END'
$csr
PEM_END
printf 'subjectAltName=IP:%s,DNS:$name.worker.kirocrew-on\nextendedKeyUsage=serverAuth\n' $ip > \$D/ext; openssl x509 -req -in \$D/request.csr -CA /etc/kirocrew-on/worker-ca.pem -CAkey /etc/kirocrew-on/worker-ca.key -CAserial /etc/kirocrew-on/worker-ca.srl -CAcreateserial -days 825 -extfile \$D/ext 2>/dev/null"
  [ -n "$DRY" ] || cert="$(remote "$platform" "$sign" | python3 -I deploy/aws/pem.py CERTIFICATE)"
  install="set -euo pipefail; cat > /etc/kirocrew-on/worker-tls.crt <<'PEM_END'
$cert
PEM_END
chmod 0644 /etc/kirocrew-on/worker-tls.crt; install -m 0644 /etc/kirocrew-on/nginx-worker.conf /etc/nginx/conf.d/kirocrew-worker.conf; [ ! -s /etc/kirocrew-on/nginx-worker-preview.conf ] || install -m 0640 -o root -g nginx /etc/kirocrew-on/nginx-worker-preview.conf /etc/nginx/conf.d/kirocrew-worker-preview.conf; nginx -t -q; systemctl enable nginx >/dev/null; systemctl reload-or-restart nginx; echo 已安装证书"
  if [ -n "$DRY" ]; then printf '%s\n\n' "$request" "$sign" "$install"; return; fi
  remote "$worker" "$install"
  echo "平台经 TLS 访问 Worker（不带 Token，期望 401）：$(remote "$platform" "curl -s -o /dev/null -w '%{http_code}' --cacert /etc/kirocrew-on/worker-ca.pem https://$ip:$WORKER_TLS_PORT/health || true")"
}

# Runs as root on the platform host: renders kirocrew-relay.conf for one Worker host from files only (the origin secret
# never appears on a command line), then checks the chain through the relay with the secret in a 0600 header file.
read -r -d '' RELAY_INSTALL <<'SH' || true
set -euo pipefail
# Two relays on one port: nginx only warns, and the second host's distribution would reach the first host's Gateway.
for other in /etc/nginx/conf.d/kirocrew-relay-*.conf; do
  if [ -e "$other" ] && [ "$other" != "$CONF" ] && grep -qE "^[[:space:]]*listen $PORT;" "$other"; then
    echo "中转端口 $PORT 已被 $other 使用：对后建的 Worker 主机重新执行 deploy.sh worker-host up <名称> 换端口；该主机已删除时删除此文件" >&2
    exit 3
  fi
done
[ -s /etc/nginx/conf.d/kirocrew-native.conf ] && [ -f /opt/kirocrew-on/current/deploy/nginx/kirocrew-relay.conf ] || {
  echo "平台主机尚未启用原生入口或发布中没有 kirocrew-relay.conf：先执行 deploy.sh push" >&2; exit 3; }
umask 027
/opt/kirocrew-on/venv/bin/python -I - /opt/kirocrew-on/current/deploy/nginx/kirocrew-relay.conf /etc/kirocrew-on/origin-secret \
  "$NAME" "$IP" "$PORT" "$GATEWAY_TLS_PORT" > "$CONF.new" <<'PY'
import re, sys
template, secret_file, name, ip, port, gateway_tls_port = sys.argv[1:]
secret = open(secret_file).read().strip()
if not re.fullmatch(r"[A-Za-z0-9_-]{32,}", secret):
    sys.exit("源站密钥缺失或格式不符")
text = open(template).read()
for key, value in {"__ORIGIN_SECRET__": secret, "__WORKER_NAME__": name, "__WORKER_IP__": ip, "__RELAY_PORT__": port,
                   "__GATEWAY_TLS_PORT__": gateway_tls_port}.items():
    text = text.replace(key, value)
print(text, end="")
PY
umask 022
# Upstream errors log the request line, one-time ?token= included: root-only, unlike /var/log/nginx/error.log.
install -d -m 0700 /var/log/nginx/kirocrew-relay
printf '%s\n' '/var/log/nginx/kirocrew-relay/*.log {' '    daily' '    rotate 14' '    missingok' '    notifempty' \
  '    compress' '    delaycompress' '    sharedscripts' '    postrotate' \
  '        /bin/kill -USR1 "$(cat /run/nginx.pid 2>/dev/null)" 2>/dev/null || true' '    endscript' '}' \
  > /etc/logrotate.d/kirocrew-relay
chown root:nginx "$CONF.new"
chmod 0640 "$CONF.new"
mv -f "$CONF.new" "$CONF"
if command -v getenforce >/dev/null 2>&1 && [ "$(getenforce)" = Enforcing ]; then
  semanage port -a -t http_port_t -p tcp "$PORT" 2>/dev/null || semanage port -m -t http_port_t -p tcp "$PORT"
fi
nginx -t -q || { rm -f "$CONF"; exit 1; }
systemctl reload nginx
# reload returns before the new port listens.
for _ in $(seq 1 20); do ss -Hltn "sport = :$PORT" | grep -q . && break; sleep 0.5; done
H="$(mktemp)"; trap 'rm -f "$H"' EXIT
printf 'X-Origin-Verify: %s\n' "$(tr -d '[:space:]' < /etc/kirocrew-on/origin-secret)" > "$H"
code="$(curl -s -o /dev/null -w '%{http_code}' -H @"$H" "http://127.0.0.1:$PORT/" || true)"
echo "中转 :$PORT → https://$IP:$GATEWAY_TLS_PORT（$NAME.worker.kirocrew-on）：HTTP $code（000 或 502 表示 TLS 或 Worker 主机 nginx 未就绪）"
SH

worker_native() {  # the platform host relays the Worker host's native distribution to its Gateway (D28)
  local name=$1 stack=$2 platform ip port native script
  platform="$(output InstanceId)"; ip="$(output PrivateIp "$stack")"; native="$(output NativeDomain "$stack")"
  port="$(stack_parameter "$stack" RelayPort)" || exit 1
  if [ -n "$DRY" ]; then port="${port:-<RelayPort>}"; else
    [[ "$ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "无法取得 Worker 主机私网地址：$ip" >&2; exit 1; }
    [[ "$port" =~ ^[0-9]+$ ]] || { echo "Worker 主机栈没有 RelayPort：先执行 deploy.sh worker-host up $name" >&2; exit 1; }
  fi
  script="NAME=$name IP=$ip PORT=$port GATEWAY_TLS_PORT=$WORKER_GATEWAY_TLS_PORT CONF=/etc/nginx/conf.d/kirocrew-relay-$name.conf"
  script="$script"$'\n'"$RELAY_INSTALL"
  if [ -n "$DRY" ]; then printf '%s\n' "$script"; return; fi
  remote "$platform" "$script"
  echo "原生入口：https://$native/（worker-host push 写入 crew_url 后，控制台「在 KiroCrew 中打开」经此打开）"
}

worker_register() {  # register the Worker host as a spare execution environment; an admin assigns it to a Team (D28)
  local name=$1 stack=$2 worker platform ip token="dry-run-token" script
  worker="$(output InstanceId "$stack")"; platform="$(output InstanceId)"; ip="$(output PrivateIp "$stack")"
  [ -n "$DRY" ] || [[ "$ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "无法取得 Worker 主机私网地址：$ip" >&2; exit 1; }
  # The token travels in an SSM command, so it stays in the SSM command history (same trade-off as admin-password).
  [ -n "$DRY" ] || token="$(remote "$worker" "cat /var/lib/octopus/boundaries/$name/worker.token" | tr -d '[:space:]')"
  [ -n "$DRY" ] || [[ "$token" =~ ^[A-Za-z0-9_-]{32,}$ ]] || { echo "未读到有效的 Worker Token" >&2; exit 1; }
  script="set -euo pipefail; T=\$(mktemp); trap 'rm -f \$T' EXIT; printf '%s' '$token' > \$T; cd /opt/kirocrew-on/current; OCTOPUS_ADMIN_PASSWORD=\"\$(cat /etc/kirocrew-on/admin-password)\" PYTHONPATH=/opt/kirocrew-on/current /opt/kirocrew-on/venv/bin/python -m octopus --config /etc/kirocrew-on/platform.toml bootstrap-worker --spare --worker-url https://$ip:$WORKER_TLS_PORT --token-file \$T --name $name"
  if [ -n "$DRY" ]; then printf '%s\n' "${script//$token/****}"; return; fi
  remote "$platform" "$script"
}

worker_status() {
  local name=$1 stack=$2 worker ip
  run cloudformation describe-stacks --stack-name "$stack" --query 'Stacks[0].[StackStatus,Outputs]' --output json
  worker="$(output InstanceId "$stack")"; ip="$(output PrivateIp "$stack")"
  run ssm describe-instance-information --filters "Key=InstanceIds,Values=$worker" \
    --query 'InstanceInformationList[0].[PingStatus,PlatformName,AgentVersion]' --output text
  [ -n "$DRY" ] && return
  echo "平台经 TLS 访问 Worker（不带 Token，期望 401）：$(remote "$(output InstanceId)" "curl -s -o /dev/null -w '%{http_code}' --cacert /etc/kirocrew-on/worker-ca.pem https://$ip:$WORKER_TLS_PORT/health || true")"
}

kiro_login() {
  # kiro-cli still prompts with every flag set and fails without a terminal (observed on EC2), so the device flow runs
  # under script(1) on the host; two carriage returns accept the remaining prompts. Every attempt writes its own log.
  # kiro-cli can stay running after "Logged in successfully" (observed on w1): that line ends the attempt, and whoami
  # gives the verdict.
  local login="kiro-cli login --use-device-flow" instance shown="" finished="" boundary="$BOUNDARY" stack="$STACK_NAME"
  local log whoami
  if [ "${1:-}" = "--host" ]; then   # a Worker host (D28): its boundary carries the host's name
    [[ "${2:-}" =~ ^[a-z][a-z0-9-]{1,19}$ ]] || { echo "用法：deploy.sh kiro-login [--host <Worker 主机名称> | --boundary <边界名称>]" >&2; exit 2; }
    boundary="$2"; stack="$STACK_NAME-worker-$2"
  elif [ "${1:-}" = "--boundary" ]; then   # another boundary on the platform host (D49): that person's own account
    [[ "${2:-}" =~ ^[a-z][a-z0-9-]{1,19}$ ]] || { echo "用法：deploy.sh kiro-login [--host <Worker 主机名称> | --boundary <边界名称>]" >&2; exit 2; }
    boundary="$2"
  elif [ -n "${1:-}" ]; then
    echo "用法：deploy.sh kiro-login [--host <Worker 主机名称> | --boundary <边界名称>]" >&2; exit 2
  fi
  log="/var/lib/octopus/boundaries/$boundary/logs/kiro-login-$(date -u +%Y%m%dT%H%M%SZ).log"
  [ -z "${KIRO_LICENSE:-}" ] || login="$login --license $KIRO_LICENSE"
  [ -z "${KIRO_IDC_START_URL:-}" ] || login="$login --identity-provider $KIRO_IDC_START_URL --region ${KIRO_IDC_REGION:-$REGION}"
  instance="$(output InstanceId "$stack")"
  # kiro-cli refuses to log in again over a valid login (worker-host add reruns this step on an existing host).
  if [ -z "$DRY" ]; then
    whoami="$(remote "$instance" "sudo -iu crew-$boundary kiro-cli whoami 2>&1 | head -1" || true)"
    if [[ "$whoami" == "Logged in"* ]]; then
      printf '%s\ncrew-%s 已登录 kiro-cli；更换账号时先在主机上以 crew-%s 执行 kiro-cli logout\n' "$whoami" "$boundary" "$boundary"
      return
    fi
  fi
  python3 - "crew-$boundary" "$login" "$log" > "$WORK/login.json" <<'PY'
import json, shlex, sys
user, login, log = sys.argv[1:]
inner = (f"script -q -f -c {shlex.quote(login)} {shlex.quote(log)} "
         f"< <(sleep 3; printf '\\r'; sleep 2; printf '\\r'; for _ in $(seq 1 450); do sleep 2; done); "
         f"echo exit=$? >> {shlex.quote(log)}")
script = f"pkill -u {shlex.quote(user)} -f 'kiro-cli login' || true; cd /tmp; sudo -iu {shlex.quote(user)} bash -c {shlex.quote(inner)}"
print(json.dumps({"commands": [script], "executionTimeout": ["960"]}))
PY
  [ -z "$DRY" ] || cat "$WORK/login.json"
  query cmd-dryrun ssm send-command --instance-ids "$instance" --document-name AWS-RunShellScript \
    --comment "kiro-cli device login" --parameters "file://$WORK/login.json" --query Command.CommandId --output text >/dev/null
  [ -n "$DRY" ] && return
  for _ in $(seq 1 12); do
    sleep 5
    shown="$(remote "$instance" "grep -aE 'Code:|https://' $log 2>/dev/null | sed 's/\x1b\[[0-9;?]*[a-zA-Z]//g' | tr -d '\r' || true")"
    [ -z "$shown" ] || break
  done
  [ -n "$shown" ] || { echo "未在 $log 中看到设备码" >&2; exit 1; }
  printf '%s\n在浏览器中打开上述地址并输入设备码（15 分钟内有效）；完成后这里自动确认\n' "$shown"
  for _ in $(seq 1 90); do
    sleep 10
    finished="$(remote "$instance" "grep -ao 'exit=[0-9]*' $log || { grep -aqF 'Logged in successfully' $log && echo signed-in; } || true")"
    [ -z "$finished" ] || break
  done
  remote "$instance" "pkill -u crew-$boundary -f 'kiro-cli login' || true" >/dev/null
  whoami="$(remote "$instance" "sudo -iu crew-$boundary kiro-cli whoami 2>&1 | head -1")"
  echo "$whoami"
  [[ "$whoami" == "Logged in"* ]] || { echo "kiro-cli login 未完成（${finished:-超时}），日志：$log" >&2; exit 1; }
}

boundary() {  # boundary add <name> --owner <username> [--memory <GiB>] [--cpu <percent>]: one person's boundary (D49)
  local usage="用法：deploy.sh boundary add <名称> --owner <平台用户名> [--memory <GiB>] [--cpu <百分比>]"
  [ "${1:-}" = add ] || { echo "$usage" >&2; exit 2; }
  local name="${2:-}" owner="" memory=8 cpu=300
  shift 2 || true
  while [ $# -gt 0 ]; do
    case "$1" in
      --owner) owner="${2:-}"; shift 2 || shift ;;
      --memory) memory="${2:-}"; shift 2 || shift ;;
      --cpu) cpu="${2:-}"; shift 2 || shift ;;
      *) echo "$usage" >&2; exit 2 ;;
    esac
  done
  # The values become part of a root shell command on the host: only these forms reach it.
  [[ "$name" =~ ^[a-z][a-z0-9-]{1,19}$ ]] && [ "$name" != personal ] && [[ "$owner" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]] &&
    [[ "$memory" =~ ^[1-9][0-9]?$ ]] && [[ "$cpu" =~ ^[1-9][0-9]{1,3}$ ]] || { echo "$usage" >&2; exit 2; }
  local script="test -x /opt/kirocrew-on/current/deploy/install/boundary.sh || { echo '当前发布没有 boundary.sh：先 deploy.sh push' >&2; exit 3; }; "
  script="${script}bash /opt/kirocrew-on/current/deploy/install/boundary.sh $name --owner $owner --memory $memory --cpu $cpu"
  if [ -n "$DRY" ]; then printf '%s\n' "$script"; return; fi
  remote "$(output InstanceId)" "$script"
}

boundaries() {  # every boundary on the platform host: its ports and whether its Gateway and Worker run (D49)
  local script='for f in /etc/kirocrew-on/boundary-*.toml; do n="${f##*/boundary-}"; n="${n%.toml}"
  case "$n" in *.*) continue ;; esac
  g="$(grep -E "^gateway_port" "$f" | grep -oE "[0-9]+")"; w="$(grep -E "^worker_port" "$f" | grep -oE "[0-9]+")"
  echo "$n gateway 127.0.0.1:$g ($(systemctl is-active "octopus-gateway@$n")) worker 127.0.0.1:$w ($(systemctl is-active "octopus-worker@$n"))"
done'
  if [ -n "$DRY" ]; then printf '%s\n' "$script"; else remote "$(output InstanceId)" "$script"; fi
}

admin_password() {  # generated on the host at first install, root-only; never kept in SSM Parameter Store
  local instance
  instance="$(output InstanceId)"
  if [ -n "$DRY" ]; then
    printf 'aws --region %s ssm send-command --instance-ids %s --document-name AWS-RunShellScript (cat /etc/kirocrew-on/admin-password)\n' \
      "$REGION" "$instance"
    return
  fi
  remote "$instance" "cat /etc/kirocrew-on/admin-password 2>/dev/null || { echo '主机上还没有 /etc/kirocrew-on/admin-password：按 docs/OPERATIONS.md「更新栈与管理员密码」完成迁移' >&2; exit 1; }"
}

down() {
  [ "${1:-}" = "--yes" ] || { echo "删除栈需要 --yes；部署桶与数据卷会保留，需另行清理" >&2; exit 2; }
  run cloudformation delete-stack --stack-name "$STACK_NAME"
  run cloudformation wait stack-delete-complete --stack-name "$STACK_NAME"
}

case "${1:-}" in
  up) up ;;
  push) push ;;
  status) status ;;
  tunnel) tunnel "${2:-}" "${3:-}" ;;
  boundary) shift; boundary "$@" ;;
  boundaries) boundaries ;;
  kiro-login) kiro_login "${2:-}" "${3:-}" ;;
  worker-host) worker_host "${2:-}" "${3:-}" ;;
  space) space "${2:-}" "${3:-}" ;;
  spaces) spaces ;;
  admin-password) admin_password ;;
  down) down "${2:-}" ;;
  *) sed -n '2,15p' "$0"; exit 2 ;;
esac
