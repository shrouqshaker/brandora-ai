"""Shared current-turn routing for standalone retrieval and evidence planning.

Reuse the legacy classifier; keep its existing standalone safety overrides in
one place. This does not change the live RulesRouter or Gemini Router.
"""
import re

from ..nlu import RuleContext, classify_rules, normalize
from ..schemas import Intent, OWNER_REVIEW_INTENTS, RouterResult


def route_current_inquiry(text: str) -> RouterResult:
    result = classify_rules(text, RuleContext())
    intent = result.intents[0] if result.intents else Intent.UNKNOWN
    normalized = normalize(text)
    public_topics = ("store name", "business name", "shop name", "اسم المتجر", "اسم المحل", "اسم البيزنس",
                     "working hours", "مواعيد", "عنوان المحل", "رقم المحل", "طرق الدفع", "رسوم التوصيل")
    if intent not in OWNER_REVIEW_INTENTS | {Intent.COMPLAINT, Intent.ORDER_STATUS, Intent.ORDER_CANCEL, Intent.ORDER_MODIFY} and any(normalize(word) in normalized for word in public_topics):
        result.intents = [Intent.GENERAL_INFO]
    if normalized in {"how do i use this bot", "how does this bot work"}:
        result.intents = [Intent.HELP]
    elif normalized in {"i have a complaint", normalize("عندي شكوى")}:
        result.intents = [Intent.COMPLAINT]
    elif intent not in OWNER_REVIEW_INTENTS and re.search(
            r"\b(?:order|delivery)\b.*\b(?:late|delayed|wrong|damaged|never arrived)\b", normalized):
        result.intents = [Intent.COMPLAINT]
    elif intent == Intent.UNKNOWN and normalized and normalized.split()[0] in {"3ayez", "3ayza", "3awz", "3andkom", "bekam"}:
        result.intents = [Intent.PRICE_INQUIRY if normalized.split()[0] == "bekam" else Intent.PRODUCT_INQUIRY]
    return result
