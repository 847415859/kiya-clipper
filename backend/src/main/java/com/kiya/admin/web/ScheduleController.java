package com.kiya.admin.web;

import java.nio.file.Files;
import java.util.LinkedHashMap;
import java.util.Map;
import org.springframework.core.io.FileSystemResource;
import org.springframework.core.io.Resource;
import org.springframework.http.CacheControl;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.multipart.MultipartFile;
import com.kiya.admin.common.ApiException;
import com.kiya.admin.service.ScheduleService;

@RestController
@RequestMapping("/api/schedules")
public class ScheduleController {
    private final ScheduleService service;
    public ScheduleController(ScheduleService service) { this.service = service; }
    @GetMapping("/calendar") public Map<String, Object> calendar() { return service.calendar(); }
    @GetMapping("/{week}/posters") public Map<String,Object> posters(@PathVariable String week) { return service.posterHistory(week); }
    public record PosterReq(String source, String supplement, String expectedRevision) {}
    @PostMapping("/{week}/posters") public Map<String,Object> generatePoster(@PathVariable String week,@RequestBody PosterReq req) {
        if(req.expectedRevision()==null) throw ApiException.badRequest("请先读取并保存日程");
        return service.generatePoster(week,Map.of("source",req.source()==null?"published":req.source(),"supplement",req.supplement()==null?"":req.supplement(),"expected_revision",req.expectedRevision()));
    }
    @GetMapping("/{week}/posters/{id}.png") public ResponseEntity<Resource> poster(@PathVariable String week,@PathVariable String id,
            @RequestParam(defaultValue="false") boolean download) {
        var file=service.posterFile(week,id);
        if(!Files.isRegularFile(file)) throw ApiException.notFound("周计划图片不存在");
        return ResponseEntity.ok().contentType(MediaType.IMAGE_PNG).cacheControl(CacheControl.noCache())
            .header("Content-Disposition",(download?"attachment":"inline")+"; filename=\"schedule-"+week+"-"+id+".png\"")
            .body(new FileSystemResource(file));
    }
    @GetMapping public Map<String, Object> get(@RequestParam(required=false) String weekStart) { return service.get(weekStart); }
    public record SaveReq(Map<String, Object> plan, String expectedRevision, Boolean confirm) {}
    @PutMapping("/{week}") public Map<String, Object> save(@PathVariable String week, @RequestBody SaveReq req) {
        return service.save(week, req.plan(), req.expectedRevision(), Boolean.TRUE.equals(req.confirm()));
    }
    public record RevisionReq(String expectedRevision) {}
    @PostMapping("/{week}/deactivate") public Map<String, Object> deactivate(@PathVariable String week, @RequestBody RevisionReq req) {
        return service.deactivate(week, req.expectedRevision());
    }
    public record PreviewReq(Boolean enabled, String at, String description) {}
    @PostMapping("/preview") public Map<String, Object> preview(@RequestBody PreviewReq req) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("enabled", req.enabled() == null || req.enabled()); body.put("at", req.at()); body.put("description", req.description());
        return service.preview(body);
    }
    @PostMapping("/images") public Map<String, Object> upload(@RequestParam("file") MultipartFile file) { return service.upload(file); }
    @GetMapping("/images/{id}") public ResponseEntity<Resource> image(@PathVariable String id) {
        var file = service.imageFile(id);
        if (!Files.isRegularFile(file)) throw ApiException.notFound("周表图片不存在");
        return ResponseEntity.ok().contentType(MediaType.IMAGE_JPEG).cacheControl(CacheControl.noCache()).body(new FileSystemResource(file));
    }
    public record ExtractReq(String imageId) {}
    @PostMapping("/{week}/extract") public Map<String, Object> extract(@PathVariable String week, @RequestBody ExtractReq req) {
        return service.extract(week, req.imageId());
    }
}
