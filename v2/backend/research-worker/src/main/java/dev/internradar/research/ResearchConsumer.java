package dev.internradar.research;

import dev.internradar.contracts.ResearchFetchRequested;
import java.io.IOException;
import java.io.InterruptedIOException;
import java.time.Duration;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ThreadLocalRandom;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.kafka.support.Acknowledgment;
import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.core.JacksonException;
import tools.jackson.databind.json.JsonMapper;

@Component
class ResearchConsumer {
    private static final int MAX_ATTEMPTS = 4;
    private final JdbcClient jdbc;
    private final TransactionTemplate tx;
    private final GreenhouseClient greenhouse;
    private final JsonMapper json = JsonMapper.builder().build();

    ResearchConsumer(JdbcClient jdbc, TransactionTemplate tx, GreenhouseClient greenhouse) {
        this.jdbc = jdbc;
        this.tx = tx;
        this.greenhouse = greenhouse;
    }

    @KafkaListener(topics = ResearchFetchRequested.TOPIC)
    void receive(ConsumerRecord<String, String> record, Acknowledgment ack) throws IOException {
        ResearchFetchRequested request;
        UUID sourceId;
        try {
            if (record.value() == null) throw new IllegalArgumentException("Missing research request");
            request = json.readValue(record.value(), ResearchFetchRequested.class);
            if (request == null || request.version() != 1 || request.taskId() == null
                    || request.windowStart() == null || request.attempt() < 1 || request.attempt() > MAX_ATTEMPTS) {
                throw new IllegalArgumentException("Invalid research request");
            }
            sourceId = UUID.fromString(request.sourceId() == null ? "" : request.sourceId());
        } catch (IllegalArgumentException | JacksonException e) {
            tx.executeWithoutResult(status -> outbox(ResearchFetchRequested.DEAD_LETTER_TOPIC,
                    "invalid-record", Map.of("partition", record.partition(), "offset", record.offset(),
                            "errorCode", "invalid_request"), 0));
            ack.acknowledge();
            return;
        }

        UUID token = UUID.randomUUID();
        Claim claim = tx.execute(status -> claim(request, sourceId, token));
        if (claim != null) {
            try {
                var postings = greenhouse.fetch(claim.board());
                tx.executeWithoutResult(status -> complete(request, claim, token, postings));
            } catch (IOException failure) {
                if (failure instanceof InterruptedIOException && Thread.currentThread().isInterrupted()) {
                    throw failure;
                }
                tx.executeWithoutResult(status -> fail(request, claim, token, failure));
            }
        }
        ack.acknowledge();
    }

    private Claim claim(ResearchFetchRequested request, UUID sourceId, UUID token) {
        return jdbc.sql("""
                UPDATE schedule.tasks t
                SET state = 'running', claim_token = :token, lease_until = now() + interval '2 minutes'
                FROM catalogue.sources s
                WHERE t.id = :task AND t.source_id = s.id AND s.id = :source
                    AND t.window_start = :window AND t.kind = 'research.fetch' AND s.kind = 'greenhouse'
                    AND s.enabled AND t.enabled AND t.state = 'pending' AND t.attempt = :attempt
                    AND t.next_run_at <= now()
                RETURNING s.id, s.board_slug, s.company_id, t.window_start
                """).param("token", token).param("task", request.taskId()).param("source", sourceId)
                .param("window", request.windowStart().atOffset(java.time.ZoneOffset.UTC)).param("attempt", request.attempt())
                .query((row, n) -> new Claim(row.getObject("id", UUID.class), row.getString("board_slug"),
                        row.getObject("company_id", UUID.class), row.getObject("window_start", OffsetDateTime.class)))
                .optional().orElse(null);
    }

    private boolean lockClaim(UUID id, UUID token) {
        return jdbc.sql("""
                SELECT id FROM schedule.tasks WHERE id = :id AND claim_token = :token AND state = 'running' FOR UPDATE
                """).param("id", id).param("token", token).query(UUID.class).optional().isPresent();
    }

    private void complete(ResearchFetchRequested request, Claim claim, UUID token, List<GreenhouseClient.Posting> postings) {
        if (!lockClaim(request.taskId(), token)) return;
        boolean newer = jdbc.sql("""
                UPDATE catalogue.sources SET last_successful_window = :window
                WHERE id = :source AND (last_successful_window IS NULL OR last_successful_window < :window)
                """).param("source", claim.source()).param("window", claim.window()).update() == 1;
        if (newer) {
            // Advance this source's misses, then reset every sighting returned by this snapshot.
            jdbc.sql("""
                    UPDATE catalogue.sightings SET consecutive_misses = LEAST(2, consecutive_misses + 1)
                    WHERE source_id = :source AND consecutive_misses < 2
                    """).param("source", claim.source()).update();
            for (var posting : postings) upsert(claim, posting);
            jdbc.sql("""
                    UPDATE catalogue.listings l SET active = false
                    WHERE l.active AND EXISTS (SELECT 1 FROM catalogue.sightings s
                        WHERE s.source_id = :source AND s.listing_id = l.id AND s.consecutive_misses >= 2)
                    AND NOT EXISTS (SELECT 1 FROM catalogue.sightings s
                        WHERE s.listing_id = l.id AND s.consecutive_misses < 2)
                    """).param("source", claim.source()).update();
        }
        jdbc.sql("""
                UPDATE schedule.tasks SET state = 'succeeded', claim_token = null, lease_until = null, error_code = null
                WHERE id = :id
                """).param("id", request.taskId()).update();
    }

