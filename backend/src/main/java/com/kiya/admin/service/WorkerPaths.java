package com.kiya.admin.service;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.regex.Pattern;

import org.springframework.stereotype.Component;

import com.kiya.admin.common.ApiException;
import com.kiya.admin.config.KiyaProperties;
import com.kiya.admin.config.StreamerContext;

/** 仓库里各目录的位置，以及防止路径穿越的校验。所有来自请求的名字都必须经过这里。 */
@Component
public class WorkerPaths {
    private static final Pattern SESSION = Pattern.compile("^[0-9A-Za-z_\\-]{1,64}$");
    private static final Pattern CLIP = Pattern.compile("^[0-9]{2,3}_[0-9\\-]{1,16}\\.mp4$");

    private final KiyaProperties props;

    public WorkerPaths(KiyaProperties props) {
        this.props = props;
    }

    public Path repo() { return Path.of(props.getRepoDir()).toAbsolutePath().normalize(); }
    public Path worker() { return repo().resolve("worker"); }

    /** 当前管理的主播标识：优先当前请求选中的主播（session），否则用配置默认值。 */
    public String streamer() {
        String s = StreamerContext.get();
        if (s == null || s.isBlank()) {
            s = props.getStreamer();
        }
        s = s == null ? "" : s.trim().toLowerCase();
        if (!s.matches("^[a-z][a-z0-9_-]{0,31}$")) {
            throw new IllegalStateException("kiya.streamer 不合法：" + props.getStreamer()
                    + "（1–32 位小写字母开头，可含小写字母 / 数字 / _ 和 -）");
        }
        return s;
    }

    /** 主播的输出目录 worker/output/<id>。首位主播 kiya 兼容旧结构：output/kiya 还不存在、
     *  而 output/ 下直接放着旧的场次或 _memory 时，继续用 output/ 本身（和 worker/clipper/streamer.py 的规则一致）。 */
    public Path output() {
        String s = streamer();
        Path base = worker().resolve("output");
        Path p = base.resolve(s);
        if ("kiya".equals(s) && !Files.isDirectory(p) && isLegacyOutput(base)) {
            return base;
        }
        return p;
    }

    /** 主播的评测目录 eval/<id>；kiya 同样兼容旧的 eval/reviews 等。 */
    public Path evalDir() {
        String s = streamer();
        Path base = repo().resolve("eval");
        Path p = base.resolve(s);
        if ("kiya".equals(s) && !Files.isDirectory(p) && isLegacyEval(base)) {
            return base;
        }
        return p;
    }

    public Path reviews() { return evalDir().resolve("reviews"); }

    private static boolean isLegacyOutput(Path base) {
        if (!Files.isDirectory(base)) {
            return false;
        }
        try (java.util.stream.Stream<Path> dirs = Files.list(base)) {
            return dirs.filter(Files::isDirectory).anyMatch(d ->
                    "_memory".equals(d.getFileName().toString())
                            || Files.exists(d.resolve("candidates.json"))
                            || Files.exists(d.resolve("transcript.json")));
        } catch (java.io.IOException e) {
            return false;
        }
    }

    private static boolean isLegacyEval(Path base) {
        return Files.isDirectory(base.resolve("reviews"))
                || Files.exists(base.resolve("annotations.csv"))
                || Files.exists(base.resolve("results.csv"));
    }
    public Path configDir() { return repo().resolve("config"); }

    /** 主播专属运行时配置（admin_overrides.json / memory_decisions.json）：按主播隔离在 output/<id>/ 下；
     *  首位主播 kiya 兼容旧的 config/ 路径（旧文件存在时优先用旧路径，不用搬）。 */
    public Path streamerStateFile(String filename) {
        Path p = output().resolve(filename);
        if (!"kiya".equals(streamer()) || Files.exists(p)) {
            return p;
        }
        Path legacy = configDir().resolve(filename);
        return Files.exists(legacy) ? legacy : p;
    }

    public Path overridesFile() { return streamerStateFile("admin_overrides.json"); }
    public Path recDir() { return Path.of(props.getRecDir()).toAbsolutePath().normalize(); }
    public Path jobLogs() { return output().resolve("_jobs"); }
    public Path previewDir() { return output().resolve("_preview"); }

    public String python() {
        String p = props.getPython();
        return (p == null || p.isBlank()) ? worker().resolve(".venv/bin/python").toString() : p;
    }

    public String checkSession(String name) {
        if (name == null || !SESSION.matcher(name).matches()) {
            throw ApiException.badRequest("场次名不合法");
        }
        return name;
    }

    public Path sessionDir(String name) {
        return inside(output(), output().resolve(checkSession(name)));
    }

    public Path clip(String session, String file) {
        if (file == null || !CLIP.matcher(file).matches()) {
            throw ApiException.badRequest("文件名不合法");
        }
        return inside(output(), sessionDir(session).resolve("clips").resolve(file));
    }

    /** 确认 p 在 base 目录之内。 */
    public static Path inside(Path base, Path p) {
        Path b = base.toAbsolutePath().normalize();
        Path n = p.toAbsolutePath().normalize();
        if (!n.startsWith(b)) {
            throw ApiException.badRequest("路径不合法");
        }
        return n;
    }
}
