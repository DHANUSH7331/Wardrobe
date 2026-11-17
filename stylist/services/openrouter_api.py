import base64
import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


OPENROUTER_API_URL = 'https://openrouter.ai/api/v1/chat/completions'
OPENROUTER_DEFAULT_MODEL = 'qwen/qwen3.7-flash'


class OpenRouterAPIError(Exception):
    def __init__(self, message, retryable=False, retry_after=None):
        super().__init__(message)
        self.retryable = retryable
        self.retry_after = retry_after


def openrouter_config():
    return (
        os.environ.get('OPENROUTER_API_KEY', '').strip(),
        os.environ.get('OPENROUTER_MODEL', OPENROUTER_DEFAULT_MODEL).strip(),
    )


def generate_content(
    prompt,
    *,
    system_instruction='',
    image_bytes=None,
    mime_type=None,
    timeout=45,
):
    api_key, model = openrouter_config()
    if not api_key:
        raise OpenRouterAPIError(
            'AI analysis is not configured. Add an OPENROUTER_API_KEY to the environment.'
        )
    if not model:
        raise OpenRouterAPIError(
            'OpenRouter is not configured with a model. Set OPENROUTER_MODEL.'
        )

    messages = []
    if system_instruction:
        messages.append({'role': 'system', 'content': system_instruction})

    if image_bytes is None:
        user_content = prompt
    else:
        data_url = (
            f'data:{mime_type or "application/octet-stream"};base64,'
            f'{base64.b64encode(image_bytes).decode("ascii")}'
        )
        user_content = [
            {'type': 'text', 'text': prompt},
            {'type': 'image_url', 'image_url': {'url': data_url}},
        ]
    messages.append({'role': 'user', 'content': user_content})

    payload = {
        'model': model,
        'messages': messages,
        'response_format': {'type': 'json_object'},
    }
    request = Request(
        OPENROUTER_API_URL,
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
        if error.code == 429:
            retry_after = None
            if error.headers:
                try:
                    retry_after = float(error.headers.get('Retry-After'))
                except (TypeError, ValueError):
                    pass
            raise OpenRouterAPIError(
                'OpenRouter is rate-limited. Please try again shortly.'
                + (f' Details: {detail}' if detail else ''),
                retryable=True,
                retry_after=retry_after,
            ) from error
        if error.code == 402:
            raise OpenRouterAPIError(
                'OpenRouter has insufficient credits. Add credits to your '
                'OpenRouter account, then retry.'
                + (f' Details: {detail}' if detail else '')
            ) from error
        if error.code >= 500:
            raise OpenRouterAPIError(
                'OpenRouter is temporarily unavailable. Please try again.'
                + (f' Details: {detail}' if detail else ''),
                retryable=True,
            ) from error
        if error.code in (401, 403):
            raise OpenRouterAPIError(
                f'OpenRouter rejected the request ({error.code}). Check '
                'OPENROUTER_API_KEY, model availability, and API access.'
                + (f' Details: {detail}' if detail else '')
            ) from error
        raise OpenRouterAPIError(
            f'OpenRouter request failed with status {error.code}.'
            + (f' Details: {detail}' if detail else '')
        ) from error
    except (URLError, TimeoutError) as error:
        reason = getattr(error, 'reason', error)
        detail = ' '.join(str(reason).split())[:200]
        raise OpenRouterAPIError(
            'OpenRouter could not be reached'
            + (f' ({detail})' if detail else '')
            + '. Check the connection and try again.',
            retryable=True,
        ) from error
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise OpenRouterAPIError('OpenRouter returned an unreadable response.') from error

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
                for key in ('message', 'type', 'code')
                if provider_error.get(key) not in (None, '')
            ]
        elif isinstance(provider_error, str):
            details = [provider_error]
        else:
            details = []
        detail = ' '.join(details)
        headers = getattr(error, 'headers', None)
        request_id = (
            headers.get('x-openrouter-trace-id') or headers.get('x-request-id')
            if headers
            else None
        )
        if request_id:
            detail = f'{detail} request_id={request_id}'.strip()
        return ' '.join(detail.split())[:500]
    except (AttributeError, json.JSONDecodeError, TypeError, UnicodeDecodeError):
        return ''


def _response_text(response_data):
    try:
        message = response_data['choices'][0]['message']
    except (KeyError, IndexError, TypeError) as error:
        raise OpenRouterAPIError(
            'OpenRouter returned content in an unexpected format.'
        ) from error

    refusal = message.get('refusal')
    if refusal:
        raise OpenRouterAPIError(f'OpenRouter declined the request: {refusal}')

    content = message.get('content')
    if isinstance(content, str) and content.strip():
        return content
    if isinstance(content, list):
        text = '\n'.join(
            part['text']
            for part in content
            if isinstance(part, dict) and isinstance(part.get('text'), str)
        )
        if text.strip():
            return text
    raise OpenRouterAPIError('OpenRouter returned no text for this request.')
