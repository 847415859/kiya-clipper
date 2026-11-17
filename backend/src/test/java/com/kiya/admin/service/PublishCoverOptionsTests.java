package com.kiya.admin.service;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.mockito.ArgumentCaptor;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.kiya.admin.common.ApiException;
import com.kiya.admin.config.KiyaProperties;

class PublishCoverOptionsTests {
    @TempDir Path root;
    ProcessRunner runner;
    PublishService service;
    Path file;
    ObjectMapper om = new ObjectMapper();

    @BeforeEach void setup() throws Exception {
        KiyaProperties props = new KiyaProperties();
        props.setRepoDir(root.toString());
        WorkerPaths paths = new WorkerPaths(props);
        runner = mock(ProcessRunner.class);
        service = new PublishService(paths, runner, om);
        file = root.resolve("worker/output/kiya/20261117-1200/publish/01.json");
        Files.createDirectories(file.getParent());
        Files.writeString(file, "{\"title\":\"original\",\"cover\":\"01_cover.jpg\"}");
        when(runner.run(eq("publish_pack.py"), anyList(), any())).thenReturn(new ProcessRunner.Result(0, "{\"prompt\":\"preview\"}"));
    }

    @Test void additionsRemainOneArgumentAndPreviewNeverRequestsGeneration() {
        var result = service.coverPrompt("20261117-1200", 1, "HELLO", "--keep\n人物靠右", "seedream");
        assertEquals("preview", result.get("prompt"));
        ArgumentCaptor<List<String>> args = ArgumentCaptor.forClass(List.class);
        verify(runner).run(eq("publish_pack.py"), args.capture(), eq(Duration.ofSeconds(30)));
        assertTrue(args.getValue().contains("--cover-prompt-only"));
        assertTrue(args.getValue().contains("--cover-supplement=--keep\n人物靠右"));
        assertTrue(args.getValue().contains("--cover-reference=seedream"));
        assertFalse(args.getValue().contains("--cover-mode"));
    }

    @Test void seedreamUsesLongerTimeoutAndOldCallersRemainCompatible() {
        service.cover("20261117-1200", 1, 5.0, "text", null, null, "seedream", "粉紫色", "frame");
        verify(runner).run(eq("publish_pack.py"), argThat(args -> args.contains("--cover-supplement=粉紫色")), eq(Duration.ofSeconds(300)));
        service.cover("20261117-1200", 1, null, "text", null, null, null);
        verify(runner).run(eq("publish_pack.py"), argThat(args -> !args.stream().anyMatch(a -> a.startsWith("--cover-supplement"))), eq(Duration.ofSeconds(120)));
    }

    @Test void settingsSaveAndCanBeClearedWithoutChangingTheCurrentImage() throws Exception {
        var saved = service.save("20261117-1200", 1, "new title", null, null,
                Map.of("supplement", "  人物靠右  ", "reference", "seedream"), "新封面字");
        assertEquals("01_cover.jpg", saved.get("cover"));
        var parsed = om.readTree(file.toFile());
        assertEquals("人物靠右", parsed.path("cover_seedream_options").path("supplement").asText());
        assertEquals("新封面字", parsed.path("cover_text").asText());
        service.save("20261117-1200", 1, null, null, null, Map.of("supplement", "", "reference", "frame"));
        assertEquals("", om.readTree(file.toFile()).path("cover_seedream_options").path("supplement").asText());
        verifyNoInteractions(runner);
    }

    @Test void invalidSettingsStopBeforeWorkerInvocation() {
        assertThrows(ApiException.class, () -> service.coverPrompt("20261117-1200", 1, null, "x".repeat(1501), "frame"));
        assertThrows(ApiException.class, () -> service.coverPrompt("20261117-1200", 1, null, "a\0b", "frame"));
        assertThrows(ApiException.class, () -> service.coverPrompt("20261117-1200", 1, null, "", "../other"));
        assertThrows(ApiException.class, () -> service.save("20261117-1200", 1, null, null, null, Map.of("supplement", 1, "reference", "frame")));
        verifyNoInteractions(runner);
    }

    @Test void historySelectionAndFilePathsAreScopedToCandidate() {
        String version = "a".repeat(32);
        service.selectCoverVersion("20261117-1200", 1, version);
        verify(runner).run(eq("publish_pack.py"), argThat(args -> args.contains("--cover-select-version") && args.contains(version)), eq(Duration.ofSeconds(30)));
        assertEquals(file.getParent().resolve("cover_history/01").resolve(version + ".jpg"), service.coverVersionFile("20261117-1200", 1, version));
        assertThrows(ApiException.class, () -> service.coverVersionFile("20261117-1200", 1, "../../other"));
        assertThrows(ApiException.class, () -> service.selectCoverVersion("20261117-1200", 1, "../other"));
    }
}
