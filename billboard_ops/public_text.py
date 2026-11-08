"""The words on the public pages (the front page, becoming a client, signing in), in English and Arabic."""

P = {
    # The frame
    "nav_network":     ("Our screens", "شاشاتنا"),
    "nav_how":         ("How it works", "كيف نعمل"),
    "nav_portal":      ("Client portal", "بوابة العملاء"),
    "sign_in":         ("Sign in", "تسجيل الدخول"),
    "become_client":   ("Become a client", "كن عميلاً"),
    "open_app":        ("Open the app", "افتح التطبيق"),
    "open_portal":     ("Open my portal", "افتح بوابتي"),
    "other_lang":      ("العربية", "English"),
    "skip":            ("Skip to the content", "انتقل إلى المحتوى"),

    # The front page
    "eyebrow":         ("Digital out-of-home · Dubai", "إعلانات رقمية خارجية · دبي"),
    "hero_1":          ("Your brand,", "علامتك التجارية،"),
    "hero_2":          ("lit up on the", "مضاءة على"),
    "hero_3":          ("city's biggest screens.", "أكبر شاشات المدينة."),
    "hero_sub":        ("{company} runs digital billboards across Dubai. Tell us your campaign and budget; we send you options "
                        "with live availability, and your ad is on air in days, not weeks.",
                        "تدير {company} لوحات إعلانية رقمية في أنحاء دبي. أخبرنا بحملتك وميزانيتك، وسنرسل لك خيارات "
                        "بحسب التوفر الفعلي، ليظهر إعلانك خلال أيام لا أسابيع."),
    "cta_start":       ("Plan a campaign", "خطط لحملة"),
    "cta_signin":      ("I already have an account", "لدي حساب بالفعل"),
    "now_playing":     ("Now playing", "يُعرض الآن"),
    "slot_of":         ("Slot {n} of {total}", "الفترة {n} من {total}"),
    "loop_s":          ("{s}-second loop", "دورة {s} ثانية"),
    "ads":             (["Your brand here", "Grand opening", "Summer sale −30%", "New collection", "Book now"],
                        ["علامتك هنا", "الافتتاح الكبير", "تخفيضات الصيف ‎−30%", "تشكيلة جديدة", "احجز الآن"]),
    "ads_sub":         (["Seen by the whole city", "This Friday · 7 pm", "Online and in store", "Out now", "Limited dates"],
                        ["تراه المدينة بأكملها", "هذا الجمعة · ٧ مساءً", "عبر الإنترنت وفي المتاجر", "متوفرة الآن", "مواعيد محدودة"]),

    "marquee":         (["Live availability", "Options for every budget", "Proposals with a PDF", "Purchase orders tracked",
                         "Artwork checked before it airs", "Proof of play", "English and Arabic"],
                        ["توفر فوري", "خيارات لكل ميزانية", "عروض مع ملف PDF", "متابعة أوامر الشراء",
                         "فحص التصاميم قبل العرض", "إثبات العرض", "العربية والإنجليزية"]),

    "stat_screens":    ("screens", "شاشة"),
    "stat_areas":      ("districts", "منطقة"),
    "stat_plays":      ("plays a day, every screen", "عرض يومياً على كل شاشة"),
    "stat_loop":       ("second loop", "ثانية في الدورة"),
    "stat_hours":      ("hours on air a day", "ساعة بث يومياً"),

    "loop_eyebrow":    ("How a screen sells time", "كيف تُباع أوقات الشاشة"),
    "loop_title":      ("One loop. Your slot. All day long.", "دورة واحدة. فترتك. طوال اليوم."),
    "loop_body":       ("Each screen plays a {loop}-second loop split into {slots} slots of {slot} seconds. "
                        "Book one slot and your ad plays once every loop: {per_hour} times an hour, "
                        "{per_day} times a day. Want more? Take more slots, choose your hours, or the whole screen.",
                        "تعرض كل شاشة دورة مدتها {loop} ثانية مقسمة إلى {slots} فترات مدة كل منها {slot} ثوانٍ. "
                        "احجز فترة واحدة ليظهر إعلانك مرة في كل دورة: {per_hour} مرة في الساعة، "
                        "و{per_day} مرة في اليوم. تريد المزيد؟ احجز فترات أكثر، أو اختر ساعاتك، أو الشاشة كاملة."),
    "loop_you":        ("Your ad", "إعلانك"),
    "loop_others":     ("Other brands", "علامات أخرى"),
    "per_hour":        ("an hour", "في الساعة"),
    "per_day":         ("a day", "في اليوم"),

    "about_kicker":    ("About {company}", "عن {company}"),
    "about_default":   ("We put brands where Dubai looks: big, bright digital screens on the roads and in the districts "
                        "people pass every day. One team handles everything, from your first brief to the proof that "
                        "your ad played.",
                        "نضع العلامات التجارية حيث تنظر دبي: شاشات رقمية كبيرة ومضيئة على الطرق وفي المناطق التي "
                        "يمر بها الناس كل يوم. فريق واحد يتولى كل شيء، من فكرتك الأولى إلى إثبات عرض إعلانك."),
    "about_lead":      ("Live availability for every screen, honest options at every budget, and a portal where you "
                        "follow your campaign from proposal to proof of play.",
                        "توفر فوري لكل شاشة، وخيارات واضحة لكل ميزانية، وبوابة تتابع فيها حملتك من العرض "
                        "حتى إثبات العرض."),
    "chip_1":          ("One slot", "فترة واحدة"),
    "chip_2":          ("Two slots", "فترتان"),
    "chip_all":        ("The whole screen", "الشاشة كاملة"),
    "mock_option":     ("Option", "الخيار"),
    "mock_accept":     ("Accept", "قبول"),
    "mock_played":     ("▶ Played 18:42", "▶ عُرض ‎18:42"),
    "net_eyebrow":     ("The network", "الشبكة"),
    "net_title":       ("Screens where the city looks.", "شاشات حيث تنظر المدينة."),
    "net_body":        ("Every screen, its size and its hours. Prices depend on dates, hours and how often you play, "
                        "so we send them in your proposal.",
                        "كل شاشة بحجمها وساعات عملها. تعتمد الأسعار على التواريخ والساعات وعدد مرات العرض، "
                        "لذلك نرسلها في عرضك."),
    "net_more":        ("and {n} more screens", "و{n} شاشة أخرى"),
    "hours_all":       ("On air 24 hours", "تعمل ٢٤ ساعة"),
    "hours_range":     ("On air {a}:00–{b}:00", "تعمل {a}:00–{b}:00"),
    "loop_tag":        ("{s}s loop", "دورة {s} ث"),

    "how_eyebrow":     ("From brief to billboard", "من الفكرة إلى اللوحة"),
    "how_title":       ("Four steps. No guesswork.", "أربع خطوات. بلا تخمين."),
    "how": ([("Tell us the campaign", "Your dates, the areas you want, your budget. A few lines is enough."),
             ("Get your options", "A proposal with options that fit your budget, checked against live availability."),
             ("Send your purchase order", "Agree the price, send the PO, and we confirm your booking."),
             ("Go live", "Upload your artwork. We check it fits the screen, load it and show you proof it played.")],
            [("أخبرنا عن الحملة", "التواريخ والمناطق التي تريدها وميزانيتك. بضعة أسطر تكفي."),
             ("استلم خياراتك", "عرض بخيارات تناسب ميزانيتك، بعد التحقق من التوفر الفعلي."),
             ("أرسل أمر الشراء", "اتفق على السعر وأرسل أمر الشراء، ونؤكد حجزك."),
             ("انطلق", "ارفع تصميمك. نتحقق من ملاءمته للشاشة ونحمّله ونريك إثبات العرض.")]),

    "portal_eyebrow":  ("Your client portal", "بوابة العملاء"),
    "portal_title":    ("Your campaigns, in one place.", "حملاتك في مكان واحد."),
    "portal_body":     ("Sign in with just your email: no password to remember. See every booking, answer proposals, "
                        "send files and approve previews.",
                        "سجّل الدخول ببريدك الإلكتروني فقط، دون كلمة مرور. اطّلع على كل حجز، وردّ على العروض، "
                        "وأرسل الملفات ووافق على المعاينات."),
    "tile_proposals":  ("Compare options side by side", "قارن الخيارات جنباً إلى جنب"),
    "tile_proposals_s":("Accept the one you want with a click.", "اقبل ما تريده بنقرة واحدة."),
    "tile_files":      ("Files checked before air", "فحص الملفات قبل العرض"),
    "tile_files_s":    ("Size, length and format, checked the moment you upload.", "المقاس والمدة والصيغة، تُفحص لحظة الرفع."),
    "tile_proof":      ("Proof it played", "إثبات العرض"),
    "tile_proof_s":    ("Photos and play logs for every campaign.", "صور وسجلات عرض لكل حملة."),
    "tile_lang":       ("English and العربية", "العربية وEnglish"),
    "tile_lang_s":     ("Every page and every message, in your language.", "كل صفحة وكل رسالة بلغتك."),

    "final_title":     ("Ready to light up the city?", "مستعد لإضاءة المدينة؟"),
    "final_body":      ("Tell us about your campaign. We reply within one working day with options and prices.",
                        "أخبرنا عن حملتك. نرد خلال يوم عمل واحد بخيارات وأسعار."),
    "footer_team":     ("Team sign-in", "دخول الفريق"),
    "rights":          ("All rights reserved.", "جميع الحقوق محفوظة."),

    # Becoming a client
    "su_title":        ("Plan your campaign", "خطط لحملتك"),
    "su_intro":        ("Tell us what you have in mind. We reply within one working day with options, prices and "
                        "availability, and set up your client portal.",
                        "أخبرنا بما تفكر فيه. نرد خلال يوم عمل واحد بالخيارات والأسعار والتوفر، "
                        "ونجهّز لك بوابة العملاء."),
    "su_you":          ("About you", "عنك"),
    "su_campaign":     ("The campaign", "الحملة"),
    "su_name":         ("Your name", "اسمك"),
    "su_company":      ("Company or brand", "الشركة أو العلامة التجارية"),
    "su_email":        ("Work email", "البريد الإلكتروني للعمل"),
    "su_phone":        ("Phone or WhatsApp", "الهاتف أو واتساب"),
    "su_optional":     ("optional", "اختياري"),
    "su_kind":         ("You are", "أنت"),
    "su_kind_brand":   ("A brand", "علامة تجارية"),
    "su_kind_agency":  ("An agency", "وكالة"),
    "su_start":        ("From", "من"),
    "su_end":          ("To", "إلى"),
    "su_where":        ("Screens or areas", "الشاشات أو المناطق"),
    "su_where_ph":     ("Any, or name a road or district", "أي منها، أو اذكر شارعاً أو منطقة"),
    "su_budget":       ("Budget", "الميزانية"),
    "su_budget_opts":  (["Not sure yet", "Under 25,000", "25,000 – 100,000", "100,000 – 250,000", "Over 250,000"],
                        ["لست متأكداً بعد", "أقل من 25,000", "25,000 – 100,000", "100,000 – 250,000", "أكثر من 250,000"]),
    "su_notes":        ("Anything else", "أي شيء آخر"),
    "su_notes_ph":     ("What you're promoting, how often you'd like to play, a launch date…",
                        "ما الذي تروّج له، وكم مرة تريد العرض، وموعد الإطلاق…"),
    "su_send":         ("Send my request", "أرسل طلبي"),
    "su_privacy":      ("We use these details only to reply to you about this campaign.",
                        "نستخدم هذه البيانات فقط للرد عليك بخصوص هذه الحملة."),
    "su_have":         ("Already a client?", "عميل بالفعل؟"),
    "su_preview":      ("Live preview", "معاينة مباشرة"),
    "su_your_brand":   ("Your brand", "علامتك"),
    "su_on_air":       ("On air across Dubai", "على الهواء في أنحاء دبي"),
    "su_need":         ("Add your name, company and a work email.", "أضف اسمك والشركة وبريداً إلكترونياً للعمل."),
    "su_bad_email":    ("That email address doesn't look right.", "عنوان البريد الإلكتروني غير صحيح."),
    "su_bad_dates":    ("The end date is before the start date.", "تاريخ النهاية قبل تاريخ البداية."),
    "su_done_title":   ("Thank you, {name}.", "شكراً لك، {name}."),
    "su_done_body":    ("Your request {ref} is with our team. We'll reply to {email} within one working day with options "
                        "and prices, and an invitation to your client portal.",
                        "طلبك {ref} لدى فريقنا. سنرد على {email} خلال يوم عمل واحد بالخيارات والأسعار، "
                        "ودعوة إلى بوابة العملاء."),
    "su_done_next":    ("What happens next", "ما الخطوة التالية"),
    "su_back":         ("Back to the front page", "العودة إلى الصفحة الرئيسية"),

    # Signing in
    "in_title":        ("Welcome back", "مرحباً بعودتك"),
    "in_sub":          ("Choose how you work with us.", "اختر طريقة عملك معنا."),
    "in_client":       ("I'm a client", "أنا عميل"),
    "in_team":         ("I'm on the team", "أنا من الفريق"),
    "in_client_body":  ("We'll email you a sign-in link. No password needed.",
                        "سنرسل إليك رابط تسجيل الدخول بالبريد. لا حاجة لكلمة مرور."),
    "in_email":        ("Your email", "بريدك الإلكتروني"),
    "in_send_link":    ("Email me a sign-in link", "أرسل لي رابط الدخول"),
    "in_team_body":    ("Sign in with your work Google account.", "سجّل الدخول بحساب Google الخاص بالعمل."),
    "in_dev":          ("Email (local development only)", "البريد (للتطوير المحلي فقط)"),
    "in_not_set":      ("Team sign-in isn't switched on yet. The owner sets GOOGLE_CLIENT_ID to switch it on.",
                        "تسجيل دخول الفريق غير مفعّل بعد. يفعّله المالك بإضافة GOOGLE_CLIENT_ID."),
    "in_open":         ("Sign-in is switched off while the app is being set up, so anyone with the address can open it.",
                        "تسجيل الدخول متوقف أثناء إعداد التطبيق، لذا يمكن لأي شخص لديه العنوان فتحه."),
    "in_new":          ("New here?", "جديد هنا؟"),
    "in_side_title":   ("Every screen. Every slot. Every play.", "كل شاشة. كل فترة. كل عرض."),
    "in_side_body":    ("Bookings, proposals, artwork and proof of play for {company}, in one place.",
                        "الحجوزات والعروض والتصاميم وإثبات العرض لـ{company}، في مكان واحد."),
}


def tw(key, lang="en", **kw):
    pair = P.get(key)
    if pair is None:
        return key
    text = pair[1] if lang == "ar" else pair[0]
    return text.format(**kw) if kw and isinstance(text, str) else text
