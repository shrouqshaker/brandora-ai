"""Canonical Backend suggestion DTO; legacy E0-3 v1 contracts stay unchanged."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, model_validator, model_serializer
from .backend import Identifier
from .response import E03Intent

class SuggestedItem(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    productId: Identifier
    variantId: Identifier
    quantity: StrictInt = Field(ge=1, le=100)

class SuggestedOrder(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    items: list[SuggestedItem] = Field(max_length=50)
    abandoned: StrictBool = False
    discountRequest: StrictStr | None = Field(default=None, max_length=500)

    @model_serializer(mode='wrap')
    def wire(self, handler):
        result = handler(self)
        if self.discountRequest is None:
            result.pop('discountRequest', None)
        if not self.abandoned:
            result.pop('abandoned', None)
        return result

    @model_validator(mode='after')
    def coherent(self):
        if len({item.variantId for item in self.items}) != len(self.items):
            raise ValueError('Duplicate variant')
        if self.abandoned and self.items:
            raise ValueError('Abandoned suggestion has items')
        return self

class CanonicalAiResponse(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    intent: E03Intent
    reply: StrictStr = Field(min_length=1, max_length=20000)
    requiresReview: StrictBool
    orderCandidate: SuggestedOrder | None

    @model_validator(mode='after')
    def read_only(self):
        if self.intent == E03Intent.CONFIRMED_ORDER:
            raise ValueError('The AI cannot confirm orders')
        return self
