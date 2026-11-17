import base64
import json
import os
from io import BytesIO
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from PIL import Image, ImageOps, UnidentifiedImageError

from .openrouter_api import openrouter_config


OPENROUTER_IMAGE_API_URL = 'https://openrouter.ai/api/v1/images'
OPENROUTER_DEFAULT_IMAGE_MODEL = 'bytedance-seed/seedream-5-5'
MAX_REFERENCE_ITEMS = 3
MAX_IMAGE_RESPONSE_BYTES = 12 * 1024 * 1024


class OutfitImageGenerationError(Exception):
    def __init__(self, message, retryable=False, status_code=502):
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code


def generate_mens_outfit_image(
    items,
    *,
    occasion='',
    season='',
    style='',
    timeout=55,
):
    api_key, _ = openrouter_config()
    model = os.environ.get(
        'OPENROUTER_IMAGE_MODEL',
        OPENROUTER_DEFAULT_IMAGE_MODEL,
    ).strip()
    if not api_key:
        raise OutfitImageGenerationError(
            'AI image generation is not configured. Add an OPENROUTER_API_KEY.',
            status_code=503,
        )
    if not model:
        raise OutfitImageGenerationError(
            'AI image generation has no model configured. Set OPENROUTER_IMAGE_MODEL.',
            status_code=503,
        )

    reference_items = list(items)[:MAX_REFERENCE_ITEMS]
    if not reference_items:
        raise OutfitImageGenerationError(
            'Select at least one wardrobe piece before generating an outfit image.',
            status_code=400,
        )

    references = []
    descriptions = []
    for index, item in enumerate(reference_items, start=1):
        image_bytes = _optimized_reference(item)
        references.append(
            {
                'type': 'image_url',
                'image_url': {
                    'url': (
                        'data:image/jpeg;base64,'
                        f'{base64.b64encode(image_bytes).decode("ascii")}'
                    )
                },
            }
        )
        descriptions.append(
            f'Piece {index}: {item.get_category_display()}, '
            f'{item.primary_color or "unspecified color"}'
            + (
                f', {item.get_pattern_display()} pattern'
                if item.pattern
                else ''
            )
        )

    prompt = (
        'Create one photorealistic men’s fashion editorial image. Show one '
        'adult male model wearing a complete, tasteful men’s outfit. The '
        'reference photos show garments from the user’s wardrobe; preserve '
        'their visible colors, patterns, and recognizable design, especially '
        'the first piece. Use all reference pieces together when they are '
        'compatible, and add suitable men’s basics only to complete the look. '
        'Do not show women, multiple people, text, logos, collages, or a '
        'fashion mood board. Show the model full-length against a simple '
        'neutral background.'
        f'\nWardrobe pieces: {"; ".join(descriptions)}.'
        f'\nOccasion: {occasion or "Everyday"}.'
        f'\nSeason: {season or "Any season"}.'
        f'\nPreferred style: {style or "Any style"}.'
    )
    payload = {
        'model': model,
        'prompt': prompt,
        'input_references': references,
        'n': 1,
        'resolution': '1K',
        'aspect_ratio': '3:4',
        'output_format': 'jpeg',
        'provider': {'data_collection': 'deny'},
    }
    request = Request(
        OPENROUTER_IMAGE_API_URL,
        data=json.dumps(payload).encode('utf-8'),
        headers={
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
            'X-OpenRouter-Title': 'Wardrobe',
        },
        method='POST',
    )

    try:
        with urlopen(request, timeout=timeout) as response:
            response_data = json.loads(response.read().decode('utf-8'))
    except HTTPError as error:
        detail = _provider_error_detail(error)
        if error.code == 402:
            raise OutfitImageGenerationError(
                'OpenRouter needs more credits to generate this image.'
                + (f' Details: {detail}' if detail else ''),
                status_code=402,
            ) from error
        if error.code == 429:
            raise OutfitImageGenerationError(
                'OpenRouter is rate-limiting image generation. Try again shortly.'
                + (f' Details: {detail}' if detail else ''),
                retryable=True,
                status_code=429,
            ) from error
        if error.code >= 500:
            raise OutfitImageGenerationError(
                'OpenRouter image generation is temporarily unavailable.'
                + (f' Details: {detail}' if detail else ''),
                retryable=True,
                status_code=503,
            ) from error
        if error.code in (401, 403):
            raise OutfitImageGenerationError(
                'OpenRouter rejected image generation. Check the API key and '
                'OPENROUTER_IMAGE_MODEL.'
                + (f' Details: {detail}' if detail else ''),
                status_code=error.code,
            ) from error
        raise OutfitImageGenerationError(
            f'OpenRouter image generation failed with status {error.code}.'
            + (f' Details: {detail}' if detail else ''),
            status_code=error.code,
        ) from error
    except (URLError, TimeoutError) as error:
        reason = getattr(error, 'reason', error)
        detail = ' '.join(str(reason).split())[:200]
        raise OutfitImageGenerationError(
            'OpenRouter image generation could not be reached'
            + (f' ({detail})' if detail else '')
            + '. Try again shortly.',
            retryable=True,
            status_code=503,
        ) from error
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise OutfitImageGenerationError(
            'OpenRouter returned an unreadable image response.'
        ) from error

    try:
        image = response_data['data'][0]
        image_base64 = image['b64_json']
        media_type = image.get('media_type', 'image/jpeg')
    except (KeyError, IndexError, TypeError) as error:
        raise OutfitImageGenerationError(
            'OpenRouter returned an image in an unexpected format.'
        ) from error

    if (
        not isinstance(image_base64, str)
        or len(image_base64) > MAX_IMAGE_RESPONSE_BYTES * 4 // 3
        or media_type not in {'image/jpeg', 'image/png', 'image/webp'}
    ):
        raise OutfitImageGenerationError(
            'OpenRouter returned an unsupported image format.'
        )
    try:
        base64.b64decode(image_base64, validate=True)
    except (ValueError, base64.binascii.Error) as error:
        raise OutfitImageGenerationError(
            'OpenRouter returned a corrupted image.'
        ) from error
    return f'data:{media_type};base64,{image_base64}'


def _optimized_reference(item):
    try:
        with item.image.open('rb') as image_file:
            image_bytes = image_file.read()
        with Image.open(BytesIO(image_bytes)) as source:
            image = ImageOps.exif_transpose(source).convert('RGB')
            image.thumbnail((768, 768), Image.Resampling.LANCZOS)
            output = BytesIO()
            image.save(output, format='JPEG', quality=75, optimize=True)
    except (OSError, ValueError, UnidentifiedImageError) as error:
        raise OutfitImageGenerationError(
            'A selected wardrobe photo could not be prepared for image generation.'
        ) from error
    return output.getvalue()


def _provider_error_detail(error):
    try:
        payload = json.loads(error.read().decode('utf-8', errors='replace'))
        if not isinstance(payload, dict):
            return ''
        provider_error = payload.get('error')
        if isinstance(provider_error, dict):
            details = [
                str(provider_error[key])
                for key in ('message', 'type', 'code')
                if provider_error.get(key) not in (None, '')
            ]
        elif isinstance(provider_error, str):
            details = [provider_error]
        else:
            details = []
        return ' '.join(' '.join(details).split())[:400]
    except (AttributeError, json.JSONDecodeError, TypeError, UnicodeDecodeError):
        return ''
