package com.kiya.admin.service;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.stream.Stream;

import org.springframework.stereotype.Service;

/** 主播列表：扫描 config/streamers/<id>.yaml 和 config/<id>.yaml，供后台顶部选择器切换。 */
@Service
public class StreamerService {
    private static final Pattern NAME = Pattern.compile("(?m)^\\s*name:\\s*(.+?)\\s*$");
    private static final Pattern ID = Pattern.compile("^[a-z][a-z0-9_-]{0,31}$");

    private final WorkerPaths paths;

    public StreamerService(WorkerPaths paths) {
        this.paths = paths;
    }

    /** 返回主播列表（id + 显示名），当前选中的主播排最前。 */
    public List<Map<String, String>> list() {
        Map<String, String> byId = new LinkedHashMap<>();
        Path streamers = paths.configDir().resolve("streamers");
        if (Files.isDirectory(streamers)) {
            try (Stream<Path> s = Files.list(streamers)) {
                s.filter(p -> p.getFileName().toString().endsWith(".yaml"))
                        .filter(p -> !p.getFileName().toString().equals("_template.yaml"))
                        .forEach(p -> add(byId, p));
            } catch (IOException ignored) {
                // 读不到目录就返回空列表
            }
        }
        try (Stream<Path> s = Files.list(paths.configDir())) {
            s.filter(p -> p.getFileName().toString().endsWith(".yaml"))
                    .filter(p -> !p.getFileName().toString().equals("subtitles.yaml"))
                    .filter(p -> !p.getFileName().toString().equals("_template.yaml"))
                    .forEach(p -> add(byId, p));
        } catch (IOException ignored) {
            // 同上
        }
        List<Map<String, String>> out = new ArrayList<>();
        String current = paths.streamer();
        for (Map.Entry<String, String> e : byId.entrySet()) {
            out.add(Map.of("id", e.getKey(), "name", e.getValue()));
        }
        out.sort(Comparator.comparing((Map<String, String> m) -> !m.get("id").equals(current))
                .thenComparing(m -> m.get("id")));
        return out;
    }

    private void add(Map<String, String> byId, Path p) {
        String id = p.getFileName().toString().replaceFirst("\\.yaml$", "");
        if (!ID.matcher(id).matches()) {
            return;
        }
        byId.putIfAbsent(id, readName(p));
    }

    /** 从主播配置里读 streamer.name（正则提取，避免引入 yaml 解析）；读不到就用主播标识。 */
    private String readName(Path p) {
        try {
            String text = Files.readString(p);
            Matcher m = NAME.matcher(text);
            if (m.find()) {
                return m.group(1);
            }
        } catch (IOException ignored) {
            // 读不到就用 id
        }
        return null;
    }
}
