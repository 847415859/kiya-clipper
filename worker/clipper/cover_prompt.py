"""Seedream cover defaults plus user additions; shared by prompt preview and generation."""
MAX_SUPPLEMENT = 1500


def seedream_options(saved=None, supplement=None, reference=None):
    saved = saved if isinstance(saved, dict) else {}
    text = saved.get("supplement", "") if supplement is None else supplement
    ref = saved.get("reference", "frame") if reference is None else reference
    if not isinstance(text, str) or len(text) > MAX_SUPPLEMENT or "\0" in text:
        raise ValueError(f"Seedream 补充要求最多 {MAX_SUPPLEMENT} 字，不能包含空字符")
    if ref not in ("frame", "seedream"):
        raise ValueError("Seedream 参考来源不合法")
    return {"supplement": text.strip(), "reference": ref}


def seedream_prompt(concept, options, headline=""):
    options = seedream_options(options)
    reference = ("参考图是上次生成的封面底图，保留其已完成的人物特征和满意的构图，在此基础上局部完善。"
                 if options["reference"] == "seedream" else
                 "参考图来自直播画面，保持主播身份特征、发型、服装和人物辨识度。")
    sections = ["为直播切片制作中文视频平台封面底图。", reference,
                f"自动主题：{concept['label']}。视觉概念：{concept.get('subline') or concept['headline']}。",
                "默认采用清晰主体、漫画感构图、鲜明色彩和简洁的视觉隐喻；补充要求可调整风格、颜色、道具和布局，避免所有元素堆在一起。"]
    if headline:
        sections.append(f"后期将叠加封面文案「{headline}」，只用于帮助理解主题，不要在图片中绘制这段文字。给画面下方中间保留干净的文字区域，人物面部与关键动作避开该区域。")
    if options["supplement"]:
        sections.append("用户补充要求（在自动方案上完善）：\n" + options["supplement"])
    sections.append("最终要求：保留人物辨识度，不改变人物身份；视觉道具作为封面设计，不冒充素材中真实发生的事实。不要生成文字、字母、数字、水印或 Logo，文案由程序后期叠加。")
    return "\n\n".join(sections)
