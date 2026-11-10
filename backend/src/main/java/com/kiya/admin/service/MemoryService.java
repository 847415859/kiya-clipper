package com.kiya.admin.service;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Pattern;

import org.springframework.stereotype.Service;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.kiya.admin.common.ApiException;

/**
 * 精排记忆：worker/scripts/learn.py 生成 output/_memory/memory.json（只读）；
 * 后台的决定（删梗、手动加梗、排除例子、切片号 UID、BV 号绑定、应用新权重）写到 config/memory_decisions.json，
 * learn.py 和 rerank.py 都读它。两边各写各的文件，不会互相覆盖。
 */
@Service
public class MemoryService {
    private static final Pattern BV = Pattern.compile("^BV[0-9A-Za-z]{10}$");
    private static final Pattern UID = Pattern.compile("^[0-9]{1,20}$");
    private static final Pattern EXAMPLE = Pattern.compile("^[0-9A-Za-z_\\-]{1,64}#[0-9]{1,3}$");
    private static final List<String> LORE_KINDS = List.of("catchphrase", "topic", "meme");
    private static final List<String> WEIGHT_KEYS = List.of("llm", "screen", "not_funny");

    private final WorkerPaths paths;
    private final ObjectMapper om;

    public MemoryService(WorkerPaths paths, ObjectMapper om) {
        this.paths = paths;
        this.om = om;
    }

    private Path memoryFile() {
        return paths.output().resolve("_memory").resolve("memory.json");
    }

    private Path decisionsFile() {
        return paths.configDir().resolve("memory_decisions.json");
    }

    private Map<String, Object> read(Path f) {
        if (!Files.exists(f)) {
            return new LinkedHashMap<>();
        }
        try {
            return om.readValue(f.toFile(), new TypeReference<LinkedHashMap<String, Object>>() {});
        } catch (IOException e) {
            throw ApiException.badRequest(f.getFileName() + " 损坏：" + e.getMessage());
        }
    }

    public Map<String, Object> get() {
        Map<String, Object> out = new LinkedHashMap<>();
        Map<String, Object> mem = read(memoryFile());
        mem.remove("my_uploads_raw");
        out.put("memory", mem.isEmpty() ? null : mem);
        out.put("decisions", decisions());
        return out;
    }

    public synchronized Map<String, Object> decisions() {
        Map<String, Object> d = read(decisionsFile());
        d.putIfAbsent("my_uid", "");
        d.putIfAbsent("rejected_lore", new ArrayList<>());
        d.putIfAbsent("manual_lore", new ArrayList<>());
        d.putIfAbsent("excluded_examples", new ArrayList<>());
        d.putIfAbsent("published", new LinkedHashMap<>());
        d.putIfAbsent("promoted_lore", new ArrayList<>());
        d.putIfAbsent("taboo", new ArrayList<>());
        d.putIfAbsent("weights", null);
        return d;
    }

    /** 禁区词（审核页标红用）。 */
    public List<String> tabooWords() {
        List<String> out = new ArrayList<>();
        for (Object o : list(decisions(), "taboo")) {
            String w = o instanceof Map<?, ?> m ? String.valueOf(m.get("word")) : String.valueOf(o);
            if (!w.isBlank() && !"null".equals(w)) {
                out.add(w.strip());
            }
        }
        return out;
    }

