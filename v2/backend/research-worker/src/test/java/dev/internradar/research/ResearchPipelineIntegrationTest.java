package dev.internradar.research;

import static dev.internradar.contracts.ResearchFetchRequested.DEAD_LETTER_TOPIC;
import static dev.internradar.contracts.ResearchFetchRequested.TOPIC;
import static org.junit.jupiter.api.Assertions.*;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

import dev.internradar.contracts.ResearchFetchRequested;
import java.io.IOException;
import java.io.InterruptedIOException;
import java.io.UncheckedIOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpHeaders;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;
import org.apache.kafka.clients.admin.Admin;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.apache.kafka.clients.consumer.KafkaConsumer;
import org.apache.kafka.clients.consumer.OffsetAndMetadata;
import org.apache.kafka.common.TopicPartition;
import org.apache.kafka.common.serialization.StringDeserializer;
import org.apache.kafka.common.serialization.StringSerializer;
import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.jdbc.datasource.DataSourceTransactionManager;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import org.springframework.kafka.core.DefaultKafkaProducerFactory;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.json.JsonMapper;

/** Requires a disposable, migrated database and a fresh broker. No live Greenhouse calls. */
class ResearchPipelineIntegrationTest {
    private static final JsonMapper JSON = JsonMapper.builder().build();
    private static final Duration WAIT = Duration.ofSeconds(15);
    private static final String JDBC_URL = "INTERNRADAR_IT_JDBC_URL";
    private static final String DB_PASSWORD = "INTERNRADAR_IT_DB_PASSWORD";
    private static final String KAFKA = "INTERNRADAR_IT_KAFKA";
    private static final URI BOARD = URI.create("https://boards-api.greenhouse.io/v1/boards/stripe/jobs?content=true");
    private static final byte[] SNAPSHOT = """
            {"meta":{"total":1},"jobs":[{"id":123,"title":"Fixture Engineer",
              "absolute_url":"https://example.com/jobs/123","location":{"name":"Remote"},
              "content":"Fixture description","first_published":"2026-01-01T00:00:00Z"}]}
            """.getBytes(StandardCharsets.UTF_8);

