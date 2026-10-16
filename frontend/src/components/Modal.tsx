import React, { useEffect } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';

export interface ModalProps {
    isOpen: boolean;
    onClose: () => void;
    title?: React.ReactNode;
    icon?: React.ReactNode;
    children: React.ReactNode;
    footer?: React.ReactNode;
    maxWidth?: string;
}

export const Modal: React.FC<ModalProps> = ({
    isOpen,
    onClose,
    title,
    icon,
    children,
    footer,
    maxWidth = '540px'
}) => {
    // Close on Escape key press and prevent background scrolling
    useEffect(() => {
        if (!isOpen) return;

        const handleKeyDown = (e: KeyboardEvent) => {
            if (e.key === 'Escape') {
                onClose();
            }
        };

        const originalOverflow = document.body.style.overflow;
        document.body.style.overflow = 'hidden';
        window.addEventListener('keydown', handleKeyDown);

        return () => {
            document.body.style.overflow = originalOverflow;
            window.removeEventListener('keydown', handleKeyDown);
        };
    }, [isOpen, onClose]);

    if (!isOpen) return null;

    const modalContent = (
        <div 
            style={{
                position: 'fixed',
                top: 0,
                left: 0,
                right: 0,
                bottom: 0,
                backgroundColor: 'rgba(15, 23, 42, 0.45)',
                backdropFilter: 'blur(8px)',
                WebkitBackdropFilter: 'blur(8px)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                zIndex: 99999,
                padding: '1rem',
                animation: 'fadeIn 0.2s ease-out'
            }}
            onClick={onClose}
        >
            <div 
                className="glass-card animate-fade-in"
                style={{
                    width: '100%',
                    maxWidth: maxWidth,
                    maxHeight: '85vh',
                    display: 'flex',
                    flexDirection: 'column',
                    padding: '1.5rem',
                    borderRadius: '16px',
                    border: '1px solid var(--color-border)',
                    boxShadow: '0 20px 45px -8px rgba(15, 23, 42, 0.18), 0 0 0 1px var(--color-border)',
                    backgroundColor: '#ffffff',
                    color: 'var(--color-text-main)',
                    position: 'relative'
                }}
                onClick={(e) => e.stopPropagation()}
            >
                {/* Header */}
                {(title || icon) && (
                    <div style={{
                        display: 'flex',
                        justifyContent: 'space-between',
                        alignItems: 'center',
                        marginBottom: '1.25rem',
                        borderBottom: '1px solid var(--color-border)',
                        paddingBottom: '0.85rem'
                    }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '0.65rem' }}>
                            {icon}
                            {typeof title === 'string' ? (
                                <h3 style={{ fontSize: '1.15rem', fontWeight: 700, margin: 0, color: 'var(--color-text-main)' }}>
                                    {title}
                                </h3>
                            ) : (
                                title
                            )}
                        </div>
                        <button 
                            onClick={onClose}
                            style={{
                                background: 'none',
                                border: 'none',
                                color: 'var(--color-text-muted)',
                                cursor: 'pointer',
                                padding: '6px',
                                display: 'flex',
                                alignItems: 'center',
                                justifyContent: 'center',
                                borderRadius: '6px',
                                transition: 'color 0.15s, background-color 0.15s'
                            }}
                            onMouseEnter={(e) => {
                                e.currentTarget.style.color = 'var(--color-text-main)';
                                e.currentTarget.style.backgroundColor = 'rgba(255, 255, 255, 0.08)';
                            }}
                            onMouseLeave={(e) => {
                                e.currentTarget.style.color = 'var(--color-text-muted)';
                                e.currentTarget.style.backgroundColor = 'transparent';
                            }}
                            title="Close"
                        >
                            <X size={18} />
                        </button>
                    </div>
                )}

                {/* Body */}
                <div style={{ flex: 1, overflowY: 'auto', display: 'flex', flexDirection: 'column' }}>
                    {children}
                </div>

                {/* Footer */}
                {footer && (
                    <div style={{
                        display: 'flex',
                        justifyContent: 'flex-end',
                        gap: '0.75rem',
                        marginTop: '1.25rem',
                        paddingTop: '0.85rem',
                        borderTop: '1px solid var(--color-border)'
                    }}>
                        {footer}
                    </div>
                )}
            </div>
        </div>
    );

    return createPortal(modalContent, document.body);
};
