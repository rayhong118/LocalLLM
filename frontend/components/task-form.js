const { LitElement, html, css } = window;

class TaskForm extends LitElement {
    static styles = css`
        :host {
            display: block;
            margin-bottom: 2rem;
        }
        .form-container {
            display: flex;
            flex-direction: column;
            gap: 1rem;
            padding: 1.5rem;
        }
        textarea {
            min-height: 100px;
            background: rgba(15, 23, 42, 0.5);
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 8px;
            padding: 1rem;
            color: white;
            font-family: inherit;
            resize: vertical;
        }
        button {
            background: #6366f1;
            color: white;
            border: none;
            border-radius: 8px;
            padding: 0.75rem;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s;
        }
        button:hover {
            background: #4f46e5;
            transform: translateY(-1px);
        }
        button:disabled {
            opacity: 0.5;
            cursor: not-allowed;
        }
    `;

    static properties = {
        loading: { type: Boolean }
    };

    constructor() {
        super();
        this.loading = false;
    }

    async _handleSubmit(e) {
        e.preventDefault();
        const prompt = this.renderRoot.querySelector('textarea').value;
        if (!prompt) return;

        this.loading = true;
        try {
            const res = await fetch('http://localhost:8000/tasks', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ prompt })
            });
            if (res.ok) {
                this.renderRoot.querySelector('textarea').value = '';
                this.dispatchEvent(new CustomEvent('task-created', { bubbles: true, composed: true }));
            }
        } catch (err) {
            console.error(err);
        } finally {
            this.loading = false;
        }
    }

    render() {
        return html`
            <div class="glass-card form-container animate-in">
                <h2 style="margin-bottom: 0.5rem">Schedule New Task</h2>
                <textarea placeholder="e.g., Search for Häagen-Dazs deals on Safeway and list flavors..."></textarea>
                <button ?disabled=${this.loading} @click=${this._handleSubmit}>
                    ${this.loading ? 'Scheduling...' : 'Schedule Task'}
                </button>
            </div>
        `;
    }
}
customElements.define('task-form', TaskForm);
