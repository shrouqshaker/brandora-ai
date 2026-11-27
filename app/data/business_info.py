"""Render public business evidence literally, never infer policies or perform actions."""
import re
from ..nlu import normalize


def missing_business_answer(language):
    return ("مش متأكد من المعلومة دي، هسأل صاحب المحل ويرد عليك." if language == "ar"
            else "I'm not sure about that information; the store owner needs to confirm it.")


_TOPICS = {
    "returns": ("return", "refund", "exchange", "استبدال", "استرجاع", "ارجع", "ترجيع", "مرتجع"),
    "payment": ("payment", "pay", "cash", "instapay", "vodafone", "دفع", "كاش", "انستاباي", "فودافون"),
    "delivery": ("delivery", "deliver", "shipping", "توصيل", "بتوصل", "شحن"),
    "hours": ("hours", "open", "close", "مواعيد", "معاد", "بتفتح", "بتقفل", "ساعات العمل"),
    "address": ("address", "location", "located", "عنوان", "مكان", "فين المحل"),
    "phone": ("phone", "contact", "telephone", "رقم", "تليفون", "تواصل"),
    "description": ("description", "about your", "نبذه", "نبذة", "وصف", "عن المتجر"),
    "name": ("business name", "store name", "shop name", "اسم المحل", "اسم المتجر", "اسم البيزنس"),
}
_STOP = set("what is the a an do you your are can how i of please هل ما هو هي في عندكم ممكن ايه إيه".split())


def business_answer(info, question, language):
    text = normalize(question)
    keys = set(re.findall(r"\w+", text)) - _STOP
    matched = []
    for faq in info.faqs:
        other = normalize(faq.question)
        terms = set(re.findall(r"\w+", other)) - _STOP
        if text == other or (len(keys) >= 2 and len(terms) >= 2 and keys <= terms):
            matched.append(faq.answer)
    if matched:
        unique = list(dict.fromkeys(matched))
        return (unique[0], False) if len(unique) == 1 else (missing_business_answer(language), True)
    topics = [topic for topic, words in _TOPICS.items() if any(normalize(word) in text for word in words)]
    settings = info.settings
    output, missing = [], False
    ar = language == "ar"
    for topic in topics:
        value = None
        if topic == "name":
            value = ("اسم المتجر: " if ar else "Store name: ") + info.business.name
        elif topic in {"address", "phone", "description"}:
            value = getattr(info.business, topic)
        elif topic == "returns" and settings:
            value = settings.returnPolicy
        elif topic == "payment" and settings:
            blocks = [settings.paymentNotes] if settings.paymentNotes and settings.paymentNotes.strip() else []
            if settings.acceptsCash is True:
                blocks.append("الدفع النقدي متاح." if ar else "Cash payment is accepted.")
            if settings.acceptsOnlinePayment is True:
                blocks.append("المتجر يقبل الدفع الإلكتروني؛ طريقة الدفع تحتاج تأكيد صاحب المتجر." if ar
                              else "The store accepts online payment; ask the owner to confirm the method.")
            value = "\n".join(blocks) or None
        elif topic == "delivery" and settings:
            if settings.deliveryEnabled is False:
                value = "التوصيل غير متاح حسب إعدادات المتجر." if ar else "Delivery is disabled in the store settings."
            elif settings.deliveryEnabled is True:
                blocks = ["التوصيل متاح." if ar else "Delivery is enabled."]
                if settings.deliveryNotes and settings.deliveryNotes.strip():
                    blocks.append(settings.deliveryNotes)
                if settings.deliveryFee is not None:
                    blocks.append(("رسوم التوصيل المسجلة: " if ar else "Recorded delivery fee: ") + f"{settings.deliveryFee:.2f} EGP")
                else:
                    blocks.append("رسوم التوصيل غير محددة." if ar else "The delivery fee is unspecified.")
                value = "\n".join(blocks)
        elif topic == "hours" and info.workingHours:
            days = {"MONDAY": "الاثنين", "TUESDAY": "الثلاثاء", "WEDNESDAY": "الأربعاء", "THURSDAY": "الخميس",
                    "FRIDAY": "الجمعة", "SATURDAY": "السبت", "SUNDAY": "الأحد"}
            value = "\n".join(f"{days[day.dayOfWeek] if ar else day.dayOfWeek}: " +
                (f"{day.opensAt}–{day.closesAt}" if day.isOpen else "مغلق" if ar else "closed") for day in info.workingHours)
        if isinstance(value, str) and value.strip():
            output.append(value)
        else:
            missing = True
    if not output or missing:
        output.append(missing_business_answer(language))
        missing = True
    return "\n".join(output), missing
