package dev.internradar.research;

import java.io.IOException;
import java.io.InterruptedIOException;
import java.io.UncheckedIOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.net.http.HttpTimeoutException;
import java.time.Duration;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.time.ZonedDateTime;
import java.time.format.DateTimeFormatter;
import java.time.format.DateTimeParseException;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Optional;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import java.util.regex.Pattern;

import org.springframework.stereotype.Component;
import tools.jackson.core.JacksonException;
import tools.jackson.databind.DeserializationFeature;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

@Component
public final class GreenhouseClient {
    private static final String API_ROOT = "https://boards-api.greenhouse.io/v1/boards/";
    private static final Pattern BOARD_SLUG = Pattern.compile("[a-z0-9][a-z0-9_-]{0,127}");
    private static final Duration CONNECT_TIMEOUT = Duration.ofSeconds(5);
    private static final Duration REQUEST_TIMEOUT = Duration.ofSeconds(20);
    static final int MAX_RESPONSE_BYTES = 8 * 1024 * 1024;

    private static final JsonMapper JSON = JsonMapper.builder()
            .enable(DeserializationFeature.FAIL_ON_TRAILING_TOKENS,
                    DeserializationFeature.FAIL_ON_READING_DUP_TREE_KEY)
            .build();

    private final Transport transport;
    private final Duration requestTimeout;

    public GreenhouseClient() {
        this(HttpClient.newBuilder().connectTimeout(CONNECT_TIMEOUT)
                .followRedirects(HttpClient.Redirect.NEVER)
                .proxy(HttpClient.Builder.NO_PROXY)
                .build()::sendAsync, REQUEST_TIMEOUT);
    }

    GreenhouseClient(Transport transport, Duration requestTimeout) {
        this.transport = transport;
        this.requestTimeout = requestTimeout;
    }

    @FunctionalInterface
    interface Transport {
        CompletableFuture<HttpResponse<byte[]>> send(HttpRequest request, HttpResponse.BodyHandler<byte[]> handler);
    }

    /**
     * Fetches one complete board snapshot. {@code fetch(String board) throws IOException}
     * returns an immutable list, possibly empty, only after validating the entire response.
     * Invalid slugs throw {@link IllegalArgumentException} before any network I/O. Non-200
     * responses throw {@link HttpStatusException}; malformed snapshots throw
     * {@link InvalidSnapshotException}. Interruption restores the interrupt flag and throws
     * {@link InterruptedIOException}.
     */
    public List<Posting> fetch(String board) throws IOException {
        if (board == null || !BOARD_SLUG.matcher(board).matches()) {
            throw new IllegalArgumentException("Invalid Greenhouse board slug");
        }
        var request = HttpRequest.newBuilder(URI.create(API_ROOT + board + "/jobs?content=true"))
                .timeout(requestTimeout)
                .header("Accept", "application/json")
                .GET().build();
        var response = transport.send(request, GreenhouseClient::responseBody);
        try {
            // Bound time waiting for the entire body, not just for the response headers.
            return parseSnapshot(response.get(requestTimeout.toNanos(), TimeUnit.NANOSECONDS).body());
        } catch (TimeoutException e) {
            response.cancel(true);
            throw new HttpTimeoutException("Greenhouse response timed out");
        } catch (InterruptedException e) {
            response.cancel(true);
            Thread.currentThread().interrupt();
            var interrupted = new InterruptedIOException("Interrupted fetching Greenhouse board");
            interrupted.initCause(e);
            throw interrupted;
        } catch (ExecutionException e) {
            if (e.getCause() instanceof UncheckedIOException io) {
                throw io.getCause();
            }
            if (e.getCause() instanceof IOException io) {
                throw io;
            }
            throw new IOException("Greenhouse request failed", e.getCause());
        }
    }

    static HttpResponse.BodySubscriber<byte[]> responseBody(HttpResponse.ResponseInfo response) {
        if (response.statusCode() != 200) {
            var retryAfter = response.headers().firstValue("Retry-After")
                    .flatMap(value -> parseRetryAfter(value, Instant.now()));
            // Fail from the headers without downloading a redirect or error body.
            throw new UncheckedIOException(new HttpStatusException(response.statusCode(), retryAfter));
        }
        if (response.headers().firstValueAsLong("Content-Length").orElse(0) > MAX_RESPONSE_BYTES) {
            throw new UncheckedIOException(new InvalidSnapshotException("Greenhouse response exceeds size limit"));
        }
        return HttpResponse.BodySubscribers.limiting(HttpResponse.BodySubscribers.ofByteArray(), MAX_RESPONSE_BYTES);
    }

