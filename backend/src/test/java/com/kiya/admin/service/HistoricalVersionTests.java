package com.kiya.admin.service;

import static org.junit.jupiter.api.Assertions.*;
import java.nio.file.*;
import java.security.MessageDigest;
import java.util.*;
import org.junit.jupiter.api.*;
import org.junit.jupiter.api.io.TempDir;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.kiya.admin.config.KiyaProperties;

class HistoricalVersionTests {
    static final String SESSION = "20261124-2151", JOB = "kiya-1795528300000.json", VERSION = "a".repeat(64);
    @TempDir Path temp;
    final ObjectMapper json = new ObjectMapper();
    OperationsQueueService service;
    Path root, candidate, version, media, quality, receipt;
    Map<String, Object> render;
    Map<String, Object> files;

    @BeforeEach void setup() throws Exception {
        KiyaProperties props = new KiyaProperties();
        props.setRepoDir(temp.toString()); props.setRecDir(temp.resolve("rec").toString());
        props.setOpsDir(temp.resolve("ops").toString()); props.setAcceptanceDir(temp.resolve("acceptance").toString());
        root = temp.resolve("acceptance/operations");
        service = new OperationsQueueService(new WorkerPaths(props), json, props);
        write(root.resolve("queue/" + JOB), Map.of("streamer", "kiya", "room", "123"));
        write(root.resolve("status/" + JOB), Map.of("session", SESSION, "production_mode", "isolated", "production_ranks", List.of(1)));
        candidate = root.resolve("production/" + JOB.replace(".json", "") + "/01");
        version = candidate.resolve("versions/" + VERSION);
        write(version.resolve("draft.json"), Map.of("id", VERSION));
        media = version.resolve("renders/retry/final/clip.mp4");
        Files.createDirectories(media.getParent()); Files.writeString(media, "01234567");
        quality = media.resolveSibling("quality.json"); receipt = media.resolveSibling("receipt.json");
        write(quality, Map.of("ok", false, "media", raw(media), "errors", List.of("audio failed")));
        write(receipt, Map.of("status", "time_covered", "callback_offset", 2, "source_to_output", List.of(Map.of("source_start", 10, "source_end", 12, "output_start", 0, "output_end", 2))));
        files = new LinkedHashMap<>();
        for (Path file : List.of(media, quality, receipt)) files.put(raw(file), sha(file));
        render = Map.of("media", raw(media), "quality", raw(quality), "receipt", raw(receipt), "files", files);
        state(raw(media), Map.of("verdict", "repair", "basis", "fixture", "simulation", true, "version_id", VERSION, "media_sha256", sha(media), "output_range", List.of(1,2)));
    }
    String raw(Path path) { return candidate.relativize(path).toString().replace('/', '\\'); }
    String sha(Path path) throws Exception { return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(Files.readAllBytes(path))); }
    void write(Path path, Object value) throws Exception { Files.createDirectories(path.getParent()); json.writeValue(path.toFile(), value); }
    void state(String path, Map<String,Object> review) throws Exception {
        Map<String,Object> current = new LinkedHashMap<>(render); current.put("media", path);
        write(candidate.resolve("state.json"), Map.of("current_version", VERSION, "status", "needs_review", "steps", Map.of("render-0", Map.of("result", current), "review-0", Map.of("inputs", List.of(VERSION, files), "result", review))));
    }
    Map<?,?> summary() {
        Map<?,?> candidateRow = (Map<?,?>)((List<?>)service.acceptanceSummary(SESSION).get("candidates")).get(0);
        return (Map<?,?>)((List<?>)candidateRow.get("versions")).get(0);
    }
    @Test void nestedWindowsPathsQualityFailurePlayableButNeverAcceptedAndSimulationExplicit() throws Exception {
        assertEquals(media.toRealPath(), service.versionMedia(SESSION,1,VERSION,true));
        Map<?,?> row = summary();
        assertEquals(true,row.get("playable")); assertEquals(false,row.get("accepted")); assertEquals(true,row.get("simulation"));
        assertEquals(true,row.get("reviewBound")); assertEquals("fail",((Map<?,?>)row.get("quality")).get("status"));
        assertEquals(2.0, ((Map<?,?>)((List<?>)row.get("sourceMapping")).get(0)).get("output_start"));
        assertThrows(RuntimeException.class, () -> service.acceptanceMedia(SESSION,1));
    }
    @Test void historicalFixturePreviewIsPlayableButFormalInventoryIsNot() throws Exception {
        write(root.resolve("queue/" + JOB), Map.of("streamer", "kiya", "room", "123", "mode", "fixture"));
        Map<?,?> detail = service.historicalDetail(root, JOB.replace(".json", ""), SESSION);
        Map<?,?> row = (Map<?,?>)((List<?>)detail.get("candidates")).get(0);
        assertEquals(false, row.get("playable")); assertEquals(true, row.get("previewPlayable"));
        assertEquals(true, row.get("simulation")); assertEquals(false, row.get("accepted"));
        assertEquals(0, detail.get("clipCount")); assertEquals(1L, detail.get("previewClipCount"));
        assertEquals(media.toRealPath(), service.historicalMedia(root, JOB.replace(".json", ""), SESSION, 1, null));
    }
    @Test void staleMediaAndReportHashesBlockPlayback() throws Exception {
        Files.writeString(quality,"{}"); assertThrows(RuntimeException.class, () -> service.versionMedia(SESSION,1,VERSION,true));
        assertEquals(false, summary().get("playable"));
    }
    @Test void staleMediaHashBlocksPlayback() throws Exception {
        Files.writeString(media,"changed"); assertThrows(RuntimeException.class, () -> service.versionMedia(SESSION,1,VERSION,true));
    }
    @Test void unselectedRankAndMissingBoundReportRejected() throws Exception {
        write(root.resolve("status/" + JOB), Map.of("session", SESSION,"production_mode","isolated","production_ranks",List.of(2)));
        assertThrows(RuntimeException.class, () -> service.versionMedia(SESSION,1,VERSION,true));
        write(root.resolve("status/" + JOB), Map.of("session", SESSION,"production_mode","isolated","production_ranks",List.of(1)));
        files.remove(raw(receipt)); state(raw(media), Map.of());
        assertThrows(RuntimeException.class, () -> service.versionMedia(SESSION,1,VERSION,true));
    }
    @Test void traversalAndSiblingVersionAreRejectedEvenWithCorrectHashes() throws Exception {
        assertThrows(RuntimeException.class, () -> service.versionMedia(SESSION,1,"../" + VERSION,true));
        Path other = candidate.resolve("versions/" + "b".repeat(64) + "/clip.mp4");
        Files.createDirectories(other.getParent()); Files.writeString(other,"other"); files.put(raw(other),sha(other));
        state(raw(other), Map.of()); assertThrows(RuntimeException.class, () -> service.versionMedia(SESSION,1,VERSION,true));
    }
    @Test void nestedCheckpointTokensAndCredentialsAreRedacted() throws Exception {
        write(candidate.resolve("state.json"), Map.of("steps", Map.of("render-0",Map.of("result",render),
                "private-input",Map.of("inputs",Map.of("token","model-value-991", "credentials",Map.of("key","model-value-992"))))));
        String response = json.writeValueAsString(service.acceptanceSummary(SESSION));
        assertFalse(response.contains("model-value-991")); assertFalse(response.contains("model-value-992"));
    }
    @Test void everyListedArtifactMustBeHashBound() throws Exception {
        Path edit = media.resolveSibling("clip.edit.json"); Files.writeString(edit,"{}");
        files.put(raw(edit), "0".repeat(64)); state(raw(media), Map.of());
        assertThrows(RuntimeException.class, () -> service.versionMedia(SESSION,1,VERSION,true));
    }
    @Test void reviewCheckpointInputsMustMatchVersionAndFiles() throws Exception {
        write(candidate.resolve("state.json"), Map.of("steps", Map.of(
                "render-0", Map.of("result",render), "review-0", Map.of("inputs",List.of("b".repeat(64),files),
                "result",Map.of("verdict","pass","media_sha256",sha(media))))));
        assertEquals(false,summary().get("reviewBound"));
        assertNull(summary().get("simulation"));
    }
    @Test void versionSymlinkOutsideCandidateRejected() throws Exception {
        Path outside = temp.resolve("outside"); Files.createDirectories(outside);
        write(outside.resolve("draft.json"),Map.of("id","b".repeat(64)));
        Path link = candidate.resolve("versions/" + "b".repeat(64));
        try { Files.createSymbolicLink(link,outside); }
        catch (FileSystemException | UnsupportedOperationException ex) { Assumptions.abort("Host symlink permission unavailable: " + ex.getClass().getSimpleName()); }
        assertThrows(RuntimeException.class, () -> service.versionMedia(SESSION,1,"b".repeat(64),true));
    }
    @Test void mismatchedManifestRejected() throws Exception {
        write(version.resolve("draft.json"), Map.of("id", "b".repeat(64)));
        assertThrows(RuntimeException.class, () -> service.versionMedia(SESSION,1,VERSION,true));
    }
    @Test void reviewHashOrVersionMismatchRemainsUnknown() throws Exception {
        state(raw(media),Map.of("verdict","pass","basis","actual_mp4_model","media_sha256",sha(media),"version_id","b".repeat(64)));
        assertEquals(false,summary().get("reviewBound"));
        assertEquals("unknown",((Map<?,?>)summary().get("review")).get("status"));
        assertEquals(media.toRealPath(),service.versionMedia(SESSION,1,VERSION,true));
    }
    @Test void symlinkEscapeRejected() throws Exception {
        Path outside = temp.resolve("external.mp4"); Files.writeString(outside,"external");
        Path link = media.resolveSibling("escape.mp4");
        try { Files.createSymbolicLink(link,outside); }
        catch (FileSystemException | UnsupportedOperationException ex) { Assumptions.abort("Host symlink permission unavailable: " + ex.getClass().getSimpleName()); }
        files.put(raw(link),sha(outside)); state(raw(link),Map.of());
        assertThrows(RuntimeException.class, () -> service.versionMedia(SESSION,1,VERSION,true));
    }
}
