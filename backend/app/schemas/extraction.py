"""Structured schemas used by Gemini extraction and financial validation.

The source documents can contain many different labels, so extraction is intentionally
field-name agnostic: Gemini returns a list of grounded fields and we convert it into
the API's key-value object afterwards. This lets us capture *all* visible fields while
still using Pydantic to validate the LLM response.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class Evidence(BaseModel):
    source_text: Optional[str] = None
    page_number: Optional[int] = Field(default=None, ge=1, le=3)


class ExtractedField(BaseModel):
    name: str = Field(..., description="snake_case field name")
    value: Any = None
    evidence: Evidence = Field(default_factory=Evidence)


class LineItem(BaseModel):
    description: Optional[str] = None
    quantity: Optional[float] = None
    unit_price: Optional[float] = None
    amount: Optional[float] = None
    tax_amount: Optional[float] = None
    discount: Optional[float] = None
    evidence: Evidence = Field(default_factory=Evidence)


class TableRow(BaseModel):
    label: Optional[str] = None
    values: dict[str, Any] = Field(default_factory=dict)
    evidence: Evidence = Field(default_factory=Evidence)


class ExtractedTable(BaseModel):
    name: str
    columns: list[str] = Field(default_factory=list)
    rows: list[TableRow] = Field(default_factory=list)


class GeminiExtraction(BaseModel):
    fields: list[ExtractedField] = Field(default_factory=list)
    line_items: list[LineItem] = Field(default_factory=list)
    tables: list[ExtractedTable] = Field(default_factory=list)


ValidationStatus = Literal["PASS", "FAIL", "NOT_APPLICABLE"]


class ValidationCheck(BaseModel):
    name: str
    formula: str
    operands: dict[str, Any] = Field(default_factory=dict)
    calculated_value: Optional[float] = None
    reported_value: Optional[float] = None
    variance: Optional[float] = None
    status: ValidationStatus


class FinancialValidationResult(BaseModel):
    checks: list[ValidationCheck] = Field(default_factory=list)
    overall_status: ValidationStatus = "NOT_APPLICABLE"
    issues: list[str] = Field(default_factory=list)
