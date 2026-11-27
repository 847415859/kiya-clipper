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

    @Test void historicalRoutesSupportSuffixOpenAndUnsatisfiableRanges() throws Exception {
        OperationsQueueService operations = mock(OperationsQueueService.class);
        Path media = Files.write(temp.resolve("historical.mp4"), new byte[] {0,1,2,3,4,5,6,7});
        String version = "a".repeat(64);
        when(operations.versionMedia("20261124-2151", 1, version, true)).thenReturn(media);
        when(operations.versionMedia("20261124-2151", 1, version, false)).thenReturn(media);
        var mvc = MockMvcBuilders.standaloneSetup(new SessionController(
                mock(SessionService.class), mock(WorkerPaths.class), operations)).build();
        for (String route : new String[] {"operations", "acceptance-operations"}) {
            String url = "/api/sessions/20261124-2151/" + route + "/media/1/version/" + version;
            mvc.perform(get(url).header("Range", "bytes=2-5"))
                    .andExpect(status().isPartialContent()).andExpect(header().string("Content-Range", "bytes 2-5/8"))
                    .andExpect(header().string("Cache-Control", "no-store"))
                    .andExpect(content().bytes(new byte[] {2,3,4,5}));
            mvc.perform(get(url).header("Range", "bytes=-2"))
                    .andExpect(status().isPartialContent()).andExpect(content().bytes(new byte[] {6,7}));
            mvc.perform(get(url).header("Range", "bytes=6-"))
                    .andExpect(status().isPartialContent()).andExpect(content().bytes(new byte[] {6,7}));
            mvc.perform(get(url).header("Range", "bytes=20-30"))
                    .andExpect(status().isRequestedRangeNotSatisfiable())
                    .andExpect(header().string("Content-Range", "bytes */8"));
        }
        verify(operations, never()).recover(anyString(), anyString());
    }

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
