package com.kiya.admin.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

/** application.yml 里 kiya.* 的配置。 */
@ConfigurationProperties(prefix = "kiya")
public class KiyaProperties {
    private String repoDir = "/opt/kiya-clipper";
    private String recDir = "/data/rec";
    private String opsDir = "";
    private String acceptanceDir = "/var/lib/kiya-next-session-acceptance";
    private boolean opsRecoveryEnabled;
    private boolean historicalTrialFixtureEnabled;
    /** Shared POSIX group, restricted to acceptanceDir/history. Ignored on non-POSIX filesystems. */
    private String historicalTrialGroup = "kiya";
    public String getHistoricalTrialGroup() { return historicalTrialGroup; }
    public void setHistoricalTrialGroup(String group) { historicalTrialGroup = group; }
    public boolean isHistoricalTrialFixtureEnabled() { return historicalTrialFixtureEnabled; }
    public void setHistoricalTrialFixtureEnabled(boolean enabled) { historicalTrialFixtureEnabled = enabled; }
    private String streamer = "kiya";
    private String python = "";
    private String adminUsername = "admin";
    private String adminPassword = "";
    private long taskTimeoutMinutes = 180;

    public String getRepoDir() { return repoDir; }
    public void setRepoDir(String repoDir) { this.repoDir = repoDir; }
    public String getRecDir() { return recDir; }
    public void setRecDir(String recDir) { this.recDir = recDir; }
    public boolean isOpsRecoveryEnabled() { return opsRecoveryEnabled; }
    public void setOpsRecoveryEnabled(boolean enabled) { this.opsRecoveryEnabled = enabled; }
    public String getOpsDir() { return opsDir; }
    public void setOpsDir(String opsDir) { this.opsDir = opsDir; }
    public String getAcceptanceDir() { return acceptanceDir; }
    public void setAcceptanceDir(String acceptanceDir) { this.acceptanceDir = acceptanceDir; }
    public String getStreamer() { return streamer; }
    public void setStreamer(String streamer) { this.streamer = streamer; }
    public String getPython() { return python; }
    public void setPython(String python) { this.python = python; }
    public String getAdminUsername() { return adminUsername; }
    public void setAdminUsername(String adminUsername) { this.adminUsername = adminUsername; }
    public String getAdminPassword() { return adminPassword; }
    public void setAdminPassword(String adminPassword) { this.adminPassword = adminPassword; }
    public long getTaskTimeoutMinutes() { return taskTimeoutMinutes; }
    public void setTaskTimeoutMinutes(long taskTimeoutMinutes) { this.taskTimeoutMinutes = taskTimeoutMinutes; }
}
