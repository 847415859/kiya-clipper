package com.kiya.admin.service;

import static org.junit.jupiter.api.Assertions.*;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Map;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.http.HttpStatus;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.kiya.admin.common.ApiException;

class EditRecoveryTests {
    @TempDir Path root;
    final EditRecoveryStore store = new EditRecoveryStore(new ObjectMapper());
    final String client = "a".repeat(32);

    @Test void oldAutosaveCannotResurrectACommittedDraftAndClientsStaySeparate() throws Exception {
        Path file = root.resolve("edits/01.json");
        store.draft(file, client, Map.of("seq", 2, "state", Map.of("text", "新内容"), "committed", true));
        store.draft(file, client, Map.of("seq", 1, "state", Map.of("text", "旧内容")));
        assertTrue(store.drafts(file).isEmpty());
        store.draft(file, "b".repeat(32), Map.of("seq", 1, "state", Map.of("text", "另一页")));
        assertEquals(1, store.drafts(file).size());
        assertEquals("b".repeat(32), store.drafts(file).get(0).get("id"));
        assertFalse(Files.exists(file));
    }

    @Test void versionsAreImmutableAndRevisionGuardRejectsOverwritingNewerContent() throws Exception {
        Path file = root.resolve("01.json");
        assertEquals("none", store.revision(file));
        Files.writeString(file, "{\"trim\":{\"start\":1}}");
        String old = store.revision(file);
        store.archive(file, "原稿"); store.archive(file, "重复");
        assertEquals(1, store.versions(file).size());
        Files.writeString(file, "{\"trim\":{\"start\":2}}");
        var ex = assertThrows(ApiException.class, () -> store.check(file, old));
        assertEquals(HttpStatus.CONFLICT, ex.getStatus());
        store.archive(file, "修订");
        assertEquals(2, store.versions(file).size());
        assertNotEquals(old, store.revision(file));
        assertTrue(store.versions(file).stream().anyMatch(m -> m.get("id").equals(old)));
    }

    @Test void draftIdsCannotEscapeSessionAndOversizedDraftsAreRejected() {
        Path file = root.resolve("01.json");
        assertThrows(ApiException.class, () -> store.draft(file, "../outside", Map.of()));
        assertThrows(ApiException.class, () -> store.draft(file, client, Map.of("seq", 1, "state", Map.of("text", "a".repeat(2_000_001)))));
        assertTrue(store.drafts(file).isEmpty());
    }
}
