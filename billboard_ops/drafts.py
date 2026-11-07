"""Messages to clients, written in their language and kept as drafts until a person approves them.

Four kinds: asking for what's missing, confirming a hold, offering alternatives when a request doesn't fit, and
reminding a client that their hold is about to expire. "mixed" (a client who writes in both) gets English then
Arabic. The Arabic wording should be checked by a native speaker on the client's team before go-live.
"""

from datetime import date

from . import settings
from .models import Message, Screen

T = {
    "hello":        ("Hello {name},", "مرحباً {name}،"),
    "hello_anon":   ("Hello,", "مرحباً،"),
    "signoff":      ("Kind regards,\n{company}", "مع أطيب التحيات،\n{company}"),
    "clarify":      ("Thank you for your request. So that we can check availability and hold the screens for you, "
                     "could you tell us:",
                     "شكراً لطلبكم. حتى نتمكن من التحقق من التوفر وحجز الشاشات لكم، نرجو تزويدنا بما يلي:"),
    "q_client":     ("Which company or brand the campaign is for?", "اسم الشركة أو العلامة التجارية صاحبة الحملة؟"),
    "q_screens":    ("Which screens or locations you would like?", "ما الشاشات أو المواقع التي ترغبون بها؟"),
    "q_dates":      ("The start and end dates?", "تاريخ البداية وتاريخ النهاية؟"),
    "q_frequency":  ("How often the ad should play (for example, slots in the loop or plays per hour)?",
                     "كم مرة ترغبون بعرض الإعلان (مثلاً عدد الفترات في الدورة أو عدد مرات العرض في الساعة)؟"),
    "held":         ("We have placed a hold for you on:", "قمنا بحجز مبدئي لكم على ما يلي:"),
    "held_until":   ("The hold is kept until {date}. To confirm the booking, please reply to this message with the "
                     "signed proposal or purchase order.",
                     "يبقى الحجز المبدئي قائماً حتى {date}. لتأكيد الحجز، يرجى الرد على هذه الرسالة مع العرض الموقّع أو "
                     "أمر الشراء."),
    "sorry":        ("Unfortunately, {what} is not available for {dates}.",
                     "نعتذر، {what} غير متاح في الفترة {dates}."),
    "offer":        ("We can offer:", "يمكننا أن نقترح:"),
    "alt_dates":    ("The nearest dates that are free: {list}", "أقرب التواريخ المتاحة: {list}"),
    "alt_screens":  ("Other screens free on your dates: {list}", "شاشات أخرى متاحة في التواريخ نفسها: {list}"),
    "offer_end":    ("Reply and we will hold the option that suits you.", "يرجى الرد لنحجز لكم الخيار المناسب."),
    "no_offer":     ("We will let you know as soon as something similar becomes available.",
                     "سنعلمكم فور توفر خيار مشابه."),
    "remind":       ("A reminder that your hold {code} on {screens} expires on {date}. Please reply to confirm it, "
                     "or to ask us to extend it.",
                     "نذكّركم بأن الحجز المبدئي {code} على {screens} ينتهي في {date}. يرجى الرد لتأكيده أو لطلب تمديده."),
    "all_hours":    ("all day", "طوال ساعات التشغيل"),
    "hours":        ("{a}:00 to {b}:00", "من الساعة {a}:00 حتى {b}:00"),
    "whole":        ("the whole screen", "الشاشة كاملة"),
    "pph":          ("{n} plays an hour", "{n} مرة عرض في الساعة"),
    "line":         ("{screen}: {start} to {end}, {hours}, {freq}", "{screen}: من {start} إلى {end}، {hours}، {freq}"),
    "subj_clarify": ("Your screen booking request", "طلب حجز الشاشات"),
    "subj_hold":    ("Hold placed: {code}", "تم الحجز المبدئي: {code}"),
    "subj_offer":   ("Your screen booking request: other options", "طلب حجز الشاشات: خيارات أخرى"),
    "subj_remind":  ("Your hold {code} expires soon", "الحجز المبدئي {code} ينتهي قريباً"),
}


def t(key, lang, **kw):
    en, ar = T[key]
    return (ar if lang == "ar" else en).format(**kw)


def _d(value, lang):
    d = value if isinstance(value, date) else date.fromisoformat(str(value)[:10])
    return d.strftime("%d/%m/%Y") if lang == "ar" else d.strftime("%-d %b %Y")


def _langs(lang):
    return ["en", "ar"] if lang == "mixed" else [lang if lang in ("en", "ar") else "en"]


