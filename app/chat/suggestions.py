"""Request-local cart reconstruction from customer turns and fresh scoped evidence.

No model-generated identifiers, assistant prose, saved quotes, stock writes or
cross-request cache. The Backend remains authoritative for consumption/versioning.
"""
import re
from dataclasses import replace
from ..nlu import normalize
from ..schemas.suggestions import CanonicalAiResponse, SuggestedItem, SuggestedOrder
from ..schemas.response import E03Intent
from ..pipeline.evidence_planner import Resolution
from ..data.search import search_terms
from ..data.routing import route_current_inquiry
from ..schemas import Intent
from .understanding import Selection, rules_meaning


def draft_action(text):
    text = normalize(text)
    discount_request = bool(re.search(r'(?:can i|could i|give me|i want|please).{0,40}discount|discount please|(?:ممكن|عايز|عايزه|عاوز|عاوزه).{0,30}خصم|خصم لو سمحت', text))
    if any(intent in {Intent.GENERAL_INFO, Intent.RETURNS_POLICY} for intent in route_current_inquiry(text).intents) and not discount_request:
        return None
    if re.fullmatch(r'(?:انا )?(?:خلاص )?مش (?:عايز|عايزه|عاوز|عاوزه)(?: خلاص)?', text):
        return 'abandon'
    if re.fullmatch(r"(?:please )?(?:never ?mind|forget (?:the )?purchase|(?:i )?do not want to buy)|(?:انا )?(?:خلاص )?مش (?:عايز|عايزه|عاوز|عاوزه) اشتري(?: خلاص)?", text):
        return 'abandon'
    if re.search(r"\b(?:clear|remove) all (?:items|products)\b|امسح كل (?:المنتجات|العناصر)", text):
        return 'clear'
    if re.search(r"\b(?:remove|drop)\b|(?:امسح|شيل|احذف)", text):
        return 'remove'
    if re.search(r"\b(?:replace|instead|change|make it)\b|(?:بدل|غير|خليها|خليهم)", text):
        return 'replace'
    if re.search(r"\b(?:add|also)\b|(?:ضيف|اضف|زود|وكمان)", text):
        return 'add'
    if rules_meaning(text, Selection()).act == 'purchase':
        return 'set'
    return 'discount' if discount_request else None


def split_items(text):
    # Conjunctions only split explicit purchase clauses; unresolved clauses veto
    # the whole snapshot rather than dropping a customer's requested line.
    parts = re.split(r"\s+(?:and|و|وكمان)\s+", text, flags=re.I)
    return parts if 1 < len(parts) <= 50 else [text]


