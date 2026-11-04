# Especificação Técnica - Apps Mobile (PWA) das Landing Pages

| Campo | Valor |
| --- | --- |
| Versão | 1.0 |
| Status | Aprovada pelo usuário (autônomo) |
| Autor | @pm |
| Base | `specs/landing-pages.md` |
| Data | 02/11/2026 |

## 1. Contexto e problema

Para cada uma das 4 landing pages já criadas, adicionar uma "app" (PWA) que seja cópia fiel da página,
com ajustes de tamanho/layout para telas menores (mobile-first), instalável e com experiência de app.

## 2. Entregáveis

Para cada segmento em `app_build/landing/<segmento>/`:
- `app/index.html` — cópia fiel da landing, otimizada para telas pequenas.
- `app/manifest.json` — Web App Manifest (nome, short_name, icons, theme_color, background_color, display: standalone, start_url).
- `app/sw.js` — Service Worker com cache básico (cache-first para assets, network-first para HTML) e fallback offline.
- `app/icons/icon.svg` — ícone adaptável.

Segmentos: advocacia, estetica, dentario, mecanica.

## 3. Requisitos funcionais

- **RF-01** O conteúdo da app é cópia fiel da landing page correspondente (mesmos textos, seções, cores, tema claro/escuro).
- **RF-02** Ajustes para telas menores: layout single-column, tipografia reduzida, touch targets ≥ 44px, safe-area-inset, bottom navigation bar com atalhos (Início, Serviços, Contato, WhatsApp), viewport-fit=cover.
- **RF-03** PWA instalável: manifest + service worker + meta tags (theme-color, apple-mobile-web-app-capable, apple-mobile-web-app-status-bar-style).
- **RF-04** Tema claro/escuro mantido (toggle + localStorage + prefers-color-scheme).
- **RF-05** Links de redes sociais mantidos.
- **RF-06** Formulários mantidos com validação e feedback simulado.
- **RF-07** Funciona offline para assets em cache (fallback simples).

## 4. Requisitos não funcionais

- **RNF-01** Sem dependências de build; arquivos estáticos.
- **RNF-02** Acessibilidade mobile: foco visível, contraste, aria-labels.
- **RNF-03** Performance: CSS/JS mínimos, imagens com loading lazy.

## 5. Critérios de aceitação

- **CA-01** 4 pastas `app/` existem com index.html, manifest.json, sw.js, icons/icon.svg.
- **CA-02** Manifest válido (JSON) e referenciado no HTML.
- **CA-03** Service worker registrado sem erros no console.
- **CA-04** Layout mobile (≤ 480px) sem overflow horizontal.
- **CA-05** Conteúdo idêntico à landing correspondente.

## 6. Fora de escopo

- Publicação em lojas (App Store/Play Store), backend, push notifications.
