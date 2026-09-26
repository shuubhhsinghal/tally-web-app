'use client';

import React, { createContext, useContext, useState, useEffect } from 'react';

const UIContext = createContext();

export const useUI = () => useContext(UIContext);

export const UIProvider = ({ children }) => {
  // Toasts
  const [toast, setToast] = useState(null); // { message, type: 'success' | 'error' | 'info' }

  // Action Sheet (New Entry menu, Ledger Picker, etc)
  const [actionSheet, setActionSheet] = useState(null); // { title, options: [{ label, icon, onClick }] }

  // Confirm Dialog
  const [confirmDialog, setConfirmDialog] = useState(null); // { title, message, confirmText, cancelText, onConfirm }

  useEffect(() => {
    if (toast) {
      const timer = setTimeout(() => {
        setToast(null);
      }, 3000);
      return () => clearTimeout(timer);
    }
  }, [toast]);

  // Lock background scroll while a full-screen overlay is open -- otherwise
  // a touch drag on the action sheet/dialog (or its backdrop) scrolls the
  // page underneath instead of staying contained to the overlay.
  useEffect(() => {
    if (actionSheet || confirmDialog) {
      const original = document.body.style.overflow;
      document.body.style.overflow = 'hidden';
      return () => { document.body.style.overflow = original; };
    }
  }, [actionSheet, confirmDialog]);

  const showToast = (message, type = 'success') => setToast({ message, type });

  const showActionSheet = (config) => setActionSheet(config);
  const hideActionSheet = () => setActionSheet(null);

  const showConfirm = (config) => setConfirmDialog(config);
  const hideConfirm = () => setConfirmDialog(null);

  // Alias for backward compatibility - both names work
  const showConfirmDialog = showConfirm;

  return (
    <UIContext.Provider value={{
      showToast,
      showActionSheet, hideActionSheet,
      showConfirm, hideConfirm,
      showConfirmDialog
    }}>
      {children}

      {/* Global Toast */}
      {toast && (
        <div className="fixed top-4 left-1/2 -translate-x-1/2 z-50 animate-in fade-in slide-in-from-top-4">
          <div className={`px-4 py-3 rounded-md shadow-lg border text-sm font-medium flex items-center gap-2 bg-surface
            ${toast.type === 'error' ? 'border-accent text-accent-700' : ''}
            ${toast.type === 'success' ? 'border-divider text-text' : ''}
            ${toast.type === 'info' ? 'border-divider text-text' : ''}
          `}>
            {toast.type === 'success' && <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" /></svg>}
            {toast.type === 'error' && <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" /></svg>}
            {toast.message}
          </div>
        </div>
      )}

      {/* Global Action Sheet */}
      {actionSheet && (
        <>
          <div className="fixed inset-0 bg-black/40 z-40 transition-opacity" onClick={hideActionSheet} />
          <div className="fixed bottom-0 left-0 right-0 z-50 bg-surface border-t border-divider rounded-t-lg shadow-lg pb-safe animate-in slide-in-from-bottom-full">
            {actionSheet.title && (
              <div className="px-6 py-4 border-b border-divider">
                <h3 className="text-sm font-semibold text-neutral-600 uppercase tracking-wider">{actionSheet.title}</h3>
              </div>
            )}
            <div className="p-2">
              {actionSheet.options.map((opt, i) => (
                <button
                  key={i}
                  onClick={() => { opt.onClick(); hideActionSheet(); }}
                  className="w-full flex items-center gap-4 px-4 py-4 hover:bg-text/5 rounded-md transition-colors text-left"
                >
                  {opt.icon && (
                    <div className={`flex-shrink-0 w-10 h-10 rounded-full flex items-center justify-center ${opt.colorClass || 'border border-divider text-neutral-600'}`}>
                      {opt.icon}
                    </div>
                  )}
                  <span className="text-base font-medium text-text">{opt.label}</span>
                  <svg className="w-5 h-5 ml-auto text-neutral-500" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" /></svg>
                </button>
              ))}
              <div className="h-px bg-divider my-2 mx-2" />
              <button
                onClick={hideActionSheet}
                className="w-full py-4 text-center text-base font-semibold text-text hover:bg-text/5 rounded-md transition-colors"
              >
                Cancel
              </button>
            </div>
          </div>
        </>
      )}

      {/* Global Confirm Dialog */}
      {confirmDialog && (
        <>
          <div className="fixed inset-0 bg-black/40 z-40 transition-opacity" onClick={hideConfirm} />
          <div className="fixed inset-0 z-50 flex items-center justify-center p-4 pointer-events-none">
            <div className="bg-surface border border-divider rounded-lg shadow-lg w-full max-w-sm p-6 pointer-events-auto animate-in zoom-in-95">
              <h3 className="font-heading font-semibold text-xl mb-2">{confirmDialog.title}</h3>
              {confirmDialog.message && <p className="text-neutral-700 mb-6">{confirmDialog.message}</p>}

              <div className="flex gap-3">
                <button
                  onClick={hideConfirm}
                  className="flex-1 py-3 px-4 border border-divider text-text font-semibold rounded-md hover:bg-text/5 transition-colors"
                >
                  {confirmDialog.cancelText || 'Cancel'}
                </button>
                <button
                  onClick={() => { confirmDialog.onConfirm(); hideConfirm(); }}
                  className={`flex-1 py-3 px-4 font-semibold rounded-md border transition-colors ${confirmDialog.danger ? 'bg-red-600 hover:bg-red-700 border-red-600 text-white' : 'border-accent text-accent hover:bg-accent/12'}`}
                >
                  {confirmDialog.confirmText || 'Confirm'}
                </button>
              </div>
            </div>
          </div>
        </>
      )}
    </UIContext.Provider>
  );
};
