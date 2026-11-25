package dev.internradar.research;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.IOException;
import java.io.UncheckedIOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpHeaders;
import java.net.http.HttpResponse;
import java.net.http.HttpTimeoutException;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.Flow;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.NullAndEmptySource;
import org.junit.jupiter.params.provider.ValueSource;

class GreenhouseClientTest {
    private static final Duration TIMEOUT = Duration.ofSeconds(1);
    private static final String JOB = """
            {"id":8394096,"title":"Engineer","absolute_url":"https://company.example/jobs/8394096",
             "location":{"name":"Remote"},"content":"&lt;p&gt;Description&lt;/p&gt;",
             "first_published":"2025-03-07T10:30:00-05:00","updated_at":"2026-11-25T08:00:00Z"}
            """;

    @Test
    void parsesCompleteSnapshotAndUsesFirstPublishedBeforeUpdatedAt() throws IOException {
        var postings = parse("{\"meta\":{\"total\":2},\"jobs\":[" + JOB + "," + """
                {"id":40,"title":"Designer","absolute_url":"https://boards.greenhouse.io/a/jobs/40",
                 "location":null,"updated_at":"2026-11-25T08:00:00Z"}]}
                """);
        assertEquals(List.of(
                new GreenhouseClient.Posting("8394096", "Engineer", "Remote", "https://company.example/jobs/8394096",
                        "&lt;p&gt;Description&lt;/p&gt;", Instant.parse("2025-03-07T15:30:00Z")),
                new GreenhouseClient.Posting("40", "Designer", null, "https://boards.greenhouse.io/a/jobs/40",
                        null, Instant.parse("2026-11-25T08:00:00Z"))), postings);
        assertThrows(UnsupportedOperationException.class, postings::clear);
    }

    @Test
    void acceptsExplicitEmptyBoardAndMissingOptionalFields() throws IOException {
        assertTrue(parse("{\"jobs\":[],\"meta\":{\"total\":0}}").isEmpty());
        var posting = parse("""
                {"jobs":[{"id":1,"title":"Role","absolute_url":"https://example.com/job"}]}
                """).getFirst();
        assertNull(posting.location());
        assertNull(posting.description());
        assertNull(posting.postedAt());
    }

    @ParameterizedTest
    @ValueSource(strings = {"", "{}", "null", "{\"jobs\":null}", "{\"jobs\":{}}", "{\"jobs\":[",
            "{\"jobs\":[]} {}", "{\"jobs\":[],\"jobs\":[]}", "{\"jobs\":[],\"meta\":{\"total\":1}}"})
    void refusesMalformedEnvelope(String payload) {
        assertThrows(GreenhouseClient.InvalidSnapshotException.class, () -> parse(payload));
    }

    @Test
    void rejectsEntireSnapshotIfAnyJobIsMalformedOrDuplicated() {
        for (String invalid : List.of(JOB, "null", "{}", JOB.replace("8394096", "-4"),
                JOB.replace("8394096", "42").replace("Engineer", " "),
                JOB.replace("8394096", "42").replace("https://", "javascript:"),
                JOB.replace("8394096", "42").replace("2026-11-25T08:00:00Z", "not-a-date"),
                JOB.replace("8394096", "42").replace("{\"name\":\"Remote\"}", "123"))) {
            var payload = "{\"jobs\":[" + JOB + "," + invalid + "]}";
            assertThrows(GreenhouseClient.InvalidSnapshotException.class, () -> parse(payload));
        }
    }

    @ParameterizedTest
    @NullAndEmptySource
    @ValueSource(strings = {"../stripe", "//other.example", "stripe%2Fjobs", "stripe?content=false",
            "stripe#jobs", "stripe/jobs", "stripe\\jobs", " stripe", "stripe\n"})
    void invalidSlugsNeverReachTransport(String slug) {
        var client = new GreenhouseClient((request, handler) -> { throw new AssertionError("unexpected I/O"); }, TIMEOUT);
        assertThrows(IllegalArgumentException.class, () -> client.fetch(slug));
    }

