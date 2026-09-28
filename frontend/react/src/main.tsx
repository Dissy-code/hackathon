import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
/* Расширение указано намеренно: рядом лежит configurator.js статической
   версии, а файловая система macOS регистр не различает — без .tsx
   сборщик подхватил бы именно его. */
import Configurator from '../../Configurator.tsx';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Configurator />
  </StrictMode>,
);
