"""Linux tray icon over D-Bus (StatusNotifierItem + com.canonical.dbusmenu), using pure-Python jeepney.

This is what KDE Plasma, GNOME's "AppIndicator and KStatusNotifierItem Support" extension, Cinnamon, XFCE,
Budgie, waybar etc. actually display. pystray without PyGObject falls back to the old X11 XEmbed protocol,
which modern desktops show as an empty, unclickable square - hence this module.
"""
import os
import threading

from jeepney import DBusAddress, HeaderFields, MessageType, new_error, new_method_call, new_method_return, new_signal
from jeepney.bus_messages import message_bus
from jeepney.io.blocking import open_dbus_connection

ITEM_PATH = "/StatusNotifierItem"
MENU_PATH = "/MenuBar"
SNI = "org.kde.StatusNotifierItem"
MENU = "com.canonical.dbusmenu"
PROPS = "org.freedesktop.DBus.Properties"
INTRO = "org.freedesktop.DBus.Introspectable"

ITEM_XML = """<node><interface name="org.kde.StatusNotifierItem">
<property name="Category" type="s" access="read"/><property name="Id" type="s" access="read"/>
<property name="Title" type="s" access="read"/><property name="Status" type="s" access="read"/>
<property name="WindowId" type="i" access="read"/><property name="IconName" type="s" access="read"/>
<property name="IconPixmap" type="a(iiay)" access="read"/><property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
<property name="ItemIsMenu" type="b" access="read"/><property name="Menu" type="o" access="read"/>
<method name="Activate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
<method name="SecondaryActivate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
<method name="ContextMenu"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
<method name="Scroll"><arg type="i" direction="in"/><arg type="s" direction="in"/></method>
<signal name="NewIcon"/><signal name="NewToolTip"/><signal name="NewStatus"><arg type="s"/></signal>
</interface></node>"""

MENU_XML = """<node><interface name="com.canonical.dbusmenu">
<property name="Version" type="u" access="read"/><property name="TextDirection" type="s" access="read"/>
<property name="Status" type="s" access="read"/><property name="IconThemePath" type="as" access="read"/>
<method name="GetLayout"><arg type="i" direction="in"/><arg type="i" direction="in"/><arg type="as" direction="in"/>
<arg type="u" direction="out"/><arg type="(ia{sv}av)" direction="out"/></method>
<method name="GetGroupProperties"><arg type="ai" direction="in"/><arg type="as" direction="in"/>
<arg type="a(ia{sv})" direction="out"/></method>
<method name="GetProperty"><arg type="i" direction="in"/><arg type="s" direction="in"/><arg type="v" direction="out"/></method>
<method name="Event"><arg type="i" direction="in"/><arg type="s" direction="in"/><arg type="v" direction="in"/>
<arg type="u" direction="in"/></method>
<method name="EventGroup"><arg type="a(isvu)" direction="in"/><arg type="ai" direction="out"/></method>
<method name="AboutToShow"><arg type="i" direction="in"/><arg type="b" direction="out"/></method>
<method name="AboutToShowGroup"><arg type="ai" direction="in"/><arg type="ai" direction="out"/><arg type="ai" direction="out"/></method>
<signal name="LayoutUpdated"><arg type="u"/><arg type="i"/></signal>
</interface></node>"""


def argb_pixmaps(png_path) -> list:
    """IconPixmap wants ARGB32 in network byte order, in a few sizes."""
    from PIL import Image
    img = Image.open(png_path).convert("RGBA")
    out = []
    for size in (22, 32, 48, 64):
        im = img.resize((size, size), Image.LANCZOS)
        r, g, b, a = im.split()
        out.append((size, size, Image.merge("RGBA", (a, r, g, b)).tobytes()))
    return out


