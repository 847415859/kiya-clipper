package com.kiya.admin.service;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.FileAlreadyExistsException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.TreeSet;
import java.util.regex.Pattern;

import org.springframework.stereotype.Service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.MissingNode;
import com.kiya.admin.common.ApiException;
import com.kiya.admin.config.KiyaProperties;

@Service
public class OperationsQueueService {
    private static final Pattern JOB = Pattern.compile("[a-z][a-z0-9_-]{0,31}-[0-9]{1,17}\\.json");
    private static final Pattern HEADER = Pattern.compile("<BililiveRecorderRecordInfo\\b([^>]+)>");
    private static final Pattern ATTR = Pattern.compile("(\\w+)=\"([^\"]*)\"");
    private static final DateTimeFormatter SESSION = DateTimeFormatter.ofPattern("yyyyMMdd-HHmm");
    private static final List<String> RESUMABLE = List.of("partial", "retry", "blocked");
    private final WorkerPaths paths;
    private final ObjectMapper json;
    private final KiyaProperties properties;

    public OperationsQueueService(WorkerPaths paths, ObjectMapper json, KiyaProperties properties) {
        this.paths = paths;
        this.json = json;
        this.properties = properties;
    }

    public Map<String, Map<String, Object>> list() {
        Catalog catalog = catalog();
        Map<String, Map<String, Object>> out = new LinkedHashMap<>();
        for (String name : catalog.names()) out.put(name, view(name, catalog));
        return out;
    }

    public Map<String, Object> summary(String name) {
        return view(paths.checkSession(name), catalog());
    }

    public Map<String, Object> recover(String name, String action) throws IOException {
        name = paths.checkSession(name);
        Catalog catalog = catalog();
        Map<String, Object> view = view(name, catalog);
        if (!action.equals(view.get("action"))) {
            if (("enqueue".equals(action) && "matched".equals(view.get("identity")))
                    || ("resume".equals(action) && Boolean.TRUE.equals(view.get("resumeRequested")))) return view;
            throw ApiException.badRequest("场次状态已变化或不能安全恢复，请刷新后核对");
        }
        Path root = paths.operationsRoot();
        String id = (String) view.get("jobId");
        if ("enqueue".equals(action)) {
            Entry ended = catalog.ended(name).get(0);
            Path target = root.resolve("queue").resolve(ended.id());
            Files.createDirectories(target.getParent());
            // Both the monitor and the dashboard publish the same immutable event under the same name.
            linkOnce(target, root.resolve("ended").resolve(ended.id()));
        } else {
            Path marker = root.resolve("status").resolve(id + ".resume");
            Files.createDirectories(marker.getParent());
            try { Files.createFile(marker); } catch (FileAlreadyExistsException ignored) { }
        }
        return summary(name);
    }

    private void linkOnce(Path target, Path source) throws IOException {
        try { Files.createLink(target, source); } catch (FileAlreadyExistsException ignored) { }
    }

    public Path media(String name, int rank) {
        String session = paths.checkSession(name);
        Catalog catalog = catalog();
        List<Entry> jobs = catalog.jobs(session);
        if (jobs.size() != 1 || rank < 1 || catalog.unresolved.stream().anyMatch(e -> couldBe(session, e.job()))) {
            throw ApiException.notFound("成片不存在或待核对");
        }
        Entry entry = jobs.get(0);
        JsonNode ranks = entry.state().path("production_ranks");
        if (ranks.isArray() && !ranks.isEmpty()
                && java.util.stream.StreamSupport.stream(ranks.spliterator(), false).noneMatch(r -> r.asInt() == rank)) {
            throw ApiException.notFound("成片不存在");
        }
        Path file = inventoryMedia(candidateDir(entry, rank));
        if (file == null) {
            for (JsonNode row : entry.state().path("inventory")) {
                if (row.path("rank").asInt() == rank) file = legacyMedia(entry, row);
            }
        }
        if (file == null) throw ApiException.notFound("成片不存在");
        return file;
    }

