import argparse
import html
import json
import os
import string
import sys
import threading
import time
import requests
import io
import urllib.request
from PIL import Image, ImageTk

from copy import deepcopy

import tkinter as tk

import dbus

from dbus.mainloop.glib import DBusGMainLoop, threads_init
from gi.repository import GLib


__version__ = "1.0"
__author__ = "un.def <me@undef.im>, kalenpw <kalenpw@kalenpw.com>"


class Formatter(string.Formatter):

    _FORMAT_FUNCS = {
        "upper": str.upper,
        "lower": str.lower,
        "capitalize": str.capitalize,
        "icon": "status_icon",
    }

    def __init__(self, format_string, status_icons=None, markup_escape=True):
        self._format_string = format_string
        if status_icons is not None:
            self._status_icons = status_icons.copy()
        else:
            self._status_icons = {}
        self._markup_escape = markup_escape

    def __call__(self, *args, **kwargs):
        return self.format(self._format_string, *args, **kwargs)

    def format_field(self, value, format_spec):
        if format_spec:
            format_func = self._FORMAT_FUNCS[format_spec]
            if isinstance(format_func, str):
                format_func = getattr(self, "_format_func__" + format_func)
            value = format_func(value)
        if self._markup_escape:
            value = html.escape(value)
        return value

    def _format_func__status_icon(self, status):
        return self._status_icons.get(status, "?")


class SpotifyBlocklet:

    DEFAULT_CONFIG = {
        # Format: {field} or {field:filter}
        # Fields: status, artist, title
        # Filters: icon (from status only), upper, lower, capitalize
        "format": "{artist} – {title}",
        #'format': '{artist} – {title}',
        # Escape special characters (such as `<>&`) for Pango markup
        "markup_escape": False,
        # MPRIS `PlaybackStatus` property to icon mapping
        "status_icons": {
            "Playing": "\uf04b",  # 
            "Paused": "\uf04c",  # 
            "Stopped": "\uf04d",  # 
        },
        # X11 mouse button number to MPRIS method mapping
        # 1 = left click, 2 = middle click
        "mouse_buttons": {
            "1": "PlayPause",
        },
        # Do not print the same info multiple times if True
        "dedupe": True,
    }

    BUS_NAME = "org.mpris.MediaPlayer2.spotify"
    OBJECT_PATH = "/org/mpris/MediaPlayer2"
    PLAYER_INTERFACE = "org.mpris.MediaPlayer2.Player"
    PROPERTIES_INTERFACE = "org.freedesktop.DBus.Properties"

    loop = None

    def __init__(self, config=None):
        _config = deepcopy(self.DEFAULT_CONFIG)
        if config:
            for key, value in config.items():
                if isinstance(value, dict):
                    _config[key].update(value)
                else:
                    _config[key] = value
        self._formatter = Formatter(
            format_string=_config["format"],
            status_icons=_config["status_icons"],
            markup_escape=_config["markup_escape"],
        )
        self._mouse_buttons = _config["mouse_buttons"]
        self._dedupe = _config["dedupe"]
        self._prev_info = None
        self._handle_input_thread = threading.Thread(
            target=self.handle_input, daemon=True
        )
        self.gui = None
        self.old_status = ""

    def handle_input(self):
        while True:
            button = sys.stdin.readline().strip()
            method_name = self._mouse_buttons.get(button)
            if method_name:
                self.gui = GUIManager(self, self.get_property("PlaybackStatus"))
                self.gui.show_window()

    def init_loop(self):
        self.loop = GLib.MainLoop()
        # See: https://dbus.freedesktop.org/doc/dbus-python/
        # dbus.mainloop.html?highlight=thread#dbus.mainloop.glib.threads_init
        threads_init()
        DBusGMainLoop(set_as_default=True)

    def _run(self):
        self.bus = dbus.SessionBus()
        self.spotify = self.bus.get_object(
            bus_name=self.BUS_NAME,
            object_path=self.OBJECT_PATH,
            follow_name_owner_changes=True,
        )
        self.connect_to_dbus_signals()
        self.show_initial_info()
        self.loop.run()

    def run(self, *, init_loop=False, forever=False):
        if init_loop:
            self.init_loop()
        elif self.loop is None:
            raise RuntimeError("Loop is not initialized; call init_loop() first.")
        self._handle_input_thread.start()
        while True:
            try:
                self._run()
            except dbus.exceptions.DBusException:
                time.sleep(1)
            except KeyboardInterrupt:
                break
            finally:
                if not forever:
                    break
        self.loop.quit()

    def connect_to_dbus_signals(self):
        self.spotify.connect_to_signal(
            signal_name="PropertiesChanged",
            handler_function=self.on_properties_changed,
            dbus_interface=self.PROPERTIES_INTERFACE,
        )
        self.bus.get_object(
            bus_name="org.freedesktop.DBus",
            object_path="/org/freedesktop/DBus",
        ).connect_to_signal(
            signal_name="NameOwnerChanged",
            handler_function=self.on_name_owner_changed,
            dbus_interface="org.freedesktop.DBus",
            arg0=self.BUS_NAME,
        )

    def on_properties_changed(self, interface_name, changed_properties, _):
        """Show updated info when playback status or track is changed"""
        self.show_info(
            status=changed_properties["PlaybackStatus"],
            metadata=changed_properties["Metadata"],
            only_if_changed=self._dedupe,
        )

    def on_name_owner_changed(self, name, old_owner, new_owner):
        """Clear info when Spotify is closed"""
        if old_owner and not new_owner:
            print(flush=True)
            self._prev_info = None

    def get_property(self, property_name):
        return self.spotify.Get(
            self.PLAYER_INTERFACE,
            property_name,
            dbus_interface=self.PROPERTIES_INTERFACE,
        )

    def show_initial_info(self):
        self.show_info(
            status=self.get_property("PlaybackStatus"),
            metadata=self.get_property("Metadata"),
        )

    def show_info(self, status, metadata, only_if_changed=False):
        artist = ", ".join(metadata["xesam:artist"])
        title = metadata["xesam:title"]
        info = self._formatter(
            status=status,
            artist=artist,
            title=title,
        )
        # Check status separately so we can update play/pause icon without picture reloading
        if self.old_status != status:
            if self.gui is not None:
                self.gui.update_play_button_text(status)
                self.old_status = status

        if not only_if_changed or self._prev_info != info:
            print(info, flush=True)
            self._prev_info = info
            if self.gui is not None:
                self.gui.update_from_spotify_block(self, status)


