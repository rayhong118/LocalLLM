import { LitElement, html, css } from 'lit';
import { customElement, property, state } from 'lit/decorators.js';

interface TaskOutput {
    id: number;
    content: string;
    created_at: string;
}

interface Task {
    id: number;
    prompt: string;
    status: 'PENDING' | 'RUNNING' | 'COMPLETED' | 'FAILED';
    frequency: 'ONCE' | 'DAILY';
    hour_of_day: number | null;
    next_run_at: string | null;
    created_at: string;
    updated_at: string;
    outputs: TaskOutput[];
}

@customElement('task-list')
export class TaskList extends LitElement {
    static override styles = css`
        :host { display: block; }
        .section-title {
            font-size: 1.25rem;
            font-weight: 600;
            margin: 2rem 0 1rem 0;
            color: #0f172a;
            display: flex;
            align-items: center;
            gap: 0.5rem;
        }
        .section-count {
            font-size: 0.8rem;
            background: #e2e8f0;
            padding: 0.1rem 0.5rem;
            border-radius: 999px;
            color: #64748b;
        }
        .list-container { display: flex; flex-direction: column; gap: 0.75rem; }
        .task-item {
            padding: 1.25rem;
            display: flex;
            flex-direction: column;
            gap: 0.5rem;
            transition: all 0.2s;
        }
        .task-item.history { cursor: pointer; }
        .task-item.history:hover { transform: translateY(-2px); box-shadow: 0 4px 12px rgba(0,0,0,0.05); }
        
        .task-header {
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            gap: 1rem;
        }
        .prompt-text { font-weight: 500; color: #0f172a; flex: 1; }
        .status-badge {
            padding: 0.25rem 0.75rem;
            border-radius: 999px;
            font-size: 0.7rem;
            font-weight: 700;
            text-transform: uppercase;
        }
        .COMPLETED { background: #dcfce7; color: #16a34a; }
        .RUNNING { background: #fef3c7; color: #d97706; }
        .PENDING { background: #f1f5f9; color: #64748b; }
        .FAILED { background: #fee2e2; color: #dc2626; }
        
        .output-container {
            margin-top: 1rem;
            padding: 1rem;
            background: #f8fafc;
            border-radius: 8px;
            font-family: 'Courier New', Courier, monospace;
            font-size: 0.9rem;
            white-space: pre-wrap;
            border-left: 3px solid #cbd5e1;
            color: #334155;
            animation: slideDown 0.2s ease-out;
        }
        @keyframes slideDown {
            from { opacity: 0; transform: translateY(-10px); }
            to { opacity: 1; transform: translateY(0); }
        }
        .time { font-size: 0.75rem; color: #64748b; }
        .empty-state {
            padding: 2rem;
            text-align: center;
            color: #94a3b8;
            border: 2px dashed #e2e8f0;
            border-radius: 12px;
        }
    `;

    @property({ type: Array })
    tasks: Task[] = [];

    @state()
    private expandedTasks = new Set<number>();

    private _toggleExpand(taskId: number) {
        const newSet = new Set(this.expandedTasks);
        if (newSet.has(taskId)) {
            newSet.delete(taskId);
        } else {
            newSet.add(taskId);
        }
        this.expandedTasks = newSet;
    }

    override render() {
        if (!this.tasks) return html``;

        const scheduledTasks = this.tasks.filter(t => t.next_run_at !== null);
        const historyTasks = this.tasks.filter(t => t.status !== 'PENDING' || (t.outputs && t.outputs.length > 0));

        return html`
            <div class="section-title">
                Scheduled Runs <span class="section-count">${scheduledTasks.length}</span>
            </div>
            <div class="list-container">
                ${scheduledTasks.length === 0 
                    ? html`<div class="empty-state">No upcoming runs scheduled.</div>`
                    : scheduledTasks.map(task => this._renderScheduledTask(task))}
            </div>

            <div class="section-title">
                Task History <span class="section-count">${historyTasks.length}</span>
            </div>
            <div class="list-container">
                ${historyTasks.length === 0 
                    ? html`<div class="empty-state">No execution history yet.</div>`
                    : historyTasks.map(task => this._renderHistoryTask(task))}
            </div>
        `;
    }

    private _renderScheduledTask(task: Task) {
        return html`
            <div class="glass-card task-item">
                <div class="task-header">
                    <span class="prompt-text">${task.prompt}</span>
                    <span style="font-size: 0.7rem; color: #64748b; background: #fff; padding: 0.2rem 0.5rem; border: 1px solid #e2e8f0; border-radius: 4px;">
                        ${task.frequency === 'DAILY' ? `RECURRING DAILY @ ${task.hour_of_day}:00` : 'ONE-TIME'}
                    </span>
                </div>
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <span class="time">Added: ${new Date(task.created_at).toLocaleString()}</span>
                    <span class="time" style="color: #2563eb; font-weight: 600;">
                        Next: ${new Date(task.next_run_at!).toLocaleString()}
                    </span>
                </div>
            </div>
        `;
    }

    private _renderHistoryTask(task: Task) {
        const isExpanded = this.expandedTasks.has(task.id);
        return html`
            <div class="glass-card task-item history" @click=${() => this._toggleExpand(task.id)}>
                <div class="task-header">
                    <span class="prompt-text">${task.prompt}</span>
                    <span class="status-badge ${task.status}">${task.status}</span>
                </div>
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <span class="time">Last Run: ${new Date(task.updated_at).toLocaleString()}</span>
                    <span style="font-size: 0.75rem; color: #3b82f6;">
                        ${isExpanded ? 'Hide Result ↑' : 'View Result ↓'}
                    </span>
                </div>

                ${isExpanded ? html`
                    <div class="output-container" @click=${(e: Event) => e.stopPropagation()}>
                        <strong>Agent Output:</strong>
                        <div style="margin-top: 0.5rem;">
                            ${task.outputs && task.outputs.length > 0 
                                ? task.outputs[0].content 
                                : 'No output available.'}
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
