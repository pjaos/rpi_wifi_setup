#!/usr/bin/env python3

import os
import argparse
import threading
import subprocess
import platform

from time import sleep, time

from p3lib.uio import UIO
from p3lib.helper import logTraceBack, get_assets_dir
from p3lib.boot_manager import BootManager

from gpiozero import Button, LED
from luma.core.interface.serial import i2c
from luma.oled.device import ssd1309
from luma.core.render import canvas
from PIL import ImageFont

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler


class OverrideHandler(FileSystemEventHandler):
    """Interrupt handler for filesystem events"""

    FORCE_DISPLAY_FILE = "/tmp/oled_override.txt"

    def __init__(self, manager):
        self.manager = manager
        self.target_file = OverrideHandler.FORCE_DISPLAY_FILE

    def on_modified(self, event):
        if event.src_path == self.target_file:
            self.manager.handle_interrupt_trigger()

    def on_created(self, event):
        if event.src_path == self.target_file:
            self.manager.handle_interrupt_trigger()

    def on_deleted(self, event):
        if event.src_path == self.target_file:
            self.manager.handle_interrupt_trigger()


class WifiLEDCtrl(threading.Thread):

    CONNECTED = 1
    CONFIGURING = 2
    DISCONNECTED = 3

    def __init__(self, gpio_pin, interval=0.5):
        super().__init__()
        self.led = LED(gpio_pin)
        self.interval = interval
        self._running = False
        self._state = WifiLEDCtrl.DISCONNECTED

    def connected(self):
        """@called when WiFi is connected to set LED on."""
        self._state = WifiLEDCtrl.CONNECTED

    def configuring(self):
        """@called when WiFi is connected to set LED flashing."""
        self._state = WifiLEDCtrl.CONFIGURING

    def disconnected(self):
        """@called when WiFi is connected to set LED off."""
        self._state = WifiLEDCtrl.DISCONNECTED

    def run(self):
        self._running = True
        while self._running:
            if self._state == WifiLEDCtrl.CONNECTED:
                self.led.on()

            elif self._state == WifiLEDCtrl.CONFIGURING:
                self.led.toggle()

            elif self._state == WifiLEDCtrl.DISCONNECTED:
                self.led.off()

            sleep(self.interval)

    def stop(self):
        self._running = False
        self.join()
        self.led.off()


