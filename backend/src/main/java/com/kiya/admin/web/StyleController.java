package com.kiya.admin.web;

import java.util.List;
import java.util.Map;

import org.springframework.http.CacheControl;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import com.fasterxml.jackson.databind.JsonNode;
import com.kiya.admin.service.StyleService;

@RestController
@RequestMapping("/api/styles")
public class StyleController {
    private final StyleService styles;

    public StyleController(StyleService styles) {
        this.styles = styles;
    }

    /** 字段定义（前端按它生成表单）。 */
    @GetMapping("/schema")
    public JsonNode schema() {
        return styles.schema();
    }

    @GetMapping
    public List<Map<String, Object>> list() {
        return styles.list();
    }

    public record SaveReq(String code, Map<String, Object> config) {}

    @PostMapping
    public Map<String, Object> create(@RequestBody SaveReq req) {
        return styles.save(null, req.code(), req.config());
    }

    @PutMapping("/{id}")
    public Map<String, Object> update(@PathVariable long id, @RequestBody SaveReq req) {
        return styles.save(id, null, req.config());
    }

    @DeleteMapping("/{id}")
    public Map<String, Object> delete(@PathVariable long id) {
        styles.delete(id);
        return Map.of("ok", true);
    }

    @PostMapping("/{id}/default")
    public Map<String, Object> setDefault(@PathVariable long id) {
        styles.setDefault(id);
        return Map.of("ok", true);
    }

    public record PreviewReq(Map<String, Object> config, String session, String clip, Double at) {}

    /** 用编辑中的样式出一张预览图（不用先保存）。 */
    @PostMapping("/preview")
    public ResponseEntity<byte[]> preview(@RequestBody PreviewReq req) {
        byte[] png = styles.preview(req.config(), req.session(), req.clip(), req.at());
        return ResponseEntity.ok().contentType(MediaType.IMAGE_PNG).cacheControl(CacheControl.noStore()).body(png);
    }
}
