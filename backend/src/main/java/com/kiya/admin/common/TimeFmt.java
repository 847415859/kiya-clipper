package com.kiya.admin.common;

public final class TimeFmt {
    private TimeFmt() {}

    /** 秒 → HH:MM:SS（与 worker 的 hhmmss 一致：四舍五入到整秒）。 */
    public static String hhmmss(double sec) {
        long s = Math.max(0, Math.round(sec));
        return String.format("%02d:%02d:%02d", s / 3600, s % 3600 / 60, s % 60);
    }
}
