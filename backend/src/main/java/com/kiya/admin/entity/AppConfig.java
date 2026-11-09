package com.kiya.admin.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;

@TableName("app_config")
public class AppConfig {
    @TableId(value = "k", type = IdType.INPUT)
    private String k;
    private String v;

    public AppConfig() {}

    public AppConfig(String k, String v) {
        this.k = k;
        this.v = v;
    }

    public String getK() { return k; }
    public void setK(String k) { this.k = k; }
    public String getV() { return v; }
    public void setV(String v) { this.v = v; }
}
