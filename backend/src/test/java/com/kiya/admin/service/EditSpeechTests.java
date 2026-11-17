package com.kiya.admin.service;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.kiya.admin.common.ApiException;
import com.kiya.admin.config.KiyaProperties;

class EditSpeechTests {
    @TempDir Path root;
    final ObjectMapper om = new ObjectMapper();
    KiyaProperties props;
    WorkerPaths paths;
    EditService service;
    ProcessRunner runner;
    Path session;

    @BeforeEach void setup() throws Exception {
        props = new KiyaProperties();
        props.setRepoDir(root.toString());
        paths = new WorkerPaths(props);
        session = root.resolve("worker/output/kiya/20261117-1200");
        Files.createDirectories(session);
        Files.writeString(session.resolve("candidates.json"), "{\"candidates\":[{\"rank\":1,\"t\":120,\"clip_start\":100,\"clip_end\":160}]}");
        runner = mock(ProcessRunner.class);
        service = new EditService(paths, runner, mock(StyleService.class), om, mock(SessionService.class));
    }

    @Test void speechUsesSessionTimesAndWordTextAndFiltersRange() throws Exception {
        Files.writeString(session.resolve("transcript.json"), """
            {"sentences":[
              {"t0":90,"t1":95,"text":"范围之前"},
              {"t0":100,"t1":104,"text":"另一种原文","words":[[100,102,"逐词"],[102,104,"原文"]]},
              {"t0":160,"t1":165,"text":"范围之后"}]}
            """);
        var result = service.speech("20261117-1200", 1, 99, 160);
        var rows = (List<?>) result.get("sentences");
        assertEquals(1, rows.size());
        assertEquals("逐词原文", ((Map<?, ?>) rows.get(0)).get("text"));
        assertEquals(100.0, ((Map<?, ?>) rows.get(0)).get("t0"));
        assertEquals(100.0, service.get("20261117-1200", 1).get("sourceCandidateStart"));
    }

    @Test void missingAndBrokenTranscriptsDoNotBreakEditing() throws Exception {
        assertEquals(false, service.speech("20261117-1200", 1, 100, 160).get("available"));
        Files.writeString(session.resolve("transcript.json"), "{");
        assertEquals(false, service.speech("20261117-1200", 1, 100, 160).get("available"));
    }

    @Test void invalidRangesAndMissingCandidateAreRejected() {
        assertThrows(ApiException.class, () -> service.speech("20261117-1200", 1, Double.NaN, 160));
        assertThrows(ApiException.class, () -> service.speech("20261117-1200", 1, 0, 1000));
        assertThrows(ApiException.class, () -> service.speech("20261117-1200", 1, 160, 100));
        assertThrows(ApiException.class, () -> service.speech("20261117-1200", 2, 100, 160));
    }

    @Test void streamsRemainIsolated() throws Exception {
        Files.writeString(session.resolve("transcript.json"), "{\"sentences\":[{\"t0\":100,\"t1\":104,\"text\":\"主播甲\"}]}");
        Path other = root.resolve("worker/output/other/20261117-1200");
        Files.createDirectories(other);
        Files.copy(session.resolve("candidates.json"), other.resolve("candidates.json"));
        props.setStreamer("other");
        assertEquals(false, service.speech("20261117-1200", 1, 100, 160).get("available"));
    }

    @Test void savingPreservesPerClipSentenceOverrides() throws Exception {
        when(runner.run(eq("edit_check.py"), anyList(), any())).thenAnswer(invocation -> {
            List<String> args = invocation.getArgument(1);
            Map<String, Object> edit = om.readValue(Path.of(args.get(0)).toFile(), new TypeReference<>() {});
            return new ProcessRunner.Result(0, om.writeValueAsString(Map.of("edit", edit, "warnings", List.of())));
        });
        Map<String, Object> correction = Map.of("t0", 100, "t1", 104, "original", "原话", "text", "修订");
        var edit = Map.<String, Object>of("trim", Map.of("start", 0, "end", 60), "items", List.of(),
            "subtitles", Map.of("enabled", true, "sentence_overrides", List.of(correction)));
        service.save("20261117-1200", 1, edit, null, false);
        var saved = om.readTree(session.resolve("edits/01.json").toFile());
        assertEquals("修订", saved.path("subtitles").path("sentence_overrides").get(0).path("text").asText());
        assertTrue(saved.has("human_saved_at"));
    }

    @Test void savingRejectsAStaleRevisionAndKeepsPriorVersions() throws Exception {
        when(runner.run(eq("edit_check.py"), anyList(), any())).thenAnswer(invocation -> {
            List<String> args = invocation.getArgument(1);
            var edit = om.readValue(Path.of(args.get(0)).toFile(), new TypeReference<Map<String, Object>>() {});
            return new ProcessRunner.Result(0, om.writeValueAsString(Map.of("edit", edit)));
        });
        var edit = Map.<String, Object>of("window", Map.of("start", 100, "end", 160), "trim", Map.of("start", 0, "end", 60),
                "subtitles", Map.of("enabled", true), "items", List.of(), "cuts", List.of(Map.of("start", 3, "end", 5)));
        var first = service.save("20261117-1200", 1, edit, null, false, "none");
        String revision = (String) first.get("revision");
        assertEquals(revision, service.get("20261117-1200", 1).get("revision"));
        var second = service.save("20261117-1200", 1, edit, null, false, revision);
        var rejected = assertThrows(ApiException.class, () -> service.save("20261117-1200", 1, edit, null, false, revision));
        assertEquals(org.springframework.http.HttpStatus.CONFLICT, rejected.getStatus());
        assertEquals(second.get("revision"), service.get("20261117-1200", 1).get("revision"));
        assertEquals(2, ((List<?>) service.recovery("20261117-1200", 1).get("versions")).size());
    }
}
