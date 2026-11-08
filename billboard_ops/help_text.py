"""One-page guides for each role, in English and Arabic (Phase 5). Printable from Help. The Arabic should be
checked by a native speaker on the client's team before training."""

GUIDES = {
    "owner": {
        "title": ("The owner", "المالك"),
        "intro": ("You see the whole business on one page and decide what the agent may do alone.",
                  "ترى العمل كله في صفحة واحدة، وتحدد ما يمكن للوكيل القيام به وحده."),
        "steps": [
            ("Start each day on the Dashboard: how full the screens are, what is at risk, and what waits for you.",
             "ابدأ كل يوم من لوحة المتابعة: نسبة إشغال الشاشات، وما هو معرّض للخطر، وما ينتظر قرارك."),
            ("Pale cells in “The next 12 weeks” are gaps worth selling; click one to open the calendar.",
             "الخانات الفاتحة في «الأسابيع الـ12 القادمة» فرص للبيع؛ اضغط عليها لفتح التقويم."),
            ("Each line under “At risk” says who owns the next step. Open it and nudge that person.",
             "كل سطر في «معرّض للخطر» يذكر المسؤول عن الخطوة التالية. افتحه وتابع مع ذلك الشخص."),
            ("Every Monday morning a summary arrives by email. Preview it any time from the Dashboard.",
             "يصلك ملخص بالبريد كل صباح اثنين. يمكنك معاينته في أي وقت من لوحة المتابعة."),
            ("Settings → Automation: switch a step to automatic only when Performance shows people rarely change "
             "the agent's work.",
             "الإعدادات ← الأتمتة: اجعل الخطوة تلقائية فقط عندما تُظهر صفحة الأداء أن الفريق نادراً ما يعدّل عمل الوكيل."),
            ("Settings → People: add the team and give each person a role. Settings → Launch: the launch checklist.",
             "الإعدادات ← الأشخاص: أضف الفريق وحدد دور كل شخص. الإعدادات ← الإطلاق: قائمة التحقق قبل الإطلاق."),
            ("A client asks to be forgotten: their page → “Delete all of this client's data”, after a backup.",
             "إذا طلب عميل حذف بياناته: صفحته ← «حذف جميع بيانات العميل»، بعد أخذ نسخة احتياطية."),
        ],
    },
    "manager": {
        "title": ("Managers", "المديرون"),
        "intro": ("You confirm bookings and decide the exceptions.", "تؤكد الحجوزات وتقرر الاستثناءات."),
        "steps": [
            ("Confirm a hold once the signed proposal or purchase order arrives; attach it when you confirm.",
             "أكّد الحجز المبدئي عند وصول العرض الموقّع أو أمر الشراء، وأرفقه عند التأكيد."),
            ("Only you can book past a screen's capacity, approve a file that failed, or confirm a plan that "
             "overfills the loop. Each needs a reason, and it is logged.",
             "أنت وحدك تستطيع الحجز فوق سعة الشاشة، أو اعتماد ملف لم يجتز الفحص، أو تأكيد خطة تتجاوز الدورة. "
             "كل ذلك يتطلب سبباً ويُسجَّل."),
            ("Watch “At risk” and the Outbox: messages to clients wait there for approval.",
             "تابع «معرّض للخطر» وصندوق الرسائل الصادرة: رسائل العملاء تنتظر الموافقة هناك."),
            ("Performance shows how long requests take to become holds, and the most common file mistakes.",
             "تُظهر صفحة الأداء الوقت اللازم لتحويل الطلبات إلى حجوزات، وأكثر أخطاء الملفات شيوعاً."),
        ],
    },
    "sales": {
        "title": ("Sales", "المبيعات"),
        "intro": ("You turn requests into holds and keep clients moving.", "تحوّل الطلبات إلى حجوزات وتتابع العملاء."),
        "steps": [
            ("Proposals: the agent reads each request. Check its reading, fix anything wrong, then place the hold.",
             "الطلبات: يقرأ الوكيل كل طلب. راجع قراءته وصحّح أي خطأ، ثم ضع الحجز المبدئي."),
            ("Nothing fits? “Offer alternatives” drafts a reply with the nearest free dates and screens.",
             "لا يوجد توفر؟ «اقتراح بدائل» يُعدّ رداً بأقرب التواريخ والشاشات المتاحة."),
            ("Check vacancy answers “is there room?” in seconds, for any screens, dates, hours and frequency.",
             "«التحقق من التوفر» يجيب عن سؤال التوفر خلال ثوانٍ لأي شاشات وتواريخ وساعات وتكرار."),
            ("Approve the drafts in the Outbox: hold confirmations, questions, reminders.",
             "وافق على المسودات في صندوق الرسائل الصادرة: تأكيدات الحجز والأسئلة والتذكيرات."),
            ("Renewal reminders reach you a week before a campaign ends: call the client.",
             "تصلك تذكيرات التجديد قبل انتهاء الحملة بأسبوع: تواصل مع العميل."),
            ("Give a client portal access from their page, so they can follow their bookings themselves.",
             "امنح العميل صلاحية البوابة من صفحته ليتابع حجوزاته بنفسه."),
        ],
    },
    "ops": {
        "title": ("Operations", "العمليات"),
        "intro": ("You approve the files, set the play plan and load the screens.",
                  "تعتمد الملفات، وتحدد خطة العرض، وتحمّل الإعلانات على الشاشات."),
        "steps": [
            ("Creative: watch each checked file, read its report, then approve it or ask for changes.",
             "التصاميم: شاهد كل ملف تم فحصه واقرأ تقريره، ثم اعتمده أو اطلب التعديلات."),
            ("Play plan: check the agent's draft (plays per hour by part of the day) and confirm it.",
             "خطة العرض: راجع مسودة الوكيل (عدد مرات العرض في الساعة حسب فترات اليوم) وأكّدها."),
            ("Playlists: export the day's playlist, or download the booking's files (ZIP) named for the screen "
             "software. Load them, then tick “Loaded” for each screen.",
             "قوائم العرض: صدّر قائمة اليوم أو نزّل ملفات الحجز (ZIP) بأسماء تناسب برنامج الشاشات. حمّلها ثم اختر "
             "«تم التحميل» لكل شاشة."),
            ("If a screen says it needs loading again, a newer file or plan was approved: load it again.",
             "إذا ظهرت عبارة «يحتاج إلى إعادة التحميل»، فقد اعتُمد ملف أو خطة أحدث: حمّلها من جديد."),
            ("Add proof of play: a photo or video of the screen, or the screen software's play report.",
             "أضف إثبات العرض: صورة أو فيديو للشاشة، أو تقرير العرض من برنامج الشاشات."),
            ("The day before a start, an alert lists anything still missing. Fix it before the morning.",
             "قبل يوم من البدء، يصلك تنبيه بما ينقص. أكمله قبل الصباح."),
        ],
    },
    "client": {
        "title": ("Clients: your portal", "العملاء: بوابتكم"),
        "intro": ("Follow your bookings, send your files and approve your campaigns, in English or Arabic.",
                  "تابعوا حجوزاتكم، وأرسلوا ملفاتكم، ووافقوا على حملاتكم، بالعربية أو الإنجليزية."),
        "steps": [
            ("Sign in with your work email: we send you a link that works once, for 30 minutes. No password.",
             "سجّلوا الدخول ببريدكم الإلكتروني للعمل: نرسل لكم رابطاً يعمل مرة واحدة خلال 30 دقيقة. لا حاجة لكلمة مرور."),
            ("My bookings shows each campaign, where it stands and what's next.",
             "تعرض «حجوزاتي» كل حملة وحالتها والخطوة التالية."),
            ("Accept a hold, and attach the signed proposal or purchase order.",
             "اقبلوا الحجز المبدئي، وأرفقوا العرض الموقّع أو أمر الشراء."),
            ("Upload your ad files. Within minutes you see whether they pass, and how to fix anything that doesn't.",
             "ارفعوا ملفات الإعلان. خلال دقائق تعرفون إن كانت مقبولة، وكيفية تصحيح ما لا يطابق المتطلبات."),
            ("All text must be in Arabic, or in Arabic and English.",
             "يجب أن تكون جميع النصوص بالعربية، أو بالعربية والإنجليزية."),
            ("Approve the play plan and the final preview when asked, or tell us what to change.",
             "وافقوا على خطة العرض والمعاينة النهائية عند الطلب، أو أخبرونا بما تريدون تعديله."),
            ("Once live, photos and play reports show here. New request: tell us what you'd like to book next.",
             "بعد البدء، تظهر هنا الصور وتقارير العرض. «طلب جديد»: أخبرونا بما ترغبون بحجزه لاحقاً."),
        ],
    },
}
TEAM_ORDER = ("owner", "manager", "sales", "ops", "client")
