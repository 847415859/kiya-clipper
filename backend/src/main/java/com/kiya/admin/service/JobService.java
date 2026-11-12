package com.kiya.admin.service;

import java.io.IOException;
import java.io.RandomAccessFile;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.context.event.EventListener;
import org.springframework.stereotype.Service;

import com.baomidou.mybatisplus.core.conditions.query.QueryWrapper;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.kiya.admin.common.ApiException;
import com.kiya.admin.config.KiyaProperties;
import com.kiya.admin.entity.Job;
import com.kiya.admin.mapper.JobMapper;

import jakarta.annotation.PreDestroy;

/**
 * 后台任务：调用 worker 脚本。2 核服务器上同一时间只跑一个（单线程队列），日志写到 output/_jobs/<id>.log。
 * 只允许固定的几种任务，参数逐个校验后作为独立参数传给 Python（不经过 shell）。
 */
@Service
public class JobService {
    private static final Logger log = LoggerFactory.getLogger(JobService.class);
    private static final DateTimeFormatter TS = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss");

    private static final List<String> AUTO_EDIT_STYLES = List.of("restrained", "standard", "lively");

    /** 任务类型及中文名。 */
    public static final Map<String, String> TYPES = Map.ofEntries(
            Map.entry("TRANSCRIBE", "语音转写"),
            Map.entry("SCREEN", "粗筛"),
            Map.entry("RERANK", "大模型精排"),
            Map.entry("RESCORE", "按新权重重排"),
            Map.entry("LEARN", "更新精排记忆"),
            Map.entry("MEME_REFRESH", "刷新梗库"),
            Map.entry("CUT", "剪切"),
            Map.entry("PIPELINE", "全流程（转写 → 粗筛 → 剪切）"),
            Map.entry("HOTWORDS_SYNC", "同步热词表"),
            Map.entry("EVALUATE", "评测"),
            Map.entry("EVAL_SWEEP", "分数线扫描"),
            Map.entry("EVAL_LOG", "评测并记录"),
            Map.entry("EVAL_RERANK", "精排对比"),
            Map.entry("AUTO_EDIT", "AI 剪辑初稿"),
            Map.entry("EDIT_PREVIEW", "剪编辑预览"),
            Map.entry("PUBLISH_BILI", "发布到B站"),
            Map.entry("INDEX_TRANSCRIPT", "转写入全文索引"),
            Map.entry("RENDER", "渲染成片"));

    private final JobMapper mapper;
    private final WorkerPaths paths;
    private final ProcessRunner runner;
    private final StyleService styles;
    private final SessionService sessions;
    private final KiyaProperties props;
    private final ObjectMapper om;
    private final ExecutorService executor = Executors.newSingleThreadExecutor(r -> {
        Thread t = new Thread(r, "kiya-job");
        t.setDaemon(true);
        return t;
    });

    private volatile Long currentId;
    private volatile Process currentProcess;

    public JobService(JobMapper mapper, WorkerPaths paths, ProcessRunner runner, StyleService styles,
                      SessionService sessions, KiyaProperties props, ObjectMapper om) {
        this.mapper = mapper;
        this.paths = paths;
        this.runner = runner;
        this.styles = styles;
        this.sessions = sessions;
        this.props = props;
        this.om = om;
    }

    /** 启动时：上次没跑完的标记为失败，排队中的重新排队。 */
    @EventListener(ApplicationReadyEvent.class)
    public void recover() {
        for (Job j : mapper.selectList(new QueryWrapper<Job>().eq("status", "RUNNING"))) {
            j.setStatus("FAILED");
            j.setSummary("服务重启，任务被中断");
            j.setFinishedAt(LocalDateTime.now());
            mapper.updateById(j);
        }
        for (Job j : mapper.selectList(new QueryWrapper<Job>().eq("status", "PENDING").orderByAsc("id"))) {
            long id = j.getId();
            executor.submit(() -> run(id));
        }
    }

