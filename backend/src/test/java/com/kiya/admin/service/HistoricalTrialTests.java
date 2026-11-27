package com.kiya.admin.service;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.Executors;
import org.junit.jupiter.api.*;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.kiya.admin.config.KiyaProperties;
import com.kiya.admin.common.ApiException;
import com.kiya.admin.web.HistoricalTrialController;

class HistoricalTrialTests {
    @TempDir Path temp;
    final ObjectMapper json = new ObjectMapper();
    KiyaProperties props; WorkerPaths paths; HistoricalTrialService service; Path dir; Path media;
    @BeforeEach void setup() throws Exception {
        props = new KiyaProperties(); props.setRepoDir(temp.toString()); props.setRecDir(temp.resolve("rec").toString());
        props.setAcceptanceDir(temp.resolve("acceptance").toString()); props.setHistoricalTrialFixtureEnabled(true);
        if (Files.getFileAttributeView(temp, java.nio.file.attribute.PosixFileAttributeView.class) != null)
            props.setHistoricalTrialGroup(Files.readAttributes(temp, java.nio.file.attribute.PosixFileAttributes.class).group().getName());
        Files.createDirectories(temp.resolve("rec"));
        paths = new WorkerPaths(props); dir = paths.output().resolve("session-a"); Files.createDirectories(dir);
        media = Files.writeString(temp.resolve("rec/raw.flv"), "fixture");
        candidates(media.toString());
        service = new HistoricalTrialService(paths, props, json, new OperationsQueueService(paths, json, props));
    }
    void candidates(String video) throws Exception { json.writeValue(dir.resolve("candidates.json").toFile(), Map.of("segments", List.of(Map.of("video", video, "offset", 0)), "candidates", List.of(Map.of("rank", 1, "start", 0, "end", 5)))); }
    @Test void posixHistoryUsesSharedGroupAndExactModesWithoutChangingOutsidePaths() throws Exception {
        Assumptions.assumeTrue(temp.getFileSystem().supportedFileAttributeViews().contains("unix"));
        Files.createDirectories(Path.of(props.getAcceptanceDir()));
        Path sentinel = Files.writeString(temp.resolve("outside-sentinel"), "unchanged");
        Object parentMode = Files.getAttribute(Path.of(props.getAcceptanceDir()), "unix:mode");
        Object outsideMode = Files.getAttribute(sentinel, "unix:mode");
        String id = (String) service.submit("session-a", 1, true, null).get("taskId");
        for (String name : List.of("", "queue", "status", "production", "locks", "ledger")) {
            Path directory = service.root().resolve(name);
            assertEquals(02770, ((Number) Files.getAttribute(directory, "unix:mode")).intValue() & 07777);
            assertEquals(props.getHistoricalTrialGroup(), Files.readAttributes(directory, java.nio.file.attribute.PosixFileAttributes.class).group().getName());
        }
        for (Path file : List.of(service.root().resolve("queue/" + id + ".json"), service.root().resolve("status/" + id + ".json"), service.root().resolve("locks/kiya-session-a-1.lock"))) {
            assertEquals(0640, ((Number) Files.getAttribute(file, "unix:mode")).intValue() & 07777);
            assertEquals(props.getHistoricalTrialGroup(), Files.readAttributes(file, java.nio.file.attribute.PosixFileAttributes.class).group().getName());
        }
        assertEquals(parentMode, Files.getAttribute(Path.of(props.getAcceptanceDir()), "unix:mode"));
        assertEquals(outsideMode, Files.getAttribute(sentinel, "unix:mode"));
        try (var entries = Files.walk(service.root())) { assertTrue(entries.noneMatch(p -> p.toString().endsWith(".tmp"))); }
    }
    @Test void missingPosixGroupFailsBeforeCreatingHistory() throws Exception {
        Assumptions.assumeTrue(temp.getFileSystem().supportedFileAttributeViews().contains("posix"));
        props.setHistoricalTrialGroup("kiya-missing-" + UUID.randomUUID());
        var error = assertThrows(java.io.IOException.class, () -> service.submit("session-a", 1, true, null));
        assertTrue(error.getMessage().contains("POSIX group"));
        assertFalse(Files.exists(service.root()));
    }
    @Test void nonPosixSubmissionDoesNotLookupConfiguredGroup() throws Exception {
        Assumptions.assumeFalse(temp.getFileSystem().supportedFileAttributeViews().contains("posix"));
        props.setHistoricalTrialGroup("not-a-windows-group");
        assertNotNull(service.submit("session-a", 1, true, null).get("taskId"));
    }
    @Test void historySymlinkAndAncestorSymlinkLeaveOutsideUntouched() throws Exception {
        Path outside = Files.createDirectory(temp.resolve("outside-history"));
        Files.createDirectories(Path.of(props.getAcceptanceDir()));
        try { Files.createSymbolicLink(service.root(), outside); }
        catch (UnsupportedOperationException | java.io.IOException | SecurityException e) { Assumptions.assumeTrue(false, "host does not permit symlinks"); }
        Object permissions = Files.getFileAttributeView(outside, java.nio.file.attribute.PosixFileAttributeView.class) == null ? null : Files.getPosixFilePermissions(outside);
        assertThrows(java.io.IOException.class, () -> service.submit("session-a", 1, true, null));
        try (var entries = Files.list(outside)) { assertEquals(0, entries.count()); }
        if (permissions != null) assertEquals(permissions, Files.getPosixFilePermissions(outside));
        Files.delete(service.root());
        Files.delete(Path.of(props.getAcceptanceDir()));
        Files.createSymbolicLink(Path.of(props.getAcceptanceDir()), outside);
        assertThrows(java.io.IOException.class, () -> service.submit("session-a", 1, true, null));
        assertFalse(Files.exists(outside.resolve("history")));
    }
    @Test void historySubdirectorySymlinkIsRejectedBeforeOutsideChmod() throws Exception {
        Path outside = Files.createDirectory(temp.resolve("outside-queue"));
        Files.createDirectories(service.root());
        try { Files.createSymbolicLink(service.root().resolve("queue"), outside); }
        catch (UnsupportedOperationException | java.io.IOException | SecurityException e) { Assumptions.assumeTrue(false, "host does not permit symlinks"); }
        Object permissions = Files.getFileAttributeView(outside, java.nio.file.attribute.PosixFileAttributeView.class) == null ? null : Files.getPosixFilePermissions(outside);
        assertThrows(java.io.IOException.class, () -> service.submit("session-a", 1, true, null));
        try (var entries = Files.list(outside)) { assertEquals(0, entries.count()); }
        if (permissions != null) assertEquals(permissions, Files.getPosixFilePermissions(outside));
    }
    @Test void concurrentDuplicateAndRestartReuseSameTaskAndKeepProductionUntouched() throws Exception {
        Path production = temp.resolve("production"); props.setOpsDir(production.toString()); Files.createDirectories(production); Files.writeString(production.resolve("sentinel"), "same-name");
        var pool = Executors.newFixedThreadPool(6);
        try {
            List<java.util.concurrent.Future<Map<String, Object>>> calls = new ArrayList<>();
            for (int i=0;i<12;i++) calls.add(pool.submit(() -> service.submit("session-a", 1, true, null)));
            Set<Object> ids = new HashSet<>(); for (var call : calls) ids.add(call.get().get("taskId")); assertEquals(1, ids.size());
            var restarted = new HistoricalTrialService(paths, props, json, new OperationsQueueService(paths, json, props));
            assertEquals(ids.iterator().next(), restarted.submit("session-a", 1, true, null).get("taskId"));
        } finally { pool.shutdownNow(); }
        assertEquals("same-name", Files.readString(production.resolve("sentinel")));
        try (var files=Files.list(service.root().resolve("queue"))) { assertEquals(1, files.count()); }
    }
    @Test void terminalRerunExplicitAndFailureReasonVisible() throws Exception {
        String id = (String) service.submit("session-a", 1, true, null).get("taskId");
        json.writeValue(service.root().resolve("status").resolve(id + ".json").toFile(), Map.of("session", "session-a", "status", "failed", "stage", "produce", "error", "fixture worker crashed", "production_mode", "isolated"));
        assertEquals("fixture worker crashed", service.detail("session-a", id).get("blocker"));
        assertThrows(ApiException.class, () -> service.submit("session-a", 1, true, null));
        Map<String,Object> rerun=service.submit("session-a",1,true,id); assertNotEquals(id,rerun.get("taskId")); assertEquals(id,rerun.get("rerunOf"));
        assertEquals(2,service.list().size());
    }
    @Test void missingWrongRankAndTraversalFailBeforeQueue() throws Exception {
        assertThrows(ApiException.class, () -> service.submit("../outside",1,true,null));
        assertThrows(ApiException.class, () -> service.submit("session-a",2,true,null));
        Files.delete(media); assertEquals(false, service.options("session-a").get("canSubmit"));
        assertThrows(ApiException.class, () -> service.submit("session-a",1,true,null));
        assertFalse(Files.exists(service.root().resolve("queue")));
    }
    @Test void fixtureDetailNeverClaimsFormalInventory() throws Exception {
        String id=(String)service.submit("session-a",1,true,null).get("taskId");
        Path candidate=service.root().resolve("production").resolve(id).resolve("01"); Files.createDirectories(candidate);
        json.writeValue(candidate.resolve("state.json").toFile(),Map.of("status","inventoried","current_version","v1"));
        json.writeValue(candidate.resolve("inventory.json").toFile(),Map.of("rank",1,"simulation",true,"version","v1"));
        Map<String,Object> detail=service.detail("session-a",id);
        assertEquals(0,detail.get("clipCount"));
        Map<?,?> row=(Map<?,?>)((List<?>)detail.get("candidates")).get(0);
        assertEquals(false,row.get("playable")); assertEquals(false,row.get("accepted")); assertEquals(true,row.get("simulation"));
    }
    @Test void metadataCoverageGapsAndInvalidOffsetsBlockSubmission() throws Exception {
        json.writeValue(dir.resolve("candidates.json").toFile(), Map.of("segments", List.of(Map.of("video",media.toString(),"offset",0,"duration",2), Map.of("video",media.toString(),"offset",4,"duration",2)), "candidates",List.of(Map.of("rank",1,"clip_start",0,"clip_end",6))));
        Map<String,Object> options=service.options("session-a"); assertEquals(false, options.get("canSubmit"));
        assertTrue(json.writeValueAsString(options).contains("missingRanges"));
        assertThrows(ApiException.class, () -> service.submit("session-a",1,true,null));
        json.writeValue(dir.resolve("candidates.json").toFile(), Map.of("segments",List.of(), "candidates",List.of(Map.of("rank",1,"clip_start",0,"clip_end",6))));
        assertEquals(false, service.options("session-a").get("canSubmit"));
    }
    @Test void outsideMediaAndCandidateOwnershipFail() throws Exception {
        candidates(Files.writeString(temp.resolve("outside.flv"),"outside").toString());
        assertFalse((Boolean)service.options("session-a").get("canSubmit"));
        json.writeValue(dir.resolve("candidates.json").toFile(), Map.of("session","another-session", "candidates",List.of(Map.of("rank",1))));
        assertFalse((Boolean)service.options("session-a").get("canSubmit"));
    }
    @Test void symlinkEscapeIsRejectedWhenFilesystemSupportsLinks() throws Exception {
        Path outside = Files.writeString(temp.resolve("outside.flv"), "outside"); Path link = dir.resolve("linked.flv");
        try { Files.createSymbolicLink(link, outside); } catch (UnsupportedOperationException | java.io.IOException | SecurityException e) { Assumptions.assumeTrue(false, "host does not permit symlinks"); }
        candidates(link.toString()); assertEquals(false, service.options("session-a").get("canSubmit"));
        assertThrows(ApiException.class, () -> service.submit("session-a",1,true,null));
    }
    @Test void unknownPaidBudgetDefaultDisabledAndConfirmationRequired() throws Exception {
        props.setHistoricalTrialFixtureEnabled(false);
        assertThrows(ApiException.class, () -> service.submit("session-a",1,true,null));
        props.setHistoricalTrialFixtureEnabled(true); assertThrows(ApiException.class, () -> service.submit("session-a",1,false,null));
        KiyaProperties defaults = new KiyaProperties(); assertFalse(defaults.isHistoricalTrialFixtureEnabled());
    }
    @Test void persistedJobContainsOnlyServerPathsAndNoPublishing() throws Exception {
        Files.writeString(dir.resolve("transcript.json"),"[]");
        String id=(String) service.submit("session-a",1,true,null).get("taskId");
        var job=json.readTree(service.root().resolve("queue").resolve(id+".json").toFile());
        assertEquals("historical_trial",job.path("source").asText()); assertFalse(job.path("publish").asBoolean());
        assertEquals(0,job.path("max_cost_fen").asInt()); assertEquals(dir.toRealPath().toString(),job.path("source_root").asText());
        assertEquals(2,job.path("input_files").size()); assertTrue(job.path("input_revision").asText().matches("[a-f0-9]{64}"));
        assertThrows(ApiException.class,()->service.detail("another",id)); assertThrows(ApiException.class,()->service.media("session-a",id,2,null));
    }
    @Test void controllerRequiresAdminAndRangeUsesSpringResource() throws Exception {
        HistoricalTrialService mock=mock(HistoricalTrialService.class); HistoricalTrialController controller=new HistoricalTrialController(mock);
        var user = new UsernamePasswordAuthenticationToken("viewer","",List.of(new SimpleGrantedAuthority("ROLE_USER")));
        assertThrows(AccessDeniedException.class, () -> controller.submit("session-a",new HistoricalTrialController.Submit(1,true,null),null));
        assertThrows(AccessDeniedException.class, () -> controller.submit("session-a",new HistoricalTrialController.Submit(1,true,null),user));
        var admin = new UsernamePasswordAuthenticationToken("admin","",List.of(new SimpleGrantedAuthority("ROLE_ADMIN")));
        when(mock.submit("session-a",1,true,null)).thenReturn(Map.of("taskId","test")); assertEquals("test",controller.submit("session-a",new HistoricalTrialController.Submit(1,true,null),admin).get("taskId"));
        Path video=Files.write(temp.resolve("test.mp4"),new byte[]{0,1,2,3,4,5,6,7}); when(mock.media("session-a","task",1,null)).thenReturn(video);
        var mvc=MockMvcBuilders.standaloneSetup(controller).build();
        mvc.perform(get("/api/sessions/session-a/isolated-trials/task/media/1").header("Range","bytes=2-5"))
            .andExpect(status().isPartialContent()).andExpect(header().string("Content-Range","bytes 2-5/8")).andExpect(content().bytes(new byte[]{2,3,4,5}));
        mvc.perform(get("/api/sessions/session-a/isolated-trials/task/media/1").header("Range","bytes=20-30")).andExpect(status().isRequestedRangeNotSatisfiable());
    }
}
