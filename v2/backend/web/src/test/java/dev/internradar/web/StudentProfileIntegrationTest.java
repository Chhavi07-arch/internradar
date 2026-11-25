package dev.internradar.web;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.mock.web.MockHttpSession;
import tools.jackson.databind.json.JsonMapper;

import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.oidcLogin;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

@SpringBootTest
@AutoConfigureMockMvc
@EnabledIfEnvironmentVariable(named = "INTERNRADAR_PROFILE_IT_JDBC_URL", matches = ".+")
class StudentProfileIntegrationTest {
    @Autowired MockMvc mvc;

    @DynamicPropertySource
    static void database(DynamicPropertyRegistry properties) {
        properties.add("spring.datasource.url", () -> System.getenv("INTERNRADAR_PROFILE_IT_JDBC_URL"));
        properties.add("spring.datasource.username", () -> "internradar_web");
        properties.add("spring.datasource.password", () -> System.getenv("INTERNRADAR_PROFILE_IT_DB_PASSWORD"));
    }

    @Test
    void twoVerifiedSubjectsWithTheSameEmailHaveSeparateProfiles() throws Exception {
        var first = browser("first-student");
        var second = browser("second-student");
        mvc.perform(get("/api/profile")).andExpect(status().isUnauthorized());
        mvc.perform(get("/api/profile").session(first.session())).andExpect(jsonPath("$.status").value("pending"));
        mvc.perform(put("/api/profile").session(first.session()).header(first.header(), first.token()).contentType("application/json")
                .content("""
                         {"roles":[],"locations":[],"remote":false,"graduationYear":9999}
                         """)).andExpect(status().isBadRequest());
        mvc.perform(put("/api/profile").session(first.session()).contentType("application/json")
                .content("{}")).andExpect(status().isForbidden());
        mvc.perform(put("/api/profile").session(first.session()).header(first.header(), first.token()).contentType("application/json")
                .content("""
                         {"roles":["Backend"],"locations":["Bengaluru"],"remote":true,"graduationYear":2028}
                         """)).andExpect(status().isOk()).andExpect(jsonPath("$.status").value("completed"));
        mvc.perform(get("/api/profile").session(second.session())).andExpect(jsonPath("$.status").value("pending"));
        mvc.perform(post("/api/profile/skip").session(second.session()).header(second.header(), second.token()))
                .andExpect(jsonPath("$.status").value("skipped"));
        mvc.perform(post("/api/profile/skip").session(first.session()).header(first.header(), first.token()))
                .andExpect(jsonPath("$.status").value("completed")).andExpect(jsonPath("$.roles[0]").value("Backend"));
        mvc.perform(get("/api/profile").session(second.session())).andExpect(jsonPath("$.roles").isEmpty());
    }

    private Browser browser(String subject) throws Exception {
        var result = mvc.perform(get("/api/csrf").with(oidcLogin().idToken(token -> token.subject(subject)
                .issuer("https://accounts.google.com").claim("email", "shared@example.test")
                .claim("email_verified", true)))).andExpect(status().isOk()).andReturn();
        var csrf = JsonMapper.builder().build().readTree(result.getResponse().getContentAsString());
        return new Browser((MockHttpSession) result.getRequest().getSession(false),
                csrf.path("headerName").asString(), csrf.path("token").asString());
    }

    private record Browser(MockHttpSession session, String header, String token) {}
}
