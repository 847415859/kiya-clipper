package com.kiya.admin.web;

import java.util.Map;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import com.kiya.admin.common.ApiException;
import com.kiya.admin.config.StreamerFilter;
import com.kiya.admin.service.StreamerService;
import com.kiya.admin.service.WorkerPaths;

import jakarta.servlet.http.HttpSession;

/** 主播选择器：顶部切换管理哪个主播。选中值存 session，StreamerFilter 每个请求读进 ThreadLocal。 */
@RestController
@RequestMapping("/api/streamers")
public class StreamerController {
    private final StreamerService service;
    private final WorkerPaths paths;

    public StreamerController(StreamerService service, WorkerPaths paths) {
        this.service = service;
        this.paths = paths;
    }

    @GetMapping
    public Map<String, Object> list() {
        return Map.of("current", paths.streamer(), "streamers", service.list());
    }

    @PostMapping("/current")
    public Map<String, Object> select(@RequestBody Map<String, String> body, HttpSession session) {
        String id = body == null ? null : body.get("streamer");
        if (id == null || !id.matches("^[a-z][a-z0-9_-]{0,31}$")) {
            throw ApiException.badRequest("主播标识不合法");
        }
        String norm = id.trim().toLowerCase();
        boolean exists = service.list().stream().anyMatch(m -> m.get("id").equals(norm));
        if (!exists) {
            throw ApiException.badRequest("没有这个主播：先复制 config/streamers/_template.yaml 为 config/streamers/"
                    + norm + ".yaml");
        }
        session.setAttribute(StreamerFilter.SESSION_KEY, norm);
        return Map.of("current", norm);
    }
}