    private Map<String, Object> view(String session, Catalog catalog) {
        List<Entry> jobs = catalog.jobs(session);
        List<Entry> ended = catalog.ended(session);
        boolean uncertain = catalog.unresolved.stream().anyMatch(e -> couldBe(session, e.job()));
        if (jobs.size() > 1 || uncertain || (jobs.isEmpty() && ended.size() != 1)) {
            boolean available = Files.isReadable(paths.operationsRoot()) && Files.isDirectory(paths.operationsRoot())
                    && (Files.isReadable(paths.operationsRoot().resolve("queue")) || Files.isReadable(paths.operationsRoot().resolve("ended")));
            String reason = !available ? "运营队列目录不可读或尚无记录，无法核对本场"
                    : jobs.size() > 1 || uncertain ? "任务与录播场次未能唯一匹配，请核对房间和下播时间"
                    : "没有唯一的下播与录播匹配记录；不能确认漏入队";
            return Map.of("status", "verify", "identity", "uncertain", "message", reason, "candidates", List.of());
        }
        boolean writable = properties.isOpsRecoveryEnabled() && !properties.getOpsDir().isBlank();
        if (jobs.isEmpty()) {
            Entry evidence = ended.get(0);
            double now = Instant.now().toEpochMilli() / 1000d;
            boolean ready = now >= evidence.job().path("ended").asDouble() + 120 && evidence.ready();
            Map<String, Object> view = new LinkedHashMap<>();
            view.put("status", "not_queued");
            view.put("identity", "confirmed_end");
            view.put("room", evidence.job().path("room").asText());
            view.put("updatedAt", modified(evidence.file()).toString());
            view.put("message", ready ? "已确认下播，未找到自动任务" : "已记录下播，等待录播文件完整且稳定");
            view.put("candidates", List.of());
            if (writable && ready) view.put("action", "enqueue");
            return view;
        }
        Entry entry = jobs.get(0);
        JsonNode state = entry.state();
        String worker = state.path("status").asText("queued");
        boolean resumeRequested = Files.isRegularFile(paths.operationsRoot().resolve("status").resolve(entry.id() + ".resume"));
        List<Map<String, Object>> candidates = new ArrayList<>();
        Instant updated = modified(entry.file());
        updated = later(updated, modified(statusPath(entry.id())));
        String step = state.path("stage").asText(worker);
        Instant lastCandidate = Instant.EPOCH;
        int clips = 0, completed = 0, reviews = 0;
        boolean paused = false;
        Path production = production(entry);
        JsonNode summary = read(production.resolve("summary-selected.json"));
        if (summary.isMissingNode()) summary = read(production.resolve("summary.json"));
        Map<Integer, JsonNode> outcomes = new LinkedHashMap<>();
        for (JsonNode candidate : summary.path("candidates")) outcomes.put(candidate.path("rank").asInt(), candidate);
        TreeSet<Integer> ranks = new TreeSet<>();
        for (JsonNode rank : state.path("production_ranks")) ranks.add(rank.asInt());
        if (ranks.isEmpty()) ranks.addAll(outcomes.keySet());
        Map<Integer, JsonNode> legacy = new LinkedHashMap<>();
        if (!"isolated".equals(state.path("production_mode").asText())) {
            for (JsonNode item : state.path("inventory")) legacy.put(item.path("rank").asInt(), item);
            ranks.addAll(legacy.keySet());
        }
        for (int rank : ranks) {
            if (rank < 1) continue;
            Path dir = candidateDir(entry, rank);
            Path stateFile = dir.resolve("state.json");
            JsonNode candidate = read(stateFile);
            String result = candidate.path("status").asText(outcomes.getOrDefault(rank, MissingNode.getInstance())
                    .path("status").asText("pending"));
            JsonNode old = legacy.get(rank);
            boolean media = old == null ? inventoryMedia(dir) != null : legacyMedia(entry, old) != null;
            if (old != null) result = "qualified".equals(old.path("decision").asText()) ? "inventoried" : "needs_review";
            if (media) clips++;
            if (List.of("inventoried", "dropped", "needs_review", "failed").contains(result)) completed++;
            if (List.of("needs_review", "failed").contains(result)) reviews++;
            if ("paused".equals(result)) paused = true;
            String stage = candidate.path("stage").asText("");
            Instant changed = modified(stateFile);
            if (!stage.isBlank() && changed.isAfter(lastCandidate)) {
                lastCandidate = changed;
                if ("produce".equals(state.path("stage").asText())) step = stage;
            }
            updated = later(updated, later(changed, modified(dir.resolve("inventory.json"))));
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("rank", rank); row.put("status", result); row.put("currentStep", stage);
            row.put("hasMedia", media);
            row.put("findings", List.of("paused", "failed", "needs_review").contains(result)
                    ? findings(candidate) : List.of());
            candidates.add(row);
        }
        String display = switch (worker) {
            case "running" -> "processing";
            case "waiting_for_recording" -> "waiting_for_resources";
            case "blocked", "retry" -> paused ? "waiting_for_resources" : "failed";
            case "partial" -> paused ? "waiting_for_resources" : "needs_review";
            case "prepared" -> reviews > 0 ? "needs_review" : clips > 0 ? "has_clips"
                    : (!ranks.isEmpty() && completed == ranks.size() ? "no_candidates" : "prepared");
            default -> worker;
        };
        Map<String, Object> view = new LinkedHashMap<>();
        view.put("identity", "matched"); view.put("jobId", entry.id());
        view.put("room", entry.job().path("room").asText()); view.put("workerStatus", worker);
        view.put("status", display); view.put("currentStep", step); view.put("updatedAt", updated.toString());
        view.put("completedCandidates", completed); view.put("totalCandidates", ranks.size());
        view.put("clipCount", clips); view.put("needsReviewCount", reviews); view.put("candidates", candidates);
        view.put("resumeRequested", resumeRequested);
        JsonNode budget = state.path("budget");
        if (budget.isObject()) view.put("budget", Map.of("bookedFen", budget.path("booked_fen").asInt(),
                "remainingFen", budget.path("available_fen").asInt(), "day", budget.path("day").asText("")));
        if (state.path("retry_at").isNumber()) view.put("retryAt", Instant.ofEpochSecond(state.path("retry_at").asLong()).toString());
        String message = switch (display) {
            case "waiting_for_budget" -> "预算账本拒绝预留，等待下个预算周期";
            case "waiting_for_resources" -> "等待录播或制作资源；请查看候选卡点";
            case "failed" -> "处理失败；请核对当前步骤及候选诊断";
            case "needs_review" -> "部分候选需要复核，已完成的成片保留";
            default -> "";
        };
        view.put("message", message);
        if (List.of("failed", "waiting_for_resources").contains(display) && state.path("error").isTextual()) {
            String error = state.path("error").asText();
            view.put("blocker", error.length() > 400 ? error.substring(0, 400) : error);
        }
        if (writable && !resumeRequested && RESUMABLE.contains(worker)
                && "isolated".equals(state.path("production_mode").asText())) view.put("action", "resume");
        return view;
    }

