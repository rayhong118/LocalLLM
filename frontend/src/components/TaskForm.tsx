import React, { useState } from 'react';
import { Calendar, Clock, Sparkles } from 'lucide-react';

interface TaskFormProps {
    onTaskCreated: () => void;
}

export const TaskForm: React.FC<TaskFormProps> = ({ onTaskCreated }) => {
    const [loading, setLoading] = useState(false);
    const [prompt, setPrompt] = useState('');
    const [frequency, setFrequency] = useState<'ONCE' | 'DAILY'>('ONCE');
    const [hourOfDay, setHourOfDay] = useState<number>(new Date().getHours());

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!prompt.trim()) return;

        setLoading(true);
        try {
            const res = await fetch('http://localhost:8000/tasks', {
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
                onTaskCreated();
            }
        } catch (err) {
            console.error('Failed to schedule task:', err);
        } finally {
            setLoading(false);
        }
    };

    return (
        <form onSubmit={handleSubmit} className="glass-card form-container animate-fade-in" style={{ padding: '1.75rem', marginBottom: '2rem' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', marginBottom: '1rem' }}>
                <Sparkles size={20} color="var(--color-primary)" />
                <h2 style={{ fontSize: '1.25rem', fontWeight: 700 }}>Schedule New Task</h2>
            </div>
            
            <textarea
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                placeholder="e.g., Search for Häagen-Dazs deals on Safeway and list flavors..."
                style={{ minHeight: '110px', marginBottom: '1.25rem', width: '100%', resize: 'vertical' }}
                disabled={loading}
                required
            />
            
            <div style={{ display: 'flex', gap: '1.25rem', flexWrap: 'wrap', marginBottom: '1.5rem' }}>
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
                style={{ width: '100%', padding: '0.85rem' }}
            >
                {loading ? (
                    <>
                        <span className="spinner"></span> Scheduling...
                    </>
                ) : (
                    <>
                        <Sparkles size={16} /> Schedule Task
                    </>
                )}
            </button>
        </form>
    );
};
