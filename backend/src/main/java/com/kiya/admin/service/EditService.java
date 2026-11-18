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
    private static final Pattern PREVIEW = Pattern.compile("^([0-9]{2,3})_([0-9]+\\.[0-9])_([0-9]+\\.[0-9])(_clean)?\\.mp4$");
    private static final Pattern RENDER_PREVIEW = Pattern.compile("^[0-9]{2,3}_[0-9]+_[0-9]+_(360|480|720|1080)p\\.mp4$");
    static final double MAX_WINDOW = 500;

    private final WorkerPaths paths;
    private final ProcessRunner runner;
    private final StyleService styles;
    private final ObjectMapper om;
    private final SessionService sessions;
    private final EditRecoveryStore recovery;

    public EditService(WorkerPaths paths, ProcessRunner runner, StyleService styles, ObjectMapper om,
                       SessionService sessions) {
        this.paths = paths;
        this.runner = runner;
        this.styles = styles;
        this.om = om;
        this.sessions = sessions;
        this.recovery = new EditRecoveryStore(om);
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

    /** AI 初稿的原稿（autoedit.py 另存的 <序号>.ai.json 里的 edit）；没有就是 null。 */
    private Map<String, Object> aiDraft(String session, int rank) {
        Path f = paths.sessionDir(session).resolve("edits").resolve(String.format("%02d.ai.json", rank));
        if (!Files.exists(f)) {
            return null;
        }
        try {
            Map<String, Object> m = om.readValue(f.toFile(), new TypeReference<Map<String, Object>>() {});
            return m.get("edit") instanceof Map<?, ?> em ? new LinkedHashMap<>(castMap(em)) : null;
        } catch (IOException e) {
            return null;
        }
    }

    private Path variantsFile(String session, int rank) {
        return paths.sessionDir(session).resolve("edits").resolve(String.format("%02d.variants.json", rank));
    }

    private Map<String, Object> readVariants(String session, int rank) {
        Path f = variantsFile(session, rank);
        if (!Files.exists(f)) {
            return null;
        }
        try {
            return om.readValue(f.toFile(), new TypeReference<LinkedHashMap<String, Object>>() {});
        } catch (IOException e) {
            return null;
        }
    }

    /** 多版本概览（给编辑器顶部切换用）：[{key, label, items, memes, note}]，没有多版本返回空列表。 */
    private List<Map<String, Object>> variantSummary(Map<String, Object> v) {
        List<Map<String, Object>> out = new ArrayList<>();
        if (v != null && v.get("variants") instanceof List<?> l) {
            for (Object o : l) {
                if (o instanceof Map<?, ?> m && m.get("edit") instanceof Map<?, ?> em) {
                    int items = 0;
                    int memes = 0;
                    if (em.get("items") instanceof List<?> its) {
                        items = its.size();
                        for (Object it : its) {
                            if (it instanceof Map<?, ?> im && im.get("meme") != null) {
                                memes++;
                            }
                        }
                    }
                    Map<String, Object> row = new LinkedHashMap<>();
                    row.put("key", m.get("key"));
                    row.put("label", m.get("label"));
                    row.put("items", items);
                    row.put("memes", memes);
                    row.put("note", em.get("note") == null ? "" : em.get("note"));
                    out.add(row);
                }
            }
        }
        return out;
    }

    /** 选用某一版：它成为当前剪辑脚本（编辑器改的就是它），AI 原稿也换成它（剪辑口味按它对比人工修改），并记下选了哪一版。 */
    public synchronized Map<String, Object> chooseVariant(String session, int rank, String key) {
        Map<String, Object> v = readVariants(session, rank);
        if (v == null || !(v.get("variants") instanceof List<?> list)) {
            throw ApiException.notFound("这个候选没有多版本初稿");
        }
        Map<String, Object> picked = null;
        for (Object o : list) {
            if (o instanceof Map<?, ?> m && String.valueOf(m.get("key")).equals(key) && m.get("edit") instanceof Map<?, ?>) {
                picked = castMap(m);
            }
        }
        if (picked == null) {
            throw ApiException.badRequest("没有这个版本");
        }
        Map<String, Object> edit = new LinkedHashMap<>(castMap((Map<?, ?>) picked.get("edit")));
        edit.put("updated_at", LocalDateTime.now().toString());
        Path edits = paths.sessionDir(session).resolve("edits");
        try {
            Files.createDirectories(edits);
            write(editFile(session, rank), edit);
            Path aiF = edits.resolve(String.format("%02d.ai.json", rank));
            Map<String, Object> ai = Files.exists(aiF)
                    ? om.readValue(aiF.toFile(), new TypeReference<LinkedHashMap<String, Object>>() {}) : new LinkedHashMap<>();
            ai.put("edit", edit);
            ai.put("variant", key);
            ai.put("meme_notes", picked.getOrDefault("meme_notes", List.of()));
            write(aiF, ai);
            v.put("chosen", key);
            v.put("chosen_by", "human");
            v.put("chosen_at", LocalDateTime.now().withNano(0).toString());
            write(variantsFile(session, rank), v);
        } catch (IOException ex) {
            throw ApiException.badRequest("切换失败：" + ex.getMessage());
        }
        return get(session, rank);
    }

    private void write(Path f, Map<String, Object> m) throws IOException {
        Path part = f.resolveSibling(f.getFileName() + ".tmp");
        Files.writeString(part, om.writerWithDefaultPrettyPrinter().writeValueAsString(m), StandardCharsets.UTF_8);
        Files.move(part, f, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE);
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> castMap(Map<?, ?> m) {
        return (Map<String, Object>) m;
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

    /** 默认素材范围 = 候选片段 ∪ 精排修正后的起止（前后各留 5 秒），与 worker 的 clipper/edit.py suggest_window 一致。 */
    static double[] suggestWindow(double cs, double ce, Map<String, Object> llm) {
        double a = cs;
        double b = ce;
        if (llm != null && llm.get("refined") instanceof Map<?, ?> r) {
            double rs = ((Number) r.get("start")).doubleValue();
            double re = ((Number) r.get("end")).doubleValue();
            a = Math.min(a, rs - 5);
            b = Math.max(b, re + 5);
            if (b - a > MAX_WINDOW) {
                a = rs - 5;
                b = a + MAX_WINDOW;
                if (b < re) {
                    a = rs;
                    b = rs + MAX_WINDOW;
                }
            }
        }
        a = Math.max(0, a);
        return new double[] {round1(a), round1(Math.max(b, a + 5))};
    }

    /** 一位小数：预览文件名在 Java / Python 两边格式化结果一致。 */
    private static double round1(double x) {
        return Math.round(x * 10) / 10.0;
    }

    /** 编辑器初始化需要的一切：剪辑脚本（没有就给默认）、素材范围、预览视频、已渲染的成片。
     *  所有时间相对“素材范围”开头；素材范围可以超出候选片段（按精排修正后的起止，成片从录像重新剪）。 */
    public Map<String, Object> get(String session, int rank) {
        JsonNode c = candidate(session, rank);
        double cs = c.path("clip_start").asDouble();
        double ce = c.path("clip_end").asDouble();
        Map<String, Object> edit;
        String revision = "none";
        Path f = editFile(session, rank);
        try {
            byte[] content = Files.exists(f) ? Files.readAllBytes(f) : null;
            if (content != null) revision = recovery.revision(content);
            edit = content == null ? null : om.readValue(content, new TypeReference<Map<String, Object>>() {});
        } catch (IOException e) {
            edit = null;
        }
        Map<String, Object> llm = rerank(session, rank);
        double ws;
        double we;
        if (edit != null && edit.get("window") instanceof Map<?, ?> w) {
            ws = ((Number) w.get("start")).doubleValue();
            we = ((Number) w.get("end")).doubleValue();
        } else if (edit != null) {                                   // 旧脚本：素材范围就是候选片段
            ws = cs;
            we = ce;
        } else {
            double[] sw = suggestWindow(cs, ce, llm);
            ws = sw[0];
            we = sw[1];
        }
        double clipLen = we - ws;
        if (edit == null) {
            double ts = cs - ws;
            double te = ce - ws;
            if (llm != null && llm.get("refined") instanceof Map<?, ?> r) {      // 默认成片范围 = 精排修正后的起止
                double rs = Math.max(0, ((Number) r.get("start")).doubleValue() - ws);
                double re = Math.min(clipLen, ((Number) r.get("end")).doubleValue() - ws);
                if (re - rs >= 5) {
                    ts = rs;
                    te = re;
                }
            }
            edit = new LinkedHashMap<>();
            edit.put("version", 1);
            edit.put("window", Map.of("start", ws, "end", we));
            edit.put("trim", Map.of("start", Math.round(ts * 10) / 10.0, "end", Math.round(te * 10) / 10.0));
            edit.put("subtitles", Map.of("enabled", true));
            edit.put("items", List.of());
        }
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("edit", edit);
        out.put("clipLen", clipLen);
        out.put("clipStart", ws);                                    // 素材范围开头（整场秒数）
        out.put("sourceCandidateStart", cs);
        out.put("sourceCandidateEnd", ce);
        out.put("speech", readSpeech(session, Math.max(0, ws - 15), Math.min(86400, we + 15)));
        out.put("window", Map.of("start", ws, "end", we));
        // “审核片段”的实际范围：有精排时 cut.py 按精排起止剪（clips/windows.json），和候选窗口不同；前端据此判断能不能直接用审核片段当预览
        double[] cw = clipWindow(session, rank, cs, ce);
        out.put("candidate", Map.of("start", cw[0], "end", cw[1]));
        out.put("t", c.path("t").asDouble());
        // 预览视频：素材范围就是候选片段时前端用审核片段（clip），否则用 edit_preview.py 按素材范围剪好的（preview）
        out.put("clip", findClip(session, rank, c.path("t").asDouble()));
        out.put("preview", findPreview(session, rank, ws, we));
        out.put("revision", revision);
        out.put("renderPreviews", renderPreviews(session, rank));
        out.put("finals", finals(session, rank));
        out.put("llm", llm);
        if (llm != null) {
            out.put("event", llm.get("event"));
            out.put("boundary_options", llm.get("boundary_options"));
        }
        Path quality = paths.sessionDir(session).resolve("quality").resolve(String.format("%02d.json", rank));
        try {
            out.put("quality", Files.exists(quality) ? om.readValue(quality.toFile(), new TypeReference<Map<String, Object>>() {}) : null);
        } catch (IOException ex) {
            out.put("quality", null);
        }
        out.put("exists", Files.exists(f));
        out.put("aiDraft", aiDraft(session, rank));
        Map<String, Object> variants = readVariants(session, rank);
        out.put("variants", variantSummary(variants));
        out.put("chosenVariant", variants == null ? null : variants.get("chosen"));
        return out;
    }

    /** 编辑器原话采用整场时间，扩展素材范围后仍可准确定位，不受成片裁剪影响。 */
    public Map<String, Object> speech(String session, int rank, double start, double end) {
        candidate(session, rank);
        if (!Double.isFinite(start) || !Double.isFinite(end) || start < 0 || end > 86400 || end <= start || end - start > 530) {
            throw ApiException.badRequest("原话查询范围须为整场 0–86400 秒，单次不超过 530 秒");
        }
        return readSpeech(session, start, end);
    }

    private Map<String, Object> readSpeech(String session, double start, double end) {
        Path file = paths.sessionDir(session).resolve("transcript.json");
        if (!Files.exists(file)) return Map.of("available", false, "sentences", List.of());
        try {
            JsonNode root = om.readTree(file.toFile());
            List<Map<String, Object>> rows = new ArrayList<>();
            boolean truncated = false;
            for (JsonNode s : root.path("sentences")) {
                if (!s.path("t0").isNumber() || !s.path("t1").isNumber()) continue;
                double a = s.path("t0").asDouble(), b = s.path("t1").asDouble();
                if (!Double.isFinite(a) || !Double.isFinite(b) || a < 0 || b <= a || b > 86400 || b <= start || a >= end) continue;
                StringBuilder words = new StringBuilder();
                for (JsonNode w : s.path("words")) if (w.isArray() && w.size() >= 3) words.append(w.get(2).asText(""));
                String text = words.isEmpty() ? s.path("text").asText("") : words.toString();
                if (text.isBlank()) continue;
                if (rows.size() >= 1000) { truncated = true; break; }
                rows.add(Map.of("t0", a, "t1", b, "text", text));
            }
            return Map.of("available", true, "sentences", rows, "truncated", truncated);
        } catch (IOException e) {
            return Map.of("available", false, "sentences", List.of(), "error", "转写文件暂时无法读取");
        }
    }

    /** 保存：选了字幕样式就把样式整份写进脚本（以后改样式不影响已编排好的片段）；校验交给 worker。 */
    public Map<String, Object> save(String session, int rank, Map<String, Object> edit, Long styleId, boolean restoredAi) {
        return save(session, rank, edit, styleId, restoredAi, null);
    }

    public synchronized Map<String, Object> save(String session, int rank, Map<String, Object> edit, Long styleId, boolean restoredAi, String expectedRevision) {
        recovery.check(editFile(session, rank), expectedRevision);
        JsonNode c = candidate(session, rank);
        double clipLen = c.path("clip_end").asDouble() - c.path("clip_start").asDouble();   // 有 window 时 worker 按它校验
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
            // 人工保存的标记：worker 把它和 AI 原稿（<序号>.ai.json）对比，得到剪辑口味。“还原为 AI 原稿”那次保存不算人工修改
            if (!restoredAi) {
                norm.put("human_saved_at", LocalDateTime.now().withNano(0).toString());
            }
            Path f = editFile(session, rank);
            recovery.check(f, expectedRevision);
            recovery.archive(f, "保存前版本");
            Path part = f.resolveSibling(f.getFileName() + ".tmp");
            byte[] content = om.writerWithDefaultPrettyPrinter().writeValueAsBytes(norm);
            Files.write(part, content);
            Files.move(part, f, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE);
            recovery.archive(f, "人工保存");
            Map<String, Object> out = new LinkedHashMap<>();
            out.put("edit", norm);
            out.put("warnings", warnings);
            out.put("revision", recovery.revision(content));
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

    private String findPreview(String session, int rank, double ws, double we) {
        String name = String.format(java.util.Locale.ROOT, "%02d_%.1f_%.1f.mp4", rank, round1(ws), round1(we));
        String clean = name.replace(".mp4", "_clean.mp4");
        if (Files.exists(paths.sessionDir(session).resolve("edit_preview").resolve(clean))) return clean;
        Path f = paths.sessionDir(session).resolve("edit_preview").resolve(name);
        return Files.exists(f) ? name : null;
    }

    public Path previewFile(String session, String file) {
        if (file == null || !PREVIEW.matcher(file).matches()) {
            throw ApiException.badRequest("文件名不合法");
        }
        return WorkerPaths.inside(paths.output(), paths.sessionDir(session).resolve("edit_preview").resolve(file));
    }

    public Map<String, Object> recovery(String session, int rank) {
        candidate(session, rank);
        Path file = editFile(session, rank);
        return Map.of("revision", recovery.revision(file), "drafts", recovery.drafts(file), "versions", recovery.versions(file));
    }

    public Map<String, Object> draft(String session, int rank, String client, Map<String, Object> value) {
        candidate(session, rank);
        return recovery.draft(editFile(session, rank), client, value);
    }

    public Map<String, Object> previewPlan(String session, int rank, Map<String, Object> raw, Long styleId) {
        candidate(session, rank);
        Map<String, Object> e = new LinkedHashMap<>(raw);
        Map<String, Object> subs = new LinkedHashMap<>(castMap(e.get("subtitles") instanceof Map<?, ?> m ? m : Map.of()));
        subs.remove("style");
        if (styleId != null) {
            try { subs.put("style", om.readValue(styles.get(styleId).getConfigJson(), Map.class)); }
            catch (IOException ex) { throw ApiException.badRequest("字幕样式数据损坏"); }
        }
        e.put("subtitles", subs);
        Path tmp = paths.sessionDir(session).resolve(".plan-" + UUID.randomUUID() + ".json");
        try {
            Files.writeString(tmp, om.writeValueAsString(e));
            var result = runner.run("edit_plan.py", List.of(paths.sessionDir(session).resolve("candidates.json").toString(),
                    "--rank", String.valueOf(rank), "--edit", tmp.toString()), Duration.ofSeconds(30));
            if (result.exitCode() != 0) throw ApiException.badRequest(SessionService.lastLine(result.output()));
            return om.readValue(SessionService.lastLine(result.output()), new TypeReference<Map<String, Object>>() {});
        } catch (IOException ex) { throw ApiException.badRequest("计算预览失败：" + ex.getMessage()); }
        finally { try { Files.deleteIfExists(tmp); } catch (IOException ignored) {} }
    }

    public List<Map<String, Object>> renderPreviews(String session, int rank) {
        Path dir = paths.sessionDir(session).resolve("render_previews");
        if (!Files.isDirectory(dir)) return List.of();
        try (var files = Files.list(dir)) {
            List<Map<String, Object>> result = new ArrayList<>();
            for (Path p : files.filter(p -> RENDER_PREVIEW.matcher(p.getFileName().toString()).matches())
                    .filter(p -> p.getFileName().toString().startsWith(String.format("%02d_", rank))).toList()) {
                Map<String, Object> m = new LinkedHashMap<>();
                m.put("file", p.getFileName().toString());
                m.put("modified", Files.getLastModifiedTime(p).toMillis());
                Path range = p.resolveSibling(p.getFileName().toString().replace(".mp4", ".range.json"));
                if (Files.exists(range)) m.putAll(om.readValue(range.toFile(), Map.class));
                result.add(m);
            }
            result.sort((a, b) -> Long.compare(((Number)b.get("modified")).longValue(), ((Number)a.get("modified")).longValue()));
            return result;
        } catch (IOException ex) { throw ApiException.badRequest("读取试渲染失败"); }
    }

    public Path renderPreviewFile(String session, String file) {
        if (file == null || !RENDER_PREVIEW.matcher(file).matches()) throw ApiException.badRequest("文件名不合法");
        return WorkerPaths.inside(paths.output(), paths.sessionDir(session).resolve("render_previews").resolve(file));
    }

    /** 审核片段的剪切范围：读 clips/windows.json，没有就是候选窗口。 */
    private double[] clipWindow(String session, int rank, double cs, double ce) {
        try {
            Path f = paths.sessionDir(session).resolve("clips").resolve("windows.json");
            if (Files.exists(f)) {
                JsonNode w = om.readTree(f.toFile()).path(String.format("%02d", rank));
                if (w.path("start").isNumber() && w.path("end").isNumber()) {
                    return new double[] {round1(w.path("start").asDouble()), round1(w.path("end").asDouble())};
                }
            }
        } catch (IOException ignored) {
            // 读不到就当没有
        }
        return new double[] {cs, ce};
    }

    private String findClip(String session, int rank, double t) {
        Path clips = paths.sessionDir(session).resolve("clips");
        if (!Files.isDirectory(clips)) {
            return null;
        }
        String prefix = String.format("%02d_", rank);
        try (Stream<Path> s = Files.list(clips)) {
            return s.map(p -> p.getFileName().toString())
                    .filter(n -> n.startsWith(prefix) && n.endsWith(".mp4") && SessionService.clipMatches(n, prefix.length(), t))
                    .sorted().findFirst().orElse(null);
        } catch (IOException e) {
            return null;
        }
    }
}
