import { useState } from 'react';
import { Github, Mail, QrCode } from 'lucide-react';
import { siteConfig } from '../siteConfig';
import { RednoteModal } from './RednoteModal';

type SiteFooterProps = {
  variant?: 'full' | 'compact';
};

export function SiteFooter({ variant = 'full' }: SiteFooterProps) {
  const [rednoteOpen, setRednoteOpen] = useState(false);

  return (
    <>
      <footer className={`site-footer${variant === 'compact' ? ' compact' : ''}`}>
        {variant === 'full' ? (
          <div className="site-footer-grid">
            <div className="site-footer-brand-block">
              <p className="site-footer-title">pocage</p>
              <p className="site-footer-copy">
                Continue sessions from any browser while commands, files, and runtime context stay on your daemon machine.
              </p>
            </div>

            <div className="site-footer-column">
              <p className="site-footer-heading">Product</p>
              <a className="site-footer-link" href={siteConfig.webAppUrl} target="_blank" rel="noreferrer">
                Web APP
              </a>
              {siteConfig.iosAppUrl ? (
                <a className="site-footer-link" href={siteConfig.iosAppUrl}>
                  iOS APP
                </a>
              ) : null}
            </div>

            <div className="site-footer-column">
              <p className="site-footer-heading">About</p>
              <a className="site-footer-link" href={siteConfig.licenseUrl} target="_blank" rel="noreferrer">
                License
              </a>
              <a className="site-footer-link" href={`mailto:${siteConfig.contactEmail}`}>
                Contact
              </a>
            </div>
          </div>
        ) : (
          <div className="site-footer-compact-row">
            <a className="site-footer-title-link" href="/">
              pocage
            </a>
            <div className="site-footer-compact-links">
              <a className="site-footer-link" href="/">
                Home
              </a>
              <a className="site-footer-link" href={siteConfig.githubUrl} target="_blank" rel="noreferrer">
                GitHub
              </a>
              <a className="site-footer-link" href={siteConfig.licenseUrl} target="_blank" rel="noreferrer">
                License
              </a>
              <a className="site-footer-link" href={`mailto:${siteConfig.contactEmail}`}>
                Contact
              </a>
            </div>
          </div>
        )}

        <div className="site-footer-bottom">
          <p className="site-footer-meta">© 2026 pocage. All rights reserved.</p>

          <div className="site-footer-social">
            <a
              className="site-footer-social-link"
              href={siteConfig.githubUrl}
              target="_blank"
              rel="noreferrer"
              aria-label="Open pocage GitHub repository"
              title="GitHub"
            >
              <Github size={18} strokeWidth={1.9} aria-hidden="true" />
            </a>
            <button
              className="site-footer-social-link"
              type="button"
              onClick={() => setRednoteOpen(true)}
              aria-label="Open Rednote QR code"
              title="Rednote"
            >
              <QrCode size={18} strokeWidth={1.9} aria-hidden="true" />
            </button>
            <a
              className="site-footer-social-link"
              href={`mailto:${siteConfig.contactEmail}`}
              aria-label="Email pocage"
              title="Email"
            >
              <Mail size={18} strokeWidth={1.9} aria-hidden="true" />
            </a>
          </div>
        </div>
      </footer>

      <RednoteModal open={rednoteOpen} onClose={() => setRednoteOpen(false)} />
    </>
  );
}
