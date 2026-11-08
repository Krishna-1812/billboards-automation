"""English and Arabic.

The layout flips right to left for Arabic. Phase 1 translates the frame the whole team uses (menu, statuses,
buttons, headings); the client portal in Phase 4 is fully bilingual. The Arabic here should be checked by a
native speaker on the client's team before clients see it.
"""

LANGS = ("en", "ar")
RTL = {"ar"}

STRINGS = {
    "dashboard":        ("Dashboard", "لوحة المتابعة"),
    "calendar":         ("Calendar", "التقويم"),
    "proposals":        ("Proposals", "الطلبات"),
    "outbox":           ("Outbox", "الرسائل الصادرة"),
    "creative":         ("Creative", "التصاميم"),
    "playlists":        ("Playlists", "قوائم العرض"),
    "availability":     ("Check vacancy", "التحقق من التوفر"),
    "bookings":         ("Bookings", "الحجوزات"),
    "clients":          ("Clients", "العملاء"),
    "screens":          ("Screens", "الشاشات"),
    "specs":            ("Specs", "المواصفات"),
    "imports":          ("Import", "الاستيراد"),
    "activity":         ("Activity", "سجل النشاط"),
    "settings":         ("Settings", "الإعدادات"),
    "sign_out":         ("Sign out", "تسجيل الخروج"),
    "sign_in":          ("Sign in", "تسجيل الدخول"),
    "notifications":    ("Notices", "التنبيهات"),
    "save":             ("Save", "حفظ"),
    "cancel":           ("Cancel", "إلغاء"),
    "search":           ("Search", "بحث"),
    "new":              ("New", "جديد"),
    "edit":             ("Edit", "تعديل"),
    "place_hold":       ("Place hold", "حجز مبدئي"),
    "confirm":          ("Confirm booking", "تأكيد الحجز"),
    "extend_hold":      ("Extend hold", "تمديد الحجز المبدئي"),
    "check":            ("Check", "تحقق"),
    "fits":             ("Fits", "متاح"),
    "partial":          ("Fits in part", "متاح جزئياً"),
    "no":               ("Doesn't fit", "غير متاح"),
    "language":         ("العربية", "English"),
    "status_enquiry":   ("Enquiry", "استفسار"),
    "status_hold":      ("Hold", "حجز مبدئي"),
    "status_confirmed": ("Confirmed", "مؤكد"),
    "status_awaiting_creative": ("Awaiting creative", "بانتظار التصاميم"),
    "status_checking":  ("Checking", "قيد الفحص"),
    "status_changes_requested": ("Changes requested", "مطلوب تعديلات"),
    "status_creative_approved": ("Creative approved", "تمت الموافقة على التصميم"),
    "status_plan_set":  ("Play plan set", "تم تحديد خطة العرض"),
    "status_scheduled": ("Scheduled", "مجدول"),
    "status_live":      ("Live", "قيد العرض"),
    "status_completed": ("Completed", "مكتمل"),
    "status_expired":   ("Expired", "منتهي"),
    "status_cancelled": ("Cancelled", "ملغى"),
}


def t(key, lang="en"):
    pair = STRINGS.get(key)
    if pair is None:
        return key
    return pair[1] if lang == "ar" else pair[0]


def direction(lang):
    return "rtl" if lang in RTL else "ltr"