    private void upsert(Claim claim, GreenhouseClient.Posting posting) {
        UUID existing = jdbc.sql("SELECT listing_id FROM catalogue.sightings WHERE source_id = :source AND external_id = :external")
                .param("source", claim.source()).param("external", posting.externalId()).query(UUID.class).optional().orElse(null);
        UUID listing = jdbc.sql(existing == null ? """
                INSERT INTO catalogue.listings (company_id, canonical_url, title, location, description, posted_at)
                VALUES (:company, :url, :title, :location, :description, :posted)
                ON CONFLICT (canonical_url) DO UPDATE SET last_seen_at = now(), active = true
                RETURNING id
                """ : """
                UPDATE catalogue.listings SET title = :title, location = :location, description = :description,
                    posted_at = COALESCE(:posted, posted_at), last_seen_at = now(), active = true
                WHERE id = :id RETURNING id
                """).param("company", claim.company()).param("url", posting.canonicalUrl()).param("id", existing)
                .param("title", posting.title()).param("location", posting.location()).param("description", posting.description())
                .param("posted", posting.postedAt() == null ? null : posting.postedAt().atOffset(java.time.ZoneOffset.UTC))
                .query(UUID.class).single();
        jdbc.sql("""
                INSERT INTO catalogue.sightings (source_id, external_id, listing_id, source_url)
                VALUES (:source, :external, :listing, :url)
                ON CONFLICT (source_id, external_id) DO UPDATE SET
                    source_url = excluded.source_url, last_seen_at = now(), consecutive_misses = 0
                """).param("source", claim.source()).param("external", posting.externalId()).param("listing", listing)
                .param("url", posting.canonicalUrl()).update();
    }

    private void fail(ResearchFetchRequested request, Claim claim, UUID token, IOException failure) {
        if (!lockClaim(request.taskId(), token)) return;
        var http = failure instanceof GreenhouseClient.HttpStatusException status ? status : null;
        boolean invalidSnapshot = failure instanceof GreenhouseClient.InvalidSnapshotException;
        boolean retry = request.attempt() < MAX_ATTEMPTS && !invalidSnapshot && (http == null || http.retryable());
        String errorCode = invalidSnapshot ? "source_invalid_snapshot"
                : http == null ? "source_io" : "source_http_" + http.statusCode();
        long delay = retry ? Math.max((30L << (request.attempt() - 1)) + ThreadLocalRandom.current().nextInt(15),
                http == null ? 0 : Math.min(Duration.ofDays(1).toSeconds(),
                        http.retryAfter().orElse(Duration.ZERO).toSeconds())) : 0;
        int attempt = request.attempt() + (retry ? 1 : 0);
        jdbc.sql("""
                UPDATE schedule.tasks SET state = :state, claim_token = null, lease_until = null,
                    next_run_at = now() + :delay * interval '1 second', error_code = :error, attempt = :attempt WHERE id = :id
                """).param("id", request.taskId()).param("state", retry ? "pending" : "failed")
                .param("delay", delay).param("error", errorCode).param("attempt", attempt).update();
        var payload = new java.util.HashMap<String, Object>(Map.of("version", 1, "taskId", request.taskId().toString(),
                "sourceId", claim.source().toString(), "windowStart", request.windowStart().toString(), "attempt", attempt));
        if (!retry) payload.put("errorCode", errorCode);
        outbox(retry ? ResearchFetchRequested.TOPIC : ResearchFetchRequested.DEAD_LETTER_TOPIC,
                claim.source().toString(), payload, delay);
    }

    private void outbox(String topic, String key, Map<String, Object> payload, long delay) {
        jdbc.sql("""
                INSERT INTO schedule.outbox (topic, message_key, payload, available_at)
                VALUES (:topic, :key, CAST(:payload AS jsonb), now() + :delay * interval '1 second')
                """).param("topic", topic).param("key", key).param("payload", json.writeValueAsString(payload))
                .param("delay", delay).update();
    }

    private record Claim(UUID source, String board, UUID company, OffsetDateTime window) {}
}
