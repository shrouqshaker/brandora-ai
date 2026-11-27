"""Current Backend contracts through signed HTTP with only synthetic transports."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import pytest
from pydantic import ValidationError
from app.offline_demo import OfflineHarness
from app.chat.synthetic import identifier
from app.schemas.backend import BusinessInformation
from app.schemas.suggestions import CanonicalAiResponse, SuggestedOrder


@pytest.fixture
def harness():
    h = OfflineHarness()
    for business in (1, 2):
        bid = identifier(1, business)
        h.backend.business_info[business] = {
            'business': {'id': bid, 'name': f'Store {business}', 'description': 'Handmade gifts', 'phone': '201000000001', 'address': 'Recorded address'},
            'settings': {'businessId': bid, 'acceptsCash': True, 'acceptsOnlinePayment': False,
                         'deliveryEnabled': True, 'pickupEnabled': True, 'returnPolicy': 'Returns within 7 days only.',
                         'paymentNotes': 'Cash or owner-provided manual instructions.', 'deliveryNotes': 'Cairo only.', 'deliveryFee': '35.00'},
            'workingHours': [{'businessId': bid, 'dayOfWeek': 'MONDAY', 'isOpen': True, 'opensAt': '09:00', 'closesAt': '17:00'}],
            'faqs': [{'id': identifier(8, business), 'question': 'Do you gift wrap?', 'answer': 'Gift wrapping is available on request.', 'position': 0}],
        }
        cap = h.backend.products[business][1]
        cap['basePrice'] = '50.00'; cap['isSellable'] = True
        cap['variants'][0].update(unitPrice='50.00', effectivePrice='50.00', trackInventory=True, stockQuantity=10)
    return h


def send(h, text, **kwargs):
    response = h.send(h.body(text, **kwargs))
    assert response.status_code == 200, response.text
    result = response.json()
    CanonicalAiResponse.model_validate(result)
    assert all(call['tool'] in {'get_business_info', 'get_conversation_history', 'get_products', 'get_product_details', 'get_product_variants', 'get_variant_details'} for call in h.calls)
    return result


@pytest.mark.parametrize('question,fragment', [
    ('what is your store name?', 'Store 1'), ('اسم المتجر ايه؟', 'Store 1'),
    ('what is your return policy?', 'Returns within 7 days only.'),
    ('what are your working hours?', '09:00'), ('مواعيد المحل ايه؟', '17:00'),
    ('can I pay cash?', 'Cash or owner-provided manual instructions.'),
    ('do you deliver?', '35.00'), ('Do you gift wrap?', 'Gift wrapping is available on request.'),
])
def test_public_information_uses_scoped_live_tool(harness, question, fragment):
    result = send(harness, question)
    assert fragment in result['reply']
    assert result['orderCandidate'] is None
    assert any(call['tool'] == 'get_business_info' for call in harness.calls)


def test_updated_policy_and_zero_or_unspecified_fee_are_not_cached(harness):
    assert '35.00' in send(harness, 'do you deliver?')['reply']
    harness.backend.business_info[1]['settings']['deliveryFee'] = '0.00'
    assert '0.00' in send(harness, 'do you deliver?')['reply']
    harness.backend.business_info[1]['settings']['deliveryFee'] = None
    result = send(harness, 'do you deliver?')
    assert 'unspecified' in result['reply'] and '0.00' not in result['reply']
    harness.backend.business_info[1]['settings']['returnPolicy'] = 'Exactly 14 owner-specified days.'
    assert 'Exactly 14 owner-specified days.' in send(harness, 'what is your return policy?')['reply']


def test_missing_policy_is_review_without_false_contact_claim(harness):
    harness.backend.business_info[1]['settings']['returnPolicy'] = None
    result = send(harness, 'سياسة الاسترجاع ايه؟')
    assert result['requiresReview'] is True
    assert result['reply'] == 'مش متأكد من المعلومة دي، هسأل صاحب المحل ويرد عليك.'
    assert 'تم التواصل' not in result['reply'] and result['orderCandidate'] is None


@pytest.mark.parametrize('section', ['settings', 'workingHours', 'faqs'])
def test_foreign_or_inactive_sections_fail_closed(harness, section):
    data = harness.backend.business_info[1]
    if section == 'settings': data[section]['businessId'] = identifier(1, 2)
    elif section == 'workingHours': data[section][0]['businessId'] = identifier(1, 2)
    else: data[section][0]['isActive'] = False
    with pytest.raises(ValidationError): BusinessInformation.model_validate(data)
    result = send(harness, 'what is your store name?')
    assert 'Store 1' not in result['reply'] and result['requiresReview']


def test_complete_single_item_suggestion_is_exact_backend_shape(harness):
    result = send(harness, 'I want 2 black tshirt size L')
    assert result['orderCandidate'] == {'items': [{'productId': identifier(4, 10), 'variantId': identifier(5, 101), 'quantity': 2}]}
    assert result['intent'] == 'PURCHASE_INTENT'


def test_add_remove_and_quantity_updates_return_full_snapshot(harness):
    send(harness, 'I want 2 black tshirt size L')
    added = send(harness, 'add 3 cap')
    assert sorted(item['quantity'] for item in added['orderCandidate']['items']) == [2, 3]
    removed = send(harness, 'remove cap')
    assert [item['variantId'] for item in removed['orderCandidate']['items']] == [identifier(5, 101)]
    changed = send(harness, 'make it 4')
    assert changed['orderCandidate']['items'][0]['quantity'] == 4


def test_multiple_items_in_one_turn_and_variant_replacement(harness):
    result = send(harness, 'I want 2 black tshirt size L and 3 cap')
    assert len(result['orderCandidate']['items']) == 2
    send(harness, 'remove cap')
    replaced = send(harness, 'change it to white size L')
    assert replaced['orderCandidate']['items'] == [{'productId': identifier(4, 10), 'variantId': identifier(5, 102), 'quantity': 2}]


def test_missing_quantity_or_variant_does_not_guess(harness):
    assert send(harness, 'I want black tshirt size L')['orderCandidate'] is None
    assert send(harness, 'I want 2 tshirt')['orderCandidate'] is None


def test_incomplete_history_and_ambiguous_changes_do_not_drop_other_items(harness):
    send(harness, 'I want 2 black tshirt size L and 3 cap')
    assert send(harness, 'make it 4')['orderCandidate'] is None
    assert send(harness, 'add 2 cap', history_status='partial')['orderCandidate'] is None


def test_explicit_clear_abandon_and_unrelated_turn_have_distinct_wire_values(harness):
    send(harness, 'I want 2 black tshirt size L')
    assert send(harness, 'thanks')['orderCandidate'] is None
    assert send(harness, 'remove all items')['orderCandidate'] == {'items': []}
    assert send(harness, 'never mind')['orderCandidate'] == {'items': [], 'abandoned': True}


def test_customer_and_business_isolation_no_stock_mutations(harness):
    before = deepcopy(harness.backend.products)
    send(harness, 'I want 2 black tshirt size L', business=1, customer=1)
    assert send(harness, 'make it 3', business=1, customer=2)['orderCandidate'] is None
    other = send(harness, 'I want 3 black tshirt size L', business=2, customer=1)
    assert other['orderCandidate']['items'][0]['productId'] == identifier(4, 20)
    assert harness.backend.products == before


def test_suggestion_models_forbid_prices_guessed_quantities_and_confirmations():
    item = {'productId': identifier(4, 10), 'variantId': identifier(5, 101), 'quantity': 2}
    for change in ({'unitPrice': '1.00'}, {'quantity': True}, {'quantity': '2'}, {'quantity': 0}, {'quantity': 101}):
        with pytest.raises(ValidationError): SuggestedOrder.model_validate({'items': [{**item, **change}]})
    with pytest.raises(ValidationError): SuggestedOrder.model_validate({'items': [item, item]})
    with pytest.raises(ValidationError): CanonicalAiResponse.model_validate({'intent': 'CONFIRMED_ORDER', 'reply': 'Confirmed', 'requiresReview': False, 'orderCandidate': None})


def test_consumed_order_items_are_not_resurrected_from_old_customer_messages(harness):
    send(harness, 'I want 2 black tshirt size L')
    key = tuple(str(getattr(harness.backend.scope(), attr)) for attr in ('business_id','customer_id','conversation_id'))
    harness.purchase_drafts[key]['items'] = []
    harness.purchase_drafts[key]['sourceMessageId'] = None
    result = send(harness, 'add 3 cap')
    assert result['orderCandidate']['items'] == [{'productId': identifier(4, 11), 'variantId': identifier(5, 110), 'quantity': 3}]


def test_abandonment_prevents_older_items_from_reappearing(harness):
    send(harness, 'I want 2 black tshirt size L')
    send(harness, 'never mind')
    assert send(harness, 'add 3 cap')['orderCandidate']['items'][0]['productId'] == identifier(4, 11)


def test_legacy_backend_without_draft_state_does_not_guess_consumption(harness):
    send(harness, 'I want 2 black tshirt size L')
    harness.history_patch = lambda data, _: data.pop('purchaseDraft', None)
    assert send(harness, 'add 3 cap')['orderCandidate'] is None


def test_discount_is_an_unapproved_request_with_no_price_or_order_action(harness):
    before = deepcopy(harness.backend.products)
    send(harness, 'I want 2 black tshirt size L')
    result = send(harness, 'can I get a discount?')
    assert result['orderCandidate']['discountRequest'] == 'can I get a discount?'
    assert 'owner approval' in result['reply']
    assert 'unitPrice' not in result['orderCandidate']['items'][0]
    assert harness.backend.products == before


def test_concurrent_businesses_keep_independent_drafts(harness):
    bodies = [harness.body('I want 2 black tshirt size L', business=1),
              harness.body('I want 3 black tshirt size L', business=2)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(harness.send, bodies))
    assert results[0].json()['orderCandidate']['items'][0]['productId'] == identifier(4, 10)
    assert results[1].json()['orderCandidate']['items'][0]['productId'] == identifier(4, 20)


def test_partially_resolved_multi_item_request_never_drops_the_unresolved_line(harness):
    assert send(harness, 'I want 2 black tshirt size L and cap')['orderCandidate'] is None


def test_newer_draft_state_is_not_used_by_an_older_signed_turn(harness):
    send(harness, 'I want 2 black tshirt size L')
    key = tuple(str(getattr(harness.backend.scope(), attr)) for attr in ('business_id','customer_id','conversation_id'))
    harness.purchase_drafts[key]['updatedAt'] = '2099-01-01T00:00:00+00:00'
    assert send(harness, 'add 3 cap')['orderCandidate'] is None


def test_removal_does_not_guess_from_a_shared_color_or_unknown_product(harness):
    send(harness, 'I want 2 black tshirt size L')
    assert send(harness, 'remove black cap')['orderCandidate'] is None


def test_quoting_abandonment_is_not_an_explicit_purchase_cancellation(harness):
    send(harness, 'I want 2 black tshirt size L')
    assert send(harness, 'What does never mind mean?')['orderCandidate'] is None


def test_discount_policy_inquiry_does_not_update_an_existing_purchase_draft(harness):
    send(harness, 'I want 2 black tshirt size L')
    assert send(harness, 'what is your discount policy?')['orderCandidate'] is None
