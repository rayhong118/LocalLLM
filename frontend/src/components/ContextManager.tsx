import React, { useState, useEffect } from 'react';
import { BookOpen, Plus, Trash2, Edit2, Check, Info } from 'lucide-react';
import { Context } from '../store';

export const ContextManager: React.FC = () => {
    const [contexts, setContexts] = useState<Context[]>([]);
    const [isSaving, setIsSaving] = useState(false);
    const [newName, setNewName] = useState('');
    const [newContent, setNewContent] = useState('');
    const [showAddForm, setShowAddForm] = useState(false);
    const [editingId, setEditingId] = useState<number | null>(null);
    const [editName, setEditName] = useState('');
    const [editContent, setEditContent] = useState('');

    const fetchContexts = async () => {
        try {
            const res = await fetch('http://localhost:8000/contexts');
            if (res.ok) {
                const data = await res.json();
                setContexts(data);
            }
        } catch (err) {
            console.error('Failed to fetch contexts:', err);
        }
    };

    useEffect(() => {
        fetchContexts();
    }, []);

    const addContext = async () => {
        if (!newName.trim() || !newContent.trim()) return;

        setIsSaving(true);
        try {
            const res = await fetch('http://localhost:8000/contexts', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: newName, content: newContent })
            });
            if (res.ok) {
                setNewName('');
                setNewContent('');
                setShowAddForm(false);
                await fetchContexts();
            }
        } catch (err) {
            console.error('Failed to add context:', err);
        } finally {
            setIsSaving(false);
        }
    };

    const startEdit = (ctx: Context) => {
        setEditingId(ctx.id);
        setEditName(ctx.name);
        setEditContent(ctx.content);
    };

    const cancelEdit = () => {
        setEditingId(null);
    };

    const saveEdit = async (id: number) => {
        if (!editName.trim() || !editContent.trim()) return;

        setIsSaving(true);
        try {
            const res = await fetch(`http://localhost:8000/contexts/${id}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: editName, content: editContent })
            });
            if (res.ok) {
                setEditingId(null);
                await fetchContexts();
            }
        } catch (err) {
            console.error('Failed to update context:', err);
        } finally {
            setIsSaving(false);
        }
    };

    const deleteContext = async (id: number) => {
        if (!confirm('Are you sure you want to delete this context?')) return;
        try {
            const res = await fetch(`http://localhost:8000/contexts/${id}`, { method: 'DELETE' });
            if (res.ok) {
                await fetchContexts();
            }
        } catch (err) {
            console.error('Failed to delete context:', err);
        }
    };

    return (
        <div className="animate-fade-in" style={{ width: '100%' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '2rem', flexWrap: 'wrap', gap: '1rem' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
                    <BookOpen size={22} color="var(--color-primary)" />
                    <h2 style={{ fontSize: '1.5rem', fontWeight: 700 }}>Reference Contexts</h2>
                </div>
                <button 
                    className={showAddForm ? "secondary" : "primary"}
                    onClick={() => setShowAddForm(!showAddForm)}
                    style={{ padding: '0.6rem 1.25rem' }}
                >
                    {showAddForm ? (
                        <>Cancel</>
                    ) : (
                        <><Plus size={16} /> Add New Context</>
                    )}
                </button>
            </div>

            {showAddForm && (
                <div className="glass-card form-container animate-fade-in" style={{ padding: '1.75rem', marginBottom: '2rem' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '1.25rem', color: 'var(--color-text-muted)', fontSize: '0.875rem' }}>
                        <Info size={16} />
                        <span>Add details the AI agent should refer to before executing any scheduled tasks.</span>
                    </div>
                    
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem', marginBottom: '1.5rem' }}>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>
                            <label style={{ fontSize: '0.8rem', fontWeight: 600, color: 'var(--color-text-muted)' }}>Context Name</label>
                            <input 
                                type="text" 
                                placeholder="e.g. Shopping Credentials / Personal Preferences" 
                                value={newName} 
                                onChange={(e) => setNewName(e.target.value)}
                                disabled={isSaving}
                            />
                        </div>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>
                            <label style={{ fontSize: '0.8rem', fontWeight: 600, color: 'var(--color-text-muted)' }}>Context Content</label>
                            <textarea 
                                placeholder="e.g. My zip code is 94611. Preferred retail brand is Safeway. Prefer organic milk." 
                                value={newContent} 
                                onChange={(e) => setNewContent(e.target.value)}
                                style={{ minHeight: '100px', resize: 'vertical' }}
                                disabled={isSaving}
                            />
                        </div>
                    </div>
                    
                    <div style={{ display: 'flex', gap: '0.75rem' }}>
                        <button 
                            className="primary" 
                            onClick={addContext} 
                            disabled={isSaving || !newName.trim() || !newContent.trim()}
                            style={{ padding: '0.6rem 1.25rem' }}
                        >
                            {isSaving ? <span className="spinner"></span> : <Check size={16} />} Save Context
                        </button>
                        <button 
                            className="secondary" 
                            onClick={() => setShowAddForm(false)} 
                            disabled={isSaving}
                            style={{ padding: '0.6rem 1.25rem' }}
                        >
                            Cancel
                        </button>
                    </div>
                </div>
            )}

            <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
                {contexts.length === 0 && !showAddForm ? (
                    <div 
                        className="glass-card" 
                        style={{ 
                            textAlign: 'center', 
                            padding: '4rem 2rem', 
                            color: 'var(--color-text-light)', 
                            border: '1.5px dashed var(--color-border)', 
                            borderRadius: '12px' 
                        }}
                    >
                        No reference contexts added yet. Click "+ Add New Context" to get started.
                    </div>
                ) : (
                    contexts.map(ctx => (
                        <div key={ctx.id}>
                            {editingId === ctx.id ? (
                                <div className="glass-card animate-fade-in" style={{ padding: '1.5rem', border: '1.5px solid var(--color-primary)' }}>
                                    <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem', marginBottom: '1.25rem' }}>
                                        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>
                                            <label style={{ fontSize: '0.8rem', fontWeight: 600, color: 'var(--color-text-muted)' }}>Name</label>
                                            <input 
                                                type="text" 
                                                value={editName} 
                                                onChange={(e) => setEditName(e.target.value)}
                                                disabled={isSaving}
                                            />
                                        </div>
                                        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>
                                            <label style={{ fontSize: '0.8rem', fontWeight: 600, color: 'var(--color-text-muted)' }}>Content</label>
                                            <textarea 
                                                value={editContent} 
                                                onChange={(e) => setEditContent(e.target.value)}
                                                style={{ minHeight: '100px', resize: 'vertical' }}
                                                disabled={isSaving}
                                            />
                                        </div>
                                    </div>
                                    <div style={{ display: 'flex', gap: '0.75rem' }}>
                                        <button 
                                            className="primary" 
                                            onClick={() => saveEdit(ctx.id)} 
                                            disabled={isSaving || !editName.trim() || !editContent.trim()}
                                            style={{ padding: '0.5rem 1rem', fontSize: '0.8rem' }}
                                        >
                                            {isSaving ? <span className="spinner"></span> : <Check size={14} />} Save Changes
                                        </button>
                                        <button 
                                            className="secondary" 
                                            onClick={cancelEdit} 
                                            disabled={isSaving}
                                            style={{ padding: '0.5rem 1rem', fontSize: '0.8rem' }}
                                        >
                                            Cancel
                                        </button>
                                    </div>
                                </div>
                            ) : (
                                <div 
                                    className="glass-card animate-fade-in" 
                                    style={{ 
                                        padding: '1.25rem', 
                                        position: 'relative',
                                        transition: 'all 0.2s',
                                        border: '1px solid var(--color-border)'
                                    }}
                                >
                                    {/* Action Group Top Right */}
                                    <div style={{ position: 'absolute', top: '0.85rem', right: '0.85rem', display: 'flex', gap: '0.35rem', alignItems: 'center' }}>
                                        <button 
                                            className="secondary"
                                            onClick={() => startEdit(ctx)}
                                            title="Edit Context"
                                            style={{
                                                padding: '5px',
                                                borderRadius: '6px',
                                                border: 'none',
                                                background: 'none',
                                                cursor: 'pointer',
                                                color: 'var(--color-text-light)',
                                                display: 'flex',
                                                alignItems: 'center',
                                                justifyContent: 'center',
                                                transition: 'color 0.2s'
                                            }}
                                            onMouseEnter={(e) => e.currentTarget.style.color = 'var(--color-info)'}
                                            onMouseLeave={(e) => e.currentTarget.style.color = 'var(--color-text-light)'}
                                        >
                                            <Edit2 size={15} />
                                        </button>
                                        <button 
                                            className="secondary"
                                            onClick={() => deleteContext(ctx.id)}
                                            title="Delete Context"
                                            style={{
                                                padding: '5px',
                                                borderRadius: '6px',
                                                border: 'none',
                                                background: 'none',
                                                cursor: 'pointer',
                                                color: 'var(--color-text-light)',
                                                display: 'flex',
                                                alignItems: 'center',
                                                justifyContent: 'center',
                                                transition: 'color 0.2s'
                                            }}
                                            onMouseEnter={(e) => e.currentTarget.style.color = 'var(--color-error)'}
                                            onMouseLeave={(e) => e.currentTarget.style.color = 'var(--color-text-light)'}
                                        >
                                            <Trash2 size={15} />
                                        </button>
                                    </div>

                                    <div style={{ fontWeight: 700, color: 'var(--color-text-main)', fontSize: '1rem', marginBottom: '0.5rem', paddingRight: '4rem' }}>
                                        {ctx.name}
                                    </div>
                                    <div 
                                        style={{ 
                                            fontSize: '0.9rem', 
                                            color: 'var(--color-text-body)', 
                                            whiteSpace: 'pre-wrap', 
                                            lineHeight: 1.5,
                                            paddingTop: '0.25rem' 
                                        }}
                                    >
                                        {ctx.content}
                                    </div>
                                </div>
                            )}
                        </div>
                    ))
                )}
            </div>
        </div>
    );
};
