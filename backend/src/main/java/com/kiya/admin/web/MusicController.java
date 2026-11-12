package com.kiya.admin.web;

import java.util.List;
import java.util.Map;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import com.kiya.admin.service.MusicService;

/** 曲库：只读。登记曲子（授权说明必填）和鼓点检测在服务器上用 scripts/music.py 做。 */
@RestController
@RequestMapping("/api/music")
public class MusicController {
    private final MusicService music;

    public MusicController(MusicService music) {
        this.music = music;
    }

    @GetMapping
    public List<Map<String, Object>> list() {
        return music.list();
    }

    @GetMapping("/{id}/beats")
    public Map<String, Object> beats(@PathVariable String id) {
        return music.beats(id);
    }
}
