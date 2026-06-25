import React, { useState, useEffect } from 'react';
import { useAtom } from 'jotai';
import { savedTasksAtom, currentPageAtom } from '../store';
import { Sparkles, Calendar, Clock, Trash2, Play, Plus, BookOpen } from 'lucide-react';

export const CommonlyUsedTasks: React.FC = () => {
    const [savedTasks, setSavedTasks] = useAtom(savedTasksAtom);
    const [, setCurrentPage] = useAtom(currentPageAtom);
    const [loading, setLoading] = useState(false);
    const [fetching, setFetching] = useState(false);

    // Form states
    const [prompt, setPrompt] = useState('');
    const [frequency, setFrequency] = useState<'ONCE' | 'DAILY'>('ONCE');
    const [hourOfDay, setHourOfDay] = useState<number>(new Date().getHours());

    const fetchSavedTasks = async () => {
        setFetching(true);
        try {
            const res = await fetch('http://localhost:8000/saved_tasks');
            if (res.ok) {
                const data = await res.json();
                setSavedTasks(data);
            }
        } catch (err) {
            console.error('Failed to fetch saved tasks:', err);
        } finally {
            setFetching(false);
        }
    };

    useEffect(() => {
        fetchSavedTasks();
    }, []);

    const handleCreateSavedTask = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!prompt.trim()) return;

        setLoading(true);
        try {
            const res = await fetch('http://localhost:8000/saved_tasks', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    prompt,
                    frequency,
                    hour_of_day: frequency === 'DAILY' ? Number(hourOfDay) : null
                })
            });
            if (res.ok) {
                setPrompt('');
                fetchSavedTasks();
            }
        } catch (err) {
            console.error('Failed to create saved task:', err);
        } finally {
            setLoading(false);
        }
    };

    const handleDeleteSavedTask = async (id: number) => {
        if (!confirm('Are you sure you want to delete this saved task?')) return;

        try {
            const res = await fetch(`http://localhost:8000/saved_tasks/${id}`, {
                method: 'DELETE'
            });
            if (res.ok) {
                setSavedTasks(prev => prev.filter(t => t.id !== id));
            }
        } catch (err) {
            console.error('Failed to delete saved task:', err);
        }
    };

    const handleRunSavedTask = async (id: number) => {
        try {
            const res = await fetch(`http://localhost:8000/saved_tasks/${id}/run`, {
                method: 'POST'
            });
            if (res.ok) {
                // Redirect back to dashboard so user can see it in queue
                setCurrentPage('dashboard');
            }
        } catch (err) {
            console.error('Failed to run saved task:', err);
        }
    };

    return (
        <div className="animate-fade-in" style={{ display: 'flex', flexDirection: 'column', gap: '2rem' }}>
            
            {/* Create Saved Task Form */}
            <form onSubmit={handleCreateSavedTask} className="glass-card form-container" style={{ padding: '1.75rem' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', marginBottom: '1rem' }}>
                    <Plus size={20} color="var(--color-primary)" />
                    <h2 style={{ fontSize: '1.25rem', fontWeight: 700 }}>Add to Common Tasks</h2>
                </div>
                
                <textarea
                    value={prompt}
                    onChange={(e) => setPrompt(e.target.value)}
                    placeholder="Enter prompt to save (e.g. Check for Software Engineer roles on SoFi jobs page)..."
                    style={{ minHeight: '80px', marginBottom: '1.25rem', width: '100%', resize: 'vertical' }}
                    disabled={loading}
                    required
                />
                
                <div style={{ display: 'flex', gap: '1.25rem', flexWrap: 'wrap', marginBottom: '1.5rem', alignItems: 'flex-end' }}>
                    <div style={{ flex: 1, minWidth: '200px' }}>
                        <label style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', fontSize: '0.8rem', fontWeight: 600, marginBottom: '0.5rem', color: 'var(--color-text-muted)' }}>
                            <Calendar size={14} /> Frequency
                        </label>
                        <select 
                            value={frequency}
                            onChange={(e) => setFrequency(e.target.value as 'ONCE' | 'DAILY')}
                            disabled={loading}
                            style={{ width: '100%' }}
                        >
                            <option value="ONCE">One-time (Run Now)</option>
                            <option value="DAILY">Daily Recurring</option>
                        </select>
                    </div>

                    {frequency === 'DAILY' && (
                        <div style={{ width: '150px' }}>
                            <label style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', fontSize: '0.8rem', fontWeight: 600, marginBottom: '0.5rem', color: 'var(--color-text-muted)' }}>
                                <Clock size={14} /> Hour (0-23)
                            </label>
                            <input 
                                type="number" 
                                min="0" 
                                max="23" 
                                value={hourOfDay}
                                onChange={(e) => setHourOfDay(Number(e.target.value))}
                                disabled={loading}
                                style={{ width: '100%' }}
                                required
                            />
                        </div>
                    )}
                </div>

                <button 
                    type="submit" 
                    className="primary" 
                    disabled={loading || !prompt.trim()} 
                    style={{ width: '100%', padding: '0.75rem' }}
                >
                    {loading ? (
                        <>
                            <span className="spinner"></span> Saving...
                        </>
                    ) : (
                        <>
                            <Sparkles size={16} /> Save Common Task
                        </>
                    )}
                </button>
            </form>

            {/* Saved Tasks List */}
            <div>
                <div style={{
                    fontSize: '0.85rem',
                    fontWeight: 600,
                    color: 'var(--color-text-muted)',
                    textTransform: 'uppercase',
                    letterSpacing: '0.05em',
                    marginBottom: '1rem',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '0.5rem'
                }}>
                    <BookOpen size={14} /> Saved Common Tasks 
                    <span style={{
                        background: 'var(--color-border)',
                        padding: '0.1rem 0.5rem',
                        borderRadius: '6px',
                        fontSize: '0.75rem',
                        color: 'var(--color-text-body)'
                    }}>{savedTasks.length}</span>
                </div>

                {fetching && savedTasks.length === 0 ? (
                    <div className="glass-card" style={{ padding: '3rem', textAlign: 'center', color: 'var(--color-text-light)' }}>
                        <span className="spinner" style={{ marginRight: '8px' }}></span> Loading saved tasks...
                    </div>
                ) : savedTasks.length === 0 ? (
                    <div 
                        className="glass-card" 
                        style={{ 
                            padding: '3rem', 
                            textAlign: 'center', 
                            color: 'var(--color-text-light)', 
                            border: '1.5px dashed var(--color-border)', 
                            fontSize: '0.9rem',
                            borderRadius: '12px'
                        }}
                    >
                        No commonly used tasks saved yet. You can add one using the form above or click "Save to Common" on any completed task in the dashboard.
                    </div>
                ) : (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
                        {savedTasks.map(task => (
                            <div 
                                key={task.id}
                                className="glass-card task-card"
                                style={{
                                    display: 'flex',
                                    justifyContent: 'space-between',
                                    alignItems: 'center',
                                    padding: '1.25rem',
                                    border: '1px solid var(--color-border)',
                                    borderRadius: '12px',
                                    gap: '1.5rem',
                                    flexWrap: 'wrap'
                                }}
                            >
                                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem', flex: 1, minWidth: '250px' }}>
                                    <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'center' }}>
                                        <span 
                                            className="status-badge"
                                            style={{
                                                padding: '0.25rem 0.75rem',
                                                borderRadius: '999px',
                                                fontSize: '0.7rem',
                                                fontWeight: 700,
                                                textTransform: 'uppercase',
                                                whiteSpace: 'nowrap',
                                                backgroundColor: task.frequency === 'DAILY' ? 'var(--color-info-bg)' : 'var(--color-neutral-bg)',
                                                color: task.frequency === 'DAILY' ? 'var(--color-info)' : 'var(--color-neutral)'
                                            }}
                                        >
                                            {task.frequency}
                                        </span>
                                        <span style={{ fontSize: '0.75rem', color: 'var(--color-text-muted)', display: 'flex', alignItems: 'center', gap: '0.2rem' }}>
                                            <Clock size={12} />
                                            {task.frequency === 'DAILY' ? `Every day at ${String(task.hour_of_day).padStart(2, '0')}:00` : 'One-time run template'}
                                        </span>
                                    </div>
                                    <span style={{ fontWeight: 600, color: 'var(--color-text-body)', fontSize: '0.95rem', wordBreak: 'break-word' }}>
                                        {task.prompt}
                                    </span>
                                </div>

                                <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
                                    <button 
                                        className="primary" 
                                        style={{ fontSize: '0.75rem', padding: '0.45rem 1rem', borderRadius: '6px' }}
                                        onClick={() => handleRunSavedTask(task.id)}
                                    >
                                        <Play size={12} /> Run Task
                                    </button>
                                    <button 
                                        className="delete-btn" 
                                        style={{
                                            padding: '8px',
                                            borderRadius: '6px',
                                            border: '1px solid transparent',
                                            background: 'none',
                                            color: 'var(--color-text-light)',
                                            cursor: 'pointer',
                                            display: 'flex',
                                            alignItems: 'center',
                                            justifyContent: 'center',
                                            transition: 'all 0.2s'
                                        }}
                                        onClick={() => handleDeleteSavedTask(task.id)}
                                        onMouseEnter={(e) => {
                                            e.currentTarget.style.color = 'var(--color-error)';
                                            e.currentTarget.style.background = 'var(--color-error-bg)';
                                            e.currentTarget.style.borderColor = 'rgba(239, 68, 68, 0.2)';
                                        }}
                                        onMouseLeave={(e) => {
                                            e.currentTarget.style.color = 'var(--color-text-light)';
                                            e.currentTarget.style.background = 'none';
                                            e.currentTarget.style.borderColor = 'transparent';
                                        }}
                                    >
                                        <Trash2 size={14} />
                                    </button>
                                </div>
                            </div>
                        ))}
                    </div>
                )}
            </div>
        </div>
    );
};
