package com.kiya.admin.service;

import java.io.IOException;
import java.io.InputStream;
import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.stream.Stream;

import org.springframework.stereotype.Service;
import org.springframework.web.multipart.MultipartFile;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.kiya.admin.common.ApiException;

/** 贴图库：<repo>/assets/stickers/（不进 git）。只收 png / jpg / gif / webp，按文件头校验真实格式。
 *  支持两种入库：本地上传（upload）和网络搜索下载（search + fetch，文件名带 net_ 前缀标记版权未验证）。 */
@Service
public class StickerService {
    static final Pattern NAME = Pattern.compile("^[\\w\\-.\\u4e00-\\u9fff]{1,80}\\.(png|jpg|jpeg|gif|webp)$",
            Pattern.CASE_INSENSITIVE | Pattern.UNICODE_CHARACTER_CLASS);
    static final long MAX_BYTES = 5L * 1024 * 1024;
    static final String SEARCH_URL = "https://www.fabiaoqing.com/search/search/keyword/%s.html";
    static final String REFERER = "https://www.fabiaoqing.com/";
    static final String UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36";
    /** fabiaoqing 搜索页把真实图片放在懒加载属性 data-original 里。 */
    private static final Pattern IMG = Pattern.compile(
            "data-original=\"(https?://[^\"]+\\.(?:gif|jpg|jpeg|png|webp))\"", Pattern.CASE_INSENSITIVE);

    private final WorkerPaths paths;
    private final ObjectMapper om;
    private final HttpClient http = HttpClient.newBuilder()
            .connectTimeout(Duration.ofSeconds(10))
            .followRedirects(HttpClient.Redirect.NORMAL)   // fabiaoqing 搜索页会 302 到 /search/bqb/...
            .build();

    public StickerService(WorkerPaths paths, ObjectMapper om) {
        this.paths = paths;
        this.om = om;
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

    /** 网络搜索梗图（发表情 fabiaoqing 搜索页），解析懒加载属性拿到图片直链。仅内部测试用，商用前换成授权素材。 */
    public List<String> search(String q, int limit) {
        String kw = q == null ? "" : q.trim();
        if (kw.isEmpty()) {
            throw ApiException.badRequest("搜索词不能为空");
        }
        try {
            String enc = URLEncoder.encode(kw, StandardCharsets.UTF_8).replace("+", "%20");
            String url = String.format(SEARCH_URL, enc);
            HttpRequest req = HttpRequest.newBuilder(URI.create(url))
                    .header("User-Agent", UA)
                    .header("Referer", REFERER)
                    .timeout(Duration.ofSeconds(15))
                    .GET().build();
            HttpResponse<String> resp = http.send(req, HttpResponse.BodyHandlers.ofString());
            if (resp.statusCode() != 200) {
                throw ApiException.badRequest("搜索失败：HTTP " + resp.statusCode());
            }
            List<String> out = new ArrayList<>();
            int cap = Math.max(1, Math.min(limit, 60));
            Matcher m = IMG.matcher(resp.body());
            while (m.find() && out.size() < cap) {
                String u = m.group(1);
                if (!out.contains(u)) {
                    out.add(u);
                }
            }
            return out;
        } catch (ApiException e) {
            throw e;
        } catch (Exception e) {
            throw ApiException.badRequest("搜索失败：" + e.getMessage());
        }
    }

    /** 从网络 URL 下载一张图入库。文件名强制加 net_ 前缀，标记「版权未验证」，商用前清点这些。 */
    public Map<String, Object> fetch(String url, String name) {
        if (url == null || !(url.startsWith("http://") || url.startsWith("https://"))) {
            throw ApiException.badRequest("只支持 http/https 图片地址");
        }
        byte[] data;
        try {
            HttpRequest req = HttpRequest.newBuilder(URI.create(url))
                    .header("User-Agent", UA)
                    .header("Referer", REFERER)
                    .timeout(Duration.ofSeconds(30))
                    .GET().build();
            HttpResponse<byte[]> resp = http.send(req, HttpResponse.BodyHandlers.ofByteArray());
            if (resp.statusCode() != 200) {
                throw ApiException.badRequest("下载失败：HTTP " + resp.statusCode());
            }
            data = resp.body();
        } catch (Exception e) {
            throw ApiException.badRequest("下载失败：" + e.getMessage());
        }
        if (data.length < 3 || data.length > MAX_BYTES) {
            throw ApiException.badRequest("图片大小不对（3 字节 ~ 5 MB）");
        }
        String ext = sniff(Arrays.copyOf(data, Math.min(data.length, 12)));
        if (ext == null) {
            throw ApiException.badRequest("只支持 PNG / JPG / GIF / WEBP 图片");
        }
        String base = clean(name);
        if (base.contains(".")) {
            base = base.substring(0, base.lastIndexOf('.'));
        }
        if (base.isBlank()) {
            base = "net_" + System.currentTimeMillis();
        } else if (!base.startsWith("net_")) {
            base = "net_" + base;
        }
        String fname = base + "." + ext;
        if (!NAME.matcher(fname).matches()) {
            throw ApiException.badRequest("文件名只能用中文、字母、数字、下划线、短横线");
        }
        try {
            Files.createDirectories(dir());
            Path dst = WorkerPaths.inside(dir(), dir().resolve(fname));
            Files.write(dst, data);
        } catch (IOException e) {
            throw ApiException.badRequest("保存失败：" + e.getMessage());
        }
        return Map.of("name", fname);
    }

    /** 读 stickers.json（贴图含义描述）：{文件名: {meaning, mood, avoid, auto}}。没有就是空表。 */
    public Map<String, Object> meta() {
        Path f = dir().resolve("stickers.json");
        if (!Files.exists(f)) {
            return new LinkedHashMap<>();
        }
        try {
            Map<String, Object> m = om.readValue(f.toFile(), new TypeReference<LinkedHashMap<String, Object>>() {});
            return m == null ? new LinkedHashMap<>() : m;
        } catch (IOException e) {
            throw ApiException.badRequest("stickers.json 损坏：" + e.getMessage());
        }
    }

    /** 保存某个贴图的含义描述（mood / avoid 可空）。人工改过的去掉 auto 标记。 */
    public Map<String, Object> saveMeta(String name, String meaning, String mood, String avoid) {
        if (name == null || !NAME.matcher(name).matches()) {
            throw ApiException.badRequest("文件名不合法");
        }
        if (meaning == null || meaning.trim().isEmpty()) {
            throw ApiException.badRequest("含义不能为空");
        }
        Map<String, Object> meta = meta();
        Map<String, Object> item = new LinkedHashMap<>();
        item.put("meaning", meaning.trim());
        item.put("mood", mood == null ? "" : mood.trim());
        item.put("avoid", avoid == null ? "" : avoid.trim());
        item.put("auto", false);
        meta.put(name, item);
        Path f = dir().resolve("stickers.json");
        try {
            Files.createDirectories(dir());
            Path tmp = f.resolveSibling("stickers.json.tmp");
            Files.writeString(tmp, om.writerWithDefaultPrettyPrinter().writeValueAsString(meta), StandardCharsets.UTF_8);
            Files.move(tmp, f, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE);
        } catch (IOException e) {
            throw ApiException.badRequest("保存失败：" + e.getMessage());
        }
        return Map.of("name", name);
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
