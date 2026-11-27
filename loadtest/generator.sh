#!/usr/bin/env bash
# Temporary load generator for the capacity tests (loadtest/generator.yaml), driven through SSM.
#
#   bash loadtest/generator.sh create          create it; prints its public IP for THROTTLE_EXEMPT_IPS
#   bash loadtest/generator.sh run <branch> -- <capacity.py arguments>
#                                              clone <branch>, start capacity.py in the background
#   bash loadtest/generator.sh status          tail the run's console output
#   bash loadtest/generator.sh fetch <file>    print a results file (report.md, report.json)
#   bash loadtest/generator.sh delete          delete the generator (always do this afterwards)
#
# The run needs LOADTEST_PASSWORD, LOADTEST_ADMIN_EMAIL and LOADTEST_ADMIN_PASSWORD in the environment
# (synthetic staging accounts); they reach the generator in the SSM command, never a file in Git.
set -euo pipefail
export AWS_PAGER="" AWS_CLI_AUTO_PROMPT=off AWS_REGION=${AWS_REGION:-us-east-1}
export AWS_DEFAULT_REGION=$AWS_REGION
stack=taasheera-loadgen
# Windows (Git Bash) AWS CLI needs a native path inside file://
native() { if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s' "$1"; fi; }
PY=$(command -v python3 >/dev/null 2>&1 && python3 -c 'import sys' >/dev/null 2>&1 && echo python3 || echo python)
output() { aws cloudformation describe-stacks --stack-name "$stack" --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue | [0]" --output text; }

ssm() {  # run a script on the generator, wait, print stdout
  local instance file id status=Pending
  instance=$(output InstanceId)
  file=$(mktemp)
  SCRIPT="$1" "$PY" -c 'import json, os, sys; json.dump({"commands": [os.environ["SCRIPT"]], "executionTimeout": ["600"]}, open(sys.argv[1], "w"))' "$file"
  id=$(aws ssm send-command --instance-ids "$instance" --document-name AWS-RunShellScript \
       --parameters "file://$(native "$file")" --query Command.CommandId --output text)
  rm -f "$file"
  for _ in $(seq 1 120); do
    status=$(aws ssm get-command-invocation --command-id "$id" --instance-id "$instance" --query Status --output text 2>/dev/null || echo Pending)
    case "$status" in Success|Failed|Cancelled|TimedOut) break;; esac
    sleep 3
  done
  aws ssm get-command-invocation --command-id "$id" --instance-id "$instance" --query StandardOutputContent --output text
  test "$status" = Success
}

case "${1:-}" in
  create)
    vpc=$(aws ec2 describe-vpcs --filters Name=is-default,Values=true --query 'Vpcs[0].VpcId' --output text)
    subnet=$(aws ec2 describe-subnets --filters "Name=vpc-id,Values=$vpc" Name=map-public-ip-on-launch,Values=true --query 'Subnets[0].SubnetId' --output text)
    aws cloudformation deploy --stack-name "$stack" --template-file loadtest/generator.yaml \
      --capabilities CAPABILITY_IAM --parameter-overrides "VpcId=$vpc" "SubnetId=$subnet" >/dev/null
    instance=$(output InstanceId)
    for _ in $(seq 1 60); do
      [ "$(aws ssm describe-instance-information --filters "Key=InstanceIds,Values=$instance" --query 'InstanceInformationList[0].PingStatus' --output text)" = Online ] && break
      sleep 5
    done
    ssm 'cloud-init status --wait >/dev/null; k6 version'
    printf 'Generator public IP: %s\n' "$(output PublicIp)";;
  run)
    branch=$2; shift 2; [ "${1:-}" = -- ] && shift
    [[ "$branch" =~ ^[A-Za-z0-9._/-]+$ ]]
    : "${LOADTEST_PASSWORD:?}" "${LOADTEST_ADMIN_EMAIL:?}" "${LOADTEST_ADMIN_PASSWORD:?}"
    args=$(printf '%q ' "$@")
    ssm "set -euo pipefail
rm -rf /opt/taasheera-loadtest && git clone -q --depth 1 --branch $branch https://github.com/Taasheera/Taasheera.git /opt/taasheera-loadtest
cd /opt/taasheera-loadtest
export LOADTEST_PASSWORD='$LOADTEST_PASSWORD' LOADTEST_ADMIN_EMAIL='$LOADTEST_ADMIN_EMAIL' LOADTEST_ADMIN_PASSWORD='$LOADTEST_ADMIN_PASSWORD' AWS_REGION=$AWS_REGION
ulimit -n 65536
nohup python3 loadtest/capacity.py --out /opt/results $args > /opt/results-console.log 2>&1 &
echo started";;
  status) ssm 'tail -n 30 /opt/results-console.log; ls -1t /opt/results | head -3';;
  fetch)
    name=${2:-report.md}
    [[ "$name" =~ ^[A-Za-z0-9._-]+$ ]]
    ssm "cat \"/opt/results/\$(ls -1t /opt/results | head -1)/$name\"";;
  delete) aws cloudformation delete-stack --stack-name "$stack" && aws cloudformation wait stack-delete-complete --stack-name "$stack" && echo deleted;;
  *) sed -n '2,12p' "$0"; exit 1;;
esac
