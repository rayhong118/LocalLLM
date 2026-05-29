import React, { useState, useEffect } from 'react';
import { TaskItem } from './TaskItem';
import { Task } from '../store';
import { RefreshCw, ListTodo, History, ChevronLeft, ChevronRight } from 'lucide-react';

interface TaskListProps {
    tasks: Task[];
    onTasksUpdated: (tasks: Task[]) => void;
}

export const TaskList: React.FC<TaskListProps> = ({ tasks, onTasksUpdated }) => {
    
    const deleteTask = async (taskId: number, needCancel: boolean) => {
        if (!confirm('Are you sure you want to delete this task?')) return;
        try {
            if (needCancel) {
                await cancelTask(taskId);
            }
            const res = await fetch(`http://localhost:8000/tasks/${taskId}`, { method: 'DELETE' });
            if (res.ok) {
                onTasksUpdated(tasks.filter(t => t.id !== taskId));
            }
        } catch (error) {
            console.error('Failed to delete task:', error);
        }
    };

    const retryTask = async (taskId: number) => {
        try {
            const res = await fetch(`http://localhost:8000/tasks/${taskId}/retry`, { method: 'POST' });
            if (res.ok) {
                const updatedTask = await res.json();
                onTasksUpdated(tasks.map(t => t.id === taskId ? updatedTask : t));
            }
        } catch (error) {
            console.error('Failed to retry task:', error);
        }
    };

    const runNowTask = async (taskId: number) => {
        try {
            const res = await fetch(`http://localhost:8000/tasks/${taskId}/run_now`, { method: 'POST' });
            if (res.ok) {
                const updatedTask = await res.json();
                onTasksUpdated(tasks.map(t => t.id === taskId ? updatedTask : t));
            }
        } catch (error) {
            console.error('Failed to run task now:', error);
        }
    };

    const cancelTask = async (taskId: number) => {
        try {
            const res = await fetch(`http://localhost:8000/tasks/${taskId}/cancel`, { method: 'POST' });
            if (res.ok) {
                const updatedTask = await res.json();
                onTasksUpdated(tasks.map(t => t.id === taskId ? updatedTask : t));
            }
        } catch (error) {
            console.error('Failed to cancel task:', error);
        }
    };

    const toLocalDate = (dateStr: string | null) => {
        if (!dateStr) return new Date();
        if (!dateStr.endsWith('Z') && !/[+-]\d{2}(?::?\d{2})?$/.test(dateStr)) {
            return new Date(dateStr + 'Z');
        }
        return new Date(dateStr);
    };

    const [currentPage, setCurrentPage] = useState(1);
    const itemsPerPage = 20;

    if (!tasks) return null;

    const recurring = tasks.filter(t => t.frequency === 'DAILY');
    const pending = [...tasks]
        .filter(t => t.frequency === 'ONCE' && (t.status === 'PENDING' || t.status === 'RUNNING' || t.status === 'CANCELLED'))
        .sort((a, b) => toLocalDate(a.created_at).getTime() - toLocalDate(b.created_at).getTime());
    const history = [...tasks]
        .filter(t => t.frequency === 'ONCE' && (t.status === 'COMPLETED' || t.status === 'FAILED'))
        .sort((a, b) => toLocalDate(b.started_at || b.created_at).getTime() - toLocalDate(a.started_at || a.created_at).getTime());

    const totalPages = Math.ceil(history.length / itemsPerPage);

    // Adjust current page if task deletion makes it out of bounds
    useEffect(() => {
        if (currentPage > totalPages && totalPages > 0) {
            setCurrentPage(totalPages);
        }
    }, [history.length, totalPages, currentPage]);

    const startIndex = (currentPage - 1) * itemsPerPage;
    const paginatedHistory = history.slice(startIndex, startIndex + itemsPerPage);

    const renderPageNumbers = () => {
        const pages = [];
        const maxVisiblePages = 5;
        let startPage = Math.max(1, currentPage - 2);
        let endPage = Math.min(totalPages, startPage + maxVisiblePages - 1);
        
        if (endPage - startPage < maxVisiblePages - 1) {
            startPage = Math.max(1, endPage - maxVisiblePages + 1);
        }
        
        for (let i = startPage; i <= endPage; i++) {
            pages.push(
                <button
                    key={i}
                    onClick={() => setCurrentPage(i)}
                    style={{
                        padding: '0.4rem 0.8rem',
                        fontSize: '0.85rem',
                        borderRadius: '6px',
                        border: '1px solid var(--color-border)',
                        background: currentPage === i ? 'var(--color-primary)' : 'rgba(31, 41, 55, 0.4)',
                        color: currentPage === i ? '#ffffff' : 'var(--color-text-muted)',
                        fontWeight: 600,
                        transition: 'all 0.2s',
                        cursor: 'pointer'
                    }}
                    onMouseEnter={(e) => {
                        if (currentPage !== i) {
                            e.currentTarget.style.color = '#ffffff';
                            e.currentTarget.style.borderColor = 'var(--color-border-hover)';
                        }
                    }}
                    onMouseLeave={(e) => {
                        if (currentPage !== i) {
                            e.currentTarget.style.color = 'var(--color-text-muted)';
                            e.currentTarget.style.borderColor = 'var(--color-border)';
                        }
                    }}
                >
                    {i}
                </button>
            );
        }
        return pages;
    };

    const sectionHeaderStyle: React.CSSProperties = {
        fontSize: '0.85rem',
        fontWeight: 600,
        color: 'var(--color-text-muted)',
        textTransform: 'uppercase',
        letterSpacing: '0.05em',
        margin: '2.5rem 0 1rem 0',
        display: 'flex',
        alignItems: 'center',
        gap: '0.5rem'
    };

    const countBadgeStyle: React.CSSProperties = {
        background: 'var(--color-border)',
        padding: '0.1rem 0.5rem',
        borderRadius: '6px',
        fontSize: '0.75rem',
        color: 'var(--color-text-body)'
    };

    return (
        <div className="animate-fade-in" style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
            {/* Recurring Tasks Section */}
            {recurring.length > 0 && (
                <div>
                    <div style={sectionHeaderStyle}>
                        <RefreshCw size={14} /> Recurring Tasks 
                        <span style={countBadgeStyle}>{recurring.length}</span>
                    </div>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
                        {recurring.map(t => (
                            <TaskItem 
                                key={t.id} 
                                task={t} 
                                onDelete={deleteTask}
                                onRetry={retryTask}
                                onRunNow={runNowTask}
                                onCancel={cancelTask}
                            />
                        ))}
                    </div>
                </div>
            )}

            {/* Pending Queue Section */}
            {pending.length > 0 && (
                <div>
                    <div style={sectionHeaderStyle}>
                        <ListTodo size={14} /> Pending Queue 
                        <span style={countBadgeStyle}>{pending.length}</span>
                    </div>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
                        {pending.map(t => (
                            <TaskItem 
                                key={t.id} 
                                task={t} 
                                onDelete={deleteTask}
                                onRetry={retryTask}
                                onRunNow={runNowTask}
                                onCancel={cancelTask}
                            />
                        ))}
                    </div>
                </div>
            )}

            {/* Completed History Section */}
            <div>
                <div style={sectionHeaderStyle}>
                    <History size={14} /> Completed History 
                    <span style={countBadgeStyle}>{history.length}</span>
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
                    {history.length === 0 ? (
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
                            No task history yet. Schedule a task above to get started.
                        </div>
                    ) : (
                        paginatedHistory.map(t => (
                            <TaskItem 
                                key={t.id} 
                                task={t} 
                                onDelete={deleteTask}
                                onRetry={retryTask}
                                onRunNow={runNowTask}
                                onCancel={cancelTask}
                            />
                        ))
                    )}
                </div>

                {/* Pagination Controls */}
                {totalPages > 1 && (
                    <div style={{
                        display: 'flex',
                        justifyContent: 'center',
                        alignItems: 'center',
                        gap: '0.5rem',
                        marginTop: '2.5rem',
                        paddingTop: '1.5rem',
                        borderTop: '1px solid var(--color-border)'
                    }}>
                        <button
                            className="secondary"
                            onClick={() => setCurrentPage(prev => Math.max(prev - 1, 1))}
                            disabled={currentPage === 1}
                            style={{ padding: '0.5rem 0.75rem', borderRadius: '8px', cursor: 'pointer' }}
                        >
                            <ChevronLeft size={16} />
                        </button>
                        
                        <div style={{ display: 'flex', gap: '0.35rem', alignItems: 'center' }}>
                            {renderPageNumbers()}
                        </div>
                        
                        <button
                            className="secondary"
                            onClick={() => setCurrentPage(prev => Math.min(prev + 1, totalPages))}
                            disabled={currentPage === totalPages}
                            style={{ padding: '0.5rem 0.75rem', borderRadius: '8px', cursor: 'pointer' }}
                        >
                            <ChevronRight size={16} />
                        </button>
                    </div>
                )}
            </div>
        </div>
    );
};