class WiFiSetupManager(object):
    # --- CONFIG ---
    DEFAULT_BUTTON_PIN = 17  # Change to your GPIO pin
    DEFAULT_I2C_ADDR = 0x3C  # Standard I2C address
    DEFAULT_DISPLAY_WIDTH_PIXELS = 128
    DEFAULT_DISPLAY_HEIGHT_PIXELS = 64
    DEFAULT_PORTAL_SSID = "RPi-Setup"
    DEFAULT_PORTAL_PASSWORD = None
    DEFAULT_SCREEN_OFF_SECONDS = 120
    WIFI_CONNECT_BIN_FILENAME = "wifi-connect"
    BUTTON_HOLD_SECONDS = 5

    def __init__(self, uio, options):
        self._uio = uio
        self._options = options
        self._display_lock = threading.Lock()
        self._btn = None
        self._device = None
        self._last_button_press_time = time()
        self._screen_on = True
        self._wifi_led = None
        self._init()

    def _init(self):
        self._font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)
        if not self._font:
            self._font = ImageFont.load_default()

        self._assets_folder = get_assets_dir(module_name='rpi_wifi_setup')

        self._ui_path = os.path.join(self._assets_folder, 'ui')
        if not os.path.isdir(self._ui_path):
            raise Exception(f"{self._ui_path} folder not found.")

        self._wifi_connect_binary = self._get_wifi_connect_bin()

        if not self._check_nmcli_present():
            raise Exception("This system does not have the nmcli command. The network manager is required.")

        if os.geteuid() != 0:
            raise Exception("This program must be executed as root user.")

    def handle_interrupt_trigger(self):
        """Called by the watchdog thread when the file changes"""
        self._reset_timer()  # Wake the screen
        self._render_current_state()  # Force redraw

    def _check_external_message(self):
        """Checks for an override message in /tmp."""
        override_path = "/tmp/oled_override.txt"
        if os.path.exists(override_path):
            try:
                with open(override_path, 'r') as f:
                    # Read first 3 lines to fit the OLED
                    msg = f.read().strip()
                    return msg if msg else None
            except Exception:
                return None
        return None

    def _render_current_state(self):
        """Consolidated rendering logic called by both loop and interrupt"""
        if not self._wifi_led:
            with self._display_lock:
                if not self._screen_on:
                    return

                msg = self._check_external_message()
                if msg:
                    self._update_display(msg)
                elif self._check_internet():
                    self._update_connected_state()
                else:
                    self._update_display("OFFLINE\nHold button to\nsetup WiFi")

    def _check_nmcli_present(self):
        present = False
        try:
            cmd = ['nmcli', '--version']
            subprocess.run(cmd, check=True)
            present = True

        except Exception:
            pass
        return present

    def _get_wifi_connect_bin(self):
        arch = platform.machine()
        if arch not in ['aarch64', 'armv7l', 'x86_64', 'i686']:
            raise Exception(f"{arch} is an unsupported architecture.")
        wifi_connect_folder = os.path.join(self._assets_folder, arch)
        wifi_connect_bin = os.path.join(wifi_connect_folder, WiFiSetupManager.WIFI_CONNECT_BIN_FILENAME)
        if not os.path.isfile(wifi_connect_bin):
            raise Exception(f'{wifi_connect_bin} file not found.')
        return wifi_connect_bin

    def _update_display(self, msg, strength=None):
        # update display if not using just a single led to indicate wifi connectivity
        if self._device:
            with canvas(self._device) as draw:
                draw.rectangle(self._device.bounding_box, outline="white", fill="black")
                draw.text((5, 5), msg, fill="white", font=self._font)

                if strength is not None:
                    self._draw_wifi_icon(draw,
                                         109,
                                         18,
                                         strength)

    def _draw_wifi_icon(self, draw, x, y, strength):
        # Draw 4 bars of increasing height
        for i in range(4):
            height = (i + 1) * 3
            fill = "white" if strength > (i * 25) else "black"
            draw.rectangle([x + (i * 4), y - height, x + (i * 4) + 2, y],
                           outline="white", fill=fill)

    def _cycle_networking(self):
        """@brief Turn networking off/on"""
        try:
            try:
                cmd = ["sudo", "nmcli", "networking", "off"]
                subprocess.run(cmd, check=True)
            finally:
                sleep(1)
                cmd = ["sudo", "nmcli", "networking", "on"]
                subprocess.run(cmd, check=True)

        except Exception:
            logTraceBack(self._uio)

    def _start_wifi_portal(self):
        with self._display_lock:
            if self._wifi_led:
                self._wifi_led.configuring()

            self._update_display(f"Connect to\n{self._options.ssid}\nto setup wifi.")

            self._ensure_wifi_on()

            # -u points to the UI files
            # --portal-ssid is the name your phone will see
            cmd = [
                "sudo", self._wifi_connect_binary,
                "--portal-ssid", self._options.ssid,
                "--ui-directory", self._ui_path
            ]

            if self._options.password:
                cmd += ['-portal-passphrase', self._options.password]

            try:
                # This will block until the user connects or you kill it
                subprocess.run(cmd, check=True)
                self._update_display("Checking\nconnectivity")

                # Example usage with your display logic:
                if self._check_internet():
                    self._update_connected_state()

                else:
                    self._update_display("OFFLINE\nNo Internet")
                    # Cycle the networking in an effort to bring it to life
                    self._cycle_networking()

            except Exception:
                logTraceBack(self._uio)
                self._update_display("OFFLINE\nConnect\nerror")
                self._cycle_networking()

    def _check_internet(self):
        """Returns True if connectivity is 'full', otherwise False."""
        try:
            # Run nmcli command: -t (terse) for easy parsing
            result = subprocess.check_output(
                ["nmcli", "-t", "-f", "CONNECTIVITY", "networking", "connectivity"],
                encoding="utf-8"
            ).strip()

            return result == "full"
        except Exception:
            return False

    def _get_wifi_ip(self):
        """Returns the IPv4 address of wlan0, or None if not connected."""
        try:
            # -g (get) pulls just the specific field. -t (terse) avoids extra text.
            cmd = ["nmcli", "-g", "IP4.ADDRESS", "device", "show", "wlan0"]
            result = subprocess.check_output(cmd, encoding="utf-8").strip()

            # result is usually "192.168.1.50/24", we strip the subnet (/24)
            if result:
                return result.split('/')[0]
            return None
        except Exception:
            return None

    def _get_wifi_strength(self):
        sig_strength = 0
        try:
            cmd = ["nmcli", "-f", "IN-USE,SIGNAL", "device", "wifi"]
            output = subprocess.check_output(cmd, encoding="utf-8")
            for line in output.splitlines():
                if line.startswith('*'):  # The connected network
                    sig_strength = int(line.split()[1])
                    break

        except Exception:
            pass
        return sig_strength

    def _update_connected_state(self):
        ip = self._get_wifi_ip()
        strength = self._get_wifi_strength()
        self._update_display(f"ONLINE\n{ip}\nSignal: {strength}%", strength=strength)

    def _set_screen_power(self, on):
        if self._device:
            if on and not self._screen_on:
                self._device.show()
                self._screen_on = True

            elif not on and self._screen_on:
                self._device.hide()
                self._screen_on = False

    def _reset_timer(self):
        self._last_button_press_time = time()
        self._set_screen_power(True)

    def _ensure_wifi_on(self):
        # Ensure WiFi is turned on
        cmd = ["nmcli", "radio", "wifi", "on"]
        subprocess.run(cmd, check=True)

    def run(self):

        # Hardware Setup
        self._btn = Button(self._options.button_pin,
                           hold_time=WiFiSetupManager.BUTTON_HOLD_SECONDS)

        if self._options.led_pin is not None:
            self._wifi_led = WifiLEDCtrl(self._options.led_pin)
            self._wifi_led.start()

        else:
            self._device = ssd1309(i2c(port=1,
                                   address=self._options.i2c_address),
                                   width=self._options.display_width,
                                   height=self._options.display_height)

            # We only look at the file system for display text updates if the display is connected.
            # Setup the Interrupt Observer for filesystem changes
            self._event_handler = OverrideHandler(self)
            self._observer = Observer()
            # Monitor /tmp for changes
            self._observer.schedule(self._event_handler, path="/tmp", recursive=False)
            self._observer.start()

        self._ensure_wifi_on()

        self._btn.when_held = self._start_wifi_portal
        self._btn.when_pressed = self._reset_timer

        try:
            while True:
                # Handle timeout check
                if self._options.screen_off_seconds and \
                   time() - self._last_button_press_time > self._options.screen_off_seconds:
                    with self._display_lock:
                        self._set_screen_power(False)

                if self._wifi_led:
                    with self._display_lock:
                        if self._check_internet():
                            self._wifi_led.connected()

                        else:
                            self._wifi_led.disconnected()

                else:
                    # Periodic background update (Signal strength/Internet status)
                    if self._screen_on:
                        self._render_current_state()

                sleep(10)  # We can sleep longer now because interrupts handle the UI!
        finally:
            if self._observer:
                self._observer.stop()
                self._observer.join()


