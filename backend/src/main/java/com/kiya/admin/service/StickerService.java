package com.kiya.admin.service;

import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.regex.Pattern;
import java.util.stream.Stream;

import org.springframework.stereotype.Service;
import org.springframework.web.multipart.MultipartFile;

import com.kiya.admin.common.ApiException;

/** 贴图库：<repo>/assets/stickers/（不进 git）。只收 png / jpg / gif / webp，按文件头校验真实格式。 */
@Service
public class StickerService {
    static final Pattern NAME = Pattern.compile("^[\\w\\-.\\u4e00-\\u9fff]{1,80}\\.(png|jpg|jpeg|gif|webp)$",
            Pattern.CASE_INSENSITIVE | Pattern.UNICODE_CHARACTER_CLASS);
    static final long MAX_BYTES = 5L * 1024 * 1024;

    private final WorkerPaths paths;

    public StickerService(WorkerPaths paths) {
        this.paths = paths;
    }

    public Path dir() {
        return paths.repo().resolve("assets").resolve("stickers");
    }

    public List<Map<String, Object>> list() {
        List<Map<String, Object>> out = new ArrayList<>();
        if (!Files.isDirectory(dir())) {
            return out;
        }
        try (Stream<Path> s = Files.list(dir())) {
            s.filter(p -> NAME.matcher(p.getFileName().toString()).matches()).sorted().forEach(p -> {
                Map<String, Object> m = new LinkedHashMap<>();
                m.put("name", p.getFileName().toString());
                try {
                    m.put("size", Files.size(p));
                } catch (IOException ignored) {
                    // 大小只是展示用
                }
                out.add(m);
            });
        } catch (IOException e) {
            throw ApiException.badRequest("读取贴图库失败");
        }
        return out;
    }

    public Map<String, Object> upload(MultipartFile file) {
        if (file == null || file.isEmpty()) {
            throw ApiException.badRequest("没有文件");
        }
        if (file.getSize() > MAX_BYTES) {
            throw ApiException.badRequest("贴图最大 5 MB");
        }
        String name = clean(file.getOriginalFilename());
        try (InputStream in = file.getInputStream()) {
            byte[] head = in.readNBytes(12);
            String ext = sniff(head);
            if (ext == null) {
                throw ApiException.badRequest("只支持 PNG / JPG / GIF / WEBP 图片");
            }
            String base = name.contains(".") ? name.substring(0, name.lastIndexOf('.')) : name;
            name = base + "." + ext;
            if (!NAME.matcher(name).matches()) {
                throw ApiException.badRequest("文件名只能用中文、字母、数字、下划线、短横线");
            }
            Files.createDirectories(dir());
            Path dst = WorkerPaths.inside(dir(), dir().resolve(name));
            try (InputStream all = file.getInputStream()) {
                Files.copy(all, dst, StandardCopyOption.REPLACE_EXISTING);
            }
        } catch (IOException e) {
            throw ApiException.badRequest("保存失败：" + e.getMessage());
        }
        return Map.of("name", name);
    }

    public void delete(String name) {
        try {
            Files.deleteIfExists(file(name));
        } catch (IOException e) {
            throw ApiException.badRequest("删除失败");
        }
    }

    public Path file(String name) {
        if (name == null || !NAME.matcher(name).matches()) {
            throw ApiException.badRequest("文件名不合法");
        }
        return WorkerPaths.inside(dir(), dir().resolve(name));
    }

    private static String clean(String original) {
        String n = original == null ? "sticker" : Path.of(original).getFileName().toString();
        n = n.replaceAll("[^\\w\\-.\\u4e00-\\u9fff]", "_");
        if (n.length() > 60) {
            n = n.substring(n.length() - 60);
        }
        return n.isBlank() || n.startsWith(".") ? "sticker" + n : n;
    }

    /** 按文件头判断真实格式（不信任扩展名）。 */
    static String sniff(byte[] h) {
        if (h.length >= 8 && (h[0] & 0xff) == 0x89 && h[1] == 'P' && h[2] == 'N' && h[3] == 'G') {
            return "png";
        }
        if (h.length >= 3 && (h[0] & 0xff) == 0xFF && (h[1] & 0xff) == 0xD8 && (h[2] & 0xff) == 0xFF) {
            return "jpg";
        }
        if (h.length >= 6 && h[0] == 'G' && h[1] == 'I' && h[2] == 'F' && h[3] == '8') {
            return "gif";
        }
        if (h.length >= 12 && h[0] == 'R' && h[1] == 'I' && h[2] == 'F' && h[3] == 'F'
                && h[8] == 'W' && h[9] == 'E' && h[10] == 'B' && h[11] == 'P') {
            return "webp";
        }
        return null;
    }

    public static String contentType(String name) {
        String n = name.toLowerCase(Locale.ROOT);
        if (n.endsWith(".png")) {
            return "image/png";
        }
        if (n.endsWith(".gif")) {
            return "image/gif";
        }
        if (n.endsWith(".webp")) {
            return "image/webp";
        }
        return "image/jpeg";
    }
}
