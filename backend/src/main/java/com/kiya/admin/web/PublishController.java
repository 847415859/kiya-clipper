package com.kiya.admin.web;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;

import org.springframework.core.io.FileSystemResource;
import org.springframework.core.io.Resource;
import org.springframework.http.CacheControl;
import org.springframework.http.ContentDisposition;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import com.kiya.admin.common.ApiException;
import com.kiya.admin.service.PublishService;

/** 发布包：封面、标题、简介、标签（可复制粘贴手动投稿，也可以一键发布，一键发布走 PUBLISH_BILI 任务）。 */
@RestController
@RequestMapping("/api/sessions/{name}/publish/{rank}")
public class PublishController {
    private final PublishService publish;

    public PublishController(PublishService publish) {
        this.publish = publish;
    }

    @GetMapping
    public Map<String, Object> get(@PathVariable String name, @PathVariable int rank) {
        return publish.get(name, rank);
    }

    public record SaveReq(String title, String description, List<String> tags) {}

    @PutMapping
    public Map<String, Object> save(@PathVariable String name, @PathVariable int rank, @RequestBody SaveReq req) {
        return publish.save(name, rank, req.title(), req.description(), req.tags());
    }

    public record CoverReq(Double t, String text, String variant, String theme, String mode) {}

    @PostMapping("/cover")
    public Map<String, Object> cover(@PathVariable String name, @PathVariable int rank, @RequestBody CoverReq req) {
        return publish.cover(name, rank, req.t(), req.text(), req.variant(), req.theme(), req.mode());
    }

    @PostMapping("/regenerate")
    public Map<String, Object> regenerate(@PathVariable String name, @PathVariable int rank) {
        return publish.regenerate(name, rank);
    }

    /** 一键发布要用的信息：投稿环境 + 最近一次结果。发布本身走任务（PUBLISH_BILI）。 */
    @GetMapping("/bili")
    public Map<String, Object> bili(@PathVariable String name, @PathVariable int rank) {
        return publish.biliInfo(name, rank);
    }

    @GetMapping("/cover.jpg")
    public ResponseEntity<Resource> coverImage(@PathVariable String name, @PathVariable int rank,
                                               @RequestParam(required = false) String variant) {
        Path p = publish.coverFile(name, rank, variant);
        if (!Files.exists(p)) {
            throw ApiException.notFound("还没有封面");
        }
        return ResponseEntity.ok().contentType(MediaType.IMAGE_JPEG).cacheControl(CacheControl.noCache())
                .header(HttpHeaders.CONTENT_DISPOSITION, ContentDisposition.inline()
                        .filename(String.format("%s_%02d_cover.jpg", name, rank)).build().toString())
                .body(new FileSystemResource(p));
    }
}
