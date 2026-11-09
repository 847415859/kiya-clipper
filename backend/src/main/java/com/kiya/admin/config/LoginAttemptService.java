package com.kiya.admin.config;

import java.time.Instant;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

import org.springframework.stereotype.Component;

/**
 * 防暴力破解：同一 IP 连续失败 5 次锁 15 分钟；同一用户名累计失败 20 次（任意 IP）锁 15 分钟。
 * 只存在内存里，重启清零，够单机后台用。
 */
@Component
public class LoginAttemptService {
    static final int MAX_PER_IP = 5;
    static final int MAX_PER_USER = 20;
    static final long LOCK_SECONDS = 15 * 60;

    private static final class Counter {
        int fails;
        long lockedUntil;
        long lastFail;
    }

    private final Map<String, Counter> byIp = new ConcurrentHashMap<>();
    private final Map<String, Counter> byUser = new ConcurrentHashMap<>();

    public long lockedSeconds(String ip, String user) {
        long now = Instant.now().getEpochSecond();
        long until = Math.max(until(byIp.get(ip)), until(user == null ? null : byUser.get(user)));
        return Math.max(0, until - now);
    }

    public void onFailure(String ip, String user) {
        bump(byIp, ip, MAX_PER_IP);
        if (user != null && !user.isBlank()) {
            bump(byUser, user, MAX_PER_USER);
        }
        if (byIp.size() > 10000) {           // 防止被刷爆内存
            byIp.clear();
        }
    }

    public void onSuccess(String ip, String user) {
        byIp.remove(ip);
        if (user != null) {
            byUser.remove(user);
        }
    }

    private static long until(Counter c) {
        return c == null ? 0 : c.lockedUntil;
    }

    private static void bump(Map<String, Counter> map, String key, int max) {
        long now = Instant.now().getEpochSecond();
        Counter c = map.computeIfAbsent(key, k -> new Counter());
        synchronized (c) {
            if (now - c.lastFail > LOCK_SECONDS) {   // 很久没失败过：重新计数
                c.fails = 0;
            }
            c.fails++;
            c.lastFail = now;
            if (c.fails >= max) {
                c.lockedUntil = now + LOCK_SECONDS;
                c.fails = 0;
            }
        }
    }
}