    @Test
    void interruptedClaimRecoversAndRepeatedHttp503EndsInOneDeadLetter() throws Exception {
        for (String name : List.of(JDBC_URL, DB_PASSWORD, KAFKA)) {
            assumeTrue(System.getenv(name) != null && !System.getenv(name).isBlank(), "Opt in via " + name);
        }
        var dataSource = new DriverManagerDataSource(System.getenv(JDBC_URL), "internradar_research",
                System.getenv(DB_PASSWORD));
        var jdbc = JdbcClient.create(dataSource);
        var tx = new TransactionTemplate(new DataSourceTransactionManager(dataSource));
        assertEquals(0, count(jdbc, "schedule.tasks"), "Requires a fresh test database");
        assertEquals(0, count(jdbc, "schedule.outbox"));
        assertEquals(0, count(jdbc, "catalogue.listings"));
        UUID source = jdbc.sql("SELECT id FROM catalogue.sources WHERE source_key = 'greenhouse:stripe'")
                .query(UUID.class).single();

        String broker = System.getenv(KAFKA);
        try (var admin = Admin.create(Map.of("bootstrap.servers", broker))) {
            var app = new ResearchApplication();
            admin.createTopics(List.of(app.fetchRequests(), app.fetchDeadLetters())).all()
                    .get(WAIT.toSeconds(), TimeUnit.SECONDS);
        }
        var producer = new DefaultKafkaProducerFactory<Object, Object>(Map.of(
                "bootstrap.servers", broker, "key.serializer", StringSerializer.class,
                "value.serializer", StringSerializer.class, "acks", "all", "enable.idempotence", true,
                "max.block.ms", 2000, "request.timeout.ms", 3000, "delivery.timeout.ms", 5000));
        var kafka = new KafkaTemplate<>(producer);
        String group = "research-pipeline-it-" + UUID.randomUUID();
        try {
            var dispatcher = new ResearchDispatcher(jdbc, tx, kafka);
            dispatcher.schedule();
            dispatcher.publish();
            UUID recoveredTask = jdbc.sql("SELECT id FROM schedule.tasks WHERE source_id = :source")
                    .param("source", source).query(UUID.class).single();
            var fetches = new AtomicInteger();
            var stalled = new CompletableFuture<HttpResponse<byte[]>>();
            var unavailable = new AtomicBoolean();
            var worker = new ResearchConsumer(jdbc, tx, new GreenhouseClient((request, handler) -> {
                assertEquals(BOARD, request.uri());
                if (fetches.incrementAndGet() == 1) {
                    assertEquals("running", task(jdbc, recoveredTask).state());
                    Thread.currentThread().interrupt();
                    return stalled;
                }
                if (unavailable.get()) {
                    try {
                        handler.apply(new HttpResponse.ResponseInfo() {
                            @Override public int statusCode() { return 503; }
                            @Override public HttpHeaders headers() { return emptyHeaders(); }
                            @Override public HttpClient.Version version() { return HttpClient.Version.HTTP_1_1; }
                        });
                        throw new AssertionError("Transport handler accepted HTTP 503");
                    } catch (UncheckedIOException failure) {
                        return CompletableFuture.failedFuture(failure);
                    }
                }
                return CompletableFuture.completedFuture(new FixtureResponse(request));
            }, WAIT));

            ResearchFetchRequested original;
            ConsumerRecord<String, String> interruptedRecord;
            try (var requests = reader(broker, group, TOPIC)) {
                interruptedRecord = next(requests);
                original = assertRequest(interruptedRecord, recoveredTask, source, 1);
                var ack = new AtomicBoolean();
                try {
                    assertThrows(InterruptedIOException.class,
                            () -> deliver(worker, requests, interruptedRecord, ack));
                    assertTrue(Thread.currentThread().isInterrupted());
                } finally {
                    Thread.interrupted();
                }
                assertTrue(stalled.isCancelled());
                assertFalse(ack.get(), "Interrupted work must not acknowledge Kafka");
                assertNull(requests.committed(java.util.Set.of(partition(interruptedRecord)), WAIT)
                        .get(partition(interruptedRecord)));
            }
            var running = task(jdbc, recoveredTask);
            assertEquals("running", running.state());
            assertEquals(1, running.attempt());
            assertNotNull(running.token());
            assertNotNull(running.lease());
            assertNull(running.error());
            assertEquals(0, count(jdbc, "catalogue.listings"));
            assertEquals(1, count(jdbc, "schedule.outbox"), "Interruption must not enqueue a retry itself");

            tx.executeWithoutResult(status -> {
                jdbc.sql("UPDATE schedule.tasks SET lease_until = now() - interval '1 second' WHERE id = :task")
                        .param("task", recoveredTask).update();
                // Recover the lease via schedule() without opening a new window if we cross a half-hour boundary.
                jdbc.sql("UPDATE catalogue.sources SET enabled = false WHERE id = :source").param("source", source).update();
                dispatcher.schedule();
                jdbc.sql("UPDATE catalogue.sources SET enabled = true WHERE id = :source").param("source", source).update();
            });
            assertEquals(new Task("pending", 2, null, null, "lease_expired"), task(jdbc, recoveredTask));
            assertEquals(1, count(jdbc, "schedule.tasks"));
            try (var requests = reader(broker, group, TOPIC)) {
                var redelivered = next(requests);
                assertEquals(partition(interruptedRecord), partition(redelivered));
                assertEquals(interruptedRecord.offset(), redelivered.offset());
                assertEquals(original, assertRequest(redelivered, recoveredTask, source, 1));
                deliverAndAssertAck(worker, requests, redelivered);
                assertEquals(1, fetches.get(), "The stale attempt cannot reclaim the recovered task");

                dispatcher.publish();
                var retry = next(requests);
                assertEquals(original.windowStart(), assertRequest(retry, recoveredTask, source, 2).windowStart());
                deliverAndAssertAck(worker, requests, retry);
                assertEquals(new Task("succeeded", 2, null, null, null), task(jdbc, recoveredTask));
                assertEquals(2, fetches.get());
                assertEquals(1, count(jdbc, "catalogue.listings"));
                assertEquals(1, count(jdbc, "catalogue.sightings"));
                assertEquals("Fixture Engineer", jdbc.sql("SELECT title FROM catalogue.listings").query(String.class).single());
                assertEquals(original.windowStart(), jdbc.sql("SELECT last_successful_window FROM catalogue.sources WHERE id = :source")
                        .param("source", source).query(OffsetDateTime.class).single().toInstant());
                String catalogue = catalogueSnapshot(jdbc, source);

                // Simulate an outbox send that reached Kafka twice; the completed attempt is fenced.
                kafka.send(TOPIC, retry.key(), retry.value()).get(WAIT.toSeconds(), TimeUnit.SECONDS);
                var duplicate = next(requests);
                assertEquals(retry.value(), duplicate.value());
                deliverAndAssertAck(worker, requests, duplicate);
                assertEquals(2, fetches.get());
                assertEquals(catalogue, catalogueSnapshot(jdbc, source));
                assertEquals(2, count(jdbc, "schedule.outbox"));

                unavailable.set(true);
                var failureWindow = original.windowStart().plus(Duration.ofMinutes(30));
                UUID failedTask = tx.execute(status -> {
                    UUID id = jdbc.sql("""
                            INSERT INTO schedule.tasks (task_key, kind, source_id, window_start)
                            VALUES (:key, 'research.fetch', :source, :window) RETURNING id
                            """).param("key", "research-it-http-503").param("source", source)
                            .param("window", failureWindow.atOffset(ZoneOffset.UTC)).query(UUID.class).single();
                    var request = new ResearchFetchRequested(1, id, source.toString(), failureWindow, 1);
                    var now = jdbc.sql("SELECT now()").query(OffsetDateTime.class).single().toInstant();
                    ResearchDispatcher.insertOutbox(jdbc, TOPIC, request, now, null);
                    return id;
                });
                for (int attempt = 1; attempt <= 4; attempt++) {
                    dispatcher.publish();
                    var record = next(requests);
                    assertEquals(failureWindow, assertRequest(record, failedTask, source, attempt).windowStart());
                    deliverAndAssertAck(worker, requests, record);
                    assertEquals(attempt + 2, fetches.get());
                    assertEquals(catalogue, catalogueSnapshot(jdbc, source));
                    if (attempt < 4) {
                        assertEquals(new Task("pending", attempt + 1, null, null, "source_http_503"), task(jdbc, failedTask));
                        assertEquals(0, jdbc.sql("SELECT count(*) FROM schedule.outbox WHERE topic = :topic")
                                .param("topic", DEAD_LETTER_TOPIC).query(Integer.class).single());
                        advanceRetry(jdbc, tx, failedTask, attempt + 1);
                    }
                }
                assertEquals(new Task("failed", 4, null, null, "source_http_503"), task(jdbc, failedTask));
                dispatcher.publish();
                dispatcher.publish();
                try (var dlt = reader(broker, group + "-dlt", DEAD_LETTER_TOPIC)) {
                    var deadLetter = next(dlt);
                    assertEquals(source.toString(), deadLetter.key());
                    var payload = JSON.readTree(deadLetter.value());
                    assertEquals(1, payload.path("version").asInt());
                    assertEquals(failedTask.toString(), payload.path("taskId").asString());
                    assertEquals(source.toString(), payload.path("sourceId").asString());
                    assertEquals(failureWindow.toString(), payload.path("windowStart").asString());
                    assertEquals(4, payload.path("attempt").asInt());
                    assertEquals("source_http_503", payload.path("errorCode").asString());
                    assertEquals(1L, dlt.endOffsets(dlt.assignment(), WAIT).values().stream().mapToLong(Long::longValue).sum());
                }
                assertEquals(7L, requests.endOffsets(requests.assignment(), WAIT).values().stream()
                        .mapToLong(Long::longValue).sum(), "Only two recovery attempts, one replay and four HTTP 503 attempts");
                assertEquals(0, jdbc.sql("SELECT count(*) FROM schedule.outbox WHERE published_at IS NULL")
                        .query(Integer.class).single());
                assertEquals(catalogue, catalogueSnapshot(jdbc, source));
            }
        } finally {
            producer.destroy();
        }
    }

