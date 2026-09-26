"""Reporting preferences (nothing is calculated, scheduled or delivered here)."""

from enum import StrEnum

from pydantic import BaseModel, Field, StrictBool, StrictInt

from app.commerce.domain.common import DOMAIN_MODEL_CONFIG, NonEmptyStr

MAX_HIGHLIGHTS_LIMIT = 100


class ReportPeriod(StrEnum):
    TODAY = "today"
    YESTERDAY = "yesterday"
    LAST_7_DAYS = "last_7_days"
    LAST_30_DAYS = "last_30_days"


class ReportingConfig(BaseModel):
    model_config = DOMAIN_MODEL_CONFIG

    # IANA name expected (e.g. "UTC"); validated as non-empty only, like Store.timezone,
    # so configuration does not depend on the host's timezone database.
    timezone: NonEmptyStr
    default_period: ReportPeriod = ReportPeriod.YESTERDAY
    include_comparison: StrictBool = True
    max_highlights: StrictInt = Field(default=5, ge=1, le=MAX_HIGHLIGHTS_LIMIT)
