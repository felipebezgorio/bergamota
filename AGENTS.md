# Bergamota: guia para IAs

Leia isto antes de mexer no projeto. Explica o que o código não mostra: arquitetura, regras, armadilhas já descobertas e decisões do dono.

## O que é

App para Ubuntu que imita a ideia do Tangerine (macOS, tangerineformac.com): enquanto o usuário arrasta um arquivo, tocar uma tecla abre uma roda radial sob o cursor; soltar numa fatia converte o arquivo ou abre uma ferramenta. Tudo é local.

O dono fala português do Brasil. Toda a interface, os comentários e as mensagens são em pt-BR.

## Mapa de arquivos

| Arquivo | Papel |
|---|---|
| `bergamota.py` | O app inteiro: um arquivo só, cerca de 6.400 linhas, GTK4 + libadwaita. Instalado como `~/.local/bin/bergamota` |
| `bergamota@local/extension.js` | Extensão do GNOME Shell: detecta arrasto e teclas, tira a captura do fundo, posiciona a roda |
| `bergamota@local/prefs.js` | Botão de configurações do gerenciador de extensões; só chama a ação `settings` do app |
| `bergamota@local/metadata.json` | Metadados da extensão, GNOME 45 a 50 |
| `io.github.bergamota.service` | Serviço DBus; `install.sh` troca `@HOME@` |
| `install.sh` | Script único: dependências, arquivos, liga a extensão, roda o selftest |
| `make_icon.py` | Gera `icon.png` a partir de `icon-source.jpg` |
| `docs/` | Capturas usadas no README |

## Como as peças conversam

```
GNOME Shell (extension.js)                     App (bergamota.py, serviço em segundo plano)
dnd-enter inicia polling de 50 ms
toque de tecla válido
  Shell.Screenshot da área sob a roda   --->   $XDG_RUNTIME_DIR/bergamota-bg.png
  DBus org.gtk.Actions.Activate "show"  --->   Wheel.open(modo): lê e desfoca a captura
sinal 'shown': move a janela para o cursor     recebe o drop GTK e roda a opção
dnd-leave:
  botão ainda apertado (Esc) "hide"     --->   fecha na hora
  botão solto "cancel"                  --->   espera 200 ms pelo drop e fecha
```

Ações GApplication do app: `show(s)`, `cancel`, `hide`, `settings`.

Por que a extensão existe: no Wayland, um app não sabe de arrastos dos outros, não lê teclas globais e não se posiciona sozinho. A janela da roda se chama `Bergamota Wheel`; a extensão a identifica por esse título.

Regra do toque em `_begin` de `extension.js`: só abre se a tecla foi pressionada sozinha, depois de o arrasto começar, por menos de 600 ms, e sem diálogo modal aberto (`Main.modalCount`). Isso evita Alt+Tab e Ctrl+clique. A mesma tecla de novo fecha; outra tecla troca o modo.

## Estrutura de `bergamota.py`

Funções puras vêm no topo (sem GTK), depois a UI. Os imports de PIL, numpy e cairo são feitos dentro das funções, para o serviço abrir rápido.

- **Conversão e comandos:** `out_path`, `unique_path`, `run`, `pil`, `ffmpeg`, `office`, `pdf_images`, `options(src, many)`.
- **`options()` é o registro central:** devolve `[(seção, rótulo, função)]` por tipo de arquivo. A seção é `Converter` ou `Ferramentas`.
- **Roda:** `MODES`, `items_for`, `draw_wheel`, `sector_path`, `inner_outline`, `Wheel`.
- **Janela simples:** `Win`, com o menu de quem solta o arquivo nela.

Bases reutilizáveis:

| Classe | Dá |
|---|---|
| `PhotoTool` | Prévia em thread e `save_copy`, `save_ffmpeg`, `_saved`. Espera `self.toast` e `self.save_btn` |
| `VideoPlayer` | Player GStreamer. A classe define `on_frame`, `on_position`, `play_range`, `play_btn` |
| `AudioPreview` | Toca arquivo; `render_preview` gera antes numa thread |
| `FileOrder` | Lista numerada com ↑ ↓ (Unir vídeos, Unir PDFs) |
| `tool_header()` | Cabeçalho comum: ↶ Redefinir, título, botão salvar |
| `Trimmer` | `AudioTrimmer` herda e troca só `paint_track` |

