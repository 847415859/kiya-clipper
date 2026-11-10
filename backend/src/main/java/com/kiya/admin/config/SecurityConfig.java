package com.kiya.admin.config;

import java.io.IOException;
import java.util.Map;
import java.util.function.Supplier;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.security.web.authentication.HttpStatusEntryPoint;
import org.springframework.security.web.authentication.UsernamePasswordAuthenticationFilter;
import org.springframework.security.web.authentication.www.BasicAuthenticationFilter;
import org.springframework.security.web.csrf.CookieCsrfTokenRepository;
import org.springframework.security.web.csrf.CsrfToken;
import org.springframework.security.web.csrf.CsrfTokenRequestAttributeHandler;
import org.springframework.security.web.csrf.CsrfTokenRequestHandler;
import org.springframework.security.web.csrf.XorCsrfTokenRequestAttributeHandler;
import org.springframework.util.StringUtils;
import org.springframework.web.filter.OncePerRequestFilter;

import com.fasterxml.jackson.databind.ObjectMapper;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;

/**
 * 登录：POST /api/auth/login（表单字段 username、password），成功后用会话 Cookie。
 * CSRF：Cookie XSRF-TOKEN + 请求头 X-XSRF-TOKEN（axios 自动处理），按 Spring Security 官方的 SPA 写法。
 * 未登录访问 /api/** 返回 401，由前端跳登录页。
 */
@Configuration
public class SecurityConfig {

    @Bean
    public PasswordEncoder passwordEncoder() {
        return new BCryptPasswordEncoder();
    }

    @Bean
    public SecurityFilterChain filterChain(HttpSecurity http, LoginAttemptService attempts, ObjectMapper om)
            throws Exception {
        http
            .csrf(csrf -> csrf
                .csrfTokenRepository(CookieCsrfTokenRepository.withHttpOnlyFalse())
                .csrfTokenRequestHandler(new SpaCsrfTokenRequestHandler()))
            .addFilterAfter(new CsrfCookieFilter(), BasicAuthenticationFilter.class)
            .addFilterBefore(new StreamerFilter(), UsernamePasswordAuthenticationFilter.class)
            .addFilterBefore(new LoginRateLimitFilter(attempts, om), UsernamePasswordAuthenticationFilter.class)
            .authorizeHttpRequests(auth -> auth
                .requestMatchers("/api/auth/me", "/api/auth/login").permitAll()
                .requestMatchers("/api/**").authenticated()
                .anyRequest().permitAll())                       // 前端静态文件
            .formLogin(form -> form
                .loginProcessingUrl("/api/auth/login")
                .successHandler((req, res, a) -> {
                    attempts.onSuccess(req.getRemoteAddr(), a.getName());
                    json(om, res, HttpStatus.OK, Map.of("username", a.getName()));
                })
                .failureHandler((req, res, e) -> {
                    attempts.onFailure(req.getRemoteAddr(), req.getParameter("username"));
                    json(om, res, HttpStatus.UNAUTHORIZED, Map.of("error", "用户名或密码错误"));
                })
                .permitAll())
            .logout(lo -> lo
                .logoutUrl("/api/auth/logout")
                .logoutSuccessHandler((req, res, a) -> json(om, res, HttpStatus.OK, Map.of("ok", true))))
            .exceptionHandling(ex -> ex.authenticationEntryPoint(new HttpStatusEntryPoint(HttpStatus.UNAUTHORIZED)))
            .headers(h -> h
                .frameOptions(f -> f.deny())
                .contentSecurityPolicy(csp -> csp.policyDirectives(
                    "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; "
                    + "style-src 'self' 'unsafe-inline'; script-src 'self'; object-src 'none'; frame-ancestors 'none'")));
        return http.build();
    }

    static void json(ObjectMapper om, HttpServletResponse res, HttpStatus status, Object body) throws IOException {
        res.setStatus(status.value());
        res.setContentType(MediaType.APPLICATION_JSON_VALUE);
        res.setCharacterEncoding("UTF-8");
        om.writeValue(res.getOutputStream(), body);
    }

    /** 登录接口前先查是否被锁定。 */
    static final class LoginRateLimitFilter extends OncePerRequestFilter {
        private final LoginAttemptService attempts;
        private final ObjectMapper om;

        LoginRateLimitFilter(LoginAttemptService attempts, ObjectMapper om) {
            this.attempts = attempts;
            this.om = om;
        }

        @Override
        protected void doFilterInternal(HttpServletRequest req, HttpServletResponse res, FilterChain chain)
                throws ServletException, IOException {
            if ("POST".equals(req.getMethod()) && "/api/auth/login".equals(req.getServletPath())) {
                long wait = attempts.lockedSeconds(req.getRemoteAddr(), req.getParameter("username"));
                if (wait > 0) {
                    json(om, res, HttpStatus.TOO_MANY_REQUESTS,
                        Map.of("error", "失败次数太多，请 " + (wait / 60 + 1) + " 分钟后再试"));
                    return;
                }
            }
            chain.doFilter(req, res);
        }
    }

    /** Spring Security 文档中的 SPA CSRF 写法：请求头里给的是原始 token，表单参数走 XOR 防 BREACH。 */
    static final class SpaCsrfTokenRequestHandler extends CsrfTokenRequestAttributeHandler {
        private final CsrfTokenRequestHandler delegate = new XorCsrfTokenRequestAttributeHandler();

        @Override
        public void handle(HttpServletRequest request, HttpServletResponse response, Supplier<CsrfToken> csrfToken) {
            this.delegate.handle(request, response, csrfToken);
        }

        @Override
        public String resolveCsrfTokenValue(HttpServletRequest request, CsrfToken csrfToken) {
            if (StringUtils.hasText(request.getHeader(csrfToken.getHeaderName()))) {
                return super.resolveCsrfTokenValue(request, csrfToken);
            }
            return this.delegate.resolveCsrfTokenValue(request, csrfToken);
        }
    }

    /** 每个请求都确保 XSRF-TOKEN Cookie 已下发（延迟加载的 token 需要主动读一次）。 */
    static final class CsrfCookieFilter extends OncePerRequestFilter {
        @Override
        protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
                throws ServletException, IOException {
            CsrfToken token = (CsrfToken) request.getAttribute("_csrf");
            if (token != null) {
                token.getToken();
            }
            chain.doFilter(request, response);
        }
    }
}
