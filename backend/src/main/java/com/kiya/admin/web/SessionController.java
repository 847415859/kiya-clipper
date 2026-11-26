package com.kiya.admin.web;

import java.io.IOException;
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
import com.kiya.admin.service.OperationsQueueService;
import com.kiya.admin.service.SessionService;
import com.kiya.admin.service.WorkerPaths;

@RestController
@RequestMapping("/api/sessions")
public class SessionController {
    private final SessionService sessions;
    private final WorkerPaths paths;
    private final OperationsQueueService operations;

    public SessionController(SessionService sessions, WorkerPaths paths, OperationsQueueService operations) {
        this.sessions = sessions;
        this.paths = paths;
        this.operations = operations;
    }

    @GetMapping
    public List<Map<String, Object>> list() {
        return sessions.list();
    }

    @GetMapping("/{name}/operations")
    public Map<String, Object> operations(@PathVariable String name) {
        return operations.summary(name);
    }

    @GetMapping("/acceptance-operations")
    public Map<String, Object> acceptanceOverview() {
        return operations.acceptanceOverview();
    }

    @GetMapping("/production-acceptance")
    public Map<String, Object> productionAcceptanceOverview() {
        return operations.productionOverview();
    }

    @GetMapping("/{name}/production-acceptance")
    public Map<String, Object> productionAcceptanceDetail(@PathVariable String name) {
        return operations.readOnlySummary(name, false);
    }

    @GetMapping("/{name}/acceptance-operations")
    public Map<String, Object> acceptanceOperations(@PathVariable String name) {
        return operations.acceptanceSummary(name);
    }

    @PostMapping("/{name}/operations/{action:enqueue|resume}")
    public Map<String, Object> recover(@PathVariable String name, @PathVariable String action) throws IOException {
        return operations.recover(name, action);
    }

    @GetMapping("/{name}/operations/media/{rank}")
    public ResponseEntity<Resource> operationsMedia(@PathVariable String name, @PathVariable int rank) {
        return video(operations.media(name, rank));
    }

    @GetMapping("/{name}/acceptance-operations/media/{rank}")
    public ResponseEntity<Resource> acceptanceMedia(@PathVariable String name, @PathVariable int rank) {
        return video(operations.acceptanceMedia(name, rank));
    }

    private ResponseEntity<Resource> video(Path media) {
        return ResponseEntity.ok().cacheControl(CacheControl.noStore())
                .contentType(MediaType.parseMediaType("video/mp4"))
                .body(new FileSystemResource(media));
    }

    @PostMapping("/scan")
    public Map<String, Object> scan() {
        return Map.of("count", sessions.scan());
    }

    @GetMapping("/{name}/candidates")
    public Map<String, Object> candidates(@PathVariable String name) {
        return sessions.candidates(name);
    }

    @GetMapping("/{name}/value-strategy")
    public Map<String, Object> valueStrategy(@PathVariable String name) {
        return sessions.valueStrategy(name);
    }

    public record MarkReq(String mark, String note, List<String> reasons, String mode, Long ms, Integer rating) {}

    @PutMapping("/{name}/candidates/{rank}/mark")
    public Map<String, Object> mark(@PathVariable String name, @PathVariable int rank, @RequestBody MarkReq req) {
        sessions.mark(name, rank, req.mark(), req.note(), req.reasons(), req.mode(), req.ms(), req.rating());
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
