package com.kiya.admin.service;

import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.stream.Stream;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import com.baomidou.mybatisplus.core.conditions.query.QueryWrapper;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.kiya.admin.common.ApiException;
import com.kiya.admin.common.Csv;
import com.kiya.admin.common.TimeFmt;
import com.kiya.admin.entity.Candidate;
import com.kiya.admin.entity.LiveSession;
import com.kiya.admin.mapper.CandidateMapper;
import com.kiya.admin.mapper.LiveSessionMapper;

/**
 * 场次与候选。worker 的输出文件是事实来源（candidates.json、transcript.json、clips/），
 * 数据库保存场次索引和人工标注；标注同时导出到 eval/reviews/<场次>.csv，evaluate.py 照常可用。
 */
@Service
public class SessionService {
    private static final Logger log = LoggerFactory.getLogger(SessionService.class);
    static final String MARK_COL = "是否有趣(Y/N)";
    static final String NOTE_COL = "备注";
    static final String REASON_COL = "原因";
    static final String MODE_COL = "模式";
    static final String SECONDS_COL = "耗时(秒)";
    static final List<String> CSV_HEADER = List.of("序号", "高峰时间", "片段起", "片段止", "分数", "主要信号", "高频弹幕",
            MARK_COL, NOTE_COL, REASON_COL, MODE_COL, SECONDS_COL);
    /** 审核时一键选的原因（代码固定，worker 的 clipper/memory.py 里有对应的中文说明）。 */
    static final Set<String> REASONS = Set.of("start_early", "start_late", "end_early", "end_late",
            "not_funny", "need_context", "read_sc", "repeat_meme", "taboo", "gem");
    /** blind 盲审（没看 AI，用于评测）；fast 看着 AI 审；confirm 直接采纳 AI 判断（不进评测和校准）。 */
    static final Set<String> MODES = Set.of("blind", "fast", "confirm");
    static final long MAX_REVIEW_MS = 10 * 60 * 1000L;

    private final LiveSessionMapper sessions;
    private final CandidateMapper candidates;
    private final WorkerPaths paths;
    private final ProcessRunner runner;
    private final ObjectMapper om;
    private final MemoryService memory;

    public SessionService(LiveSessionMapper sessions, CandidateMapper candidates, WorkerPaths paths,
                          ProcessRunner runner, ObjectMapper om, MemoryService memory) {
        this.memory = memory;
        this.sessions = sessions;
        this.candidates = candidates;
        this.paths = paths;
        this.runner = runner;
        this.om = om;
    }

    // ------------------------------------------------------------------ 场次
    /** 扫描录像目录（worker/scripts/sessions.py），更新场次表。 */
    public int scan() {
        ProcessRunner.Result r = runner.run("sessions.py", List.of(paths.recDir().toString(), "--json"),
                Duration.ofMinutes(3));
        if (r.exitCode() != 0) {
            throw ApiException.badRequest("扫描失败：" + lastLine(r.output()));
        }
        String out = r.output().trim();
        int start = out.lastIndexOf("\n[");
        String json = start >= 0 ? out.substring(start + 1) : out;
        List<Map<String, Object>> list;
        try {
            list = om.readValue(json, new TypeReference<List<Map<String, Object>>>() {});
        } catch (IOException e) {
            throw ApiException.badRequest("扫描结果解析失败：" + lastLine(out));
        }
        for (Map<String, Object> m : list) {
            String name = String.valueOf(m.get("name"));
            if (!name.matches("^[0-9A-Za-z_\\-]{1,64}$")) {
                continue;
            }
            LiveSession s = sessions.selectOne(new QueryWrapper<LiveSession>().eq("name", name));
            boolean isNew = s == null;
            if (isNew) {
                s = new LiveSession();
                s.setName(name);
            }
            s.setStartTime(m.get("start_time") == null ? null : String.valueOf(m.get("start_time")));
            s.setSegments(num(m.get("segments")).intValue());
            s.setDurationSeconds(num(m.get("duration_seconds")).doubleValue());
            s.setVideosPresent(num(m.get("videos_present")).intValue());
            s.setVideoBytes(num(m.get("video_bytes")).longValue());
            s.setDanmaku(num(m.get("danmaku")).intValue());
            s.setScannedAt(LocalDateTime.now());
            if (isNew) {
                sessions.insert(s);
            } else {
                sessions.updateById(s);
            }
        }
        return list.size();
    }

