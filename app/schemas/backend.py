"""Validated Backend evidence, independent of the legacy v1 request models.

Options maps and business aggregate are provisional DTOs documented in
BACKEND_AI_CONTRACT.md. No factual defaults or inventory/price inheritance.
"""
from decimal import Decimal
from enum import Enum
import re
from typing import Annotated, Generic, Literal, TypeVar
from uuid import UUID

from pydantic import (
    BaseModel, BeforeValidator, ConfigDict, Field, JsonValue, StrictBool, StrictInt,
    StrictStr, model_validator,
)

MAX_SAFE_INTEGER = 2**53 - 1
APPLICATION_CURRENCY = "EGP"  # Application policy; never a claimed database field.


def _identifier(value):
    if isinstance(value, UUID):
        return value
    if not isinstance(value, str):
        raise ValueError("Expected canonical UUID")
    try:
        parsed = UUID(value)
    except ValueError:
        raise ValueError("Expected canonical UUID") from None
    if str(parsed) != value.lower():
        raise ValueError("Expected canonical UUID")
    return parsed


def _price(value):
    if not isinstance(value, str) or re.fullmatch(r"[0-9]{1,10}\.[0-9]{2}", value) is None:
        raise ValueError("Expected nonnegative Decimal(12,2) price string with at most 10 integer digits and exactly 2 fractional digits")
    return Decimal(value)


Identifier = Annotated[UUID, BeforeValidator(_identifier)]
Price = Annotated[Decimal, BeforeValidator(_price)]
SafeInteger = Annotated[StrictInt, Field(ge=0, le=MAX_SAFE_INTEGER)]
T = TypeVar("T")


