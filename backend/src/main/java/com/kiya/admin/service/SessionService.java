package com.kiya.admin.service;

import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.file.Files;
import java.nio.file.Path;
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
    static final List<String> CSV_HEADER = List.of("序号", "高峰时间", "片段起", "片段止", "分数", "主要信号", "高频弹幕",
            MARK_COL, NOTE_COL);

    private final LiveSessionMapper sessions;
    private final CandidateMapper candidates;
    private final WorkerPaths paths;
    private final ProcessRunner runner;
    private final ObjectMapper om;

    public SessionService(LiveSessionMapper sessions, CandidateMapper candidates, WorkerPaths paths,
                          ProcessRunner runner, ObjectMapper om) {
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
            m.put("candidateCount", cj == null ? 0 : cj.path("candidates").size());
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
        for (Candidate c : candidates.selectList(new QueryWrapper<Candidate>()
                .eq("session_name", session).orderByAsc("rnk"))) {
            list.add(toMap(c));
        }
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("session", session);
        out.put("candidates", list);
        if (cj != null) {
            out.put("signals", cj.path("signals_used").asText(""));
            out.put("delay", cj.path("delay_seconds").asDouble(0));
            out.put("minScore", cj.path("min_score").isNumber() ? cj.path("min_score").asDouble() : null);
            out.put("belowThreshold", cj.path("below_threshold").size());
            out.put("transcript", cj.path("transcript").isTextual() ? cj.path("transcript").asText() : null);
        }
        out.put("hasCurve", Files.exists(paths.sessionDir(session).resolve("curve.png")));
        return out;
    }

    /** candidates.json → 数据库。片段时间没变的保留标注；变了（重跑粗筛后换了片段）就清掉标注。 */
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
        Set<Integer> seen = new HashSet<>();
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
            } else if (c.getClipStart() != null && Math.abs(c.getClipStart() - n.path("clip_start").asDouble()) > 1) {
                c.setMark(null);
                c.setNote(null);
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
            c.setClipFile(findClip(clips, rank));
            if (isNew) {
                candidates.insert(c);
            } else {
                candidates.updateById(c);
            }
        }
        for (Candidate c : existing) {
            if (!seen.contains(c.getRnk())) {
                candidates.deleteById(c.getId());
            }
        }
        if (!hadMarks) {
            importMarks(session);
        }
        return cj;
    }

    public void mark(String session, int rank, String mark, String note) {
        paths.checkSession(session);
        Candidate c = candidates.selectOne(new QueryWrapper<Candidate>().eq("session_name", session).eq("rnk", rank));
        if (c == null) {
            throw ApiException.notFound("没有这个候选");
        }
        if (mark != null) {
            String m = mark.trim().toUpperCase();
            if (!m.isEmpty() && !m.equals("Y") && !m.equals("N")) {
                throw ApiException.badRequest("标注只能是 Y、N 或空");
            }
            c.setMark(m.isEmpty() ? null : m);
        }
        if (note != null) {
            if (note.length() > 500) {
                throw ApiException.badRequest("备注最多 500 字");
            }
            c.setNote(note.isBlank() ? null : note.trim());
        }
        candidates.updateById(c);
        exportCsv(session);
    }

    /** 导出 eval/reviews/<场次>.csv（evaluate.py 和旧的 review.py 都读这个文件）。 */
    public void exportCsv(String session) {
        List<List<String>> rows = new ArrayList<>();
        for (Candidate c : candidates.selectList(new QueryWrapper<Candidate>()
                .eq("session_name", session).orderByAsc("rnk"))) {
            rows.add(List.of(String.valueOf(c.getRnk()), TimeFmt.hhmmss(c.getT()), TimeFmt.hhmmss(c.getClipStart()),
                    TimeFmt.hhmmss(c.getClipEnd()), String.format("%.2f", c.getScore()), mainSignals(c.getContrib()),
                    joinJsonArray(c.getTopDanmaku()), c.getMark() == null ? "" : c.getMark(),
                    c.getNote() == null ? "" : c.getNote()));
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
                }
            }
            log.info("已从 {} 导入标注", f);
        } catch (IOException e) {
            log.warn("导入标注失败：{}", f, e);
        }
    }

    // ------------------------------------------------------------------ 工具
    private Map<String, Object> toMap(Candidate c) {
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

    private static String findClip(Path clips, int rank) {
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
