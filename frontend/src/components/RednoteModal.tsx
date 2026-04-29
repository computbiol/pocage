import { useEffect } from 'react';
import { X } from 'lucide-react';
import { siteConfig } from '../siteConfig';

type RednoteModalProps = {
  open: boolean;
  onClose: () => void;
};

export function RednoteModal({ open, onClose }: RednoteModalProps) {
  useEffect(() => {
    if (!open) {
      return undefined;
    }

    function handleKeyDown(event: KeyboardEvent): void {
      if (event.key === 'Escape') {
        onClose();
      }
    }

    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [open, onClose]);

  if (!open) {
    return null;
  }

  return (
    <div
      className="rednote-modal-backdrop"
      role="presentation"
      onClick={onClose}
    >
      <div
        className="rednote-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="rednote-modal-title"
        onClick={(event) => event.stopPropagation()}
      >
        <button className="rednote-modal-close" type="button" onClick={onClose} aria-label="Close Rednote QR modal">
          <X size={16} strokeWidth={2} aria-hidden="true" />
        </button>
        <p className="rednote-modal-eyebrow">Rednote</p>
        <h2 id="rednote-modal-title">{siteConfig.rednoteDisplayName}</h2>
        <p className="rednote-modal-copy">rednote ID: {siteConfig.rednoteId}</p>
        <img className="rednote-modal-qr" src={siteConfig.xiaohongshuQrSrc} alt={`${siteConfig.rednoteDisplayName} Rednote QR code`} />
        <p className="rednote-modal-caption">Scan the QR code to find me on Xiaohongshu.</p>
      </div>
    </div>
  );
}
