import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import '@copilot/core-ui/styles.css';
import './iic.css';
import './i18n';
import App from './App.tsx';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
