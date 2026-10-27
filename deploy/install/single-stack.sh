#!/usr/bin/env bash
# The single-node edition's CloudFront stack (MVP 0.2.0 P2 Task 4, design §4.2.4). install.sh runs it as root with the
# installing credentials — the instance role, or AK/SK in its environment, used by this process only, never written down:
#   single-stack.sh up <file>    make or update the stack kirocrew-on-single; its domains go to <file> as CF_DOMAIN=… lines
#   single-stack.sh check-role   after the install: does the host role (EC2_ROLE) still hold write permissions? (S29)
# EC2_REGION, EC2_INSTANCE_ID and EC2_ROLE come from install.sh's preflight; INSTALL_FS_ROOT moves /etc (tests).
set -euo pipefail
STACK=kirocrew-on-single
HERE="$(cd "$(dirname "$0")" && pwd)"
TEMPLATE="$HERE/../aws/kirocrew-on-single.yaml"
POLICY="$HERE/../aws/kirocrew-on-single-install-policy.json"
REGION="${EC2_REGION:?需要 EC2_REGION}"
ETC="${INSTALL_FS_ROOT:-}/etc/kirocrew-on"
SECRET="$ETC/origin-secret"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
# shellcheck source=os-lib.sh
command -v aws >/dev/null 2>&1 || { . "$HERE/os-lib.sh"; os_detect; install_aws_cli; }
# what an Agent must not be able to do with the host role (S29): change AWS resources or reach other hosts
WRITES=(cloudformation:CreateStack cloudformation:CreateChangeSet cloudfront:CreateDistribution cloudfront:UpdateDistribution
        ec2:RunInstances ec2:AuthorizeSecurityGroupIngress ec2:AllocateAddress ec2:AssociateAddress ec2:CreateTags
        ec2:CreateVolume ec2:AttachVolume ec2:ModifyInstanceAttribute iam:CreateRole iam:AttachRolePolicy iam:PutRolePolicy
        iam:PassRole iam:CreateAccessKey s3:PutObject s3:DeleteObject dlm:CreateLifecyclePolicy ssm:SendCommand
        lambda:CreateFunction)

need_permissions() {  # need_permissions OPERATION: what the install needs, from the policy file shipped beside the template
  {
    echo "当前凭证没有安装单机版 CloudFront 栈所需的权限（${1} 被拒绝）。二选一："
    echo "- 给实例角色临时加上 deploy/aws/kirocrew-on-single-install-policy.json 这份策略，装完再卸下；"
    echo "- 或在运行 install.sh 的 shell 里提供有这些权限的 AK/SK（只在本次安装的进程里使用，不写盘）。"
    echo "需要的操作："
    python3 -I -c 'import json, sys
for statement in json.load(open(sys.argv[1]))["Statement"]:
    print("  " + "、".join(statement["Action"]))' "$POLICY"
  } >&2
  exit 4
}

call() {  # call ARGS...: aws's output; a refusal for missing permissions ends with the list of what the install needs
  if aws --region "$REGION" "$@" 2>"$WORK/err"; then
    return 0
  fi
  if grep -qE 'AccessDenied|UnauthorizedOperation|not authorized' "$WORK/err"; then
    need_permissions "$1 $2"
  fi
  cat "$WORK/err" >&2
  return 1
}

origin_secret() {  # made here once and kept (a rerun never changes it); root-only until al2023.sh hands it to octopus
  if [ ! -s "$SECRET" ]; then
    install -d -m 0755 "$ETC"
    ( umask 077; python3 -I -c 'import secrets; print(secrets.token_urlsafe(36))' > "$SECRET.new" )
    mv -f "$SECRET.new" "$SECRET"
  fi
  [[ "$(tr -d '[:space:]' < "$SECRET")" =~ ^[A-Za-z0-9_-]{32,}$ ]] || { echo "$SECRET 的内容不是源站密钥" >&2; exit 1; }
}

