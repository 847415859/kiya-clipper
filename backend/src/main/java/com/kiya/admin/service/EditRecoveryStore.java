package com.kiya.admin.service;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

import org.springframework.http.HttpStatus;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.kiya.admin.common.ApiException;

/** Per-session recovery files; draft clients cannot overwrite one another or a formal edit. */
final class EditRecoveryStore {
    private final ObjectMapper om;
    EditRecoveryStore(ObjectMapper om) { this.om = om; }

    String revision(Path file) {
        try {
            return Files.exists(file) ? revision(Files.readAllBytes(file)) : "none";
        } catch (IOException e) { throw ApiException.badRequest("读取编辑版本失败"); }
    }

    String revision(byte[] content) {
        try { return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(content)); }
        catch (NoSuchAlgorithmException e) { throw new IllegalStateException(e); }
    }

    void check(Path file, String expected) {
        if (expected != null && !expected.equals(revision(file))) {
            throw new ApiException(HttpStatus.CONFLICT, "服务器脚本已更新，当前修改已保留；请先恢复或对照最新版本后再保存");
        }
    }

    private Path folder(Path file, String kind) {
        return file.getParent().resolve(kind).resolve(file.getFileName().toString().replace(".json", ""));
    }

    void archive(Path file, String label) throws IOException {
        if (!Files.exists(file)) return;
        byte[] content = Files.readAllBytes(file);
        String revision = revision(content);
        Path dst = folder(file, "history").resolve(revision + ".json");
        if (Files.exists(dst)) return;
        Map<String, Object> value = new LinkedHashMap<>();
        value.put("id", revision);
        value.put("label", label);
        value.put("updatedAt", Files.getLastModifiedTime(file).toMillis());
        value.put("edit", om.readValue(content, Map.class));
        write(dst, value);
    }

    List<Map<String, Object>> versions(Path file) {
        return list(folder(file, "history"));
    }

    List<Map<String, Object>> drafts(Path file) {
        return list(folder(file, "drafts")).stream().filter(m -> !Boolean.TRUE.equals(m.get("committed"))).toList();
    }

    synchronized Map<String, Object> draft(Path file, String client, Map<String, Object> value) {
        if (client == null || !client.matches("[a-f0-9]{32}")) throw ApiException.badRequest("草稿标识不合法");
        if (!(value.get("seq") instanceof Number n) || n.longValue() < 0 || !(value.get("state") instanceof Map<?, ?>)) {
            throw ApiException.badRequest("草稿格式不对");
        }
        Path dst = folder(file, "drafts").resolve(client + ".json");
        try {
            if (om.writeValueAsBytes(value).length > 2_000_000) throw ApiException.badRequest("草稿过大");
            if (Files.exists(dst)) {
                Map<String, Object> old = read(dst);
                if (((Number) old.getOrDefault("seq", -1)).longValue() >= n.longValue()) return old;
            }
            Map<String, Object> stored = new LinkedHashMap<>(value);
            stored.put("id", client);
            stored.put("updatedAt", System.currentTimeMillis());
            write(dst, stored);
            return stored;
        } catch (IOException e) { throw ApiException.badRequest("草稿保存失败：" + e.getMessage()); }
    }

    private List<Map<String, Object>> list(Path folder) {
        List<Map<String, Object>> out = new ArrayList<>();
        if (!Files.isDirectory(folder)) return out;
        try (var files = Files.list(folder)) {
            for (Path file : files.filter(p -> p.getFileName().toString().matches("[a-f0-9]{32,64}\\.json")).toList()) {
                try { out.add(read(file)); } catch (IOException ignored) { /* Isolate a damaged recovery file. */ }
            }
        } catch (IOException e) { throw ApiException.badRequest("读取恢复记录失败"); }
        out.sort(Comparator.comparingLong((Map<String, Object> m) -> ((Number) m.getOrDefault("updatedAt", 0)).longValue()).reversed());
        return out.stream().limit(30).toList();
    }

    private Map<String, Object> read(Path file) throws IOException {
        return om.readValue(file.toFile(), new TypeReference<Map<String, Object>>() {});
    }

    private void write(Path dst, Map<String, Object> value) throws IOException {
        Files.createDirectories(dst.getParent());
        Path tmp = dst.resolveSibling(".tmp-" + UUID.randomUUID());
        try {
            Files.writeString(tmp, om.writeValueAsString(value));
            Files.move(tmp, dst, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE);
        } finally { Files.deleteIfExists(tmp); }
    }
}
