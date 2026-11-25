package dev.internradar.web;

import java.net.URI;

import jakarta.servlet.DispatcherType;

import org.springframework.beans.factory.ObjectProvider;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Condition;
import org.springframework.context.annotation.ConditionContext;
import org.springframework.context.annotation.Conditional;
import org.springframework.context.annotation.Configuration;
import org.springframework.core.type.AnnotatedTypeMetadata;
import org.springframework.http.HttpStatus;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.annotation.web.configurers.AbstractHttpConfigurer;
import org.springframework.security.config.oauth2.client.CommonOAuth2Provider;
import org.springframework.security.oauth2.client.oidc.userinfo.OidcUserService;
import org.springframework.security.oauth2.client.registration.ClientRegistrationRepository;
import org.springframework.security.oauth2.client.registration.InMemoryClientRegistrationRepository;
import org.springframework.security.oauth2.core.OAuth2AuthenticationException;
import org.springframework.security.oauth2.core.OAuth2Error;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.security.web.authentication.HttpStatusEntryPoint;
import org.springframework.security.web.authentication.logout.HttpStatusReturningLogoutSuccessHandler;
import org.springframework.security.web.servlet.util.matcher.PathPatternRequestMatcher;
import org.springframework.security.web.util.matcher.AndRequestMatcher;
import org.springframework.security.web.util.matcher.DispatcherTypeRequestMatcher;
import org.springframework.util.StringUtils;

@Configuration(proxyBeanMethods = false)
class SecurityConfig {
    @Bean
    SecurityFilterChain securityFilterChain(
            HttpSecurity http, ObjectProvider<ClientRegistrationRepository> registrations) {
        var googleEnabled = registrations.getIfAvailable() != null;
        http.authorizeHttpRequests(auth -> {
                    auth.dispatcherTypeMatchers(DispatcherType.ERROR).permitAll();
                    // Boot forwards the public welcome page internally to this static resource.
                    auth.requestMatchers(new AndRequestMatcher(new DispatcherTypeRequestMatcher(DispatcherType.FORWARD),
                            PathPatternRequestMatcher.withDefaults().matcher("/index.html"))).permitAll();
                    auth.requestMatchers("/", "/assets/**", "/actuator/health", "/api/session", "/api/csrf")
                            .permitAll();
                    if (googleEnabled) {
                        auth.requestMatchers("/oauth2/authorization/google", "/login/oauth2/code/google")
                                .permitAll();
                    }
                    auth.anyRequest().authenticated();
                })
                .exceptionHandling(errors -> errors.authenticationEntryPoint(new HttpStatusEntryPoint(HttpStatus.UNAUTHORIZED)))
                .requestCache(AbstractHttpConfigurer::disable)
                .logout(logout -> logout.logoutSuccessHandler(new HttpStatusReturningLogoutSuccessHandler(HttpStatus.NO_CONTENT)));

        // Keep Spring Security's session-backed, masked CSRF tokens, including for POST /logout.
        if (googleEnabled) {
            var oidcUserService = new OidcUserService();
            http.oauth2Login(login -> login.loginPage("/")
                    .defaultSuccessUrl("/", true)
                    .failureUrl("/?loginError=1")
                    .userInfoEndpoint(userInfo -> userInfo.oidcUserService(request -> {
                        var idToken = request.getIdToken();
                        if (!Boolean.TRUE.equals(idToken.getClaims().get("email_verified"))
                                || !StringUtils.hasText(idToken.getEmail())) {
                            throw new OAuth2AuthenticationException(new OAuth2Error("unverified_email"));
                        }
                        return oidcUserService.loadUser(request);
                    })));
        }
        return http.build();
    }

    @Bean
    @Conditional(GoogleCredentialsPresent.class)
    ClientRegistrationRepository googleClientRegistrationRepository(
            @Value("${internradar.google.client-id}") String clientId,
            @Value("${internradar.google.client-secret}") String clientSecret,
            @Value("${internradar.google.redirect-uri}") String redirectUri) {
        URI callback = URI.create(redirectUri);
        if (callback.getHost() == null || callback.getUserInfo() != null || callback.getFragment() != null
                || !("http".equalsIgnoreCase(callback.getScheme()) || "https".equalsIgnoreCase(callback.getScheme()))) {
            throw new IllegalArgumentException("Google redirect URI must be a fixed HTTP(S) URL without user info or a fragment");
        }
        return new InMemoryClientRegistrationRepository(CommonOAuth2Provider.GOOGLE.getBuilder("google")
                .clientId(clientId)
                .clientSecret(clientSecret)
                .redirectUri(redirectUri)
                .build());
    }

    static final class GoogleCredentialsPresent implements Condition {
        @Override
        public boolean matches(ConditionContext context, AnnotatedTypeMetadata metadata) {
            var environment = context.getEnvironment();
            return StringUtils.hasText(environment.getProperty("internradar.google.client-id"))
                    && StringUtils.hasText(environment.getProperty("internradar.google.client-secret"));
        }
    }
}
