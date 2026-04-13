import { LitElement, html, css } from 'lit';
import { customElement, property, state } from 'lit/decorators.js';
import { marked } from 'marked';
import { unsafeHTML } from 'lit/directives/unsafe-html.js';

interface TaskOutput {
    id: number;
    content: string;
    created_at: string;
}

interface Task {
    id: number;
    prompt: string;
    status: 'PENDING' | 'RUNNING' | 'COMPLETED' | 'FAILED' | 'DAILY' | 'CANCELLED';
    frequency: 'ONCE' | 'DAILY';
    hour_of_day: number | null;
    next_run_at: string | null;
    started_at: string | null;
    created_at: string;
    updated_at: string;
    outputs: TaskOutput[];
}

@customElement('task-list')
export class TaskList extends LitElement {
    static override styles = css`
        :host { 
            display: block; 
            font-family: inherit;
        }
        
        .section-header {
            font-size: 0.9rem;
            font-weight: 600;
            color: #64748b;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            margin: 2.5rem 0 1rem 0;
            display: flex;
            align-items: center;
            gap: 0.5rem;
        }
        .section-header span {
            background: #f1f5f9;
            padding: 0.1rem 0.5rem;
            border-radius: 4px;
            font-size: 0.75rem;
            color: #475569;
        }

        .list-container { 
            display: flex; 
            flex-direction: column; 
            gap: 1rem; 
        }

        .task-item {
            position: relative;
            padding: 1.25rem;
            background: var(--color-card-bg);
            border: 1px solid var(--color-border);
            border-radius: 12px;
            transition: all 0.2s;
        }
        .task-item:hover {
            transform: translateY(-2px);
            box-shadow: var(--color-shadow-md);
            border-color: var(--color-border-hover);
        }

        /* Pulsing animation for active tasks */
        @keyframes pulse-border {
            0% { border-color: var(--color-border); box-shadow: 0 0 0 0 var(--color-primary-soft); }
            50% { border-color: var(--color-primary); box-shadow: 0 0 0 4px var(--color-primary-soft); }
            100% { border-color: var(--color-border); box-shadow: 0 0 0 0 var(--color-primary-soft); }
        }
        .task-item.RUNNING {
            animation: pulse-border 2s infinite ease-in-out;
            border-width: 2px;
        }

        .task-header {
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            gap: 1.5rem;
            margin-bottom: 0.75rem;
            padding-right: 2rem; /* Make room for absolute delete button */
        }

        .prompt-text {
            font-weight: 600;
            color: var(--color-text-body);
            flex: 1;
            line-height: 1.4;
            font-size: 0.95rem;
        }

        .status-badge {
            padding: 0.25rem 0.75rem;
            border-radius: 999px;
            font-size: 0.7rem;
            font-weight: 700;
            text-transform: uppercase;
            white-space: nowrap;
        }
        .status-badge.COMPLETED { background: var(--color-success-bg); color: var(--color-success); }
        .status-badge.RUNNING { background: var(--color-warning-bg); color: var(--color-warning); }
        .status-badge.PENDING { background: var(--color-neutral-bg); color: var(--color-text-muted); }
        .status-badge.FAILED { background: var(--color-error-bg); color: var(--color-error); }
        .status-badge.DAILY { background: var(--color-info-bg); color: var(--color-info); }
        .status-badge.CANCELLED { background: var(--color-neutral-bg); color: var(--color-neutral); }

        .delete-btn {
            position: absolute;
            top: 0.75rem;
            right: 0.75rem;
            background: var(--color-bg);
            border: 1px solid var(--color-border);
            color: var(--color-text-light);
            width: 24px;
            height: 24px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            transition: all 0.2s;
            font-size: 1.1rem;
            line-height: 1;
            z-index: 10;
        }
        .delete-btn:hover {
            background: var(--color-error-bg);
            color: var(--color-error);
            border-color: var(--color-error-bg);
        }

        .task-actions {
            display: flex;
            gap: 0.5rem;
            align-items: center;
            flex-shrink: 0;
        }

        .action-btn {
            border: none;
            padding: 0.35rem 0.75rem;
            border-radius: 8px;
            font-size: 0.7rem;
            font-weight: 700;
            text-transform: uppercase;
            cursor: pointer;
            transition: all 0.2s;
            white-space: nowrap;
        }

        .retry-btn {
            background: var(--color-primary);
            color: white;
            box-shadow: 0 2px 4px var(--color-primary-soft);
        }
        .retry-btn:hover { background: var(--color-primary-hover); transform: translateY(-1px); }

        .cancel-btn {
            background: var(--color-bg-alt);
            color: var(--color-text-muted);
            border: 1px solid var(--color-border);
        }
        .cancel-btn:hover { 
            background: var(--color-neutral-bg); 
            color: var(--color-text-body);
            transform: translateY(-1px); 
        }
        
        .view-result-btn {
            background: none;
            border: none;
            padding: 0;
            margin: 0;
            font-family: inherit;
            font-size: 0.75rem;
            color: var(--color-info);
            font-weight: 600;
            cursor: pointer;
            transition: color 0.1s;
        }
        .view-result-btn:hover {
            color: var(--color-primary);
        }
        .view-result-btn:focus-visible {
            outline: 2px solid var(--color-info);
            outline-offset: 4px;
            border-radius: 2px;
        }

        .time { font-size: 0.75rem; color: var(--color-text-muted); }

        .output-container {
            margin-top: 1rem;
            padding: 1.25rem;
            background: var(--color-bg-alt);
            border-radius: 8px;
            border-left: 4px solid var(--color-info);
            animation: slideDown 0.2s ease-out;
        }
        @keyframes slideDown {
            from { opacity: 0; transform: translateY(-10px); }
            to { opacity: 1; transform: translateY(0); }
        }

        .markdown-body { font-size: 0.95rem; line-height: 1.6; color: var(--color-text-body); }
        .markdown-body h1, .markdown-body h2 { border-bottom: 1px solid var(--color-border); padding-bottom: 0.3rem; margin-top: 1.5rem; }
        .markdown-body code { background: var(--color-neutral-bg); padding: 0.2rem 0.4rem; border-radius: 4px; font-size: 85%; }
        .markdown-body pre { background: var(--color-neutral-bg); padding: 1rem; border-radius: 8px; overflow-x: auto; }
        
        .empty-state {
            padding: 3rem;
            text-align: center;
            color: var(--color-text-light);
            border: 2px dashed var(--color-border);
            border-radius: 12px;
            font-size: 0.9rem;
        }
    `;

