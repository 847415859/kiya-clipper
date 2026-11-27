package com.kiya.admin.service;

import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.web.servlet.MockMvc;

@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("test")
class HistoricalTrialSecurityTests {
    @Autowired MockMvc mvc;
    @Test void anonymousHistoricalEndpointsRequireLogin() throws Exception {
        for (String path : new String[]{"/api/sessions/isolated-trials", "/api/sessions/session-a/isolated-trials/options", "/api/sessions/session-a/isolated-trials/history-00000000-0000-0000-0000-000000000000"})
            mvc.perform(get(path)).andExpect(status().isUnauthorized());
        mvc.perform(post("/api/sessions/session-a/isolated-trials").with(csrf()).contentType("application/json").content("{\"rank\":1,\"confirmation\":true}"))
            .andExpect(status().isUnauthorized());
    }
    @Test void viewerCannotSubmitAndAdminStillRequiresCsrf() throws Exception {
        mvc.perform(post("/api/sessions/session-a/isolated-trials").with(user("viewer").roles("USER")).with(csrf())
            .contentType("application/json").content("{\"rank\":1,\"confirmation\":true}")).andExpect(status().isForbidden());
        mvc.perform(post("/api/sessions/session-a/isolated-trials").with(user("admin").roles("ADMIN"))
            .contentType("application/json").content("{\"rank\":1,\"confirmation\":true}")).andExpect(status().isForbidden());
        mvc.perform(get("/api/sessions/session-a/isolated-trials/options").with(user("viewer").roles("USER"))).andExpect(status().isOk());
    }
}
