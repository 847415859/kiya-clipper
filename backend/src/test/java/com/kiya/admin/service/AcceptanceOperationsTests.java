package com.kiya.admin.service;

import static org.junit.jupiter.api.Assertions.*;

import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.time.Instant;
import java.util.HexFormat;
import java.util.List;
import java.util.Map;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.kiya.admin.config.KiyaProperties;

class AcceptanceOperationsTests {
    private static final String SESSION = "20261124-2151";
    private static final String ID = "kiya-1795528300000.json";
    private static final long START = Instant.parse("2026-11-24T13:51:00Z").getEpochSecond();
    @TempDir Path temp;
    private ObjectMapper json;
    private KiyaProperties props;
    private WorkerPaths paths;
    private OperationsQueueService service;
    private Path isolated;

    @BeforeEach void setup() throws Exception {
        json = new ObjectMapper(); props = new KiyaProperties();
        props.setRepoDir(temp.toString()); props.setRecDir(temp.resolve("rec").toString());
        props.setOpsDir(temp.resolve("production").toString()); props.setAcceptanceDir(temp.resolve("acceptance").toString());
        Files.createDirectories(Path.of(props.getRecDir()));
        paths = new WorkerPaths(props); service = new OperationsQueueService(paths, json, props);
        isolated = Path.of(props.getAcceptanceDir(), "operations");
    }

    @Test void sourcesRemainIsolatedAndReadOnlySummaryHasNoAction() throws Exception {
        seed(Path.of(props.getOpsDir()), "production-only");
        seed(isolated, "acceptance-only");
        assertEquals(ID, service.readOnlySummary(SESSION, false).get("jobId"));
        assertEquals(ID, service.readOnlySummary(SESSION, true).get("jobId"));
        assertFalse(service.readOnlySummary(SESSION, true).containsKey("action"));
        assertEquals(ID, service.acceptanceOverview().get("sessions") instanceof List<?> rows
                ? ((Map<?, ?>) rows.get(0)).get("jobId") : null);
    }

    @Test void unmatchedQueueTasksRemainVisibleAndMonitorUnknownsStayUnknown() throws Exception {
        write(isolated.resolve("queue").resolve(ID), job());
        write(Path.of(props.getAcceptanceDir(), "monitor-state.json"), Map.of("api_fail", 2, "live", true));
        Map<?, ?> overview = service.acceptanceOverview();
        assertEquals(1, overview.get("unmatched"));
        assertEquals(ID, ((Map<?, ?>) ((List<?>) overview.get("unmatchedTasks")).get(0)).get("jobId"));
        Map<?, ?> monitor = (Map<?, ?>) overview.get("monitor");
        assertNull(monitor.get("live")); assertNull(monitor.get("lastCheck")); assertEquals(true, monitor.get("stale"));
    }

    @Test void candidateHistoryAndNestedSecretsArePreservedButRedacted() throws Exception {
        seed(isolated, "acceptance-only");
        Path production = isolated.resolve("production").resolve(ID.replace(".json", ""));
        write(production.resolve("summary.json"), Map.of("candidates", List.of(Map.of("rank", 4, "status", "needs_review"))));
        Path candidate = production.resolve("04");
        write(candidate.resolve("state.json"), Map.of("status", "needs_review", "stage", "review-1",
                "current_version", "v2", "attempts", List.of(Map.of("status", "unknown", "error", "token=abc123 /srv/private/log")),
                "events", List.of(Map.of("stage", "render", "reason", "Cookie: SESSIONID=private"))));
        Map<?, ?> detail = service.acceptanceSummary(SESSION);
        Map<?, ?> row = ((List<?>) detail.get("candidates")).stream().map(v -> (Map<?, ?>) v)
                .filter(v -> Integer.valueOf(4).equals(v.get("rank"))).findFirst().orElseThrow();
        assertEquals(4, row.get("rank"));
        String encoded = json.writeValueAsString(detail);
        assertFalse(encoded.contains("abc123")); assertFalse(encoded.contains("/srv/private/log"));
        assertTrue(encoded.contains("unknown"));
    }

