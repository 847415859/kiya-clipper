package com.kiya.admin.entity;

import java.time.LocalDateTime;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;

@TableName("live_session")
public class LiveSession {
    @TableId(type = IdType.AUTO)
    private Long id;
    private String streamerId;
    private String name;
    private String startTime;
    private Integer segments;
    private Double durationSeconds;
    private Integer videosPresent;
    private Long videoBytes;
    private Integer danmaku;
    private LocalDateTime scannedAt;

    public Long getId() { return id; }
    public void setId(Long id) { this.id = id; }
    public String getStreamerId() { return streamerId; }
    public void setStreamerId(String streamerId) { this.streamerId = streamerId; }
    public String getName() { return name; }
    public void setName(String name) { this.name = name; }
    public String getStartTime() { return startTime; }
    public void setStartTime(String startTime) { this.startTime = startTime; }
    public Integer getSegments() { return segments; }
    public void setSegments(Integer segments) { this.segments = segments; }
    public Double getDurationSeconds() { return durationSeconds; }
    public void setDurationSeconds(Double durationSeconds) { this.durationSeconds = durationSeconds; }
    public Integer getVideosPresent() { return videosPresent; }
    public void setVideosPresent(Integer videosPresent) { this.videosPresent = videosPresent; }
    public Long getVideoBytes() { return videoBytes; }
    public void setVideoBytes(Long videoBytes) { this.videoBytes = videoBytes; }
    public Integer getDanmaku() { return danmaku; }
    public void setDanmaku(Integer danmaku) { this.danmaku = danmaku; }
    public LocalDateTime getScannedAt() { return scannedAt; }
    public void setScannedAt(LocalDateTime scannedAt) { this.scannedAt = scannedAt; }
}
