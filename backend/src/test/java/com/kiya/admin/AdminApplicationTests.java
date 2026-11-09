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
    }

    @Test
    void meIsPublic() throws Exception {
        mvc.perform(get("/api/auth/me"))
            .andExpect(status().isOk())
            .andExpect(jsonPath("$.authenticated").value(false));
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
}
