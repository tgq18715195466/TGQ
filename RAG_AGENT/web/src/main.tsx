import { createRoot } from 'react-dom/client';
import { HashRouter } from 'react-router-dom';
import { WorkspaceProvider } from './workspace';
import App from './App';
import '../styles.css';

createRoot(document.getElementById('root')!).render(
  <HashRouter><WorkspaceProvider><App /></WorkspaceProvider></HashRouter>,
);
