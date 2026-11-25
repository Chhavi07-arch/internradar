package dev.internradar.research;

import dev.internradar.contracts.ResearchFetchRequested;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.HashMap;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.json.JsonMapper;

@Component
public class ResearchDispatcher {
    private static final Logger log = LoggerFactory.getLogger(ResearchDispatcher.class);
    private static final JsonMapper JSON = JsonMapper.builder().build();
    private static final int MAX_ATTEMPTS = 4;
    private static final String LEASE_EXPIRED = "lease_expired";
    private static final int ACK_TIMEOUT_SECONDS = 5;
    private static final int MAX_PUBLISH_BATCH = 25;

    private final JdbcClient jdbc;
    private final TransactionTemplate tx;
    private final KafkaTemplate<Object, Object> kafka;

    public ResearchDispatcher(JdbcClient jdbc, TransactionTemplate tx, KafkaTemplate<Object, Object> kafka) {
        this.jdbc = jdbc;
        this.tx = tx;
        this.kafka = kafka;
    }

    @Scheduled(fixedDelayString = "${internradar.research.schedule-delay:10s}")
    void schedule() {
        tx.executeWithoutResult(status -> recoverExpiredLeases());
        tx.executeWithoutResult(status -> scheduleLatestWindow());
    }

    private void scheduleLatestWindow() {
        // The unique source/window constraint makes the task-and-outbox pair safe across replicas.
        var created = jdbc.sql("""
                WITH current_window AS (
                    SELECT date_bin(interval '30 minutes', now(), timestamptz '1970-01-01 00:00:00+00') AS starts_at
                )
                INSERT INTO schedule.tasks (task_key, kind, source_id, window_start, next_run_at)
                SELECT 'research.fetch:' || s.id::text || ':' || extract(epoch FROM w.starts_at)::bigint::text,
                    'research.fetch', s.id, w.starts_at, w.starts_at
                FROM catalogue.sources s CROSS JOIN current_window w
                WHERE s.enabled AND s.kind = 'greenhouse' AND s.board_slug IS NOT NULL AND s.company_id IS NOT NULL
                ORDER BY s.id
                ON CONFLICT (source_id, window_start) DO NOTHING
                RETURNING id, source_id, window_start, attempt, next_run_at
                """).query((row, n) -> new ScheduledRequest(
                        request(row.getObject("id", UUID.class), row.getObject("source_id", UUID.class),
                                row.getObject("window_start", OffsetDateTime.class), row.getInt("attempt")),
                        row.getObject("next_run_at", OffsetDateTime.class).toInstant(), false)).list();
        for (var item : created) {
            insertOutbox(jdbc, ResearchFetchRequested.TOPIC, item.request(), item.availableAt(), null);
        }
    }

    private void recoverExpiredLeases() {
        var expired = jdbc.sql("""
                WITH expired AS (
                    SELECT id FROM schedule.tasks
                    WHERE enabled AND kind = 'research.fetch' AND state = 'running' AND lease_until <= now()
                    ORDER BY lease_until, id
                    LIMIT 100
                    FOR UPDATE SKIP LOCKED
                )
                UPDATE schedule.tasks t
                SET state = CASE WHEN t.attempt < :maxAttempts THEN 'pending' ELSE 'failed' END,
                    attempt = LEAST(t.attempt + 1, :maxAttempts),
                    claim_token = NULL, lease_until = NULL, next_run_at = now(), error_code = :errorCode
                FROM expired WHERE t.id = expired.id
                RETURNING t.id, t.source_id, t.window_start, t.attempt, t.next_run_at, t.state
                """).param("maxAttempts", MAX_ATTEMPTS).param("errorCode", LEASE_EXPIRED)
                .query((row, n) -> new ScheduledRequest(
                        request(row.getObject("id", UUID.class), row.getObject("source_id", UUID.class),
                                row.getObject("window_start", OffsetDateTime.class), row.getInt("attempt")),
                        row.getObject("next_run_at", OffsetDateTime.class).toInstant(),
                        "failed".equals(row.getString("state")))).list();
        for (var item : expired) {
            insertOutbox(jdbc, item.terminal() ? ResearchFetchRequested.DEAD_LETTER_TOPIC : ResearchFetchRequested.TOPIC,
                    item.request(), item.availableAt(), item.terminal() ? LEASE_EXPIRED : null);
        }
    }

