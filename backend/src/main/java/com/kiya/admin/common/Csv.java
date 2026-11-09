package com.kiya.admin.common;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** 极简 CSV 读写（RFC 4180：逗号分隔、双引号转义），够读写 worker 生成的表格。 */
public final class Csv {
    private Csv() {}

    public static List<List<String>> parse(String text) {
        if (text.startsWith("﻿")) {
            text = text.substring(1);
        }
        List<List<String>> rows = new ArrayList<>();
        List<String> row = new ArrayList<>();
        StringBuilder cell = new StringBuilder();
        boolean quoted = false;
        for (int i = 0; i < text.length(); i++) {
            char c = text.charAt(i);
            if (quoted) {
                if (c == '"') {
                    if (i + 1 < text.length() && text.charAt(i + 1) == '"') {
                        cell.append('"');
                        i++;
                    } else {
                        quoted = false;
                    }
                } else {
                    cell.append(c);
                }
            } else if (c == '"') {
                quoted = true;
            } else if (c == ',') {
                row.add(cell.toString());
                cell.setLength(0);
            } else if (c == '\n' || c == '\r') {
                if (c == '\r' && i + 1 < text.length() && text.charAt(i + 1) == '\n') {
                    i++;
                }
                row.add(cell.toString());
                cell.setLength(0);
                rows.add(row);
                row = new ArrayList<>();
            } else {
                cell.append(c);
            }
        }
        if (cell.length() > 0 || !row.isEmpty()) {
            row.add(cell.toString());
            rows.add(row);
        }
        rows.removeIf(r -> r.size() == 1 && r.get(0).isEmpty());
        return rows;
    }

    /** 第一行做表头，返回每行的 列名→值。 */
    public static List<Map<String, String>> readMaps(Path file) throws IOException {
        List<List<String>> rows = parse(Files.readString(file, StandardCharsets.UTF_8));
        List<Map<String, String>> out = new ArrayList<>();
        if (rows.isEmpty()) {
            return out;
        }
        List<String> header = rows.get(0);
        for (List<String> r : rows.subList(1, rows.size())) {
            Map<String, String> m = new LinkedHashMap<>();
            for (int i = 0; i < header.size(); i++) {
                m.put(header.get(i), i < r.size() ? r.get(i) : "");
            }
            out.add(m);
        }
        return out;
    }

    /** 写成带 BOM 的 UTF-8（Excel 打开不乱码），先写临时文件再替换，避免写一半被读到。 */
    public static void write(Path file, List<String> header, List<List<String>> rows) throws IOException {
        StringBuilder sb = new StringBuilder("﻿");
        line(sb, header);
        for (List<String> r : rows) {
            line(sb, r);
        }
        Files.createDirectories(file.getParent());
        Path tmp = file.resolveSibling(file.getFileName() + ".tmp");
        Files.writeString(tmp, sb.toString(), StandardCharsets.UTF_8);
        Files.move(tmp, file, java.nio.file.StandardCopyOption.REPLACE_EXISTING,
                java.nio.file.StandardCopyOption.ATOMIC_MOVE);
    }

    private static void line(StringBuilder sb, List<String> cells) {
        for (int i = 0; i < cells.size(); i++) {
            if (i > 0) {
                sb.append(',');
            }
            String v = cells.get(i) == null ? "" : cells.get(i);
            if (v.contains(",") || v.contains("\"") || v.contains("\n") || v.contains("\r")) {
                sb.append('"').append(v.replace("\"", "\"\"")).append('"');
            } else {
                sb.append(v);
            }
        }
        sb.append("\r\n");
    }
}
