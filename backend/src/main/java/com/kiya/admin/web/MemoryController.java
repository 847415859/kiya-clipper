package com.kiya.admin.web;

import java.util.Map;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import com.kiya.admin.service.MemoryService;

/** 精排记忆：查看自动积累的梗 / 例子 / 投稿 / 权重，以及后台的取舍决定。 */
@RestController
@RequestMapping("/api/memory")
public class MemoryController {
    private final MemoryService memory;

    public MemoryController(MemoryService memory) {
        this.memory = memory;
    }

    @GetMapping
    public Map<String, Object> get() {
        return memory.get();
    }

    @PostMapping("/actions")
    public Map<String, Object> act(@RequestBody Map<String, Object> req) {
        return memory.act(req);
    }
}
