package com.kiya.admin.config;

import java.io.IOException;

import org.springframework.web.filter.OncePerRequestFilter;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import jakarta.servlet.http.HttpSession;

/** 每个请求开始时，从 session 读「当前主播」（没有就用配置默认值），放进 ThreadLocal，请求结束清除。 */
public class StreamerFilter extends OncePerRequestFilter {
    public static final String SESSION_KEY = "kiya.streamer";

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {
        HttpSession session = request.getSession(false);
        String streamer = session == null ? null : (String) session.getAttribute(SESSION_KEY);
        if (streamer != null && !streamer.isBlank()) {
            StreamerContext.set(streamer);
        }
        try {
            chain.doFilter(request, response);
        } finally {
            StreamerContext.clear();
        }
    }
}
