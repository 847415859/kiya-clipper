package com.kiya.admin.service;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.time.Duration;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.regex.Pattern;
import java.util.stream.Stream;

import org.springframework.stereotype.Service;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.kiya.admin.common.ApiException;
import com.kiya.admin.entity.SubtitleStyle;

/**
 * 成片剪辑脚本：output/<场次>/edits/<序号>.json。校验规则只有一份，在 worker 的 clipper/edit.py，
 * 保存时调用 scripts/edit_check.py 校验并规范化。渲染结果在 output/<场次>/final/。
 */
@Service
public class EditService {
    private static final Pattern FINAL = Pattern.compile("^[0-9]{2,3}_[0-9\\-]{1,16}_[0-9]{3,4}p\\.mp4$");

    private final WorkerPaths paths;
    private final ProcessRunner runner;
    private final StyleService styles;
    private final ObjectMapper om;
    private final SessionService sessions;

    public EditService(WorkerPaths paths, ProcessRunner runner, StyleService styles, ObjectMapper om,
                       SessionService sessions) {
        this.paths = paths;
        this.runner = runner;
        this.styles = styles;
        this.om = om;
        this.sessions = sessions;
    }

    /** 这个候选的精排结果（没有返回 null）。 */
    private Map<String, Object> rerank(String session, int rank) {
        Path f = paths.sessionDir(session).resolve("rerank.json");
        if (!Files.exists(f)) {
            return null;
        }
        try {
            Map<String, Object> m = sessions.rerankByRank(om.readTree(f.toFile())).get(rank);
            double cs = candidate(session, rank).path("clip_start").asDouble();
            return m != null && Math.abs((Double) m.get("clipStart") - cs) <= 1 ? m : null;   // 粗筛重跑后的旧结果不用
        } catch (IOException e) {
            return null;
        }
    }

    private JsonNode candidate(String session, int rank) {
        Path f = paths.sessionDir(session).resolve("candidates.json");
        try {
            for (JsonNode c : om.readTree(f.toFile()).path("candidates")) {
                if (c.path("rank").asInt() == rank) {
                    return c;
                }
            }
        } catch (IOException e) {
            throw ApiException.notFound("这场还没有粗筛结果");
        }
        throw ApiException.notFound("没有这个候选");
    }

    private Path editFile(String session, int rank) {
        return paths.sessionDir(session).resolve("edits").resolve(String.format("%02d.json", rank));
    }

    /** 编辑器初始化需要的一切：剪辑脚本（没有就给默认）、片段长度、审核用的片段文件、已渲染的成片。 */
    public Map<String, Object> get(String session, int rank) {
        JsonNode c = candidate(session, rank);
        double clipLen = c.path("clip_end").asDouble() - c.path("clip_start").asDouble();
        Map<String, Object> edit;
        Path f = editFile(session, rank);
        try {
            edit = Files.exists(f) ? om.readValue(f.toFile(), new TypeReference<Map<String, Object>>() {}) : null;
        } catch (IOException e) {
            edit = null;
        }
        Map<String, Object> llm = rerank(session, rank);
        if (edit == null) {
            double ts = 0.0;
            double te = clipLen;
            if (llm != null && llm.get("refined") instanceof Map<?, ?> r) {      // 默认用精排修正后的起止（限制在片段内）
                double cs = c.path("clip_start").asDouble();
                double rs = ((Number) r.get("start")).doubleValue() - cs;
                double re = ((Number) r.get("end")).doubleValue() - cs;
                rs = Math.max(0, Math.min(rs, clipLen));
                re = Math.max(0, Math.min(re, clipLen));
                if (re - rs >= 5) {
                    ts = Math.round(rs * 10) / 10.0;
                    te = Math.round(re * 10) / 10.0;
                }
            }
            edit = new LinkedHashMap<>();
            edit.put("version", 1);
            edit.put("trim", Map.of("start", ts, "end", te));
            edit.put("subtitles", Map.of("enabled", true));
            edit.put("items", List.of());
        }
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("edit", edit);
        out.put("clipLen", clipLen);
        out.put("clipStart", c.path("clip_start").asDouble());
        out.put("t", c.path("t").asDouble());
        out.put("clip", findClip(session, rank));
        out.put("finals", finals(session, rank));
        out.put("llm", llm);
        out.put("exists", Files.exists(f));
        return out;
    }

