(() => {
    const MAX_UPLOAD_BYTES = 3_500_000;
    const MAX_SOURCE_BYTES = 20 * 1024 * 1024;
    const MAX_IMAGE_EDGE = 1024;

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

    const analysisPanel = document.querySelector('[data-analyze-item]');
    if (analysisPanel) {
        const message = analysisPanel.querySelector('[data-analysis-message]');
        const retry = analysisPanel.querySelector('[data-analysis-retry]');
        const csrf = analysisPanel.querySelector('[name="csrfmiddlewaretoken"]')?.value;
        let analyzing = false;

        const runAnalysis = async () => {
            if (analyzing) return;
            analyzing = true;
            retry.hidden = true;
            message.textContent = 'Photo saved. AI is analyzing it; the app remains usable while this runs…';
            try {
                const response = await fetch(analysisPanel.dataset.url, {
                    method: 'POST',
                    headers: {
                        'X-CSRFToken': csrf,
                        'X-Requested-With': 'XMLHttpRequest',
                    },
                });
                let result;
                try {
                    result = await response.json();
                } catch {
                    throw new Error('The image service returned an unreadable response.');
                }
                if (!response.ok) throw new Error(result.error || 'AI analysis did not finish.');
                message.textContent = 'Analysis complete. Updating clothing details…';
                window.location.replace(window.location.pathname);
            } catch (error) {
                message.textContent = `Photo is saved, but analysis did not finish: ${error.message}`;
                retry.hidden = false;
            } finally {
                analyzing = false;
            }
        };

        retry.addEventListener('click', runAnalysis);
        const cleanUrl = new URL(window.location.href);
        if (cleanUrl.searchParams.has('analyze')) {
            cleanUrl.searchParams.delete('analyze');
            window.history.replaceState({}, '', cleanUrl);
            runAnalysis();
        }
    }

    const outfitImageGenerator = document.querySelector('[data-outfit-image-generator]');
    if (outfitImageGenerator) {
        const status = outfitImageGenerator.querySelector('[data-outfit-image-status]');
        const image = outfitImageGenerator.querySelector('[data-outfit-image-result]');
        const generateButton = outfitImageGenerator.querySelector('[data-outfit-image-trigger]');
        const csrf = outfitImageGenerator.querySelector('[name="csrfmiddlewaretoken"]')?.value;
        const itemIds = outfitImageGenerator.dataset.itemIds
            .split(',')
            .filter((itemId) => /^\d+$/.test(itemId));
        let generating = false;

        const generateImage = async () => {
            if (generating) return;
            generating = true;
            generateButton.disabled = true;
            generateButton.hidden = true;
            status.textContent = 'Generating a men’s outfit image. This can take a little while…';
            const formData = new FormData();
            itemIds.forEach((itemId) => formData.append('item_ids', itemId));
            formData.append('occasion', outfitImageGenerator.dataset.occasion);
            formData.append('season', outfitImageGenerator.dataset.season);
            formData.append('style', outfitImageGenerator.dataset.style);

            try {
                const response = await fetch(outfitImageGenerator.dataset.url, {
                    method: 'POST',
                    headers: {
                        'X-CSRFToken': csrf,
                        'X-Requested-With': 'XMLHttpRequest',
                    },
                    body: formData,
                });
                const result = await response.json();
                if (!response.ok) {
                    throw new Error(result.error || 'Image generation did not finish.');
                }
                if (typeof result.image_data_url !== 'string'
                    || !result.image_data_url.startsWith('data:image/')) {
                    throw new Error('The image service returned no usable image.');
                }
                image.src = result.image_data_url;
                image.hidden = false;
                status.textContent = 'AI-generated men’s outfit preview.';
            } catch (error) {
                status.textContent = `AI image could not be generated: ${error.message}`;
                generateButton.textContent = 'Try image generation again';
                generateButton.hidden = false;
            } finally {
                generateButton.disabled = false;
                generating = false;
            }
        };

        generateButton.addEventListener('click', generateImage);
        if (outfitImageGenerator.dataset.autoGenerate === 'true') {
            const cleanUrl = new URL(window.location.href);
            cleanUrl.searchParams.delete('generate_image');
            window.history.replaceState({}, '', cleanUrl);
            generateImage();
        }
    }

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
                const optimized = await window.AtelierImageTools.optimizeImage(imageInput.files[0]);
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
