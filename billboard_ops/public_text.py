"""The words on the public pages (the front page, becoming a client, signing in), in English and Arabic.

Strings with <em> mark the words set in italic in headlines; templates render those with |safe (they're ours).
"""

P = {
    # The frame
    "nav_network":     ("Network", "الشبكة"),
    "nav_how":         ("How it works", "كيف نعمل"),
    "nav_portal":      ("For clients", "للعملاء"),
    "sign_in":         ("Sign in", "تسجيل الدخول"),
    "cta":             ("Plan a campaign", "خطّط لحملتك"),
    "become_client":   ("Become a client", "كن عميلاً"),
    "open_app":        ("Open the app", "افتح التطبيق"),
    "open_portal":     ("Open my portal", "افتح بوابتي"),
    "other_lang":      ("العربية", "English"),
    "skip":            ("Skip to the content", "انتقل إلى المحتوى"),
    "dubai":           ("Dubai", "دبي"),
    "gst":             ("GST", "بتوقيت الخليج"),

    # The front page: the opening
    "hero_1":          ("Seen by Dubai,", "تراك دبي،"),
    "hero_2":          ("<em>{slot} seconds</em> at a time.", "<em>{slot} ثوانٍ</em> في كل مرة."),
    "hero_sub":        ("{company} runs digital screens on the roads and in the districts Dubai passes every day. "
                        "Tell us what you're launching and we'll come back with options that fit your dates and "
                        "budget, checked against live availability.",
                        "تدير {company} شاشات رقمية على الطرق وفي المناطق التي تمر بها دبي كل يوم. "
                        "أخبرنا بما تطلقه، وسنعود إليك بخيارات تناسب تواريخك وميزانيتك، "
                        "بعد التحقق من التوفر الفعلي."),
    "meta_network":    ("Network", "الشبكة"),
    "meta_screens":    ("{n} screens", "{n} شاشات"),
    "meta_coverage":   ("Coverage", "التغطية"),
    "meta_districts":  ("{n} districts", "{n} مناطق"),
    "meta_loop":       ("Loop", "الدورة"),
    "meta_loop_v":     ("{loop} s · {slots} slots", "{loop} ث · {slots} فترة"),
    "meta_reply":      ("Reply", "الرد"),
    "meta_reply_v":    ("Within one working day", "خلال يوم عمل واحد"),
    "now_showing":     ("Now showing", "يُعرض الآن"),
    "slot_of":         ("Slot {n} / {total}", "الفترة {n} / {total}"),
    "scroll":          ("Scroll", "مرّر"),
    # The posters on the big screen: (small line, headline, foot). Fictional advertisers.
    "posters": ([("Space available", "Your name, <em>here.</em>", "From one slot, one week"),
                 ("Maison Sable", "Summer, <em>slowly.</em>", "The new collection · In store 12 June"),
                 ("Nocturne", "OPEN LATE", "Every night until 2 am · Al Wasl"),
                 ("Atelier 9", "New <em>season.</em>", "Collection 02"),
                 ("Halo Mobile", "Launch day.", "09.01 · 10:00 · Online and in store")],
                [("مساحة متاحة", "اسمك <em>هنا.</em>", "من فترة واحدة لأسبوع واحد"),
                 ("ميزون سابل", "صيفٌ <em>بلا عجلة.</em>", "التشكيلة الجديدة · في المتاجر ١٢ يونيو"),
                 ("نوكتورن", "مفتوح حتى وقت متأخر", "كل ليلة حتى ٢ صباحاً · الوصل"),
                 ("أتيليه ٩", "موسم <em>جديد.</em>", "التشكيلة ٠٢"),
                 ("هالو موبايل", "يوم الإطلاق.", "٠٩.٠١ · ١٠:٠٠ · عبر الإنترنت وفي المتاجر")]),

    # In numbers
    "num_kicker":      ("In numbers", "بالأرقام"),
    "stat_screens":    ("Screens", "شاشة"),
    "stat_areas":      ("Districts", "منطقة"),
    "stat_loop":       ("Second loop", "ثانية في الدورة"),
    "stat_plays":      ("Plays a day, one slot", "عرض يومياً لفترة واحدة"),
    "stat_hours":      ("Hours on air, daily", "ساعة بث يومياً"),

    # About
    "about_kicker":    ("About", "من نحن"),
    "about_default":   ("We put brands where Dubai looks: large digital screens on the roads and in the districts "
                        "people pass every day. One team handles everything, from the first brief to the proof "
                        "that your ad played.",
                        "نضع العلامات التجارية حيث تنظر دبي: شاشات رقمية كبيرة على الطرق وفي المناطق التي "
                        "يمر بها الناس كل يوم. فريق واحد يتولى كل شيء، من الفكرة الأولى إلى إثبات عرض إعلانك."),
    "about_lead":      ("Live availability for every screen, clear options at every budget, and a portal where you "
                        "follow your campaign from proposal to proof of play.",
                        "توفر فوري لكل شاشة، وخيارات واضحة لكل ميزانية، وبوابة تتابع فيها حملتك من العرض "
                        "حتى إثبات العرض."),

    # The loop
    "loop_kicker":     ("How screen time is sold", "كيف يُباع وقت الشاشة"),
    "loop_title":      ("Every screen runs a loop. <em>You buy a place in it.</em>",
                        "كل شاشة تعرض دورة. <em>وأنت تحجز مكانك فيها.</em>"),
    "loop_body":       ("Each screen plays a {loop}-second loop of {slots} slots, {slot} seconds each. One slot "
                        "puts your ad on screen once every loop: {per_hour} times an hour, {per_day} times a day. "
                        "Take more slots for more plays, choose the hours you want, or take the whole screen.",
                        "تعرض كل شاشة دورة مدتها {loop} ثانية من {slots} فترة، مدة كل منها {slot} ثوانٍ. "
                        "الفترة الواحدة تعرض إعلانك مرة في كل دورة: {per_hour} مرة في الساعة، و{per_day} مرة في اليوم. "
                        "احجز فترات أكثر لعروض أكثر، أو اختر الساعات التي تريدها، أو الشاشة كاملة."),
    "chip_1":          ("One slot", "فترة واحدة"),
    "chip_2":          ("Two slots", "فترتان"),
    "chip_all":        ("Whole screen", "الشاشة كاملة"),
    "per_hour":        ("Plays an hour", "عرض في الساعة"),
    "per_day":         ("Plays a day", "عرض في اليوم"),
    "loop_you":        ("Your slot", "فترتك"),

    # The network
    "net_kicker":      ("The network", "الشبكة"),
    "net_title":       ("Where the city <em>looks.</em>", "حيث تنظر <em>المدينة.</em>"),
    "net_body":        ("Every screen, its size and its hours. Prices depend on dates, hours and how often you play, "
                        "so they come with your proposal.",
                        "كل شاشة بحجمها وساعات عملها. تعتمد الأسعار على التواريخ والساعات وعدد مرات العرض، "
                        "لذلك تأتي مع عرضك."),
    "col_screen":      ("Screen", "الشاشة"),
    "col_where":       ("Location", "الموقع"),
    "col_size":        ("Size", "المقاس"),
    "col_res":         ("Resolution", "الدقة"),
    "col_hours":       ("On air", "ساعات العرض"),
    "net_more":        ("And {n} more screens.", "و{n} شاشة أخرى."),
    "hours_all":       ("24 hours", "٢٤ ساعة"),
    "hours_range":     ("{a}:00–{b}:00", "{a}:00–{b}:00"),

    # Process
    "how_kicker":      ("Process", "طريقة العمل"),
    "how_title":       ("From brief <em>to broadcast.</em>", "من الفكرة <em>إلى الشاشة.</em>"),
    "how": ([("Tell us the campaign", "Your dates, the areas you want and your budget. A few lines is enough."),
             ("Get your options", "A proposal with options that fit your budget, checked against live availability."),
             ("Send your purchase order", "Agree the price, send the PO, and we confirm the booking."),
             ("Go live", "Send your artwork. We check it fits the screen, load it, and show you proof it played.")],
            [("أخبرنا عن الحملة", "التواريخ والمناطق التي تريدها وميزانيتك. بضعة أسطر تكفي."),
             ("استلم خياراتك", "عرض بخيارات تناسب ميزانيتك، بعد التحقق من التوفر الفعلي."),
             ("أرسل أمر الشراء", "اتفق على السعر وأرسل أمر الشراء، ونؤكد الحجز."),
             ("انطلق", "أرسل تصميمك. نتحقق من ملاءمته للشاشة ونحمّله ونريك إثبات العرض.")]),

    # For clients
    "portal_kicker":   ("For clients", "للعملاء"),
    "portal_title":    ("Your campaign, <em>in one place.</em>", "حملتك، <em>في مكان واحد.</em>"),
    "portal_body":     ("Sign in with your email, no password to remember. Compare and accept proposals, send your "
                        "purchase order and artwork, approve previews, and see proof that every play went out.",
                        "سجّل الدخول ببريدك الإلكتروني دون كلمة مرور. قارن العروض واقبلها، وأرسل أمر الشراء "
                        "والتصاميم، ووافق على المعاينات، واطّلع على إثبات كل عرض."),
    "features": ([("Proposals", "Options side by side. Accept one in a click."),
                  ("Files", "Size, length and format checked the moment you upload."),
                  ("Proof", "Photos and play logs for every campaign."),
                  ("Language", "Every page and message in English or Arabic.")],
                 [("العروض", "خيارات جنباً إلى جنب. اقبل أحدها بنقرة."),
                  ("الملفات", "المقاس والمدة والصيغة تُفحص لحظة الرفع."),
                  ("الإثبات", "صور وسجلات عرض لكل حملة."),
                  ("اللغة", "كل صفحة ورسالة بالعربية أو الإنجليزية.")]),
    "mock_title":      ("Proposal for Maison Sable", "عرض لميزون سابل"),
    "mock_version":    ("Version 2 · sent today", "النسخة ٢ · أُرسلت اليوم"),
    "mock_option":     ("Option", "الخيار"),
    "mock_screens":    ("Screens", "الشاشات"),
    "mock_weeks":      ("Weeks", "الأسابيع"),
    "mock_plays":      ("Plays", "العروض"),
    "mock_accept":     ("Accept", "قبول"),
    "mock_chosen":     ("Chosen", "تم الاختيار"),
    "mock_steps":      (["Proposal", "Purchase order", "Artwork", "On air"], ["العرض", "أمر الشراء", "التصميم", "على الشاشة"]),

    # The close
    "final_title":     ("Put your brand <em>on the skyline.</em>", "ضع علامتك <em>في أفق المدينة.</em>"),
    "final_body":      ("Tell us about your campaign. We reply within one working day with options, availability "
                        "and prices.",
                        "أخبرنا عن حملتك. نرد خلال يوم عمل واحد بالخيارات والتوفر والأسعار."),
    "contact":         ("Contact", "تواصل معنا"),
    "footer_team":     ("Team sign-in", "دخول الفريق"),
    "rights":          ("All rights reserved.", "جميع الحقوق محفوظة."),
    "coords":          ("25.2048° N, 55.2708° E", "25.2048° شمالاً، 55.2708° شرقاً"),

    # Becoming a client
    "su_kicker":       ("New campaign", "حملة جديدة"),
    "su_title":        ("Tell us about <em>your campaign.</em>", "أخبرنا عن <em>حملتك.</em>"),
    "su_intro":        ("We reply within one working day with options, prices and availability, and set up your "
                        "client portal.",
                        "نرد خلال يوم عمل واحد بالخيارات والأسعار والتوفر، ونجهّز لك بوابة العملاء."),
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
    "su_notes_ph":     ("What you're promoting, how often you'd like to play, a launch date",
                        "ما الذي تروّج له، وكم مرة تريد العرض، وموعد الإطلاق"),
    "su_send":         ("Send request", "أرسل الطلب"),
    "su_privacy":      ("We use these details only to reply to you about this campaign.",
                        "نستخدم هذه البيانات فقط للرد عليك بخصوص هذه الحملة."),
    "su_have":         ("Already a client?", "عميل بالفعل؟"),
    "su_preview":      ("Preview", "معاينة"),
    "su_your_brand":   ("Your brand", "علامتك"),
    "su_on_air":       ("On air across Dubai", "على الشاشات في أنحاء دبي"),
    "su_need":         ("Add your name, company and a work email.", "أضف اسمك والشركة وبريداً إلكترونياً للعمل."),
    "su_bad_email":    ("That email address doesn't look right.", "عنوان البريد الإلكتروني غير صحيح."),
    "su_bad_dates":    ("The end date is before the start date.", "تاريخ النهاية قبل تاريخ البداية."),
    "su_done_kicker":  ("Request received", "تم استلام الطلب"),
    "su_done_title":   ("Thank you, <em>{name}.</em>", "شكراً لك، <em>{name}.</em>"),
    "su_done_body":    ("Your request {ref} is with our team. We'll reply to {email} within one working day with "
                        "options and prices, and an invitation to your client portal.",
                        "طلبك {ref} لدى فريقنا. سنرد على {email} خلال يوم عمل واحد بالخيارات والأسعار، "
                        "ودعوة إلى بوابة العملاء."),
    "su_done_next":    ("What happens next", "ما الخطوة التالية"),
    "su_back":         ("Back to the front page", "العودة إلى الصفحة الرئيسية"),

    # Signing in
    "in_kicker":       ("Sign in", "تسجيل الدخول"),
    "in_title":        ("Welcome <em>back.</em>", "مرحباً <em>بعودتك.</em>"),
    "in_sub":          ("Choose how you work with us.", "اختر طريقة عملك معنا."),
    "in_client":       ("Client", "عميل"),
    "in_team":         ("Team", "الفريق"),
    "in_client_body":  ("We'll email you a sign-in link. No password needed.",
                        "سنرسل إليك رابط تسجيل الدخول بالبريد. لا حاجة لكلمة مرور."),
    "in_email":        ("Email", "البريد الإلكتروني"),
    "in_send_link":    ("Email me a sign-in link", "أرسل لي رابط الدخول"),
    "in_team_body":    ("Sign in with your work Google account.", "سجّل الدخول بحساب Google الخاص بالعمل."),
    "in_dev":          ("Email (local development only)", "البريد (للتطوير المحلي فقط)"),
    "in_not_set":      ("Team sign-in isn't switched on yet. The owner sets GOOGLE_CLIENT_ID to switch it on.",
                        "تسجيل دخول الفريق غير مفعّل بعد. يفعّله المالك بإضافة GOOGLE_CLIENT_ID."),
    "in_new":          ("New here?", "جديد هنا؟"),
    "in_side_title":   ("Every screen. Every slot. <em>Every play.</em>", "كل شاشة. كل فترة. <em>كل عرض.</em>"),
}


def tw(key, lang="en", **kw):
    pair = P.get(key)
    if pair is None:
        return key
    text = pair[1] if lang == "ar" else pair[0]
    return text.format(**kw) if kw and isinstance(text, str) else text
