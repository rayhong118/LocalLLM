import { LitElement, html, css } from 'lit';
import { customElement, state } from 'lit/decorators.js';
import './task-form.ts';
import './task-list.ts';
import './context-manager.ts';

@customElement('app-root')
export class AppRoot extends LitElement {
    static override styles = css`
        :host {
            display: block;
            max-width: 1100px;
            margin: 0 auto;
            padding: 2rem;
            font-family: inherit;
        }
        header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 3rem;
            padding-bottom: 1.5rem;
            border-bottom: 1px solid #e2e8f0;
        }
        .logo h1 {
            color: #0f172a;
            font-size: 1.8rem;
            letter-spacing: -0.025em;
            margin: 0;
        }
        .logo h1 span {
            color: #2563eb;
        }
        
        nav {
            display: flex;
            gap: 1.5rem;
            align-items: center;
        }
        .nav-link {
            text-decoration: none;
            color: #64748b;
            font-weight: 500;
            font-size: 0.95rem;
            padding: 0.5rem 0.25rem;
            position: relative;
            cursor: pointer;
            transition: color 0.2s;
        }
        .nav-link:hover { color: #0f172a; }
        .nav-link.active {
            color: #2563eb;
        }
        .nav-link.active::after {
            content: '';
            position: absolute;
            bottom: -1rem;
            left: 0;
            right: 0;
            height: 2px;
            background: #2563eb;
        }

        .refresh-btn {
            background: #f8fafc;
            color: #64748b;
            border: 1px solid #e2e8f0;
            padding: 0.5rem 1rem;
            border-radius: 8px;
            cursor: pointer;
            font-size: 0.875rem;
            font-weight: 600;
        }
        .refresh-btn:hover {
            background: #f1f5f9;
            color: #0f172a;
        }
        
        main {
            animation: fadeIn 0.3s ease-out;
        }
        @keyframes fadeIn {
            from { opacity: 0; transform: translateY(10px); }
            to { opacity: 1; transform: translateY(0); }
        }
        .spinner {
            display: inline-block;
            width: 1rem;
            height: 1rem;
            border: 2px solid #cbd5e1;
            border-top-color: #2563eb;
            border-radius: 50%;
            animation: spin 0.8s linear infinite;
            margin-right: 0.5rem;
        }
        @keyframes spin {
            to { transform: rotate(360deg); }
        }
    `;

    @state()
    private _tasks: any[] = [];

    @state()
    private _currentPage: 'dashboard' | 'contexts' = 'dashboard';

    @state()
    private _isLoading: boolean = false;

    @state()
    private _isInitialLoaded: boolean = false;

    private _eventSource?: EventSource;

    override connectedCallback() {
        super.connectedCallback();
        this._isLoading = true;
        
        this._eventSource = new EventSource('http://localhost:8000/tasks/stream');
        this._eventSource.onmessage = (event) => {
            this._tasks = JSON.parse(event.data);
            this._isLoading = false;
            this._isInitialLoaded = true;
        };
        this._eventSource.onerror = (err) => {
            console.error("Task stream error:", err);
            this._isLoading = false;
        };
    }

    override disconnectedCallback() {
        super.disconnectedCallback();
        if (this._eventSource) {
            this._eventSource.close();
        }
    }

    private async _fetchTasks() {
        this._isLoading = true;
        try {
            const res = await fetch('http://localhost:8000/tasks');
            if (res.ok) {
                this._tasks = await res.json();
            }
        } catch (err) {
            console.error("Failed to fetch tasks:", err);
        } finally {
            this._isLoading = false;
            this._isInitialLoaded = true;
        }
    }

    override render() {
        return html`
            <header>
                <div class="logo">
                    <h1>LocalLLM <span>Agent</span></h1>
                </div>
                <nav>
                    <span 
                        class="nav-link ${this._currentPage === 'dashboard' ? 'active' : ''}" 
                        @click=${() => this._currentPage = 'dashboard'}
                    >Dashboard</span>
                    <span 
                        class="nav-link ${this._currentPage === 'contexts' ? 'active' : ''}" 
                        @click=${() => this._currentPage = 'contexts'}
                    >Context Manager</span>
                    <button class="refresh-btn" @click=${this._fetchTasks} ?disabled=${this._isLoading}>
                        ${this._isLoading ? html`<span class="spinner"></span> Refreshing...` : 'Refresh Status'}
                    </button>
                </nav>
            </header>

            <main>
                ${this._currentPage === 'dashboard' ? html`
                    <task-form @task-created=${this._fetchTasks}></task-form>
                    <h2 style="margin: 2rem 0 1.5rem 0; color: #0f172a; font-size: 1.5rem;">Tasks</h2>
                    ${!this._isInitialLoaded ? html`
                        <div style="display: flex; justify-content: center; align-items: center; padding: 4rem; color: #64748b; font-size: 1.1rem; gap: 0.75rem; border: 2px dashed #e2e8f0; border-radius: 12px;">
                            <span class="spinner" style="width: 1.5rem; height: 1.5rem; border-width: 3px;"></span> Loading tasks...
                        </div>
                    ` : html`
                        <task-list .tasks=${this._tasks}></task-list>
                    `}
                ` : html`
                    <context-manager></context-manager>
                `}
            </main>
        `;
    }
}

declare global {
    interface HTMLElementTagNameMap {
        'app-root': AppRoot;
    }
}