Janelas de ferramenta: `Editor`, `Cropper`, `VideoCropper`, `Collage`, `Background`, `Annotator`, `Trimmer`, `Redactor`, `SpeedChanger`, `Snapshots`, `Normalizer`, `Visualizer`, `AudioTrimmer`, `Channels`, `Bleeper`, `VideoJoin`, `PdfMerge`, `Organizer`, `Compressor`, `Metadata`, `Splitter`, `Settings`, `CommandEditor`.

Cada ferramenta tem `open_x()` chamado por `GLib.idle_add`, porque `options()` roda em thread. Ferramenta que recebe a lista inteira de arquivos é marcada com `multi()`. Ferramentas que só aparecem com vários arquivos dependem de `same_kind()`.

### Adicionar uma ferramenta

1. Escreva a parte pura no topo: função que monta o comando ou faz o trabalho.
2. Teste em `selftest()` com arquivo real gerado na hora.
3. Crie a janela, normalmente `class X(PhotoTool, Adw.ApplicationWindow)`.
4. Registre em `options()` com `tool("Nome", lambda s: GLib.idle_add(open_x, s) and None)`.
5. Ponha o ícone em `MENU_ICONS` e desenhe em `tool_icon()`.
6. Dependência nova entra em `install.sh` e no README.

## Regras do projeto

- **Nunca alterar nem sobrescrever o original.** Toda saída passa por `out_path` ou `unique_path`.
- **Tudo local.** Nada de rede.
- **Janelas pequenas.** O dono pediu 50% de área: largura padrão entre 400 e 540. Texto longo não pode cortar o título do cabeçalho.
- **Notificações só para resultado de conversão e erro.** O dono reclamou do excesso.
- **Ícones próprios desenhados em cairo**, em 20×20, sempre que possível.
- **Cor de destaque do sistema**, com laranja como reserva (`FALLBACK_ACCENT`); respeitar tema claro e escuro.
- **Código simples.** Sem dependência nova quando dá para resolver com o que existe. Atalhos conscientes levam comentário `ponytail:`.
- **Nome de arquivo nunca entra cru em shell.** `fill_command` usa `shlex.quote`; o resto usa lista de argumentos.
- **TAR extraído com `filter="data"`** para recusar caminhos que saem da pasta.

## Testes

```bash
python3 bergamota.py --selftest
```

Roda sem janela, com arquivos reais gerados por ffmpeg, Pillow e exiftool. Imprime `ok`. Toda lógica nova entra aqui.

Para janelas, renderize em PNG num script à parte:

```python
pt = Gtk.WidgetPaintable.new(w); sn = Gtk.Snapshot(); pt.snapshot(sn, W, H)
node = sn.to_node()   # pode vir None: tente de novo num timeout
w.get_renderer().render_texture(node, None).save_to_png(path)
```

Use `application_id` próprio e `NON_UNIQUE`. Chame `b.enable_pixbuf_formats()` à mão, pois ele roda em `do_startup`. Ao testar `Settings`, troque `load_keys`/`save_keys` e `load_commands`/`save_commands` para um arquivo temporário: não toque na configuração real do usuário.

**O arrasto real pela roda não dá para automatizar.** Diga isso ao relatar; só o dono testa.

## Armadilhas já descobertas

**GNOME e Wayland**
- Extensão alterada só carrega após sair e entrar na sessão. Compare `ps -o lstart= -p $(pgrep -x gnome-shell)` com a data do `extension.js` instalado antes de concluir que uma correção falhou. O botão de configurações também só aparece após novo login.
- App alterado vale após matar o serviço. Mate por PID (`pgrep -af "gapplication-servic[e]"`), nunca com `pkill -f`.
- Mexer em `MetaWindow` já desgerenciada derruba o GNOME Shell: `_place` em `extension.js` se protege com o sinal `unmanaging`.
- `Shell.BlurEffect` só desfoca retângulos. Daí a captura desfocada pelo app.

