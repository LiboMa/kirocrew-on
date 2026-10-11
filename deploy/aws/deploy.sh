#!/usr/bin/env bash
# KiroCrew ON on EC2 with Shell + CloudFormation (D15). Run from a kirocrew-on checkout:
#   deploy/aws/deploy.sh up            # validate, look up CloudFront prefix list and AZ, deploy the stack
#   deploy/aws/deploy.sh push          # publish the committed HEAD to S3 and install it on the host via SSM
#   deploy/aws/deploy.sh status        # stack outputs, SSM ping, https://<cloudfront>/health
#   deploy/aws/deploy.sh tunnel        # SSM port forwarding to the native KiroCrew entry (localhost:15476)
#   deploy/aws/deploy.sh kiro-login    # one-time kiro-cli device-flow login as crew-<boundary>
#   deploy/aws/deploy.sh admin-password
#   deploy/aws/deploy.sh down --yes    # delete the stack (artifact bucket and data volume are retained)
# DEPLOY_DRY_RUN=1 prints the AWS calls instead of running them (secrets masked).
set -euo pipefail
cd "$(dirname "$0")/../.."
# shellcheck source=stack.env
source deploy/aws/stack.env
TEMPLATE=deploy/aws/kirocrew-on-ec2.yaml
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

secret() {  # SecureString under /kirocrew-on/<stack>/<name>; created with a random value when absent
  local name="$PARAMS/$1" value
  if [ -n "$DRY" ]; then printf '%s' "${ORIGIN_SECRET:-dry-run-$1-000000000000000000000000}"; return; fi
  if ! value="$(aws --region "$REGION" ssm get-parameter --with-decryption --name "$name" \
      --query Parameter.Value --output text 2>/dev/null)"; then
    value="$(python3 -c 'import secrets; print(secrets.token_urlsafe(36))')"
    (umask 077; printf '%s' "$value" > "$WORK/secret")
    aws --region "$REGION" ssm put-parameter --type SecureString --name "$name" --value "file://$WORK/secret" >/dev/null
    rm -f "$WORK/secret"
  fi
  printf '%s' "$value"
}

output() {
  query "<$1>" cloudformation describe-stacks --stack-name "$STACK_NAME" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}

up() {
  run cloudformation validate-template --template-body "file://$TEMPLATE" >/dev/null
  local prefix_list zone origin
  prefix_list="$(query pl-dryrun ec2 describe-managed-prefix-lists \
    --filters Name=prefix-list-name,Values=com.amazonaws.global.cloudfront.origin-facing \
    --query 'PrefixLists[0].PrefixListId' --output text)"
  zone="$(query us-east-1a ec2 describe-instance-type-offerings --location-type availability-zone \
    --filters "Name=instance-type,Values=$INSTANCE_TYPE" --query 'sort(InstanceTypeOfferings[].Location)[0]' --output text)"
  [ -n "$zone" ] && [ "$zone" != "None" ] || { echo "$REGION 没有提供 $INSTANCE_TYPE 的可用区" >&2; exit 2; }
  origin="$(secret origin-secret)"
  secret admin-password >/dev/null
  run cloudformation deploy --stack-name "$STACK_NAME" --template-file "$TEMPLATE" --capabilities CAPABILITY_IAM \
    --no-fail-on-empty-changeset --tags Project=kirocrew-on \
    --parameter-overrides "InstanceType=$INSTANCE_TYPE" "VolumeSizeGiB=$VOLUME_SIZE_GIB" "AvailabilityZone=$zone" \
    "CloudFrontPrefixListId=$prefix_list" "OriginSecret=$origin" "ApiPort=$API_PORT" "AlarmEmail=$ALARM_EMAIL"
  [ -n "$DRY" ] || run cloudformation describe-stacks --stack-name "$STACK_NAME" --query 'Stacks[0].Outputs' --output table
}

push() {
  [ -z "$(git status --porcelain)" ] || { echo "工作区有未提交改动；push 只发布已提交的 commit" >&2; exit 2; }
  local sha bucket instance domain archive command_id status
  sha="$(git rev-parse HEAD)"
  bucket="$(output ArtifactBucket)"; instance="$(output InstanceId)"; domain="$(output CloudFrontDomain)"
  archive="$WORK/kirocrew-on-$sha.tar.gz"
  git archive --format=tar.gz --prefix="kirocrew-on-$sha/" -o "$archive" HEAD
  run s3 cp "$archive" "s3://$bucket/releases/$sha.tar.gz" --only-show-errors
  python3 - "$sha" "$bucket" "$domain" "$PARAMS" "$REGION" "$BOUNDARY" > "$WORK/commands.json" <<'PY'
import json, sys
sha, bucket, domain, params, region, boundary = sys.argv[1:]
d = "/opt/kirocrew-on/incoming"
script = (f"set -euo pipefail; mkdir -p {d}; aws s3 cp s3://{bucket}/releases/{sha}.tar.gz {d}/{sha}.tar.gz "
          f"--region {region} --only-show-errors; rm -rf {d}/kirocrew-on-{sha}; tar -xzf {d}/{sha}.tar.gz -C {d}; "
          f"KIROCREW_ON_RELEASE={sha} CF_DOMAIN={domain} ORIGIN_SECRET_PARAM={params}/origin-secret "
          f"ADMIN_PASSWORD_PARAM={params}/admin-password AWS_REGION={region} "
          f"bash {d}/kirocrew-on-{sha}/deploy/install/al2023.sh {boundary}")
print(json.dumps({"commands": [script], "executionTimeout": ["3600"]}))
PY
  command_id="$(query cmd-dryrun ssm send-command --instance-ids "$instance" --document-name AWS-RunShellScript \
    --comment "kirocrew-on $sha" --parameters "file://$WORK/commands.json" --query Command.CommandId --output text)"
  [ -n "$DRY" ] && return
  echo "SSM 命令 $command_id 已发送，等待安装完成（首次安装约 10–20 分钟）"
  while :; do
    status="$(aws --region "$REGION" ssm get-command-invocation --command-id "$command_id" --instance-id "$instance" \
      --query Status --output text 2>/dev/null || echo Pending)"
    case "$status" in Pending|InProgress|Delayed) sleep 15 ;; *) break ;; esac
  done
  aws --region "$REGION" ssm get-command-invocation --command-id "$command_id" --instance-id "$instance" \
    --query '[StandardOutputContent,StandardErrorContent]' --output text | tail -40
  [ "$status" = "Success" ] || { echo "安装状态：$status" >&2; exit 1; }
  echo "控制台：https://$domain/"
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

kiro_login() {
  local login="kiro-cli login --use-device-flow"
  [ -z "${KIRO_LICENSE:-}" ] || login="$login --license $KIRO_LICENSE"
  [ -z "${KIRO_IDC_START_URL:-}" ] || login="$login --identity-provider $KIRO_IDC_START_URL --region ${KIRO_IDC_REGION:-$REGION}"
  run ssm start-session --target "$(output InstanceId)" --document-name AWS-StartInteractiveCommand \
    --parameters "{\"command\":[\"sudo -iu crew-$BOUNDARY $login\"]}"
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
  kiro-login) kiro_login ;;
  admin-password) secret admin-password; echo ;;
  down) down "${2:-}" ;;
  *) sed -n '2,10p' "$0"; exit 2 ;;
esac