    @SuppressWarnings("unchecked")
    private static List<Object> list(Map<String, Object> d, String k) {
        return (List<Object>) d.get(k);
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> map(Map<String, Object> d, String k) {
        return (Map<String, Object>) d.get(k);
    }

    private static String text(Map<String, Object> req, String k, int max) {
        Object v = req.get(k);
        String s = v == null ? "" : String.valueOf(v).strip();
        if (s.length() > max) {
            throw ApiException.badRequest(k + " 最多 " + max + " 个字");
        }
        return s;
    }

    /** 一次一个操作：action = myUid / reject / unreject / addLore / removeLore / promote / unpromote / addTaboo / removeTaboo
     *  / exclude / include / publish / unpublish / applyWeights / resetWeights。 */
    public synchronized Map<String, Object> act(Map<String, Object> req) {
        Map<String, Object> d = decisions();
        String action = text(req, "action", 32);
        switch (action) {
            case "myUid" -> {
                String uid = text(req, "uid", 20);
                if (!uid.isEmpty() && !UID.matcher(uid).matches()) {
                    throw ApiException.badRequest("UID 只能是数字");
                }
                d.put("my_uid", uid);
            }
            case "reject", "unreject" -> {
                String t = text(req, "text", 40);
                if (t.isEmpty()) {
                    throw ApiException.badRequest("缺少内容");
                }
                List<Object> r = list(d, "rejected_lore");
                r.removeIf(x -> String.valueOf(x).equalsIgnoreCase(t));
                if (action.equals("reject")) {
                    r.add(t);
                    list(d, "manual_lore").removeIf(x -> x instanceof Map<?, ?> m && t.equalsIgnoreCase(String.valueOf(m.get("text"))));
                }
            }
            case "addLore" -> {
                String t = text(req, "text", 20);
                if (t.isEmpty()) {
                    throw ApiException.badRequest("请填写梗 / 说法");
                }
                List<Object> m = list(d, "manual_lore");
                m.removeIf(x -> x instanceof Map<?, ?> mm && t.equalsIgnoreCase(String.valueOf(mm.get("text"))));
                Map<String, Object> item = new LinkedHashMap<>();
                item.put("text", t);
                item.put("note", text(req, "note", 60));
                m.add(item);
                list(d, "rejected_lore").removeIf(x -> String.valueOf(x).equalsIgnoreCase(t));
            }
            case "removeLore" -> {
                String t = text(req, "text", 40);
                list(d, "manual_lore").removeIf(x -> x instanceof Map<?, ?> m && t.equalsIgnoreCase(String.valueOf(m.get("text"))));
            }
            case "promote" -> {
                // 梗转正：并入主播档案（热词、字幕关键词、标签、提示词都会用），不再受“自动发现”的数量限制
                String t = text(req, "text", 20);
                String kind = text(req, "kind", 16);
                if (t.length() < 2) {
                    throw ApiException.badRequest("至少 2 个字");
                }
                if (!LORE_KINDS.contains(kind)) {
                    throw ApiException.badRequest("类型只能是 catchphrase / topic / meme");
                }
                List<Object> pl = list(d, "promoted_lore");
                pl.removeIf(x -> x instanceof Map<?, ?> m && t.equalsIgnoreCase(String.valueOf(m.get("text"))));
                Map<String, Object> item = new LinkedHashMap<>();
                item.put("text", t);
                item.put("kind", kind);
                item.put("note", text(req, "note", 60));
                item.put("at", LocalDateTime.now().withNano(0).toString());
                pl.add(item);
                list(d, "rejected_lore").removeIf(x -> String.valueOf(x).equalsIgnoreCase(t));
                list(d, "manual_lore").removeIf(x -> x instanceof Map<?, ?> m && t.equalsIgnoreCase(String.valueOf(m.get("text"))));
                d.put("hotwords_dirty", true);
            }
            case "unpromote" -> {
                String t = text(req, "text", 20);
                list(d, "promoted_lore").removeIf(x -> x instanceof Map<?, ?> m && t.equalsIgnoreCase(String.valueOf(m.get("text"))));
                d.put("hotwords_dirty", true);
            }
            case "hotwordsSynced" -> d.remove("hotwords_dirty");
            case "addTaboo" -> {
                String w = text(req, "word", 20);
                if (w.length() < 2) {
                    throw ApiException.badRequest("禁区词至少 2 个字（单字会误伤很多句子）");
                }
                List<Object> tb = list(d, "taboo");
                tb.removeIf(x -> x instanceof Map<?, ?> m && w.equalsIgnoreCase(String.valueOf(m.get("word"))));
                Map<String, Object> item = new LinkedHashMap<>();
                item.put("word", w);
                item.put("note", text(req, "note", 60));
                item.put("at", LocalDateTime.now().withNano(0).toString());
                tb.add(item);
            }
            case "removeTaboo" -> {
                String w = text(req, "word", 20);
                list(d, "taboo").removeIf(x -> x instanceof Map<?, ?> m && w.equalsIgnoreCase(String.valueOf(m.get("word"))));
            }
            case "exclude", "include" -> {
                String id = text(req, "id", 80);
                if (!EXAMPLE.matcher(id).matches()) {
                    throw ApiException.badRequest("例子编号不合法");
                }
                List<Object> e = list(d, "excluded_examples");
                e.remove(id);
                if (action.equals("exclude")) {
                    e.add(id);
                }
            }
            case "publish" -> {
                String bv = text(req, "bvid", 12);
                if (!BV.matcher(bv).matches()) {
                    throw ApiException.badRequest("BV 号格式不对（BV 开头共 12 位）");
                }
                String session = paths.checkSession(text(req, "session", 64));
                int rank = intOf(req.get("rank"), "rank");
                if (rank < 1 || rank > 999) {
                    throw ApiException.badRequest("序号不合法");
                }
                Map<String, Object> pub = map(d, "published");
                pub.entrySet().removeIf(en -> en.getValue() instanceof Map<?, ?> m
                        && session.equals(m.get("session")) && Integer.valueOf(rank).equals(intOrNull(m.get("rank"))));
                Map<String, Object> link = new LinkedHashMap<>();
                link.put("session", session);
                link.put("rank", rank);
                Double completion = numOrNull(req.get("completion"));
                if (completion != null && (completion < 0 || completion > 1)) {
                    throw ApiException.badRequest("完播率填 0–1 之间的小数（如 0.35）");
                }
                if (completion != null) {
                    link.put("completion", completion);
                }
                Double followers = numOrNull(req.get("followers"));
                if (followers != null) {
                    link.put("followers", followers.intValue());
                }
                pub.put(bv, link);
            }
            case "unpublish" -> map(d, "published").remove(text(req, "bvid", 12));
            case "applyWeights" -> {
                Map<String, Object> mem = read(memoryFile());
                Object w = mem.get("weights") instanceof Map<?, ?> wm ? wm.get("proposed") : null;
                if (!(w instanceof Map<?, ?> pw)) {
                    throw ApiException.badRequest("现在没有建议的新权重");
                }
                Map<String, Object> clean = new LinkedHashMap<>();
                for (String k : WEIGHT_KEYS) {
                    Double v = numOrNull(pw.get(k));
                    if (v == null || v < 0 || v > 1) {
                        throw ApiException.badRequest("建议权重数据不对，请重新“更新精排记忆”");
                    }
                    clean.put(k, v);
                }
                d.put("weights", clean);
                d.put("weights_applied_at", LocalDateTime.now().toString());
            }
            case "resetWeights" -> {
                d.put("weights", null);
                d.remove("weights_applied_at");
            }
            default -> throw ApiException.badRequest("不支持的操作");
        }
        d.put("updated_at", LocalDateTime.now().toString());
        write(d);
        return d;
    }

    private void write(Map<String, Object> d) {
        Path f = decisionsFile();
        Path tmp = f.resolveSibling(f.getFileName() + ".tmp");
        try {
            Files.createDirectories(f.getParent());
            Files.writeString(tmp, om.writerWithDefaultPrettyPrinter().writeValueAsString(d), StandardCharsets.UTF_8);
            Files.move(tmp, f, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE);
        } catch (IOException e) {
            throw ApiException.badRequest("保存失败：" + e.getMessage());
        }
    }

    private static int intOf(Object v, String k) {
        Integer i = intOrNull(v);
        if (i == null) {
            throw ApiException.badRequest(k + " 不合法");
        }
        return i;
    }

    private static Integer intOrNull(Object v) {
        if (v instanceof Number n) {
            return n.intValue();
        }
        try {
            return v == null ? null : Integer.parseInt(String.valueOf(v).strip());
        } catch (NumberFormatException e) {
            return null;
        }
    }

    private static Double numOrNull(Object v) {
        if (v instanceof Number n) {
            return n.doubleValue();
        }
        if (v == null || String.valueOf(v).isBlank()) {
            return null;
        }
        try {
            return Double.parseDouble(String.valueOf(v).strip());
        } catch (NumberFormatException e) {
            throw ApiException.badRequest("数字格式不对：" + v);
        }
    }
}
