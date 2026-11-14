from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen


SEARCH_URL = 'https://www.bing.com/search'
MAX_RESULTS = 3


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


def _is_safe_url(value):
    parsed = urlparse(value)
    return parsed.scheme == 'https' and bool(parsed.netloc)
