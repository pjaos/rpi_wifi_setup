### RPi WiFi Setup Manager

A robust, headless WiFi provisioning and status tool for Raspberry Pi. This tool manages a captive portal via wifi-connect and provides an interrupt-driven OLED interface for system status and external notifications.

## Hardware Requirements

 - Display: 128 x 64 SSD1309 / SSD1306 OLED (I2C). Click [here](https://www.amazon.co.uk/s?k=oled+display+arduino+128+x+64&crid=3I23B4EH9PBEX&sprefix=oled+display+arduino+128+x+64%2Caps%2C114&ref=nb_sb_noss_1) for an examples.

    Button: Momentary switch (Default: GPIO 17). 5s hold for portal, tap to wake.

## Installation & Versions

This project is packaged with Poetry and managed via a custom install script that handles virtual environments and version switching.
1. System Prep

The RPi must have bookworm or later OS and have I2C enabled. The raspi-config can be used to enable I2C.

The RPI should be connected as shown below

![RPI Connections](images/rpi_con.png)

2. Run the Installer

 - Copy the install.py and python wheel file should be copied to the Rpi.

 - Run 'sudo ./install.py rpi_wifi_setup-0.1.0-py3-none-any.whl' (filename version may change) as root user to install.

3. Service Control

As root user run

```
sudo rpi_wifi_setup --enable_auto_start
INFO:  OS: Linux
INFO:  SERVICE FILE: /etc/systemd/system/rpi_wifi_setup.service
INFO:  Created /etc/systemd/system/rpi_wifi_setup.service
INFO:  Enabled rpi_wifi_setup.service on restart
INFO:  Started rpi_wifi_setup.service
```

## Usage
Standard Mode

The screen displays ONLINE/OFFLINE status, the IP Address, and a Signal Strength icon. The screen sleeps after 120s (default) to prevent OLED burn-in.

# Setting up RPi WiFi

- Hold down the button for 5 seconds. The oled display will show

```
Connect to
RPi-Setup
to setup wifi
```

- Using a mobile or tablet connect to the hotspot named 'RPi-Setup'. You will be prompted to sign in to network. Select the SSID of the Wifi network that you wish the RPi to connect to along with the associated password. Select the Connect button.

- When connected the oled display on the RPi will show the online state, it's IP address and the WiFi signal level.

# External App Integration (Override)

The app now supports an interrupt-driven "Mailbox" feature. Any application on the RPi can hijack the OLED display by writing to a temporary file.

    Override Path: /tmp/oled_override.txt

    Behavior: Writing to this file triggers an instant kernel interrupt (inotify). The manager wakes the screen, ignores WiFi status, and displays the file's text.

    Reverting: Deleting the file instantly returns the display to the standard WiFi/IP status screen.

# E.G Display system stats
echo -e "CPU: 55C\nLoad: 0.4\nStatus: Active" > /tmp/oled_override.txt

# E.G Clear and return to WiFi Status
rm /tmp/oled_override.txt


## CLI Arguments
The command line help is displayed if the -h argument is used on the command line.

```
rpi_wifi_setup -h
usage: rpi_wifi_setup [-h] [-b BUTTON_PIN] [-a I2C_ADDRESS] [-w DISPLAY_WIDTH] [-v DISPLAY_HEIGHT]
                      [-s SSID] [-p PASSWORD] [-o SCREEN_OFF_SECONDS] [-d] [--enable_auto_start]
                      [--disable_auto_start] [--check_auto_start]

Linux WiFi provisioning tool.

options:
  -h, --help            show this help message and exit
  -b, --button_pin BUTTON_PIN
                        The GPIO pin that the WiFi button is connected to (default = 17).
  -a, --i2c_address I2C_ADDRESS
                        The I2C bus address of the SSD1306 display (default=3c).
  -w, --display_width DISPLAY_WIDTH
                        The display width in pixels (default = 128).
  -v, --display_height DISPLAY_HEIGHT
                        The display height in pixels (default = 64).
  -s, --ssid SSID       The portal SSID to connect your mobile/tablet (default = RPi-Setup).
  -p, --password PASSWORD
                        The portal password when connecting your mobile/tablet (default = None).
  -o, --screen_off_seconds SCREEN_OFF_SECONDS
                        The the screen off timer (default = 120). Set to 0 to disable.
  -d, --debug           Enable debugging.
  --enable_auto_start   Auto start when this computer starts.
  --disable_auto_start  Disable auto starting when this computer starts.
  --check_auto_start    Check the running status.
```

### Architecture

    Main Loop: 10s heartbeat for signal/network checks (low power).

    Interrupt Thread: watchdog (inotify) monitoring /tmp for zero-latency UI updates.

    Thread Safety: threading.Lock ensures atomic access to the I2C bus between the heartbeat and interrupt triggers.

### Credits & Acknowledgments

This project is a high-level Python wrapper and hardware interface for the balena-io/wifi-connect project.

    WiFi Management: All captive portal logic, AP switching, and captive portal UI foundations are provided by Balena.

    Balena WiFi-Connect is used under the MIT License.
