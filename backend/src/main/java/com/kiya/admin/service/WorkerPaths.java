package com.kiya.admin.service;

import java.nio.file.Path;
import java.util.regex.Pattern;

import org.springframework.stereotype.Component;

import com.kiya.admin.common.ApiException;
import com.kiya.admin.config.KiyaProperties;

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
    public Path output() { return worker().resolve("output"); }
    public Path reviews() { return repo().resolve("eval").resolve("reviews"); }
    public Path evalDir() { return repo().resolve("eval"); }
    public Path configDir() { return repo().resolve("config"); }
    public Path overridesFile() { return configDir().resolve("admin_overrides.json"); }
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