def _compose(lang, name, company, body_fn):
    parts = []
    for lg in _langs(lang):
        greet = t("hello", lg, name=name) if name else t("hello_anon", lg)
        parts.append("\n\n".join([greet, body_fn(lg), t("signoff", lg, company=company)]))
    return "\n\n――――――――\n\n".join(parts)


def _subject(key, lang, **kw):
    return " / ".join(t(key, lg, **kw) for lg in _langs(lang))


def describe_line(lg, screen_label, start, end, sh, eh, exclusive, pph):
    hours = t("all_hours", lg) if sh in (None, "") else t("hours", lg, a=f"{int(sh):02d}", b=f"{int(eh):02d}")
    freq = t("whole", lg) if exclusive else t("pph", lg, n=f"{float(pph):g}")
    return t("line", lg, screen=screen_label, start=_d(start, lg), end=_d(end, lg), hours=hours, freq=freq)


def clarify(lang, name, company, missing, questions):
    def body(lg):
        asks = [q for q in questions if (lg == "ar") == bool(_has_arabic(q))] or \
               [t(f"q_{m}", lg) for m in missing] or questions
        return t("clarify", lg) + "\n" + "\n".join(f"- {q}" for q in asks)
    return _subject("subj_clarify", lang), _compose(lang, name, company, body)


def hold_confirmation(lang, name, company, booking):
    def body(lg):
        rows = [describe_line(lg, f"{l.screen.name} ({l.screen.code})", l.start_date, l.end_date, l.start_hour,
                              l.end_hour, l.exclusive, l.plays_per_hour) for l in booking.lines]
        until = _d(booking.hold_expires_at, lg) if booking.hold_expires_at else ""
        return "\n".join([t("held", lg)] + [f"- {r}" for r in rows] + ["", t("held_until", lg, date=until)])
    return _subject("subj_hold", lang, code=booking.code), _compose(lang, name, company, body)


def alternatives(lang, name, company, problems):
    """problems: [{screen, start, end, alt_dates: [(a, b)], alt_screens: [labels]}]"""
    def body(lg):
        out = []
        for p in problems:
            out.append(t("sorry", lg, what=p["screen"], dates=f"{_d(p['start'], lg)} – {_d(p['end'], lg)}"))
            offers = []
            if p["alt_dates"]:
                offers.append(t("alt_dates", lg, list="; ".join(f"{_d(a, lg)} – {_d(b, lg)}"
                                                               for a, b in p["alt_dates"])))
            if p["alt_screens"]:
                offers.append(t("alt_screens", lg, list=", ".join(p["alt_screens"])))
            if offers:
                out += [t("offer", lg)] + [f"- {o}" for o in offers]
        has_offer = any(p["alt_dates"] or p["alt_screens"] for p in problems)
        out += ["", t("offer_end", lg) if has_offer else t("no_offer", lg)]
        return "\n".join(out)
    return _subject("subj_offer", lang), _compose(lang, name, company, body)


def hold_reminder(lang, name, company, booking):
    def body(lg):
        screens = ", ".join(l.screen.code for l in booking.lines)
        return t("remind", lg, code=booking.code, screens=screens, date=_d(booking.hold_expires_at, lg))
    return _subject("subj_remind", lang, code=booking.code), _compose(lang, name, company, body)


def _has_arabic(s):
    return any("؀" <= ch <= "ۿ" for ch in s)


def new_draft(session, *, kind, channel, recipient, subject, body, language, proposal=None, booking=None,
              in_reply_to=""):
    m = Message(direction="out", channel=channel, kind=kind, status="draft", recipient=recipient,
                subject=subject, body=body, language=language, proposal=proposal,
                booking_id=booking.id if booking is not None else None, in_reply_to=in_reply_to,
                sender=settings.get(session, "company_name"))
    session.add(m)
    return m


def screen_label(session, screen_id):
    s = session.get(Screen, screen_id)
    return f"{s.name} ({s.code})" if s else str(screen_id)


# ── Phase 3: creative ───────────────────────────────────────────────────────────

