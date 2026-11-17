import json
import mimetypes

from ..models import ClothingItem
from .gemini_api import GeminiAPIError, gemini_config, generate_content


class ImageAnalysisError(Exception):
    def __init__(self, message, retryable=False, retry_after=None):
        super().__init__(message)
        self.retryable = retryable
        self.retry_after = retry_after


def is_configured():
    return bool(gemini_config()[0])


def provider_name():
    return 'Gemini' if is_configured() else 'AI'


def analyze_clothing(image_file):
    api_key = gemini_config()[0]
    if not api_key:
        raise ImageAnalysisError(
            'AI analysis is not configured. Add a GEMINI_API_KEY to the environment.'
        )

    image_file.seek(0)
    image_bytes = image_file.read()
    image_file.seek(0)
    mime_type = mimetypes.guess_type(image_file.name)[0] or 'image/jpeg'
    prompt = (
        'Identify the single main clothing item in this photo. '
        'Return only a JSON object with fields: name, category, '
        'primary_color, secondary_color, pattern, style, fit, '
        'occasion, season, material. category must be one of shirt, '
        't-shirt, jeans, trousers, jacket, shoes, hoodie, sweater, '
        'dress, skirt, shorts, other. pattern must be plain, striped, '
        'checked, printed, textured, other, or an empty string. '
        'style must be casual, formal, streetwear, traditional, '
        'sporty, other, or an empty string. fit must be slim, regular, '
        'relaxed, oversized, baggy, other, or an empty string. '
        'occasion and season must be arrays of short strings. Use an '
        'empty string or empty array when uncertain. Do not infer '
        'hidden details. Only identify material when visually apparent.'
    )

    try:
        content = generate_content(
            prompt,
            image_bytes=image_bytes,
            mime_type=mime_type,
            timeout=45,
        )
    except GeminiAPIError as error:
        raise ImageAnalysisError(
            str(error),
            retryable=error.retryable,
            retry_after=error.retry_after,
        ) from error

    return _parse_analysis(content)


def _parse_analysis(content):
    try:
        data = json.loads(content)
    except (TypeError, json.JSONDecodeError) as error:
        raise ImageAnalysisError(
            'Gemini returned clothing details in an unexpected format.'
        ) from error

    if not isinstance(data, dict):
        raise ImageAnalysisError(
            'Gemini returned clothing details in an unexpected format.'
        )

    name = _text_value(data, 'name', required=True, max_length=100)
    primary_color = _text_value(
        data,
        'primary_color',
        required=True,
        max_length=40,
    )
    return {
        'name': name,
        'category': _choice_value(
            data.get('category'),
            dict(ClothingItem.Category.choices),
            fallback='other',
        ),
        'primary_color': primary_color,
        'secondary_color': _text_value(
            data,
            'secondary_color',
            max_length=40,
        ),
        'pattern': _choice_value(
            data.get('pattern'),
            {
                'plain': 'Plain',
                'striped': 'Striped',
                'checked': 'Checked',
                'printed': 'Printed',
                'textured': 'Textured',
                'other': 'Other',
                '': '',
            },
            fallback='other',
        ),
        'style': _choice_value(
            data.get('style'),
            {
                'casual': 'Casual',
                'formal': 'Formal',
                'streetwear': 'Streetwear',
                'traditional': 'Traditional',
                'sporty': 'Sporty',
                'other': 'Other',
                '': '',
            },
            fallback='other',
        ),
        'fit': _choice_value(
            data.get('fit'),
            {
                **dict(ClothingItem.Fit.choices),
                '': '',
            },
            fallback='',
        ),
        'occasion': _list_value(data, 'occasion', max_length=160),
        'season': _list_value(data, 'season', max_length=80),
        'material': _text_value(data, 'material', max_length=80),
    }


def _choice_value(value, choices, fallback):
    if not isinstance(value, str):
        return fallback
    normalized = value.strip().lower().replace('_', '-')
    for key, label in choices.items():
        if normalized in (key, label.lower()):
            return key
    return fallback


def _text_value(data, key, required=False, max_length=100):
    value = data.get(key, '')
    if not isinstance(value, str):
        raise ImageAnalysisError(
            f'Gemini returned an invalid {key} value.'
        )
    value = value.strip()
    if required and not value:
        raise ImageAnalysisError(
            f'Gemini could not identify {key.replace("_", " ")}.'
        )
    return value[:max_length]


def _list_value(data, key, max_length):
    values = data.get(key, [])
    if isinstance(values, str):
        values = [value.strip() for value in values.split(',')]
    if not isinstance(values, list) or not all(
        isinstance(value, str) for value in values
    ):
        raise ImageAnalysisError(
            f'Gemini returned an invalid {key} value.'
        )
    return ', '.join(dict.fromkeys(value.strip() for value in values if value.strip()))[
        :max_length
    ]
