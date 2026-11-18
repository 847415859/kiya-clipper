package com.kiya.admin.web;

import java.util.Map;
import org.springframework.http.CacheControl;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import com.kiya.admin.service.PublishAccountService;
import jakarta.servlet.http.HttpSession;

@RestController
@RequestMapping("/api/publish-account")
public class PublishAccountController {
    private final PublishAccountService accounts;
    public PublishAccountController(PublishAccountService accounts) { this.accounts = accounts; }
    private ResponseEntity<Map<String,Object>> response(Map<String,Object> body) {
        return ResponseEntity.ok().cacheControl(CacheControl.noStore()).body(body);
    }
    @GetMapping public ResponseEntity<Map<String,Object>> status() { return response(accounts.status()); }
    @PostMapping("/qr") public ResponseEntity<Map<String,Object>> start(HttpSession session) { return response(accounts.start(session)); }
    @PostMapping("/qr/{id}/poll") public ResponseEntity<Map<String,Object>> poll(HttpSession session, @PathVariable String id) { return response(accounts.poll(session,id)); }
    @PostMapping("/login/{method}") public ResponseEntity<Map<String,Object>> login(HttpSession session, @PathVariable String method, @RequestBody Map<String,Object> body) { return response(accounts.login(session,method,null,body)); }
    @PostMapping("/login/{id}/{method}") public ResponseEntity<Map<String,Object>> continueLogin(HttpSession session, @PathVariable String id, @PathVariable String method, @RequestBody Map<String,Object> body) { return response(accounts.login(session,method,id,body)); }
    public record Confirm(String uid) {}
    @PostMapping("/qr/{id}/confirm") public ResponseEntity<Map<String,Object>> confirm(HttpSession session, @PathVariable String id, @RequestBody Confirm body) { return response(accounts.activate(session,id,body.uid())); }
    @PostMapping("/qr/{id}/cancel") public ResponseEntity<Map<String,Object>> cancel(HttpSession session, @PathVariable String id) { return response(accounts.cancel(session,id)); }
    @PostMapping("/logout") public ResponseEntity<Map<String,Object>> logout(HttpSession session) { return response(accounts.logout(session)); }
}