**Vídeo e áudio**
- O GTK daqui não tem backend de mídia; o player é `playbin` + `appsink`. O typelib GstApp não existe: use sinais `new-sample`/`new-preroll` e `emit("pull-sample")`.
- VA-API no playbin entrega o primeiro quadro verde. `setup_player` zera o rank dos plugins `va` e `vaapi`.
- `loudnorm` sobe a taxa para 192 kHz: use sempre `-ar` com a taxa original. A normalização é em duas passadas.
- `volume` com `enable=` vale por bloco de áudio: `bleep_cmd` usa `asetnsamples=n=64` para precisão de borda.
- `showwaves` com alfa escurece a cor: use `draw=full` e `format=rgb24` antes do `pad`.
- `-map 0:v?` com saída WAV ou Ogg falha quando o áudio tem capa: use `cover_args`.

**Imagens**
- `convert("RGB")` deixa a transparência preta: use `flatten`.
- O Pillow daqui não tem AVIF, HEIC nem SVG. `enable_pixbuf_formats` registra os três via GdkPixbuf e todas as ferramentas herdam. Gravar AVIF vem do glycin; ffmpeg com libaom falhou com transparência e o arquivo parcial derrubou o ffmpeg.
- Gravar HEIC exigiria `libheif-plugin-x265`, não testado.
- `Gtk.Picture` pede o tamanho natural da imagem e estica o layout: passe miniatura pronta, do tamanho certo.

**PDF**
- Não há biblioteca de PDF. `organize_pdf`: `pdfseparate`, `gs` só nas páginas alteradas, `pdfunite`. Uma chamada `gs` para várias páginas não serve: `-dFIXEDMEDIA` trava o tamanho.
- A edição do exiftool em PDF é reversível, como ele mesmo avisa. `scrub_pdf` regrava com `gs`.

**Metadados:** o exiftool não grava MP3, WAV, FLAC, Ogg, MKV nem WebM. Esses vão por ffmpeg com `-c copy` (`meta_route`). Etiquetas de Ogg e Opus ficam na faixa: use também `-metadata:s:a:0`.

**LibreOffice no snap:** não enxerga `/tmp`. `office()` usa pasta temporária ao lado do arquivo.

**ptyxis:** ignora `--working-directory` sem `--new-window`. Use `terminal_argv`.

**Shell neste ambiente:** comandos passam pelo proxy `rtk`, que altera a saída de `grep` e `apt-cache`. Na dúvida, confira pelo Python.

## Decisões do dono

- Tecla Alt para Ações: ele testou outra e pediu para voltar.
- **Remover fundo: cancelado.** Não faça sem pedido.
- **Ocultar em fotos: adiado.** Não faça sem pedido.
- **Tecla livre (capturar qualquer tecla): descartada por ora.** Se voltar: durante arrasto de arquivo o Mutter processa atalhos antes do arrasto, então `global.display.grab_accelerator` só durante o arrasto (junto de `Main.wm.allowKeybinding` e do sinal `accelerator-activated`) deve funcionar, sem atrapalhar a digitação. Isso veio da leitura de `events.c`, não foi testado. O polling atual só vê modificadores. Proibir teclas com função própria (Esc, Delete e afins).
- Comandos personalizados sem parâmetro rodam como escritos: o caminho não é acrescentado.
- Comando personalizado na roda: só contorno interno, nunca preenchido; ao passar o mouse o contorno fica mais grosso.
- "Mostrar na pasta" foi removida.
- Copiar e Mover usam janela de escolher pasta.

## O que falta, comparando com o Tangerine

O site não detalha as 26 ferramentas. Hoje são 20 feitas; duas ficaram de fora por decisão do dono. O dono manda capturas uma a uma: siga esse fluxo.

- PDF para PPTX, XLSX, EPUB e Markdown
- Documentos: Markdown e EPUB
- Planilhas: TSV
- Criar TAR e GZIP; extrair RAR
- Menu só com teclado: Enter no arquivo, setas, Enter, Esc
- Tela de toque
- Ação Float
- Envio de arquivo no lugar do AirDrop
- Gravar HEIC
- Dividir vídeo e áudio: o site diz "split" sem detalhar

## Como trabalhar aqui

- Responda em pt-BR.
- Rode o selftest antes de dizer que terminou.
- Diga o que não foi testado, principalmente arrasto real.
- Avise quando a mudança exigir novo login.
- Instalar: `./install.sh --sem-dependencias`.
