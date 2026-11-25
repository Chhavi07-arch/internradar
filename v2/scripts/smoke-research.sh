#!/usr/bin/env bash
# Run against a disposable, already running local Compose stack only.
set -euo pipefail

if [[ ${1:-} != --local-smoke || $# != 1 ]]; then
  printf 'Usage: %s --local-smoke (disposable running local stack only)\n' "$0" >&2
  exit 2
fi
cd "$(dirname "$0")/.."
# Use a dedicated project so the replay cannot hit the regular Compose stack.
compose=(docker compose -p internradar-v2-smoke)
topic=research.fetch.requested.v1
group=internradar-research-v1

sql() {
  "${compose[@]}" exec -T postgres psql -XqAt -v ON_ERROR_STOP=1 \
    -U postgres -d internradar "$@"
}

kafka() {
  local program=$1
  shift
  "${compose[@]}" exec -T kafka "/opt/kafka/bin/${program}.sh" --bootstrap-server kafka:9092 "$@"
}

# Wait for a successful task that actually imported sightings. Retrieve its
# original dispatch (including the attempt that completed) instead of inventing one.
dispatch=''
for ((i=0; i<90; i++)); do
  dispatch=$(sql -c "
    SELECT o.message_key || '|' || o.payload::text
    FROM schedule.tasks t JOIN catalogue.sources s ON s.id = t.source_id
    JOIN schedule.outbox o ON o.payload->>'taskId' = t.id::text
      AND o.payload->>'attempt' = t.attempt::text
    WHERE s.source_key = 'greenhouse:stripe' AND t.state = 'succeeded'
      AND o.topic = '$topic' AND o.published_at IS NOT NULL
      AND EXISTS (SELECT 1 FROM catalogue.sightings v WHERE v.source_id = s.id)
    ORDER BY t.window_start DESC LIMIT 1;")
  [[ -n $dispatch ]] && break
  sleep 2
done
[[ -n $dispatch ]] || { printf 'No successful Stripe import with listings within 180 seconds.\n' >&2; exit 1; }

fingerprint() {
  # Hash only relevant source state, not unrelated catalogue writes.
  sql -c "
    SELECT md5(s.last_successful_window::text || '|' ||
      (SELECT string_agg(to_jsonb(v)::text || '|' || to_jsonb(l)::text, '|' ORDER BY v.external_id)
       FROM catalogue.sightings v JOIN catalogue.listings l ON l.id = v.listing_id
       WHERE v.source_id = s.id))
    FROM catalogue.sources s WHERE s.source_key = 'greenhouse:stripe';"
}

before=$(fingerprint)
[[ -n $before ]]
printf '%s\n' "$dispatch" | kafka kafka-console-producer --topic "$topic" \
  --property parse.key=true --property 'key.separator=|' --producer-property acks=all

# Capture end offsets *after* producing. A group committed offset is the next
# offset to read; waiting for it prevents a false pass before replay is consumed.
ends=$(kafka kafka-get-offsets --topic "$topic")
printf '%s\n' "$ends" | awk -F: -v topic="$topic" '$1 == topic && $3 > 0 { found=1 } END { exit !found }'
consumed=false
for ((i=0; i<30; i++)); do
  offsets=$(kafka kafka-consumer-groups --group "$group" --describe)
  if awk -v topic="$topic" '
    NR == FNR { split($0, end, ":"); if (end[1] == topic && end[3] > 0) need[end[2]]=end[3]; next }
    $2 == topic && $4 ~ /^[0-9]+$/ && ($3 in need) && $4+0 >= need[$3]+0 { delete need[$3] }
    END { for (partition in need) exit 1 }
  ' <(printf '%s\n' "$ends") <(printf '%s\n' "$offsets"); then
    consumed=true
    break
  fi
  sleep 2
done
[[ $consumed == true ]] || { printf 'Research group did not commit through replay.\n' >&2; exit 1; }
[[ $before == "$(fingerprint)" ]] || {
  printf 'Source state changed during replay. Inspect the worker or retry away from the half-hour boundary.\n' >&2
  exit 1
}
printf 'PASS: Stripe import populated listings; committed Kafka replay left source sightings unchanged.\n'