    static List<Posting> parseSnapshot(byte[] body) throws InvalidSnapshotException {
        final JsonNode root;
        try {
            root = JSON.readTree(body);
        } catch (JacksonException e) {
            throw new InvalidSnapshotException("Invalid Greenhouse JSON", e);
        }
        var jobs = root.path("jobs");
        if (!root.isObject() || !jobs.isArray()) {
            throw new InvalidSnapshotException("Expected a Greenhouse jobs array");
        }
        var meta = root.path("meta");
        if (!meta.isMissingNode()) {
            var total = meta.path("total");
            if (!meta.isObject() || (!total.isMissingNode()
                    && (!total.isIntegralNumber() || !total.canConvertToInt() || total.intValue() != jobs.size()))) {
                throw new InvalidSnapshotException("Invalid Greenhouse snapshot metadata or incomplete jobs array");
            }
        }

        var postings = new ArrayList<Posting>();
        var ids = new HashSet<String>();
        for (var job : jobs) {
            int index = postings.size();
            var id = job.path("id");
            if (!job.isObject() || !id.isIntegralNumber() || !id.canConvertToLong() || id.longValue() <= 0) {
                throw invalidJob(index, "id");
            }
            String externalId = id.asString();
            if (!ids.add(externalId)) {
                throw invalidJob(index, "duplicate id");
            }
            String title = requiredText(job, "title", index);
            String url = requiredText(job, "absolute_url", index);
            if (!isHttpsUrl(url)) {
                throw invalidJob(index, "absolute_url");
            }
            var location = job.path("location");
            if (!location.isMissingNode() && !location.isNull() && !location.isObject()) {
                throw invalidJob(index, "location");
            }
            Instant published = timestamp(job, "first_published", index);
            Instant updated = timestamp(job, "updated_at", index);
            postings.add(new Posting(externalId, title, optionalText(location, "name", index), url,
                    optionalText(job, "content", index), published != null ? published : updated));
        }
        return List.copyOf(postings);
    }

    private static String requiredText(JsonNode parent, String field, int index) throws InvalidSnapshotException {
        var value = optionalText(parent, field, index);
        if (value == null || value.isBlank()) {
            throw invalidJob(index, field);
        }
        return value;
    }

    private static String optionalText(JsonNode parent, String field, int index) throws InvalidSnapshotException {
        var node = parent.path(field);
        if (node.isMissingNode() || node.isNull()) {
            return null;
        }
        if (!node.isTextual() || node.asString().indexOf('\0') >= 0) {
            throw invalidJob(index, field);
        }
        return node.asString();
    }

    private static boolean isHttpsUrl(String value) {
        try {
            var uri = URI.create(value);
            return "https".equalsIgnoreCase(uri.getScheme()) && uri.getHost() != null && uri.getUserInfo() == null;
        } catch (IllegalArgumentException e) {
            return false;
        }
    }

    private static Instant timestamp(JsonNode job, String field, int index) throws InvalidSnapshotException {
        var value = optionalText(job, field, index);
        if (value == null) {
            return null;
        }
        try {
            return OffsetDateTime.parse(value).toInstant();
        } catch (DateTimeParseException e) {
            throw invalidJob(index, field);
        }
    }

    static Optional<Duration> parseRetryAfter(String header, Instant now) {
        String value = header.strip();
        try {
            if (value.matches("[0-9]+")) {
                return Optional.of(Duration.ofSeconds(Long.parseLong(value)));
            }
            var date = ZonedDateTime.parse(value, DateTimeFormatter.RFC_1123_DATE_TIME).toInstant();
            return Optional.of(date.isAfter(now) ? Duration.between(now, date) : Duration.ZERO);
        } catch (NumberFormatException | DateTimeParseException e) {
            return Optional.empty();
        }
    }

    private static InvalidSnapshotException invalidJob(int index, String field) {
        return new InvalidSnapshotException("Invalid Greenhouse jobs[" + index + "] field: " + field);
    }

    /** The URL is supplied by the board, not fetched by this client. Description is nullable, untrusted HTML. */
    public record Posting(String externalId, String title, String location, String canonicalUrl,
                          String description, Instant postedAt) {}

    public static final class InvalidSnapshotException extends IOException {
        private InvalidSnapshotException(String message) {
            super(message);
        }

        private InvalidSnapshotException(String message, Throwable cause) {
            super(message, cause);
        }
    }

    public static final class HttpStatusException extends IOException {
        private final int statusCode;
        private final Optional<Duration> retryAfter;

        private HttpStatusException(int statusCode, Optional<Duration> retryAfter) {
            super("Greenhouse returned HTTP " + statusCode);
            this.statusCode = statusCode;
            this.retryAfter = retryAfter;
        }

        public int statusCode() { return statusCode; }

        public boolean retryable() {
            return statusCode == 408 || statusCode == 429 || (statusCode >= 500 && statusCode < 600);
        }

        /** Suggested delay when supplied by Greenhouse; callers should cap it to their retry policy. */
        public Optional<Duration> retryAfter() { return retryAfter; }
    }
}