    /** 场次列表 = 数据库里扫描到的 + output/ 下有结果但录像已经不在的。附带每场的处理进度。 */
    public List<Map<String, Object>> list() {
        Map<String, Map<String, Object>> byName = new LinkedHashMap<>();
        for (LiveSession s : sessions.selectList(new QueryWrapper<LiveSession>().orderByDesc("name"))) {
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("name", s.getName());
            m.put("startTime", s.getStartTime());
            m.put("segments", s.getSegments());
            m.put("durationSeconds", s.getDurationSeconds());
            m.put("videosPresent", s.getVideosPresent());
            m.put("videoBytes", s.getVideoBytes());
            m.put("danmaku", s.getDanmaku());
            byName.put(s.getName(), m);
        }
        if (Files.isDirectory(paths.output())) {
            try (Stream<Path> dirs = Files.list(paths.output())) {
                dirs.filter(d -> Files.exists(d.resolve("candidates.json")) || Files.exists(d.resolve("transcript.json")))
                    .map(d -> d.getFileName().toString())
                    .filter(n -> n.matches("^[0-9A-Za-z_\\-]{1,64}$"))
                    .forEach(n -> byName.computeIfAbsent(n, k -> {
                        Map<String, Object> m = new LinkedHashMap<>();
                        m.put("name", k);
                        return m;
                    }));
            } catch (IOException e) {
                throw new UncheckedIOException(e);
            }
        }
        Map<String, int[]> marks = new HashMap<>();
        for (Candidate c : candidates.selectList(new QueryWrapper<Candidate>().select("session_name", "mark"))) {
            int[] a = marks.computeIfAbsent(c.getSessionName(), k -> new int[2]);
            if ("Y".equals(c.getMark()) || "N".equals(c.getMark())) {
                a[0]++;
            }
            if ("Y".equals(c.getMark())) {
                a[1]++;
            }
        }
        List<Map<String, Object>> out = new ArrayList<>(byName.values());
        for (Map<String, Object> m : out) {
            String name = (String) m.get("name");
            Path dir = paths.output().resolve(name);
            m.put("hasTranscript", Files.exists(dir.resolve("transcript.json")));
            JsonNode cj = readJson(dir.resolve("candidates.json"));
            m.put("hasCandidates", cj != null);
            m.put("hasRerank", Files.exists(dir.resolve("rerank.json")));
            m.put("candidateCount", cj == null ? 0 : cj.path("candidates").size());
            m.put("live", cj != null && cj.path("live").asBoolean(false));
            m.put("clipCount", countClips(dir.resolve("clips")));
            int[] a = marks.getOrDefault(name, new int[2]);
            m.put("reviewed", a[0]);
            m.put("funny", a[1]);
        }
        out.sort(Comparator.comparing((Map<String, Object> m) -> (String) m.get("name")).reversed());
        return out;
    }

