package com.kiya.admin.service;

import java.io.IOException;
import java.io.Reader;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.regex.Pattern;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.context.event.EventListener;
import org.springframework.stereotype.Service;
import org.yaml.snakeyaml.Yaml;

import com.baomidou.mybatisplus.core.conditions.query.QueryWrapper;
import com.baomidou.mybatisplus.core.conditions.update.UpdateWrapper;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.kiya.admin.common.ApiException;
import com.kiya.admin.entity.SubtitleStyle;
import com.kiya.admin.mapper.SubtitleStyleMapper;

/**
 * 字幕样式：存在数据库里，结构由 config/schema/subtitle_style.schema.json 定义（与 worker 共用同一份 schema）。
 * 默认样式会写进 config/admin_overrides.json，worker 不指定样式时（包括监控自动剪切）就用它。
 */
@Service
public class StyleService {
    private static final Logger log = LoggerFactory.getLogger(StyleService.class);
    private static final Pattern CODE = Pattern.compile("^[a-z0-9_\\-]{1,32}$");

    private final SubtitleStyleMapper mapper;
    private final WorkerPaths paths;
    private final ProcessRunner runner;
    private final OverridesService overrides;
    private final ObjectMapper om;

    public StyleService(SubtitleStyleMapper mapper, WorkerPaths paths, ProcessRunner runner,
                        OverridesService overrides, ObjectMapper om) {
        this.mapper = mapper;
        this.paths = paths;
        this.runner = runner;
        this.overrides = overrides;
        this.om = om;
    }

    public JsonNode schema() {
        Path f = paths.configDir().resolve("schema").resolve("subtitle_style.schema.json");
        try {
            return om.readTree(f.toFile());
        } catch (IOException e) {
            throw ApiException.notFound("找不到字幕样式 schema：" + f);
        }
    }

    /** 按 schema 补默认值并校验，返回完整样式（规则与 worker 的 validate_style 一致）。 */
    public Map<String, Object> validate(Map<String, Object> raw) {
        JsonNode props = schema().path("properties");
        Map<String, Object> in = raw == null ? Map.of() : raw;
        for (String k : in.keySet()) {
            if (!props.has(k)) {
                throw ApiException.badRequest("未知字段：" + k);
            }
        }
        Map<String, Object> out = new LinkedHashMap<>();
        Iterator<Map.Entry<String, JsonNode>> it = props.fields();
        while (it.hasNext()) {
            Map.Entry<String, JsonNode> e = it.next();
            String k = e.getKey();
            JsonNode p = e.getValue();
            String title = k + "（" + p.path("title").asText(k) + "）";
            Object v = in.containsKey(k) ? in.get(k) : om.convertValue(p.path("default"), Object.class);
            String type = p.path("type").asText();
            switch (type) {
                case "integer" -> {
                    if (!(v instanceof Number n) || n.doubleValue() != Math.rint(n.doubleValue())) {
                        throw ApiException.badRequest(title + "应为整数");
                    }
                    v = n.longValue();
                    range(p, ((Number) v).doubleValue(), title);
                }
                case "number" -> {
                    if (!(v instanceof Number n)) {
                        throw ApiException.badRequest(title + "应为数字");
                    }
                    range(p, n.doubleValue(), title);
                }
                case "boolean" -> {
                    if (!(v instanceof Boolean)) {
                        throw ApiException.badRequest(title + "应为开 / 关");
                    }
                }
                case "string" -> {
                    if (!(v instanceof String s)) {
                        throw ApiException.badRequest(title + "应为文字");
                    }
                    if (s.length() > 64) {
                        throw ApiException.badRequest(title + "太长");
                    }
                    if (p.has("pattern") && !Pattern.compile(p.path("pattern").asText()).matcher(s).find()) {
                        throw ApiException.badRequest(title + "格式不对（颜色写成 #RRGGBB 或 #RRGGBBAA）");
                    }
                    if (p.has("enum")) {
                        boolean ok = false;
                        for (JsonNode x : p.path("enum")) {
                            ok |= x.asText().equals(s);
                        }
                        if (!ok) {
                            throw ApiException.badRequest(title + "取值不对");
                        }
                    }
                }
                case "array" -> {
                    if (!(v instanceof List<?> l)) {
                        throw ApiException.badRequest(title + "应为列表");
                    }
                    List<String> s = new ArrayList<>();
                    for (Object x : l) {
                        String str = String.valueOf(x).trim();
                        if (!str.isEmpty() && str.length() <= 30) {
                            s.add(str);
                        }
                    }
                    v = s;
                }
                default -> { }
            }
            out.put(k, v);
        }
        return out;
    }

