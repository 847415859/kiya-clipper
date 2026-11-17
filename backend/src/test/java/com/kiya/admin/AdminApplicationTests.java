package com.kiya.admin;

import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.csrf;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.mock.web.MockHttpSession;
import org.springframework.security.test.context.support.WithMockUser;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.web.servlet.MockMvc;

/** 冒烟测试：应用能启动、未登录拦截、登录流程、CSRF、参数校验。用内存数据库 H2，不需要 MySQL。 */
@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("test")
class AdminApplicationTests {

    @Autowired
    MockMvc mvc;

    @Test
    void apiRequiresLogin() throws Exception {
        mvc.perform(get("/api/sessions")).andExpect(status().isUnauthorized());
        mvc.perform(get("/api/schedules")).andExpect(status().isUnauthorized());
        mvc.perform(get("/api/schedules/images/" + "a".repeat(32))).andExpect(status().isUnauthorized());
    }

    @Test
    void meIsPublic() throws Exception {
        mvc.perform(get("/api/auth/me"))
            .andExpect(status().isOk())
            .andExpect(jsonPath("$.authenticated").value(false));
    }

    @Test
    @org.springframework.test.annotation.DirtiesContext(methodMode=org.springframework.test.annotation.DirtiesContext.MethodMode.BEFORE_METHOD)
    void csrfEndpointWorksWithoutJavascriptCookieAccess() throws Exception {
        var result=mvc.perform(get("/api/auth/csrf")).andExpect(status().isOk()).andReturn();
        var body=new com.fasterxml.jackson.databind.ObjectMapper().readTree(result.getResponse().getContentAsString());
        var cookie=result.getResponse().getCookie("XSRF-TOKEN");
        org.junit.jupiter.api.Assertions.assertNotNull(cookie);
        org.junit.jupiter.api.Assertions.assertEquals(cookie.getValue(),body.path("token").asText());
        mvc.perform(post("/api/auth/login").cookie(cookie).header(body.path("headerName").asText(),body.path("token").asText())
            .param("username","admin").param("password","test-password-123")).andExpect(status().isOk());
    }

    @Test
    void loginWithoutCsrfIsRejected() throws Exception {
        mvc.perform(post("/api/auth/login").param("username", "admin").param("password", "test-password-123"))
            .andExpect(status().isForbidden());
    }

    @Test
    void loginFlow() throws Exception {
        mvc.perform(post("/api/auth/login").with(csrf()).param("username", "admin").param("password", "wrong-password-x"))
            .andExpect(status().isUnauthorized());
        MockHttpSession session = (MockHttpSession) mvc.perform(post("/api/auth/login").with(csrf())
                .param("username", "admin").param("password", "test-password-123"))
            .andExpect(status().isOk())
            .andExpect(jsonPath("$.username").value("admin"))
            .andReturn().getRequest().getSession(false);
        mvc.perform(get("/api/auth/me").session(session))
            .andExpect(jsonPath("$.authenticated").value(true));
        mvc.perform(get("/api/jobs").session(session)).andExpect(status().isOk());
        mvc.perform(get("/api/config/words").session(session)).andExpect(status().isOk());
    }

    @Test
    @WithMockUser
    void badSessionNameRejected() throws Exception {
        mvc.perform(get("/api/sessions/bad.name/candidates")).andExpect(status().isBadRequest());
        mvc.perform(get("/api/sessions/20261109-2014/clips/..%2Fsecret.mp4")).andExpect(status().isBadRequest());
    }

    @Test
    @WithMockUser
    void unknownJobTypeRejected() throws Exception {
        mvc.perform(post("/api/jobs").with(csrf()).contentType("application/json")
                .content("{\"type\":\"RM_RF\",\"session\":\"20261109-2014\"}"))
            .andExpect(status().isBadRequest());
    }
    @Test
    @WithMockUser
    void valueStrategyReadIsScopedToValidSessions() throws Exception {
        mvc.perform(get("/api/sessions/20261109-2014/value-strategy"))
            .andExpect(status().isOk()).andExpect(jsonPath("$.exploration_candidates").isMap());
        mvc.perform(get("/api/sessions/bad.name/value-strategy"))
            .andExpect(status().isBadRequest());
    }

    @Test
    @WithMockUser
    void valueRecallRejectsInvalidBudgetBeforeExecution() throws Exception {
        mvc.perform(post("/api/jobs").with(csrf()).contentType("application/json")
            .content("{\"type\":\"VALUE_RECALL\",\"session\":\"20261109-2014\",\"params\":{\"budget\":11}}"))
            .andExpect(status().isBadRequest());
    }

    @Test
    @WithMockUser
    void attributionKeepsTargetsAndAgeWindowsSeparate() throws Exception {
        mvc.perform(post("/api/memory/actions").with(csrf()).contentType("application/json")
            .content("{\"action\":\"publish\",\"bvid\":\"BV1ab411c7de\",\"session\":\"20261109-2014\",\"rank\":1}"))
            .andExpect(status().isOk());
        for (String window : java.util.List.of("24h", "7d")) {
            mvc.perform(post("/api/memory/actions").with(csrf()).contentType("application/json")
                .content("{\"action\":\"valueOutcome\",\"bvid\":\"BV1ab411c7de\",\"target\":\"clip_account\",\"window\":\"" + window + "\",\"count\":2,\"view\":1000,\"source\":\"test export\",\"verified\":true}"))
                .andExpect(status().isOk());
        }
        mvc.perform(get("/api/memory"))
            .andExpect(jsonPath("$.decisions.published.BV1ab411c7de.attribution.clip_account.24h.count").value(2))
            .andExpect(jsonPath("$.decisions.published.BV1ab411c7de.attribution.clip_account.7d.count").value(2));
        mvc.perform(post("/api/memory/actions").with(csrf()).contentType("application/json")
            .content("{\"action\":\"valueOutcome\",\"bvid\":\"BV1ab411c7de\",\"target\":\"streamer\",\"window\":\"24h\",\"count\":2,\"view\":1000,\"source\":\"test export\",\"verified\":false}"))
            .andExpect(status().isBadRequest());
    }

}