T.update({
    "subj_files":    ("Files needed for {code}", "الملفات المطلوبة للحجز {code}"),
    "files_intro":   ("Your booking {code} is confirmed. Please send the ad files by {date}. Each screen needs:",
                      "تم تأكيد حجزكم {code}. يرجى إرسال ملفات الإعلان قبل {date}. متطلبات كل شاشة:"),
    "files_spec":    ("{screens}: {w}x{h} pixels ({orient}), {secs} seconds exactly, {types}{fps}{size}, {sound}",
                      "{screens}: {w}x{h} بكسل ({orient})، مدة {secs} ثانية بالضبط، بصيغة {types}{fps}{size}، {sound}"),
    "landscape":     ("landscape", "أفقي"), "portrait": ("portrait", "عمودي"), "square": ("square", "مربع"),
    "fps":           (", {fps} frames per second", "، {fps} إطار في الثانية"),
    "maxsize":       (", up to {mb} MB", "، بحجم أقصاه {mb} ميغابايت"),
    "no_sound":      ("no sound", "بدون صوت"), "sound_ok": ("sound allowed", "الصوت مسموح"),
    "files_rule":    ("All text must be in Arabic, or in Arabic and English.",
                      "يجب أن تكون جميع النصوص باللغة العربية، أو بالعربية والإنجليزية."),
    "files_upload":  ("Upload the files here: {url}", "يرجى رفع الملفات عبر هذا الرابط: {url}"),
    "files_drive":   ("Or put them in this Google Drive folder: {url}", "أو ضعوها في مجلد Google Drive هذا: {url}"),
    "files_reply":   ("You can also reply to this message with the files attached.",
                      "يمكنكم أيضاً الرد على هذه الرسالة مع إرفاق الملفات."),
    "subj_fix":      ("Changes needed to your ad for {code}", "تعديلات مطلوبة على إعلانكم للحجز {code}"),
    "fix_intro":     ("Thank you for the file {file}. Before it can play, a few things need fixing:",
                      "شكراً على الملف {file}. قبل عرضه، يلزم إجراء بعض التعديلات:"),
    "fix_outro":     ("Please send the corrected file by {date}, the same way as before.",
                      "يرجى إرسال الملف المعدّل قبل {date} بالطريقة نفسها."),
    "subj_files_rem": ("Reminder: files needed for {code}", "تذكير: الملفات المطلوبة للحجز {code}"),
    "files_rem":     ("A reminder that the ad files for {code} are due by {date}, so the campaign can start on {start}.",
                      "نذكّركم بأن ملفات الإعلان للحجز {code} مطلوبة قبل {date}، حتى تبدأ الحملة في {start}."),
})

# How to fix each failed check, in plain words. {m} = what was measured, {r} = what is required.
FIX = {
    "file_type":   ("The file is a {m}. This screen accepts {r}. Please send it in one of those formats.",
                    "صيغة الملف {m}. هذه الشاشة تقبل {r}. يرجى إرسال الملف بإحدى هذه الصيغ."),
    "codec":       ("The video is compressed as {m}. Please export it as {r} (for example, an H.264 MP4).",
                    "الفيديو مضغوط بصيغة {m}. يرجى تصديره بصيغة {r} (مثلاً MP4 بترميز H.264)."),
    "resolution":  ("The file is {m} pixels. This screen needs exactly {r}. Please export it at that size.",
                    "أبعاد الملف {m} بكسل. هذه الشاشة تحتاج إلى {r} بالضبط. يرجى تصديره بهذه الأبعاد."),
    "duration":    ("The video is {m} long. This screen plays {r} ads, so please make it exactly {r}.",
                    "مدة الفيديو {m}. هذه الشاشة تعرض إعلانات مدتها {r}، لذا يرجى جعل مدته {r} بالضبط."),
    "frame_rate":  ("The video runs at {m}. This screen needs {r}.",
                    "معدل الإطارات {m}. هذه الشاشة تحتاج إلى {r}."),
    "file_size":   ("The file is {m}. The largest this screen takes is {r}. Please export a smaller file.",
                    "حجم الملف {m}. الحد الأقصى لهذه الشاشة {r}. يرجى تصدير ملف أصغر."),
    "sound":       ("The video has a sound track, but this screen plays no sound. Please remove the audio.",
                    "يحتوي الفيديو على صوت، لكن هذه الشاشة لا تعرض الصوت. يرجى إزالة الصوت."),
    "black_frames": ("The video has {m}. Please remove it so the ad fills its whole slot.",
                     "يحتوي الفيديو على شاشة سوداء ({m}). يرجى إزالتها ليملأ الإعلان مدته كاملة."),
    "borders":     ("The picture has black bars inside the frame ({m}). Please make it fill the whole frame.",
                    "تظهر أشرطة سوداء داخل الإطار ({m}). يرجى جعل الصورة تملأ الإطار بالكامل."),
    "damaged":     ("The file seems to be damaged and doesn't play all the way through. Please export it and send it "
                    "again.", "يبدو أن الملف تالف ولا يُعرض حتى النهاية. يرجى تصديره وإرساله مرة أخرى."),
    "colour":      ("The image uses CMYK colours, which are for print. Please save it in RGB.",
                    "الصورة بألوان CMYK المخصصة للطباعة. يرجى حفظها بألوان RGB."),
    "language":    ("The ad's text is {m}. Ads on these screens must be in Arabic, or in Arabic and English. "
                    "Please add Arabic text{at}.",
                    "نص الإعلان: {m}. يجب أن تكون الإعلانات على هذه الشاشات بالعربية، أو بالعربية والإنجليزية. "
                    "يرجى إضافة نص عربي{at}."),
    "arabic_rendering": ("The Arabic text looks broken{at}: letters not joined, or running left to right. This "
                         "usually happens when the design software doesn't support Arabic. Please fix it.",
                         "يبدو النص العربي غير سليم{at}: حروف غير متصلة أو مكتوبة من اليسار إلى اليمين. يحدث هذا عادةً "
                         "عندما لا يدعم برنامج التصميم اللغة العربية. يرجى تصحيحه."),
    "spelling":    ("Please check the spelling{at}: {d}", "يرجى مراجعة الإملاء{at}: {d}"),
    "brand":       ("We couldn't see {r}'s brand in this ad. Please check it's the right file for this booking.",
                    "لم نتمكن من رؤية علامة {r} التجارية في هذا الإعلان. يرجى التأكد من أنه الملف الصحيح لهذا الحجز."),
    "safe_area":   ("Some text is cut off or too close to the edge{at}. Please keep text at least {r}.",
                    "بعض النصوص مقطوعة أو قريبة جداً من الحافة{at}. يرجى إبقاء النصوص على بعد {r} على الأقل."),
    "content_rules": ("The ad may not meet our content guidelines: {d}. Please review it.",
                      "قد لا يتوافق الإعلان مع إرشادات المحتوى لدينا: {d}. يرجى مراجعته."),
}


