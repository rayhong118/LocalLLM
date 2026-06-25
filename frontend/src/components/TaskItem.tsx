import React, { useState, useEffect } from 'react';
import { marked } from 'marked';
import { Trash2, Play, RotateCcw, XCircle, Copy, Check, ChevronDown, ChevronUp, Clock, CalendarDays, Sparkles } from 'lucide-react';
import { Task } from '../store';

interface TaskItemProps {
    task: Task;
    onDelete: (id: number, needCancel: boolean) => void;
    onRetry: (id: number) => void;
    onRunNow: (id: number) => void;
    onCancel: (id: number) => void;
}

export const TaskItem: React.FC<TaskItemProps> = ({
    task,
    onDelete,
    onRetry,
    onRunNow,
    onCancel
}) => {
    const [isExpanded, setIsExpanded] = useState(false);
    const [copied, setCopied] = useState(false);
    const [saved, setSaved] = useState(false);
    const [saving, setSaving] = useState(false);
    const [tzAbbreviation, setTzAbbreviation] = useState('');

    useEffect(() => {
        const tz = new Intl.DateTimeFormat('en-US', { timeZoneName: 'short' })
            .formatToParts(new Date())
            .find(p => p.type === 'timeZoneName')?.value || '';
        setTzAbbreviation(tz);
    }, []);

    const toLocalDate = (dateStr: string | null) => {
        if (!dateStr) return new Date();
        if (!dateStr.endsWith('Z') && !/[+-]\d{2}(?::?\d{2})?$/.test(dateStr)) {
            return new Date(dateStr + 'Z');
        }
        return new Date(dateStr);
    };

    const handleCopy = async (e: React.MouseEvent) => {
        e.stopPropagation();
        try {
            await navigator.clipboard.writeText(task.prompt);
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
        } catch {
            const ta = document.createElement('textarea');
            ta.value = task.prompt;
            document.body.appendChild(ta);
            ta.select();
            document.execCommand('copy');
            document.body.removeChild(ta);
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
        }
    };

    const handleSaveToCommon = async (e: React.MouseEvent) => {
        e.stopPropagation();
        setSaving(true);
        try {
            const res = await fetch('http://localhost:8000/saved_tasks', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    prompt: task.prompt,
                    frequency: task.frequency,
                    hour_of_day: task.hour_of_day
                })
            });
            if (res.ok) {
                setSaved(true);
                setTimeout(() => setSaved(false), 2000);
            }
        } catch (err) {
            console.error('Failed to save to commonly used tasks:', err);
        } finally {
            setSaving(false);
        }
    };

    const hasOutput = task.outputs && task.outputs.length > 0 && task.status !== 'RUNNING';
    const latestOutput = hasOutput ? task.outputs[task.outputs.length - 1].content : '';
    const parsedHtml = hasOutput ? (marked.parse(latestOutput, { async: false, gfm: true, breaks: true }) as string) : '';



    return (
        <div 
            className={`glass-card task-card animate-fade-in ${task.status}`}
            style={{
                position: 'relative',
                padding: '1.25rem',
                border: '1px solid var(--color-border)',
                animation: task.status === 'RUNNING' ? 'pulseBorder 2.5s infinite ease-in-out' : undefined,
                borderWidth: task.status === 'RUNNING' ? '1.5px' : '1px'
            }}
        >
            {/* Delete button absolutely positioned top right */}
            <button 
                className="delete-btn" 
                onClick={(e) => { e.stopPropagation(); onDelete(task.id, task.status !== 'RUNNING'); }}
                title="Delete Task"
                style={{
                    position: 'absolute',
                    top: '0.85rem',
                    right: '0.85rem',
                    background: 'none',
                    border: 'none',
                    color: 'var(--color-text-light)',
                    cursor: 'pointer',
                    padding: '4px',
                    borderRadius: '50%',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    transition: 'all 0.2s'
                }}
                onMouseEnter={(e) => {
                    e.currentTarget.style.color = 'var(--color-error)';
                    e.currentTarget.style.background = 'var(--color-error-bg)';
                }}
                onMouseLeave={(e) => {
                    e.currentTarget.style.color = 'var(--color-text-light)';
                    e.currentTarget.style.background = 'none';
                }}
            >
                <Trash2 size={16} />
            </button>

            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '1.5rem', marginBottom: '0.85rem', paddingRight: '2rem' }}>
                <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'center', flex: 1, minWidth: 0 }}>
                    <span 
                        className="status-badge"
                        style={{
                            padding: '0.25rem 0.75rem',
                            borderRadius: '999px',
                            fontSize: '0.7rem',
                            fontWeight: 700,
                            textTransform: 'uppercase',
                            whiteSpace: 'nowrap',
                            display: 'inline-block',
                            backgroundColor: 
                                task.frequency === 'DAILY' ? 'var(--color-info-bg)' :
                                task.status === 'COMPLETED' ? 'var(--color-success-bg)' :
                                task.status === 'RUNNING' ? 'var(--color-warning-bg)' :
                                task.status === 'FAILED' ? 'var(--color-error-bg)' :
                                'var(--color-neutral-bg)',
                            color:
                                task.frequency === 'DAILY' ? 'var(--color-info)' :
                                task.status === 'COMPLETED' ? 'var(--color-success)' :
                                task.status === 'RUNNING' ? 'var(--color-warning)' :
                                task.status === 'FAILED' ? 'var(--color-error)' :
                                'var(--color-neutral)'
                        }}
                    >
                        {task.frequency === 'DAILY' ? 'DAILY' : task.status}
                    </span>
                    <span style={{ fontWeight: 600, color: 'var(--color-text-body)', lineHeight: 1.4, fontSize: '0.95rem', wordBreak: 'break-word' }}>
                        {task.prompt}
                    </span>
                </div>
                
                {/* Task Actions */}
                <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', flexShrink: 0 }}>
                    {task.frequency === 'DAILY' && task.status !== 'RUNNING' && (
                        <button 
                            className="secondary" 
                            style={{ fontSize: '0.75rem', padding: '0.35rem 0.75rem', borderRadius: '6px' }}
                            onClick={(e) => { e.stopPropagation(); onRunNow(task.id); }}
                        >
                            <Play size={12} /> Run Now
                        </button>
                    )}
                    {(task.status === 'FAILED' || task.status === 'CANCELLED') && task.frequency !== 'DAILY' && (
                        <button 
                            className="primary" 
                            style={{ fontSize: '0.75rem', padding: '0.35rem 0.75rem', borderRadius: '6px' }}
                            onClick={(e) => { e.stopPropagation(); onRetry(task.id); }}
                        >
                            <RotateCcw size={12} /> Retry
                        </button>
                    )}
                    {task.status === 'COMPLETED' && (
                        <div style={{ display: 'flex', gap: '0.5rem' }}>
                            <button 
                                className="secondary" 
                                style={{ 
                                    fontSize: '0.75rem', 
                                    padding: '0.35rem 0.75rem', 
                                    borderRadius: '6px',
                                    borderColor: copied ? 'var(--color-success)' : undefined,
                                    color: copied ? 'var(--color-success)' : undefined,
                                    background: copied ? 'var(--color-success-bg)' : undefined
                                }}
                                onClick={handleCopy}
                            >
                                {copied ? <Check size={12} /> : <Copy size={12} />}
                                {copied ? 'Copied!' : 'Copy'}
                            </button>
                            <button 
                                className="secondary" 
                                style={{ 
                                    fontSize: '0.75rem', 
                                    padding: '0.35rem 0.75rem', 
                                    borderRadius: '6px',
                                    borderColor: saved ? 'var(--color-success)' : undefined,
                                    color: saved ? 'var(--color-success)' : undefined,
                                    background: saved ? 'var(--color-success-bg)' : undefined
                                }}
                                onClick={handleSaveToCommon}
                                disabled={saving}
                            >
                                {saved ? <Check size={12} /> : <Sparkles size={12} />}
                                {saved ? 'Saved!' : 'Save to Common'}
                            </button>
                        </div>
                    )}
                    {task.status === 'RUNNING' && (
                        <button 
                            className="danger" 
                            style={{ fontSize: '0.75rem', padding: '0.35rem 0.75rem', borderRadius: '6px' }}
                            onClick={(e) => { e.stopPropagation(); onCancel(task.id); }}
                        >
                            <XCircle size={12} /> Cancel
                        </button>
                    )}
                </div>
            </div>

            {/* Time Stamp and Expandable Toggle */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '0.5rem' }}>
                <span style={{ fontSize: '0.75rem', color: 'var(--color-text-muted)', display: 'flex', alignItems: 'center', gap: '0.3rem' }}>
                    {task.frequency === 'DAILY' ? <Clock size={12} /> : <CalendarDays size={12} />}
                    {task.frequency === 'DAILY' ? `Every day at ${String(task.hour_of_day).padStart(2, '0')}:00 ${tzAbbreviation}` :
                     task.status === 'RUNNING' ? `Started: ${toLocalDate(task.started_at).toLocaleTimeString()} ${tzAbbreviation}` :
                     task.status === 'CANCELLED' ? `Cancelled at: ${toLocalDate(task.updated_at).toLocaleTimeString()} ${tzAbbreviation}` :
                     `Last Run: ${toLocalDate(task.updated_at).toLocaleString()} ${tzAbbreviation}`}
                </span>
                
                {hasOutput && (
                    <button 
                        style={{
                            background: 'none',
                            border: 'none',
                            padding: '4px 8px',
                            fontSize: '0.75rem',
                            color: 'var(--color-info)',
                            fontWeight: 600,
                            cursor: 'pointer',
                            display: 'flex',
                            alignItems: 'center',
                            gap: '0.2rem',
                            borderRadius: '4px',
                            transition: 'color 0.2s'
                        }}
                        onClick={() => setIsExpanded(!isExpanded)}
                        onMouseEnter={(e) => e.currentTarget.style.color = 'var(--color-primary)'}
                        onMouseLeave={(e) => e.currentTarget.style.color = 'var(--color-info)'}
                    >
                        {isExpanded ? (
                            <>Hide Result <ChevronUp size={12} /></>
                        ) : (
                            <>View Result <ChevronDown size={12} /></>
                        )}
                    </button>
                )}
            </div>

            {/* Collapsible output display with custom markdown renderer */}
            {isExpanded && hasOutput && (
                <div 
                    className="output-container" 
                    onClick={(e) => e.stopPropagation()}
                    style={{
                        marginTop: '1rem',
                        padding: '1.25rem',
                        background: 'rgba(0, 0, 0, 0.25)',
                        borderRadius: '8px',
                        borderLeft: '4px solid var(--color-info)',
                        animation: 'slideDown 0.3s cubic-bezier(0.16, 1, 0.3, 1) forwards',
                        maxHeight: '2000px',
                        overflowY: 'auto'
                    }}
                >
                    <div 
                        className="markdown-body"
                        style={{
                            fontSize: '0.95rem',
                            lineHeight: 1.6,
                            color: 'var(--color-text-body)'
                        }}
                        dangerouslySetInnerHTML={{ __html: parsedHtml }}
                    />
                </div>
            )}
        </div>
    );
};