    // ------------------------------------------------------------------ 候选
    /** 返回场次信息 + 候选列表（每次读取前先和 candidates.json 同步）。 */
    public Map<String, Object> candidates(String session) {
        paths.checkSession(session);
        JsonNode cj = sync(session);
        List<Map<String, Object>> list = new ArrayList<>();
        Map<String, Map<String, Object>> meta0 = readMeta(session);
        for (Candidate c : candidates.selectList(new QueryWrapper<Candidate>()
                .eq("session_name", session).orderByAsc("rnk"))) {
            list.add(toMap(c, meta0.get(String.valueOf(c.getRnk()))));
        }
        JsonNode rr = readJson(paths.sessionDir(session).resolve("rerank.json"));
        Map<Integer, Map<String, Object>> llm = rerankByRank(rr);
        for (Map<String, Object> m : list) {
            Map<String, Object> info = llm.get((Integer) m.get("rank"));
            Object cs = m.get("clipStart");
            if (info != null && cs instanceof Number n && Math.abs(n.doubleValue() - (Double) info.get("clipStart")) <= 1) {   // 重跑粗筛换了片段的旧结果不显示
                m.put("llm", info);
            }
        }
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("session", session);
        out.put("candidates", list);
        if (rr != null) {
            Map<String, Object> meta = new LinkedHashMap<>();
            meta.put("model", rr.path("model").asText(""));
            meta.put("prompt", rr.path("prompt").asText(""));
            meta.put("generatedAt", rr.path("generated_at").asText(""));
            meta.put("costYuan", rr.path("cost_yuan").isNumber() ? rr.path("cost_yuan").asDouble() : null);
            out.put("rerank", meta);
        }
        if (cj != null) {
            out.put("signals", cj.path("signals_used").asText(""));
            out.put("delay", cj.path("delay_seconds").asDouble(0));
            out.put("minScore", cj.path("min_score").isNumber() ? cj.path("min_score").asDouble() : null);
            out.put("belowThreshold", cj.path("below_threshold").size());
            out.put("transcript", cj.path("transcript").isTextual() ? cj.path("transcript").asText() : null);
            if (cj.path("live").asBoolean(false)) {                   // 边播边筛的临时结果：下播后会被正式粗筛覆盖
                Map<String, Object> live = new LinkedHashMap<>();
                live.put("coveredSeconds", cj.path("covered_seconds").asInt(0));
                live.put("segmentsDone", cj.path("segments_done").asInt(0));
                live.put("segmentsTotal", cj.path("segments_total").asInt(0));
                live.put("generatedAt", cj.path("generated_at").asText(""));
                out.put("live", live);
            }
        }
        out.put("taboo", memory.tabooWords());
        out.put("hasCurve", Files.exists(paths.sessionDir(session).resolve("curve.png")));
        return out;
    }

    /** 新旧候选的对应关系（新序号 → 旧序号），用来把标注搬到重新粗筛后的候选上。
     *  先认“序号没变、片段起点差不到 1 秒”的；剩下的新候选，再找带标注、离得最近（5 秒内）的旧候选。 */
    static Map<Integer, Integer> matchByClipStart(List<Candidate> old, JsonNode fresh, Set<Integer> hasMarks) {
        Map<Integer, Candidate> oldBy = new HashMap<>();
        old.forEach(c -> oldBy.put(c.getRnk(), c));
        Map<Integer, Integer> out = new LinkedHashMap<>();
        Set<Integer> used = new HashSet<>();
        for (JsonNode n : fresh) {
            int r = n.path("rank").asInt();
            Candidate o = oldBy.get(r);
            if (o != null && (o.getClipStart() == null || Math.abs(o.getClipStart() - n.path("clip_start").asDouble()) <= 1)) {
                out.put(r, r);
                used.add(r);
            }
        }
        List<double[]> pairs = new ArrayList<>();
        for (JsonNode n : fresh) {
            int r = n.path("rank").asInt();
            if (out.containsKey(r)) {
                continue;
            }
            for (Candidate o : old) {
                if (!used.contains(o.getRnk()) && hasMarks.contains(o.getRnk()) && o.getClipStart() != null) {
                    double d = Math.abs(o.getClipStart() - n.path("clip_start").asDouble());
                    if (d <= 5) {
                        pairs.add(new double[] {d, r, o.getRnk()});
                    }
                }
            }
        }
        pairs.sort(Comparator.comparingDouble((double[] x) -> x[0]));
        Set<Integer> usedNew = new HashSet<>();
        for (double[] x : pairs) {
            int nr = (int) x[1];
            int or = (int) x[2];
            if (!usedNew.contains(nr) && !used.contains(or)) {
                out.put(nr, or);
                usedNew.add(nr);
                used.add(or);
            }
        }
        return out;
    }