    /** 保存：选了字幕样式就把样式整份写进脚本（以后改样式不影响已编排好的片段）；校验交给 worker。 */
    public Map<String, Object> save(String session, int rank, Map<String, Object> edit, Long styleId) {
        JsonNode c = candidate(session, rank);
        double clipLen = c.path("clip_end").asDouble() - c.path("clip_start").asDouble();
        Map<String, Object> e = new LinkedHashMap<>(edit == null ? Map.of() : edit);
        Object subsRaw = e.get("subtitles");
        Map<String, Object> subs = new LinkedHashMap<>();
        if (subsRaw instanceof Map<?, ?> m) {
            m.forEach((k, v) -> subs.put(String.valueOf(k), v));
        }
        subs.remove("style");
        subs.remove("style_name");
        if (styleId != null) {
            SubtitleStyle s = styles.get(styleId);
            try {
                subs.put("style", om.readValue(s.getConfigJson(), new TypeReference<Map<String, Object>>() {}));
            } catch (IOException ex) {
                throw ApiException.badRequest("字幕样式数据损坏");
            }
            subs.put("style_name", s.getName());
            subs.put("style_id", styleId);
        } else {
            subs.remove("style_id");
        }
        e.put("subtitles", subs);
        Path dir = paths.sessionDir(session).resolve("edits");
        Path tmp = dir.resolve(".check-" + UUID.randomUUID() + ".json");
        try {
            Files.createDirectories(dir);
            Files.writeString(tmp, om.writeValueAsString(e), StandardCharsets.UTF_8);
            ProcessRunner.Result r = runner.run("edit_check.py",
                    List.of(tmp.toString(), "--clip-len", String.valueOf(clipLen)), Duration.ofSeconds(30));
            if (r.exitCode() != 0) {
                throw ApiException.badRequest(SessionService.lastLine(r.output()));
            }
            Map<String, Object> checked = om.readValue(SessionService.lastLine(r.output()),
                    new TypeReference<Map<String, Object>>() {});
            if (!(checked.get("edit") instanceof Map<?, ?>)) {
                throw ApiException.badRequest("校验结果格式不对（worker 版本和后台不一致？）");
            }
            @SuppressWarnings("unchecked")
            Map<String, Object> norm = new LinkedHashMap<>((Map<String, Object>) checked.get("edit"));
            Object warnings = checked.getOrDefault("warnings", List.of());
            Object sid = subs.get("style_id");
            if (sid != null && norm.get("subtitles") instanceof Map<?, ?> ns) {
                @SuppressWarnings("unchecked")
                Map<String, Object> nsm = (Map<String, Object>) ns;
                nsm.put("style_id", sid);                       // 方便前端回显选中的样式
            }
            norm.put("session", session);
            norm.put("rank", rank);
            norm.put("updated_at", LocalDateTime.now().toString());
            Path f = editFile(session, rank);
            Path part = f.resolveSibling(f.getFileName() + ".tmp");
            Files.writeString(part, om.writerWithDefaultPrettyPrinter().writeValueAsString(norm), StandardCharsets.UTF_8);
            Files.move(part, f, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE);
            Map<String, Object> out = new LinkedHashMap<>();
            out.put("edit", norm);
            out.put("warnings", warnings);
            return out;
        } catch (IOException ex) {
            throw ApiException.badRequest("保存失败：" + ex.getMessage());
        } finally {
            try {
                Files.deleteIfExists(tmp);
            } catch (IOException ignored) {
                // 临时文件删不掉不影响
            }
        }
    }

    public List<Map<String, Object>> finals(String session, Integer rank) {
        Path dir = paths.sessionDir(session).resolve("final");
        List<Map<String, Object>> out = new ArrayList<>();
        if (!Files.isDirectory(dir)) {
            return out;
        }
        String prefix = rank == null ? "" : String.format("%02d_", rank);
        try (Stream<Path> s = Files.list(dir)) {
            s.filter(p -> FINAL.matcher(p.getFileName().toString()).matches())
             .filter(p -> p.getFileName().toString().startsWith(prefix))
             .sorted()
             .forEach(p -> {
                 Map<String, Object> m = new LinkedHashMap<>();
                 m.put("file", p.getFileName().toString());
                 try {
                     m.put("size", Files.size(p));
                     m.put("modified", Files.getLastModifiedTime(p).toMillis());
                 } catch (IOException ignored) {
                     // 文件刚被替换，下次刷新就有
                 }
                 out.add(m);
             });
        } catch (IOException e) {
            return out;
        }
        return out;
    }

    public Path finalFile(String session, String file) {
        if (file == null || !FINAL.matcher(file).matches()) {
            throw ApiException.badRequest("文件名不合法");
        }
        return WorkerPaths.inside(paths.output(), paths.sessionDir(session).resolve("final").resolve(file));
    }

    private String findClip(String session, int rank) {
        Path clips = paths.sessionDir(session).resolve("clips");
        if (!Files.isDirectory(clips)) {
            return null;
        }
        String prefix = String.format("%02d_", rank);
        try (Stream<Path> s = Files.list(clips)) {
            return s.map(p -> p.getFileName().toString())
                    .filter(n -> n.startsWith(prefix) && n.endsWith(".mp4"))
                    .sorted().findFirst().orElse(null);
        } catch (IOException e) {
            return null;
        }
    }
}
