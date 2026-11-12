package com.kiya.admin.service;

import java.io.IOException;
import java.time.Duration;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import org.springframework.stereotype.Service;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.kiya.admin.common.ApiException;

/** 曲库（assets/music，由 worker/scripts/music.py 管理）：剪辑器里选背景音乐、时间轴上画鼓点刻度。曲库为空时返回空列表。 */
@Service
public class MusicService {
    private final ProcessRunner runner;
    private final ObjectMapper om;

    public MusicService(ProcessRunner runner, ObjectMapper om) {
        this.runner = runner;
        this.om = om;
    }

    public List<Map<String, Object>> list() {
        ProcessRunner.Result r = runner.run("music.py", List.of("list", "--json"), Duration.ofSeconds(30));
        if (r.exitCode() != 0) {
            throw ApiException.badRequest("读取曲库失败：" + SessionService.lastLine(r.output()));
        }
        try {
            return om.readValue(SessionService.lastLine(r.output()), new TypeReference<List<Map<String, Object>>>() {});
        } catch (IOException e) {
            throw ApiException.badRequest("曲库结果解析失败：" + e.getMessage());
        }
    }

    public Map<String, Object> beats(String id) {
        if (id == null || !id.matches("^[\\w\\-\\u4e00-\\u9fff]{1,60}$")) {
            throw ApiException.badRequest("曲子 id 不合法");
        }
        ProcessRunner.Result r = runner.run("music.py", List.of("beats", id), Duration.ofSeconds(30));
        if (r.exitCode() != 0) {
            throw ApiException.notFound(SessionService.lastLine(r.output()));
        }
        try {
            return om.readValue(SessionService.lastLine(r.output()), new TypeReference<LinkedHashMap<String, Object>>() {});
        } catch (IOException e) {
            throw ApiException.badRequest("鼓点结果解析失败：" + e.getMessage());
        }
    }
}
