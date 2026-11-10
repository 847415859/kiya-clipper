package com.kiya.admin.web;

import java.nio.file.Path;
import java.util.Map;

import org.springframework.core.io.FileSystemResource;
import org.springframework.core.io.Resource;
import org.springframework.http.CacheControl;
import org.springframework.http.ContentDisposition;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import com.kiya.admin.service.WeeklyService;

/** 主播周报：看（含运营提示）和一键导出（只含适合主播看的内容）。 */
@RestController
@RequestMapping("/api/weekly")
public class WeeklyController {
    private final WeeklyService weekly;

    public WeeklyController(WeeklyService weekly) {
        this.weekly = weekly;
    }

    @GetMapping
    public Map<String, Object> get(@RequestParam(required = false) String end, @RequestParam(required = false) Integer days) {
        return weekly.generate(end, days);
    }

    @GetMapping("/export")
    public ResponseEntity<Resource> export(@RequestParam(required = false) String end, @RequestParam(required = false) Integer days) {
        Path f = weekly.export(end, days);
        return ResponseEntity.ok().contentType(MediaType.TEXT_HTML).cacheControl(CacheControl.noCache())
                .header(HttpHeaders.CONTENT_DISPOSITION, ContentDisposition.attachment().filename(weekly.exportName(end)).build().toString())
                .body(new FileSystemResource(f));
    }
}