    @PreDestroy
    public void shutdown() {
        Process p = currentProcess;
        if (p != null) {
            p.destroy();
        }
        executor.shutdownNow();
    }

    // ------------------------------------------------------------------ 提交 / 查询 / 取消
    public Job submit(String type, String session, Map<String, Object> params, String user) {
        if (!TYPES.containsKey(type)) {
            throw ApiException.badRequest("不支持的任务类型");
        }
        Map<String, Object> p = params == null ? new LinkedHashMap<>() : new LinkedHashMap<>(params);
        if (needsSession(type)) {
            paths.checkSession(session);
        } else {
            session = null;
        }
        steps(type, session, p);            // 提前校验参数，不合法直接报错，不进队列
        Job j = new Job();
        j.setType(type);
        j.setSessionName(session);
        j.setParams(json(p));
        j.setStatus("PENDING");
        j.setCreatedBy(user);
        j.setCreatedAt(LocalDateTime.now());
        mapper.insert(j);
        long id = j.getId();
        executor.submit(() -> run(id));
        return j;
    }

    public List<Job> list(String session, int limit) {
        QueryWrapper<Job> q = new QueryWrapper<Job>().orderByDesc("id").last("limit " + Math.max(1, Math.min(limit, 200)));
        if (session != null && !session.isBlank()) {
            q.eq("session_name", paths.checkSession(session));
        }
        return mapper.selectList(q);
    }

    public Job get(long id) {
        Job j = mapper.selectById(id);
        if (j == null) {
            throw ApiException.notFound("没有这个任务");
        }
        return j;
    }

    public synchronized void cancel(long id) {
        Job j = get(id);
        if ("PENDING".equals(j.getStatus())) {
            j.setStatus("CANCELED");
            j.setFinishedAt(LocalDateTime.now());
            j.setSummary("已取消");
            mapper.updateById(j);
        } else if ("RUNNING".equals(j.getStatus()) && Long.valueOf(id).equals(currentId)) {
            j.setStatus("CANCELED");
            mapper.updateById(j);
            Process p = currentProcess;
            if (p != null) {
                p.descendants().forEach(ProcessHandle::destroy);
                p.destroy();
            }
        } else {
            throw ApiException.badRequest("任务已结束");
        }
    }

    /** 读日志：从 offset 字节开始，只返回完整的行（避免把一个汉字截成两半）。 */
    public Map<String, Object> log(long id, long offset) {
        Job j = get(id);
        Map<String, Object> out = new LinkedHashMap<>();
        boolean done = !"PENDING".equals(j.getStatus()) && !"RUNNING".equals(j.getStatus());
        out.put("status", j.getStatus());
        out.put("done", done);
        if (j.getLogFile() == null || !Files.exists(Path.of(j.getLogFile()))) {
            out.put("text", "");
            out.put("offset", offset);
            return out;
        }
        try (RandomAccessFile f = new RandomAccessFile(j.getLogFile(), "r")) {
            long len = f.length();
            long start = Math.max(0, Math.min(offset, len));
            int n = (int) Math.min(256 * 1024, len - start);
            byte[] buf = new byte[n];
            f.seek(start);
            f.readFully(buf);
            int end = n;
            if (!done || start + n < len) {
                end = 0;
                for (int i = n - 1; i >= 0; i--) {
                    if (buf[i] == '\n') {
                        end = i + 1;
                        break;
                    }
                }
            }
            out.put("text", new String(buf, 0, end, StandardCharsets.UTF_8));
            out.put("offset", start + end);
        } catch (IOException e) {
            out.put("text", "");
            out.put("offset", offset);
        }
        return out;
    }

    // ------------------------------------------------------------------ 执行
    private static boolean needsSession(String type) {
        return switch (type) {
            case "TRANSCRIBE", "SCREEN", "RERANK", "RESCORE", "CUT", "PIPELINE", "RENDER", "AUTO_EDIT", "EDIT_PREVIEW", "PUBLISH_BILI", "INDEX_TRANSCRIPT" -> true;
            default -> false;
        };
    }

