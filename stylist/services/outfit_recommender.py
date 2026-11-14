from heapq import heappop, heappush, heapreplace
from itertools import chain, product
import re

from ..models import ClothingItem


OCCASION_CHOICES = (
    ('', 'Any occasion'),
    ('College', 'College'),
    ('Office', 'Office'),
    ('Casual outing', 'Casual outing'),
    ('Party', 'Party'),
    ('Wedding', 'Wedding'),
    ('Formal meeting', 'Formal meeting'),
    ('Travel', 'Travel'),
)
SEASON_CHOICES = (
    ('', 'Any season'),
    ('Spring', 'Spring'),
    ('Summer', 'Summer'),
    ('Autumn', 'Autumn'),
    ('Winter', 'Winter'),
)
MAX_RECOMMENDATIONS = 6
MAX_CANDIDATES = 60

TOP_CATEGORIES = {
    ClothingItem.Category.SHIRT,
    ClothingItem.Category.T_SHIRT,
    ClothingItem.Category.HOODIE,
    ClothingItem.Category.SWEATER,
}
BOTTOM_CATEGORIES = {
    ClothingItem.Category.JEANS,
    ClothingItem.Category.TROUSERS,
    ClothingItem.Category.SHORTS,
    ClothingItem.Category.SKIRT,
}

_COLOR_FAMILIES = {
    'neutral': ('black', 'white', 'grey', 'gray', 'beige', 'cream', 'tan', 'brown'),
    'blue': ('blue', 'navy', 'teal', 'turquoise'),
    'red': ('red', 'burgundy', 'maroon'),
    'green': ('green', 'olive', 'mint'),
    'yellow': ('yellow', 'gold', 'mustard'),
    'purple': ('purple', 'violet', 'lavender'),
    'pink': ('pink', 'rose'),
    'orange': ('orange', 'coral', 'rust'),
}
_COMPLEMENTARY_FAMILIES = {
    frozenset(('blue', 'orange')),
    frozenset(('red', 'green')),
    frozenset(('yellow', 'purple')),
}
_FORMAL_OCCASIONS = {'office', 'wedding', 'formal meeting'}
_CASUAL_OCCASIONS = {'college', 'casual outing', 'travel'}


def generate_outfits(
    items,
    occasion='',
    season='',
    style='',
    preferred_item_ids=(),
):
    wardrobe = list(items)
    preferred_item_ids = set(preferred_item_ids)
    by_category = {}
    for item in wardrobe:
        by_category.setdefault(item.category, []).append(item)

    tops = [
        item
        for category in TOP_CATEGORIES
        for item in by_category.get(category, ())
    ]
    bottoms = [
        item
        for category in BOTTOM_CATEGORIES
        for item in by_category.get(category, ())
    ]
    shoes = by_category.get(ClothingItem.Category.SHOES, [])
    dresses = by_category.get(ClothingItem.Category.DRESS, [])
    candidate_groups = []

    if tops and bottoms and shoes:
        candidate_groups.append(product(tops, bottoms, shoes))
    elif tops and bottoms:
        candidate_groups.append(product(tops, bottoms))
    elif tops and shoes:
        candidate_groups.append(product(tops, shoes))
    elif bottoms and shoes:
        candidate_groups.append(product(bottoms, shoes))

    if dresses and shoes:
        candidate_groups.append(product(dresses, shoes))

    best = []
    sequence = 0
    for pieces in chain.from_iterable(candidate_groups):
        score, reasons = _score_outfit(
            pieces,
            occasion,
            season,
            style,
            preferred_item_ids,
        )
        entry = (score, sequence, pieces, reasons)
        sequence += 1
        if len(best) < MAX_CANDIDATES:
            heappush(best, entry)
        elif score > best[0][0]:
            heapreplace(best, entry)

    ranked = sorted(best, key=lambda entry: (entry[0], entry[1]), reverse=True)
    recommendations = []
    for score, _, pieces, reasons in ranked:
        item_ids = {item.pk for item in pieces}
        if len(pieces) > 2 and any(
            len(item_ids.intersection(recommendation['item_ids'])) >= 2
            for recommendation in recommendations
        ):
            continue
        recommendations.append(
            {
                'items': pieces,
                'item_ids': tuple(item.pk for item in pieces),
                'score': score,
                'reasons': reasons,
            }
        )
        if len(recommendations) == MAX_RECOMMENDATIONS:
            break
    return recommendations