    @Test
    void constructsFixedHttpsEndpointAndSurfacesRedirectWithoutFollowingIt() {
        var client = new GreenhouseClient((request, handler) -> {
            assertEquals(URI.create("https://boards-api.greenhouse.io/v1/boards/stripe_2/jobs?content=true"), request.uri());
            assertEquals("GET", request.method());
            assertEquals(Optional.of(TIMEOUT), request.timeout());
            return failedResponse(handler, responseInfo(302, Map.of("Location", List.of("https://other.example"))));
        }, TIMEOUT);
        var failure = assertThrows(GreenhouseClient.HttpStatusException.class, () -> client.fetch("stripe_2"));
        assertEquals(302, failure.statusCode());
        assertFalse(failure.retryable());
        assertEquals(Optional.empty(), failure.retryAfter());
    }

    @Test
    void propagatesRetryAfterForThrottling() {
        var client = new GreenhouseClient((request, handler) ->
                failedResponse(handler, responseInfo(429, Map.of("Retry-After", List.of("37")))), TIMEOUT);
        var failure = assertThrows(GreenhouseClient.HttpStatusException.class, () -> client.fetch("stripe"));
        assertEquals(429, failure.statusCode());
        assertTrue(failure.retryable());
        assertEquals(Optional.of(Duration.ofSeconds(37)), failure.retryAfter());
    }

    @Test
    void parsesHttpDateAndIgnoresMalformedRetryHints() {
        var now = Instant.parse("2015-10-21T07:27:00Z");
        var date = "Wed, 21 Oct 2015 07:28:00 GMT";
        assertEquals(Optional.of(Duration.ofMinutes(1)), GreenhouseClient.parseRetryAfter(date, now));
        assertEquals(Optional.of(Duration.ZERO), GreenhouseClient.parseRetryAfter(date, now.plusSeconds(120)));
        for (String invalid : List.of("", "-3", "999999999999999999999")) {
            assertEquals(Optional.empty(), GreenhouseClient.parseRetryAfter(invalid, now));
        }
    }

    @Test
    void cancelsAResponseThatNeverCompletes() {
        var incomplete = new CompletableFuture<HttpResponse<byte[]>>();
        var client = new GreenhouseClient((request, handler) -> incomplete, Duration.ofMillis(20));
        assertThrows(HttpTimeoutException.class, () -> client.fetch("stripe"));
        assertTrue(incomplete.isCancelled());
    }

    @Test
    void rejectsAdvertisedOversizedResponseBeforeReadingBody() {
        var response = responseInfo(200, Map.of("Content-Length", List.of("" + (GreenhouseClient.MAX_RESPONSE_BYTES + 1))));
        var failure = assertThrows(UncheckedIOException.class, () -> GreenhouseClient.responseBody(response));
        assertTrue(failure.getCause() instanceof GreenhouseClient.InvalidSnapshotException);
    }

    @Test
    void stopsReadingChunkedResponseAtSizeLimit() {
        var body = GreenhouseClient.responseBody(responseInfo(200, Map.of()));
        var cancelled = new AtomicBoolean();
        body.onSubscribe(new Flow.Subscription() {
            @Override public void request(long n) {}
            @Override public void cancel() { cancelled.set(true); }
        });
        body.onNext(List.of(ByteBuffer.allocate(GreenhouseClient.MAX_RESPONSE_BYTES + 1)));
        var failure = assertThrows(ExecutionException.class,
                () -> body.getBody().toCompletableFuture().get(1, TimeUnit.SECONDS));
        assertTrue(failure.getCause() instanceof IOException);
        assertTrue(cancelled.get());
    }

    private static List<GreenhouseClient.Posting> parse(String payload) throws IOException {
        return GreenhouseClient.parseSnapshot(payload.getBytes(StandardCharsets.UTF_8));
    }

    private static HttpResponse.ResponseInfo responseInfo(int status, Map<String, List<String>> headers) {
        return new HttpResponse.ResponseInfo() {
            @Override public int statusCode() { return status; }
            @Override public HttpHeaders headers() { return HttpHeaders.of(headers, (name, value) -> true); }
            @Override public HttpClient.Version version() { return HttpClient.Version.HTTP_1_1; }
        };
    }

    private static CompletableFuture<HttpResponse<byte[]>> failedResponse(
            HttpResponse.BodyHandler<byte[]> handler, HttpResponse.ResponseInfo info) {
        try {
            handler.apply(info);
            throw new AssertionError("non-200 response unexpectedly accepted");
        } catch (UncheckedIOException rejected) {
            return CompletableFuture.failedFuture(rejected);
        }
    }
}
