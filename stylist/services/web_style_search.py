from html.parser import HTMLParser
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen


SEARCH_URL = 'https://www.bing.com/search'
OPENVERSE_IMAGES_URL = 'https://api.openverse.org/v1/images/'
MAX_RESULTS = 3
OPENVERSE_LICENSES = {'cc0', 'by', 'by-sa'}


class WebStyleSearchError(Exception):
    pass


class _SearchResultsParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.results = []
        self._active = None
        self._capture = None
        self._text = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        classes = attributes.get('class', '').split()
        if tag == 'li' and 'b_algo' in classes:
            self._active = {'title': '', 'url': '', 'snippet': ''}
        elif self._active and tag == 'h2':
            self._capture = 'title'
            self._text = []
        elif self._active and tag == 'a' and self._capture == 'title':
            self._active['url'] = attributes.get('href', '')
        elif self._active and tag == 'p' and not self._active['snippet']:
            self._capture = 'snippet'
            self._text = []

    def handle_data(self, data):
        if self._capture:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == 'h2' and self._capture == 'title' and self._active:
            self._active['title'] = ' '.join(''.join(self._text).split())
            self._capture = None
            self._text = []
        elif tag == 'p' and self._capture == 'snippet' and self._active:
            self._active['snippet'] = ' '.join(''.join(self._text).split())
            self._capture = None
            self._text = []
            self._finish_result()
        elif tag == 'li' and self._active:
            self._finish_result()

    def _finish_result(self):
        if self._active['title'] and _is_safe_url(self._active['url']):
            self.results.append(self._active)
        self._active = None
        self._capture = None
        self._text = []


def search_style_guidance(recommendation, occasion='', season=''):
    categories = ' '.join(
        item.get_category_display().lower()
        for item in recommendation['items']
    )
    colors = ' '.join(
        item.primary_color.lower()
        for item in recommendation['items']
        if item.primary_color
    )
    terms = [
        'outfit inspiration',
        colors,
        categories,
        occasion.lower(),
        season.lower(),
    ]
    query = ' '.join(term for term in terms if term)

    request = Request(
        f'{SEARCH_URL}?{urlencode({"q": query})}',
        headers={
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 Chrome/131 Safari/537.36'
            ),
            'Accept': 'text/html',
        },
    )
    try:
        with urlopen(request, timeout=8) as response:
            page = response.read(500_000).decode('utf-8', errors='replace')
    except (HTTPError, URLError, TimeoutError, OSError) as error:
        raise WebStyleSearchError(
            'Web style search is temporarily unavailable.'
        ) from error

    parser = _SearchResultsParser()
    parser.feed(page)
    return parser.results[:MAX_RESULTS]


def search_mens_outfit_images(recommendation, occasion='', season=''):
    categories = ' '.join(
        item.get_category_display().lower()
        for item in recommendation['items']
    )
    colors = ' '.join(
        item.primary_color.lower()
        for item in recommendation['items']
        if item.primary_color
    )
    query = ' '.join(
        term
        for term in (
            "men's outfit",
            'menswear',
            colors,
            categories,
            occasion.lower(),
            season.lower(),
        )
        if term
    )[:300]
    request = Request(
        f'{OPENVERSE_IMAGES_URL}?{urlencode({
            "q": query,
            "license": ",".join(sorted(OPENVERSE_LICENSES)),
            "mature": "false",
            "page_size": 20,
        })}',
        headers={
            'User-Agent': 'Wardrobe outfit inspiration/1.0',
            'Accept': 'application/json',
        },
    )
    try:
        with urlopen(request, timeout=8) as response:
            payload = json.loads(response.read(500_000).decode('utf-8'))
    except HTTPError as error:
        if error.code == 429:
            raise WebStyleSearchError(
                'Web image search is temporarily rate-limited. Try again later.'
            ) from error
        raise WebStyleSearchError(
            'Web image search is temporarily unavailable.'
        ) from error
    except (URLError, TimeoutError, OSError) as error:
        raise WebStyleSearchError(
            'Web image search is temporarily unavailable.'
        ) from error
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise WebStyleSearchError(
            'Web image search returned an unreadable response.'
        ) from error

    results = payload.get('results')
    if not isinstance(results, list):
        raise WebStyleSearchError(
            'Web image search returned an unexpected response.'
        )

    images = []
    for result in results:
        if not isinstance(result, dict):
            continue
        license_name = result.get('license')
        title = result.get('title')
        creator = result.get('creator') or 'Unknown creator'
        tags = result.get('tags')
        if not isinstance(title, str) or license_name not in OPENVERSE_LICENSES:
            continue
        if not _is_menswear_result(title, creator, tags):
            continue

        thumbnail_url = result.get('thumbnail')
        source_url = result.get('foreign_landing_url')
        license_url = result.get('license_url')
        if not all(
            isinstance(value, str) and _is_safe_url(value)
            for value in (thumbnail_url, source_url, license_url)
        ):
            continue
        images.append(
            {
                'title': title[:160],
                'thumbnail_url': thumbnail_url,
                'source_url': source_url,
                'creator': str(creator)[:120],
                'license': str(license_name).upper(),
                'license_url': license_url,
            }
        )
        if len(images) == MAX_RESULTS:
            break
    return images


def _is_menswear_result(title, creator, tags):
    tag_names = [
        tag.get('name', '')
        for tag in tags or ()
        if isinstance(tag, dict) and isinstance(tag.get('name'), str)
    ]
    metadata = ' '.join((title, str(creator), *tag_names)).lower()
    if re.search(r'\b(?:women|woman|female|girls?)\b', metadata):
        return False
    return bool(
        re.search(r"\b(?:men|mens|menswear|man|male|gentleman)\b", metadata)
    )


def _is_safe_url(value):
    parsed = urlparse(value)
    return parsed.scheme == 'https' and bool(parsed.netloc)