    @property({ type: Array })
    tasks: Task[] = [];

    @state()
    private _expandedTaskId: number | null = null;

    private async _deleteTask(taskId: number, needCancel: boolean) {
        if (!confirm('Are you sure you want to delete this task?')) return;
        try {
            if (needCancel) {
                await this._cancelTask(taskId);
            }
            const res = await fetch(`http://localhost:8000/tasks/${taskId}`, { method: 'DELETE' });
            if (res.ok) {
                this.tasks = this.tasks.filter(t => t.id !== taskId);
            }
        } catch (error) {
            console.error('Failed to delete task:', error);
        }
    }

    private async _retryTask(taskId: number) {
        try {
            const res = await fetch(`http://localhost:8000/tasks/${taskId}/retry`, { method: 'POST' });
            if (res.ok) {
                const updatedTask = await res.json();
                this.tasks = this.tasks.map(t => t.id === taskId ? updatedTask : t);
            }
        } catch (error) {
            console.error('Failed to retry task:', error);
        }
    }

    private async _runNowTask(taskId: number) {
        try {
            const res = await fetch(`http://localhost:8000/tasks/${taskId}/run_now`, { method: 'POST' });
            if (res.ok) {
                const updatedTask = await res.json();
                this.tasks = this.tasks.map(t => t.id === taskId ? updatedTask : t);
            }
        } catch (error) {
            console.error('Failed to run task now:', error);
        }
    }

    private async _cancelTask(taskId: number) {
        try {
            const res = await fetch(`http://localhost:8000/tasks/${taskId}/cancel`, { method: 'POST' });
            if (res.ok) {
                const updatedTask = await res.json();
                this.tasks = this.tasks.map(t => t.id === taskId ? updatedTask : t);
            }
        } catch (error) {
            console.error('Failed to cancel task:', error);
        }
    }

    private _getTimezoneAbbreviation() {
        return new Intl.DateTimeFormat('en-US', { timeZoneName: 'short' })
            .formatToParts(new Date())
            .find(p => p.type === 'timeZoneName')?.value || '';
    }

    private _toLocalDate(dateStr: string | null) {
        if (!dateStr) return new Date();
        // If the string doesn't end with 'Z' and doesn't contain an offset, assume UTC
        if (!dateStr.endsWith('Z') && !/[+-]\d{2}(?::?\d{2})?$/.test(dateStr)) {
            return new Date(dateStr + 'Z');
        }
        return new Date(dateStr);
    }