class DraftCart:
    def __init__(self):
        self.items = {}
        self.labels = {}
        self.products = {}
        self.invalid = False
        self.ambiguous = False

    async def seed(self, draft, data, scope, token):
        from fastapi import HTTPException
        from ..schemas.backend import RetrievalStatus
        from ..data.options import variant_options_valid
        for item in draft.items:
            product = await data.get_product_details(item.productId, scope=scope, context_token=token)
            variant = await data.get_variant_details(item.productId, item.variantId, scope=scope, context_token=token)
            for outcome in (product, variant):
                if outcome.status in {RetrievalStatus.AUTHORIZATION_FAILED, RetrievalStatus.SCOPE_MISMATCH}:
                    raise HTTPException(403, 'Backend draft authorization rejected')
            if product.envelope is None or variant.envelope is None:
                self.invalid = True
                return
            parent, selected = product.envelope.data, variant.envelope.data
            if not parent.isActive or not selected.isActive or selected.effectivePrice is None or not variant_options_valid(parent, selected):
                self.invalid = True
                return
            self.items[item.variantId] = SuggestedItem.model_validate(item.model_dump())
            self.labels[item.variantId] = set(search_terms(parent.name + ' ' + ' '.join(selected.optionValues.values())))
            self.products[parent.id] = parent

    def _target(self, text):
        words = set(search_terms(text))
        filler = set(search_terms('remove drop it that one please make change quantity to add replace size color colour شيل امسح دي ده خليها خليهم بدل غير مقاس لون'))
        selectors = {word for word in words - filler if not word.isdigit()}
        scored = [(len(words & label), key) for key, label in self.labels.items() if selectors <= label]
        best = max((score for score, _ in scored), default=0)
        found = [key for score, key in scored if score == best and score > 0]
        if not found and len(self.items) == 1:
            # Pronouns only: a new unrecognized product must not select an old one.
            if not selectors:
                return next(iter(self.items))
        return found[0] if len(found) == 1 else None

    def apply(self, text, turn):
        action = draft_action(text)
        if action in {'abandon', 'clear'}:
            self.items.clear(); self.labels.clear(); self.invalid = False
            return action
        if action == 'remove':
            target = self._target(text)
            if target is None:
                self.invalid = True
                self.ambiguous = True
                return None
            self.items.pop(target); self.labels.pop(target)
            return action
        if action is None:
            return None
        if action == 'discount':
            self.invalid = not bool(self.items)
            return action
        if action == 'replace' and len(self.items) > 1 and self._target(text) is None:
            self.invalid = True
            self.ambiguous = True
            return None
        plan = turn.execution.plan
        matches = [v for v in plan.variants if v.variant_id in plan.matching_variant_ids]
        if (turn.execution.retrieval.failure or plan.resolution != Resolution.SINGLE_VARIANT or len(matches) != 1
                or len(plan.products) != 1 or plan.ambiguities or 'partial_catalog_not_globally_unique' in plan.limitations):
            self.invalid = True
            return None
        variant, product = matches[0], plan.products[0]
        if not (variant.active and variant.parent_active and variant.options_verified and variant.effective_price is not None):
            self.invalid = True
            return None
        quantity = plan.requested_quantity
        previous = [key for key, item in self.items.items() if item.productId == product.product_id]
        target = self._target(text) if action == 'replace' else None
        if action == 'replace' and (len(previous) == 1 or target in previous):
            old = previous[0] if len(previous) == 1 else target
            if quantity is None:
                quantity = self.items[old].quantity  # explicitly requested in this scoped history
            self.items.pop(old); self.labels.pop(old)
        elif action == 'replace' and len(self.items) > 1:
            self.invalid = True
            return None
        if type(quantity) is not int or not 1 <= quantity <= 100:
            self.invalid = True
            return None
        if action == 'add' and variant.variant_id in self.items:
            quantity += self.items[variant.variant_id].quantity
        if quantity > 100 or (variant.variant_id not in self.items and len(self.items) >= 50):
            self.invalid = True
            return None
        self.items[variant.variant_id] = SuggestedItem(productId=product.product_id, variantId=variant.variant_id, quantity=quantity)
        self.labels[variant.variant_id] = set(search_terms(product.name + ' ' + ' '.join(value for _, value in variant.option_values)))
        for candidate in turn.execution.retrieval.candidates:
            self.products[candidate.id] = candidate
        return action

    def selection(self):
        if len(self.items) != 1:
            return None
        item = next(iter(self.items.values()))
        product = self.products.get(item.productId)
        if product is None:
            return None
        variant = next((v for v in product.variants if v.id == item.variantId), None)
        if variant is None:
            return None
        return Selection((product,), tuple(variant.optionValues.items()), item.quantity)

    def response(self, text, turn, action, complete):
        candidate = None
        payload = turn.response.model_dump(mode='json')
        if action in {'abandon', 'clear'}:
            candidate = SuggestedOrder(items=[], abandoned=action == 'abandon')
            payload['intent'] = E03Intent.OTHER.value
            payload['reply'] = ('تمام، نسيب الاختيار ده ونمسح مسودة الاختيارات فقط؛ لم يتم تغيير أي طلب فعلي.' if turn.understanding.meaning.language == 'ar'
                                else 'The draft selection is cleared; no actual order was changed.')
        elif action is not None and complete and not self.invalid:
            discount = text[:500] if re.search(r'discount|خصم|تخفيض', normalize(text)) else None
            candidate = SuggestedOrder(items=list(self.items.values()), discountRequest=discount)
            payload['intent'] = E03Intent.PURCHASE_INTENT.value
            if action in {'remove', 'discount'}:
                payload['reply'] = ('تم تحديث مسودة الاختيارات فقط؛ أي خصم يحتاج موافقة المالك ولم يتم تغيير سعر أو طلب فعلي.'
                                    if turn.understanding.meaning.language == 'ar' else
                                    'Only the draft selection is updated. Discounts need owner approval; no price or actual order was changed.')
        elif action is not None:
            clarification = ('تقصد أنهي منتج؟ ممكن توضح قائمة المنتجات والاختيارات والكميات النهائية؟ لم يتم إنشاء أو تعديل طلب.'
                                if turn.understanding.meaning.language == 'ar' else
                                'Please clarify the full final products, variants and quantities; no order has been created or changed.')
            payload['reply'] = (payload['reply'] + '\n' + clarification
                                if complete and turn.execution.plan.products and not self.ambiguous else clarification)
        payload['orderCandidate'] = candidate.model_dump(mode='json') if candidate is not None else None
        return replace(turn, execution=replace(turn.execution, response=CanonicalAiResponse.model_validate(payload)))