    private List<Map<String, Object>> findings(JsonNode state) {
        List<Map<String, Object>> found = new ArrayList<>();
        for (var entry = state.path("steps").fields(); entry.hasNext();) {
            var checkpoint = entry.next();
            if (!checkpoint.getKey().matches("evidence-[0-9]+")) continue;
            JsonNode evidence = checkpoint.getValue().path("result");
            for (JsonNode gap : evidence.path("media_gaps")) {
                if (!gap.isArray() || gap.size() != 2 || !gap.get(0).isNumber() || !gap.get(1).isNumber()) continue;
                JsonNode question = evidence.path("question");
                String label = question.path("question").asText(question.isTextual() ? question.asText() : "关键上下文");
                String missing = "response".equals(question.path("kind").asText())
                        ? "缺少可读取的原声" : "缺少可读取的连续原片证据";
                found.add(Map.of("step", checkpoint.getKey(), "message", label + "：" + missing,
                        "range", gap, "timeline", "source"));
            }
        }
        String stage = state.path("stage").asText();
        JsonNode decision = state.path("steps").path(stage).path("result");
        if (stage.matches("review-[0-9]+") && decision.path("output_range").isArray()
                && decision.path("output_range").size() == 2 && !"pass".equals(decision.path("verdict").asText())) {
            found.add(Map.of("step", stage, "message", decision.path("reason").asText("审片发现问题"),
                    "range", decision.path("output_range"), "timeline", "output"));
        }
        if (!found.isEmpty()) return found;
        JsonNode events = state.path("events");
        for (int i = events.size() - 1; i >= 0; i--) {
            JsonNode event = events.get(i);
            if (!List.of("paused", "failed", "needs_review").contains(event.path("status").asText())) continue;
            Map<String, Object> issue = new LinkedHashMap<>();
            issue.put("step", event.path("stage").asText());
            issue.put("message", event.path("reason").asText("请核对本步骤"));
            if (event.path("range").isArray()) issue.put("range", event.path("range"));
            found.add(issue);
            break;
        }
        return found;
    }

