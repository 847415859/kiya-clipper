package com.kiya.admin.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

/** application.yml 里 kiya.* 的配置。 */
@ConfigurationProperties(prefix = "kiya")
public class KiyaProperties {
    private String repoDir = "/opt/kiya-clipper";
    private String recDir = "/data/rec";
    private String python = "";
    private String adminUsername = "admin";
    private String adminPassword = "";
    private long taskTimeoutMinutes = 180;

    public String getRepoDir() { return repoDir; }
    public void setRepoDir(String repoDir) { this.repoDir = repoDir; }
    public String getRecDir() { return recDir; }
    public void setRecDir(String recDir) { this.recDir = recDir; }
    public String getPython() { return python; }
    public void setPython(String python) { this.python = python; }
    public String getAdminUsername() { return adminUsername; }
    public void setAdminUsername(String adminUsername) { this.adminUsername = adminUsername; }
    public String getAdminPassword() { return adminPassword; }
    public void setAdminPassword(String adminPassword) { this.adminPassword = adminPassword; }
    public long getTaskTimeoutMinutes() { return taskTimeoutMinutes; }
    public void setTaskTimeoutMinutes(long taskTimeoutMinutes) { this.taskTimeoutMinutes = taskTimeoutMinutes; }
}
