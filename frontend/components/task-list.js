const { LitElement, html, css } = window;

class TaskList extends LitElement {
    static styles = css`
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
        .prompt-text { font-weight: 400; color: #f8fafc; }
        .status-badge {
            padding: 0.25rem 0.75rem;
            border-radius: 999px;
            font-size: 0.7rem;
            font-weight: 700;
            text-transform: uppercase;
        }
        .COMPLETED { background: rgba(16, 185, 129, 0.2); color: #10b981; }
        .RUNNING { background: rgba(245, 158, 11, 0.2); color: #f59e0b; }
        .PENDING { background: rgba(148, 163, 184, 0.2); color: #94a3b8; }
        .FAILED { background: rgba(239, 68, 68, 0.2); color: #ef4444; }
        
        .output-container {
            margin-top: 1rem;
            padding: 1rem;
            background: rgba(15, 23, 42, 0.4);
            border-radius: 8px;
            font-family: 'Courier New', Courier, monospace;
            font-size: 0.9rem;
            white-space: pre-wrap;
            border-left: 3px solid #6366f1;
        }
        .time { font-size: 0.75rem; color: #64748b; }
    `;

    static properties = {
        tasks: { type: Array }
    };

    render() {
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
customElements.define('task-list', TaskList);
