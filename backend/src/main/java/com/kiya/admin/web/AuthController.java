package com.kiya.admin.web;

import java.util.Map;

import org.springframework.security.authentication.AnonymousAuthenticationToken;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import com.kiya.admin.service.UserService;
import org.springframework.security.web.csrf.DeferredCsrfToken;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;

@RestController
@RequestMapping("/api/auth")
public class AuthController {
    private final UserService users;

    public AuthController(UserService users) {
        this.users = users;
    }

    @GetMapping("/csrf")
    public Map<String,String> csrf(HttpServletRequest request,HttpServletResponse response) {
        var token=((DeferredCsrfToken)request.getAttribute(DeferredCsrfToken.class.getName())).get();
        response.setHeader("Cache-Control","no-store");
        return Map.of("token",token.getToken(),"headerName",token.getHeaderName());
    }

    /** 前端启动时调用：是否已登录（同时会下发 XSRF-TOKEN Cookie）。 */
    @GetMapping("/me")
    public Map<String, Object> me(Authentication auth) {
        if (auth == null || !auth.isAuthenticated() || auth instanceof AnonymousAuthenticationToken) {
            return Map.of("authenticated", false);
        }
        return Map.of("authenticated", true, "username", auth.getName());
    }

    public record PasswordReq(String oldPassword, String newPassword) {}

    @PostMapping("/password")
    public Map<String, Object> password(@RequestBody PasswordReq req, Authentication auth) {
        users.changePassword(auth.getName(), req.oldPassword(), req.newPassword());
        return Map.of("ok", true);
    }
}
