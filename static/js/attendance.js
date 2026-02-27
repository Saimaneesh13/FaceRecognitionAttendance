// Attendance marking utilities
class AttendanceManager {
    constructor() {
        this.apiEndpoint = '/api/check-attendance';
    }

    // Mark attendance with image
    async markAttendance(imageBlob) {
        const formData = new FormData();
        formData.append('image', imageBlob, 'face.jpg');

        try {
            const response = await fetch(this.apiEndpoint, {
                method: 'POST',
                body: formData
            });

            const data = await response.json();

            if (data.error) {
                throw new Error(data.error);
            }

            return data.results;
        } catch (error) {
            console.error('Attendance marking error:', error);
            throw error;
        }
    }

    // Get today's attendance
    async getTodayAttendance() {
        try {
            const response = await fetch('/api/attendance/today');
            const data = await response.json();
            return data;
        } catch (error) {
            console.error('Error fetching today\'s attendance:', error);
            throw error;
        }
    }

    // Update attendance list UI
    updateAttendanceList(results, containerId = 'attendanceList') {
        const container = document.getElementById(containerId);
        if (!container) return;

        results.forEach(result => {
            const statusClass = result.status === 'new' ? 'success' : 'info';
            const statusText = result.status === 'new' ? '✓ Marked' : '✓ Already Marked';

            const item = document.createElement('div');
            item.className = `attendance-item alert alert-${statusClass} mb-2`;
            item.innerHTML = `
                <div class="d-flex justify-content-between align-items-center">
                    <div>
                        <strong>${result.name}</strong><br>
                        <small>ID: ${result.student_id}</small>
                    </div>
                    <div class="text-end">
                        <span class="badge bg-${statusClass}">${statusText}</span><br>
                        <small>${new Date().toLocaleTimeString()}</small>
                    </div>
                </div>
            `;

            container.insertBefore(item, container.firstChild);

            // Keep only last 10 items
            while (container.children.length > 10) {
                container.removeChild(container.lastChild);
            }
        });
    }

    // Show notification
    showNotification(message, type = 'info') {
        const alertDiv = document.createElement('div');
        alertDiv.className = `alert alert-${type} alert-dismissible fade show position-fixed top-0 end-0 m-3`;
        alertDiv.style.zIndex = '9999';
        alertDiv.innerHTML = `
            ${message}
            <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
        `;

        document.body.appendChild(alertDiv);

        setTimeout(() => {
            alertDiv.remove();
        }, 5000);
    }
}

// Export for use in other files
if (typeof module !== 'undefined' && module.exports) {
    module.exports = AttendanceManager;
}