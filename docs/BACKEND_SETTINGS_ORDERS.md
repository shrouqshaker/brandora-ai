# Current Backend integration: policies, FAQs and suggested orders

Local implementation on top of AI commit 2545c74. No deployment or live-model
success is implied by offline tests. The existing Railway HTTPS edge-trust fix is
preserved; no environment files, credentials, dependencies or Railway settings
are changed. Canonical integration is POST /v2/ai/respond. Legacy E0-3 v1 and
Products-only endpoints retain their separate response contracts.

## Business information and the original generic-answer failure

Previously the canonical tool allowlist excluded get_business_info, the typed
aggregate retained only business.id/name, returns/delivery/payment were routed
as unsupported, and the planner could fall back to product clarification for a
business question. The canonical client now permits the existing six read tools,
parses the complete aggregate and routes public business/policy questions to it.

The same signed X-AI-Context and Bearer BACKEND_SERVICE_TOKEN are used for:

```json
{"tool":"get_business_info","arguments":{}}
```

The envelope is {tool,data}, with data containing business, settings,
workingHours and faqs. Profile fields include description/category/phone/address/
logoUrl; policies include returnPolicy/paymentNotes/deliveryNotes and deliveryFee,
plus the existing cash/online-payment/delivery/pickup flags. Fees are nullable
nonnegative two-decimal strings. Missing/empty policy text and null fees are
unknown, not a default policy or free delivery; explicit 0.00 is a zero fee.

Only scoped, published FAQ evidence is accepted. Foreign section business IDs,
duplicate weekdays/FAQ IDs, inactive FAQs and malformed money/schedules fail
closed. Owner text is rendered literally, never run as instructions, and policy
periods/payment methods are not rewritten. Missing information marks owner review;
the Arabic fallback does not assert successful owner contact. Business reads are
request-local and refreshed on subsequent requests. No customer context, service
credential or token is sent to the model as factual business information.

## Canonical suggested-order response

The four top-level response keys remain unchanged:

```json
{
  "intent":"PURCHASE_INTENT",
  "reply":"The owner must review this draft; no order is confirmed.",
  "requiresReview":false,
  "orderCandidate":{
    "items":[{
      "productId":"00000004-0000-4000-8000-000000000010",
      "variantId":"00000005-0000-4000-8000-000000000101",
      "quantity":2
    }]
  }
}
```

The canonical suggestion DTO is deliberately separate from legacy E0-3 models.
No prices, totals, stock/status fields, confidence, catalogItemId, sourceMessageId
or action commands appear inside this candidate. IDs come from current scoped
Backend evidence, never model output. Each unique variant has an explicit integer
quantity 1-100; snapshots contain at most 50 items. Partial search/ambiguous
variants/missing quantities/incomplete history/budget failures never produce a
partial replacement snapshot. Inquiries still return orderCandidate:null.

A non-null candidate is the complete final requested list. Explicit add/remove,
quantity changes, variant replacement, and multiple clearly separated purchase
clauses are reconstructed within the existing request budget. Existing explicitly
requested quantities may be retained for an unambiguous variant replacement;
unknown quantities are never invented. Compound or ambiguous natural-language
requests may require clarification. Stock is observed, not reserved; unavailable
quantities can be flagged for owner review. No inventory mutation or owner Orders
endpoint is called by this service.

- null: no suggestion update.
- {items:[]}: clear only the draft selection.
- {items:[],abandoned:true}: explicit abandonment of the purchase draft.
- Optional discountRequest: original bounded customer request, never an approved
  discount or new catalog/selling price. Owner approval is required.

Actual order cancellation/modification/status remain owner operations. Clearing a
draft is explicitly described as leaving actual orders unchanged. The Backend
creates PENDING_APPROVAL only after owner review; approval, completion, rejection,
restocking and custom selling prices remain entirely Backend/owner-controlled.

## Required history metadata: consumption and ordering

The existing get_conversation_history tool now additionally supplies purchaseDraft:

```json
{
  "revision":3,
  "sourceMessageId":"00000009-0000-4000-8000-000000000010",
  "items":[{
    "productId":"00000004-0000-4000-8000-000000000010",
    "variantId":"00000005-0000-4000-8000-000000000101",
    "quantity":2
  }],
  "updatedAt":"2026-11-27T12:00:00.000Z",
  "updateMessageId":"00000009-0000-4000-8000-000000000010"
}
```

This is a read-only addition to the same signed conversation/customer/business
boundary, not an extra tool. Source and items are whitelisted; prices/discount
approval/private fields are absent. An empty items list retains the watermark
following consumption/abandonment, so older customer requests are not resurrected.
The AI revalidates saved IDs against current scoped product/variant reads, then
replays only newer authorized CUSTOMER turns, ordered by timestamp and UUID.
Assistant/owner prose never becomes cart contents or price authority. Metadata
revision is included in the Backend history snapshot to detect pagination races.

Explicit purchaseDraft:null means state is unavailable or newer than the signed
source boundary: no replacement suggestion is emitted. Legacy ordered conversations
without a safe watermark also fail closed; a fresh explicit draft reset can
establish a new boundary without changing the actual order. A missing field means
an older Backend: informational replies remain compatible, but old purchase
messages cannot safely reconstruct full snapshots when consumption is unknown.
Deploy this paired Backend read addition before enabling the updated AI behavior.
No migration is needed; the existing conversation fields are reused.

The Backend owns source-message idempotency, atomic consumption and stale-response
rejection. The AI introduces no global session/cache or durable write state.
Repeated direct AI calls can safely suppress a candidate once that source was
already applied; the Backend returns the first unique saved reply.

## Verification and handoff

Use a Python environment with the already maintained requirements/dev requirements:

```powershell
$env:APP_ENV='local'
$env:AI_PROVIDER='rules'
$env:PYTHON_DOTENV_DISABLED='1'
python -m pytest -q
python -m compileall -q app main.py tools
python -m app.offline_demo --evaluate
$env:PYTHONPATH=(Get-Location).Path
python tools/verify_current_backend.py --backend-root '<local brandora-backend root>'
```

The last check invokes the real compiled Backend parser/Business tool assembly
with synthetic persistence, and validates AI responses against it. It does not
connect to a database or running production service. Build the intended Backend
first. Unit/HTTP tests use MockTransport, fresh synthetic RS256 keys and fake
providers; they do not prove a live Groq response, Meta delivery, deployed
credentials or the currently deployed Backend version.

Existing cumulative Backend-call, context-expiry, history, response-size and
request-time limits remain. Large/long carts that cannot be fully revalidated
within those limits require clarification; they are never silently truncated.
No paid provider call, package installation, production data access, push or
deployment is part of this implementation. Owner approval is still needed for
any later publication/deployment; no secrets belong in Git or this guide.
