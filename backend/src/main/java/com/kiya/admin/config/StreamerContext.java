package com.kiya.admin.config;

/** 当前请求管理的主播标识（ThreadLocal）。由 StreamerFilter 在请求开始从 session 读入，请求结束清除。 */
public final class StreamerContext {
    private static final ThreadLocal<String> CURRENT = new ThreadLocal<>();

    private StreamerContext() {
    }

    public static void set(String streamer) {
        CURRENT.set(streamer);
    }

    public static String get() {
        return CURRENT.get();
    }

    public static void clear() {
        CURRENT.remove();
    }
}
