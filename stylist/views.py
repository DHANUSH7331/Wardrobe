import hashlib
import mimetypes
import os
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.staticfiles import finders
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.db import transaction
from django.db.models import Count, Q
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .forms import (
    ClothingItemForm,
    ClothingPhotoForm,
    OutfitGenerationForm,
    RegistrationForm,
    SavedOutfitForm,
)
from .models import ClothingItem, SavedOutfit, ScanBatch, ScanPhoto
from .services.image_analyzer import (
    ImageAnalysisError,
    analyze_clothing,
    is_configured,
    provider_name,
)
from .services.ai_outfit_stylist import (
    AIOutfitStylingError,
    rank_outfits,
)
from .services.outfit_recommender import generate_outfits
from .storage import image_storage_configured
from .services.web_style_search import (
    WebStyleSearchError,
    search_mens_outfit_images,
    search_style_guidance,
)
from .validators import validate_clothing_image


MAX_SCAN_BATCH_PHOTOS = 300
MAX_SCAN_CHUNK_PHOTOS = 1
STALE_SCAN_MINUTES = 5
scan_image_extension_validator = FileExtensionValidator(
    allowed_extensions=('jpg', 'jpeg', 'png', 'webp')
)


def home(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    landing_page = Path(settings.BASE_DIR, 'index.html').read_text(encoding='utf-8')
    # The root index is also used by GitHub Pages, where its relative manifest
    # path is correct. On Django/Vercel, serve the app's collected static PWA
    # manifest and favicon instead.
    landing_page = landing_page.replace(
        'href="./manifest.webmanifest"',
        'href="/static/wardrobe/manifest.webmanifest"',
    ).replace(
        'href="./stylist/static/wardrobe/icons/favicon.svg"',
        'href="/static/wardrobe/icons/favicon.svg"',
    )
    return HttpResponse(landing_page, content_type='text/html; charset=utf-8')


@require_GET
def service_worker(request):
    worker_path = finders.find('wardrobe/service-worker.js')
    if not worker_path:
        raise Http404('Service worker file not found.')
    response = FileResponse(
        open(worker_path, 'rb'),
        content_type='application/javascript',
    )
    response['Cache-Control'] = 'no-cache'
    response['Service-Worker-Allowed'] = '/'
    return response


def register(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    form = RegistrationForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.save()
        login(request, user)
        return redirect('dashboard')

    return render(request, 'registration/register.html', {'form': form})


@login_required
def dashboard(request):
    items = ClothingItem.objects.filter(user=request.user)
    return render(
        request,
        'wardrobe/dashboard.html',
        {
            'item_count': items.count(),
            'favorite_count': items.filter(is_favorite=True).count(),
            'recent_items': items[:4],
            'saved_outfit_count': SavedOutfit.objects.filter(
                user=request.user
            ).count(),
        },
    )


@login_required
def clothing_list(request):
    items = ClothingItem.objects.filter(user=request.user)
    search = request.GET.get('q', '').strip()
    category = request.GET.get('category', '').strip()
    color = request.GET.get('color', '').strip()
    style = request.GET.get('style', '').strip()
    season = request.GET.get('season', '').strip()
    occasion = request.GET.get('occasion', '').strip()

    if search:
        items = items.filter(
            Q(name__icontains=search)
            | Q(category__icontains=search)
            | Q(primary_color__icontains=search)
            | Q(secondary_color__icontains=search)
            | Q(pattern__icontains=search)
            | Q(style__icontains=search)
            | Q(occasion__icontains=search)
            | Q(season__icontains=search)
        )
    if category:
        items = items.filter(category=category)
    if color:
        items = items.filter(
            Q(primary_color__icontains=color)
            | Q(secondary_color__icontains=color)
        )
    if style:
        items = items.filter(style=style)
    if season:
        items = items.filter(season__icontains=season)
    if occasion:
        items = items.filter(occasion__icontains=occasion)

    return render(
        request,
        'wardrobe/wardrobe.html',
        {
            'items': items,
            'item_count': ClothingItem.objects.filter(user=request.user).count(),
            'search': search,
            'selected_category': category,
            'selected_color': color,
            'selected_style': style,
            'selected_season': season,
            'selected_occasion': occasion,
            'categories': ClothingItem.Category.choices,
            'styles': ClothingItem.Style.choices,
        },
    )


@login_required
def outfit_generator(request):
    wardrobe_items = ClothingItem.objects.filter(user=request.user)
    form = OutfitGenerationForm(
        request.GET or None,
        items=wardrobe_items,
    )
    recommendations = []
    selected_items = []
    web_results = []
    web_image_results = []
    web_search_error = ''
    web_image_error = ''
    web_search_attempted = False
    ai_error = ''
    ai_used = False
    has_generated = False
    if request.GET:
        if form.is_valid():
            has_generated = True
            selected_items = list(form.cleaned_data['selected_items'])
            recommendations = generate_outfits(
                wardrobe_items,
                occasion=form.cleaned_data['occasion'],
                season=form.cleaned_data['season'],
                style=form.cleaned_data['style'],
                preferred_item_ids=(item.pk for item in selected_items),
            )
            if recommendations:
                inspiration_items = list(selected_items[:3])
                inspiration_item_ids = {item.pk for item in inspiration_items}
                inspiration_items.extend(
                    item
                    for item in recommendations[0]['items']
                    if item.pk not in inspiration_item_ids
                )
                inspiration_items = inspiration_items[:3]
            else:
                inspiration_items = selected_items or list(wardrobe_items[:3])
            if inspiration_items:
                web_search_attempted = True
                try:
                    web_results = search_style_guidance(
                        {'items': inspiration_items},
                        occasion=form.cleaned_data['occasion'],
                        season=form.cleaned_data['season'],
                    )
                except WebStyleSearchError as error:
                    web_search_error = str(error)
                try:
                    web_image_results = search_mens_outfit_images(
                        {'items': inspiration_items},
                        occasion=form.cleaned_data['occasion'],
                        season=form.cleaned_data['season'],
                    )
                except WebStyleSearchError as error:
                    web_image_error = str(error)
            if recommendations:
                try:
                    recommendations = rank_outfits(
                        recommendations,
                        web_results,
                        occasion=form.cleaned_data['occasion'],
                        season=form.cleaned_data['season'],
                        style=form.cleaned_data['style'],
                        selected_items=selected_items,
                    )
                    ai_used = True
                except AIOutfitStylingError as error:
                    ai_error = str(error)

    return render(
        request,
        'wardrobe/outfit_generator.html',
        {
            'form': form,
            'recommendations': recommendations,
            'has_generated': has_generated,
            'wardrobe_items': wardrobe_items,
            'selected_item_ids': {item.pk for item in selected_items},
            'web_results': web_results,
            'web_search_error': web_search_error,
            'web_image_results': web_image_results,
            'web_image_error': web_image_error,
            'show_web_results': bool(recommendations or web_search_attempted),
            'ai_error': ai_error,
            'ai_used': ai_used,
        },
    )


@login_required
@require_POST
def outfit_save(request):
    form = SavedOutfitForm(request.POST)
    if not form.is_valid():
        messages.error(request, 'Enter a valid outfit name and styling preference.')
        return redirect('outfit_generator')

    raw_ids = request.POST.getlist('item_ids')
    try:
        item_ids = [int(value) for value in raw_ids]
    except ValueError:
        item_ids = []

    if len(item_ids) not in (2, 3) or len(set(item_ids)) != len(item_ids):
        messages.error(request, 'This outfit selection is invalid. Generate it again.')
        return redirect('outfit_generator')

    owned_items = {
        item.pk: item
        for item in ClothingItem.objects.filter(
            user=request.user,
            pk__in=item_ids,
        )
    }
    if len(owned_items) != len(item_ids):
        messages.error(request, 'Outfits can only contain pieces in your wardrobe.')
        return redirect('outfit_generator')

    outfit = SavedOutfit.objects.create(
        user=request.user,
        name=form.cleaned_data['name'],
        notes=form.cleaned_data['notes'],
        occasion=form.cleaned_data['occasion'],
        season=form.cleaned_data['season'],
    )
    outfit.items.set(owned_items[item_id] for item_id in item_ids)
    messages.success(request, f'{outfit.name} was saved to your outfits.')
    return redirect('saved_outfits')


@login_required
def saved_outfits(request):
    outfits = (
        SavedOutfit.objects.filter(user=request.user)
        .prefetch_related('items')
    )
    return render(request, 'wardrobe/saved_outfits.html', {'outfits': outfits})


@login_required
@require_POST
def outfit_delete(request, pk):
    outfit = _owned_outfit(request, pk)
    name = outfit.name
    outfit.delete()
    messages.success(request, f'{name} was removed from your saved outfits.')
    return redirect('saved_outfits')


@login_required
@require_POST
def outfit_favorite(request, pk):
    outfit = _owned_outfit(request, pk)
    outfit.is_favorite = not outfit.is_favorite
    outfit.save(update_fields=('is_favorite',))
    return redirect('saved_outfits')


@login_required
def clothing_add(request):
    form = ClothingPhotoForm(request.POST or None, request.FILES or None)
    ai_enabled = is_configured()
    if request.method == 'POST' and not image_storage_configured():
        form.add_error(
            'image',
            'Photo storage is not configured yet. Connect a private Vercel Blob store to this project.',
        )
    if request.method == 'POST' and form.is_valid() and image_storage_configured():
        image = form.cleaned_data['image']
        item = ClothingItem.objects.create(
            user=request.user,
            image=image,
            name='Uncategorized piece',
            category=ClothingItem.Category.OTHER,
            primary_color='Unspecified',
        )
        if ai_enabled:
            messages.info(
                request,
                'Photo saved. AI is identifying the clothing details now.',
            )
            return redirect(f'{reverse("clothing_detail", args=(item.pk,))}?analyze=1')

        messages.warning(
            request,
            'AI is not configured. The photo was saved without analysis; '
            'edit the piece to add its details manually.',
        )
        return redirect('clothing_detail', pk=item.pk)

    return render(
        request,
        'wardrobe/clothing_form.html',
        {
            'form': form,
            'page_title': 'Add a piece',
            'submit_label': 'Add to wardrobe',
            'active_page': 'add',
            'ai_enabled': ai_enabled,
        },
    )


@login_required
def scan_clothing(request):
    return render(
        request,
        'wardrobe/bulk_scanner.html',
        {
            'active_page': 'scan',
            'ai_enabled': is_configured(),
            'ai_provider': provider_name(),
            'max_batch_photos': MAX_SCAN_BATCH_PHOTOS,
            'chunk_size': MAX_SCAN_CHUNK_PHOTOS,
        },
    )


@login_required
@require_POST
def scan_batch_create(request):
    batch = ScanBatch.objects.create(user=request.user)
    return JsonResponse({'batch_id': str(batch.pk)}, status=201)


@login_required
@require_POST
def scan_upload(request, batch_id):
    if not image_storage_configured():
        return JsonResponse(
            {
                'error': (
                    'Photo storage is not configured yet. Connect a private '
                    'Vercel Blob store to this project.'
                )
            },
            status=503,
        )
    uploads = request.FILES.getlist('images')
    if not uploads:
        return JsonResponse({'error': 'Choose at least one photo.'}, status=400)
    if len(uploads) > MAX_SCAN_CHUNK_PHOTOS:
        return JsonResponse(
            {'error': f'Upload no more than {MAX_SCAN_CHUNK_PHOTOS} photos at once.'},
            status=400,
        )

    batch = get_object_or_404(ScanBatch, pk=batch_id, user=request.user)
    if batch.status == ScanBatch.Status.COMPLETED:
        return JsonResponse(
            {'error': 'This scan is complete. Start a new batch for more photos.'},
            status=409,
        )

    known_hashes = set(
        batch.photos.values_list('upload_hash', flat=True)
    )
    existing_count = len(known_hashes)
    valid_uploads = []
    errors = []
    duplicates = []
    for upload in uploads:
        try:
            scan_image_extension_validator(upload)
            validate_clothing_image(upload)
        except ValidationError as error:
            errors.append(
                {
                    'name': os.path.basename(upload.name),
                    'error': ' '.join(error.messages),
                }
            )
        else:
            digest = hashlib.sha256()
            for chunk in upload.chunks():
                digest.update(chunk)
            upload_hash = digest.hexdigest()
            upload.seek(0)
            if upload_hash in known_hashes:
                duplicates.append(os.path.basename(upload.name))
            else:
                known_hashes.add(upload_hash)
                valid_uploads.append((upload, upload_hash))

    if existing_count + len(valid_uploads) > MAX_SCAN_BATCH_PHOTOS:
        return JsonResponse(
            {
                'error': (
                    f'A scan batch can contain at most '
                    f'{MAX_SCAN_BATCH_PHOTOS} photos.'
                )
            },
            status=400,
        )
    if not valid_uploads and duplicates:
        return JsonResponse(
            {
                'batch_id': str(batch.pk),
                'uploaded': 0,
                'photos': [],
                'duplicates': duplicates,
                'file_errors': errors,
            }
        )
    if not valid_uploads:
        return JsonResponse(
            {
                'error': 'No valid photos in this upload.',
                'file_errors': errors,
            },
            status=400,
        )

    created_photos = []
    for upload, upload_hash in valid_uploads:
        photo = ScanPhoto.objects.create(
            batch=batch,
            image=upload,
            original_name=os.path.basename(upload.name)[:255],
            upload_hash=upload_hash,
        )
        created_photos.append(photo)

    batch.status = ScanBatch.Status.UPLOADING
    batch.save(update_fields=('status',))
    return JsonResponse(
        {
            'batch_id': str(batch.pk),
            'uploaded': len(created_photos),
            'photos': [
                {'id': photo.pk, 'name': photo.original_name}
                for photo in created_photos
            ],
            'duplicates': duplicates,
            'file_errors': errors,
        },
        status=201,
    )


@login_required
@require_GET
def scan_status(request, batch_id):
    batch = _owned_batch(request, batch_id)
    _recover_stale_photos(batch)
    counts = _scan_counts(batch)
    _refresh_batch_status(batch, counts)
    photos = batch.photos.select_related('clothing_item')
    response = JsonResponse(
        {
            'batch_id': str(batch.pk),
            'status': batch.status,
            'counts': counts,
            'photos': [
                {
                    'id': photo.pk,
                    'name': photo.original_name,
                    'status': photo.status,
                    'error': photo.last_error,
                    'image_url': reverse(
                        'scan_photo_image',
                        args=(batch.pk, photo.pk),
                    ),
                    'clothing_url': (
                        reverse('clothing_detail', args=(photo.clothing_item_id,))
                        if photo.clothing_item_id
                        else None
                    ),
                }
                for photo in photos
            ],
        }
    )
    response['Cache-Control'] = 'private, no-store'
    return response


@login_required
@require_POST
def scan_process_photo(request, batch_id, photo_id):
    batch = _owned_batch(request, batch_id)
    if not is_configured():
        return JsonResponse(
            {
                'error': (
                    'AI is not configured. Add an OPENROUTER_API_KEY, then resume this scan.'
                ),
                'retryable': False,
            },
            status=503,
        )

    _recover_stale_photos(batch)
    photo = get_object_or_404(ScanPhoto, batch=batch, pk=photo_id)
    if photo.status == ScanPhoto.Status.COMPLETE:
        return JsonResponse(
            {
                'status': photo.status,
                'clothing_url': (
                    reverse(
                        'clothing_detail',
                        args=(photo.clothing_item_id,),
                    )
                    if photo.clothing_item_id
                    else None
                ),
            }
        )

    now = timezone.now()
    claimed = ScanPhoto.objects.filter(
        pk=photo.pk,
        status__in=(ScanPhoto.Status.PENDING, ScanPhoto.Status.FAILED),
    ).update(
        status=ScanPhoto.Status.PROCESSING,
        last_error='',
        updated_at=now,
    )
    if not claimed:
        return JsonResponse(
            {'error': 'This photo is already being processed.', 'retryable': True},
            status=409,
        )

    photo.refresh_from_db()
    try:
        with photo.image.storage.open(photo.image.name, 'rb') as image_file:
            details = analyze_clothing(image_file)
    except ImageAnalysisError as error:
        photo.status = ScanPhoto.Status.FAILED
        photo.last_error = str(error)[:500]
        photo.save(update_fields=('status', 'last_error', 'updated_at'))
        _refresh_batch_status(batch)
        return JsonResponse(
            {
                'status': photo.status,
                'error': photo.last_error,
                'retryable': error.retryable,
                'retry_after': error.retry_after,
            }
        )

    with transaction.atomic():
        photo = ScanPhoto.objects.select_for_update().get(pk=photo.pk)
        item = ClothingItem.objects.create(
            user=request.user,
            image=photo.image.name,
            **details,
        )
        photo.image = ''
        photo.status = ScanPhoto.Status.COMPLETE
        photo.clothing_item = item
        photo.last_error = ''
        photo.save(
            update_fields=(
                'image',
                'status',
                'clothing_item',
                'last_error',
                'updated_at',
            )
        )

    _refresh_batch_status(batch)
    return JsonResponse(
        {
            'status': photo.status,
            'clothing_url': reverse('clothing_detail', args=(item.pk,)),
        }
    )


@login_required
@require_GET
def scan_photo_image(request, batch_id, photo_id):
    batch = _owned_batch(request, batch_id)
    photo = get_object_or_404(
        ScanPhoto.objects.select_related('clothing_item'),
        batch=batch,
        pk=photo_id,
    )
    if photo.clothing_item_id:
        return redirect('clothing_image', pk=photo.clothing_item_id)
    if not photo.image or not photo.image.storage.exists(photo.image.name):
        raise Http404('Scan photo not found.')

    response = FileResponse(
        photo.image.open('rb'),
        content_type=(
            mimetypes.guess_type(photo.image.name)[0]
            or 'application/octet-stream'
        ),
    )
    response['Cache-Control'] = 'private, no-store'
    return response


@login_required
def clothing_detail(request, pk):
    item = _owned_item(request, pk)
    return render(request, 'wardrobe/clothing_detail.html', {'item': item})


@login_required
@require_POST
def clothing_analyze(request, pk):
    """Analyze a saved photo after returning the detail page to the user."""
    item = _owned_item(request, pk)
    if not is_configured():
        return JsonResponse({'error': 'AI analysis is not configured.'}, status=503)
    try:
        details = analyze_clothing(item.image)
    except ImageAnalysisError as error:
        return JsonResponse({'error': str(error)}, status=502)

    for field, value in details.items():
        setattr(item, field, value)
    item.save(update_fields=tuple(details))
    return JsonResponse({'ok': True, 'name': item.name})


@login_required
def clothing_edit(request, pk):
    item = _owned_item(request, pk)
    old_image = item.image
    form = ClothingItemForm(
        request.POST or None,
        request.FILES or None,
        instance=item,
    )
    if request.method == 'POST' and form.is_valid():
        item = form.save()
        if old_image and old_image.name != item.image.name:
            old_image.delete(save=False)
        messages.success(request, f'{item.name} was updated.')
        return redirect('clothing_detail', pk=item.pk)

    return render(
        request,
        'wardrobe/clothing_form.html',
        {
            'form': form,
            'item': item,
            'page_title': 'Edit piece',
            'submit_label': 'Save changes',
            'active_page': 'wardrobe',
        },
    )


@login_required
@require_POST
def clothing_delete(request, pk):
    item = _owned_item(request, pk)
    name = item.name
    item.delete()
    messages.success(request, f'{name} was removed from your wardrobe.')
    return redirect('wardrobe')


@login_required
@require_POST
def clothing_favorite(request, pk):
    item = _owned_item(request, pk)
    item.is_favorite = not item.is_favorite
    item.save(update_fields=('is_favorite',))
    return redirect('wardrobe')


@login_required
def clothing_image(request, pk):
    item = _owned_item(request, pk)
    if not item.image or not item.image.storage.exists(item.image.name):
        raise Http404('Clothing image not found.')

    response = FileResponse(
        item.image.open('rb'),
        content_type=(
            mimetypes.guess_type(item.image.name)[0]
            or 'application/octet-stream'
        ),
    )
    response['Cache-Control'] = 'private, no-store'
    return response


@login_required
@require_GET
def private_media(request, name):
    """Serve a private blob only when its database record belongs to the user."""
    owns_clothing_image = ClothingItem.objects.filter(
        user=request.user,
        image=name,
    ).exists()
    owns_scan_image = ScanPhoto.objects.filter(
        batch__user=request.user,
        image=name,
    ).exists()
    if not (owns_clothing_image or owns_scan_image):
        raise Http404('Image not found.')

    from django.core.files.storage import default_storage

    if not default_storage.exists(name):
        raise Http404('Image not found.')
    response = FileResponse(
        default_storage.open(name, 'rb'),
        content_type=mimetypes.guess_type(name)[0] or 'application/octet-stream',
    )
    response['Cache-Control'] = 'private, no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


def _owned_item(request, pk):
    return get_object_or_404(ClothingItem, pk=pk, user=request.user)


def _owned_outfit(request, pk):
    return get_object_or_404(SavedOutfit, pk=pk, user=request.user)


def _owned_batch(request, batch_id):
    return get_object_or_404(ScanBatch, pk=batch_id, user=request.user)


def _recover_stale_photos(batch):
    stale_before = timezone.now() - timedelta(minutes=STALE_SCAN_MINUTES)
    batch.photos.filter(
        status=ScanPhoto.Status.PROCESSING,
        updated_at__lt=stale_before,
    ).update(
        status=ScanPhoto.Status.PENDING,
        last_error='A previous scan stopped before finishing; ready to retry.',
        updated_at=timezone.now(),
    )


def _refresh_batch_status(batch, counts=None):
    counts = counts or _scan_counts(batch)
    if counts['pending'] or counts['processing']:
        status = (
            ScanBatch.Status.PROCESSING
            if counts['processing']
            else ScanBatch.Status.UPLOADING
        )
    elif counts['failed']:
        status = ScanBatch.Status.PARTIAL
    else:
        status = ScanBatch.Status.COMPLETED

    if batch.status != status:
        batch.status = status
        batch.save(update_fields=('status',))


def _scan_counts(batch):
    return batch.photos.aggregate(
        total=Count('pk'),
        pending=Count(
            'pk',
            filter=Q(status=ScanPhoto.Status.PENDING),
        ),
        processing=Count(
            'pk',
            filter=Q(status=ScanPhoto.Status.PROCESSING),
        ),
        complete=Count(
            'pk',
            filter=Q(status=ScanPhoto.Status.COMPLETE),
        ),
        failed=Count(
            'pk',
            filter=Q(status=ScanPhoto.Status.FAILED),
        ),
    )
