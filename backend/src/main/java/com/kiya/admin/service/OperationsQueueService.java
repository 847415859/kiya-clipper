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

    public Map<String, Object> productionOverview() { return overview(paths.operationsRoot(), MissingNode.getInstance()); }

    // Historical tasks have explicit identities, never monitor/recording name matching.
    public Map<String, Object> historicalDetail(Path root, String id, String session) {
        Entry entry = new Entry(root, root.resolve("queue").resolve(id + ".json"), id + ".json", session,
                read(root.resolve("queue").resolve(id + ".json")), read(root.resolve("status").resolve(id + ".json")), true);
        Catalog catalog = new Catalog(root, false, List.of(entry), List.of(), List.of());
        Map<String, Object> result = new LinkedHashMap<>(view(session, catalog));
        result.remove("action");
        result.put("timeline", timeline(catalog, session));
        List<Map<String, Object>> historicalRows = candidateRows(entry, session);
        for (Map<String, Object> row : historicalRows) {
            int rank = (Integer) row.get("rank");
            Path dir = candidateDir(entry, rank);
            JsonNode candidate = read(dir.resolve("state.json"));
            String currentVersion = candidate.path("current_version").asText();
            Path scoped = historicalVersion(dir, currentVersion, root);
            if ("fixture".equals(entry.job().path("mode").asText())) { row.put("playable", false); row.put("accepted", false); row.put("simulation", true); }
            if (scoped != null) {
                Historical current = historical(candidate, dir, scoped, currentVersion);
                row.put("previewPlayable", current.playable()); row.put("simulation", current.simulation()); row.put("accepted", false);
                if (Boolean.TRUE.equals(current.simulation()) || "fixture".equals(entry.job().path("mode").asText())) row.put("playable", false);
                row.put("review", current.review()); row.put("quality", current.quality()); row.put("evidence", current.receipt());
            }
        }
        result.put("candidates", historicalRows);
        if ("fixture".equals(entry.job().path("mode").asText())) {
            result.put("clipCount", 0);
            result.put("previewClipCount", historicalRows.stream().filter(r -> Boolean.TRUE.equals(r.get("previewPlayable"))).count());
        }
        JsonNode state = entry.state();
        if (state.path("reason").isTextual() && !state.path("reason").asText().isBlank()) {
            result.put("blocker", state.path("reason").asText()); result.put("message", state.path("reason").asText());
        }
        if (state.path("phase").isTextual()) result.put("currentStep", state.path("phase").asText());
        if (!state.path("events").isArray()) {
            Map<String, Object> event = new LinkedHashMap<>();
            event.put("stage", state.path("phase").asText(state.path("stage").asText("queue")));
            event.put("status", state.path("status").asText("queued"));
            event.put("at", state.path("updated_at").asText(modified(entry.file()).toString()));
            event.put("reason", state.path("reason").asText(state.path("error").asText("")));
            result.put("timeline", List.of(event));
        }
        return sanitized(result);
    }

    public Path historicalMedia(Path root, String id, String session, int rank, String version) {
        Entry entry = new Entry(root, root.resolve("queue").resolve(id + ".json"), id + ".json", session,
                read(root.resolve("queue").resolve(id + ".json")), read(root.resolve("status").resolve(id + ".json")), true);
        Path dir = candidateDir(entry, rank);
        if (version == null) {
            Path current = inventoryMedia(entry, dir);
            if (current != null) return current;
            version = read(dir.resolve("state.json")).path("current_version").asText();
        }
        Path scoped = historicalVersion(dir, version, root);
        if (scoped != null) {
            Historical result = historical(read(dir.resolve("state.json")), dir, scoped, version);
            if (result.playable()) return result.media();
        }
        throw ApiException.notFound("历史试跑媒体或报告绑定无效");
    }

    public Map<String, Object> acceptanceOverview() {
        Path directory = acceptanceDirectory();
        return overview(directory.resolve("operations"), read(directory.resolve("monitor-state.json")));
    }

    private Map<String, Object> overview(Path root, JsonNode monitor) {
        Catalog catalog = catalog(root, false);
        List<Map<String, Object>> sessions = new ArrayList<>();
        for (String name : catalog.names().stream().sorted(Comparator.reverseOrder()).toList()) {
            Map<String, Object> session = new LinkedHashMap<>(view(name, catalog));
            session.put("name", name);
            sessions.add(session);
        }
        Map<String, Object> out = new LinkedHashMap<>(publicMonitor(monitor));
        out.put("monitor", publicMonitor(monitor));
        out.put("sessions", sessions);
        out.put("unmatched", catalog.unresolved().size());
        out.put("unmatchedTasks", catalog.unresolved().stream().map(e -> {
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("jobId", e.id()); row.put("room", e.job().path("room").asText());
            row.put("status", "verify"); row.put("message", "录播缺失、时间范围冲突或多个场次，待核对");
            row.put("queued", e.file().getParent().getFileName().toString().equals("queue"));
            row.put("observedStart", epoch(e.job().path("since")));
            row.put("observedEnd", epoch(e.job().path("ended")));
            return row;
        }).toList());
        return sanitized(out);
    }

    public Map<String, Object> acceptanceSummary(String name) { return readOnlySummary(name, true); }

    public Map<String, Object> readOnlySummary(String name, boolean acceptance) {
        name = paths.checkSession(name);
        Catalog catalog = catalog(acceptance ? acceptanceDirectory().resolve("operations") : paths.operationsRoot(), false);
        Map<String, Object> result = new LinkedHashMap<>(view(name, catalog));
        result.remove("action");
        Path directory = acceptanceDirectory();
        JsonNode monitor = acceptance ? read(directory.resolve("monitor-state.json")) : MissingNode.getInstance();
        result.put("monitor", publicMonitor(monitor));
        result.put("timeline", timeline(catalog, name));
        result.put("match", matchEvidence(catalog, name));
        result.put("candidates", candidateDetails(catalog, name));
        result.put("source", acceptance ? "acceptance" : "production");
        return sanitized(result);
    }

    private Map<String, Object> publicMonitor(JsonNode monitor) {
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("lastCheck", monitor.path("last_check").isTextual() ? monitor.path("last_check").asText() : null);
        out.put("timeBasis", monitor.path("last_check").isTextual() ? "local wall clock; timezone not recorded" : "unknown");
        out.put("live", monitor.path("live").isBoolean() && monitor.path("api_fail").asInt(-1) == 0
                ? monitor.path("live").asBoolean() : null);
        out.put("apiFailures", monitor.path("api_fail").isNumber() ? monitor.path("api_fail").asInt() : null);
        out.put("observed", monitor.path("last_check").isTextual());
        out.put("lastKnownLive", monitor.path("live").isBoolean() ? monitor.path("live").asBoolean() : null);
        out.put("stale", monitor.path("api_fail").asInt(0) > 0);
        out.put("liveSince", epoch(monitor.path("live_since")));
        out.put("previousLiveEnd", epoch(monitor.path("previous_live_end")));
        return out;
    }

    private Map<String, Object> matchEvidence(Catalog catalog, String session) {
        Map<String, Object> out = new LinkedHashMap<>();
        List<Entry> jobs = catalog.jobs(session);
        List<Entry> ended = catalog.ended(session);
        out.put("endedCount", ended.size());
        out.put("queueCount", jobs.size());
        out.put("unique", jobs.size() == 1 && ended.size() <= 1
                && catalog.unresolved.stream().noneMatch(e -> couldBe(session, e.job())));
        if (!ended.isEmpty()) {
            Entry e = ended.get(0);
            out.put("endedAt", e.job().path("ended").isNumber()
                    ? Instant.ofEpochSecond(e.job().path("ended").asLong()).toString() : null);
            out.put("room", e.job().path("room").asText(""));
            out.put("source", "monitor observation");
        }
        if (!jobs.isEmpty()) out.put("jobId", jobs.get(0).id());
        if (!out.containsKey("jobId")) out.put("jobId", null);
        return out;
    }

    private List<Map<String, Object>> timeline(Catalog catalog, String session) {
        List<Map<String, Object>> out = new ArrayList<>();
        for (Entry entry : catalog.jobs(session)) {
            JsonNode state = entry.state();
            for (JsonNode event : state.path("events")) {
                Map<String, Object> row = new LinkedHashMap<>();
                row.put("stage", event.path("stage").asText(""));
                row.put("status", event.path("status").asText(""));
                row.put("at", event.path("at").asText(""));
                if (event.path("reason").isTextual()) row.put("reason", clean(event.path("reason").asText()));
                out.add(row);
            }
            if (out.isEmpty()) {
                Map<String, Object> row = new LinkedHashMap<>();
                row.put("stage", state.path("stage").asText("queue"));
                row.put("status", state.path("status").asText("queued"));
                row.put("at", modified(entry.file()).toString());
                out.add(row);
            }
        }
        out.sort(Comparator.comparing(x -> String.valueOf(x.get("at"))));
        return out;
    }

    private List<Map<String, Object>> candidateDetails(Catalog catalog, String session) {
        List<Map<String, Object>> out = new ArrayList<>();
        for (Entry entry : catalog.jobs(session)) {
            for (Map<String, Object> row : candidateRows(entry, session)) out.add(row);
        }
        return out;
    }

    private List<Map<String, Object>> candidateRows(Entry entry, String session) {
        List<Map<String, Object>> out = new ArrayList<>();
        JsonNode state = entry.state();
        TreeSet<Integer> ranks = candidateRanks(entry);
        for (int rank : ranks) {
            Path dir = candidateDir(entry, rank);
            JsonNode candidate = read(dir.resolve("state.json"));
            JsonNode inventory = read(dir.resolve("inventory.json"));
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("rank", rank);
            row.put("status", candidate.path("status").asText("pending"));
            row.put("stage", candidate.path("stage").asText(""));
            row.put("version", candidate.path("current_version").asText(inventory.path("version").asText("")));
            row.put("hasInventory", inventory.isObject());
            boolean currentInventory = inventory.path("version").isTextual()
                    && inventory.path("version").asText().equals(candidate.path(candidate.path("qualified_version").isTextual()
                    ? "qualified_version" : "current_version").asText());
            JsonNode render = currentInventory ? inventory.path("render") : candidate.path("render");
            row.put("review", boundReview(currentInventory ? inventory.path("review") : MissingNode.getInstance(), render, dir));
            row.put("quality", currentInventory ? report(render, "quality", dir) : Map.of("status", "unknown", "bound", false));
            row.put("evidence", currentInventory ? report(render, "receipt", dir) : Map.of("status", "unknown", "bound", false));
            row.put("events", candidate.path("events").isArray() ? candidate.path("events") : null);
            row.put("steps", candidate.path("steps").isObject() ? candidate.path("steps") : null);
            row.put("attempts", candidate.path("attempts").isArray() ? candidate.path("attempts") : null);
            row.put("versions", versions(candidate, dir, entry, session, rank));
            row.put("inventoryVersion", inventory.path("version").asText(null));
            row.put("budget", budget(candidate));
            row.put("findings", findings(candidate));
            Path media = inventoryMedia(entry, dir);
            if (!inventory.has("rank")) media = null;
            row.put("playable", media != null);
            if (media != null) row.put("mediaUrl", "/api/sessions/" + session
                    + (entry.root().toAbsolutePath().normalize().equals(acceptanceDirectory().resolve("operations"))
                    ? "/acceptance-operations/media/" : "/operations/media/") + rank);
            out.add(row);
        }
        return out;
    }

    private Map<String, Object> publicReview(JsonNode review) {
        if (!review.isObject()) return Map.of("status", "unknown");
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("status", review.path("verdict").asText("unknown"));
        if (review.path("reason").isTextual()) out.put("reason", clean(review.path("reason").asText()));
        if (review.path("output_range").isArray()) { out.put("range", review.path("output_range")); out.put("timeline", "output"); }
        if (review.path("action").isTextual()) out.put("action", review.path("action").asText());
        return out;
    }

    private Map<String, Object> publicQuality(JsonNode quality) {
        if (!quality.isObject()) return Map.of("status", "unknown");
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("status", quality.path("ok").isBoolean() ? (quality.path("ok").asBoolean() ? "pass" : "fail") : "unknown");
        if (quality.path("checks").isObject()) out.put("checks", quality.path("checks"));
        if (quality.path("errors").isArray()) out.put("errors", quality.path("errors"));
        return out;
    }

    private TreeSet<Integer> candidateRanks(Entry entry) {
        TreeSet<Integer> ranks = new TreeSet<>();
        for (JsonNode rank : entry.state().path("production_ranks")) if (rank.asInt() > 0) ranks.add(rank.asInt());
        JsonNode summary = read(production(entry).resolve("summary-selected.json"));
        if (!summary.isObject()) summary = read(production(entry).resolve("summary.json"));
        for (JsonNode row : summary.path("candidates")) if (row.path("rank").asInt() > 0) ranks.add(row.path("rank").asInt());
        for (Path dir : directories(production(entry))) {
            if (dir.getFileName().toString().matches("[0-9]{2,6}")) {
                int rank = Integer.parseInt(dir.getFileName().toString());
                if (rank > 0) ranks.add(rank);
            }
        }
        return ranks;
    }

    private Map<String, Object> budget(JsonNode state) {
        Map<String, Object> out = new LinkedHashMap<>();
        JsonNode b = state.path("budget");
        out.put("day", b.path("day").isTextual() ? b.path("day").asText() : null);
        out.put("bookedFen", b.path("booked_fen").isNumber() ? b.path("booked_fen").numberValue() : null);
        out.put("remainingFen", b.path("available_fen").isNumber() ? b.path("available_fen").numberValue() : null);
        out.put("attempts", state.path("attempts").isArray() ? state.path("attempts") : null);
        out.put("requestAttempts", null); // logical attempts are not HTTP request counts
        out.put("unknownCost", true);
        return out;
    }

    private List<Map<String, Object>> versions(JsonNode state, Path dir, Entry entry, String session, int rank) {
        List<Map<String, Object>> rows = new ArrayList<>();
        for (Path version : directories(dir.resolve("versions"))) {
            String id = version.getFileName().toString();
            Path scoped = historicalVersion(dir, id, entry.root());
            if (scoped == null) continue;
            JsonNode draft = read(scoped.resolve("draft.json"));
            Historical historical = historical(state, dir, scoped, id);
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("version", id);
            row.put("current", id.equals(state.path("current_version").asText()));
            row.put("playable", historical.playable());
            row.put("renderBound", historical.playable());
            row.put("mediaSha256", historical.playable() ? sha256(historical.media()) : null);
            String route = entry.root().toAbsolutePath().normalize().equals(acceptanceDirectory().resolve("operations"))
                    ? "/acceptance-operations/media/" : "/operations/media/";
            row.put("mediaUrl", historical.playable() ? "/api/sessions/" + session + route + rank + "/version/" + id : null);
            row.put("review", historical.review());
            row.put("reviewBound", Boolean.TRUE.equals(historical.review().get("bound")));
            row.put("simulation", historical.simulation());
            row.put("accepted", false); // Historical comparison is never inventory acceptance.
            row.put("quality", historical.quality());
            row.put("evidence", historical.receipt());
            row.put("sourceMapping", historical.playable() ? historical.mapping() : null);
            row.put("mapping", historical.playable() ? historical.mapping() : null);
            row.put("parent", draft.path("parent").isTextual() ? draft.path("parent").asText() : null);
            row.put("createdAt", draft.path("created_at").asText(null));
            row.put("draft", draft);
            rows.add(row);
        }
        return rows;
    }

    private Path historicalVersion(Path dir, String version, Path root) {
        if (version == null || !version.matches("[a-fA-F0-9]{64}")) return null;
        Path candidate = under(dir.toString(), root);
        if (candidate == null) return null;
        Path versions = under(dir.resolve("versions").toString(), candidate);
        if (versions == null) return null;
        Path scoped = under(dir.resolve("versions").resolve(version).toString(), versions);
        if (scoped == null || !scoped.equals(versions.resolve(version))) return null;
        Path manifest = historicalPath(scoped.resolve("draft.json").toString(), dir, scoped);
        return manifest != null && version.equals(read(manifest).path("id").asText()) ? scoped : null;
    }

    // Resolve both worker Windows paths and portable candidate-relative paths; enforce lexical AND real containment.
    private Path historicalPath(String raw, Path dir, Path scoped) {
        if (raw == null || raw.isBlank()) return null;
        try {
            Path path = Path.of(raw.replace('\\', '/'));
            if (!path.isAbsolute()) path = dir.resolve(path);
            path = path.toAbsolutePath().normalize();
            Path lexicalScope = scoped.toAbsolutePath().normalize();
            if (!path.startsWith(lexicalScope)) return null;
            Path real = path.toRealPath();
            return real.startsWith(scoped.toRealPath()) && Files.isRegularFile(real) ? real : null;
        } catch (IOException | RuntimeException e) { return null; }
    }

    private boolean historicalArtifact(JsonNode render, Path target, Path dir, Path scoped) {
        if (target == null) return false;
        for (var fields = render.path("files").fields(); fields.hasNext();) {
            var field = fields.next();
            Path file = historicalPath(field.getKey(), dir, scoped);
            if (target.equals(file) && field.getValue().asText().matches("[a-fA-F0-9]{64}")
                    && field.getValue().asText().equalsIgnoreCase(sha256(file))) return true;
        }
        return false;
    }

    private Historical historical(JsonNode state, Path dir, Path scoped, String version) {
        JsonNode render = MissingNode.getInstance();
        String renderStep = "";
        Path media = null;
        for (var fields = state.path("steps").fields(); fields.hasNext();) {
            var field = fields.next();
            JsonNode result = field.getValue().path("result");
            Path file = historicalPath(result.path("media").asText(""), dir, scoped);
            if (field.getKey().matches("render-[0-9]+") && file != null
                    && (!result.has("version") || version.equals(result.path("version").asText()))) {
                render = result; media = mp4(file); renderStep = field.getKey();
            }
        }
        boolean valid = media != null && historicalArtifact(render, media, dir, scoped);
        for (var fields = render.path("files").fields(); fields.hasNext();) {
            var field = fields.next();
            Path file = historicalPath(field.getKey(), dir, scoped);
            valid &= file != null && field.getValue().asText().matches("[a-fA-F0-9]{64}")
                    && field.getValue().asText().equalsIgnoreCase(sha256(file));
        }
        Path qualityFile = historicalPath(render.path("quality").asText(""), dir, scoped);
        Path receiptFile = historicalPath(render.path("receipt").asText(""), dir, scoped);
        JsonNode quality = qualityFile == null ? MissingNode.getInstance() : read(qualityFile);
        JsonNode receipt = receiptFile == null ? MissingNode.getInstance() : read(receiptFile);
        boolean qualityBound = valid && quality.isObject() && historicalArtifact(render, qualityFile, dir, scoped)
                && media.equals(historicalPath(quality.path("media").asText(""), dir, scoped));
        boolean receiptBound = valid && receipt.isObject() && historicalArtifact(render, receiptFile, dir, scoped);
        if (receipt.has("media")) receiptBound &= media != null && media.equals(historicalPath(receipt.path("media").asText(""), dir, scoped));
        valid &= qualityBound && receiptBound;
        Map<String, Object> q = new LinkedHashMap<>(publicQuality(quality));
        q.put("bound", qualityBound); if (!qualityBound) q.put("status", "unknown");
        Map<String, Object> r = new LinkedHashMap<>(); r.put("bound", receiptBound);
        r.put("status", receiptBound ? receipt.path("status").asText("unknown") : "unknown");
        JsonNode checkpoint = state.path("steps").path(renderStep.replace("render-", "review-"));
        JsonNode review = checkpoint.path("result");
        boolean reviewBound = valid && review.path("media_sha256").asText("").equalsIgnoreCase(sha256(media));
        if (review.has("version_id")) reviewBound &= version.equals(review.path("version_id").asText());
        JsonNode inputs = checkpoint.path("inputs");
        if (inputs.isArray()) reviewBound &= inputs.size() >= 2 && version.equals(inputs.path(0).asText())
                && inputs.path(1).equals(render.path("files"));
        JsonNode binding = checkpoint.path("binding");
        if (binding.isObject()) reviewBound &= version.equals(binding.path("version").asText())
                && binding.path("files").equals(render.path("files"));
        Map<String, Object> reviewView = new LinkedHashMap<>(publicReview(review));
        reviewView.put("bound", reviewBound);
        if (!reviewBound) reviewView.put("status", "unknown");
        String basis = review.path("basis").asText("");
        Boolean simulation = null;
        if (review.path("simulation").isBoolean()) simulation = review.path("simulation").asBoolean();
        else if (basis.contains("fixture") || basis.contains("simulat") || "fixture".equals(binding.path("adapter").asText())) simulation = Boolean.TRUE;
        else if ("actual_mp4_model".equals(basis)) simulation = Boolean.FALSE;
        reviewView.put("basis", basis.isBlank() ? "unknown" : basis);
        return new Historical(media, valid, reviewView, q, r, simulation, sourceMapping(receipt));
    }

    private List<Map<String, Object>> sourceMapping(JsonNode receipt) {
        List<Map<String, Object>> rows = new ArrayList<>();
        double offset = receipt.path("callback_offset").asDouble(0);
        for (JsonNode source : receipt.path("source_to_output")) {
            if (!source.path("output_start").isNumber() || !source.path("output_end").isNumber()) continue;
            Map<String, Object> row = new LinkedHashMap<>();
            for (String key : List.of("source_start", "source_end", "source_index", "segment_index", "kind")) {
                if (source.has(key)) row.put(key, source.get(key));
            }
            row.put("output_start", source.path("output_start").asDouble() + offset);
            row.put("output_end", source.path("output_end").asDouble() + offset);
            rows.add(row);
        }
        return rows;
    }

    private record Historical(Path media, boolean playable, Map<String, Object> review,
                              Map<String, Object> quality, Map<String, Object> receipt, Object simulation, List<Map<String, Object>> mapping) {}

    private Map<String, Object> boundReview(JsonNode review, JsonNode render, Path dir) {
        Map<String, Object> out = new LinkedHashMap<>(publicReview(review));
        boolean bound = mediaHashMatches(render, dir) && review.path("media_sha256").isTextual()
                && review.path("media_sha256").asText().equals(render.path("files").path(render.path("media").asText()).asText());
        out.put("bound", bound);
        if (!bound) out.put("status", "unknown");
        if ("repair".equals(review.path("verdict").asText())
                && (!review.path("action").isTextual() || !review.path("output_range").isArray())) {
            out.put("status", "needs_review"); out.put("missingRepairFields", true);
        }
        return out;
    }

    private Map<String, Object> report(JsonNode render, String key, Path dir) {
        Path file = under(render.path(key).asText(""), dir);
        JsonNode report = file == null ? MissingNode.getInstance() : read(file);
        boolean bound = file != null && mediaHashMatches(render, dir) && artifactMatches(render, file);
        if ("quality".equals(key)) {
            Path media = under(render.path("media").asText(""), dir);
            Path referred = under(report.path("media").asText(""), dir);
            bound = bound && media != null && media.equals(referred);
        }
        Map<String, Object> out = new LinkedHashMap<>("quality".equals(key) ? publicQuality(report) : Map.of("status", report.path("status").asText("unknown")));
        out.put("bound", bound);
        out.put("data", report.isObject() ? report : null);
        if (!bound) out.put("status", "unknown");
        return out;
    }

    private boolean mediaHashMatches(JsonNode render, Path dir) {
        Path media = under(render.path("media").asText(""), dir);
        return media != null && artifactMatches(render, media);
    }

    private boolean artifactMatches(JsonNode render, Path file) {
        String expected = render.path("files").path(file.toString()).asText("");
        if (!expected.matches("[a-fA-F0-9]{64}")) return false;
        return expected.equalsIgnoreCase(sha256(file));
    }

    private String sha256(Path file) {
        try (var input = Files.newInputStream(file)) {
            var digest = java.security.MessageDigest.getInstance("SHA-256");
            byte[] buffer = new byte[65536];
            for (int n; (n = input.read(buffer)) > 0;) digest.update(buffer, 0, n);
            return java.util.HexFormat.of().formatHex(digest.digest());
        } catch (IOException | java.security.NoSuchAlgorithmException e) { return ""; }
    }

    private List<Path> directories(Path dir) {
        try (var stream = Files.list(dir)) { return stream.filter(Files::isDirectory).sorted().toList(); }
        catch (IOException | RuntimeException e) { return List.of(); }
    }

    private String epoch(JsonNode value) {
        try { return value.isNumber() ? Instant.ofEpochMilli((long) (value.asDouble() * 1000)).toString() : null; }
        catch (RuntimeException e) { return null; }
    }

    private String clean(String value) {
        return value.replaceAll("(?i)(?:bearer\\s+)[^\\s,;]+", "[secret]")
                .replaceAll("(?i)(?:cookie|api[_-]?key|token|authorization|password|secret)\\s*[:=]\\s*[^\\s,;]+", "[secret]")
                .replaceAll("(?i)\\bsk-[a-z0-9_-]+", "[secret]")
                .replaceAll("(?i)(?:[A-Z]:[\\\\/]|(?<![a-zA-Z0-9])/(?:[a-zA-Z0-9_.-]+/))[^\\s\\\"'<>]*", "[path]");
    }

    private Object scrub(JsonNode node) {
        if (node.isObject()) {
            Map<String, Object> out = new LinkedHashMap<>();
            node.fields().forEachRemaining(e -> {
                String key = e.getKey();
                if (key.matches("(?i).*(cookie|api.?key|authorization|password|secret|environment|raw_log|request_key|token|credential|access.?token|refresh.?token).*")) out.put(clean(key), "[redacted]");
                else out.put(clean(key), scrub(e.getValue()));
            });
            return out;
        }
        if (node.isArray()) { List<Object> out = new ArrayList<>(); node.forEach(n -> out.add(scrub(n))); return out; }
        if (node.isTextual()) return clean(node.asText());
        if (node.isNumber()) return node.numberValue();
        if (node.isBoolean()) return node.asBoolean();
        return null;
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> sanitized(Map<String, Object> value) {
        return (Map<String, Object>) scrub(json.valueToTree(value));
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> sanitizeMap(Map<String, Object> value) {
        return (Map<String, Object>) scrub(json.valueToTree(value));
    }

    private Path acceptanceDirectory() {
        return Path.of(properties.getAcceptanceDir()).toAbsolutePath().normalize();
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
        return media(name, rank, catalog(), false);
    }

    public Path acceptanceMedia(String name, int rank) {
        return media(name, rank, catalog(acceptanceDirectory().resolve("operations"), false), true);
    }

    public Path versionMedia(String name, int rank, String version, boolean acceptance) {
        String session = paths.checkSession(name);
        Catalog catalog = acceptance ? catalog(acceptanceDirectory().resolve("operations"), false) : catalog();
        List<Entry> jobs = catalog.jobs(session);
        if (jobs.size() != 1 || rank < 1 || (version == null || !version.matches("[a-fA-F0-9]{64}"))
                || catalog.unresolved.stream().anyMatch(e -> couldBe(session, e.job()))) {
            throw ApiException.notFound("历史版本不存在或待核对");
        }
        Entry entry = jobs.get(0);
        if (acceptance && !"isolated".equals(entry.state().path("production_mode").asText())) {
            throw ApiException.notFound("隔离版本不存在");
        }
        JsonNode ranks = entry.state().path("production_ranks");
        if (ranks.isArray() && !ranks.isEmpty()
                && java.util.stream.StreamSupport.stream(ranks.spliterator(), false).noneMatch(r -> r.asInt() == rank)) {
            throw ApiException.notFound("历史候选不存在");
        }
        Path dir = candidateDir(entry, rank);
        Path scoped = historicalVersion(dir, version, entry.root());
        if (scoped == null) throw ApiException.notFound("版本清单或路径无效");
        Historical result = historical(read(dir.resolve("state.json")), dir, scoped, version);
        if (!result.playable()) throw ApiException.notFound("历史媒体或报告哈希不匹配");
        return result.media();
    }

    private Path media(String name, int rank, Catalog catalog, boolean isolatedOnly) {
        String session = paths.checkSession(name);
        List<Entry> jobs = catalog.jobs(session);
        if (jobs.size() != 1 || rank < 1 || catalog.unresolved.stream().anyMatch(e -> couldBe(session, e.job()))
                || (isolatedOnly && !"isolated".equals(jobs.get(0).state().path("production_mode").asText()))) {
            throw ApiException.notFound("成片不存在或待核对");
        }
        Entry entry = jobs.get(0);
        JsonNode ranks = entry.state().path("production_ranks");
        if (ranks.isArray() && !ranks.isEmpty()
                && java.util.stream.StreamSupport.stream(ranks.spliterator(), false).noneMatch(r -> r.asInt() == rank)) {
            throw ApiException.notFound("成片不存在");
        }
        Path candidate = candidateDir(entry, rank);
        Path file = inventoryMedia(entry, candidate);
        if (isolatedOnly && !read(candidate.resolve("inventory.json")).has("rank")) file = null;
        if (file == null && !isolatedOnly) {
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
            Path root = catalog.root();
            boolean available = Files.isReadable(root) && Files.isDirectory(root)
                    && (Files.isReadable(root.resolve("queue")) || Files.isReadable(root.resolve("ended")));
            String reason = !available ? "运营队列目录不可读或尚无记录，无法核对本场"
                    : jobs.size() > 1 || uncertain ? "任务与录播场次未能唯一匹配，请核对房间和下播时间"
                    : "没有唯一的下播与录播匹配记录；不能确认漏入队";
            return Map.of("status", "verify", "identity", "uncertain", "message", reason, "candidates", List.of());
        }
        boolean writable = catalog.writable();
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
        boolean resumeRequested = Files.isRegularFile(statusPath(entry.root(), entry.id() + ".resume"));
        List<Map<String, Object>> candidates = new ArrayList<>();
        Instant updated = modified(entry.file());
        updated = later(updated, modified(statusPath(entry.root(), entry.id())));
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
        ranks.addAll(candidateRanks(entry));
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
            boolean media = old == null ? inventoryMedia(entry, dir) != null : legacyMedia(entry, old) != null;
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
                    : "no_candidates";
            default -> worker;
        };
        Map<String, Object> view = new LinkedHashMap<>();
        view.put("identity", "matched"); view.put("jobId", entry.id());
        view.put("room", entry.job().path("room").asText()); view.put("workerStatus", worker);
        view.put("status", display); view.put("currentStep", step); view.put("updatedAt", updated.toString());
        view.put("completedCandidates", completed); view.put("totalCandidates", ranks.size());
        view.put("clipCount", clips); view.put("needsReviewCount", reviews); view.put("candidates", candidates);
        view.put("resumeRequested", resumeRequested);
        view.put("budget", budget(state));
        view.put("attempts", state.path("attempts").isArray() ? state.path("attempts") : null);
        view.put("processingAttempts", state.path("attempts").isNumber() ? state.path("attempts").numberValue() : null);
        if (state.path("retry_at").isNumber()) view.put("retryAt", Instant.ofEpochSecond(state.path("retry_at").asLong()).toString());
        String message = switch (display) {
            case "waiting_for_budget" -> "预算账本拒绝预留，等待下个预算周期";
            case "waiting_for_resources" -> "等待录播或制作资源；请查看候选卡点";
            case "failed" -> "处理失败；请核对当前步骤及候选诊断";
            case "needs_review" -> "部分候选需要复核，已完成的成片保留";
            case "no_candidates" -> "处理完成，无成片";
            case "queued" -> "已入队，等待制作";
            default -> "";
        };
        view.put("message", message);
        if (List.of("failed", "waiting_for_resources").contains(display) && state.path("error").isTextual()) {
            String error = clean(state.path("error").asText());
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
        if (!found.isEmpty()) return found.stream().map(this::sanitizeMap).toList();
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
        return found.stream().map(this::sanitizeMap).toList();
    }

    private Catalog catalog() {
        return catalog(paths.operationsRoot(), properties.isOpsRecoveryEnabled() && !properties.getOpsDir().isBlank());
    }

    private Catalog catalog(Path root, boolean writable) {
        List<Entry> jobs = new ArrayList<>(), ended = new ArrayList<>(), unresolved = new ArrayList<>();
        Map<Path, List<Recording>> recordings = new LinkedHashMap<>();
        for (String kind : List.of("queue", "ended")) {
            for (Path file : files(root.resolve(kind))) {
                String id = file.getFileName().toString();
                JsonNode job = read(file);
                if (!JOB.matcher(id).matches() || !paths.streamer().equals(job.path("streamer").asText())
                        || !id.startsWith(paths.streamer() + "-") || !job.path("room").asText().matches("[0-9]+")) continue;
                JsonNode state = "queue".equals(kind) ? read(statusPath(root, id)) : MissingNode.getInstance();
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
                Entry entry = new Entry(root, file, id, session, job, state, ready);
                if ("queue".equals(kind)) {
                    if (session.isBlank()) unresolved.add(entry); else jobs.add(entry);
                } else if (!session.isBlank()) ended.add(entry);
                else if (unresolved.stream().noneMatch(e -> e.id().equals(id))
                        && jobs.stream().noneMatch(e -> e.id().equals(id))) unresolved.add(entry);
            }
        }
        return new Catalog(root, writable, jobs, ended, unresolved);
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
        Path session = entry.root().resolve("sessions").resolve(entry.id().replaceFirst("\\.json$", ""))
                .resolve(paths.streamer()).resolve(paths.checkSession(entry.session()));
        Path quality = under(item.path("quality").asText(), session);
        if (quality == null || under(quality.toString(), entry.root()) == null) return null;
        return mp4(under(read(quality).path("media").asText(), session));
    }

    private Path mp4(Path file) {
        return file != null && Files.isRegularFile(file) && file.getFileName().toString().endsWith(".mp4") ? file : null;
    }

    private Path inventoryMedia(Entry entry, Path dir) {
        Path root = entry.root();
        JsonNode inventory = read(dir.resolve("inventory.json"));
        JsonNode render = inventory.path("render");
        String raw = render.path("media").asText("");
        if (raw.isBlank()) return null;
        JsonNode state = read(dir.resolve("state.json"));
        // A failed working draft may retain an explicitly qualified prior inventory. This never
        // falls back to history: the complete modern inventory bindings below still must pass.
        boolean qualified = state.path("qualified_version").isTextual();
        String expectedVersion = state.path(qualified ? "qualified_version" : "current_version").asText(null);
        if ((!qualified && !"inventoried".equals(state.path("status").asText()))
                || !Objects.equals(inventory.path("version").asText(null), expectedVersion)
                || !inventory.path("version").isTextual()) return null;
        // Minimal pre-versioning records remain readable; qualified retention always opts into full validation.
        boolean modern = qualified || inventory.has("rank") || inventory.has("review") || inventory.has("simulation") || render.has("files");
        if (modern) {
            int rank = Integer.parseInt(dir.getFileName().toString());
            if (inventory.path("rank").asInt(-1) != rank || inventory.path("simulation").asBoolean(true)
                    || !entry.session().equals(state.path("session").asText())
                    || state.path("rank").asInt(rank) != rank
                    || !"pass".equals(inventory.path("review").path("verdict").asText())
                    || !"actual_mp4_model".equals(inventory.path("review").path("basis").asText())
                    || !Boolean.TRUE.equals(boundReview(inventory.path("review"), render, dir).get("bound"))
                    || !"pass".equals(report(render, "quality", dir).get("status"))
                    || !Boolean.TRUE.equals(report(render, "receipt", dir).get("bound"))) return null;
            if (inventory.has("event_id") && !Objects.equals(inventory.path("event_id").asText(), state.path("event_id").asText())) return null;
            Path draft = under(inventory.path("draft").asText(""), dir);
            if (draft == null || !sha256(draft).equals(inventory.path("draft_sha256").asText())
                    || !inventory.path("version").asText().equals(read(draft).path("id").asText())) return null;
            for (var fields = render.path("files").fields(); fields.hasNext();) {
                var artifact = fields.next();
                Path path = under(artifact.getKey(), dir);
                if (path == null || !artifact.getValue().asText().equals(sha256(path))) return null;
            }
        }
        try {
            Path file = Path.of(raw).toRealPath();
            return file.startsWith(dir.toRealPath()) && file.startsWith(root.toRealPath())
                    && Files.isRegularFile(file) && file.getFileName().toString().endsWith(".mp4") ? file : null;
        } catch (IOException | RuntimeException e) { return null; }
    }

    private Path production(Entry entry) { return entry.root().resolve("production").resolve(entry.id().replaceFirst("\\.json$", "")); }
    private Path candidateDir(Entry entry, int rank) { return production(entry).resolve(String.format(java.util.Locale.ROOT, "%02d", rank)); }
    private Path statusPath(Path root, String id) { return root.resolve("status").resolve(id); }
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
    private record Entry(Path root, Path file, String id, String session, JsonNode job, JsonNode state, boolean ready) {}
    private record Catalog(Path root, boolean writable, List<Entry> entries, List<Entry> endings, List<Entry> unresolved) {
        List<Entry> jobs(String name) { return entries.stream().filter(e -> name.equals(e.session())).toList(); }
        List<Entry> ended(String name) { return endings.stream().filter(e -> name.equals(e.session())).toList(); }
        List<String> names() {
            return java.util.stream.Stream.concat(entries.stream(), endings.stream()).map(Entry::session)
                    .filter(s -> s.matches("[0-9A-Za-z_-]{1,64}")).distinct().toList();
        }
    }
}