def main():
    """@brief Program entry point"""
    uio = UIO(use_emojis=True)

    try:
        parser = argparse.ArgumentParser(description="Linux WiFi provisioning tool.",
                                         formatter_class=argparse.RawDescriptionHelpFormatter)

        parser.add_argument("-b",
                            "--button_pin",
                            type=int,
                            help=f"The GPIO pin that the WiFi button is connected to (default = {WiFiSetupManager.DEFAULT_BUTTON_PIN}).",
                            default=WiFiSetupManager.DEFAULT_BUTTON_PIN)

        parser.add_argument("-a",
                            "--i2c_address",
                            type=lambda x: hex(int(x, 16)),
                            help=f"The I2C bus address of the SSD1306 display (default={WiFiSetupManager.DEFAULT_I2C_ADDR:x}).",
                            default=WiFiSetupManager.DEFAULT_I2C_ADDR)

        parser.add_argument("-l",
                            "--led_pin",
                            type=int,
                            help="If using an LED rather than an oled display to indicate WiFi connectivity then this argument must be the GPIO pin used to drive the LED.")

        parser.add_argument("-w",
                            "--display_width",
                            type=int,
                            help=f"The display width in pixels (default = {WiFiSetupManager.DEFAULT_DISPLAY_WIDTH_PIXELS}).",
                            default=WiFiSetupManager.DEFAULT_DISPLAY_WIDTH_PIXELS)

        parser.add_argument("-v",
                            "--display_height",
                            type=int,
                            help=f"The display height in pixels (default = {WiFiSetupManager.DEFAULT_DISPLAY_HEIGHT_PIXELS}).",
                            default=WiFiSetupManager.DEFAULT_DISPLAY_HEIGHT_PIXELS)

        parser.add_argument("-s",
                            "--ssid",
                            help=f"The portal SSID to connect your mobile/tablet (default = {WiFiSetupManager.DEFAULT_PORTAL_SSID}).",
                            default=WiFiSetupManager.DEFAULT_PORTAL_SSID)

        parser.add_argument("-p",
                            "--password",
                            help=f"The portal password when connecting your mobile/tablet (default = {WiFiSetupManager.DEFAULT_PORTAL_PASSWORD}).",
                            default=WiFiSetupManager.DEFAULT_PORTAL_PASSWORD)

        parser.add_argument("-o",
                            "--screen_off_seconds",
                            type=int,
                            help=f"The the screen off timer (default = {WiFiSetupManager.DEFAULT_SCREEN_OFF_SECONDS}). Set to 0 to disable.",
                            default=WiFiSetupManager.DEFAULT_SCREEN_OFF_SECONDS)

        parser.add_argument("-d", "--debug",
                            action='store_true',
                            help="Enable debugging.")

        # Add args to auto boot cmd
        BootManager.AddCmdArgs(parser)

        options = parser.parse_args()

        uio.enableDebug(options.debug)
        handled = BootManager.HandleOptions(uio, options, False)
        if not handled:
            wiFiSetupManager = WiFiSetupManager(uio, options)
            wiFiSetupManager.run()

    # If the program throws a system exit exception
    except SystemExit:
        pass
    # Don't print error information if CTRL C pressed
    except KeyboardInterrupt:
        pass
    except Exception as ex:
        logTraceBack(uio)

        if options.debug:
            raise
        else:
            uio.error(str(ex))


if __name__ == '__main__':
    main()
