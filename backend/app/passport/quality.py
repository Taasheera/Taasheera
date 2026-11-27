"""Fast, conservative image checks before sending a passport photo to OCR."""
from PIL import Image, ImageFilter, ImageStat

from .images import InvalidImageError


def centre_sample(image: Image.Image) -> Image.Image:
    sample = image.convert("L")
    sample.thumbnail((500, 500))
    w, h = sample.size
    return sample.crop((w // 10, h // 10, w - w // 10, h - h // 10))


def edge_strength(crop: Image.Image) -> float:
    edges = crop.filter(ImageFilter.FIND_EDGES).crop((1, 1, crop.width - 1, crop.height - 1))
    return ImageStat.Stat(edges).stddev[0]


def validate_scan(image: Image.Image) -> None:
    if min(image.size) < 360 or max(image.size) < 600:
        raise InvalidImageError("This photo is too small to read. Retake it at full resolution with all four corners visible.", 422)

    if max(image.size) / min(image.size) > 3.4:
        raise InvalidImageError("The passport page may be cropped. Retake the photo with the whole page in view.", 422)

    # Measure soft edges in the center, where the passport should dominate the photo.
    crop = centre_sample(image)
    if edge_strength(crop) < 7:
        highlights = sum(crop.histogram()[250:]) / (crop.width * crop.height)
        if highlights > 0.65:
            raise InvalidImageError("The details are washed out. Retake the photo in even light without flash or glare.", 422)
        raise InvalidImageError("The details look blurry or unreadable. Hold the camera steady and focus on the passport page.", 422)
