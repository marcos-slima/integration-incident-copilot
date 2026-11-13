// DA-73 + DA-95: catalogo do produto IIC. O mecanismo (deteccao, cookie,
// pseudo-locale) e os textos comuns (`core.*`) vem de @copilot/core-ui.
import { initI18n } from '@copilot/core-ui';
import en from './locales/en.json';
import ptBR from './locales/pt-BR.json';

export default initI18n({ 'pt-BR': ptBR, en });
