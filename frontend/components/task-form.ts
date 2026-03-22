import { LitElement, html, css } from 'lit';
import { customElement, state, query } from 'lit/decorators.js';

@customElement('task-form')
export class TaskForm extends LitElement {
    static override styles = css`
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
            min-height: 120px;
            background: #fff;
            border: 1px solid #e2e8f0;
            border-radius: 8px;
            padding: 1rem;
            color: #0f172a;
            font-family: inherit;
            resize: vertical;
            font-size: 1rem;
        }
        button {
            background: #2563eb;
            color: white;
            border: none;
            border-radius: 8px;
            padding: 0.875rem;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s;
            font-size: 1rem;
        }
        button:hover {
            background: #1d4ed8;
            transform: translateY(-1px);
            box-shadow: 0 4px 12px rgba(37, 99, 235, 0.2);
        }
        button:disabled {
            opacity: 0.5;
            cursor: not-allowed;
        }
    `;

    @state()
    private loading = false;

    @query('textarea')
    private textarea!: HTMLTextAreaElement;

    private async _handleSubmit(e: Event) {
        e.preventDefault();
        const prompt = this.textarea.value;
        if (!prompt) return;

        this.loading = true;
        try {
            const res = await fetch('http://localhost:8000/tasks', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ prompt })
            });
            if (res.ok) {
                this.textarea.value = '';
                this.dispatchEvent(new CustomEvent('task-created', { bubbles: true, composed: true }));
            }
        } catch (err) {
            console.error(err);
        } finally {
            this.loading = false;
        }
    }

    override render() {
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

declare global {
    interface HTMLElementTagNameMap {
        'task-form': TaskForm;
    }
}
