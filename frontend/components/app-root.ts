import { LitElement, html, css } from 'lit';
import { customElement, state } from 'lit/decorators.js';
import './task-form.ts';
import './task-list.ts';

@customElement('app-root')
export class AppRoot extends LitElement {
    static override styles = css`
        :host {
            display: block;
            max-width: 1000px;
            margin: 0 auto;
            padding: 2rem;
        }
        header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 3rem;
        }
        .logo h1 {
            color: #0f172a;
            font-size: 2.2rem;
            letter-spacing: -0.025em;
        }
        .logo h1 span {
            color: #2563eb;
        }
        .refresh-btn {
            background: #fff;
            color: #64748b;
            border: 1px solid #e2e8f0;
            padding: 0.5rem 1rem;
            border-radius: 8px;
            cursor: pointer;
            font-size: 0.875rem;
        }
        .refresh-btn:hover {
            background: #f8fafc;
            color: #0f172a;
        }
    `;

    @state()
    private _tasks: any[] = [];

    private _pollInterval?: number;

    override connectedCallback() {
        super.connectedCallback();
        this._fetchTasks();
        this._pollInterval = window.setInterval(() => this._fetchTasks(), 5000);
    }

    override disconnectedCallback() {
        super.disconnectedCallback();
        if (this._pollInterval) {
            clearInterval(this._pollInterval);
        }
    }

    private async _fetchTasks() {
        try {
            const res = await fetch('http://localhost:8000/tasks');
            if (res.ok) {
                this._tasks = await res.json();
            }
        } catch (err) {
            console.error("Failed to fetch tasks:", err);
        }
    }

    override render() {
        return html`
            <header>
                <div class="logo">
                    <h1>LocalLLM <span>Agent</span></h1>
                </div>
                <button class="refresh-btn" @click=${this._fetchTasks}>Refresh Status</button>
            </header>

            <main>
                <task-form @task-created=${this._fetchTasks}></task-form>
                
                <h2 style="margin-bottom: 1.5rem; color: #f8fafc;">Task History</h2>
                <task-list .tasks=${this._tasks}></task-list>
            </main>
        `;
    }
}

declare global {
    interface HTMLElementTagNameMap {
        'app-root': AppRoot;
    }
}
