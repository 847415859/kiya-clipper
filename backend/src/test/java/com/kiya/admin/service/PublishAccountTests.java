package com.kiya.admin.service;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;
import java.time.Duration;
import java.util.List;
import java.util.Map;
import java.nio.file.Path;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.mock.web.MockHttpSession;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.kiya.admin.common.ApiException;
import com.kiya.admin.config.KiyaProperties;
import com.kiya.admin.config.StreamerContext;
import com.kiya.admin.mapper.JobMapper;
import com.kiya.admin.entity.Job;

class PublishAccountTests {
    final String id = "a".repeat(32);
    ProcessRunner runner;
    JobMapper jobs;
    PublishAccountService service;
    MockHttpSession session;
    ObjectMapper om = new ObjectMapper();

    @BeforeEach void init() {
        runner = mock(ProcessRunner.class); jobs = mock(JobMapper.class);
        KiyaProperties props = new KiyaProperties(); props.setStreamer("kiya");
        service = new PublishAccountService(new WorkerPaths(props), runner, om, jobs);
        session = new MockHttpSession();
        when(jobs.selectCount(any())).thenReturn(0L);
        when(runner.run(eq("publish_account.py"), anyList(), any(Duration.class))).thenAnswer(call -> {
            List<String> args = call.getArgument(1);
            Map<String,Object> response = switch (args.get(0)) {
                case "start" -> Map.of("login_id",id,"state","waiting","qr_image","data:image/png;base64,test","expires_at",1234);
                case "poll" -> Map.of("state","ready","account",Map.of("uid","123","name","用户","cookie","secret"),"token_info","secret");
                default -> Map.of("state","logged_in","account",Map.of("uid","123","name","用户"));
            };
            return new ProcessRunner.Result(0, om.writeValueAsString(response));
        });
    }
    @AfterEach void clear() { StreamerContext.clear(); }

    private void ready() {
        service.start(session);
        ((PublishAccountService.Ticket)session.getAttribute("publish-login:kiya")).checkedAt = 0;
        service.poll(session,id);
    }

    @Test void loginIsBoundToSessionAndStreamer() {
        service.start(session);
        assertThrows(ApiException.class, () -> service.poll(new MockHttpSession(),id));
        StreamerContext.set("miko");
        assertThrows(ApiException.class, () -> service.poll(session,id));
        verify(runner,times(1)).run(anyString(),anyList(),any());
    }

    @Test void readyResponseIsRedactedAndRequiresConfirmation() throws Exception {
        ready();
        Map<String,Object> result = service.poll(session,id);
        assertFalse(om.writeValueAsString(result).contains("secret"));
        assertThrows(ApiException.class, () -> service.activate(session,id,"999"));
        assertEquals("logged_in",service.activate(session,id,"123").get("state"));
        assertNull(session.getAttribute("publish-login:kiya"));
    }

    @Test void pendingUploadBlocksSwitchAndLogout() {
        ready();
        when(jobs.selectCount(any())).thenReturn(1L);
        assertThrows(ApiException.class, () -> service.activate(session,id,"123"));
        assertThrows(ApiException.class, () -> service.logout(session));
        verify(runner,times(2)).run(anyString(),anyList(),any());
    }

    @Test void pollingAndQrRefreshAreThrottled() {
        service.start(session);
        assertEquals("waiting", service.poll(session,id).get("state"));
        assertThrows(ApiException.class, () -> service.start(session));
        verify(runner,times(1)).run(anyString(),anyList(),any());
    }

    @Test void unverifiedOrCancelledQrCannotBeActivated() {
        service.start(session);
        assertThrows(ApiException.class, () -> service.activate(session,id,"123"));
        service.cancel(session,id);
        assertThrows(ApiException.class, () -> service.activate(session,id,"123"));
    }

    @Test void passwordTravelsOnlyThroughStdinAndPublicResultIsFiltered() throws Exception {
        when(runner.runInput(anyString(),anyList(),anyString(),any())).thenAnswer(call -> {
            assertFalse(call.<List<String>>getArgument(1).toString().contains("private-password"));
            assertEquals("private-password", om.readTree(call.<String>getArgument(2)).path("password").asText());
            return new ProcessRunner.Result(0,om.writeValueAsString(Map.of("login_id",id,"state","ready",
                "expires_at",System.currentTimeMillis()/1000+300,"account",Map.of("uid","123","name","用户","cookie","secret"),
                "credentials","secret","login_url","https://passport.bilibili.com/?access_token=secret")));
        });
        Map<String,Object> result=service.login(session,"password",null,Map.of("username","user","password","private-password"));
        assertFalse(om.writeValueAsString(result).contains("secret"));
        assertThrows(ApiException.class,()->service.login(new MockHttpSession(),"password",id,Map.of()));
        assertThrows(ApiException.class,()->service.login(session,"sms-code",id,Map.of("code","001234")));
        verify(runner,never()).run(anyString(),anyList(),any());
        assertEquals("logged_in",service.activate(session,id,"123").get("state"));
    }