    @Test void newInventoryRequiresQueueCandidateVersionAndBoundReports() throws Exception {
        seed(isolated, "acceptance-only");
        Path candidate = isolated.resolve("production").resolve(ID.replace(".json", "")).resolve("01");
        Files.createDirectories(candidate);
        Path media = Files.writeString(candidate.resolve("clip.mp4"), "fixture media");
        String hash = HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(Files.readAllBytes(media)));
        write(candidate.resolve("state.json"), Map.of("status", "inventoried", "current_version", "v1", "session", SESSION,
                "rank", 1, "event_id", "event-a"));
        write(candidate.resolve("quality.json"), Map.of("ok", true, "media", media.toString(), "checks", Map.of("audio", Map.of("status", "pass"))));
        write(candidate.resolve("receipt.json"), Map.of("status", "time_covered", "media_size", Files.size(media)));
        Map<String, Object> render = Map.of("media", media.toString(), "quality", candidate.resolve("quality.json").toString(),
                "receipt", candidate.resolve("receipt.json").toString(), "files", Map.of(media.toString(), hash, candidate.resolve("quality.json").toString(), sha(candidate.resolve("quality.json")),
                        candidate.resolve("receipt.json").toString(), sha(candidate.resolve("receipt.json"))));
        write(candidate.resolve("draft.json"), Map.of("id", "v1"));
        write(candidate.resolve("inventory.json"), Map.of("rank", 1, "version", "v1", "event_id", "event-a", "session", SESSION,
                "draft", candidate.resolve("draft.json").toString(), "draft_sha256", sha(candidate.resolve("draft.json")),
                "review", Map.of("verdict", "pass", "basis", "actual_mp4_model", "media_sha256", hash), "render", render, "simulation", false));
        assertEquals(media.toRealPath(), service.acceptanceMedia(SESSION, 1));
        write(candidate.resolve("state.json"), Map.of("status", "inventoried", "current_version", "v2", "session", SESSION, "rank", 1, "event_id", "event-a"));
        assertNull(service.acceptanceSummary(SESSION).get("action"));
        assertThrows(RuntimeException.class, () -> service.acceptanceMedia(SESSION, 1));
        // Explicit qualification preserves the fully bound prior inventory after working repair failure.
        write(candidate.resolve("state.json"), Map.of("status", "needs_review", "current_version", "v2", "qualified_version", "v1",
                "session", SESSION, "rank", 1, "event_id", "event-a"));
        assertEquals(media.toRealPath(), service.acceptanceMedia(SESSION, 1));
        write(candidate.resolve("state.json"), Map.of("status", "needs_review", "current_version", "v2", "qualified_version", "v2",
                "session", SESSION, "rank", 1, "event_id", "event-a"));
        assertThrows(RuntimeException.class, () -> service.acceptanceMedia(SESSION, 1));
        write(candidate.resolve("state.json"), Map.of("status", "needs_review", "current_version", "v2", "qualified_version", "v1",
                "session", SESSION, "rank", 1, "event_id", "event-a"));
        Files.writeString(media, "changed after qualification");
        assertThrows(RuntimeException.class, () -> service.acceptanceMedia(SESSION, 1));
    }

    private void seed(Path root, String statusSession) throws Exception {
        write(root.resolve("queue").resolve(ID), job());
        write(root.resolve("status").resolve(ID), Map.of("session", SESSION, "status", "prepared", "production_mode", "isolated",
                "production_ranks", List.of(1), "fixture_session", statusSession));
    }
    private Map<String, Object> job() {
        return Map.of("streamer", "kiya", "room", "123", "since", START, "earliest", START - 300,
                "ended", START + 600, "rec_dir", props.getRecDir());
    }
    private String sha(Path file) throws Exception {
        return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(Files.readAllBytes(file)));
    }
    private void write(Path file, Object value) throws Exception {
        Files.createDirectories(file.getParent()); json.writeValue(file.toFile(), value);
    }
}
