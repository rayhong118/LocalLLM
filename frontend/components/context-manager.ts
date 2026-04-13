import { LitElement, html, css } from 'lit';
import { customElement, state } from 'lit/decorators.js';

interface Context {
    id: number;
    name: string;
    content: string;
    created_at: string;
}

@customElement('context-manager')
export class ContextManager extends LitElement {
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
            margin-bottom: 2rem;
        }
        .input-group {
            display: flex;
            flex-direction: column;
            gap: 0.5rem;
        }
        label {
            font-weight: 600;
            font-size: 0.9rem;
            color: #475569;
        }
        input, textarea {
            padding: 0.75rem;
            border: 1px solid #e2e8f0;
            border-radius: 8px;
            font-family: inherit;
            font-size: 0.95rem;
        }
        textarea {
            height: 100px;
            resize: vertical;
        }
        button {
            padding: 0.75rem 1.5rem;
            background: #2563eb;
            color: white;
            border: none;
            border-radius: 8px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s;
            align-self: flex-start;
        }
        button:hover { background: #1d4ed8; }
        
        .context-list {
            display: flex;
            flex-direction: column;
            gap: 1rem;
        }
        .context-item {
            padding: 1rem;
            background: #f8fafc;
            border: 1px solid #e2e8f0;
            border-radius: 8px;
            position: relative;
        }
        .context-name {
            font-weight: 600;
            color: #0f172a;
            margin-bottom: 0.25rem;
        }
        .context-content {
            font-size: 0.9rem;
            color: #475569;
            white-space: pre-wrap;
        }
        .delete-btn {
            background: none;
            border: none;
            color: #94a3b8;
            cursor: pointer;
            font-size: 1rem;
            padding: 0.5rem;
            line-height: 1;
            border-radius: 4px;
            transition: all 0.2s;
        }
        .delete-btn:hover { color: #dc2626; background: #fee2e2; }

        .edit-btn {
            background: none;
            border: none;
            color: #64748b;
            cursor: pointer;
            font-size: 0.85rem;
            font-weight: 600;
            padding: 0.4rem 0.8rem;
            border-radius: 4px;
            transition: all 0.2s;
        }
        .edit-btn:hover { color: #2563eb; background: #eff6ff; }
        
        .action-group {
            position: absolute;
            top: 0.5rem;
            right: 0.5rem;
            display: flex;
            gap: 0.25rem;
            align-items: center;
        }

        .btn-primary { background: #2563eb; color: white; border: none; }
        .btn-primary:hover { background: #1d4ed8; }
        .btn-secondary { background: #f8fafc; color: #64748b; border: 1px solid #e2e8f0; }
        .btn-secondary:hover { background: #f1f5f9; color: #0f172a; }

        .form-actions {
            display: flex;
            gap: 0.75rem;
            margin-top: 1rem;
        }

        .add-trigger-btn {
            display: flex;
            align-items: center;
            gap: 0.5rem;
            padding: 0.6rem 1.25rem;
            background: #2563eb;
            color: white;
            border: none;
            border-radius: 8px;
            font-weight: 600;
            cursor: pointer;
            margin-bottom: 2rem;
        }

        .editing-card {
            background: #fff;
            border: 2px solid #3b82f6;
            padding: 1rem;
            border-radius: 8px;
            display: flex;
            flex-direction: column;
            gap: 0.75rem;
        }
        .spinner {
            display: inline-block;
            width: 1rem;
            height: 1rem;
            border: 2px solid rgba(255, 255, 255, 0.3);
            border-top-color: #ffffff;
            border-radius: 50%;
            animation: spin 0.8s linear infinite;
            margin-right: 0.5rem;
            vertical-align: middle;
        }
        @keyframes spin {
            to { transform: rotate(360deg); }
        }
    `;

    @state()
    private contexts: Context[] = [];

    @state()
    private _isSaving = false;

    @state()
    private newName = '';

    @state()
    private newContent = '';

    @state()
    private _showAddForm = false;

    @state()
    private _editingId: number | null = null;

    @state()
    private _editName = '';

    @state()
    private _editContent = '';

    override async connectedCallback() {
        super.connectedCallback();
        await this._fetchContexts();
    }

    private async _fetchContexts() {
        try {
            const res = await fetch('http://localhost:8000/contexts');
            if (res.ok) {
                this.contexts = await res.json();
            }
        } catch (err) {
            console.error("Failed to fetch contexts:", err);
        }
    }

    private async _addContext() {
        if (!this.newName || !this.newContent) return;

        this._isSaving = true;
        try {
            const res = await fetch('http://localhost:8000/contexts', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: this.newName, content: this.newContent })
            });
            if (res.ok) {
                this.newName = '';
                this.newContent = '';
                this._showAddForm = false;
                await this._fetchContexts();
            }
        } catch (err) {
            console.error("Failed to add context:", err);
        } finally {
            this._isSaving = false;
        }
    }

    private _startEdit(ctx: Context) {
        this._editingId = ctx.id;
        this._editName = ctx.name;
        this._editContent = ctx.content;
    }

    private _cancelEdit() {
        this._editingId = null;
    }

    private async _saveEdit(id: number) {
        this._isSaving = true;
        try {
            const res = await fetch(`http://localhost:8000/contexts/${id}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: this._editName, content: this._editContent })
            });
            if (res.ok) {
                this._editingId = null;
                await this._fetchContexts();
            }
        } catch (err) {
            console.error("Failed to update context:", err);
        } finally {
            this._isSaving = false;
        }
    }

    private async _deleteContext(id: number) {
        if (!confirm('Delete this context?')) return;
        try {
            const res = await fetch(`http://localhost:8000/contexts/${id}`, { method: 'DELETE' });
            if (res.ok) {
                await this._fetchContexts();
            }
        } catch (err) {
            console.error("Failed to delete context:", err);
        }
    }

    override render() {
        return html`
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 2rem;">
                <h2 style="margin: 0; color: #011E41;">Reference Contexts</h2>
                <button class="add-trigger-btn" @click=${() => this._showAddForm = !this._showAddForm}>
                    ${this._showAddForm ? 'Cancel' : '+ Add New Context'}
                </button>
            </div>

            ${this._showAddForm ? html`
                <div class="glass-card form-container">
                    <p style="font-size: 0.85rem; color: #64748b; margin-bottom: 1rem;">
                        Add information the agent should know before starting any task.
                    </p>
                    
                    <div class="input-group">
                        <label>Context Name</label>
                        <input type="text" placeholder="e.g. Personal Preferences" .value=${this.newName} @input=${(e: any) => this.newName = e.target.value}>
                    </div>
                    <div class="input-group">
                        <label>Context Content</label>
                        <textarea placeholder="e.g. I prefer dark chocolate." .value=${this.newContent} @input=${(e: any) => this.newContent = e.target.value}></textarea>
                    </div>
                    <div class="form-actions">
                        <button class="btn-primary" @click=${this._addContext} ?disabled=${this._isSaving} style="display: flex; align-items: center; justify-content: center;">
                            ${this._isSaving ? html`<span class="spinner"></span> Saving...` : 'Save Context'}
                        </button>
                        <button class="btn-secondary" @click=${() => this._showAddForm = false} ?disabled=${this._isSaving}>Cancel</button>
                    </div>
                </div>
            ` : ''}

            <div class="context-list">
                ${this.contexts.length === 0 && !this._showAddForm ? html`
                    <div style="text-align: center; padding: 3rem; color: #94a3b8; border: 2px dashed #e2e8f0; border-radius: 12px;">
                        No contexts added yet. Click "+ Add New Context" to get started.
                    </div>
                ` : ''}

                ${this.contexts.map(ctx => html`
                    ${this._editingId === ctx.id ? html`
                        <div class="editing-card">
                            <div class="input-group">
                                <label>Name</label>
                                <input type="text" .value=${this._editName} @input=${(e: any) => this._editName = e.target.value}>
                            </div>
                            <div class="input-group">
                                <label>Content</label>
                                <textarea .value=${this._editContent} @input=${(e: any) => this._editContent = e.target.value}></textarea>
                            </div>
                            <div class="form-actions">
                                <button class="btn-primary" style="padding: 0.5rem 1rem; display: flex; align-items: center; justify-content: center;" @click=${() => this._saveEdit(ctx.id)} ?disabled=${this._isSaving}>
                                    ${this._isSaving && this._editingId === ctx.id ? html`<span class="spinner"></span> Saving...` : 'Save Changes'}
                                </button>
                                <button class="btn-secondary" style="padding: 0.5rem 1rem;" @click=${this._cancelEdit} ?disabled=${this._isSaving}>Cancel</button>
                            </div>
                        </div>
                    ` : html`
                        <div class="context-item">
                            <div class="action-group">
                                <button class="edit-btn" @click=${() => this._startEdit(ctx)}>Edit</button>
                                <button class="delete-btn" title="Delete" @click=${() => this._deleteContext(ctx.id)}>&times;</button>
                            </div>
                            <div class="context-name">${ctx.name}</div>
                            <div class="context-content">${ctx.content}</div>
                        </div>
                    `}
                `)}
            </div>
        `;
    }
}
