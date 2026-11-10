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
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;

import com.kiya.admin.common.ApiException;
import com.kiya.admin.service.EditService;
import com.kiya.admin.service.StickerService;

/** 成片编辑：剪辑脚本、贴图库、渲染结果。渲染本身走任务队列（任务类型 RENDER）。 */
@RestController
public class EditController {
    private final EditService edits;
    private final StickerService stickers;

    public EditController(EditService edits, StickerService stickers) {
        this.edits = edits;
        this.stickers = stickers;
    }

    @GetMapping("/api/sessions/{name}/edits/{rank}")
    public Map<String, Object> get(@PathVariable String name, @PathVariable int rank) {
        return edits.get(name, rank);
    }

    public record SaveReq(Map<String, Object> edit, Long styleId, Boolean restoredAi) {}

    @PutMapping("/api/sessions/{name}/edits/{rank}")
    public Map<String, Object> save(@PathVariable String name, @PathVariable int rank, @RequestBody SaveReq req) {
        return edits.save(name, rank, req.edit(), req.styleId(), Boolean.TRUE.equals(req.restoredAi()));
    }

    public record VariantReq(String key) {}

    /** 多版本初稿：选用某一版（克制 / 标准 / 热闹），替换当前剪辑脚本。 */
    @PostMapping("/api/sessions/{name}/edits/{rank}/variant")
    public Map<String, Object> variant(@PathVariable String name, @PathVariable int rank, @RequestBody VariantReq req) {
        return edits.chooseVariant(name, rank, req.key());
    }

    @GetMapping("/api/sessions/{name}/finals")
    public List<Map<String, Object>> finals(@PathVariable String name) {
        return edits.finals(name, null);
    }

    /** 成片编辑器的预览视频（素材范围超出候选片段时，edit_preview.py 剪的 480p）。 */
    @GetMapping("/api/sessions/{name}/edit-previews/{file}")
    public ResponseEntity<Resource> previewVideo(@PathVariable String name, @PathVariable String file) {
        Path p = edits.previewFile(name, file);
        if (!Files.exists(p)) {
            throw ApiException.notFound("预览视频不存在");
        }
        return ResponseEntity.ok().contentType(MediaType.parseMediaType("video/mp4"))
                .cacheControl(CacheControl.noCache()).body(new FileSystemResource(p));
    }

    @GetMapping("/api/sessions/{name}/finals/{file}")
    public ResponseEntity<Resource> finalVideo(@PathVariable String name, @PathVariable String file) {
        Path p = edits.finalFile(name, file);
        if (!Files.exists(p)) {
            throw ApiException.notFound("成片不存在");
        }
        return ResponseEntity.ok().contentType(MediaType.parseMediaType("video/mp4"))
                .cacheControl(CacheControl.noCache()).body(new FileSystemResource(p));
    }

    @GetMapping("/api/stickers")
    public List<Map<String, Object>> stickers() {
        return stickers.list();
    }

    @PostMapping("/api/stickers")
    public Map<String, Object> upload(@RequestParam("file") MultipartFile file) {
        return stickers.upload(file);
    }

    @DeleteMapping("/api/stickers/{name}")
    public Map<String, Object> deleteSticker(@PathVariable String name) {
        stickers.delete(name);
        return Map.of("ok", true);
    }

    @GetMapping("/api/stickers/{name}")
    public ResponseEntity<Resource> sticker(@PathVariable String name) {
        Path p = stickers.file(name);
        if (!Files.exists(p)) {
            throw ApiException.notFound("贴图不存在");
        }
        return ResponseEntity.ok().contentType(MediaType.parseMediaType(StickerService.contentType(name)))
                .cacheControl(CacheControl.maxAge(java.time.Duration.ofDays(1))).body(new FileSystemResource(p));
    }
}
