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
        long deadline = System.currentTimeMillis() + 600_000;
        final String method;
        long checkedAt;
        Map<String, Object> result = Map.of("state", "waiting");
        Ticket(String id) { this(id, "qr"); }
        Ticket(String id, String method) { this.id = id; this.method = method; }
    }

    public PublishAccountService(WorkerPaths paths, ProcessRunner runner, ObjectMapper om, JobMapper jobs) {
        this.paths = paths; this.runner = runner; this.om = om; this.jobs = jobs;
    }

    private String key() { return "publish-login:" + paths.streamer(); }

    private Map<String, Object> call(String action, List<String> extra) {
        List<String> args = new ArrayList<>(List.of(action, "--streamer", paths.streamer()));
        args.addAll(extra);
        ProcessRunner.Result response = runner.run("publish_account.py", args, Duration.ofSeconds(45));
        return publicResult(response);
    }

    private Map<String, Object> callInput(String action, String id, Map<String,Object> body) {
        Map<String,Object> safeInput = new LinkedHashMap<>();
        for (String name : List.of("username", "password", "country", "phone", "code", "cookie")) {
            if (body.containsKey(name)) {
                Object value = body.get(name);
                int limit = name.equals("cookie") ? 65536 : 256;
                if (!(value instanceof String text) || text.length() > limit) throw ApiException.badRequest("登录输入格式或长度不正确");
                safeInput.put(name, value);
            }
        }
        if (body.get("proof") instanceof Map<?,?> proof) {
            Map<String,Object> clean = new LinkedHashMap<>();
            for (String name : List.of("challenge", "validate", "seccode")) {
                Object value = proof.get(name);
                if (!(value instanceof String text) || text.length() > 512) throw ApiException.badRequest("人机验证结果无效");
                clean.put(name, value);
            }
            safeInput.put("proof", clean);
        }
        String input;
        try { input = om.writeValueAsString(safeInput); } catch (Exception e) { throw ApiException.badRequest("登录输入无法读取"); }
        if (input.getBytes(java.nio.charset.StandardCharsets.UTF_8).length > 131072) throw ApiException.badRequest("登录输入过长");
        List<String> args = new ArrayList<>(List.of(action, "--streamer", paths.streamer()));
        if (id != null) args.addAll(List.of("--login-id", id));
        return publicResult(runner.runInput("publish_account.py", args, input, Duration.ofSeconds(75)));
    }

    private Map<String, Object> publicResult(ProcessRunner.Result response) {
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
        for (String name : List.of("state", "source", "account", "message", "login_id", "qr_image", "expires_at", "method", "login_url", "captcha", "retry_after", "masked_phone")) {
            if (result.containsKey(name)) safe.put(name, result.get(name));
        }
        if (safe.get("account") instanceof Map<?, ?> account) {
            safe.put("account", Map.of("uid", String.valueOf(account.get("uid")), "name", String.valueOf(account.get("name"))));
        }
        if (safe.get("captcha") instanceof Map<?,?> captcha) {
            safe.put("captcha", Map.of("gt", String.valueOf(captcha.get("gt")), "challenge", String.valueOf(captcha.get("challenge"))));
        }
        if (safe.containsKey("login_url")) {
            try {
                var url = java.net.URI.create(String.valueOf(safe.get("login_url")));
                if (!"https".equals(url.getScheme()) || !List.of("passport.bilibili.com", "www.bilibili.com").contains(url.getHost())
                        || url.getUserInfo() != null || url.getPort() != -1 && url.getPort() != 443
                        || java.util.Arrays.stream((url.getRawQuery() == null ? "" : url.getRawQuery()).split("&"))
                           .map(part -> java.net.URLDecoder.decode(part.split("=",2)[0], java.nio.charset.StandardCharsets.UTF_8).toLowerCase(java.util.Locale.ROOT))
                           .anyMatch(List.of("sessdata","bili_jct","access_token","refresh_token")::contains)) safe.remove("login_url");
            } catch (Exception e) { safe.remove("login_url"); }
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

    private String family(String method) { return method.startsWith("sms-") ? "sms" : method; }

    private void limitInput(HttpSession session, String method) {
        long now = System.currentTimeMillis();
        if (method.equals("sms-send") && session.getAttribute(key()+":sms") instanceof Long at && now-at < 60000) {
            throw ApiException.badRequest("短信已发送，请等待 60 秒后再试");
        }
        if (method.equals("password")) {
            @SuppressWarnings("unchecked")
            List<Long> times = session.getAttribute(key()+":password") instanceof List<?> list ? new ArrayList<>((List<Long>) list) : new ArrayList<>();
            times.removeIf(at -> now-at >= 60000);
            if (times.size() >= 5) throw ApiException.badRequest("密码登录尝试过于频繁，请一分钟后再试");
            times.add(now); session.setAttribute(key()+":password", times);
        }
    }

    private void updateTicket(HttpSession session, Ticket t, Map<String,Object> result, String method) {
        t.result = result; t.checkedAt = System.currentTimeMillis();
        if (result.get("expires_at") instanceof Number expiry) t.deadline = Math.min(expiry.longValue()*1000+30000, System.currentTimeMillis()+600000);
        if (method.equals("sms-send") && "sms_sent".equals(result.get("state"))) session.setAttribute(key()+":sms",System.currentTimeMillis());
    }

    public Map<String,Object> login(HttpSession session, String method, String id, Map<String,Object> body) {
        if (!List.of("password", "sms-send", "sms-code", "cookie", "browser").contains(method)) throw ApiException.badRequest("不支持的登录方式");
        synchronized(session) {
            Ticket old = session.getAttribute(key()) instanceof Ticket t ? t : null;
            if (id != null) {
                old = ticket(session,id);
                if (!old.method.equals(family(method)) || !List.of("password","sms-send","sms-code").contains(method)) throw ApiException.badRequest("登录方式或会话不一致");
            } else {
                if (method.equals("sms-code")) throw ApiException.badRequest("请先发送短信验证码");
                if (session.getAttribute(key()+":started") instanceof Long at && System.currentTimeMillis()-at < 3000) throw ApiException.badRequest("请稍后再开始新的登录请求");
            }
            limitInput(session,method);
            Map<String,Object> result = callInput(method,id,body);
            String resultId = String.valueOf(result.get("login_id"));
            if (!resultId.matches("[a-f0-9]{32}") || id != null && !id.equals(resultId)) throw ApiException.badRequest("登录请求结果无效");
            Ticket t = id == null ? new Ticket(resultId,family(method)) : old;
            updateTicket(session,t,result,method);
            session.setAttribute(key(),t);
            if (id == null) {
                session.setAttribute(key()+":started",System.currentTimeMillis());
                if (old != null) { try { call("cancel",List.of("--login-id",old.id)); } catch (ApiException ignored) {} }
            }
            return result;
        }
    }

    private Ticket ticket(HttpSession session, String id) {
        if (!(session.getAttribute(key()) instanceof Ticket t) || !t.id.equals(id)
                || !id.matches("[a-f0-9]{32}") || t.deadline < System.currentTimeMillis()) {
            throw ApiException.badRequest("登录请求已过期或不属于当前会话，请重新开始登录");
        }
        return t;
    }

    public Map<String, Object> poll(HttpSession session, String id) {
        synchronized (session) {
            Ticket t = ticket(session, id);
            if ("ready".equals(t.result.get("state")) || System.currentTimeMillis() - t.checkedAt < 3000) return t.result;
            updateTicket(session,t,call("poll", List.of("--login-id", id)),t.method);
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
