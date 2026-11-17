import base64
import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


OPENAI_API_URL = 'https://api.openai.com/v1/chat/completions'
OPENAI_DEFAULT_MODEL = 'gpt-5.4-mini'


class OpenAIAPIError(Exception):
    def __init__(self, message, retryable=False, retry_after=None):
        super().__init__(message)
        self.retryable = retryable
        self.retry_after = retry_after


def openai_config():
    return (
        os.environ.get('OPENAI_API_KEY', '').strip(),
        os.environ.get('OPENAI_MODEL', OPENAI_DEFAULT_MODEL).strip(),
    )


def generate_content(
    prompt,
    *,
    system_instruction='',
    image_bytes=None,
    mime_type=None,
    timeout=45,
):
    api_key, model = openai_config()
    if not api_key:
        raise OpenAIAPIError(
            'AI analysis is not configured. Add an OPENAI_API_KEY to the environment.'
        )
    if not model:
        raise OpenAIAPIError(
            'OpenAI is not configured with a model. Set OPENAI_MODEL.'
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
        OPENAI_API_URL,
        data=json.dumps(payload).encode('utf-8'),
        headers={
            'Authorization': f'Bearer {api_key}',
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
            raise OpenAIAPIError(
                'OpenAI is rate-limited. Please try again shortly.'
                + (f' Details: {detail}' if detail else ''),
                retryable=True,
                retry_after=retry_after,
            ) from error
        if error.code >= 500:
            raise OpenAIAPIError(
                'OpenAI is temporarily unavailable. Please try again.'
                + (f' Details: {detail}' if detail else ''),
                retryable=True,
            ) from error
        if error.code in (401, 403):
            raise OpenAIAPIError(
                f'OpenAI rejected the request ({error.code}). Check '
                'OPENAI_API_KEY and API access.'
                + (f' Details: {detail}' if detail else '')
            ) from error
        raise OpenAIAPIError(
            f'OpenAI request failed with status {error.code}.'
            + (f' Details: {detail}' if detail else '')
        ) from error
    except (URLError, TimeoutError) as error:
        reason = getattr(error, 'reason', error)
        detail = ' '.join(str(reason).split())[:200]
        raise OpenAIAPIError(
            'OpenAI could not be reached'
            + (f' ({detail})' if detail else '')
            + '. Check the connection and try again.',
            retryable=True,
        ) from error
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise OpenAIAPIError('OpenAI returned an unreadable response.') from error

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
            headers.get('x-request-id') or headers.get('openai-request-id')
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
        raise OpenAIAPIError(
            'OpenAI returned content in an unexpected format.'
        ) from error

    refusal = message.get('refusal')
    if refusal:
        raise OpenAIAPIError(f'OpenAI declined the request: {refusal}')

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
    raise OpenAIAPIError('OpenAI returned no text for this request.')
