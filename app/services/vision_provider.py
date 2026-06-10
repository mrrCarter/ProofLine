from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field

class BoundingBox(BaseModel):
    """
    Represented as 4 vertices: [[x1, y1], [x2, y2], [x3, y3], [x4, y4]]
    Typical for OCR engines like PaddleOCR.
    """
    vertices: List[List[float]]

class OCRResult(BaseModel):
    text: str
    confidence: float
    bbox: BoundingBox

class VisionResponse(BaseModel):
    results: List[OCRResult]
    readability_score: float
    metadata: Dict[str, Any] = Field(default_factory=dict)

class VisionProvider(ABC):
    @abstractmethod
    async def process_image(
        self,
        image_bytes: bytes,
        artifact_hash: Optional[str] = None,
    ) -> VisionResponse:
        """
        Processes an image and returns OCR results.

        :param image_bytes: The raw image data.
        :param artifact_hash: Optional SHA256 hash of the image for caching or mock lookup.
        :return: VisionResponse containing OCR results and metadata.
        """
        pass
