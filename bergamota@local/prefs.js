// Botão de configurações do app Extensões: abre os Ajustes do próprio Bergamota, que já têm tudo.
import Adw from 'gi://Adw';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import {ExtensionPreferences} from 'resource:///org/gnome/Shell/Extensions/js/extensions/prefs.js';

export default class BergamotaPreferences extends ExtensionPreferences {
    fillPreferencesWindow(window) {
        const page = new Adw.PreferencesPage();
        const group = new Adw.PreferencesGroup({
            title: 'Bergamota',
            description: 'Abrindo os Ajustes do Bergamota…',
        });
        page.add(group);
        window.add(page);

        Gio.DBus.session.call('io.github.bergamota', '/io/github/bergamota', 'org.gtk.Actions', 'Activate',
            new GLib.Variant('(sava{sv})', ['settings', [], {}]), null, Gio.DBusCallFlags.NONE, -1, null,
            (bus, res) => {
                try {
                    bus.call_finish(res);
                    window.close();
                } catch (e) {
                    group.description = `Não deu para abrir os Ajustes: ${e.message}`;
                }
            });
    }
}
