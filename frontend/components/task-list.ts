import { LitElement, html, css } from 'lit';
import { customElement, property } from 'lit/decorators.js';
import { repeat } from 'lit/directives/repeat.js';
import './task-item.ts';

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
                    ${repeat(recurring, t => t.id, t => html`<task-item .task=${t} 
                        @task-delete=${(e: CustomEvent) => this._deleteTask(e.detail.taskId, e.detail.status !== 'RUNNING')}
                        @task-retry=${(e: CustomEvent) => this._retryTask(e.detail.taskId)}
                        @task-run-now=${(e: CustomEvent) => this._runNowTask(e.detail.taskId)}
                        @task-cancel=${(e: CustomEvent) => this._cancelTask(e.detail.taskId)}
                    ></task-item>`)}
                </div>
            ` : ''}

            ${pending.length > 0 ? html`
                <div class="section-header">Pending Tasks Queue <span>${pending.length}</span></div>
                <div class="list-container">
                    ${repeat(pending, t => t.id, t => html`<task-item .task=${t} 
                        @task-delete=${(e: CustomEvent) => this._deleteTask(e.detail.taskId, e.detail.status !== 'RUNNING')}
                        @task-retry=${(e: CustomEvent) => this._retryTask(e.detail.taskId)}
                        @task-run-now=${(e: CustomEvent) => this._runNowTask(e.detail.taskId)}
                        @task-cancel=${(e: CustomEvent) => this._cancelTask(e.detail.taskId)}
                    ></task-item>`)}
                </div>
            ` : ''}

            <div class="section-header">Completed Tasks History <span>${history.length}</span></div>
            <div class="list-container">
                ${history.length === 0 ? html`
                    <div class="empty-state">No task history yet.</div>
                ` : repeat(history, t => t.id, t => html`<task-item .task=${t} 
                        @task-delete=${(e: CustomEvent) => this._deleteTask(e.detail.taskId, e.detail.status !== 'RUNNING')}
                        @task-retry=${(e: CustomEvent) => this._retryTask(e.detail.taskId)}
                        @task-run-now=${(e: CustomEvent) => this._runNowTask(e.detail.taskId)}
                        @task-cancel=${(e: CustomEvent) => this._cancelTask(e.detail.taskId)}
                ></task-item>`)}
            </div>
        `;
    }
}

declare global {
    interface HTMLElementTagNameMap {
        'task-list': TaskList;
    }
}
