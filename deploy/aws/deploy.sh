#!/usr/bin/env bash
# KiroCrew ON on EC2 with Shell + CloudFormation (D15). Run from a kirocrew-on checkout:
#   deploy/aws/deploy.sh up            # validate, look up CloudFront prefix list and AZ, deploy the stack
#   deploy/aws/deploy.sh push          # publish the committed HEAD to S3 and install it on the host via SSM
#   deploy/aws/deploy.sh status        # stack outputs, SSM ping, https://<cloudfront>/health
#   deploy/aws/deploy.sh tunnel        # SSM port forwarding to the native KiroCrew entry (localhost:15476)
#   deploy/aws/deploy.sh kiro-login [--host <name>]  # one-time kiro-cli device-flow login as crew-<boundary>
#   deploy/aws/deploy.sh worker-host <up|push|tls|register|status> <name>  # a dedicated Worker host for Teams (D28)
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
  origin="$(secret origin-secret)"
  # Parameters go through a 0600 file: the origin secret never appears on a command line.
  (umask 077; ORIGIN_VALUE="$origin" python3 - "$INSTANCE_TYPE" "$VOLUME_SIZE_GIB" "$zone" "$image" "$prefix_list" \
    "$API_PORT" "$NATIVE_PORT" "$ALARM_EMAIL" > "$WORK/parameters.json" <<'PY'
import json, os, sys
keys = ["InstanceType", "VolumeSizeGiB", "AvailabilityZone", "ImageId", "CloudFrontPrefixListId", "ApiPort", "NativePort", "AlarmEmail"]
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
  local sha bucket instance domain native archive command_id
  sha="$(git rev-parse HEAD)"
  bucket="$(output ArtifactBucket)"; instance="$(output InstanceId)"; domain="$(output CloudFrontDomain)"
  native="$(output NativeDomain)"
  archive="$WORK/kirocrew-on-$sha.tar.gz"
  git archive --format=tar.gz --prefix="kirocrew-on-$sha/" -o "$archive" HEAD
  run s3 cp "$archive" "s3://$bucket/releases/$sha.tar.gz" --only-show-errors
  python3 - "$sha" "$bucket" "$domain" "$PARAMS" "$REGION" "$BOUNDARY" "$native" "$NATIVE_PORT" > "$WORK/commands.json" <<'PY'
import json, sys
sha, bucket, domain, params, region, boundary, native, native_port = sys.argv[1:]
d = "/opt/kirocrew-on/incoming"
script = (f"set -euo pipefail; mkdir -p {d}; aws s3 cp s3://{bucket}/releases/{sha}.tar.gz {d}/{sha}.tar.gz "
          f"--region {region} --only-show-errors; rm -rf {d}/kirocrew-on-{sha}; tar -xzf {d}/{sha}.tar.gz -C {d}; "
          f"KIROCREW_ON_RELEASE={sha} CF_DOMAIN={domain} NATIVE_DOMAIN={native} NATIVE_PORT={native_port} "
          f"ORIGIN_SECRET_PARAM={params}/origin-secret "
          f"AWS_REGION={region} "
          f"bash {d}/kirocrew-on-{sha}/deploy/install/al2023.sh {boundary}")
print(json.dumps({"commands": [script], "executionTimeout": ["3600"]}))
PY
  command_id="$(query cmd-dryrun ssm send-command --instance-ids "$instance" --document-name AWS-RunShellScript \
    --comment "kirocrew-on $sha" --parameters "file://$WORK/commands.json" --query Command.CommandId --output text)"
  [ -n "$DRY" ] && return
  wait_command "$instance" "$command_id"
  echo "控制台：https://$domain/"
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

tunnel() {
  run ssm start-session --target "$(output InstanceId)" --document-name AWS-StartPortForwardingSession \
    --parameters '{"portNumber":["5476"],"localPortNumber":["15476"]}'
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
  script="DIR=/var/lib/octopus/boundaries/$BOUNDARY/worker/$run/workspace/project UNIT=$unit CREW=crew-$BOUNDARY"
  script="$script CREW_HOME=/var/lib/octopus/boundaries/$BOUNDARY/home NODE_BIN=/opt/kirocrew-node/bin"$'\n'"$SPACE_START"
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

worker_host() {  # worker-host <up|push|tls|register|status> <name>: a dedicated Worker host for Teams (D28)
  local action="${1:-}" name="${2:-}"
  [[ "$action" =~ ^(up|push|tls|register|status)$ ]] && [[ "$name" =~ ^[a-z][a-z0-9-]{1,19}$ ]] || {
    echo "用法：deploy.sh worker-host <up|push|tls|register|status> <名称>；名称为小写字母开头的 2–20 位小写字母、数字或连字符" >&2
    exit 2; }
  "worker_$action" "$name" "$STACK_NAME-worker-$name"
}

worker_up() {  # the Worker host stack in the platform's VPC and subnet, admitted only by the platform host
  local name=$1 stack=$2 status type
  run cloudformation validate-template --template-body "file://$WORKER_TEMPLATE" >/dev/null
  status="$(query "" cloudformation describe-stacks --stack-name "$stack" --query 'Stacks[0].StackStatus' \
    --output text 2>/dev/null || true)"
  type=UPDATE
  [ -n "$status" ] || type=CREATE
  python3 - "$(output VpcId)" "$(output SubnetId)" "$(output HostSecurityGroupId)" "${IMAGE_ID:-$(output ImageId)}" \
    "${WORKER_INSTANCE_TYPE:-$INSTANCE_TYPE}" "${WORKER_VOLUME_SIZE_GIB:-$VOLUME_SIZE_GIB}" "$(output ArtifactBucket)" \
    "$WORKER_TLS_PORT" "$STACK_NAME" > "$WORK/worker-parameters.json" <<'PY'
import json, sys
keys = ["VpcId", "SubnetId", "PlatformSecurityGroupId", "ImageId", "InstanceType", "VolumeSizeGiB", "ArtifactBucket",
        "WorkerPort", "BackupTag"]
print(json.dumps([{"ParameterKey": k, "ParameterValue": v} for k, v in zip(keys, sys.argv[1:])]))
PY
  [ -z "$DRY" ] || cat "$WORK/worker-parameters.json"
  change_stack "$stack" "$WORKER_TEMPLATE" "$WORK/worker-parameters.json" "$type"
  [ -n "$DRY" ] || run cloudformation describe-stacks --stack-name "$stack" --query 'Stacks[0].Outputs' --output table
}

worker_push() {  # the committed HEAD, installed in Worker host mode (WORKER_HOST=1) with one dedicated boundary <name>
  local name=$1 stack=$2 sha bucket instance archive command_id
  [ -n "$DRY" ] || [ -z "$(git status --porcelain)" ] || { echo "工作区有未提交改动；只发布已提交的 commit" >&2; exit 2; }
  sha="$(git rev-parse HEAD)"
  bucket="$(output ArtifactBucket)"; instance="$(output InstanceId "$stack")"
  archive="$WORK/kirocrew-on-$sha.tar.gz"
  git archive --format=tar.gz --prefix="kirocrew-on-$sha/" -o "$archive" HEAD
  run s3 cp "$archive" "s3://$bucket/releases/$sha.tar.gz" --only-show-errors
  python3 - "$sha" "$bucket" "$REGION" "$name" "$WORKER_TLS_PORT" > "$WORK/commands.json" <<'PY'
import json, sys
sha, bucket, region, name, port = sys.argv[1:]
d = "/opt/kirocrew-on/incoming"
script = (f"set -euo pipefail; mkdir -p {d}; aws s3 cp s3://{bucket}/releases/{sha}.tar.gz {d}/{sha}.tar.gz "
          f"--region {region} --only-show-errors; rm -rf {d}/kirocrew-on-{sha}; tar -xzf {d}/{sha}.tar.gz -C {d}; "
          f"WORKER_HOST=1 WORKER_TLS_PORT={port} KIROCREW_ON_RELEASE={sha} AWS_REGION={region} "
          f"bash {d}/kirocrew-on-{sha}/deploy/install/al2023.sh {name}")
print(json.dumps({"commands": [script], "executionTimeout": ["3600"]}))
PY
  [ -z "$DRY" ] || cat "$WORK/commands.json"
  command_id="$(query cmd-dryrun ssm send-command --instance-ids "$instance" --document-name AWS-RunShellScript \
    --comment "kirocrew-on worker $name $sha" --parameters "file://$WORK/commands.json" --query Command.CommandId --output text)"
  [ -n "$DRY" ] && return
  wait_command "$instance" "$command_id"
}

worker_tls() {  # the Worker host's key stays on it; the platform's private CA signs its CSR (SAN = private IP)
  local name=$1 stack=$2 worker platform ip request sign install csr="<CSR>" cert="<certificate>"
  worker="$(output InstanceId "$stack")"; platform="$(output InstanceId)"; ip="$(output PrivateIp "$stack")"
  [ -n "$DRY" ] || [[ "$ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "无法取得 Worker 主机私网地址：$ip" >&2; exit 1; }
  request="set -euo pipefail; umask 077; K=/etc/kirocrew-on/worker-tls.key; [ -s \$K ] || openssl genrsa -out \$K 3072 2>/dev/null; openssl req -new -key \$K -subj /CN=$name"
  # The Worker host is a lower-trust boundary: only a well-formed PEM block reaches the root script on the platform.
  [ -n "$DRY" ] || csr="$(remote "$worker" "$request" | python3 -I deploy/aws/pem.py "CERTIFICATE REQUEST")"
  sign="set -euo pipefail; umask 077; D=\$(mktemp -d); trap 'rm -rf \$D' EXIT; cat > \$D/request.csr <<'PEM_END'
$csr
PEM_END
printf 'subjectAltName=IP:%s\nextendedKeyUsage=serverAuth\n' $ip > \$D/ext; openssl x509 -req -in \$D/request.csr -CA /etc/kirocrew-on/worker-ca.pem -CAkey /etc/kirocrew-on/worker-ca.key -CAserial /etc/kirocrew-on/worker-ca.srl -CAcreateserial -days 825 -extfile \$D/ext 2>/dev/null"
  [ -n "$DRY" ] || cert="$(remote "$platform" "$sign" | python3 -I deploy/aws/pem.py CERTIFICATE)"
  install="set -euo pipefail; cat > /etc/kirocrew-on/worker-tls.crt <<'PEM_END'
$cert
PEM_END
chmod 0644 /etc/kirocrew-on/worker-tls.crt; install -m 0644 /etc/kirocrew-on/nginx-worker.conf /etc/nginx/conf.d/kirocrew-worker.conf; nginx -t -q; systemctl enable nginx >/dev/null; systemctl reload-or-restart nginx; echo 已安装证书"
  if [ -n "$DRY" ]; then printf '%s\n\n' "$request" "$sign" "$install"; return; fi
  remote "$worker" "$install"
  echo "平台经 TLS 访问 Worker（不带 Token，期望 401）：$(remote "$platform" "curl -s -o /dev/null -w '%{http_code}' --cacert /etc/kirocrew-on/worker-ca.pem https://$ip:$WORKER_TLS_PORT/health || true")"
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
  # under script(1) on the host; two carriage returns accept the remaining prompts. The feeder is a process substitution,
  # so the result is recorded as soon as kiro-cli exits, and every attempt writes its own log.
  local login="kiro-cli login --use-device-flow" instance shown="" finished="" boundary="$BOUNDARY" stack="$STACK_NAME"
  local log
  if [ "${1:-}" = "--host" ]; then   # a Worker host (D28): its boundary carries the host's name
    [[ "${2:-}" =~ ^[a-z][a-z0-9-]{1,19}$ ]] || { echo "用法：deploy.sh kiro-login [--host <Worker 主机名称>]" >&2; exit 2; }
    boundary="$2"; stack="$STACK_NAME-worker-$2"
  fi
  log="/var/lib/octopus/boundaries/$boundary/logs/kiro-login-$(date -u +%Y%m%dT%H%M%SZ).log"
  [ -z "${KIRO_LICENSE:-}" ] || login="$login --license $KIRO_LICENSE"
  [ -z "${KIRO_IDC_START_URL:-}" ] || login="$login --identity-provider $KIRO_IDC_START_URL --region ${KIRO_IDC_REGION:-$REGION}"
  instance="$(output InstanceId "$stack")"
  python3 - "crew-$boundary" "$login" "$log" > "$WORK/login.json" <<'PY'
import json, shlex, sys
user, login, log = sys.argv[1:]
inner = (f"script -q -f -c {shlex.quote(login)} {shlex.quote(log)} "
         f"< <(sleep 3; printf '\\r'; sleep 2; printf '\\r'; sleep 900); echo exit=$? >> {shlex.quote(log)}")
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
    finished="$(remote "$instance" "grep -ao 'exit=[0-9]*' $log || true")"
    [ -z "$finished" ] || break
  done
  remote "$instance" "sudo -iu crew-$boundary kiro-cli whoami 2>&1 | head -1"
  [ "$finished" = "exit=0" ] || { echo "kiro-cli login 结束状态：${finished:-超时}" >&2; exit 1; }
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
  tunnel) tunnel ;;
  kiro-login) kiro_login "${2:-}" "${3:-}" ;;
  worker-host) worker_host "${2:-}" "${3:-}" ;;
  space) space "${2:-}" "${3:-}" ;;
  spaces) spaces ;;
  admin-password) admin_password ;;
  down) down "${2:-}" ;;
  *) sed -n '2,13p' "$0"; exit 2 ;;
esac
