package dev.internradar.web;

import jakarta.servlet.DispatcherType;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.mock.web.MockHttpSession;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.request.RequestPostProcessor;
import tools.jackson.databind.json.JsonMapper;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.oidcLogin;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@SpringBootTest(properties = {
        "spring.autoconfigure.exclude=org.springframework.boot.jdbc.autoconfigure.DataSourceAutoConfiguration",
        "internradar.google.client-id=",
        "internradar.google.client-secret="
})
@AutoConfigureMockMvc
class SecurityConfigTest {
    @Autowired MockMvc mvc;
    @MockitoBean JdbcClient jdbc;

    @Test
    void publicEndpointsAndProtectedApiWhenGoogleIsDisabled() throws Exception {
        mvc.perform(get("/actuator/health")).andExpect(status().isOk());
        mvc.perform(get("/api/session")).andExpect(status().isOk())
                .andExpect(jsonPath("$.authenticated").value(false))
                .andExpect(jsonPath("$.loginAvailable").value(false));
        mvc.perform(get("/api/profile")).andExpect(status().isUnauthorized())
                .andExpect(header().doesNotExist("Location"));
        mvc.perform(get("/oauth2/authorization/google")).andExpect(status().isUnauthorized());
    }

    @Test
    void onlyTheSpaIndexCanBeForwardedWithoutAuthentication() throws Exception {
        RequestPostProcessor forward = request -> {
            request.setDispatcherType(DispatcherType.FORWARD);
            return request;
        };
        mvc.perform(get("/index.html").with(forward))
                .andExpect(result -> assertThat(result.getResponse().getStatus()).isIn(200, 404));
        mvc.perform(get("/index.html")).andExpect(status().isUnauthorized());
        mvc.perform(get("/api/profile").with(forward))
                .andExpect(status().isUnauthorized());
    }

    @Test
    void logoutRequiresPostAndTheCsrfHeaderFromTheSameSession() throws Exception {
        var signIn = oidcLogin().idToken(token -> token.claim("email", "student@example.test"));
        var csrfResponse = mvc.perform(get("/api/csrf").with(signIn)).andExpect(status().isOk()).andReturn();
        var session = (MockHttpSession) csrfResponse.getRequest().getSession(false);
        var csrf = JsonMapper.builder().build().readTree(csrfResponse.getResponse().getContentAsString());
        String headerName = csrf.get("headerName").asString();
        String token = csrf.get("token").asString();
        assertThat(session).isNotNull();
        assertThat(headerName).isNotBlank();
        assertThat(token).isNotBlank();

        mvc.perform(get("/logout").session(session)).andExpect(status().isNotFound());
        mvc.perform(post("/logout").session(session)).andExpect(status().isForbidden());
        mvc.perform(post("/logout").session(session).header(headerName, "wrong-token"))
                .andExpect(status().isForbidden());
        mvc.perform(post("/logout").header(headerName, token)).andExpect(status().isForbidden());
        mvc.perform(get("/api/session").session(session)).andExpect(status().isOk())
                .andExpect(jsonPath("$.authenticated").value(true));
        mvc.perform(post("/logout").session(session).header(headerName, token)).andExpect(status().isNoContent());
        assertThat(session.isInvalid()).isTrue();
    }
}

@SpringBootTest(properties = {
        "spring.autoconfigure.exclude=org.springframework.boot.jdbc.autoconfigure.DataSourceAutoConfiguration",
        "internradar.google.client-id=example-client",
        "internradar.google.client-secret=example-secret",
        "INTERNRADAR_GOOGLE_REDIRECT_URI=https://login.example.test/login/oauth2/code/google"
})
@AutoConfigureMockMvc
class GoogleSecurityConfigTest {
    @Autowired MockMvc mvc;
    @MockitoBean JdbcClient jdbc;

    @Test
    void googleLoginUsesTheConfiguredRedirectUriRegardlessOfRequestHost() throws Exception {
        mvc.perform(get("/api/session")).andExpect(status().isOk())
                .andExpect(jsonPath("$.loginAvailable").value(true));
        var result = mvc.perform(get("/oauth2/authorization/google").header("Host", "other.example.test"))
                .andExpect(status().isFound()).andReturn();
        var location = result.getResponse().getRedirectedUrl();
        assertThat(location).startsWith("https://accounts.google.com/")
                .contains("redirect_uri=https://login.example.test/login/oauth2/code/google")
                .contains("client_id=example-client");
        mvc.perform(get("/login/oauth2/code/google").param("error", "access_denied").param("state", "invalid"))
                .andExpect(status().isFound()).andExpect(header().string("Location", "/?loginError=1"));
        mvc.perform(get("/api/profile")).andExpect(status().isUnauthorized())
                .andExpect(header().doesNotExist("Location"));
    }
}
