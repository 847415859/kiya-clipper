package com.kiya.admin.service;

import java.security.SecureRandom;
import java.time.LocalDateTime;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.context.event.EventListener;
import org.springframework.security.core.userdetails.User;
import org.springframework.security.core.userdetails.UserDetails;
import org.springframework.security.core.userdetails.UserDetailsService;
import org.springframework.security.core.userdetails.UsernameNotFoundException;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;

import com.baomidou.mybatisplus.core.conditions.query.QueryWrapper;
import com.kiya.admin.common.ApiException;
import com.kiya.admin.config.KiyaProperties;
import com.kiya.admin.entity.SysUser;
import com.kiya.admin.mapper.SysUserMapper;

@Service
public class UserService implements UserDetailsService {
    private static final Logger log = LoggerFactory.getLogger(UserService.class);
    public static final int MIN_PASSWORD = 10;

    private final SysUserMapper mapper;
    private final PasswordEncoder encoder;
    private final KiyaProperties props;

    public UserService(SysUserMapper mapper, PasswordEncoder encoder, KiyaProperties props) {
        this.mapper = mapper;
        this.encoder = encoder;
        this.props = props;
    }

    @Override
    public UserDetails loadUserByUsername(String username) {
        SysUser u = find(username);
        if (u == null) {
            throw new UsernameNotFoundException("no user");
        }
        return User.withUsername(u.getUsername()).password(u.getPasswordHash()).roles("ADMIN").build();
    }

    private SysUser find(String username) {
        return mapper.selectOne(new QueryWrapper<SysUser>().eq("username", username));
    }

    /** 第一次启动没有账号时创建管理员：用 ADMIN_PASSWORD，没给就随机生成并打印到日志（只打印这一次）。 */
    @EventListener(ApplicationReadyEvent.class)
    public void initAdmin() {
        if (mapper.selectCount(null) > 0) {
            return;
        }
        String pwd = props.getAdminPassword();
        boolean generated = pwd == null || pwd.isBlank();
        if (generated) {
            pwd = randomPassword();
        } else if (pwd.length() < MIN_PASSWORD) {
            throw new IllegalStateException("ADMIN_PASSWORD 至少 " + MIN_PASSWORD + " 位");
        }
        SysUser u = new SysUser();
        u.setUsername(props.getAdminUsername());
        u.setPasswordHash(encoder.encode(pwd));
        u.setCreatedAt(LocalDateTime.now());
        mapper.insert(u);
        if (generated) {
            log.warn("已创建管理员 {}，初始密码：{}  ——登录后请立即在“配置”页修改", props.getAdminUsername(), pwd);
        } else {
            log.info("已创建管理员 {}（密码来自 ADMIN_PASSWORD）", props.getAdminUsername());
        }
    }

    public void changePassword(String username, String oldPassword, String newPassword) {
        SysUser u = find(username);
        if (u == null || oldPassword == null || !encoder.matches(oldPassword, u.getPasswordHash())) {
            throw ApiException.badRequest("原密码不正确");
        }
        if (newPassword == null || newPassword.length() < MIN_PASSWORD) {
            throw ApiException.badRequest("新密码至少 " + MIN_PASSWORD + " 位");
        }
        u.setPasswordHash(encoder.encode(newPassword));
        mapper.updateById(u);
    }

    private static String randomPassword() {
        String chars = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789";
        SecureRandom r = new SecureRandom();
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < 16; i++) {
            sb.append(chars.charAt(r.nextInt(chars.length())));
        }
        return sb.toString();
    }
}