    private static ResearchFetchRequested request(UUID taskId, UUID sourceId, OffsetDateTime windowStart, int attempt) {
        return new ResearchFetchRequested(1, taskId, sourceId.toString(), windowStart.toInstant(), attempt);
    }

    /** Call inside the transaction that creates or changes the task. Non-null errorCode is for DLT records. */
    public static void insertOutbox(JdbcClient jdbc, String topic, ResearchFetchRequested request,
                                    Instant availableAt, String errorCode) {
        var payload = new HashMap<String, Object>(Map.of("version", request.version(),
                "taskId", request.taskId().toString(), "sourceId", request.sourceId(),
                "windowStart", request.windowStart().toString(), "attempt", request.attempt()));
        if (errorCode != null) {
            payload.put("errorCode", errorCode);
        }
        jdbc.sql("""
                INSERT INTO schedule.outbox (topic, message_key, payload, available_at)
                VALUES (:topic, :key, CAST(:payload AS jsonb), :availableAt)
                """).param("topic", topic).param("key", request.sourceId())
                .param("payload", JSON.writeValueAsString(payload))
                .param("availableAt", availableAt.atOffset(ZoneOffset.UTC)).update();
    }

    @Scheduled(fixedDelayString = "${internradar.research.publish-delay:1s}")
    void publish() {
        long started = System.nanoTime();
        for (int i = 0; i < MAX_PUBLISH_BATCH && System.nanoTime() - started < TimeUnit.SECONDS.toNanos(5); i++) {
            if (!Boolean.TRUE.equals(tx.execute(status -> publishOne()))) {
                return;
            }
        }
    }

    private boolean publishOne() {
        var next = jdbc.sql("""
                SELECT id, topic, message_key, CAST(payload AS text) AS payload, attempts
                FROM schedule.outbox
                WHERE published_at IS NULL AND available_at <= now()
                ORDER BY available_at, id
                LIMIT 1
                FOR UPDATE SKIP LOCKED
                """).query((row, n) -> new OutboxMessage(row.getObject("id", UUID.class), row.getString("topic"),
                row.getString("message_key"), row.getString("payload"), row.getInt("attempts"))).optional();
        if (next.isEmpty()) {
            return false;
        }
        var message = next.get();

        // Hold the row lock only across a bounded producer send and ack wait. A crash after Kafka's
        // ack but before commit can still redeliver; task/attempt checks in the listener fence duplicates.
        try {
            kafka.send(message.topic(), message.key(), message.payload()).get(ACK_TIMEOUT_SECONDS, TimeUnit.SECONDS);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return false;
        } catch (ExecutionException | TimeoutException | RuntimeException e) {
            long delay = Math.min(60, 1L << Math.min(6, message.attempts()));
            jdbc.sql("""
                    UPDATE schedule.outbox
                    SET attempts = attempts + 1, available_at = clock_timestamp() + :delay * interval '1 second'
                    WHERE id = :id
                    """).param("id", message.id()).param("delay", delay).update();
            log.warn("Kafka publish failed for outbox row {} (topic {}); retry in {}s",
                    message.id(), message.topic(), delay, e);
            return false;
        }
        jdbc.sql("""
                UPDATE schedule.outbox SET published_at = clock_timestamp(), attempts = attempts + 1 WHERE id = :id
                """).param("id", message.id()).update();
        return true;
    }

    private record ScheduledRequest(ResearchFetchRequested request, Instant availableAt, boolean terminal) {}

    private record OutboxMessage(UUID id, String topic, String key, String payload, int attempts) {}
}