    private static void range(JsonNode p, double v, String title) {
        if ((p.has("minimum") && v < p.path("minimum").asDouble())
                || (p.has("maximum") && v > p.path("maximum").asDouble())) {
            throw ApiException.badRequest(title + "超出范围 " + p.path("minimum").asText() + "–"
                    + p.path("maximum").asText());
        }
    }

    // ------------------------------------------------------------------ 增删改查
    public List<Map<String, Object>> list() {
        List<Map<String, Object>> out = new ArrayList<>();
        for (SubtitleStyle s : mapper.selectList(new QueryWrapper<SubtitleStyle>().orderByDesc("is_default").orderByAsc("id"))) {
            out.add(toMap(s));
        }
        return out;
    }

    public Map<String, Object> save(Long id, String code, Map<String, Object> config) {
        Map<String, Object> cfg = validate(config);
        String name = String.valueOf(cfg.get("name"));
        SubtitleStyle s;
        if (id == null) {
            if (code == null || !CODE.matcher(code).matches()) {
                throw ApiException.badRequest("样式代码只能用小写字母、数字、下划线、短横线，最多 32 位");
            }
            if (mapper.selectCount(new QueryWrapper<SubtitleStyle>().eq("code", code)) > 0) {
                throw ApiException.badRequest("样式代码已存在");
            }
            s = new SubtitleStyle();
            s.setCode(code);
            s.setIsDefault(mapper.selectCount(null) == 0 ? 1 : 0);
        } else {
            s = get(id);
        }
        s.setName(name);
        s.setConfigJson(write(cfg));
        s.setUpdatedAt(LocalDateTime.now());
        if (id == null) {
            mapper.insert(s);
        } else {
            mapper.updateById(s);
        }
        if (s.getIsDefault() != null && s.getIsDefault() == 1) {
            overrides.writeFile();
        }
        return toMap(s);
    }

    public void delete(long id) {
        SubtitleStyle s = get(id);
        if (s.getIsDefault() != null && s.getIsDefault() == 1) {
            throw ApiException.badRequest("默认样式不能删除，先把别的样式设为默认");
        }
        mapper.deleteById(id);
    }

    public synchronized void setDefault(long id) {
        get(id);
        mapper.update(null, new UpdateWrapper<SubtitleStyle>().set("is_default", 0).ne("id", id));
        mapper.update(null, new UpdateWrapper<SubtitleStyle>().set("is_default", 1).eq("id", id));
        overrides.writeFile();
    }

    public SubtitleStyle get(long id) {
        SubtitleStyle s = mapper.selectById(id);
        if (s == null) {
            throw ApiException.notFound("没有这个样式");
        }
        return s;
    }

    /** 把样式写成 JSON 文件给 cut.py --sub-style-json 用。 */
    public Path exportForJob(long id, Path dir) {
        SubtitleStyle s = get(id);
        try {
            Files.createDirectories(dir);
            Path f = dir.resolve("style-" + s.getCode() + ".json");
            Files.writeString(f, s.getConfigJson(), StandardCharsets.UTF_8);
            return f;
        } catch (IOException e) {
            throw ApiException.badRequest("写样式文件失败：" + e.getMessage());
        }
    }

