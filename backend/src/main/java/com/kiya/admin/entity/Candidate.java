package com.kiya.admin.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;

/** 候选片段。topDanmaku / speech / contrib 是 JSON 字符串。rnk 即 worker 里的 rank（MySQL 8 里 rank 是保留字）。 */
@TableName("candidate")
public class Candidate {
    @TableId(type = IdType.AUTO)
    private Long id;
    private String sessionName;
    private Integer rnk;
    private Double t;
    private Double score;
    private Double clipStart;
    private Double clipEnd;
    private Double danmakuPerMin;
    private Integer emotionHits;
    private Double giftValue;
    private String topDanmaku;
    private String speech;
    private String contrib;
    private String mark;
    private String note;
    private String clipFile;

    public Long getId() { return id; }
    public void setId(Long id) { this.id = id; }
    public String getSessionName() { return sessionName; }
    public void setSessionName(String sessionName) { this.sessionName = sessionName; }
    public Integer getRnk() { return rnk; }
    public void setRnk(Integer rnk) { this.rnk = rnk; }
    public Double getT() { return t; }
    public void setT(Double t) { this.t = t; }
    public Double getScore() { return score; }
    public void setScore(Double score) { this.score = score; }
    public Double getClipStart() { return clipStart; }
    public void setClipStart(Double clipStart) { this.clipStart = clipStart; }
    public Double getClipEnd() { return clipEnd; }
    public void setClipEnd(Double clipEnd) { this.clipEnd = clipEnd; }
    public Double getDanmakuPerMin() { return danmakuPerMin; }
    public void setDanmakuPerMin(Double danmakuPerMin) { this.danmakuPerMin = danmakuPerMin; }
    public Integer getEmotionHits() { return emotionHits; }
    public void setEmotionHits(Integer emotionHits) { this.emotionHits = emotionHits; }
    public Double getGiftValue() { return giftValue; }
    public void setGiftValue(Double giftValue) { this.giftValue = giftValue; }
    public String getTopDanmaku() { return topDanmaku; }
    public void setTopDanmaku(String topDanmaku) { this.topDanmaku = topDanmaku; }
    public String getSpeech() { return speech; }
    public void setSpeech(String speech) { this.speech = speech; }
    public String getContrib() { return contrib; }
    public void setContrib(String contrib) { this.contrib = contrib; }
    public String getMark() { return mark; }
    public void setMark(String mark) { this.mark = mark; }
    public String getNote() { return note; }
    public void setNote(String note) { this.note = note; }
    public String getClipFile() { return clipFile; }
    public void setClipFile(String clipFile) { this.clipFile = clipFile; }
}