    /** candidates.json → 数据库。重新粗筛（比如边播边筛的结果被下播后的正式结果覆盖）后序号会变，
     *  标注、原因、耗时按片段起点对应搬过去；对不上的（片段已经不在候选里）才丢掉。 */
    public synchronized JsonNode sync(String session) {
        Path dir = paths.sessionDir(session);
        JsonNode cj = readJson(dir.resolve("candidates.json"));
        if (cj == null) {
            return null;
        }
        List<Candidate> existing = candidates.selectList(new QueryWrapper<Candidate>().eq("session_name", session));
        Map<Integer, Candidate> byRank = new HashMap<>();
        existing.forEach(c -> byRank.put(c.getRnk(), c));
        boolean hadMarks = existing.stream().anyMatch(c -> c.getMark() != null || c.getNote() != null);
        Map<String, Map<String, Object>> oldMeta = readMeta(session);
        Set<Integer> marked = new HashSet<>();
        Map<Integer, String[]> oldMarks = new HashMap<>();
        for (Candidate c : existing) {
            oldMarks.put(c.getRnk(), new String[] {c.getMark(), c.getNote()});
            if (c.getMark() != null || c.getNote() != null || oldMeta.containsKey(String.valueOf(c.getRnk()))) {
                marked.add(c.getRnk());
            }
        }
        Map<Integer, Integer> carry = matchByClipStart(existing, cj.path("candidates"), marked);
        Set<Integer> seen = new HashSet<>();
        boolean moved = false;
        Path clips = dir.resolve("clips");
        for (JsonNode n : cj.path("candidates")) {
            int rank = n.path("rank").asInt();
            seen.add(rank);
            Candidate c = byRank.get(rank);
            boolean isNew = c == null;
            if (isNew) {
                c = new Candidate();
                c.setSessionName(session);
                c.setRnk(rank);
            }
            Integer from = carry.get(rank);
            if (!isNew && (from == null || from != rank)) {            // 这个序号现在是另一个片段：标注换成对应过来的（没有就清空）
                String[] m = from == null ? new String[2] : oldMarks.get(from);
                c.setMark(m[0]);
                c.setNote(m[1]);
                moved |= marked.contains(rank) || from != null;
            } else if (isNew && from != null) {
                String[] m = oldMarks.get(from);
                c.setMark(m[0]);
                c.setNote(m[1]);
                moved = true;
            }
            c.setT(n.path("t").asDouble());
            c.setScore(n.path("score").asDouble());
            c.setClipStart(n.path("clip_start").asDouble());
            c.setClipEnd(n.path("clip_end").asDouble());
            c.setDanmakuPerMin(n.path("danmaku_per_min").asDouble());
            c.setEmotionHits(n.path("emotion_hits").asInt());
            c.setGiftValue(n.path("gift_value").asDouble());
            c.setTopDanmaku(n.path("top_danmaku").toString());
            c.setSpeech(n.has("speech") ? n.path("speech").toString() : null);
            c.setContrib(n.path("contrib").toString());
            c.setClipFile(findClip(clips, rank, c.getT()));
            if (isNew) {
                candidates.insert(c);
            } else {
                candidates.updateById(c);
            }
        }
        for (Candidate c : existing) {
            if (!seen.contains(c.getRnk())) {
                candidates.deleteById(c.getId());
                moved |= marked.contains(c.getRnk());
            }
        }
        if (!existing.isEmpty()) {
            Map<String, Map<String, Object>> newMeta = new LinkedHashMap<>();
            for (Map.Entry<Integer, Integer> e : carry.entrySet()) {
                Map<String, Object> m = oldMeta.get(String.valueOf(e.getValue()));
                if (m != null) {
                    newMeta.put(String.valueOf(e.getKey()), m);
                }
            }
            if (!newMeta.equals(oldMeta)) {
                writeMeta(session, newMeta);
                moved = true;
            }
            long lost = marked.stream().filter(r -> !carry.containsValue(r)).count();
            if (lost > 0) {
                log.info("{} 重新粗筛后有 {} 条标注对不上新的候选，已丢弃", session, lost);
            }
        }
        if (moved) {
            exportCsv(session);                                          // eval/reviews 里的序号跟着换
        }
        if (!hadMarks) {
            importMarks(session);
        }
        return cj;
    }


