import { Session } from './api';

export function formatTime(value: string): string {
  const date = new Date(value);
  return `${date.getHours().toString().padStart(2, '0')}:${date
    .getMinutes()
    .toString()
    .padStart(2, '0')}`;
}

export function sessionTitle(session: Session): string {
  const text = session.title.trim();
  if (text.length === 0 || text.toLowerCase() === 'new task') {
    return 'New session';
  }
  return text;
}
