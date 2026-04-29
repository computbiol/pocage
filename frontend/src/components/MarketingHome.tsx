import { useEffect, useRef, useState } from 'react';
import { ArrowRight, Github, GlobeLock, LaptopMinimal, LockKeyhole, Menu, MonitorSmartphone, QrCode, ServerCog, SquareTerminal, X } from 'lucide-react';
import pocageLogo from '../assets/brand/pocage-logo.svg';
import { siteConfig } from '../siteConfig';
import { RednoteModal } from './RednoteModal';
import { SiteFooter } from './SiteFooter';

type MarketingHomeProps = {
  onOpenLogin: () => void;
  onOpenRegister: () => void;
};

const capabilityBlocks = [
  {
    icon: LockKeyhole,
    title: 'Privacy first',
    body: 'Your sessions stay on your machines. No remote transcript sync. State remains local.'
  },
  {
    icon: LaptopMinimal,
    title: 'Runs stay local',
    body: 'Agents keep running where they started. Control them remotely from any browser.'
  },
  {
    icon: MonitorSmartphone,
    title: 'One place for every agent',
    body: 'Manage all your sessions from one control plane. Across laptops, desktops, and servers.'
  },
  {
    icon: SquareTerminal,
    title: 'Simple to start',
    body: 'Pair a machine in two commands. Start managing it in minutes.'
  },
  {
    icon: GlobeLock,
    title: 'Encrypted transport',
    body: 'HTTPS and WSS by default. Traffic stays encrypted in transit.'
  },
  {
    icon: ServerCog,
    title: 'Hosted or self-hosted',
    body: 'Use our managed cloud or deploy your own stack.'
  }
] as const;

export function MarketingHome({ onOpenLogin, onOpenRegister }: MarketingHomeProps) {
  const [xhsOpen, setXhsOpen] = useState(false);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const homeRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    const closeOnDesktop = window.matchMedia('(min-width: 641px)');
    const handleViewportChange = (event: MediaQueryListEvent) => {
      if (event.matches) {
        setMobileNavOpen(false);
      }
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setMobileNavOpen(false);
      }
    };

    closeOnDesktop.addEventListener('change', handleViewportChange);
    window.addEventListener('keydown', handleKeyDown);

    return () => {
      closeOnDesktop.removeEventListener('change', handleViewportChange);
      window.removeEventListener('keydown', handleKeyDown);
    };
  }, []);

  return (
    <>
      <main className="marketing-home" ref={homeRef}>
        <div className="marketing-shell">
          <header className="marketing-nav">
            <div className="marketing-nav-top">
              <button
                className="marketing-brand"
                type="button"
                onClick={() => {
                  setMobileNavOpen(false);
                  homeRef.current?.scrollTo({ top: 0, behavior: 'smooth' });
                }}
              >
                <img className="marketing-brand-logo" src={pocageLogo} alt="pocage logo" />
                <span className="marketing-brand-copy">pocage</span>
              </button>

              <button
                className="marketing-nav-menu-toggle"
                type="button"
                aria-expanded={mobileNavOpen}
                aria-controls="marketing-nav-actions"
                aria-label={mobileNavOpen ? 'Close navigation menu' : 'Open navigation menu'}
                onClick={() => setMobileNavOpen((open) => !open)}
              >
                {mobileNavOpen ? <X size={18} strokeWidth={2.1} aria-hidden="true" /> : <Menu size={18} strokeWidth={2.1} aria-hidden="true" />}
              </button>
            </div>

            <div
              className={mobileNavOpen ? 'marketing-nav-actions is-open' : 'marketing-nav-actions'}
              id="marketing-nav-actions"
            >
              <button
                className="marketing-nav-link secondary"
                type="button"
                onClick={() => {
                  setMobileNavOpen(false);
                  setXhsOpen(true);
                }}
              >
                <QrCode size={16} strokeWidth={1.9} aria-hidden="true" />
                <span>Follow on rednote</span>
              </button>
              <a
                className="marketing-nav-link secondary"
                href={siteConfig.githubUrl}
                target="_blank"
                rel="noreferrer"
                onClick={() => setMobileNavOpen(false)}
              >
                <Github size={16} strokeWidth={1.9} aria-hidden="true" />
                <span>GitHub</span>
              </a>
              <button
                className="marketing-nav-link"
                type="button"
                onClick={() => {
                  setMobileNavOpen(false);
                  onOpenLogin();
                }}
              >
                Log in
              </button>
            </div>
          </header>

          <section className="marketing-hero">
            <div className="marketing-copy">
              <button className="marketing-hero-chip" type="button" onClick={onOpenRegister}>
                <span>iOS APP</span>
                <ArrowRight size={15} strokeWidth={2} aria-hidden="true" />
              </button>
              <h1>
                Your <span className="marketing-title-accent">age</span>nts, in your{' '}
                <span className="marketing-title-word">
                  <span className="marketing-title-accent">poc</span>ket
                </span>
              </h1>
              <p className="marketing-lede">
                Manage Codex sessions across all your machines from your phone or web.
              </p>

              <div className="marketing-cta-row">
                <button className="marketing-primary-button" type="button" onClick={onOpenRegister}>
                  <span>Get started</span>
                  <ArrowRight size={16} strokeWidth={2} aria-hidden="true" />
                </button>
                <a
                  className="marketing-tertiary-button"
                  href={siteConfig.githubUrl}
                  target="_blank"
                  rel="noreferrer"
                >
                  <Github size={16} strokeWidth={1.9} aria-hidden="true" />
                  <span>GitHub</span>
                </a>
              </div>
            </div>
          </section>

          <section className="marketing-capability-section" aria-label="What pocage does">
            {capabilityBlocks.map((block) => {
              const Icon = block.icon;
              return (
                <article className="marketing-capability-card" key={block.title}>
                  <div className="marketing-capability-icon">
                    <Icon size={18} strokeWidth={1.9} aria-hidden="true" />
                  </div>
                  <h2>{block.title}</h2>
                  <p>{block.body}</p>
                </article>
              );
            })}
          </section>

          <SiteFooter />
        </div>
      </main>

      <RednoteModal open={xhsOpen} onClose={() => setXhsOpen(false)} />
    </>
  );
}
