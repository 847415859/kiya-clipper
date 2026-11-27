package com.kiya.admin.service;

import java.io.IOException;
import java.nio.channels.FileChannel;
import java.nio.file.*;
import java.nio.file.attribute.GroupPrincipal;
import java.nio.file.attribute.PosixFileAttributeView;
import java.nio.file.attribute.PosixFilePermissions;
import java.security.MessageDigest;
import java.time.Instant;
import java.util.*;
import java.util.concurrent.ConcurrentHashMap;
import org.springframework.stereotype.Service;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.kiya.admin.common.ApiException;
import com.kiya.admin.config.KiyaProperties;

/** Immutable, server-resolved historical jobs. No models or subprocesses run in HTTP requests. */
@Service
public class HistoricalTrialService {
    private static final ConcurrentHashMap<String, Object> MUTEX = new ConcurrentHashMap<>();
    private static final Set<String> TERMINAL = Set.of("completed", "prepared", "has_clips", "failed", "cancelled", "needs_review", "partial");
    private final WorkerPaths paths;
    private final KiyaProperties props;
    private final ObjectMapper json;
    private final OperationsQueueService operations;
    public HistoricalTrialService(WorkerPaths paths, KiyaProperties props, ObjectMapper json, OperationsQueueService operations) {
        this.paths = paths; this.props = props; this.json = json; this.operations = operations;
    }
    public Path root() { return Path.of(props.getAcceptanceDir()).toAbsolutePath().normalize().resolve("history"); }
    private Path safe(Path base, Path file, boolean regular) throws IOException {
        Path lexical = file.toAbsolutePath().normalize();
        if (!lexical.startsWith(base.toAbsolutePath().normalize())) throw ApiException.badRequest("路径越界");
        for (Path ancestor = lexical; ancestor != null; ancestor = ancestor.getParent()) {
            if (Files.isSymbolicLink(ancestor)) throw ApiException.badRequest("符号链接路径不允许");
        }
        Path real = lexical.toRealPath();
        if (!real.startsWith(base.toRealPath()) || (regular && (!Files.isRegularFile(real) || !Files.isReadable(real))))
            throw ApiException.badRequest("文件归属或可读性检查失败");
        if (regular) try (var input = Files.newInputStream(real)) { if (input.read() < 0) throw ApiException.badRequest("素材为空"); }
        return real;
    }
    private Path candidateFile(String session) throws IOException {
        Path directory = paths.sessionDir(paths.checkSession(session));
        safe(paths.output(), directory, false);
        return safe(directory, directory.resolve("candidates.json"), true);
    }
    public Map<String, Object> options(String session) {
        paths.checkSession(session);
        List<Map<String, Object>> rows = new ArrayList<>();
        List<String> reasons = new ArrayList<>();
        try {
            Path file = candidateFile(session);
            JsonNode data = json.readTree(file.toFile());
            if (data.has("session") && !session.equals(data.path("session").asText())) throw ApiException.badRequest("候选场次归属错误");
            if (data.has("streamer") && !paths.streamer().equals(data.path("streamer").asText())) throw ApiException.badRequest("候选主播归属错误");
            int missing = 0;
            for (JsonNode segment : data.path("segments")) {
                try {
                    String raw = segment.path("video").asText();
                    if (raw.isBlank()) throw ApiException.badRequest("素材路径缺失");
                    Path media = Path.of(raw);
                    if (!media.isAbsolute()) media = file.getParent().resolve(media);
                    Path normalized = media.toAbsolutePath().normalize();
                    Path allowed = normalized.startsWith(file.getParent()) ? file.getParent() : paths.recDir();
                    safe(allowed, normalized, true);
                } catch (IOException | RuntimeException e) { missing++; }
            }
            if (!data.path("segments").isArray() || data.path("segments").isEmpty()) missing++;
            Set<Integer> seen = new HashSet<>();
            for (JsonNode c : data.path("candidates")) {
                if (!c.path("rank").isIntegralNumber() || c.path("rank").asInt() < 1) continue;
                int rank = c.path("rank").asInt();
                boolean unique = seen.add(rank) && java.util.stream.StreamSupport.stream(data.path("candidates").spliterator(), false)
                        .filter(x -> x.path("rank").asInt(-1) == rank).count() == 1;
                List<String> blockers = new ArrayList<>();
                if (!unique) blockers.add("候选编号重复");
                if ((c.has("session") && !session.equals(c.path("session").asText()))
                        || (c.has("streamer") && !paths.streamer().equals(c.path("streamer").asText()))) blockers.add("候选归属错误");
                double start = c.path("clip_start").asDouble(c.path("start").asDouble(Double.NaN));
                double end = c.path("clip_end").asDouble(c.path("end").asDouble(Double.NaN));
                if (!Double.isFinite(start) || !Double.isFinite(end) || start < 0 || end <= start) blockers.add("候选时间范围无效");
                List<List<Double>> gaps = new ArrayList<>();
                List<double[]> spans = new ArrayList<>();
                boolean durationsKnown = true;
                for (JsonNode segment : data.path("segments")) {
                    double offset = segment.path("offset").asDouble(Double.NaN);
                    double duration = segment.path("duration").asDouble(Double.NaN);
                    if (!Double.isFinite(offset) || offset < 0) blockers.add("媒体段 offset 无效");
                    if (segment.has("duration") && (!Double.isFinite(duration) || duration <= 0)) blockers.add("媒体段 duration 无效");
                    if (!Double.isFinite(duration)) durationsKnown = false;
                    else if (Double.isFinite(offset)) spans.add(new double[]{offset, offset + duration});
                }
                if (durationsKnown && Double.isFinite(start) && Double.isFinite(end)) {
                    spans.sort(Comparator.comparingDouble(x -> x[0])); double cursor = start;
                    for (double[] span : spans) {
                        if (span[1] <= cursor || span[0] >= end) continue;
                        if (span[0] > cursor) gaps.add(List.of(cursor, Math.min(span[0], end)));
                        cursor = Math.max(cursor, span[1]);
                    }
                    if (cursor < end) gaps.add(List.of(cursor, end));
                    if (!gaps.isEmpty()) blockers.add("候选原始媒体覆盖缺口：" + gaps);
                }
                if (missing > 0) blockers.add("原始媒体缺失或不可读：" + missing + " 段（含路径归属失败）");
                if (!props.isHistoricalTrialFixtureEnabled()) blockers.add("夹具模式未启用；付费步骤预算和成本类型未确定，预检停止");
                Map<String, Object> row = new LinkedHashMap<>();
                row.put("rank", rank); row.put("available", missing == 0 && unique); row.put("canSubmit", blockers.isEmpty());
                row.put("start", c.path("clip_start").asDouble(c.path("start").asDouble())); row.put("end", c.path("clip_end").asDouble(c.path("end").asDouble()));
                row.put("missingSegments", missing); row.put("missingRanges", gaps); row.put("reasons", blockers); row.put("blockers", blockers);
                List<Map<String, Object>> prior = list().stream().filter(x -> session.equals(x.get("session")) && Integer.valueOf(rank).equals(x.get("rank"))).toList();
                if (!prior.isEmpty()) row.put("lastTask", prior.get(prior.size() - 1));
                prior.stream().filter(x -> !TERMINAL.contains(String.valueOf(x.get("workerStatus")))).findFirst().ifPresent(x -> row.put("activeTask", x));
                rows.add(row);
            }
        } catch (IOException | RuntimeException e) { reasons.add("候选文件缺失、不可读或归属检查失败"); }
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("session", session); result.put("candidates", rows); result.put("reasons", reasons); result.put("blockers", reasons);
        result.put("mode", "fixture"); result.put("enabled", props.isHistoricalTrialFixtureEnabled());
        result.put("canSubmit", rows.stream().anyMatch(x -> Boolean.TRUE.equals(x.get("canSubmit"))));
        result.put("maxCostFen", 0); result.put("publish", false); result.put("steps", List.of("证据检查", "夹具制作", "质检", "隔离验收"));
        return result;
    }
    public Map<String, Object> submit(String session, int rank, boolean confirmation, String rerun) throws IOException {
        paths.checkSession(session);
        if (!confirmation || rank < 1) throw ApiException.badRequest("请确认场次、候选和零费用夹具步骤");
        if (!props.isHistoricalTrialFixtureEnabled()) throw ApiException.badRequest("夹具模式未启用；付费预算未知，预检停止");
        Map<String, Object> option = options(session);
        boolean valid = ((List<?>) option.get("candidates")).stream().map(x -> (Map<?, ?>) x)
                .anyMatch(x -> Integer.valueOf(rank).equals(x.get("rank")) && Boolean.TRUE.equals(x.get("canSubmit")));
        if (!valid) throw ApiException.badRequest("候选或素材预检失败，请刷新查看缺失条件");
        Path file = candidateFile(session);
        Path root = root();
        prepareHistory();
        String key = paths.streamer() + "-" + session + "-" + rank;
        synchronized (MUTEX.computeIfAbsent(root + key, k -> new Object())) {
            Path lock = root.resolve("locks").resolve(key + ".lock");
            checkHistoryPath(lock);
            try (var channel = FileChannel.open(lock, StandardOpenOption.CREATE, StandardOpenOption.WRITE, LinkOption.NOFOLLOW_LINKS)) {
                shareHistoryPath(lock, false);
                try (var ignored = channel.lock()) {
                List<Map<String, Object>> previous = list().stream().filter(x -> session.equals(x.get("session")) && Integer.valueOf(rank).equals(x.get("rank"))).toList();
                for (Map<String, Object> prior : previous) if (!TERMINAL.contains(String.valueOf(prior.get("workerStatus")))) return prior;
                if (!previous.isEmpty()) {
                    if (rerun == null || previous.stream().noneMatch(x -> rerun.equals(x.get("taskId"))))
                        throw ApiException.badRequest("任务已结束；明确提供 rerunTaskId 后创建新的历史记录");
                } else if (rerun != null) throw ApiException.badRequest("重跑来源不存在");
                String id = "history-" + UUID.randomUUID();
                Map<String, Object> job = new LinkedHashMap<>();
                job.put("schema", 1); job.put("source", "historical_trial"); job.put("task_id", id);
                job.put("session", session); job.put("streamer", paths.streamer()); job.put("rank", rank);
                job.put("candidates", file.toString()); job.put("input_revision", hash(Files.readAllBytes(file)));
                job.put("source_root", file.getParent().toString()); job.put("rec_root", paths.recDir().toRealPath().toString());
                Map<String, String> inputFiles = new LinkedHashMap<>(); inputFiles.put(file.toString(), hash(Files.readAllBytes(file)));
                Path transcript = file.getParent().resolve("transcript.json");
                if (Files.exists(transcript)) { transcript = safe(file.getParent(), transcript, true); inputFiles.put(transcript.toString(), hash(Files.readAllBytes(transcript))); }
                job.put("input_files", inputFiles);
                job.put("mode", "fixture"); job.put("publish", false); job.put("max_cost_fen", 0);
                job.put("production_mode", "isolated"); job.put("production_ranks", List.of(rank));
                job.put("created_at", Instant.now().toString()); if (rerun != null) job.put("rerun_of", rerun);
                atomic(root.resolve("status").resolve(id + ".json"), Map.of("session", session, "status", "queued", "stage", "queue", "production_mode", "isolated", "production_ranks", List.of(rank)));
                atomic(root.resolve("queue").resolve(id + ".json"), job);
                return detail(session, id);
                }
            }
        }
    }
    /** Check every existing ancestor before creating or changing anything, including outside history. */
    private void checkHistoryPath(Path path) throws IOException {
        Path normalized = path.toAbsolutePath().normalize();
        if (!normalized.startsWith(root())) throw new IOException("Historical permissions are restricted to " + root());
        for (Path current = normalized; current != null; current = current.getParent()) {
            if (Files.isSymbolicLink(current)) throw new IOException("Historical path contains symbolic link: " + current);
        }
    }
    private GroupPrincipal historyGroup(Path existing) throws IOException {
        if (Files.getFileAttributeView(existing, PosixFileAttributeView.class, LinkOption.NOFOLLOW_LINKS) == null) return null;
        String group = props.getHistoricalTrialGroup();
        if (group == null || group.isBlank()) throw new IOException("kiya.historical-trial-group must name an existing POSIX group");
        try { return existing.getFileSystem().getUserPrincipalLookupService().lookupPrincipalByGroupName(group); }
        catch (IOException e) { throw new IOException("Historical trial POSIX group '" + group + "' is unavailable; provision it or set kiya.historical-trial-group", e); }
    }
    private void prepareHistory() throws IOException {
        Path root = root();
        checkHistoryPath(root);
        Path existing = root;
        while (!Files.exists(existing, LinkOption.NOFOLLOW_LINKS)) existing = existing.getParent();
        historyGroup(existing); // Fail closed before creating even the history root if the configured group is missing.
        Files.createDirectories(root);
        safe(Path.of(props.getAcceptanceDir()), root, false);
        shareHistoryPath(root, true);
        for (String kind : List.of("queue", "status", "production", "locks", "ledger")) {
            Path directory = root.resolve(kind);
            checkHistoryPath(directory);
            Files.createDirectories(directory);
            shareHistoryPath(directory, true);
        }
    }
    private void shareHistoryPath(Path path, boolean directory) throws IOException {
        checkHistoryPath(path);
        Path checked = safe(root(), path, false);
        if (directory ? !Files.isDirectory(checked, LinkOption.NOFOLLOW_LINKS) : !Files.isRegularFile(checked, LinkOption.NOFOLLOW_LINKS))
            throw new IOException("Unexpected historical path type: " + checked);
        GroupPrincipal group = historyGroup(checked);
        if (group == null) return; // Windows/non-POSIX retains its native ACL behavior.
        PosixFileAttributeView view = Files.getFileAttributeView(checked, PosixFileAttributeView.class, LinkOption.NOFOLLOW_LINKS);
        if (!view.readAttributes().group().equals(group)) view.setGroup(group);
        if (directory) {
            if (!checked.getFileSystem().supportedFileAttributeViews().contains("unix"))
                throw new IOException("Historical POSIX directories require unix setgid support: " + checked);
            Files.setAttribute(checked, "unix:mode", 02770, LinkOption.NOFOLLOW_LINKS);
        } else view.setPermissions(PosixFilePermissions.fromString("rw-r-----"));
    }
    private void atomic(Path target, Object data) throws IOException {
        checkHistoryPath(target);
        Path tmp = Files.createTempFile(target.getParent(), ".history-", ".tmp");
        try {
            Files.write(tmp, json.writeValueAsBytes(data));
            shareHistoryPath(tmp, false);
            try (var channel = FileChannel.open(tmp, StandardOpenOption.WRITE)) { channel.force(true); }
            Files.move(tmp, target, StandardCopyOption.ATOMIC_MOVE);
        } finally { Files.deleteIfExists(tmp); }
    }
    private String hash(byte[] bytes) {
        try { return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes)); }
        catch (java.security.NoSuchAlgorithmException e) { throw new IllegalStateException(e); }
    }
    private JsonNode job(String session, String id) throws IOException {
        paths.checkSession(session);
        if (id == null || !id.matches("history-[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}")) throw ApiException.notFound("任务不存在");
        JsonNode job;
        try { job = json.readTree(safe(root(), root().resolve("queue").resolve(id + ".json"), true).toFile()); }
        catch (IOException e) { throw ApiException.notFound("任务不存在"); }
        if (!"historical_trial".equals(job.path("source").asText()) || !session.equals(job.path("session").asText())
                || !paths.streamer().equals(job.path("streamer").asText()) || !id.equals(job.path("task_id").asText())) throw ApiException.notFound("任务归属错误");
        return job;
    }
    public List<Map<String, Object>> list() {
        List<Map<String, Object>> rows = new ArrayList<>();
        try (var files = Files.list(root().resolve("queue"))) {
            for (Path f : files.filter(p -> p.getFileName().toString().endsWith(".json")).sorted().toList()) {
                try { JsonNode j = json.readTree(safe(root(), f, true).toFile()); rows.add(detail(j.path("session").asText(), j.path("task_id").asText())); }
                catch (IOException | RuntimeException ignored) { }
            }
        } catch (IOException ignored) { }
        rows.sort(Comparator.comparing(x -> String.valueOf(x.get("createdAt"))));
        return rows;
    }
    public Map<String, Object> detail(String session, String id) throws IOException {
        JsonNode job = job(session, id);
        Path status = root().resolve("status").resolve(id + ".json");
        if (Files.exists(status)) safe(root(), status, true);
        Map<String, Object> out = new LinkedHashMap<>(operations.historicalDetail(root(), id, session));
        out.put("taskId", id); out.put("jobId", id); out.put("session", session); out.put("name", session);
        out.put("source", "historical_trial"); out.put("rank", job.path("rank").asInt()); out.put("mode", job.path("mode").asText());
        out.put("maxCostFen", 0); out.put("publish", false); out.put("rerunOf", job.path("rerun_of").asText(null));
        out.put("createdAt", job.path("created_at").asText());
        if (!out.containsKey("workerStatus")) out.put("workerStatus", "queued");
        for (Object row : (List<?>) out.getOrDefault("candidates", List.of())) {
            @SuppressWarnings("unchecked") Map<String, Object> candidate = (Map<String, Object>) row;
            candidate.put("mediaUrl", "/api/sessions/" + session + "/isolated-trials/" + id + "/media/" + candidate.get("rank"));
            for (Object v : (List<?>) candidate.getOrDefault("versions", List.of())) {
                @SuppressWarnings("unchecked") Map<String, Object> version = (Map<String, Object>) v;
                if (Boolean.TRUE.equals(version.get("playable"))) version.put("mediaUrl", candidate.get("mediaUrl") + "/version/" + version.get("version"));
            }
        }
        return out;
    }
    public Path media(String session, String id, int rank, String version) throws IOException {
        JsonNode job = job(session, id);
        if (rank != job.path("rank").asInt()) throw ApiException.notFound("候选归属错误");
        return operations.historicalMedia(root(), id, session, rank, version);
    }
}
