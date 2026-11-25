package dev.internradar.web;

import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.client.registration.ClientRegistrationRepository;
import org.springframework.security.oauth2.core.oidc.user.OidcUser;
import org.springframework.security.web.csrf.CsrfToken;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.server.ResponseStatusException;
import tools.jackson.databind.json.JsonMapper;

@RestController
@RequestMapping("/api")
class StudentController {
    private static final Profile PENDING = new Profile("pending", List.of(), List.of(), false, null);
    private static final Profile SKIPPED = new Profile("skipped", List.of(), List.of(), false, null);
    private final JdbcClient jdbc;
    private final JsonMapper json = JsonMapper.builder().build();
    private final ObjectProvider<ClientRegistrationRepository> registrations;

    StudentController(JdbcClient jdbc, ObjectProvider<ClientRegistrationRepository> registrations) {
        this.jdbc = jdbc;
        this.registrations = registrations;
    }

    @GetMapping("/session")
    Session session(@AuthenticationPrincipal OidcUser user) {
        var registration = registrations.getIfAvailable();
        boolean available = registration != null && registration.findByRegistrationId("google") != null;
        return user == null ? new Session(false, available, null, null)
                : new Session(true, available, user.getFullName(), user.getEmail());
    }

    @GetMapping("/csrf")
    Map<String, String> csrf(CsrfToken token) {
        return Map.of("headerName", token.getHeaderName(), "token", token.getToken());
    }

    @GetMapping("/profile")
    Profile getProfile(@AuthenticationPrincipal OidcUser user) {
        return read(account(user));
    }

    @PutMapping("/profile")
    Profile updateProfile(@AuthenticationPrincipal OidcUser user, @RequestBody Preferences preferences) {
        var roles = clean(preferences.roles());
        var locations = clean(preferences.locations());
        var year = preferences.graduationYear();
        if (roles.isEmpty() || (year != null && (year < 2000 || year > 2100))) {
            throw badPreferences();
        }
        var profile = new Profile("completed", roles, locations, preferences.remote(), year);
        jdbc.sql("""
                INSERT INTO app.profiles (account_id, document) VALUES (:id, CAST(:profile AS jsonb))
                ON CONFLICT (account_id) DO UPDATE SET document = excluded.document, updated_at = now()
                """).param("id", account(user)).param("profile", json.writeValueAsString(profile)).update();
        return profile;
    }

    @PostMapping("/profile/skip")
    Profile skipProfile(@AuthenticationPrincipal OidcUser user) {
        UUID id = account(user);
        jdbc.sql("""
                INSERT INTO app.profiles (account_id, document) VALUES (:id, CAST(:profile AS jsonb))
                ON CONFLICT (account_id) DO NOTHING
                """).param("id", id).param("profile", json.writeValueAsString(SKIPPED)).update();
        return read(id);
    }

    private Profile read(UUID id) {
        return jdbc.sql("SELECT document::text FROM app.profiles WHERE account_id = :id")
                .param("id", id).query(String.class).optional()
                .map(document -> json.readValue(document, Profile.class)).orElse(PENDING);
    }

    private UUID account(OidcUser user) {
        if (user == null || user.getIssuer() == null || user.getSubject() == null || user.getSubject().isBlank()
                || !Boolean.TRUE.equals(user.getEmailVerified()) || user.getEmail() == null || user.getEmail().isBlank()) {
            throw new ResponseStatusException(HttpStatus.UNAUTHORIZED);
        }
        return jdbc.sql("""
                INSERT INTO app.accounts (issuer, subject, email, display_name)
                VALUES (:issuer, :subject, :email, :name)
                ON CONFLICT (issuer, subject) DO UPDATE SET email = excluded.email, display_name = excluded.display_name
                RETURNING id
                """).param("issuer", user.getIssuer().toString()).param("subject", user.getSubject())
                .param("email", user.getEmail()).param("name", user.getFullName()).query(UUID.class).single();
    }

    private static List<String> clean(List<String> values) {
        if (values == null || values.size() > 10) throw badPreferences();
        for (String value : values) {
            if (value == null || value.isBlank() || value.length() > 80 || value.indexOf('\0') >= 0) {
                throw badPreferences();
            }
        }
        return values.stream().map(String::strip).distinct().toList();
    }

    private static ResponseStatusException badPreferences() {
        return new ResponseStatusException(HttpStatus.BAD_REQUEST, "Invalid student preferences");
    }

    record Session(boolean authenticated, boolean loginAvailable, String name, String email) {}
    record Profile(String status, List<String> roles, List<String> locations, boolean remote, Integer graduationYear) {}
    record Preferences(List<String> roles, List<String> locations, boolean remote, Integer graduationYear) {}
}
