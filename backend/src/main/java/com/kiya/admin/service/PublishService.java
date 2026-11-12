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
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;

import org.springframework.stereotype.Service;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.kiya.admin.common.ApiException;

/**
 * 发布包：output/<场次>/publish/<序号>.json + <序号>_cover.jpg，由 worker/scripts/publish_pack.py 生成。
 * 标题 / 简介 / 标签在后台改了直接写回 JSON（publish_pack.py 不会覆盖改过的）；封面每次调用脚本重画。
 */
@Service
public class PublishService {
    private final WorkerPaths paths;
    private final ProcessRunner runner;
    private final ObjectMapper om;

    public PublishService(WorkerPaths paths, ProcessRunner runner, ObjectMapper om) {
        this.paths = paths;
        this.runner = runner;
        this.om = om;
    }

    private Path packFile(String session, int rank) {
        checkRank(rank);
        return paths.sessionDir(session).resolve("publish").resolve(String.format("%02d.json", rank));
    }

    private static void checkRank(int rank) {
        if (rank < 1 || rank > 999) {
            throw ApiException.badRequest("序号不合法");
        }
    }

    private Map<String, Object> runPack(String session, int rank, List<String> extra, int timeoutSec) {
        List<String> args = new ArrayList<>(List.of(paths.sessionDir(session).resolve("candidates.json").toString(),
                "--rank", String.valueOf(rank)));
        args.addAll(extra);
        ProcessRunner.Result r = runner.run("publish_pack.py", args, Duration.ofSeconds(timeoutSec));
        if (r.exitCode() != 0) {
            throw ApiException.badRequest(SessionService.lastLine(r.output()));
        }
        try {
            return om.readValue(SessionService.lastLine(r.output()), new TypeReference<LinkedHashMap<String, Object>>() {});
        } catch (IOException e) {
            throw ApiException.badRequest("发布包结果解析失败：" + e.getMessage());
        }
    }

    /** 读发布包；还没有就先生成文字部分（不截封面，快）。 */
    public Map<String, Object> get(String session, int rank) {
        Path f = packFile(session, rank);
        if (!Files.exists(f)) {
            return runPack(session, rank, List.of("--no-cover"), 60);
        }
        try {
            return om.readValue(f.toFile(), new TypeReference<LinkedHashMap<String, Object>>() {});
        } catch (IOException e) {
            throw ApiException.badRequest("发布包文件损坏：" + e.getMessage());
        }
    }

    /** 生成 / 重画封面。t = 整场秒数（null 用上次的或笑点），text = 封面大字（null 用上次的）。 */
    public Map<String, Object> cover(String session, int rank, Double t, String text) {
        List<String> extra = new ArrayList<>();
        if (t != null) {
            if (t < 0 || t > 24 * 3600) {
                throw ApiException.badRequest("时间不合法");
            }
            extra.addAll(List.of("--cover-t", String.format(Locale.ROOT, "%.2f", t)));
        }
        if (text != null) {
            String s = text.strip();
            if (s.length() > 30) {
                throw ApiException.badRequest("封面文字最多 30 个字");
            }
            extra.add("--cover-text=" + s);                 // 用 = 连写，文字以 - 开头时也不会被当成参数
        }
        return runPack(session, rank, extra, 120);
    }

    /** 标题 / 简介 / 标签重新按精排结果生成（覆盖改过的）。 */
    public Map<String, Object> regenerate(String session, int rank) {
        return runPack(session, rank, List.of("--refresh", "--no-cover"), 60);
    }

    public synchronized Map<String, Object> save(String session, int rank, String title, String description, List<String> tags) {
        Map<String, Object> pack = new LinkedHashMap<>(get(session, rank));
        if (title != null) {
            String t = title.strip();
            if (t.isEmpty() || t.length() > 80) {
                throw ApiException.badRequest("标题 1–80 个字");
            }
            pack.put("title", t);
        }
        if (description != null) {
            if (description.length() > 2000) {
                throw ApiException.badRequest("简介最多 2000 个字");
            }
            pack.put("description", description.strip());
        }
        if (tags != null) {
            LinkedHashSet<String> clean = new LinkedHashSet<>();
            for (String s : tags) {
                String x = s == null ? "" : s.replaceAll("[#,，\\s]", "");
                if (x.length() > 20) {
                    throw ApiException.badRequest("单个标签最多 20 个字：" + x);
                }
                if (!x.isEmpty()) {
                    clean.add(x);
                }
            }
            if (clean.size() > 10) {
                throw ApiException.badRequest("标签最多 10 个");
            }
            pack.put("tags", new ArrayList<>(clean));
        }
        pack.put("updated_at", LocalDateTime.now().toString());
        Path f = packFile(session, rank);
        Path tmp = f.resolveSibling(f.getFileName() + ".tmp");
        try {
            Files.createDirectories(f.getParent());
            Files.writeString(tmp, om.writerWithDefaultPrettyPrinter().writeValueAsString(pack), StandardCharsets.UTF_8);
            Files.move(tmp, f, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE);
        } catch (IOException e) {
            throw ApiException.badRequest("保存失败：" + e.getMessage());
        }
        return pack;
    }

    /** B站投稿环境（biliup 装了没 / 登录了没）+ 这一条最近一次投稿的结果。 */
    public Map<String, Object> biliInfo(String session, int rank) {
        Map<String, Object> out = new LinkedHashMap<>();
        ProcessRunner.Result r = runner.run("publish_bili.py",
                List.of("--status", paths.sessionDir(session).resolve("candidates.json").toString()), Duration.ofSeconds(30));
        try {
            out.put("env", om.readValue(SessionService.lastLine(r.output()), new TypeReference<LinkedHashMap<String, Object>>() {}));
        } catch (IOException | RuntimeException e) {
            out.put("env", Map.of("biliup", false, "logged_in", false, "error", "读取投稿环境失败"));
        }
        Path f = paths.sessionDir(session).resolve("publish").resolve(String.format("%02d.bili.json", checkedRank(rank)));
        if (Files.exists(f)) {
            try {
                out.put("last", om.readValue(f.toFile(), new TypeReference<LinkedHashMap<String, Object>>() {}));
            } catch (IOException e) {
                // 结果文件读不到不影响发布
            }
        }
        return out;
    }

    private static int checkedRank(int rank) {
        checkRank(rank);
        return rank;
    }

    public Path coverFile(String session, int rank) {
        checkRank(rank);
        return WorkerPaths.inside(paths.output(),
                paths.sessionDir(session).resolve("publish").resolve(String.format("%02d_cover.jpg", rank)));
    }
}