    @Test void smsCanContinueOnlyWithinOwnedTicketAndSendIsThrottled() throws Exception {
        when(runner.runInput(anyString(),anyList(),anyString(),any())).thenReturn(new ProcessRunner.Result(0,
            om.writeValueAsString(Map.of("login_id",id,"state","sms_sent","expires_at",System.currentTimeMillis()/1000+600))));
        service.login(session,"sms-send",null,Map.of("country","86","phone","13800000000"));
        assertThrows(ApiException.class,()->service.login(session,"sms-send",id,Map.of("country","86","phone","13800000000")));
        service.login(session,"sms-code",id,Map.of("code","001234"));
        verify(runner,times(2)).runInput(anyString(),anyList(),anyString(),any());
        StreamerContext.set("miko");
        assertThrows(ApiException.class,()->service.login(session,"sms-code",id,Map.of("code","001234")));
    }

    @Test void stdinInputReachesChildWithoutBeingACommandArgument(@TempDir Path tmp) throws Exception {
        java.nio.file.Files.createDirectories(tmp.resolve("worker"));
        Path source=tmp.resolve("StdinCheck.java");
        java.nio.file.Files.writeString(source,"class StdinCheck { public static void main(String[] args) throws Exception { "
            + "byte[] data=System.in.readAllBytes(); System.out.print(data.length); } }");
        KiyaProperties props=new KiyaProperties(); props.setRepoDir(tmp.toString()); props.setStreamer("kiya");
        String java=Path.of(System.getProperty("java.home"),"bin",System.getProperty("os.name").startsWith("Windows")?"java.exe":"java").toString();
        ProcessRunner process=new ProcessRunner(new WorkerPaths(props)) {
            @Override public List<String> command(String script,List<String> args) { return List.of(java,source.toString()); }
        };
        String input="{\"password\":\"private-秘密\"}";
        var result=process.runInput("ignored.py",List.of(),input,Duration.ofSeconds(20));
        assertEquals(0,result.exitCode());
        assertEquals(String.valueOf(input.getBytes("UTF-8").length),result.output());
    }

    @Test void queuedTaskRestoresSubmittedStreamerBeforeBuildingWorkerCommand(@TempDir Path tmp) throws Exception {
        KiyaProperties props = new KiyaProperties(); props.setStreamer("kiya"); props.setRepoDir(tmp.toString());
        WorkerPaths paths = new WorkerPaths(props);
        ProcessRunner process = mock(ProcessRunner.class);
        JobMapper mapper = mock(JobMapper.class);
        Job job = new Job(); job.setId(77L); job.setType("LEARN"); job.setStatus("PENDING"); job.setParams("{\"_streamer\":\"miko\"}");
        when(mapper.selectById(77L)).thenReturn(job);
        String java = Path.of(System.getProperty("java.home"),"bin",System.getProperty("os.name").startsWith("Windows")?"java.exe":"java").toString();
        when(process.command(anyString(),anyList())).thenReturn(List.of(java,"-version"));
        when(process.builder(anyList())).thenAnswer(call -> {
            assertEquals("miko", paths.streamer());
            ProcessBuilder pb = new ProcessRunner(paths).builder(call.getArgument(0));
            assertEquals("miko", pb.environment().get("KIYA_STREAMER"));
            return pb.directory(tmp.toFile());
        });
        JobService tasks = new JobService(mapper, paths, process, mock(StyleService.class),mock(SessionService.class),props,om);
        StreamerContext.set("kiya");
        try {
            var run = JobService.class.getDeclaredMethod("run",long.class); run.setAccessible(true); run.invoke(tasks,77L);
            assertEquals("SUCCESS",job.getStatus());
            assertEquals("kiya",StreamerContext.get());
            verify(process).builder(anyList());
        } finally { tasks.shutdown(); }
    }
}
