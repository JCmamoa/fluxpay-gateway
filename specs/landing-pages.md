# Especificação Técnica - 4 Landing Pages por Segmento

| Campo | Valor |
| --- | --- |
| Versão | 1.0 |
| Status | Aprovada pelo usuário (autônomo) |
| Autor | @pm |
| Data | 02/11/2026 |

## 1. Contexto e problema

Criar 4 landing pages institucionais para segmentos distintos, inspiradas nos modelos do repositório
https://github.com/topics/landingpages?l=html (em especial PaulleDemon/awesome-landing-pages), porém com
layouts e paletas de cores próprias, sem repetição entre si. Todas devem suportar tema claro e escuro
(toggle persistido em localStorage, default seguindo prefers-color-scheme) e exibir links de redes sociais.

## 2. Segmentos

1. **Escritório de advocacia** (`app_build/landing/advocacia/index.html`)
2. **Clínica de estética** (`app_build/landing/estetica/index.html`)
3. **Consultório dentário** (`app_build/landing/dentario/index.html`)
4. **Oficina mecânica de carros** (`app_build/landing/mecanica/index.html`)

## 3. Requisitos funcionais

- **RF-01** Cada página é um arquivo HTML autocontido (CSS e JS embutidos), responsivo (mobile-first).
- **RF-02** Cada página possui toggle de tema claro/escuro no header, persistido em localStorage e
  respeitando prefers-color-scheme como default.
- **RF-03** Cada página exibe links de redes sociais (Facebook, Instagram, WhatsApp, Twitter/X, LinkedIn,
  YouTube quando aplicável) no header e/ou footer.
- **RF-04** Nenhuma página repete layout nem paleta de cores das demais.
- **RF-05** Conteúdo em português (pt-BR), com SEO básico (title, meta description, Open Graph).
- **RF-06** Seções mínimas: Hero, Sobre/Serviços, Diferenciais/Benefícios, Depoimentos (quando fizer
  sentido), Contato/Agendamento (formulário) e Footer.
- **RF-07** Formulários de contato/agendamento com validação HTML5 e feedback visual (sem backend, apenas
  simulação de envio).
- **RF-08** Imagens via URLs públicas (Unsplash/Pexels) ou SVG inline; sem dependências de build.

## 4. Requisitos não funcionais

- **RNF-01** Sem CDN obrigatório para funcionamento básico (ícones via SVG inline; fontes via Google Fonts
  opcional com fallback).
- **RNF-02** Acessibilidade: landmarks semânticos, alt em imagens, foco visível, contraste adequado nos
  dois temas.
- **RNF-03** Performance: página única, sem frameworks pesados; CSS/JS mínimos.
- **RNF-04** Compatibilidade com navegadores modernos (Chrome, Edge, Firefox, Safari).

## 5. Diretrizes de design por segmento

- **Advocacia**: navy profundo + dourado; layout clássico com hero dividido, áreas de atuação em grid,
  casos de sucesso, depoimentos em carrossel e formulário de consulta.
- **Estética**: blush/rose + sage; layout editorial com hero full-bleed, cards de tratamentos com preços,
  galeria antes/depois, equipe e agendamento.
- **Dentário**: teal/mint + branco clínico; layout limpo com hero, serviços em ícones, equipe de
  dentistas, planos/convênios e agendamento online.
- **Mecânica**: carvão + laranja industrial; layout robusto com hero com imagem de carro, serviços em
  cards com preços, tabela de manutenção, depoimentos e agendamento.

## 6. Critérios de aceitação

- **CA-01** 4 arquivos HTML existem nos caminhos indicados e abrem sem erros no navegador.
- **CA-02** Toggle claro/escuro funciona e persiste entre reloads.
- **CA-03** Links de redes sociais presentes e com target="_blank" rel="noopener noreferrer".
- **CA-04** Layouts e paletas visivelmente distintos entre si.
- **CA-05** Responsividade verificada em larguras 360px, 768px e 1280px.

## 7. Fora de escopo

- Backend, persistência de formulários, CMS, multi-página, e-commerce.
