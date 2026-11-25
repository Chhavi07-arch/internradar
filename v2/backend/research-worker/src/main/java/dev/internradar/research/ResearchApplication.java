package dev.internradar.research;

import dev.internradar.contracts.ResearchFetchRequested;
import org.apache.kafka.clients.admin.NewTopic;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.context.annotation.Bean;
import org.springframework.kafka.config.TopicBuilder;
import org.springframework.kafka.listener.DefaultErrorHandler;
import org.springframework.scheduling.annotation.EnableScheduling;
import org.springframework.util.backoff.FixedBackOff;

@SpringBootApplication
@EnableScheduling
public class ResearchApplication {
    public static void main(String[] args) {
        SpringApplication.run(ResearchApplication.class, args);
    }

    @Bean
    DefaultErrorHandler preserveUndurableRecords() {
        var handler = new DefaultErrorHandler(new FixedBackOff(5000L, FixedBackOff.UNLIMITED_ATTEMPTS));
        handler.setClassifications(java.util.Map.of(), true);
        handler.setAckAfterHandle(false);
        return handler;
    }

    @Bean
    NewTopic fetchRequests() {
        return TopicBuilder.name(ResearchFetchRequested.TOPIC).partitions(3).replicas(1)
                .config("retention.ms", "604800000").build();
    }

    @Bean
    NewTopic fetchDeadLetters() {
        return TopicBuilder.name(ResearchFetchRequested.DEAD_LETTER_TOPIC).partitions(3).replicas(1)
                .config("retention.ms", "2592000000").build();
    }
}
