"""Private, persistent image storage for the Vercel deployment."""

import mimetypes
import os
from pathlib import PurePosixPath

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.files.base import ContentFile
from django.core.files.storage import Storage
from django.urls import reverse
from django.utils.crypto import get_random_string


def image_storage_configured():
    if not settings.USE_VERCEL_BLOB:
        return True
    return bool(
        os.environ.get('BLOB_STORE_ID')
        or os.environ.get('BLOB_READ_WRITE_TOKEN')
        or os.environ.get('VERCEL_BLOB_READ_WRITE_TOKEN')
    )


class VercelPrivateBlobStorage(Storage):
    """Store image files in private Vercel Blob, not a function's filesystem."""

    @staticmethod
    def _client():
        if not image_storage_configured():
            raise ImproperlyConfigured(
                'Connect a private Vercel Blob store to this project before '
                'uploading photos.'
            )
        try:
            from vercel.blob import sync as blob
        except ImportError as error:
            raise ImproperlyConfigured(
                'Install the vercel-blob package to use persistent photo storage.'
            ) from error
        return blob

    def get_available_name(self, name, max_length=None):
        # Add an application-side suffix so the final database path stays within
        # ImageField's max_length without making a remote existence request.
        directory, filename = os.path.split(name)
        stem, extension = os.path.splitext(filename)
        unique_suffix = f'_{get_random_string(12)}'
        prefix_length = len(directory) + (1 if directory else 0)
        allowed_stem_length = (
            max_length - prefix_length - len(extension) - len(unique_suffix)
            if max_length
            else len(stem)
        )
        if allowed_stem_length < 1:
            raise ValueError('The image upload path is too long to store.')
        safe_name = f'{stem[:allowed_stem_length]}{unique_suffix}{extension}'
        return os.path.join(directory, safe_name).replace('\\', '/')

    def _save(self, name, content):
        blob = self._client()
        content.seek(0)
        result = blob.put_blob(
            name,
            content,
            access='private',
            content_length=content.size,
            content_type=(
                getattr(content, 'content_type', None)
                or mimetypes.guess_type(name)[0]
                or 'application/octet-stream'
            ),
            add_random_suffix=False,
        )
        return result.pathname

    def _open(self, name, mode='rb'):
        if mode not in {'rb', 'r'}:
            raise ValueError('Private Blob images can only be opened for reading.')
        result = self._client().get_blob(name, access='private')
        return ContentFile(result.body, name=PurePosixPath(name).name)

    def exists(self, name):
        try:
            self._client().head_blob(name)
            return True
        except FileNotFoundError:
            return False

    def delete(self, name):
        self._client().delete_blob(name)

    def size(self, name):
        return self._client().head_blob(name).size

    def url(self, name):
        return reverse('private_media', kwargs={'name': name})
