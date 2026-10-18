import React, { useState, useEffect } from 'react';
import { Calendar, Clock, AlertCircle, FileText } from 'lucide-react';
import { Modal } from './Modal';
import { Task, TaskFrequency } from '../store';

interface EditScheduleModalProps {
    isOpen: boolean;
    onClose: () => void;
    task: Task;
    onSave: (data: {
        prompt: string;
        frequency: TaskFrequency;
        hour_of_day: number;
        weekday_only?: boolean;
        day_of_week?: number;
        day_of_month?: number;
    }) => Promise<void>;
}

export const EditScheduleModal: React.FC<EditScheduleModalProps> = ({
    isOpen,
    onClose,
    task,
    onSave
}) => {
    const [prompt, setPrompt] = useState<string>(task.prompt || '');
    const [frequency, setFrequency] = useState<TaskFrequency>(
        task.frequency === 'ONCE' ? 'DAILY' : task.frequency
    );
    const [hourOfDay, setHourOfDay] = useState<number>(task.hour_of_day ?? 9);
    const [weekdayOnly, setWeekdayOnly] = useState<boolean>(task.weekday_only ?? false);
    const [dayOfWeek, setDayOfWeek] = useState<number>(task.day_of_week ?? 0);
    const [dayOfMonth, setDayOfMonth] = useState<number>(task.day_of_month ?? 1);
    const [tzAbbreviation, setTzAbbreviation] = useState('');
    const [isSaving, setIsSaving] = useState(false);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        if (isOpen) {
            setPrompt(task.prompt || '');
            setFrequency(task.frequency === 'ONCE' ? 'DAILY' : task.frequency);
            setHourOfDay(task.hour_of_day ?? 9);
            setWeekdayOnly(task.weekday_only ?? false);
            setDayOfWeek(task.day_of_week ?? 0);
            setDayOfMonth(task.day_of_month ?? 1);
            setError(null);
            setIsSaving(false);
        }
    }, [isOpen, task]);

    useEffect(() => {
        const tz = new Intl.DateTimeFormat('en-US', { timeZoneName: 'short' })
            .formatToParts(new Date())
            .find(p => p.type === 'timeZoneName')?.value || '';
        setTzAbbreviation(tz);
    }, []);

    const formatHourLabel = (h: number) => {
        const hh = String(h).padStart(2, '0');
        const period = h >= 12 ? 'PM' : 'AM';
        const h12 = h % 12 === 0 ? 12 : h % 12;
        return `${hh}:00 (${h12}:00 ${period})`;
    };

    const getPreviewText = () => {
        const hh = String(hourOfDay).padStart(2, '0') + ':00';
        if (frequency === 'DAILY') {
            return weekdayOnly 
                ? `Every weekday (Mon – Fri) at ${hh} ${tzAbbreviation}` 
                : `Every day at ${hh} ${tzAbbreviation}`;
        }
        if (frequency === 'WEEKLY') {
            const days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
            const dayName = days[dayOfWeek] || 'Monday';
            return `Every week on ${dayName} at ${hh} ${tzAbbreviation}`;
        }
        if (frequency === 'MONTHLY') {
            const d = dayOfMonth;
            const suffix = (d % 10 === 1 && d !== 11) ? 'st' : (d % 10 === 2 && d !== 12) ? 'nd' : (d % 10 === 3 && d !== 13) ? 'rd' : 'th';
            return `Day ${d}${suffix} of every month at ${hh} ${tzAbbreviation}`;
        }
        return '';
    };

    const handleConfirm = async () => {
        setError(null);
        const trimmedPrompt = prompt.trim();
        if (!trimmedPrompt) {
            setError('Task prompt cannot be empty');
            return;
        }
        if (frequency === 'MONTHLY' && (dayOfMonth < 1 || dayOfMonth > 31)) {
            setError('Day of month must be between 1 and 31');
            return;
        }

        setIsSaving(true);
        try {
            await onSave({
                prompt: trimmedPrompt,
                frequency,
                hour_of_day: hourOfDay,
                weekday_only: frequency === 'DAILY' ? weekdayOnly : false,
                day_of_week: frequency === 'WEEKLY' ? dayOfWeek : undefined,
                day_of_month: frequency === 'MONTHLY' ? dayOfMonth : undefined
            });
        } catch (err: any) {
            setError(err?.message || 'Failed to update schedule');
            setIsSaving(false);
        }
    };

    return (
        <Modal
            isOpen={isOpen}
            onClose={onClose}
            maxWidth="520px"
            title="Edit Scheduled Task"
            icon={<Calendar size={20} color="var(--color-primary)" />}
            footer={
                <>
                    <button 
                        type="button" 
                        className="secondary" 
                        onClick={onClose}
                        disabled={isSaving}
                    >
                        Cancel
                    </button>
                    <button 
                        type="button" 
                        className="primary" 
                        onClick={handleConfirm}
                        disabled={isSaving}
                    >
                        {isSaving ? 'Saving...' : 'Save Changes'}
                    </button>
                </>
            }
        >
            <div style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>
                {/* Task prompt input */}
                <div>
                    <label style={{ 
                        display: 'flex', 
                        alignItems: 'center', 
                        gap: '0.4rem', 
                        fontSize: '0.8rem', 
                        fontWeight: 600, 
                        marginBottom: '0.5rem', 
                        color: 'var(--color-text-muted)' 
                    }}>
                        <FileText size={14} /> Task Prompt
                    </label>
                    <textarea
                        value={prompt}
                        onChange={(e) => setPrompt(e.target.value)}
                        placeholder="Enter task prompt..."
                        rows={3}
                        style={{
                            minHeight: '88px',
                            resize: 'vertical',
                            lineHeight: 1.5,
                            fontSize: '0.9rem'
                        }}
                        disabled={isSaving}
                        required
                    />
                </div>

                {error && (
                    <div style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: '0.5rem',
                        padding: '0.75rem 1rem',
                        borderRadius: '8px',
                        background: 'var(--color-error-bg)',
                        color: 'var(--color-error)',
                        fontSize: '0.85rem'
                    }}>
                        <AlertCircle size={16} />
                        <span>{error}</span>
                    </div>
                )}

                {/* Frequency selection */}
                <div>
                    <label style={{ 
                        display: 'flex', 
                        alignItems: 'center', 
                        gap: '0.4rem', 
                        fontSize: '0.8rem', 
                        fontWeight: 600, 
                        marginBottom: '0.5rem', 
                        color: 'var(--color-text-muted)' 
                    }}>
                        <Calendar size={14} /> Frequency
                    </label>
                    <select
                        value={frequency}
                        onChange={(e) => setFrequency(e.target.value as TaskFrequency)}
                        disabled={isSaving}
                    >
                        <option value="DAILY">Daily Recurring</option>
                        <option value="WEEKLY">Weekly Recurring</option>
                        <option value="MONTHLY">Monthly Recurring</option>
                    </select>
                </div>

                {/* Conditional frequency options */}
                {frequency === 'DAILY' && (
                    <div>
                        <label 
                            style={{ 
                                display: 'inline-flex', 
                                alignItems: 'center', 
                                gap: '0.55rem', 
                                cursor: 'pointer', 
                                fontSize: '0.85rem', 
                                fontWeight: 600, 
                                color: weekdayOnly ? 'var(--color-primary-active)' : 'var(--color-text-body)',
                                background: weekdayOnly ? 'var(--color-primary-soft-hover)' : 'var(--color-bg-alt)',
                                border: `1px solid ${weekdayOnly ? 'var(--color-primary)' : 'var(--color-border)'}`,
                                padding: '0.65rem 0.95rem',
                                borderRadius: '10px',
                                transition: 'all 0.2s cubic-bezier(0.4, 0, 0.2, 1)',
                                userSelect: 'none',
                                width: '100%',
                                boxSizing: 'border-box'
                            }}
                        >
                            <input 
                                type="checkbox"
                                checked={weekdayOnly}
                                onChange={(e) => setWeekdayOnly(e.target.checked)}
                                disabled={isSaving}
                                style={{ 
                                    width: '16px', 
                                    height: '16px', 
                                    accentColor: 'var(--color-primary)', 
                                    cursor: 'pointer', 
                                    margin: 0 
                                }}
                            />
                            <span>Weekday only (Monday – Friday)</span>
                        </label>
                    </div>
                )}

                {frequency === 'WEEKLY' && (
                    <div>
                        <label style={{ 
                            display: 'flex', 
                            alignItems: 'center', 
                            gap: '0.4rem', 
                            fontSize: '0.8rem', 
                            fontWeight: 600, 
                            marginBottom: '0.5rem', 
                            color: 'var(--color-text-muted)' 
                        }}>
                            <Calendar size={14} /> Day of Week
                        </label>
                        <select
                            value={dayOfWeek}
                            onChange={(e) => setDayOfWeek(Number(e.target.value))}
                            disabled={isSaving}
                        >
                            <option value={0}>Every Monday</option>
                            <option value={1}>Every Tuesday</option>
                            <option value={2}>Every Wednesday</option>
                            <option value={3}>Every Thursday</option>
                            <option value={4}>Every Friday</option>
                            <option value={5}>Every Saturday</option>
                            <option value={6}>Every Sunday</option>
                        </select>
                    </div>
                )}

                {frequency === 'MONTHLY' && (
                    <div>
                        <label style={{ 
                            display: 'flex', 
                            alignItems: 'center', 
                            gap: '0.4rem', 
                            fontSize: '0.8rem', 
                            fontWeight: 600, 
                            marginBottom: '0.5rem', 
                            color: 'var(--color-text-muted)' 
                        }}>
                            <Calendar size={14} /> Day of Month (1 – 31)
                        </label>
                        <input 
                            type="number" 
                            min="1" 
                            max="31" 
                            value={dayOfMonth}
                            onChange={(e) => setDayOfMonth(Math.max(1, Math.min(31, Number(e.target.value) || 1)))}
                            disabled={isSaving}
                            required
                        />
                    </div>
                )}

                {/* Hour of Day selection */}
                <div>
                    <label style={{ 
                        display: 'flex', 
                        alignItems: 'center', 
                        gap: '0.4rem', 
                        fontSize: '0.8rem', 
                        fontWeight: 600, 
                        marginBottom: '0.5rem', 
                        color: 'var(--color-text-muted)' 
                    }}>
                        <Clock size={14} /> Hour of Day ({tzAbbreviation ? `${tzAbbreviation} Local Time` : 'Local Time'})
                    </label>
                    <select
                        value={hourOfDay}
                        onChange={(e) => setHourOfDay(Number(e.target.value))}
                        disabled={isSaving}
                    >
                        {Array.from({ length: 24 }, (_, i) => (
                            <option key={i} value={i}>
                                {formatHourLabel(i)}
                            </option>
                        ))}
                    </select>
                </div>

                {/* Live Preview Card */}
                <div style={{
                    padding: '0.85rem 1rem',
                    borderRadius: '8px',
                    background: 'var(--color-primary-soft)',
                    border: '1px solid var(--color-primary-border)',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '0.65rem'
                }}>
                    <Clock size={16} color="var(--color-primary-active)" />
                    <div style={{ fontSize: '0.85rem', color: 'var(--color-primary-active)', fontWeight: 600 }}>
                        <span style={{ fontSize: '0.72rem', textTransform: 'uppercase', display: 'block', opacity: 0.8 }}>
                            Schedule Preview
                        </span>
                        {getPreviewText()}
                    </div>
                </div>
            </div>
        </Modal>
    );
};
