package com.kiya.admin.web;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;

import org.springframework.core.io.FileSystemResource;
import org.springframework.core.io.Resource;
import org.springframework.http.CacheControl;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import com.kiya.admin.common.ApiException;
import com.kiya.admin.service.SessionService;
import com.kiya.admin.service.WorkerPaths;

@RestController
@RequestMapping("/api/sessions")
public class SessionController {
    private final SessionService sessions;
    private final WorkerPaths paths;

    public SessionController(SessionService sessions, WorkerPaths paths) {
        this.sessions = sessions;
        this.paths = paths;
    }

    @GetMapping
    public List<Map<String, Object>> list() {
        return sessions.list();
    }

    @PostMapping("/scan")
    public Map<String, Object> scan() {
        return Map.of("count", sessions.scan());
    }

    @GetMapping("/{name}/candidates")
    public Map<String, Object> candidates(@PathVariable String name) {
        return sessions.candidates(name);
    }

    public record MarkReq(String mark, String note, List<String> reasons, String mode, Long ms) {}

    @PutMapping("/{name}/candidates/{rank}/mark")
    public Map<String, Object> mark(@PathVariable String name, @PathVariable int rank, @RequestBody MarkReq req) {
        sessions.mark(name, rank, req.mark(), req.note(), req.reasons(), req.mode(), req.ms());
        return Map.of("ok", true);
    }

    /** 片段视频：返回 Resource 时 Spring 会自动处理 Range 请求（视频拖动进度条需要）。 */
    @GetMapping("/{name}/clips/{file}")
    public ResponseEntity<Resource> clip(@PathVariable String name, @PathVariable String file) {
        Path p = paths.clip(name, file);
        if (!Files.exists(p)) {
            throw ApiException.notFound("片段不存在");
        }
        return ResponseEntity.ok().contentType(MediaType.parseMediaType("video/mp4"))
                .cacheControl(CacheControl.noCache()).body(new FileSystemResource(p));
    }

    @GetMapping("/{name}/curve")
    public ResponseEntity<Resource> curve(@PathVariable String name) {
        Path p = paths.sessionDir(name).resolve("curve.png");
        if (!Files.exists(p)) {
            throw ApiException.notFound("还没有信号曲线");
        }
        return ResponseEntity.ok().contentType(MediaType.IMAGE_PNG).cacheControl(CacheControl.noCache())
                .body(new FileSystemResource(p));
    }

    @GetMapping("/{name}/transcript")
    public ResponseEntity<Resource> transcript(@PathVariable String name) {
        Path p = paths.sessionDir(name).resolve("transcript.txt");
        if (!Files.exists(p)) {
            throw ApiException.notFound("这场还没有转写");
        }
        return ResponseEntity.ok().contentType(new MediaType("text", "plain", java.nio.charset.StandardCharsets.UTF_8))
                .body(new FileSystemResource(p));
    }
}
