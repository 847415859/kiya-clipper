package com.kiya.admin.service;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.attribute.FileTime;
import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.concurrent.Executors;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.kiya.admin.common.ApiException;
import com.kiya.admin.config.KiyaProperties;
import com.kiya.admin.mapper.CandidateMapper;
import com.kiya.admin.mapper.LiveSessionMapper;

class OperationsQueueTests {
    private static final String SESSION = "20261124-2151";
    private static final String ID = "kiya-1795528300000.json";
    private static final long START = Instant.parse("2026-11-24T13:51:00Z").getEpochSecond();
    @TempDir Path temp;
    private Path root;
    private KiyaProperties props;
    private WorkerPaths paths;
    private OperationsQueueService service;
    private final ObjectMapper json = new ObjectMapper();

    @BeforeEach void setup() throws Exception {
        root = temp.resolve("ops");
        Files.createDirectories(temp.resolve("rec"));
        props = new KiyaProperties();
        props.setRepoDir(temp.toString());
        props.setRecDir(temp.resolve("rec").toString());
        props.setOpsDir(root.toString());
        props.setAcceptanceDir(temp.resolve("acceptance").toString());
        props.setOpsRecoveryEnabled(true);
        paths = new WorkerPaths(props);
        service = new OperationsQueueService(paths, json, props);
    }

    @Test void acceptanceQueueDisplaysItsOwnMonitorAndInventoryWithoutRecovery() throws Exception {
        Path acceptance = temp.resolve("acceptance");
        Path ops = acceptance.resolve("operations");
        Files.createDirectories(ops.resolve("queue"));
        Files.createDirectories(ops.resolve("status"));
        json.writeValue(acceptance.resolve("monitor-state.json").toFile(),
                Map.of("last_check", "2026-11-26 20:00:00", "live", false));
        json.writeValue(ops.resolve("queue").resolve(ID).toFile(), job("kiya"));
        Path status = ops.resolve("status").resolve(ID);
        json.writeValue(status.toFile(), Map.of("session", SESSION, "status", "blocked",
                "production_mode", "isolated", "production_ranks", List.of(1)));
        Map<String, Object> overview = service.acceptanceOverview();
        assertEquals(true, overview.get("observed"));
        assertEquals("2026-11-26 20:00:00", overview.get("lastCheck"));
        assertEquals(SESSION, ((Map<?, ?>) ((List<?>) overview.get("sessions")).get(0)).get("name"));
        assertNull(service.acceptanceSummary(SESSION).get("action"));
        assertEquals("verify", service.summary(SESSION).get("status"));
        assertThrows(ApiException.class, () -> service.recover(SESSION, "resume"));

        Path candidate = ops.resolve("production").resolve(ID.replace(".json", "")).resolve("01");
        Files.createDirectories(candidate);
        Path media = Files.writeString(candidate.resolve("result.mp4"), "fixture");
        json.writeValue(candidate.resolve("state.json").toFile(),
                Map.of("status", "inventoried", "current_version", "v1"));
        json.writeValue(candidate.resolve("inventory.json").toFile(),
                Map.of("version", "v1", "render", Map.of("media", media.toString())));
        // Incomplete legacy inventories lack identity and report bindings: do not claim qualification.
        assertThrows(ApiException.class, () -> service.acceptanceMedia(SESSION, 1));
        assertThrows(ApiException.class, () -> service.media(SESSION, 1));
    }

    @Test void missedEnqueueRequiresConfirmedEndAndDeduplicatesConcurrentRequests() throws Exception {
        Path rec = temp.resolve("rec/source.xml");
        Files.writeString(rec, "<i><BililiveRecorderRecordInfo roomid=\"123\" start_time=\"2026-11-24T21:51:00+08:00\"/></i>");
        Path video = Files.writeString(rec.resolveSibling("source.flv"), "fixture");
        assertEquals("verify", service.summary(SESSION).get("status"));
        write("ended/" + ID, job("kiya"));
        Files.writeString(rec, "<i><BililiveRecorderRecordInfo roomid=\"123\" start_time=\"2026-11-24T21:51:00+08:00\"/></i>");
        assertNull(service.summary(SESSION).get("action"));
        FileTime stable = FileTime.from(Instant.now().minusSeconds(180));
        Files.setLastModifiedTime(rec, stable);
        Files.setLastModifiedTime(video, stable);
        assertEquals("enqueue", service.summary(SESSION).get("action"));
        var pool = Executors.newFixedThreadPool(3);
        try {
            var a = pool.submit(() -> service.recover(SESSION, "enqueue"));
            var b = pool.submit(() -> service.recover(SESSION, "enqueue"));
            var monitor = pool.submit(() -> {
                Files.createDirectories(root.resolve("queue"));
                try { Files.createLink(root.resolve("queue").resolve(ID), root.resolve("ended").resolve(ID)); }
                catch (java.nio.file.FileAlreadyExistsException ignored) { }
                return null;
            });
            assertEquals(ID, a.get().get("jobId"));
            assertEquals(ID, b.get().get("jobId"));
            monitor.get();
        } finally { pool.shutdownNow(); }
        try (var files = Files.list(root.resolve("queue"))) { assertEquals(1, files.count()); }
        assertEquals("queued", service.summary(SESSION).get("status"));
        assertTrue(service.summary(SESSION).get("action") == null || "resume".equals(service.summary(SESSION).get("action")));
    }

