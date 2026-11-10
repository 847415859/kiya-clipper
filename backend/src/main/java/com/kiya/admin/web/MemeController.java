package com.kiya.admin.web;

import java.util.Map;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import com.kiya.admin.service.MemeService;

/** 梗库：看每个梗的来源 / 热度 / 使用与保留；启用、停用、拉黑、手动添加走 /api/memory/actions（memeSave 等）。 */
@RestController
@RequestMapping("/api/memes")
public class MemeController {
    private final MemeService memes;

    public MemeController(MemeService memes) {
        this.memes = memes;
    }

    @GetMapping
    public Map<String, Object> get() {
        return memes.get();
    }
}