    public synchronized void mark(String session, int rank, String mark, String note, List<String> reasons,
                                  String mode, Long ms) {
        paths.checkSession(session);
        Candidate c = candidates.selectOne(new QueryWrapper<Candidate>().eq("session_name", session).eq("rnk", rank));
        if (c == null) {
            throw ApiException.notFound("没有这个候选");
        }
        boolean cleared = false;
        if (mark != null) {
            String m = mark.trim().toUpperCase();
            if (!m.isEmpty() && !m.equals("Y") && !m.equals("N")) {
                throw ApiException.badRequest("标注只能是 Y、N 或空");
            }
            c.setMark(m.isEmpty() ? null : m);
            cleared = m.isEmpty();
        }
        if (note != null) {
            if (note.length() > 500) {
                throw ApiException.badRequest("备注最多 500 字");
            }
            c.setNote(note.isBlank() ? null : note.trim());
        }
        candidates.updateById(c);
        if (cleared) {
            clearMeta(session, Set.of(rank));               // 清除标注时，原因 / 模式 / 耗时一起清
        } else if (reasons != null || mode != null || (ms != null && ms > 0)) {
            Map<String, Map<String, Object>> all = readMeta(session);
            Map<String, Object> e = all.computeIfAbsent(String.valueOf(rank), k -> new LinkedHashMap<>());
            if (reasons != null) {
                List<String> clean = new ArrayList<>();
                for (String r : reasons) {
                    if (!REASONS.contains(r)) {
                        throw ApiException.badRequest("不认识的原因：" + r);
                    }
                    if (!clean.contains(r)) {
                        clean.add(r);
                    }
                }
                e.put("reasons", clean);
            }
            if (mode != null && !mode.isBlank()) {
                if (!MODES.contains(mode)) {
                    throw ApiException.badRequest("不认识的审核模式：" + mode);
                }
                e.put("mode", mode);
            }
            if (ms != null && ms > 0) {
                long add = Math.min(ms, MAX_REVIEW_MS);              // 一次最多算 10 分钟，挂机不算数
                e.put("ms", ((Number) e.getOrDefault("ms", 0L)).longValue() + add);
            }
            e.put("at", LocalDateTime.now().withNano(0).toString());
            writeMeta(session, all);
        }
        exportCsv(session);
    }

    // ------------------------------------------------------------------ 审核附加信息（原因 / 模式 / 耗时）
    private Path metaFile(String session) {
        return paths.sessionDir(session).resolve("review_meta.json");
    }

    Map<String, Map<String, Object>> readMeta(String session) {
        Path f = metaFile(session);
        if (!Files.exists(f)) {
            return new LinkedHashMap<>();
        }
        try {
            return om.readValue(f.toFile(), new TypeReference<LinkedHashMap<String, Map<String, Object>>>() {});
        } catch (IOException e) {
            log.warn("读取失败：{}", f, e);
            return new LinkedHashMap<>();
        }
    }

