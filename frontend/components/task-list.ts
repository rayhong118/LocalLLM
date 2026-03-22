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
        :host { 
            display: block; 
            font-family: inherit;
        }
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
            padding: 1.5rem;
            background: #f8fafc;
            border-radius: 8px;
            font-size: 0.95rem;
            border-left: 4px solid #3b82f6;
            color: #1e293b;
            animation: slideDown 0.2s ease-out;
            overflow-x: auto;
        }

        /* Markdown Styles */
        .markdown-body {
            line-height: 1.6;
        }
        .markdown-body h1, .markdown-body h2, .markdown-body h3 {
            margin-top: 1.5rem;
            margin-bottom: 1rem;
            font-weight: 600;
            line-height: 1.25;
            color: #0f172a;
        }
        .markdown-body h1 { font-size: 1.5rem; border-bottom: 1px solid #e2e8f0; padding-bottom: 0.3rem; }
        .markdown-body h2 { font-size: 1.25rem; }
        .markdown-body p { margin-bottom: 1rem; }
        .markdown-body code {
            padding: 0.2rem 0.4rem;
            margin: 0;
            font-size: 85%;
            background-color: #f1f5f9;
            border-radius: 6px;
            font-family: ui-monospace, SFMono-Regular, SF Mono, Menlo, Consolas, Liberation Mono, monospace;
        }
        .markdown-body pre {
            padding: 1rem;
            overflow: auto;
            font-size: 85%;
            line-height: 1.45;
            background-color: #f1f5f9;
            border-radius: 8px;
            margin-bottom: 1rem;
        }
        .markdown-body pre code {
            padding: 0;
            background-color: transparent;
            font-size: 100%;
        }
        .markdown-body ul, .markdown-body ol {
            padding-left: 2rem;
            margin-bottom: 1rem;
        }
        .markdown-body blockquote {
            padding: 0 1rem;
            color: #64748b;
            border-left: 0.25rem solid #e2e8f0;
            margin: 0 0 1rem 0;
        }
        .markdown-body table {
            border-collapse: collapse;
            width: 100%;
            margin-bottom: 1rem;
        }
        .markdown-body table th, .markdown-body table td {
            border: 1px solid #e2e8f0;
            padding: 0.5rem 0.75rem;
        }
        .markdown-body table tr:nth-child(2n) {
            background-color: #f8fafc;
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
        const hasOutput = task.outputs && task.outputs.length > 0;
        
        let contentHtml = html`No output available.`;
        if (hasOutput) {
            try {
                const rawContent = task.outputs[0].content;
                const parsedContent = marked.parse(rawContent) as string;
                contentHtml = html`${unsafeHTML(parsedContent)}`;
            } catch (e) {
                console.error("Failed to parse markdown:", e);
                contentHtml = html`${task.outputs[0].content}`;
            }
        }

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
                        <div style="margin-bottom: 0.5rem; color: #64748b; font-size: 0.75rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em;">
                            Agent Output
                        </div>
                        <div class="markdown-body">
                            ${contentHtml}
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
