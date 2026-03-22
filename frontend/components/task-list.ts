import { LitElement, html, css } from 'lit';
import { customElement, property } from 'lit/decorators.js';

interface TaskOutput {
    id: number;
    content: string;
    created_at: string;
}

interface Task {
    id: number;
    prompt: string;
    status: 'PENDING' | 'RUNNING' | 'COMPLETED' | 'FAILED';
    created_at: string;
    updated_at: string;
    outputs: TaskOutput[];
}

@customElement('task-list')
export class TaskList extends LitElement {
    static override styles = css`
        :host { display: block; }
        .list-container { display: flex; flex-direction: column; gap: 1rem; }
        .task-item {
            padding: 1.5rem;
            display: flex;
            flex-direction: column;
            gap: 0.5rem;
            transition: transform 0.2s;
        }
        .task-header {
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
        }
        .prompt-text { font-weight: 500; color: #0f172a; }
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
        }
        .time { font-size: 0.75rem; color: #64748b; }
    `;

    @property({ type: Array })
    tasks: Task[] = [];

    override render() {
        if (!this.tasks || this.tasks.length === 0) {
            return html`<div class="glass-card" style="padding: 2rem; text-align: center; color: #94a3b8;">No tasks scheduled yet.</div>`;
        }

        return html`
            <div class="list-container">
                ${this.tasks.map(task => html`
                    <div class="glass-card task-item animate-in">
                        <div class="task-header">
                            <span class="prompt-text">${task.prompt}</span>
                            <span class="status-badge ${task.status}">${task.status}</span>
                        </div>
                        <span class="time">${new Date(task.created_at).toLocaleString()}</span>
                        
                        ${task.outputs && task.outputs.length > 0 ? html`
                            <div class="output-container">
                                <strong>Agent Output:</strong>
                                <div>${task.outputs[0].content}</div>
                            </div>
                        ` : ''}
                    </div>
                `)}
            </div>
        `;
    }
}

declare global {
    interface HTMLElementTagNameMap {
        'task-list': TaskList;
    }
}