    private void writeMeta(String session, Map<String, Map<String, Object>> all) {
        Path f = metaFile(session);
        Path tmp = f.resolveSibling(f.getFileName() + ".tmp");
        try {
            Files.createDirectories(f.getParent());
            Files.writeString(tmp, om.writerWithDefaultPrettyPrinter().writeValueAsString(all), StandardCharsets.UTF_8);
            Files.move(tmp, f, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE);
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
    }

    private void clearMeta(String session, Set<Integer> ranks) {
        Map<String, Map<String, Object>> all = readMeta(session);
        boolean changed = false;
        for (Integer r : ranks) {
            changed |= all.remove(String.valueOf(r)) != null;
        }
        if (changed) {
            writeMeta(session, all);
        }
    }

    /** 导出 eval/reviews/<场次>.csv（evaluate.py 和旧的 review.py 都读这个文件）。 */
    public void exportCsv(String session) {
        List<List<String>> rows = new ArrayList<>();
        Map<String, Map<String, Object>> meta = readMeta(session);
        for (Candidate c : candidates.selectList(new QueryWrapper<Candidate>()
                .eq("session_name", session).orderByAsc("rnk"))) {
            Map<String, Object> e = meta.getOrDefault(String.valueOf(c.getRnk()), Map.of());
            Object rs = e.get("reasons");
            String reasons = rs instanceof List<?> l ? String.join("|", l.stream().map(String::valueOf).toList()) : "";
            long ms = e.get("ms") instanceof Number n ? n.longValue() : 0;
            rows.add(List.of(String.valueOf(c.getRnk()), TimeFmt.hhmmss(c.getT()), TimeFmt.hhmmss(c.getClipStart()),
                    TimeFmt.hhmmss(c.getClipEnd()), String.format("%.2f", c.getScore()), mainSignals(c.getContrib()),
                    joinJsonArray(c.getTopDanmaku()), c.getMark() == null ? "" : c.getMark(),
                    c.getNote() == null ? "" : c.getNote(), reasons,
                    e.get("mode") == null ? "" : String.valueOf(e.get("mode")),
                    ms > 0 ? String.valueOf(Math.round(ms / 1000.0)) : ""));
        }
        try {
            Csv.write(paths.reviews().resolve(session + ".csv"), CSV_HEADER, rows);
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
    }

    /** 第一次同步时，把之前用 review.py 标注的结果导进来。 */
    private void importMarks(String session) {
        Path f = paths.reviews().resolve(session + ".csv");
        if (!Files.exists(f)) {
            return;
        }
        Map<String, Map<String, Object>> imported = readMeta(session);
        try {
            for (Map<String, String> r : Csv.readMaps(f)) {
                String rank = r.getOrDefault("序号", "").trim();
                String mark = r.getOrDefault(MARK_COL, "").trim().toUpperCase();
                String note = r.getOrDefault(NOTE_COL, "").trim();
                if (!rank.matches("\\d+") || (mark.isEmpty() && note.isEmpty())) {
                    continue;
                }
                Candidate c = candidates.selectOne(new QueryWrapper<Candidate>()
                        .eq("session_name", session).eq("rnk", Integer.parseInt(rank)));
                if (c != null) {
                    c.setMark(mark.equals("Y") || mark.equals("N") ? mark : null);
                    c.setNote(note.isEmpty() ? null : note.substring(0, Math.min(500, note.length())));
                    candidates.updateById(c);
                    importMeta(imported, rank, r);
                }
            }
            writeMeta(session, imported);
            log.info("已从 {} 导入标注", f);
        } catch (IOException e) {
            log.warn("导入标注失败：{}", f, e);
        }
    }

    private static void importMeta(Map<String, Map<String, Object>> into, String rank, Map<String, String> row) {
        List<String> reasons = new ArrayList<>();
        for (String x : row.getOrDefault(REASON_COL, "").split("\\|")) {
            if (REASONS.contains(x.trim())) {
                reasons.add(x.trim());
            }
        }
        String mode = row.getOrDefault(MODE_COL, "").trim();
        String sec = row.getOrDefault(SECONDS_COL, "").trim();
        if (reasons.isEmpty() && !MODES.contains(mode) && !sec.matches("\\d+")) {
            return;
        }
        Map<String, Object> e = new LinkedHashMap<>();
        if (!reasons.isEmpty()) {
            e.put("reasons", reasons);
        }
        if (MODES.contains(mode)) {
            e.put("mode", mode);
        }
        if (sec.matches("\\d+")) {
            e.put("ms", Long.parseLong(sec) * 1000);
        }
        into.put(rank, e);
    }

    // ------------------------------------------------------------------ 工具
    /** rerank.json（精排结果）按原序号整理成前端要的字段；没有精排或解析失败返回空表。 */
    Map<Integer, Map<String, Object>> rerankByRank(JsonNode rr) {
        Map<Integer, Map<String, Object>> out = new HashMap<>();
        if (rr == null) {
            return out;
        }
        for (JsonNode c : rr.path("candidates")) {
            Map<String, Object> m = new LinkedHashMap<>();
            JsonNode l = c.path("llm");
            m.put("newRank", c.path("new_rank").isNumber() ? c.path("new_rank").asInt() : null);
            m.put("final", c.path("final").isNumber() ? c.path("final").asDouble() : null);
            m.put("error", c.path("error").isTextual() ? c.path("error").asText() : null);
            m.put("promptVersion", c.path("prompt_version").asText(""));
            m.put("clipStart", c.path("clip_start").asDouble());
            if (l.isObject()) {
                m.put("funny", l.path("funny").asBoolean(false));
                m.put("score", l.path("score").asDouble(0));
                m.put("type", l.path("type").asText(""));
                m.put("standalone", l.path("standalone").asBoolean(false));
                m.put("whatHappened", l.path("what_happened").asText(""));
                m.put("reason", l.path("reason").asText(""));
                m.put("story", om.convertValue(l.path("story"), new TypeReference<Map<String, Object>>() {}));
                m.put("titles", om.convertValue(l.path("title_candidates"), new TypeReference<List<String>>() {}));
            }
            JsonNode r = c.path("refined");
            if (r.isObject()) {
                Map<String, Object> rm = new LinkedHashMap<>();
                rm.put("start", r.path("start").asDouble());
                rm.put("end", r.path("end").asDouble());
                rm.put("note", r.path("note").asText(""));
                m.put("refined", rm);
            }
            out.put(c.path("rank").asInt(), m);
        }
        return out;
    }

    private Map<String, Object> toMap(Candidate c, Map<String, Object> meta) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("rank", c.getRnk());
        m.put("t", c.getT());
        m.put("score", c.getScore());
        m.put("clipStart", c.getClipStart());
        m.put("clipEnd", c.getClipEnd());
        m.put("danmakuPerMin", c.getDanmakuPerMin());
        m.put("emotionHits", c.getEmotionHits());
        m.put("giftValue", c.getGiftValue());
        m.put("topDanmaku", parse(c.getTopDanmaku()));
        m.put("speech", parse(c.getSpeech()));
        m.put("contrib", parse(c.getContrib()));
        m.put("mark", c.getMark());
        m.put("note", c.getNote());
        m.put("clip", c.getClipFile());
        m.put("reasons", meta != null && meta.get("reasons") != null ? meta.get("reasons") : List.of());
        m.put("mode", meta == null ? null : meta.get("mode"));
        m.put("reviewMs", meta != null && meta.get("ms") instanceof Number n ? n.longValue() : 0L);
        return m;
    }

    private Object parse(String json) {
        if (json == null) {
            return null;
        }
        try {
            return om.readValue(json, Object.class);
        } catch (IOException e) {
            return null;
        }
    }

    private String joinJsonArray(String json) {
        Object o = parse(json);
        if (o instanceof List<?> l) {
            List<String> s = new ArrayList<>();
            l.forEach(x -> s.add(String.valueOf(x)));
            return String.join(" | ", s);
        }
        return "";
    }

    private String mainSignals(String contribJson) {
        Object o = parse(contribJson);
        if (!(o instanceof Map<?, ?> m)) {
            return "";
        }
        List<Map.Entry<?, ?>> e = new ArrayList<>(m.entrySet());
        e.sort((a, b) -> Double.compare(num(b.getValue()).doubleValue(), num(a.getValue()).doubleValue()));
        List<String> out = new ArrayList<>();
        for (int i = 0; i < Math.min(2, e.size()); i++) {
            out.add(String.format("%s%+.1f", e.get(i).getKey(), num(e.get(i).getValue()).doubleValue()));
        }
        return String.join(" ", out);
    }

    private JsonNode readJson(Path f) {
        if (!Files.exists(f)) {
            return null;
        }
        try {
            return om.readTree(f.toFile());
        } catch (IOException e) {
            log.warn("读取失败：{}", f, e);
            return null;
        }
    }

    /** 文件名是 序号_时-分-秒.mp4（高峰时间）；重新粗筛后同一个序号可能是别的片段，时间对不上的旧文件不算。 */
    private static String findClip(Path clips, int rank, Double t) {
        if (!Files.isDirectory(clips)) {
            return null;
        }
        String prefix = String.format("%02d_", rank);
        try (Stream<Path> s = Files.list(clips)) {
            return s.map(p -> p.getFileName().toString())
                    .filter(n -> n.startsWith(prefix) && n.endsWith(".mp4") && clipMatches(n, prefix.length(), t))
                    .sorted().findFirst().orElse(null);
        } catch (IOException e) {
            return null;
        }
    }

    private static final Pattern CLIP_TIME = Pattern.compile("(\\d+)-(\\d\\d)-(\\d\\d)\\.mp4");

    static boolean clipMatches(String name, int prefixLen, Double t) {
        Matcher m = CLIP_TIME.matcher(name.substring(prefixLen));
        if (t == null || !m.matches()) {
            return true;                                             // 不是这个格式的文件名就不管了
        }
        double sec = Integer.parseInt(m.group(1)) * 3600.0 + Integer.parseInt(m.group(2)) * 60 + Integer.parseInt(m.group(3));
        return Math.abs(sec - t) <= 3;
    }

    private static int countClips(Path clips) {
        if (!Files.isDirectory(clips)) {
            return 0;
        }
        try (Stream<Path> s = Files.list(clips)) {
            return (int) s.filter(p -> p.getFileName().toString().endsWith(".mp4")).count();
        } catch (IOException e) {
            return 0;
        }
    }

    private static Number num(Object o) {
        if (o instanceof Number n) {
            return n;
        }
        try {
            return Double.parseDouble(String.valueOf(o));
        } catch (NumberFormatException e) {
            return 0;
        }
    }

    static String lastLine(String s) {
        String[] lines = s.strip().split("\n");
        return lines.length == 0 ? "" : lines[lines.length - 1];
    }
}