    private Catalog catalog() {
        Path root = paths.operationsRoot();
        List<Entry> jobs = new ArrayList<>(), ended = new ArrayList<>(), unresolved = new ArrayList<>();
        Map<Path, List<Recording>> recordings = new LinkedHashMap<>();
        for (String kind : List.of("queue", "ended")) {
            for (Path file : files(root.resolve(kind))) {
                String id = file.getFileName().toString();
                JsonNode job = read(file);
                if (!JOB.matcher(id).matches() || !paths.streamer().equals(job.path("streamer").asText())
                        || !id.startsWith(paths.streamer() + "-") || !job.path("room").asText().matches("[0-9]+")) continue;
                JsonNode state = "queue".equals(kind) ? read(statusPath(id)) : MissingNode.getInstance();
                String session = state.path("session").asText("");
                boolean ready = false;
                if (session.isBlank()) {
                    Path recorder = under(job.path("rec_dir").asText(), paths.recDir());
                    List<Recording> matches = recorder == null ? List.of() : recordings
                            .computeIfAbsent(recorder, this::recordings).stream().filter(r -> overlaps(job, r)).toList();
                    if (!matches.isEmpty() && sameGroup(matches)) {
                        session = matches.get(0).start().format(SESSION);
                        ready = matches.stream().allMatch(Recording::ready);
                    }
                }
                Entry entry = new Entry(file, id, session, job, state, ready);
                if ("queue".equals(kind)) {
                    if (session.isBlank()) unresolved.add(entry); else jobs.add(entry);
                } else if (!session.isBlank()) ended.add(entry);
            }
        }
        return new Catalog(jobs, ended, unresolved);
    }

    private boolean overlaps(JsonNode job, Recording recording) {
        double start = recording.start().toEpochSecond();
        return recording.room().equals(job.path("room").asText()) && start >= earliest(job)
                && start <= job.path("ended").asDouble();
    }

    private double earliest(JsonNode job) { return job.path("earliest").asDouble(job.path("since").asDouble() - 300); }

    private boolean couldBe(String session, JsonNode job) {
        try {
            long start = java.time.LocalDateTime.parse(session, SESSION).atOffset(java.time.ZoneOffset.ofHours(8)).toEpochSecond();
            return start + 59 >= earliest(job) && start <= job.path("ended").asDouble();
        } catch (RuntimeException e) { return true; }
    }

    private boolean sameGroup(List<Recording> matches) {
        for (int i = 1; i < matches.size(); i++) {
            long diff = matches.get(i).start().toEpochSecond() - matches.get(i - 1).start().toEpochSecond();
            if (diff <= 0 || diff > 7200) return false;
        }
        return true;
    }

    private Path under(String raw, Path root) {
        if (raw.isBlank()) return null;
        try {
            Path file = Path.of(raw).toRealPath();
            return file.startsWith(root.toRealPath()) ? file : null;
        } catch (IOException | RuntimeException e) { return null; }
    }

