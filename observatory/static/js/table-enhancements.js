/**
 * Enhanced Table Features for Wildcat Mesh Observatory
 * Provides sorting, filtering, and export functionality
 */

class EnhancedTable {
    constructor(tableId, options = {}) {
        this.table = document.getElementById(tableId);
        if (!this.table) return;

        this.tbody = this.table.querySelector('tbody');
        this.thead = this.table.querySelector('thead');
        this.rows = Array.from(this.tbody.querySelectorAll('tr'));
        this.sortDirection = {};
        this.options = {
            sortable: options.sortable !== false,
            exportable: options.exportable !== false,
            ...options
        };

        this.init();
    }

    init() {
        if (this.options.sortable) {
            this.addSortingFeatures();
        }
        if (this.options.exportable) {
            this.addExportButton();
        }
    }

    addSortingFeatures() {
        const headers = this.thead.querySelectorAll('th');
        headers.forEach((header, index) => {
            if (header.dataset.sortable !== 'false') {
                header.style.cursor = 'pointer';
                header.style.userSelect = 'none';
                header.style.position = 'relative';
                header.style.paddingRight = '1.5rem';

                // Add sort indicator
                const indicator = document.createElement('span');
                indicator.className = 'sort-indicator';
                indicator.innerHTML = '⇅';
                indicator.style.cssText = `
                    position: absolute;
                    right: 0.5rem;
                    opacity: 0.3;
                    transition: opacity 0.2s;
                `;
                header.appendChild(indicator);

                header.addEventListener('mouseenter', () => {
                    indicator.style.opacity = '0.6';
                });

                header.addEventListener('mouseleave', () => {
                    if (!this.sortDirection[index]) {
                        indicator.style.opacity = '0.3';
                    }
                });

                header.addEventListener('click', () => {
                    this.sortTable(index, header);
                });
            }
        });
    }

    sortTable(columnIndex, header) {
        const currentDirection = this.sortDirection[columnIndex] || 'none';
        const newDirection = currentDirection === 'asc' ? 'desc' : 'asc';

        // Reset all indicators
        this.thead.querySelectorAll('.sort-indicator').forEach(ind => {
            ind.innerHTML = '⇅';
            ind.style.opacity = '0.3';
        });

        // Update current indicator
        const indicator = header.querySelector('.sort-indicator');
        indicator.innerHTML = newDirection === 'asc' ? '▲' : '▼';
        indicator.style.opacity = '1';
        indicator.style.color = 'var(--primary)';

        // Sort rows
        this.rows.sort((a, b) => {
            const aCell = a.cells[columnIndex];
            const bCell = b.cells[columnIndex];

            if (!aCell || !bCell) return 0;

            let aValue = aCell.textContent.trim();
            let bValue = bCell.textContent.trim();

            // Try to parse as number
            const aNum = parseFloat(aValue.replace(/[^0-9.-]/g, ''));
            const bNum = parseFloat(bValue.replace(/[^0-9.-]/g, ''));

            if (!isNaN(aNum) && !isNaN(bNum)) {
                return newDirection === 'asc' ? aNum - bNum : bNum - aNum;
            }

            // String comparison
            return newDirection === 'asc'
                ? aValue.localeCompare(bValue)
                : bValue.localeCompare(aValue);
        });

        // Re-append sorted rows
        this.rows.forEach(row => this.tbody.appendChild(row));

        // Update sort direction
        this.sortDirection = { [columnIndex]: newDirection };

        // Show toast
        if (window.toast) {
            toast.info(`Sorted by ${header.textContent.trim()}`, 2000);
        }
    }

    addExportButton() {
        // Find the card containing this table
        let card = this.table.closest('.card');
        if (!card) return;

        const cardHeader = card.querySelector('.card-header');
        if (!cardHeader) return;

        // Create export button
        const exportBtn = document.createElement('button');
        exportBtn.className = 'btn btn-sm btn-secondary';
        exportBtn.innerHTML = '📥 Export CSV';
        exportBtn.style.cssText = `
            float: right;
            margin-top: -0.25rem;
        `;

        exportBtn.addEventListener('click', () => {
            this.exportToCSV();
        });

        cardHeader.appendChild(exportBtn);
    }

