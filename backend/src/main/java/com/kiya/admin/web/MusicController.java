package com.kiya.admin.web;

import java.util.List;
import java.util.Map;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import com.kiya.admin.service.MusicService;
import com.kiya.admin.service.WorkerPaths;
import com.kiya.admin.common.ApiException;
import java.nio.file.Path;
import java.nio.file.Files;
import org.springframework.core.io.FileSystemResource;
import org.springframework.core.io.Resource;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;

/** 曲库：只读。登记曲子（授权说明必填）和鼓点检测在服务器上用 scripts/music.py 做。 */
@RestController
@RequestMapping("/api/music")
public class MusicController {
    private final MusicService music;
    private final WorkerPaths paths;

    public MusicController(MusicService music, WorkerPaths paths) {
        this.music = music;
        this.paths = paths;
    }

    @GetMapping
    public List<Map<String, Object>> list() {
        return music.list();
    }

    @GetMapping("/{id}/beats")
    public Map<String, Object> beats(@PathVariable String id) {
        return music.beats(id);
    }

    @GetMapping("/{id}/audio")
    public ResponseEntity<Resource> audio(@PathVariable String id) {
        Map<String, Object> track = music.list().stream().filter(t -> id.equals(t.get("id"))).findFirst()
                .orElseThrow(() -> ApiException.notFound("曲子不存在"));
        String env = System.getenv("KIYA_MUSIC");
        Path base = env == null || env.isBlank() ? paths.repo().resolve("assets/music") : Path.of(env).toAbsolutePath();
        Path file = WorkerPaths.inside(base, base.resolve(String.valueOf(track.get("file"))));
        if (!Files.isRegularFile(file)) throw ApiException.notFound("音频不存在");
        return ResponseEntity.ok().contentType(MediaType.APPLICATION_OCTET_STREAM).body(new FileSystemResource(file));
    }
}
