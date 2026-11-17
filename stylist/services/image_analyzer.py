import base64
import json
import mimetypes
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ..models import ClothingItem


class ImageAnalysisError(Exception):
    def __init__(self, message, retryable=False, retry_after=None):
        super().__init__(message)
        self.retryable = retryable
        self.retry_after = retry_after


def _provider_error_detail(error):
    """Extract a short provider explanation without exposing request secrets."""
    try:
        payload = json.loads(error.read().decode('utf-8', errors='replace'))
        if not isinstance(payload, dict):
            return ''
        provider_error = payload.get('error')
        if isinstance(provider_error, dict):
            details = [
                provider_error.get(key)
                for key in ('message', 'type', 'code')
                if isinstance(provider_error.get(key), str)
            ]
        elif isinstance(provider_error, str):
            details = [provider_error]
        else:
            details = [
                payload.get('message')
                if isinstance(payload.get('message'), str)
                else ''
            ]
        detail = ' '.join(value.strip() for value in details if value.strip())
        headers = getattr(error, 'headers', None)
        request_id = (
            headers.get('x-groq-request-id')
            or headers.get('x-request-id')
            if headers
            else None
        )
        if request_id:
            details.append(f'request_id={request_id}')
            detail = ' '.join(value.strip() for value in details if value.strip())
        if detail:
            return ' '.join(detail.split())[:500]
    except (AttributeError, json.JSONDecodeError, TypeError, UnicodeDecodeError):
        pass
    return ''


GROQ_API_URL = 'https://api.groq.com/openai/v1/chat/completions'
GROQ_DEFAULT_MODEL = 'qwen/qwen3.8-27b'


def is_configured():
    return bool(os.environ.get('GROQ_API_KEY', '').strip())


def provider_name():
    if os.environ.get('GROQ_API_KEY', '').strip():
        return 'Groq'
    return 'AI'


def analyze_clothing(image_file):
    api_key, api_url, model = groq_config()
    if not api_key:
        raise ImageAnalysisError(
            'AI analysis is not configured. Add a GROQ_API_KEY to the environment.'
        )

    image_file.seek(0)
    encoded_image = base64.b64encode(image_file.read()).decode('ascii')
    image_file.seek(0)
    mime_type = mimetypes.guess_type(image_file.name)[0] or 'image/jpeg'
    data_url = f'data:{mime_type};base64,{encoded_image}'
    payload = {
        'model': model,
        'temperature': 0,
        'response_format': {'type': 'json_object'},
        'messages': [
            {
                'role': 'user',
                'content': [
                    {
                        'type': 'text',
                        'text': (
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
                        ),
                    },
                    {
                        'type': 'image_url',
                        'image_url': {'url': data_url},
                    },
                ],
            }
        ],
    }
    request = Request(
        api_url,
        data=json.dumps(payload).encode('utf-8'),
        headers={
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
        },
        method='POST',
    )

    try:
        with urlopen(request, timeout=45) as response:
            response_data = json.loads(response.read().decode('utf-8'))
    except HTTPError as error:
        detail = _provider_error_detail(error)
        if error.code == 401:
            raise ImageAnalysisError(
                'The vision API rejected the configured API key.'
                + (f' Details: {detail}' if detail else '')
            ) from error
        if error.code == 429:
            retry_after = None
            if error.headers:
                try:
                    retry_after = float(error.headers.get('Retry-After'))
                except (TypeError, ValueError):
                    pass
            raise ImageAnalysisError(
                'The vision API is rate-limited. Please try again shortly.',
                retryable=True,
                retry_after=retry_after,
            ) from error
        if error.code >= 500:
            raise ImageAnalysisError(
                'The vision API is temporarily unavailable. Please try again.',
                retryable=True,
            ) from error
        if error.code == 403:
            raise ImageAnalysisError(
                f'Groq denied access to model "{model}" (403). '
                'Verify that GROQ_API_KEY in the Vercel Production environment '
                'has permission to use GROQ_VISION_MODEL, then redeploy. '
                'This is a provider permission error, not an image upload or '
                'storage failure.'
                + (f' Details: {detail}' if detail else '')
            ) from error
        raise ImageAnalysisError(
            f'The vision API request failed with status {error.code}.'
            + (f' Details: {detail}' if detail else '')
        ) from error
    except (URLError, TimeoutError) as error:
        raise ImageAnalysisError(
            'The vision API could not be reached. Check the connection and try again.',
            retryable=True,
        ) from error
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ImageAnalysisError(
            'The vision API returned an unreadable response.'
        ) from error

    return _parse_analysis(response_data)


def groq_config():
    return (
        os.environ.get('GROQ_API_KEY', '').strip(),
        os.environ.get('GROQ_API_URL', GROQ_API_URL),
        os.environ.get('GROQ_VISION_MODEL', GROQ_DEFAULT_MODEL),
    )


def _parse_analysis(response_data):
    try:
        content = response_data['choices'][0]['message']['content']
        data = json.loads(content)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
        raise ImageAnalysisError(
            'The vision API returned clothing details in an unexpected format.'
        ) from error

    if not isinstance(data, dict):
        raise ImageAnalysisError(
            'The vision API returned clothing details in an unexpected format.'
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
            f'The vision API returned an invalid {key} value.'
        )
    value = value.strip()
    if required and not value:
        raise ImageAnalysisError(
            f'The vision API could not identify {key.replace("_", " ")}.'
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
            f'The vision API returned an invalid {key} value.'
        )
    return ', '.join(dict.fromkeys(value.strip() for value in values if value.strip()))[
        :max_length
    ]