class Tray:
    """items: list of (label_or_callable, callback) or None for a separator. First item = left-click action."""

    def __init__(self, title: str, icon_png, items: list):
        self.title = title
        self.items = items
        self.pixmaps = argb_pixmaps(icon_png)
        self.revision = 1
        self._stop = threading.Event()
        self.conn = None
        self.name = f"org.kde.StatusNotifierItem-{os.getpid()}-1"

    # ---------------------------------------------------------------- setup
    def start(self) -> bool:
        """Connects and registers with the tray host. False if there is no host (e.g. plain GNOME)."""
        self.conn = open_dbus_connection(bus="SESSION")
        self.conn.send_and_get_reply(message_bus.RequestName(self.name))
        watcher = DBusAddress("/StatusNotifierWatcher", bus_name="org.kde.StatusNotifierWatcher",
                              interface="org.kde.StatusNotifierWatcher")
        reply = self.conn.send_and_get_reply(new_method_call(watcher, "RegisterStatusNotifierItem", "s", (self.name,)),
                                             timeout=5)
        if reply.header.message_type == MessageType.error:
            self.conn.close()
            return False
        return True

    def stop(self):
        self._stop.set()

    def refresh_menu(self):
        """Call after a label changes."""
        self.revision += 1
        if self.conn:
            sig = new_signal(DBusAddress(MENU_PATH, interface=MENU), "LayoutUpdated", "ui", (self.revision, 0))
            try:
                self.conn.send(sig)
            except Exception:  # noqa: BLE001
                pass

    def run(self):
        while not self._stop.is_set():
            try:
                msg = self.conn.receive(timeout=0.5)
            except TimeoutError:
                continue
            if msg.header.message_type != MessageType.method_call:
                continue
            try:
                reply = self._dispatch(msg)
            except Exception as e:  # noqa: BLE001
                reply = new_error(msg, "org.freedesktop.DBus.Error.Failed", "s", (str(e),))
            if reply is not None and not (msg.header.flags & 1):  # 1 = NO_REPLY_EXPECTED
                self.conn.send(reply)
        try:
            self.conn.close()
        except Exception:  # noqa: BLE001
            pass

    # ---------------------------------------------------------------- D-Bus
    def _label(self, k):
        lab = self.items[k][0]
        return lab() if callable(lab) else lab

    def _item_props(self):
        return {
            "Category": ("s", "ApplicationStatus"), "Id": ("s", "live-clipper"), "Title": ("s", self.title),
            "Status": ("s", "Active"), "WindowId": ("i", 0), "IconName": ("s", ""),
            "IconPixmap": ("a(iiay)", self.pixmaps), "ToolTip": ("(sa(iiay)ss)", ("", [], self.title, "")),
            "ItemIsMenu": ("b", False), "Menu": ("o", MENU_PATH),
        }

    def _menu_props(self):
        return {"Version": ("u", 3), "TextDirection": ("s", "ltr"), "Status": ("s", "normal"),
                "IconThemePath": ("as", [])}

    def _node(self, k):
        if self.items[k] is None:
            props = {"type": ("s", "separator")}
        else:
            props = {"label": ("s", self._label(k)), "enabled": ("b", True), "visible": ("b", True)}
        return (k + 1, props, [])

    def _layout(self):
        children = [("(ia{sv}av)", self._node(k)) for k in range(len(self.items))]
        return (0, {"children-display": ("s", "submenu")}, children)

    def _click(self, item_id):
        k = item_id - 1
        if 0 <= k < len(self.items) and self.items[k] is not None:
            threading.Thread(target=self.items[k][1], daemon=True).start()

    def _dispatch(self, msg):
        f = msg.header.fields
        path, iface, member = f.get(HeaderFields.path), f.get(HeaderFields.interface), f.get(HeaderFields.member)
        if member == "Introspect":
            xml = ITEM_XML if path == ITEM_PATH else MENU_XML if path == MENU_PATH else "<node/>"
            return new_method_return(msg, "s", (xml,))
        if iface == PROPS or member in ("Get", "GetAll"):
            props = self._item_props() if path == ITEM_PATH else self._menu_props()
            if member == "GetAll":
                return new_method_return(msg, "a{sv}", (props,))
            if member == "Get":
                name = msg.body[1]
                if name not in props:
                    return new_error(msg, "org.freedesktop.DBus.Error.UnknownProperty", "s", (name,))
                return new_method_return(msg, "v", (props[name],))
            return new_error(msg, "org.freedesktop.DBus.Error.PropertyReadOnly", "s", ("read only",))
        if path == ITEM_PATH:
            if member in ("Activate", "SecondaryActivate"):
                self._click(1)
            return new_method_return(msg)
        if path == MENU_PATH:
            if member == "GetLayout":
                return new_method_return(msg, "u(ia{sv}av)", (self.revision, self._layout()))
            if member == "GetGroupProperties":
                ids = msg.body[0] or list(range(1, len(self.items) + 1))
                return new_method_return(msg, "a(ia{sv})", ([(i, self._node(i - 1)[1]) for i in ids
                                                            if 1 <= i <= len(self.items)],))
            if member == "GetProperty":
                i, name = msg.body
                props = self._node(i - 1)[1] if 1 <= i <= len(self.items) else {}
                return new_method_return(msg, "v", (props.get(name, ("s", "")),))
            if member == "Event":
                if msg.body[1] == "clicked":
                    self._click(msg.body[0])
                return new_method_return(msg)
            if member == "EventGroup":
                for i, ev, _d, _t in msg.body[0]:
                    if ev == "clicked":
                        self._click(i)
                return new_method_return(msg, "ai", ([],))
            if member == "AboutToShow":
                return new_method_return(msg, "b", (False,))
            if member == "AboutToShowGroup":
                return new_method_return(msg, "aiai", ([], []))
        return new_error(msg, "org.freedesktop.DBus.Error.UnknownMethod", "s", (str(member),))