    // ------------------------------------------------------------------ 预览
    /** 调 worker 的 subtitle.py preview 出一张预览图；给了场次和片段就用真实画面做背景。 */
    public byte[] preview(Map<String, Object> config, String session, String clip, Double at) {
        Map<String, Object> cfg = validate(config);
        Path dir = paths.previewDir();
        String id = UUID.randomUUID().toString().replace("-", "");
        Path json = dir.resolve(id + ".json");
        Path png = dir.resolve(id + ".png");
        try {
            Files.createDirectories(dir);
            Files.writeString(json, write(cfg), StandardCharsets.UTF_8);
            List<String> args = new ArrayList<>(List.of("preview", "--style-json", json.toString(), "--name", id,
                    "--out", dir.toString()));
            if (session != null && clip != null && !clip.isBlank()) {
                args.addAll(List.of("--video", paths.clip(session, clip).toString(),
                        "--at", String.valueOf(at == null ? 5.0 : Math.max(0, at))));
            }
            ProcessRunner.Result r = runner.run("subtitle.py", args, Duration.ofSeconds(60));
            if (r.exitCode() != 0 || !Files.exists(png)) {
                throw ApiException.badRequest("预览失败：" + SessionService.lastLine(r.output()));
            }
            return Files.readAllBytes(png);
        } catch (IOException e) {
            throw ApiException.badRequest("预览失败：" + e.getMessage());
        } finally {
            try {
                Files.deleteIfExists(json);
                Files.deleteIfExists(png);
            } catch (IOException ignored) {
                // 临时文件删不掉不影响使用
            }
        }
    }

    // ------------------------------------------------------------------ 初始导入
    /** 第一次启动时把 config/subtitles.yaml 里的样式导入数据库，active 设为默认。 */
    @EventListener(ApplicationReadyEvent.class)
    public void importFromYaml() {
        if (mapper.selectCount(null) > 0) {
            return;
        }
        Path f = paths.configDir().resolve("subtitles.yaml");
        if (!Files.exists(f)) {
            log.warn("找不到 {}，跳过字幕样式导入", f);
            return;
        }
        try (Reader r = Files.newBufferedReader(f, StandardCharsets.UTF_8)) {
            Map<String, Object> y = new Yaml().load(r);
            Object active = y.get("active");
            Object styles = y.get("styles");
            if (!(styles instanceof Map<?, ?> sm)) {
                return;
            }
            for (Map.Entry<?, ?> e : sm.entrySet()) {
                String code = String.valueOf(e.getKey());
                Map<String, Object> raw = om.convertValue(e.getValue(), new TypeReference<Map<String, Object>>() {});
                try {
                    Map<String, Object> cfg = validate(raw);
                    SubtitleStyle s = new SubtitleStyle();
                    s.setCode(code);
                    s.setName(String.valueOf(cfg.get("name")));
                    s.setConfigJson(write(cfg));
                    s.setIsDefault(code.equals(String.valueOf(active)) ? 1 : 0);
                    s.setUpdatedAt(LocalDateTime.now());
                    mapper.insert(s);
                } catch (ApiException ex) {
                    log.warn("样式 {} 导入失败：{}", code, ex.getMessage());
                }
            }
            log.info("已从 {} 导入字幕样式", f);
            overrides.writeFile();
        } catch (IOException e) {
            log.warn("读取 {} 失败", f, e);
        }
    }

    // ------------------------------------------------------------------ 工具
    private Map<String, Object> toMap(SubtitleStyle s) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("id", s.getId());
        m.put("code", s.getCode());
        m.put("name", s.getName());
        m.put("isDefault", s.getIsDefault() != null && s.getIsDefault() == 1);
        m.put("config", read(s.getConfigJson()));
        m.put("updatedAt", s.getUpdatedAt());
        return m;
    }

    private Map<String, Object> read(String json) {
        try {
            return om.readValue(json, new TypeReference<Map<String, Object>>() {});
        } catch (IOException e) {
            return new LinkedHashMap<>();
        }
    }

    private String write(Object o) {
        try {
            return om.writeValueAsString(o);
        } catch (IOException e) {
            throw ApiException.badRequest("样式无法保存");
        }
    }
}
