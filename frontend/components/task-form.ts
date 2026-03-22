import { LitElement, html, css } from 'lit';
import { customElement, state, query } from 'lit/decorators.js';

@customElement('task-form')
export class TaskForm extends LitElement {
    static override styles = css`
        :host {
            display: block;
            margin-bottom: 2rem;
            font-family: inherit;
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

    @state()
    private frequency: 'ONCE' | 'DAILY' = 'ONCE';

    @state()
    private hourOfDay: number = new Date().getHours();

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
                body: JSON.stringify({ 
                    prompt,
                    frequency: this.frequency,
                    hour_of_day: this.frequency === 'DAILY' ? Number(this.hourOfDay) : null
                })
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
                
                <div style="display: flex; gap: 1rem; align-items: flex-end; margin-bottom: 0.5rem;">
                    <div style="flex: 1;">
                        <label style="display: block; font-size: 0.8rem; margin-bottom: 0.4rem; color: #64748b;">Frequency</label>
                        <select 
                            style="width: 100%; padding: 0.6rem; border-radius: 8px; border: 1px solid #e2e8f0; background: #fff;"
                            @change=${(e: any) => this.frequency = e.target.value}>
                            <option value="ONCE">One-time (Run Now)</option>
                            <option value="DAILY">Daily Recurring</option>
                        </select>
                    </div>

                    ${this.frequency === 'DAILY' ? html`
                        <div style="width: 120px;">
                            <label style="display: block; font-size: 0.8rem; margin-bottom: 0.4rem; color: #64748b;">Hour (0-23)</label>
                            <input 
                                type="number" 
                                min="0" 
                                max="23" 
                                .value=${String(this.hourOfDay)}
                                style="width: 100%; padding: 0.6rem; border-radius: 8px; border: 1px solid #e2e8f0;"
                                @input=${(e: any) => this.hourOfDay = e.target.value}
                            >
                        </div>
                    ` : ''}
                </div>

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