up() {
  local out="$1" prefix group status type change reason replaced outputs
  origin_secret
  prefix="$(call ec2 describe-managed-prefix-lists \
    --filters Name=prefix-list-name,Values=com.amazonaws.global.cloudfront.origin-facing \
    --query 'PrefixLists[0].PrefixListId' --output text)"
  [[ "$prefix" =~ ^pl-[0-9a-f]+$ ]] || { echo "找不到 CloudFront 的源站前缀列表（${prefix}）" >&2; exit 1; }
  group="$(call ec2 describe-instances --instance-ids "$EC2_INSTANCE_ID" \
    --query 'Reservations[0].Instances[0].SecurityGroups[0].GroupId' --output text)"
  [[ "$group" =~ ^sg-[0-9a-f]+$ ]] || { echo "找不到实例 $EC2_INSTANCE_ID 的安全组（${group}）" >&2; exit 1; }
  call ec2 create-tags --resources "$EC2_INSTANCE_ID" --tags "Key=Backup,Value=$STACK" >/dev/null   # DLM's daily snapshots
  status="$(aws --region "$REGION" cloudformation describe-stacks --stack-name "$STACK" --query 'Stacks[0].StackStatus' \
    --output text 2>/dev/null || true)"
  if [ "$status" = ROLLBACK_COMPLETE ]; then   # a first attempt that failed leaves a stack that can only be deleted
    call cloudformation delete-stack --stack-name "$STACK"
    call cloudformation wait stack-delete-complete --stack-name "$STACK"
    status=""
  fi
  type=UPDATE
  [ -n "$status" ] || type=CREATE
  python3 -I - "$WORK/parameters.json" "$SECRET" "$EC2_INSTANCE_ID" "$group" "$prefix" <<'KC_PARAMS'
import json, os, sys
path, secret, instance, group, prefix = sys.argv[1:]
values = {"InstanceId": instance, "SecurityGroupId": group, "CloudFrontPrefixListId": prefix,
          "OriginSecret": open(secret).read().strip()}
with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as file:   # the secret: a file, not argv
    json.dump([{"ParameterKey": k, "ParameterValue": v} for k, v in values.items()], file)
KC_PARAMS
  change="kirocrew-on-$(date -u +%Y%m%d%H%M%S)"
  echo "CloudFront 栈 $STACK（${type}；首次创建分发需要几分钟）" >&2
  call cloudformation create-change-set --stack-name "$STACK" --change-set-name "$change" --change-set-type "$type" \
    --template-body "file://$TEMPLATE" --capabilities CAPABILITY_IAM --tags Key=Project,Value=kirocrew-on \
    --parameters "file://$WORK/parameters.json" >/dev/null
  rm -f "$WORK/parameters.json"
  if ! aws --region "$REGION" cloudformation wait change-set-create-complete --stack-name "$STACK" \
      --change-set-name "$change" 2>/dev/null; then
    reason="$(call cloudformation describe-change-set --stack-name "$STACK" --change-set-name "$change" \
      --query StatusReason --output text)"
    call cloudformation delete-change-set --stack-name "$STACK" --change-set-name "$change" >/dev/null || true
    case "$reason" in
      *"didn't contain changes"*|*"No updates are to be performed"*) echo "栈没有需要更新的内容" >&2 ;;
      *) echo "变更集创建失败：${reason}" >&2; exit 1 ;;
    esac
  else
    replaced="$(call cloudformation describe-change-set --stack-name "$STACK" --change-set-name "$change" \
      --query "Changes[?ResourceChange.Replacement=='True' || ResourceChange.Replacement=='Conditional'].ResourceChange.LogicalResourceId" \
      --output text)"
    if [ -n "$replaced" ] && [ "$replaced" != None ]; then
      call cloudformation delete-change-set --stack-name "$STACK" --change-set-name "$change" >/dev/null
      echo "变更会替换资源：${replaced}（例如换掉 Elastic IP 会让分发指向新的源站地址），未执行" >&2
      exit 3
    fi
    call cloudformation execute-change-set --stack-name "$STACK" --change-set-name "$change" >/dev/null
    call cloudformation wait "stack-$([ "$type" = CREATE ] && echo create || echo update)-complete" --stack-name "$STACK"
  fi
  outputs="$(call cloudformation describe-stacks --stack-name "$STACK" \
    --query 'Stacks[0].Outputs[].[OutputKey,OutputValue]' --output text)"
  local key value console="" native="" preview="" ip=""
  set -- $outputs
  while [ $# -ge 2 ]; do
    key=$1 value=$2
    shift 2
    [[ "$value" =~ ^[A-Za-z0-9.-]+$ ]] || continue
    case "$key" in
      ConsoleDomain) console=$value ;; NativeDomain) native=$value ;; PreviewDomain) preview=$value ;; PublicIp) ip=$value ;;
    esac
  done
  [ -n "$console" ] && [ -n "$native" ] && [ -n "$preview" ] || { echo "栈 $STACK 没有给出三个分发的域名" >&2; exit 1; }
  printf 'CF_DOMAIN=%s\nNATIVE_DOMAIN=%s\nPREVIEW_DOMAIN=%s\nPUBLIC_IP=%s\n' "$console" "$native" "$preview" "$ip" > "$out"
}

