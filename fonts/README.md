# 字幕字体

`config/subtitles.yaml` 里的样式用到的字体，系统没装的放这个目录（.ttf / .otf），cut.py 烧录字幕时会自动加载。
字体文件不进 git，每台机器各自下载。只用开源、可商用的字体（发布到 B 站属于商用）。

| 样式 | 字体 | 获取方式 |
|---|---|---|
| default / box / karaoke | Noto Sans CJK SC（思源黑体） | `apt install -y fonts-noto-cjk`（装到系统，不用放这里） |
| kuaile | ZCOOL KuaiLe（站酷快乐体，OFL 协议） | 见下 |

```bash
cd /opt/kiya-clipper/fonts
wget -O ZCOOLKuaiLe-Regular.ttf https://raw.githubusercontent.com/google/fonts/main/ofl/zcoolkuaile/ZCOOLKuaiLe-Regular.ttf
```

GitHub 下载慢的话：在电脑上打开 https://fonts.google.com/specimen/ZCOOL+KuaiLe 下载，再用 `rz` 传到这个目录。

想加新字体：放进来 → 运行 `python worker/scripts/subtitle.py styles` 确认识别到了 → 在 config/subtitles.yaml 里写字体族名。
