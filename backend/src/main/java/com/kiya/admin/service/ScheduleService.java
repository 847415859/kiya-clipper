package com.kiya.admin.service;

import java.awt.Color;
import java.awt.RenderingHints;
import java.awt.image.BufferedImage;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import javax.imageio.IIOImage;
import javax.imageio.ImageIO;
import javax.imageio.ImageWriteParam;

import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.web.multipart.MultipartFile;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.kiya.admin.common.ApiException;

/** Authenticated, streamer-scoped weekly plans. The worker owns date filtering and validation. */
@Service
public class ScheduleService {
    private final WorkerPaths paths;
    private final ProcessRunner runner;
    private final ObjectMapper om;
    public ScheduleService(WorkerPaths paths, ProcessRunner runner, ObjectMapper om) {
        this.paths = paths; this.runner = runner; this.om = om;
    }

    private Map<String, Object> run(String action, String week, Map<String, Object> input, List<String> extra, int timeout) {
        if (week != null && !week.matches("\\d{4}-\\d{2}-\\d{2}")) throw ApiException.badRequest("日期须为 YYYY-MM-DD");
        List<String> args = new ArrayList<>(List.of(action, "--root", paths.output().toString(), "--streamer", paths.streamer()));
        if (week != null) args.addAll(List.of("--week-start", week));
        args.addAll(extra);
        Path tmp = paths.output().resolve("_schedule/.request-" + UUID.randomUUID() + ".json");
        try {
            if (input != null) {
                Files.createDirectories(tmp.getParent());
                Files.writeString(tmp, om.writeValueAsString(input));
                args.addAll(List.of("--input", tmp.toString()));
            }
            var result = runner.run("schedule.py", args, Duration.ofSeconds(timeout));
            if (result.exitCode() != 0) throw ApiException.badRequest(SessionService.lastLine(result.output()));
            Map<String, Object> value = om.readValue(SessionService.lastLine(result.output()), new TypeReference<>() {});
            if (Boolean.TRUE.equals(value.get("conflict"))) throw new ApiException(HttpStatus.CONFLICT, String.valueOf(value.get("error")));
            return value;
        } catch (IOException e) { throw ApiException.badRequest("日程处理失败：" + e.getMessage()); }
        finally { try { Files.deleteIfExists(tmp); } catch (IOException ignored) {} }
    }

    public Map<String, Object> get(String week) { return run("get", week, null, List.of(), 30); }
    public Map<String, Object> save(String week, Map<String, Object> plan, String revision, boolean confirm) {
        if (plan == null || revision == null) throw ApiException.badRequest("缺少日程或版本，请重新读取后保存");
        return run("save", week, Map.of("plan", plan, "expected_revision", revision, "confirm", confirm), List.of(), 30);
    }
    public Map<String, Object> deactivate(String week, String revision) {
        if (revision == null) throw ApiException.badRequest("缺少日程版本");
        return run("deactivate", week, Map.of("expected_revision", revision), List.of(), 30);
    }
    public Map<String, Object> preview(Map<String, Object> body) { return run("preview", null, body, List.of(), 30); }
    public Map<String, Object> extract(String week, String image) {
        imageFile(image);
        return run("extract", week, null, List.of("--image-id", image), 130);
    }
    public Path imageFile(String id) {
        if (id == null || !id.matches("[a-f0-9]{32}")) throw ApiException.badRequest("周表图片标识不合法");
        return WorkerPaths.inside(paths.output(), paths.output().resolve("_schedule/images").resolve(id + ".jpg"));
    }

    public synchronized Map<String, Object> upload(MultipartFile file) {
        if (file == null || file.isEmpty() || file.getSize() > 8 * 1024 * 1024) throw ApiException.badRequest("请上传不超过 8MB 的 PNG 或 JPG 周表");
        String id = UUID.randomUUID().toString().replace("-", "");
        Path dst = imageFile(id);
        Path tmp = dst.resolveSibling(".upload-" + id);
        try (var source = file.getInputStream(); var stream = ImageIO.createImageInputStream(source)) {
            if (stream == null) throw ApiException.badRequest("无法读取图片");
            var readers = ImageIO.getImageReaders(stream);
            if (!readers.hasNext()) throw ApiException.badRequest("请上传有效的 PNG 或 JPG 图片");
            var reader = readers.next();
            BufferedImage original;
            try {
                String format = reader.getFormatName();
                if (!List.of("png", "jpeg", "jpg").contains(format.toLowerCase(java.util.Locale.ROOT))) throw ApiException.badRequest("仅支持 PNG 或 JPG 周表");
                reader.setInput(stream, true, true);
                int width = reader.getWidth(0), height = reader.getHeight(0);
                if (width < 1 || height < 1 || (long)width * height > 12_000_000) throw ApiException.badRequest("图片像素过大，请缩小到 1200 万像素以内");
                original = reader.read(0);
            } finally { reader.dispose(); }
            double scale = Math.min(1, 4096.0 / Math.max(original.getWidth(), original.getHeight()));
            int width = Math.max(1, (int)Math.round(original.getWidth() * scale));
            int height = Math.max(1, (int)Math.round(original.getHeight() * scale));
            BufferedImage normalized = new BufferedImage(width, height, BufferedImage.TYPE_INT_RGB);
            var graphics = normalized.createGraphics();
            try {
                graphics.setColor(Color.WHITE); graphics.fillRect(0, 0, width, height);
                graphics.setRenderingHint(RenderingHints.KEY_INTERPOLATION, RenderingHints.VALUE_INTERPOLATION_BICUBIC);
                graphics.drawImage(original, 0, 0, width, height, null);
            } finally { graphics.dispose(); }
            Files.createDirectories(dst.getParent());
            var writer = ImageIO.getImageWritersByFormatName("jpeg").next();
            try (var output = ImageIO.createImageOutputStream(tmp.toFile())) {
                writer.setOutput(output);
                var param = writer.getDefaultWriteParam();
                param.setCompressionMode(ImageWriteParam.MODE_EXPLICIT); param.setCompressionQuality(0.93f);
                writer.write(null, new IIOImage(normalized, null, null), param);
            } finally { writer.dispose(); }
            Files.move(tmp, dst, StandardCopyOption.ATOMIC_MOVE);
            return Map.of("image_id", id, "width", width, "height", height);
        } catch (IOException e) { throw ApiException.badRequest("上传图片失败，请换一张 PNG 或 JPG 图片"); }
        finally { try { Files.deleteIfExists(tmp); } catch (IOException ignored) {} }
    }
}