def fix_sentence(lg, c, client_name=""):
    en, ar = FIX.get(c["key"], ("{d}", "{d}"))
    where = ""
    if c.get("at") is not None:
        from .creative_checks import mmss
        where = (f" (at {mmss(c['at'])})" if lg != "ar" else f" (عند {mmss(c['at'])})")
    req = client_name if c["key"] == "brand" else c.get("required", "")
    return (ar if lg == "ar" else en).format(m=c.get("measured", ""), r=req, d=c.get("detail", ""), at=where)


def _orient_word(lg, w, h):
    return t("square" if w == h else ("landscape" if w > h else "portrait"), lg)


def creative_request(lang, name, company, booking, targets, deadline, upload_url, drive_url):
    """targets: [(spec, ad_seconds, [screen codes])]"""
    def body(lg):
        rows = []
        for spec, secs, codes in targets:
            fps = t("fps", lg, fps=f"{spec.fps_max or spec.fps_min:g}") if (spec.fps_min or spec.fps_max) else ""
            size = t("maxsize", lg, mb=f"{spec.max_file_mb:g}") if spec.max_file_mb else ""
            rows.append(t("files_spec", lg, screens=", ".join(codes), w=spec.width_px, h=spec.height_px,
                          orient=_orient_word(lg, spec.width_px, spec.height_px), secs=secs,
                          types="/".join(x.upper() for x in spec.file_types.split(",") if x), fps=fps, size=size,
                          sound=t("sound_ok" if spec.audio_allowed else "no_sound", lg)))
        out = [t("files_intro", lg, code=booking.code, date=_d(deadline, lg))] + [f"- {r}" for r in rows]
        out += ["", t("files_rule", lg), ""]
        if upload_url:
            out.append(t("files_upload", lg, url=upload_url))
        if drive_url:
            out.append(t("files_drive", lg, url=drive_url))
        out.append(t("files_reply", lg))
        return "\n".join(out)
    return _subject("subj_files", lang, code=booking.code), _compose(lang, name, company, body)


def creative_feedback(lang, name, company, booking, filename, failures, deadline):
    def body(lg):
        lines = [t("fix_intro", lg, file=filename)]
        lines += [f"- {fix_sentence(lg, c, booking.client.name)}" for c in failures]
        if deadline:
            lines += ["", t("fix_outro", lg, date=_d(deadline, lg))]
        return "\n".join(lines)
    return _subject("subj_fix", lang, code=booking.code), _compose(lang, name, company, body)


def creative_reminder(lang, name, company, booking, deadline):
    def body(lg):
        return t("files_rem", lg, code=booking.code, date=_d(deadline, lg), start=_d(booking.start_date, lg))
    return _subject("subj_files_rem", lang, code=booking.code), _compose(lang, name, company, body)
