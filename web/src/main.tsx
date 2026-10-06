import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import { initializeLanguage } from './i18n';
import './styles.css';
import './comparison.css';
import './run-status.css';

initializeLanguage();
createRoot(document.getElementById('root')!).render(<StrictMode><App /></StrictMode>);
