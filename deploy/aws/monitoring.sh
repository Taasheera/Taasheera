#!/usr/bin/env bash
# Create or update the CloudWatch dashboard and alarms for one application stack.
# Usage: bash deploy/aws/monitoring.sh [taasheera|taasheera-staging]
# Free metrics only; confirm the SNS subscription email AWS sends before alerts arrive.
set -euo pipefail
export AWS_PAGER=""
export AWS_CLI_AUTO_PROMPT=off
export AWS_REGION=${AWS_REGION:-us-east-1}
export AWS_DEFAULT_REGION=$AWS_REGION
stack=${1:-${TAASHEERA_STACK:-taasheera}}
[[ "$stack" =~ ^taasheera(-[a-z0-9]+)?$ ]]
cd "$(dirname "$0")/../.."
account=$(aws sts get-caller-identity --query Account --output text)
test "$account" = "${TAASHEERA_ACCOUNT_ID:-632630731308}"
output() { aws cloudformation describe-stacks --stack-name "$stack" --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue | [0]" --output text; }
instance=$(output InstanceId)
volume=$(output DataVolumeId)
distribution=$(output DistributionId)
aws cloudformation deploy --stack-name "$stack-monitoring" --template-file deploy/aws/monitoring.yaml \
  --no-fail-on-empty-changeset --parameter-overrides \
  "AppStackName=$stack" "InstanceId=$instance" "DataVolumeId=$volume" "DistributionId=$distribution"
printf 'Dashboard: https://%s.console.aws.amazon.com/cloudwatch/home?region=%s#dashboards/dashboard/%s-health\n' \
  "$AWS_REGION" "$AWS_REGION" "$stack"