    exportToCSV() {
        const headers = Array.from(this.thead.querySelectorAll('th'))
            .map(th => th.textContent.trim().replace(/[⇅▲▼]/g, '').trim());

        const rows = this.rows.map(row => {
            return Array.from(row.cells).map(cell => {
                let text = cell.textContent.trim();
                // Escape quotes and wrap in quotes if contains comma
                if (text.includes(',') || text.includes('"')) {
                    text = '"' + text.replace(/"/g, '""') + '"';
                }
                return text;
            });
        });

        let csv = headers.join(',') + '\n';
        csv += rows.map(row => row.join(',')).join('\n');

        // Download
        const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
        const link = document.createElement('a');
        const url = URL.createObjectURL(blob);

        link.setAttribute('href', url);
        link.setAttribute('download', `wildcat-mesh-${Date.now()}.csv`);
        link.style.visibility = 'hidden';

        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);

        if (window.toast) {
            toast.success('Table exported to CSV!', 3000);
        }
    }

    refresh(newRows) {
        this.tbody.innerHTML = '';
        newRows.forEach(row => this.tbody.appendChild(row));
        this.rows = Array.from(this.tbody.querySelectorAll('tr'));
    }
}

// Multi-select filter component
class MultiSelectFilter {
    constructor(containerId, options = {}) {
        this.container = document.getElementById(containerId);
        if (!this.container) return;

        this.options = options;
        this.selectedValues = new Set();
        this.onChange = options.onChange || (() => {});

        this.init();
    }

    init() {
        const filterHTML = `
            <div class="multi-select-filter">
                <label style="display: block; margin-bottom: 0.5rem; color: var(--text-secondary); font-size: 0.9rem;">
                    ${this.options.label || 'Filter'}
                </label>
                <div class="filter-tags" style="display: flex; flex-wrap: wrap; gap: 0.5rem; margin-bottom: 0.75rem;"></div>
                <select class="filter-select" multiple
                    style="width: 100%; padding: 0.5rem; background: var(--bg-surface); border: 1px solid var(--border); border-radius: 6px; color: var(--text-primary); max-height: 150px;">
                    ${this.options.items.map(item => `
                        <option value="${item.value}">${item.label}</option>
                    `).join('')}
                </select>
            </div>
        `;

        this.container.innerHTML = filterHTML;

        const select = this.container.querySelector('.filter-select');
        select.addEventListener('change', (e) => {
            this.selectedValues = new Set(Array.from(e.target.selectedOptions).map(opt => opt.value));
            this.renderTags();
            this.onChange(Array.from(this.selectedValues));
        });
    }

    renderTags() {
        const tagsContainer = this.container.querySelector('.filter-tags');
        tagsContainer.innerHTML = '';

        this.selectedValues.forEach(value => {
            const item = this.options.items.find(i => i.value === value);
            if (!item) return;

            const tag = document.createElement('span');
            tag.className = 'badge badge-primary';
            tag.innerHTML = `
                ${item.label}
                <span style="margin-left: 0.35rem; cursor: pointer;" onclick="this.parentElement.parentElement.previousElementSibling.querySelector('option[value=\\'${value}\\']').selected = false; this.parentElement.parentElement.previousElementSibling.dispatchEvent(new Event('change'));">×</span>
            `;
            tagsContainer.appendChild(tag);
        });
    }

    getSelected() {
        return Array.from(this.selectedValues);
    }

    clear() {
        this.selectedValues.clear();
        const select = this.container.querySelector('.filter-select');
        Array.from(select.options).forEach(opt => opt.selected = false);
        this.renderTags();
        this.onChange([]);
    }
}

// Export to global scope
window.EnhancedTable = EnhancedTable;
window.MultiSelectFilter = MultiSelectFilter;