    private static void advanceRetry(JdbcClient jdbc, TransactionTemplate tx, UUID task, int attempt) {
        tx.executeWithoutResult(status -> {
            assertEquals(1, jdbc.sql("""
                    UPDATE schedule.tasks SET next_run_at = now() - interval '1 second'
                    WHERE id = :task AND attempt = :attempt AND next_run_at > now()
                    """).param("task", task).param("attempt", attempt).update());
            assertEquals(1, jdbc.sql("""
                    UPDATE schedule.outbox SET available_at = now() - interval '1 second'
                    WHERE topic = :topic AND payload->>'taskId' = :task AND (payload->>'attempt')::int = :attempt
                      AND published_at IS NULL AND available_at > now()
                    """).param("topic", TOPIC).param("task", task.toString()).param("attempt", attempt).update());
        });
    }

    private static ResearchFetchRequested assertRequest(ConsumerRecord<String, String> record, UUID task, UUID source, int attempt) {
        assertEquals(source.toString(), record.key());
        var request = JSON.readValue(record.value(), ResearchFetchRequested.class);
        assertEquals(new ResearchFetchRequested(1, task, source.toString(), request.windowStart(), attempt), request);
        assertNotNull(request.windowStart());
        return request;
    }

    private static KafkaConsumer<String, String> reader(String broker, String group, String topic) {
        var consumer = new KafkaConsumer<String, String>(Map.of(
                "bootstrap.servers", broker, "group.id", group, "enable.auto.commit", false,
                "auto.offset.reset", "earliest", "default.api.timeout.ms", 15000,
                "key.deserializer", StringDeserializer.class, "value.deserializer", StringDeserializer.class));
        try {
            consumer.assign(consumer.partitionsFor(topic, WAIT).stream()
                    .map(info -> new TopicPartition(topic, info.partition())).toList());
            return consumer;
        } catch (RuntimeException e) {
            consumer.close();
            throw e;
        }
    }

