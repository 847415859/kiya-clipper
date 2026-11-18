package com.kiya.admin.service;

import java.time.Duration;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import org.springframework.stereotype.Service;
import com.baomidou.mybatisplus.core.conditions.query.QueryWrapper;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.kiya.admin.common.ApiException;
import com.kiya.admin.entity.Job;
import com.kiya.admin.mapper.JobMapper;
import jakarta.servlet.http.HttpSession;

@Service
public class PublishAccountService {
    // Account activation and publish-job insertion must not race in this service instance.
    public static final Object CHANGE_LOCK = new Object();
    private final WorkerPaths paths;
    private final ProcessRunner runner;
    private final ObjectMapper om;
    private final JobMapper jobs;

    static final class Ticket {
        final String id;
        final long deadline = System.currentTimeMillis() + 600_000;
        long checkedAt;
        Map<String, Object> result = Map.of("state", "waiting");
        Ticket(String id) { this.id = id; }
    }

    public PublishAccountService(WorkerPaths paths, ProcessRunner runner, ObjectMapper om, JobMapper jobs) {
        this.paths = paths; this.runner = runner; this.om = om; this.jobs = jobs;
    }

    private String key() { return "publish-login:" + paths.streamer(); }

    private Map<String, Object> call(String action, List<String> extra) {
        List<String> args = new ArrayList<>(List.of(action, "--streamer", paths.streamer()));
        args.addAll(extra);
        ProcessRunner.Result response = runner.run("publish_account.py", args, Duration.ofSeconds(45));
        Map<String, Object> result;
        try {
            result = om.readValue(response.output().trim(), new TypeReference<LinkedHashMap<String, Object>>() {});
        } catch (Exception e) {
            throw ApiException.badRequest("发布账号服务不可用，请检查 worker 依赖后重试");
        }
        if (response.exitCode() != 0 || result.containsKey("error")) {
            throw ApiException.badRequest(String.valueOf(result.getOrDefault("error", "发布账号操作失败")));
        }
        // Only explicit public fields may cross the HTTP boundary.
        Map<String, Object> safe = new LinkedHashMap<>();
        for (String name : List.of("state", "source", "account", "message", "login_id", "qr_image", "expires_at")) {
            if (result.containsKey(name)) safe.put(name, result.get(name));
        }
        if (safe.get("account") instanceof Map<?, ?> account) {
            safe.put("account", Map.of("uid", String.valueOf(account.get("uid")), "name", String.valueOf(account.get("name"))));
        }
        return safe;
    }

    public Map<String, Object> status() { return call("status", List.of()); }

    public Map<String, Object> start(HttpSession session) {
        synchronized (session) {
            Ticket previous = session.getAttribute(key()) instanceof Ticket t ? t : null;
            if (session.getAttribute(key()+":started") instanceof Long at && System.currentTimeMillis() - at < 3000) {
                throw ApiException.badRequest("请稍等几秒再刷新二维码");
            }
            Map<String, Object> result = call("start", List.of());
            String id = String.valueOf(result.get("login_id"));
            if (!id.matches("[a-f0-9]{32}")) throw ApiException.badRequest("二维码请求无效");
            Ticket ticket = new Ticket(id);
            ticket.checkedAt = System.currentTimeMillis();
            session.setAttribute(key()+":started", ticket.checkedAt);
            session.setAttribute(key(), ticket);
            if (previous != null) {
                try { call("cancel", List.of("--login-id", previous.id)); } catch (ApiException ignored) { }
            }
            return result;
        }
    }

    private Ticket ticket(HttpSession session, String id) {
        if (!(session.getAttribute(key()) instanceof Ticket t) || !t.id.equals(id)
                || !id.matches("[a-f0-9]{32}") || t.deadline < System.currentTimeMillis()) {
            throw ApiException.badRequest("登录请求已过期或不属于当前会话，请重新获取二维码");
        }
        return t;
    }

    public Map<String, Object> poll(HttpSession session, String id) {
        synchronized (session) {
            Ticket t = ticket(session, id);
            if ("ready".equals(t.result.get("state")) || System.currentTimeMillis() - t.checkedAt < 3000) return t.result;
            t.result = call("poll", List.of("--login-id", id));
            t.checkedAt = System.currentTimeMillis();
            return t.result;
        }
    }

    private void ensureIdle() {
        if (jobs.selectCount(new QueryWrapper<Job>().eq("type", "PUBLISH_BILI").in("status", List.of("PENDING", "RUNNING"))) > 0) {
            throw ApiException.badRequest("有投稿任务正在排队或上传，请等待完成后再切换或停用发布账号");
        }
    }

    public Map<String, Object> activate(HttpSession session, String id, String uid) {
        synchronized (CHANGE_LOCK) {
            synchronized (session) {
                Ticket t = ticket(session, id);
                if (!"ready".equals(t.result.get("state")) || uid == null || !uid.matches("[0-9]{1,20}")
                        || !(t.result.get("account") instanceof Map<?, ?> account) || !uid.equals(String.valueOf(account.get("uid")))) {
                    throw ApiException.badRequest("请先扫码并核对账号后确认");
                }
                ensureIdle();
                Map<String, Object> result = call("activate", List.of("--login-id", id, "--uid", uid));
                session.removeAttribute(key());
                return result;
            }
        }
    }

    public Map<String, Object> cancel(HttpSession session, String id) {
        synchronized (session) {
            ticket(session, id);
            Map<String, Object> result = call("cancel", List.of("--login-id", id));
            session.removeAttribute(key());
            return result;
        }
    }

    public Map<String, Object> logout(HttpSession session) {
        synchronized (CHANGE_LOCK) {
            ensureIdle();
            Map<String, Object> result = call("logout", List.of());
            session.removeAttribute(key());
            return result;
        }
    }
}
