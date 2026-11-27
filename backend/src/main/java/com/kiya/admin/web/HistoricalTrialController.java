package com.kiya.admin.web;

import java.io.IOException;
import java.util.Map;
import org.springframework.core.io.FileSystemResource;
import org.springframework.core.io.Resource;
import org.springframework.http.CacheControl;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.*;
import com.kiya.admin.service.HistoricalTrialService;

@RestController
@RequestMapping("/api/sessions")
public class HistoricalTrialController {
    private final HistoricalTrialService service;
    public HistoricalTrialController(HistoricalTrialService service) { this.service = service; }
    @GetMapping("/{name}/isolated-trials/options")
    public Map<String, Object> options(@PathVariable String name) { return service.options(name); }
    @GetMapping("/isolated-trials")
    public Map<String, Object> list() { return Map.of("tasks", service.list()); }
    public record Submit(int rank, boolean confirmation, String rerunTaskId) {}
    @PostMapping("/{name}/isolated-trials")
    public Map<String, Object> submit(@PathVariable String name, @RequestBody Submit body, Authentication authentication) throws IOException {
        if (authentication == null || authentication.getAuthorities().stream().noneMatch(a -> "ROLE_ADMIN".equals(a.getAuthority())))
            throw new AccessDeniedException("管理员权限不足");
        return service.submit(name, body.rank(), body.confirmation(), body.rerunTaskId());
    }
    @GetMapping("/{name}/isolated-trials/{taskId}")
    public Map<String, Object> detail(@PathVariable String name, @PathVariable String taskId) throws IOException { return service.detail(name, taskId); }
    @GetMapping({"/{name}/isolated-trials/{taskId}/media/{rank}", "/{name}/isolated-trials/{taskId}/media/{rank}/version/{version}"})
    public ResponseEntity<Resource> media(@PathVariable String name, @PathVariable String taskId, @PathVariable int rank,
                                        @PathVariable(required = false) String version) throws IOException {
        return ResponseEntity.ok().cacheControl(CacheControl.noStore()).contentType(MediaType.parseMediaType("video/mp4"))
                .body(new FileSystemResource(service.media(name, taskId, rank, version)));
    }
}
