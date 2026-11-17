from django.core.exceptions import ValidationError
from django.db.models.fields.files import FieldFile
from PIL import Image, UnidentifiedImageError


MAX_IMAGE_SIZE = 4 * 1024 * 1024
MAX_IMAGE_PIXELS = 25_000_000
ALLOWED_IMAGE_FORMATS = {'JPEG', 'PNG', 'WEBP'}


def validate_clothing_image(upload):
    if isinstance(upload, FieldFile) and upload._committed:
        return
    if upload.size > MAX_IMAGE_SIZE:
        raise ValidationError('Images must be 4 MB or smaller after optimization.')

    try:
        upload.seek(0)
        with Image.open(upload) as image:
            if image.format not in ALLOWED_IMAGE_FORMATS:
                raise ValidationError(
                    'Use a JPEG, PNG, or WebP image.'
                )
            if image.width * image.height > MAX_IMAGE_PIXELS:
                raise ValidationError(
                    'Image dimensions must be 25 megapixels or smaller.'
                )
            image.verify()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise ValidationError('Upload a valid JPEG, PNG, or WebP image.') from error
    finally:
        upload.seek(0)
