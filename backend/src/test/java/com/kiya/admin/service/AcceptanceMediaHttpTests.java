package com.kiya.admin.service;

import static org.mockito.Mockito.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

import java.nio.file.Files;
import java.nio.file.Path;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import com.kiya.admin.web.SessionController;

class AcceptanceMediaHttpTests {
    @TempDir Path temp;

    @Test void acceptanceMediaSupportsByteRangesAndNeverCallsRecovery() throws Exception {
        OperationsQueueService operations = mock(OperationsQueueService.class);
        Path media = temp.resolve("clip.mp4");
        Files.write(media, new byte[] {0, 1, 2, 3, 4, 5, 6, 7});
        when(operations.acceptanceMedia("20261124-2151", 1)).thenReturn(media);
        var mvc = MockMvcBuilders.standaloneSetup(new SessionController(
                mock(SessionService.class), mock(WorkerPaths.class), operations)).build();
        mvc.perform(get("/api/sessions/20261124-2151/acceptance-operations/media/1")
                .header("Range", "bytes=2-5"))
                .andExpect(status().isPartialContent())
                .andExpect(header().string("Content-Range", "bytes 2-5/8"))
                .andExpect(header().string("Accept-Ranges", "bytes"))
                .andExpect(content().bytes(new byte[] {2, 3, 4, 5}));
        verify(operations).acceptanceMedia("20261124-2151", 1);
        verifyNoMoreInteractions(operations);
    }
}