def _score_outfit(pieces, occasion, season, style, preferred_item_ids):
    score = 55
    reasons = []
    color_scores = [
        _color_pair_score(first, second)
        for index, first in enumerate(pieces)
        for second in pieces[index + 1 :]
    ]
    score += sum(color_scores)
    if color_scores and all(value >= 6 for value in color_scores):
        reasons.append('The colors create a coordinated, balanced palette.')
    else:
        reasons.append('The colors add a balanced contrast to the look.')

    styles = {
        item.style
        for item in pieces
        if item.style and item.style != ClothingItem.Style.OTHER
    }
    if len(styles) == 1:
        score += 8
        reasons.append(f'The pieces share a {next(iter(styles))} style.')
    elif len(styles) > 1:
        score -= 2
        reasons.append('The combination balances the different piece styles.')

    style_key = _normalize(style)
    if style_key:
        matching = sum(
            _normalize(item.get_style_display()) == style_key
            for item in pieces
            if item.style
        )
        score += matching * 5
        if matching:
            reasons.append(f'{matching} piece(s) match your preferred style.')

    occasion_key = _normalize(occasion)
    if occasion_key:
        matching = sum(
            _has_tag(item.occasion, occasion_key)
            for item in pieces
        )
        score += matching * 5
        if matching:
            reasons.append(f'{matching} piece(s) are tagged for this occasion.')
        else:
            reasons.append('The outfit is a versatile option for this occasion.')
        score += _occasion_style_score(styles, occasion_key)

    season_key = _normalize(season)
    if season_key:
        matching = sum(_has_tag(item.season, season_key) for item in pieces)
        score += matching * 4
        if matching:
            reasons.append(f'{matching} piece(s) suit this season.')

    score += sum(1 for item in pieces if item.is_favorite)
    preferred_count = sum(
        item.pk in preferred_item_ids
        for item in pieces
    )
    score += preferred_count * 12
    if preferred_count:
        reasons.append(f'Includes {preferred_count} piece(s) you selected.')
    reasons.append('Every suggested piece is already in your wardrobe.')
    return max(0, min(100, score)), reasons


def _color_pair_score(first, second):
    first_families = _color_families_for(first)
    second_families = _color_families_for(second)
    if not first_families or not second_families:
        return 2
    if 'neutral' in first_families or 'neutral' in second_families:
        return 7
    if first_families.intersection(second_families):
        return 8
    if any(
        frozenset((first_family, second_family)) in _COMPLEMENTARY_FAMILIES
        for first_family in first_families
        for second_family in second_families
    ):
        return 7
    return 2


def _color_families_for(item):
    value = f'{item.primary_color} {item.secondary_color}'.lower()
    return {
        family
        for family, colors in _COLOR_FAMILIES.items()
        if any(color in value for color in colors)
    }


def _occasion_style_score(styles, occasion):
    if not styles:
        return 0
    if occasion in _FORMAL_OCCASIONS:
        if ClothingItem.Style.FORMAL in styles or ClothingItem.Style.TRADITIONAL in styles:
            return 5
        if ClothingItem.Style.CASUAL in styles or ClothingItem.Style.SPORTY in styles:
            return -5
    elif occasion in _CASUAL_OCCASIONS:
        if ClothingItem.Style.CASUAL in styles or ClothingItem.Style.STREETWEAR in styles:
            return 4
        if ClothingItem.Style.FORMAL in styles:
            return -3
    return 0


def _has_tag(value, target):
    return any(_normalize(part) == target for part in re.split(r'[,;/]', value))


def _normalize(value):
    return ' '.join(re.sub(r'[^a-z0-9]+', ' ', value.lower()).split())
