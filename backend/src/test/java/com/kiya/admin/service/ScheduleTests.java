package com.kiya.admin.service;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;
import java.awt.image.BufferedImage;
import java.io.ByteArrayOutputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.List;
import java.util.Map;
import javax.imageio.ImageIO;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.mock.web.MockMultipartFile;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.kiya.admin.common.ApiException;
import com.kiya.admin.config.KiyaProperties;
import com.kiya.admin.config.StreamerContext;

class ScheduleTests {
    @TempDir Path root;
    WorkerPaths paths;
    ProcessRunner runner;
    ScheduleService service;
    ObjectMapper om = new ObjectMapper();
    @BeforeEach void setup() {
        var props=new KiyaProperties();props.setRepoDir(root.toString());paths=new WorkerPaths(props);
        runner=mock(ProcessRunner.class);service=new ScheduleService(paths,runner,om);
        when(runner.run(eq("schedule.py"),anyList(),any())).thenReturn(new ProcessRunner.Result(0,"{\"revision\":\"v1\"}"));
    }
    @Test void uploadNormalizesAndSeparatesStreamers() throws Exception {
        var bytes=new ByteArrayOutputStream();ImageIO.write(new BufferedImage(100,50,BufferedImage.TYPE_INT_ARGB),"png",bytes);
        var result=service.upload(new MockMultipartFile("file","../../unsafe.png","image/png",bytes.toByteArray()));
        String id=(String)result.get("image_id");Path image=service.imageFile(id);
        assertTrue(Files.isRegularFile(image));assertEquals(100,ImageIO.read(image.toFile()).getWidth());
        assertEquals(0xffffff,ImageIO.read(image.toFile()).getRGB(0,0)&0xffffff);
        try { StreamerContext.set("other");assertFalse(Files.exists(service.imageFile(id))); }
        finally { StreamerContext.clear(); }
        assertThrows(ApiException.class,()->service.imageFile("../secret"));
        verifyNoInteractions(runner);
    }
    @Test void rejectsSpoofedImagesAndOversizedFiles() {
        assertThrows(ApiException.class,()->service.upload(new MockMultipartFile("file","x.png","image/png","<html>text</html>".getBytes())));
        assertThrows(ApiException.class,()->service.upload(new MockMultipartFile("file","x.jpg","image/jpeg",new byte[8*1024*1024+1])));
    }
    @Test void savePassesScopedRootAndTemporaryRequestIsCleaned() {
        when(runner.run(eq("schedule.py"),anyList(),any())).thenAnswer(call->{
            List<String> args=call.getArgument(1);
            assertEquals(paths.output().toString(),args.get(args.indexOf("--root")+1));
            assertEquals("kiya",args.get(args.indexOf("--streamer")+1));
            var body=om.readTree(Path.of(args.get(args.indexOf("--input")+1)).toFile());
            assertTrue(body.path("confirm").asBoolean());assertEquals("none",body.path("expected_revision").asText());
            return new ProcessRunner.Result(0,"{\"revision\":\"v1\"}");
        });
        assertEquals("v1",service.save("2026-11-16",Map.of("entries",List.of()),"none",true).get("revision"));
        try(var files=Files.list(paths.output().resolve("_schedule"))) {assertEquals(0,files.count());}
        catch(Exception e){fail(e);}
    }
    @Test void conflictAndBadDateDoNotBecomeSuccessfulSave() {
        when(runner.run(eq("schedule.py"),anyList(),any())).thenReturn(new ProcessRunner.Result(0,"{\"conflict\":true,\"error\":\"changed\"}"));
        assertThrows(ApiException.class,()->service.save("2026-11-16",Map.of(),"old",true));
        assertThrows(ApiException.class,()->service.get("../file"));
        verify(runner,times(1)).run(eq("schedule.py"),anyList(),eq(Duration.ofSeconds(30)));
    }
    @Test void publishSettingsPersistWithoutAppendingGeneratedDescription() throws Exception {
        Path file=paths.sessionDir("20261117-1200").resolve("publish/01.json");Files.createDirectories(file.getParent());
        Files.writeString(file,"{\"title\":\"title\",\"description\":\"base\"}");
        var publish=new PublishService(paths,runner,om);
        publish.save("20261117-1200",1,null,null,null,null,null,true,"2026-11-18T12:00");
        var body=om.readTree(file.toFile());assertTrue(body.path("include_schedule").asBoolean());
        assertEquals("2026-11-18T12:00",body.path("schedule_publish_at").asText());
        assertEquals("base",body.path("description").asText());
        assertThrows(ApiException.class,()->publish.save("20261117-1200",1,null,null,null,null,null,true,"wrong"));
    }
}
