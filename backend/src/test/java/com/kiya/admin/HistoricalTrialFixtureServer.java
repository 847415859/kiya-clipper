package com.kiya.admin;

import org.springframework.boot.SpringApplication;

/** Local HTTP acceptance harness. Uses test-scoped H2; never package or deploy this launcher. */
public final class HistoricalTrialFixtureServer {
    private HistoricalTrialFixtureServer() {}
    public static void main(String[] args) {
        SpringApplication.run(AdminApplication.class, args);
    }
}
