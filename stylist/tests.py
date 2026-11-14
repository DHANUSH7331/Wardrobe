import json
import os
from io import BytesIO
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PIL import Image
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import ClothingItem, SavedOutfit, ScanBatch, ScanPhoto
from .services.ai_outfit_stylist import AIOutfitStylingError, rank_outfits
from .services.image_analyzer import (
    ImageAnalysisError,
    analyze_clothing,
    is_configured,
)
from .services.web_style_search import search_style_guidance
from .validators import MAX_IMAGE_SIZE


class AuthenticationFlowTests(TestCase):
    def test_service_worker_is_available_at_root_scope(self):
        response = self.client.get('/service-worker.js')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Service-Worker-Allowed'], '/')
        self.assertEqual(response['Cache-Control'], 'no-cache')
        body = b''.join(response.streaming_content)
        self.assertIn(b"request.mode === 'navigate'", body)
        response.close()

    def test_home_offers_sign_in_and_registration(self):
        response = self.client.get(reverse('home'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse('login'))
        self.assertContains(response, reverse('register'))

    def test_registration_creates_account_and_signs_user_in(self):
        response = self.client.post(
            reverse('register'),
            {
                'username': 'stylefan',
                'email': 'style@example.com',
                'password1': 'WardrobePass!482',
                'password2': 'WardrobePass!482',
            },
        )

        self.assertRedirects(response, reverse('dashboard'))
        self.assertTrue(
            User.objects.filter(
                username='stylefan',
                email='style@example.com',
            ).exists()
        )
        self.assertIn('_auth_user_id', self.client.session)

    def test_invalid_registration_renders_form_errors(self):
        response = self.client.post(
            reverse('register'),
            {
                'username': 'stylefan',
                'email': 'not-an-email',
                'password1': 'short',
                'password2': 'different',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username='stylefan').exists())
        self.assertContains(response, 'Enter a valid email address.')

    def test_login_authenticates_existing_user(self):
        User.objects.create_user(username='member', password='pass12345')

        response = self.client.post(
            reverse('login'),
            {'username': 'member', 'password': 'pass12345'},
        )

        self.assertRedirects(response, reverse('dashboard'))
        self.assertIn('_auth_user_id', self.client.session)

    def test_dashboard_requires_login_and_is_available_after_login(self):
        response = self.client.get(reverse('dashboard'))
        self.assertRedirects(
            response,
            f'{reverse("login")}?next={reverse("dashboard")}',
        )

        self.client.force_login(
            User.objects.create_user(username='member', password='pass12345')
        )
        response = self.client.get(reverse('dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Your style,')

    def test_logout_ends_session(self):
        self.client.force_login(
            User.objects.create_user(username='member', password='pass12345')
        )

        response = self.client.post(reverse('logout'))

        self.assertRedirects(response, reverse('home'))
        self.assertNotIn('_auth_user_id', self.client.session)


class ClothingWorkflowTests(TestCase):
    def setUp(self):
        self.media_directory = TemporaryDirectory()
        self.addCleanup(self.media_directory.cleanup)
        media_override = override_settings(MEDIA_ROOT=self.media_directory.name)
        media_override.enable()
        self.addCleanup(media_override.disable)
        self.user = User.objects.create_user(username='owner', password='pass12345')
        self.other_user = User.objects.create_user(
            username='someone-else',
            password='pass12345',
        )
        self.client.force_login(self.user)

    def make_image(self, name='linen-shirt.png', color=(190, 160, 125)):
        content = BytesIO()
        Image.new('RGB', (8, 8), color=color).save(content, format='PNG')
        return SimpleUploadedFile(
            name,
            content.getvalue(),
            content_type='image/png',
        )

    def clothing_data(self, **overrides):
        data = {
            'name': 'Linen shirt',
            'category': ClothingItem.Category.SHIRT,
            'primary_color': 'Cream',
            'secondary_color': '',
            'pattern': ClothingItem.Pattern.PLAIN,
            'style': ClothingItem.Style.CASUAL,
            'fit': '',
            'occasion': 'Office, Casual outing',
            'season': 'Spring, Summer',
            'image': self.make_image(),
        }
        data.update(overrides)
        return data

    def analysis_details(self):
        return {
            'name': 'Linen shirt',
            'category': ClothingItem.Category.SHIRT,
            'primary_color': 'Cream',
            'secondary_color': '',
            'pattern': ClothingItem.Pattern.PLAIN,
            'style': ClothingItem.Style.CASUAL,
            'material': 'Linen',
            'occasion': 'Office, Casual outing',
            'season': 'Spring, Summer',
        }

    def create_item(self, user=None, **overrides):
        data = self.clothing_data(**overrides)
        return ClothingItem.objects.create(
            user=user or self.user,
            **data,
        )

    def test_clothing_pages_require_authentication(self):
        self.client.logout()

        response = self.client.get(reverse('wardrobe'))

        self.assertRedirects(
            response,
            f'{reverse("login")}?next={reverse("wardrobe")}',
        )

    def test_bulk_scanner_page_explains_ai_status(self):
        with patch('stylist.views.is_configured', return_value=True):
            response = self.client.get(reverse('scan_clothing'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Bulk scan')
        self.assertContains(response, '300 clothing photos')
        self.assertContains(response, 'data-batch-upload-url')

    def test_bulk_scan_batch_is_created_for_the_signed_in_user(self):
        response = self.client.post(reverse('scan_batch_create'))

        self.assertEqual(response.status_code, 201)
        batch = ScanBatch.objects.get(pk=response.json()['batch_id'])
        self.assertEqual(batch.user, self.user)

    def test_add_displays_piece_and_serves_image_privately(self):
        with (
            patch('stylist.views.is_configured', return_value=True),
            patch(
                'stylist.views.analyze_clothing',
                return_value=self.analysis_details(),
            ),
        ):
            response = self.client.post(
                reverse('clothing_add'),
                {'image': self.make_image()},
            )

        item = ClothingItem.objects.get(name='Linen shirt')
        self.assertRedirects(
            response,
            reverse('clothing_detail', args=(item.pk,)),
        )
        self.assertEqual(item.user, self.user)
        self.assertEqual(item.material, 'Linen')

        image_response = self.client.get(
            reverse('clothing_image', args=(item.pk,))
        )
        self.assertEqual(image_response.status_code, 200)
        self.assertEqual(image_response['Content-Type'], 'image/png')
        self.assertEqual(image_response['Cache-Control'], 'private, no-store')
        image_response.close()

        self.client.force_login(self.other_user)
        self.assertEqual(
            self.client.get(
                reverse('clothing_image', args=(item.pk,))
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(
                reverse('clothing_detail', args=(item.pk,))
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(reverse('wardrobe')).context['item_count'],
            0,
        )

    def test_add_form_only_asks_for_a_photo(self):
        response = self.client.get(reverse('clothing_add'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="image"')
        self.assertContains(response, 'Add to wardrobe')
        self.assertNotContains(response, 'name="category"')
        self.assertNotContains(response, 'name="primary_color"')

    def test_unconfigured_ai_saves_a_clear_manual_fallback(self):
        with (
            patch('stylist.views.is_configured', return_value=False),
            patch('stylist.views.analyze_clothing') as analyze,
        ):
            response = self.client.post(
                reverse('clothing_add'),
                {'image': self.make_image()},
            )

        item = ClothingItem.objects.get()
        self.assertEqual(item.name, 'Uncategorized piece')
        self.assertEqual(item.category, ClothingItem.Category.OTHER)
        self.assertEqual(item.primary_color, 'Unspecified')
        analyze.assert_not_called()
        detail = self.client.get(response.url)
        self.assertContains(detail, 'AI is not configured.')

    def test_analysis_failure_does_not_save_a_success_shaped_fallback(self):
        with (
            patch('stylist.views.is_configured', return_value=True),
            patch(
                'stylist.views.analyze_clothing',
                side_effect=ImageAnalysisError('The vision API is unavailable.'),
            ),
        ):
            response = self.client.post(
                reverse('clothing_add'),
                {'image': self.make_image()},
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'The vision API is unavailable.')
        self.assertFalse(ClothingItem.objects.exists())

    def test_search_filters_by_category_and_color(self):
        self.create_item(name='Cream shirt')
        self.create_item(
            name='Blue jeans',
            category=ClothingItem.Category.JEANS,
            primary_color='Blue',
        )

        response = self.client.get(
            reverse('wardrobe'),
            {'category': ClothingItem.Category.JEANS, 'color': 'blue'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [item.name for item in response.context['items']],
            ['Blue jeans'],
        )

    def test_edit_favorite_and_delete_are_owner_scoped(self):
        item = self.create_item()
        update_data = self.clothing_data(
            name='Updated linen shirt',
            image=None,
            fit=ClothingItem.Fit.BAGGY,
        )
        update_data.pop('image')

        response = self.client.post(
            reverse('clothing_edit', args=(item.pk,)),
            update_data,
        )
        self.assertRedirects(
            response,
            reverse('clothing_detail', args=(item.pk,)),
        )
        item.refresh_from_db()
        self.assertEqual(item.name, 'Updated linen shirt')
        self.assertEqual(item.fit, ClothingItem.Fit.BAGGY)
        self.assertTrue(item.image.storage.exists(item.image.name))

        self.assertEqual(
            self.client.get(
                reverse('clothing_delete', args=(item.pk,))
            ).status_code,
            405,
        )

        self.client.post(reverse('clothing_favorite', args=(item.pk,)))
        item.refresh_from_db()
        self.assertTrue(item.is_favorite)

        self.client.force_login(self.other_user)
        response = self.client.post(reverse('clothing_delete', args=(item.pk,)))
        self.assertEqual(response.status_code, 404)
        self.assertTrue(ClothingItem.objects.filter(pk=item.pk).exists())

        self.client.force_login(self.user)
        image_name = item.image.name
        response = self.client.post(reverse('clothing_delete', args=(item.pk,)))
        self.assertRedirects(response, reverse('wardrobe'))
        self.assertFalse(ClothingItem.objects.filter(pk=item.pk).exists())
        self.assertFalse(item.image.storage.exists(image_name))

    def test_invalid_and_oversized_images_are_rejected(self):
        invalid_image = SimpleUploadedFile(
            'not-an-image.png',
            b'not an image',
            content_type='image/png',
        )
        response = self.client.post(
            reverse('clothing_add'),
            self.clothing_data(image=invalid_image),
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(ClothingItem.objects.exists())

        oversized_image = SimpleUploadedFile(
            'too-large.png',
            b'x' * (MAX_IMAGE_SIZE + 1),
            content_type='image/png',
        )
        response = self.client.post(
            reverse('clothing_add'),
            {'image': oversized_image},
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(ClothingItem.objects.exists())

    def test_bulk_scan_uploads_multiple_private_photos(self):
        batch = ScanBatch.objects.create(user=self.user)
        response = self.client.post(
            reverse('scan_batch_upload', args=(batch.pk,)),
            {
                'images': [
                    self.make_image('shirt.png'),
                    self.make_image('jeans.png', color=(10, 30, 180)),
                ]
            },
        )

        self.assertEqual(response.status_code, 201)
        payload = response.json()
        batch.refresh_from_db()
        self.assertEqual(payload['batch_id'], str(batch.pk))
        self.assertEqual(batch.user, self.user)
        self.assertEqual(batch.photos.count(), 2)

        status = self.client.get(
            reverse('scan_status', args=(batch.pk,))
        )
        self.assertEqual(status.json()['counts']['pending'], 2)
        self.assertEqual(status.json()['status'], ScanBatch.Status.UPLOADING)

        self.client.force_login(self.other_user)
        self.assertEqual(
            self.client.get(reverse('scan_status', args=(batch.pk,))).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(
                reverse('scan_photo_image', args=(batch.pk, batch.photos.first().pk))
            ).status_code,
            404,
        )

    def test_bulk_scan_rejects_chunks_larger_than_ten_photos(self):
        batch = ScanBatch.objects.create(user=self.user)
        response = self.client.post(
            reverse('scan_batch_upload', args=(batch.pk,)),
            {'images': [self.make_image(f'{index}.png') for index in range(11)]},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(batch.photos.count(), 0)

    def test_bulk_scan_skips_duplicate_uploads_within_a_batch(self):
        batch = ScanBatch.objects.create(user=self.user)
        first_response = self.client.post(
            reverse('scan_batch_upload', args=(batch.pk,)),
            {'images': [self.make_image()]},
        )
        duplicate_response = self.client.post(
            reverse('scan_batch_upload', args=(batch.pk,)),
            {'images': [self.make_image()]},
        )

        self.assertEqual(first_response.status_code, 201)
        self.assertEqual(duplicate_response.status_code, 200)
        self.assertEqual(batch.photos.count(), 1)
        self.assertEqual(
            duplicate_response.json()['duplicates'],
            ['linen-shirt.png'],
        )

    def test_bulk_scan_rejects_more_than_three_hundred_photos(self):
        batch = ScanBatch.objects.create(user=self.user)
        ScanPhoto.objects.bulk_create(
            [
                ScanPhoto(batch=batch, original_name=f'{index}.png')
                for index in range(300)
            ]
        )

        response = self.client.post(
            reverse('scan_batch_upload', args=(batch.pk,)),
            {'images': [self.make_image()]},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(batch.photos.count(), 300)

    def test_bulk_scan_stays_queued_without_an_ai_key(self):
        batch = ScanBatch.objects.create(user=self.user)
        photo = ScanPhoto.objects.create(
            batch=batch,
            image=self.make_image(),
            original_name='linen-shirt.png',
        )

        with (
            patch('stylist.views.is_configured', return_value=False),
            patch('stylist.views.analyze_clothing') as analyze,
        ):
            response = self.client.post(
                reverse('scan_process_photo', args=(batch.pk, photo.pk))
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(photo.status, ScanPhoto.Status.PENDING)
        self.assertEqual(ClothingItem.objects.count(), 0)
        analyze.assert_not_called()

    def test_bulk_scan_moves_image_to_one_clothing_item_and_is_idempotent(self):
        batch = ScanBatch.objects.create(user=self.user)
        photo = ScanPhoto.objects.create(
            batch=batch,
            image=self.make_image(),
            original_name='linen-shirt.png',
        )

        with (
            patch('stylist.views.is_configured', return_value=True),
            patch(
                'stylist.views.analyze_clothing',
                return_value=self.analysis_details(),
            ) as analyze,
        ):
            response = self.client.post(
                reverse('scan_process_photo', args=(batch.pk, photo.pk))
            )
            repeated_response = self.client.post(
                reverse('scan_process_photo', args=(batch.pk, photo.pk))
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(repeated_response.status_code, 200)
        self.assertEqual(ClothingItem.objects.filter(user=self.user).count(), 1)
        self.assertEqual(analyze.call_count, 1)
        photo.refresh_from_db()
        item = ClothingItem.objects.get()
        self.assertEqual(photo.status, ScanPhoto.Status.COMPLETE)
        self.assertFalse(photo.image)
        self.assertEqual(photo.clothing_item, item)
        self.assertTrue(item.image.storage.exists(item.image.name))
        self.assertEqual(batch.photos.get().status, ScanPhoto.Status.COMPLETE)

    def test_bulk_scan_keeps_rate_limited_photo_retryable(self):
        batch = ScanBatch.objects.create(user=self.user)
        photo = ScanPhoto.objects.create(
            batch=batch,
            image=self.make_image(),
            original_name='linen-shirt.png',
        )

        with (
            patch('stylist.views.is_configured', return_value=True),
            patch(
                'stylist.views.analyze_clothing',
                side_effect=[
                    ImageAnalysisError('Try again shortly.', retryable=True),
                    self.analysis_details(),
                ],
            ) as analyze,
        ):
            first_response = self.client.post(
                reverse('scan_process_photo', args=(batch.pk, photo.pk))
            )
            second_response = self.client.post(
                reverse('scan_process_photo', args=(batch.pk, photo.pk))
            )

        self.assertTrue(first_response.json()['retryable'])
        self.assertEqual(first_response.json()['status'], ScanPhoto.Status.FAILED)
        self.assertEqual(second_response.json()['status'], ScanPhoto.Status.COMPLETE)
        self.assertEqual(analyze.call_count, 2)
        self.assertEqual(ClothingItem.objects.filter(user=self.user).count(), 1)


class OutfitWorkflowTests(TestCase):
    def setUp(self):
        self.media_directory = TemporaryDirectory()
        self.addCleanup(self.media_directory.cleanup)
        media_override = override_settings(MEDIA_ROOT=self.media_directory.name)
        media_override.enable()
        self.addCleanup(media_override.disable)
        self.user = User.objects.create_user(username='outfit-owner')
        self.other_user = User.objects.create_user(username='another-owner')
        self.client.force_login(self.user)

    def make_image(self, name):
        content = BytesIO()
        Image.new('RGB', (8, 8), color=(30, 50, 90)).save(content, format='PNG')
        return SimpleUploadedFile(name, content.getvalue(), content_type='image/png')

    def make_item(self, user, name, category, color, style='casual'):
        return ClothingItem.objects.create(
            user=user,
            image=self.make_image(f'{name}.png'),
            name=name,
            category=category,
            primary_color=color,
            style=style,
            occasion='Casual outing',
            season='Summer',
        )

    def test_outfit_ideas_use_existing_owned_items_and_explain_the_score(self):
        shirt = self.make_item(
            self.user,
            'Black shirt',
            ClothingItem.Category.SHIRT,
            'Black',
        )
        jeans = self.make_item(
            self.user,
            'Blue jeans',
            ClothingItem.Category.JEANS,
            'Blue',
        )
        shoes = self.make_item(
            self.user,
            'White sneakers',
            ClothingItem.Category.SHOES,
            'White',
        )
        other_item = self.make_item(
            self.other_user,
            'Private jacket',
            ClothingItem.Category.JACKET,
            'Brown',
        )

        with (
            patch('stylist.views.search_style_guidance', return_value=[]),
            patch(
                'stylist.views.rank_outfits',
                side_effect=lambda recommendations, *args, **kwargs: recommendations,
            ),
        ):
            response = self.client.get(
                reverse('outfit_generator'),
                {
                    'occasion': 'Casual outing',
                    'season': 'Summer',
                    'style': 'casual',
                    'selected_items': [str(shirt.pk)],
                },
            )

        self.assertEqual(response.status_code, 200)
        recommendations = response.context['recommendations']
        self.assertTrue(recommendations)
        self.assertEqual(
            {item.pk for item in recommendations[0]['items']},
            {shirt.pk, jeans.pk, shoes.pk},
        )
        self.assertNotIn(other_item, recommendations[0]['items'])
        self.assertTrue(0 <= recommendations[0]['score'] <= 100)
        self.assertTrue(recommendations[0]['reasons'])
        self.assertContains(response, 'Black shirt')
        self.assertEqual(response.context['selected_item_ids'], {shirt.pk})
        self.assertRegex(
            response.content.decode(),
            rf'value="{shirt.pk}"\s+checked',
        )

    def test_ai_stylist_only_reorders_supplied_looks_and_sends_no_photos(self):
        shirt = self.make_item(
            self.user,
            'Black shirt',
            ClothingItem.Category.SHIRT,
            'Black',
        )
        jeans = self.make_item(
            self.user,
            'Blue jeans',
            ClothingItem.Category.JEANS,
            'Blue',
        )
        recommendations = [
            {'items': (shirt, jeans), 'score': 70, 'reasons': ['Local reason']},
            {'items': (jeans, shirt), 'score': 65, 'reasons': ['Another reason']},
        ]
        model_response = {
            'choices': [
                {
                    'message': {
                        'content': json.dumps(
                            {
                                'ranked': [
                                    {'look': 2, 'reason': 'A relaxed color pairing.'},
                                    {'look': 99, 'reason': 'Invented look.'},
                                ]
                            }
                        )
                    }
                }
            ]
        }

        with (
            patch(
                'stylist.services.ai_outfit_stylist.groq_config',
                return_value=('test-key', 'https://api.example.test/chat', 'test-model'),
            ),
            patch(
                'stylist.services.ai_outfit_stylist.urlopen',
                return_value=BytesIO(json.dumps(model_response).encode()),
            ) as request_call,
        ):
            ranked = rank_outfits(
                recommendations,
                [{'title': 'Style', 'snippet': 'Use neutral colors.', 'url': 'https://example.test'}],
                selected_items=[shirt],
            )

        self.assertIs(ranked[0]['items'], recommendations[1]['items'])
        self.assertEqual(ranked[0]['reasons'], ['A relaxed color pairing.'])
        self.assertEqual(len(ranked), 2)
        request_payload = json.loads(request_call.call_args.args[0].data)
        prompt = request_payload['messages'][1]['content']
        self.assertIn('Black shirt', prompt)
        self.assertIn('Use neutral colors.', prompt)
        self.assertNotIn('data:image', prompt)

    def test_outfit_generation_rejects_selected_items_owned_by_another_user(self):
        shirt = self.make_item(
            self.user,
            'Black shirt',
            ClothingItem.Category.SHIRT,
            'Black',
        )
        private_shoes = self.make_item(
            self.other_user,
            'Private sneakers',
            ClothingItem.Category.SHOES,
            'White',
        )

        response = self.client.get(
            reverse('outfit_generator'),
            {'selected_items': [str(private_shoes.pk)]},
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['has_generated'])
        self.assertEqual(response.context['recommendations'], [])
        self.assertNotContains(response, f'value="{private_shoes.pk}"')

    def test_outfit_generation_labels_rule_based_fallback_when_ai_fails(self):
        self.make_item(
            self.user,
            'Black shirt',
            ClothingItem.Category.SHIRT,
            'Black',
        )
        self.make_item(
            self.user,
            'Blue jeans',
            ClothingItem.Category.JEANS,
            'Blue',
        )

        with (
            patch('stylist.views.search_style_guidance', return_value=[]),
            patch(
                'stylist.views.rank_outfits',
                side_effect=AIOutfitStylingError('Groq is unavailable.'),
            ),
        ):
            response = self.client.get(
                reverse('outfit_generator'),
                {'occasion': 'Casual outing'},
            )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['ai_used'])
        self.assertContains(response, 'Groq is unavailable.')
        self.assertContains(response, 'no AI ranking was applied')

    def test_user_can_save_a_reviewed_look_and_other_users_cannot_access_it(self):
        shirt = self.make_item(
            self.user,
            'Black shirt',
            ClothingItem.Category.SHIRT,
            'Black',
        )
        jeans = self.make_item(
            self.user,
            'Blue jeans',
            ClothingItem.Category.JEANS,
            'Blue',
        )

        response = self.client.post(
            reverse('outfit_save'),
            {
                'name': 'Weekend look',
                'notes': 'Wear with the new jacket.',
                'occasion': 'Casual outing',
                'season': 'Summer',
                'item_ids': [str(shirt.pk), str(jeans.pk)],
            },
        )

        self.assertRedirects(response, reverse('saved_outfits'))
        outfit = SavedOutfit.objects.get(user=self.user, name='Weekend look')
        self.assertEqual(set(outfit.items.all()), {shirt, jeans})
        self.assertEqual(outfit.notes, 'Wear with the new jacket.')

        self.client.force_login(self.other_user)
        self.assertNotContains(
            self.client.get(reverse('saved_outfits')),
            'Weekend look',
        )
        self.assertEqual(
            self.client.post(
                reverse('outfit_delete', args=(outfit.pk,))
            ).status_code,
            404,
        )

    def test_saving_another_users_piece_is_rejected(self):
        shirt = self.make_item(
            self.user,
            'Black shirt',
            ClothingItem.Category.SHIRT,
            'Black',
        )
        shoes = self.make_item(
            self.other_user,
            'Private sneakers',
            ClothingItem.Category.SHOES,
            'White',
        )

        response = self.client.post(
            reverse('outfit_save'),
            {
                'name': 'Invalid look',
                'item_ids': [str(shirt.pk), str(shoes.pk)],
            },
        )

        self.assertRedirects(response, reverse('outfit_generator'))
        self.assertFalse(SavedOutfit.objects.exists())

    def test_outfit_generation_shows_live_web_style_references(self):
        self.make_item(
            self.user,
            'Black shirt',
            ClothingItem.Category.SHIRT,
            'Black',
        )
        self.make_item(
            self.user,
            'Blue jeans',
            ClothingItem.Category.JEANS,
            'Blue',
        )

        with (
            patch(
                'stylist.views.search_style_guidance',
                return_value=[
                    {
                        'title': 'How to style dark denim',
                        'url': 'https://example.com/style',
                        'snippet': 'Pair denim with a neutral top.',
                    }
                ],
            ) as search,
            patch(
                'stylist.views.rank_outfits',
                side_effect=lambda recommendations, *args, **kwargs: recommendations,
            ) as rank,
        ):
            response = self.client.get(
                reverse('outfit_generator'),
                {'occasion': 'Casual outing'},
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Live web search')
        self.assertContains(response, 'How to style dark denim')
        search.assert_called_once()
        rank.assert_called_once()

    def test_web_search_extracts_safe_search_result_links(self):
        response = BytesIO(
            b'<ol>'
            b'<li class="b_algo"><h2><a href="https://example.com/style">'
            b'Style guide</a></h2><p>Wear <b>neutral</b> colors.</p></li>'
            b'<li class="b_algo"><h2><a href="javascript:alert(1)">Unsafe</a>'
            b'</h2><p>Not a safe link.</p></li>'
            b'</ol>'
        )

        with patch('stylist.services.web_style_search.urlopen', return_value=response):
            results = search_style_guidance(
                {'items': []},
                occasion='Party',
            )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['title'], 'Style guide')
        self.assertEqual(results[0]['url'], 'https://example.com/style')
        self.assertEqual(results[0]['snippet'], 'Wear neutral colors.')


class ImageAnalyzerTests(TestCase):
    def make_image(self):
        content = BytesIO()
        Image.new('RGB', (8, 8), color=(190, 160, 125)).save(content, format='PNG')
        return SimpleUploadedFile(
            'shirt.png',
            content.getvalue(),
            content_type='image/png',
        )

    def test_openai_key_does_not_enable_groq_analysis(self):
        with patch.dict(
            os.environ,
            {'GROQ_API_KEY': '', 'OPENAI_API_KEY': 'legacy-key'},
            clear=True,
        ):
            self.assertFalse(is_configured())

    def test_groq_response_becomes_structured_clothing_attributes(self):
        api_response = {
            'choices': [
                {
                    'message': {
                        'content': json.dumps(
                            {
                                'name': 'Blue denim jeans',
                                'category': 'jeans',
                                'primary_color': 'Indigo blue',
                                'secondary_color': '',
                                'pattern': 'plain',
                                'style': 'casual',
                                'fit': 'baggy',
                                'material': 'Denim',
                                'occasion': ['Casual outing'],
                                'season': ['Spring', 'Autumn'],
                            }
                        )
                    }
                }
            ]
        }
        with (
            patch.dict(
                os.environ,
                {
                    'GROQ_API_KEY': 'test-key',
                    'GROQ_VISION_MODEL': 'qwen/qwen3.8-27b',
                },
            ),
            patch(
                'stylist.services.image_analyzer.urlopen',
                return_value=BytesIO(json.dumps(api_response).encode('utf-8')),
            ) as urlopen_mock,
        ):
            result = analyze_clothing(self.make_image())

        request = urlopen_mock.call_args.args[0]
        request_data = json.loads(request.data)
        self.assertEqual(
            request.full_url,
            'https://api.groq.com/openai/v1/chat/completions',
        )
        self.assertEqual(request_data['model'], 'qwen/qwen3.8-27b')
        self.assertEqual(result['category'], ClothingItem.Category.JEANS)
        self.assertEqual(result['fit'], ClothingItem.Fit.BAGGY)
        self.assertEqual(result['material'], 'Denim')
        self.assertEqual(result['season'], 'Spring, Autumn')
        self.assertIn(
            'data:image/png;base64,',
            request_data['messages'][0]['content'][1]['image_url']['url'],
        )