    /** 每种任务对应的 worker 命令（可以有多步，前一步失败就停）。 */
    private List<List<String>> steps(String type, String session, Map<String, Object> p) {
        String rec = paths.recDir().toString();
        List<List<String>> steps = new ArrayList<>();
        switch (type) {
            case "TRANSCRIBE" -> {
                steps.add(transcribe(rec, session, p));
                steps.add(indexTranscript(session, true));
            }
            case "INDEX_TRANSCRIPT" -> steps.add(indexTranscript(session, false));
            case "SCREEN" -> steps.add(screen(rec, session, p));
            case "CUT" -> steps.add(cut(session, p));
            case "PIPELINE" -> {
                steps.add(transcribe(rec, session, p));
                steps.add(indexTranscript(session, true));
                steps.add(screen(rec, session, p));
                steps.add(cut(session, p));
            }
            case "RENDER" -> steps.add(render(session, p));
            case "RERANK" -> steps.add(rerank(session, p));
            case "RESCORE" -> steps.add(runner.command("rerank.py",
                    List.of(paths.sessionDir(session).resolve("candidates.json").toString(), "--rescore")));
            case "LEARN" -> steps.add(runner.command("learn.py",
                    Boolean.TRUE.equals(p.get("noFetch")) ? List.of("--no-fetch") : List.of()));
            case "MEME_REFRESH" -> steps.add(runner.command("memes.py",
                    Boolean.TRUE.equals(p.get("noFetch")) ? List.of("refresh", "--no-fetch") : List.of("refresh")));
            case "AUTO_EDIT" -> steps.add(autoEdit(session, p));
            case "EDIT_PREVIEW" -> steps.add(editPreview(session, p));
            case "PUBLISH_BILI" -> steps.add(publishBili(session, p));
            case "EVAL_RERANK" -> steps.add(runner.command("evaluate.py", List.of("--rerank")));
            case "HOTWORDS_SYNC" -> steps.add(runner.command("hotwords.py", List.of("sync")));
            case "EVALUATE" -> steps.add(runner.command("evaluate.py", List.of()));
            case "EVAL_SWEEP" -> steps.add(runner.command("evaluate.py", List.of("--sweep")));
            case "EVAL_LOG" -> {
                String note = String.valueOf(p.getOrDefault("note", "")).strip();
                if (note.isEmpty() || note.length() > 100) {
                    throw ApiException.badRequest("请填写这次改了什么（100 字以内）");
                }
                steps.add(runner.command("evaluate.py", List.of("--log", note)));
            }
            default -> throw ApiException.badRequest("不支持的任务类型");
        }
        return steps;
    }

    /** 转写入全文索引（跨场次呼应用）。soft：MySQL / pymysql 用不了时只提示、不让任务失败。 */
    private List<String> indexTranscript(String session, boolean soft) {
        List<String> a = new ArrayList<>(List.of("index", paths.sessionDir(session).toString()));
        if (soft) {
            a.add("--soft");
        }
        return runner.command("transcript_index.py", a);
    }

    private List<String> transcribe(String rec, String session, Map<String, Object> p) {
        List<String> a = new ArrayList<>(List.of(rec, "--session", session));
        if (Boolean.TRUE.equals(p.get("refresh"))) {
            a.add("--refresh");
        }
        if (p.get("model") != null) {
            String m = String.valueOf(p.get("model"));
            if (!m.equals("fun-asr") && !m.equals("paraformer-v2")) {
                throw ApiException.badRequest("识别模型只能是 fun-asr 或 paraformer-v2");
            }
            a.addAll(List.of("--model", m));
        }
        return runner.command("transcribe.py", a);
    }

    private List<String> screen(String rec, String session, Map<String, Object> p) {
        List<String> a = new ArrayList<>(List.of(rec, "--session", session));
        if (Boolean.TRUE.equals(p.get("noVideo"))) {
            a.add("--no-video");
        }
        if (Boolean.TRUE.equals(p.get("refresh"))) {
            a.add("--refresh");
        }
        return runner.command("screen.py", a);
    }

