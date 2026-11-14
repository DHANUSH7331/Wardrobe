from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.core.validators import FileExtensionValidator

from .models import ClothingItem
from .services.outfit_recommender import OCCASION_CHOICES, SEASON_CHOICES
from .validators import validate_clothing_image


class RegistrationForm(UserCreationForm):
    email = forms.EmailField(
        required=True,
        widget=forms.EmailInput(attrs={'autocomplete': 'email'}),
    )

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ('username', 'email')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['username'].widget.attrs.update(
            {'autocomplete': 'username'}
        )
        self.fields['password1'].widget.attrs.update(
            {'autocomplete': 'new-password'}
        )
        self.fields['password2'].widget.attrs.update(
            {'autocomplete': 'new-password'}
        )


class ClothingItemForm(forms.ModelForm):
    class Meta:
        model = ClothingItem
        fields = (
            'name',
            'image',
            'category',
            'primary_color',
            'secondary_color',
            'pattern',
            'style',
            'fit',
            'material',
            'occasion',
            'season',
        )
        widgets = {
            'name': forms.TextInput(attrs={'placeholder': 'e.g. Linen white shirt'}),
            'image': forms.FileInput(
                attrs={
                    'accept': 'image/jpeg,image/png,image/webp',
                    'capture': 'environment',
                    'data-image-input': 'true',
                }
            ),
            'primary_color': forms.TextInput(
                attrs={'placeholder': 'e.g. Navy blue'}
            ),
            'secondary_color': forms.TextInput(
                attrs={'placeholder': 'Optional'}
            ),
            'occasion': forms.TextInput(
                attrs={'placeholder': 'e.g. Office, Casual outing'}
            ),
            'season': forms.TextInput(
                attrs={'placeholder': 'e.g. Spring, Autumn'}
            ),
            'material': forms.TextInput(
                attrs={'placeholder': 'Optional, e.g. Cotton'}
            ),
        }


class ClothingPhotoForm(forms.Form):
    image = forms.ImageField(
        label='Piece photo',
        validators=[
            FileExtensionValidator(
                allowed_extensions=('jpg', 'jpeg', 'png', 'webp')
            ),
            validate_clothing_image,
        ],
        widget=forms.FileInput(
            attrs={
                'accept': 'image/jpeg,image/png,image/webp',
                'capture': 'environment',
                'data-image-input': 'true',
            }
        ),
    )


class OutfitGenerationForm(forms.Form):
    occasion = forms.ChoiceField(
        choices=OCCASION_CHOICES,
        required=False,
        widget=forms.Select(attrs={'id': 'outfit-occasion'}),
    )
    season = forms.ChoiceField(
        choices=SEASON_CHOICES,
        required=False,
        widget=forms.Select(attrs={'id': 'outfit-season'}),
    )
    style = forms.ChoiceField(
        choices=(('', 'Any style'), *ClothingItem.Style.choices),
        required=False,
        widget=forms.Select(attrs={'id': 'outfit-style'}),
    )

    def __init__(self, *args, items=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['selected_items'] = forms.ModelMultipleChoiceField(
            queryset=items if items is not None else ClothingItem.objects.none(),
            required=False,
        )


class SavedOutfitForm(forms.Form):
    name = forms.CharField(max_length=100)
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'rows': 3}),
    )
    occasion = forms.ChoiceField(choices=OCCASION_CHOICES, required=False)
    season = forms.ChoiceField(choices=SEASON_CHOICES, required=False)
