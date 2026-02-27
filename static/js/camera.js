// Camera handling utilities
class CameraManager {
    constructor(videoElement, canvasElement, options = {}) {
        this.video = videoElement;
        this.canvas = canvasElement;
        this.options = {
            width: options.width || 640,
            height: options.height || 480,
            facingMode: options.facingMode || 'user',
            ...options
        };
        this.stream = null;
        this.isActive = false;
    }

    // Initialize camera
    async init() {
        try {
            this.stream = await navigator.mediaDevices.getUserMedia({
                video: {
                    width: { ideal: this.options.width },
                    height: { ideal: this.options.height },
                    facingMode: this.options.facingMode
                }
            });

            this.video.srcObject = this.stream;
            this.isActive = true;

            return new Promise((resolve) => {
                this.video.onloadedmetadata = () => {
                    this.video.play();
                    resolve(true);
                };
            });
        } catch (error) {
            console.error('Camera initialization error:', error);
            this.isActive = false;
            throw error;
        }
    }

    // Capture image from camera
    capture() {
        if (!this.isActive) {
            throw new Error('Camera not initialized');
        }

        this.canvas.width = this.video.videoWidth;
        this.canvas.height = this.video.videoHeight;
        const context = this.canvas.getContext('2d');
        context.drawImage(this.video, 0, 0, this.canvas.width, this.canvas.height);

        return {
            dataURL: this.canvas.toDataURL('image/jpeg'),
            blob: this.dataURLToBlob(this.canvas.toDataURL('image/jpeg')),
            width: this.canvas.width,
            height: this.canvas.height
        };
    }

    // Convert dataURL to Blob
    dataURLToBlob(dataURL) {
        const parts = dataURL.split(';base64,');
        const contentType = parts[0].split(':')[1];
        const raw = window.atob(parts[1]);
        const rawLength = raw.length;
        const uInt8Array = new Uint8Array(rawLength);

        for (let i = 0; i < rawLength; ++i) {
            uInt8Array[i] = raw.charCodeAt(i);
        }

        return new Blob([uInt8Array], { type: contentType });
    }

    // Stop camera
    stop() {
        if (this.stream) {
            this.stream.getTracks().forEach(track => track.stop());
            this.isActive = false;
        }
    }

    // Switch camera (front/back)
    async switchCamera() {
        this.stop();
        this.options.facingMode = this.options.facingMode === 'user' ? 'environment' : 'user';
        await this.init();
    }

    // Take multiple photos (for better recognition)
    async captureBurst(count = 3, delay = 500) {
        const photos = [];
        for (let i = 0; i < count; i++) {
            photos.push(this.capture());
            if (i < count - 1) {
                await new Promise(resolve => setTimeout(resolve, delay));
            }
        }
        return photos;
    }
}

// Export for use in other files
if (typeof module !== 'undefined' && module.exports) {
    module.exports = CameraManager;
}