    @Test void onlyIsolatedRetryCanRequestResumeAndBudgetCannotBeBypassed() throws Exception {
        write("queue/" + ID, job("kiya"));
        write("status/" + ID, Map.of("session", SESSION, "status", "waiting_for_budget", "production_mode", "isolated",
                "budget", Map.of("booked_fen", 5000, "available_fen", 0, "day", "2026-11-24")));
        assertEquals("waiting_for_budget", service.summary(SESSION).get("status"));
        assertNull(service.summary(SESSION).get("action"));
        assertThrows(ApiException.class, () -> service.recover(SESSION, "resume"));
        write("status/" + ID, Map.of("session", SESSION, "status", "blocked", "production_mode", "isolated"));
        String before = Files.readString(root.resolve("status").resolve(ID));
        props.setOpsRecoveryEnabled(false);
        assertNull(service.summary(SESSION).get("action"));
        props.setOpsRecoveryEnabled(true);
        assertEquals("resume", service.summary(SESSION).get("action"));
        assertEquals(true, service.recover(SESSION, "resume").get("resumeRequested"));
        assertEquals(true, service.recover(SESSION, "resume").get("resumeRequested"));
        assertEquals(before, Files.readString(root.resolve("status").resolve(ID)));
    }

    @Test void isolatedOnlySessionListsPlayableInventoryAlongsideReviewGap() throws Exception {
        write("queue/" + ID, job("kiya"));
        write("status/" + ID, Map.of("session", SESSION, "status", "prepared", "production_mode", "isolated",
                "production_ranks", List.of(1, 2)));
        String production = "production/" + ID.replace(".json", "");
        write(production + "/01/state.json", Map.of("status", "inventoried", "current_version", "v1"));
        Path media = Files.writeString(root.resolve(production + "/01/clip.mp4"), "fixture");
        write(production + "/01/inventory.json", Map.of("version", "v1", "render", Map.of("media", media.toString())));
        write(production + "/02/state.json", Map.of("status", "needs_review", "stage", "evidence-0", "steps",
                Map.of("evidence-0", Map.of("result", Map.of("media_gaps", List.of(List.of(47, 49)),
                        "question", Map.of("question", "最后一句回应", "kind", "response"))))));
        Map<String, Object> view = service.summary(SESSION);
        assertEquals("needs_review", view.get("status"));
        assertEquals(2, view.get("completedCandidates"));
        assertEquals(1, view.get("clipCount"));
        String detail = json.writeValueAsString(view);
        assertTrue(detail.contains("[47,49]"));
        assertTrue(detail.contains("缺少可读取的原声"));
        assertEquals(media.toRealPath(), service.media(SESSION, 1));
        write(production + "/02/state.json", Map.of("status", "failed", "stage", "render-0"));
        assertEquals(1, service.summary(SESSION).get("clipCount"));
        assertEquals(media.toRealPath(), service.media(SESSION, 1));
        LiveSessionMapper sessions = mock(LiveSessionMapper.class);
        CandidateMapper candidates = mock(CandidateMapper.class);
        when(sessions.selectList(any())).thenReturn(List.of());
        when(candidates.selectList(any())).thenReturn(List.of());
        assertEquals(SESSION, new SessionService(sessions, candidates, paths, null, json, null, service).list().get(0).get("name"));
        write(production + "/01/state.json", Map.of("status", "inventoried", "current_version", "v2"));
        assertThrows(ApiException.class, () -> service.media(SESSION, 1));
        props.setStreamer("other");
        assertEquals("verify", service.summary(SESSION).get("status"));
        assertThrows(ApiException.class, () -> service.media(SESSION, 1));
    }

