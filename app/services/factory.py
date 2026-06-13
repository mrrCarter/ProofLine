import os
from .mock_vision import MockVisionProvider
from .vision_provider import VisionProvider


def get_vision_provider() -> VisionProvider:
    """
    Factory function to get the configured VisionProvider.
    Respects environment variables to choose between Mock and Real providers.
    """
    provider_type = os.getenv("VISION_PROVIDER", "mock").strip().lower().replace("-", "_")

    if provider_type == "mock":
        return MockVisionProvider()

    if provider_type == "local":
        from .local_vision import LocalVisionProvider

        return LocalVisionProvider()

    if provider_type in {"rapid", "rapid_ocr", "rapidocr"}:
        from .rapid_vision import RapidVisionProvider

        return RapidVisionProvider()

    return MockVisionProvider()
