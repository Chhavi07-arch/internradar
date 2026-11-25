import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import './styles.css';

function App() {
  return (
    <main>
      <header>
        <p className="eyebrow">Public scaffold</p>
        <h1>InternRadar v2</h1>
        <p>This preview contains no listings or personal data.</p>
      </header>
      <section aria-labelledby="availability">
        <h2 id="availability">Not available yet</h2>
        <dl>
          <dt>Internship discovery</dt>
          <dd>No internship listings are loaded in this preview.</dd>
          <dt>Profiles and saved internships</dt>
          <dd>Sign-in, profiles, and saving are not enabled.</dd>
        </dl>
      </section>
    </main>
  );
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
