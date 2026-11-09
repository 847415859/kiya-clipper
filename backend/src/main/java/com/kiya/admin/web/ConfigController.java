package com.kiya.admin.web;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import com.kiya.admin.common.Csv;
import com.kiya.admin.service.OverridesService;
import com.kiya.admin.service.WorkerPaths;

@RestController
@RequestMapping("/api")
public class ConfigController {
    private final OverridesService overrides;
    private final WorkerPaths paths;

    public ConfigController(OverridesService overrides, WorkerPaths paths) {
        this.overrides = overrides;
        this.paths = paths;
    }

    @GetMapping("/config/words")
    public Map<String, Object> words() {
        return overrides.words();
    }

    public record WordsReq(List<String> hotwords, Map<String, String> corrections) {}

    @PutMapping("/config/words")
    public Map<String, Object> saveWords(@RequestBody WordsReq req) {
        return overrides.saveWords(req.hotwords(), req.corrections());
    }

    /** 热词表最终会提交的词（hotwords.py show 的输出）。 */
    @GetMapping("/config/hotwords-preview")
    public Map<String, Object> hotwordsPreview() {
        return Map.of("text", overrides.hotwordsPreview());
    }

    /** eval/results.csv：每次 evaluate.py --log 记录的结果。 */
    @GetMapping("/eval/results")
    public List<Map<String, String>> evalResults() throws IOException {
        Path f = paths.evalDir().resolve("results.csv");
        return Files.exists(f) ? Csv.readMaps(f) : List.of();
    }
}