check_role() {
  local arn allowed attached inline policy name
  if [ -z "${EC2_ROLE:-}" ]; then
    echo "这台实例没有角色：Claude Code 与 Codex 经 Amazon Bedrock 调用模型，需要实例角色带 bedrock:InvokeModel*（远程运维另需 AmazonSSMManagedInstanceCore）" >&2
    exit 12
  fi
  if ! arn="$(aws --region "$REGION" iam get-role --role-name "$EC2_ROLE" --query Role.Arn --output text 2>/dev/null)" ||
     ! allowed="$(aws --region "$REGION" iam simulate-principal-policy --policy-source-arn "$arn" --action-names "${WRITES[@]}" \
       --query "EvaluationResults[?EvalDecision=='allowed'].EvalActionName" --output text 2>/dev/null)"; then
    echo "无法核对主机角色 $EC2_ROLE 的权限（需要 iam:GetRole、iam:SimulatePrincipalPolicy、iam:ListAttachedRolePolicies、iam:ListRolePolicies）：请自行确认它只剩 Bedrock 调用与 SSM 的权限" >&2
    exit 11
  fi
  if [ -z "$allowed" ] || [ "$allowed" = None ]; then
    echo "主机角色 $EC2_ROLE 只剩 Bedrock 调用与 SSM 的权限：同机的 Agent 不能用它改动 AWS 资源"
    return 0
  fi
  attached="$(aws --region "$REGION" iam list-attached-role-policies --role-name "$EC2_ROLE" \
    --query 'AttachedPolicies[].PolicyArn' --output text 2>/dev/null || true)"
  inline="$(aws --region "$REGION" iam list-role-policies --role-name "$EC2_ROLE" --query PolicyNames --output text 2>/dev/null || true)"
  {
    echo "主机角色 $EC2_ROLE 仍有写权限（$(echo $allowed | sed 's/ /、/g')）：同机所有进程都能用它，包括 Agent。请卸下安装用的权限："
    for policy in $attached; do
      case "$policy" in None|*/AmazonSSMManagedInstanceCore) continue ;; esac
      echo "  aws iam detach-role-policy --role-name $EC2_ROLE --policy-arn $policy"
    done
    for name in $inline; do
      [ "$name" = None ] && continue
      echo "  内联策略 ${name}：只含 bedrock:InvokeModel* 时保留，否则 aws iam delete-role-policy --role-name $EC2_ROLE --policy-name $name"
    done
    echo "只保留 Bedrock 的调用权限与 AmazonSSMManagedInstanceCore；卸下后再运行一次 install.sh，它会重新核对。"
  } >&2
  exit 10
}

case "${1:-}" in
  up) up "${2:?用法：single-stack.sh up <输出文件>}" ;;
  check-role) check_role ;;
  *) echo "用法：single-stack.sh up <输出文件> | check-role" >&2; exit 2 ;;
esac
