package com.kiya.admin.web;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import com.kiya.admin.entity.Job;
import com.kiya.admin.service.JobService;

@RestController
@RequestMapping("/api/jobs")
public class JobController {
    private final JobService jobs;

    public JobController(JobService jobs) {
        this.jobs = jobs;
    }

    @GetMapping("/types")
    public Map<String, String> types() {
        return JobService.TYPES;
    }

    @GetMapping
    public List<Map<String, Object>> list(@RequestParam(required = false) String session,
                                          @RequestParam(defaultValue = "50") int limit) {
        List<Map<String, Object>> out = new ArrayList<>();
        for (Job j : jobs.list(session, limit)) {
            out.add(view(j));
        }
        return out;
    }

    public record SubmitReq(String type, String session, Map<String, Object> params) {}

    @PostMapping
    public Map<String, Object> submit(@RequestBody SubmitReq req, Authentication auth) {
        return view(jobs.submit(req.type(), req.session(), req.params(), auth.getName()));
    }

    @GetMapping("/{id}")
    public Map<String, Object> get(@PathVariable long id) {
        return view(jobs.get(id));
    }

    @GetMapping("/{id}/log")
    public Map<String, Object> log(@PathVariable long id, @RequestParam(defaultValue = "0") long offset) {
        return jobs.log(id, offset);
    }

    @PostMapping("/{id}/cancel")
    public Map<String, Object> cancel(@PathVariable long id) {
        jobs.cancel(id);
        return Map.of("ok", true);
    }

    private static Map<String, Object> view(Job j) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("id", j.getId());
        m.put("type", j.getType());
        m.put("typeName", JobService.typeName(j.getType()));
        m.put("session", j.getSessionName());
        m.put("params", j.getParams());
        m.put("status", j.getStatus());
        m.put("exitCode", j.getExitCode());
        m.put("summary", j.getSummary());
        m.put("createdBy", j.getCreatedBy());
        m.put("createdAt", j.getCreatedAt());
        m.put("startedAt", j.getStartedAt());
        m.put("finishedAt", j.getFinishedAt());
        return m;
    }
}
