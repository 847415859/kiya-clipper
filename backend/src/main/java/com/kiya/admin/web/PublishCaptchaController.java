package com.kiya.admin.web;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.UUID;
import org.springframework.core.io.ClassPathResource;
import org.springframework.http.CacheControl;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

/** Public, credential-free sandbox document. Third-party captcha scripts never run in the settings DOM. */
@RestController
public class PublishCaptchaController {
    @GetMapping("/publish-captcha")
    public ResponseEntity<String> frame() throws IOException {
        String nonce = UUID.randomUUID().toString().replace("-", "");
        String vendors = " https://*.geetest.com https://*.geevisit.com https://*.gsensebot.com";
        String csp = "default-src 'none'; script-src 'nonce-" + nonce + "' 'unsafe-eval'" + vendors
                + "; style-src 'unsafe-inline'" + vendors + "; img-src data:" + vendors
                + "; connect-src" + vendors + "; font-src data:" + vendors
                + "; frame-src" + vendors + "; worker-src blob:; frame-ancestors 'self'; base-uri 'none'; form-action 'none'";
        String html;
        try (var input = new ClassPathResource("login/captcha-frame.html").getInputStream()) {
            html = new String(input.readAllBytes(), StandardCharsets.UTF_8).replace("{{nonce}}", nonce);
        }
        return ResponseEntity.ok().cacheControl(CacheControl.noStore()).contentType(MediaType.TEXT_HTML)
                .header("Content-Security-Policy", csp).header("X-Frame-Options", "SAMEORIGIN").body(html);
    }
}
