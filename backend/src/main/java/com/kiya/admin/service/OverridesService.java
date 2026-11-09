package com.kiya.admin.service;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.time.Duration;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;

import org.springframework.stereotype.Service;

import com.baomidou.mybatisplus.core.conditions.query.QueryWrapper;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.kiya.admin.common.ApiException;
import com.kiya.admin.entity.AppConfig;
import com.kiya.admin.entity.SubtitleStyle;
import com.kiya.admin.mapper.AppConfigMapper;
import com.kiya.admin.mapper.SubtitleStyleMapper;

/**
 * 后台里改的“额外热词、纠错词、默认字幕样式”，统一写到 config/admin_overrides.json，
 * worker（包括监控自动剪切、命令行）读取时与 config/*.yaml 合并。
 */
@Service
public class OverridesService {
    static final String K_HOTWORDS = "hotwords";
    static final String K_CORRECTIONS = "corrections";

    private final AppConfigMapper configs;
    private final SubtitleStyleMapper styles;
    private final WorkerPaths paths;
    private final ProcessRunner runner;
    private final ObjectMapper om;

    public OverridesService(AppConfigMapper configs, SubtitleStyleMapper styles, WorkerPaths paths,
                            ProcessRunner runner, ObjectMapper om) {
        this.configs = configs;
        this.styles = styles;
        this.paths = paths;
        this.runner = runner;
        this.om = om;
    }

    public Map<String, Object> words() {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put(K_HOTWORDS, hotwords());
        m.put(K_CORRECTIONS, corrections());
        return m;
    }

    public synchronized Map<String, Object> saveWords(List<String> hotwords, Map<String, String> corrections) {
        LinkedHashSet<String> hw = new LinkedHashSet<>();
        for (String w : hotwords == null ? List.<String>of() : hotwords) {
            String t = w == null ? "" : w.trim();
            if (t.isEmpty()) {
                continue;
            }
            if (t.length() < 2 || t.length() > 15 || t.matches("\\d+")) {
                throw ApiException.badRequest("热词“" + t + "”不合适：2–15 个字，不能是纯数字");
            }
            hw.add(t);
        }
        if (hw.size() > 1000) {
            throw ApiException.badRequest("热词太多（最多 1000 个）");
        }
        Map<String, String> cr = new LinkedHashMap<>();
        for (Map.Entry<String, String> e : (corrections == null ? Map.<String, String>of() : corrections).entrySet()) {
            String a = e.getKey() == null ? "" : e.getKey().trim();
            String b = e.getValue() == null ? "" : e.getValue().trim();
            if (a.isEmpty()) {
                continue;
            }
            if (a.length() > 30 || b.length() > 30) {
                throw ApiException.badRequest("纠错词每项最多 30 个字");
            }
            cr.put(a, b);
        }
        put(K_HOTWORDS, new ArrayList<>(hw));
        put(K_CORRECTIONS, cr);
        writeFile();
        return words();
    }

    /** 热词表最终会提交哪些词（worker 合并 yaml + 主播资料 + 后台的之后）。 */
    public String hotwordsPreview() {
        ProcessRunner.Result r = runner.run("hotwords.py", List.of("show"), Duration.ofSeconds(30));
        return r.output();
    }

    /** 重写 config/admin_overrides.json（先写临时文件再替换）。 */
    public synchronized void writeFile() {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put(K_HOTWORDS, hotwords());
        m.put(K_CORRECTIONS, corrections());
        SubtitleStyle s = styles.selectOne(new QueryWrapper<SubtitleStyle>().eq("is_default", 1).last("limit 1"));
        if (s != null) {
            try {
                m.put("subtitle_style", om.readValue(s.getConfigJson(), new TypeReference<Map<String, Object>>() {}));
                m.put("subtitle_style_name", s.getName());
            } catch (IOException ignored) {
                // 样式 JSON 坏了就不写，worker 会退回 yaml 里的 active
            }
        }
        Path f = paths.overridesFile();
        try {
            Files.createDirectories(f.getParent());
            Path tmp = f.resolveSibling(f.getFileName() + ".tmp");
            Files.writeString(tmp, om.writerWithDefaultPrettyPrinter().writeValueAsString(m), StandardCharsets.UTF_8);
            Files.move(tmp, f, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE);
        } catch (IOException e) {
            throw ApiException.badRequest("写入 " + f + " 失败：" + e.getMessage());
        }
    }

    private List<String> hotwords() {
        AppConfig c = configs.selectById(K_HOTWORDS);
        if (c == null || c.getV() == null) {
            return new ArrayList<>();
        }
        try {
            return om.readValue(c.getV(), new TypeReference<List<String>>() {});
        } catch (IOException e) {
            return new ArrayList<>();
        }
    }

    private Map<String, String> corrections() {
        AppConfig c = configs.selectById(K_CORRECTIONS);
        if (c == null || c.getV() == null) {
            return new LinkedHashMap<>();
        }
        try {
            return om.readValue(c.getV(), new TypeReference<LinkedHashMap<String, String>>() {});
        } catch (IOException e) {
            return new LinkedHashMap<>();
        }
    }

    private void put(String k, Object v) {
        String json;
        try {
            json = om.writeValueAsString(v);
        } catch (IOException e) {
            throw ApiException.badRequest("保存失败");
        }
        if (configs.selectById(k) == null) {
            configs.insert(new AppConfig(k, json));
        } else {
            configs.updateById(new AppConfig(k, json));
        }
    }
}
