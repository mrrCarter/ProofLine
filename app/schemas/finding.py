from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from enum import Enum

class FindingStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    UNREADABLE = "UNREADABLE"

class FindingSeverity(str, Enum):
    INFO = "INFO"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"

class Evidence(BaseModel):
    text: Optional[str] = None
    bbox: Optional[List[float]] = None
    cropUri: Optional[str] = None
    provider: Optional[str] = None

class Finding(BaseModel):
    ruleId: str
    severity: FindingSeverity
    status: FindingStatus
    expected: Optional[Any] = None
    observed: Optional[Any] = None
    confidence: float = Field(..., ge=0.0, le=1.0)
    evidence: Optional[Evidence] = None
    explanation: str
    remediation: Optional[str] = None
