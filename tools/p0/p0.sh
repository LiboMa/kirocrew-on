#!/usr/bin/env bash
# MVP 0.2.0 P0 (owner approved 2026-10-26, <= $20): throwaway test resources, all tagged kirocrew:p0=1, in the platform's
# subnet in us-east-1a, with their own security group (no inbound) and an SSM-only role. Never touches the platform's own.
#   tools/p0/p0.sh setup | launch <name> <type> | volume <name> <GiB> | status | teardown
set -euo pipefail
REGION=us-east-1; AZ=${P0_AZ:-us-east-1a}; SUBNET=${P0_SUBNET:-subnet-0b12e072d10082aee}; VPC=${P0_VPC:-vpc-00940cce97b48953a}
# T7 (cross-AZ) puts its second host in the account's default VPC: the platform's VPC has one subnet only (us-east-1a).
VPCS="vpc-00940cce97b48953a ${P0_VPC:-}"
TAG='Key=kirocrew:p0,Value=1'; ROLE=kc-p0-ssm; SG_NAME=kc-p0
aws() { command aws --region "$REGION" "$@"; }
sg_id() { aws ec2 describe-security-groups --filters Name=group-name,Values=$SG_NAME Name=vpc-id,Values=$VPC --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null | grep -v None || true; }
ami() { aws ssm get-parameter --name "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-$1" --query Parameter.Value --output text; }
case "${1:-}" in
  setup)
    if [ -z "$(sg_id)" ]; then
      aws ec2 create-security-group --group-name $SG_NAME --description "kirocrew P0 tests: no inbound" --vpc-id $VPC \
        --tag-specifications "ResourceType=security-group,Tags=[{$TAG}]" --query GroupId --output text
    fi
    if ! aws iam get-role --role-name $ROLE >/dev/null 2>&1; then
      aws iam create-role --role-name $ROLE --tags "$TAG" --assume-role-policy-document \
        '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}' >/dev/null
      aws iam attach-role-policy --role-name $ROLE --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore
      aws iam create-instance-profile --instance-profile-name $ROLE --tags "$TAG" >/dev/null
      aws iam add-role-to-instance-profile --instance-profile-name $ROLE --role-name $ROLE
      sleep 10   # instance profiles take a moment to be usable
    fi
    echo "sg=$(sg_id) role=$ROLE" ;;
  launch)   # launch NAME TYPE [ARCH]
    name=$2; type=$3; arch=${4:-arm64}
    aws ec2 run-instances --image-id "$(ami "$arch")" --instance-type "$type" --subnet-id $SUBNET --security-group-ids "$(sg_id)" \
      --iam-instance-profile Name=$ROLE --metadata-options HttpTokens=required \
      --block-device-mappings 'DeviceName=/dev/xvda,Ebs={VolumeSize=16,VolumeType=gp3,Encrypted=true,DeleteOnTermination=true}' \
      --tag-specifications "ResourceType=instance,Tags=[{$TAG},{Key=Name,Value=kc-p0-$name}]" "ResourceType=volume,Tags=[{$TAG}]" \
      --query 'Instances[0].InstanceId' --output text ;;
  sg)   # sg: the test security group of $VPC (created by setup there)
    sg_id ;;
  volume)   # volume NAME GIB
    aws ec2 create-volume --availability-zone $AZ --size "$3" --volume-type gp3 --encrypted \
      --tag-specifications "ResourceType=volume,Tags=[{$TAG},{Key=Name,Value=kc-p0-$2}]" --query VolumeId --output text ;;
  status)
    echo "instances:"; aws ec2 describe-instances --filters Name=tag:kirocrew:p0,Values=1 Name=instance-state-name,Values=pending,running,stopping,stopped \
      --query 'Reservations[].Instances[].[InstanceId,InstanceType,State.Name,Tags[?Key==`Name`]|[0].Value,LaunchTime]' --output text
    echo "volumes:"; aws ec2 describe-volumes --filters Name=tag:kirocrew:p0,Values=1 --query 'Volumes[].[VolumeId,Size,State,AvailabilityZone]' --output text
    echo "snapshots:"; aws ec2 describe-snapshots --owner-ids self --filters Name=tag:kirocrew:p0,Values=1 --query 'Snapshots[].[SnapshotId,VolumeSize,State]' --output text ;;
  teardown)
    if aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names kc-p0-t8 --query 'AutoScalingGroups[0].AutoScalingGroupName' --output text 2>/dev/null | grep -q kc-p0-t8; then
      aws autoscaling delete-auto-scaling-group --auto-scaling-group-name kc-p0-t8 --force-delete && echo "deleted group kc-p0-t8"
      for _ in $(seq 1 60); do aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names kc-p0-t8 --query 'AutoScalingGroups[0]' --output text 2>/dev/null | grep -q . || break; sleep 10; done
    fi
    aws ec2 delete-launch-template --launch-template-name kc-p0-t8 >/dev/null 2>&1 && echo "deleted template kc-p0-t8" || true
    ids=$(aws ec2 describe-instances --filters Name=tag:kirocrew:p0,Values=1 Name=instance-state-name,Values=pending,running,stopping,stopped --query 'Reservations[].Instances[].InstanceId' --output text)
    if [ -n "$ids" ]; then aws ec2 terminate-instances --instance-ids $ids >/dev/null; aws ec2 wait instance-terminated --instance-ids $ids; fi
    for v in $(aws ec2 describe-volumes --filters Name=tag:kirocrew:p0,Values=1 --query 'Volumes[].VolumeId' --output text); do
      aws ec2 detach-volume --volume-id "$v" --force >/dev/null 2>&1 || true; aws ec2 wait volume-available --volume-ids "$v" || true
      aws ec2 delete-volume --volume-id "$v" && echo "deleted $v"; done
    for s in $(aws ec2 describe-snapshots --owner-ids self --filters Name=tag:kirocrew:p0,Values=1 --query 'Snapshots[].SnapshotId' --output text); do
      aws ec2 delete-snapshot --snapshot-id "$s" && echo "deleted $s"; done
    for VPC in $VPCS $(aws ec2 describe-vpcs --filters Name=isDefault,Values=true --query 'Vpcs[].VpcId' --output text); do
      sg=$(sg_id); [ -z "$sg" ] || { aws ec2 delete-security-group --group-id "$sg" && echo "deleted $sg"; }
    done
    if aws iam get-role --role-name $ROLE >/dev/null 2>&1; then
      aws iam delete-role-policy --role-name $ROLE --policy-name kc-p0-litestream 2>/dev/null || true
      aws iam remove-role-from-instance-profile --instance-profile-name $ROLE --role-name $ROLE 2>/dev/null || true
      aws iam delete-instance-profile --instance-profile-name $ROLE 2>/dev/null || true
      aws iam detach-role-policy --role-name $ROLE --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore 2>/dev/null || true
      aws iam delete-role --role-name $ROLE && echo "deleted role $ROLE"; fi ;;
  *) echo "usage: $0 setup | launch <name> <type> [arm64|x86_64] | volume <name> <GiB> | status | teardown" >&2; exit 2 ;;
esac
