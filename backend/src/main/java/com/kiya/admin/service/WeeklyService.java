package com.kiya.admin.service;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.time.LocalDate;
import java.time.format.DateTimeParseException;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import org.springframework.stereotype.Service;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.kiya.admin.common.ApiException;

/**
 * 主播周报：worker/scripts/weekly_report.py 统计（不调大模型，几秒），结果在 output/_reports/weekly-<截止日>.json / .html。
 * 后台页面看完整的（含给运营看的“数据够不够”提示），导出的 .html 只有适合主播看的内容。
 */
@Service
public class WeeklyService {
    private final WorkerPaths paths;
    private final ProcessRunner runner;
    private final ObjectMapper om;

    public WeeklyService(WorkerPaths paths, ProcessRunner runner, ObjectMapper om) {
        this.paths = paths;
        this.runner = runner;
        this.om = om;
    }

    private static LocalDate parseEnd(String end) {
        if (end == null || end.isBlank()) {
            return LocalDate.now();
        }
        try {
            return LocalDate.parse(end.strip());
        } catch (DateTimeParseException e) {
            throw ApiException.badRequest("日期格式应为 YYYY-MM-DD");
        }
    }

    private Path reportFile(LocalDate end, String ext) {
        return WorkerPaths.inside(paths.output(), paths.output().resolve("_reports").resolve("weekly-" + end + "." + ext));
    }

    /** 重新统计一遍并返回完整报告（数据都在本地，几秒；第一次可能要读各场的弹幕文件，给到 2 分钟）。 */
    public Map<String, Object> generate(String endStr, Integer days) {
        LocalDate end = parseEnd(endStr);
        if (days != null && (days < 1 || days > 31)) {
            throw ApiException.badRequest("统计天数 1–31");
        }
        List<String> args = new java.util.ArrayList<>(List.of("--end", end.toString(),
                "--reviews", paths.reviews().toString(), "--out", paths.output().toString()));
        if (days != null) {
            args.addAll(List.of("--days", String.valueOf(days)));
        }
        ProcessRunner.Result r = runner.run("weekly_report.py", args, Duration.ofSeconds(120));
        if (r.exitCode() != 0) {
            throw ApiException.badRequest(SessionService.lastLine(r.output()));
        }
        try {
            return om.readValue(reportFile(end, "json").toFile(), new TypeReference<LinkedHashMap<String, Object>>() {});
        } catch (IOException e) {
            throw ApiException.badRequest("周报结果读取失败：" + e.getMessage());
        }
    }

    /** 导出给主播的文档（.html，浏览器打开即可，也能直接打印成 PDF）。 */
    public Path export(String endStr, Integer days) {
        generate(endStr, days);
        Path f = reportFile(parseEnd(endStr), "html");
        if (!Files.exists(f)) {
            throw ApiException.notFound("还没有生成周报");
        }
        return f;
    }

    public String exportName(String endStr) {
        return "kiya-weekly-" + parseEnd(endStr) + ".html";
    }
}