    private List<String> cut(String session, Map<String, Object> p) {
        String cand = paths.sessionDir(session).resolve("candidates.json").toString();
        List<String> a = new ArrayList<>(List.of(cand));
        int height = intParam(p, "height", 480);
        if (height != 360 && height != 480 && height != 720 && height != 1080) {
            throw ApiException.badRequest("分辨率只能是 360 / 480 / 720 / 1080");
        }
        a.addAll(List.of("--height", String.valueOf(height)));
        Object ranks = p.get("ranks");
        if (ranks instanceof List<?> l && !l.isEmpty()) {
            a.add("--ranks");
            for (Object r : l) {
                int v = (r instanceof Number n) ? n.intValue() : Integer.parseInt(String.valueOf(r));
                if (v < 1 || v > 999) {
                    throw ApiException.badRequest("序号不合法");
                }
                a.add(String.valueOf(v));
            }
        }
        if (Boolean.TRUE.equals(p.get("noSubs"))) {
            a.add("--no-subs");
        } else if (p.get("styleId") != null) {
            long styleId = intParam(p, "styleId", 0);
            styles.get(styleId);                                  // 校验存在；真正的文件在执行时导出
            a.addAll(List.of("--sub-style-json", "@style:" + styleId));
        }
        return runner.command("cut.py", a);
    }

    private List<String> rerank(String session, Map<String, Object> p) {
        String cand = paths.sessionDir(session).resolve("candidates.json").toString();
        List<String> a = new ArrayList<>(List.of(cand));
        Object ranks = p.get("ranks");
        if (ranks instanceof List<?> l && !l.isEmpty()) {
            a.add("--ranks");
            for (Object r : l) {
                int v = (r instanceof Number n) ? n.intValue() : Integer.parseInt(String.valueOf(r));
                if (v < 1 || v > 999) {
                    throw ApiException.badRequest("序号不合法");
                }
                a.add(String.valueOf(v));
            }
        }
        return runner.command("rerank.py", a);              // 重跑时没变的候选走缓存，不重复花钱
    }

    private List<String> autoEdit(String session, Map<String, Object> p) {
        int rank = intParam(p, "rank", 0);
        if (rank < 1 || rank > 999) {
            throw ApiException.badRequest("请指定序号");
        }
        String cand = paths.sessionDir(session).resolve("candidates.json").toString();
        List<String> a = new ArrayList<>(List.of(cand, "--rank", String.valueOf(rank)));
        if (Boolean.TRUE.equals(p.get("force"))) {
            a.add("--force");
        }
        if (p.get("styles") instanceof List<?> picked && !picked.isEmpty()) {
            // 人在编辑器里勾选的版本（克制 / 标准 / 热闹，默认三个都选），不受前 N 个的限制
            List<String> keys = new ArrayList<>();
            for (Object o : picked) {
                String k = String.valueOf(o);
                if (!AUTO_EDIT_STYLES.contains(k)) {
                    throw ApiException.badRequest("不认识的版本：" + k);
                }
                if (!keys.contains(k)) {
                    keys.add(k);
                }
            }
            a.addAll(List.of("--styles", String.join(",", keys)));
        } else if (Boolean.TRUE.equals(p.get("variants"))) {
            a.addAll(List.of("--variants", "auto"));          // 批量：AI 评分靠前的前 N 个出三版，其余只出标准版
        }
        return runner.command("autoedit.py", a);
    }

