#!/usr/bin/env bash
# Operate the staging copy used by the capacity tests, through AWS Systems Manager (no SSH).
#
#   bash loadtest/staging.sh env KEY=VALUE ...   set allowlisted test settings, then recreate api/worker
#   bash loadtest/staging.sh env KEY=            remove a setting
#   bash loadtest/staging.sh seed 300            create synthetic accounts (LOADTEST_PASSWORD, LOADTEST_ADMIN_PASSWORD)
#   bash loadtest/staging.sh logs [api|worker]   last 60 log lines (metadata only)
#   bash loadtest/staging.sh stop|start          stop or start the staging instance (stopped: disks only)
#
# Refuses any stack but taasheera-staging, and any setting outside the allowlist below.
set -euo pipefail
export AWS_PAGER="" AWS_CLI_AUTO_PROMPT=off AWS_REGION=${AWS_REGION:-us-east-1}
export AWS_DEFAULT_REGION=$AWS_REGION
stack=${TAASHEERA_STACK:-taasheera-staging}
test "$stack" = taasheera-staging || { echo "staging.sh only operates taasheera-staging." >&2; exit 1; }
# Windows (Git Bash) AWS CLI needs a native path inside file://
native() { if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s' "$1"; fi; }
PY=$(command -v python3 >/dev/null 2>&1 && python3 -c 'import sys' >/dev/null 2>&1 && echo python3 || echo python)
instance=$(aws cloudformation describe-stacks --stack-name "$stack" \
  --query "Stacks[0].Outputs[?OutputKey=='InstanceId'].OutputValue | [0]" --output text)
[[ "$instance" =~ ^i-[0-9a-f]+$ ]]
compose='docker compose -f /opt/taasheera/deploy/aws/compose.yaml'

run() {  # run one shell script on the host and print its output
  local script=$1 file
  file=$(mktemp)
  SCRIPT="$script" "$PY" -c 'import json, os, sys; json.dump({"commands": [os.environ["SCRIPT"]], "executionTimeout": ["1800"]}, open(sys.argv[1], "w"))' "$file"
  local id
  id=$(aws ssm send-command --instance-ids "$instance" --document-name AWS-RunShellScript \
       --parameters "file://$(native "$file")" --query Command.CommandId --output text)
  rm -f "$file"
  for _ in $(seq 1 180); do
    status=$(aws ssm get-command-invocation --command-id "$id" --instance-id "$instance" --query Status --output text 2>/dev/null || echo Pending)
    case "$status" in Success|Failed|Cancelled|TimedOut) break;; esac
    sleep 5
  done
  aws ssm get-command-invocation --command-id "$id" --instance-id "$instance" \
    --query '[StandardOutputContent,StandardErrorContent]' --output text
  test "$status" = Success
}

case "${1:-}" in
  env)
    shift
    allowed='^(THROTTLE_EXEMPT_IPS|ASSISTANT_FAKE_LATENCY_MS|SQLITE_JOURNAL_MODE|WEB_CONCURRENCY|API_THREADS|SENTRY_TRACES_SAMPLE_RATE|NOTIFICATION_JOB_SECONDS|FEATURE_[A-Z_]+)=[A-Za-z0-9.,:/_-]*$'
    edits=""
    for pair in "$@"; do
      [[ "$pair" =~ $allowed ]] || { echo "Refusing setting: ${pair%%=*}" >&2; exit 1; }
      edits+="$pair"$'\n'
    done
    run "set -euo pipefail
cat > /tmp/taasheera-edits <<'EDITS'
$edits
EDITS
python3 - <<'PY'
from pathlib import Path
path = Path('/srv/taasheera/private/app.env')
settings = dict(line.split('=', 1) for line in path.read_text().splitlines() if '=' in line)
for line in Path('/tmp/taasheera-edits').read_text().splitlines():
    if '=' not in line: continue
    key, value = line.split('=', 1)
    if value: settings[key] = value
    else: settings.pop(key, None)
tmp = path.with_suffix('.tmp'); tmp.write_text(''.join(f'{k}={v}\n' for k, v in settings.items())); tmp.chmod(0o600); tmp.replace(path)
print('Settings updated:', ', '.join(sorted(line.split('=')[0] for line in Path('/tmp/taasheera-edits').read_text().splitlines() if '=' in line)))
PY
rm -f /tmp/taasheera-edits
$compose up -d --no-build --force-recreate --wait --wait-timeout 180 api worker
# nginx resolves the api container's address once at start; a recreated api gets a new one.
$compose restart web
echo ready";;
  restart-web) run "$compose restart web";;
  seed)
    users=${2:-300}
    [[ "$users" =~ ^[0-9]+$ ]]
    : "${LOADTEST_PASSWORD:?}" "${LOADTEST_ADMIN_PASSWORD:?}"
    [[ "$LOADTEST_PASSWORD$LOADTEST_ADMIN_PASSWORD" =~ ^[A-Za-z0-9_-]+$ ]] || { echo "Use letters, digits, - and _ in load-test passwords." >&2; exit 1; }
    run "$compose exec -T -e LOADTEST_PASSWORD=$LOADTEST_PASSWORD -e LOADTEST_ADMIN_PASSWORD=$LOADTEST_ADMIN_PASSWORD api python manage.py seed-load-test --users $users";;
  logs)
    service=${2:-api}
    [[ "$service" =~ ^(api|worker|web)$ ]]
    run "$compose logs --no-color --tail 60 $service";;
  stop) aws ec2 stop-instances --instance-ids "$instance" --query 'StoppingInstances[0].CurrentState.Name' --output text;;
  start) aws ec2 start-instances --instance-ids "$instance" --query 'StartingInstances[0].CurrentState.Name' --output text;;
  *) sed -n '2,10p' "$0"; exit 1;;
esac