    private List<Recording> recordings(Path recorder) {
        List<Recording> result = new ArrayList<>();
        try (var walk = Files.walk(recorder)) {
            walk.filter(p -> p.toString().endsWith(".xml") && Files.isRegularFile(p)).forEach(xml -> {
                try (var reader = Files.newBufferedReader(xml, StandardCharsets.UTF_8)) {
                    char[] head = new char[16384];
                    int size = reader.read(head);
                    if (size < 1) return;
                    var header = HEADER.matcher(new String(head, 0, size));
                    if (!header.find()) return;
                    var attributes = ATTR.matcher(header.group(1));
                    Map<String, String> values = new LinkedHashMap<>();
                    while (attributes.find()) values.put(attributes.group(1), attributes.group(2));
                    Path video = xml.resolveSibling(xml.getFileName().toString().replaceFirst("\\.xml$", ".flv"));
                    Instant stable = Instant.now().minusSeconds(90);
                    boolean ready = Files.isRegularFile(video) && Files.size(video) > 0
                            && modified(xml).isBefore(stable) && modified(video).isBefore(stable);
                    result.add(new Recording(values.getOrDefault("roomid", ""), OffsetDateTime.parse(values.get("start_time")), ready));
                } catch (IOException | RuntimeException ignored) { }
            });
        } catch (IOException | java.io.UncheckedIOException ignored) { }
        return result.stream().sorted(Comparator.comparing(r -> r.start().toInstant())).toList();
    }

    private Path legacyMedia(Entry entry, JsonNode item) {
        if ("isolated".equals(entry.state().path("production_mode").asText())) return null;
        Path session = paths.operationsRoot().resolve("sessions").resolve(entry.id().replaceFirst("\\.json$", ""))
                .resolve(paths.streamer()).resolve(paths.checkSession(entry.session()));
        Path quality = under(item.path("quality").asText(), session);
        if (quality == null || under(quality.toString(), paths.operationsRoot()) == null) return null;
        return mp4(under(read(quality).path("media").asText(), session));
    }

    private Path mp4(Path file) {
        return file != null && Files.isRegularFile(file) && file.getFileName().toString().endsWith(".mp4") ? file : null;
    }

    private Path inventoryMedia(Path dir) {
        JsonNode inventory = read(dir.resolve("inventory.json"));
        JsonNode render = inventory.path("render");
        String raw = render.path("media").asText("");
        if (raw.isBlank()) return null;
        JsonNode state = read(dir.resolve("state.json"));
        if (!"inventoried".equals(state.path("status").asText())
                || !Objects.equals(inventory.path("version").asText(null), state.path("current_version").asText(null))
                || !inventory.path("version").isTextual()) return null;
        try {
            Path file = Path.of(raw).toRealPath();
            return file.startsWith(dir.toRealPath()) && file.startsWith(paths.operationsRoot().toRealPath())
                    && Files.isRegularFile(file) && file.getFileName().toString().endsWith(".mp4") ? file : null;
        } catch (IOException | RuntimeException e) { return null; }
    }

    private Path production(Entry entry) { return paths.operationsRoot().resolve("production").resolve(entry.id().replaceFirst("\\.json$", "")); }
    private Path candidateDir(Entry entry, int rank) { return production(entry).resolve(String.format(java.util.Locale.ROOT, "%02d", rank)); }
    private Path statusPath(String id) { return paths.operationsRoot().resolve("status").resolve(id); }
    private Instant later(Instant a, Instant b) { return a.isAfter(b) ? a : b; }
    private Instant modified(Path p) {
        try { return Files.getLastModifiedTime(p).toInstant(); } catch (IOException e) { return Instant.EPOCH; }
    }
    private List<Path> files(Path dir) {
        try (var stream = Files.list(dir)) { return stream.filter(Files::isRegularFile).toList(); }
        catch (IOException e) { return List.of(); }
    }
    private JsonNode read(Path path) {
        try { return Files.isRegularFile(path) ? json.readTree(path.toFile()) : MissingNode.getInstance(); }
        catch (IOException e) { return MissingNode.getInstance(); }
    }

    private record Recording(String room, OffsetDateTime start, boolean ready) {}
    private record Entry(Path file, String id, String session, JsonNode job, JsonNode state, boolean ready) {}
    private record Catalog(List<Entry> entries, List<Entry> endings, List<Entry> unresolved) {
        List<Entry> jobs(String name) { return entries.stream().filter(e -> name.equals(e.session())).toList(); }
        List<Entry> ended(String name) { return endings.stream().filter(e -> name.equals(e.session())).toList(); }
        List<String> names() {
            return java.util.stream.Stream.concat(entries.stream(), endings.stream()).map(Entry::session)
                    .filter(s -> s.matches("[0-9A-Za-z_-]{1,64}")).distinct().toList();
        }
    }
}
