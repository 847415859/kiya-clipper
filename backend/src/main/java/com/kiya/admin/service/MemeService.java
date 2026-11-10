package com.kiya.admin.service;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import org.springframework.stereotype.Service;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.kiya.admin.common.ApiException;

/**
 * 梗库：worker/scripts/memes.py 生成 output/_memory/memes.json（热度、使用 / 保留统计、来源，每天刷新一次）；
 * 后台的决定（启用 / 停用、含义、尺度、黑名单、手动添加）在 config/memory_decisions.json（MemoryService 写）。
 * 这里把两边合起来给页面看——决定立刻生效显示，不用等下次刷新；判定规则和 clipper/memes.py 保持一致：
 * 命中黑名单词或禁区话题的一律是黑名单，后台改了状态也不例外。
 */
@Service
public class MemeService {
    private static final List<String> OVERLAY = List.of("level", "meaning", "origin", "usage", "forms", "tags");

    private final WorkerPaths paths;
    private final MemoryService memory;
    private final ObjectMapper om;

    public MemeService(WorkerPaths paths, MemoryService memory, ObjectMapper om) {
        this.paths = paths;
        this.memory = memory;
        this.om = om;
    }

    private Path libraryFile() {
        return paths.output().resolve("_memory").resolve("memes.json");
    }

    public Map<String, Object> get() {
        Map<String, Object> lib = new LinkedHashMap<>();
        Path f = libraryFile();
        if (Files.exists(f)) {
            try {
                lib = om.readValue(f.toFile(), new TypeReference<LinkedHashMap<String, Object>>() {});
            } catch (IOException e) {
                throw ApiException.badRequest("memes.json 损坏：" + e.getMessage());
            }
        }
        Map<String, Object> dec = memory.decisions();
        List<String> black = new ArrayList<>();
        for (Object o : (List<?>) dec.get("meme_blacklist")) {
            black.add(String.valueOf(o).toLowerCase());
        }
        List<String> taboo = new ArrayList<>();
        for (String w : memory.tabooWords()) {
            taboo.add(w.toLowerCase());
        }
        Map<?, ?> decisions = (Map<?, ?>) dec.get("memes");

        List<Map<String, Object>> rows = new ArrayList<>();
        java.util.Set<String> seen = new java.util.HashSet<>();
        Object raw = lib.get("memes");
        if (raw instanceof List<?> l) {
            for (Object o : l) {
                if (o instanceof Map<?, ?> m) {
                    Map<String, Object> e = new LinkedHashMap<>();
                    m.forEach((k, v) -> e.put(String.valueOf(k), v));
                    rows.add(e);
                }
            }
        }
        for (Map<String, Object> e : rows) {
            seen.add(String.valueOf(e.get("text")).toLowerCase());
            overlay(e, decisions);
        }
        if (decisions != null) {
            for (Map.Entry<?, ?> en : decisions.entrySet()) {
                if (!seen.contains(String.valueOf(en.getKey())) && en.getValue() instanceof Map<?, ?> d && Boolean.TRUE.equals(d.get("manual"))) {
                    Map<String, Object> e = new LinkedHashMap<>();
                    e.put("text", d.get("text") != null ? d.get("text") : en.getKey());
                    e.put("sources", List.of("手动添加"));
                    e.put("status", "approved");
                    e.put("weight", 1.0);
                    e.put("forms", List.of("text"));
                    e.put("tags", List.of());
                    overlay(e, decisions);
                    rows.add(e);
                }
            }
        }
        Map<String, Integer> counts = new LinkedHashMap<>();
        for (String s : List.of("approved", "pending", "disabled", "blacklisted")) {
            counts.put(s, 0);
        }
        for (Map<String, Object> e : rows) {
            String t = String.valueOf(e.get("text")).toLowerCase();
            String hit = black.stream().filter(t::contains).findFirst().orElseGet(
                    () -> taboo.stream().filter(t::contains).findFirst().orElse(null));
            if (hit != null) {
                e.put("status", "blacklisted");
                e.put("blocked_by", hit);
            }
            counts.merge(String.valueOf(e.get("status")), 1, Integer::sum);
        }
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("generated_at", lib.get("generated_at"));
        out.put("hot_days", lib.get("hot_days"));
        out.put("memes", rows);
        out.put("counts", counts);
        out.put("blacklist", dec.get("meme_blacklist"));
        return out;
    }

    private static void overlay(Map<String, Object> e, Map<?, ?> decisions) {
        if (decisions == null) {
            return;
        }
        Object d = decisions.get(String.valueOf(e.get("text")).toLowerCase());
        if (!(d instanceof Map<?, ?> dm)) {
            return;
        }
        Object st = dm.get("status");
        if (st instanceof String s && List.of("pending", "approved", "disabled").contains(s)) {
            e.put("status", s);
        }
        for (String k : OVERLAY) {
            Object v = dm.get(k);
            if (v != null && !"".equals(v)) {
                e.put(k, v);
            }
        }
        e.put("decided", true);
    }
}
