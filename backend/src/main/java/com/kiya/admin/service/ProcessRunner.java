package com.kiya.admin.service;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.TimeUnit;

import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Component;

import com.kiya.admin.common.ApiException;

/** 同步调用 worker 的短命令（扫描场次、预览图、热词预览），不经过 shell，参数原样传给脚本。 */
@Component
public class ProcessRunner {
    public record Result(int exitCode, String output) {}

    private final WorkerPaths paths;

    public ProcessRunner(WorkerPaths paths) {
        this.paths = paths;
    }

    public List<String> command(String script, List<String> args) {
        List<String> cmd = new ArrayList<>();
        cmd.add(paths.python());
        cmd.add("scripts/" + script);
        cmd.addAll(args);
        return cmd;
    }

    public ProcessBuilder builder(List<String> cmd) {
        ProcessBuilder pb = new ProcessBuilder(cmd).directory(paths.worker().toFile()).redirectErrorStream(true);
        pb.environment().put("PYTHONUNBUFFERED", "1");
        pb.environment().put("PYTHONIOENCODING", "utf-8");
        return pb;
    }

    public Result run(String script, List<String> args, Duration timeout) {
        Process p;
        try {
            p = builder(command(script, args)).start();
        } catch (IOException e) {
            throw new ApiException(HttpStatus.INTERNAL_SERVER_ERROR, "无法启动 worker（检查 kiya.repo-dir 和 Python 虚拟环境）：" + e.getMessage());
        }
        InputStream in = p.getInputStream();
        CompletableFuture<String> out = CompletableFuture.supplyAsync(() -> {
            try {
                ByteArrayOutputStream buf = new ByteArrayOutputStream();
                in.transferTo(buf);
                return buf.toString(StandardCharsets.UTF_8);
            } catch (IOException e) {
                return "";
            }
        });
        try {
            if (!p.waitFor(timeout.toMillis(), TimeUnit.MILLISECONDS)) {
                p.destroyForcibly();
                throw new ApiException(HttpStatus.GATEWAY_TIMEOUT, "worker 命令超时：" + script);
            }
            return new Result(p.exitValue(), out.get(10, TimeUnit.SECONDS));
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            p.destroyForcibly();
            throw new ApiException(HttpStatus.INTERNAL_SERVER_ERROR, "被中断");
        } catch (java.util.concurrent.ExecutionException | java.util.concurrent.TimeoutException e) {
            return new Result(p.exitValue(), "");
        }
    }
}