    override render() {
        if (!this.tasks) return html``;

        const recurring = this.tasks.filter(t => t.frequency === 'DAILY');
        const pending = [...this.tasks].filter(t => t.frequency === 'ONCE' && (t.status === 'PENDING' || t.status === 'RUNNING' || t.status === 'CANCELLED')).sort((a, b) => this._toLocalDate(a.created_at).getTime() - this._toLocalDate(b.created_at).getTime());
        const history = this.tasks.filter(t => t.frequency === 'ONCE' && (t.status === 'COMPLETED' || t.status === 'FAILED'));

        return html`
            ${recurring.length > 0 ? html`
                <div class="section-header">Recurring Tasks <span>${recurring.length}</span></div>
                <div class="list-container">
                    ${recurring.map(t => this._renderTaskItem(t))}
                </div>
            ` : ''}

            ${pending.length > 0 ? html`
                <div class="section-header">Pending Tasks Queue <span>${pending.length}</span></div>
                <div class="list-container">
                    ${pending.map(t => this._renderTaskItem(t))}
                </div>
            ` : ''}

            <div class="section-header">Completed Tasks History <span>${history.length}</span></div>
            <div class="list-container">
                ${history.length === 0 ? html`
                    <div class="empty-state">No task history yet.</div>
                ` : history.map(t => this._renderTaskItem(t))}
            </div>
        `;
    }

    private _renderTaskItem(task: Task) {
        const isExpanded = this._expandedTaskId === task.id;
        const hasOutput = task.outputs && task.outputs.length > 0 && task.status !== 'RUNNING';

        return html`
            <div class="task-item ${task.status}">
                <button class="delete-btn" title="Delete" @click=${(e: Event) => { e.stopPropagation(); this._deleteTask(task.id, task.status !== 'RUNNING'); }}>&times;</button>
                
                <div class="task-header">
                    <div style="display: flex; gap: 0.75rem; align-items: center; flex: 1; min-width: 0;">
                        <span class="status-badge ${task.frequency === 'DAILY' ? 'DAILY' : task.status}">${task.frequency === 'DAILY' ? 'DAILY' : task.status}</span>
                        <span class="prompt-text">${task.prompt}</span>
                    </div>
                    <div class="task-actions">
                        ${task.frequency === 'DAILY' && task.status !== 'RUNNING' ? html`
                            <button class="action-btn retry-btn" @click=${(e: Event) => { e.stopPropagation(); this._runNowTask(task.id); }}>Run Now</button>
                        ` : ''}
                        ${(task.status === 'FAILED' || task.status === 'CANCELLED') && task.frequency !== 'DAILY' ? html`
                            <button class="action-btn retry-btn" @click=${(e: Event) => { e.stopPropagation(); this._retryTask(task.id); }}>Retry</button>
                        ` : ''}
                        ${task.status === 'RUNNING' ? html`
                            <button class="action-btn cancel-btn" @click=${(e: Event) => { e.stopPropagation(); this._cancelTask(task.id); }}>Cancel</button>
                        ` : ''}
                    </div>
                </div>

                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <span class="time">
                        ${task.frequency === 'DAILY' ? `Every day at ${String(task.hour_of_day).padStart(2, '0')}:00 ${this._getTimezoneAbbreviation()}` :
                task.status === 'RUNNING' ? `Started: ${this._toLocalDate(task.started_at).toLocaleTimeString()} ${this._getTimezoneAbbreviation()}` :
                    task.status === 'CANCELLED' ? `Cancelled at: ${this._toLocalDate(task.updated_at).toLocaleTimeString()} ${this._getTimezoneAbbreviation()}` :
                        `Last Run: ${this._toLocalDate(task.updated_at).toLocaleString()} ${this._getTimezoneAbbreviation()}`}
                    </span>
                    ${hasOutput ? html`
                        <button class="view-result-btn"
                              @click=${() => this._expandedTaskId = isExpanded ? null : task.id}>
                            ${isExpanded ? 'Hide Result ↑' : 'View Result ↓'}
                        </button>
                    ` : ''}
                </div>

                ${isExpanded && hasOutput ? html`
                    <div class="output-container" @click=${(e: Event) => e.stopPropagation()}>
                        <div class="markdown-body">
                            ${unsafeHTML(marked.parse(task.outputs[0].content, { async: false }) as string)}
                        </div>
                    </div>
                ` : ''}
            </div>
        `;
    }
}

declare global {
    interface HTMLElementTagNameMap {
        'task-list': TaskList;
    }
}
