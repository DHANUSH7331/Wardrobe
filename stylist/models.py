import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.db import models
from django.db.models.signals import post_delete
from django.dispatch import receiver

from .validators import validate_clothing_image


class ClothingItem(models.Model):
    class Category(models.TextChoices):
        SHIRT = 'shirt', 'Shirt'
        T_SHIRT = 't-shirt', 'T-shirt'
        JEANS = 'jeans', 'Jeans'
        TROUSERS = 'trousers', 'Trousers'
        JACKET = 'jacket', 'Jacket'
        SHOES = 'shoes', 'Shoes'
        HOODIE = 'hoodie', 'Hoodie'
        SWEATER = 'sweater', 'Sweater'
        DRESS = 'dress', 'Dress'
        SKIRT = 'skirt', 'Skirt'
        SHORTS = 'shorts', 'Shorts'
        OTHER = 'other', 'Other'

    class Pattern(models.TextChoices):
        PLAIN = 'plain', 'Plain'
        STRIPED = 'striped', 'Striped'
        CHECKED = 'checked', 'Checked'
        PRINTED = 'printed', 'Printed'
        TEXTURED = 'textured', 'Textured'
        OTHER = 'other', 'Other'

    class Style(models.TextChoices):
        CASUAL = 'casual', 'Casual'
        FORMAL = 'formal', 'Formal'
        STREETWEAR = 'streetwear', 'Streetwear'
        TRADITIONAL = 'traditional', 'Traditional'
        SPORTY = 'sporty', 'Sporty'
        OTHER = 'other', 'Other'

    class Fit(models.TextChoices):
        SLIM = 'slim', 'Slim'
        REGULAR = 'regular', 'Regular'
        RELAXED = 'relaxed', 'Relaxed'
        OVERSIZED = 'oversized', 'Oversized'
        BAGGY = 'baggy', 'Baggy'
        OTHER = 'other', 'Other'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='clothing_items',
    )
    image = models.ImageField(
        upload_to='clothing/%Y/%m/',
        validators=[
            FileExtensionValidator(
                allowed_extensions=('jpg', 'jpeg', 'png', 'webp')
            ),
            validate_clothing_image,
        ],
    )
    name = models.CharField(max_length=100)
    category = models.CharField(max_length=20, choices=Category.choices)
    primary_color = models.CharField(max_length=40)
    secondary_color = models.CharField(max_length=40, blank=True)
    pattern = models.CharField(
        max_length=20,
        choices=Pattern.choices,
        blank=True,
    )
    style = models.CharField(
        max_length=20,
        choices=Style.choices,
        blank=True,
    )
    fit = models.CharField(
        max_length=20,
        choices=Fit.choices,
        blank=True,
    )
    material = models.CharField(max_length=80, blank=True)
    occasion = models.CharField(max_length=160, blank=True)
    season = models.CharField(max_length=80, blank=True)
    is_favorite = models.BooleanField(default=False)
    date_added = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('-date_added', '-pk')

    def __str__(self):
        return self.name


class SavedOutfit(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='saved_outfits',
    )
    items = models.ManyToManyField(
        ClothingItem,
        related_name='saved_outfits',
    )
    name = models.CharField(max_length=100)
    notes = models.TextField(blank=True)
    occasion = models.CharField(max_length=40, blank=True)
    season = models.CharField(max_length=20, blank=True)
    is_favorite = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('-created_at', '-pk')

    def clean(self):
        super().clean()
        if self.pk and self.items.exclude(user=self.user).exists():
            raise ValidationError('An outfit can only use your own wardrobe items.')

    def __str__(self):
        return self.name


class ScanBatch(models.Model):
    class Status(models.TextChoices):
        UPLOADING = 'uploading', 'Uploading'
        PROCESSING = 'processing', 'Processing'
        COMPLETED = 'completed', 'Completed'
        PARTIAL = 'partial', 'Completed with errors'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='scan_batches',
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.UPLOADING,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('-created_at',)


class ScanPhoto(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        PROCESSING = 'processing', 'Processing'
        COMPLETE = 'complete', 'Complete'
        FAILED = 'failed', 'Failed'

    batch = models.ForeignKey(
        ScanBatch,
        on_delete=models.CASCADE,
        related_name='photos',
    )
    image = models.ImageField(
        upload_to='scan-queue/%Y/%m/',
        validators=[
            FileExtensionValidator(
                allowed_extensions=('jpg', 'jpeg', 'png', 'webp')
            ),
            validate_clothing_image,
        ],
        blank=True,
    )
    original_name = models.CharField(max_length=255)
    upload_hash = models.CharField(max_length=64, default=uuid.uuid4)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    last_error = models.CharField(max_length=500, blank=True)
    clothing_item = models.ForeignKey(
        ClothingItem,
        on_delete=models.SET_NULL,
        related_name='scan_photos',
        blank=True,
        null=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('created_at', 'pk')
        constraints = [
            models.UniqueConstraint(
                fields=('batch', 'upload_hash'),
                name='unique_scan_photo_per_batch',
            ),
        ]


@receiver(post_delete, sender=ClothingItem)
def remove_clothing_image(sender, instance, **kwargs):
    if instance.image:
        instance.image.delete(save=False)


@receiver(post_delete, sender=ScanPhoto)
def remove_scan_photo(sender, instance, **kwargs):
    if instance.image:
        instance.image.delete(save=False)
