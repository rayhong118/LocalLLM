const { LitElement, html, css } = window;

class AppRoot extends LitElement {
    static styles = css`
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
            background: linear-gradient(to right, #818cf8, #c084fc);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            font-size: 2.5rem;
        }
        .refresh-btn {
            background: rgba(255, 255, 255, 0.05);
            color: #94a3b8;
            border: 1px solid rgba(255, 255, 255, 0.1);
            padding: 0.5rem 1rem;
            border-radius: 8px;
            cursor: pointer;
        }
    `;

    static properties = {
        _tasks: { type: Array, state: true }
    };

    constructor() {
        super();
        this._tasks = [];
        this._fetchTasks();
        this._pollInterval = setInterval(() => this._fetchTasks(), 5000);
    }

    disconnectedCallback() {
        super.disconnectedCallback();
        clearInterval(this._pollInterval);
    }

    async _fetchTasks() {
        try {
            const res = await fetch('http://localhost:8000/tasks');
            if (res.ok) {
                this._tasks = await res.json();
            }
        } catch (err) {
            console.error("Failed to fetch tasks:", err);
        }
    }

    render() {
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
customElements.define('app-root', AppRoot);
