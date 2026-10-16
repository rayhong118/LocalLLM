import React, { useState, useEffect } from 'react';
import { BookOpen, Check, Plus } from 'lucide-react';
import { Context } from '../store';
import { Modal } from './Modal';

interface ContextSelectModalProps {
    isOpen: boolean;
    onClose: () => void;
    onAdd: (selectedContexts: Context[]) => void;
    alreadySelectedIds?: number[];
    title?: string;
}

export const ContextSelectModal: React.FC<ContextSelectModalProps> = ({
    isOpen,
    onClose,
    onAdd,
    alreadySelectedIds = [],
    title = "Attach Context to Task"
}) => {
    const [allContexts, setAllContexts] = useState<Context[]>([]);
    const [selectedIds, setSelectedIds] = useState<number[]>([]);
    const [loading, setLoading] = useState<boolean>(false);

    useEffect(() => {
        if (isOpen) {
            fetchContexts();
            setSelectedIds([]);
        }
    }, [isOpen]);

    const fetchContexts = async () => {
        setLoading(true);
        try {
            const res = await fetch('http://localhost:8000/contexts');
            if (res.ok) {
                const data = await res.json();
                setAllContexts(data);
            }
        } catch (err) {
            console.error('Failed to load contexts in modal:', err);
        } finally {
            setLoading(false);
        }
    };

    const toggleSelect = (id: number) => {
        if (selectedIds.includes(id)) {
            setSelectedIds(selectedIds.filter(i => i !== id));
        } else {
            setSelectedIds([...selectedIds, id]);
        }
    };

    const handleConfirm = () => {
        const chosen = allContexts.filter(c => selectedIds.includes(c.id));
        onAdd(chosen);
        onClose();
    };

    return (
        <Modal
            isOpen={isOpen}
            onClose={onClose}
            title={title}
            icon={<BookOpen size={20} color="var(--color-primary)" />}
            footer={
                <>
                    <button 
                        type="button"
                        className="secondary" 
                        onClick={onClose}
                        style={{ padding: '0.5rem 1rem', fontSize: '0.85rem' }}
                    >
                        Cancel
                    </button>
                    <button 
                        type="button"
                        className="primary" 
                        onClick={handleConfirm}
                        disabled={selectedIds.length === 0}
                        style={{ padding: '0.5rem 1.25rem', fontSize: '0.85rem' }}
                    >
                        Attach ({selectedIds.length})
                    </button>
                </>
            }
        >
            <p style={{ fontSize: '0.825rem', color: 'var(--color-text-muted)', margin: '0 0 1rem 0' }}>
                Select custom data profiles (e.g. stock watchlists, grocery shopping lists) to bind to this task.
            </p>

            <div style={{ flex: 1, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: '0.75rem', paddingRight: '4px', minHeight: '120px' }}>
                {loading ? (
                    <div style={{ textAlign: 'center', padding: '2rem', color: 'var(--color-text-muted)' }}>
                        <span className="spinner"></span> Loading contexts...
                    </div>
                ) : allContexts.length === 0 ? (
                    <div style={{ textAlign: 'center', padding: '2rem 1rem', color: 'var(--color-text-muted)', border: '1px dashed var(--color-border)', borderRadius: '8px' }}>
                        No reference contexts found in database.<br />
                        <span style={{ fontSize: '0.8rem', color: 'var(--color-text-light)' }}>Create one in the Reference Contexts tab.</span>
                    </div>
                ) : (
                    allContexts.map(ctx => {
                        const isAlreadyAttached = alreadySelectedIds.includes(ctx.id);
                        const isSelected = selectedIds.includes(ctx.id);

                        return (
                            <div 
                                key={ctx.id}
                                onClick={() => {
                                    if (!isAlreadyAttached) toggleSelect(ctx.id);
                                }}
                                style={{
                                    padding: '0.85rem 1rem',
                                    borderRadius: '8px',
                                    border: isAlreadyAttached 
                                        ? '1px dashed rgba(148, 163, 184, 0.4)'
                                        : isSelected 
                                        ? '1.5px solid var(--color-primary)' 
                                        : '1px solid var(--color-border)',
                                    backgroundColor: isSelected 
                                        ? 'rgba(99, 102, 241, 0.12)' 
                                        : isAlreadyAttached 
                                        ? 'rgba(0, 0, 0, 0.15)' 
                                        : 'rgba(255, 255, 255, 0.02)',
                                    cursor: isAlreadyAttached ? 'default' : 'pointer',
                                    opacity: isAlreadyAttached ? 0.6 : 1,
                                    display: 'flex',
                                    alignItems: 'flex-start',
                                    justifyContent: 'space-between',
                                    gap: '0.75rem',
                                    transition: 'all 0.15s ease'
                                }}
                            >
                                <div style={{ flex: 1, minWidth: 0 }}>
                                    <div style={{ fontWeight: 600, fontSize: '0.9rem', color: isSelected ? 'var(--color-primary)' : 'var(--color-text-main)', marginBottom: '0.25rem' }}>
                                        {ctx.name}
                                    </div>
                                    <div style={{ fontSize: '0.8rem', color: 'var(--color-text-muted)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                                        {ctx.content}
                                    </div>
                                </div>
                                <div style={{ flexShrink: 0, marginTop: '2px' }}>
                                    {isAlreadyAttached ? (
                                        <span style={{ fontSize: '0.75rem', padding: '0.2rem 0.5rem', borderRadius: '4px', backgroundColor: 'rgba(148, 163, 184, 0.2)', color: 'var(--color-text-muted)' }}>
                                            Already Added
                                        </span>
                                    ) : isSelected ? (
                                        <div style={{ width: '22px', height: '22px', borderRadius: '6px', backgroundColor: 'var(--color-primary)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#fff' }}>
                                            <Check size={14} />
                                        </div>
                                    ) : (
                                        <div style={{ width: '22px', height: '22px', borderRadius: '6px', border: '1.5px solid var(--color-border)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--color-text-muted)' }}>
                                            <Plus size={14} />
                                        </div>
                                    )}
                                </div>
                            </div>
                        );
                    })
                )}
            </div>
        </Modal>
    );
};
