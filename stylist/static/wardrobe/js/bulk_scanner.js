document.addEventListener('DOMContentLoaded', () => {
    const root = document.querySelector('[data-bulk-scanner]');
    if (!root) return;

    const form = root.querySelector('[data-upload-form]');
    const input = root.querySelector('[data-bulk-image-input]');
    const uploadButton = root.querySelector('[data-upload-button]');
    const retryButton = root.querySelector('[data-retry-button]');
    const previewGrid = root.querySelector('[data-preview-grid]');
    const progressSection = root.querySelector('[data-scan-progress]');
    const progressTitle = root.querySelector('[data-progress-title]');
    const progressCounts = root.querySelector('[data-scan-counts]');
    const progressBar = root.querySelector('[data-progress-bar]');
    const statusMessage = root.querySelector('[data-status-message]');
    const photoList = root.querySelector('[data-photo-list]');
    const feedback = root.querySelector('[data-feedback]');
    const csrfToken = form.querySelector('[name=csrfmiddlewaretoken]').value;
    const maxPhotos = Number(root.dataset.maxPhotos);
    const chunkSize = Number(root.dataset.chunkSize);
    const placeholder = '00000000-0000-0000-0000-000000000000';
    let batchId = new URLSearchParams(window.location.search).get('batch');
    let batchStatus = null;
    let photoData = [];
    let isWorking = false;
    let pageActive = true;
    let previewUrls = [];

    const batchUrl = (template, id) => template.replace(placeholder, id);
    const wait = (milliseconds) => new Promise((resolve) => {
        window.setTimeout(resolve, milliseconds);
    });

    function showFeedback(message, isError = false) {
        feedback.textContent = message;
        feedback.classList.toggle('error', isError);
    }

    function setBatchInAddress(id) {
        const address = new URL(window.location.href);
        if (id) address.searchParams.set('batch', id);
        else address.searchParams.delete('batch');
        window.history.replaceState({}, '', address);
    }

    function clearPreviews() {
        previewUrls.forEach((url) => URL.revokeObjectURL(url));
        previewUrls = [];
        previewGrid.replaceChildren();
    }

    function showPreviews(files) {
        clearPreviews();
        files.forEach((file) => {
            const url = URL.createObjectURL(file);
            previewUrls.push(url);
            const card = document.createElement('div');
            card.className = 'bulk-preview-card';
            const image = document.createElement('img');
            image.src = url;
            image.alt = '';
            image.loading = 'lazy';
            const name = document.createElement('span');
            name.textContent = file.name;
            card.append(image, name);
            previewGrid.append(card);
        });
    }

    function renderStatus(data) {
        batchStatus = data.status;
        photoData = data.photos;
        progressSection.hidden = false;
        const counts = data.counts;
        progressTitle.textContent = data.status === 'completed'
            ? 'Scan complete'
            : data.status === 'partial'
                ? 'Some photos need attention'
                : 'Scanning your photos';
        progressCounts.textContent = `${counts.complete} of ${counts.total} added`;
        const processed = counts.complete + counts.failed;
        progressBar.value = counts.total ? Math.round((processed / counts.total) * 100) : 0;
        statusMessage.textContent = `${counts.pending} waiting · ${counts.processing} in progress · ${counts.failed} failed`;
        photoList.replaceChildren();

        data.photos.forEach((photo) => {
            const row = document.createElement('li');
            row.className = `scan-photo-row status-${photo.status}`;
            const image = document.createElement('img');
            image.className = 'scan-photo-thumb';
            image.src = photo.image_url;
            image.alt = '';
            image.loading = 'lazy';
            const copy = document.createElement('div');
            copy.className = 'scan-photo-copy';
            const name = document.createElement('span');
            name.className = 'scan-photo-name';
            name.textContent = photo.name;
            const state = document.createElement('span');
            state.className = 'scan-photo-state';
            state.textContent = photo.status === 'complete' ? 'Added'
                : photo.status === 'failed' ? `Needs retry · ${photo.error}`
                    : photo.status === 'processing' ? 'Analyzing'
                        : 'Waiting';
            copy.append(name, state);
            row.append(image, copy);

            if (photo.clothing_url) {
                const link = document.createElement('a');
                link.className = 'text-link';
                link.href = photo.clothing_url;
                link.textContent = 'View piece';
                row.append(link);
            }
            photoList.append(row);
        });
        retryButton.hidden = !data.photos.some((photo) => photo.status === 'failed');
        uploadButton.disabled = isWorking || counts.total >= maxPhotos;
    }

    async function getStatus() {
        if (!batchId) return null;
        const response = await fetch(
            batchUrl(root.dataset.statusUrl, batchId),
            {headers: {'X-Requested-With': 'XMLHttpRequest'}},
        );
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Could not load this scan.');
        renderStatus(data);
        return data;
    }

    async function processPhoto(photo, automaticRetries) {
        const url = batchUrl(root.dataset.processUrl, batchId)
            .replace(/\/0\/process\/$/, `/${photo.id}/process/`);
        for (let attempt = 0; attempt <= automaticRetries; attempt += 1) {
            let response;
            let result;
            try {
                response = await fetch(url, {
                    method: 'POST',
                    headers: {
                        'X-CSRFToken': csrfToken,
                        'X-Requested-With': 'XMLHttpRequest',
                    },
                });
                result = await response.json();
            } catch {
                showFeedback('Connection lost. Your uploaded photos are saved; reload this page to resume.', true);
                return false;
            }

            if (response.status === 503) {
                showFeedback(result.error || 'AI is not configured.', true);
                return false;
            }
            if (response.status === 409 && result.retryable) {
                await wait(5000);
                continue;
            }
            if (!response.ok) {
                showFeedback(result.error || 'This photo could not be processed.', true);
                return false;
            }
            if (result.status !== 'failed') return true;

            if (!result.retryable || attempt === automaticRetries) return true;
            showFeedback(`Temporary AI limit reached. Retrying ${photo.name}…`);
            const retryDelay = Number(result.retry_after) * 1000;
            await wait(retryDelay > 0 ? retryDelay : 2500 * (attempt + 1));
        }
        return false;
    }

    async function processQueue(retryFailed = false) {
        if (!batchId || isWorking) return;
        isWorking = true;
        uploadButton.disabled = true;
        retryButton.disabled = true;
        showFeedback('Your photos are being analyzed one at a time. You can leave and resume later.');

        try {
            let data = await getStatus();
            const attemptedIds = new Set();
            while (data) {
                const nextPhoto = data.photos.find((photo) => (
                    !attemptedIds.has(photo.id)
                    && (
                        photo.status === 'pending'
                        || (retryFailed && photo.status === 'failed')
                    )
                ));
                if (!nextPhoto) break;
                attemptedIds.add(nextPhoto.id);

                const completedRequest = await processPhoto(nextPhoto, 2);
                if (!completedRequest) break;
                await wait(2500);
                data = await getStatus();
            }
            if (data && data.status === 'completed') {
                showFeedback('All photos were analyzed and added to your wardrobe.');
            } else if (data && data.status === 'partial') {
                showFeedback('The scan stopped with failed photos. You can retry them or continue later.', true);
            } else if (data && data.counts.pending === 0 && data.counts.processing === 0) {
                showFeedback('There are no photos waiting to scan.');
            }
        } catch (error) {
            showFeedback(error.message, true);
        } finally {
            isWorking = false;
            retryButton.disabled = false;
            if (batchId) {
                try {
                    await getStatus();
                } catch (error) {
                    showFeedback(error.message, true);
                }
            }
            uploadButton.disabled = batchStatus === 'completed'
                || photoData.length >= maxPhotos;
        }
    }

    async function uploadFiles(files) {
        if (!files.length) {
            showFeedback('Choose at least one photo.', true);
            return;
        }
        if (files.length > maxPhotos) {
            showFeedback(`Select no more than ${maxPhotos} photos in one scan.`, true);
            return;
        }
        if (files.some((file) => file.size > 10 * 1024 * 1024)) {
            showFeedback('Each photo must be 10 MB or smaller.', true);
            return;
        }

        isWorking = true;
        uploadButton.disabled = true;
        retryButton.disabled = true;
        showFeedback(`Uploading ${files.length} photo${files.length === 1 ? '' : 's'}…`);

        try {
            if (batchStatus === 'completed') {
                batchId = null;
                batchStatus = null;
                photoData = [];
                setBatchInAddress(null);
                progressSection.hidden = true;
            }
            const existingCount = photoData.length;
            if (existingCount + files.length > maxPhotos) {
                throw new Error(`This scan already has ${existingCount} photos; the limit is ${maxPhotos}.`);
            }
            if (!batchId) {
                const response = await fetch(root.dataset.createUrl, {
                    method: 'POST',
                    headers: {
                        'X-CSRFToken': csrfToken,
                        'X-Requested-With': 'XMLHttpRequest',
                    },
                });
                const result = await response.json();
                if (!response.ok) throw new Error(result.error || 'Could not start a scan.');
                batchId = result.batch_id;
                setBatchInAddress(batchId);
            }

            for (let offset = 0; offset < files.length; offset += chunkSize) {
                const chunk = files.slice(offset, offset + chunkSize);
                const formData = new FormData();
                formData.append('csrfmiddlewaretoken', csrfToken);
                chunk.forEach((file) => formData.append('images', file));
                const url = batchUrl(root.dataset.batchUploadUrl, batchId);
                const response = await fetch(url, {
                    method: 'POST',
                    headers: {
                        'X-CSRFToken': csrfToken,
                        'X-Requested-With': 'XMLHttpRequest',
                    },
                    body: formData,
                });
                const result = await response.json();
                if (!response.ok) {
                    const fileErrors = (result.file_errors || [])
                        .map((item) => `${item.name}: ${item.error}`)
                        .join(' · ');
                    throw new Error([result.error, fileErrors].filter(Boolean).join(' '));
                }
                batchId = result.batch_id;
                setBatchInAddress(batchId);
                const notices = [
                    ...result.duplicates.map((name) => `${name} was already uploaded and was skipped.`),
                    ...result.file_errors.map((item) => `${item.name}: ${item.error}`),
                ];
                if (notices.length) {
                    showFeedback(notices.join(' · '), result.file_errors.length > 0);
                } else {
                    showFeedback(`Uploaded ${Math.min(offset + chunk.length, files.length)} of ${files.length} photos.`);
                }
                await getStatus();
            }

            input.value = '';
            clearPreviews();
            isWorking = false;
            await processQueue();
        } catch (error) {
            showFeedback(error.message, true);
        } finally {
            isWorking = false;
            uploadButton.disabled = batchStatus === 'completed'
                || photoData.length >= maxPhotos;
            retryButton.disabled = false;
        }
    }

    input.addEventListener('change', () => {
        showPreviews(Array.from(input.files));
        if (input.files.length) {
            showFeedback(`${input.files.length} photo${input.files.length === 1 ? '' : 's'} selected.`);
        }
    });

    form.addEventListener('submit', (event) => {
        event.preventDefault();
        uploadFiles(Array.from(input.files));
    });

    retryButton.addEventListener('click', () => processQueue(true));

    window.addEventListener('pagehide', () => {
        pageActive = false;
        clearPreviews();
    });

    if (batchId) {
        (async () => {
            try {
                let data = await getStatus();
                while (pageActive && data && data.counts.processing > 0) {
                    await wait(5000);
                    data = await getStatus();
                }
                if (pageActive && data && data.counts.pending > 0) {
                    processQueue();
                }
            } catch (error) {
                showFeedback(error.message, true);
            }
        })();
    }
});
