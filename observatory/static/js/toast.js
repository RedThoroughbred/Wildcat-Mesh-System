/**
 * Toast Notification System for Wildcat Mesh Observatory
 * Displays real-time notifications for mesh events
 */

class ToastManager {
    constructor() {
        this.container = null;
        this.init();
    }

    init() {
        // Create toast container
        this.container = document.createElement('div');
        this.container.id = 'toast-container';
        this.container.style.cssText = `
            position: fixed;
            top: 80px;
            right: 20px;
            z-index: 9999;
            display: flex;
            flex-direction: column;
            gap: 0.75rem;
            max-width: 400px;
            pointer-events: none;
        `;
        document.body.appendChild(this.container);
    }

    /**
     * Show a toast notification
     * @param {string} message - The message to display
     * @param {string} type - Type: 'success', 'error', 'warning', 'info'
     * @param {number} duration - Duration in ms (default: 4000)
     */
    show(message, type = 'info', duration = 4000) {
        const toast = document.createElement('div');
        toast.className = `toast toast-${type}`;

        // Icon mapping
        const icons = {
            success: '✅',
            error: '❌',
            warning: '⚠️',
            info: 'ℹ️'
        };

        toast.innerHTML = `
            <div class="toast-icon">${icons[type] || icons.info}</div>
            <div class="toast-content">
                <div class="toast-message">${message}</div>
            </div>
            <button class="toast-close" onclick="this.parentElement.remove()">×</button>
        `;

        toast.style.cssText = `
            display: flex;
            align-items: center;
            gap: 0.75rem;
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 1rem;
            box-shadow: 0 8px 16px rgba(0, 0, 0, 0.4);
            pointer-events: auto;
            animation: slideInRight 0.3s ease-out;
            min-width: 300px;
        `;

        // Type-specific styling
        const borderColors = {
            success: '#4CAF50',
            error: '#F44336',
            warning: '#FF9800',
            info: '#1565C0'
        };
        toast.style.borderLeftColor = borderColors[type] || borderColors.info;
        toast.style.borderLeftWidth = '4px';

        this.container.appendChild(toast);

        // Auto-remove after duration
        if (duration > 0) {
            setTimeout(() => {
                toast.style.animation = 'slideOutRight 0.3s ease-out';
                setTimeout(() => toast.remove(), 300);
            }, duration);
        }

        return toast;
    }

    success(message, duration) {
        return this.show(message, 'success', duration);
    }

    error(message, duration) {
        return this.show(message, 'error', duration);
    }

    warning(message, duration) {
        return this.show(message, 'warning', duration);
    }

    info(message, duration) {
        return this.show(message, 'info', duration);
    }

    // Clear all toasts
    clear() {
        this.container.innerHTML = '';
    }
}

// Add toast animations to CSS
const style = document.createElement('style');
style.textContent = `
    @keyframes slideInRight {
        from {
            opacity: 0;
            transform: translateX(100%);
        }
        to {
            opacity: 1;
            transform: translateX(0);
        }
    }

    @keyframes slideOutRight {
        from {
            opacity: 1;
            transform: translateX(0);
        }
        to {
            opacity: 0;
            transform: translateX(100%);
        }
    }

    .toast-icon {
        font-size: 1.5rem;
        flex-shrink: 0;
    }

    .toast-content {
        flex: 1;
        color: var(--text-primary);
    }

    .toast-message {
        font-size: 0.95rem;
        line-height: 1.4;
    }

    .toast-close {
        background: none;
        border: none;
        color: var(--text-secondary);
        font-size: 1.5rem;
        cursor: pointer;
        padding: 0;
        width: 24px;
        height: 24px;
        display: flex;
        align-items: center;
        justify-content: center;
        border-radius: 4px;
        transition: all 0.2s ease;
        flex-shrink: 0;
    }

    .toast-close:hover {
        background: var(--bg-surface);
        color: var(--text-primary);
    }

    @media (max-width: 768px) {
        #toast-container {
            left: 20px;
            right: 20px;
            max-width: none;
        }
    }
`;
document.head.appendChild(style);

// Create global instance
window.toast = new ToastManager();

// Example mesh event listeners (integrate with your WebSocket)
if (typeof socket !== 'undefined') {
    socket.on('new_node', function(data) {
        toast.info(`New node discovered: ${data.name || data.id}`);
    });

    socket.on('node_offline', function(data) {
        toast.warning(`Node offline: ${data.name || data.id}`);
    });

    socket.on('low_battery', function(data) {
        toast.warning(`Low battery alert: ${data.name} (${data.battery}%)`);
    });

    socket.on('message_received', function(data) {
        if (data.important) {
            toast.info(`New message from ${data.sender}`);
        }
    });
}