    @Test void legacyResultIsReadOnlyAndServedFromItsOwnSession() throws Exception {
        write("queue/" + ID, job("kiya"));
        String base = "sessions/" + ID.replace(".json", "") + "/kiya/" + SESSION;
        Files.createDirectories(root.resolve(base));
        Path media = Files.writeString(root.resolve(base + "/clip.mp4"), "fixture");
        String quality = base + "/quality/01.json";
        write(quality, Map.of("media", media.toString()));
        write("status/" + ID, Map.of("session", SESSION, "status", "prepared", "production_mode", "legacy",
                "inventory", List.of(Map.of("rank", 1, "decision", "qualified", "quality", root.resolve(quality).toString()))));
        assertEquals("has_clips", service.summary(SESSION).get("status"));
        assertNull(service.summary(SESSION).get("action"));
        assertEquals(media.toRealPath(), service.media(SESSION, 1));
    }

    @Test void acceptanceReadOnlyApisKeepSameNamedProductionAndAcceptanceRootsSeparate() throws Exception {
        write("queue/" + ID, job("kiya"));
        write("status/" + ID, Map.of("session", SESSION, "status", "prepared", "production_mode", "legacy"));
        Path acceptOps = temp.resolve("acceptance/operations");
        Files.createDirectories(acceptOps.resolve("queue"));
        Files.createDirectories(acceptOps.resolve("status"));
        json.writeValue(acceptOps.resolve("queue").resolve(ID).toFile(), job("kiya"));
        json.writeValue(acceptOps.resolve("status").resolve(ID).toFile(), Map.of("session", SESSION, "status", "blocked", "production_mode", "isolated"));
        Map<String, Object> prod = service.readOnlySummary(SESSION, false);
        Map<String, Object> isolated = service.readOnlySummary(SESSION, true);
        assertEquals("production", prod.get("source"));
        assertEquals("acceptance", isolated.get("source"));
        assertEquals("prepared", prod.get("workerStatus"));
        assertEquals("blocked", isolated.get("workerStatus"));
        assertFalse(prod.containsKey("action"));
        assertFalse(isolated.containsKey("action"));
        assertEquals("prepared", service.readOnlySummary(SESSION, false).get("workerStatus"));
    }

    @Test void acceptanceDetailDoesNotBindOldReviewToNewCurrentVersion() throws Exception {
        Path acceptOps = temp.resolve("acceptance/operations");
        Files.createDirectories(acceptOps.resolve("queue"));
        Files.createDirectories(acceptOps.resolve("status"));
        json.writeValue(acceptOps.resolve("queue").resolve(ID).toFile(), job("kiya"));
        json.writeValue(acceptOps.resolve("status").resolve(ID).toFile(), Map.of("session", SESSION, "status", "prepared", "production_mode", "isolated", "production_ranks", List.of(1)));
        Path candidate = acceptOps.resolve("production").resolve(ID.replace(".json", "")).resolve("01");
        Files.createDirectories(candidate);
        json.writeValue(candidate.resolve("state.json").toFile(), Map.of("session", SESSION, "rank", 1, "status", "needs_review", "current_version", "v2"));
        json.writeValue(candidate.resolve("inventory.json").toFile(), Map.of("rank", 1, "version", "v1", "review", Map.of("verdict", "pass", "media_sha256", "a".repeat(64)), "render", Map.of("media", "old.mp4")));
        @SuppressWarnings("unchecked") List<Map<String, Object>> candidates = (List<Map<String, Object>>) service.acceptanceSummary(SESSION).get("candidates");
        assertEquals("v2", candidates.get(0).get("version"));
        assertFalse(Boolean.TRUE.equals(((Map<?, ?>) candidates.get(0).get("review")).get("bound")));
        assertFalse(Boolean.TRUE.equals(candidates.get(0).get("playable")));
    }

    @Test void acceptanceOverviewExposesUnmatchedTaskWithoutTouchingProduction() throws Exception {
        Path acceptOps = temp.resolve("acceptance/operations");
        Files.createDirectories(acceptOps.resolve("queue"));
        json.writeValue(acceptOps.resolve("queue").resolve(ID).toFile(), job("kiya"));
        Map<String, Object> overview = service.acceptanceOverview();
        assertEquals(1, overview.get("unmatched"));
        assertEquals(ID, ((Map<?, ?>) ((List<?>) overview.get("unmatchedTasks")).get(0)).get("jobId"));
        assertFalse(Files.exists(root.resolve("queue")));
    }

    private Map<String, Object> job(String streamer) {
        return Map.of("streamer", streamer, "room", "123", "since", START, "earliest", START - 300,
                "ended", START + 600, "rec_dir", props.getRecDir());
    }

    private void write(String relative, Object data) throws Exception {
        Path file = root.resolve(relative);
        Files.createDirectories(file.getParent());
        json.writeValue(file.toFile(), data);
    }
}
