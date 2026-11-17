(() => {
    const MAX_UPLOAD_BYTES = 3_500_000;
    const MAX_SOURCE_BYTES = 20 * 1024 * 1024;
    const MAX_IMAGE_EDGE = 1280;

    function canvasBlob(canvas, quality) {
        return new Promise((resolve, reject) => {
            canvas.toBlob((blob) => {
                if (blob) resolve(blob);
                else reject(new Error('The photo could not be optimized on this device.'));
            }, 'image/jpeg', quality);
        });
    }

    async function optimizeImage(file) {
        if (!file.type.startsWith('image/')) {
            throw new Error('Choose a JPEG, PNG, or WebP photo.');
        }
        if (file.size > MAX_SOURCE_BYTES) {
            throw new Error('This photo is over 20 MB. Choose a smaller photo to avoid mobile memory problems.');
        }

        let bitmap;
        try {
            // Ask the browser's image decoder to downsample before exposing a
            // full-size bitmap to JavaScript. Phone photos can be 12–50 MP;
            // decoding those at native size can freeze or kill the PWA tab.
            bitmap = await createImageBitmap(file, {
                resizeWidth: MAX_IMAGE_EDGE,
                resizeQuality: 'low',
            });
            const scale = Math.min(1, MAX_IMAGE_EDGE / Math.max(bitmap.width, bitmap.height));
            let width = Math.max(1, Math.round(bitmap.width * scale));
            let height = Math.max(1, Math.round(bitmap.height * scale));
            const canvas = document.createElement('canvas');
            const context = canvas.getContext('2d', {alpha: false});
            if (!context) throw new Error('Photo optimization is not supported by this browser.');

            let blob;
            for (let pass = 0; pass < 5; pass += 1) {
                canvas.width = width;
                canvas.height = height;
                context.drawImage(bitmap, 0, 0, width, height);
                const quality = Math.max(0.5, 0.78 - pass * 0.07);
                blob = await canvasBlob(canvas, quality);
                if (blob.size <= MAX_UPLOAD_BYTES) break;
                width = Math.max(1, Math.round(width * 0.8));
                height = Math.max(1, Math.round(height * 0.8));
            }
            canvas.width = 1;
            canvas.height = 1;
            if (!blob || blob.size > MAX_UPLOAD_BYTES) {
                throw new Error('This photo could not be reduced enough. Choose a smaller photo.');
            }
            const basename = file.name.replace(/\.[^.]+$/, '') || 'wardrobe-photo';
            return new File([blob], `${basename}.jpg`, {
                type: 'image/jpeg',
                lastModified: file.lastModified,
            });
        } catch (error) {
            if (error instanceof DOMException && error.name === 'QuotaExceededError') {
                throw new Error('This photo is too large for available phone memory. Choose a smaller photo.');
            }
            throw error;
        } finally {
            bitmap?.close();
        }
    }

    window.AtelierImageTools = {optimizeImage};
})();

document.addEventListener('DOMContentLoaded', () => {
    if ('serviceWorker' in navigator) {
        navigator.serviceWorker.register('/service-worker.js')
            .catch((error) => console.error('Service worker registration failed:', error));
    }

    const installButton = document.querySelector('[data-pwa-install]');
    let installPrompt = null;
    if (installButton) {
        window.addEventListener('beforeinstallprompt', (event) => {
            event.preventDefault();
            installPrompt = event;
            installButton.hidden = false;
        });

        installButton.addEventListener('click', async () => {
            if (!installPrompt) return;
            installPrompt.prompt();
            await installPrompt.userChoice;
            installPrompt = null;
            installButton.hidden = true;
        });

        window.addEventListener('appinstalled', () => {
            installPrompt = null;
            installButton.hidden = true;
        });
    }

    const imageInput = document.querySelector('[data-image-input]');
    const imagePreview = document.getElementById('image-preview');
    const imagePlaceholder = document.getElementById('image-placeholder');
    const imageFeedback = document.querySelector('[data-image-feedback]');
    let previewUrl = null;

    if (imageInput && imagePreview && imagePlaceholder) {
        imageInput.addEventListener('change', () => {
            const [file] = imageInput.files;
            if (!file) return;

            if (previewUrl) URL.revokeObjectURL(previewUrl);
            previewUrl = null;
            imagePreview.removeAttribute('src');
            imagePreview.hidden = true;
            imagePlaceholder.hidden = false;
            if (imageFeedback) imageFeedback.textContent = 'Photo selected; it will be optimized before upload.';
        });

        const form = imageInput.closest('form');
        const submitButton = form?.querySelector('[type="submit"]');
        let submittingOptimized = false;
        form?.addEventListener('submit', async (event) => {
            if (submittingOptimized || !imageInput.files?.length) return;
            event.preventDefault();
            if (submitButton) submitButton.disabled = true;
            if (imageFeedback) imageFeedback.textContent = 'Optimizing photo on this device…';
            try {
                const optimized = await optimizeImage(imageInput.files[0]);
                const transfer = new DataTransfer();
                transfer.items.add(optimized);
                imageInput.files = transfer.files;
                if (previewUrl) URL.revokeObjectURL(previewUrl);
                previewUrl = URL.createObjectURL(optimized);
                imagePreview.src = previewUrl;
                imagePreview.hidden = false;
                imagePlaceholder.hidden = true;
                submittingOptimized = true;
                if (imageFeedback) imageFeedback.textContent = 'Photo optimized; uploading…';
                if (event.submitter) form.requestSubmit(event.submitter);
                else form.requestSubmit();
            } catch (error) {
                if (imageFeedback) imageFeedback.textContent = error.message;
                if (submitButton) submitButton.disabled = false;
            }
        });
    }

    document.querySelectorAll('form[data-confirm]').forEach((form) => {
        form.addEventListener('submit', (event) => {
            if (!window.confirm(form.dataset.confirm)) event.preventDefault();
        });
    });

    window.addEventListener('pagehide', () => {
        if (previewUrl) URL.revokeObjectURL(previewUrl);
    });
});
