import base64
import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


GEMINI_API_URL = 'https://generativelanguage.googleapis.com/v1beta/models'
GEMINI_DEFAULT_MODEL = 'gemini-3.8-flash'


class GeminiAPIError(Exception):
    def __init__(self, message, retryable=False, retry_after=None):
        super().__init__(message)
        self.retryable = retryable
        self.retry_after = retry_after


def gemini_config():
    return (
        os.environ.get('GEMINI_API_KEY', '').strip(),
        os.environ.get('GEMINI_MODEL', GEMINI_DEFAULT_MODEL).strip(),
    )


def generate_content(
    prompt,
    *,
    system_instruction='',
    image_bytes=None,
    mime_type=None,
    timeout=45,
):
    api_key, model = gemini_config()
    if not api_key:
        raise GeminiAPIError(
            'AI analysis is not configured. Add a GEMINI_API_KEY to the environment.'
        )
    if not model:
        raise GeminiAPIError(
            'Gemini is not configured with a model. Set GEMINI_MODEL.'
        )

    parts = [{'text': prompt}]
    if image_bytes is not None:
        parts.append(
            {
                'inlineData': {
                    'mimeType': mime_type or 'application/octet-stream',
                    'data': base64.b64encode(image_bytes).decode('ascii'),
                }
            }
        )
    payload = {
        'contents': [{'role': 'user', 'parts': parts}],
        'generationConfig': {'responseMimeType': 'application/json'},
    }
    if system_instruction:
        payload['systemInstruction'] = {'parts': [{'text': system_instruction}]}

    request = Request(
        f'{GEMINI_API_URL}/{quote(model, safe="")}:generateContent',
        data=json.dumps(payload).encode('utf-8'),
        headers={
            'x-goog-api-key': api_key,
            'Content-Type': 'application/json',
        },
        method='POST',
    )

    try:
        with urlopen(request, timeout=timeout) as response:
            response_data = json.loads(response.read().decode('utf-8'))
    except HTTPError as error:
        detail = _provider_error_detail(error)
        if error.code == 429:
            retry_after = None
            if error.headers:
                try:
                    retry_after = float(error.headers.get('Retry-After'))
                except (TypeError, ValueError):
                    pass
            raise GeminiAPIError(
                'Gemini is rate-limited. Please try again shortly.'
                + (f' Details: {detail}' if detail else ''),
                retryable=True,
                retry_after=retry_after,
            ) from error
        if error.code >= 500:
            raise GeminiAPIError(
                'Gemini is temporarily unavailable. Please try again.'
                + (f' Details: {detail}' if detail else ''),
                retryable=True,
            ) from error
        if error.code in (401, 403):
            raise GeminiAPIError(
                f'Gemini rejected the request ({error.code}). Check '
                'GEMINI_API_KEY and Google AI API access.'
                + (f' Details: {detail}' if detail else '')
            ) from error
        raise GeminiAPIError(
            f'Gemini request failed with status {error.code}.'
            + (f' Details: {detail}' if detail else '')
        ) from error
    except (URLError, TimeoutError) as error:
        reason = getattr(error, 'reason', error)
        detail = ' '.join(str(reason).split())[:200]
        raise GeminiAPIError(
            'Gemini could not be reached'
            + (f' ({detail})' if detail else '')
            + '. Check the connection and try again.',
            retryable=True,
        ) from error
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise GeminiAPIError(
            'Gemini returned an unreadable response.'
        ) from error

    return _response_text(response_data)


def _provider_error_detail(error):
    try:
        payload = json.loads(error.read().decode('utf-8', errors='replace'))
        if not isinstance(payload, dict):
            return ''
        provider_error = payload.get('error')
        if isinstance(provider_error, dict):
            details = [
                str(provider_error[key])
                for key in ('message', 'status', 'code')
                if provider_error.get(key) not in (None, '')
            ]
        elif isinstance(provider_error, str):
            details = [provider_error]
        else:
            details = []
        detail = ' '.join(details)
        headers = getattr(error, 'headers', None)
        request_id = (
            headers.get('x-goog-request-id') or headers.get('x-request-id')
            if headers
            else None
        )
        if request_id:
            detail = f'{detail} request_id={request_id}'.strip()
        return ' '.join(detail.split())[:500]
    except (AttributeError, json.JSONDecodeError, TypeError, UnicodeDecodeError):
        return ''


def _response_text(response_data):
    if not isinstance(response_data, dict):
        raise GeminiAPIError(
            'Gemini returned content in an unexpected format.'
        )
    try:
        parts = response_data['candidates'][0]['content']['parts']
    except (KeyError, IndexError, TypeError) as error:
        feedback = response_data.get('promptFeedback', {})
        reason = feedback.get('blockReason') if isinstance(feedback, dict) else None
        if reason:
            raise GeminiAPIError(f'Gemini blocked the request ({reason}).') from error
        raise GeminiAPIError(
            'Gemini returned content in an unexpected format.'
        ) from error

    text = '\n'.join(
        part['text']
        for part in parts
        if isinstance(part, dict) and isinstance(part.get('text'), str)
    )
    if not text.strip():
        raise GeminiAPIError('Gemini returned no text for this request.')
    return text
