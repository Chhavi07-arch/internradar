#!/usr/bin/env bash
# Real local PostgreSQL and Kafka; only the upstream HTTP transport is simulated.
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ $(java -version 2>&1) != *'version "25'* ]]; then
  printf 'Set JAVA_HOME and PATH to a Java 25 JDK.\n' >&2
  exit 1
fi

project="internradar-v2-research-it-$(openssl rand -hex 6)"
export POSTGRES_PASSWORD=$(openssl rand -hex 24)
export MIGRATION_DB_PASSWORD=$(openssl rand -hex 24)
export WEB_DB_PASSWORD=$(openssl rand -hex 24)
export RESEARCH_DB_PASSWORD=$(openssl rand -hex 24)
export RESEARCH_TEST_KAFKA_PORT=${RESEARCH_TEST_KAFKA_PORT:-29092}
stack=(docker compose -p "$project" -f compose.yaml -f scripts/research-test.compose.yaml)
trap '"${stack[@]}" down' EXIT
if ! "${stack[@]}" up -d --wait --wait-timeout 120 postgres kafka migrate; then
  "${stack[@]}" logs --tail 50 postgres kafka migrate
  exit 1
fi
pg_address=$("${stack[@]}" port postgres 5432)
export INTERNRADAR_IT_JDBC_URL="jdbc:postgresql://${pg_address}/internradar"
export INTERNRADAR_IT_DB_PASSWORD=$RESEARCH_DB_PASSWORD
export INTERNRADAR_IT_KAFKA="127.0.0.1:${RESEARCH_TEST_KAFKA_PORT}"
(cd backend && ./mvnw -B -pl research-worker -am -Dtest=ResearchPipelineIntegrationTest -Dsurefire.failIfNoSpecifiedTests=false test)