class DarkButton(tk.Button):
    def __init__(self, master, **kw):
        tk.Button.__init__(
            self,
            master=master,
            fg="#eeeeee",
            bg="#222222",
            activebackground="#2a2a2a",
            activeforeground="#eeeeee",
            borderwidth=0,
            highlightthickness=0,
            width=5,
            **kw,
        )
        self.defaultBackground = self["background"]
        self.bind("<Enter>", self.on_enter)
        self.bind("<Leave>", self.on_leave)

    def on_enter(self, e):
        self["background"] = self["activebackground"]

    def on_leave(self, e):
        self["background"] = self.defaultBackground


class GUIManager:
    def __init__(self, spotify_block, status):
        self.root = tk.Tk()
        # must be initialized before trying to display
        self.status_str = tk.StringVar()
        self.image = None
        self.image_wrapper = None

        self.update_from_spotify_block(spotify_block, status)

    def update_from_spotify_block(self, spotify_block, status):
        metadata = spotify_block.get_property("Metadata")
        self.artist = ", ".join(metadata["xesam:artist"])
        self.title = metadata["xesam:title"]
        self.album = metadata["xesam:album"]
        self.album_art = metadata["mpris:artUrl"]
        self.album_art = self.old_url_to_new(self.album_art)
        self.previous_func = getattr(spotify_block.spotify, "Previous")
        self.play_pause_func = getattr(spotify_block.spotify, "PlayPause")
        self.next_func = getattr(spotify_block.spotify, "Next")
        self.player_interface = spotify_block.PLAYER_INTERFACE

        self.update_play_button_text(status)
        self.root.title(f"{self.artist} - {self.title}")
        if self.image and self.image_wrapper:
            self.update_image()

    def show_window(self):
        self.root.attributes("-type", "dialog")
        self.root.configure(background="#222222")

        # width x height + right offset + top offset
        self.root.geometry(f"+1650+1082")
        self.root.bind("<Escape>", self.on_focus_out)

        top_frame = tk.Frame(self.root)

        self.image = self.image_from_url(self.album_art)
        self.image_wrapper = tk.Label(
            top_frame,
            image=self.image,
            borderwidth=0,
            highlightthickness=0,
            padx=0,
            pady=0,
        )
        self.image_wrapper.pack()

        bottom_frame = tk.Frame(self.root, bg="#222222")

        btn_prev = DarkButton(
            bottom_frame,
            text="\u25c0",
            command=self.previous_song,
        ).grid(row=0, column=0)

        btn_toggle_play = DarkButton(
            bottom_frame,
            textvariable=self.status_str,
            command=self.toggle_play,
        ).grid(row=0, column=1)

        btn_next = DarkButton(bottom_frame, text="\u25b6", command=self.next_song).grid(
            row=0, column=2
        )

        top_frame.pack(side="top", fill="both", expand=True)
        bottom_frame.pack(side="bottom")

        self.root.mainloop()

    def update_play_button_text(self, status):
        if status == "Playing":
            self.status_str.set("\uf04c")
        else:
            self.status_str.set("\uf04b")

    def update_image(self):
        self.image = self.image_from_url(self.old_url_to_new(self.album_art))
        self.image_wrapper.configure(image=self.image)

    def next_song(self):
        self.next_func(dbus_interface=self.player_interface)

    def previous_song(self):
        self.previous_func(dbus_interface=self.player_interface)

    def toggle_play(self):
        self.play_pause_func(dbus_interface=self.player_interface)

    def on_focus_out(self, event):
        self.root.destroy()
        # self.root.quit()

    def image_from_url(self, url):
        with urllib.request.urlopen(url) as connection:
            raw_data = connection.read()
        im = Image.open(io.BytesIO(raw_data))
        image = ImageTk.PhotoImage(im)
        return image

    # at some point spotify changed the url of album art, but mpris wasn't updated
    # from: https://open.spotify.com/image/ab67616d00001e024474cfb9ed594824a1d5ec66
    # to: https://i.scdn.co/image/ab67616d00001e024474cfb9ed594824a1d5ec66
    def old_url_to_new(self, url):
        new_base_url = "https://i.scdn.co/image/"
        song_id = url.split("/")[-1]
        return f"{new_base_url}{song_id}"


def _parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("-c", "--config")
    parser.add_argument("-f", "--format")
    markup_escape_group = parser.add_mutually_exclusive_group()
    markup_escape_group.add_argument(
        "--markup-escape",
        action="store_true",
        default=None,
        dest="markup_escape",
    )
    markup_escape_group.add_argument(
        "--no-markup-escape",
        action="store_false",
        default=None,
        dest="markup_escape",
    )
    dedupe_group = parser.add_mutually_exclusive_group()
    dedupe_group.add_argument(
        "--dedupe",
        action="store_true",
        default=None,
        dest="dedupe",
    )
    dedupe_group.add_argument(
        "--no-dedupe",
        action="store_false",
        default=None,
        dest="dedupe",
    )
    args = parser.parse_args()
    return args


def _main():
    args = _parse_args()
    if args.config:
        with open(os.path.abspath(args.config)) as fp:
            config = json.load(fp)
    else:
        config = {}
    for key in ["format", "markup_escape", "dedupe"]:
        value = getattr(args, key)
        if value is not None:
            config[key] = value
    SpotifyBlocklet(config=config).run(init_loop=True, forever=True)


if __name__ == "__main__":
    _main()
