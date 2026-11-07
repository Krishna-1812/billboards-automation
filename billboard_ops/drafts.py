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