    private List<String> editPreview(String session, Map<String, Object> p) {
        int rank = intParam(p, "rank", 0);
        if (rank < 1 || rank > 999) {
            throw ApiException.badRequest("请指定序号");
        }
        String cand = paths.sessionDir(session).resolve("candidates.json").toString();
        List<String> a = new ArrayList<>(List.of(cand, "--rank", String.valueOf(rank)));
        if (p.get("start") != null && p.get("end") != null) {
            double s0 = numParam(p, "start");
            double e0 = numParam(p, "end");
            if (s0 < 0 || e0 - s0 < 5 || e0 - s0 > 500) {
                throw ApiException.badRequest("素材范围要在 5 秒到 500 秒之间");
            }
            a.addAll(List.of("--start", String.format(java.util.Locale.ROOT, "%.1f", s0),
                    "--end", String.format(java.util.Locale.ROOT, "%.1f", e0)));
        }
        return runner.command("edit_preview.py", a);
    }

    private static double numParam(Map<String, Object> p, String k) {
        Object v = p.get(k);
        if (v instanceof Number n) {
            return n.doubleValue();
        }
        try {
            return Double.parseDouble(String.valueOf(v));
        } catch (NumberFormatException e) {
            throw ApiException.badRequest(k + " 不合法");
        }
    }

    private List<String> render(String session, Map<String, Object> p) {
        int rank = intParam(p, "rank", 0);
        if (rank < 1 || rank > 999) {
            throw ApiException.badRequest("请指定要渲染的序号");
        }
        int height = intParam(p, "height", 720);
        if (height != 360 && height != 480 && height != 720 && height != 1080) {
            throw ApiException.badRequest("分辨率只能是 360 / 480 / 720 / 1080");
        }
        String cand = paths.sessionDir(session).resolve("candidates.json").toString();
        return runner.command("render.py", List.of(cand, "--rank", String.valueOf(rank), "--height", String.valueOf(height)));
    }

    /** 一键发布到 B站：mode = hold（先放着，默认）/ now / timed（要 at）/ private；dryRun 只预演不上传。 */
    private List<String> publishBili(String session, Map<String, Object> p) {
        int rank = intParam(p, "rank", 0);
        if (rank < 1 || rank > 999) {
            throw ApiException.badRequest("请指定要发布的序号");
        }
        String mode = String.valueOf(p.getOrDefault("mode", "hold"));
        if (!List.of("hold", "now", "timed", "private").contains(mode)) {
            throw ApiException.badRequest("发布方式只能是 hold / now / timed / private");
        }
        List<String> args = new ArrayList<>(List.of(paths.sessionDir(session).resolve("candidates.json").toString(),
                "--rank", String.valueOf(rank), "--mode", mode));
        if ("timed".equals(mode)) {
            String at = String.valueOf(p.getOrDefault("at", "")).strip();
            if (!at.matches("\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}")) {
                throw ApiException.badRequest("请选择定时发布的时间");
            }
            args.addAll(List.of("--at", at));
        }
        if (Boolean.TRUE.equals(p.get("dryRun"))) {
            args.add("--dry-run");
        }
        if (Boolean.TRUE.equals(p.get("allowTaboo"))) {
            args.add("--allow-taboo");
        }
        if (Boolean.TRUE.equals(p.get("again"))) {
            args.add("--again");
        }
        return runner.command("publish_bili.py", args);
    }

    private static int intParam(Map<String, Object> p, String k, int def) {
        Object v = p.get(k);
        if (v == null) {
            return def;
        }
        if (v instanceof Number n) {
            return n.intValue();
        }
        try {
            return Integer.parseInt(String.valueOf(v));
        } catch (NumberFormatException e) {
            throw ApiException.badRequest(k + " 不合法");
        }
    }

