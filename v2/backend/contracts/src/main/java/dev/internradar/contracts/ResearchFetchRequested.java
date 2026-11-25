package dev.internradar.contracts;

import java.time.Instant;
import java.util.UUID;

public record ResearchFetchRequested(int version, UUID taskId, String sourceId,
                                     Instant windowStart, int attempt) {
    public static final String TOPIC = "research.fetch.requested.v1";
    public static final String DEAD_LETTER_TOPIC = "research.fetch.dead-letter.v1";
}
