#!/usr/bin/env bash
# Daglig produktionsimport. Se docs/scheduled-import.md før installation.
set -Eeuo pipefail
umask 077

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
mode="${1:-run}"
if [[ $# -gt 1 || ( "$mode" != run && "$mode" != --stop-post ) ]]; then
    echo "Brug: scripts/scheduled-import.sh [--stop-post]" >&2
    exit 2
fi
settings="$(python3 "$project_dir/scripts/import-settings.py" --settings "$project_dir")"
mapfile -t values <<< "$settings"
healthcheck_url="${values[0]%/}"
state_dir="${values[1]}"
backup_dir="${values[2]}"
run_date="$(date -u +%F)"
unset settings values

mkdir -p -- "$state_dir"
exec >> "$state_dir/import-$run_date.log" 2>&1
log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*"; }
compose=(docker compose --project-directory "$project_dir" --env-file "$project_dir/.env"
    -f "$project_dir/docker-compose.yml" -f "$project_dir/docker-compose.tunnel.yml" --profile ops)
run_id="${INVOCATION_ID:-manual-$(date -u +%s)-$$}"
child_pid=""
partial_backup=""

notify() {
    local url="${healthcheck_url}${1}" escaped
    escaped="${url//\\/\\\\}"
    escaped="${escaped//\"/\\\"}"
    # URL'en er en hemmelighed: via stdin, aldrig argv, trace eller curl-fejltekst.
    printf 'url = "%s"\n' "$escaped" |
        curl --disable --config - --silent --fail --output /dev/null \
            --connect-timeout 5 --max-time 15 --retry 1 --retry-max-time 30
}

cleanup_containers() {
    local ids
    ids="$(timeout 15 docker ps -aq --filter "label=dk.maritim.scheduled-import=$run_id")" || return 1
    if [[ -n "$ids" ]]; then
        local -a containers
        mapfile -t containers <<< "$ids"
        timeout 45 docker rm --force "${containers[@]}" >/dev/null 2>&1 || return 1
    fi
}

# systemd kalder også dette efter SIGKILL/timeout, hvor Bash ikke kan køre sin trap.
# Etiketten sikrer, at kun containere fra NETOP denne invocation fjernes.
if [[ "$mode" == --stop-post ]]; then
    [[ -n "${INVOCATION_ID:-}" ]] || exit 2
    result=0
    cleanup_containers || { log "event=cleanup_failed"; result=1; }
    if [[ "${SERVICE_RESULT:-unknown}" != success || "$result" != 0 ]]; then
        log "event=systemd_failed result=${SERVICE_RESULT:-unknown}"
        notify /fail || { log "event=notification_failed"; result=1; }
    fi
    exit "$result"
fi

finish() {
    local result=$?
    trap - EXIT INT TERM HUP
    if [[ -n "$child_pid" ]]; then
        cleanup_containers || { log "event=cleanup_failed"; result=1; }
        kill "$child_pid" 2>/dev/null || true
        wait "$child_pid" 2>/dev/null || true
    fi
    [[ -z "$partial_backup" ]] || rm -f -- "$partial_backup"
    if [[ "$result" != 0 ]]; then
        log "event=scheduled_import_failed exit_code=$result"
        # Under systemd står ExecStopPost for varslingen, også ved hårde stop.
        if [[ -z "${INVOCATION_ID:-}" ]]; then
            notify /fail || log "event=notification_failed"
        fi
    fi
    exit "$result"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

exec 9> "$state_dir/import.lock"
if ! flock -n 9; then
    log "event=already_running"
    exit 75
fi
window="$(python3 "$project_dir/scripts/import-settings.py" --window "$state_dir")"
mapfile -t dates <<< "$window"
since="${dates[0]}"
run_date="${dates[1]}"
history_gap="${dates[2]}"
log "event=scheduled_import_started since=$since run_id=$run_id"
if [[ "$history_gap" != no ]]; then
    log "event=backfill_review_required reason=history_outside_verified_feed_window"
fi
notify /start || log "event=start_notification_failed"

mkdir -p -- "$backup_dir"
partial_backup="$(mktemp "$backup_dir/pre-scheduled-import-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX.partial")"
# Bruger navne fra den kørende DB-containers miljø, aldrig kodeord i argv.
# shellcheck disable=SC2016 # Variablerne skal udvides inde i DB-containeren.
"${compose[@]}" exec -T db sh -c 'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$partial_backup"
test -s "$partial_backup"
"${compose[@]}" exec -T db pg_restore --list < "$partial_backup" > /dev/null
backup="${partial_backup%.partial}.dump"
mv -- "$partial_backup" "$backup"
partial_backup=""
log "event=backup_verified file=$backup"
# Kun dette jobs egne, færdige backups/logs. Aldrig operatørens manuelle dumps.
find "$backup_dir" -maxdepth 1 -type f -name 'pre-scheduled-import-*.dump' -mmin +20160 -delete
find "$state_dir" -maxdepth 1 -type f -name 'import-????-??-??.log' -mmin +20160 -delete

run_cli() {
    # Fast navn afviser også en efterladt jobcontainer efter et værtsnedbrud.
    # --no-deps starter hverken database, backend, frontend eller tunnel.
    "${compose[@]}" run --rm --no-deps -T --name maritim-scheduled-import \
        --label "dk.maritim.scheduled-import=$run_id" maintenance python -m app.cli "$@" &
    child_pid=$!
    wait "$child_pid"
    child_pid=""
}
run_cli import --source production --since "$since" --require-complete
run_cli embed run
run_cli embed status --require-complete

# Datoen er kørselsstarten, så en lang kørsel ikke springer mellemliggende ændringer over.
printf '%s\n' "$run_date" > "$state_dir/last-success-date.tmp"
mv -- "$state_dir/last-success-date.tmp" "$state_dir/last-success-date"
if ! notify ""; then
    log "event=notification_failed stage=success"
    exit 1
fi
log "event=scheduled_import_completed"