    private void run(long id) {
        Job j = mapper.selectById(id);
        if (j == null || !"PENDING".equals(j.getStatus())) {
            return;                                               // 已取消
        }
        Path logFile = paths.jobLogs().resolve(id + ".log");
        currentId = id;
        j.setStatus("RUNNING");
        j.setStartedAt(LocalDateTime.now());
        j.setLogFile(logFile.toString());
        mapper.updateById(j);
        int code = 0;
        String summary;
        try {
            Files.createDirectories(logFile.getParent());
            Map<String, Object> p = om.readValue(j.getParams() == null ? "{}" : j.getParams(),
                    new TypeReference<Map<String, Object>>() {});
            List<List<String>> steps = steps(j.getType(), j.getSessionName(), p);
            for (List<String> cmd : steps) {
                cmd = resolveStyleArg(cmd, j.getSessionName());
                append(logFile, "\n[" + LocalDateTime.now().format(TS) + "] $ " + display(cmd) + "\n");
                ProcessBuilder pb = runner.builder(cmd)
                        .redirectOutput(ProcessBuilder.Redirect.appendTo(logFile.toFile()));
                Process proc = pb.start();
                currentProcess = proc;
                boolean finished = proc.waitFor(props.getTaskTimeoutMinutes(), TimeUnit.MINUTES);
                if (!finished) {
                    proc.descendants().forEach(ProcessHandle::destroyForcibly);
                    proc.destroyForcibly();
                    append(logFile, "\n超时（" + props.getTaskTimeoutMinutes() + " 分钟），已终止\n");
                    code = -1;
                    break;
                }
                code = proc.exitValue();
                currentProcess = null;
                if (code != 0 || "CANCELED".equals(mapper.selectById(id).getStatus())) {
                    break;
                }
            }
            summary = lastMeaningfulLine(logFile);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            code = -1;
            summary = "被中断";
        } catch (Exception e) {
            log.warn("任务 {} 执行出错", id, e);
            code = -1;
            summary = "执行出错：" + e.getMessage();
            try {
                append(logFile, "\n" + summary + "\n");
            } catch (IOException ignored) {
                // 日志写不进去也要把状态落库
            }
        } finally {
            currentProcess = null;
            currentId = null;
        }
        Job done = mapper.selectById(id);
        if (!"CANCELED".equals(done.getStatus())) {
            done.setStatus(code == 0 ? "SUCCESS" : "FAILED");
        }
        done.setExitCode(code);
        done.setSummary(summary == null ? null : summary.substring(0, Math.min(480, summary.length())));
        done.setFinishedAt(LocalDateTime.now());
        mapper.updateById(done);
        if (done.getSessionName() != null) {
            try {
                sessions.sync(done.getSessionName());             // 粗筛 / 剪切后刷新候选和片段
            } catch (Exception e) {
                log.warn("同步场次 {} 失败", done.getSessionName(), e);
            }
        }
    }

    /** 把占位的 @style:<id> 换成真实的样式 JSON 文件（执行时才导出，保证用的是最新样式）。 */
    private List<String> resolveStyleArg(List<String> cmd, String session) {
        List<String> out = new ArrayList<>(cmd.size());
        for (String a : cmd) {
            if (a.startsWith("@style:")) {
                long styleId = Long.parseLong(a.substring(7));
                Path dir = session == null ? paths.previewDir() : paths.sessionDir(session);
                out.add(styles.exportForJob(styleId, dir).toString());
            } else {
                out.add(a);
            }
        }
        return out;
    }

    private static void append(Path f, String s) throws IOException {
        Files.writeString(f, s, StandardCharsets.UTF_8, StandardOpenOption.CREATE, StandardOpenOption.APPEND);
    }

    private String display(List<String> cmd) {
        List<String> c = new ArrayList<>(cmd);
        c.set(0, "python");
        return String.join(" ", c);
    }

    private static String lastMeaningfulLine(Path f) {
        try {
            List<String> lines = Files.readAllLines(f, StandardCharsets.UTF_8);
            for (int i = lines.size() - 1; i >= 0; i--) {
                String l = lines.get(i).strip();
                if (!l.isEmpty() && !l.startsWith("[")) {
                    return l;
                }
            }
        } catch (IOException ignored) {
            // 没有日志就没有摘要
        }
        return null;
    }

    private String json(Object o) {
        try {
            return om.writeValueAsString(o);
        } catch (IOException e) {
            return "{}";
        }
    }

    public static String typeName(String type) {
        return TYPES.getOrDefault(type, type);
    }
}
