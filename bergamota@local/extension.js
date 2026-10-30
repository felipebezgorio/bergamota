// Detecta arrastos de arquivo e abre a roda do Bergamota sob o cursor ao tocar um modificador.
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const MASKS = {
    shift: Clutter.ModifierType.SHIFT_MASK,
    ctrl: Clutter.ModifierType.CONTROL_MASK,
    alt: Clutter.ModifierType.MOD1_MASK,
    super: Clutter.ModifierType.MOD4_MASK | Clutter.ModifierType.SUPER_MASK,
};
// parseKeys: início (a mesma regra de load_keys no app)
const DEFAULT_KEYS = {convert: 'shift', tools: 'ctrl', actions: 'alt'};

function parseKeys(text, names) {
    try {
        const keys = JSON.parse(text);
        const chosen = Object.keys(DEFAULT_KEYS).map(mode => keys[mode]);
        if (chosen.every(k => names.includes(k)) && new Set(chosen).size === chosen.length)
            return keys;
    } catch {
        // arquivo ausente ou inválido: fica o padrão
    }
    return DEFAULT_KEYS;
}
// parseKeys: fim
const SIZE = 300;
const TAP_US = 600000; // segurou mais que isso: era atalho, não toque

Gio._promisify(Shell.Screenshot.prototype, 'screenshot_area');

export default class BergamotaExtension extends Extension {
    enable() {
        this._dnd = global.backend.get_dnd();
        this._dndIds = [
            this._dnd.connect('dnd-enter', () => this._begin()),
            this._dnd.connect('dnd-leave', () => this._end()),
        ];
        this._winId = global.display.connect('window-created', (_d, w) => this._place(w));
    }

    disable() {
        this._end();
        this._dndIds.forEach(id => this._dnd.disconnect(id));
        global.display.disconnect(this._winId);
        this._dnd = this._dndIds = this._winId = null;
    }

    _begin() {
        this._mode = null;
        this._tap = null;
        this._keys = this._loadKeys();
        // Tecla que já estava segurada quando o arrasto começou (Ctrl+clique, Ctrl+arrastar para copiar)
        // não conta como toque: só vale depois de soltar e tocar de novo.
        this._stale = global.get_pointer()[2];
        // ponytail: polling de 50ms durante o arrasto; o shell não emite sinal de tecla no meio de um DnD
        this._poll = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 50, () => {
            const [x, y, mods] = global.get_pointer();
            this._stale &= mods;
            const held = this._keys.filter(([mask]) => mods & mask);
            if (!this._tap && held.length === 1 && !(this._stale & held[0][0]))
                this._tap = {mask: held[0][0], mode: held[0][1], t: GLib.get_monotonic_time(), ok: true};
            if (this._tap) {
                // Só um toque rápido e sozinho abre a roda: Alt+Tab, Ctrl+C etc. seguram a tecla junto
                // com outra ou abrem um diálogo modal (o seletor de janelas), e são ignorados.
                if (held.length > 1 || Main.modalCount > 0)
                    this._tap.ok = false;
                if (!(mods & this._tap.mask)) {
                    const quick = GLib.get_monotonic_time() - this._tap.t < TAP_US;
                    if (this._tap.ok && quick && this._tap.mode === this._mode) {
                        this._mode = null; // mesma tecla de novo: fecha; o próximo toque reabre
                        this._call('hide', null);
                    } else if (this._tap.ok && quick) {
                        const first = !this._mode;
                        this._mode = this._tap.mode;
                        if (first) {
                            [this._x, this._y] = [x, y];
                            this._shoot(x, y).finally(() => this._show());
                        } else {
                            this._show();
                        }
                    }
                    this._tap = null;
                }
            }
            return GLib.SOURCE_CONTINUE;
        });
    }

    _loadKeys() {
        // Lido a cada arrasto: trocar as teclas nos Ajustes vale sem sair da sessão
        let text = '';
        try {
            const path = GLib.build_filenamev([GLib.get_user_config_dir(), 'bergamota', 'keys.json']);
            text = new TextDecoder().decode(GLib.file_get_contents(path)[1]);
        } catch {
            // sem arquivo: padrão
        }
        const keys = parseKeys(text, Object.keys(MASKS));
        return Object.keys(DEFAULT_KEYS).map(mode => [MASKS[keys[mode]], mode]);
    }

    _show() {
        if (this._mode) // o arrasto pode ter acabado durante a captura
            this._call('show', new GLib.Variant('s', this._mode));
    }

    async _shoot(x, y) {
        // O app não enxerga o que está atrás da janela no Wayland: a extensão captura a área sob a roda
        // e o app desfoca a imagem no disco (Shell.BlurEffect só desfoca retângulos).
        try {
            const file = Gio.File.new_for_path(GLib.build_filenamev([GLib.get_user_runtime_dir(), 'bergamota-bg.png']));
            const stream = file.replace(null, false, Gio.FileCreateFlags.NONE, null);
            await new Shell.Screenshot().screenshot_area(x - SIZE / 2, y - SIZE / 2, SIZE, SIZE, stream);
            stream.close(null);
        } catch (e) {
            console.debug(`Bergamota: sem fundo desfocado: ${e.message}`);
        }
    }

    _end() {
        if (this._poll)
            GLib.source_remove(this._poll);
        this._poll = 0;
        // Botão ainda apertado = arrasto cancelado (Esc): fecha na hora. Solto = pode ser um drop na roda,
        // que chega ao app depois deste aviso; aí o app espera um pouco antes de fechar.
        if (this._mode)
            this._call(global.get_pointer()[2] & Clutter.ModifierType.BUTTON1_MASK ? 'hide' : 'cancel', null);
        this._mode = null;
    }

    _call(action, param) {
        Gio.DBus.session.call('io.github.bergamota', '/io/github/bergamota', 'org.gtk.Actions',
            'Activate', new GLib.Variant('(sava{sv})', [action, param ? [param] : [], {}]),
            null, Gio.DBusCallFlags.NONE, -1, null, null);
    }

    _place(win) {
        // Posiciona só quando a janela é exibida (já está na pilha) e nunca depois de ela sumir:
        // mexer numa MetaWindow já desgerenciada derruba o gnome-shell.
        const appId = win.get_wm_class();
        if (appId && appId !== 'io.github.bergamota')
            return; // não prende sinais em janelas de outros apps
        const ids = [];
        const done = () => ids.splice(0).forEach(id => win.disconnect(id));
        ids.push(win.connect('unmanaging', done));
        ids.push(win.connect('shown', () => {
            done();
            if (win.get_title() !== 'Bergamota Wheel' || !win.get_compositor_private() || this._x === undefined)
                return;
            win.make_above();
            win.move_frame(true, this._x - SIZE / 2, this._y - SIZE / 2);
        }));
    }
}
