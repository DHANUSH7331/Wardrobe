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
    let previewUrl = null;

    if (imageInput && imagePreview && imagePlaceholder) {
        imageInput.addEventListener('change', () => {
            const [file] = imageInput.files;
            if (!file) return;

            if (previewUrl) URL.revokeObjectURL(previewUrl);
            previewUrl = URL.createObjectURL(file);
            imagePreview.src = previewUrl;
            imagePreview.hidden = false;
            imagePlaceholder.hidden = true;
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