    private static ConsumerRecord<String, String> next(KafkaConsumer<String, String> consumer) {
        long deadline = System.nanoTime() + WAIT.toNanos();
        do {
            var records = consumer.poll(Duration.ofMillis(250));
            if (!records.isEmpty()) {
                assertEquals(1, records.count(), "Expected exactly one publication for this step");
                return records.iterator().next();
            }
        } while (System.nanoTime() < deadline);
        return fail("Timed out waiting for a record on " + consumer.assignment());
    }

    private static TopicPartition partition(ConsumerRecord<String, String> record) {
        return new TopicPartition(record.topic(), record.partition());
    }

    private static void deliver(ResearchConsumer worker, KafkaConsumer<String, String> consumer,
                                ConsumerRecord<String, String> record, AtomicBoolean ack) throws IOException {
        worker.receive(record, () -> {
            assertTrue(ack.compareAndSet(false, true), "Record acknowledged twice");
            consumer.commitSync(Map.of(partition(record), new OffsetAndMetadata(record.offset() + 1)), WAIT);
        });
    }

    private static void deliverAndAssertAck(ResearchConsumer worker, KafkaConsumer<String, String> consumer,
                                           ConsumerRecord<String, String> record) throws IOException {
        var ack = new AtomicBoolean();
        deliver(worker, consumer, record, ack);
        assertTrue(ack.get(), "Durable processing must acknowledge the record");
    }

    private static Task task(JdbcClient jdbc, UUID task) {
        return jdbc.sql("SELECT state, attempt, claim_token, lease_until, error_code FROM schedule.tasks WHERE id = :task")
                .param("task", task).query((row, n) -> new Task(row.getString("state"), row.getInt("attempt"),
                        row.getObject("claim_token", UUID.class), row.getObject("lease_until", OffsetDateTime.class),
                        row.getString("error_code"))).single();
    }

    private static int count(JdbcClient jdbc, String table) {
        return jdbc.sql("SELECT count(*) FROM " + table).query(Integer.class).single();
    }

    private static String catalogueSnapshot(JdbcClient jdbc, UUID source) {
        return jdbc.sql("""
                SELECT jsonb_build_object('source', to_jsonb(s),
                    'listings', (SELECT jsonb_agg(to_jsonb(l) ORDER BY l.id) FROM catalogue.listings l),
                    'sightings', (SELECT jsonb_agg(to_jsonb(v) ORDER BY v.source_id, v.external_id) FROM catalogue.sightings v))::text
                FROM catalogue.sources s WHERE s.id = :source
                """).param("source", source).query(String.class).single();
    }

    private static HttpHeaders emptyHeaders() {
        return HttpHeaders.of(Map.of(), (name, value) -> true);
    }

    private record Task(String state, int attempt, UUID token, OffsetDateTime lease, String error) {}

    private record FixtureResponse(HttpRequest request) implements HttpResponse<byte[]> {
        @Override public int statusCode() { return 200; }
        @Override public Optional<HttpResponse<byte[]>> previousResponse() { return Optional.empty(); }
        @Override public HttpHeaders headers() { return emptyHeaders(); }
        @Override public byte[] body() { return SNAPSHOT; }
        @Override public Optional<javax.net.ssl.SSLSession> sslSession() { return Optional.empty(); }
        @Override public URI uri() { return request.uri(); }
        @Override public HttpClient.Version version() { return HttpClient.Version.HTTP_1_1; }
    }
}