class BackendModel(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class ExpectedScope(BackendModel):
    """Caller-established scope, NOT cryptographic proof or token decoding.

    History calls require customer_id and conversation_id. No credentials live
    here; the future authenticated boundary must establish these identities.
    """
    business_id: Identifier
    customer_id: Identifier | None = None
    conversation_id: Identifier | None = None


class Pagination(BackendModel):
    limit: Annotated[StrictInt, Field(ge=1, le=100)]
    offset: SafeInteger
    totalCount: SafeInteger
    hasMore: StrictBool
    nextOffset: SafeInteger | None

    @model_validator(mode="after")
    def continuation(self):
        if self.hasMore:
            if self.nextOffset is None or self.nextOffset <= self.offset:
                raise ValueError("Pagination must make safe progress")
        elif self.nextOffset is not None:
            raise ValueError("Terminal page must not have a continuation")
        return self


class Page(BackendModel, Generic[T]):
    items: list[T]
    pagination: Pagination

    @model_validator(mode="after")
    def page_size(self):
        if len(self.items) > self.pagination.limit:
            raise ValueError("Page exceeds limit")
        # totalCount may come from a different database snapshot.
        return self


class InventoryState(str, Enum):
    RECORDED_POSITIVE = "recorded_positive"
    RECORDED_ZERO = "recorded_zero"
    UNTRACKED = "untracked"


class ProductVariant(BackendModel):
    id: Identifier
    businessId: Identifier
    catalogItemId: Identifier
    optionValues: dict[StrictStr, StrictStr]
    unitPrice: Price | None
    effectivePrice: Price | None
    trackInventory: StrictBool
    stockQuantity: SafeInteger | None
    isActive: StrictBool

    @model_validator(mode="before")
    @classmethod
    def accept_backend_product_id_alias(cls, value):
        """Normalize the Backend parent key without weakening validation."""
        if not isinstance(value, dict):
            return value
        has_product = "productId" in value
        has_catalog = "catalogItemId" in value
        if has_product and has_catalog and value["productId"] != value["catalogItemId"]:
            raise ValueError("Conflicting productId and catalogItemId")
        if has_product and not has_catalog:
            value = dict(value)
            value["catalogItemId"] = value["productId"]
        return value

    @model_validator(mode="after")
    def inventory(self):
        if self.trackInventory and self.stockQuantity is None:
            raise ValueError("Tracked inventory requires an integer quantity")
        if not self.trackInventory and self.stockQuantity is not None:
            raise ValueError("Untracked inventory must have null quantity")
        if self.effectivePrice is not None and self.unitPrice is not None and self.effectivePrice != self.unitPrice:
            raise ValueError("Conflicting Backend variant prices")
        return self

    @property
    def inventory_state(self) -> InventoryState:
        if not self.trackInventory:
            return InventoryState.UNTRACKED
        if self.stockQuantity is None:
            raise ValueError("Invalid tracked inventory evidence")
        return InventoryState.RECORDED_POSITIVE if self.stockQuantity > 0 else InventoryState.RECORDED_ZERO


class Product(BackendModel):
    id: Identifier
    businessId: Identifier
    name: Annotated[StrictStr, Field(min_length=1)]
    description: StrictStr | None
    basePrice: Price | None
    options: dict[StrictStr, list[StrictStr]]
    attributes: dict[str, JsonValue] | None
    isActive: StrictBool
    isSellable: StrictBool
    variants: list[ProductVariant]
    variantsPagination: Pagination

    @model_validator(mode="after")
    def embedded_page(self):
        if not self.name.strip():
            raise ValueError("Product name must not be blank")
        if len(self.variants) > self.variantsPagination.limit:
            raise ValueError("Embedded variant page exceeds limit")
        for variant in self.variants:
            expected = variant.unitPrice if variant.unitPrice is not None else self.basePrice
            if variant.effectivePrice is not None and variant.effectivePrice != expected:
                raise ValueError("Conflicting Backend effective price")
        # Null effectivePrice stays unknown, even when inputs could derive it.
        # Relationships are checked by the data layer to distinguish scope
        # violations from malformed data. Never recompute isSellable from a page.
        return self


class BusinessSummary(BackendModel):
    id: Identifier
    name: Annotated[StrictStr, Field(min_length=1)]
    description: StrictStr | None = None
    category: StrictStr | None = None
    phone: StrictStr | None = None
    address: StrictStr | None = None
    logoUrl: StrictStr | None = None


class PublicBusinessSettings(BackendModel):
    businessId: Identifier
    acceptsCash: StrictBool | None = None
    acceptsOnlinePayment: StrictBool | None = None
    deliveryEnabled: StrictBool | None = None
    pickupEnabled: StrictBool | None = None
    returnPolicy: StrictStr | None = None
    paymentNotes: StrictStr | None = None
    deliveryNotes: StrictStr | None = None
    deliveryFee: Price | None = None


class PublicWorkingHour(BackendModel):
    businessId: Identifier
    dayOfWeek: Literal['MONDAY','TUESDAY','WEDNESDAY','THURSDAY','FRIDAY','SATURDAY','SUNDAY']
    isOpen: StrictBool
    opensAt: Annotated[StrictStr, Field(pattern=r'^(?:[01]\d|2[0-3]):[0-5]\d$')] | None
    closesAt: Annotated[StrictStr, Field(pattern=r'^(?:[01]\d|2[0-3]):[0-5]\d$')] | None

    @model_validator(mode='after')
    def valid_schedule(self):
        if self.isOpen and (not self.opensAt or not self.closesAt or self.opensAt >= self.closesAt):
            raise ValueError('Invalid opening interval')
        if not self.isOpen and (self.opensAt is not None or self.closesAt is not None):
            raise ValueError('Closed day has opening interval')
        return self


class PublicBusinessFaq(BackendModel):
    id: Identifier
    question: Annotated[StrictStr, Field(min_length=1, max_length=300)]
    answer: Annotated[StrictStr, Field(min_length=1, max_length=3000)]
    position: Annotated[StrictInt, Field(ge=0)]
    businessId: Identifier | None = None
    isActive: StrictBool | None = None


class BusinessInformation(BackendModel):
    # Optional sections support the older name-only aggregate without inventing facts.
    business: BusinessSummary
    settings: PublicBusinessSettings | None = None
    workingHours: list[PublicWorkingHour] = Field(default_factory=list, max_length=7)
    faqs: list[PublicBusinessFaq] = Field(default_factory=list)

    @model_validator(mode='after')
    def consistent_sections(self):
        if self.settings is not None and self.settings.businessId != self.business.id:
            raise ValueError('Business scope mismatch')
        if any(day.businessId != self.business.id for day in self.workingHours):
            raise ValueError('Business scope mismatch')
        if len({day.dayOfWeek for day in self.workingHours}) != len(self.workingHours):
            raise ValueError('Duplicate weekday')
        if len({faq.id for faq in self.faqs}) != len(self.faqs):
            raise ValueError('Duplicate FAQ')
        if any(faq.isActive is False or (faq.businessId is not None and faq.businessId != self.business.id) for faq in self.faqs):
            raise ValueError('Unpublished or foreign FAQ')
        return self


class HistoryMessage(BackendModel):
    """Documented fields only; preserve order, role and raw timestamp text."""
    senderType: Literal["CUSTOMER", "AI", "OWNER"]
    id: StrictStr | None = None
    content: StrictStr | None = None
    messageType: StrictStr | None = None
    createdAt: StrictStr | None = None


MAX_HISTORY_CONTENT_CHARS = 2000


class ScopedHistoryMessage(BackendModel):
    """PR message DTO plus mandatory identity/type/time for secure reconstruction."""
    model_config = ConfigDict(extra="forbid",frozen=True)
    id: Identifier
    conversationId: Identifier
    senderType: Literal["CUSTOMER","AI","OWNER"]
    content: Annotated[StrictStr,Field(max_length=MAX_HISTORY_CONTENT_CHARS)] | None
    messageType: Literal["TEXT","IMAGE","AUDIO","VIDEO","DOCUMENT","LOCATION","OTHER"]
    createdAt: Annotated[StrictStr,Field(max_length=40)]
    externalMessageId: Annotated[StrictStr,Field(max_length=255)] | None = None


class PurchaseDraftItem(BackendModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    productId: Identifier
    variantId: Identifier
    quantity: Annotated[StrictInt, Field(ge=1, le=100)]


class PurchaseDraft(BackendModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    revision: SafeInteger
    sourceMessageId: Identifier | None
    items: list[PurchaseDraftItem] = Field(max_length=50)
    updatedAt: StrictStr | None
    updateMessageId: Identifier | None

    @model_validator(mode='after')
    def unique_items(self):
        if len({item.variantId for item in self.items}) != len(self.items):
            raise ValueError('Duplicate draft variant')
        if bool(self.updatedAt) != bool(self.updateMessageId):
            raise ValueError('Incomplete draft watermark')
        if self.items and self.sourceMessageId is None:
            raise ValueError('Draft source is missing')
        return self


class AuthorizedHistoryPage(BackendModel):
    """Proposed authenticated internal envelope; not the unauthenticated PR page."""
    model_config = ConfigDict(extra="forbid",frozen=True)
    businessId: Identifier
    customerId: Identifier
    conversationId: Identifier
    beforeMessageId: Identifier
    beforeMessageCreatedAt: Annotated[StrictStr,Field(max_length=40)]
    purchaseDraft: PurchaseDraft | None = None
    snapshot: Annotated[StrictStr,Field(min_length=1,max_length=128)]
    order: Literal["createdAt_desc_id_desc"]
    complete: StrictBool
    pagination: Pagination
    items: list[ScopedHistoryMessage] = Field(max_length=100)

    @model_validator(mode="after")
    def page_shape(self):
        if len(self.items) > self.pagination.limit or (self.pagination.hasMore and not self.items):
            raise ValueError("Invalid history page")
        return self


class ToolEnvelope(BackendModel, Generic[T]):
    tool: StrictStr
    data: T


class RetrievalStatus(str, Enum):
    AVAILABLE = "available"
    EMPTY = "empty"
    PARTIAL = "partial"
    INVALID = "invalid"
    UNAVAILABLE = "unavailable"
    AUTHORIZATION_FAILED = "authorization_failed"
    SCOPE_MISMATCH = "scope_mismatch"


class RetrievalOutcome(BackendModel, Generic[T]):
    status: RetrievalStatus
    envelope: ToolEnvelope[T] | None = Field(default=None, repr=False)
    error: Literal["invalid_response", "invalid_arguments", "timeout", "backend_error",
                   "authorization_failed", "scope_mismatch"] | None = None

    @model_validator(mode="after")
    def consistent(self):
        success = self.status in {RetrievalStatus.AVAILABLE, RetrievalStatus.EMPTY, RetrievalStatus.PARTIAL}
        if success != (self.envelope is not None) or success != (self.error is None):
            raise ValueError("Retrieval outcome must contain evidence or a safe error")
        return self
